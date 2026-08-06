from __future__ import annotations

import os
from pathlib import Path
import sys
import tomllib

from backend.supabase_diagnostics import IMPORTANT_TABLES, run_supabase_connection_diagnostics


PROJECT_ROOT = Path(__file__).resolve().parent


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


def main() -> int:
    load_local_config()
    result = run_supabase_connection_diagnostics(IMPORTANT_TABLES)
    if result.get("masked_url"):
        print(f"DB URL: {result['masked_url']}")
    if result.get("host"):
        print(
            f"Host: {result.get('host')}:{result.get('port') or '-'} / "
            f"Database: {result.get('database') or '-'} / User: {result.get('user') or '-'}"
        )
    if result.get("select_1_ok"):
        print(f"[OK] SELECT 1 = {result.get('select_1_result')}")
    else:
        print("[FAIL] SELECT 1 failed")

    for table, table_result in (result.get("tables") or {}).items():
        if table_result.get("exists"):
            print(f"[OK] {table}: {table_result.get('count'):,} rows")
        else:
            print(f"[FAIL] {table}: {table_result.get('error') or 'missing'}")

    if result.get("ok"):
        print("[OK] Supabase connection diagnostics passed")
        return 0
    print(f"[FAIL] {result.get('error') or 'Supabase connection diagnostics failed'}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
