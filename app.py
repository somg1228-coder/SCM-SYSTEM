from pathlib import Path
import importlib

import streamlit as st

from components.header import render_header
from components import sidebar as sidebar_component
from backend import services as backend_services
from pages import dashboard as dashboard_page
from pages import inventory as inventory_page
from pages import bom as bom_page
from pages import meeting as meeting_page
from pages import purchase as purchase_page
from pages import schedule as schedule_page
from pages import warehouse3d as warehouse3d_page
from ReturnCaseSystem.app import render_return_case_system


BASE_DIR = Path(__file__).parent


def load_css() -> None:
    css_path = BASE_DIR / "assets" / "style.css"
    if css_path.exists():
        st.markdown(f"<style>{css_path.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


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
    importlib.reload(dashboard_page).render_dashboard()


def render_schedule() -> None:
    importlib.reload(schedule_page).render_schedule_page()


def render_meeting() -> None:
    importlib.reload(meeting_page).render_meeting_page()


def render_return_as() -> None:
    render_return_case_system()


def render_inventory() -> None:
    importlib.reload(backend_services)
    importlib.reload(inventory_page).render_inventory_page()


def render_order() -> None:
    importlib.reload(purchase_page).render_purchase_page()


def render_bom() -> None:
    importlib.reload(bom_page).render_bom_page()


def render_warehouse_3d() -> None:
    importlib.reload(warehouse3d_page).render_warehouse3d_page()


def render_guide() -> None:
    render_placeholder("업무가이드")


def render_files() -> None:
    render_placeholder("자료실")


def render_settings() -> None:
    render_placeholder("시스템 설정")


def render_page(page: str) -> None:
    if page == "홈":
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
    elif page == "시스템 설정":
        render_settings()
    else:
        render_home()


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
    st.set_page_config(
        page_title="SCM 물류운영포털",
        layout="wide",
        initial_sidebar_state="auto",
    )
    load_css()
    sync_query_params_to_state()

    page = importlib.reload(sidebar_component).render_sidebar()
    st.session_state["page"] = page

    main_container = st.empty()
    with main_container.container():
        if page != "반품/AS 관리":
            render_header(page)
        render_page(page)


if __name__ == "__main__":
    main()
