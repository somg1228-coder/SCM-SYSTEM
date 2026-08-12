from __future__ import annotations

import streamlit as st


MENU_ITEMS = [
    ("home", "대시보드"),
    ("calendar", "일정관리"),
    ("meeting", "회의자료"),
    ("return_as", "반품/AS 관리"),
    ("inventory", "재고관리"),
    ("purchase", "구매관리"),
    ("bom", "BOM 관리"),
    ("warehouse3d", "3D 창고관리"),
    ("guide", "업무가이드"),
    ("files", "자료실"),
]

MENU_GROUPS = [
    ("업무", MENU_ITEMS[0:3]),
    ("운영관리", MENU_ITEMS[3:8]),
    ("지원", MENU_ITEMS[8:10]),
]

VALID_PAGES = {label for _, label in MENU_ITEMS}


def normalize_page() -> str:
    page = st.session_state.get("page", "대시보드")
    page = st.session_state.get("selected_menu", page)
    page = st.session_state.get("current_page", page)
    if page == "홈":
        page = "대시보드"
    if page == "발주관리":
        page = "구매관리"
    if page not in VALID_PAGES:
        page = "대시보드"
    st.session_state["page"] = page
    st.session_state["selected_menu"] = page
    st.session_state["current_page"] = page
    return page


def activate_page(label: str) -> None:
    st.session_state["page"] = label
    st.session_state["selected_menu"] = label
    st.session_state["current_page"] = label
    try:
        st.query_params.clear()
        st.query_params["page"] = label
    except Exception:
        pass


def sidebar_group_state_key(group_tone: str) -> str:
    return f"sidebar_group_expanded_{group_tone}"


def is_group_expanded(group_tone: str) -> bool:
    key = sidebar_group_state_key(group_tone)
    if key not in st.session_state:
        st.session_state[key] = True
    return bool(st.session_state[key])


def toggle_group(group_tone: str) -> None:
    key = sidebar_group_state_key(group_tone)
    st.session_state[key] = not bool(st.session_state.get(key, True))


def render_group_toggle(group_label: str, group_tone: str) -> bool:
    expanded = is_group_expanded(group_tone)
    st.button(
        group_label,
        key=f"sidebar_group_toggle_{group_tone}",
        use_container_width=True,
        type="secondary",
        on_click=toggle_group,
        args=(group_tone,),
    )
    return expanded


def render_sidebar() -> str:
    active_page = normalize_page()
    group_tones = ["work", "ops", "support"]

    with st.sidebar:
        st.markdown(
            '<div class="sidebar-brand"><span class="sidebar-brand-mark" aria-hidden="true"><i></i><i></i><i></i></span><span class="sidebar-brand-title">SCM</span></div>'
            '<div class="sidebar-divider"></div>',
            unsafe_allow_html=True,
        )
        for group_index, (group_label, items) in enumerate(MENU_GROUPS):
            group_tone = group_tones[group_index] if group_index < len(group_tones) else "support"
            if render_group_toggle(group_label, group_tone):
                for key, label in items:
                    st.button(
                        label,
                        key=f"sidebar_nav_{key}",
                        use_container_width=True,
                        type="primary" if active_page == label else "secondary",
                        on_click=activate_page,
                        args=(label,),
                    )

        st.markdown('<div class="sidebar-meta">SCM Portal · v1.0</div>', unsafe_allow_html=True)

    return st.session_state["page"]
