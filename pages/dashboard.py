from __future__ import annotations

from datetime import date, timedelta
from html import escape
import math
from pathlib import Path
import sqlite3
from urllib.parse import urlencode

import pandas as pd
import streamlit as st
from sqlalchemy import select

try:
    from backend.database import SessionLocal, init_db
    from backend import services
    from backend.models import CategoryBomItem, InventoryDaily, PurchaseOrder, PurchaseRequest, RfqQuote
except (ModuleNotFoundError, RuntimeError) as exc:
    SessionLocal = None
    init_db = None
    services = None
    CategoryBomItem = None
    InventoryDaily = None
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


def render_html(markup: str) -> None:
    if hasattr(st, "html"):
        st.html(markup)
    else:
        st.markdown(markup, unsafe_allow_html=True)


def render_dashboard() -> None:
    inventory_summary = get_home_inventory_summary()
    work_date = inventory_summary.get("work_date") or date.today()
    purchase_summary = get_home_purchase_summary(work_date)
    core_tasks_summary = get_dashboard_core_tasks()
    inventory_charts = inventory_summary.get("charts", {})
    return_case_summary = get_return_case_summary(work_date)
    render_html(
        f"""
        <main class="dashboard-shell">
            {weekly_schedule_html()}
            {kpi_cards_html(inventory_summary, purchase_summary)}
            <section class="chart-grid">
                {shipping_chart_html(inventory_charts.get("outbound_trend", []), inventory_summary.get("outbound_qty", 0))}
                {inventory_chart_html(inventory_charts.get("stock_trend", []))}
            </section>
            <section class="middle-grid">
                {issue_donut_html(return_case_summary.get("category_rows", []), return_case_summary.get("total_count", 0))}
                {monthly_chart_html(return_case_summary.get("monthly_rows", []), return_case_summary.get("year", date.today().year))}
                {warehouse_status_html(inventory_summary.get("source_status", []))}
                {purchase_progress_html(purchase_summary.get("progress_rows", []))}
            </section>
            <section class="bottom-grid">
                {schedule_core_tasks_html(core_tasks_summary)}
                {recent_orders_html(purchase_summary.get("recent_po_inbound", []))}
            </section>
        </main>
        """
    )


def dashboard_available() -> bool:
    if init_db is None or SessionLocal is None or services is None:
        return False
    try:
        init_db()
    except Exception as exc:
        global DASHBOARD_IMPORT_ERROR
        DASHBOARD_IMPORT_ERROR = f"재고관리 DB 초기화 실패: {exc}"
        return False
    return True


def with_db(action):
    if SessionLocal is None:
        return None
    db = SessionLocal()
    try:
        return action(db)
    finally:
        db.close()


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

    date_payload = with_db(lambda db: services.list_work_dates(db)) or []
    date_values = [value.date() for value in pd.to_datetime(date_payload, errors="coerce") if not pd.isna(value)]
    work_date = date_values[0] if date_values else date.today()
    payload = with_db(lambda db: build_home_inventory_payload(db, work_date)) or {}
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
    payload = with_db(lambda db: build_home_purchase_payload(db, work_date, trend_days)) or {}
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
    week_start = work_date - timedelta(days=work_date.weekday())
    week_end = week_start + timedelta(days=6)
    inbound_rows = [
        row
        for row in services.list_inbound(db, "3PL")
        if row.inbound_date and week_start <= row.inbound_date <= week_end
    ]

    stock_map = {
        (row.product_name, row.barcode or ""): row
        for row in services.list_daily(db, "3PL", work_date)
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
    rows = []
    for source_type in SOURCE_TYPES:
        rows.extend(services.list_inbound(db, source_type))
    rows.sort(key=lambda row: (row.inbound_date or date.min, row.id or 0), reverse=True)
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


def get_return_case_summary(work_date: date) -> dict:
    current_year = work_date.year if hasattr(work_date, "year") else date.today().year
    summary = {
        "total_count": 0,
        "category_rows": [],
        "monthly_rows": [{"month": month, "month_key": f"{current_year}{month:02d}", "count": 0} for month in range(1, 13)],
        "recent_cases": [],
        "year": current_year,
    }
    if not RETURN_CASE_DB_PATH.exists():
        return summary
    try:
        with sqlite3.connect(RETURN_CASE_DB_PATH) as conn:
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
    except sqlite3.Error:
        return summary

    colors = ["#ff4545", "#ffb22e", "#3d86ff", "#58d163", "#bd72ff", "#25d6ce", "#94a3b8"]
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
        "year": current_year,
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
    if not RETURN_CASE_DB_PATH.exists():
        return 0
    month_key = work_date.strftime("%Y%m")
    try:
        with sqlite3.connect(RETURN_CASE_DB_PATH) as conn:
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


def weekly_schedule_html() -> str:
    week_start, days = get_dashboard_week_schedule()
    week_end = week_start + pd.Timedelta(days=6)
    cells = "".join(
        f"""
        <div class="week-cell {state}">
            <div class="week-date">{date}</div>
            <ul>{''.join(f'<li>{item}</li>' for item in items)}</ul>
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

    if SCHEDULE_DB_PATH.exists():
        try:
            with sqlite3.connect(SCHEDULE_DB_PATH) as conn:
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

    if not any(schedule_by_day.values()):
        schedule_by_day = fallback_dashboard_schedule()

    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    days = []
    for index, weekday in enumerate(weekdays):
        day = week_start + pd.Timedelta(days=index)
        state = "active" if day.date() == today.date() else ""
        if index == 5:
            state = f"{state} blue".strip()
        if index == 6:
            state = f"{state} red".strip()
        items = schedule_by_day.get(index) or ["-"]
        days.append((f"{day:%m.%d} ({weekday})", items, state))
    return week_start, days


def get_dashboard_core_tasks(limit: int = 8) -> dict:
    today = pd.Timestamp(date.today())
    current_week_start = today - pd.Timedelta(days=today.weekday())
    summary = {
        "week_start": current_week_start,
        "week_end": current_week_start + pd.Timedelta(days=6),
        "rows": [],
        "source": "current",
    }
    if not SCHEDULE_DB_PATH.exists():
        return summary

    try:
        with sqlite3.connect(SCHEDULE_DB_PATH) as conn:
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
        return summary
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


def fallback_dashboard_schedule() -> dict[int, list[str]]:
    return {
        0: ["일정관리에서 주간 일정을 저장하세요"],
        1: ["-"],
        2: ["-"],
        3: ["-"],
        4: ["-"],
        5: ["-"],
        6: ["-"],
    }


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
    shortage = format_metric(summary.get("need_inbound_count", 0))
    soldout = format_metric(summary.get("soldout_count", 0))
    pending_pr = format_metric(purchase_summary.get("pending_pr_count", 0))
    pending_amount = format_won(purchase_summary.get("pending_pr_amount", 0))
    po_progress = format_metric(purchase_summary.get("po_progress_count", 0))
    uninbound_amount = format_won(purchase_summary.get("uninbound_amount", 0))
    delayed_count = format_metric(purchase_summary.get("delayed_count", 0))
    max_delay = int(purchase_summary.get("max_delay_days", 0) or 0)
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
        ("alert", "재고부족 SKU", f"{shortage}건", f"품절 {soldout}건", "orange", inventory_link("need_inbound")),
        ("case", "구매요청 대기", f"{pending_pr}건", f"대기 {pending_amount}", "purple", purchase_link("구매요청(PR)", "pr_pending")),
        ("truck", "발주 진행", f"{po_progress}건", f"미입고 {uninbound_amount}", "blue", purchase_link("발주관리(PO)", "po_progress")),
        ("alert", "입고 지연", f"{delayed_count}건", f"최대 {max_delay}일 지연", "red" if max_delay else "cyan", purchase_link("발주관리(PO)", "po_delay")),
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


def inventory_chart_html(rows: list[dict]) -> str:
    return trend_chart_html(
        rows,
        title="재고 추이",
        color="#20d6c8",
        fill_id="inventoryFill",
        tooltip_class="cyan-tip",
        metric_label="총 재고",
    )


def shipping_chart_html(rows: list[dict], outbound_qty: int = 0) -> str:
    return trend_chart_html(
        rows,
        title="출고 추이",
        color="#4b9cff",
        fill_id="shippingFill",
        tooltip_class="blue-tip",
        metric_label="출고",
        summary_html=f'<div class="chart-inline-summary"><span>기준일 출고수량</span><strong>{format_metric(outbound_qty)}개</strong></div>',
    )


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
    filter_7 = "?" + urlencode({"page": "홈", "purchase_trend": "7"})
    filter_30 = "?" + urlencode({"page": "홈", "purchase_trend": "30"})
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
                <polyline points="{ordered_points}" fill="none" stroke="#4b9cff" stroke-width="3"/>
                <polyline points="{inbound_points}" fill="none" stroke="#58d163" stroke-width="3"/>
                {chart_points(ordered_points, "#4b9cff")}
                {chart_points(inbound_points, "#58d163")}
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


def issue_donut_html(rows: list[dict], total_count: int) -> str:
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
                <polyline points="{points}" fill="none" stroke="#ff4545" stroke-width="3"/>
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
        f'<a class="{"active" if period == key else ""}" href="?{urlencode({"page": "홈", "outbound_top_period": key})}" target="_self">{label}</a>'
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
        <tr>
            <td><a href="{po_href}" target="_self">{po_number}</a></td>
            <td>{supplier}</td>
            <td>{item}</td>
            <td>{order_qty}</td>
            <td>{inbound_qty}</td>
            <td>{expected_date}</td>
            <td>{actual_date}</td>
            <td><span class="status-badge {tone}">{status}</span></td>
        </tr>
        """
        for po_href, po_number, supplier, item, order_qty, inbound_qty, expected_date, actual_date, status, tone in [
            (
                purchase_po_link(str(row.get("po_number", ""))),
                escape(str(row.get("po_number", "-"))),
                escape(str(row.get("supplier_name", "-"))),
                escape(str(row.get("item_name", "-"))),
                format_metric(row.get("order_qty", 0)),
                format_metric(row.get("inbound_qty", 0)),
                format_date_label(row.get("expected_inbound_date")),
                format_date_label(row.get("actual_inbound_date")),
                escape(str(row.get("status", "-"))),
                escape(str(row.get("tone", "pending"))),
            )
            for row in rows
        ]
    ) or f'<tr><td colspan="8" class="empty-cell">구매관리 탭에 저장된 발주·입고 내역이 없습니다.</td></tr>'
    return f"""
    <article class="panel order-panel">
        <h2>최근 발주·입고 내역 <small>(구매관리)</small></h2>
        <table>
            <thead>
                <tr>
                    <th>PO번호</th>
                    <th>거래처</th>
                    <th>품목</th>
                    <th>발주수량</th>
                    <th>입고수량</th>
                    <th>입고예정일</th>
                    <th>실제입고일</th>
                    <th>상태</th>
                </tr>
            </thead>
            <tbody>{body}</tbody>
        </table>
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
