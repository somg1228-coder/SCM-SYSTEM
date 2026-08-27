from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tomllib

from sqlalchemy import create_engine, delete, inspect, select
from sqlalchemy.dialects.postgresql import insert as pg_insert


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQLITE_PATH = PROJECT_ROOT / "data" / "scm.db"
DEFAULT_SCHEDULE_SQLITE_PATH = PROJECT_ROOT / "data" / "schedule.db"
DEFAULT_MEETING_SQLITE_PATH = PROJECT_ROOT / "data" / "meeting_reports.db"
DEFAULT_RETURN_CASE_SQLITE_PATH = PROJECT_ROOT / "ReturnCaseSystem" / "cases.db"
DEFAULT_LAYOUT_JSON_PATH = PROJECT_ROOT / "data" / "warehouse3d_layouts.json"
DEFAULT_PURCHASE_BUDGET_JSON_PATH = PROJECT_ROOT / "data" / "purchase_budgets.json"


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
    if not secrets_path.exists() or "SCM_DATABASE_URL" in os.environ:
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
    parser = argparse.ArgumentParser(description="Migrate SCM SQLite/JSON data to Supabase PostgreSQL.")
    parser.add_argument("--sqlite-path", default=str(DEFAULT_SQLITE_PATH), help="Legacy main SQLite DB path")
    parser.add_argument("--schedule-sqlite-path", default=str(DEFAULT_SCHEDULE_SQLITE_PATH), help="Legacy schedule SQLite DB path")
    parser.add_argument("--meeting-sqlite-path", default=str(DEFAULT_MEETING_SQLITE_PATH), help="Legacy meeting SQLite DB path")
    parser.add_argument("--return-case-sqlite-path", default=str(DEFAULT_RETURN_CASE_SQLITE_PATH), help="Legacy return case SQLite DB path")
    parser.add_argument("--layout-json", default=str(DEFAULT_LAYOUT_JSON_PATH), help="Legacy 3D warehouse layout JSON path")
    parser.add_argument("--purchase-budget-json", default=str(DEFAULT_PURCHASE_BUDGET_JSON_PATH), help="Legacy purchase budget JSON path")
    parser.add_argument("--skip-layout-json", action="store_true", help="Skip legacy 3D warehouse layout JSON")
    parser.add_argument("--skip-purchase-budget-json", action="store_true", help="Skip legacy purchase budget JSON")
    parser.add_argument("--dry-run", action="store_true", help="Preview source row counts without writing")
    parser.add_argument("--tables", nargs="*", default=[], help="Optional table names to migrate")
    return parser.parse_args()
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


def migrate_table(source_conn, target_conn, table, chunk_size: int = 500) -> dict:
    table_name = table.name
    if table_name == "cases":
        return migrate_return_cases_table(source_conn, target_conn, table, chunk_size)

    rows = [dict(row) for row in source_conn.execute(select(table)).mappings()]
    source_count = len(rows)
    first_column = next(iter(table.c))
    target_before = target_conn.execute(select(first_column).limit(1)).fetchone()
    inserted = 0
    if rows:
        for offset in range(0, len(rows), chunk_size):
            batch = rows[offset : offset + chunk_size]
            result = target_conn.execute(pg_insert(table).values(batch).on_conflict_do_nothing())
            inserted += int(result.rowcount or 0)
    primary_keys = [column.name for column in table.primary_key.columns]
    if len(primary_keys) == 1 and primary_keys[0] == "id":
        reset_postgresql_sequence(target_conn, table_name, "id")
    return {
        "table": table_name,
        "source_rows": source_count,
        "target_had_rows": bool(target_before),
        "inserted_rows": inserted,
        "skipped_rows": source_count - inserted,
    }


def migrate_return_cases_table(source_conn, target_conn, table, chunk_size: int = 200) -> dict:
    rows = [dict(row) for row in source_conn.execute(select(table)).mappings()]
    source_count = len(rows)
    first_column = next(iter(table.c))
    target_before = target_conn.execute(select(first_column).limit(1)).fetchone()
    case_ids = [str(row.get("case_id") or "").strip() for row in rows if str(row.get("case_id") or "").strip()]
    deleted = 0
    inserted = 0
    if case_ids:
        result = target_conn.execute(delete(table).where(table.c.case_id.in_(case_ids)))
        deleted = int(result.rowcount or 0)
    if rows:
        insert_rows = [{key: value for key, value in row.items() if key != "id"} for row in rows]
        for offset in range(0, len(insert_rows), chunk_size):
            batch = insert_rows[offset : offset + chunk_size]
            result = target_conn.execute(table.insert(), batch)
            inserted += int(result.rowcount or 0)
    reset_postgresql_sequence(target_conn, table.name, "id")
    return {
        "table": table.name,
        "source_rows": source_count,
        "target_had_rows": bool(target_before),
        "inserted_rows": inserted,
        "skipped_rows": source_count - inserted,
        "note": f"case_id 기준 기존 {deleted}건 교체",
    }


def migrate_sqlite_file(sqlite_path: Path, label: str, model_tables: list, selected_tables: set[str], target_conn) -> list[dict]:
    if not sqlite_path.exists():
        return [
            {
                "table": label,
                "source_rows": 0,
                "target_had_rows": False,
                "inserted_rows": 0,
                "skipped_rows": 0,
                "note": f"source DB missing: {sqlite_path}",
            }
        ]

    source_engine = create_engine(f"sqlite:///{sqlite_path.as_posix()}", future=True)
    source_tables = set(inspect(source_engine).get_table_names())
    results = []
    with source_engine.connect() as source_conn:
        for table in model_tables:
            if selected_tables and table.name not in selected_tables:
                continue
            if table.name not in source_tables:
                continue
            results.append(migrate_table(source_conn, target_conn, table))
    if not results:
        results.append(
            {
                "table": label,
                "source_rows": 0,
                "target_had_rows": False,
                "inserted_rows": 0,
                "skipped_rows": 0,
                "note": "matching source tables missing",
            }
        )
    return results


def layout_rows_from_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    locations = payload.get("locations") if isinstance(payload, dict) else None
    if not isinstance(locations, dict):
        return []
    rows = []
    for building, floors in locations.items():
        if not isinstance(floors, dict):
            continue
        for floor, layout_data in floors.items():
            if not isinstance(layout_data, dict):
                continue
            if not (layout_data.get("racks") or layout_data.get("fixtures") or layout_data.get("floor_size")):
                continue
            rows.append(
                {
                    "building": str(building),
                    "floor": str(floor),
                    "layout_data": layout_data,
                    "is_active": True,
                }
            )
    return rows


def migrate_layout_json(target_conn, layout_table, layout_path: Path) -> dict:
    rows = layout_rows_from_json(layout_path)
    inserted = 0
    if rows:
        stmt = pg_insert(layout_table).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["building", "floor"],
            set_={
                "layout_data": stmt.excluded.layout_data,
                "is_active": True,
            },
        )
        result = target_conn.execute(stmt)
        inserted = int(result.rowcount or 0)
    return {
        "table": "warehouse_layouts(json)",
        "source_rows": len(rows),
        "target_had_rows": False,
        "inserted_rows": inserted,
        "skipped_rows": len(rows) - inserted,
        "note": str(layout_path),
    }


def migrate_purchase_budget_json(target_conn, budget_table, budget_path: Path) -> dict:
    if not budget_path.exists():
        return {
            "table": "purchase_budget_stores(json)",
            "source_rows": 0,
            "target_had_rows": False,
            "inserted_rows": 0,
            "skipped_rows": 0,
            "note": str(budget_path),
        }
    payload = json.loads(budget_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        payload = {"version": 1, "budgets": [], "approvals": []}
    payload.setdefault("version", 1)
    payload.setdefault("budgets", [])
    payload.setdefault("approvals", [])
    stmt = pg_insert(budget_table).values(store_key=budget_path.name, payload=payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=["store_key"],
        set_={"payload": stmt.excluded.payload},
    )
    result = target_conn.execute(stmt)
    return {
        "table": "purchase_budget_stores(json)",
        "source_rows": 1,
        "target_had_rows": False,
        "inserted_rows": int(result.rowcount or 0),
        "skipped_rows": 0,
        "note": str(budget_path),
    }


def main() -> int:
    bootstrap_project_imports()
    args = parse_args()

    from backend import models  # noqa: F401
    from backend.database import Base, DATABASE_URL, engine, init_db, is_postgresql_url

    if not args.dry_run and not os.getenv("SCM_DATABASE_URL", "").strip():
        raise RuntimeError("SCM_DATABASE_URL이 필요합니다. .streamlit/secrets.toml 또는 환경변수에 설정해주세요.")
    if not args.dry_run and not is_postgresql_url(DATABASE_URL):
        raise RuntimeError("SCM_DATABASE_URL이 PostgreSQL 연결 문자열이 아닙니다.")
    selected_tables = set(args.tables or [])
    model_tables = [
        table
        for table in Base.metadata.sorted_tables
        if not selected_tables or table.name in selected_tables
    ]
    sqlite_sources = [
        ("data/scm.db", Path(args.sqlite_path).expanduser().resolve()),
        ("data/schedule.db", Path(args.schedule_sqlite_path).expanduser().resolve()),
        ("data/meeting_reports.db", Path(args.meeting_sqlite_path).expanduser().resolve()),
        ("ReturnCaseSystem/cases.db", Path(args.return_case_sqlite_path).expanduser().resolve()),
    ]

    if args.dry_run:
        print(f"[DRY RUN] PostgreSQL: {DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else 'configured'}")
        for label, sqlite_path in sqlite_sources:
            print(f"[DRY RUN] SQLite: {sqlite_path}")
            if not sqlite_path.exists():
                print(f"- {label}: source DB 없음")
                continue
            source_engine = create_engine(f"sqlite:///{sqlite_path.as_posix()}", future=True)
            source_tables = set(inspect(source_engine).get_table_names())
            with source_engine.connect() as source_conn:
                for table in model_tables:
                    if table.name not in source_tables:
                        continue
                    first_column = next(iter(table.c))
                    count = source_conn.execute(select(first_column)).fetchall()
                    print(f"- {table.name}: SQLite {len(count)}건")
        if not args.skip_purchase_budget_json:
            budget_path = Path(args.purchase_budget_json).expanduser().resolve()
            print(f"- purchase_budget_stores(json): {1 if budget_path.exists() else 0}건")
        if not args.skip_layout_json:
            layout_rows = layout_rows_from_json(Path(args.layout_json).expanduser().resolve())
            print(f"- warehouse_layouts(json): {len(layout_rows)}건")
        return 0

    init_db()
    results = []
    with engine.begin() as target_conn:
        Base.metadata.create_all(bind=target_conn)
        for label, sqlite_path in sqlite_sources:
            results.extend(migrate_sqlite_file(sqlite_path, label, model_tables, selected_tables, target_conn))
        if not args.skip_purchase_budget_json and (not selected_tables or "purchase_budget_stores" in selected_tables):
            budget_table = Base.metadata.tables.get("purchase_budget_stores")
            if budget_table is not None:
                budget_path = Path(args.purchase_budget_json).expanduser().resolve()
                results.append(migrate_purchase_budget_json(target_conn, budget_table, budget_path))
        if not args.skip_layout_json and (not selected_tables or "warehouse_layouts" in selected_tables):
            layout_table = Base.metadata.tables.get("warehouse_layouts")
            if layout_table is not None:
                layout_path = Path(args.layout_json).expanduser().resolve()
                results.append(migrate_layout_json(target_conn, layout_table, layout_path))

    print("SQLite -> Supabase PostgreSQL 이관 결과")
    for row in results:
        note = f" / {row['note']}" if row.get("note") else ""
        print(
            f"- {row['table']}: SQLite {row['source_rows']}건, "
            f"삽입 {row['inserted_rows']}건, 중복/기존 {row['skipped_rows']}건{note}"
        )
    print("완료: 기존 SQLite DB는 삭제하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

