import streamlit as st


def render_header(active_menu: str) -> None:
    section = "대시보드" if active_menu == "홈" else active_menu

    st.markdown(
        f"""
        <header class="topbar">
            <div class="breadcrumb">
                <span class="home-mark">⌂</span>
                <strong>{section}</strong>
            </div>
            <div class="topbar-actions">
                <span class="calendar-icon">▣</span>
                <span>2026-07-02 (수)&nbsp;&nbsp;10:30</span>
                <span class="bell-wrap">♧<em>3</em></span>
                <span class="gear-mark">⚙</span>
            </div>
        </header>
        """,
        unsafe_allow_html=True,
    )
