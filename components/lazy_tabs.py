from __future__ import annotations

from collections.abc import Sequence

import streamlit as st


def lazy_tab_selector(options: Sequence[str], key: str, default: str | None = None, compact: bool = False) -> str:
    """Return one selected tab label without rendering inactive tab bodies."""
    labels = [str(option) for option in options]
    if not labels:
        return ""

    state_key = f"{key}_selected"
    widget_key = f"{key}_widget"
    current = st.session_state.get(state_key) or default or labels[0]
    if current not in labels:
        current = labels[0]
    st.session_state[state_key] = current

    if compact and hasattr(st, "pills"):
        selected = st.pills(
            "section",
            labels,
            selection_mode="single",
            default=current,
            key=widget_key,
            label_visibility="collapsed",
            width="content",
        )
    else:
        try:
            selected = st.segmented_control(
                "section",
                labels,
                default=current,
                key=widget_key,
                label_visibility="collapsed",
            )
        except Exception:
            selected = st.radio(
                "section",
                labels,
                index=labels.index(current),
                horizontal=True,
                key=widget_key,
                label_visibility="collapsed",
            )

    if isinstance(selected, list):
        selected = selected[0] if selected else None
    if selected in labels:
        st.session_state[state_key] = selected
        return selected
    return current
