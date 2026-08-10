from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.dialects.postgresql import insert as pg_insert


BASE_DIR = Path(__file__).resolve().parents[1]
MIGRATION_KEY = "sqlite-bootstrap-2026-08-10"
REFERENCE_BACKFILL_KEY = "sqlite-product-master-reference-backfill-2026-08-10"
SQLITE_SOURCES = [
    BASE_DIR / "data" / "scm.db",
    BASE_DIR / "data" / "schedule.db",
    BASE_DIR / "data" / "meeting_reports.db",
    BASE_DIR / "data" / "scm_inventory.db",
    BASE_DIR / "ReturnCaseSystem" / "cases.db",
]
PRODUCT_MASTER_REFERENCE_TABLES = (
    "offline_product_master",
    "thirdparty_product_master",
    "warehouse_product_master",
)
PRODUCT_MASTER_TEXT_REFERENCE_FIELDS = (
    "large_category",
    "medium_category",
    "small_category",
    "memo",
)
PRODUCT_MASTER_NUMBER_REFERENCE_FIELDS = (
    "pack_qty",
    "box_qty",
)


def run_once() -> dict[str, Any]:
    from backend import models  # noqa: F401
    from backend.database import Base, engine, init_db, is_postgresql_url, DATABASE_URL

    if not is_postgresql_url(DATABASE_URL):
        return {"ok": False, "skipped": True, "reason": "not-postgresql"}

    init_db()
    with engine.begin() as target_conn:
        ensure_migration_table(target_conn)
        if migration_already_done(target_conn):
            reference_backfill = backfill_product_master_references_once(target_conn, Base.metadata.tables)
            return {"ok": True, "skipped": True, "reason": "already-done", "reference_backfill": reference_backfill}

        target_tables = Base.metadata.tables
        summaries = []
        for sqlite_path in SQLITE_SOURCES:
            summaries.extend(migrate_sqlite_file(sqlite_path, target_conn, target_tables))
        source_total = sum(int(row.get("source_rows") or 0) for row in summaries)
        if source_total <= 0:
            reference_backfill = backfill_product_master_references_once(target_conn, target_tables)
            return {"ok": True, "skipped": True, "reason": "no-source-sqlite-rows", "summaries": summaries, "reference_backfill": reference_backfill}
        record_migration_done(target_conn, summaries)
        reference_backfill = backfill_product_master_references_once(target_conn, target_tables)
        return {"ok": True, "skipped": False, "summaries": summaries, "reference_backfill": reference_backfill}


def ensure_migration_table(conn) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS sqlite_migration_runs (
                migration_key TEXT PRIMARY KEY,
                ran_at TEXT NOT NULL,
                summary JSONB NOT NULL DEFAULT '[]'::jsonb
            )
            """
        )
    )


def migration_already_done(conn) -> bool:
    row = conn.execute(
        text("SELECT 1 FROM sqlite_migration_runs WHERE migration_key = :key"),
        {"key": MIGRATION_KEY},
    ).fetchone()
    return row is not None


def record_migration_done(conn, summaries: list[dict[str, Any]]) -> None:
    conn.execute(
        text(
            """
            INSERT INTO sqlite_migration_runs (migration_key, ran_at, summary)
            VALUES (:key, :ran_at, CAST(:summary AS jsonb))
            ON CONFLICT (migration_key)
            DO UPDATE SET ran_at = EXCLUDED.ran_at, summary = EXCLUDED.summary
            """
        ),
        {
            "key": MIGRATION_KEY,
            "ran_at": datetime.now().isoformat(timespec="seconds"),
            "summary": json.dumps(summaries, ensure_ascii=False, default=str),
        },
    )


def reference_backfill_already_done(conn) -> bool:
    row = conn.execute(
        text("SELECT 1 FROM sqlite_migration_runs WHERE migration_key = :key"),
        {"key": REFERENCE_BACKFILL_KEY},
    ).fetchone()
    return row is not None


def record_reference_backfill_done(conn, summaries: list[dict[str, Any]]) -> None:
    conn.execute(
        text(
            """
            INSERT INTO sqlite_migration_runs (migration_key, ran_at, summary)
            VALUES (:key, :ran_at, CAST(:summary AS jsonb))
            ON CONFLICT (migration_key)
            DO UPDATE SET ran_at = EXCLUDED.ran_at, summary = EXCLUDED.summary
            """
        ),
        {
            "key": REFERENCE_BACKFILL_KEY,
            "ran_at": datetime.now().isoformat(timespec="seconds"),
            "summary": json.dumps(summaries, ensure_ascii=False, default=str),
        },
    )


def backfill_product_master_references_once(target_conn, target_tables) -> dict[str, Any]:
    if reference_backfill_already_done(target_conn):
        return {"ok": True, "skipped": True, "reason": "already-done"}

    summaries = []
    for sqlite_path in SQLITE_SOURCES:
        summaries.extend(backfill_product_master_references_from_sqlite(sqlite_path, target_conn, target_tables))

    source_rows = sum(int(row.get("source_rows") or 0) for row in summaries)
    updated_rows = sum(int(row.get("updated_rows") or 0) for row in summaries)
    if source_rows <= 0:
        return {"ok": True, "skipped": True, "reason": "no-source-product-master-rows", "summaries": summaries}

    record_reference_backfill_done(target_conn, summaries)
    return {"ok": True, "skipped": False, "updated_rows": updated_rows, "summaries": summaries}


def backfill_product_master_references_from_sqlite(sqlite_path: Path, target_conn, target_tables) -> list[dict[str, Any]]:
    if not sqlite_path.exists() or sqlite_path.stat().st_size == 0:
        return []

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    try:
        source_tables = set(sqlite_table_names(sqlite_conn))
        summaries = []
        for table_name in PRODUCT_MASTER_REFERENCE_TABLES:
            table = target_tables.get(table_name)
            if table is None or table_name not in source_tables:
                continue
            summaries.append(backfill_product_master_reference_table(sqlite_conn, target_conn, table, sqlite_path))
        return summaries
    finally:
        sqlite_conn.close()


def backfill_product_master_reference_table(sqlite_conn: sqlite3.Connection, target_conn, table, sqlite_path: Path) -> dict[str, Any]:
    source_columns = sqlite_column_names(sqlite_conn, table.name)
    identity_columns = [column for column in ("sku", "barcode", "product_name") if column in source_columns]
    reference_columns = [
        column
        for column in (*PRODUCT_MASTER_TEXT_REFERENCE_FIELDS, *PRODUCT_MASTER_NUMBER_REFERENCE_FIELDS)
        if column in source_columns and column in table.columns
    ]
    if not identity_columns or not reference_columns:
        return {
            "source": str(sqlite_path.relative_to(BASE_DIR)),
            "table": table.name,
            "source_rows": 0,
            "updated_rows": 0,
            "skipped": "columns-missing",
        }

    selected_columns = ", ".join(f'"{column}"' for column in [*identity_columns, *reference_columns])
    rows = sqlite_conn.execute(f'SELECT {selected_columns} FROM "{table.name}"').fetchall()
    updated_rows = 0
    for row in rows:
        updated_rows += backfill_product_master_reference_row(target_conn, table, dict(row), reference_columns)

    return {
        "source": str(sqlite_path.relative_to(BASE_DIR)),
        "table": table.name,
        "source_rows": len(rows),
        "updated_rows": updated_rows,
        "skipped": "",
    }


def backfill_product_master_reference_row(target_conn, table, row: dict[str, Any], reference_columns: list[str]) -> int:
    sku = clean_migration_text(row.get("sku"))
    barcode = clean_migration_text(row.get("barcode"))
    product_name = clean_migration_text(row.get("product_name"))
    if not sku and not (barcode and product_name):
        return 0

    values: dict[str, Any] = {}
    missing_conditions = []
    set_parts = []
    for field in reference_columns:
        if field in PRODUCT_MASTER_TEXT_REFERENCE_FIELDS:
            value = clean_migration_text(row.get(field))
            if not value:
                continue
            values[field] = value
            set_parts.append(f'"{field}" = CASE WHEN COALESCE("{field}", \'\') = \'\' THEN :{field} ELSE "{field}" END')
            missing_conditions.append(f'COALESCE("{field}", \'\') = \'\'')
        elif field in PRODUCT_MASTER_NUMBER_REFERENCE_FIELDS:
            value = int(row.get(field) or 0)
            if value <= 0:
                continue
            values[field] = value
            set_parts.append(f'"{field}" = CASE WHEN COALESCE("{field}", 0) = 0 THEN :{field} ELSE "{field}" END')
            missing_conditions.append(f'COALESCE("{field}", 0) = 0')

    if not set_parts:
        return 0
    if "updated_at" in table.columns:
        set_parts.append('"updated_at" = CURRENT_TIMESTAMP')

    values.update({"sku": sku, "barcode": barcode, "product_name": product_name})
    table_name = '"' + table.name.replace('"', '""') + '"'
    if sku:
        identity_condition = '"sku" = :sku'
    else:
        identity_condition = '"barcode" = :barcode AND "product_name" = :product_name'
    stmt = text(
        f"""
        UPDATE {table_name}
        SET {", ".join(set_parts)}
        WHERE {identity_condition}
          AND ({" OR ".join(missing_conditions)})
        """
    )
    result = target_conn.execute(stmt, values)
    return int(result.rowcount or 0)


def clean_migration_text(value) -> str:
    return "" if value is None else str(value).strip()


def migrate_sqlite_file(sqlite_path: Path, target_conn, target_tables) -> list[dict[str, Any]]:
    if not sqlite_path.exists() or sqlite_path.stat().st_size == 0:
        return [{"source": str(sqlite_path.relative_to(BASE_DIR)), "table": "", "source_rows": 0, "upserted_rows": 0, "skipped": "missing"}]

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    try:
        source_tables = sqlite_table_names(sqlite_conn)
        summaries = []
        for table_name in source_tables:
            table = target_tables.get(table_name)
            if table is None:
                summaries.append({"source": str(sqlite_path.relative_to(BASE_DIR)), "table": table_name, "source_rows": sqlite_count(sqlite_conn, table_name), "upserted_rows": 0, "skipped": "target-missing"})
                continue
            summaries.append(migrate_table(sqlite_conn, target_conn, table, sqlite_path))
        return summaries
    finally:
        sqlite_conn.close()


def sqlite_table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]


def sqlite_count(conn: sqlite3.Connection, table_name: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0] or 0)


def migrate_table(sqlite_conn: sqlite3.Connection, target_conn, table, sqlite_path: Path, chunk_size: int = 250) -> dict[str, Any]:
    rows = sqlite_rows(sqlite_conn, table.name, table)
    source_rows = len(rows)
    upserted_rows = 0
    if rows:
        pk_columns = [column.name for column in table.primary_key.columns]
        for offset in range(0, len(rows), chunk_size):
            batch = rows[offset : offset + chunk_size]
            stmt = pg_insert(table).values(batch)
            if pk_columns:
                update_columns = [column.name for column in table.columns if column.name not in pk_columns]
                if update_columns:
                    stmt = stmt.on_conflict_do_update(
                        index_elements=pk_columns,
                        set_={name: getattr(stmt.excluded, name) for name in update_columns},
                    )
                else:
                    stmt = stmt.on_conflict_do_nothing(index_elements=pk_columns)
            else:
                stmt = stmt.on_conflict_do_nothing()
            result = target_conn.execute(stmt)
            upserted_rows += int(result.rowcount or 0)
        reset_sequence(target_conn, table.name)
    return {
        "source": str(sqlite_path.relative_to(BASE_DIR)),
        "table": table.name,
        "source_rows": source_rows,
        "upserted_rows": upserted_rows,
        "skipped": "",
    }


def sqlite_rows(conn: sqlite3.Connection, table_name: str, table) -> list[dict[str, Any]]:
    source_columns = sqlite_column_names(conn, table_name)
    target_columns = [column.name for column in table.columns]
    shared_columns = [column for column in target_columns if column in source_columns]
    if not shared_columns:
        return []

    selected_columns = ", ".join(f'"{column}"' for column in shared_columns)
    rows = conn.execute(f'SELECT {selected_columns} FROM "{table_name}"').fetchall()
    target_column_map = {column.name: column for column in table.columns}
    return [
        {
            column: normalize_value(row[column], target_column_map.get(column))
            for column in shared_columns
        }
        for row in rows
    ]


def sqlite_column_names(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()}


def normalize_value(value, target_column=None):
    if isinstance(value, memoryview):
        return bytes(value)
    if target_column is not None and "JSON" in str(target_column.type).upper() and isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def reset_sequence(conn, table_name: str) -> None:
    inspector = inspect(conn)
    columns = inspector.get_columns(table_name)
    if not any(column["name"] == "id" for column in columns):
        return
    sequence = conn.execute(text("SELECT pg_get_serial_sequence(:table_name, 'id')"), {"table_name": table_name}).scalar()
    if not sequence:
        return
    safe_table = '"' + table_name.replace('"', '""') + '"'
    conn.execute(
        text(
            f"""
            SELECT setval(
                :sequence,
                GREATEST(COALESCE((SELECT MAX(id) FROM {safe_table}), 1), 1),
                (SELECT COUNT(*) FROM {safe_table}) > 0
            )
            """
        ),
        {"sequence": sequence},
    )
