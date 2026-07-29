from __future__ import annotations

from html import escape

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


def activate_page(label: str) -> None:
    st.session_state["page"] = label
    try:
        st.query_params.clear()
        st.query_params["page"] = label
    except Exception:
        pass


def render_sidebar() -> str:
    active_page = normalize_page()
    group_tones = ["work", "ops", "support"]

    with st.sidebar:
        st.markdown(
            '<div class="sidebar-brand"><span class="sidebar-brand-mark"></span><span>SCM 물류운영포털</span></div>'
            '<div class="sidebar-divider"></div>',
            unsafe_allow_html=True,
        )
        for group_index, (group_label, items) in enumerate(MENU_GROUPS):
            group_tone = group_tones[group_index] if group_index < len(group_tones) else "support"
            st.markdown(
                f'<div class="sidebar-group-title sidebar-group-{group_tone}">{escape(group_label)}</div>',
                unsafe_allow_html=True,
            )
            for key, label in items:
                st.button(
                    label,
                    key=f"sidebar_nav_{key}",
                    use_container_width=True,
                    type="primary" if active_page == label else "secondary",
                    on_click=activate_page,
                    args=(label,),
                )

        settings_label = SETTINGS_ITEM[1]
        st.markdown('<div class="sidebar-group-title sidebar-group-settings">설정</div>', unsafe_allow_html=True)
        st.button(
            settings_label,
            key=f"sidebar_nav_{SETTINGS_ITEM[0]}",
            use_container_width=True,
            type="primary" if active_page == settings_label else "secondary",
            on_click=activate_page,
            args=(settings_label,),
        )
        st.markdown('<div class="sidebar-meta">SCM Portal · v1.0</div>', unsafe_allow_html=True)

    return st.session_state["page"]
