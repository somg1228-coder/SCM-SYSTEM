from __future__ import annotations

import os
from pathlib import Path
import sys
import tomllib
from urllib.parse import urlsplit, urlunsplit

import psycopg2
from psycopg2 import sql


PROJECT_ROOT = Path(__file__).resolve().parent
REQUIRED_TABLES = [
    "inventory_daily",
    "thirdparty_product_master",
    "category_bom_items",
    "warehouse_layouts",
]


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


def masked_url(value: str) -> str:
    parts = urlsplit(value)
    if "@" not in parts.netloc:
        return value
    userinfo, hostinfo = parts.netloc.rsplit("@", 1)
    username = userinfo.split(":", 1)[0]
    return urlunsplit((parts.scheme, f"{username}:***@{hostinfo}", parts.path, parts.query, parts.fragment))


def table_exists(cur, table_name: str) -> bool:
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
        )
        """,
        (table_name,),
    )
    return bool(cur.fetchone()[0])


def table_count(cur, table_name: str) -> int:
    cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table_name)))
    return int(cur.fetchone()[0])


def main() -> int:
    load_local_config()
    url = database_url()
    print(f"DB URL: {masked_url(url)}")

    try:
        conn = psycopg2.connect(url, connect_timeout=10)
    except Exception as exc:
        print(f"[FAIL] Connection failed: {exc}", file=sys.stderr)
        return 1

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                print(f"[OK] SELECT 1 = {cur.fetchone()[0]}")
                cur.execute("SELECT inet_server_addr(), inet_server_port(), current_database(), current_user")
                host, port, db_name, user = cur.fetchone()
                print(f"[OK] Host: {host}:{port} / Database: {db_name} / User: {user}")

                failed = False
                for table in REQUIRED_TABLES:
                    if not table_exists(cur, table):
                        print(f"[FAIL] Missing table: {table}")
                        failed = True
                        continue
                    print(f"[OK] {table}: {table_count(cur, table):,} rows")
                return 1 if failed else 0
    except Exception as exc:
        print(f"[FAIL] Query failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
