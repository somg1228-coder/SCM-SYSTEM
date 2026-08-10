from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
import traceback

from backend.config import config_bool_value, config_key_diagnostics, config_text_value, is_deployed_environment, streamlit_secret_value, truthy_config_value

try:
    from sqlalchemy import create_engine, event, inspect
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import declarative_base, sessionmaker
    from sqlalchemy.schema import CreateColumn
except ModuleNotFoundError as exc:
    raise RuntimeError("sqlalchemy가 설치되어 있지 않습니다. `pip install -r requirements.txt` 후 다시 실행해주세요.") from exc


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
LOGGER = logging.getLogger("scm.database")
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError as exc:
    LOGGER.warning("기본 data 폴더를 만들 수 없습니다. 쓰기 가능한 대체 SQLite 경로를 사용합니다: %s", exc)

try:
    LOG_PATH = DATA_DIR / "scm_database.log"
    if not any(isinstance(handler, logging.FileHandler) and getattr(handler, "baseFilename", "") == str(LOG_PATH) for handler in LOGGER.handlers):
        file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOGGER.addHandler(file_handler)
    LOGGER.setLevel(logging.INFO)
except OSError:
    pass

DEFAULT_DB_PATH = (DATA_DIR / "scm.db").resolve()
DATABASE_URL_SOURCE = "unset"
SUPABASE_DATABASE_URL = ""
SUPABASE_DB_ENABLED = False
SUPABASE_DB_ENABLED_SOURCE = "unset"
_LAST_SELECT_1_OK = False
_LAST_SCHEMA_INIT_OK = False
_LAST_DB_STAGE = ""
_LAST_DB_ERROR = ""
_LAST_SAVE_SUCCESS_AT = ""
_LAST_SAVE_FAILURE_ITEM = ""
_LAST_SELECT_1_CHECK_AT = 0.0
_SELECT_1_TTL_SECONDS = 60.0
_INIT_DB_DONE = False
_INIT_DB_PROFILE: dict[str, float] = {}
_QUERY_PROFILE_MAX_EVENTS = 240
_QUERY_PROFILER_INSTALLED_ENGINE_IDS: set[int] = set()
_PAGE_PROFILE: dict[str, object] = {"page": "", "started_at": 0.0}


def _streamlit_cache_resource():
    try:
        import streamlit as st

        return st.cache_resource
    except Exception:
        return None


def _legacy_load_local_env_file() -> None:
    if os.getenv("SCM_IGNORE_DOTENV", "").strip().lower() in {"1", "true", "yes", "y"}:
        return
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        LOGGER.warning(".env 파일을 읽지 못했습니다: %s", exc)
        return
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def _legacy_streamlit_secret_value(key: str):
    try:
        import streamlit as st
    except Exception:
        return None
    try:
        return st.secrets.get(key, None)
    except Exception:
        return None


def _legacy_config_text_value(key: str) -> tuple[str, str]:
    secret_value = streamlit_secret_value(key)
    if secret_value is not None and str(secret_value).strip():
        return str(secret_value).strip(), "streamlit_secrets"

    env_value = os.getenv(key, "").strip()
    if env_value:
        return env_value, "environment"

    load_local_env_file()
    env_value = os.getenv(key, "").strip()
    if env_value:
        return env_value, "environment"

    return "", "unset"


def _legacy_truthy_config_value(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def load_streamlit_secrets_to_env() -> None:
    for key in ("SCM_DATABASE_URL", "SCM_USE_SUPABASE_DB"):
        value = streamlit_secret_value(key)
        if value is not None and str(value).strip() and key not in os.environ:
            os.environ[key] = str(value).strip()


def streamlit_secret_database_url() -> str:
    return config_text_value("SCM_DATABASE_URL")[0]


def env_database_url() -> str:
    return os.getenv("SCM_DATABASE_URL", "").strip()


def _legacy_is_deployed_environment() -> bool:
    cloud_markers = (
        "STREAMLIT_CLOUD",
        "STREAMLIT_SHARING_MODE",
        "STREAMLIT_RUNTIME_ENV",
        "STREAMLIT_SERVER_PORT",
        "K_SERVICE",
        "DYNO",
        "RENDER",
    )
    for key in cloud_markers:
        value = os.getenv(key, "").strip().lower()
        if value and value not in {"0", "false", "local", "development"}:
            return True
    return False


def sqlite_explicitly_allowed() -> bool:
    value, _ = config_text_value("SCM_ALLOW_SQLITE")
    return truthy_config_value(value)


def _unused_legacy_configured_database_url() -> str:
    global DATABASE_URL_SOURCE

    secret_url = streamlit_secret_database_url()
    if secret_url:
        os.environ["SCM_DATABASE_URL"] = secret_url
        DATABASE_URL_SOURCE = "streamlit_secrets"
        return secret_url

    env_url = env_database_url()
    if env_url:
        DATABASE_URL_SOURCE = "environment"
        return env_url

    load_local_env_file()
    env_url = env_database_url()
    if env_url:
        DATABASE_URL_SOURCE = "environment"
        return env_url

    if not sqlite_explicitly_allowed():
        location = "배포 환경" if is_deployed_environment() else "로컬 환경"
        raise RuntimeError(
            f"{location}에서 SCM_DATABASE_URL이 설정되지 않았습니다. "
            "Supabase PostgreSQL 저장을 사용하려면 Streamlit Secrets 또는 환경변수에 SCM_DATABASE_URL을 등록하세요. "
            "로컬 SQLite fallback은 SCM_ALLOW_SQLITE=true를 명시한 경우에만 허용됩니다."
        )

    DATABASE_URL_SOURCE = "sqlite_fallback_explicit"
    return f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"


def is_sqlite_url(raw_url: str) -> bool:
    try:
        return make_url(raw_url).drivername.startswith("sqlite")
    except Exception:
        return raw_url.startswith("sqlite")


def is_postgresql_url(raw_url: str) -> bool:
    try:
        driver = make_url(raw_url).drivername
    except Exception:
        return raw_url.startswith(("postgres://", "postgresql://", "postgresql+"))
    return driver == "postgres" or driver.startswith("postgresql")


def supabase_database_url_config() -> str:
    global DATABASE_URL_SOURCE

    database_url, source = config_text_value("SCM_DATABASE_URL")
    if database_url:
        DATABASE_URL_SOURCE = source
        if source == "st.secrets":
            os.environ["SCM_DATABASE_URL"] = database_url
        return database_url

    DATABASE_URL_SOURCE = "unset"
    return ""


def supabase_database_enabled_config() -> tuple[bool, str]:
    return config_bool_value("SCM_USE_SUPABASE_DB", default=False)


def use_supabase_as_app_database() -> bool:
    return SUPABASE_DB_ENABLED


def configured_database_url() -> str:
    global DATABASE_URL_SOURCE, SUPABASE_DATABASE_URL, SUPABASE_DB_ENABLED, SUPABASE_DB_ENABLED_SOURCE

    SUPABASE_DATABASE_URL = supabase_database_url_config()
    SUPABASE_DB_ENABLED, SUPABASE_DB_ENABLED_SOURCE = supabase_database_enabled_config()
    if SUPABASE_DB_ENABLED:
        if not SUPABASE_DATABASE_URL:
            location = "deployed environment" if is_deployed_environment() else "local environment"
            raise RuntimeError(
                f"SCM_USE_SUPABASE_DB is true, but SCM_DATABASE_URL is not configured in the {location}. "
                "Add SCM_DATABASE_URL to Streamlit Secrets or os.environ."
            )
        return SUPABASE_DATABASE_URL

    if SUPABASE_DB_ENABLED_SOURCE == "unset" and (is_deployed_environment() or SUPABASE_DATABASE_URL):
        raise RuntimeError(
            "SCM_USE_SUPABASE_DB is not configured or was not readable. "
            f"SCM_DATABASE_URL source={DATABASE_URL_SOURCE}. "
            "Set SCM_USE_SUPABASE_DB=true in Streamlit Cloud Secrets to use Supabase, "
            "or set SCM_USE_SUPABASE_DB=false explicitly for local SQLite."
        )

    if SUPABASE_DATABASE_URL:
        DATABASE_URL_SOURCE = f"{DATABASE_URL_SOURCE}_status_only"
    else:
        DATABASE_URL_SOURCE = "sqlite_local"
    return f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"


RAW_DATABASE_URL = configured_database_url()
DATABASE_URL_FROM_CONFIG = bool(SUPABASE_DATABASE_URL)


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


def is_sqlite_lock_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def is_sqlite_recoverable_open_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    recoverable_fragments = (
        "readonly",
        "read-only",
        "attempt to write a readonly database",
        "unable to open database file",
        "database is locked",
        "database table is locked",
        "disk i/o error",
        "file is not a database",
        "database disk image is malformed",
    )
    return any(fragment in message for fragment in recoverable_fragments)


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


def runtime_data_dir_candidates() -> list[Path]:
    candidates = []
    env_dir = os.getenv("SCM_WRITABLE_DATA_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.extend(
        [
            Path.home() / ".scm_portal" / "data",
            Path(tempfile.gettempdir()) / "scm_portal_data",
        ]
    )
    return candidates


def writable_runtime_data_dir() -> Path:
    candidates = runtime_data_dir_candidates()
    for directory in candidates:
        try:
            resolved = directory.expanduser().resolve()
            test_sqlite_directory_writable(resolved)
            return resolved
        except OSError as exc:
            LOGGER.warning("SQLite 대체 폴더 쓰기 테스트 실패: %s (%s)", directory, exc)
    raise RuntimeError("SQLite DB를 저장할 쓰기 가능한 폴더를 찾지 못했습니다.")


def runtime_sqlite_paths(source_path: Path) -> list[Path]:
    paths = []
    for directory in runtime_data_dir_candidates():
        try:
            paths.append((directory.expanduser().resolve() / source_path.name).resolve())
        except OSError as exc:
            LOGGER.warning("SQLite 대체 DB 경로 확인 실패: %s (%s)", directory, exc)
    return paths


def newest_existing_sqlite_path(source_path: Path) -> Path:
    candidates = [source_path, *runtime_sqlite_paths(source_path)]
    existing = []
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.stat().st_size > 0:
                existing.append(candidate)
        except OSError as exc:
            LOGGER.warning("SQLite DB 후보 확인 실패: %s (%s)", candidate, exc)
    if not existing:
        return source_path
    return max(existing, key=lambda path: path.stat().st_mtime)


def restore_newer_runtime_sqlite(source_path: Path) -> Path:
    newest_path = newest_existing_sqlite_path(source_path)
    if newest_path.resolve() == source_path.resolve():
        return source_path
    try:
        source_mtime = source_path.stat().st_mtime if source_path.exists() else 0
        newest_mtime = newest_path.stat().st_mtime
    except OSError as exc:
        LOGGER.warning("SQLite DB 최신본 비교 실패: %s", exc)
        return newest_path
    if newest_mtime <= source_mtime:
        return source_path
    try:
        source_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(newest_path, source_path)
        make_file_writable(source_path)
        LOGGER.warning("더 최신 SQLite 런타임 DB를 기본 DB로 복구했습니다: %s -> %s", newest_path, source_path)
        return source_path
    except OSError as exc:
        LOGGER.warning("최신 SQLite 런타임 DB 복구 실패. 런타임 DB를 계속 사용합니다: %s -> %s (%s)", newest_path, source_path, exc)
        return newest_path


def copy_sqlite_to_writable_path(source_path: Path) -> Path:
    target_dir = writable_runtime_data_dir()
    target_path = (target_dir / source_path.name).resolve()
    if source_path.exists() and source_path.resolve() != target_path:
        if not target_path.exists() or source_path.stat().st_mtime > target_path.stat().st_mtime:
            try:
                shutil.copy2(source_path, target_path)
                LOGGER.warning("읽기 전용 SQLite DB를 쓰기 가능한 경로로 복사했습니다: %s -> %s", source_path, target_path)
            except OSError as exc:
                LOGGER.warning("SQLite DB 복사 실패. 쓰기 가능한 새 DB로 계속합니다: %s -> %s (%s)", source_path, target_path, exc)
    if not target_path.exists():
        target_path.touch()
    make_file_writable(target_path)
    return target_path


def normalize_database_url(raw_url: str) -> str:
    if not is_sqlite_url(raw_url):
        return normalize_postgresql_url(raw_url) if is_postgresql_url(raw_url) else raw_url
    url = make_url(raw_url)
    db_name = url.database
    if not db_name or db_name == ":memory:":
        return raw_url
    db_path = Path(db_name)
    if not db_path.is_absolute():
        db_path = (BASE_DIR / db_path).resolve()
    else:
        db_path = db_path.resolve()
    db_path = restore_newer_runtime_sqlite(db_path)
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


def normalize_postgresql_url(raw_url: str) -> str:
    url = make_url(raw_url)
    if url.drivername == "postgres":
        url = url.set(drivername="postgresql+psycopg2")
    elif url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg2")
    query = dict(url.query)
    if "sslmode" not in query:
        query["sslmode"] = "require"
        url = url.set(query=query)
    return url.render_as_string(hide_password=False)


DATABASE_URL = normalize_database_url(RAW_DATABASE_URL)


def database_connect_args(database_url: str) -> dict:
    if is_sqlite_url(database_url):
        return {"check_same_thread": False, "timeout": 15}
    if is_postgresql_url(database_url):
        return {
            "sslmode": "require",
            "connect_timeout": 5,
            "options": "-c statement_timeout=10000 -c idle_in_transaction_session_timeout=10000",
        }
    return {}


CONNECT_ARGS = database_connect_args(DATABASE_URL)


def database_engine_options(database_url: str) -> dict:
    options = {
        "connect_args": database_connect_args(database_url),
        "future": True,
        "pool_pre_ping": True,
    }
    if is_postgresql_url(database_url):
        options.update({"pool_recycle": 300})
        try:
            url = make_url(database_url)
        except Exception:
            url = None
        pool_size = 5 if url is not None and url.port == 6543 else 3
        max_overflow = 5 if url is not None and url.port == 6543 else 2
        options.update({"pool_size": pool_size, "max_overflow": max_overflow, "pool_timeout": 5})
    return options


ENGINE_OPTIONS = database_engine_options(DATABASE_URL)


_cache_resource = _streamlit_cache_resource()


def _create_uncached_engine(database_url: str):
    return create_engine(database_url, **database_engine_options(database_url))


if _cache_resource is not None:

    @_cache_resource(show_spinner=False)
    def create_cached_engine(database_url: str):
        return _create_uncached_engine(database_url)

else:

    def create_cached_engine(database_url: str):
        return _create_uncached_engine(database_url)


def create_app_engine(database_url: str):
    return create_cached_engine(database_url)


def supabase_transaction_pooler_url(database_url: str) -> str | None:
    if not is_postgresql_url(database_url):
        return None
    try:
        url = make_url(database_url)
    except Exception:
        return None
    host = url.host or ""
    if not host.endswith(".pooler.supabase.com") or url.port == 6543:
        return None
    return url.set(port=6543).render_as_string(hide_password=False)


def supabase_direct_database_url(database_url: str) -> str | None:
    if not is_postgresql_url(database_url):
        return None
    try:
        url = make_url(database_url)
    except Exception:
        return None
    host = url.host or ""
    username = url.username or ""
    project_ref = ""
    if username.startswith("postgres."):
        project_ref = username.split(".", 1)[1]
    if not project_ref or host == f"db.{project_ref}.supabase.co":
        return None
    return url.set(username="postgres", host=f"db.{project_ref}.supabase.co", port=5432).render_as_string(hide_password=False)


def supabase_connection_retry_urls(database_url: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    transaction_pooler = supabase_transaction_pooler_url(database_url)
    if transaction_pooler:
        candidates.append(("transaction_pooler_6543", transaction_pooler))
    direct_database = supabase_direct_database_url(database_url)
    if direct_database:
        candidates.append(("direct_database_5432", direct_database))
    return candidates


def switch_database_url(next_database_url: str) -> None:
    global DATABASE_URL, CONNECT_ARGS, ENGINE_OPTIONS, engine, SessionLocal

    DATABASE_URL = normalize_database_url(next_database_url)
    CONNECT_ARGS = database_connect_args(DATABASE_URL)
    ENGINE_OPTIONS = database_engine_options(DATABASE_URL)
    try:
        engine.dispose()
    except NameError:
        pass
    engine = create_app_engine(DATABASE_URL)
    install_query_profiler(engine)
    try:
        SessionLocal.configure(bind=engine)
    except NameError:
        pass


def sqlite_database_path() -> Path | None:
    if not is_sqlite_url(DATABASE_URL):
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
        "error": "",
    }
    if db_path is None:
        report.update(
            {
                "database_engine": database_engine_name(),
                "is_sqlite": False,
                "is_postgresql": is_postgresql_url(DATABASE_URL),
            }
        )
        return report
    try:
        ensure_path_writable(db_path)
        assert_directory_writable(db_path.parent)
        with db_path.open("ab"):
            pass
        report["db_exists"] = db_path.exists()
        report["db_file_writable"] = os.access(db_path, os.W_OK)
        report["db_dir_writable"] = os.access(db_path.parent, os.W_OK)
        report["sqlite_writeable"] = sqlite_write_probe(db_path)
    except OSError as exc:
        report["error"] = str(exc)
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


def database_engine_name() -> str:
    if is_postgresql_url(DATABASE_URL):
        return "Supabase PostgreSQL" if is_supabase_database_url(DATABASE_URL) else "PostgreSQL"
    if is_sqlite_url(DATABASE_URL):
        return "SQLite"
    try:
        return make_url(DATABASE_URL).drivername
    except Exception:
        return "unknown"


def is_supabase_database_url(raw_url: str) -> bool:
    if not is_postgresql_url(raw_url):
        return False
    try:
        host = (make_url(raw_url).host or "").lower()
    except Exception:
        host = raw_url.lower()
    return SUPABASE_DB_ENABLED or "supabase.co" in host or "supabase.com" in host or "pooler.supabase" in host


def masked_database_url() -> str:
    try:
        return make_url(DATABASE_URL).render_as_string(hide_password=True)
    except Exception:
        return ""


def sanitize_database_text(value: object) -> str:
    text = str(value)
    for candidate in {RAW_DATABASE_URL, DATABASE_URL}:
        if not candidate:
            continue
        try:
            masked = make_url(candidate).render_as_string(hide_password=True)
        except Exception:
            masked = "<masked database url>"
        text = text.replace(candidate, masked)
    try:
        password = make_url(DATABASE_URL).password
    except Exception:
        password = None
    if password:
        text = text.replace(str(password), "***")
    return text


def log_database_exception(stage: str, exc: BaseException) -> None:
    safe_repr = sanitize_database_text(repr(exc))
    safe_traceback = sanitize_database_text("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    LOGGER.error("%s 실패: %s", stage, safe_repr)
    print(safe_repr, flush=True)
    print(safe_traceback, file=sys.stderr, flush=True)


def test_database_connection(force: bool = False) -> bool:
    global _LAST_SELECT_1_OK, _LAST_DB_STAGE, _LAST_DB_ERROR, _LAST_SELECT_1_CHECK_AT

    now = time.monotonic()
    if _LAST_SELECT_1_OK and not force and now - _LAST_SELECT_1_CHECK_AT < _SELECT_1_TTL_SECONDS:
        return True
    _LAST_DB_STAGE = "select_1"
    started_at = time.perf_counter()
    LOGGER.info("DB SELECT 1 start force=%s engine=%s", force, database_engine_name())
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        _LAST_SELECT_1_OK = True
        _LAST_SELECT_1_CHECK_AT = time.monotonic()
        _LAST_DB_ERROR = ""
        LOGGER.info("DB SELECT 1 done seconds=%.3f", time.perf_counter() - started_at)
        return True
    except Exception as exc:
        if try_supabase_transaction_pooler_after_failure(exc):
            return True
        _LAST_SELECT_1_OK = False
        if not _LAST_DB_ERROR:
            _LAST_DB_ERROR = sanitize_database_text(repr(exc))
        LOGGER.info("DB SELECT 1 failed seconds=%.3f", time.perf_counter() - started_at)
        log_database_exception("DB SELECT 1 연결 테스트", exc)
        return False


def test_supabase_select_1() -> tuple[bool, str]:
    if not SUPABASE_DATABASE_URL:
        return False, "SCM_DATABASE_URL not configured"
    try:
        supabase_url = normalize_postgresql_url(SUPABASE_DATABASE_URL)
        options = database_engine_options(supabase_url)
        probe_engine = create_engine(supabase_url, **options)
        try:
            with probe_engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            return True, ""
        finally:
            probe_engine.dispose()
    except Exception as exc:
        return False, sanitize_database_text(repr(exc))


def try_supabase_transaction_pooler_after_failure(primary_exc: BaseException) -> bool:
    global _LAST_SELECT_1_OK, _LAST_DB_STAGE, _LAST_DB_ERROR

    retry_urls = supabase_connection_retry_urls(DATABASE_URL)
    if not retry_urls:
        return False
    errors = ["primary=" + sanitize_database_text(repr(primary_exc))]
    for label, retry_url in retry_urls:
        try:
            LOGGER.warning(
                "Supabase SELECT 1 failed; retrying %s. primary=%s",
                label,
                sanitize_database_text(repr(primary_exc)),
            )
            switch_database_url(retry_url)
            with engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            _LAST_SELECT_1_OK = True
            _LAST_DB_STAGE = f"select_1_{label}"
            _LAST_DB_ERROR = ""
            return True
        except Exception as retry_exc:
            errors.append(f"{label}=" + sanitize_database_text(repr(retry_exc)))
            _LAST_SELECT_1_OK = False
            _LAST_DB_STAGE = f"select_1_{label}"
            log_database_exception(f"Supabase {label} 연결 테스트", retry_exc)
    _LAST_DB_ERROR = " / ".join(errors)
    return False


def database_status() -> dict:
    url = make_url(DATABASE_URL)
    supabase_ok, supabase_error = test_supabase_select_1()
    status = {
        "configured": DATABASE_URL_FROM_CONFIG,
        "url_source": DATABASE_URL_SOURCE,
        "engine": database_engine_name(),
        "app_database_engine": database_engine_name(),
        "supabase_configured": bool(SUPABASE_DATABASE_URL),
        "supabase_db_enabled": use_supabase_as_app_database(),
        "supabase_select_1_ok": supabase_ok,
        "supabase_last_error": supabase_error,
        "is_sqlite": is_sqlite_url(DATABASE_URL),
        "is_postgresql": is_postgresql_url(DATABASE_URL),
        "is_supabase_postgresql": is_supabase_database_url(DATABASE_URL),
        "display_url": masked_database_url(),
        "host": url.host or "",
        "port": url.port or "",
        "database": url.database or "",
        "connected": False,
        "select_1_ok": False,
        "schema_initialized": _LAST_SCHEMA_INIT_OK,
        "last_stage": _LAST_DB_STAGE,
        "last_error": _LAST_DB_ERROR,
        "last_save_success_at": _LAST_SAVE_SUCCESS_AT,
        "last_save_failure_item": _LAST_SAVE_FAILURE_ITEM,
        "message": "",
    }
    if test_database_connection():
        status["connected"] = True
        status["select_1_ok"] = True
        if status["is_supabase_postgresql"]:
            status["message"] = "데이터베이스: Supabase PostgreSQL 연결됨"
        elif status["is_postgresql"]:
            status["message"] = "데이터베이스: PostgreSQL 연결됨"
        else:
            status["message"] = "데이터베이스: 로컬 SQLite 사용 중"
    else:
        status["message"] = f"데이터베이스 SELECT 1 실패: {_LAST_DB_ERROR}"
    return status


def database_status(include_live_checks: bool = False, include_config_diagnostics: bool = False) -> dict:
    url = make_url(DATABASE_URL)
    if include_live_checks:
        supabase_ok, supabase_error = test_supabase_select_1()
    else:
        supabase_ok, supabase_error = _LAST_SELECT_1_OK, ""
    config_diagnostics = {}
    if include_config_diagnostics:
        config_diagnostics = {
            key: config_key_diagnostics(key)
            for key in ("SCM_USE_SUPABASE_DB", "SCM_DATABASE_URL", "SUPABASE_URL", "SUPABASE_KEY")
        }
    status = {
        "configured": DATABASE_URL_FROM_CONFIG,
        "url_source": DATABASE_URL_SOURCE,
        "engine": database_engine_name(),
        "app_database_engine": database_engine_name(),
        "selected_database": database_engine_name(),
        "supabase_configured": bool(SUPABASE_DATABASE_URL),
        "supabase_db_enabled": SUPABASE_DB_ENABLED,
        "supabase_db_enabled_source": SUPABASE_DB_ENABLED_SOURCE,
        "supabase_select_1_ok": supabase_ok,
        "supabase_last_error": supabase_error,
        "is_sqlite": is_sqlite_url(DATABASE_URL),
        "is_postgresql": is_postgresql_url(DATABASE_URL),
        "is_supabase_postgresql": is_supabase_database_url(DATABASE_URL),
        "display_url": masked_database_url(),
        "host": url.host or "",
        "port": url.port or "",
        "database": url.database or "",
        "connected": False,
        "select_1_ok": False,
        "schema_initialized": _LAST_SCHEMA_INIT_OK,
        "last_stage": _LAST_DB_STAGE,
        "last_error": _LAST_DB_ERROR,
        "last_save_success_at": _LAST_SAVE_SUCCESS_AT,
        "last_save_failure_item": _LAST_SAVE_FAILURE_ITEM,
        "init_profile": dict(_INIT_DB_PROFILE),
        "config_diagnostics": config_diagnostics,
        "message": "",
    }
    connected = False
    if include_live_checks:
        connected = test_database_connection(force=True)
    elif _LAST_SELECT_1_OK:
        connected = True

    if connected:
        status["connected"] = True
        status["select_1_ok"] = True
        if status["is_supabase_postgresql"]:
            status["message"] = "Database: Supabase PostgreSQL connected"
        elif status["is_postgresql"]:
            status["message"] = "Database: PostgreSQL connected"
        else:
            status["message"] = "Database: local SQLite in use"
    elif not include_live_checks:
        status["message"] = "Database check pending; live diagnostics run from Admin."
    else:
        status["message"] = f"Database SELECT 1 failed: {_LAST_DB_ERROR}"
    return status


def record_save_success(item: str) -> None:
    global _LAST_SAVE_SUCCESS_AT, _LAST_SAVE_FAILURE_ITEM

    _LAST_SAVE_SUCCESS_AT = datetime_now_iso()
    _LAST_SAVE_FAILURE_ITEM = ""
    LOGGER.info("DB save success: %s", sanitize_database_text(item))


def record_save_failure(item: str, exc: BaseException | None = None) -> None:
    global _LAST_SAVE_FAILURE_ITEM

    _LAST_SAVE_FAILURE_ITEM = sanitize_database_text(item)
    if exc is not None:
        log_database_exception(f"DB save failed: {item}", exc)


def datetime_now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


def clear_streamlit_data_cache() -> None:
    try:
        import streamlit as st

        st.cache_data.clear()
    except Exception:
        pass


def begin_page_query_profile(page: str) -> None:
    _PAGE_PROFILE["page"] = str(page or "")
    _PAGE_PROFILE["started_at"] = time.perf_counter()
    try:
        import streamlit as st

        st.session_state["db_query_profile_events"] = []
        st.session_state["db_query_profile_page"] = str(page or "")
    except Exception:
        pass


def finish_page_query_profile(page: str, render_seconds: float) -> None:
    try:
        import streamlit as st

        events = list(st.session_state.get("db_query_profile_events", []))
    except Exception:
        events = []
    call_count = len(events)
    db_seconds = sum(float(item.get("elapsed_seconds", 0.0) or 0.0) for item in events)
    slowest = sorted(events, key=lambda item: float(item.get("elapsed_seconds", 0.0) or 0.0), reverse=True)[:10]
    summary = {
        "page": page,
        "api_calls": call_count,
        "db_seconds": round(db_seconds, 3),
        "render_seconds": round(render_seconds, 3),
        "slowest": slowest,
    }
    try:
        import streamlit as st

        st.session_state["db_query_profile_summary"] = summary
    except Exception:
        pass
    LOGGER.info(
        "page_profile page=%s api_calls=%s db_seconds=%.3f render_seconds=%.3f",
        page,
        call_count,
        db_seconds,
        render_seconds,
    )


def install_query_profiler(target_engine) -> None:
    engine_id = id(target_engine)
    if engine_id in _QUERY_PROFILER_INSTALLED_ENGINE_IDS:
        return
    _QUERY_PROFILER_INSTALLED_ENGINE_IDS.add(engine_id)

    @event.listens_for(target_engine, "before_cursor_execute")
    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        context._scm_query_started_at = time.perf_counter()
        operation, table_name = describe_sql_statement(statement)
        try:
            from backend.perf import log_perf

            log_perf(f"sqlalchemy_execute START page={_PAGE_PROFILE.get('page') or ''} table={table_name} operation={operation}")
        except Exception:
            pass

    @event.listens_for(target_engine, "after_cursor_execute")
    def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        started_at = getattr(context, "_scm_query_started_at", None)
        elapsed = time.perf_counter() - started_at if started_at else 0.0
        record_query_profile(statement, elapsed, True, "")

    @event.listens_for(target_engine, "handle_error")
    def _handle_error(exception_context):
        started_at = getattr(exception_context.execution_context, "_scm_query_started_at", None)
        elapsed = time.perf_counter() - started_at if started_at else 0.0
        record_query_profile(
            str(exception_context.statement or ""),
            elapsed,
            False,
            sanitize_database_text(exception_context.original_exception),
        )


def record_query_profile(statement: str, elapsed_seconds: float, success: bool, error: str) -> None:
    operation, table_name = describe_sql_statement(statement)
    page = str(_PAGE_PROFILE.get("page") or "")
    if not page:
        try:
            import streamlit as st

            page = str(st.session_state.get("current_page") or st.session_state.get("selected_menu") or "")
        except Exception:
            page = ""
    event_row = {
        "page": page,
        "function": "sqlalchemy",
        "table": table_name,
        "operation": operation,
        "elapsed_seconds": round(float(elapsed_seconds or 0.0), 4),
        "success": bool(success),
        "error": error,
    }
    try:
        import streamlit as st

        rows = list(st.session_state.get("db_query_profile_events", []))
        rows.append(event_row)
        st.session_state["db_query_profile_events"] = rows[-_QUERY_PROFILE_MAX_EVENTS:]
    except Exception:
        pass
    try:
        from backend.perf import record_perf_event

        record_perf_event(
            "sqlalchemy_execute",
            elapsed_seconds,
            page=page,
            table=table_name,
            operation=operation,
            success=success,
            error=error,
        )
    except Exception:
        pass
    if not success or float(elapsed_seconds or 0.0) >= 0.05:
        LOGGER.info(
            "query_profile page=%s operation=%s table=%s elapsed=%.4f success=%s",
            page,
            operation,
            table_name,
            elapsed_seconds,
            success,
        )


def describe_sql_statement(statement: str) -> tuple[str, str]:
    text = " ".join(str(statement or "").strip().split())
    if not text:
        return "", ""
    operation = text.split(" ", 1)[0].upper()
    table_name = ""
    patterns = (
        r"\bFROM\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)",
        r"\bJOIN\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)",
        r"\bINTO\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)",
        r"\bUPDATE\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)",
        r"\bDELETE\s+FROM\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            table_name = match.group(1).strip('"')
            break
    return operation, table_name


try:
    if is_sqlite_url(DATABASE_URL):
        log_sqlite_writability("module import")
except Exception as exc:
    LOGGER.warning("SQLite write check skipped during import: %s", exc)

engine = create_app_engine(DATABASE_URL)
install_query_profiler(engine)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def init_db(force: bool = False, ensure_schema: bool | None = None) -> None:
    global _LAST_SCHEMA_INIT_OK, _LAST_DB_STAGE, _LAST_DB_ERROR, _INIT_DB_DONE, _INIT_DB_PROFILE

    if _INIT_DB_DONE and not force:
        return
    started_at = time.perf_counter()
    from backend import models  # noqa: F401

    select_started_at = time.perf_counter()
    if not test_database_connection(force=force):
        if is_postgresql_url(DATABASE_URL):
            raise RuntimeError(
                "Supabase PostgreSQL SELECT 1 failed. "
                "Check SCM_DATABASE_URL host, port, user, password, and sslmode. "
                f"Last error: {_LAST_DB_ERROR}"
            )
        raise RuntimeError(f"DB SELECT 1 failed: {_LAST_DB_ERROR}")
    select_elapsed = time.perf_counter() - select_started_at

    if ensure_schema is None:
        ensure_schema = is_sqlite_url(DATABASE_URL)
    if not ensure_schema:
        _LAST_SCHEMA_INIT_OK = True
        _LAST_DB_ERROR = ""
        _INIT_DB_DONE = True
        _INIT_DB_PROFILE = {
            "select_1_seconds": select_elapsed,
            "schema_seconds": 0.0,
            "total_seconds": time.perf_counter() - started_at,
        }
        LOGGER.info("init_db done without schema seconds=%.3f", _INIT_DB_PROFILE["total_seconds"])
        return

    if is_sqlite_url(DATABASE_URL):
        try:
            schema_started_at = time.perf_counter()
            _LAST_DB_STAGE = "sqlite_schema_init"
            repair_sqlite_schema()
            Base.metadata.create_all(bind=engine)
            ensure_sqlite_columns()
            _LAST_SCHEMA_INIT_OK = True
            _LAST_DB_ERROR = ""
            _INIT_DB_DONE = True
            _INIT_DB_PROFILE = {
                "select_1_seconds": select_elapsed,
                "schema_seconds": time.perf_counter() - schema_started_at,
                "total_seconds": time.perf_counter() - started_at,
            }
        except Exception as exc:
            _LAST_SCHEMA_INIT_OK = False
            _LAST_DB_STAGE = "sqlite_schema_init"
            _LAST_DB_ERROR = sanitize_database_text(repr(exc))
            log_database_exception("SQLite DB 초기화", exc)
            if not is_sqlite_recoverable_open_error(exc):
                raise
            LOGGER.exception("SQLite DB 초기화 실패. 런타임 DB 경로로 전환해 재시도합니다: %s", exc)
            switch_to_runtime_sqlite_copy()
            repair_sqlite_schema()
            Base.metadata.create_all(bind=engine)
            ensure_sqlite_columns()
            _LAST_SCHEMA_INIT_OK = True
            _LAST_DB_ERROR = ""
            _INIT_DB_DONE = True
        return

    try:
        schema_started_at = time.perf_counter()
        _LAST_DB_STAGE = "postgres_schema_init"
        Base.metadata.create_all(bind=engine)
        ensure_postgresql_columns()
        _LAST_SCHEMA_INIT_OK = True
        _LAST_DB_ERROR = ""
        _INIT_DB_DONE = True
        _INIT_DB_PROFILE = {
            "select_1_seconds": select_elapsed,
            "schema_seconds": time.perf_counter() - schema_started_at,
            "total_seconds": time.perf_counter() - started_at,
        }
    except Exception as exc:
        _LAST_SCHEMA_INIT_OK = False
        _LAST_DB_STAGE = "postgres_schema_init"
        _LAST_DB_ERROR = sanitize_database_text(repr(exc))
        log_database_exception("Supabase PostgreSQL 스키마 초기화", exc)
        raise RuntimeError(
            "Supabase PostgreSQL 스키마 초기화(create_all)에 실패했습니다. "
            "SELECT 1은 성공했지만 테이블 생성/컬럼 확인 단계에서 실패했습니다."
        ) from exc


def switch_to_runtime_sqlite_copy() -> None:
    global DATABASE_URL, CONNECT_ARGS, ENGINE_OPTIONS, engine, SessionLocal

    db_path = sqlite_database_path() or DEFAULT_DB_PATH
    runtime_path = copy_sqlite_to_writable_path(db_path)
    DATABASE_URL = f"sqlite:///{runtime_path.as_posix()}"
    CONNECT_ARGS = database_connect_args(DATABASE_URL)
    ENGINE_OPTIONS = database_engine_options(DATABASE_URL)
    engine.dispose()
    engine = create_app_engine(DATABASE_URL)
    SessionLocal.configure(bind=engine)


def reset_sqlite_engine_after_write_error(exc: BaseException) -> bool:
    if not is_sqlite_url(DATABASE_URL) or not is_sqlite_recoverable_open_error(exc):
        return False
    LOGGER.warning("SQLite 쓰기 오류 감지. 연결을 재설정하고 1회 재시도합니다: %s", exc)
    try:
        report = sqlite_writability_report()
        if report.get("db_path") and not report.get("sqlite_writeable"):
            switch_to_runtime_sqlite_copy()
        else:
            engine.dispose()
    except Exception as reset_exc:
        LOGGER.warning("SQLite 연결 재설정 중 추가 오류: %s", reset_exc)
        engine.dispose()
    return True


def repair_sqlite_schema() -> None:
    if not is_sqlite_url(DATABASE_URL):
        return

    known_stale_indexes = ("ix_purchase_documents_created_at",)
    with engine.begin() as conn:
        restore_incomplete_product_master_rebuilds(conn)
        for index_name in known_stale_indexes:
            drop_sqlite_index(conn, index_name)
        drop_orphan_sqlite_indexes(conn)
        conn.exec_driver_sql("PRAGMA writable_schema = OFF")


def restore_incomplete_product_master_rebuilds(conn) -> None:
    for table_name in ("offline_product_master", "thirdparty_product_master", "warehouse_product_master"):
        old_table = f"{table_name}_old_barcode_unique"
        new_table = f"{table_name}_constraint_rebuild"
        table_exists = sqlite_table_exists(conn, table_name)
        old_exists = sqlite_table_exists(conn, old_table)
        if not table_exists and old_exists:
            conn.exec_driver_sql(
                f"ALTER TABLE {quote_sqlite_identifier(old_table)} RENAME TO {quote_sqlite_identifier(table_name)}"
            )
            LOGGER.warning("중단된 상품 마스터 테이블 보정을 복구했습니다: %s", table_name)
        if sqlite_table_exists(conn, new_table):
            conn.exec_driver_sql(f"DROP TABLE IF EXISTS {quote_sqlite_identifier(new_table)}")


def sqlite_table_exists(conn, table_name: str) -> bool:
    row = conn.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return bool(row)


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
    if not is_sqlite_url(DATABASE_URL):
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
            "supplier_code": "VARCHAR(40) NOT NULL DEFAULT ''",
            "business_number": "VARCHAR(80) NOT NULL DEFAULT ''",
            "handled_items": "VARCHAR(500) NOT NULL DEFAULT ''",
            "moq_terms": "VARCHAR(500) NOT NULL DEFAULT ''",
            "transaction_status": "VARCHAR(40) NOT NULL DEFAULT '거래중'",
            "current_grade": "VARCHAR(20) NOT NULL DEFAULT '미평가'",
            "latest_score": "FLOAT NOT NULL DEFAULT 0",
            "latest_evaluation_date": "DATE",
            "next_evaluation_date": "DATE",
            "special_management": "BOOLEAN NOT NULL DEFAULT 0",
            "special_reason": "VARCHAR(500) NOT NULL DEFAULT ''",
            "avg_unit_price_currency": "VARCHAR(10) NOT NULL DEFAULT 'KRW'",
            "payment_terms": "VARCHAR(120) NOT NULL DEFAULT ''",
        },
        "supplier_evaluations": {
            "next_evaluation_date": "DATE",
            "special_warning": "BOOLEAN NOT NULL DEFAULT 0",
            "applicable_weight": "FLOAT NOT NULL DEFAULT 0",
            "earned_score": "FLOAT NOT NULL DEFAULT 0",
            "base_grade": "VARCHAR(20) NOT NULL DEFAULT '미평가'",
            "grade_limit_reason": "VARCHAR(500) NOT NULL DEFAULT ''",
            "special_reasons": "TEXT NOT NULL DEFAULT ''",
            "excellent_points": "TEXT NOT NULL DEFAULT ''",
            "problem_points": "TEXT NOT NULL DEFAULT ''",
            "improvement_owner": "VARCHAR(120) NOT NULL DEFAULT ''",
            "improvement_status": "VARCHAR(40) NOT NULL DEFAULT '해당 없음'",
            "attachment_ref": "VARCHAR(500) NOT NULL DEFAULT ''",
            "internal_memo": "TEXT NOT NULL DEFAULT ''",
            "rejection_reason": "TEXT NOT NULL DEFAULT ''",
            "is_deleted": "BOOLEAN NOT NULL DEFAULT 0",
            "inactive_reason": "TEXT NOT NULL DEFAULT ''",
            "inactive_at": "DATETIME",
            "created_by": "VARCHAR(120) NOT NULL DEFAULT ''",
            "updated_by": "VARCHAR(120) NOT NULL DEFAULT ''",
        },
        "supplier_evaluation_criteria": {
            "category_order": "INTEGER NOT NULL DEFAULT 0",
            "item_order": "INTEGER NOT NULL DEFAULT 0",
            "item_description": "VARCHAR(500) NOT NULL DEFAULT ''",
            "is_required": "BOOLEAN NOT NULL DEFAULT 1",
        },
        "supplier_evaluation_history": {
            "change_reason": "VARCHAR(500) NOT NULL DEFAULT ''",
        },
    }

    with engine.begin() as conn:
        for table_name, columns in column_specs.items():
            if not sqlite_table_exists(conn, table_name):
                LOGGER.warning("SQLite column migration skipped missing table: %s", table_name)
                continue
            quoted_table = quote_sqlite_identifier(table_name)
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({quoted_table})")}
            for column_name, ddl in columns.items():
                if column_name not in existing:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {quoted_table} ADD COLUMN {quote_sqlite_identifier(column_name)} {ddl}"
                    )
        ensure_product_master_barcode_constraints(conn)


def ensure_postgresql_columns() -> None:
    if not is_postgresql_url(DATABASE_URL):
        return
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                column_sql = str(CreateColumn(column).compile(dialect=engine.dialect))
                if column.server_default is None:
                    column_sql = column_sql.replace(" NOT NULL", "")
                conn.exec_driver_sql(f'ALTER TABLE "{table.name}" ADD COLUMN {column_sql}')


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
        if not product_master_table_can_be_rebuilt(conn, table_name, keep_barcode_product_unique):
            continue
        try:
            rebuild_product_master_table(conn, table_name, prefix, keep_barcode_product_unique)
        except Exception as exc:
            LOGGER.exception("상품 마스터 테이블 제약 보정 실패. 기존 테이블은 유지합니다: %s (%s)", table_name, exc)


def product_master_table_can_be_rebuilt(conn, table_name: str, keep_barcode_product_unique: bool) -> bool:
    duplicate_sku = conn.exec_driver_sql(
        f"""
        SELECT sku, COUNT(*)
        FROM {quote_sqlite_identifier(table_name)}
        GROUP BY sku
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    ).fetchone()
    if duplicate_sku:
        LOGGER.warning("SKU 중복이 있어 상품 마스터 제약 보정을 건너뜁니다: %s / %s", table_name, duplicate_sku[0])
        return False
    if keep_barcode_product_unique:
        duplicate_barcode_product = conn.exec_driver_sql(
            f"""
            SELECT barcode, product_name, COUNT(*)
            FROM {quote_sqlite_identifier(table_name)}
            GROUP BY barcode, product_name
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        ).fetchone()
        if duplicate_barcode_product:
            LOGGER.warning(
                "바코드/상품명 중복이 있어 상품 마스터 제약 보정을 건너뜁니다: %s / %s / %s",
                table_name,
                duplicate_barcode_product[0],
                duplicate_barcode_product[1],
            )
            return False
    return True


def rebuild_product_master_table(conn, table_name: str, prefix: str, keep_barcode_product_unique: bool = True) -> None:
    old_table = f"{table_name}_old_barcode_unique"
    new_table = f"{table_name}_constraint_rebuild"
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
    quoted_table = quote_sqlite_identifier(table_name)
    quoted_old_table = quote_sqlite_identifier(old_table)
    quoted_new_table = quote_sqlite_identifier(new_table)

    conn.exec_driver_sql(f"DROP TABLE IF EXISTS {quoted_new_table}")
    barcode_constraint = (
        f"CONSTRAINT uq_{prefix}_product_master_barcode_product_name UNIQUE (barcode, product_name),"
        if keep_barcode_product_unique
        else ""
    )
    conn.exec_driver_sql(
        f"""
        CREATE TABLE {quoted_new_table} (
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
    conn.exec_driver_sql(f"INSERT INTO {quoted_new_table} ({column_sql}) SELECT {column_sql} FROM {quoted_table}")
    conn.exec_driver_sql(f"DROP TABLE IF EXISTS {quoted_old_table}")
    conn.exec_driver_sql(f"ALTER TABLE {quoted_table} RENAME TO {quoted_old_table}")
    conn.exec_driver_sql(f"ALTER TABLE {quoted_new_table} RENAME TO {quoted_table}")
    conn.exec_driver_sql(f"DROP TABLE {quoted_old_table}")
    conn.exec_driver_sql(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_id ON {quoted_table} (id)")
    conn.exec_driver_sql(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_sku ON {quoted_table} (sku)")
    conn.exec_driver_sql(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_barcode ON {quoted_table} (barcode)")
    conn.exec_driver_sql(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_product_name ON {quoted_table} (product_name)")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
