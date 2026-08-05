from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import streamlit as st


@dataclass(frozen=True)
class SupabaseStatus:
    configured: bool
    connected: bool
    message: str


@st.cache_resource(show_spinner=False)
def get_supabase_client() -> tuple[Any | None, SupabaseStatus]:
    try:
        from supabase import create_client
    except Exception as exc:
        return None, SupabaseStatus(False, False, f"Supabase REST API 패키지를 불러오지 못했습니다: {exc}")

    try:
        url = str(st.secrets["SUPABASE_URL"]).strip()
        key = str(st.secrets["SUPABASE_KEY"]).strip()
    except Exception:
        return None, SupabaseStatus(False, False, "Streamlit Secrets에 SUPABASE_URL, SUPABASE_KEY가 없습니다. REST API 상태 확인만 비활성화됩니다.")

    if not url or not key:
        return None, SupabaseStatus(False, False, "SUPABASE_URL 또는 SUPABASE_KEY가 비어 있습니다. REST API 상태 확인만 비활성화됩니다.")

    try:
        client = create_client(url, key)
        client.table("inventory_items").select("id", count="exact").limit(1).execute()
        return client, SupabaseStatus(True, True, "Supabase REST API 연결 정상")
    except Exception as exc:
        return None, SupabaseStatus(True, False, f"Supabase REST API 연결 실패: {exc}")


def supabase_status() -> SupabaseStatus:
    return get_supabase_client()[1]


def show_supabase_error() -> None:
    status = supabase_status()
    if not status.connected:
        st.warning(status.message)
