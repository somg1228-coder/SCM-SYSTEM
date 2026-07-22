from pathlib import Path
import importlib
import socket
import subprocess
import sys
import time
from typing import Optional, Tuple
from urllib.parse import urlencode

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


BASE_DIR = Path(__file__).parent
RETURN_SYSTEM_DIR = BASE_DIR / "ReturnCaseSystem"
RETURN_SYSTEM_APP = RETURN_SYSTEM_DIR / "app.py"
RETURN_SYSTEM_BIND_HOST = "0.0.0.0"
RETURN_SYSTEM_LOCAL_HOST = "127.0.0.1"
RETURN_SYSTEM_PORT = 8502


def load_css() -> None:
    css_path = BASE_DIR / "assets" / "style.css"
    if css_path.exists():
        st.markdown(f"<style>{css_path.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def is_port_open(port: int, host: str = RETURN_SYSTEM_LOCAL_HOST, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def find_available_port(start_port: int = RETURN_SYSTEM_PORT, max_port: int = 8599) -> int:
    for port in range(start_port, max_port + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((RETURN_SYSTEM_BIND_HOST, port))
            except OSError:
                continue
            return port

    raise RuntimeError("사용 가능한 Streamlit 포트를 찾지 못했습니다.")


def wait_for_port(port: int, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_port_open(port):
            return True
        time.sleep(0.25)
    return False


def return_case_public_url(port: int) -> str:
    host = request_host_without_port() or RETURN_SYSTEM_LOCAL_HOST
    scheme = request_scheme()
    return f"{scheme}://{host}:{port}/?embed=true"


def request_scheme() -> str:
    forwarded_proto = request_header("x-forwarded-proto")
    if forwarded_proto:
        return forwarded_proto.split(",")[0].strip()
    context_url = str(getattr(getattr(st, "context", None), "url", "") or "")
    if context_url.startswith("https://"):
        return "https"
    return "http"


def request_host_without_port() -> str:
    host = request_header("host")
    if not host:
        return ""
    host = host.split(",")[0].strip()
    if host.startswith("["):
        return host.split("]", 1)[0] + "]"
    if host.count(":") == 1:
        return host.rsplit(":", 1)[0]
    return host


def request_header(name: str) -> str:
    headers = getattr(getattr(st, "context", None), "headers", None)
    if not headers:
        return ""
    try:
        return str(headers.get(name, "") or headers.get(name.title(), "") or "")
    except AttributeError:
        return ""


def ensure_return_case_system() -> Tuple[Optional[str], Optional[str], bool]:
    if not RETURN_SYSTEM_APP.exists():
        return None, "ReturnCaseSystem/app.py 파일을 찾을 수 없습니다.", False

    current_port = st.session_state.get("return_case_system_port")
    current_bind_host = st.session_state.get("return_case_system_bind_host")
    if current_port and current_bind_host == RETURN_SYSTEM_BIND_HOST and is_port_open(current_port):
        return return_case_public_url(current_port), None, True

    try:
        port = find_available_port()
    except RuntimeError as exc:
        return None, str(exc), False
    out_log = RETURN_SYSTEM_DIR / "streamlit.out.log"
    err_log = RETURN_SYSTEM_DIR / "streamlit.err.log"
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(RETURN_SYSTEM_APP),
        "--server.address",
        RETURN_SYSTEM_BIND_HOST,
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]

    try:
        with out_log.open("ab") as stdout, err_log.open("ab") as stderr:
            process = subprocess.Popen(
                command,
                cwd=str(RETURN_SYSTEM_DIR),
                stdout=stdout,
                stderr=stderr,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
    except OSError as exc:
        return None, f"반품/AS 앱 실행에 실패했습니다: {exc}", False

    st.session_state.return_case_system_port = port
    st.session_state.return_case_system_bind_host = RETURN_SYSTEM_BIND_HOST
    st.session_state.return_case_system_pid = process.pid
    is_ready = wait_for_port(port)
    if not is_ready and process.poll() is not None:
        return None, "반품/AS 앱이 시작 직후 종료되었습니다. ReturnCaseSystem/streamlit.err.log를 확인해주세요.", False

    return return_case_public_url(port), None, is_ready


def render_return_case_frame() -> None:
    with st.spinner("기존 ReturnCaseSystem 앱을 별도 Streamlit 프로세스로 실행하는 중입니다..."):
        app_url, error, _is_ready = ensure_return_case_system()

    if error:
        st.error(error)
        return

    app_url = append_return_case_query(app_url)

    st.markdown(
        f"""
        <section class="return-system-shell">
            <iframe
                class="return-system-frame"
                src="{app_url}"
                title="반품/AS 관리 시스템"
                loading="eager"
                style="width:100%; height:100%; border:0;"
            ></iframe>
        </section>
        """,
        unsafe_allow_html=True,
    )


def append_return_case_query(app_url: str) -> str:
    params = {}
    return_case_filter = st.session_state.get("return_case_filter", "")
    return_case_month = st.session_state.get("return_case_month", "")
    return_case_id = st.session_state.get("return_case_id", "")
    if return_case_filter:
        params["return_case_filter"] = return_case_filter
    if return_case_month:
        params["return_case_month"] = return_case_month
    if return_case_id:
        params["return_case_id"] = return_case_id
    if not params:
        return app_url
    separator = "&" if "?" in app_url else "?"
    return f"{app_url}{separator}{urlencode(params)}"


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
    render_return_case_frame()


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
        initial_sidebar_state="expanded",
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
