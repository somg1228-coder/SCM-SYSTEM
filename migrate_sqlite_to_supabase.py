from __future__ import annotations

from datetime import date, datetime
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import tomllib
from typing import Any

import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_values, Json


PROJECT_ROOT = Path(__file__).resolve().parent
SQLITE_PATH = PROJECT_ROOT / "data" / "scm.db"
IMPORTANT_TABLES = {
    "inventory_daily": 1218,
    "thirdparty_product_master": 284,
    "category_bom_items": 719,
    "warehouse_layouts": 2,
}


def load_key_value_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def load_local_config() -> None:
    load_key_value_file(PROJECT_ROOT / ".env")
    secrets_path = PROJECT_ROOT / ".streamlit" / "secrets.toml"
    if secrets_path.exists() and "SCM_DATABASE_URL" not in os.environ:
        payload = tomllib.loads(secrets_path.read_text(encoding="utf-8"))
        value = str(payload.get("SCM_DATABASE_URL") or "").strip()
        if value:
            os.environ["SCM_DATABASE_URL"] = value


def database_url() -> str:
    value = os.getenv("SCM_DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("SCM_DATABASE_URL is not configured.")
    return value.replace("postgresql+psycopg2://", "postgresql://", 1)


def sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [row[0] for row in rows]


def table_columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [
        {"name": row[1], "type": str(row[2] or "").upper(), "notnull": bool(row[3]), "default": row[4], "pk": bool(row[5])}
        for row in rows
    ]


def conflict_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    indexes = conn.execute(f'PRAGMA index_list("{table}")').fetchall()
    for index in indexes:
        if not index[2]:
            continue
        index_name = index[1]
        cols = [row[2] for row in conn.execute(f'PRAGMA index_info("{index_name}")').fetchall()]
        if cols and cols != ["id"]:
            return cols
    pk_cols = [column["name"] for column in table_columns(conn, table) if column["pk"]]
    if pk_cols:
        return pk_cols
    return []


def normalize_value(value: Any, column_type: str) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    type_text = column_type.upper()
    if "BOOL" in type_text:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)
    if "JSON" in type_text:
        if isinstance(value, (dict, list)):
            return Json(value)
        if isinstance(value, str):
            try:
                return Json(json.loads(value))
            except json.JSONDecodeError:
                return Json(value)
    if "BLOB" in type_text or "BINARY" in type_text:
        return psycopg2.Binary(value)
    return value


def fetch_rows(conn: sqlite3.Connection, table: str, columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    col_names = [column["name"] for column in columns]
    rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
    result = []
    for row in rows:
        item = {}
        for column, value in zip(columns, row):
            item[column["name"]] = normalize_value(value, column["type"])
        result.append(item)
    return result


def postgres_table_exists(cur, table: str) -> bool:
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
        )
        """,
        (table,),
    )
    return bool(cur.fetchone()[0])


def upsert_rows(pg_conn, sqlite_conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    columns = table_columns(sqlite_conn, table)
    column_names = [column["name"] for column in columns]
    rows = fetch_rows(sqlite_conn, table, columns)
    conflicts = conflict_columns(sqlite_conn, table)
    result = {"table": table, "source": len(rows), "success": 0, "failed": 0, "errors": []}
    if not rows:
        return result

    with pg_conn.cursor() as cur:
        if not postgres_table_exists(cur, table):
            result["failed"] = len(rows)
            result["errors"].append({"row": "-", "error": f"target table missing: {table}"})
            return result

        insert_columns = sql.SQL(", ").join(sql.Identifier(name) for name in column_names)
        conflict_sql = sql.SQL("")
        if conflicts:
            conflict_targets = sql.SQL(", ").join(sql.Identifier(name) for name in conflicts)
            update_cols = [name for name in column_names if name not in conflicts and name != "id"]
            if update_cols:
                assignments = sql.SQL(", ").join(
                    sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(name), sql.Identifier(name)) for name in update_cols
                )
                conflict_sql = sql.SQL(" ON CONFLICT ({}) DO UPDATE SET {}").format(conflict_targets, assignments)
            else:
                conflict_sql = sql.SQL(" ON CONFLICT ({}) DO NOTHING").format(conflict_targets)
        else:
            conflict_sql = sql.SQL(" ON CONFLICT DO NOTHING")

        statement = sql.SQL("INSERT INTO {} ({}) VALUES %s{}").format(sql.Identifier(table), insert_columns, conflict_sql)
        values = [tuple(row.get(name) for name in column_names) for row in rows]
        try:
            execute_values(cur, statement.as_string(cur), values, page_size=500)
            result["success"] = len(rows)
        except Exception as batch_exc:
            pg_conn.rollback()
            for index, value_tuple in enumerate(values, start=1):
                try:
                    with pg_conn.cursor() as row_cur:
                        execute_values(row_cur, statement.as_string(row_cur), [value_tuple], page_size=1)
                    pg_conn.commit()
                    result["success"] += 1
                except Exception as row_exc:
                    pg_conn.rollback()
                    result["failed"] += 1
                    result["errors"].append({"row": index, "error": str(row_exc)})
            result["errors"].insert(0, {"row": "batch", "error": str(batch_exc)})
        else:
            pg_conn.commit()

        reset_sequence(cur, table, columns)
        pg_conn.commit()
    return result


def reset_sequence(cur, table: str, columns: list[dict[str, Any]]) -> None:
    id_column = next((column for column in columns if column["name"] == "id"), None)
    if not id_column or "INT" not in id_column["type"].upper():
        return
    cur.execute(
        """
        SELECT pg_get_serial_sequence(%s, 'id')
        """,
        (table,),
    )
    sequence = cur.fetchone()[0]
    if not sequence:
        return
    cur.execute(
        sql.SQL("SELECT setval(%s, GREATEST(COALESCE((SELECT MAX(id) FROM {}), 1), 1), (SELECT COUNT(*) FROM {}) > 0)").format(
            sql.Identifier(table),
            sql.Identifier(table),
        ),
        (sequence,),
    )


def main() -> int:
    load_local_config()
    if not SQLITE_PATH.exists():
        print(f"[FAIL] SQLite DB not found: {SQLITE_PATH}", file=sys.stderr)
        return 1

    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    try:
        pg_conn = psycopg2.connect(database_url(), connect_timeout=10)
    except Exception as exc:
        print(f"[FAIL] Supabase connection failed: {exc}", file=sys.stderr)
        return 1

    summaries = []
    try:
        for table in sqlite_tables(sqlite_conn):
            summary = upsert_rows(pg_conn, sqlite_conn, table)
            summaries.append(summary)
    finally:
        sqlite_conn.close()
        pg_conn.close()

    print("SQLite -> Supabase PostgreSQL migration result")
    failed_total = 0
    for summary in summaries:
        expected = IMPORTANT_TABLES.get(summary["table"])
        suffix = f" / expected current SQLite count {expected}" if expected is not None else ""
        print(
            f"- {summary['table']}: source={summary['source']}, "
            f"success={summary['success']}, failed={summary['failed']}{suffix}"
        )
        failed_total += int(summary["failed"])
        for error in summary["errors"][:10]:
            print(f"  [ERROR] row={error['row']} reason={error['error']}")
    print("Done. The SQLite source file was not deleted.")
    return 1 if failed_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
