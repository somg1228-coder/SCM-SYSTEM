from pathlib import Path
import importlib
import logging
import time
import traceback

import pandas as pd
import streamlit as st

from components.header import render_header
from components import sidebar as sidebar_component


BASE_DIR = Path(__file__).parent
APP_ERROR_LOG_PATH = BASE_DIR / "data" / "app_error.log"
APP_ROUTING_LOG_PATH = BASE_DIR / "data" / "app_routing.log"


def import_page_module(module_name: str, label: str):
    try:
        return importlib.import_module(module_name)
    except Exception as exc:
        log_app_exception(exc)
        st.error(f"{label} 화면을 불러오지 못했습니다. 배포 로그와 아래 오류를 확인해주세요.")
        st.exception(exc)
        return None


def log_route_event(message: str) -> None:
    try:
        APP_ROUTING_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with APP_ROUTING_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except Exception:
        pass


def log_app_exception(exc: BaseException) -> None:
    try:
        APP_ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=APP_ERROR_LOG_PATH,
            level=logging.ERROR,
            format="%(asctime)s %(levelname)s %(message)s",
            encoding="utf-8",
        )
        logging.exception("Unhandled SCM Portal app error")
        with APP_ERROR_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write("\n--- traceback ---\n")
            handle.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except Exception:
        pass


def load_css() -> None:
    css_path = BASE_DIR / "assets" / "style.css"
    if css_path.exists():
        st.markdown(f"<style>{css_path.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def database_status_nonblocking() -> dict:
    try:
        from backend.database import database_status

        return database_status(include_live_checks=False)
    except Exception as exc:
        log_app_exception(exc)
        return {
            "supabase_db_enabled": False,
            "is_postgresql": False,
            "is_sqlite": False,
            "select_1_ok": False,
            "connected": False,
            "message": f"Database status unavailable: {exc}",
        }


def render_startup_config_diagnostics() -> None:
    try:
        from backend.config import config_key_diagnostics
    except Exception:
        return
    rows = []
    for key in ("SCM_USE_SUPABASE_DB", "SCM_DATABASE_URL", "SUPABASE_URL", "SUPABASE_KEY"):
        item = config_key_diagnostics(key)
        rows.append(
            {
                "Key": key,
                "Selected source": item.get("selected_source"),
                "Selected present": item.get("selected_present"),
                "Masked value": item.get("selected_masked"),
                "st.secrets present": item.get("st_secrets_present"),
                "env present": item.get("environment_present"),
                ".env present": item.get("local_env_present"),
                "st.secrets error": item.get("streamlit_secret_error"),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_sidebar_config_summary(status: dict) -> None:
    diagnostics = status.get("config_diagnostics") or {}
    use_diag = diagnostics.get("SCM_USE_SUPABASE_DB") or {}
    db_diag = diagnostics.get("SCM_DATABASE_URL") or {}
    with st.sidebar.expander("Database status", expanded=False):
        if status.get("supabase_db_enabled") and status.get("is_postgresql") and status.get("select_1_ok"):
            st.success("Supabase connected")
        elif status.get("is_sqlite"):
            st.warning("SQLite in use")
        else:
            st.error(status.get("message") or "Check database connection status.")
        st.caption(
            " / ".join(
                [
                    f"SCM_USE_SUPABASE_DB={str(status.get('supabase_db_enabled')).lower()}",
                    f"use source={status.get('supabase_db_enabled_source')}",
                    f"db url source={status.get('url_source')}",
                    f"selected={status.get('selected_database')}",
                ]
            )
        )
        host = status.get("host") or ""
        port = status.get("port") or ""
        if host:
            st.caption(f"DB host={host} port={port or '-'}")
        if use_diag.get("streamlit_secret_error") or db_diag.get("streamlit_secret_error"):
            st.caption(use_diag.get("streamlit_secret_error") or db_diag.get("streamlit_secret_error"))


def render_placeholder(active_menu: str) -> None:
    st.markdown(
        f"""
        <section class="panel placeholder-panel">
            <p class="panel-eyebrow">COMING SOON</p>
            <h2>{active_menu}</h2>
            <p>이 메뉴는 추후 업무 화면을 연결할 수 있도록 자리만 준비했습니다.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )


def render_home() -> None:
    module = import_page_module("pages.dashboard", "대시보드")
    if module is not None:
        module.render_dashboard()


def render_schedule() -> None:
    module = import_page_module("pages.schedule", "일정관리")
    if module is not None:
        module.render_schedule_page()


def render_meeting() -> None:
    module = import_page_module("pages.meeting", "회의자료")
    if module is not None:
        module.render_meeting_page()


def render_return_as() -> None:
    module = import_page_module("ReturnCaseSystem.app", "반품/AS 관리")
    if module is not None:
        module.render_return_case_system()


def render_inventory() -> None:
    import_page_module("backend.services", "재고관리 서비스")
    module = import_page_module("pages.inventory", "재고관리")
    if module is not None:
        module.render_inventory_page()


def render_order() -> None:
    module = import_page_module("pages.purchase", "구매관리")
    if module is not None:
        module.render_purchase_page()


def render_bom() -> None:
    module = import_page_module("pages.bom", "BOM 관리")
    if module is not None:
        module.render_bom_page()


def render_warehouse_3d() -> None:
    module = import_page_module("pages.warehouse3d", "3D 창고관리")
    if module is not None:
        module.render_warehouse3d_page()


def render_guide() -> None:
    render_placeholder("업무가이드")


def render_files() -> None:
    render_placeholder("자료실")


def render_settings() -> None:
    module = import_page_module("pages.settings", "관리자")
    if module is not None:
        module.render_settings_page()


def render_page(page: str) -> None:
    st.session_state["page"] = page
    st.session_state["selected_menu"] = page
    st.session_state["current_page"] = page
    route_context = (
        f"page={page!r} selected_menu={st.session_state.get('selected_menu')!r} "
        f"current_page={st.session_state.get('current_page')!r}"
    )
    log_route_event(f"render_page_start {route_context}")
    started_at = time.perf_counter()
    try:
        if page in {"홈", "대시보드"}:
            log_route_event("call render_home")
            render_home()
        elif page == "일정관리":
            log_route_event("call render_schedule")
            render_schedule()
        elif page == "회의자료":
            log_route_event("call render_meeting")
            render_meeting()
        elif page == "반품/AS 관리":
            log_route_event("call render_return_as")
            render_return_as()
        elif page == "재고관리":
            log_route_event("call render_inventory")
            render_inventory()
        elif page in {"구매관리", "발주관리"}:
            log_route_event("call render_order")
            render_order()
        elif page == "BOM 관리":
            log_route_event("call render_bom")
            render_bom()
        elif page == "3D 창고관리":
            log_route_event("call render_warehouse_3d")
            render_warehouse_3d()
        elif page == "업무가이드":
            log_route_event("call render_guide")
            render_guide()
        elif page == "자료실":
            log_route_event("call render_files")
            render_files()
        elif page == "관리자":
            log_route_event("call render_settings")
            render_settings()
        else:
            log_route_event(f"unknown_page_fallback {page!r}")
            render_home()
    except Exception as exc:
        log_app_exception(exc)
        log_route_event(f"render_page_error {page!r} {type(exc).__name__}: {exc}")
        st.error(f"{page} 화면 렌더링 중 오류가 발생했습니다.")
        st.exception(exc)
    finally:
        elapsed = time.perf_counter() - started_at
        st.session_state["last_page_render_seconds"] = round(elapsed, 3)
        log_route_event(f"render_page_done {page!r} seconds={elapsed:.3f}")


def query_value(name: str) -> str:
    value = st.query_params.get(name)
    if isinstance(value, list):
        return value[0] if value else ""
    return value or ""


def sync_query_params_to_state() -> None:
    query_page = query_value("page")
    if query_page:
        st.session_state["page"] = query_page

    inventory_filter = query_value("inventory_filter")
    if inventory_filter:
        st.session_state["inventory_filter"] = inventory_filter

    inventory_date = query_value("inventory_date")
    if inventory_date:
        st.session_state["inventory_filter_date"] = inventory_date

    outbound_item = query_value("outbound_item")
    if outbound_item:
        st.session_state["outbound_item_filter"] = outbound_item

    outbound_start = query_value("outbound_start")
    if outbound_start:
        st.session_state["outbound_start_date"] = outbound_start

    outbound_end = query_value("outbound_end")
    if outbound_end:
        st.session_state["outbound_end_date"] = outbound_end

    return_case_filter = query_value("return_case_filter")
    if return_case_filter:
        st.session_state["return_case_filter"] = return_case_filter
        st.session_state.pop("return_case_id", None)

    return_case_month = query_value("return_case_month")
    if return_case_month:
        st.session_state["return_case_month"] = return_case_month

    return_case_id = query_value("return_case_id")
    if return_case_id:
        st.session_state["return_case_id"] = return_case_id
        st.session_state.pop("return_case_filter", None)
        st.session_state.pop("return_case_month", None)


def main() -> None:
    started_at = time.perf_counter()
    st.set_page_config(
        page_title="SCM 물류운영포털",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    load_css()
    sync_query_params_to_state()

    page = sidebar_component.render_sidebar()
    st.session_state["page"] = page
    st.session_state["selected_menu"] = page
    st.session_state["current_page"] = page

    if page != "반품/AS 관리":
        render_header(page)
    render_page(page)
    render_sidebar_config_summary(database_status_nonblocking())
    load_css()
    st.session_state["last_app_render_seconds"] = round(time.perf_counter() - started_at, 3)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log_app_exception(exc)
        st.error("앱 실행 중 오류가 발생했습니다. 아래 상세 오류를 확인해주세요.")
        st.exception(exc)
