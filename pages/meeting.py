from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlencode

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "data" / "meeting_reports.db"
RETURN_CASE_DB_PATH = BASE_DIR / "ReturnCaseSystem" / "cases.db"

PRODUCTION_COLUMNS = ["바코드", "상품명", "현재수량", "요청수량", "납기일", "상태", "비고"]
EVENT_COLUMNS = ["행사명", "행사기간", "행사품목", "요청수량", "상세내용"]
EVENT_SUMMARY_COLUMNS = ["행사명", "행사기간", "행사품목", "요청수량"]
ACTION_COLUMNS = ["담당부서/담당자", "진행내용", "수량", "완료예정일", "납기일", "진행상태"]
EDIT_DELETE_COLUMN = "삭제"
EVENT_PRODUCT_OPTIONS = ["SKUA", "SKUB", "SKUC", "SKUD", "SKUE"]
ISSUE_KEYS = {
    "생산/재고 이슈": "issue_delay",
    "발주진행": "issue_inventory",
    "특이사항": "issue_special",
}
ISSUE_ALIASES = {
    "생산/재고 이슈": ("생산/재고 이슈", "생산지연"),
    "발주진행": ("발주진행", "재고부족"),
    "특이사항": ("특이사항",),
}
PRODUCTION_STATUS = ["생산중", "생산완료", "대기", "지연"]
ACTION_STATUS = ["진행중", "대기", "완료"]


def render_meeting_page() -> None:
    ensure_schema()
    inject_page_css()

    default_date = next_tuesday(date.today())
    if "meeting_date" not in st.session_state:
        st.session_state.meeting_date = default_date

    st.markdown('<main class="meeting-shell">', unsafe_allow_html=True)

    meta = render_control_card()
    report = get_or_create_report(meta["meeting_date"], meta["author"])
    kpi_slot = st.empty()

    production_df = render_production_section(report["id"])
    events_df, event_month = render_events_section(report["id"], meta["meeting_date"])
    meta["event_month"] = event_month
    events_df = render_event_detail_section(report, events_df, event_month)
    issues = render_issue_section(report)
    action_df = render_action_section(report["id"])
    kpis = build_kpis(meta["meeting_date"], event_month, production_df, events_df, issues, action_df)
    with kpi_slot.container():
        render_kpi_cards(kpis, section_number="00")

    if st.session_state.pop("meeting_delete_production_requested", False):
        delete_report_section(report["id"], "meeting_production_requests")
        clear_editor_state(f"meeting_production_editor_{report['id']}")
        clear_editor_state(f"meeting_production_editor_v2_{report['id']}")
        clear_editor_state(f"meeting_production_editor_v3_{report['id']}")
        clear_editor_state(f"meeting_production_editor_v4_{report['id']}")
        clear_editor_state(f"meeting_production_editor_v5_{report['id']}")
        clear_editor_state(f"meeting_production_editor_v6_{report['id']}")
        clear_editor_state(f"meeting_production_editor_v7_{report['id']}")
        clear_editor_state(f"meeting_production_edit_buffer_{report['id']}")
        clear_editor_state(table_draft_state_key(report["id"], "production"))
        st.rerun()

    if st.session_state.pop("meeting_delete_events_requested", False):
        delete_event_month(event_month)
        clear_editor_state(f"meeting_events_editor_{report['id']}_{month_key(event_month)}")
        clear_editor_state(f"meeting_events_editor_v2_{report['id']}_{month_key(event_month)}")
        clear_editor_state(f"meeting_events_editor_v3_{report['id']}_{month_key(event_month)}")
        clear_editor_state(f"meeting_events_editor_v4_{report['id']}_{month_key(event_month)}")
        clear_editor_state(f"meeting_events_editor_v5_{report['id']}_{month_key(event_month)}")
        clear_editor_state(f"meeting_events_editor_v6_{report['id']}_{month_key(event_month)}")
        clear_editor_state(f"meeting_events_editor_v7_{report['id']}_{month_key(event_month)}")
        clear_editor_state(f"meeting_events_edit_buffer_{report['id']}_{month_key(event_month)}")
        clear_event_detail_editor_states(report["id"], event_month)
        clear_editor_state(table_draft_state_key(report["id"], f"events_{month_key(event_month)}"))
        clear_pending_event_rows(report["id"], event_month)
        st.rerun()

    if st.session_state.pop("meeting_delete_actions_requested", False):
        delete_report_section(report["id"], "meeting_action_items")
        clear_editor_state(f"meeting_action_editor_v2_{report['id']}")
        clear_editor_state(f"meeting_action_editor_v3_{report['id']}")
        clear_editor_state(f"meeting_action_editor_v4_{report['id']}")
        clear_editor_state(f"meeting_action_editor_v5_{report['id']}")
        clear_editor_state(f"meeting_action_editor_v6_{report['id']}")
        clear_editor_state(f"meeting_action_editor_v7_{report['id']}")
        clear_editor_state(f"meeting_action_editor_v8_{report['id']}")
        clear_editor_state(f"meeting_action_editor_v9_{report['id']}")
        clear_editor_state(f"meeting_action_editor_v10_{report['id']}")
        clear_editor_state(f"meeting_action_edit_buffer_{report['id']}")
        clear_editor_state(action_draft_state_key(report["id"]))
        st.rerun()

    if st.session_state.pop("meeting_save_requested", False):
        save_report_state(report["id"], meta, production_df, events_df, issues, action_df)
        clear_pending_event_rows(report["id"], event_month)
        clear_editor_state(f"meeting_production_editor_{report['id']}")
        clear_editor_state(f"meeting_production_editor_v2_{report['id']}")
        clear_editor_state(f"meeting_production_editor_v3_{report['id']}")
        clear_editor_state(f"meeting_production_editor_v4_{report['id']}")
        clear_editor_state(f"meeting_production_editor_v5_{report['id']}")
        clear_editor_state(f"meeting_production_editor_v6_{report['id']}")
        clear_editor_state(f"meeting_production_editor_v7_{report['id']}")
        clear_editor_state(f"meeting_production_edit_buffer_{report['id']}")
        clear_editor_state(f"meeting_events_editor_{report['id']}_{month_key(event_month)}")
        clear_editor_state(f"meeting_events_editor_v2_{report['id']}_{month_key(event_month)}")
        clear_editor_state(f"meeting_events_editor_v3_{report['id']}_{month_key(event_month)}")
        clear_editor_state(f"meeting_events_editor_v4_{report['id']}_{month_key(event_month)}")
        clear_editor_state(f"meeting_events_editor_v5_{report['id']}_{month_key(event_month)}")
        clear_editor_state(f"meeting_events_editor_v6_{report['id']}_{month_key(event_month)}")
        clear_editor_state(f"meeting_events_editor_v7_{report['id']}_{month_key(event_month)}")
        clear_editor_state(f"meeting_events_edit_buffer_{report['id']}_{month_key(event_month)}")
        clear_event_detail_editor_states(report["id"], event_month)
        clear_editor_state(table_draft_state_key(report["id"], "production"))
        clear_editor_state(table_draft_state_key(report["id"], f"events_{month_key(event_month)}"))
        clear_editor_state(f"meeting_action_editor_v2_{report['id']}")
        clear_editor_state(f"meeting_action_editor_v3_{report['id']}")
        clear_editor_state(f"meeting_action_editor_v4_{report['id']}")
        clear_editor_state(f"meeting_action_editor_v5_{report['id']}")
        clear_editor_state(f"meeting_action_editor_v6_{report['id']}")
        clear_editor_state(f"meeting_action_editor_v7_{report['id']}")
        clear_editor_state(f"meeting_action_editor_v8_{report['id']}")
        clear_editor_state(f"meeting_action_editor_v9_{report['id']}")
        clear_editor_state(f"meeting_action_editor_v10_{report['id']}")
        clear_editor_state(f"meeting_action_edit_buffer_{report['id']}")
        clear_editor_state(action_draft_state_key(report["id"]))
        st.rerun()

    if st.session_state.get("meeting_pdf_requested"):
        pdf_bytes = create_meeting_pdf(meta, production_df, events_df, issues, action_df, kpis)
        if pdf_bytes:
            meta["pdf_download_slot"].download_button(
                "PDF 다운로드",
                data=pdf_bytes,
                file_name=f"물류_{meta['meeting_date'].strftime('%Y%m%d')}.pdf",
                mime="application/pdf",
                key="meeting_pdf_download",
                use_container_width=True,
            )

    st.markdown("</main>", unsafe_allow_html=True)


def inject_page_css() -> None:
    st.markdown(
        """
        <style>
        div[data-testid="stElementContainer"]:has(.meeting-shell),
        div[data-testid="stMarkdownContainer"]:has(.meeting-shell) {
            margin: 0 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_control_card() -> dict:
    if "meeting_author" not in st.session_state:
        st.session_state.meeting_author = "송광선"

    with st.container(key="meeting_control_card"):
        st.markdown(
            """
            <div class="meeting-control-header">
                <p class="panel-eyebrow">WEEKLY REPORT</p>
                <h1>물류</h1>
            </div>
            """,
            unsafe_allow_html=True,
        )
        date_col, author_col, save_col, button_col, history_col = st.columns(
            [1.12, 1.12, 0.68, 0.82, 1.18],
            gap="medium",
        )
        with date_col:
            meeting_date = st.date_input("회의일 선택", key="meeting_date")
        with author_col:
            author = st.text_input("작성자", key="meeting_author")
        with save_col:
            st.write("")
            st.write("")
            if st.button("저장", key="meeting_save_button", type="secondary", use_container_width=True):
                st.session_state.meeting_save_requested = True
        with button_col:
            st.write("")
            st.write("")
            if st.button("PDF 생성", key="meeting_pdf_button", type="primary", use_container_width=True):
                st.session_state.meeting_pdf_requested = True
            pdf_download_slot = st.empty()
        with history_col:
            st.write("")
            st.write("")
            history_bytes, history_count = meeting_history_excel_bytes()
            st.download_button(
                "지난내역 다운로드",
                data=history_bytes,
                file_name=f"회의자료_지난내역_{date.today():%Y%m%d}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="meeting_history_download",
                disabled=history_count == 0,
            )

    return {
        "meeting_date": meeting_date,
        "author": author.strip() or "작성자 미입력",
        "pdf_download_slot": pdf_download_slot,
    }


def render_production_section(report_id: int) -> pd.DataFrame:
    st.markdown(section_title("01", "생산요청 리스트"), unsafe_allow_html=True)
    saved_df = filter_filled_rows(load_table_df("meeting_production_requests", report_id, PRODUCTION_COLUMNS), PRODUCTION_COLUMNS)
    draft_key = table_draft_state_key(report_id, "production")
    edit_buffer_key = f"meeting_production_edit_buffer_{report_id}"
    if draft_key not in st.session_state:
        st.session_state[draft_key] = prepare_production_editor_df(saved_df)

    current_draft = prepare_production_editor_df(st.session_state[draft_key])
    if edit_buffer_key not in st.session_state:
        st.session_state[edit_buffer_key] = add_delete_marker_column(current_draft)
    edit_buffer = st.session_state[edit_buffer_key]
    preview_df = filter_filled_rows(strip_delete_marker_column(current_draft), PRODUCTION_COLUMNS)
    st.markdown(render_table_html(PRODUCTION_COLUMNS, preview_df, "production"), unsafe_allow_html=True)

    with st.expander("생산요청 리스트 편집", expanded=False):
        st.caption("입력한 내용은 생산요청 저장 또는 수정 저장을 눌렀을 때만 반영됩니다. 행 삭제는 삭제 칸을 체크한 뒤 선택 삭제를 누르세요.")
        editor_df = add_delete_marker_column(prepare_production_editor_df(edit_buffer), marker_source=edit_buffer)
        editor_columns = [EDIT_DELETE_COLUMN, *PRODUCTION_COLUMNS]
        editor_key = f"meeting_production_editor_v7_{report_id}"
        with st.form(key=f"meeting_production_editor_form_{report_id}", clear_on_submit=False):
            edited = st.data_editor(
                editor_df,
                num_rows="dynamic",
                use_container_width=True,
                key=editor_key,
                hide_index=True,
                column_order=editor_columns,
                column_config={
                    EDIT_DELETE_COLUMN: st.column_config.CheckboxColumn("삭제", default=False),
                    "바코드": st.column_config.TextColumn("바코드", default=""),
                    "상품명": st.column_config.TextColumn("상품명", default=""),
                    "현재수량": st.column_config.NumberColumn("현재수량", min_value=0, step=1, default=0),
                    "요청수량": st.column_config.NumberColumn("요청수량", min_value=0, step=1, default=0),
                    "납기일": st.column_config.DateColumn("납기일", format="YYYY-MM-DD"),
                    "상태": st.column_config.SelectboxColumn("상태", options=["", *PRODUCTION_STATUS], default=""),
                    "비고": st.column_config.TextColumn("비고", default=""),
                },
            )
            editor_action = render_editor_actions(
                "meeting_production",
                save_label="생산요청 저장",
                secondary_save_label="수정 저장",
                selected_delete_label="선택 삭제",
                selected_delete_count=None,
                delete_label="생산요청 전체 삭제",
                delete_flag="meeting_delete_production_requested",
            )
    production_source = current_draft
    if editor_action == "selected_delete":
        before_delete_count = len(strip_delete_marker_column(edited))
        if count_marked_rows(edited) > 0:
            edited = drop_marked_rows(edited)
        else:
            edited = drop_selected_editor_rows(edited, editor_key, original_count=len(current_draft))
        if len(strip_delete_marker_column(edited)) >= before_delete_count:
            st.session_state.meeting_save_requested = False
        else:
            production_source = prepare_production_editor_df(strip_delete_marker_column(edited))
            st.session_state[draft_key] = prepare_production_editor_df(production_source)
            st.session_state[edit_buffer_key] = add_delete_marker_column(production_source)
    elif editor_action == "save":
        production_source = prepare_production_editor_df(strip_delete_marker_column(edited))
        st.session_state[draft_key] = prepare_production_editor_df(production_source)
        st.session_state[edit_buffer_key] = add_delete_marker_column(production_source)
    else:
        st.session_state[edit_buffer_key] = edited
    return normalize_df(production_source, PRODUCTION_COLUMNS)


def render_events_section(report_id: int, meeting_date: date) -> tuple[pd.DataFrame, date]:
    st.markdown(section_title("02", "행사 일정", anchor_id="meeting-events-section"), unsafe_allow_html=True)
    calendar_month = render_event_calendar_controls(report_id, meeting_date)
    saved_df = filter_filled_rows(load_event_month_df(calendar_month), EVENT_COLUMNS)
    draft_key = table_draft_state_key(report_id, f"events_{month_key(calendar_month)}")
    edit_buffer_key = f"meeting_events_edit_buffer_{report_id}_{month_key(calendar_month)}"
    if draft_key not in st.session_state:
        st.session_state[draft_key] = prepare_event_editor_df(build_event_editor_df(saved_df, report_id, calendar_month))
        clear_pending_event_rows(report_id, calendar_month)

    current_draft = prepare_event_editor_df(st.session_state[draft_key])
    if edit_buffer_key not in st.session_state:
        st.session_state[edit_buffer_key] = add_delete_marker_column(current_draft)
    edit_buffer = st.session_state[edit_buffer_key]
    selected_index = selected_event_index(report_id, filter_filled_rows(strip_delete_marker_column(current_draft), EVENT_COLUMNS), calendar_month)
    st.caption(f"{calendar_month:%Y년 %m월} 행사일정입니다. 같은 달 회의자료에서는 계속 이어지고, 이전월/다음월 버튼으로 지난 행사도 확인할 수 있습니다.")
    preview_df = filter_filled_rows(strip_delete_marker_column(current_draft), EVENT_COLUMNS)
    render_html(render_event_calendar_html(preview_df, calendar_month, meeting_date, report_id, selected_index))
    st.markdown(render_table_html(EVENT_SUMMARY_COLUMNS, preview_df, "events"), unsafe_allow_html=True)

    with st.expander("행사 일정 편집", expanded=False):
        st.caption("입력한 내용은 행사일정 저장 또는 수정 저장을 눌렀을 때만 반영됩니다. 행 삭제는 삭제 칸을 체크한 뒤 선택 삭제를 누르세요.")
        render_event_product_quick_add(report_id, calendar_month)
        pending_rows = get_pending_event_rows(report_id, calendar_month)
        if pending_rows:
            pending_df = pd.DataFrame(pending_rows, columns=EVENT_COLUMNS)
            buffer_source = prepare_event_editor_df(strip_delete_marker_column(edit_buffer))
            edit_buffer = add_delete_marker_column(pd.concat([buffer_source, pending_df], ignore_index=True))
            st.session_state[edit_buffer_key] = edit_buffer
            clear_pending_event_rows(report_id, calendar_month)
        buffer_full = prepare_event_editor_df(strip_delete_marker_column(edit_buffer))
        editor_df = add_delete_marker_column(buffer_full[EVENT_SUMMARY_COLUMNS], marker_source=edit_buffer)
        editor_columns = [EDIT_DELETE_COLUMN, *EVENT_SUMMARY_COLUMNS]
        editor_key = f"meeting_events_editor_v7_{report_id}_{month_key(calendar_month)}"
        with st.form(key=f"meeting_events_editor_form_{report_id}_{month_key(calendar_month)}", clear_on_submit=False):
            edited = st.data_editor(
                editor_df,
                num_rows="dynamic",
                use_container_width=True,
                key=editor_key,
                hide_index=True,
                column_order=editor_columns,
                column_config={
                    EDIT_DELETE_COLUMN: st.column_config.CheckboxColumn("삭제", default=False),
                    "행사명": st.column_config.TextColumn("행사명", default=""),
                    "행사기간": st.column_config.TextColumn("행사기간", help="예: 2026-07-10 ~ 2026-07-15", default=""),
                    "행사품목": st.column_config.TextColumn("행사품목", help="선택 추가 또는 직접 입력 가능, 여러 품목은 쉼표로 구분하세요.", default=""),
                    "요청수량": st.column_config.NumberColumn("요청수량", min_value=0, step=1, default=0),
                },
            )
            editor_action = render_editor_actions(
                "meeting_events",
                save_label="행사일정 저장",
                secondary_save_label="수정 저장",
                selected_delete_label="선택 삭제",
                selected_delete_count=None,
                delete_label="행사일정 전체 삭제",
                delete_flag="meeting_delete_events_requested",
            )
    events_source = current_draft
    buffer_source = prepare_event_editor_df(strip_delete_marker_column(edit_buffer))
    if editor_action == "selected_delete":
        before_delete_count = len(strip_delete_marker_column(edited))
        if count_marked_rows(edited) > 0:
            edited = drop_marked_rows(edited)
        else:
            edited = drop_selected_editor_rows(edited, editor_key, original_count=len(current_draft))
        if len(strip_delete_marker_column(edited)) >= before_delete_count:
            st.session_state.meeting_save_requested = False
        else:
            events_source = prepare_event_editor_df(merge_event_details(strip_delete_marker_column(edited), buffer_source))
            st.session_state[draft_key] = prepare_event_editor_df(events_source)
            st.session_state[edit_buffer_key] = add_delete_marker_column(events_source)
    elif editor_action == "save":
        events_source = prepare_event_editor_df(merge_event_details(strip_delete_marker_column(edited), buffer_source))
        st.session_state[draft_key] = prepare_event_editor_df(events_source)
        st.session_state[edit_buffer_key] = add_delete_marker_column(events_source)
    else:
        buffer_next = prepare_event_editor_df(merge_event_details(strip_delete_marker_column(edited), buffer_source))
        st.session_state[edit_buffer_key] = add_delete_marker_column(buffer_next, marker_source=edited)
    return normalize_event_editor_df(events_source), calendar_month


def render_event_detail_section(report: dict, events_df: pd.DataFrame, event_month: date) -> pd.DataFrame:
    st.markdown('<div class="meeting-subsection-title">행사 상세 내용</div>', unsafe_allow_html=True)
    selected_index = selected_event_index(report["id"], events_df, event_month)
    if selected_index is None:
        st.markdown('<div class="meeting-note-box empty">위 행사 캘린더에서 상세내용을 등록할 일정을 선택하세요.</div>', unsafe_allow_html=True)
        return events_df

    selected_row = events_df.iloc[selected_index]
    selected_name = normalize_cell_value(selected_row.get("행사명")) or "행사"
    selected_period = normalize_cell_value(selected_row.get("행사기간"))
    selected_detail = normalize_cell_value(selected_row.get("상세내용"))
    detail_key = f"meeting_event_detail_{report['id']}_{month_key(event_month)}_{selected_index}"
    st.markdown(
        f"""
        <div class="meeting-selected-event">
            <strong>{escape_html(selected_name)}</strong>
            <span>{escape_html(selected_period)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if selected_detail:
        st.markdown(render_note_html(selected_detail), unsafe_allow_html=True)
    else:
        st.markdown('<div class="meeting-note-box empty">선택한 일정의 상세내용이 없습니다.</div>', unsafe_allow_html=True)

    with st.expander("선택 일정 상세 내용 편집", expanded=True):
        edited_detail = st.text_area(
            "선택 일정 상세 내용",
            value=selected_detail,
            height=140,
            placeholder="선택한 행사 일정의 운영 방식, 주요 요청사항, 준비물, 리스크, 협의 필요사항 등을 입력하세요.",
            key=detail_key,
        )
        detail_col, delete_col, spacer = st.columns([0.9, 0.9, 3.2], gap="small")
        detail_action = "none"
        with detail_col:
            if st.button("상세내용 저장", key="meeting_event_detail_save_btn", type="primary", use_container_width=True):
                st.session_state.meeting_save_requested = True
                detail_action = "save"
        with delete_col:
            if st.button("상세내용 삭제", key="meeting_event_detail_delete_btn", use_container_width=True):
                st.session_state.meeting_save_requested = True
                detail_action = "delete"
        with spacer:
            st.empty()

    updated_events_df = events_df.copy()
    if detail_action == "delete":
        return update_event_detail_draft(report["id"], event_month, updated_events_df, selected_index, "")
    if detail_action == "save":
        return update_event_detail_draft(report["id"], event_month, updated_events_df, selected_index, edited_detail)
    return events_df


def render_editor_actions(
    key_prefix: str,
    save_label: str,
    selected_delete_label: str | None,
    selected_delete_count: int | None,
    delete_label: str,
    delete_flag: str,
    secondary_save_label: str | None = None,
) -> str:
    st.markdown('<div class="meeting-editor-actions">', unsafe_allow_html=True)
    action = "none"
    if secondary_save_label and selected_delete_label:
        save_col, secondary_save_col, selected_delete_col, delete_col, spacer = st.columns([0.9, 0.9, 0.9, 0.9, 1.4], gap="small")
    elif secondary_save_label:
        save_col, secondary_save_col, delete_col, spacer = st.columns([0.9, 0.9, 0.9, 2.3], gap="small")
        selected_delete_col = None
    elif selected_delete_label:
        save_col, selected_delete_col, delete_col, spacer = st.columns([0.9, 0.9, 0.9, 2.3], gap="small")
    else:
        save_col, delete_col, spacer = st.columns([0.9, 0.9, 3.2], gap="small")
        selected_delete_col = None
    with save_col:
        if st.form_submit_button(save_label, type="primary", use_container_width=True):
            st.session_state.meeting_save_requested = True
            action = "save"
    if secondary_save_label:
        with secondary_save_col:
            if st.form_submit_button(secondary_save_label, use_container_width=True):
                st.session_state.meeting_save_requested = True
                action = "save"
    if selected_delete_col is not None and selected_delete_label:
        with selected_delete_col:
            if st.form_submit_button(selected_delete_label, use_container_width=True):
                if selected_delete_count is None or selected_delete_count > 0:
                    st.session_state.meeting_save_requested = True
                    action = "selected_delete"
                else:
                    st.warning("삭제 체크된 행이 없습니다.")
    with delete_col:
        if st.form_submit_button(delete_label, use_container_width=True):
            st.session_state[delete_flag] = True
            action = "full_delete"
    with spacer:
        st.empty()
    st.markdown("</div>", unsafe_allow_html=True)
    return action


def add_delete_marker_column(df: pd.DataFrame, marker_source: pd.DataFrame | None = None) -> pd.DataFrame:
    editor_df = strip_delete_marker_column(df).copy()
    markers = delete_marker_values(marker_source if marker_source is not None else df)
    if len(markers) != len(editor_df):
        markers = [False] * len(editor_df)
    marker_series = pd.Series(markers, index=editor_df.index, dtype=bool)
    editor_df.insert(0, EDIT_DELETE_COLUMN, marker_series)
    return editor_df


def drop_marked_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    cleaned = df.copy()
    keep_rows = [not checked for checked in delete_marker_values(cleaned)]
    return cleaned.iloc[keep_rows].drop(columns=[EDIT_DELETE_COLUMN], errors="ignore").reset_index(drop=True)


def strip_delete_marker_column(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    cleaned = df.drop(columns=[EDIT_DELETE_COLUMN], errors="ignore")
    if is_delete_marker_index(cleaned):
        cleaned = cleaned.reset_index(drop=True)
    return cleaned


def count_marked_rows(df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    return int(delete_marker_mask(df).sum())


def delete_marker_mask(df: pd.DataFrame) -> pd.Series:
    return pd.Series(delete_marker_values(df), index=df.index, dtype=bool)


def delete_marker_values(df: pd.DataFrame | None) -> list[bool]:
    if df is None or df.empty:
        return []
    if EDIT_DELETE_COLUMN in df.columns:
        return [is_delete_checked(value) for value in df[EDIT_DELETE_COLUMN].tolist()]
    if is_delete_marker_index(df):
        return [is_delete_checked(value) for value in df.index.tolist()]
    return [False] * len(df)


def is_delete_marker_index(df: pd.DataFrame) -> bool:
    if df is None:
        return False
    return df.index.name == EDIT_DELETE_COLUMN


def is_delete_checked(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y", "checked", "삭제"}


def drop_selected_editor_rows(df: pd.DataFrame, editor_key: str, original_count: int | None = None) -> pd.DataFrame:
    cleaned = strip_delete_marker_column(df)
    selected_rows = selected_editor_row_positions(editor_key)
    if not selected_rows:
        if original_count is None or len(cleaned) >= original_count:
            st.warning("선택된 행 정보를 읽지 못했습니다. 표 오른쪽 위 휴지통 아이콘으로 선택 행을 삭제한 뒤 저장하거나, 다시 체크 후 선택 삭제를 눌러주세요.")
        return cleaned

    valid_positions = {position for position in selected_rows if 0 <= position < len(cleaned)}
    if not valid_positions:
        return cleaned
    keep_mask = [index not in valid_positions for index in range(len(cleaned))]
    return cleaned.iloc[keep_mask].reset_index(drop=True)


def selected_editor_row_positions(editor_key: str) -> list[int]:
    state = st.session_state.get(editor_key)
    if not isinstance(state, dict):
        return []

    candidates = [
        state.get("selected_rows"),
        state.get("selectedRows"),
        state.get("rows"),
        (state.get("selection") or {}).get("rows") if isinstance(state.get("selection"), dict) else None,
        (state.get("selection") or {}).get("selected_rows") if isinstance(state.get("selection"), dict) else None,
    ]
    for candidate in candidates:
        positions = normalize_selected_row_positions(candidate)
        if positions:
            return positions
    return []


def normalize_selected_row_positions(value) -> list[int]:
    if value is None:
        return []
    if isinstance(value, int):
        return [value]
    if not isinstance(value, list):
        return []

    positions = []
    for item in value:
        if isinstance(item, int):
            positions.append(item)
        elif isinstance(item, dict):
            row_index = item.get("_index")
            if row_index is None:
                row_index = item.get("index")
            if row_index is None:
                row_index = item.get("row")
            try:
                positions.append(int(row_index))
            except (TypeError, ValueError):
                continue
    return sorted(set(positions))


def render_event_product_quick_add(report_id: int, event_month: date) -> None:
    product_options = get_event_product_options(report_id, event_month)
    with st.form(key=f"meeting_event_product_form_{report_id}_{month_key(event_month)}", clear_on_submit=False):
        select_col, custom_col, add_col = st.columns([1.25, 1.6, 0.8], gap="small")
        with select_col:
            selected_products = st.multiselect(
                "행사품목 선택",
                options=product_options,
                key=f"meeting_event_product_select_{report_id}",
            )
        with custom_col:
            typed_products = st.text_input(
                "직접 입력",
                key=f"meeting_event_product_custom_{report_id}",
                placeholder="여러 품목은 쉼표로 구분",
            )
        with add_col:
            st.write("")
            st.write("")
            add_submitted = st.form_submit_button("품목 추가", use_container_width=True)
        if add_submitted:
            products = merge_unique_values([*selected_products, *split_product_text(typed_products)])
            if products:
                append_pending_event_row(
                    report_id,
                    event_month,
                    {
                        "행사명": "",
                        "행사기간": "",
                        "행사품목": ", ".join(products),
                        "요청수량": 0,
                        "상세내용": "",
                    },
                )
            else:
                st.warning("추가할 행사품목을 선택하거나 입력하세요.")


def build_event_editor_df(df: pd.DataFrame, report_id: int, event_month: date) -> pd.DataFrame:
    editor_df = normalize_df(df, EVENT_COLUMNS)
    pending_df = pd.DataFrame(get_pending_event_rows(report_id, event_month), columns=EVENT_COLUMNS)
    if not pending_df.empty:
        editor_df = pd.concat([editor_df, normalize_df(pending_df, EVENT_COLUMNS)], ignore_index=True)
    return editor_df[EVENT_COLUMNS]


def normalize_event_editor_df(df: pd.DataFrame, apply_delete: bool = False) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)

    df = drop_marked_rows(df) if apply_delete else strip_delete_marker_column(df)
    rows = []
    for _, row in df.fillna("").iterrows():
        typed_period = normalize_cell_value(row.get("행사기간", ""))
        typed_products = split_product_text(row.get("행사품목", ""))
        merged_products = merge_unique_values(typed_products)
        rows.append(
            {
                "행사명": normalize_cell_value(row.get("행사명", "")),
                "행사기간": typed_period,
                "행사품목": ", ".join(merged_products),
                "요청수량": parse_int(row.get("요청수량", "")),
                "상세내용": normalize_cell_value(row.get("상세내용", row.get("비고", ""))),
            }
        )

    return filter_filled_rows(pd.DataFrame(rows), EVENT_COLUMNS)


def merge_event_details(edited_df: pd.DataFrame, source_df: pd.DataFrame) -> pd.DataFrame:
    edited = normalize_df(edited_df, EVENT_SUMMARY_COLUMNS)
    source = normalize_df(source_df, EVENT_COLUMNS)
    if edited.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)

    used_source_indexes: set[int] = set()
    merged_rows = []
    for position, (_, row) in enumerate(edited.iterrows()):
        detail = ""
        match_index = find_matching_event_detail_index(row, source, used_source_indexes)
        if match_index is not None:
            used_source_indexes.add(match_index)
            detail = normalize_cell_value(source.iloc[match_index].get("상세내용", ""))
        elif position < len(source):
            detail = normalize_cell_value(source.iloc[position].get("상세내용", ""))

        merged_rows.append(
            {
                "행사명": row.get("행사명", ""),
                "행사기간": row.get("행사기간", ""),
                "행사품목": row.get("행사품목", ""),
                "요청수량": row.get("요청수량", 0),
                "상세내용": detail,
            }
        )
    return pd.DataFrame(merged_rows, columns=EVENT_COLUMNS)


def find_matching_event_detail_index(row: pd.Series, source: pd.DataFrame, used_indexes: set[int]) -> int | None:
    target_key = event_summary_key(row)
    for index, (_, source_row) in enumerate(source.iterrows()):
        if index in used_indexes:
            continue
        if event_summary_key(source_row) == target_key:
            return index
    return None


def event_summary_key(row) -> tuple[str, str, str, int]:
    return (
        normalize_cell_value(row.get("행사명", "")),
        normalize_cell_value(row.get("행사기간", "")),
        normalize_cell_value(row.get("행사품목", "")),
        parse_int(row.get("요청수량", "")),
    )


def split_product_text(value) -> list[str]:
    text = normalize_cell_value(value)
    if not text:
        return []
    separators = [",", "\n", "/", "|"]
    for separator in separators[1:]:
        text = text.replace(separator, ",")
    return [part.strip() for part in text.split(",") if part.strip()]


def merge_unique_values(values: list[str]) -> list[str]:
    merged = []
    seen = set()
    for value in values:
        normalized = normalize_cell_value(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)
    return merged


def render_issue_section(report: dict) -> dict[str, str]:
    st.markdown(section_title("03", "주요 이슈"), unsafe_allow_html=True)
    issue_values: dict[str, str] = {}
    cols = st.columns(3, gap="small")
    defaults = {
        "생산/재고 이슈": report["issue_delay"],
        "발주진행": report["issue_inventory"],
        "특이사항": report["issue_special"],
    }
    for col, (title, key) in zip(cols, ISSUE_KEYS.items()):
        with col:
            with st.container(key=f"meeting_issue_{key}"):
                st.markdown(f"<h3>{title}</h3>", unsafe_allow_html=True)
                issue_values[title] = st.text_area(
                    title,
                    value=defaults[title],
                    label_visibility="collapsed",
                    height=118,
                    placeholder="1. 발주내용 입력" if key == "issue_inventory" else None,
                    key=f"meeting_issue_text_{report['id']}_{key}",
                )
                if key == "issue_inventory":
                    inject_order_numbering_script(title, "1. 발주내용 입력")
    return issue_values


def inject_order_numbering_script(label: str, placeholder: str) -> None:
    components.html(
        f"""
        <script>
        (() => {{
            const label = {label!r};
            const placeholder = {placeholder!r};
            const doc = window.parent.document;
            const nativeSetter = Object.getOwnPropertyDescriptor(window.parent.HTMLTextAreaElement.prototype, "value").set;

            function bind() {{
                const areas = Array.from(doc.querySelectorAll("textarea"));
                const target = areas.find((area) => (
                    area.getAttribute("aria-label") === label ||
                    area.getAttribute("placeholder") === placeholder
                ));
                if (!target || target.dataset.orderNumberingBound === "1") return;
                target.dataset.orderNumberingBound = "1";
                target.addEventListener("keydown", (event) => {{
                    if (event.key !== "Enter" || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;

                    const start = target.selectionStart;
                    const end = target.selectionEnd;
                    const value = target.value;
                    const lineStart = value.lastIndexOf("\\n", start - 1) + 1;
                    const currentLine = value.slice(lineStart, start);
                    const match = currentLine.match(/^\\s*(\\d{{1,2}})\\s*[.)]\\s*.*\\S\\s*$/);
                    if (!match) return;

                    event.preventDefault();
                    const next = Number(match[1]) + 1;
                    const insertion = "\\n" + next + ". ";
                    const nextValue = value.slice(0, start) + insertion + value.slice(end);
                    nativeSetter.call(target, nextValue);
                    target.dispatchEvent(new Event("input", {{ bubbles: true }}));
                    target.selectionStart = target.selectionEnd = start + insertion.length;
                }});
            }}

            bind();
            new MutationObserver(bind).observe(doc.body, {{ childList: true, subtree: true }});
        }})();
        </script>
        """,
        height=0,
    )


def render_action_section(report_id: int) -> pd.DataFrame:
    st.markdown(section_title("04", "진행사항", anchor_id="meeting-actions-section"), unsafe_allow_html=True)
    saved_df = filter_filled_rows(load_table_df("meeting_action_items", report_id, ACTION_COLUMNS), ACTION_COLUMNS)
    draft_key = action_draft_state_key(report_id)
    edit_buffer_key = f"meeting_action_edit_buffer_{report_id}"
    if draft_key not in st.session_state:
        st.session_state[draft_key] = prepare_action_editor_df(saved_df)

    current_draft = prepare_action_editor_df(st.session_state[draft_key])
    if edit_buffer_key not in st.session_state:
        st.session_state[edit_buffer_key] = add_delete_marker_column(current_draft)
    edit_buffer = st.session_state[edit_buffer_key]
    preview_df = filter_filled_rows(strip_delete_marker_column(current_draft), ACTION_COLUMNS)
    st.markdown(render_table_html(ACTION_COLUMNS, preview_df, "actions"), unsafe_allow_html=True)

    with st.expander("진행사항 편집", expanded=False):
        st.caption("입력한 내용은 진행사항 저장 또는 수정 저장을 눌렀을 때만 반영됩니다. 행 삭제는 삭제 칸을 체크한 뒤 선택 삭제를 누르세요.")
        editor_df = add_delete_marker_column(prepare_action_editor_df(edit_buffer), marker_source=edit_buffer)
        editor_columns = [EDIT_DELETE_COLUMN, *ACTION_COLUMNS]
        editor_key = f"meeting_action_editor_v10_{report_id}"
        with st.form(key=f"meeting_action_editor_form_{report_id}", clear_on_submit=False):
            edited = st.data_editor(
                editor_df,
                num_rows="dynamic",
                use_container_width=True,
                key=editor_key,
                hide_index=True,
                column_order=editor_columns,
                column_config={
                    EDIT_DELETE_COLUMN: st.column_config.CheckboxColumn("삭제", default=False),
                    "담당부서/담당자": st.column_config.TextColumn("담당부서/담당자", default=""),
                    "진행내용": st.column_config.TextColumn("진행내용", default=""),
                    "수량": st.column_config.NumberColumn("수량", min_value=0, step=1, format="%d"),
                    "완료예정일": st.column_config.DateColumn("완료예정일", format="YYYY-MM-DD"),
                    "납기일": st.column_config.DateColumn("납기일", format="YYYY-MM-DD"),
                    "진행상태": st.column_config.SelectboxColumn("진행상태", options=["", *ACTION_STATUS], default=""),
                },
            )
            editor_action = render_editor_actions(
                "meeting_actions",
                save_label="진행사항 저장",
                secondary_save_label="수정 저장",
                selected_delete_label="선택 삭제",
                selected_delete_count=None,
                delete_label="진행사항 전체 삭제",
                delete_flag="meeting_delete_actions_requested",
            )
    action_source = current_draft
    if editor_action == "selected_delete":
        before_delete_count = len(strip_delete_marker_column(edited))
        if count_marked_rows(edited) > 0:
            edited = drop_marked_rows(edited)
        else:
            edited = drop_selected_editor_rows(edited, editor_key, original_count=len(current_draft))
        if len(strip_delete_marker_column(edited)) >= before_delete_count:
            st.session_state.meeting_save_requested = False
        else:
            action_source = prepare_action_editor_df(strip_delete_marker_column(edited))
            st.session_state[draft_key] = prepare_action_editor_df(action_source)
            st.session_state[edit_buffer_key] = add_delete_marker_column(action_source)
    elif editor_action == "save":
        action_source = prepare_action_editor_df(strip_delete_marker_column(edited))
        st.session_state[draft_key] = prepare_action_editor_df(action_source)
        st.session_state[edit_buffer_key] = add_delete_marker_column(action_source)
    else:
        st.session_state[edit_buffer_key] = edited
    return normalize_df(action_source, ACTION_COLUMNS)


def table_draft_state_key(report_id: int, table_key: str) -> str:
    return f"meeting_{table_key}_draft_{report_id}"


def prepare_production_editor_df(df: pd.DataFrame) -> pd.DataFrame:
    editor_df = normalize_df(strip_delete_marker_column(df), PRODUCTION_COLUMNS)
    if editor_df.empty:
        editor_df = pd.DataFrame(columns=PRODUCTION_COLUMNS)
    for column in ["바코드", "상품명", "상태", "비고"]:
        editor_df[column] = editor_df[column].apply(normalize_cell_value)
    editor_df["현재수량"] = pd.to_numeric(editor_df["현재수량"], errors="coerce").fillna(0).astype(int)
    editor_df["요청수량"] = pd.to_numeric(editor_df["요청수량"], errors="coerce").fillna(0).astype(int)
    editor_df["납기일"] = editor_df["납기일"].apply(normalize_editor_date_value)
    return editor_df[PRODUCTION_COLUMNS]


def normalize_editor_date_value(value):
    text = normalize_date_value(value)
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def prepare_event_editor_df(df: pd.DataFrame) -> pd.DataFrame:
    editor_df = normalize_event_editor_df(strip_delete_marker_column(df))
    if editor_df.empty:
        editor_df = pd.DataFrame(columns=EVENT_COLUMNS)
    for column in ["행사명", "행사기간", "행사품목", "상세내용"]:
        editor_df[column] = editor_df[column].apply(normalize_cell_value)
    editor_df["요청수량"] = pd.to_numeric(editor_df["요청수량"], errors="coerce").fillna(0).astype(int)
    return editor_df[EVENT_COLUMNS]


def action_draft_state_key(report_id: int) -> str:
    return f"meeting_action_draft_{report_id}"


def prepare_action_editor_df(df: pd.DataFrame) -> pd.DataFrame:
    editor_df = normalize_df(strip_delete_marker_column(df), ACTION_COLUMNS)
    if editor_df.empty:
        editor_df = pd.DataFrame(columns=ACTION_COLUMNS)
    for column in ["담당부서/담당자", "진행내용", "진행상태"]:
        editor_df[column] = editor_df[column].apply(normalize_cell_value)
    editor_df["수량"] = pd.to_numeric(editor_df["수량"], errors="coerce").fillna(0).astype(int)
    editor_df["완료예정일"] = editor_df["완료예정일"].apply(normalize_editor_date_value)
    editor_df["납기일"] = editor_df["납기일"].apply(normalize_editor_date_value)
    return editor_df[ACTION_COLUMNS]


def render_kpi_cards(kpis: list[dict], section_number: str = "05") -> None:
    st.markdown(section_title(section_number, "회의 KPI 요약"), unsafe_allow_html=True)
    cards = "".join(
        (
            f'<{item.get("tag", "article")} class="meeting-kpi-card {item["tone"]}"{kpi_link_attrs(item)}>'
            f'<span>{escape_html(item["label"])}</span>'
            f'<strong>{escape_html(item["value"])}</strong>'
            f'</{item.get("tag", "article")}>'
        )
        for item in kpis
    )
    render_html(f'<section class="meeting-kpi-grid">{cards}</section>')


def kpi_link_attrs(item: dict) -> str:
    href = item.get("href")
    if not href:
        return ""
    return f' href="{escape_html(href)}" target="_self" title="{escape_html(item["label"])} 보기"'


def render_html(markup: str) -> None:
    if hasattr(st, "html"):
        st.html(markup)
    else:
        st.markdown(markup, unsafe_allow_html=True)


def format_kpi_delta_html(value: str, prefix: str = "전주 대비") -> str:
    if not is_meaningful_content(value):
        return ""
    label = f"{prefix} {value}".strip() if prefix else value
    return f"<small>{escape_html(label)}</small>"


def format_kpi_delta_text(value: str, prefix: str = "전주 대비") -> str:
    if not is_meaningful_content(value):
        return ""
    return f"{prefix} {value}".strip() if prefix else value


def ensure_schema() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meeting_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_date TEXT NOT NULL UNIQUE,
                author TEXT NOT NULL,
                event_detail TEXT NOT NULL DEFAULT '',
                issue_delay TEXT NOT NULL DEFAULT '',
                issue_inventory TEXT NOT NULL DEFAULT '',
                issue_special TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS meeting_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS meeting_production_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL,
                sort_order INTEGER NOT NULL,
                production_code TEXT NOT NULL,
                product_name TEXT NOT NULL,
                current_qty INTEGER NOT NULL DEFAULT 0,
                request_qty INTEGER NOT NULL DEFAULT 0,
                due_date TEXT NOT NULL,
                status TEXT NOT NULL,
                memo TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(report_id) REFERENCES meeting_reports(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS meeting_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL,
                sort_order INTEGER NOT NULL,
                event_name TEXT NOT NULL,
                period TEXT NOT NULL,
                affected_products TEXT NOT NULL,
                request_qty INTEGER NOT NULL DEFAULT 0,
                summary TEXT NOT NULL,
                owner TEXT NOT NULL,
                memo TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(report_id) REFERENCES meeting_reports(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS meeting_action_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL,
                sort_order INTEGER NOT NULL,
                owner TEXT NOT NULL,
                content TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                due_date TEXT NOT NULL,
                delivery_date TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                FOREIGN KEY(report_id) REFERENCES meeting_reports(id) ON DELETE CASCADE
            );
            """
        )
        ensure_column(conn, "meeting_reports", "event_detail", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "meeting_production_requests", "current_qty", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "meeting_events", "request_qty", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "meeting_events", "event_month", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "meeting_action_items", "quantity", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "meeting_action_items", "delivery_date", "TEXT NOT NULL DEFAULT ''")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_meeting_events_event_month
            ON meeting_events(event_month, sort_order, id)
            """
        )
        migrate_event_months(conn)
        clear_seed_rows_once(conn)


def get_or_create_report(meeting_date: date, author: str) -> dict:
    date_key = meeting_date.isoformat()
    now = datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM meeting_reports WHERE meeting_date = ?", (date_key,)).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO meeting_reports
                    (meeting_date, author, event_detail, issue_delay, issue_inventory, issue_special, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    date_key,
                    author,
                    "",
                    "",
                    "",
                    "",
                    now,
                    now,
                ),
            )
            report_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            row = conn.execute("SELECT * FROM meeting_reports WHERE id = ?", (report_id,)).fetchone()
        return dict(row)


def load_table_df(table_name: str, report_id: int, columns: list[str]) -> pd.DataFrame:
    query_map = {
        "meeting_production_requests": """
            SELECT production_code AS 바코드, product_name AS 상품명,
                   current_qty AS 현재수량, request_qty AS 요청수량,
                   due_date AS 납기일, status AS 상태, memo AS 비고
            FROM meeting_production_requests
            WHERE report_id = ?
            ORDER BY sort_order, id
        """,
        "meeting_events": """
            SELECT event_name AS 행사명, period AS 행사기간, affected_products AS 행사품목,
                   request_qty AS 요청수량, memo AS 상세내용
            FROM meeting_events
            WHERE report_id = ?
            ORDER BY sort_order, id
        """,
        "meeting_action_items": """
            SELECT owner AS "담당부서/담당자", content AS 진행내용,
                   quantity AS 수량, due_date AS 완료예정일,
                   delivery_date AS 납기일, status AS 진행상태
            FROM meeting_action_items
            WHERE report_id = ?
            ORDER BY sort_order, id
        """,
    }
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(query_map[table_name], conn, params=(report_id,))
    return normalize_df(df, columns)


def load_event_month_df(event_month: date) -> pd.DataFrame:
    query = """
        SELECT event_name AS 행사명, period AS 행사기간, affected_products AS 행사품목,
               request_qty AS 요청수량, memo AS 상세내용
        FROM meeting_events
        WHERE event_month = ?
        ORDER BY sort_order, id
    """
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(query, conn, params=(month_key(event_month),))
    return normalize_df(df, EVENT_COLUMNS)


def meeting_history_excel_bytes() -> tuple[bytes, int]:
    frames = load_meeting_history_frames()
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        workbook = writer.book
        title_format = workbook.add_format(
            {
                "bold": True,
                "font_size": 15,
                "font_color": "#FFFFFF",
                "bg_color": "#07544B",
                "align": "center",
                "valign": "vcenter",
            }
        )
        header_format = workbook.add_format(
            {
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": "#0B6B60",
                "border": 1,
                "align": "center",
                "valign": "vcenter",
            }
        )
        cell_format = workbook.add_format({"border": 1, "border_color": "#E5EFEA", "valign": "top", "text_wrap": True})
        number_format = workbook.add_format({"border": 1, "border_color": "#E5EFEA", "valign": "top", "num_format": "#,##0"})

        for sheet_name, df in frames.items():
            safe_sheet_name = sheet_name[:31]
            df.to_excel(writer, index=False, sheet_name=safe_sheet_name, startrow=1)
            worksheet = writer.sheets[safe_sheet_name]
            last_col = max(len(df.columns) - 1, 0)
            if last_col:
                worksheet.merge_range(0, 0, 0, last_col, f"회의자료 {sheet_name}", title_format)
            elif len(df.columns):
                worksheet.write(0, 0, f"회의자료 {sheet_name}", title_format)
            for idx, column in enumerate(df.columns):
                worksheet.write(1, idx, column, header_format)
                width = meeting_history_column_width(df[column] if column in df else pd.Series(dtype=str), column)
                fmt = number_format if column in {"현재수량", "요청수량", "수량", "생산요청 건수", "행사 건수", "진행사항 건수"} else cell_format
                worksheet.set_column(idx, idx, width, fmt)
            worksheet.freeze_panes(2, 0)
            if len(df.columns):
                worksheet.autofilter(1, 0, max(len(df) + 1, 1), last_col)

    return output.getvalue(), len(frames["요약"])


def load_meeting_history_frames() -> dict[str, pd.DataFrame]:
    with sqlite3.connect(DB_PATH) as conn:
        report_ids = meaningful_report_ids(conn)
        if not report_ids:
            empty_summary = pd.DataFrame(
                columns=["회의일", "작성자", "생산요청 건수", "행사 건수", "진행사항 건수", "생산/재고 이슈", "발주진행", "특이사항", "수정일시"]
            )
            return {
                "요약": empty_summary,
                "생산요청": pd.DataFrame(columns=["회의일", "작성자", *PRODUCTION_COLUMNS]),
                "행사일정": pd.DataFrame(columns=["회의일", "작성자", "행사월", *EVENT_COLUMNS]),
                "진행사항": pd.DataFrame(columns=["회의일", "작성자", *ACTION_COLUMNS]),
            }

        placeholders = ",".join("?" for _ in report_ids)
        summary = pd.read_sql_query(
            f"""
            SELECT
                report.meeting_date AS 회의일,
                report.author AS 작성자,
                COUNT(DISTINCT production.id) AS '생산요청 건수',
                COUNT(DISTINCT event.id) AS '행사 건수',
                COUNT(DISTINCT action.id) AS '진행사항 건수',
                report.issue_delay AS '생산/재고 이슈',
                report.issue_inventory AS 발주진행,
                report.issue_special AS 특이사항,
                report.updated_at AS 수정일시
            FROM meeting_reports report
            LEFT JOIN meeting_production_requests production ON production.report_id = report.id
            LEFT JOIN meeting_events event ON event.report_id = report.id
            LEFT JOIN meeting_action_items action ON action.report_id = report.id
            WHERE report.id IN ({placeholders})
            GROUP BY report.id
            ORDER BY report.meeting_date DESC
            """,
            conn,
            params=report_ids,
        )
        production = pd.read_sql_query(
            f"""
            SELECT report.meeting_date AS 회의일, report.author AS 작성자,
                   item.production_code AS 바코드, item.product_name AS 상품명,
                   item.current_qty AS 현재수량, item.request_qty AS 요청수량, item.due_date AS 납기일,
                   item.status AS 상태, item.memo AS 비고
            FROM meeting_production_requests item
            JOIN meeting_reports report ON report.id = item.report_id
            WHERE report.id IN ({placeholders})
            ORDER BY report.meeting_date DESC, item.sort_order, item.id
            """,
            conn,
            params=report_ids,
        )
        events = pd.read_sql_query(
            f"""
            SELECT report.meeting_date AS 회의일, report.author AS 작성자,
                   item.event_month AS 행사월, item.event_name AS 행사명,
                   item.period AS 행사기간, item.affected_products AS 행사품목,
                   item.request_qty AS 요청수량, item.memo AS 상세내용
            FROM meeting_events item
            JOIN meeting_reports report ON report.id = item.report_id
            WHERE report.id IN ({placeholders})
            ORDER BY report.meeting_date DESC, item.event_month DESC, item.sort_order, item.id
            """,
            conn,
            params=report_ids,
        )
        actions = pd.read_sql_query(
            f"""
            SELECT report.meeting_date AS 회의일, report.author AS 작성자,
                   item.owner AS "담당부서/담당자", item.content AS 진행내용,
                   item.quantity AS 수량, item.due_date AS 완료예정일,
                   item.delivery_date AS 납기일,
                   item.status AS 진행상태
            FROM meeting_action_items item
            JOIN meeting_reports report ON report.id = item.report_id
            WHERE report.id IN ({placeholders})
            ORDER BY report.meeting_date DESC, item.sort_order, item.id
            """,
            conn,
            params=report_ids,
        )
    return {"요약": summary, "생산요청": production, "행사일정": events, "진행사항": actions}


def meaningful_report_ids(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        """
        SELECT report.id
        FROM meeting_reports report
        WHERE TRIM(report.issue_delay) != ''
           OR TRIM(report.issue_inventory) != ''
           OR TRIM(report.issue_special) != ''
           OR EXISTS (SELECT 1 FROM meeting_production_requests item WHERE item.report_id = report.id)
           OR EXISTS (SELECT 1 FROM meeting_events item WHERE item.report_id = report.id)
           OR EXISTS (SELECT 1 FROM meeting_action_items item WHERE item.report_id = report.id)
        ORDER BY report.meeting_date DESC
        """
    ).fetchall()
    return [row[0] for row in rows]


def meeting_history_column_width(series: pd.Series, column: str) -> int:
    base_widths = {
        "회의일": 14,
        "작성자": 12,
        "행사월": 12,
        "바코드": 18,
        "상품명": 32,
        "행사명": 24,
        "행사기간": 24,
        "행사품목": 34,
        "진행내용": 42,
        "상세내용": 42,
        "생산/재고 이슈": 38,
        "발주진행": 38,
        "특이사항": 38,
        "비고": 30,
        "수정일시": 22,
    }
    if column in base_widths:
        return base_widths[column]
    values = [len(str(column)), *(len(str(value)) for value in series.fillna("").head(100))]
    return min(max(max(values, default=12) + 2, 10), 32)


def normalize_df(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=columns)
    normalized = df.copy()
    for column in columns:
        if column not in normalized.columns:
            normalized[column] = ""
    normalized = normalized[columns].fillna("")
    for column in normalized.columns:
        if column not in {"현재수량", "요청수량", "수량"}:
            normalized[column] = normalized[column].astype(str)
    return normalized


def filter_filled_rows(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    normalized = normalize_df(df, columns)
    if normalized.empty:
        return normalized
    meaningful_columns = [column for column in columns if column != "수량"] or columns
    keep_rows = []
    for _, row in normalized.iterrows():
        has_value = any(normalize_cell_value(row[column]).strip() for column in meaningful_columns)
        keep_rows.append(has_value)
    return normalized.loc[keep_rows].reset_index(drop=True)


def normalize_cell_value(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    text = str(value).strip()
    if text.lower() in {"nan", "nat", "none"}:
        return ""
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def parse_int(value) -> int:
    text = normalize_cell_value(value)
    if not text:
        return 0
    try:
        return int(float(text.replace(",", "")))
    except ValueError:
        return 0


def get_event_product_options(report_id: int, event_month: date) -> list[str]:
    products = list(EVENT_PRODUCT_OPTIONS)
    try:
        with sqlite3.connect(DB_PATH) as conn:
            products.extend(
                row[0]
                for row in conn.execute(
                    """
                    SELECT DISTINCT product_name
                    FROM meeting_production_requests
                    WHERE report_id = ? AND TRIM(product_name) != ''
                    """,
                    (report_id,),
                ).fetchall()
            )
            event_product_rows = conn.execute(
                """
                SELECT DISTINCT affected_products
                FROM meeting_events
                WHERE event_month = ? AND TRIM(affected_products) != ''
                """,
                (month_key(event_month),),
            ).fetchall()
    except sqlite3.Error:
        event_product_rows = []

    for row in event_product_rows:
        products.extend(split_product_text(row[0]))
    for row in get_pending_event_rows(report_id, event_month):
        products.extend(split_product_text(row.get("행사품목", "")))
    return merge_unique_values(products)


def pending_event_rows_key(report_id: int, event_month: date) -> str:
    return f"meeting_events_pending_rows_{report_id}_{month_key(event_month)}"


def get_pending_event_rows(report_id: int, event_month: date) -> list[dict]:
    rows = st.session_state.get(pending_event_rows_key(report_id, event_month), [])
    return rows if isinstance(rows, list) else []


def append_pending_event_row(report_id: int, event_month: date, row: dict) -> None:
    rows = [*get_pending_event_rows(report_id, event_month)]
    rows.append({column: row.get(column, "") for column in EVENT_COLUMNS})
    st.session_state[pending_event_rows_key(report_id, event_month)] = rows


def clear_pending_event_rows(report_id: int, event_month: date) -> None:
    st.session_state.pop(pending_event_rows_key(report_id, event_month), None)


def ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def migrate_event_months(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE meeting_events
        SET event_month = COALESCE(
            (
                SELECT substr(report.meeting_date, 1, 7)
                FROM meeting_reports report
                WHERE report.id = meeting_events.report_id
            ),
            strftime('%Y-%m', 'now')
        )
        WHERE TRIM(COALESCE(event_month, '')) = ''
        """
    )


def clear_seed_rows_once(conn: sqlite3.Connection) -> None:
    done = conn.execute("SELECT value FROM meeting_meta WHERE key = 'seed_cleanup_done'").fetchone()
    if done:
        return
    clear_seed_rows(conn)
    conn.execute(
        "INSERT OR REPLACE INTO meeting_meta (key, value) VALUES ('seed_cleanup_done', ?)",
        (datetime.now().isoformat(timespec="seconds"),),
    )


def clear_seed_rows(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        DELETE FROM meeting_production_requests
        WHERE production_code IN ('P240701', 'P240702', 'P240703', 'P240704', 'P240705')
          AND product_name IN ('SKUA', 'SKUB', 'SKUC', 'SKUD', 'SKUE')
        """
    )
    conn.execute(
        """
        DELETE FROM meeting_events
        WHERE event_name IN ('여름세일', '라이브방송', '브랜드데이')
          AND affected_products IN ('SKUA, SKUB', 'SKUC', 'SKUD, SKUE')
        """
    )
    conn.execute(
        """
        DELETE FROM meeting_action_items
        WHERE content IN ('SKUA 생산 일정 준수', '지재 발주 및 입고 관리', '행사별 재고 확보', '프로모션 일정 공유')
        """
    )
    conn.execute(
        """
        UPDATE meeting_reports
        SET issue_delay = '', issue_inventory = '', issue_special = ''
        WHERE issue_delay = '- SKUB 원부자재 입고 지연으로 생산 일정 지연'
          AND issue_inventory = '- SKUC 현재 재고가 안전재고 이하'
          AND issue_special = '- 신규 거래처 출고 시작'
        """
    )


def save_report_state(
    report_id: int,
    meta: dict,
    production_df: pd.DataFrame,
    events_df: pd.DataFrame,
    issues: dict[str, str],
    action_df: pd.DataFrame,
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE meeting_reports
            SET author = ?, event_detail = ?, issue_delay = ?, issue_inventory = ?, issue_special = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                meta["author"],
                "",
                issue_value(issues, "생산/재고 이슈"),
                issue_value(issues, "발주진행"),
                issue_value(issues, "특이사항"),
                now,
                report_id,
            ),
        )
        replace_rows(conn, "meeting_production_requests", report_id, production_df, PRODUCTION_COLUMNS)
        replace_event_month_rows(conn, report_id, meta.get("event_month") or month_start(meta["meeting_date"]), events_df)
        replace_rows(conn, "meeting_action_items", report_id, action_df, ACTION_COLUMNS)


def delete_report_section(report_id: int, table_name: str) -> None:
    allowed_tables = {"meeting_production_requests", "meeting_events", "meeting_action_items"}
    if table_name not in allowed_tables:
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(f"DELETE FROM {table_name} WHERE report_id = ?", (report_id,))


def clear_editor_state(key: str) -> None:
    st.session_state.pop(key, None)


def clear_event_detail_editor_states(report_id: int, event_month: date) -> None:
    key_prefix = f"meeting_event_detail_{report_id}_{month_key(event_month)}_"
    for key in list(st.session_state.keys()):
        if str(key).startswith(key_prefix):
            clear_editor_state(key)


def update_event_detail_draft(
    report_id: int,
    event_month: date,
    events_df: pd.DataFrame,
    selected_index: int,
    detail: str,
) -> pd.DataFrame:
    updated_df = prepare_event_editor_df(events_df)
    if updated_df.empty or selected_index < 0 or selected_index >= len(updated_df):
        return updated_df

    updated_df.at[updated_df.index[selected_index], "상세내용"] = normalize_cell_value(detail)
    st.session_state[table_draft_state_key(report_id, f"events_{month_key(event_month)}")] = prepare_event_editor_df(updated_df)
    return updated_df


def delete_event_month(event_month: date) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM meeting_events WHERE event_month = ?", (month_key(event_month),))


def replace_event_month_rows(conn: sqlite3.Connection, report_id: int, event_month: date, df: pd.DataFrame) -> None:
    conn.execute("DELETE FROM meeting_events WHERE event_month = ?", (month_key(event_month),))
    cleaned = filter_filled_rows(df, EVENT_COLUMNS)
    if cleaned.empty:
        return
    rows = [
        (
            report_id,
            month_key(event_month),
            index,
            row["행사명"],
            normalize_date_value(row["행사기간"]),
            row["행사품목"],
            parse_int(row["요청수량"]),
            "",
            "",
            row["상세내용"],
        )
        for index, row in cleaned.iterrows()
        if any(normalize_cell_value(row[column]) for column in EVENT_COLUMNS)
    ]
    conn.executemany(
        """
        INSERT INTO meeting_events
            (report_id, event_month, sort_order, event_name, period, affected_products, request_qty, summary, owner, memo)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def replace_rows(conn: sqlite3.Connection, table_name: str, report_id: int, df: pd.DataFrame, columns: list[str]) -> None:
    conn.execute(f"DELETE FROM {table_name} WHERE report_id = ?", (report_id,))
    cleaned = filter_filled_rows(df, columns)
    if cleaned.empty:
        return
    if table_name == "meeting_production_requests":
        rows = [
            (
                report_id,
                index,
                normalize_cell_value(row["바코드"]),
                row["상품명"],
                parse_int(row["현재수량"]),
                parse_int(row["요청수량"]),
                normalize_date_value(row["납기일"]),
                row["상태"],
                row["비고"],
            )
            for index, row in cleaned.iterrows()
            if any(normalize_cell_value(row[column]) for column in columns)
        ]
        conn.executemany(
            """
            INSERT INTO meeting_production_requests
                (report_id, sort_order, production_code, product_name, current_qty, request_qty, due_date, status, memo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    elif table_name == "meeting_events":
        report_month_row = conn.execute("SELECT substr(meeting_date, 1, 7) FROM meeting_reports WHERE id = ?", (report_id,)).fetchone()
        event_month = report_month_row[0] if report_month_row and report_month_row[0] else month_key(date.today())
        rows = [
            (
                report_id,
                event_month,
                index,
                row["행사명"],
                normalize_date_value(row["행사기간"]),
                row["행사품목"],
                parse_int(row["요청수량"]),
                "",
                "",
                row["상세내용"],
            )
            for index, row in cleaned.iterrows()
            if any(normalize_cell_value(row[column]) for column in columns)
        ]
        conn.executemany(
            """
            INSERT INTO meeting_events
                (report_id, event_month, sort_order, event_name, period, affected_products, request_qty, summary, owner, memo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    else:
        rows = [
            (
                report_id,
                index,
                row["담당부서/담당자"],
                row["진행내용"],
                parse_int(row["수량"]),
                normalize_date_value(row["완료예정일"]),
                normalize_date_value(row["납기일"]),
                row["진행상태"],
            )
            for index, row in cleaned.iterrows()
            if any(normalize_cell_value(row[column]) for column in columns)
        ]
        conn.executemany(
            """
            INSERT INTO meeting_action_items
                (report_id, sort_order, owner, content, quantity, due_date, delivery_date, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def normalize_date_value(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text or text.lower() in {"nat", "nan", "none"}:
        return ""
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return text
    return parsed.date().isoformat()


def build_kpis(
    meeting_date: date,
    event_month: date,
    production_df: pd.DataFrame,
    events_df: pd.DataFrame,
    issues: dict[str, str],
    action_df: pd.DataFrame,
) -> list[dict]:
    order_lines = count_issue_lines(issue_value(issues, "발주진행"))
    production_count = count_meaningful_rows(production_df, ["바코드", "상품명", "납기일", "상태", "비고"])
    event_count = count_meaningful_rows(events_df, ["행사명", "행사기간", "행사품목", "상세내용"])
    action_count = count_action_rows(action_df)
    previous_action_count = count_previous_week_action_rows(meeting_date)
    return_case_count, previous_return_case_count = count_weekly_return_cases(meeting_date)
    production_detail = summarize_production_items(production_df)
    event_detail = summarize_event_items(events_df)
    order_detail = summarize_issue_items(issue_value(issues, "발주진행"))
    action_detail = summarize_action_items(action_df)
    return_case_detail = summarize_weekly_return_case_items(meeting_date)
    return [
        {
            "label": "생산요청 건수",
            "value": f"{production_count}건",
            "detail": production_detail,
            "delta": "",
            "tone": "blue",
        },
        {
            "label": "월간 행사 일정",
            "value": f"{event_count}건",
            "detail": event_detail,
            "delta": summarize_event_month(events_df, event_month),
            "delta_prefix": "",
            "tone": "cyan",
            "tag": "a",
            "href": "#meeting-events-section",
        },
        {
            "label": "발주진행",
            "value": f"{order_lines}건",
            "detail": order_detail,
            "delta": "",
            "tone": "orange",
        },
        {
            "label": "진행사항",
            "value": f"{action_count}건",
            "detail": action_detail,
            "delta": format_delta(action_count - previous_action_count),
            "tone": "green",
            "tag": "a",
            "href": "#meeting-actions-section",
        },
        {
            "label": "반품/AS 주요건",
            "value": f"{return_case_count}건",
            "detail": return_case_detail,
            "delta": format_delta(return_case_count - previous_return_case_count),
            "tone": "purple",
            "tag": "a",
            "href": "?" + urlencode({"page": "반품/AS 관리"}),
        },
    ]


def summarize_production_items(production_df: pd.DataFrame) -> str:
    normalized = filter_filled_rows(production_df, PRODUCTION_COLUMNS)
    values = []
    for _, row in normalized.iterrows():
        product = normalize_cell_value(row.get("상품명", ""))
        code = normalize_cell_value(row.get("바코드", ""))
        values.append(product or code)
    return summarize_values(values, empty_text="생산요청 없음")


def summarize_event_items(events_df: pd.DataFrame) -> str:
    normalized = filter_filled_rows(events_df, EVENT_COLUMNS)
    values = []
    for _, row in normalized.iterrows():
        name = normalize_cell_value(row.get("행사명", ""))
        products = normalize_cell_value(row.get("행사품목", ""))
        values.append(name or products)
    return summarize_values(values, empty_text="행사 일정 없음")


def summarize_issue_items(text: str) -> str:
    values = format_numbered_issue_items(split_issue_items(text))
    return summarize_values(values, empty_text="발주진행 없음")


def summarize_action_items(action_df: pd.DataFrame) -> str:
    normalized = filter_filled_rows(action_df, ACTION_COLUMNS)
    values = []
    for _, row in normalized.iterrows():
        owner = normalize_cell_value(row.get("담당부서/담당자", ""))
        content = normalize_cell_value(row.get("진행내용", ""))
        values.append(f"{owner}: {content}" if owner and content else content or owner)
    return summarize_values(values, empty_text="진행사항 없음")


def summarize_weekly_return_case_items(meeting_date: date) -> str:
    start, end = week_range(meeting_date)
    values = list_return_case_summaries_between(start, end)
    return summarize_values(values, empty_text="반품/AS 없음")


def summarize_values(values: list[str], limit: int = 2, empty_text: str = "-") -> str:
    meaningful = [normalize_cell_value(value) for value in values if is_meaningful_content(value)]
    if not meaningful:
        return empty_text
    shown = [truncate_text(value, 18) for value in meaningful[:limit]]
    extra_count = max(len(meaningful) - len(shown), 0)
    suffix = f" 외 {extra_count}건" if extra_count else ""
    return "\n".join(shown) + suffix


def count_issue_lines(text: str) -> int:
    return len(split_issue_items(text))


def split_issue_items(text: str) -> list[str]:
    value = normalize_cell_value(text)
    if not value:
        return []

    numbered_pattern = re.compile(r"(?<!\S)(\d{1,2})\s*[.)]\s*(?=\S)")
    matches = list(numbered_pattern.finditer(value))
    if matches:
        items = []
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
            body = value[start:end].strip(" \n\r\t,")
            body = re.sub(r"\s*\n\s*", " ", body)
            body = re.sub(r"\s+", " ", body).strip()
            if is_meaningful_content(body):
                items.append(body)
        if items:
            return items

    return []


def format_numbered_issue_items(items: list[str]) -> list[str]:
    return [f"{index}. {item}" for index, item in enumerate(items, start=1)]


def format_issue_pdf_value(title: str, value: str) -> str:
    if title == "발주진행":
        items = split_issue_items(value)
        if items:
            return "\n".join(format_numbered_issue_items(items))
    return normalize_cell_value(value)


def issue_value(issues: dict[str, str], title: str) -> str:
    for key in ISSUE_ALIASES.get(title, (title,)):
        value = normalize_cell_value(issues.get(key, ""))
        if value:
            return value
    return ""


def count_meaningful_rows(df: pd.DataFrame, columns: list[str]) -> int:
    normalized = normalize_df(df, columns)
    if normalized.empty:
        return 0
    return sum(
        any(is_meaningful_content(row.get(column, "")) for column in columns)
        for _, row in normalized.iterrows()
    )


def count_action_rows(df: pd.DataFrame) -> int:
    normalized = normalize_df(df, ACTION_COLUMNS)
    if normalized.empty:
        return 0
    return count_meaningful_rows(normalized, ["담당부서/담당자", "진행내용", "완료예정일", "납기일", "진행상태"])


def summarize_event_month(events_df: pd.DataFrame, event_month: date) -> str:
    normalized = filter_filled_rows(events_df, EVENT_COLUMNS)
    if normalized.empty:
        return f"{event_month:%Y.%m} 저장 내역 없음"
    names = [
        normalize_cell_value(row.get("행사명", "")) or normalize_cell_value(row.get("행사품목", "")) or "행사"
        for _, row in normalized.head(2).iterrows()
    ]
    extra_count = max(len(normalized) - len(names), 0)
    summary = ", ".join(truncate_text(name, 12) for name in names)
    if extra_count:
        summary = f"{summary} 외 {extra_count}건"
    return f"{event_month:%Y.%m} {summary}"


def count_previous_week_action_rows(meeting_date: date) -> int:
    previous_start, previous_end = week_range(meeting_date - timedelta(days=7))
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT item.owner, item.content, item.quantity, item.due_date, item.delivery_date, item.status
            FROM meeting_action_items item
            JOIN meeting_reports report ON report.id = item.report_id
            WHERE report.meeting_date BETWEEN ? AND ?
            """,
            (previous_start.isoformat(), previous_end.isoformat()),
        ).fetchall()
    df = pd.DataFrame(
        [
            {
                "담당부서/담당자": row["owner"],
                "진행내용": row["content"],
                "수량": row["quantity"],
                "완료예정일": row["due_date"],
                "납기일": row["delivery_date"],
                "진행상태": row["status"],
            }
            for row in rows
        ],
        columns=ACTION_COLUMNS,
    )
    return count_action_rows(df)


def week_range(value: date) -> tuple[date, date]:
    start = value - timedelta(days=value.weekday())
    return start, start + timedelta(days=6)


def count_weekly_return_cases(meeting_date: date) -> tuple[int, int]:
    current_start, current_end = week_range(meeting_date)
    previous_start, previous_end = week_range(meeting_date - timedelta(days=7))
    return (
        count_return_cases_between(current_start, current_end),
        count_return_cases_between(previous_start, previous_end),
    )


def count_return_cases_between(start_date: date, end_date: date) -> int:
    if not RETURN_CASE_DB_PATH.exists():
        return 0
    try:
        with sqlite3.connect(RETURN_CASE_DB_PATH) as conn:
            rows = conn.execute("SELECT case_id FROM cases WHERE case_id IS NOT NULL").fetchall()
    except sqlite3.Error:
        return 0
    count = 0
    for (case_id,) in rows:
        case_date = parse_case_id_date(case_id)
        if case_date and start_date <= case_date <= end_date:
            count += 1
    return count


def list_return_case_summaries_between(start_date: date, end_date: date) -> list[str]:
    if not RETURN_CASE_DB_PATH.exists():
        return []
    try:
        with sqlite3.connect(RETURN_CASE_DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT case_id, category, product
                FROM cases
                WHERE case_id IS NOT NULL
                ORDER BY case_id DESC
                """
            ).fetchall()
    except sqlite3.Error:
        return []

    values = []
    for case_id, category, product in rows:
        case_date = parse_case_id_date(case_id)
        if not case_date or not (start_date <= case_date <= end_date):
            continue
        category_text = normalize_cell_value(category)
        product_text = normalize_cell_value(product)
        label = normalize_cell_value(case_id)
        if category_text:
            label = f"{label} {category_text}"
        if product_text:
            label = f"{label} / {product_text}"
        values.append(label)
    return values


def parse_case_id_date(case_id) -> date | None:
    text = normalize_cell_value(case_id)
    if len(text) < 8 or not text[:8].isdigit():
        return None
    try:
        return datetime.strptime(text[:8], "%Y%m%d").date()
    except ValueError:
        return None


def format_delta(delta: int) -> str:
    if delta > 0:
        return f"+{delta}"
    if delta < 0:
        return str(delta)
    return "0"


def is_meaningful_content(value) -> bool:
    text = normalize_cell_value(value)
    return bool(text and text not in {"-", "–", "—"})


def section_title(number: str, title: str, anchor_id: str | None = None) -> str:
    id_attr = f' id="{escape_html(anchor_id)}"' if anchor_id else ""
    return f'<div{id_attr} class="meeting-section-title"><span>{number}</span><h2>{title}</h2></div>'


def event_calendar_state_key(report_id: int) -> str:
    return f"meeting_event_calendar_month_{report_id}"


def event_calendar_base_key(report_id: int) -> str:
    return f"meeting_event_calendar_base_{report_id}"


def month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def month_key(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year = month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def render_event_calendar_controls(report_id: int, meeting_date: date) -> date:
    state_key = event_calendar_state_key(report_id)
    base_key = event_calendar_base_key(report_id)
    query_key = f"meeting_event_calendar_query_{report_id}"
    current_base = meeting_date.isoformat()
    default_month = month_start(meeting_date)
    query_month_value = meeting_query_value("meeting_event_month")
    query_month = parse_query_month(query_month_value, default_month)

    if st.session_state.get(base_key) != current_base:
        st.session_state[base_key] = current_base
        st.session_state[state_key] = query_month.isoformat()
        st.session_state[query_key] = query_month_value
    elif query_month_value and st.session_state.get(query_key) != query_month_value:
        st.session_state[state_key] = query_month.isoformat()
        st.session_state[query_key] = query_month_value

    calendar_month = parse_stored_month(st.session_state.get(state_key), default_month)
    prev_col, reset_col, next_col, spacer = st.columns([0.72, 0.72, 0.72, 4.8], gap="small")
    with prev_col:
        if st.button("‹ 이전월", key=f"meeting_event_calendar_prev_{report_id}", use_container_width=True):
            calendar_month = add_months(calendar_month, -1)
            st.session_state[state_key] = calendar_month.isoformat()
    with reset_col:
        if st.button("당월", key=f"meeting_event_calendar_reset_{report_id}", use_container_width=True):
            calendar_month = default_month
            st.session_state[state_key] = calendar_month.isoformat()
    with next_col:
        if st.button("다음월 ›", key=f"meeting_event_calendar_next_{report_id}", use_container_width=True):
            calendar_month = add_months(calendar_month, 1)
            st.session_state[state_key] = calendar_month.isoformat()
    with spacer:
        st.empty()
    return calendar_month


def parse_stored_month(value, default_month: date) -> date:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return default_month
    return date(parsed.year, parsed.month, 1)


def parse_query_month(value: str, default_month: date) -> date:
    text = normalize_cell_value(value)
    if re.fullmatch(r"\d{4}-\d{2}", text):
        text = f"{text}-01"
    return parse_stored_month(text, default_month)


def meeting_query_value(name: str) -> str:
    value = st.query_params.get(name)
    if isinstance(value, list):
        return value[0] if value else ""
    return value or ""


def selected_event_index(report_id: int, df: pd.DataFrame, event_month: date) -> int | None:
    state_key = f"meeting_selected_event_row_{report_id}_{month_key(event_month)}"
    query_value = meeting_query_value("meeting_event_row")
    query_month_value = meeting_query_value("meeting_event_month")
    query_matches_month = not query_month_value or month_key(parse_query_month(query_month_value, event_month)) == month_key(event_month)
    if query_value != "" and query_matches_month:
        try:
            st.session_state[state_key] = int(query_value)
        except ValueError:
            st.session_state.pop(state_key, None)

    selected = st.session_state.get(state_key)
    if not isinstance(selected, int) or df is None or df.empty:
        return None
    if selected < 0 or selected >= len(df):
        st.session_state.pop(state_key, None)
        return None
    return selected


def render_event_calendar_html(
    df: pd.DataFrame,
    display_month: date,
    meeting_date: date,
    report_id: int | None = None,
    selected_index: int | None = None,
) -> str:
    month_weeks = calendar.monthcalendar(display_month.year, display_month.month)
    events_by_day: dict[int, list[str]] = {}
    for row_position, (_, row) in enumerate(df.iterrows()):
        label = str(row.get("행사명", "")).strip() or "행사"
        sku = str(row.get("행사품목", "")).strip()
        detail = truncate_text(normalize_cell_value(row.get("상세내용", row.get("비고", ""))), 42)
        for day in extract_event_days(str(row.get("행사기간", "")), display_month):
            chip_body = (
                f"{escape_html(label)}"
                + (f"<small>{escape_html(truncate_text(sku, 32))}</small>" if sku else "")
                + (f"<em>{escape_html(detail)}</em>" if detail else "")
            )
            chip_class = "meeting-event-chip"
            if selected_index == row_position:
                chip_class += " selected"
            if report_id is None:
                chip = f'<span class="{chip_class}">{chip_body}</span>'
            else:
                params = {
                    "page": "회의자료",
                    "meeting_event_month": month_key(display_month),
                    "meeting_event_row": str(row_position),
                    "meeting_event_day": f"{display_month.year:04d}-{display_month.month:02d}-{day:02d}",
                }
                chip = f'<a class="{chip_class}" href="?{urlencode(params)}">{chip_body}</a>'
            events_by_day.setdefault(day, []).append(chip)

    week_labels = "".join(f"<b>{day}</b>" for day in ["월", "화", "수", "목", "금", "토", "일"])
    cells = []
    for week in month_weeks:
        for day in week:
            if day == 0:
                cells.append('<div class="meeting-calendar-day muted"></div>')
                continue
            chips = "".join(events_by_day.get(day, [])[:3])
            state = "today" if display_month.year == meeting_date.year and display_month.month == meeting_date.month and day == meeting_date.day else ""
            cells.append(
                f"""
                <div class="meeting-calendar-day {state}">
                    <strong>{day}</strong>
                    <div>{chips}</div>
                </div>
                """
            )

    return f"""
    <section class="meeting-calendar">
        <div class="meeting-calendar-head">
            <h3>{display_month:%Y년 %m월} 행사 캘린더</h3>
            <span>회의일 {format_korean_date(meeting_date)}</span>
        </div>
        <div class="meeting-calendar-week">{week_labels}</div>
        <div class="meeting-calendar-grid">{''.join(cells)}</div>
    </section>
    """


def extract_event_days(period: str, display_month: date) -> list[int]:
    start, end = parse_event_period_range_for_month(period, display_month)
    if start is None:
        return []
    end = end or start
    if end < start:
        start, end = end, start
    days = []
    cursor = start
    while cursor <= end:
        if cursor.year == display_month.year and cursor.month == display_month.month:
            days.append(cursor.day)
        cursor += timedelta(days=1)
    return days


def parse_event_period_range_for_month(period: str, display_month: date) -> tuple[date | None, date | None]:
    text = normalize_event_period_text(period)
    if not text:
        return None, None
    parts = [part for part in text.split("~") if part]
    if not parts:
        return None, None
    start = parse_period_date_part(parts[0], display_month)
    end = parse_period_date_part(parts[-1], display_month) if len(parts) > 1 else start
    return start, end


def normalize_event_period_text(period: str) -> str:
    text = normalize_cell_value(period)
    if not text:
        return ""
    text = text.replace("부터", "~").replace("까지", "")
    text = text.replace("–", "~").replace("—", "~").replace("∼", "~")
    text = re.sub(r"\s+[-~]\s+", "~", text)
    text = re.sub(r"\s+", "", text)
    return text


def parse_period_date_part(value: str, display_month: date) -> date | None:
    value = normalize_cell_value(value).strip(".")
    candidates = []
    if re.fullmatch(r"\d{1,2}[/-]\d{1,2}", value):
        month, day = re.split(r"[/-]", value)
        candidates.append(f"{display_month.year}-{int(month):02d}-{int(day):02d}")
    elif re.fullmatch(r"\d{1,2}\.\d{1,2}", value):
        month, day = value.split(".")
        candidates.append(f"{display_month.year}-{int(month):02d}-{int(day):02d}")
    elif re.fullmatch(r"\d{1,2}", value):
        candidates.append(f"{display_month.year}-{display_month.month:02d}-{int(value):02d}")
    candidates.append(value)
    for candidate in candidates:
        parsed = pd.to_datetime(candidate, errors="coerce")
        if not pd.isna(parsed):
            return parsed.date()
    return None


def render_table_html(columns: list[str], df: pd.DataFrame, table_type: str) -> str:
    head = "".join(f"<th>{column}</th>" for column in columns)
    rows = []
    for _, row in df.iterrows():
        cells = []
        for column in columns:
            value = str(row.get(column, ""))
            if table_type == "events" and column == "상세내용":
                value = truncate_text(value, 56)
            if column in {"상태", "진행상태"}:
                cells.append(f'<td>{status_badge(value)}</td>')
            else:
                cells.append(f"<td>{escape_html(value)}</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    empty = f'<tr><td colspan="{len(columns)}" class="empty-cell">등록된 데이터가 없습니다.</td></tr>'
    return f"""
    <div class="meeting-table-wrap {table_type}">
        <table>
            <thead><tr>{head}</tr></thead>
            <tbody>{''.join(rows) if rows else empty}</tbody>
        </table>
    </div>
    """


def render_note_html(text: str) -> str:
    lines = "".join(f"<p>{escape_html(line)}</p>" for line in text.splitlines() if line.strip())
    return f'<div class="meeting-note-box">{lines or "<p></p>"}</div>'


def truncate_text(value, limit: int = 50) -> str:
    text = normalize_cell_value(value).replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 1)].rstrip() + "…"


def status_badge(status: str) -> str:
    if not status:
        return ""
    tone_map = {
        "생산중": "blue",
        "생산완료": "green",
        "대기": "gray",
        "지연": "red",
        "진행중": "blue",
        "완료": "green",
    }
    tone = tone_map.get(status, "gray")
    return f'<span class="meeting-status-badge {tone}">{escape_html(status)}</span>'


def escape_html(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def next_tuesday(today: date) -> date:
    return today + timedelta(days=(1 - today.weekday()) % 7)


def create_meeting_pdf(
    meta: dict,
    production_df: pd.DataFrame,
    events_df: pd.DataFrame,
    issues: dict[str, str],
    action_df: pd.DataFrame,
    kpis: list[dict],
) -> bytes | None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError:
        st.error("PDF 생성을 위해 reportlab 패키지가 필요합니다. `pip install reportlab` 후 다시 실행해주세요.")
        return None

    font_pair = register_korean_pdf_fonts()
    if font_pair is None:
        st.error("PDF 한글 폰트를 찾지 못했습니다. Windows 한글 글꼴(맑은 고딕 등)을 설치한 뒤 다시 생성해주세요.")
        return None
    font_name, bold_font_name = font_pair
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=11 * mm,
        bottomMargin=11 * mm,
    )
    styles = {
        "title": ParagraphStyle("title", fontName=bold_font_name, fontSize=24, alignment=TA_CENTER, leading=30),
        "meta": ParagraphStyle("meta", fontName=font_name, fontSize=12, alignment=TA_CENTER, leading=16),
        "section": ParagraphStyle("section", fontName=bold_font_name, fontSize=14, textColor=colors.HexColor("#075aa8"), leading=18),
        "cell": ParagraphStyle("cell", fontName=font_name, fontSize=10.2, leading=13, alignment=TA_CENTER),
        "cell_left": ParagraphStyle("cell_left", fontName=font_name, fontSize=10.2, leading=13, alignment=TA_LEFT),
        "cell_nowrap": ParagraphStyle("cell_nowrap", fontName=font_name, fontSize=8.6, leading=11, alignment=TA_CENTER, splitLongWords=0),
        "issue_title": ParagraphStyle("issue_title", fontName=bold_font_name, fontSize=10.2, leading=13, alignment=TA_CENTER),
        "issue_body": ParagraphStyle(
            "issue_body",
            fontName=font_name,
            fontSize=9,
            leading=12,
            alignment=TA_LEFT,
            splitLongWords=1,
            wordWrap="CJK",
        ),
        "kpi_label": ParagraphStyle("kpi_label", fontName=font_name, fontSize=9.5, leading=12, alignment=TA_CENTER),
        "kpi_value": ParagraphStyle("kpi_value", fontName=bold_font_name, fontSize=17, leading=20, alignment=TA_CENTER),
        "kpi_detail": ParagraphStyle("kpi_detail", fontName=font_name, fontSize=8.3, leading=10, alignment=TA_CENTER),
        "small": ParagraphStyle("small", fontName=font_name, fontSize=8.5, leading=11),
    }
    detail_cards = pdf_event_detail_cards(events_df, styles, font_name)

    story = [
        Paragraph("물류", styles["title"]),
        Paragraph(f"{format_korean_date(meta['meeting_date'])} / 작성자: {meta['author']}", styles["meta"]),
        Spacer(1, 6 * mm),
    ]

    story += pdf_section("00", "회의 KPI 요약", styles)
    kpi_table = Table(
        [
            [Paragraph(item["label"], styles["kpi_label"]) for item in kpis],
            [Paragraph(item["value"], styles["kpi_value"]) for item in kpis],
            [Paragraph(text_to_br(item.get("detail", "-")), styles["kpi_detail"]) for item in kpis],
        ],
        colWidths=[37.2 * mm] * 5,
        rowHeights=[10 * mm, 13 * mm, 18 * mm],
    )
    kpi_table.setStyle(base_table_style(font_name, header_rows=1))
    story.append(kpi_table)
    story.append(Spacer(1, 4.5 * mm))

    story += pdf_section("01", "생산요청 리스트", styles)
    story.append(
        pdf_table(
            production_df,
            PRODUCTION_COLUMNS,
            styles,
            status_column="상태",
            col_widths=[30 * mm, 48 * mm, 18 * mm, 18 * mm, 27 * mm, 20 * mm, 22 * mm],
        )
    )
    story.append(Spacer(1, 4.5 * mm))

    story += pdf_section("02", "행사 일정", styles)
    story.append(
        pdf_table(
            pdf_event_summary_df(events_df),
            EVENT_SUMMARY_COLUMNS,
            styles,
            col_widths=[40 * mm, 58 * mm, 66 * mm, 22 * mm],
        )
    )
    story.append(Spacer(1, 4.5 * mm))

    story += pdf_section("03", "주요 이슈", styles)
    issue_rows = [
        [
            Paragraph(title, styles["issue_title"]),
            Paragraph(text_to_br(format_issue_pdf_value(title, issue_value(issues, title)) or "-"), styles["issue_body"]),
        ]
        for title in ISSUE_KEYS
    ]
    issue_table = Table(
        issue_rows,
        colWidths=[32 * mm, 154 * mm],
    )
    issue_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#9aa8b4")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef5fb")),
                ("BACKGROUND", (1, 0), (1, -1), colors.HexColor("#ffffff")),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#0f3764")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("ALIGN", (1, 0), (1, -1), "LEFT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(issue_table)
    story.append(Spacer(1, 4.5 * mm))

    has_actions = count_action_rows(action_df) > 0
    if has_actions:
        story += pdf_section("04", "진행사항", styles)
        story.append(
            pdf_table(
                action_df,
                ACTION_COLUMNS,
                styles,
                status_column="진행상태",
                col_widths=[23 * mm, 62 * mm, 16 * mm, 28 * mm, 28 * mm, 29 * mm],
            )
        )
        story.append(Spacer(1, 4.5 * mm))

    if detail_cards:
        story += pdf_section("05" if has_actions else "04", "행사 상세 내용", styles)
        story.extend(detail_cards)

    story.append(Paragraph(f"작성자  {meta['author']}", styles["small"]))
    story.append(Paragraph("SCM 물류운영 Portal", styles["small"]))

    doc.build(story)
    return buffer.getvalue()


def register_korean_pdf_fonts() -> tuple[str, str] | None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    font_dirs = [
        Path(r"C:\Windows\Fonts"),
        Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts",
        BASE_DIR / "assets" / "fonts",
    ]
    candidates = [
        ("MalgunGothic", "MalgunGothicBold", "malgun.ttf", "malgunbd.ttf"),
        ("NanumGothic", "NanumGothicBold", "NanumGothic.ttf", "NanumGothicBold.ttf"),
        ("NanumGothic", "NanumGothicBold", "NanumGothic-Regular.ttf", "NanumGothic-Bold.ttf"),
        ("NotoSansKR", "NotoSansKRBold", "NotoSansKR-Regular.ttf", "NotoSansKR-Bold.ttf"),
        ("NotoSansCJKkr", "NotoSansCJKkrBold", "NotoSansCJKkr-Regular.otf", "NotoSansCJKkr-Bold.otf"),
    ]
    for regular_name, bold_name, regular_file, bold_file in candidates:
        for font_dir in font_dirs:
            regular_path = font_dir / regular_file
            bold_path = font_dir / bold_file
            if not regular_path.exists():
                continue
            try:
                if regular_name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(regular_name, str(regular_path)))
                if bold_name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(bold_name, str(bold_path if bold_path.exists() else regular_path)))
                return regular_name, bold_name
            except Exception:
                continue

    for regular_name, bold_name in [
        ("HYGothic-Medium", "HYGothic-Medium"),
        ("HYSMyeongJo-Medium", "HYGothic-Medium"),
    ]:
        try:
            if regular_name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(UnicodeCIDFont(regular_name))
            if bold_name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(UnicodeCIDFont(bold_name))
            return regular_name, bold_name
        except Exception:
            continue
    return None


def pdf_section(number: str, title: str, styles: dict) -> list:
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer

    return [Paragraph(f"{number}  {title}", styles["section"]), Spacer(1, 2.2 * mm)]


def pdf_table(
    df: pd.DataFrame,
    columns: list[str],
    styles: dict,
    status_column: str | None = None,
    col_widths: list | None = None,
):
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table

    normalized = filter_filled_rows(df, columns)
    data = [[Paragraph(column, styles["cell"]) for column in columns]]
    for _, row in normalized.iterrows():
        data.append(
            [
                Paragraph(
                    text_to_br(normalize_cell_value(row[column])),
                    pdf_cell_style(column, styles),
                )
                for column in columns
            ]
        )

    page_width = 186 * mm
    col_width = page_width / len(columns)
    table = Table(data, colWidths=col_widths or [col_width] * len(columns), repeatRows=1)
    style = base_table_style(styles["cell"].fontName, header_rows=1)
    if status_column and status_column in columns:
        status_index = columns.index(status_column)
        for row_index, (_, row) in enumerate(normalized.iterrows(), start=1):
            bg, fg = pdf_status_colors(str(row.get(status_column, "")))
            style.add("BACKGROUND", (status_index, row_index), (status_index, row_index), bg)
            style.add("TEXTCOLOR", (status_index, row_index), (status_index, row_index), fg)
    table.setStyle(style)
    return table


def pdf_cell_style(column: str, styles: dict):
    if column in {"바코드", "88바코드/규격", "SKU", "행사기간", "납기일", "완료예정일", "수량"}:
        return styles.get("cell_nowrap", styles["cell"])
    if column in {"상품명", "진행내용", "행사품목", "비고", "상세내용"}:
        return styles["cell_left"]
    return styles["cell"]


def pdf_note_box(title: str, text: str, styles: dict, font_name: str):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle

    table = Table(
        [
            [Paragraph(title, styles["cell_left"])],
            [Paragraph(text_to_br(text), styles["cell_left"])],
        ],
        colWidths=[186 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#9aa8b4")),
                ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#eef5fb")),
                ("TEXTCOLOR", (0, 0), (0, 0), colors.HexColor("#0f3764")),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def pdf_event_summary_df(events_df: pd.DataFrame) -> pd.DataFrame:
    normalized = normalize_df(events_df, EVENT_COLUMNS)
    if normalized.empty:
        return pd.DataFrame(columns=EVENT_SUMMARY_COLUMNS)
    return normalized[EVENT_SUMMARY_COLUMNS]


def pdf_event_detail_cards(events_df: pd.DataFrame, styles: dict, font_name: str) -> list:
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    normalized = filter_filled_rows(events_df, EVENT_COLUMNS)
    cards = []
    for _, row in normalized.iterrows():
        detail = normalize_cell_value(row.get("상세내용", ""))
        if not detail:
            continue

        event_name = normalize_cell_value(row.get("행사명", "")) or "행사"
        period = normalize_cell_value(row.get("행사기간", ""))
        products = normalize_cell_value(row.get("행사품목", ""))
        qty = normalize_cell_value(row.get("요청수량", ""))
        meta_parts = []
        if period:
            meta_parts.append(f"기간: {period}")
        if products:
            meta_parts.append(f"품목: {products}")
        if qty and qty != "0":
            meta_parts.append(f"요청수량: {qty}")
        meta_text = "   |   ".join(meta_parts) if meta_parts else "-"

        table = Table(
            [
                [Paragraph(event_name, styles["cell_left"])],
                [Paragraph(text_to_br(meta_text), styles["small"])],
                [Paragraph(text_to_br(detail), styles["cell_left"])],
            ],
            colWidths=[186 * mm],
        )
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), font_name),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#b7c6d0")),
                    ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#e8f2ff")),
                    ("BACKGROUND", (0, 1), (0, 1), colors.HexColor("#f7fbff")),
                    ("TEXTCOLOR", (0, 0), (0, 0), colors.HexColor("#0f3764")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        cards.extend([table, Spacer(1, 1.8 * mm)])
    return cards


def base_table_style(font_name: str, header_rows: int):
    from reportlab.lib import colors
    from reportlab.platypus import TableStyle

    style = TableStyle(
        [
            ("FONTNAME", (0, 0), (-1, -1), font_name),
            ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#9aa8b4")),
            ("BACKGROUND", (0, 0), (-1, header_rows - 1), colors.HexColor("#eef5fb")),
            ("TEXTCOLOR", (0, 0), (-1, header_rows - 1), colors.HexColor("#0f3764")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
    )
    return style


def pdf_status_colors(status: str):
    from reportlab.lib import colors

    palette = {
        "생산중": ("#e8f2ff", "#075aa8"),
        "진행중": ("#e8f2ff", "#075aa8"),
        "생산완료": ("#e9f8ef", "#127a3a"),
        "완료": ("#e9f8ef", "#127a3a"),
        "지연": ("#ffecec", "#b42318"),
        "대기": ("#f1f4f7", "#475569"),
    }
    bg, fg = palette.get(status, palette["대기"])
    return colors.HexColor(bg), colors.HexColor(fg)


def text_to_br(value: str) -> str:
    safe = escape_html(value).replace("\n", "<br/>")
    return safe


def format_korean_date(value: date) -> str:
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    return f"{value:%Y.%m.%d} ({weekdays[value.weekday()]})"
