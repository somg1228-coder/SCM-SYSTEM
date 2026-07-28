from __future__ import annotations

from urllib.parse import urlencode

import streamlit as st


MENU_ITEMS = [
    ("home", "홈"),
    ("calendar", "일정관리"),
    ("meeting", "회의자료"),
    ("return_as", "반품/AS 관리"),
    ("inventory", "재고관리"),
    ("purchase", "구매관리"),
    ("bom", "BOM 관리"),
    ("warehouse3d", "3D 창고관리"),
    ("guide", "업무가이드"),
    ("files", "자료실"),
    ("settings", "시스템 설정"),
]

MENU_GROUPS = [
    ("업무", MENU_ITEMS[0:3]),
    ("운영관리", MENU_ITEMS[3:8]),
    ("지원", MENU_ITEMS[8:10]),
]

SETTINGS_ITEM = MENU_ITEMS[10]
VALID_PAGES = {label for _, label in MENU_ITEMS}


def normalize_page() -> str:
    page = st.session_state.get("page", "홈")
    if page == "발주관리":
        page = "구매관리"
    if page not in VALID_PAGES:
        page = "홈"
    st.session_state["page"] = page
    return page


def render_menu_link(item_key: str, label: str, active_page: str) -> None:
    display_label = f"• {label}" if active_page == label else label
    href = "?" + urlencode({"page": label})
    st.markdown(f"[{display_label}]({href})")


def render_sidebar() -> str:
    active_page = normalize_page()

    with st.sidebar:
        st.markdown("**SCM 물류운영포털**")
        st.markdown("---")

        for group_label, items in MENU_GROUPS:
            st.markdown(f"###### {group_label}")
            for item_key, label in items:
                render_menu_link(item_key, label, active_page)

        st.markdown("---")
        st.markdown("###### 설정")
        st.markdown("SCM Portal · v1.0")
        render_menu_link(SETTINGS_ITEM[0], SETTINGS_ITEM[1], active_page)

    return st.session_state["page"]
