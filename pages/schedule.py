from __future__ import annotations

from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
import sqlite3

import pandas as pd
import streamlit as st


BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "data" / "schedule.db"

WEEKDAYS = ["월", "화", "수", "목", "금"]
SLOT_COLUMNS = ["시간", *WEEKDAYS]
HIGHLIGHT_COLUMNS = ["완료", "이번 주 핵심"]

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
    ensure_schema()
    ensure_weeks_through_current()
    clear_unsaved_seeded_highlights_from_current()
    inject_schedule_css()

    default_week = monday_of(date.today())
    if "schedule_week_start" not in st.session_state:
        st.session_state.schedule_week_start = default_week

    st.markdown('<main class="weekly-schedule-shell">', unsafe_allow_html=True)
    st.markdown('<h1 class="weekly-schedule-title">주간 캘린더</h1>', unsafe_allow_html=True)

    week_start = render_week_controls()
    week = get_or_create_week(week_start)

    highlights_df = render_highlights(week["id"])
    slots_df = render_week_table(week["id"])
    comment = render_comment(week)
    render_save_actions(week["id"], week_start, highlights_df, slots_df, comment)
    render_history()

    st.markdown("</main>", unsafe_allow_html=True)


def ensure_schema() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
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
    df = load_highlights_df(week_id)
    buffer_key = f"schedule_highlights_buffer_{week_id}"
    if buffer_key not in st.session_state:
        st.session_state[buffer_key] = df
    st.markdown('<div class="schedule-highlight-editor">', unsafe_allow_html=True)
    with st.form(key=f"schedule_highlights_form_{week_id}", clear_on_submit=False):
        edited = st.data_editor(
            st.session_state[buffer_key],
            hide_index=True,
            use_container_width=False,
            num_rows="dynamic",
            height=212,
            key=f"schedule_highlights_editor_{week_id}",
            column_order=HIGHLIGHT_COLUMNS,
            column_config={
                "완료": st.column_config.CheckboxColumn("✓", default=False, width=56),
                "이번 주 핵심": st.column_config.TextColumn("이번 주 핵심", width=520),
            },
        )
        if st.form_submit_button("핵심 반영", type="primary", use_container_width=True):
            st.session_state[buffer_key] = normalize_highlights_df(edited)
    st.markdown("</div>", unsafe_allow_html=True)
    return normalize_highlights_df(st.session_state[buffer_key])


def render_week_table(week_id: int) -> pd.DataFrame:
    st.markdown('<h2 class="weekly-section-title">월~금 시간대별 일정</h2>', unsafe_allow_html=True)
    df = load_slots_df(week_id)
    render_schedule_table_html(df)
    with st.expander("시간대별 일정 편집", expanded=True):
        slot_column_config = {
            "시간": st.column_config.TextColumn("시간", width=180),
            **{weekday: st.column_config.TextColumn(weekday, width=310) for weekday in WEEKDAYS},
        }
        with st.form(key=f"schedule_slots_form_{week_id}", clear_on_submit=False):
            edited = st.data_editor(
                df,
                hide_index=True,
                use_container_width=True,
                num_rows="dynamic",
                height=380,
                key=f"schedule_slots_editor_{week_id}",
                column_order=SLOT_COLUMNS,
                column_config=slot_column_config,
            )
            if st.form_submit_button("일정 저장", type="primary", use_container_width=True):
                normalized = normalize_slots_df(edited)
                save_slots_only(week_id, normalized)
                st.success("시간대별 일정 저장 완료")
                st.rerun()
    return normalize_slots_df(edited)


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
            st.success("주간 일정 저장 완료")
            st.rerun()
    with copy_col:
        if st.button("전주 일정 복사", key=f"schedule_copy_previous_{week_id}", use_container_width=True):
            copied = copy_previous_week(week_id, week_start)
            st.success(f"전주 일정 복사 완료 ({copied}건)")
            st.rerun()
    with spacer:
        st.empty()


def render_history() -> None:
    rows = load_history_rows(recent_days=31)
    all_rows = load_history_rows(recent_days=None)
    title_col, download_col = st.columns([4.8, 1.0], gap="small")
    with title_col:
        st.caption("최근 한 달간 저장된 일정과 히스토리입니다. 이전 기록은 DB에 계속 보관됩니다.")
    with download_col:
        st.download_button(
            "히스토리 다운로드",
            data=history_excel_bytes(all_rows),
            file_name=f"물류히스토리_전체요약_{date.today():%Y%m%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="schedule_history_download",
            disabled=not all_rows,
        )
    if not rows:
        st.info("최근 한 달간 저장된 히스토리가 없습니다.")
        return
    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, use_container_width=True)


def get_or_create_week(week_start: date) -> dict:
    week_key = week_start.isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM schedule_weeks WHERE week_start = ?", (week_key,)).fetchone()
        if row is None:
            week_id = create_week(conn, week_start)
            row = conn.execute("SELECT * FROM schedule_weeks WHERE id = ?", (week_id,)).fetchone()
        return dict(row)


def ensure_weeks_through_current() -> None:
    current_week = monday_of(date.today())
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
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
            padding-bottom: 2rem;
        }
        .weekly-schedule-title {
            color: #ffffff;
            font-size: 1.25rem;
            font-weight: 950;
            line-height: 1.2;
            margin: 0.1rem 0 0.25rem;
        }
        div[class*="st-key-schedule_week_picker"] {
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.12);
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
            color: #f5fffb;
            display: flex;
            font-weight: 800;
            min-height: 40px;
        }
        .weekly-section-title {
            color: #ffffff;
            font-size: 1rem;
            font-weight: 900;
            margin: 0.55rem 0 0.25rem;
        }
        .schedule-highlight-editor {
            max-width: 640px;
        }
        div[class*="st-key-schedule_highlights_editor_"] {
            max-width: 640px;
        }
        div[class*="st-key-schedule_highlights_editor_"] [data-testid="stDataFrame"] {
            max-width: 640px;
        }
        div[class*="st-key-schedule_slots_editor_"] {
            min-height: 390px;
        }
        div[class*="st-key-schedule_slots_editor_"] [data-testid="stDataFrame"] {
            min-height: 380px;
        }
        .weekly-section-title.history-title {
            background: rgba(120, 74, 49, 0.72);
            border-radius: 6px 6px 0 0;
            margin-top: 1.25rem;
            padding: 0.34rem 0.55rem;
        }
        .weekly-table-wrap {
            border: 1px solid rgba(200, 218, 213, 0.18);
            border-radius: 6px;
            overflow-x: auto;
        }
        .weekly-table-wrap table {
            border-collapse: collapse;
            color: #f4fffc;
            font-size: 0.82rem;
            table-layout: fixed;
            width: 100%;
        }
        .weekly-table-wrap th,
        .weekly-table-wrap td {
            border: 1px solid rgba(200, 218, 213, 0.18);
            padding: 0.52rem 0.58rem;
            vertical-align: top;
            white-space: normal;
        }
        .weekly-table-wrap th {
            background: rgba(255, 255, 255, 0.05);
            font-weight: 900;
            text-align: left;
        }
        .weekly-table-wrap th:first-child,
        .weekly-table-wrap td:first-child {
            width: 120px;
            font-weight: 900;
        }
        .weekly-table-wrap td {
            min-height: 78px;
            line-height: 1.45;
        }
        .weekly-table-wrap .empty {
            color: #b2d5cd;
            text-align: center;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
