from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile

try:
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import declarative_base, sessionmaker
except ModuleNotFoundError as exc:
    raise RuntimeError("sqlalchemy가 설치되어 있지 않습니다. `pip install -r requirements.txt` 후 다시 실행해주세요.") from exc


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
LOGGER = logging.getLogger("scm.database")
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError as exc:
    LOGGER.warning("기본 data 폴더를 만들 수 없습니다. 쓰기 가능한 대체 SQLite 경로를 사용합니다: %s", exc)

DEFAULT_DB_PATH = (DATA_DIR / "scm.db").resolve()
RAW_DATABASE_URL = os.getenv("SCM_DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH.as_posix()}")


def make_file_writable(path: Path) -> None:
    if not path.exists():
        return
    path.chmod(path.stat().st_mode | stat.S_IWRITE | stat.S_IREAD)


def test_sqlite_directory_writable(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    test_names = [
        ".scm_sqlite_write_test",
        ".scm_sqlite_write_test-journal",
        ".scm_sqlite_write_test-wal",
        ".scm_sqlite_write_test-shm",
    ]
    created = []
    try:
        for name in test_names:
            test_path = directory / name
            with test_path.open("wb") as handle:
                handle.write(b"ok")
            created.append(test_path)
    finally:
        for test_path in created:
            try:
                test_path.unlink()
            except OSError:
                LOGGER.warning("SQLite 쓰기 테스트 파일 삭제 실패: %s", test_path)


def sqlite_path_is_writable(db_path: Path) -> bool:
    try:
        make_file_writable(db_path)
        test_sqlite_directory_writable(db_path.parent)
        with db_path.open("ab"):
            pass
        if not os.access(db_path, os.W_OK) or not os.access(db_path.parent, os.W_OK):
            return False
        return sqlite_write_probe(db_path)
    except (OSError, sqlite3.Error) as exc:
        LOGGER.warning("SQLite 경로가 쓰기 불가입니다: %s (%s)", db_path, exc)
        return False


def sqlite_write_probe(db_path: Path) -> bool:
    try:
        with sqlite3.connect(str(db_path), timeout=2) as conn:
            query_only = conn.execute("PRAGMA query_only").fetchone()
            if query_only and int(query_only[0] or 0) != 0:
                return False
            conn.execute("BEGIN IMMEDIATE")
            conn.rollback()
        return True
    except sqlite3.Error as exc:
        LOGGER.warning("SQLite 실제 쓰기 트랜잭션 테스트 실패: %s (%s)", db_path, exc)
        return False


def writable_runtime_data_dir() -> Path:
    candidates = []
    env_dir = os.getenv("SCM_WRITABLE_DATA_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.extend(
        [
            Path(tempfile.gettempdir()) / "scm_portal_data",
            Path.home() / ".scm_portal" / "data",
        ]
    )
    for directory in candidates:
        try:
            resolved = directory.expanduser().resolve()
            test_sqlite_directory_writable(resolved)
            return resolved
        except OSError as exc:
            LOGGER.warning("SQLite 대체 폴더 쓰기 테스트 실패: %s (%s)", directory, exc)
    raise RuntimeError("SQLite DB를 저장할 쓰기 가능한 폴더를 찾지 못했습니다.")


def copy_sqlite_to_writable_path(source_path: Path) -> Path:
    target_dir = writable_runtime_data_dir()
    target_path = (target_dir / source_path.name).resolve()
    if source_path.exists() and source_path.resolve() != target_path:
        if not target_path.exists() or source_path.stat().st_mtime > target_path.stat().st_mtime:
            shutil.copy2(source_path, target_path)
            LOGGER.warning("읽기 전용 SQLite DB를 쓰기 가능한 경로로 복사했습니다: %s -> %s", source_path, target_path)
    if not target_path.exists():
        target_path.touch()
    make_file_writable(target_path)
    return target_path


def normalize_database_url(raw_url: str) -> str:
    if not raw_url.startswith("sqlite"):
        return raw_url
    url = make_url(raw_url)
    db_name = url.database
    if not db_name or db_name == ":memory:":
        return raw_url
    db_path = Path(db_name)
    if not db_path.is_absolute():
        db_path = (BASE_DIR / db_path).resolve()
    else:
        db_path = db_path.resolve()
    if not sqlite_path_is_writable(db_path):
        db_path = copy_sqlite_to_writable_path(db_path)
    query = dict(url.query)
    if query.get("mode") == "ro":
        LOGGER.warning("SQLite URL에 mode=ro가 포함되어 있어 쓰기 가능한 rwc 모드로 변경합니다.")
        query["mode"] = "rwc"
    query.pop("immutable", None)
    query_string = ""
    if query:
        query_string = "?" + "&".join(f"{key}={value}" for key, value in sorted(query.items()))
    return f"sqlite:///{db_path.as_posix()}{query_string}"


DATABASE_URL = normalize_database_url(RAW_DATABASE_URL)
CONNECT_ARGS = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}


def sqlite_database_path() -> Path | None:
    if not DATABASE_URL.startswith("sqlite"):
        return None
    db_name = make_url(DATABASE_URL).database
    if not db_name or db_name == ":memory:":
        return None
    return Path(db_name).resolve()


def ensure_path_writable(path: Path) -> None:
    if not path.exists():
        return
    try:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        LOGGER.exception("SQLite 파일 읽기 전용 속성 해제 실패: %s", path)


def assert_directory_writable(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    test_names = [
        ".scm_sqlite_write_test",
        ".scm_sqlite_write_test-journal",
        ".scm_sqlite_write_test-wal",
        ".scm_sqlite_write_test-shm",
    ]
    created = []
    try:
        for name in test_names:
            test_path = directory / name
            with test_path.open("wb") as handle:
                handle.write(b"ok")
            created.append(test_path)
    finally:
        for test_path in created:
            try:
                test_path.unlink()
            except OSError:
                LOGGER.warning("SQLite 쓰기 테스트 파일 삭제 실패: %s", test_path)


def sqlite_writability_report() -> dict:
    db_path = sqlite_database_path()
    report = {
        "database_url": DATABASE_URL,
        "raw_database_url": RAW_DATABASE_URL,
        "db_path": str(db_path or ""),
        "db_exists": bool(db_path and db_path.exists()),
        "db_file_writable": False,
        "db_dir": str(db_path.parent if db_path else ""),
        "db_dir_writable": False,
        "sqlite_writeable": False,
        "readonly_url_option": "mode=ro" in RAW_DATABASE_URL.lower(),
    }
    if db_path is None:
        return report
    ensure_path_writable(db_path)
    assert_directory_writable(db_path.parent)
    with db_path.open("ab"):
        pass
    report["db_exists"] = db_path.exists()
    report["db_file_writable"] = os.access(db_path, os.W_OK)
    report["db_dir_writable"] = os.access(db_path.parent, os.W_OK)
    report["sqlite_writeable"] = sqlite_write_probe(db_path)
    return report


def log_sqlite_writability(context: str = "") -> dict:
    report = sqlite_writability_report()
    LOGGER.warning(
        "SQLite write check%s | path=%s | file_writable=%s | dir=%s | dir_writable=%s | sqlite_writeable=%s | readonly_url_option=%s | url=%s",
        f" ({context})" if context else "",
        report.get("db_path"),
        report.get("db_file_writable"),
        report.get("db_dir"),
        report.get("db_dir_writable"),
        report.get("sqlite_writeable"),
        report.get("readonly_url_option"),
        report.get("database_url"),
    )
    return report


log_sqlite_writability("module import")

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
