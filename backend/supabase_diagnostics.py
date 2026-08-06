from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import psycopg2
from psycopg2 import sql

from backend.config import config_text_value, masked_database_url


IMPORTANT_TABLES = [
    "inventory_daily",
    "thirdparty_product_master",
    "category_bom_items",
    "warehouse_layouts",
]


def normalized_psycopg_url(value: str) -> str:
    return value.replace("postgresql+psycopg2://", "postgresql://", 1)


def database_url_from_config() -> tuple[str, str]:
    value, source = config_text_value("SCM_DATABASE_URL")
    if not value:
        raise RuntimeError("SCM_DATABASE_URL is not configured.")
    return normalized_psycopg_url(value), source


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


def run_supabase_connection_diagnostics(tables: list[str] | None = None) -> dict[str, Any]:
    tables = tables or IMPORTANT_TABLES
    result: dict[str, Any] = {
        "ok": False,
        "configured": False,
        "source": "unset",
        "masked_url": "",
        "host": "",
        "port": "",
        "database": "",
        "user": "",
        "select_1_ok": False,
        "select_1_result": None,
        "tables": {},
        "error": "",
    }
    try:
        url, source = database_url_from_config()
        result["configured"] = True
        result["source"] = source
        result["masked_url"] = masked_database_url(url)
        parts = urlsplit(url)
        result["host"] = parts.hostname or ""
        result["port"] = parts.port or ""
    except Exception as exc:
        result["error"] = str(exc)
        return result

    try:
        conn = psycopg2.connect(url, connect_timeout=10)
    except Exception as exc:
        result["error"] = str(exc)
        return result

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                result["select_1_result"] = cur.fetchone()[0]
                result["select_1_ok"] = True
                cur.execute("SELECT inet_server_addr(), inet_server_port(), current_database(), current_user")
                host, port, db_name, user = cur.fetchone()
                result["host"] = str(host or result["host"])
                result["port"] = str(port or result["port"])
                result["database"] = str(db_name or "")
                result["user"] = str(user or "")

                failed = False
                for table in tables:
                    table_result = {"exists": False, "count": None, "error": ""}
                    try:
                        table_result["exists"] = table_exists(cur, table)
                        if table_result["exists"]:
                            table_result["count"] = table_count(cur, table)
                        else:
                            failed = True
                            table_result["error"] = "table missing"
                    except Exception as table_exc:
                        failed = True
                        table_result["error"] = str(table_exc)
                    result["tables"][table] = table_result
                result["ok"] = result["select_1_ok"] and not failed
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        conn.close()
    return result
