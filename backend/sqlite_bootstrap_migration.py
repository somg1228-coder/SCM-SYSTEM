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
SQLITE_SOURCES = [
    BASE_DIR / "data" / "scm.db",
    BASE_DIR / "data" / "schedule.db",
    BASE_DIR / "data" / "meeting_reports.db",
    BASE_DIR / "data" / "scm_inventory.db",
    BASE_DIR / "ReturnCaseSystem" / "cases.db",
]


def run_once() -> dict[str, Any]:
    from backend import models  # noqa: F401
    from backend.database import Base, engine, init_db, is_postgresql_url, DATABASE_URL

    if not is_postgresql_url(DATABASE_URL):
        return {"ok": False, "skipped": True, "reason": "not-postgresql"}

    init_db()
    with engine.begin() as target_conn:
        ensure_migration_table(target_conn)
        if migration_already_done(target_conn):
            return {"ok": True, "skipped": True, "reason": "already-done"}

        target_tables = Base.metadata.tables
        summaries = []
        for sqlite_path in SQLITE_SOURCES:
            summaries.extend(migrate_sqlite_file(sqlite_path, target_conn, target_tables))
        source_total = sum(int(row.get("source_rows") or 0) for row in summaries)
        if source_total <= 0:
            return {"ok": True, "skipped": True, "reason": "no-source-sqlite-rows", "summaries": summaries}
        record_migration_done(target_conn, summaries)
        return {"ok": True, "skipped": False, "summaries": summaries}


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
