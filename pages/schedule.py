from __future__ import annotations

from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
import sqlite3

import pandas as pd
import streamlit as st

from backend.legacy_storage import connect_sqlite_compatible, legacy_uses_local_sqlite
from backend.perf import perf_span


BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "data" / "schedule.db"

WEEKDAYS = ["월", "화", "수", "목", "금"]
SLOT_COLUMNS = ["시간", *WEEKDAYS]
HIGHLIGHT_COLUMNS = ["완료", "이번 주 핵심"]
EDIT_DELETE_COLUMN = "삭제"

DEFAULT_SLOTS = [
    {
        "시간": "오전\n(09:00~11:30)",
        "월": "1. 업댄트 발송\n2. CS업무",
        "화": "1. 생산부 회의\n2. 업댄트 발송\n3. 밀크런 발송\n4. CS업무",
        "수": "1. 업댄트발송\n2. CS업무\n3. 고무장갑:OPP봉투 발주\n4. JC 설명서 발주\n5. 빨리이브 마감",
        "목": "",
        "금": "",
    },
    {
        "시간": "오후\n(12:30~14:00)",
        "월": "1. 성현물류 입고준비\n2. 상품교환 롯데택배 발송 준비",
        "화": "1. 성현 입고준비\n2. 상품교환 발송준비",
        "수": "",
        "목": "",
        "금": "",
    },
    {
        "시간": "오후\n(14:00~18:00)",
        "월": "1. 성현물류 상품입고\n2. 반품 작업\n3. 생산부 회의내역 작성",
        "화": "1. 성현물류 상품입고\n2. 반품작업\n3. OKR 보기 마무리",
        "수": "",
        "목": "",
        "금": "",
    },
]


def render_schedule_page() -> None:
    with perf_span("schedule.ensure_schema"):
        ensure_schema()
    with perf_span("schedule.ensure_weeks"):
        ensure_weeks_through_current()
    with perf_span("schedule.clear_seed_rows"):
        clear_unsaved_seeded_highlights_from_current()
    with perf_span("schedule.inject_css"):
        inject_schedule_css()

    default_week = monday_of(date.today())
    with perf_span("schedule.session_state"):
        if "schedule_week_start" not in st.session_state:
            st.session_state.schedule_week_start = default_week

    st.markdown('<main class="weekly-schedule-shell">', unsafe_allow_html=True)
    st.markdown('<h1 class="weekly-schedule-title">주간 캘린더</h1>', unsafe_allow_html=True)

    with perf_span("schedule.controls_render"):
        week_start = render_week_controls()
    with perf_span("schedule.get_or_create_week"):
        week = get_or_create_week(week_start)

    with perf_span("schedule.highlights_section"):
        highlights_df = render_highlights(week["id"])
    with perf_span("schedule.slots_section"):
        slots_df = render_week_table(week["id"])
    with perf_span("schedule.comment_render"):
        comment = render_comment(week)
    with perf_span("schedule.save_actions_render"):
        render_save_actions(week["id"], week_start, highlights_df, slots_df, comment)
    with perf_span("schedule.history_render"):
        render_history()

    st.markdown("</main>", unsafe_allow_html=True)


def ensure_schema() -> None:
    if legacy_uses_local_sqlite():
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect_sqlite_compatible(DB_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schedule_weeks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                owner TEXT NOT NULL DEFAULT '',
                comment TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS schedule_highlights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_id INTEGER NOT NULL,
                sort_order INTEGER NOT NULL,
                title TEXT NOT NULL,
                checked INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(week_id) REFERENCES schedule_weeks(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS schedule_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_id INTEGER NOT NULL,
                sort_order INTEGER NOT NULL,
                time_label TEXT NOT NULL,
                mon TEXT NOT NULL DEFAULT '',
                tue TEXT NOT NULL DEFAULT '',
                wed TEXT NOT NULL DEFAULT '',
                thu TEXT NOT NULL DEFAULT '',
                fri TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(week_id) REFERENCES schedule_weeks(id) ON DELETE CASCADE
            );
            """
        )


def render_week_controls() -> date:
    week_start = monday_of(st.session_state.schedule_week_start)
    with st.container(key="schedule_week_picker"):
        prev_col, date_col, this_col, next_col, spacer = st.columns([0.72, 1.35, 0.72, 0.72, 5.0], gap="small")
        with prev_col:
            st.markdown('<div class="schedule-control-spacer"></div>', unsafe_allow_html=True)
            if st.button("‹ 이전주", key="schedule_prev_week", use_container_width=True):
                week_start -= timedelta(days=7)
                st.session_state.schedule_week_start = week_start
                st.rerun()
        with date_col:
            selected_date = st.date_input(
                "주 선택",
                value=week_start,
                key=f"schedule_week_input_{week_start.isoformat()}",
            )
            selected_monday = monday_of(selected_date)
            if selected_monday != week_start:
                st.session_state.schedule_week_start = selected_monday
                st.rerun()
        with this_col:
            st.markdown('<div class="schedule-control-spacer"></div>', unsafe_allow_html=True)
            if st.button("이번주", key="schedule_this_week", use_container_width=True):
                st.session_state.schedule_week_start = monday_of(date.today())
                st.rerun()
        with next_col:
            st.markdown('<div class="schedule-control-spacer"></div>', unsafe_allow_html=True)
            if st.button("다음주 ›", key="schedule_next_week", use_container_width=True):
                week_start += timedelta(days=7)
                st.session_state.schedule_week_start = week_start
                st.rerun()
        with spacer:
            st.markdown('<div class="schedule-control-spacer"></div>', unsafe_allow_html=True)
            st.markdown(f'<div class="schedule-week-chip">📅 {week_start:%Y-%m-%d} 주</div>', unsafe_allow_html=True)
    return week_start


def render_highlights(week_id: int) -> pd.DataFrame:
    st.markdown('<h2 class="weekly-section-title">이번 주 핵심</h2>', unsafe_allow_html=True)
    with perf_span("schedule.highlights_dataframe"):
        df = load_highlights_df(week_id)
    with perf_span("schedule.highlights_session_state"):
        buffer_key = f"schedule_highlights_buffer_{week_id}"
        if buffer_key not in st.session_state:
            st.session_state[buffer_key] = df
    st.markdown('<div class="schedule-highlight-editor">', unsafe_allow_html=True)
    with st.form(key=f"schedule_highlights_form_{week_id}", clear_on_submit=False):
        with perf_span("schedule.highlights_data_editor"):
            editor_key = f"schedule_highlights_editor_{week_id}"
            edited = render_schedule_visible_editor(
                add_schedule_delete_column(normalize_highlights_df(st.session_state[buffer_key])),
                HIGHLIGHT_COLUMNS,
                editor_key,
                checkbox_columns={"완료"},
                compact=True,
            )
        action = render_schedule_editor_actions("schedule_highlights", save_label="핵심 반영", delete_label="선택 삭제")
        action = edited.attrs.get("editor_row_action") if edited.attrs.get("editor_row_action") != "none" else action
        if action in {"row_plus", "row_minus", "add_row", "selected_delete", "save"}:
            edited_source = apply_schedule_editor_action(edited, action, editor_key, HIGHLIGHT_COLUMNS)
            st.session_state[buffer_key] = normalize_highlights_df(edited_source)
            if action != "save":
                st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)
    return normalize_highlights_df(st.session_state[buffer_key])


def render_week_table(week_id: int) -> pd.DataFrame:
    st.markdown('<h2 class="weekly-section-title">월~금 시간대별 일정</h2>', unsafe_allow_html=True)
    with perf_span("schedule.slots_dataframe"):
        df = load_slots_df(week_id)
    buffer_key = f"schedule_slots_buffer_{week_id}"
    if buffer_key not in st.session_state:
        st.session_state[buffer_key] = df
    with perf_span("schedule.slots_table_render"):
        render_schedule_table_html(st.session_state[buffer_key])
    with st.expander("시간대별 일정 편집", expanded=True):
        with st.form(key=f"schedule_slots_form_{week_id}", clear_on_submit=False):
            with perf_span("schedule.slots_data_editor"):
                editor_key = f"schedule_slots_editor_{week_id}"
                edited = render_schedule_visible_editor(
                    add_schedule_delete_column(normalize_slots_df(st.session_state[buffer_key])),
                    SLOT_COLUMNS,
                    editor_key,
                )
            action = render_schedule_editor_actions("schedule_slots", save_label="일정 저장", delete_label="선택 삭제")
            action = edited.attrs.get("editor_row_action") if edited.attrs.get("editor_row_action") != "none" else action
            if action in {"row_plus", "row_minus", "add_row", "selected_delete", "save"}:
                edited_source = apply_schedule_editor_action(edited, action, editor_key, SLOT_COLUMNS)
                normalized = normalize_slots_df(edited_source)
                st.session_state[buffer_key] = normalized
                if action != "save":
                    st.rerun()
            if action == "save":
                save_slots_only(week_id, normalized)
                st.success("시간대별 일정 저장 완료")
                st.rerun()
    return normalize_slots_df(st.session_state[buffer_key])


def render_comment(week: dict) -> str:
    st.markdown('<h2 class="weekly-section-title history-title">📌 물류 히스토리</h2>', unsafe_allow_html=True)
    return st.text_area(
        "코멘트",
        value=week.get("comment", ""),
        key=f"schedule_comment_{week['id']}",
        height=74,
        placeholder="이번 주 물류 이슈, 공유사항, 다음 주로 넘길 내용을 입력하세요.",
    )


def render_save_actions(week_id: int, week_start: date, highlights_df: pd.DataFrame, slots_df: pd.DataFrame, comment: str) -> None:
    save_col, copy_col, spacer = st.columns([0.86, 1.05, 5.4], gap="small")
    with save_col:
        if st.button("저장", key=f"schedule_save_{week_id}", type="primary", use_container_width=True):
            save_week(week_id, week_start, highlights_df, slots_df, comment)
            st.session_state.pop("schedule_history_download_payload", None)
            st.success("주간 일정 저장 완료")
            st.rerun()
    with copy_col:
        if st.button("전주 일정 복사", key=f"schedule_copy_previous_{week_id}", use_container_width=True):
            copied = copy_previous_week(week_id, week_start)
            st.session_state.pop("schedule_history_download_payload", None)
            st.success(f"전주 일정 복사 완료 ({copied}건)")
            st.rerun()
    with spacer:
        st.empty()


def render_schedule_visible_editor(
    df: pd.DataFrame,
    columns: list[str],
    key_prefix: str,
    checkbox_columns: set[str] | None = None,
    compact: bool = False,
    blank_rows: int = 3,
) -> pd.DataFrame:
    checkbox_columns = checkbox_columns or set()
    source = df.copy() if df is not None else pd.DataFrame(columns=[EDIT_DELETE_COLUMN, *columns])
    for column in [EDIT_DELETE_COLUMN, *columns]:
        if column not in source.columns:
            source[column] = False if column in {EDIT_DELETE_COLUMN, *checkbox_columns} else ""
    source = source[[EDIT_DELETE_COLUMN, *columns]].reset_index(drop=True)
    if columns == SLOT_COLUMNS and not compact:
        return render_schedule_slot_editor(source, columns, key_prefix, blank_rows)
    rows = source.to_dict("records")
    row_count_key = schedule_editor_row_count_key(key_prefix)
    current_row_count = max(blank_rows, len(rows), int(st.session_state.get(row_count_key, 0) or 0))
    st.session_state[row_count_key] = current_row_count

    st.markdown('<div class="schedule-visible-editor">', unsafe_allow_html=True)
    row_action = "none"
    control_cols = st.columns([0.34, 0.34, 0.72, 5.0], gap="small")
    if control_cols[0].form_submit_button("-", use_container_width=True):
        row_action = "row_minus"
    if control_cols[1].form_submit_button("+", use_container_width=True):
        st.session_state[row_count_key] = min(current_row_count + 1, 80)
        row_action = "row_plus"
    if control_cols[2].form_submit_button("행 추가", use_container_width=True):
        row_action = "add_row"
    with control_cols[3]:
        st.empty()

    for _ in range(max(0, current_row_count - len(rows))):
        rows.append({EDIT_DELETE_COLUMN: False, **{column: False if column in checkbox_columns else "" for column in columns}})

    header_weights = schedule_editor_column_weights(columns)
    header_cols = st.columns(header_weights, gap="small")
    header_cols[0].markdown('<div class="sheet-header">삭제</div>', unsafe_allow_html=True)
    for index, column in enumerate(columns, start=1):
        header_cols[index].markdown(f'<div class="sheet-header">{html_escape(column)}</div>', unsafe_allow_html=True)

    edited_rows = []
    for row_index, row in enumerate(rows):
        row_cols = st.columns(header_weights, gap="small")
        edited_row = {
            EDIT_DELETE_COLUMN: row_cols[0].checkbox(
                "삭제",
                value=is_checked(row.get(EDIT_DELETE_COLUMN)),
                key=f"{key_prefix}_delete_{row_index}",
                label_visibility="collapsed",
            )
        }
        for column_index, column in enumerate(columns, start=1):
            value = row.get(column, "")
            cell_key = f"{key_prefix}_{row_index}_{column_index}_{safe_widget_key(column)}"
            if column in checkbox_columns:
                edited_row[column] = row_cols[column_index].checkbox(
                    column,
                    value=is_checked(value),
                    key=cell_key,
                    label_visibility="collapsed",
                )
            else:
                height = 44 if compact else 76
                edited_row[column] = row_cols[column_index].text_area(
                    column,
                    value=clean_text(value),
                    height=height,
                    key=cell_key,
                    label_visibility="collapsed",
                )
        edited_rows.append(edited_row)
    st.markdown("</div>", unsafe_allow_html=True)

    edited_df = pd.DataFrame(edited_rows, columns=[EDIT_DELETE_COLUMN, *columns])
    edited_df.attrs["editor_row_action"] = row_action
    return edited_df


def render_schedule_slot_editor(df: pd.DataFrame, columns: list[str], key_prefix: str, blank_rows: int = 3) -> pd.DataFrame:
    rows = df.to_dict("records")
    row_count_key = schedule_editor_row_count_key(key_prefix)
    current_row_count = max(blank_rows, len(rows), int(st.session_state.get(row_count_key, 0) or 0))
    st.session_state[row_count_key] = current_row_count
    for _ in range(max(0, current_row_count - len(rows))):
        rows.append({EDIT_DELETE_COLUMN: False, **{column: "" for column in columns}})

    st.markdown('<div class="schedule-slot-editor">', unsafe_allow_html=True)
    toolbar_cols = st.columns([1.15, 5.8], gap="small")
    row_action = "none"
    delete_row_index: int | None = None
    if toolbar_cols[0].form_submit_button("+ 시간대 추가", use_container_width=True):
        row_action = "add_row"
    with toolbar_cols[1]:
        st.empty()

    header_cols = st.columns([1.05, 1.28, 1.28, 1.28, 1.28, 1.28, 0.34], gap="small")
    for index, column in enumerate(columns):
        header_cols[index].markdown(f'<div class="slot-sheet-header">{html_escape(column)}</div>', unsafe_allow_html=True)
    header_cols[-1].markdown('<div class="slot-sheet-header slot-action-header"></div>', unsafe_allow_html=True)

    edited_rows = []
    for row_index, row in enumerate(rows):
        st.markdown('<div class="schedule-slot-row-anchor"></div>', unsafe_allow_html=True)
        row_cols = st.columns([1.05, 1.28, 1.28, 1.28, 1.28, 1.28, 0.34], gap="small")
        edited_row = {EDIT_DELETE_COLUMN: False}
        for column_index, column in enumerate(columns):
            value = clean_text(row.get(column, ""))
            cell_key = f"{key_prefix}_{row_index}_{column_index + 1}_{safe_widget_key(column)}"
            if column == "시간":
                edited_row[column] = row_cols[column_index].text_area(
                    column,
                    value=value,
                    height=66,
                    key=cell_key,
                    label_visibility="collapsed",
                    placeholder="오전\n09:00~11:30",
                )
            else:
                edited_row[column] = row_cols[column_index].text_area(
                    column,
                    value=value,
                    height=76,
                    key=cell_key,
                    label_visibility="collapsed",
                    placeholder="+ 일정 입력",
                )
        with row_cols[-1]:
            st.markdown('<div class="slot-trash-spacer"></div>', unsafe_allow_html=True)
            if st.form_submit_button(f"🗑 {row_index + 1}", help="이 시간대 삭제", use_container_width=True):
                row_action = "selected_delete"
                delete_row_index = row_index
        if delete_row_index == row_index:
            edited_row[EDIT_DELETE_COLUMN] = True
        edited_rows.append(edited_row)
    st.markdown("</div>", unsafe_allow_html=True)

    edited_df = pd.DataFrame(edited_rows, columns=[EDIT_DELETE_COLUMN, *columns])
    edited_df.attrs["editor_row_action"] = row_action
    return edited_df


def render_schedule_editor_actions(key_prefix: str, save_label: str, delete_label: str) -> str:
    action = "none"
    if key_prefix == "schedule_slots":
        action_cols = st.columns([1.0, 5.0], gap="small")
        if action_cols[0].form_submit_button(save_label, type="primary", use_container_width=True):
            action = "save"
        with action_cols[1]:
            st.empty()
        return action
    action_cols = st.columns([0.9, 0.9, 4.0], gap="small")
    if action_cols[0].form_submit_button(save_label, type="primary", use_container_width=True):
        action = "save"
    if action_cols[1].form_submit_button(delete_label, use_container_width=True):
        action = "selected_delete"
    with action_cols[2]:
        st.empty()
    return action


def add_schedule_delete_column(df: pd.DataFrame) -> pd.DataFrame:
    source = df.copy() if df is not None else pd.DataFrame()
    if EDIT_DELETE_COLUMN in source.columns:
        return source
    source.insert(0, EDIT_DELETE_COLUMN, False)
    return source


def strip_schedule_delete_column(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    return df.drop(columns=[EDIT_DELETE_COLUMN], errors="ignore")


def apply_schedule_editor_action(edited: pd.DataFrame, action: str, key_prefix: str, columns: list[str]) -> pd.DataFrame:
    source = strip_schedule_delete_column(edited)
    if action == "row_minus":
        rows = source.to_dict("records")
        removable_index = None
        for index in range(len(rows) - 1, 2, -1):
            if not any(clean_text(rows[index].get(column)) for column in columns):
                removable_index = index
                break
        if removable_index is not None:
            rows.pop(removable_index)
        else:
            st.warning("내용이 있는 행은 - 버튼으로 삭제하지 않습니다. 삭제 체크 후 선택 삭제를 눌러주세요.")
        st.session_state[schedule_editor_row_count_key(key_prefix)] = max(len(rows), 3)
        return pd.DataFrame(rows, columns=columns)
    if action == "add_row":
        rows = source.to_dict("records")
        rows.append({column: False if column == "완료" else "" for column in columns})
        st.session_state[schedule_editor_row_count_key(key_prefix)] = max(len(rows), 3)
        return pd.DataFrame(rows, columns=columns)
    if action == "selected_delete":
        if EDIT_DELETE_COLUMN not in edited.columns:
            return source
        checked_values = [is_checked(value) for value in edited[EDIT_DELETE_COLUMN].tolist()]
        if not any(checked_values):
            st.warning("삭제 체크된 행이 없습니다.")
            return source
        keep_rows = [not checked for checked in checked_values]
        cleaned = source.iloc[keep_rows].reset_index(drop=True)
        st.session_state[schedule_editor_row_count_key(key_prefix)] = max(len(cleaned), 3)
        return cleaned
    return source


def schedule_editor_row_count_key(key_prefix: str) -> str:
    return f"{key_prefix}_visible_row_count"


def schedule_editor_column_weights(columns: list[str]) -> list[float]:
    if columns == HIGHLIGHT_COLUMNS:
        return [0.42, 0.5, 4.4]
    return [0.42, 0.88, 1.3, 1.3, 1.3, 1.3, 1.3]


def is_checked(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y", "checked", "완료", "삭제"}


def safe_widget_key(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(value))


def render_history() -> None:
    rows = load_history_rows(recent_days=31)
    history_state_key = "schedule_history_download_payload"
    title_col, download_col = st.columns([4.8, 1.0], gap="small")
    with title_col:
        st.caption("최근 한 달간 저장된 일정과 히스토리입니다. 이전 기록은 DB에 계속 보관됩니다.")
    with download_col:
        if st.button("다운로드 준비", key="schedule_history_prepare", use_container_width=True):
            all_rows = load_history_rows(recent_days=None)
            st.session_state[history_state_key] = (history_excel_bytes(all_rows), len(all_rows))
        payload = st.session_state.get(history_state_key)
        history_bytes, history_count = payload if isinstance(payload, tuple) and len(payload) == 2 else (b"", 0)
        st.download_button(
            "히스토리 다운로드",
            data=history_bytes,
            file_name=f"물류히스토리_전체요약_{date.today():%Y%m%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="schedule_history_download",
            disabled=history_count == 0,
        )
    if not rows:
        st.info("최근 한 달간 저장된 히스토리가 없습니다.")
        return
    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, use_container_width=True)


def get_or_create_week(week_start: date) -> dict:
    week_key = week_start.isoformat()
    with connect_sqlite_compatible(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM schedule_weeks WHERE week_start = ?", (week_key,)).fetchone()
        if row is None:
            week_id = create_week(conn, week_start)
            row = conn.execute("SELECT * FROM schedule_weeks WHERE id = ?", (week_id,)).fetchone()
        return dict(row)


def ensure_weeks_through_current() -> None:
    current_week = monday_of(date.today())
    with connect_sqlite_compatible(DB_PATH) as conn:
        rows = conn.execute("SELECT week_start FROM schedule_weeks WHERE week_start <= ?", (current_week.isoformat(),)).fetchall()
        existing_week_keys = {row[0] for row in rows}
        if current_week.isoformat() in existing_week_keys:
            return

        previous_week_keys = sorted(key for key in existing_week_keys if key < current_week.isoformat())
        week_start = date.fromisoformat(previous_week_keys[-1]) + timedelta(days=7) if previous_week_keys else current_week
        while week_start <= current_week:
            week_key = week_start.isoformat()
            if week_key not in existing_week_keys:
                create_week(conn, week_start)
                existing_week_keys.add(week_key)
            week_start += timedelta(days=7)


def create_week(conn: sqlite3.Connection, week_start: date) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO schedule_weeks (week_start, title, owner, comment, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (week_start.isoformat(), f"{week_start:%m월%d일}주", "송광선", "", now, now),
    )
    week_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    seed_week(conn, week_id, week_start)
    return week_id


def seed_week(conn: sqlite3.Connection, week_id: int, week_start: date) -> None:
    for order, row in enumerate(DEFAULT_SLOTS):
        conn.execute(
            """
            INSERT INTO schedule_slots (week_id, sort_order, time_label, mon, tue, wed, thu, fri)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (week_id, order, row["시간"], row["월"], row["화"], row["수"], row["목"], row["금"]),
        )


def clear_unsaved_seeded_highlights_from_current() -> None:
    current_week_key = monday_of(date.today()).isoformat()
    with connect_sqlite_compatible(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT id
            FROM schedule_weeks
            WHERE week_start >= ?
              AND created_at = updated_at
              AND EXISTS (
                  SELECT 1 FROM schedule_highlights
                  WHERE schedule_highlights.week_id = schedule_weeks.id
              )
            """,
            (current_week_key,),
        ).fetchall()
        if not rows:
            return
        conn.executemany("DELETE FROM schedule_highlights WHERE week_id = ?", [(row[0],) for row in rows])


def load_highlights_df(week_id: int) -> pd.DataFrame:
    with connect_sqlite_compatible(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT checked, title
            FROM schedule_highlights
            WHERE week_id = ?
            ORDER BY sort_order, id
            """,
            (week_id,),
        ).fetchall()
    df = pd.DataFrame([{"완료": bool(row[0]), "이번 주 핵심": row[1]} for row in rows], columns=HIGHLIGHT_COLUMNS)
    return normalize_highlights_df(df)


def load_slots_df(week_id: int) -> pd.DataFrame:
    with connect_sqlite_compatible(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT time_label, mon, tue, wed, thu, fri
            FROM schedule_slots
            WHERE week_id = ?
            ORDER BY sort_order, id
            """,
            (week_id,),
        ).fetchall()
    df = pd.DataFrame(
        [{"시간": row[0], "월": row[1], "화": row[2], "수": row[3], "목": row[4], "금": row[5]} for row in rows],
        columns=SLOT_COLUMNS,
    )
    return normalize_slots_df(df)


def save_week(week_id: int, week_start: date, highlights_df: pd.DataFrame, slots_df: pd.DataFrame, comment: str) -> None:
    now = datetime.now().isoformat(timespec="microseconds")
    with connect_sqlite_compatible(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE schedule_weeks
            SET title = ?, comment = ?, updated_at = ?
            WHERE id = ?
            """,
            (f"{week_start:%m월%d일}주", clean_text(comment), now, week_id),
        )
        conn.execute("DELETE FROM schedule_highlights WHERE week_id = ?", (week_id,))
        conn.execute("DELETE FROM schedule_slots WHERE week_id = ?", (week_id,))

        for order, row in normalize_highlights_df(highlights_df).iterrows():
            if not clean_text(row.get("이번 주 핵심")):
                continue
            conn.execute(
                """
                INSERT INTO schedule_highlights (week_id, sort_order, title, checked)
                VALUES (?, ?, ?, ?)
                """,
                (week_id, int(order), clean_text(row["이번 주 핵심"]), int(bool(row.get("완료")))),
            )

        for order, row in normalize_slots_df(slots_df).iterrows():
            if not any(clean_text(row.get(column)) for column in SLOT_COLUMNS):
                continue
            conn.execute(
                """
                INSERT INTO schedule_slots (week_id, sort_order, time_label, mon, tue, wed, thu, fri)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    week_id,
                    int(order),
                    clean_text(row.get("시간")),
                    clean_text(row.get("월")),
                    clean_text(row.get("화")),
                    clean_text(row.get("수")),
                    clean_text(row.get("목")),
                    clean_text(row.get("금")),
                ),
            )


def save_slots_only(week_id: int, slots_df: pd.DataFrame) -> None:
    now = datetime.now().isoformat(timespec="microseconds")
    with connect_sqlite_compatible(DB_PATH) as conn:
        conn.execute("UPDATE schedule_weeks SET updated_at = ? WHERE id = ?", (now, week_id))
        conn.execute("DELETE FROM schedule_slots WHERE week_id = ?", (week_id,))
        for order, row in normalize_slots_df(slots_df).iterrows():
            if not any(clean_text(row.get(column)) for column in SLOT_COLUMNS):
                continue
            conn.execute(
                """
                INSERT INTO schedule_slots (week_id, sort_order, time_label, mon, tue, wed, thu, fri)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    week_id,
                    int(order),
                    clean_text(row.get("시간")),
                    clean_text(row.get("월")),
                    clean_text(row.get("화")),
                    clean_text(row.get("수")),
                    clean_text(row.get("목")),
                    clean_text(row.get("금")),
                ),
            )


def copy_previous_week(week_id: int, week_start: date) -> int:
    previous_start = (week_start - timedelta(days=7)).isoformat()
    now = datetime.now().isoformat(timespec="microseconds")
    with connect_sqlite_compatible(DB_PATH) as conn:
        previous = conn.execute("SELECT id FROM schedule_weeks WHERE week_start = ?", (previous_start,)).fetchone()
        if previous is None:
            return 0
        previous_id = previous[0]
        conn.execute("DELETE FROM schedule_highlights WHERE week_id = ?", (week_id,))
        conn.execute("DELETE FROM schedule_slots WHERE week_id = ?", (week_id,))
        highlights = conn.execute(
            "SELECT sort_order, title, checked FROM schedule_highlights WHERE week_id = ? ORDER BY sort_order, id",
            (previous_id,),
        ).fetchall()
        slots = conn.execute(
            "SELECT sort_order, time_label, mon, tue, wed, thu, fri FROM schedule_slots WHERE week_id = ? ORDER BY sort_order, id",
            (previous_id,),
        ).fetchall()
        for row in highlights:
            conn.execute(
                "INSERT INTO schedule_highlights (week_id, sort_order, title, checked) VALUES (?, ?, ?, ?)",
                (week_id, row[0], row[1], row[2]),
            )
        for row in slots:
            conn.execute(
                """
                INSERT INTO schedule_slots (week_id, sort_order, time_label, mon, tue, wed, thu, fri)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (week_id, row[0], row[1], row[2], row[3], row[4], row[5], row[6]),
            )
        conn.execute("UPDATE schedule_weeks SET updated_at = ? WHERE id = ?", (now, week_id))
        return len(highlights) + len(slots)


def load_history_rows(recent_days: int | None = 31) -> list[dict]:
    since = (date.today() - timedelta(days=recent_days)).isoformat() if recent_days else None
    where_clause = "WHERE week_start >= ?" if since else ""
    params = (since,) if since else ()
    with connect_sqlite_compatible(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT id, week_start, title, owner, comment, updated_at
            FROM schedule_weeks
            {where_clause}
            ORDER BY week_start DESC
            """,
            params,
        ).fetchall()
        return [history_row_summary(conn, row) for row in rows]


def history_row_summary(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    week_id = row["id"]
    highlights = conn.execute(
        """
        SELECT title, checked
        FROM schedule_highlights
        WHERE week_id = ?
        ORDER BY sort_order, id
        """,
        (week_id,),
    ).fetchall()
    slots = conn.execute(
        """
        SELECT time_label, mon, tue, wed, thu, fri
        FROM schedule_slots
        WHERE week_id = ?
        ORDER BY sort_order, id
        """,
        (week_id,),
    ).fetchall()
    return {
        "일정": row["week_start"],
        "제목": row["title"],
        "담당자": row["owner"] or "송광선",
        "핵심요약": summarize_core(highlights, slots, row["comment"]),
        "수정일시": row["updated_at"],
    }


def summarize_core(highlights, slots, comment: str) -> str:
    sections = []
    highlight_summary = summarize_highlights(highlights)
    slot_summary = summarize_slots(slots)
    comment = clean_text(comment)

    if highlight_summary:
        sections.append(f"이번 주 핵심\n{highlight_summary}")
    if slot_summary:
        sections.append(f"월~금 일정 요약\n{slot_summary}")
    if comment:
        sections.append(f"코멘트\n{comment}")
    return "\n\n".join(sections)


def summarize_highlights(rows) -> str:
    parts = []
    for title, checked in rows:
        title = clean_text(title)
        if title:
            parts.append(f"{'완료' if checked else '진행'}: {title}")
    return "\n".join(parts)


def summarize_slots(rows) -> str:
    day_labels = ["월", "화", "수", "목", "금"]
    parts = []
    for row in rows:
        time_label = clean_text(row[0]).replace("\n", " ")
        for day, value in zip(day_labels, row[1:]):
            text = clean_text(value)
            if text:
                prefix = f"{day} {time_label}".strip()
                parts.append(f"{prefix}: {text}")
    return "\n".join(parts)


def history_excel_bytes(rows: list[dict]) -> bytes:
    output = BytesIO()
    df = pd.DataFrame(rows)
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        sheet_name = "물류히스토리"
        df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=1)
        workbook = writer.book
        worksheet = writer.sheets[sheet_name]
        title_format = workbook.add_format(
            {
                "bold": True,
                "font_size": 16,
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
        text_format = workbook.add_format({"border": 1, "border_color": "#E5EFEA", "valign": "top", "text_wrap": True})
        last_col = max(len(df.columns) - 1, 0)
        if last_col:
            worksheet.merge_range(0, 0, 0, last_col, "물류 히스토리 전체 요약", title_format)
        elif len(df.columns):
            worksheet.write(0, 0, "물류 히스토리 전체 요약", title_format)
        for idx, column in enumerate(df.columns):
            worksheet.write(1, idx, column, header_format)
            width = 18
            if column == "핵심요약":
                width = 58
            worksheet.set_column(idx, idx, width, text_format)
        worksheet.freeze_panes(2, 0)
        if len(df.columns):
            worksheet.autofilter(1, 0, max(len(df) + 1, 1), last_col)
    return output.getvalue()


def normalize_highlights_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=HIGHLIGHT_COLUMNS)
    normalized = df.copy()
    for column in HIGHLIGHT_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = False if column == "완료" else ""
    normalized = normalized[HIGHLIGHT_COLUMNS].fillna("")
    normalized["완료"] = normalized["완료"].apply(lambda value: bool(value) if not isinstance(value, str) else value.lower() in {"true", "1", "yes", "y"})
    normalized["이번 주 핵심"] = normalized["이번 주 핵심"].apply(clean_text)
    return normalized


def normalize_slots_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=SLOT_COLUMNS)
    normalized = df.copy()
    for column in SLOT_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = ""
    normalized = normalized[SLOT_COLUMNS].fillna("")
    for column in SLOT_COLUMNS:
        normalized[column] = normalized[column].apply(clean_text)
    return normalized


def render_schedule_table_html(df: pd.DataFrame) -> None:
    normalized = normalize_slots_df(df)
    header = "".join(f"<th>{column}</th>" for column in SLOT_COLUMNS)
    rows = []
    for _, row in normalized.iterrows():
        cells = []
        for column in SLOT_COLUMNS:
            value = html_escape(row.get(column, "")).replace("\n", "<br>")
            cells.append(f"<td>{value}</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    body = "".join(rows) or f'<tr><td colspan="{len(SLOT_COLUMNS)}" class="empty">등록된 일정이 없습니다.</td></tr>'
    st.markdown(
        f"""
        <div class="weekly-table-wrap">
            <table>
                <thead><tr>{header}</tr></thead>
                <tbody>{body}</tbody>
            </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


def monday_of(value: date) -> date:
    return value - timedelta(days=value.weekday())


def clean_text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "nat", "none"} else text


def html_escape(value) -> str:
    return (
        clean_text(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def inject_schedule_css() -> None:
    st.markdown(
        """
        <style>
        .weekly-schedule-shell {
            display: flex;
            flex-direction: column;
            gap: 0.85rem;
            min-width: 1180px;
            padding-bottom: 2rem;
        }
        .weekly-schedule-title {
            color: #475569;
            font-size: 1.25rem;
            font-weight: 950;
            line-height: 1.2;
            margin: 0.1rem 0 0.25rem;
        }
        div[class*="st-key-schedule_week_picker"] {
            background: rgba(255, 255, 255, 0.72);
            border: 1px solid #E2E8F0;
            border-radius: 8px;
            padding: 0.72rem;
        }
        div[class*="st-key-schedule_week_picker"] .stButton button,
        div[class*="st-key-schedule_week_picker"] input {
            min-height: 40px;
        }
        .schedule-control-spacer {
            height: 1.62rem;
        }
        .schedule-week-chip {
            align-items: center;
            color: #475569;
            display: flex;
            font-weight: 800;
            min-height: 40px;
        }
        .weekly-section-title {
            color: #475569;
            font-size: 1rem;
            font-weight: 900;
            margin: 0.55rem 0 0.25rem;
        }
        .schedule-highlight-editor {
            max-width: 640px;
        }
        .schedule-visible-editor {
            background: #FAF8F5;
            border: 1px solid #D8D2C8;
            border-radius: 8px;
            overflow-x: auto;
            padding: 0.56rem;
        }
        .schedule-visible-editor [data-testid="stHorizontalBlock"] {
            min-width: 980px;
        }
        .schedule-visible-editor .sheet-header {
            background: #EDE8E1;
            border: 1px solid #D8D2C8;
            border-radius: 6px;
            color: #102033;
            font-size: 0.86rem;
            font-weight: 900;
            min-height: 32px;
            padding: 0.46rem 0.5rem;
            text-align: center;
            white-space: nowrap;
        }
        .schedule-visible-editor textarea {
            background: #FFFFFF !important;
            border-color: #C9BFB1 !important;
            color: #172033 !important;
            font-size: 0.9rem !important;
            font-weight: 760 !important;
            line-height: 1.34 !important;
        }
        .schedule-visible-editor [data-testid="stCheckbox"] {
            align-items: center;
            display: flex;
            justify-content: center;
            min-height: 40px;
        }
        .schedule-visible-editor div[data-testid="stButton"] button {
            min-height: 36px;
        }
        div[class*="st-key-schedule_highlights_editor_"] {
            max-width: 640px;
        }
        div[class*="st-key-schedule_highlights_editor_"] [data-testid="stDataFrame"] {
            max-width: 640px;
        }
        div[class*="st-key-schedule_slots_editor_"] {
            min-height: 300px;
        }
        div[class*="st-key-schedule_slots_editor_"] [data-testid="stDataFrame"] {
            min-height: 290px;
        }
        .weekly-section-title.history-title {
            background: rgba(120, 74, 49, 0.72);
            border-radius: 6px 6px 0 0;
            margin-top: 1.25rem;
            padding: 0.34rem 0.55rem;
        }
        .weekly-table-wrap {
            border: 1px solid #E2E8F0;
            border-radius: 6px;
            overflow-x: auto;
            width: 100%;
        }
        .weekly-table-wrap table {
            border-collapse: collapse;
            color: #334155;
            font-size: 0.82rem;
            min-width: 1120px;
            table-layout: auto;
            width: 100%;
        }
        .weekly-table-wrap th,
        .weekly-table-wrap td {
            border: 1px solid #E5E7EB;
            color: #334155;
            padding: 0.52rem 0.58rem;
            vertical-align: top;
            white-space: normal;
        }
        .weekly-table-wrap th {
            background: #F8FAFC;
            color: #64748B;
            font-weight: 900;
            text-align: left;
        }
        .weekly-table-wrap th:first-child,
        .weekly-table-wrap td:first-child {
            font-weight: 900;
            min-width: 132px;
            width: 132px;
        }
        .weekly-table-wrap th:not(:first-child),
        .weekly-table-wrap td:not(:first-child) {
            min-width: 190px;
        }
        .weekly-table-wrap td {
            line-height: 1.45;
        }
        .weekly-table-wrap .empty {
            color: #94A3B8;
            text-align: center;
        }

        /* Compact time-slot editor. Keep schedule row heights content-driven. */
        .schedule-slot-editor {
            background: #FAF8F5;
            border: 1px solid #D8D2C8;
            border-radius: 10px;
            box-shadow: 0 8px 18px rgba(45, 38, 30, 0.035);
            display: flex;
            flex-direction: column;
            gap: 6px;
            height: auto !important;
            margin: 0;
            min-height: 0 !important;
            overflow-x: auto;
            padding: 0.72rem;
        }
        .schedule-slot-editor [data-testid="stHorizontalBlock"] {
            align-items: flex-start !important;
            display: flex !important;
            flex: none !important;
            flex-grow: 0 !important;
            height: auto !important;
            justify-content: flex-start !important;
            margin: 0 !important;
            min-height: 0 !important;
            min-width: 1100px !important;
        }
        .schedule-slot-editor [data-testid="stVerticalBlock"],
        .schedule-slot-editor [data-testid="stElementContainer"],
        .schedule-slot-editor div[class*="st-key-schedule_slots_editor_"] {
            flex: none !important;
            flex-grow: 0 !important;
            height: auto !important;
            min-height: 0 !important;
        }
        .schedule-slot-editor div[class*="st-key-schedule_slots_editor_"] [data-testid="stDataFrame"] {
            height: auto !important;
            min-height: 0 !important;
        }
        .slot-sheet-header {
            align-items: center;
            background: #EDE8E1;
            border: 1px solid #D8D2C8;
            border-radius: 8px;
            color: #102033;
            display: flex;
            font-size: 0.84rem;
            font-weight: 900;
            justify-content: center;
            min-height: 34px;
            padding: 0.4rem 0.5rem;
            white-space: nowrap;
        }
        .slot-action-header {
            background: transparent;
            border-color: transparent;
        }
        .schedule-slot-editor textarea,
        .schedule-slot-editor [data-testid="stTextArea"] textarea,
        .schedule-slot-editor [data-baseweb="textarea"] textarea {
            background: #FFFDF9 !important;
            border: 1px solid #D7CEC1 !important;
            border-radius: 9px !important;
            box-shadow: none !important;
            color: #172033 !important;
            font-size: 0.88rem !important;
            font-weight: 720 !important;
            height: auto !important;
            line-height: 1.34 !important;
            min-height: 76px !important;
            padding: 0.48rem 0.58rem !important;
            resize: vertical !important;
        }
        .schedule-slot-editor [data-testid="stHorizontalBlock"] > div:first-child textarea,
        .schedule-slot-editor [data-testid="stHorizontalBlock"] > div:first-child [data-testid="stTextArea"] textarea {
            font-weight: 850 !important;
            min-height: 66px !important;
            text-align: center !important;
        }
        .schedule-slot-editor textarea::placeholder {
            color: #A59B8C !important;
            -webkit-text-fill-color: #A59B8C !important;
        }
        .schedule-slot-editor textarea:hover {
            background: #FFFFFF !important;
            border-color: #BDAF9F !important;
        }
        .schedule-slot-editor textarea:focus {
            background: #FFFFFF !important;
            border-color: #8CA0B3 !important;
            box-shadow: 0 0 0 2px rgba(79, 111, 143, 0.14) !important;
        }
        .slot-trash-spacer {
            height: 0.1rem;
        }
        .schedule-slot-editor [data-testid="stFormSubmitButton"] button {
            border-radius: 8px !important;
            min-height: 36px !important;
        }
        .schedule-slot-editor [data-testid="stFormSubmitButton"] button:has(span:only-child) {
            padding-left: 0.4rem !important;
            padding-right: 0.4rem !important;
        }
        .schedule-slot-editor [data-testid="stHorizontalBlock"] + [data-testid="stHorizontalBlock"] {
            margin-top: 0 !important;
        }
        div[data-testid="stExpander"]:has(.schedule-slot-editor) {
            height: auto !important;
            min-height: 0 !important;
        }
        div[data-testid="stExpander"]:has(.schedule-slot-editor) [data-testid="stExpanderDetails"] {
            height: auto !important;
            min-height: 0 !important;
            padding-top: 0.75rem !important;
        }
        @media (max-width: 1180px) {
            .schedule-slot-editor {
                overflow-x: auto;
            }
            .schedule-slot-editor [data-testid="stHorizontalBlock"] {
                min-width: 1100px !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
