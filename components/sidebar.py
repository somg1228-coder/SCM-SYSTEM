from __future__ import annotations

from html import escape
from urllib.parse import urlencode

import streamlit as st


MENU_ITEMS = [
    ("home", "홈"),
    ("calendar", "일정관리"),
    ("file-text", "회의자료"),
    ("rotate-ccw", "반품/AS 관리"),
    ("boxes", "재고관리"),
    ("shopping-cart", "구매관리"),
    ("git-branch", "BOM 관리"),
    ("warehouse", "3D 창고관리"),
    ("book-open", "업무가이드"),
    ("folder-open", "자료실"),
    ("settings", "시스템 설정"),
]

MENU_GROUPS = [
    ("업무", MENU_ITEMS[0:3]),
    ("운영관리", MENU_ITEMS[3:8]),
    ("지원", MENU_ITEMS[8:10]),
]

SETTINGS_ITEM = MENU_ITEMS[10]

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


def icon_svg(name: str) -> str:
    paths = {
        "home": '<path d="m3 10 9-7 9 7"/><path d="M5 10v10h14V10"/><path d="M9 20v-6h6v6"/>',
        "calendar": '<path d="M8 2v4M16 2v4"/><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 10h18M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01"/>',
        "file-text": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h6"/>',
        "rotate-ccw": '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
        "boxes": '<path d="M21 8.5 12 3 3 8.5 12 14l9-5.5Z"/><path d="M3 8.5V16l9 5 9-5V8.5M12 14v7"/>',
        "shopping-cart": '<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.7 13.4a2 2 0 0 0 2 1.6h8.7a2 2 0 0 0 2-1.6L22 6H6"/>',
        "git-branch": '<circle cx="6" cy="5" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="6" cy="19" r="2"/><path d="M6 7v10M8 5h4a6 6 0 0 1 6 6v-3"/>',
        "warehouse": '<path d="M3 21V9l9-6 9 6v12"/><path d="M7 21v-8h10v8M7 13h10M9 17h6"/>',
        "book-open": '<path d="M2 4.5A3 3 0 0 1 5 2h7v20H5a3 3 0 0 0-3 3V4.5Z"/><path d="M22 4.5A3 3 0 0 0 19 2h-7v20h7a3 3 0 0 1 3 3V4.5Z"/>',
        "folder-open": '<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v2"/><path d="M3 11h18l-2 8H5l-2-8Z"/>',
        "settings": '<path d="M12 15.5A3.5 3.5 0 1 0 12 8a3.5 3.5 0 0 0 0 7.5Z"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1A2 2 0 1 1 4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1A2 2 0 1 1 7.1 4.2l.1.1a1.7 1.7 0 0 0 1.9.3h.1a1.7 1.7 0 0 0 1-1.6V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.6h.1a1.7 1.7 0 0 0 1.9-.3l.1-.1A2 2 0 1 1 19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9v.1a1.7 1.7 0 0 0 1.6 1h.1a2 2 0 1 1 0 4H21a1.7 1.7 0 0 0-1.6 1Z"/>',
    }
    return f'<svg class="sidebar-icon" viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>'


def menu_link(icon: str, label: str, active_page: str) -> str:
    active = " active" if active_page == label else ""
    href = "?" + urlencode({"page": label})
    return (
        f'<a class="sidebar-menu-item{active}" href="{href}" target="_self" title="{escape(label)}">'
        f'{icon_svg(icon)}<span>{escape(label)}</span></a>'
    )


def sidebar_markup(active_page: str) -> str:
    groups = []
    for group_label, items in MENU_GROUPS:
        rows = "".join(menu_link(icon, label, active_page) for icon, label in items)
        groups.append(
            f"""
            <section class="sidebar-menu-group">
                <div class="sidebar-group-title">{escape(group_label)}</div>
                <nav class="sidebar-menu-list">{rows}</nav>
            </section>
            """
        )
    settings_icon, settings_label = SETTINGS_ITEM
    return f"""
    <aside class="portal-sidebar-shell">
        <div class="portal-brand">
            <div class="portal-logo-mark">{icon_svg("boxes")}</div>
            <div class="portal-name">SCM 물류운영포털</div>
        </div>
        <div class="sidebar-menu-main">{''.join(groups)}</div>
        <div class="sidebar-bottom">
            <div class="sidebar-meta">SCM Portal · v1.0</div>
            <nav class="sidebar-menu-list">
                {menu_link(settings_icon, settings_label, active_page)}
            </nav>
        </div>
    </aside>
    """


def render_sidebar() -> str:
    if "page" not in st.session_state:
        st.session_state["page"] = "홈"
    if st.session_state.get("page") == "발주관리":
        st.session_state["page"] = "구매관리"

    with st.sidebar:
        st.markdown(sidebar_markup(st.session_state["page"]), unsafe_allow_html=True)

    return st.session_state["page"]
