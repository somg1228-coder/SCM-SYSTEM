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


REQUIRED_REST_TABLES = [
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
    return "OK" if ok else "Check needed"


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


def render_diagnostic_details(title: str, rows: list[dict]) -> None:
    st.markdown(f"##### {title}")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_app_database_panel() -> None:
    if init_db is None or database_status is None or SessionLocal is None:
        st.subheader("Application database")
        st.error(SETTINGS_IMPORT_ERROR or "Database module could not be loaded.")
        return

    schema_error = ""
    try:
        init_db()
    except Exception as exc:
        schema_error = str(exc)

    status = database_status()
    render_diagnostic_details(
        "Supabase PostgreSQL connection diagnostics",
        [
            {"Item": "SCM_DATABASE_URL loaded", "Status": "OK" if status.get("supabase_configured") else "MISSING", "Detail": status.get("url_source") or ""},
            {"Item": "SCM_USE_SUPABASE_DB enabled", "Status": "OK" if status.get("supabase_db_enabled") else "OFF", "Detail": str(status.get("supabase_db_enabled"))},
            {"Item": "App DB engine", "Status": status.get("engine") or "", "Detail": status.get("display_url") or ""},
            {"Item": "Supabase SELECT 1", "Status": "OK" if status.get("supabase_select_1_ok") else "FAIL", "Detail": status.get("supabase_last_error") or ""},
            {"Item": "App DB SELECT 1", "Status": "OK" if status.get("select_1_ok") else "FAIL", "Detail": status.get("last_error") or ""},
        ],
    )

    st.subheader("Application database status")
    cols = st.columns(5)
    cols[0].metric("Database", status.get("engine") or "Unknown")
    cols[1].metric("URL loaded", status_badge(bool(status.get("configured"))))
    cols[2].metric("SELECT 1", status_badge(bool(status.get("select_1_ok"))))
    cols[3].metric("Schema", status_badge(bool(status.get("schema_initialized"))))
    cols[4].metric("Git", current_git_commit_hash())

    if status.get("connected") and status.get("schema_initialized"):
        if status.get("engine") == "Supabase PostgreSQL":
            st.success("Supabase connected")
        else:
            st.warning(f"SQLite in use: {status.get('display_url')}")
    else:
        st.error(schema_error or status.get("message") or "Database connection failed.")

    if status.get("host"):
        st.caption(f"DB Host: {status.get('host')} / Port: {status.get('port') or '-'} / Source: {status.get('url_source')}")
    st.caption(
        f"Last save success: {status.get('last_save_success_at') or '-'} / "
        f"Last save failure item: {status.get('last_save_failure_item') or '-'}"
    )

    st.markdown("##### Application table probes")
    try:
        with SessionLocal() as db:
            rows = []
            for table in APP_DB_TABLES:
                try:
                    count = db.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one()
                    rows.append({"Table": table, "Probe": "OK", "Rows": count, "Error": ""})
                except Exception as exc:
                    rows.append({"Table": table, "Probe": "FAIL", "Rows": "", "Error": str(exc)})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    except Exception as exc:
        st.warning(f"Could not check application tables: {exc}")


def render_connection_panel(status: dict) -> None:
    render_diagnostic_details(
        "Supabase REST API connection diagnostics",
        [
            {"Item": "SUPABASE_URL / SUPABASE_KEY loaded", "Status": "OK" if status.get("configured") else "MISSING", "Detail": status.get("source") or ""},
            {"Item": "warehouse_layouts table probe", "Status": "OK" if status.get("connected") else "FAIL", "Detail": status.get("message") or ""},
        ],
    )

    st.subheader("Supabase REST API status")
    cols = st.columns(3)
    cols[0].metric("Secrets", status_badge(bool(status.get("configured"))))
    cols[1].metric("Connection", status_badge(bool(status.get("connected"))))
    cols[2].metric("Purpose", "REST status")

    message = status.get("message") or ""
    if status.get("connected"):
        st.success(message or "Supabase REST API connected.")
    else:
        st.warning(message or "Check Supabase REST API settings.")


def render_table_panel(status: dict) -> None:
    st.subheader("Supabase REST table status")
    counts = status.get("counts") or {}
    table_errors = status.get("table_errors") or {}
    rows = [
        {
            "Table": table,
            "Probe": "OK" if counts.get(table) is not None else "FAIL",
            "Rows": counts.get(table) if counts.get(table) is not None else "",
            "Error": table_errors.get(table, ""),
        }
        for table in REQUIRED_REST_TABLES
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    item_count = counts.get("inventory_items") or 0
    tx_count = counts.get("inventory_transactions") or 0
    cols = st.columns(2)
    cols[0].metric("Items", f"{item_count:,}")
    cols[1].metric("Transactions", f"{tx_count:,}")


def render_recent_transactions(status: dict) -> None:
    st.subheader("Recent transactions")
    rows = status.get("recent_transactions") or []
    if not rows:
        if status.get("recent_error"):
            st.error(f"Recent transaction probe failed: {status.get('recent_error')}")
        st.caption("No recent transactions.")
        return
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_storage_analysis() -> None:
    if supabase_migration is None:
        st.subheader("Local storage analysis")
        st.warning(SETTINGS_IMPORT_ERROR or "Storage analysis module could not be loaded.")
        return
    st.subheader("Local storage analysis")
    rows = supabase_migration.discover_local_storage()
    if not rows:
        st.caption("No local CSV, Excel, JSON, pickle, or SQLite files were found.")
        return
    df = pd.DataFrame(rows)
    df["size"] = df["size"].map(format_bytes)
    df["last_modified"] = df["last_modified"].map(format_mtime)
    df["operational_candidate"] = df["operational_candidate"].map(lambda value: "Operational candidate" if value else "Archive/config")
    df = df.rename(
        columns={
            "path": "File",
            "kind": "Kind",
            "size": "Size",
            "last_modified": "Modified",
            "operational_candidate": "Classification",
        }
    )
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_migration_panel(connected: bool) -> None:
    if supabase_migration is None or SessionLocal is None:
        st.subheader("Legacy data migration")
        st.warning(SETTINGS_IMPORT_ERROR or "Migration module could not be loaded.")
        return
    st.subheader("Legacy data migration")
    st.caption("Preview or upsert existing operational data without deleting the local source files.")

    preview_key = "supabase_migration_preview"
    cols = st.columns([1, 1, 3])
    if cols[0].button("Preview migration", use_container_width=True):
        with SessionLocal() as db:
            st.session_state[preview_key] = supabase_migration.build_migration_preview(db)

    if cols[1].button("Run Supabase migration", use_container_width=True, disabled=not connected):
        with st.spinner("Migrating to Supabase..."):
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

    st.markdown("##### Preview")
    product_counts = preview.get("product_master_counts") or {}
    daily_counts = preview.get("latest_daily_counts") or {}
    inbound_counts = preview.get("inbound_counts") or {}
    summary_rows = []
    for source_type, count in product_counts.items():
        latest = daily_counts.get(source_type) or {}
        summary_rows.append(
            {
                "Source": source_type,
                "Master rows": count,
                "Latest inventory date": latest.get("latest_date") or "",
                "Latest inventory rows": latest.get("rows") or 0,
                "Inbound rows": inbound_counts.get(source_type) or 0,
            }
        )
    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)


def render_settings_page() -> None:
    st.markdown("## Admin")
    st.caption("Check the application DB, Supabase REST API, table probes, local storage, and migration status.")

    render_app_database_panel()
    if supabase_store is None:
        st.warning(SETTINGS_IMPORT_ERROR or "Supabase REST status module could not be loaded.")
        return
    status = supabase_store.admin_status()
    render_connection_panel(status)
    render_table_panel(status)
    render_recent_transactions(status)
    render_storage_analysis()
    render_migration_panel(bool(status.get("connected")))
