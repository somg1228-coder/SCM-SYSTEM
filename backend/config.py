from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STREAMLIT_SECRET_SECTIONS = ("scm", "supabase", "database")
_LAST_STREAMLIT_SECRET_ERROR = ""


def streamlit_secret_value(key: str) -> Any | None:
    global _LAST_STREAMLIT_SECRET_ERROR
    _LAST_STREAMLIT_SECRET_ERROR = ""
    try:
        import streamlit as st
    except Exception as exc:
        _LAST_STREAMLIT_SECRET_ERROR = f"streamlit import failed: {type(exc).__name__}: {exc}"
        return None
    try:
        secrets = st.secrets
        if key in secrets:
            return secrets.get(key, None)
        key_lower = key.lower()
        for existing_key in secrets.keys():
            if str(existing_key).lower() == key_lower:
                return secrets.get(existing_key, None)
        for section_name in STREAMLIT_SECRET_SECTIONS:
            section = secrets.get(section_name, None)
            if not hasattr(section, "get"):
                continue
            if key in section:
                return section.get(key, None)
            for existing_key in section.keys():
                if str(existing_key).lower() == key_lower:
                    return section.get(existing_key, None)
    except Exception as exc:
        _LAST_STREAMLIT_SECRET_ERROR = f"st.secrets read failed: {type(exc).__name__}: {exc}"
        return None
    return None


def last_streamlit_secret_error() -> str:
    return _LAST_STREAMLIT_SECRET_ERROR


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
        return str(secret_value).strip(), "st.secrets"

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
        return truthy_config_value(secret_value), "st.secrets"

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
    path_text = str(PROJECT_ROOT).replace("\\", "/").lower()
    if path_text.startswith("/mount/src/") or "/mount/src/" in path_text:
        return True
    return False


def local_env_value(key: str, *, env_path: str | Path | None = None) -> str:
    path = Path(env_path) if env_path else PROJECT_ROOT / ".env"
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        item_key, value = text.split("=", 1)
        if item_key.strip() == key:
            return value.strip().strip("\"'")
    return ""


def mask_secret_text(key: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    key_upper = key.upper()
    if "DATABASE_URL" in key_upper or text.startswith(("postgres://", "postgresql://", "postgresql+")):
        return masked_database_url(text)
    if "KEY" in key_upper or "PASSWORD" in key_upper or "SECRET" in key_upper:
        if len(text) <= 8:
            return "***"
        return f"{text[:4]}...{text[-4:]}"
    return text


def masked_database_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text.replace("postgresql+psycopg2://", "postgresql://", 1)
    try:
        parts = urlsplit(normalized)
    except Exception:
        return "***invalid-url***"
    if not parts.netloc:
        return "***invalid-url***"
    hostinfo = parts.netloc.rsplit("@", 1)[-1]
    username = ""
    if "@" in parts.netloc:
        userinfo = parts.netloc.rsplit("@", 1)[0]
        username = userinfo.split(":", 1)[0]
    safe_netloc = f"{username}:***@{hostinfo}" if username else hostinfo
    return urlunsplit((parts.scheme, safe_netloc, parts.path, parts.query, parts.fragment))


def config_key_diagnostics(key: str) -> dict[str, Any]:
    secret_value = streamlit_secret_value(key)
    env_value = os.getenv(key, "")
    local_value = local_env_value(key)
    selected_value, selected_source = config_text_value(key)
    return {
        "key": key,
        "selected_source": selected_source,
        "selected_present": bool(selected_value),
        "selected_masked": mask_secret_text(key, selected_value),
        "st_secrets_present": secret_value is not None and str(secret_value).strip() != "",
        "st_secrets_type": type(secret_value).__name__ if secret_value is not None else "",
        "st_secrets_masked": mask_secret_text(key, secret_value),
        "environment_present": bool(str(env_value).strip()),
        "environment_masked": mask_secret_text(key, env_value),
        "local_env_present": bool(str(local_value).strip()),
        "local_env_masked": mask_secret_text(key, local_value),
        "streamlit_secret_error": last_streamlit_secret_error(),
    }
