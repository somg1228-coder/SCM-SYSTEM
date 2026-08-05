from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import text

try:
    from backend.database import SessionLocal, database_status, init_db
    from backend import supabase_migration, supabase_store
except Exception as exc:
    SessionLocal = None
    database_status = None
    init_db = None
    supabase_migration = None
    supabase_store = None
    SETTINGS_IMPORT_ERROR = str(exc)
else:
    SETTINGS_IMPORT_ERROR = ""


REQUIRED_TABLES = [
    "suppliers",
    "inventory_items",
    "warehouse_locations",
    "warehouse_layouts",
    "inventory_transactions",
    "stock_counts",
    "audit_logs",
    "import_batches",
]

APP_DB_TABLES = [
    "thirdparty_product_master",
    "offline_product_master",
    "warehouse_product_master",
    "inventory_daily",
    "inventory_inbound",
    "inventory_upload_histories",
    "inventory_upload_snapshots",
    "inventory_output_histories",
    "category_bom_items",
    "material_inventory_items",
    "production_plans",
    "purchase_requests",
    "rfq_quotes",
    "purchase_orders",
    "purchase_documents",
    "suppliers",
    "supplier_evaluations",
    "supplier_evaluation_items",
    "supplier_evaluation_criteria",
    "supplier_grade_rules",
    "warehouse_layouts",
    "schedule_weeks",
    "schedule_highlights",
    "schedule_slots",
    "meeting_reports",
    "meeting_meta",
    "meeting_production_requests",
    "meeting_events",
    "meeting_action_items",
    "cases",
    "purchase_budget_stores",
]


def format_bytes(size: int | float | None) -> str:
    value = float(size or 0)
    for unit in ["B", "KB", "MB", "GB"]:
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def format_mtime(value: float | int | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def status_badge(ok: bool) -> str:
    return "정상" if ok else "확인 필요"


def current_git_commit_hash() -> str:
    root = Path(__file__).resolve().parents[1]
    git_dir = root / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            ref_path = git_dir / head.split(" ", 1)[1]
            return ref_path.read_text(encoding="utf-8").strip()[:12]
        return head[:12]
    except Exception:
        return "unknown"


def render_app_database_panel() -> None:
    if init_db is None or database_status is None or SessionLocal is None:
        st.subheader("운영 DB 상태")
        st.error(SETTINGS_IMPORT_ERROR or "DB 설정 모듈을 불러오지 못했습니다.")
        return
    schema_error = ""
    try:
        init_db()
    except Exception as exc:
        schema_error = str(exc)
    status = database_status()
    st.subheader("운영 DB 상태")
    cols = st.columns(5)
    cols[0].metric("데이터베이스", status.get("engine") or "확인 필요")
    cols[1].metric("URL 로드", status_badge(bool(status.get("configured"))))
    cols[2].metric("SELECT 1", status_badge(bool(status.get("select_1_ok"))))
    cols[3].metric("스키마 초기화", status_badge(bool(status.get("schema_initialized"))))
    cols[4].metric("Git", current_git_commit_hash())

    if status.get("connected") and status.get("schema_initialized"):
        st.success(status.get("message") or "운영 DB에 연결되었습니다.")
    else:
        st.error(schema_error or status.get("message") or "운영 DB 연결을 확인해주세요.")
    if status.get("host"):
        st.caption(f"DB Host: {status.get('host')} / Port: {status.get('port') or '-'} / Source: {status.get('url_source')}")
    st.caption(
        f"최근 저장 성공: {status.get('last_save_success_at') or '-'} / "
        f"최근 저장 실패 항목: {status.get('last_save_failure_item') or '-'}"
    )

    st.markdown("##### 실제 앱 테이블")
    try:
        with SessionLocal() as db:
            rows = [
                {
                    "테이블": table,
                    "건수": db.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one(),
                }
                for table in APP_DB_TABLES
            ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    except Exception as exc:
        st.warning(f"앱 테이블 상태를 확인하지 못했습니다: {exc}")


def render_connection_panel(status: dict) -> None:
    st.subheader("Supabase REST API 연결상태")
    cols = st.columns(3)
    cols[0].metric("Secrets 설정", status_badge(bool(status.get("configured"))))
    cols[1].metric("연결", status_badge(bool(status.get("connected"))))
    cols[2].metric("용도", "REST API 상태 확인")

    message = status.get("message") or ""
    if status.get("connected"):
        st.success(message or "Supabase에 연결되었습니다.")
    else:
        st.warning(message or "Supabase 연결 정보를 확인해 주세요.")


def render_table_panel(status: dict) -> None:
    st.subheader("테이블 상태")
    counts = status.get("counts") or {}
    rows = [
        {
            "테이블": table,
            "존재": "예" if counts.get(table) is not None else "아니오",
            "건수": counts.get(table) if counts.get(table) is not None else "",
        }
        for table in REQUIRED_TABLES
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    item_count = counts.get("inventory_items") or 0
    tx_count = counts.get("inventory_transactions") or 0
    cols = st.columns(2)
    cols[0].metric("품목 수", f"{item_count:,}")
    cols[1].metric("거래 건수", f"{tx_count:,}")


def render_recent_transactions(status: dict) -> None:
    st.subheader("최근 거래")
    rows = status.get("recent_transactions") or []
    if not rows:
        st.caption("최근 거래가 없습니다.")
        return
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_storage_analysis() -> None:
    st.subheader("기존 저장소 분석")
    rows = supabase_migration.discover_local_storage()
    if not rows:
        st.caption("CSV, Excel, JSON, pickle, SQLite 저장 파일을 찾지 못했습니다.")
        return
    df = pd.DataFrame(rows)
    df["size"] = df["size"].map(format_bytes)
    df["last_modified"] = df["last_modified"].map(format_mtime)
    df["operational_candidate"] = df["operational_candidate"].map(lambda value: "운영 후보" if value else "보관/설정")
    df = df.rename(
        columns={
            "path": "파일",
            "kind": "종류",
            "size": "크기",
            "last_modified": "수정일",
            "operational_candidate": "분류",
        }
    )
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_migration_panel(connected: bool) -> None:
    st.subheader("기존 데이터 이관")
    st.caption("기존 SQLite/Excel/JSON 파일은 삭제하지 않고, 운영 데이터만 Supabase로 Upsert합니다.")

    preview_key = "supabase_migration_preview"
    cols = st.columns([1, 1, 3])
    if cols[0].button("이관 미리보기", use_container_width=True):
        with SessionLocal() as db:
            st.session_state[preview_key] = supabase_migration.build_migration_preview(db)

    if cols[1].button("Supabase 이관 실행", use_container_width=True, disabled=not connected):
        with st.spinner("Supabase로 이관 중입니다..."):
            with SessionLocal() as db:
                result = supabase_migration.migrate_operational_data(db)
        if result.get("ok"):
            st.success(result.get("message"))
            st.json(result)
        else:
            st.error(result.get("message"))

    preview = st.session_state.get(preview_key)
    if not preview:
        return

    st.markdown("##### 미리보기")
    product_counts = preview.get("product_master_counts") or {}
    daily_counts = preview.get("latest_daily_counts") or {}
    inbound_counts = preview.get("inbound_counts") or {}
    summary_rows = []
    for source_type, count in product_counts.items():
        latest = daily_counts.get(source_type) or {}
        summary_rows.append(
            {
                "재고처": source_type,
                "마스터 품목": count,
                "최신 재고일": latest.get("latest_date") or "",
                "최신 재고 행": latest.get("rows") or 0,
                "입고 이력": inbound_counts.get(source_type) or 0,
            }
        )
    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)


def render_settings_page() -> None:
    st.markdown("## 관리자")
    st.caption("실제 운영 DB 연결, Supabase REST API, 테이블 상태, 기존 로컬 데이터 이관 상태를 확인합니다.")

    render_app_database_panel()
    status = supabase_store.admin_status()
    render_connection_panel(status)
    render_table_panel(status)
    render_recent_transactions(status)
    render_storage_analysis()
    render_migration_panel(bool(status.get("connected")))
