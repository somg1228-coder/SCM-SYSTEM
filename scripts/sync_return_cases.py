from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
from pathlib import Path
import sqlite3
import sys
import tomllib
from typing import Any

from sqlalchemy import create_engine, func, select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQLITE_PATH = PROJECT_ROOT / "ReturnCaseSystem" / "cases.db"
DEFAULT_SEED_PATH = PROJECT_ROOT / "ReturnCaseSystem" / "cases_seed.json.gz"
BLOB_COLUMNS = {
    "product_image",
    "case_image",
    "case_image_original",
    "repair_image",
    "repair_image_original",
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


def load_streamlit_secrets() -> None:
    secrets_path = PROJECT_ROOT / ".streamlit" / "secrets.toml"
    if not secrets_path.exists() or os.getenv("SCM_DATABASE_URL", "").strip():
        return
    payload = tomllib.loads(secrets_path.read_text(encoding="utf-8"))
    value = str(payload.get("SCM_DATABASE_URL") or "").strip()
    if value:
        os.environ["SCM_DATABASE_URL"] = value


def bootstrap_project_imports() -> None:
    sys.path.insert(0, str(PROJECT_ROOT))
    load_key_value_file(PROJECT_ROOT / ".env")
    load_streamlit_secrets()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export or sync ReturnCaseSystem cases with Supabase PostgreSQL.")
    parser.add_argument("--sqlite-path", default=str(DEFAULT_SQLITE_PATH), help="Local ReturnCaseSystem cases.db path")
    parser.add_argument("--seed-path", default=str(DEFAULT_SEED_PATH), help="Tracked gzip JSON seed path")
    parser.add_argument("--export-seed", action="store_true", help="Export local cases.db to the gzip JSON seed")
    parser.add_argument("--from-seed", action="store_true", help="Sync from the gzip JSON seed instead of local SQLite")
    parser.add_argument("--dry-run", action="store_true", help="Print counts without writing to PostgreSQL")
    return parser.parse_args()


def encode_blob(value: bytes | None) -> str:
    return base64.b64encode(value or b"").decode("ascii") if value else ""


def decode_blob(value: str | None) -> bytes | None:
    if not value:
        return None
    return base64.b64decode(value.encode("ascii"))


def rows_from_sqlite(sqlite_path: Path) -> list[dict[str, Any]]:
    if not sqlite_path.exists():
        raise FileNotFoundError(f"Return case SQLite DB not found: {sqlite_path}")
    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = []
        for row in conn.execute("SELECT * FROM cases ORDER BY id").fetchall():
            item = dict(row)
            for column in BLOB_COLUMNS:
                item[column] = encode_blob(item.get(column))
            rows.append(item)
        return rows
    finally:
        conn.close()


def write_seed(rows: list[dict[str, Any]], seed_path: Path) -> None:
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "source": "ReturnCaseSystem/cases.db",
        "row_count": len(rows),
        "blob_columns": sorted(BLOB_COLUMNS),
        "rows": rows,
    }
    with gzip.open(seed_path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))


def rows_from_seed(seed_path: Path) -> list[dict[str, Any]]:
    if not seed_path.exists():
        raise FileNotFoundError(f"Return case seed not found: {seed_path}")
    with gzip.open(seed_path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"Invalid return case seed: {seed_path}")
    return rows


def database_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        item = {key: value for key, value in row.items() if key != "id"}
        for column in BLOB_COLUMNS:
            item[column] = decode_blob(item.get(column))
        output.append(item)
    return output


def reset_postgresql_sequence(conn, table_name: str, pk_name: str) -> None:
    conn.exec_driver_sql(
        """
        SELECT setval(
            pg_get_serial_sequence(%s, %s),
            GREATEST(COALESCE((SELECT MAX(%s) FROM %s), 1), 1),
            (SELECT COUNT(*) FROM %s) > 0
        )
        """
        % (
            repr(table_name),
            repr(pk_name),
            '"' + pk_name.replace('"', '""') + '"',
            '"' + table_name.replace('"', '""') + '"',
            '"' + table_name.replace('"', '""') + '"',
        )
    )


def sync_to_postgresql(rows: list[dict[str, Any]]) -> dict[str, int]:
    bootstrap_project_imports()
    from backend import models  # noqa: F401
    from backend.database import Base, DATABASE_URL, engine, init_db, is_postgresql_url

    if not os.getenv("SCM_DATABASE_URL", "").strip():
        raise RuntimeError("SCM_DATABASE_URL is required to sync return cases to Supabase.")
    if not is_postgresql_url(DATABASE_URL):
        raise RuntimeError("SCM_DATABASE_URL must point to PostgreSQL/Supabase.")

    init_db()
    table = Base.metadata.tables["cases"]
    db_rows = database_rows(rows)
    case_ids = [str(row.get("case_id") or "").strip() for row in db_rows if str(row.get("case_id") or "").strip()]
    with engine.begin() as conn:
        Base.metadata.create_all(bind=conn)
        before = int(conn.execute(select(func.count()).select_from(table)).scalar() or 0)
        existing_case_ids = set()
        if case_ids:
            existing_case_ids = {
                str(row[0]).strip()
                for row in conn.execute(select(table.c.case_id).where(table.c.case_id.in_(case_ids))).fetchall()
                if row[0]
            }
        insert_rows = []
        seen_case_ids = set()
        for row in db_rows:
            case_id = str(row.get("case_id") or "").strip()
            if not case_id or case_id in existing_case_ids or case_id in seen_case_ids:
                continue
            seen_case_ids.add(case_id)
            insert_rows.append(row)
        inserted = 0
        if insert_rows:
            result = conn.execute(table.insert(), insert_rows)
            inserted = int(result.rowcount or 0)
        reset_postgresql_sequence(conn, "cases", "id")
        after = int(conn.execute(select(func.count()).select_from(table)).scalar() or 0)
    return {
        "source_rows": len(rows),
        "target_before": before,
        "existing": len(existing_case_ids),
        "inserted": inserted,
        "skipped": len(rows) - inserted,
        "target_after": after,
    }


def main() -> int:
    args = parse_args()
    sqlite_path = Path(args.sqlite_path).expanduser().resolve()
    seed_path = Path(args.seed_path).expanduser().resolve()

    if args.export_seed:
        rows = rows_from_sqlite(sqlite_path)
        write_seed(rows, seed_path)
        print(f"Exported {len(rows)} return cases to {seed_path}")
        return 0

    rows = rows_from_seed(seed_path) if args.from_seed else rows_from_sqlite(sqlite_path)
    source_label = "seed" if args.from_seed else "sqlite"
    if args.dry_run:
        print(f"[DRY RUN] source={source_label} rows={len(rows)} seed={seed_path}")
        return 0

    result = sync_to_postgresql(rows)
    print(
        "Return cases synced: "
        f"source={result['source_rows']}, "
        f"target_before={result['target_before']}, "
        f"inserted={result['inserted']}, "
        f"existing_or_skipped={result['skipped']}, "
        f"target_after={result['target_after']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
