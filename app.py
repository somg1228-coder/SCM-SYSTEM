from pathlib import Path
from contextlib import nullcontext
import importlib
import logging
import re
import time
import traceback

import streamlit as st

from components.header import render_header
from components import sidebar as sidebar_component


BASE_DIR = Path(__file__).parent
APP_ERROR_LOG_PATH = BASE_DIR / "data" / "app_error.log"
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def perf_tools():
    try:
        from backend.perf import perf_span, start_streamlit_run, summarize_current_run

        return perf_span, start_streamlit_run, summarize_current_run
    except Exception:
        return (lambda *_args, **_kwargs: nullcontext()), (lambda *_args, **_kwargs: ""), (lambda *_args, **_kwargs: {})


def import_page_module(module_name: str, label: str):
    try:
        return importlib.import_module(module_name)
    except Exception as exc:
        log_app_exception(exc)
        st.error(f"{label} 화면을 불러오지 못했습니다. 배포 로그와 아래 오류를 확인해주세요.")
        st.exception(exc)
        return None


def log_route_event(message: str) -> None:
    return


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


@st.cache_data(show_spinner=False)
def read_css_text(css_path: str, mtime: float) -> str:
    css = Path(css_path).read_text(encoding="utf-8")
    css = CSS_COMMENT_RE.sub("", css)
    return "\n".join(line.strip() for line in css.splitlines() if line.strip())


def load_css() -> None:
    css_path = BASE_DIR / "assets" / "style.css"
    if css_path.exists():
        st.markdown(f"<style>{read_css_text(str(css_path), css_path.stat().st_mtime)}</style>", unsafe_allow_html=True)


def run_sqlite_bootstrap_migration() -> None:
    try:
        from backend.config import config_bool_value

        supabase_enabled, _ = config_bool_value("SCM_USE_SUPABASE_DB", default=False)
    except Exception:
        supabase_enabled = False
    if supabase_enabled:
        st.session_state["sqlite_bootstrap_migration_checked"] = True
        st.session_state["sqlite_bootstrap_migration_result"] = {"ok": True, "skipped": True, "reason": "supabase-production-skip"}
        return

    if st.session_state.get("sqlite_bootstrap_migration_checked"):
        return
    st.session_state["sqlite_bootstrap_migration_checked"] = True
    try:
        from backend.sqlite_bootstrap_migration import run_once

        result = run_once()
        st.session_state["sqlite_bootstrap_migration_result"] = result
        if result.get("ok") and not result.get("skipped"):
            st.success("SQLite 기존 업무 데이터 Supabase 이관 완료")
    except Exception as exc:
        log_app_exception(exc)
        st.error(f"SQLite 기존 데이터 Supabase 이관 실패: {exc}")


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
        import pandas as pd
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


def render_sidebar_status_placeholder() -> None:
    with st.sidebar.expander("Database status", expanded=False):
        status = st.session_state.get("last_database_status")
        if isinstance(status, dict):
            if status.get("supabase_db_enabled") and status.get("is_postgresql"):
                st.success("Supabase configured")
            elif status.get("is_sqlite"):
                st.warning("SQLite in use")
            else:
                st.caption(status.get("message") or "Database status cached.")
            host = status.get("host") or ""
            port = status.get("port") or ""
            if host:
                st.caption(f"DB host={host} port={port or '-'}")
        else:
            st.caption("Live diagnostics run from Admin.")


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
    try:
        if page in {"홈", "대시보드"}:
            render_home()
        elif page == "일정관리":
            render_schedule()
        elif page == "회의자료":
            render_meeting()
        elif page == "반품/AS 관리":
            render_return_as()
        elif page == "재고관리":
            render_inventory()
        elif page in {"구매관리", "발주관리"}:
            render_order()
        elif page == "BOM 관리":
            render_bom()
        elif page == "3D 창고관리":
            render_warehouse_3d()
        elif page == "업무가이드":
            render_guide()
        elif page == "자료실":
            render_files()
        elif page == "관리자":
            render_settings()
        else:
            render_home()
    except Exception as exc:
        log_app_exception(exc)
        st.error(f"{page} 화면 렌더링 중 오류가 발생했습니다.")
        st.exception(exc)


def inject_route_transition_cleanup(page: str) -> None:
    if page != "3D 창고관리":
        return
    st.markdown(
        """
        <div class="route-warehouse3d-active" aria-hidden="true"></div>
        <style>
        .route-warehouse3d-active {
            display: none !important;
        }
        .stApp:has(.route-warehouse3d-active) .st-key-inventory_nav_shell,
        .stApp:has(.route-warehouse3d-active) .product-master-title,
        .stApp:has(.route-warehouse3d-active) .product-master-control-title,
        .stApp:has(.route-warehouse3d-active) .product-master-form-title,
        .stApp:has(.route-warehouse3d-active) .product-master-visible-table-wrap,
        .stApp:has(.route-warehouse3d-active) div[class*="st-key-product_master_"] {
            display: none !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def query_value(name: str) -> str:
    value = st.query_params.get(name)
    if isinstance(value, list):
        return value[0] if value else ""
    return value or ""


def sync_query_params_to_state() -> None:
    query_page = query_value("page")
    if query_page:
        st.session_state["page"] = query_page
        st.session_state["selected_menu"] = query_page
        st.session_state["current_page"] = query_page

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
    st.set_page_config(
        page_title="SCM SYSTEM",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    total_started_at = time.perf_counter()
    perf_span, start_streamlit_run, summarize_current_run = perf_tools()
    page_hint = query_value("page") or st.session_state.get("current_page") or st.session_state.get("selected_menu") or st.session_state.get("page") or "대시보드"
    start_streamlit_run(page_hint)

    with perf_span("app.sync_query_params"):
        sync_query_params_to_state()

    with perf_span("app.load_css"):
        load_css()

    with perf_span("app.sidebar_render"):
        page = sidebar_component.render_sidebar()
    st.session_state["page"] = page
    st.session_state["selected_menu"] = page
    st.session_state["current_page"] = page
    with perf_span("app.route_cleanup", page=page):
        inject_route_transition_cleanup(page)

    if page != "반품/AS 관리":
        with perf_span("app.header_render", page=page):
            render_header(page)
    with perf_span("app.page_render", page=page):
        render_page(page)
    summarize_current_run(time.perf_counter() - total_started_at, page=page)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log_app_exception(exc)
        st.error("앱 실행 중 오류가 발생했습니다. 아래 상세 오류를 확인해주세요.")
        st.exception(exc)
