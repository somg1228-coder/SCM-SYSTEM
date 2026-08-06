from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

import streamlit as st


@dataclass(frozen=True)
class SupabaseStatus:
    configured: bool
    connected: bool
    message: str
    source: str = ""


def config_text_value(key: str) -> tuple[str, str]:
    try:
        secret_value = st.secrets.get(key, None)
    except Exception:
        secret_value = None
    if secret_value is not None and str(secret_value).strip():
        return str(secret_value).strip(), "streamlit_secrets"

    env_value = os.getenv(key, "").strip()
    if env_value:
        return env_value, "environment"

    return "", "unset"


@st.cache_resource(show_spinner=False)
def get_supabase_client() -> tuple[Any | None, SupabaseStatus]:
    try:
        from supabase import create_client
    except Exception as exc:
        return None, SupabaseStatus(False, False, f"Supabase REST API 패키지를 불러오지 못했습니다: {exc}")

    url, url_source = config_text_value("SUPABASE_URL")
    key, key_source = config_text_value("SUPABASE_KEY")
    source = url_source if url_source == key_source else f"{url_source}/{key_source}"
    if not url or not key:
        missing = ", ".join(name for name, value in (("SUPABASE_URL", url), ("SUPABASE_KEY", key)) if not value)
        return None, SupabaseStatus(False, False, f"Supabase REST API 설정 누락: {missing}", source)

    try:
        client = create_client(url, key)
        result = client.table("warehouse_layouts").select("id", count="exact").limit(1).execute()
        return client, SupabaseStatus(
            True,
            True,
            f"Supabase REST API 연결 성공: warehouse_layouts 조회 OK (count={result.count})",
            source,
        )
    except Exception as exc:
        return None, SupabaseStatus(True, False, f"Supabase REST API 연결 실패: {exc}", source)


def supabase_status() -> SupabaseStatus:
    return get_supabase_client()[1]


def show_supabase_error() -> None:
    status = supabase_status()
    if not status.connected:
        st.warning(status.message)
