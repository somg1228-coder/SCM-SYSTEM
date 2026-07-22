import streamlit as st


MENU_ITEMS = [
    ("⌂", "홈"),
    ("▣", "일정관리"),
    ("▦", "회의자료"),
    ("◌", "반품/AS 관리"),
    ("□", "재고관리"),
    ("▥", "구매관리"),
    ("◇", "BOM 관리"),
    ("▱", "3D 창고관리"),
    ("▤", "업무가이드"),
    ("▧", "자료실"),
    ("⚙", "시스템 설정"),
]


RESET_STATE_PREFIXES = (
    "meeting_",
    "bom_",
    "dashboard_inventory_",
    "inventory_dashboard_",
    "product_master_",
    "return_case_",
    "purchase_",
    "3PL_",
    "오프라인_",
    "창고_",
)

RESET_STATE_KEYS = {
    "active_menu",
}

RESET_STATE_FRAGMENTS = (
    "search",
    "selected",
    "detail",
    "tab",
    "filter",
    "query",
)


def reset_page_state() -> None:
    keys_to_delete = [
        key
        for key in st.session_state.keys()
        if (
            key in RESET_STATE_KEYS
            or any(str(key).startswith(prefix) for prefix in RESET_STATE_PREFIXES)
            or any(fragment in str(key).lower() for fragment in RESET_STATE_FRAGMENTS)
        )
    ]
    for key in keys_to_delete:
        del st.session_state[key]


def select_page(page: str) -> None:
    if st.session_state.get("page") == page:
        try:
            has_query_params = bool(dict(st.query_params))
        except Exception:
            has_query_params = False
        if has_query_params:
            reset_page_state()
            try:
                st.query_params.clear()
            except Exception:
                pass
            st.session_state["page"] = page
            st.rerun()
        return
    reset_page_state()
    try:
        st.query_params.clear()
    except Exception:
        pass
    st.session_state["page"] = page
    st.rerun()


def render_sidebar() -> str:
    if "page" not in st.session_state:
        st.session_state["page"] = "홈"
    if st.session_state.get("page") == "발주관리":
        st.session_state["page"] = "구매관리"

    with st.sidebar:
        st.markdown(
            """
            <div class="portal-brand">
                <div class="portal-name">SCM 물류운영포털</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        for icon, item in MENU_ITEMS:
            button_type = "primary" if st.session_state["page"] == item else "secondary"
            if st.button(f"{icon}  {item}", key=f"menu_{item}", type=button_type, use_container_width=True):
                select_page(item)

    return st.session_state["page"]
