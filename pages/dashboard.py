from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta
from html import escape
import math
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlencode

import pandas as pd
import streamlit as st

from sqlalchemy import case, distinct, func, select
from backend.perf import perf_span, record_perf_event

try:
    from backend.legacy_storage import connect_sqlite_compatible, legacy_store_available
    from backend.database import SessionLocal, init_db
    from backend import services
    from backend.models import CategoryBomItem, InventoryDaily, InventoryInbound, ProductionPlan, PurchaseOrder, PurchaseRequest, RfqQuote
except (ModuleNotFoundError, RuntimeError) as exc:
    connect_sqlite_compatible = None
    legacy_store_available = None
    SessionLocal = None
    init_db = None
    services = None
    CategoryBomItem = None
    InventoryDaily = None
    InventoryInbound = None
    ProductionPlan = None
    PurchaseOrder = None
    PurchaseRequest = None
    RfqQuote = None
    DASHBOARD_IMPORT_ERROR = str(exc)
else:
    DASHBOARD_IMPORT_ERROR = ""

BASE_DIR = Path(__file__).resolve().parents[1]
RETURN_CASE_DB_PATH = BASE_DIR / "ReturnCaseSystem" / "cases.db"
SCHEDULE_DB_PATH = BASE_DIR / "data" / "schedule.db"
SOURCE_TYPES = ["3PL", "오프라인", "창고"]
_DASHBOARD_READY = False
PURCHASE_PR_PENDING_STATUSES = ["작성", "상신"]
PURCHASE_PR_OPEN_STATUSES = ["작성", "상신", "승인"]
PO_INBOUND_DONE = "입고완료"
PO_INBOUND_WAITING_STATUSES = ["입고대기", "부분입고"]
PO_PROGRESS_DONE = "발주완료"
PO_PROGRESS_CANCELED = "취소"
DASHBOARD_QUERY_CATALOG = {
    "inventory_latest_work_date": {
        "table": "inventory_daily",
        "columns": "max(work_date)",
        "filter": "-",
        "purpose": "대시보드 기준 재고 일자",
        "reusable": True,
    },
    "inventory_kpi_summary": {
        "table": "inventory_daily",
        "columns": "count, stock sums, inbound/outbound sums, safety flags",
        "filter": "work_date = latest",
        "purpose": "상단 KPI 및 재고 위험 통계",
        "reusable": True,
    },
    "inventory_source_group": {
        "table": "inventory_daily",
        "columns": "source_type, stock sums, safety flags",
        "filter": "work_date = latest GROUP BY source_type",
        "purpose": "재고처별 현황 차트",
        "reusable": True,
    },
    "purchase_snapshot": {
        "table": "purchase_requests, rfq_quotes, purchase_orders",
        "columns": "PR count/amount, RFQ count, PO status/month aggregates",
        "filter": "PO order_date <= work_date OR NULL",
        "purpose": "구매 KPI 및 진행 현황",
        "reusable": True,
    },
    "purchase_recent_po_5": {
        "table": "purchase_orders",
        "columns": "id, po_number, supplier_name, item_name, quantity, inbound/progress dates/status",
        "filter": "ORDER BY updated_at/order_date/id DESC LIMIT 5",
        "purpose": "최근 발주/입고 테이블",
        "reusable": False,
    },
    "production_week_rows": {
        "table": "production_plans",
        "columns": "id, product_name, plan_qty, due_date, status",
        "filter": "current week, status != 취소, plan_qty > 0",
        "purpose": "주간 일정 및 핵심업무 생산 항목",
        "reusable": True,
    },
}


def render_html(markup: str) -> None:
    if hasattr(st, "html"):
        st.html(markup)
    else:
        st.markdown(markup, unsafe_allow_html=True)


def log_dashboard_event(message: str) -> None:
    try:
        log_path = BASE_DIR / "data" / "dashboard_perf.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except Exception:
        pass


def dashboard_result_size(value) -> str:
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            if isinstance(item, (list, tuple, set)):
                parts.append(f"{key}={len(item)}")
        return ", ".join(parts) if parts else f"keys={len(value)}"
    if isinstance(value, (list, tuple, set)):
        return f"rows={len(value)}"
    return type(value).__name__


def timed_dashboard_step(name: str, action):
    started_at = time.perf_counter()
    log_dashboard_event(f"{name} start")
    try:
        result = action()
        elapsed = time.perf_counter() - started_at
        log_dashboard_event(f"{name} done seconds={elapsed:.3f} {dashboard_result_size(result)}")
        record_perf_event(f"dashboard.{name}", elapsed, result=dashboard_result_size(result))
        return result
    except Exception as exc:
        elapsed = time.perf_counter() - started_at
        log_dashboard_event(f"{name} error seconds={elapsed:.3f} {type(exc).__name__}: {exc}")
        record_perf_event(f"dashboard.{name}", elapsed, success=False, error=str(exc))
        raise


def dashboard_query_result_size(value) -> str:
    if isinstance(value, list):
        return f"rows={len(value)}"
    if hasattr(value, "__len__") and not isinstance(value, (str, bytes, dict)):
        try:
            return f"rows={len(value)}"
        except TypeError:
            pass
    return type(value).__name__


def dashboard_statement_preview(statement) -> str:
    try:
        text = " ".join(str(statement.compile(compile_kwargs={"literal_binds": False})).split())
    except Exception:
        text = " ".join(str(statement).split())
    if len(text) > 360:
        return f"{text[:357]}..."
    return text


def dashboard_table_set(table_text: str) -> set[str]:
    return {part.strip() for part in str(table_text or "").split(",") if part.strip()}


def record_dashboard_query_metadata(metrics: dict, label: str, elapsed: float, result_size: str, statement) -> None:
    catalog = DASHBOARD_QUERY_CATALOG.get(label, {})
    queries = metrics.setdefault("queries", [])
    table = str(catalog.get("table") or "")
    table_set = dashboard_table_set(table)
    duplicate_table = bool(
        table_set
        and any(table_set.intersection(dashboard_table_set(str(item.get("table") or ""))) for item in queries)
    )
    query_row = {
        "label": label,
        "table": table,
        "columns": catalog.get("columns", ""),
        "filter": catalog.get("filter", ""),
        "purpose": catalog.get("purpose", ""),
        "elapsed_seconds": round(float(elapsed or 0.0), 4),
        "result": result_size,
        "duplicate_table": duplicate_table,
        "reusable": bool(catalog.get("reusable", False)),
        "statement": dashboard_statement_preview(statement),
    }
    queries.append(query_row)
    log_dashboard_event(
        "api_call "
        f"label={label} table={query_row['table']} columns={query_row['columns']} "
        f"filter={query_row['filter']} purpose={query_row['purpose']} "
        f"elapsed={elapsed:.3f}s duplicate_table={duplicate_table} reusable={query_row['reusable']}"
    )


def record_dashboard_stage_skip(metrics: dict, name: str, reason: str) -> None:
    metrics.setdefault("stages", []).append({"stage": name, "elapsed_seconds": 0.0, "skipped": True, "reason": reason})
    log_dashboard_event(f"{name} skipped reason={reason}")
    record_perf_event(f"dashboard.{name}", 0.0, skipped=True, reason=reason)


@contextmanager
def dashboard_stage(metrics: dict, name: str):
    started_at = time.perf_counter()
    log_dashboard_event(f"{name} start")
    try:
        yield
    finally:
        elapsed = time.perf_counter() - started_at
        metrics.setdefault("stages", []).append({"stage": name, "elapsed_seconds": round(elapsed, 4)})
        log_dashboard_event(f"{name} done seconds={elapsed:.3f}")
        record_perf_event(f"dashboard.{name}", elapsed)


def dashboard_query(metrics: dict, db, label: str, statement, mode: str = "all"):
    started_at = time.perf_counter()
    metrics["count"] = int(metrics.get("count", 0)) + 1
    log_dashboard_event(f"sql {label} start")
    result = db.execute(statement)
    if mode == "one":
        value = result.one()
    elif mode == "scalar":
        value = result.scalar()
    elif mode == "scalars":
        value = list(result.scalars())
    else:
        value = result.all()
    elapsed = time.perf_counter() - started_at
    metrics["seconds"] = float(metrics.get("seconds", 0.0)) + elapsed
    result_size = dashboard_query_result_size(value)
    record_dashboard_query_metadata(metrics, label, elapsed, result_size, statement)
    log_dashboard_event(f"sql {label} done seconds={elapsed:.3f} {result_size}")
    record_perf_event("dashboard.sql", elapsed, label=label, result=result_size)
    return value


def dashboard_top_slowest(items: list[dict], limit: int = 10) -> list[dict]:
    return sorted(items, key=lambda item: float(item.get("elapsed_seconds", 0.0) or 0.0), reverse=True)[:limit]


def log_dashboard_perf_summary(dashboard_data: dict, render_stages: list[dict] | None = None) -> None:
    stage_rows = list(dashboard_data.get("stage_timings", []))
    if render_stages:
        stage_rows.extend(render_stages)
    queries = list(dashboard_data.get("api_calls", []))
    slowest = dashboard_top_slowest([*stage_rows, *queries], 10)
    log_dashboard_event(
        "[PERF] SUMMARY "
        f"dashboard TOTAL={dashboard_data.get('total_seconds', 0.0)}s "
        f"DB={dashboard_data.get('db_seconds', 0.0)}s API calls={dashboard_data.get('db_call_count', 0)}"
    )
    for index, item in enumerate(slowest, start=1):
        label = item.get("stage") or item.get("label") or "-"
        log_dashboard_event(f"[PERF] TOP{index} {label} elapsed={float(item.get('elapsed_seconds', 0.0) or 0.0):.3f}s")
    for index, query in enumerate(queries, start=1):
        log_dashboard_event(
            "[API] "
            f"{index}. label={query.get('label')} table={query.get('table')} "
            f"columns={query.get('columns')} filter={query.get('filter')} "
            f"purpose={query.get('purpose')} elapsed={query.get('elapsed_seconds')}s "
            f"duplicate_table={query.get('duplicate_table')} reusable={query.get('reusable')}"
        )


def log_dashboard_cache_status(dashboard_data: dict, trend_days: int) -> None:
    cache_token = dashboard_data.get("cache_created_at")
    cache_key = f"trend_days={trend_days}|created_at={cache_token}"
    previous_key = st.session_state.get("dashboard_data_cache_key")
    status = "HIT" if previous_key == cache_key else "MISS"
    st.session_state["dashboard_data_cache_key"] = cache_key
    log_dashboard_event(f"cache {status} key={cache_key}")
    record_perf_event("dashboard.cache", 0.0, status=status, key=cache_key)


def render_dashboard() -> None:
    log_dashboard_event("render_dashboard start")
    with perf_span("dashboard.purchase_trend_param"):
        trend_days = dashboard_purchase_trend_days()
    dashboard_data = timed_dashboard_step("dashboard_data", lambda: get_dashboard_data(trend_days))
    log_dashboard_cache_status(dashboard_data, trend_days)
    inventory_summary = dashboard_data.get("inventory_summary", {})
    work_date = inventory_summary.get("work_date") or date.today()
    purchase_summary = dashboard_data.get("purchase_summary", {})
    core_tasks_summary = dashboard_data.get("core_tasks_summary", {})
    return_case_summary = dashboard_data.get("return_case_summary", {})
    weekly_markup = dashboard_data.get("weekly_markup") or weekly_schedule_html()
    log_dashboard_event(
        "dashboard_data metrics "
        f"db_calls={dashboard_data.get('db_call_count', 0)} "
        f"db_seconds={float(dashboard_data.get('db_seconds', 0.0)):.3f}"
    )
    log_dashboard_event("dashboard_html render start")
    render_metrics = {"stages": []}
    with dashboard_stage(render_metrics, "cards_render"):
        kpi_markup = kpi_cards_html(inventory_summary, purchase_summary)
    with dashboard_stage(render_metrics, "charts_render"):
        status_grid_markup = (
            issue_donut_html(
                return_case_summary.get("category_rows", []),
                return_case_summary.get("total_count", 0),
                return_case_summary.get("monthly_rows", []),
                return_case_summary.get("year", date.today().year),
            )
            + occurrence_status_html(return_case_summary)
        )
        warehouse_markup = warehouse_status_html(inventory_summary.get("source_status", []))
    with dashboard_stage(render_metrics, "tables_render"):
        recent_orders_markup = recent_orders_html(purchase_summary.get("recent_po_inbound", []))
        purchase_progress_markup = purchase_progress_html(purchase_summary.get("progress_rows", []))
        core_tasks_markup = schedule_core_tasks_html(core_tasks_summary)
    with dashboard_stage(render_metrics, "html_build"):
        markup = f"""
            <main class="dashboard-shell">
                {weekly_markup}
                {kpi_markup}
                <section class="dashboard-middle-grid">
                    <section class="status-grid">
                        {status_grid_markup}
                    </section>
                    {recent_orders_markup}
                </section>
                <section class="dashboard-bottom-grid">
                    {warehouse_markup}
                    {purchase_progress_markup}
                    {core_tasks_markup}
                </section>
            </main>
            """
    with dashboard_stage(render_metrics, "html_render"):
        render_html(markup)
    log_dashboard_perf_summary(dashboard_data, render_metrics.get("stages", []))
    log_dashboard_event("render_dashboard done")


def dashboard_available() -> bool:
    global _DASHBOARD_READY
    if _DASHBOARD_READY:
        return True
    if init_db is None or SessionLocal is None or services is None:
        return False
    try:
        init_db(ensure_schema=False)
    except Exception as exc:
        global DASHBOARD_IMPORT_ERROR
        DASHBOARD_IMPORT_ERROR = f"재고관리 DB 초기화 실패: {exc}"
        return False
    _DASHBOARD_READY = True
    return True


def default_inventory_summary() -> dict:
    return {
        "sku_count": 0,
        "current_stock": 0,
        "available_stock": 0,
        "need_inbound_count": 0,
        "soldout_count": 0,
        "short_count": 0,
        "outbound_qty": 0,
        "inbound_qty": 0,
        "return_as_count": 0,
        "work_date": None,
        "charts": {},
        "source_status": [],
        "weekly_3pl_inbound": [],
        "recent_inbound": [],
    }


def default_purchase_summary(trend_days: int = 7) -> dict:
    return {
        "pending_pr_count": 0,
        "pending_pr_amount": 0,
        "po_progress_count": 0,
        "uninbound_amount": 0,
        "delayed_count": 0,
        "max_delay_days": 0,
        "month_amount": 0,
        "month_change_rate": 0,
        "trend_days": trend_days,
        "trend_rows": [],
        "progress_rows": [],
        "priority_rows": [],
        "recent_po_inbound": [],
    }


def default_return_case_summary(work_date: date) -> dict:
    current_year = work_date.year if hasattr(work_date, "year") else date.today().year
    return {
        "total_count": 0,
        "category_rows": [],
        "monthly_rows": [{"month": month, "month_key": f"{current_year}{month:02d}", "count": 0} for month in range(1, 13)],
        "recent_cases": [],
        "today_count": 0,
        "week_count": 0,
        "in_progress_count": 0,
        "done_count": 0,
        "delayed_count": 0,
        "year": current_year,
    }


@st.cache_data(ttl=60, show_spinner=False)
def get_dashboard_data(trend_days: int = 7) -> dict:
    started_at = time.perf_counter()
    metrics = {"count": 0, "seconds": 0.0, "queries": [], "stages": []}
    inventory_summary = default_inventory_summary()
    purchase_summary = default_purchase_summary(trend_days)
    return_case_summary = default_return_case_summary(date.today())
    core_tasks_summary = default_core_tasks_summary()
    weekly_markup = ""

    if not dashboard_available():
        return {
            "inventory_summary": inventory_summary,
            "purchase_summary": purchase_summary,
            "return_case_summary": return_case_summary,
            "core_tasks_summary": core_tasks_summary,
            "weekly_markup": weekly_markup,
            "db_call_count": 0,
            "db_seconds": 0.0,
            "api_calls": [],
            "stage_timings": [],
            "total_seconds": 0.0,
            "cache_created_at": started_at,
        }

    with dashboard_stage(metrics, "db_session_and_payload"):
        db = SessionLocal()
        try:
            payload = build_dashboard_data_payload(db, trend_days, metrics)
        finally:
            db.close()

    with dashboard_stage(metrics, "data_processing.merge_payload"):
        inventory_summary = {**inventory_summary, **payload.get("inventory_summary", {})}
        work_date = inventory_summary.get("work_date") or date.today()
    with dashboard_stage(metrics, "return_case_summary"):
        return_case_summary = get_return_case_summary(work_date)
    with dashboard_stage(metrics, "schedule_processing"):
        inventory_summary["return_as_count"] = int(return_case_summary.get("week_count") or return_case_summary.get("total_count") or 0)
        core_tasks_summary = build_core_tasks_summary_from_schedule(payload.get("production_rows", []))
        weekly_markup = build_weekly_schedule_html_from_production(payload.get("production_rows", []))
    with dashboard_stage(metrics, "purchase_summary_merge"):
        purchase_summary = {**purchase_summary, **payload.get("purchase_summary", {})}
    elapsed = time.perf_counter() - started_at
    slowest = dashboard_top_slowest([*metrics.get("stages", []), *metrics.get("queries", [])], 10)
    log_dashboard_event(
        f"dashboard_payload done seconds={elapsed:.3f} "
        f"db_calls={metrics['count']} db_seconds={metrics['seconds']:.3f}"
    )
    for index, item in enumerate(slowest, start=1):
        log_dashboard_event(
            f"dashboard_payload top{index} name={item.get('stage') or item.get('label')} "
            f"seconds={float(item.get('elapsed_seconds', 0.0) or 0.0):.3f}"
        )
    return {
        "inventory_summary": inventory_summary,
        "purchase_summary": purchase_summary,
        "return_case_summary": return_case_summary,
        "core_tasks_summary": core_tasks_summary,
        "weekly_markup": weekly_markup,
        "db_call_count": metrics["count"],
        "db_seconds": round(metrics["seconds"], 3),
        "api_calls": metrics.get("queries", []),
        "stage_timings": metrics.get("stages", []),
        "total_seconds": round(elapsed, 3),
        "cache_created_at": started_at,
    }


def build_dashboard_data_payload(db, trend_days: int, metrics: dict) -> dict:
    record_dashboard_stage_skip(metrics, "load_product_master", "dashboard does not need product master rows")
    with dashboard_stage(metrics, "load_inventory"):
        work_date = dashboard_query(metrics, db, "inventory_latest_work_date", select(func.max(InventoryDaily.work_date)), "scalar") or date.today()
        inventory_summary = build_dashboard_inventory_summary_optimized(db, work_date, metrics)
    record_dashboard_stage_skip(metrics, "load_inbound", "inbound KPI is aggregated from inventory_daily")
    record_dashboard_stage_skip(metrics, "load_outbound", "outbound KPI is aggregated from inventory_daily")
    with dashboard_stage(metrics, "load_purchase"):
        purchase_summary = build_dashboard_purchase_summary_optimized(db, work_date, trend_days, metrics)
    with dashboard_stage(metrics, "load_production"):
        production_rows = dashboard_query(
            metrics,
            db,
            "production_week_rows",
            select(
                ProductionPlan.id,
                ProductionPlan.product_name,
                ProductionPlan.plan_qty,
                ProductionPlan.due_date,
                ProductionPlan.status,
            )
            .where(
                ProductionPlan.due_date >= week_start_date(work_date),
                ProductionPlan.due_date <= week_start_date(work_date) + timedelta(days=6),
                ProductionPlan.status != "취소",
                ProductionPlan.plan_qty > 0,
            )
            .order_by(ProductionPlan.due_date, ProductionPlan.id),
        ) if ProductionPlan is not None else []
    return {
        "inventory_summary": inventory_summary,
        "purchase_summary": purchase_summary,
        "production_rows": production_rows,
    }


def week_start_date(value: date) -> date:
    return value - timedelta(days=value.weekday())


def build_dashboard_inventory_summary_optimized(db, work_date: date, metrics: dict) -> dict:
    with dashboard_stage(metrics, "metrics"):
        summary_row = dashboard_query(
            metrics,
            db,
            "inventory_kpi_summary",
            select(
                func.count(InventoryDaily.id),
                func.coalesce(func.sum(InventoryDaily.current_stock), 0),
                func.coalesce(func.sum(InventoryDaily.available_stock), 0),
                func.coalesce(func.sum(InventoryDaily.outbound_qty), 0),
                func.coalesce(func.sum(InventoryDaily.inbound_qty), 0),
                func.coalesce(func.sum(case((InventoryDaily.available_stock <= InventoryDaily.safe_stock, 1), else_=0)), 0),
                func.coalesce(func.sum(case((InventoryDaily.current_stock <= 0, 1), else_=0)), 0),
                func.coalesce(func.sum(case((InventoryDaily.available_stock < InventoryDaily.safe_stock, 1), else_=0)), 0),
            ).where(InventoryDaily.work_date == work_date),
            "one",
        )
    with dashboard_stage(metrics, "stock_summary"):
        source_rows = dashboard_query(
            metrics,
            db,
            "inventory_source_group",
            select(
                InventoryDaily.source_type,
                func.coalesce(func.sum(InventoryDaily.current_stock), 0),
                func.coalesce(func.sum(InventoryDaily.available_stock), 0),
                func.coalesce(func.sum(case((InventoryDaily.available_stock <= InventoryDaily.safe_stock, 1), else_=0)), 0),
                func.coalesce(func.sum(case((InventoryDaily.current_stock <= 0, 1), else_=0)), 0),
                func.coalesce(func.sum(case((InventoryDaily.available_stock < InventoryDaily.safe_stock, 1), else_=0)), 0),
            )
            .where(InventoryDaily.work_date == work_date)
            .group_by(InventoryDaily.source_type),
        )
    source_lookup = {str(row[0] or ""): row for row in source_rows}
    source_status = []
    for source_type in SOURCE_TYPES:
        row = source_lookup.get(source_type)
        current_stock = int(row[1] or 0) if row else 0
        available_stock = int(row[2] or 0) if row else 0
        problem_count = (int(row[3] or 0) + int(row[4] or 0) + int(row[5] or 0)) if row else 0
        ratio = round((available_stock / current_stock) * 100) if current_stock > 0 else 0
        source_status.append(
            {
                "name": source_type,
                "rate": max(0, min(ratio, 100)),
                "qty": current_stock,
                "problem_count": problem_count,
                "tone": source_status_tone(current_stock, ratio, problem_count),
            }
        )

    record_dashboard_stage_skip(metrics, "inbound_summary", "dashboard only needs latest inbound quantity aggregate")
    record_dashboard_stage_skip(metrics, "outbound_summary", "dashboard only needs latest outbound quantity aggregate")
    with dashboard_stage(metrics, "charts_prepare"):
        charts = {
            "stock_by_source": [{"label": str(row[0] or "미분류"), "value": int(row[1] or 0)} for row in source_rows],
            "stock_by_category": [],
            "outbound_by_category": [],
            "stock_trend": [],
            "outbound_trend": [],
            "need_inbound_top10": [],
        }

    return {
        "sku_count": int(summary_row[0] or 0),
        "current_stock": int(summary_row[1] or 0),
        "available_stock": int(summary_row[2] or 0),
        "outbound_qty": int(summary_row[3] or 0),
        "inbound_qty": int(summary_row[4] or 0),
        "need_inbound_count": int(summary_row[5] or 0),
        "soldout_count": int(summary_row[6] or 0),
        "short_count": int(summary_row[7] or 0),
        "work_date": work_date,
        "charts": charts,
        "source_status": source_status,
        "weekly_3pl_inbound": [],
        "recent_inbound": [],
    }


def build_dashboard_purchase_summary_optimized(db, work_date: date, trend_days: int, metrics: dict) -> dict:
    month_start = work_date.replace(day=1)
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)
    with dashboard_stage(metrics, "purchase_summary"):
        purchase_snapshot = dashboard_purchase_snapshot(db, work_date, month_start, prev_month_start, prev_month_end, metrics)
    pending_count = purchase_snapshot[0] or 0
    pending_amount = purchase_snapshot[1] or 0
    purchase_aggregate = purchase_snapshot[2:12]
    month_amount = purchase_aggregate[4] or 0
    prev_month_amount = purchase_aggregate[5] or 0
    rfq_count = purchase_snapshot[12] or 0
    with dashboard_stage(metrics, "production_summary"):
        progress_rows = build_purchase_progress_rows_from_aggregate(work_date, pending_count, pending_amount, rfq_count, purchase_aggregate)
    with dashboard_stage(metrics, "recent_po_inbound_summary"):
        recent_rows = get_recent_po_inbound_rows_optimized(db, metrics)
    priority_rows = []
    trend_rows = []
    min_delayed_date = purchase_aggregate[3]
    max_delay_days = (work_date - min_delayed_date).days if hasattr(min_delayed_date, "toordinal") else 0
    change_rate = ((float(month_amount or 0) - float(prev_month_amount or 0)) / float(prev_month_amount) * 100) if prev_month_amount else (100.0 if month_amount else 0.0)
    return {
        "pending_pr_count": int(pending_count or 0),
        "pending_pr_amount": int(pending_amount or 0),
        "po_progress_count": int(purchase_aggregate[0] or 0),
        "uninbound_amount": int(purchase_aggregate[1] or 0),
        "delayed_count": int(purchase_aggregate[2] or 0),
        "max_delay_days": max_delay_days,
        "month_amount": int(month_amount or 0),
        "month_change_rate": change_rate,
        "trend_days": trend_days,
        "trend_rows": trend_rows,
        "progress_rows": progress_rows,
        "priority_rows": priority_rows,
        "recent_po_inbound": recent_rows,
    }


def dashboard_purchase_snapshot(db, work_date: date, month_start: date, prev_month_start: date, prev_month_end: date, metrics: dict):
    open_po = (PurchaseOrder.inbound_status != PO_INBOUND_DONE) & (PurchaseOrder.progress_status != PO_PROGRESS_CANCELED)
    delayed_po = open_po & (PurchaseOrder.expected_inbound_date < work_date)
    inbound_waiting = PurchaseOrder.inbound_status.in_(PO_INBOUND_WAITING_STATUSES) & (PurchaseOrder.progress_status != PO_PROGRESS_CANCELED)
    pending_count = (
        select(func.count(PurchaseRequest.id))
        .where(PurchaseRequest.approval_status.in_(PURCHASE_PR_PENDING_STATUSES))
        .scalar_subquery()
    )
    pending_amount = (
        select(func.coalesce(func.sum(quote_total_expr()), 0))
        .select_from(PurchaseRequest)
        .join(RfqQuote, RfqQuote.pr_number == PurchaseRequest.pr_number)
        .where(PurchaseRequest.approval_status.in_(PURCHASE_PR_PENDING_STATUSES))
        .scalar_subquery()
    )
    rfq_count = select(func.count(distinct(RfqQuote.pr_number))).scalar_subquery()
    return dashboard_query(
        metrics,
        db,
        "purchase_snapshot",
        select(
            pending_count,
            pending_amount,
            func.coalesce(func.sum(case((open_po, 1), else_=0)), 0),
            func.coalesce(func.sum(case((open_po, PurchaseOrder.order_amount), else_=0)), 0),
            func.coalesce(func.sum(case((delayed_po, 1), else_=0)), 0),
            func.min(case((delayed_po, PurchaseOrder.expected_inbound_date), else_=None)),
            func.coalesce(func.sum(case((PurchaseOrder.order_date >= month_start, PurchaseOrder.order_amount), else_=0)), 0),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (PurchaseOrder.order_date >= prev_month_start)
                            & (PurchaseOrder.order_date <= prev_month_end),
                            PurchaseOrder.order_amount,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(func.sum(case((PurchaseOrder.progress_status == PO_PROGRESS_DONE, 1), else_=0)), 0),
            func.coalesce(func.sum(case((PurchaseOrder.progress_status == PO_PROGRESS_DONE, PurchaseOrder.order_amount), else_=0)), 0),
            func.coalesce(func.sum(case((inbound_waiting, 1), else_=0)), 0),
            func.coalesce(func.sum(case((inbound_waiting, PurchaseOrder.order_amount), else_=0)), 0),
            rfq_count,
        ).where((PurchaseOrder.order_date <= work_date) | (PurchaseOrder.order_date.is_(None))),
        "one",
    )


def quote_total_expr():
    order_qty = case((PurchaseRequest.quantity >= RfqQuote.moq, PurchaseRequest.quantity), else_=RfqQuote.moq)
    return order_qty * RfqQuote.unit_price + RfqQuote.shipping_fee


def build_purchase_progress_rows_from_aggregate(work_date: date, pending_count: int, pending_amount: int, rfq_count: int, row) -> list[dict]:
    delayed_count = int(row[2] or 0)
    min_delayed_date = row[3]
    max_delay_days = (work_date - min_delayed_date).days if hasattr(min_delayed_date, "toordinal") else 0
    return [
        {"label": "구매요청 대기", "value": int(pending_count or 0), "caption": f"{int(pending_amount or 0):,}원", "tone": "orange", "href": purchase_link("구매요청(PR)", "pr_pending")},
        {"label": "견적 진행", "value": int(rfq_count or 0), "caption": "RFQ 등록", "tone": "cyan", "href": purchase_link("견적관리(RFQ)", "rfq_progress")},
        {"label": "발주 완료", "value": int(row[6] or 0), "caption": f"{int(row[7] or 0):,}원", "tone": "blue", "href": purchase_link("발주관리(PO)", "po_progress")},
        {"label": "입고 대기", "value": int(row[8] or 0), "caption": f"{int(row[9] or 0):,}원", "tone": "green", "href": purchase_link("발주관리(PO)", "inbound_waiting")},
        {"label": "납기 지연", "value": delayed_count, "caption": f"최대 {max_delay_days}일", "tone": "red" if delayed_count else "cyan", "href": purchase_link("발주관리(PO)", "po_delay")},
    ]


def get_recent_po_inbound_rows_optimized(db, metrics: dict, limit: int = 5) -> list[dict]:
    rows = dashboard_query(
        metrics,
        db,
        "purchase_recent_po_5",
        select(
            PurchaseOrder.id,
            PurchaseOrder.po_number,
            PurchaseOrder.supplier_name,
            PurchaseOrder.item_name,
            PurchaseOrder.quantity,
            PurchaseOrder.inbound_status,
            PurchaseOrder.expected_inbound_date,
            PurchaseOrder.actual_inbound_date,
            PurchaseOrder.progress_status,
            PurchaseOrder.updated_at,
            PurchaseOrder.order_date,
        )
        .order_by(PurchaseOrder.updated_at.desc(), PurchaseOrder.order_date.desc(), PurchaseOrder.id.desc())
        .limit(limit),
    )
    return recent_po_inbound_rows(rows, limit)


def default_core_tasks_summary() -> dict:
    today = pd.Timestamp(date.today())
    current_week_start = today - pd.Timedelta(days=today.weekday())
    return {
        "week_start": current_week_start,
        "week_end": current_week_start + pd.Timedelta(days=6),
        "rows": [],
        "source": "current",
    }


def build_weekly_schedule_html_from_production(production_rows: list) -> str:
    week_start, days = get_dashboard_week_schedule_without_production()
    week_end = week_start + pd.Timedelta(days=6)
    schedule_by_day = days_to_schedule_map(days)
    merge_week_schedule_items(schedule_by_day, production_schedule_items_from_rows(production_rows, week_start.date()))
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    today = pd.Timestamp(date.today())
    rebuilt_days = []
    for index, weekday in enumerate(weekdays):
        day = week_start + pd.Timedelta(days=index)
        state = "active" if day.date() == today.date() else ""
        if index == 5:
            state = f"{state} blue".strip()
        if index == 6:
            state = f"{state} red".strip()
        rebuilt_days.append((f"{day:%m.%d} ({weekday})", schedule_by_day.get(index) or [], state))
    cells = "".join(
        f"""
        <div class="week-cell {state}">
            <div class="week-date">{label}</div>
            <ul>{''.join(f'<li>{escape(str(item))}</li>' for item in items)}</ul>
        </div>
        """
        for label, items, state in rebuilt_days
    )
    return f"""
    <section class="panel schedule-panel">
        <div class="panel-title-row">
            <h2>물류 주간 일정표</h2>
            <div class="week-range">
                <span>월</span>
                <strong>{week_start:%Y.%m.%d} ~ {week_end:%Y.%m.%d}</strong>
                <span>주</span>
                <span>차</span>
            </div>
        </div>
        <div class="week-board">{cells}</div>
    </section>
    """


def get_dashboard_week_schedule_without_production() -> tuple[pd.Timestamp, list[tuple[str, list[str], str]]]:
    today = pd.Timestamp(date.today())
    current_week_start = today - pd.Timedelta(days=today.weekday())
    week_start = current_week_start
    schedule_by_day = {index: [] for index in range(7)}
    if legacy_store_available is not None and connect_sqlite_compatible is not None and legacy_store_available(SCHEDULE_DB_PATH):
        try:
            with connect_sqlite_compatible(SCHEDULE_DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                week_row = conn.execute(
                    "SELECT id, week_start FROM schedule_weeks WHERE week_start = ?",
                    (current_week_start.date().isoformat(),),
                ).fetchone()
                if week_row is None:
                    week_row = conn.execute("SELECT id, week_start FROM schedule_weeks ORDER BY week_start DESC LIMIT 1").fetchone()
                if week_row is not None:
                    week_start = pd.Timestamp(week_row["week_start"])
                    rows = conn.execute(
                        """
                        SELECT time_label, mon, tue, wed, thu, fri
                        FROM schedule_slots
                        WHERE week_id = ?
                        ORDER BY sort_order, id
                        """,
                        (week_row["id"],),
                    ).fetchall()
                    schedule_by_day.update(summarize_schedule_slots(rows))
        except sqlite3.Error:
            schedule_by_day = {index: [] for index in range(7)}
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    days = []
    for index, weekday in enumerate(weekdays):
        day = week_start + pd.Timedelta(days=index)
        state = "active" if day.date() == today.date() else ""
        if index == 5:
            state = f"{state} blue".strip()
        if index == 6:
            state = f"{state} red".strip()
        days.append((f"{day:%m.%d} ({weekday})", schedule_by_day.get(index) or [], state))
    return week_start, days


def days_to_schedule_map(days: list[tuple[str, list[str], str]]) -> dict[int, list[str]]:
    return {index: list(items) for index, (_label, items, _state) in enumerate(days)}


def production_schedule_items_from_rows(rows: list, week_start: date) -> dict[int, list[str]]:
    items_by_day = {index: [] for index in range(7)}
    for row in rows or []:
        due_date = row.due_date
        if not due_date:
            continue
        day_index = (due_date - week_start).days
        if 0 <= day_index <= 6:
            items_by_day[day_index].append(production_schedule_label(row))
    return {index: compact_schedule_items(items) for index, items in items_by_day.items()}


def build_core_tasks_summary_from_schedule(production_rows: list, limit: int = 8) -> dict:
    summary = get_dashboard_core_tasks_without_production(limit)
    add_dashboard_production_tasks_from_rows(summary, production_rows, limit)
    return summary


def get_dashboard_core_tasks_without_production(limit: int = 8) -> dict:
    summary = default_core_tasks_summary()
    current_week_start = summary["week_start"]
    if legacy_store_available is None or connect_sqlite_compatible is None or not legacy_store_available(SCHEDULE_DB_PATH):
        return summary
    try:
        with connect_sqlite_compatible(SCHEDULE_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            week_row = conn.execute(
                "SELECT id, week_start FROM schedule_weeks WHERE week_start = ?",
                (current_week_start.date().isoformat(),),
            ).fetchone()
            if week_row is None:
                week_row = conn.execute(
                    """
                    SELECT id, week_start
                    FROM schedule_weeks
                    WHERE EXISTS (
                        SELECT 1 FROM schedule_highlights
                        WHERE schedule_highlights.week_id = schedule_weeks.id
                    )
                    ORDER BY week_start DESC
                    LIMIT 1
                    """
                ).fetchone()
                summary["source"] = "latest"
            if week_row is None:
                return summary
            rows = conn.execute(
                """
                SELECT title, checked
                FROM schedule_highlights
                WHERE week_id = ?
                ORDER BY checked ASC, sort_order, id
                LIMIT ?
                """,
                (week_row["id"], limit),
            ).fetchall()
            week_start = pd.Timestamp(week_row["week_start"])
            summary.update(
                {
                    "week_start": week_start,
                    "week_end": week_start + pd.Timedelta(days=6),
                    "rows": [
                        {"title": str(row["title"] or "").strip(), "checked": bool(row["checked"])}
                        for row in rows
                        if str(row["title"] or "").strip()
                    ],
                }
            )
    except sqlite3.Error:
        return summary
    return summary


def add_dashboard_production_tasks_from_rows(summary: dict, production_rows: list, limit: int) -> None:
    rows = summary.setdefault("rows", [])
    remaining = max(limit - len(rows), 0)
    if remaining <= 0:
        return
    for row in (production_rows or [])[:remaining]:
        rows.append(
            {
                "title": production_core_task_label(row),
                "checked": str(row.status or "").strip() == "완료",
            }
        )


def with_db(action, label: str = "db_action"):
    if SessionLocal is None:
        return None
    started_at = time.perf_counter()
    log_dashboard_event(f"{label} session_open start")
    db = SessionLocal()
    try:
        result = action(db)
        elapsed = time.perf_counter() - started_at
        log_dashboard_event(f"{label} session_done seconds={elapsed:.3f} {dashboard_result_size(result)}")
        return result
    except Exception as exc:
        elapsed = time.perf_counter() - started_at
        log_dashboard_event(f"{label} session_error seconds={elapsed:.3f} {type(exc).__name__}: {exc}")
        raise
    finally:
        db.close()


def latest_inventory_work_date(db) -> date | None:
    if InventoryDaily is None:
        return None
    return db.scalar(select(func.max(InventoryDaily.work_date)))


def dashboard_inventory_summary(db, work_date: date, source_type: str | None = None) -> dict:
    if InventoryDaily is None:
        return {}
    filters = [InventoryDaily.work_date == work_date]
    if source_type and source_type != "전체":
        filters.append(InventoryDaily.source_type == source_type)
    row = db.execute(
        select(
            func.count(InventoryDaily.id),
            func.coalesce(func.sum(InventoryDaily.current_stock), 0),
            func.coalesce(func.sum(InventoryDaily.available_stock), 0),
            func.coalesce(func.sum(InventoryDaily.outbound_qty), 0),
            func.coalesce(func.sum(InventoryDaily.inbound_qty), 0),
            func.coalesce(func.sum(case((InventoryDaily.available_stock <= InventoryDaily.safe_stock, 1), else_=0)), 0),
            func.coalesce(func.sum(case((InventoryDaily.current_stock <= 0, 1), else_=0)), 0),
            func.coalesce(func.sum(case((InventoryDaily.available_stock < InventoryDaily.safe_stock, 1), else_=0)), 0),
        ).where(*filters)
    ).one()
    return {
        "sku_count": int(row[0] or 0),
        "current_stock": int(row[1] or 0),
        "available_stock": int(row[2] or 0),
        "outbound_qty": int(row[3] or 0),
        "inbound_qty": int(row[4] or 0),
        "need_inbound_count": int(row[5] or 0),
        "soldout_count": int(row[6] or 0),
        "short_count": int(row[7] or 0),
    }


@st.cache_data(ttl=60, show_spinner=False)
def get_home_inventory_summary() -> dict:
    default_summary = {
        "sku_count": 0,
        "current_stock": 0,
        "available_stock": 0,
        "need_inbound_count": 0,
        "soldout_count": 0,
        "short_count": 0,
        "outbound_qty": 0,
        "inbound_qty": 0,
        "return_as_count": 0,
        "work_date": None,
        "charts": {},
        "source_status": [],
        "weekly_3pl_inbound": [],
        "recent_inbound": [],
    }
    if not dashboard_available():
        return default_summary

    work_date = with_db(latest_inventory_work_date, "inventory_latest_work_date") or date.today()
    payload = with_db(lambda db: build_home_inventory_payload(db, work_date), "inventory_payload") or {}
    summary = payload.get("summary", {})
    return {
        **default_summary,
        **summary,
        "return_as_count": count_return_as_cases_for_month(work_date),
        "work_date": work_date,
        "charts": payload.get("charts", {}),
        "source_status": payload.get("source_status", []),
        "weekly_3pl_inbound": payload.get("weekly_3pl_inbound", []),
        "recent_inbound": payload.get("recent_inbound", []),
    }


def build_home_inventory_payload(db, work_date: date) -> dict:
    return {
        "summary": services.dashboard_summary(db, work_date, "전체"),
        "charts": services.dashboard_chart(db, work_date, "전체"),
        "source_status": get_source_status_rows(db, work_date),
        "weekly_3pl_inbound": get_weekly_3pl_inbound_rows(db, work_date),
        "recent_inbound": get_recent_inbound_rows(db),
    }


@st.cache_data(ttl=60, show_spinner=False)
def get_home_purchase_summary(work_date: date) -> dict:
    trend_days = dashboard_purchase_trend_days()
    default = {
        "pending_pr_count": 0,
        "pending_pr_amount": 0,
        "po_progress_count": 0,
        "uninbound_amount": 0,
        "delayed_count": 0,
        "max_delay_days": 0,
        "month_amount": 0,
        "month_change_rate": 0,
        "trend_days": trend_days,
        "trend_rows": [],
        "progress_rows": [],
        "priority_rows": [],
        "recent_po_inbound": [],
    }
    if not dashboard_available() or PurchaseRequest is None or PurchaseOrder is None or RfqQuote is None:
        return default
    payload = with_db(lambda db: build_home_purchase_payload(db, work_date, trend_days), "purchase_payload") or {}
    return {**default, **payload}


def dashboard_purchase_trend_days() -> int:
    value = st.query_params.get("purchase_trend")
    if isinstance(value, list):
        value = value[0] if value else ""
    try:
        days = int(value or 7)
    except (TypeError, ValueError):
        days = 7
    return 30 if days == 30 else 7


def build_home_purchase_payload(db, work_date: date, trend_days: int) -> dict:
    pr_rows = list(db.execute(select(PurchaseRequest)).scalars())
    quote_rows = list(db.execute(select(RfqQuote)).scalars())
    po_rows = list(db.execute(select(PurchaseOrder)).scalars())

    pending_prs = [row for row in pr_rows if row.approval_status in {"작성", "상신"}]
    pending_amount = sum(estimated_pr_amount(row, quote_rows) for row in pending_prs)

    progress_pos = [row for row in po_rows if row.inbound_status != "입고완료" and row.progress_status != "취소"]
    uninbound_amount = sum(int(row.order_amount or 0) for row in progress_pos)
    delayed_pos = [
        row
        for row in progress_pos
        if row.expected_inbound_date and row.expected_inbound_date < work_date
    ]
    max_delay_days = max([(work_date - row.expected_inbound_date).days for row in delayed_pos], default=0)

    month_start = work_date.replace(day=1)
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)
    month_amount = sum(int(row.order_amount or 0) for row in po_rows if row.order_date and month_start <= row.order_date <= work_date)
    prev_month_amount = sum(int(row.order_amount or 0) for row in po_rows if row.order_date and prev_month_start <= row.order_date <= prev_month_end)
    change_rate = ((month_amount - prev_month_amount) / prev_month_amount * 100) if prev_month_amount else (100.0 if month_amount else 0.0)

    rfq_prs = {row.pr_number for row in quote_rows}
    ordered_pos = [row for row in po_rows if row.progress_status == "발주완료"]
    inbound_waiting = [row for row in progress_pos if row.inbound_status in {"입고대기", "부분입고"}]
    progress_rows = [
        {"label": "구매요청 대기", "value": len(pending_prs), "caption": f"{pending_amount:,}원", "tone": "orange", "href": purchase_link("구매요청(PR)", "pr_pending")},
        {"label": "견적 진행", "value": len(rfq_prs), "caption": "RFQ 등록", "tone": "cyan", "href": purchase_link("견적관리(RFQ)", "rfq_progress")},
        {"label": "발주 완료", "value": len(ordered_pos), "caption": f"{sum(int(row.order_amount or 0) for row in ordered_pos):,}원", "tone": "blue", "href": purchase_link("발주관리(PO)", "po_progress")},
        {"label": "입고 대기", "value": len(inbound_waiting), "caption": f"{sum(int(row.order_amount or 0) for row in inbound_waiting):,}원", "tone": "green", "href": purchase_link("발주관리(PO)", "inbound_waiting")},
        {"label": "납기 지연", "value": len(delayed_pos), "caption": f"최대 {max_delay_days}일", "tone": "red" if delayed_pos else "cyan", "href": purchase_link("발주관리(PO)", "po_delay")},
    ]

    return {
        "pending_pr_count": len(pending_prs),
        "pending_pr_amount": pending_amount,
        "po_progress_count": len(progress_pos),
        "uninbound_amount": uninbound_amount,
        "delayed_count": len(delayed_pos),
        "max_delay_days": max_delay_days,
        "month_amount": month_amount,
        "month_change_rate": change_rate,
        "trend_days": trend_days,
        "trend_rows": purchase_trend_rows(po_rows, work_date, trend_days),
        "progress_rows": progress_rows,
        "priority_rows": purchase_priority_rows(db, work_date, pr_rows, po_rows),
        "recent_po_inbound": recent_po_inbound_rows(po_rows),
    }


def purchase_priority_rows(db, work_date: date, pr_rows: list, po_rows: list, limit: int = 5) -> list[dict]:
    if InventoryDaily is None:
        return []
    latest_date = db.scalar(select(InventoryDaily.work_date).order_by(InventoryDaily.work_date.desc()))
    if latest_date is None:
        return []
    rows = list(db.execute(select(InventoryDaily).where(InventoryDaily.work_date == latest_date)).scalars())
    open_pr_items = {row.item_name for row in pr_rows if row.linked_po_number == "" and row.approval_status in {"작성", "상신", "승인"}}
    open_po_items = {row.item_name for row in po_rows if row.inbound_status != "입고완료" and row.progress_status != "취소"}
    candidates = []
    for row in rows:
        current_stock = int(row.available_stock if row.available_stock is not None else row.current_stock or 0)
        safe_stock = int(row.safe_stock or 0)
        shortage = max(safe_stock - current_stock, 0)
        if shortage <= 0 and row.stock_status not in {"입고필요", "품절", "미출"}:
            continue
        lead_time = int(row.inbound_cycle or 0)
        priority_score = shortage + lead_time * 2 + (40 if row.stock_status == "품절" else 0) + (20 if row.stock_status == "입고필요" else 0)
        candidates.append(
            {
                "item_name": row.product_name,
                "source_type": row.source_type,
                "current_stock": current_stock,
                "safe_stock": safe_stock,
                "shortage": shortage,
                "lead_time": lead_time,
                "supplier": row.supplier or "-",
                "status": row.stock_status or ("입고필요" if shortage > 0 else "확인"),
                "action": priority_action(row.product_name, open_pr_items, open_po_items),
                "score": priority_score,
            }
        )
    candidates.sort(key=lambda item: (item["score"], item["shortage"], item["lead_time"]), reverse=True)
    return candidates[:limit]


def dashboard_outbound_period() -> str:
    value = st.query_params.get("outbound_top_period")
    if isinstance(value, list):
        value = value[0] if value else ""
    return value if value in {"7", "30", "month"} else "7"


def outbound_period_range(work_date: date, period: str) -> tuple[date, date, str]:
    if period == "30":
        return work_date - timedelta(days=29), work_date, "최근 30일"
    if period == "month":
        return work_date.replace(day=1), work_date, "이번 달"
    return work_date - timedelta(days=6), work_date, "최근 7일"


def get_home_outbound_top_summary(work_date: date) -> dict:
    period = dashboard_outbound_period()
    start_date, end_date, label = outbound_period_range(work_date, period)
    default = {
        "period": period,
        "label": label,
        "start_date": start_date,
        "end_date": end_date,
        "total_qty": 0,
        "total_amount": 0,
        "sku_count": 0,
        "rows": [],
    }
    if not dashboard_available() or InventoryDaily is None:
        return default
    payload = with_db(lambda db: build_outbound_top_summary(db, start_date, end_date)) or {}
    return {**default, **payload}


def build_outbound_top_summary(db, start_date: date, end_date: date) -> dict:
    rows = list(
        db.execute(
            select(InventoryDaily).where(
                InventoryDaily.work_date >= start_date,
                InventoryDaily.work_date <= end_date,
                InventoryDaily.outbound_qty > 0,
            )
        ).scalars()
    )
    if not rows:
        return {"total_qty": 0, "total_amount": 0, "sku_count": 0, "rows": []}

    unit_prices = outbound_unit_price_lookup(db)
    grouped: dict[tuple[str, str], dict] = {}
    for row in rows:
        item_name = row.product_name or "-"
        item_code = row.product_code or row.barcode or "-"
        key = (item_code, item_name)
        qty = int(row.outbound_qty or 0)
        unit_price = unit_prices.get(item_name, 0)
        item = grouped.setdefault(
            key,
            {
                "item_code": item_code,
                "item_name": item_name,
                "outbound_qty": 0,
                "outbound_amount": 0,
            },
        )
        item["outbound_qty"] += qty
        item["outbound_amount"] += qty * unit_price

    total_qty = sum(int(item["outbound_qty"] or 0) for item in grouped.values())
    total_amount = sum(int(item["outbound_amount"] or 0) for item in grouped.values())
    ranked = sorted(grouped.values(), key=lambda item: (item["outbound_qty"], item["outbound_amount"], item["item_name"]), reverse=True)
    for index, item in enumerate(ranked[:3], start=1):
        item["rank"] = index
        item["share"] = (int(item["outbound_qty"] or 0) / total_qty * 100) if total_qty else 0
    return {
        "total_qty": total_qty,
        "total_amount": total_amount,
        "sku_count": len(grouped),
        "rows": ranked[:3],
    }


def outbound_unit_price_lookup(db) -> dict[str, int]:
    prices: dict[str, int] = {}
    if CategoryBomItem is not None:
        bom_rows = db.execute(select(CategoryBomItem).order_by(CategoryBomItem.updated_at, CategoryBomItem.id)).scalars()
        for row in bom_rows:
            price = parse_money_value(row.memo)
            if row.item_name and price > 0:
                prices[row.item_name] = price
    if PurchaseOrder is not None:
        po_rows = db.execute(select(PurchaseOrder).where(PurchaseOrder.unit_price > 0).order_by(PurchaseOrder.order_date, PurchaseOrder.id)).scalars()
        for row in po_rows:
            if row.item_name:
                prices[row.item_name] = int(row.unit_price or 0)
    return prices


def parse_money_value(value) -> int:
    text = str(value or "").replace(",", "").strip()
    digits = "".join(ch for ch in text if ch.isdigit() or ch == ".")
    if not digits:
        return 0
    try:
        return int(float(digits))
    except ValueError:
        return 0


def priority_action(item_name: str, open_pr_items: set[str], open_po_items: set[str]) -> str:
    if item_name in open_po_items:
        return "입고추적"
    if item_name in open_pr_items:
        return "PR진행"
    return "PR필요"


def estimated_pr_amount(pr, quote_rows: list) -> int:
    quotes = [row for row in quote_rows if row.pr_number == pr.pr_number]
    if not quotes:
        return 0
    quote = min(quotes, key=lambda row: quote_total_amount(row, int(pr.quantity or 0)))
    return quote_total_amount(quote, int(pr.quantity or 0))


def quote_total_amount(quote, quantity: int) -> int:
    order_qty = max(quantity, int(quote.moq or 0), 1)
    return order_qty * int(quote.unit_price or 0) + int(quote.shipping_fee or 0)


def purchase_trend_rows(po_rows: list, work_date: date, days: int) -> list[dict]:
    start = work_date - timedelta(days=days - 1)
    rows = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        day_orders = [row for row in po_rows if row.order_date == day]
        day_inbounds = [row for row in po_rows if row.actual_inbound_date == day or (row.inbound_status == "입고완료" and row.expected_inbound_date == day and not row.actual_inbound_date)]
        ordered_amount = sum(int(row.order_amount or 0) for row in day_orders)
        inbound_amount = sum(int(row.order_amount or 0) for row in day_inbounds)
        open_balance = sum(int(row.order_amount or 0) for row in po_rows if row.order_date and row.order_date <= day and row.inbound_status != "입고완료")
        rows.append(
            {
                "date": day,
                "label": f"{day.month}/{day.day}",
                "ordered_amount": ordered_amount,
                "inbound_amount": inbound_amount,
                "open_balance": open_balance,
                "value": ordered_amount + inbound_amount,
            }
        )
    previous_start = start - timedelta(days=days)
    previous_end = start - timedelta(days=1)
    current_total = sum(row["ordered_amount"] for row in rows)
    previous_total = sum(
        int(row.order_amount or 0)
        for row in po_rows
        if row.order_date and previous_start <= row.order_date <= previous_end
    )
    change_rate = ((current_total - previous_total) / previous_total * 100) if previous_total else (100.0 if current_total else 0.0)
    for row in rows:
        row["change_rate"] = change_rate
    return rows


def recent_po_inbound_rows(po_rows: list, limit: int = 5) -> list[dict]:
    rows = sorted(po_rows, key=lambda row: (row.updated_at or row.order_date or date.min, row.id or 0), reverse=True)[:limit]
    return [
        {
            "po_number": row.po_number,
            "supplier_name": row.supplier_name,
            "item_name": row.item_name,
            "order_qty": int(row.quantity or 0),
            "inbound_qty": int(row.quantity or 0) if row.inbound_status == "입고완료" else 0,
            "expected_inbound_date": row.expected_inbound_date,
            "actual_inbound_date": row.actual_inbound_date,
            "status": po_dashboard_status(row),
            "tone": po_dashboard_tone(row),
        }
        for row in rows
    ]


def po_dashboard_status(row) -> str:
    if row.inbound_status == "입고완료":
        return "입고완료"
    if row.expected_inbound_date and row.expected_inbound_date < date.today():
        return "납기지연"
    if row.inbound_status == "부분입고":
        return "부분입고"
    if row.inbound_status == "입고대기":
        return "입고대기"
    return row.progress_status or "발주완료"


def po_dashboard_tone(row) -> str:
    status = po_dashboard_status(row)
    return {
        "입고완료": "done",
        "납기지연": "delay",
        "부분입고": "partial",
        "입고대기": "pending",
        "발주완료": "ordered",
    }.get(status, "ordered")


def get_source_status_rows(db, work_date: date) -> list[dict]:
    rows = []
    for source_type in SOURCE_TYPES:
        summary = services.dashboard_summary(db, work_date, source_type)
        current_stock = int(summary.get("current_stock") or 0)
        available_stock = int(summary.get("available_stock") or 0)
        problem_count = int(summary.get("need_inbound_count") or 0) + int(summary.get("soldout_count") or 0) + int(summary.get("short_count") or 0)
        ratio = round((available_stock / current_stock) * 100) if current_stock > 0 else 0
        rows.append(
            {
                "name": source_type,
                "rate": max(0, min(ratio, 100)),
                "qty": current_stock,
                "problem_count": problem_count,
                "tone": source_status_tone(current_stock, ratio, problem_count),
            }
        )
    return rows


def source_status_tone(current_stock: int, ratio: int, problem_count: int) -> str:
    if current_stock <= 0 and problem_count == 0:
        return "cyan"
    if problem_count > 0 or ratio < 50:
        return "red"
    if ratio < 75:
        return "orange"
    if ratio >= 90:
        return "green"
    return "cyan"


def get_weekly_3pl_inbound_rows(db, work_date: date, limit: int = 5) -> list[dict]:
    if InventoryInbound is None or InventoryDaily is None:
        return []
    week_start = work_date - timedelta(days=work_date.weekday())
    week_end = week_start + timedelta(days=6)
    inbound_rows = list(
        db.execute(
            select(InventoryInbound)
            .where(
                InventoryInbound.source_type == "3PL",
                InventoryInbound.inbound_date >= week_start,
                InventoryInbound.inbound_date <= week_end,
            )
            .order_by(InventoryInbound.inbound_date.desc(), InventoryInbound.id.desc())
            .limit(limit)
        ).scalars()
    )

    stock_map = {
        (row.product_name, row.barcode or ""): row
        for row in db.execute(
            select(
                InventoryDaily.product_name,
                InventoryDaily.barcode,
                InventoryDaily.available_stock,
                InventoryDaily.current_stock,
                InventoryDaily.safe_stock,
                InventoryDaily.stock_status,
            ).where(InventoryDaily.source_type == "3PL", InventoryDaily.work_date == work_date)
        ).all()
    }
    result = []
    for inbound in inbound_rows[:limit]:
        stock = stock_map.get((inbound.product_name, inbound.barcode or ""))
        label, tone = inbound_stock_status(stock)
        result.append(
            {
                "product_name": inbound.product_name or "-",
                "quantity": int(inbound.inbound_qty or 0),
                "status": label,
                "tone": tone,
            }
        )
    return result


def inbound_stock_status(stock) -> tuple[str, str]:
    if stock is None:
        return "-", "normal"
    stock_value = int(stock.available_stock if stock.available_stock is not None else stock.current_stock or 0)
    safe_stock = int(stock.safe_stock or 0)
    if stock.stock_status in {"입고필요", "품절", "미출"} or (safe_stock > 0 and stock_value < safe_stock):
        return "부족", "short"
    if safe_stock > 0 and stock_value <= safe_stock * 1.2:
        return "주의", "caution"
    return "정상", "normal"


def get_recent_inbound_rows(db, limit: int = 5) -> list[dict]:
    if InventoryInbound is None:
        return []
    rows = list(
        db.execute(
            select(InventoryInbound)
            .order_by(InventoryInbound.inbound_date.desc(), InventoryInbound.id.desc())
            .limit(limit)
        ).scalars()
    )
    return [
        {
            "source_type": row.source_type,
            "inbound_date": row.inbound_date,
            "product_name": row.product_name or "-",
            "vendor": row.vendor or "-",
            "inbound_qty": int(row.inbound_qty or 0),
            "inbound_type": row.inbound_type or "-",
            "is_applied": bool(row.is_applied),
        }
        for row in rows[:limit]
    ]


@st.cache_data(ttl=60, show_spinner=False)
def get_return_case_summary(work_date: date) -> dict:
    current_year = work_date.year if hasattr(work_date, "year") else date.today().year
    summary = {
        "total_count": 0,
        "category_rows": [],
        "monthly_rows": [{"month": month, "month_key": f"{current_year}{month:02d}", "count": 0} for month in range(1, 13)],
        "recent_cases": [],
        "today_count": 0,
        "week_count": 0,
        "in_progress_count": 0,
        "done_count": 0,
        "delayed_count": 0,
        "year": current_year,
    }
    if legacy_store_available is None or connect_sqlite_compatible is None or not legacy_store_available(RETURN_CASE_DB_PATH):
        return summary
    try:
        with connect_sqlite_compatible(RETURN_CASE_DB_PATH) as conn:
            total_count = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
            category_rows = conn.execute(
                """
                SELECT category, COUNT(*) AS cnt
                FROM cases
                WHERE TRIM(COALESCE(category, '')) != ''
                GROUP BY category
                ORDER BY cnt DESC, category
                """
            ).fetchall()
            monthly_counts = dict(
                conn.execute(
                    """
                    SELECT substr(case_id, 5, 2) AS month, COUNT(*) AS cnt
                    FROM cases
                    WHERE substr(case_id, 1, 4) = ?
                    GROUP BY month
                    """,
                    (str(current_year),),
                ).fetchall()
            )
            recent_cases = conn.execute(
                """
                SELECT case_id, category, product
                FROM cases
                ORDER BY case_id DESC, id DESC
                LIMIT 5
                """
            ).fetchall()
            occurrence_recent_rows = conn.execute(
                """
                SELECT case_id, category, product, action, repair_method, prevention
                FROM cases
                ORDER BY case_id DESC, id DESC
                LIMIT 4
                """
            ).fetchall()
            today_key = work_date.strftime("%Y%m%d") if hasattr(work_date, "strftime") else date.today().strftime("%Y%m%d")
            week_start_key = (work_date - timedelta(days=work_date.weekday())).strftime("%Y%m%d") if hasattr(work_date, "weekday") else date.today().strftime("%Y%m%d")
            delayed_cutoff_key = (work_date - timedelta(days=7)).strftime("%Y%m%d") if hasattr(work_date, "strftime") else date.today().strftime("%Y%m%d")
            status_counts = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN substr(COALESCE(case_id, ''), 1, 8) = ? THEN 1 ELSE 0 END) AS today_count,
                    SUM(CASE WHEN substr(COALESCE(case_id, ''), 1, 8) BETWEEN ? AND ? THEN 1 ELSE 0 END) AS week_count,
                    SUM(CASE WHEN TRIM(COALESCE(action, '')) != ''
                               OR TRIM(COALESCE(repair_method, '')) != ''
                               OR TRIM(COALESCE(prevention, '')) != ''
                             THEN 1 ELSE 0 END) AS done_count,
                    SUM(CASE WHEN TRIM(COALESCE(action, '')) = ''
                               AND TRIM(COALESCE(repair_method, '')) = ''
                               AND TRIM(COALESCE(prevention, '')) = ''
                             THEN 1 ELSE 0 END) AS in_progress_count,
                    SUM(CASE WHEN TRIM(COALESCE(action, '')) = ''
                               AND TRIM(COALESCE(repair_method, '')) = ''
                               AND TRIM(COALESCE(prevention, '')) = ''
                               AND substr(COALESCE(case_id, ''), 1, 8) <= ?
                               AND length(substr(COALESCE(case_id, ''), 1, 8)) = 8
                             THEN 1 ELSE 0 END) AS delayed_count
                FROM cases
                """,
                (today_key, week_start_key, today_key, delayed_cutoff_key),
            ).fetchone()
    except sqlite3.Error:
        return summary

    occurrence_summary = summarize_return_occurrence_status(status_counts, occurrence_recent_rows)
    colors = ["#66849C", "#5F8F7B", "#A98755", "#8A94A3", "#A86464", "#7B8794"]
    return {
        "total_count": int(total_count or 0),
        "category_rows": [
            {"category": category or "-", "count": int(count or 0), "color": colors[index % len(colors)]}
            for index, (category, count) in enumerate(category_rows)
        ],
        "monthly_rows": [
            {"month": month, "month_key": f"{current_year}{month:02d}", "count": int(monthly_counts.get(f"{month:02d}", 0) or 0)}
            for month in range(1, 13)
        ],
        "recent_cases": [
            {
                "case_id": case_id or "-",
                "category": category or "-",
                "product": product or "-",
                "date": format_case_id_date(case_id),
            }
            for case_id, category, product in recent_cases
        ],
        **occurrence_summary,
        "year": current_year,
    }


def summarize_return_occurrence_status(status_counts, recent_rows: list) -> dict:
    return {
        "today_count": int(status_counts[0] or 0) if status_counts else 0,
        "week_count": int(status_counts[1] or 0) if status_counts else 0,
        "done_count": int(status_counts[2] or 0) if status_counts else 0,
        "in_progress_count": int(status_counts[3] or 0) if status_counts else 0,
        "delayed_count": int(status_counts[4] or 0) if status_counts else 0,
        "recent_cases": [
            {
                "case_id": case_id or "-",
                "category": category or "-",
                "product": product or "-",
                "date": format_case_id_date(case_id),
                "done": any(str(value or "").strip() for value in (action, repair_method, prevention)),
            }
            for case_id, category, product, action, repair_method, prevention in recent_rows
        ],
    }


def summarize_return_occurrence_rows(rows: list, today: date, week_start: date) -> dict:
    today_key = today.strftime("%Y%m%d")
    week_start_key = week_start.strftime("%Y%m%d")
    today_count = 0
    week_count = 0
    in_progress_count = 0
    done_count = 0
    delayed_count = 0
    recent_cases = []
    for case_id, category, product, action, repair_method, prevention in rows:
        case_text = str(case_id or "")
        case_key = case_text[:8] if len(case_text) >= 8 and case_text[:8].isdigit() else ""
        is_done = any(str(value or "").strip() for value in (action, repair_method, prevention))
        if case_key == today_key:
            today_count += 1
        if case_key and week_start_key <= case_key <= today_key:
            week_count += 1
        if is_done:
            done_count += 1
        else:
            in_progress_count += 1
            if case_key:
                try:
                    case_date = date(int(case_key[:4]), int(case_key[4:6]), int(case_key[6:8]))
                except ValueError:
                    case_date = today
                if (today - case_date).days >= 7:
                    delayed_count += 1
        if len(recent_cases) < 4:
            recent_cases.append(
                {
                    "case_id": case_id or "-",
                    "category": category or "-",
                    "product": product or "-",
                    "date": format_case_id_date(case_id),
                    "done": is_done,
                }
            )
    return {
        "today_count": today_count,
        "week_count": week_count,
        "in_progress_count": in_progress_count,
        "done_count": done_count,
        "delayed_count": delayed_count,
        "recent_cases": recent_cases,
    }


def format_case_id_date(case_id: str) -> str:
    text = str(case_id or "")
    if len(text) < 8 or not text[:8].isdigit():
        return "-"
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def return_case_filter_link(filter_key: str, month_key: str = "") -> str:
    params = {"page": "반품/AS 관리", "return_case_filter": filter_key}
    if month_key:
        params["return_case_month"] = month_key
    return "?" + urlencode(params)


def return_case_detail_link(case_id: str) -> str:
    return "?" + urlencode({"page": "반품/AS 관리", "return_case_id": str(case_id or "")})


def count_return_as_cases_for_month(work_date: date) -> int:
    if legacy_store_available is None or connect_sqlite_compatible is None or not legacy_store_available(RETURN_CASE_DB_PATH):
        return 0
    month_key = work_date.strftime("%Y%m")
    try:
        with connect_sqlite_compatible(RETURN_CASE_DB_PATH) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM cases
                WHERE case_id IS NOT NULL
                  AND substr(case_id, 1, 6) = ?
                """,
                (month_key,),
            ).fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0] or 0)


def render_inventory_dashboard() -> None:
    if not dashboard_available():
        st.warning(DASHBOARD_IMPORT_ERROR or "재고관리 DB를 불러오지 못했습니다.")
        return

    date_payload = with_db(lambda db: services.list_work_dates(db))
    date_values = [value.date() for value in pd.to_datetime(date_payload or [], errors="coerce") if not pd.isna(value)]
    default_date = date_values[0] if date_values else date.today()

    with st.container(key="inventory_dashboard_panel"):
        st.markdown("### 재고 대시보드")
        filter_col, source_col, spacer = st.columns([1, 1, 4], gap="small")
        with filter_col:
            work_date = st.date_input("기준일자", value=default_date, key="dashboard_inventory_date")
        with source_col:
            source_type = st.selectbox("source_type", ["전체", "3PL", "오프라인", "창고"], key="dashboard_inventory_source")
        with spacer:
            st.empty()

        payload = with_db(
            lambda db: {
                "summary": services.dashboard_summary(db, work_date, source_type),
                "charts": services.dashboard_chart(db, work_date, source_type),
            }
        )
        if not payload:
            st.info("집계할 재고 데이터가 없습니다.")
            return

        summary = payload.get("summary", {})
        charts = payload.get("charts", {})
        metric_cols = st.columns(7, gap="small")
        metrics = [
            ("전체 SKU 수", summary.get("sku_count", 0)),
            ("총 현재고", summary.get("current_stock", 0)),
            ("입고필요 SKU 수", summary.get("need_inbound_count", 0)),
            ("품절 SKU 수", summary.get("soldout_count", 0)),
            ("미출 SKU 수", summary.get("short_count", 0)),
            ("오늘 출고수량 합계", summary.get("outbound_qty", 0)),
            ("오늘 입고수량 합계", summary.get("inbound_qty", 0)),
        ]
        for column, (label, value) in zip(metric_cols, metrics):
            column.metric(label, f"{value:,}")

        chart_cols_1 = st.columns(3, gap="small")
        with chart_cols_1[0]:
            st.markdown("#### source_type별 현재고")
            render_bar_chart(charts.get("stock_by_source", []))
        with chart_cols_1[1]:
            st.markdown("#### 카테고리별 현재고")
            render_bar_chart(charts.get("stock_by_category", []))
        with chart_cols_1[2]:
            st.markdown("#### 카테고리별 출고수량")
            render_bar_chart(charts.get("outbound_by_category", []))

        chart_cols_2 = st.columns(3, gap="small")
        with chart_cols_2[0]:
            st.markdown("#### 날짜별 현재고 추이")
            render_line_chart(charts.get("stock_trend", []))
        with chart_cols_2[1]:
            st.markdown("#### 날짜별 출고수량 추이")
            render_line_chart(charts.get("outbound_trend", []))
        with chart_cols_2[2]:
            st.markdown("#### 입고필요 상품 TOP 10")
            top_df = pd.DataFrame(charts.get("need_inbound_top10", []))
            if top_df.empty:
                st.info("입고필요 상품이 없습니다.")
            else:
                st.dataframe(top_df, hide_index=True, use_container_width=True)


def render_bar_chart(rows: list[dict]) -> None:
    if not rows:
        st.info("표시할 데이터가 없습니다.")
        return
    df = pd.DataFrame(rows).set_index("label")
    st.bar_chart(df["value"])


def render_line_chart(rows: list[dict]) -> None:
    if not rows:
        st.info("표시할 데이터가 없습니다.")
        return
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    st.line_chart(df.set_index("date")["value"])


@st.cache_data(ttl=60, show_spinner=False)
def weekly_schedule_html() -> str:
    week_start, days = get_dashboard_week_schedule()
    week_end = week_start + pd.Timedelta(days=6)
    cells = "".join(
        f"""
        <div class="week-cell {state}">
            <div class="week-date">{date}</div>
            <ul>{''.join(f'<li>{escape(str(item))}</li>' for item in items)}</ul>
        </div>
        """
        for date, items, state in days
    )
    return f"""
    <section class="panel schedule-panel">
        <div class="panel-title-row">
            <h2>물류 주간 일정표</h2>
            <div class="week-range">
                <span>‹</span>
                <strong>{week_start:%Y.%m.%d} ~ {week_end:%Y.%m.%d}</strong>
                <span>›</span>
                <span>›</span>
            </div>
        </div>
        <div class="week-board">{cells}</div>
    </section>
    """


def get_dashboard_week_schedule() -> tuple[pd.Timestamp, list[tuple[str, list[str], str]]]:
    today = pd.Timestamp(date.today())
    current_week_start = today - pd.Timedelta(days=today.weekday())
    week_start = current_week_start
    schedule_by_day = {index: [] for index in range(7)}

    if legacy_store_available is not None and connect_sqlite_compatible is not None and legacy_store_available(SCHEDULE_DB_PATH):
        try:
            with connect_sqlite_compatible(SCHEDULE_DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                week_row = conn.execute(
                    "SELECT id, week_start FROM schedule_weeks WHERE week_start = ?",
                    (current_week_start.date().isoformat(),),
                ).fetchone()
                if week_row is None:
                    week_row = conn.execute(
                        "SELECT id, week_start FROM schedule_weeks ORDER BY week_start DESC LIMIT 1"
                    ).fetchone()
                if week_row is not None:
                    week_start = pd.Timestamp(week_row["week_start"])
                    rows = conn.execute(
                        """
                        SELECT time_label, mon, tue, wed, thu, fri
                        FROM schedule_slots
                        WHERE week_id = ?
                        ORDER BY sort_order, id
                        """,
                        (week_row["id"],),
                    ).fetchall()
                    schedule_by_day.update(summarize_schedule_slots(rows))
        except sqlite3.Error:
            schedule_by_day = {index: [] for index in range(7)}

    merge_week_schedule_items(schedule_by_day, get_week_production_schedule_items(week_start.date()))

    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    days = []
    for index, weekday in enumerate(weekdays):
        day = week_start + pd.Timedelta(days=index)
        state = "active" if day.date() == today.date() else ""
        if index == 5:
            state = f"{state} blue".strip()
        if index == 6:
            state = f"{state} red".strip()
        items = schedule_by_day.get(index) or []
        days.append((f"{day:%m.%d} ({weekday})", items, state))
    return week_start, days


@st.cache_data(ttl=60, show_spinner=False)
def get_dashboard_core_tasks(limit: int = 8) -> dict:
    today = pd.Timestamp(date.today())
    current_week_start = today - pd.Timedelta(days=today.weekday())
    summary = {
        "week_start": current_week_start,
        "week_end": current_week_start + pd.Timedelta(days=6),
        "rows": [],
        "source": "current",
    }
    if legacy_store_available is None or connect_sqlite_compatible is None or not legacy_store_available(SCHEDULE_DB_PATH):
        add_dashboard_production_tasks(summary, limit)
        return summary

    try:
        with connect_sqlite_compatible(SCHEDULE_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            week_row = conn.execute(
                "SELECT id, week_start FROM schedule_weeks WHERE week_start = ?",
                (current_week_start.date().isoformat(),),
            ).fetchone()
            if week_row is None:
                week_row = conn.execute(
                    """
                    SELECT id, week_start
                    FROM schedule_weeks
                    WHERE EXISTS (
                        SELECT 1 FROM schedule_highlights
                        WHERE schedule_highlights.week_id = schedule_weeks.id
                    )
                    ORDER BY week_start DESC
                    LIMIT 1
                    """
                ).fetchone()
                summary["source"] = "latest"
            if week_row is None:
                add_dashboard_production_tasks(summary, limit)
                return summary

            rows = conn.execute(
                """
                SELECT title, checked
                FROM schedule_highlights
                WHERE week_id = ?
                ORDER BY checked ASC, sort_order, id
                LIMIT ?
                """,
                (week_row["id"], limit),
            ).fetchall()
            if not rows and summary["source"] == "current":
                fallback_week = conn.execute(
                    """
                    SELECT id, week_start
                    FROM schedule_weeks
                    WHERE week_start <> ?
                      AND EXISTS (
                          SELECT 1 FROM schedule_highlights
                          WHERE schedule_highlights.week_id = schedule_weeks.id
                      )
                    ORDER BY week_start DESC
                    LIMIT 1
                    """,
                    (current_week_start.date().isoformat(),),
                ).fetchone()
                if fallback_week is not None:
                    week_row = fallback_week
                    summary["source"] = "latest"
                    rows = conn.execute(
                        """
                        SELECT title, checked
                        FROM schedule_highlights
                        WHERE week_id = ?
                        ORDER BY checked ASC, sort_order, id
                        LIMIT ?
                        """,
                        (week_row["id"], limit),
                    ).fetchall()

            week_start = pd.Timestamp(week_row["week_start"])
            summary.update(
                {
                    "week_start": week_start,
                    "week_end": week_start + pd.Timedelta(days=6),
                    "rows": [
                        {"title": str(row["title"] or "").strip(), "checked": bool(row["checked"])}
                        for row in rows
                        if str(row["title"] or "").strip()
                    ],
                }
            )
    except sqlite3.Error:
        add_dashboard_production_tasks(summary, limit)
        return summary
    add_dashboard_production_tasks(summary, limit)
    return summary


def summarize_schedule_slots(rows) -> dict[int, list[str]]:
    columns = ["mon", "tue", "wed", "thu", "fri"]
    schedule_by_day = {index: [] for index in range(7)}
    for row in rows:
        for index, column in enumerate(columns):
            schedule_by_day[index].extend(extract_schedule_items(row[column]))
    for index, items in schedule_by_day.items():
        schedule_by_day[index] = compact_schedule_items(items)
    return schedule_by_day


def extract_schedule_items(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    items = []
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        items.append(cleaned)
    return items


def compact_schedule_items(items: list[str], limit: int = 3) -> list[str]:
    cleaned = []
    seen = set()
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    if len(cleaned) > limit:
        return [*cleaned[:limit], f"+{len(cleaned) - limit}건"]
    return cleaned


def merge_week_schedule_items(base: dict[int, list[str]], extra: dict[int, list[str]]) -> None:
    for index, items in extra.items():
        base[index] = compact_schedule_items([*(base.get(index) or []), *items])


def get_week_production_schedule_items(week_start: date) -> dict[int, list[str]]:
    items_by_day = {index: [] for index in range(7)}
    if not dashboard_available() or ProductionPlan is None:
        return items_by_day
    week_end = week_start + timedelta(days=6)
    rows = with_db(lambda db: list_week_production_plans(db, week_start, week_end)) or []
    for row in rows:
        due_date = row.due_date
        if not due_date:
            continue
        day_index = (due_date - week_start).days
        if 0 <= day_index <= 6:
            items_by_day[day_index].append(production_schedule_label(row))
    return {index: compact_schedule_items(items) for index, items in items_by_day.items()}


def list_week_production_plans(db, week_start: date, week_end: date) -> list:
    if ProductionPlan is None:
        return []
    return list(
        db.execute(
            select(ProductionPlan)
            .where(
                ProductionPlan.due_date >= week_start,
                ProductionPlan.due_date <= week_end,
                ProductionPlan.status != "취소",
                ProductionPlan.plan_qty > 0,
            )
            .order_by(ProductionPlan.due_date, ProductionPlan.id)
        ).scalars()
    )


def production_schedule_label(row) -> str:
    qty = format_metric(row.plan_qty or 0)
    status = str(row.status or "").strip()
    status_text = f" / {status}" if status else ""
    return f"생산 {row.product_name} {qty}EA{status_text}"


def add_dashboard_production_tasks(summary: dict, limit: int) -> None:
    if not dashboard_available() or ProductionPlan is None:
        return
    rows = summary.setdefault("rows", [])
    remaining = max(limit - len(rows), 0)
    if remaining <= 0:
        return
    week_start = summary.get("week_start")
    week_end = summary.get("week_end")
    if not hasattr(week_start, "date") or not hasattr(week_end, "date"):
        return
    production_rows = with_db(lambda db: list_week_production_plans(db, week_start.date(), week_end.date())) or []
    for row in production_rows[:remaining]:
        rows.append(
            {
                "title": production_core_task_label(row),
                "checked": str(row.status or "").strip() == "완료",
            }
        )


def production_core_task_label(row) -> str:
    due = row.due_date.strftime("%m.%d") if hasattr(row.due_date, "strftime") else "-"
    qty = format_metric(row.plan_qty or 0)
    status = str(row.status or "").strip() or "계획"
    return f"생산 {row.product_name} {qty}EA / {due} / {status}"


def format_metric(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def format_won(value) -> str:
    try:
        amount = int(value or 0)
    except (TypeError, ValueError):
        amount = 0
    if abs(amount) >= 100_000_000:
        return f"{amount / 100_000_000:.1f}억"
    if abs(amount) >= 10_000:
        return f"{amount / 10_000:.0f}만"
    return f"{amount:,}원"


def format_percent(value) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.1f}%"


def purchase_link(tab: str, filter_key: str = "") -> str:
    return "?" + urlencode({"page": "구매관리"})


def purchase_po_link(po_number: str) -> str:
    return "?" + urlencode({"page": "구매관리"})


def kpi_cards_html(summary: dict, purchase_summary: dict) -> str:
    stock = format_metric(summary.get("current_stock", 0))
    outbound_qty = format_metric(summary.get("outbound_qty", 0))
    inbound_qty = format_metric(summary.get("inbound_qty", 0))
    pending_pr = format_metric(purchase_summary.get("pending_pr_count", 0))
    pending_amount = format_won(purchase_summary.get("pending_pr_amount", 0))
    po_progress = format_metric(purchase_summary.get("po_progress_count", 0))
    uninbound_amount = format_won(purchase_summary.get("uninbound_amount", 0))
    month_amount = format_won(purchase_summary.get("month_amount", 0))
    month_change = format_percent(purchase_summary.get("month_change_rate", 0))
    work_date = summary.get("work_date")
    caption_date = work_date.strftime("%Y.%m.%d 기준") if hasattr(work_date, "strftime") else "최신 기준일자"
    inventory_date = work_date.isoformat() if hasattr(work_date, "isoformat") else ""

    def inventory_link(filter_key: str) -> str:
        params = {"page": "재고관리", "inventory_filter": filter_key}
        if inventory_date:
            params["inventory_date"] = inventory_date
        return "?" + urlencode(params)

    cards = [
        ("cube", "총 현재고", f"{stock}개", caption_date, "cyan", inventory_link("all")),
        ("truck", "출고수량", f"{outbound_qty}개", caption_date, "blue", inventory_link("outbound")),
        ("case", "구매요청", f"{pending_pr}건", f"대기 {pending_amount}", "purple", purchase_link("구매요청(PR)", "pr_pending")),
        ("truck", "발주 진행", f"{po_progress}건", f"미입고 {uninbound_amount}", "blue", purchase_link("발주관리(PO)", "po_progress")),
        ("box", "입고수량", f"{inbound_qty}개", caption_date, "green", inventory_link("all")),
        ("box", "이번 달 구매금액", month_amount, f"전월 대비 {month_change}", "green", purchase_link("구매 KPI")),
    ]
    return '<section class="kpi-row">' + "".join(
        f"""
        <a class="kpi-tile {tone}" href="{href}" target="_self" title="{label} 보기">
            <div class="kpi-icon">{icon_svg(icon)}</div>
            <div>
                <span>{label}</span>
                <strong>{value}</strong>
                <small>{caption}</small>
            </div>
        </a>
        """
        for icon, label, value, caption, tone, href in cards
    ) + "</section>"


def purchase_inbound_chart_html(rows: list[dict], trend_days: int, outbound_qty: int = 0) -> str:
    chart_rows = rows[-trend_days:] if rows else []
    labels = [str(row.get("label", "-")) for row in chart_rows]
    ordered_values = [int(row.get("ordered_amount") or 0) for row in chart_rows]
    inbound_values = [int(row.get("inbound_amount") or 0) for row in chart_rows]
    balance_values = [int(row.get("open_balance") or 0) for row in chart_rows]
    max_value = max([*ordered_values, *inbound_values, *balance_values], default=0)
    grid_values = chart_grid_values(max_value)
    ordered_points = trend_points_scaled(ordered_values, max_value)
    inbound_points = trend_points_scaled(inbound_values, max_value)
    balance_points = trend_points_scaled(balance_values, max_value)
    last = chart_rows[-1] if chart_rows else {}
    total_ordered = sum(ordered_values)
    total_inbound = sum(inbound_values)
    last_balance = int(last.get("open_balance") or 0)
    change_rate = float(last.get("change_rate") or 0)
    empty = '<div class="empty-cell">구매관리 발주 데이터가 없습니다.</div>' if not chart_rows or max_value == 0 else ""
    filter_7 = "?" + urlencode({"page": "대시보드", "purchase_trend": "7"})
    filter_30 = "?" + urlencode({"page": "대시보드", "purchase_trend": "30"})
    return f"""
    <article class="panel chart-card purchase-chart-card">
        <div class="panel-title-row compact">
            <h2>구매·입고 추이</h2>
            <div class="chart-filter-links">
                <a class="{active_filter_class(trend_days, 7)}" href="{filter_7}" target="_self">최근 7일</a>
                <a class="{active_filter_class(trend_days, 30)}" href="{filter_30}" target="_self">최근 30일</a>
            </div>
        </div>
        <div class="purchase-trend-summary">
            <span>발주 {format_won(total_ordered)}</span>
            <span>입고 {format_won(total_inbound)}</span>
            <span>미입고 {format_won(last_balance)}</span>
            <span>전기간 {format_percent(change_rate)}</span>
            <span>출고 {format_metric(outbound_qty)}개</span>
        </div>
        <div class="svg-chart">
            {grid_lines(grid_values)}
            <svg viewBox="0 0 620 230" preserveAspectRatio="none">
                <polyline points="{balance_points}" fill="none" stroke="#ffb22e" stroke-width="2.4"/>
                <polyline points="{ordered_points}" fill="none" stroke="#4F6F8F" stroke-width="3"/>
                <polyline points="{inbound_points}" fill="none" stroke="#6F927D" stroke-width="3"/>
                {chart_points(ordered_points, "#4F6F8F")}
                {chart_points(inbound_points, "#6F927D")}
            </svg>
            <div class="chart-tooltip blue-tip"><b>{escape(str(last.get("label", "-")))}</b><span>발주 {format_won(last.get("ordered_amount", 0))}</span></div>
            {axis_labels(compact_axis_labels(labels))}
            {empty}
        </div>
    </article>
    """


def active_filter_class(current: int, target: int) -> str:
    return "active" if int(current or 0) == target else ""


def compact_axis_labels(labels: list[str]) -> list[str]:
    if len(labels) <= 7:
        return labels or ["-"]
    indexes = {0, len(labels) - 1, len(labels) // 2}
    step = max(1, len(labels) // 5)
    indexes.update(range(0, len(labels), step))
    return [label if index in indexes else "" for index, label in enumerate(labels)]


def trend_chart_html(rows: list[dict], title: str, color: str, fill_id: str, tooltip_class: str, metric_label: str, summary_html: str = "") -> str:
    chart_rows = normalize_trend_rows(rows)[-7:]
    values = [row["value"] for row in chart_rows]
    labels = [row["label"] for row in chart_rows]
    max_value = max(values) if values else 0
    grid_values = chart_grid_values(max_value)
    point_string = trend_points(values)
    polygon_points = f"{point_string} 592,190 42,190" if point_string else "42,190 592,190"
    last_label = labels[-1] if labels else "-"
    last_value = values[-1] if values else 0
    empty = '<div class="empty-cell">재고관리 탭에 반영된 데이터가 없습니다.</div>' if not values else ""
    return f"""
    <article class="panel chart-card">
        <div class="panel-title-row compact">
            <h2>{title}</h2>
            <button>최근 7일⌄</button>
        </div>
        {summary_html}
        <div class="svg-chart">
            {grid_lines(grid_values)}
            <svg viewBox="0 0 620 230" preserveAspectRatio="none">
                <defs>
                    <linearGradient id="{fill_id}" x1="0" x2="0" y1="0" y2="1">
                        <stop offset="0%" stop-color="{color}" stop-opacity="0.34"/>
                        <stop offset="100%" stop-color="{color}" stop-opacity="0.02"/>
                    </linearGradient>
                </defs>
                <polygon points="{polygon_points}" fill="url(#{fill_id})"/>
                <polyline points="{point_string}" fill="none" stroke="{color}" stroke-width="3"/>
                {chart_points(point_string, color)}
            </svg>
            <div class="chart-tooltip {tooltip_class}"><b>{last_label}</b><span>{metric_label} : {last_value:,}</span></div>
            {axis_labels(labels or ["-"])}
            {empty}
        </div>
    </article>
    """


def issue_donut_html(rows: list[dict], total_count: int, monthly_rows: list[dict] | None = None, year: int | None = None) -> str:
    if rows and total_count:
        stops = donut_gradient_stops(rows, total_count)
        labels = donut_segment_labels(rows, total_count)
        legend = "".join(
            f"""
            <li title="{escape(str(row.get("category", "-")))} / {int(row.get("count") or 0):,}건 / {category_percent(row.get("count", 0), total_count)}">
                <i style="background:{escape(str(row.get("color", "#94a3b8")))}"></i>
                <a href="{return_case_filter_link(str(row.get("category", "")))}" target="_self">{escape(str(row.get("category", "-")))}</a>
                <b>{category_percent(row.get("count", 0), total_count)} ({int(row.get("count") or 0):,}건)</b>
            </li>
            """
            for row in rows
        )
    else:
        stops = "#214d47 0 100%"
        labels = ""
        legend = '<li class="empty-cell">등록된 반품/AS 사례가 없습니다.</li>'
    return f"""
    <article class="panel donut-card">
        <h2>유형별 발생 현황 <small>(반품/AS)</small></h2>
        <div class="donut-layout">
            <a class="donut" href="{return_case_filter_link("ALL")}" target="_self" style="background:conic-gradient({stops});" title="반품/AS 전체 보기">
                {labels}
                <div><strong>{int(total_count or 0):,}건</strong><span>전체</span></div>
            </a>
            <ul class="legend">{legend}</ul>
        </div>
        {issue_monthly_strip_html(monthly_rows or [], year or date.today().year)}
    </article>
    """


def issue_monthly_strip_html(rows: list[dict], year: int) -> str:
    if not rows:
        rows = [{"month": month, "month_key": f"{year}{month:02d}", "count": 0} for month in range(1, 13)]
    values = [int(row.get("count") or 0) for row in rows]
    max_value = max(max(values), 1) if values else 1
    month_nodes = []
    for row in rows:
        month = int(row.get("month") or 0)
        count = int(row.get("count") or 0)
        href = return_case_filter_link("MONTH", str(row.get("month_key", "")))
        height = max(8, count / max_value * 100)
        month_nodes.append(
            f"""
            <a href="{href}" target="_self" title="{month}M {count:,} cases">
                <i style="--bar-h:{height:.1f}%"></i>
                <span>{month}</span>
            </a>
            """
        )
    return f"""
    <div class="issue-monthly-mini">
        <div class="issue-monthly-head">
            <span>월별 발생 추이</span>
            <b>{year}년</b>
        </div>
        <div class="issue-monthly-bars">{''.join(month_nodes)}</div>
    </div>
    """


def occurrence_status_html(summary: dict) -> str:
    metrics = [
        ("오늘", int(summary.get("today_count") or 0), "info"),
        ("이번 주", int(summary.get("week_count") or 0), "info"),
        ("처리 중", int(summary.get("in_progress_count") or 0), "warning"),
        ("완료", int(summary.get("done_count") or 0), "success"),
        ("지연", int(summary.get("delayed_count") or 0), "danger"),
    ]
    metric_nodes = "".join(
        f"""
        <div class="occurrence-metric {tone}">
            <span>{label}</span>
            <strong>{value:,}건</strong>
        </div>
        """
        for label, value, tone in metrics
    )
    recent_rows = summary.get("recent_cases", [])
    if recent_rows:
        recent_nodes = "".join(
            f"""
            <a class="occurrence-row" href="{return_case_detail_link(case_id)}" target="_self">
                <span>{category}</span>
                <strong title="{product}">{product}</strong>
                <em>{status}</em>
            </a>
            """
            for case_id, category, product, status in [
                (
                    escape(str(row.get("case_id", ""))),
                    escape(str(row.get("category", "-"))),
                    escape(str(row.get("product", "-"))),
                    "완료" if row.get("done") else "처리 중",
                )
                for row in recent_rows
            ]
        )
    else:
        recent_nodes = '<div class="empty-cell">최근 발생 사례가 없습니다.</div>'
    return f"""
    <article class="panel occurrence-panel">
        <div class="panel-title-row compact">
            <h2>발생 현황 <small>(반품/AS)</small></h2>
            <a class="mini-filter-link" href="{return_case_filter_link("ALL")}" target="_self">전체</a>
        </div>
        <div class="occurrence-metrics">{metric_nodes}</div>
        <div class="occurrence-list">
            <div class="occurrence-list-head"><span>유형</span><span>최근 발생 사례</span><span>상태</span></div>
            {recent_nodes}
        </div>
    </article>
    """


def donut_gradient_stops(rows: list[dict], total_count: int) -> str:
    cursor = 0.0
    stops = []
    for row in rows:
        count = int(row.get("count") or 0)
        if count <= 0:
            continue
        start = cursor
        cursor += (count / total_count) * 100
        color = escape(str(row.get("color", "#94a3b8")))
        stops.append(f"{color} {start:.2f}% {cursor:.2f}%")
    return ", ".join(stops) if stops else "#214d47 0 100%"


def donut_segment_labels(rows: list[dict], total_count: int) -> str:
    cursor = 0.0
    labels = []
    for row in rows:
        count = int(row.get("count") or 0)
        if count <= 0:
            continue
        percent = count / total_count * 100 if total_count else 0
        start = cursor
        cursor += percent
        if percent < 5:
            continue
        angle = math.radians((start + percent / 2) / 100 * 360 - 90)
        radius = 38
        left = 50 + math.cos(angle) * radius
        top = 50 + math.sin(angle) * radius
        category = escape(str(row.get("category", "-")))
        tooltip = f"{category} / {count:,}건 / {percent:.1f}%"
        labels.append(
            f'<span class="donut-segment-label" style="left:{left:.1f}%; top:{top:.1f}%;" title="{tooltip}">{percent:.0f}%</span>'
        )
    return "".join(labels)


def category_percent(count: int, total_count: int) -> str:
    if not total_count:
        return "0.0%"
    return f"{(int(count or 0) / total_count) * 100:.1f}%"


def monthly_chart_html(rows: list[dict], year: int) -> str:
    values = [int(row.get("count") or 0) for row in rows]
    max_value = max(values) if values else 0
    points = monthly_points(values)
    point_nodes = monthly_point_links(rows, values)
    peak_value = max_value
    peak_index = values.index(max_value) if max_value else 0
    peak_left = 24 + (336 / max(len(values) - 1, 1)) * peak_index
    month_labels = "".join(
        f'<a href="{return_case_filter_link("MONTH", str(row.get("month_key", "")))}" target="_self">{int(row.get("month") or 0)}월</a>'
        for row in rows
    )
    empty = '<div class="empty-cell">해당 연도 반품/AS 사례가 없습니다.</div>' if not max_value else ""
    return f"""
    <article class="panel mini-line-card">
        <div class="panel-title-row compact">
            <h2>월별 발생 추이 <small>(반품/AS)</small></h2>
            <a class="mini-filter-link" href="{return_case_filter_link("MONTH", f"{year}{date.today().month:02d}")}" target="_self">{year}년</a>
        </div>
        <div class="mini-chart">
            <svg viewBox="0 0 380 160" preserveAspectRatio="none">
                <g stroke="#214d47" stroke-width="1">
                    <line x1="20" y1="24" x2="365" y2="24"/>
                    <line x1="20" y1="72" x2="365" y2="72"/>
                    <line x1="20" y1="120" x2="365" y2="120"/>
                </g>
                <polyline points="{points}" fill="none" stroke="#4F6F8F" stroke-width="3"/>
                {point_nodes}
            </svg>
            <div class="peak-label" style="left:{peak_left / 380 * 100:.1f}%">{peak_value:,}건</div>
            <div class="month-labels">{month_labels}</div>
            {empty}
        </div>
    </article>
    """


def monthly_points(values: list[int]) -> str:
    if not values:
        values = [0] * 12
    max_value = max(values) if values else 0
    max_value = max(max_value, 1)
    count = len(values)
    points = []
    for index, value in enumerate(values):
        x = 24 + (336 / max(count - 1, 1)) * index
        y = 128 - (int(value or 0) / max_value) * 104
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def monthly_point_links(rows: list[dict], values: list[int]) -> str:
    if not rows:
        return ""
    max_value = max(values) if values else 0
    max_value = max(max_value, 1)
    count = len(rows)
    nodes = []
    for index, row in enumerate(rows):
        value = int(row.get("count") or 0)
        x = 24 + (336 / max(count - 1, 1)) * index
        y = 128 - (value / max_value) * 104
        href = return_case_filter_link("MONTH", str(row.get("month_key", "")))
        label = f"{int(row.get('month') or 0)}월 {value}건"
        nodes.append(
            f'<a href="{href}" target="_self"><circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="#ffffff" stroke="#0f766e" stroke-width="2"><title>{escape(label)}</title></circle></a>'
        )
    return "".join(nodes)


def warehouse_status_html(rows: list[dict]) -> str:
    if not rows:
        rows = [{"name": source_type, "rate": 0, "qty": 0, "problem_count": 0, "tone": "cyan"} for source_type in SOURCE_TYPES]
    body = "".join(
        f"""
        <div class="warehouse-row {tone}">
            <span>{name}</span>
            <div class="bar"><i style="width:{rate}%"></i></div>
            <b>{rate}%</b>
            <strong>{qty}</strong>
        </div>
        """
        for name, rate, qty, tone in [
            (
                escape(str(row.get("name", "-"))),
                int(row.get("rate") or 0),
                format_metric(row.get("qty", 0)),
                escape(str(row.get("tone", "cyan"))),
            )
            for row in rows
        ]
    )
    return f"""
    <article class="panel warehouse-panel">
        <h2>재고처별 현황 <small>(재고관리)</small></h2>
        <div class="warehouse-head"><span>구분</span><span>가용 비율</span><span>현재고</span></div>
        {body}
        <a class="ghost-link" href="?{urlencode({"page": "재고관리"})}" target="_self">재고관리 바로가기&nbsp;&nbsp;→</a>
    </article>
    """


def inbound_3pl_html(rows: list[dict]) -> str:
    if not rows:
        rows = [{"product_name": "입고내역 없음", "quantity": 0, "status": "-", "tone": "normal"}]
    inventory_href = "?" + urlencode({"page": "재고관리"})
    body = "".join(
        f"""
        <a class="inbound-row" href="{inventory_href}" target="_self" title="재고관리에서 3PL 입고내역 보기">
            <strong title="{product_name}">{product_name}</strong>
            <em>{quantity}</em>
            <span class="stock-badge {tone}">{status}</span>
        </a>
        """
        for product_name, quantity, status, tone in [
            (
                escape(str(row.get("product_name", "-"))),
                f"{int(row.get('quantity') or 0):,} EA",
                escape(str(row.get("status", "-"))),
                escape(str(row.get("tone", "normal"))),
            )
            for row in rows
        ]
    )
    return f"""
    <article class="panel top-panel">
        <div class="panel-title-row compact">
            <h2>이번 주 3PL 입고 품목</h2>
            <span class="mini-badge">SAFETY STOCK</span>
        </div>
        <div class="inbound-head">
            <span>상품명</span>
            <span>입고수량(EA)</span>
            <span>안전재고 상태</span>
        </div>
        {body}
        <a class="ghost-link" href="{inventory_href}" target="_self">3PL 입고내역 보기&nbsp;&nbsp;→</a>
    </article>
    """


def purchase_progress_html(rows: list[dict]) -> str:
    if not rows:
        rows = [
            {"label": "구매요청 대기", "value": 0, "caption": "0원", "tone": "orange", "href": purchase_link("구매요청(PR)", "pr_pending")},
            {"label": "견적 진행", "value": 0, "caption": "RFQ 등록", "tone": "cyan", "href": purchase_link("견적관리(RFQ)", "rfq_progress")},
            {"label": "발주 완료", "value": 0, "caption": "0원", "tone": "blue", "href": purchase_link("발주관리(PO)", "po_progress")},
            {"label": "입고 대기", "value": 0, "caption": "0원", "tone": "green", "href": purchase_link("발주관리(PO)", "inbound_waiting")},
            {"label": "납기 지연", "value": 0, "caption": "최대 0일", "tone": "cyan", "href": purchase_link("발주관리(PO)", "po_delay")},
        ]
    body = "".join(
        f"""
        <a class="purchase-progress-row {tone}" href="{href}" target="_self">
            <strong>{label}</strong>
            <em>{value:,}건</em>
            <span>{caption}</span>
        </a>
        """
        for label, value, caption, tone, href in [
            (
                escape(str(row.get("label", "-"))),
                int(row.get("value") or 0),
                escape(str(row.get("caption", ""))),
                escape(str(row.get("tone", "cyan"))),
                escape(str(row.get("href", "#"))),
            )
            for row in rows
        ]
    )
    return f"""
    <article class="panel top-panel purchase-progress-panel">
        <div class="panel-title-row compact">
            <h2>구매 진행 현황</h2>
            <span class="mini-badge">PURCHASE</span>
        </div>
        <div class="purchase-progress-head">
            <span>업무상태</span>
            <span>건수</span>
            <span>금액/요약</span>
        </div>
        {body}
        <a class="ghost-link" href="{purchase_link("구매요청(PR)")}" target="_self">구매관리 바로가기&nbsp;&nbsp;→</a>
    </article>
    """


def purchase_priority_html(rows: list[dict]) -> str:
    inventory_href = "?" + urlencode({"page": "재고관리", "inventory_filter": "need_inbound"})
    if rows:
        body = "".join(
            f"""
            <tr>
                <td><a href="{inventory_href}" target="_self" title="{item}">{item}</a></td>
                <td>{source}</td>
                <td>{current}</td>
                <td>{safe}</td>
                <td>{shortage}</td>
                <td>{lead_time}</td>
                <td><span class="status-badge {tone}">{action}</span></td>
            </tr>
            """
            for item, source, current, safe, shortage, lead_time, action, tone in [
                (
                    escape(str(row.get("item_name", "-"))),
                    escape(str(row.get("source_type", "-"))),
                    format_metric(row.get("current_stock", 0)),
                    format_metric(row.get("safe_stock", 0)),
                    format_metric(row.get("shortage", 0)),
                    f"{int(row.get('lead_time') or 0)}일",
                    escape(str(row.get("action", "PR필요"))),
                    priority_tone(str(row.get("action", ""))),
                )
                for row in rows
            ]
        )
    else:
        body = '<tr><td colspan="7" class="empty-cell">발주 우선순위 대상이 없습니다.</td></tr>'
    return f"""
    <article class="panel table-panel priority-panel">
        <h2>발주 우선순위 <small>(MRP/안전재고)</small></h2>
        <table>
            <thead><tr><th>품목</th><th>구분</th><th>현재고</th><th>안전</th><th>부족</th><th>리드타임</th><th>권장</th></tr></thead>
            <tbody>{body}</tbody>
        </table>
        <a class="ghost-link" href="{inventory_href}" target="_self">재고부족 품목 보기&nbsp;&nbsp;→</a>
    </article>
    """


def weekly_outbound_top_html(summary: dict) -> str:
    rows = summary.get("rows", [])
    period = str(summary.get("period", "7"))
    start_date = summary.get("start_date")
    end_date = summary.get("end_date")
    period_links = "".join(
        f'<a class="{"active" if period == key else ""}" href="?{urlencode({"page": "대시보드", "outbound_top_period": key})}" target="_self">{label}</a>'
        for key, label in [("7", "최근 7일"), ("30", "최근 30일"), ("month", "이번 달")]
    )
    summary_html = f"""
    <div class="outbound-top-summary">
        <span>총 출고수량 <strong>{format_metric(summary.get("total_qty", 0))}EA</strong></span>
        <span>총 출고금액 <strong>{format_won(summary.get("total_amount", 0))}</strong></span>
        <span>출고 SKU 수 <strong>{format_metric(summary.get("sku_count", 0))}</strong></span>
    </div>
    """
    if rows:
        body = "".join(
            f"""
            <a class="outbound-top-row" href="{outbound_history_link(item_name, start_date, end_date)}" target="_self">
                <span class="rank">{rank}</span>
                <span title="{item_code}">{item_code}</span>
                <strong title="{item_name}">{item_name}</strong>
                <span>{qty}EA</span>
                <span>{amount}</span>
                <span>{share}</span>
            </a>
            """
            for rank, item_code, item_name, qty, amount, share in [
                (
                    int(row.get("rank") or index),
                    escape(str(row.get("item_code", "-"))),
                    escape(str(row.get("item_name", "-"))),
                    format_metric(row.get("outbound_qty", 0)),
                    format_won(row.get("outbound_amount", 0)),
                    f'{float(row.get("share") or 0):.1f}%',
                )
                for index, row in enumerate(rows, start=1)
            ]
        )
    else:
        body = '<div class="outbound-top-empty">선택한 기간의 출고 데이터가 없습니다.</div>'
    return f"""
    <article class="panel table-panel outbound-top-panel">
        <div class="panel-title-row compact">
            <h2>주간 출고 TOP3 <small>({escape(str(summary.get("label", "최근 7일")))})</small></h2>
            <div class="chart-filter-links">{period_links}</div>
        </div>
        {summary_html}
        <div class="outbound-top-table">
            <div class="outbound-top-head">
                <span>순위</span><span>품목코드</span><span>품목명</span><span>출고수량(EA)</span><span>출고금액</span><span>전체 비중(%)</span>
            </div>
            {body}
        </div>
    </article>
    """


def schedule_core_tasks_html(summary: dict) -> str:
    rows = summary.get("rows", [])
    week_start = summary.get("week_start")
    week_end = summary.get("week_end")
    if hasattr(week_start, "strftime") and hasattr(week_end, "strftime"):
        label = f"{week_start:%m.%d} ~ {week_end:%m.%d}"
    else:
        label = "이번 주"
    source_note = "최근 저장 주" if summary.get("source") == "latest" else "이번 주"
    if rows:
        body = "".join(
            f"""
            <li class="{status_class}">
                <i>{status_mark}</i>
                <span title="{title}">{title}</span>
                <b>{status_text}</b>
            </li>
            """
            for title, status_mark, status_text, status_class in [
                (
                    escape(str(row.get("title", "-"))),
                    "✓" if row.get("checked") else "•",
                    "완료" if row.get("checked") else "진행",
                    "done" if row.get("checked") else "active",
                )
                for row in rows
            ]
        )
    else:
        body = '<li class="empty"><span>일정관리에서 이번 주 핵심업무를 등록하세요.</span></li>'
    completed = sum(1 for row in rows if row.get("checked"))
    return f"""
    <article class="panel table-panel core-task-panel">
        <div class="panel-title-row compact">
            <h2>핵심업무 <small>({escape(label)})</small></h2>
            <span class="mini-badge">{escape(source_note)}</span>
        </div>
        <div class="core-task-summary">
            <span>등록 <strong>{format_metric(len(rows))}건</strong></span>
            <span>완료 <strong>{format_metric(completed)}건</strong></span>
            <span>진행 <strong>{format_metric(max(len(rows) - completed, 0))}건</strong></span>
        </div>
        <ul class="core-task-list">{body}</ul>
        <a class="ghost-link" href="?{urlencode({"page": "일정관리"})}" target="_self">일정관리 바로가기&nbsp;&nbsp;→</a>
    </article>
    """


def outbound_history_link(item_name: str, start_date, end_date) -> str:
    params = {
        "page": "재고관리",
        "inventory_filter": "outbound",
        "outbound_item": item_name,
    }
    if hasattr(start_date, "isoformat"):
        params["outbound_start"] = start_date.isoformat()
    if hasattr(end_date, "isoformat"):
        params["outbound_end"] = end_date.isoformat()
        params["inventory_date"] = end_date.isoformat()
    return "?" + urlencode(params)


def priority_tone(action: str) -> str:
    if action == "입고추적":
        return "ordered"
    if action == "PR진행":
        return "pending"
    return "delay"


def recent_cases_html(rows: list[dict]) -> str:
    if rows:
        body = "".join(
            f"""
            <tr>
                <td><a href="{return_case_detail_link(case_id)}" target="_self">{case_id}</a></td>
                <td><a href="{return_case_filter_link(kind)}" target="_self">{kind}</a></td>
                <td><a href="{return_case_detail_link(case_id)}" target="_self" title="{product}">{product}</a></td>
                <td>{registered_date}</td>
            </tr>
            """
            for case_id, kind, product, registered_date in [
                (
                    escape(str(row.get("case_id", "-"))),
                    escape(str(row.get("category", "-"))),
                    escape(str(row.get("product", "-"))),
                    escape(str(row.get("date", "-"))),
                )
                for row in rows
            ]
        )
    else:
        body = '<tr><td colspan="4" class="empty-cell">등록된 반품/AS 사례가 없습니다.</td></tr>'
    return f"""
    <article class="panel table-panel">
        <h2>최근 등록 사례</h2>
        <table>
            <thead><tr><th>사례번호</th><th>유형</th><th>상품명</th><th>등록일</th></tr></thead>
            <tbody>{body}</tbody>
        </table>
        <a class="ghost-link" href="{return_case_filter_link("ALL")}" target="_self">전체 사례 보기&nbsp;&nbsp;→</a>
    </article>
    """


def recent_orders_html(rows: list[dict]) -> str:
    if not rows:
        rows = []
    body = "".join(
        f"""
        <a class="order-summary-row" href="{po_href}" target="_self">
            <span>{po_number}</span>
            <span>{supplier}</span>
            <strong title="{item}">{item}</strong>
            <em class="status-badge {tone}">{status}</em>
        </a>
        """
        for po_href, po_number, supplier, item, status, tone in [
            (
                purchase_po_link(str(row.get("po_number", ""))),
                escape(str(row.get("po_number", "-"))),
                escape(str(row.get("supplier_name", "-"))),
                escape(str(row.get("item_name", "-"))),
                escape(str(row.get("status", "-"))),
                escape(str(row.get("tone", "pending"))),
            )
            for row in rows
        ]
    ) or '<div class="order-summary-empty">저장된 발주·입고 내역이 없습니다.</div>'
    return f"""
    <article class="panel order-panel">
        <h2>최근 발주·입고 내역 <small>(구매관리)</small></h2>
        <div class="order-summary-table">
            <div class="order-summary-head">
                <span>PO번호</span>
                <span>거래처</span>
                <span>품목</span>
                <span>상태</span>
            </div>
            <div class="order-summary-body">{body}</div>
        </div>
        <a class="ghost-link" href="{purchase_link("발주관리(PO)", "po_progress")}" target="_self">전체 발주내역 보기&nbsp;&nbsp;→</a>
    </article>
    """


def normalize_trend_rows(rows: list[dict]) -> list[dict]:
    normalized = []
    for row in rows or []:
        parsed_date = pd.to_datetime(row.get("date"), errors="coerce")
        if pd.isna(parsed_date):
            continue
        normalized.append(
            {
                "date": parsed_date.date(),
                "label": f"{parsed_date.month}/{parsed_date.day}",
                "value": int(row.get("value") or 0),
            }
        )
    normalized.sort(key=lambda row: row["date"])
    return normalized


def trend_points(values: list[int]) -> str:
    if not values:
        return ""
    left, right = 42, 592
    top, bottom = 38, 190
    max_value = max(values)
    min_value = min(values)
    span = max(max_value - min_value, 1)
    step = (right - left) / max(len(values) - 1, 1)
    points = []
    for index, value in enumerate(values):
        x = left + step * index
        y = bottom - ((value - min_value) / span) * (bottom - top)
        if max_value == min_value:
            y = (top + bottom) / 2
        points.append(f"{x:.0f},{y:.0f}")
    return " ".join(points)


def trend_points_scaled(values: list[int], max_value: int) -> str:
    if not values:
        return ""
    left, right = 42, 592
    top, bottom = 38, 190
    scale = max(max_value, 1)
    step = (right - left) / max(len(values) - 1, 1)
    points = []
    for index, value in enumerate(values):
        x = left + step * index
        y = bottom - (int(value or 0) / scale) * (bottom - top)
        points.append(f"{x:.0f},{y:.0f}")
    return " ".join(points)


def chart_grid_values(max_value: int) -> list[int]:
    if max_value <= 0:
        return [0, 1, 2, 3, 4]
    step = max(1, round(max_value / 4))
    return [step * index for index in range(5)]


def format_date_label(value) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return "-"
    return parsed.strftime("%Y-%m-%d")


def grid_lines(values: list) -> str:
    y_positions = [88, 66, 44, 22, 0]
    labels = "".join(f'<span style="top:calc({y}% - 0.35rem)">{value:,}</span>' for value, y in zip(values, y_positions))
    lines = "".join(f'<i style="top:{y}%"></i>' for y in y_positions)
    return f'<div class="chart-y-labels">{labels}</div><div class="chart-grid-lines">{lines}</div>'


def axis_labels(labels: list) -> str:
    return '<div class="axis-labels">' + "".join(f"<span>{escape(str(label))}</span>" for label in labels) + "</div>"


def chart_points(point_string: str, color: str) -> str:
    if not point_string:
        return ""
    return "".join(
        f'<circle cx="{point.split(",")[0]}" cy="{point.split(",")[1]}" r="4" fill="{color}" stroke="#bffcf6" stroke-width="1.5"/>'
        for point in point_string.split()
    )


def icon_svg(name: str) -> str:
    paths = {
        "cube": '<path d="M12 2 3 7v10l9 5 9-5V7l-9-5Zm0 0v10m9-5-9 5-9-5m5 2.8v6.4l4 2.2 4-2.2V9.8"/>',
        "truck": '<path d="M3 6h11v10H3zM14 10h4l3 3v3h-7z"/><circle cx="7" cy="18" r="2"/><circle cx="18" cy="18" r="2"/>',
        "return": '<path d="M5 12a7 7 0 0 1 12-5l2 2M19 4v5h-5M19 12a7 7 0 0 1-12 5l-2-2M5 20v-5h5"/>',
        "box": '<path d="M12 2 4 6v12l8 4 8-4V6l-8-4Zm0 0v8m8-4-8 4-8-4"/><path d="M8 4v6"/>',
        "alert": '<path d="M12 3 2 20h20L12 3Z"/><path d="M12 9v5m0 3h.01"/>',
        "case": '<path d="M8 3h8l2 3v15H6V3h2Z"/><path d="M9 10h6M9 14h6M9 18h4M15 3v4h3"/>',
    }
    return f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">{paths[name]}</svg>'
