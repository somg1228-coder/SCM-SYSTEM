from __future__ import annotations

import os
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def streamlit_secret_value(key: str) -> Any | None:
    try:
        import streamlit as st
    except Exception:
        return None
    try:
        return st.secrets.get(key, None)
    except Exception:
        return None


def load_local_env_file(env_path: str | Path | None = None) -> None:
    if os.getenv("SCM_IGNORE_DOTENV", "").strip().lower() in {"1", "true", "yes", "y", "on"}:
        return
    path = Path(env_path) if env_path else PROJECT_ROOT / ".env"
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
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


def config_text_value(key: str, *, env_path: str | Path | None = None) -> tuple[str, str]:
    secret_value = streamlit_secret_value(key)
    if secret_value is not None and str(secret_value).strip():
        return str(secret_value).strip(), "streamlit_secrets"

    env_value = os.getenv(key, "").strip()
    if env_value:
        return env_value, "environment"

    load_local_env_file(env_path)
    env_value = os.getenv(key, "").strip()
    if env_value:
        return env_value, "local_env"

    return "", "unset"


def truthy_config_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def config_bool_value(key: str, *, default: bool = False, env_path: str | Path | None = None) -> tuple[bool, str]:
    secret_value = streamlit_secret_value(key)
    if secret_value is not None:
        return truthy_config_value(secret_value), "streamlit_secrets"

    text, source = config_text_value(key, env_path=env_path)
    if source == "unset":
        return default, source
    return truthy_config_value(text), source


def is_deployed_environment() -> bool:
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
