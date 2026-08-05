from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tomllib

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.dialects.postgresql import insert as pg_insert


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQLITE_PATH = PROJECT_ROOT / "data" / "scm.db"


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
    parser = argparse.ArgumentParser(description="Migrate SCM SQLite data to Supabase PostgreSQL.")
    parser.add_argument("--sqlite-path", default=str(DEFAULT_SQLITE_PATH), help="기존 SQLite DB 경로")
    parser.add_argument("--dry-run", action="store_true", help="이관 없이 테이블별 건수만 확인")
    parser.add_argument("--tables", nargs="*", default=[], help="일부 테이블만 이관할 때 테이블명 지정")
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
    rows = [dict(row) for row in source_conn.execute(select(table)).mappings()]
    source_count = len(rows)
    target_before = target_conn.execute(select(table.c.id).limit(1)).fetchone()
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


def main() -> int:
    bootstrap_project_imports()

    from backend import models  # noqa: F401
    from backend.database import Base, DATABASE_URL, engine, init_db, is_postgresql_url

    if not os.getenv("SCM_DATABASE_URL", "").strip():
        raise RuntimeError("SCM_DATABASE_URL이 필요합니다. .streamlit/secrets.toml 또는 환경변수에 설정해주세요.")
    if not is_postgresql_url(DATABASE_URL):
        raise RuntimeError("SCM_DATABASE_URL이 PostgreSQL 연결 문자열이 아닙니다.")

    args = parse_args()
    sqlite_path = Path(args.sqlite_path).expanduser().resolve()
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite DB를 찾지 못했습니다: {sqlite_path}")

    source_engine = create_engine(f"sqlite:///{sqlite_path.as_posix()}", future=True)
    source_tables = set(inspect(source_engine).get_table_names())
    selected_tables = set(args.tables or [])
    model_tables = [
        table
        for table in Base.metadata.sorted_tables
        if not selected_tables or table.name in selected_tables
    ]

    if args.dry_run:
        print(f"[DRY RUN] SQLite: {sqlite_path}")
        print(f"[DRY RUN] PostgreSQL: {DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else 'configured'}")
        with source_engine.connect() as source_conn:
            for table in model_tables:
                if table.name not in source_tables:
                    print(f"- {table.name}: source table 없음")
                    continue
                count = source_conn.execute(select(table.c.id)).fetchall()
                print(f"- {table.name}: SQLite {len(count)}건")
        return 0

    init_db()
    results = []
    with source_engine.connect() as source_conn:
        with engine.begin() as target_conn:
            Base.metadata.create_all(bind=target_conn)
            for table in model_tables:
                if table.name not in source_tables:
                    results.append(
                        {
                            "table": table.name,
                            "source_rows": 0,
                            "target_had_rows": False,
                            "inserted_rows": 0,
                            "skipped_rows": 0,
                            "note": "source table 없음",
                        }
                    )
                    continue
                results.append(migrate_table(source_conn, target_conn, table))

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
