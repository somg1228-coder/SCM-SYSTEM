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


def sidebar_markdown(active_page: str) -> str:
    lines = ["### SCM 물류운영포털", "---"]
    for group_label, items in MENU_GROUPS:
        lines.append(f"###### {group_label}")
        for _, label in items:
            display_label = f"**{label}**" if active_page == label else label
            href = "?" + urlencode({"page": label})
            lines.append(f"[{display_label}]({href})")
        lines.append("")
    settings_label = SETTINGS_ITEM[1]
    settings_display = f"**{settings_label}**" if active_page == settings_label else settings_label
    settings_href = "?" + urlencode({"page": settings_label})
    lines.extend(["---", "###### 설정", "SCM Portal · v1.0", f"[{settings_display}]({settings_href})"])
    return "\n\n".join(lines)


def render_sidebar() -> str:
    active_page = normalize_page()

    with st.sidebar:
        st.markdown(sidebar_markdown(active_page))

    return st.session_state["page"]
