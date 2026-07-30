from __future__ import annotations

import os
from pathlib import Path

try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import declarative_base, sessionmaker
except ModuleNotFoundError as exc:
    raise RuntimeError("sqlalchemy가 설치되어 있지 않습니다. `pip install -r requirements.txt` 후 다시 실행해주세요.") from exc


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("SCM_DATABASE_URL", f"sqlite:///{DATA_DIR / 'scm.db'}")
CONNECT_ARGS = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=CONNECT_ARGS, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def init_db() -> None:
    from backend import models  # noqa: F401

    repair_sqlite_schema()
    Base.metadata.create_all(bind=engine)
    ensure_sqlite_columns()


def repair_sqlite_schema() -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return

    known_stale_indexes = ("ix_purchase_documents_created_at",)
    with engine.begin() as conn:
        for index_name in known_stale_indexes:
            drop_sqlite_index(conn, index_name)
        drop_orphan_sqlite_indexes(conn)
        conn.exec_driver_sql("PRAGMA writable_schema = OFF")


def drop_sqlite_index(conn, index_name: str) -> None:
    try:
        conn.exec_driver_sql(f"DROP INDEX IF EXISTS {quote_sqlite_identifier(index_name)}")
    except Exception:
        conn.exec_driver_sql("PRAGMA writable_schema = ON")
        conn.exec_driver_sql(
            "DELETE FROM sqlite_master WHERE type = 'index' AND name = ?",
            (index_name,),
        )
        conn.exec_driver_sql("PRAGMA writable_schema = OFF")


def drop_orphan_sqlite_indexes(conn) -> None:
    try:
        tables = {
            row[0]
            for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        indexes = conn.exec_driver_sql(
            "SELECT name, tbl_name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
        ).fetchall()
    except Exception:
        return

    for index_name, table_name in indexes:
        if table_name not in tables:
            drop_sqlite_index(conn, index_name)


def quote_sqlite_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def ensure_sqlite_columns() -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return

    column_specs = {
        "inventory_daily": {
            "product_code": "VARCHAR(120) NOT NULL DEFAULT ''",
            "available_stock": "INTEGER NOT NULL DEFAULT 0",
            "supplier": "VARCHAR(160) NOT NULL DEFAULT ''",
        },
        "inventory_inbound": {
            "product_code": "VARCHAR(120) NOT NULL DEFAULT ''",
        },
        "offline_product_master": {
            "sort_order": "INTEGER NOT NULL DEFAULT 0",
        },
        "thirdparty_product_master": {
            "sort_order": "INTEGER NOT NULL DEFAULT 0",
        },
        "warehouse_product_master": {
            "sort_order": "INTEGER NOT NULL DEFAULT 0",
        },
        "category_bom_items": {
            "barcode": "VARCHAR(120) NOT NULL DEFAULT ''",
            "spec": "VARCHAR(160) NOT NULL DEFAULT ''",
        },
        "purchase_orders": {
            "actual_inbound_date": "DATE",
            "currency": "VARCHAR(10) NOT NULL DEFAULT 'KRW'",
        },
        "purchase_requests": {
            "item_code": "VARCHAR(120) NOT NULL DEFAULT ''",
            "unit": "VARCHAR(40) NOT NULL DEFAULT 'EA'",
            "reply_due_date": "DATE",
            "desired_due_date": "DATE",
            "delivery_place": "VARCHAR(160) NOT NULL DEFAULT '로긴 물류센터'",
            "request_notes": "VARCHAR(500) NOT NULL DEFAULT ''",
            "approver": "VARCHAR(120) NOT NULL DEFAULT ''",
        },
        "rfq_quotes": {
            "quote_number": "VARCHAR(40) NOT NULL DEFAULT ''",
            "supplier_manager": "VARCHAR(120) NOT NULL DEFAULT ''",
            "supplier_phone": "VARCHAR(80) NOT NULL DEFAULT ''",
            "supplier_email": "VARCHAR(160) NOT NULL DEFAULT ''",
            "payment_terms": "VARCHAR(120) NOT NULL DEFAULT ''",
            "quote_valid_until": "DATE",
            "is_selected": "BOOLEAN NOT NULL DEFAULT 0",
            "selection_reason": "VARCHAR(500) NOT NULL DEFAULT ''",
            "currency": "VARCHAR(10) NOT NULL DEFAULT 'KRW'",
        },
        "suppliers": {
            "handled_items": "VARCHAR(500) NOT NULL DEFAULT ''",
            "moq_terms": "VARCHAR(500) NOT NULL DEFAULT ''",
            "avg_unit_price_currency": "VARCHAR(10) NOT NULL DEFAULT 'KRW'",
            "payment_terms": "VARCHAR(120) NOT NULL DEFAULT ''",
        },
    }

    with engine.begin() as conn:
        for table_name, columns in column_specs.items():
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table_name})")}
            for column_name, ddl in columns.items():
                if column_name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")
        ensure_product_master_barcode_constraints(conn)


def ensure_product_master_barcode_constraints(conn) -> None:
    table_prefixes = {
        "offline_product_master": ("offline", True),
        "thirdparty_product_master": ("thirdparty", False),
        "warehouse_product_master": ("warehouse", True),
    }
    for table_name, (prefix, keep_barcode_product_unique) in table_prefixes.items():
        row = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        if not row or not row[0]:
            continue
        table_sql = row[0]
        if keep_barcode_product_unique and "UNIQUE (barcode)" not in table_sql:
            continue
        if not keep_barcode_product_unique and "UNIQUE (barcode, product_name)" not in table_sql:
            continue
        rebuild_product_master_table(conn, table_name, prefix, keep_barcode_product_unique)


def rebuild_product_master_table(conn, table_name: str, prefix: str, keep_barcode_product_unique: bool = True) -> None:
    old_table = f"{table_name}_old_barcode_unique"
    columns = [
        "id",
        "sku",
        "barcode",
        "product_name",
        "large_category",
        "medium_category",
        "small_category",
        "brand",
        "supplier",
        "pack_qty",
        "box_qty",
        "default_lead_time",
        "min_stock",
        "sort_order",
        "is_active",
        "memo",
        "created_at",
        "updated_at",
    ]
    column_sql = ", ".join(columns)

    conn.exec_driver_sql(f"DROP TABLE IF EXISTS {old_table}")
    conn.exec_driver_sql(f"ALTER TABLE {table_name} RENAME TO {old_table}")
    barcode_constraint = (
        f"CONSTRAINT uq_{prefix}_product_master_barcode_product_name UNIQUE (barcode, product_name),"
        if keep_barcode_product_unique
        else ""
    )
    conn.exec_driver_sql(
        f"""
        CREATE TABLE {table_name} (
            id INTEGER NOT NULL,
            sku VARCHAR(120) NOT NULL,
            barcode VARCHAR(120) NOT NULL,
            product_name VARCHAR(255) NOT NULL,
            large_category VARCHAR(120) NOT NULL,
            medium_category VARCHAR(120) NOT NULL,
            small_category VARCHAR(120) NOT NULL,
            brand VARCHAR(120) NOT NULL,
            supplier VARCHAR(160) NOT NULL,
            pack_qty INTEGER NOT NULL,
            box_qty INTEGER NOT NULL,
            default_lead_time INTEGER NOT NULL,
            min_stock INTEGER NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_active VARCHAR(20) NOT NULL,
            memo VARCHAR(500) NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_{prefix}_product_master_sku UNIQUE (sku),
            {barcode_constraint}
            CONSTRAINT ck_{prefix}_product_master_is_active CHECK (is_active IN ('사용', '미사용'))
        )
        """
    )
    conn.exec_driver_sql(f"INSERT INTO {table_name} ({column_sql}) SELECT {column_sql} FROM {old_table}")
    conn.exec_driver_sql(f"DROP TABLE {old_table}")
    conn.exec_driver_sql(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_id ON {table_name} (id)")
    conn.exec_driver_sql(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_sku ON {table_name} (sku)")
    conn.exec_driver_sql(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_barcode ON {table_name} (barcode)")
    conn.exec_driver_sql(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_product_name ON {table_name} (product_name)")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
