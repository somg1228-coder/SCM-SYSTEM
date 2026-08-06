from __future__ import annotations

import importlib
import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def run_child(code: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    child_env = os.environ.copy()
    child_env.pop("SCM_DATABASE_URL", None)
    child_env.pop("SCM_ALLOW_SQLITE", None)
    child_env.pop("SCM_USE_SUPABASE_DB", None)
    if env:
        child_env.update(env)
    child_env.setdefault("SCM_IGNORE_DOTENV", "true")
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(PROJECT_ROOT),
        env=child_env,
        text=True,
        capture_output=True,
        timeout=30,
    )


def assert_ok(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} 실패: {detail}")
    print(f"[OK] {name}")


def test_sqlite_explicit_allow() -> None:
    result = run_child(
        "import backend.database as db; print(db.database_status()['engine'])",
        {"SCM_ALLOW_SQLITE": "true"},
    )
    assert_ok("SQLite 명시 허용 import", result.returncode == 0, result.stderr)
    assert_ok("SQLite 명시 허용 상태", "SQLite" in result.stdout, result.stdout)


def test_sqlite_default_local_mode() -> None:
    result = run_child("import backend.database")
    assert_ok("SQLite 기본 로컬 모드 import", result.returncode == 0, result.stderr)
    assert_ok("SQLite 기본 로컬 모드 상태", "SQLite" in result.stdout or result.returncode == 0, result.stdout)


def test_supabase_flag_string_true() -> None:
    result = run_child(
        "import backend.database as db; print(db.RAW_DATABASE_URL); print(db.use_supabase_as_app_database())",
        {
            "SCM_DATABASE_URL": "postgresql://u:p@aws-1-ap-south-1.pooler.supabase.com:5432/postgres",
            "SCM_USE_SUPABASE_DB": "true",
        },
    )
    assert_ok("SCM_USE_SUPABASE_DB 문자열 true", result.returncode == 0, result.stderr)
    assert_ok("문자열 true로 Supabase DB 사용", "True" in result.stdout, result.stdout)


def test_supabase_flag_boolean_secret_true() -> None:
    code = """
import sys
import types

streamlit = types.ModuleType("streamlit")
streamlit.secrets = {
    "SCM_DATABASE_URL": "postgresql://u:p@aws-1-ap-south-1.pooler.supabase.com:5432/postgres",
    "SCM_USE_SUPABASE_DB": True,
}
sys.modules["streamlit"] = streamlit

import backend.database as db
print(db.DATABASE_URL_SOURCE)
print(db.use_supabase_as_app_database())
"""
    result = run_child(code)
    assert_ok("Streamlit Secrets boolean true import", result.returncode == 0, result.stderr)
    assert_ok("boolean true로 Supabase DB 사용", "True" in result.stdout and "streamlit_secrets" in result.stdout, result.stdout)


def test_supabase_url_without_flag_uses_sqlite() -> None:
    result = run_child(
        "import backend.database as db; print(db.database_status()['engine']); print(db.database_status()['supabase_db_enabled'])",
        {"SCM_DATABASE_URL": "postgresql://u:p@aws-1-ap-south-1.pooler.supabase.com:5432/postgres"},
    )
    assert_ok("SCM_DATABASE_URL만 있을 때 SQLite 로컬 모드", result.returncode == 0, result.stdout + result.stderr)
    assert_ok("SCM_USE_SUPABASE_DB false", "False" in result.stdout and "SQLite" in result.stdout, result.stdout)


def test_supabase_true_without_url_blocked() -> None:
    result = run_child("import backend.database", {"SCM_USE_SUPABASE_DB": "true"})
    assert_ok("SCM_USE_SUPABASE_DB true에서 URL 누락 차단", result.returncode != 0, result.stdout + result.stderr)
    assert_ok("SCM_DATABASE_URL 안내", "SCM_DATABASE_URL" in (result.stderr + result.stdout), result.stderr + result.stdout)


def test_postgresql_url_normalization() -> None:
    os.environ["SCM_ALLOW_SQLITE"] = "true"
    db = importlib.import_module("backend.database")
    normalized = db.normalize_database_url(
        "postgresql://postgres.example_ref:pass%40word@aws-1-ap-south-1.pooler.supabase.com:5432/postgres"
    )
    assert_ok("PostgreSQL driver 정규화", normalized.startswith("postgresql+psycopg2://"), normalized)
    assert_ok("PostgreSQL sslmode 추가", "sslmode=require" in normalized, normalized)
    assert_ok("비밀번호 중복 인코딩 방지", "pass%2540word" not in normalized, normalized)
    assert_ok("명시 포트 유지", ":5432/" in normalized, normalized)


def test_engine_options() -> None:
    os.environ["SCM_ALLOW_SQLITE"] = "true"
    db = importlib.import_module("backend.database")
    url_5432 = db.normalize_database_url("postgresql://u:p@aws-1-ap-south-1.pooler.supabase.com:5432/postgres")
    opts_5432 = db.database_engine_options(url_5432)
    assert_ok("pool_pre_ping 설정", opts_5432.get("pool_pre_ping") is True, str(opts_5432))
    assert_ok("pool_recycle 설정", opts_5432.get("pool_recycle") == 300, str(opts_5432))
    assert_ok("connect_args ssl/connect_timeout", opts_5432.get("connect_args") == {"sslmode": "require", "connect_timeout": 10}, str(opts_5432))
    assert_ok("pool_size 과대 설정 방지", int(opts_5432.get("pool_size", 0)) <= 3, str(opts_5432))

    url_6543 = db.normalize_database_url("postgresql://u:p@aws-1-ap-south-1.pooler.supabase.com:6543/postgres")
    opts_6543 = db.database_engine_options(url_6543)
    assert_ok("transaction pooler NullPool", "poolclass" in opts_6543, str(opts_6543))


def main() -> int:
    test_sqlite_explicit_allow()
    test_sqlite_default_local_mode()
    test_supabase_flag_string_true()
    test_supabase_flag_boolean_secret_true()
    test_supabase_url_without_flag_uses_sqlite()
    test_supabase_true_without_url_blocked()
    test_postgresql_url_normalization()
    test_engine_options()
    print("DB 설정 단위 테스트 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
