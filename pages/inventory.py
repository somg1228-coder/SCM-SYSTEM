from __future__ import annotations

from datetime import date, timedelta
from html import escape
from io import BytesIO
from math import ceil
from pathlib import Path
import re
from time import perf_counter
from urllib.parse import urlencode

import pandas as pd
import streamlit as st
from sqlalchemy import delete, func, select

from components.lazy_tabs import lazy_tab_selector
from pages import product_master as product_master_page

try:
    import plotly.express as px
except ModuleNotFoundError:
    px = None

try:
    from backend.database import SessionLocal
    from backend.models import (
        CategoryBomItem,
        InventoryDaily,
        InventoryOutputHistory,
        InventoryUploadHistory,
        InventoryUploadSnapshot,
        MaterialInventoryItem,
        ProductionPlan,
        PurchaseRequest,
    )
    from backend import services
except (ModuleNotFoundError, RuntimeError) as exc:
    SessionLocal = None
    CategoryBomItem = None
    InventoryDaily = None
    InventoryOutputHistory = None
    InventoryUploadHistory = None
    InventoryUploadSnapshot = None
    MaterialInventoryItem = None
    ProductionPlan = None
    PurchaseRequest = None
    services = None
    INVENTORY_IMPORT_ERROR = str(exc)
else:
    INVENTORY_IMPORT_ERROR = ""

SOURCE_TYPES = ["3PL", "오프라인", "창고"]
SOURCE_KEY_MAP = {
    "3PL": "threepl",
    "오프라인": "offline",
    "창고": "warehouse",
}

INVENTORY_STATUS_COLUMN_CONFIG = [
    {"key": "category", "label": "카테고리"},
    {"key": "barcode", "label": "바코드"},
    {"key": "product_name", "label": "상품명"},
    {"key": "available_stock", "label": "가용재고"},
    {"key": "avg_daily_outbound_1w", "label": "1주 평균출고수량"},
    {"key": "stock_status", "label": "재고상태"},
    {"key": "pending_outbound_qty", "label": "출고예정"},
    {"key": "current_stock", "label": "현재고"},
    {"key": "order_required_date", "label": "발주필요일"},
    {"key": "box_pallet_unit", "label": "박스/파렛트 단위"},
    {"key": "supplier", "label": "업체명"},
    {"key": "manager", "label": "담당자"},
    {"key": "inbound_cycle", "label": "리드타임"},
]
INVENTORY_STATUS_DISPLAY_COLUMNS = [column["label"] for column in INVENTORY_STATUS_COLUMN_CONFIG]
DAILY_COLUMNS = ["선택", *INVENTORY_STATUS_DISPLAY_COLUMNS]

INBOUND_COLUMNS = [
    "삭제",
    "입고일자",
    "SKU",
    "바코드",
    "상품명",
    "공급처",
    "입고수량",
    "입고구분",
    "비고",
]

DASHBOARD_FILTER_LABELS = {
    "all": "전체 재고 목록",
    "outbound": "기준일 출고수량 목록",
    "need_inbound": "재고부족 SKU 목록",
    "soldout": "품절 SKU 목록",
}

INVENTORY_MAIN_SECTIONS = ["현재재고", "안전재고", "재고이력", "MRP", "발주추천", "자재/반제품"]
INVENTORY_CURRENT_SOURCES = ["3PL", "오프라인", "창고"]
INVENTORY_SOURCE_MAP = {"3PL": "3PL", "오프라인": "오프라인", "창고": "창고"}
INVENTORY_SOURCE_TABS = ["재고조회", "입고내역", "출고내역", "대시보드", "마스터 관리"]


def render_inventory_page() -> None:
    inject_inventory_css()
    product_master_page.inject_product_master_css()

    if not inventory_available():
        st.error(INVENTORY_IMPORT_ERROR or "재고관리 DB를 초기화하지 못했습니다. requirements.txt 설치 상태를 확인해주세요.")
        return

    sync_inventory_filter_from_query()
    render_inventory_page_lazy()


def inventory_available() -> bool:
    return SessionLocal is not None and services is not None


def render_inventory_page_lazy() -> None:
    selected_section, selected_source, selected_tab = render_inventory_navigation()

    if selected_section == "현재재고":
        source_type = INVENTORY_SOURCE_MAP[selected_source]
        render_inventory_list_panel()
        render_outbound_history_linked_panel()
        render_source_inventory_tabs_lazy(source_type, selected_tab)
    elif selected_section == "안전재고":
        render_safe_stock_tab()
    elif selected_section == "재고이력":
        render_stock_history_tab()
    elif selected_section == "MRP":
        render_mrp_tab()
    elif selected_section == "발주추천":
        render_purchase_recommendation_tab()
    elif selected_section == "자재/반제품":
        render_material_inventory_tab()


def render_inventory_navigation() -> tuple[str, str, str]:
    with st.container(key="inventory_nav_shell"):
        st.markdown('<div class="inventory-nav-caption">재고관리</div>', unsafe_allow_html=True)
        selected_section = lazy_tab_selector(
            INVENTORY_MAIN_SECTIONS,
            "inventory_main_section",
            default="현재재고",
            compact=True,
        )

        selected_source = ""
        selected_tab = ""
        if selected_section == "현재재고":
            with st.container(key="inventory_nav_source"):
                st.markdown('<div class="inventory-nav-caption">현재재고 하위 구분</div>', unsafe_allow_html=True)
                selected_source = lazy_tab_selector(
                    INVENTORY_CURRENT_SOURCES,
                    "inventory_current_source",
                    default=st.session_state.get("inventory_active_source") or "3PL",
                    compact=True,
                )
                if selected_source not in INVENTORY_CURRENT_SOURCES:
                    selected_source = "3PL"
                st.session_state["inventory_active_source"] = selected_source

            source_type = INVENTORY_SOURCE_MAP[selected_source]
            with st.container(key="inventory_nav_detail"):
                st.markdown(f'<div class="inventory-nav-caption">{selected_source} 메뉴</div>', unsafe_allow_html=True)
                selected_tab = lazy_tab_selector(
                    INVENTORY_SOURCE_TABS,
                    f"inventory_{source_key(source_type)}_section",
                    default="재고조회",
                    compact=True,
                )

    return selected_section, selected_source, selected_tab


def render_source_inventory_tabs_lazy(source_type: str, selected_tab: str | None = None) -> None:
    if not selected_tab:
        selected_tab = lazy_tab_selector(
            INVENTORY_SOURCE_TABS,
            f"inventory_{source_key(source_type)}_section",
            default="재고조회",
            compact=True,
        )
    if selected_tab == "재고조회":
        render_daily_tab(source_type)
    elif selected_tab == "입고내역":
        render_inbound_tab(source_type)
    elif selected_tab == "출고내역":
        render_outbound_tab(source_type)
    elif selected_tab == "대시보드":
        render_inventory_dashboard_tab(source_type)
    elif selected_tab == "마스터 관리":
        product_master_page.render_master_tab(source_type, master_title(source_type))


def with_db(action):
    if SessionLocal is None:
        st.error(INVENTORY_IMPORT_ERROR or "DB 세션을 만들 수 없습니다.")
        return None
    db = SessionLocal()
    try:
        return action(db)
    except Exception as exc:
        db.rollback()
        st.error(f"처리 실패: {exc}")
        return None
    finally:
        db.close()


def result(message: str, count: int = 0, ok: bool = True) -> dict:
    return {"ok": ok, "message": message, "count": count}


def import_upload_result(message: str, outcome) -> dict:
    if isinstance(outcome, dict):
        count = int(outcome.get("count", 0) or 0)
        if outcome.get("used_html"):
            html_message = outcome.get("message") or "엑셀 형식이 HTML 기반이라 read_html로 처리했습니다"
            return result(f"{message} - {html_message}", count)
        return result(message, count)
    return result(message, int(outcome or 0))


def source_key(source_type: str) -> str:
    return SOURCE_KEY_MAP.get(source_type, source_type)


def master_title(source_type: str) -> str:
    if source_type == "창고":
        return "창고 마스터"
    return f"{source_type} 마스터"


def query_value(name: str) -> str:
    value = st.query_params.get(name)
    if isinstance(value, list):
        return value[0] if value else ""
    return value or ""


def sync_inventory_filter_from_query() -> None:
    inventory_filter = query_value("inventory_filter")
    inventory_date = query_value("inventory_date")
    outbound_item = query_value("outbound_item")
    outbound_start = query_value("outbound_start")
    outbound_end = query_value("outbound_end")
    if inventory_filter:
        st.session_state["inventory_filter"] = inventory_filter
    if inventory_date:
        st.session_state["inventory_filter_date"] = inventory_date
    if outbound_item:
        st.session_state["outbound_item_filter"] = outbound_item
    if outbound_start:
        st.session_state["outbound_start_date"] = outbound_start
    if outbound_end:
        st.session_state["outbound_end_date"] = outbound_end


def parse_date_value(value) -> date | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def dashboard_filter_work_date() -> date:
    session_date = parse_date_value(st.session_state.get("inventory_filter_date"))
    if session_date:
        return session_date
    if not st.session_state.get("inventory_filter"):
        return date.today()
    all_dates = []
    for source_type in SOURCE_TYPES:
        all_dates.extend(fetch_work_dates(source_type))
    return max(all_dates) if all_dates else date.today()


def render_inventory_list_panel() -> None:
    linked_filter = st.session_state.get("inventory_filter", "")
    if not linked_filter:
        return
    default_filter = linked_filter if linked_filter in DASHBOARD_FILTER_LABELS else "all"
    default_date = dashboard_filter_work_date()
    st.session_state["inventory_list_filter"] = default_filter
    st.session_state["inventory_list_work_date"] = default_date

    with st.expander("재고 목록", expanded=bool(linked_filter)):
        with st.container(key="inventory_dashboard_linked_panel"):
            filter_options = list(DASHBOARD_FILTER_LABELS.keys())
            control_cols = st.columns([1.2, 1.35, 3.2, 0.9, 0.7], gap="small")
            with control_cols[0]:
                work_date = st.date_input(
                    "목록 기준일자",
                    value=default_date,
                    key="inventory_list_work_date",
                )
            with control_cols[1]:
                filter_key = st.selectbox(
                    "재고 목록",
                    options=filter_options,
                    index=filter_options.index(default_filter),
                    format_func=lambda key: DASHBOARD_FILTER_LABELS[key],
                    key="inventory_list_filter",
                )
            label = DASHBOARD_FILTER_LABELS.get(filter_key, "재고 목록")
            rows = fetch_dashboard_filter_rows(filter_key, work_date)
            df = pd.DataFrame(rows)
            with control_cols[2]:
                st.markdown(f"### {label}")
                caption = f"{work_date:%Y-%m-%d} 기준"
                if linked_filter:
                    caption += " / 대시보드 카드에서 연결됨"
                st.caption(caption)
            with control_cols[3]:
                st.write("")
                if not df.empty:
                    st.download_button(
                        "목록 다운로드",
                        data=dataframe_to_excel(df),
                        file_name=f"{label}_{work_date:%Y%m%d}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                        key=f"dashboard_filter_download_{filter_key}_{work_date}",
                    )
            with control_cols[4]:
                st.write("")
                if linked_filter and st.button("닫기", key="dashboard_filter_close", use_container_width=True):
                    st.session_state.pop("inventory_filter", None)
                    st.session_state.pop("inventory_filter_date", None)
                    try:
                        st.query_params.clear()
                    except Exception:
                        pass
                    st.rerun()

            if df.empty:
                st.info("해당 조건의 재고 데이터가 없습니다.")
                return

            st.dataframe(df, hide_index=True, use_container_width=True)


def render_outbound_history_linked_panel() -> None:
    linked_item = clean_cell(st.session_state.get("outbound_item_filter"))
    if not linked_item:
        return
    default_end = parse_date_value(st.session_state.get("outbound_end_date")) or dashboard_filter_work_date()
    default_start = parse_date_value(st.session_state.get("outbound_start_date")) or (default_end - timedelta(days=6))

    with st.expander("출고 품목 이력", expanded=True):
        with st.container(key="inventory_outbound_linked_panel"):
            control_cols = st.columns([1.4, 0.95, 0.95, 2.45, 0.9, 0.7], gap="small")
            item_name = control_cols[0].text_input("품목", value=linked_item, key="outbound_history_item")
            start_date = control_cols[1].date_input("시작일", value=default_start, key="outbound_history_start")
            end_date = control_cols[2].date_input("종료일", value=default_end, key="outbound_history_end")
            rows = fetch_outbound_history_rows(item_name, start_date, end_date)
            df = pd.DataFrame(rows)
            with control_cols[3]:
                st.markdown(f"### {item_name} 출고이력")
                st.caption(f"{start_date:%Y-%m-%d} ~ {end_date:%Y-%m-%d} / 대시보드 TOP3에서 연결됨")
            with control_cols[4]:
                st.write("")
                if not df.empty:
                    st.download_button(
                        "이력 다운로드",
                        data=dataframe_to_excel(df),
                        file_name=f"{item_name}_출고이력_{start_date:%Y%m%d}_{end_date:%Y%m%d}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                        key=f"outbound_history_download_{item_name}_{start_date}_{end_date}",
                    )
            with control_cols[5]:
                st.write("")
                if st.button("닫기", key="outbound_history_close", use_container_width=True):
                    for key in ["outbound_item_filter", "outbound_start_date", "outbound_end_date"]:
                        st.session_state.pop(key, None)
                    try:
                        st.query_params.clear()
                    except Exception:
                        pass
                    st.rerun()

            if df.empty:
                st.info("선택한 기간의 출고 데이터가 없습니다.")
                return
            st.dataframe(df.sort_values("기준일자", ascending=False), hide_index=True, use_container_width=True)


def render_dashboard_filter_panel() -> None:
    render_inventory_list_panel()


def fetch_dashboard_filter_rows(filter_key: str, work_date: date) -> list[dict]:
    rows = []
    for source_type in SOURCE_TYPES:
        source_rows = with_db(lambda db, source_type=source_type: [services.daily_to_dict(row) for row in services.list_daily(db, source_type, work_date)]) or []
        for row in source_rows:
            if not include_dashboard_filter_row(row, filter_key):
                continue
            rows.append(
                {
                    "구분": row.get("source_type") or source_type,
                    "SKU": row.get("product_code", ""),
                    "바코드": row.get("barcode", ""),
                    "상품명": row.get("product_name", ""),
                    "대분류": row.get("category", ""),
                    "공급처": row.get("supplier", ""),
                    "보유재고": row.get("current_stock", 0),
                    "가용재고": row.get("available_stock", 0),
                    "안전재고": row.get("safe_stock", 0),
                    "재고상태": row.get("stock_status", ""),
                    "출고수량": row.get("outbound_qty", 0),
                }
            )

    if filter_key == "outbound":
        return sorted(rows, key=lambda row: int(row.get("출고수량") or 0), reverse=True)
    if filter_key == "need_inbound":
        return sorted(rows, key=lambda row: int(row.get("안전재고") or 0) - int(row.get("가용재고") or row.get("보유재고") or 0), reverse=True)
    return rows


def fetch_outbound_history_rows(item_name: str, start_date: date, end_date: date) -> list[dict]:
    keyword = clean_cell(item_name)
    if not keyword:
        return []
    rows = []
    for source_type in SOURCE_TYPES:
        source_rows = with_db(
            lambda db, source_type=source_type: [
                services.daily_to_dict(row)
                for row in services.list_outbound(
                    db,
                    source_type,
                    start_date=start_date,
                    end_date=end_date,
                    keyword=keyword,
                    limit=None,
                    offset=0,
                )
            ]
        ) or []
        for row in source_rows:
            work_date = parse_date_value(row.get("work_date"))
            if not work_date:
                continue
            rows.append(
                {
                    "구분": row.get("source_type") or source_type,
                    "기준일자": work_date,
                    "SKU": row.get("product_code", ""),
                    "바코드": row.get("barcode", ""),
                    "상품명": row.get("product_name", ""),
                    "출고수량": row.get("outbound_qty", 0),
                    "재고상태": row.get("stock_status", ""),
                }
            )
    return rows


def include_dashboard_filter_row(row: dict, filter_key: str) -> bool:
    product_name = clean_cell(row.get("product_name"))
    if not product_name:
        return False
    current_stock = to_int(row.get("current_stock"))
    available_stock = to_int(row.get("available_stock"))
    safe_stock = to_int(row.get("safe_stock"))
    stock_status = clean_cell(row.get("stock_status"))
    stock_for_status = available_stock if row.get("available_stock") is not None else current_stock

    if filter_key == "outbound":
        return to_int(row.get("outbound_qty")) > 0
    if filter_key == "soldout":
        return stock_status == "품절" or stock_for_status == 0
    if filter_key == "need_inbound":
        if stock_status in {"부족", "주의", "입고필요"}:
            return True
        if stock_status in {"품절", "미출"}:
            return False
        return safe_stock > 0 and stock_for_status <= safe_stock
    return True


def render_safe_stock_tab() -> None:
    st.markdown('<div class="inventory-tab-title">안전재고 관리</div>', unsafe_allow_html=True)
    cols = st.columns([0.9, 0.9, 1.0, 3.4], gap="small")
    source_type = cols[0].selectbox("재고처", SOURCE_TYPES, index=2, key="safe_stock_source")
    dates = fetch_work_dates(source_type)
    work_date = cols[1].date_input("기준일자", value=dates[0] if dates else date.today(), key="safe_stock_date")
    with cols[2]:
        st.write("")
        if st.button("안전재고 재계산", type="primary", use_container_width=True, key="safe_stock_recalc"):
            show_result(with_db(lambda db: result("안전재고 계산 완료", services.calculate_safe_stock(db, source_type, work_date))))
    with cols[3]:
        st.caption("최근 출고 흐름을 기준으로 안전재고를 계산하고 현재고 대비 부족 상태를 확인합니다.")

    rows = fetch_daily(source_type, work_date)
    df = pd.DataFrame(
        [
            {
                "SKU": row.get("product_code", ""),
                "상품명": row.get("product_name", ""),
                "현재고": row.get("current_stock", 0),
                "가용재고": row.get("available_stock", 0),
                "안전재고": row.get("safe_stock", 0),
                "부족수량": max(to_int(row.get("safe_stock")) - to_int(row.get("available_stock") or row.get("current_stock")), 0),
                "재고상태": row.get("stock_status", ""),
                "리드타임": row.get("inbound_cycle") or 0,
            }
            for row in rows
        ]
    )
    if df.empty:
        st.info("안전재고를 표시할 재고 데이터가 없습니다.")
        return
    st.dataframe(df.sort_values("부족수량", ascending=False), hide_index=True, use_container_width=True)


def render_stock_history_tab() -> None:
    st.markdown('<div class="inventory-tab-title">재고이력</div>', unsafe_allow_html=True)
    cols = st.columns([0.9, 1.8, 3.2], gap="small")
    source_type = cols[0].selectbox("재고처", SOURCE_TYPES, index=2, key="stock_history_source")
    item_options = with_db(lambda db: stock_history_item_options(db, source_type)) or []
    if not item_options:
        st.info("재고이력을 표시할 품목이 없습니다.")
        return
    item_name = cols[1].selectbox("품목", item_options, key="stock_history_item")
    with cols[2]:
        st.caption("기준일자별 현재고, 출고수량, 입고수량 흐름을 확인합니다.")
    rows = with_db(lambda db: stock_history_rows(db, source_type, item_name)) or []
    df = pd.DataFrame(rows)
    if df.empty:
        st.info("선택 품목의 재고이력이 없습니다.")
        return
    chart_df = df.copy()
    chart_df["기준일자"] = pd.to_datetime(chart_df["기준일자"], errors="coerce")
    st.line_chart(chart_df.dropna(subset=["기준일자"]).set_index("기준일자")[["현재고", "출고수량", "입고수량"]])
    st.dataframe(df.sort_values("기준일자", ascending=False), hide_index=True, use_container_width=True)


def render_mrp_tab() -> None:
    st.markdown('<div class="inventory-tab-title">MRP</div>', unsafe_allow_html=True)
    st.caption("생산계획 + BOM + 현재재고 기준으로 자재 필요수량, 부족수량, 발주추천수량을 자동 계산합니다.")
    render_production_plan_editor()

    cols = st.columns([0.9, 0.9, 1.0, 3.4], gap="small")
    source_type = cols[0].selectbox("재고 기준", SOURCE_TYPES, index=2, key="mrp_stock_source")
    dates = fetch_work_dates(source_type)
    work_date = cols[1].date_input("현재고 기준일", value=dates[0] if dates else date.today(), key="mrp_stock_date")
    only_shortage = cols[2].checkbox("부족만 보기", value=True, key="mrp_only_shortage")
    with cols[3]:
        st.empty()

    rows = with_db(lambda db: calculate_mrp_rows(db, source_type, work_date)) or []
    if only_shortage:
        rows = [row for row in rows if row["부족수량"] > 0]
    if not rows:
        st.info("MRP 계산 결과가 없습니다. 생산계획과 해당 완제품 BOM을 먼저 등록하세요.")
        return
    df = pd.DataFrame(rows)
    df.insert(0, "PR생성", False)
    edited = st.data_editor(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={"PR생성": st.column_config.CheckboxColumn("PR생성", default=False)},
        disabled=[column for column in df.columns if column != "PR생성"],
        key="mrp_result_editor",
    )
    cols = st.columns([1.05, 4.95], gap="small")
    with cols[0]:
        if st.button("선택 PR 생성", type="primary", use_container_width=True, key="mrp_create_pr"):
            count = with_db(lambda db: create_pr_from_recommendation_rows(db, edited, "MRP"))
            if count:
                st.success(f"구매요청 생성 완료: {count}건")
                st.rerun()
    with cols[1]:
        st.caption("생성된 PR은 구매관리 > 구매요청(PR)에서 승인 후 RFQ/PO로 진행합니다.")


def render_purchase_recommendation_tab() -> None:
    st.markdown('<div class="inventory-tab-title">발주추천</div>', unsafe_allow_html=True)
    cols = st.columns([0.9, 0.9, 1.1, 3.2], gap="small")
    source_type = cols[0].selectbox("재고처", SOURCE_TYPES, index=2, key="recommend_source")
    dates = fetch_work_dates(source_type)
    work_date = cols[1].date_input("기준일자", value=dates[0] if dates else date.today(), key="recommend_date")
    include_leadtime = cols[2].checkbox("리드타임 고려", value=True, key="recommend_leadtime")
    with cols[3]:
        st.caption("안전재고 이하, 리드타임 중 예상소요, 부족자재를 기준으로 발주 권장 여부를 표시합니다.")

    rows = with_db(lambda db: purchase_recommendation_rows(db, source_type, work_date, include_leadtime)) or []
    if not rows:
        st.info("발주 추천 대상이 없습니다.")
        return
    df = pd.DataFrame(rows)
    df.insert(0, "PR생성", False)
    edited = st.data_editor(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={"PR생성": st.column_config.CheckboxColumn("PR생성", default=False)},
        disabled=[column for column in df.columns if column != "PR생성"],
        key="purchase_recommend_editor",
    )
    cols = st.columns([1.05, 4.95], gap="small")
    with cols[0]:
        if st.button("선택 PR 생성", type="primary", use_container_width=True, key="recommend_create_pr"):
            count = with_db(lambda db: create_pr_from_recommendation_rows(db, edited, "발주추천"))
            if count:
                st.success(f"구매요청 생성 완료: {count}건")
                st.rerun()
    with cols[1]:
        st.caption("중복 미발주 PR이 있는 품목은 추가 생성하지 않습니다.")


def render_material_inventory_tab() -> None:
    st.markdown('<div class="inventory-tab-title">자재/반제품 관리</div>', unsafe_allow_html=True)
    if MaterialInventoryItem is None:
        st.error("자재/반제품 DB를 사용할 수 없습니다.")
        return

    rows = with_db(lambda db: material_inventory_rows(db)) or []
    df = material_to_editor(rows)
    category_options = sorted({row.get("카테고리", "") for row in df.to_dict("records") if row.get("카테고리")})

    metric_cols = st.columns(4, gap="small")
    total_stock = int(df["현재고"].apply(to_int).sum()) if not df.empty else 0
    shortage_count = int((df["부족수량"].apply(to_int) > 0).sum()) if not df.empty else 0
    metric_cols[0].metric("등록 품목", f"{len(df):,}")
    metric_cols[1].metric("총 현재고", f"{total_stock:,}")
    metric_cols[2].metric("부족 품목", f"{shortage_count:,}")
    metric_cols[3].metric("관리 제품", f'{df["연결제품"].replace("", pd.NA).dropna().nunique() if not df.empty else 0:,}')

    category_filter = inventory_single_category_toggle(
        category_options,
        state_key="material_inventory_category_filter",
        widget_key="material_inventory_category_toggle",
    )

    filter_cols = st.columns([1.2, 1.0, 3.6], gap="small")
    keyword = filter_cols[0].text_input("검색", placeholder="품목명 / 품목코드 / 연결제품 / 공급처", key="material_inventory_keyword")
    type_filter = filter_cols[1].selectbox("유형", ["전체", "자재", "반제품"], key="material_inventory_type_filter")
    with filter_cols[2]:
        st.caption("완제품과 연결되는 원부자재, 반제품 재고를 별도 관리합니다. MRP와 구매요청 기준 자료로 사용할 수 있습니다.")

    view_df = filter_material_editor_df(df, keyword, type_filter, category_filter)
    with st.form("material_inventory_form", clear_on_submit=False):
        edited = st.data_editor(
            view_df,
            num_rows="dynamic",
            hide_index=True,
            use_container_width=True,
            height=440,
            column_order=[
                "삭제",
                "카테고리",
                "유형",
                "연결제품",
                "품목코드",
                "품목명",
                "규격",
                "단위",
                "현재고",
                "안전재고",
                "부족수량",
                "보관위치",
                "공급처",
                "리드타임",
                "비고",
            ],
            column_config={
                "ID": None,
                "삭제": st.column_config.CheckboxColumn("삭제", width=62, default=False),
                "카테고리": st.column_config.TextColumn("카테고리", width="medium"),
                "유형": st.column_config.SelectboxColumn("유형", options=["자재", "반제품"], width="small"),
                "연결제품": st.column_config.TextColumn("연결제품", width="large"),
                "품목코드": st.column_config.TextColumn("품목코드", width="medium"),
                "품목명": st.column_config.TextColumn("품목명", width="large"),
                "규격": st.column_config.TextColumn("규격", width="medium"),
                "단위": st.column_config.TextColumn("단위", width="small"),
                "현재고": st.column_config.NumberColumn("현재고", min_value=0, step=1),
                "안전재고": st.column_config.NumberColumn("안전재고", min_value=0, step=1),
                "부족수량": st.column_config.NumberColumn("부족수량", min_value=0, step=1),
                "보관위치": st.column_config.TextColumn("보관위치", width="medium"),
                "공급처": st.column_config.TextColumn("공급처", width="medium"),
                "리드타임": st.column_config.NumberColumn("리드타임", min_value=0, step=1),
            },
            disabled=["ID", "부족수량"],
            key="material_inventory_editor",
        )

        action_cols = st.columns([1.0, 5.0], gap="small")
        with action_cols[0]:
            submitted = st.form_submit_button("자재 저장", type="primary", use_container_width=True)
        with action_cols[1]:
            st.empty()

    if submitted:
        count = with_db(lambda db: save_material_inventory_rows(db, edited))
        st.success(f"자재/반제품 저장 완료: {count or 0}건")
        st.rerun()

    download_cols = st.columns([1.0, 5.0], gap="small")
    with download_cols[0]:
        download_df = edited.drop(columns=["ID", "삭제"], errors="ignore")
        st.download_button(
            "엑셀 다운로드",
            data=dataframe_to_excel(download_df),
            file_name=f"자재_반제품_관리_{date.today():%Y%m%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="material_inventory_download",
        )
    with download_cols[1]:
        st.empty()


def render_warehouse_location_detail_panel(df: pd.DataFrame) -> None:
    if df is None or df.empty or "위치상세" not in df.columns:
        return
    detail_rows = []
    for _, row in df.iterrows():
        detail_text = clean_cell(row.get("위치상세"))
        if not detail_text:
            continue
        for detail in [part.strip() for part in detail_text.split(";") if part.strip()]:
            location, _, quantity = detail.rpartition(" / ")
            detail_rows.append(
                {
                    "상품명": row.get("상품명", ""),
                    "바코드": row.get("바코드", ""),
                    "위치": location or detail,
                    "위치별 수량": quantity.replace("개", "").strip(),
                }
            )
    if not detail_rows:
        return
    detail_df = pd.DataFrame(detail_rows)
    with st.expander(f"적재위치 상세 {len(detail_df):,}건", expanded=False):
        render_plain_inventory_table(detail_df, height=300, empty_message="표시할 적재위치 상세가 없습니다.")


def render_daily_tab(source_type: str, source_label: str | None = None) -> None:
    st.markdown(f'<div class="inventory-tab-title">{source_type} 재고조회</div>', unsafe_allow_html=True)
    today = date.today()
    saved_work_dates = fetch_work_dates(source_type)
    default_work_date = saved_work_dates[0] if saved_work_dates else today
    daily_date_key = f"{source_type}_daily_date"
    pending_daily_date_key = f"{source_type}_daily_date_sync"
    if pending_daily_date_key in st.session_state:
        st.session_state[daily_date_key] = st.session_state.pop(pending_daily_date_key)
    elif daily_date_key not in st.session_state:
        st.session_state[daily_date_key] = default_work_date
    work_date = st.date_input("기준일자", value=default_work_date, key=daily_date_key)

    rows = fetch_master_inventory(source_type, work_date)
    base_df = daily_to_editor(rows)
    if rows and saved_work_dates and work_date not in set(saved_work_dates):
        st.caption(f"{work_date:%Y-%m-%d} 기준 저장된 현재고가 없어 마스터 품목을 0재고로 표시합니다. 최신 저장일자는 {saved_work_dates[0]:%Y-%m-%d}입니다.")
    filters = render_inventory_filters(source_type, base_df)
    filtered_df = apply_inventory_filters(base_df, filters)
    paged_df, page, total_pages = paginate_inventory_df(filtered_df, filters)
    with st.container(key=f"{source_key(source_type)}_daily_summary_metrics"):
        summary_cols = st.columns(6, gap="small")
        summary_cols[0].metric("필터 결과", f"{len(filtered_df):,}개")
        summary_cols[1].metric("정상", f"{int((filtered_df.get('재고상태', pd.Series(dtype=str)) == '정상').sum()):,}개")
        summary_cols[2].metric("주의", f"{int((filtered_df.get('재고상태', pd.Series(dtype=str)) == '주의').sum()):,}개")
        summary_cols[3].metric("부족", f"{int((filtered_df.get('재고상태', pd.Series(dtype=str)) == '부족').sum()):,}개")
        summary_cols[4].metric("품절", f"{int((filtered_df.get('재고상태', pd.Series(dtype=str)) == '품절').sum()):,}개")
        summary_cols[5].metric("가용재고", f"{filtered_df.get('가용재고', pd.Series(dtype=int)).apply(to_int).sum():,}개")
    if source_type == "창고":
        render_warehouse_location_detail_panel(filtered_df)

    download_scope = st.radio(
        "다운로드 범위",
        ["현재 필터 결과 다운로드", "전체 데이터 다운로드"],
        horizontal=True,
        key=f"{source_type}_daily_download_scope_{work_date}",
    )
    download_source_df = filtered_df if download_scope == "현재 필터 결과 다운로드" else base_df
    output_df = download_source_df.drop(columns=["선택"], errors="ignore")
    output_filters = filters if download_scope == "현재 필터 결과 다운로드" else {}
    output_signature = inventory_output_signature(output_df, output_filters)
    upload_preview_key = f"{source_type}_stock_upload_preview_{work_date.isoformat()}"
    uploaded_inventory_key = f"{source_type}_uploaded_inventory_df_{work_date.isoformat()}"
    inventory_preview_df_key = f"{source_type}_inventory_preview_df_{work_date.isoformat()}"
    applied_inventory_df_key = f"{source_type}_applied_inventory_df_{work_date.isoformat()}"
    excluded_inventory_df_key = f"{source_type}_excluded_inventory_df_{work_date.isoformat()}"
    output_payload_key = f"{source_type}_daily_output_payload_{work_date.isoformat()}"
    payload = st.session_state.get(output_payload_key)
    if isinstance(payload, dict) and payload.get("signature") != output_signature:
        st.session_state.pop(output_payload_key, None)
    st.session_state.setdefault("uploaded_inventory_df", None)
    st.session_state.setdefault("inventory_preview_df", None)
    st.session_state.setdefault("applied_inventory_df", None)

    action_cols = st.columns([1.05, 1.05, 1.05, 1.05, 1.05, 1.7], gap="small")
    with action_cols[0]:
        uploaded = st.file_uploader(
            "재고 업로드",
            type=["xlsx", "xls", "csv"],
            key=f"{source_type}_stock_master_upload_{work_date}",
            label_visibility="collapsed",
        )
        upload_mode = st.radio(
            "업로드 방식",
            ["일부 재고 파일", "전체 재고 파일"],
            horizontal=True,
            key=f"{source_type}_stock_upload_mode",
            label_visibility="collapsed",
        )
        if st.button("재고 업로드", key=f"{source_type}_stock_preview_btn_{work_date}", use_container_width=True):
            if uploaded is None:
                st.warning("먼저 재고 파일을 업로드해주세요.")
            else:
                mode = "full" if upload_mode == "전체 재고 파일" else "partial"
                preview = with_db(
                    lambda db: services.prepare_stock_upload_preview(
                        db,
                        source_type,
                        work_date,
                        uploaded.getvalue(),
                        uploaded.name,
                        mode,
                    )
                )
                if preview:
                    st.session_state[upload_preview_key] = preview
                    preview_df = stock_preview_display_dataframe(preview)
                    uploaded_df = pd.DataFrame(preview.get("debug", {}).get("normalized_sample", []))
                    st.session_state[uploaded_inventory_key] = uploaded_df
                    st.session_state[inventory_preview_df_key] = preview_df
                    st.session_state["uploaded_inventory_df"] = uploaded_df
                    st.session_state["inventory_preview_df"] = preview_df
                    st.session_state["applied_inventory_df"] = None
                    st.session_state["excluded_inventory_df"] = None
    with action_cols[1]:
        if st.button("안전재고 계산", key=f"{source_type}_safe", use_container_width=True):
            show_result(with_db(lambda db: result("안전재고 계산 완료", services.calculate_safe_stock(db, source_type, work_date))))
    with action_cols[2]:
        if st.button("구매이력 동기화", key=f"{source_type}_purchase_metric_sync", use_container_width=True):
            show_result(
                with_db(
                    lambda db: result(
                        "구매이력 기준 리드타임 동기화 완료",
                        services.sync_purchase_metrics_to_inventory(db, source_type, work_date),
                    )
                )
            )
            st.rerun()
    with action_cols[3]:
        payload = st.session_state.get(output_payload_key, {})
        if st.button("PDF 준비", key=f"{source_type}_daily_pdf_prepare_{work_date}", use_container_width=True):
            try:
                payload = {
                    **payload,
                    "signature": output_signature,
                    "pdf": inventory_pdf_bytes(output_df, source_type, work_date, output_filters),
                    "pdf_filters": output_filters,
                    "pdf_count": len(output_df),
                }
                st.session_state[output_payload_key] = payload
            except Exception as exc:
                st.error(f"PDF 생성 준비 중 오류가 발생했습니다: {exc}")
        if isinstance(payload, dict) and payload.get("pdf"):
            st.download_button(
                "PDF 다운로드",
                data=payload["pdf"],
                file_name=inventory_file_name("pdf", output_df, output_filters),
                mime="application/pdf",
                use_container_width=True,
                key=f"{source_type}_daily_pdf_download_{work_date}",
                on_click=record_inventory_output,
                args=(source_type, work_date, "PDF", payload.get("pdf_filters", output_filters), int(payload.get("pdf_count") or len(output_df))),
            )
    with action_cols[4]:
        payload = st.session_state.get(output_payload_key, {})
        if st.button("엑셀 준비", key=f"{source_type}_daily_excel_prepare_{work_date}", use_container_width=True):
            payload = {
                **payload,
                "signature": output_signature,
                "excel": dataframe_to_excel(output_df),
                "excel_filters": output_filters,
                "excel_count": len(output_df),
            }
            st.session_state[output_payload_key] = payload
        if isinstance(payload, dict) and payload.get("excel"):
            st.download_button(
                "엑셀 다운로드",
                data=payload["excel"],
                file_name=inventory_file_name("xlsx", output_df, output_filters),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key=f"{source_type}_daily_download_{work_date}",
                on_click=record_inventory_output,
                args=(source_type, work_date, "EXCEL", payload.get("excel_filters", output_filters), int(payload.get("excel_count") or len(output_df))),
            )
    with action_cols[5]:
        st.caption(f"전체 {len(base_df):,}개 중 {len(filtered_df):,}개 표시 · {page}/{total_pages} 페이지")

    preview = st.session_state.get(upload_preview_key)
    if preview:
        render_stock_upload_preview(
            source_type,
            work_date,
            upload_preview_key,
            preview,
            inventory_preview_df_key,
            applied_inventory_df_key,
            excluded_inventory_df_key,
        )
    applied_df = st.session_state.get(applied_inventory_df_key)
    if isinstance(applied_df, pd.DataFrame) and not applied_df.empty:
        st.markdown("#### 현재고 반영 결과")
        render_inventory_visible_table(applied_df, height=520)
    excluded_df = st.session_state.get(excluded_inventory_df_key)
    if isinstance(excluded_df, pd.DataFrame) and not excluded_df.empty:
        with st.expander(f"반영 제외 {len(excluded_df):,}건 및 사유", expanded=False):
            render_inventory_visible_table(excluded_df, height=260)

    if filtered_df.empty:
        st.info("현재 필터 조건에 해당하는 재고 데이터가 없습니다.")
        if st.button("필터 전체 초기화", key=f"{source_type}_daily_empty_reset_{work_date}", use_container_width=True):
            reset_inventory_filters(source_key(source_type))
            st.rerun()
    else:
        display_df = paged_df.drop(columns=["선택"], errors="ignore")
        render_inventory_visible_table(display_df, height=520)
        nav_prev, nav_info, nav_next, spacer = st.columns([0.8, 1.0, 0.8, 4.6], gap="small")
        filter_key = source_key(source_type)
        with nav_prev:
            if st.button("이전", key=f"{source_type}_daily_page_prev_{work_date}", disabled=page <= 1, use_container_width=True):
                st.session_state[f"{filter_key}_page"] = max(page - 1, 1)
                st.rerun()
        with nav_info:
            st.caption(f"{page:,} / {total_pages:,} 페이지")
        with nav_next:
            if st.button("다음", key=f"{source_type}_daily_page_next_{work_date}", disabled=page >= total_pages, use_container_width=True):
                st.session_state[f"{filter_key}_page"] = min(page + 1, total_pages)
                st.rerun()
        with spacer:
            st.empty()


def render_inbound_tab(source_type: str) -> None:
    st.markdown(f'<div class="inventory-tab-title">{source_type} 입고내역</div>', unsafe_allow_html=True)
    with st.container(key=f"{source_key(source_type)}_inbound_import_panel"):
        render_inventory_html(
            """
            <div class="inventory-update-heading">
                <div>
                    <h2>입고내역 파일 반영</h2>
                    <p>성현물류 거래명세서는 품목명이 마스터 상품명과 같으면 해당 상품 입고로 자동 매칭됩니다.</p>
                </div>
            </div>
            """
        )
        upload_col, import_col, apply_col, download_col, spacer = st.columns([2.2, 0.85, 0.95, 0.95, 2.4], gap="small")
        with upload_col:
            uploaded = st.file_uploader(
                "입고내역 엑셀 업로드",
                type=["xlsx", "xls", "html"],
                key=f"{source_type}_inbound_file",
                label_visibility="collapsed",
            )
        with import_col:
            st.write("")
            if st.button("파일 반영", key=f"{source_type}_inbound_import", use_container_width=True):
                if uploaded is None:
                    st.warning("먼저 입고내역 파일을 업로드하세요.")
                else:
                    outcome = with_db(lambda db: import_upload_result("입고내역 파일 반영 완료", services.import_inbound_excel(db, source_type, uploaded.getvalue())))
                    if outcome and outcome.get("ok", True):
                        clear_inventory_editor_buffer(f"{source_type}_inbound_editor_buffer")
                    show_result(outcome)
        with apply_col:
            apply_date = st.date_input("반영 기준일자", value=date.today(), key=f"{source_type}_inbound_apply_date")
            if st.button("재고 반영", key=f"{source_type}_inbound_apply", type="primary", use_container_width=True):
                outcome = with_db(lambda db: result("재고현황 반영 완료", services.apply_inbound_to_stock(db, source_type, apply_date)))
                if outcome and outcome.get("ok", True):
                    clear_inventory_data_caches()
                    clear_inventory_editor_buffer(f"{source_type}_inbound_editor_buffer")
                show_result(outcome)
        with download_col:
            st.write("")
            download_data = inbound_excel(source_type)
            st.download_button(
                "엑셀 다운로드",
                data=download_data or b"",
                file_name=f"{source_type}_입고내역.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key=f"{source_type}_inbound_download",
            )
        with spacer:
            st.empty()

    df = inbound_to_editor(fetch_inbound(source_type))
    inbound_buffer_key = f"{source_type}_inbound_editor_buffer"
    st.session_state[inbound_buffer_key] = df
    display_df = df.drop(columns=["삭제"], errors="ignore")
    render_plain_inventory_table(display_df, height=360, empty_message="입고내역 데이터가 없습니다.")
    with st.form(key=f"{source_type}_inbound_editor_form", clear_on_submit=False):
        if st.form_submit_button("입고내역 저장", type="primary", use_container_width=True):
            rows = inbound_payload(df, source_type)
            outcome = with_db(lambda db: result("입고내역 저장 완료", services.bulk_save_inbound(db, source_type, rows)))
            if outcome and outcome.get("ok", True):
                clear_inventory_editor_buffer(inbound_buffer_key)
            else:
                st.session_state[inbound_buffer_key] = df
            show_result(outcome)


def render_outbound_tab(source_type: str) -> None:
    st.markdown(f'<div class="inventory-tab-title">{source_type} 출고내역</div>', unsafe_allow_html=True)
    source_token = source_key(source_type)
    linked_item = clean_cell(st.session_state.get(f"{source_type}_outbound_item_filter"))
    default_end = parse_date_value(st.session_state.get(f"{source_type}_outbound_end")) or date.today()
    default_start = parse_date_value(st.session_state.get(f"{source_type}_outbound_start")) or (default_end - timedelta(days=30))
    filter_cols = st.columns([1.35, 0.95, 0.95, 2.7], gap="small")
    item_filter = filter_cols[0].text_input("품목 필터", value=linked_item, placeholder="상품명 / SKU / 바코드", key=f"{source_type}_outbound_item_filter")
    start_date = filter_cols[1].date_input("시작일", value=default_start, key=f"{source_type}_outbound_start")
    end_date = filter_cols[2].date_input("종료일", value=default_end, key=f"{source_type}_outbound_end")
    with filter_cols[3]:
        st.caption("출고수량이 있는 기준일자별 품목 이력을 표시합니다.")
    if start_date > end_date:
        st.warning("시작일은 종료일보다 늦을 수 없습니다.")
        return

    keyword = clean_cell(item_filter)
    page_size = 50
    signature = f"{source_type}|{start_date.isoformat()}|{end_date.isoformat()}|{keyword}"
    signature_key = f"{source_token}_outbound_filter_signature"
    page_key = f"{source_token}_outbound_page"
    download_key = f"{source_token}_outbound_download_payload"
    if st.session_state.get(signature_key) != signature:
        st.session_state[signature_key] = signature
        st.session_state[page_key] = 1
        st.session_state.pop(download_key, None)
    page = max(int(st.session_state.get(page_key, 1) or 1), 1)

    timings: dict[str, float] = {}
    db_started = perf_counter()

    def load_page(db):
        total = services.count_outbound(db, source_type, start_date=start_date, end_date=end_date, keyword=keyword)
        if total <= 0:
            return {"total": 0, "rows": [], "query_count": 1}
        max_page = max(ceil(total / page_size), 1)
        current_page = min(page, max_page)
        current_offset = (current_page - 1) * page_size
        page_rows = services.list_outbound(
            db,
            source_type,
            start_date=start_date,
            end_date=end_date,
            keyword=keyword,
            limit=page_size,
            offset=current_offset,
        )
        return {"total": total, "rows": [services.daily_to_dict(row) for row in page_rows], "page": current_page, "query_count": 2}

    payload = with_db(load_page)
    timings["DB 조회"] = (perf_counter() - db_started) * 1000
    if payload is None:
        st.error("출고내역을 불러오지 못했습니다.")
        return

    total_count = int(payload.get("total") or 0)
    page = int(payload.get("page") or page)
    st.session_state[page_key] = page
    df_started = perf_counter()
    page_df = pd.DataFrame(
        [
            {
                "기준일자": row.get("work_date"),
                "SKU": row.get("product_code", ""),
                "바코드": row.get("barcode", ""),
                "상품명": row.get("product_name", ""),
                "출고수량": row.get("outbound_qty", 0),
                "재고상태": row.get("stock_status", ""),
            }
            for row in payload.get("rows", [])
        ]
    )
    if not page_df.empty:
        page_df["기준일자"] = pd.to_datetime(page_df["기준일자"], errors="coerce").dt.date
    timings["DataFrame 가공"] = (perf_counter() - df_started) * 1000

    pagination_started = perf_counter()
    total_pages = max(ceil(total_count / page_size), 1)
    timings["pagination"] = (perf_counter() - pagination_started) * 1000

    download_cols = st.columns([0.95, 0.95, 4.0], gap="small")
    prepare_download = download_cols[0].button(
        "출고내역 엑셀 준비",
        key=f"{source_token}_outbound_download_prepare",
        disabled=total_count <= 0,
        use_container_width=True,
    )
    if prepare_download:
        export_started = perf_counter()

        def load_export(db):
            return [
                services.daily_to_dict(row)
                for row in services.list_outbound(
                    db,
                    source_type,
                    start_date=start_date,
                    end_date=end_date,
                    keyword=keyword,
                    limit=None,
                    offset=0,
                )
            ]

        export_rows = with_db(load_export) or []
        export_df = pd.DataFrame(
            [
                {
                    "기준일자": row.get("work_date"),
                    "SKU": row.get("product_code", ""),
                    "바코드": row.get("barcode", ""),
                    "상품명": row.get("product_name", ""),
                    "출고수량": row.get("outbound_qty", 0),
                    "재고상태": row.get("stock_status", ""),
                }
                for row in export_rows
            ]
        )
        if not export_df.empty:
            export_df["기준일자"] = pd.to_datetime(export_df["기준일자"], errors="coerce").dt.date
        st.session_state[download_key] = {
            "signature": signature,
            "bytes": dataframe_to_excel(export_df),
            "count": len(export_df),
            "elapsed_ms": (perf_counter() - export_started) * 1000,
        }
    prepared_download = st.session_state.get(download_key)
    if prepared_download and prepared_download.get("signature") == signature:
        download_cols[1].download_button(
            "출고내역 엑셀 다운로드",
            data=prepared_download.get("bytes", b""),
            file_name=f"{source_type}_출고내역_{start_date:%Y%m%d}_{end_date:%Y%m%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key=f"{source_token}_outbound_download",
        )
        download_cols[2].caption(f"다운로드 준비 완료: {int(prepared_download.get('count') or 0):,}건")

    if total_count <= 0:
        st.info("해당 기간에 출고내역이 없습니다.")
    else:
        render_started = perf_counter()
        st.caption(f"조회 결과 {total_count:,}건 중 {page:,}/{total_pages:,}페이지, 현재 화면 {len(page_df):,}건")
        render_plain_inventory_table(page_df, height=420, empty_message="해당 기간에 출고내역이 없습니다.")
        timings["UI 렌더링 준비"] = (perf_counter() - render_started) * 1000

        pager_cols = st.columns([0.55, 0.8, 0.55, 5.0], gap="small")
        if pager_cols[0].button("이전", key=f"{source_token}_outbound_prev", disabled=page <= 1, use_container_width=True):
            st.session_state[page_key] = max(page - 1, 1)
            st.rerun()
        pager_cols[1].caption(f"{page} / {total_pages}")
        if pager_cols[2].button("다음", key=f"{source_token}_outbound_next", disabled=page >= total_pages, use_container_width=True):
            st.session_state[page_key] = min(page + 1, total_pages)
            st.rerun()

    with st.expander("출고내역 처리 로그", expanded=False):
        st.write(
            {
                "DB 조회": f"{timings.get('DB 조회', 0):.1f} ms",
                "수신 row": len(payload.get("rows", [])),
                "필터 결과 row": total_count,
                "상품마스터 조회": "별도 호출 없음",
                "JOIN/가공": f"{timings.get('DataFrame 가공', 0):.1f} ms",
                "필터링": "DB query 단계 적용",
                "pagination": f"{timings.get('pagination', 0):.1f} ms",
                "UI 렌더링 준비": f"{timings.get('UI 렌더링 준비', 0):.1f} ms",
                "DB 조회 건수": int(payload.get("query_count") or 0),
            }
        )


def render_inventory_dashboard_tab(source_type: str) -> None:
    st.markdown(f'<div class="inventory-tab-title">{source_type} 대시보드</div>', unsafe_allow_html=True)
    dates = fetch_work_dates(source_type)
    default_date = dates[0] if dates else date.today()
    control_cols = st.columns([1.0, 4.0], gap="small")
    with control_cols[0]:
        work_date = st.date_input("기준일자", value=default_date, key=f"{source_type}_dashboard_date")
    with control_cols[1]:
        st.empty()

    payload = with_db(
        lambda db: {
            "summary": services.dashboard_summary(db, work_date, source_type),
            "charts": services.dashboard_chart(db, work_date, source_type),
        }
    ) or {"summary": {}, "charts": {}}
    summary = payload.get("summary", {})
    charts = payload.get("charts", {})

    metric_cols = st.columns(6, gap="small")
    metrics = [
        ("전체 SKU", summary.get("sku_count", 0)),
        ("총 현재고", summary.get("current_stock", 0)),
        ("가용재고", summary.get("available_stock", 0)),
        ("출고수량", summary.get("outbound_qty", 0)),
        ("재고부족 SKU", summary.get("need_inbound_count", 0)),
        ("품절 SKU", summary.get("soldout_count", 0)),
    ]
    for column, (label, value) in zip(metric_cols, metrics):
        column.metric(label, f"{int(value or 0):,}")

    trend_cols = st.columns(2, gap="small")
    with trend_cols[0]:
        st.markdown("#### 날짜별 재고추이")
        render_inventory_line_chart(charts.get("stock_trend", []), "현재고")
    with trend_cols[1]:
        st.markdown("#### 날짜별 출고추이")
        render_inventory_line_chart(charts.get("outbound_trend", []), "출고수량")

    category_cols = st.columns(3, gap="small")
    with category_cols[0]:
        st.markdown("#### 카테고리별 현재고")
        render_inventory_bar_chart(charts.get("stock_by_category", []), "현재고")
    with category_cols[1]:
        st.markdown("#### 카테고리별 출고수량")
        render_inventory_bar_chart(charts.get("outbound_by_category", []), "출고수량")
    with category_cols[2]:
        st.markdown("#### 입고필요 상품 TOP 10")
        top_df = pd.DataFrame(charts.get("need_inbound_top10", []))
        if top_df.empty:
            st.info("입고필요 상품이 없습니다.")
        else:
            top_df = top_df.rename(
                columns={
                    "product_name": "상품명",
                    "current_stock": "현재고",
                    "safe_stock": "안전재고",
                }
            )
            st.dataframe(top_df, hide_index=True, use_container_width=True)

    st.divider()
    st.markdown("#### 입고내역 대시보드")
    inbound_df = inbound_to_editor(fetch_inbound(source_type)).drop(columns=["삭제"], errors="ignore")
    if inbound_df.empty:
        st.info("입고내역 데이터가 없습니다.")
        return

    inbound_df["입고일자"] = pd.to_datetime(inbound_df["입고일자"], errors="coerce")
    inbound_df["입고수량"] = inbound_df["입고수량"].apply(to_int)
    valid_df = inbound_df.dropna(subset=["입고일자"])
    total_qty = int(valid_df["입고수량"].sum()) if not valid_df.empty else 0

    metric_cols = st.columns(4, gap="small")
    metric_cols[0].metric("총 입고수량", f"{total_qty:,}")
    metric_cols[1].metric("입고 상품 수", f'{valid_df["상품명"].nunique():,}')
    metric_cols[2].metric("입고 건수", f"{len(valid_df):,}")
    metric_cols[3].metric("입고구분 수", f'{valid_df["입고구분"].replace("", pd.NA).dropna().nunique():,}')

    chart_cols = st.columns(2, gap="small")
    with chart_cols[0]:
        st.markdown("#### 월별 입고수량")
        monthly = valid_df.assign(월=valid_df["입고일자"].dt.strftime("%Y-%m")).groupby("월", as_index=False)["입고수량"].sum()
        if monthly.empty:
            st.info("표시할 데이터가 없습니다.")
        else:
            st.bar_chart(monthly.set_index("월")["입고수량"])
    with chart_cols[1]:
        st.markdown("#### 입고구분별 비중")
        by_type = valid_df.assign(입고구분=valid_df["입고구분"].replace("", "미분류")).groupby("입고구분", as_index=False)["입고수량"].sum()
        if by_type.empty:
            st.info("표시할 데이터가 없습니다.")
        else:
            st.bar_chart(by_type.set_index("입고구분")["입고수량"])

    table_cols = st.columns(2, gap="small")
    with table_cols[0]:
        st.markdown("#### 상품별 입고수량 TOP")
        top_df = valid_df.groupby(["상품명"], as_index=False)["입고수량"].sum().sort_values("입고수량", ascending=False).head(10)
        st.dataframe(top_df, hide_index=True, use_container_width=True)
    with table_cols[1]:
        st.markdown("#### 최근 입고내역")
        recent_cols = ["입고일자", "SKU", "바코드", "상품명", "입고수량", "입고구분", "비고"]
        recent_df = valid_df.sort_values("입고일자", ascending=False)[recent_cols].head(10)
        recent_df["입고일자"] = recent_df["입고일자"].dt.date
        st.dataframe(recent_df, hide_index=True, use_container_width=True)


def render_inventory_line_chart(rows: list[dict], label: str) -> None:
    if not rows:
        st.info("표시할 데이터가 없습니다.")
        return
    df = pd.DataFrame(rows)
    if df.empty or "date" not in df.columns or "value" not in df.columns:
        st.info("표시할 데이터가 없습니다.")
        return
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    if df.empty:
        st.info("표시할 데이터가 없습니다.")
        return
    st.line_chart(df.set_index("date")["value"].rename(label))


def render_inventory_bar_chart(rows: list[dict], label: str) -> None:
    if not rows:
        st.info("표시할 데이터가 없습니다.")
        return
    df = pd.DataFrame(rows)
    if df.empty or "label" not in df.columns or "value" not in df.columns:
        st.info("표시할 데이터가 없습니다.")
        return
    df = df.rename(columns={"label": "구분", "value": label})
    df[label] = df[label].apply(to_int)
    df["구분"] = df["구분"].replace("", "미분류").astype(str)
    df = df.groupby("구분", as_index=False)[label].sum().sort_values(label, ascending=True)
    df = df[df[label] > 0]
    if df.empty:
        st.info(f"표시할 {label} 데이터가 없습니다.")
        return
    if px is None:
        st.bar_chart(df.set_index("구분")[label])
        return

    height = min(520, max(260, 34 * len(df) + 80))
    fig = px.bar(
        df,
        x=label,
        y="구분",
        orientation="h",
        text=label,
        color_discrete_sequence=["#0B74C8"],
    )
    fig.update_traces(
        texttemplate="%{text:,}",
        textposition="outside",
        cliponaxis=False,
        hovertemplate="%{y}<br>" + label + ": %{x:,}<extra></extra>",
    )
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=28, t=4, b=12),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis_title="",
        yaxis_title="",
        bargap=0.28,
        font=dict(size=11, color="#26384A"),
    )
    fig.update_xaxes(showgrid=True, gridcolor="#E2DCD4", tickformat=",")
    fig.update_yaxes(automargin=True)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


@st.cache_data(ttl=30, show_spinner=False)
def cached_fetch_work_dates(source_type: str) -> list[date]:
    data = with_db(lambda db: services.list_work_dates(db, source_type)) or []
    dates = pd.to_datetime(data, errors="coerce")
    return [value.date() for value in dates if not pd.isna(value)]


def fetch_work_dates(source_type: str) -> list[date]:
    return cached_fetch_work_dates(source_type)


def fetch_daily(source_type: str, work_date: date) -> list[dict]:
    return with_db(lambda db: [services.daily_to_dict(row) for row in services.list_daily(db, source_type, work_date)]) or []


def inventory_normalize_barcode(value) -> str:
    if services is not None and hasattr(services, "normalize_barcode_text"):
        return services.normalize_barcode_text(value)
    return clean_cell(value)


def build_master_category_lookup(db, source_type: str) -> dict[str, dict[str, str]]:
    by_sku: dict[str, str] = {}
    by_barcode: dict[str, str] = {}
    by_barcode_name: dict[str, str] = {}
    by_name: dict[str, str] = {}
    products = services.list_product_master(db, source_type, "", "전체") if services is not None else []
    for product in products or []:
        sku = clean_cell(getattr(product, "sku", ""))
        barcode = inventory_normalize_barcode(getattr(product, "barcode", ""))
        product_name = clean_cell(getattr(product, "product_name", ""))
        category = clean_cell(getattr(product, "large_category", "") or getattr(product, "category", ""))
        if not category:
            continue
        if sku:
            by_sku.setdefault(sku, category)
        if barcode:
            by_barcode.setdefault(barcode, category)
        if barcode and product_name:
            by_barcode_name.setdefault(f"{barcode}|{product_name}", category)
        if product_name:
            by_name.setdefault(product_name, category)
    return {
        "sku": by_sku,
        "barcode": by_barcode,
        "barcode_name": by_barcode_name,
        "name": by_name,
    }


def apply_master_categories(rows: list[dict], lookup: dict[str, dict[str, str]]) -> list[dict]:
    normalized = []
    for row in rows:
        next_row = dict(row)
        sku = clean_cell(next_row.get("product_code") or next_row.get("sku"))
        barcode = inventory_normalize_barcode(next_row.get("barcode"))
        product_name = clean_cell(next_row.get("product_name"))
        existing_category = clean_cell(next_row.get("category"))
        master_category = (
            lookup["sku"].get(sku)
            or lookup["barcode"].get(barcode)
            or lookup["barcode_name"].get(f"{barcode}|{product_name}")
            or lookup["name"].get(product_name)
        )
        next_row["category"] = master_category or existing_category or "미분류"
        normalized.append(next_row)
    return normalized


@st.cache_data(ttl=30, show_spinner=False)
def cached_fetch_master_inventory(source_type: str, work_date_iso: str) -> list[dict]:
    target_date = parse_date_or_today(work_date_iso)

    def load_rows(db):
        return services.master_based_inventory_rows(db, source_type, target_date)

    return with_db(load_rows) or []


def fetch_master_inventory(source_type: str, work_date: date) -> list[dict]:
    return cached_fetch_master_inventory(source_type, work_date.isoformat())


def clear_inventory_data_caches() -> None:
    cached_fetch_work_dates.clear()
    cached_fetch_master_inventory.clear()


def fetch_master_category_options(source_type: str, df: pd.DataFrame) -> list[str]:
    table_categories = df.get("카테고리", pd.Series(dtype=str)).dropna().unique() if df is not None else []
    if len(table_categories):
        return sorted({clean_cell(value) for value in table_categories if clean_cell(value)})
    master_categories = with_db(lambda db: services.list_product_master_categories(db, source_type)) or []
    return sorted({clean_cell(value) for value in master_categories if clean_cell(value)})


def inventory_output_signature(df: pd.DataFrame, filters: dict) -> tuple:
    if df is None or df.empty:
        row_marker = ("empty", 0)
    else:
        sample_columns = [column for column in ("바코드", "상품명", "현재고", "가용재고") if column in df.columns]
        sample = tuple(tuple(clean_cell(value) for value in row) for row in df[sample_columns].head(5).fillna("").to_numpy())
        row_marker = (len(df), sample)
    filter_marker = tuple(sorted((str(key), str(value)) for key, value in (filters or {}).items()))
    return row_marker, filter_marker


def inventory_filter_multiselect(container, label: str, options: list[str], key: str) -> list[str]:
    if not options:
        st.session_state[key] = []
        container.text_input(label, value="데이터 없음", disabled=True, key=f"{key}_empty")
        return []
    return container.multiselect(label, options, key=key, placeholder="선택하세요")


def inventory_single_category_toggle(options: list[str], state_key: str, widget_key: str) -> str:
    choices = ["전체", *options]
    current = clean_cell(st.session_state.get(state_key)) or "전체"
    if current not in choices:
        current = "전체"

    if not options:
        st.session_state[state_key] = "전체"
        choices = ["전체"]
        current = "전체"

    st.session_state.pop(f"{widget_key}_open", None)
    if clean_cell(st.session_state.get(widget_key)) not in choices:
        st.session_state[widget_key] = current

    with st.container(key=f"{widget_key}_select"):
        selected = st.selectbox(
            "카테고리 선택",
            choices,
            index=choices.index(current),
            key=widget_key,
            disabled=not options,
        )
    selected = clean_cell(selected) or "전체"
    st.session_state[state_key] = selected
    return selected


def inventory_category_toggle(options: list[str], filter_key: str) -> list[str]:
    state_key = f"{filter_key}_category_toggle"
    filter_state_key = f"{filter_key}_category_filter"
    selected_values = st.session_state.get(filter_state_key) or []
    previous = clean_cell(st.session_state.get(state_key)) or (selected_values[0] if selected_values else "전체")
    selected = inventory_single_category_toggle(options, state_key, f"{filter_key}_category_toggle_widget")
    if selected != previous:
        st.session_state[f"{filter_key}_page"] = 1
    categories = [] if selected == "전체" else [selected]
    st.session_state[filter_state_key] = categories
    return categories


def render_inventory_filters(source_type: str, df: pd.DataFrame) -> dict:
    category_options = fetch_master_category_options(source_type, df)
    supplier_options = sorted([value for value in df.get("업체명", pd.Series(dtype=str)).dropna().unique() if clean_cell(value)])
    manager_options = sorted([value for value in df.get("담당자", pd.Series(dtype=str)).dropna().unique() if clean_cell(value)])
    status_options = ["정상", "주의", "부족", "품절"]
    filter_key = source_key(source_type)
    defaults = {
        "search": "",
        "category_filter": [],
        "supplier_filter": [],
        "manager_filter": [],
        "status_filter": [],
        "stock_presence": "전체",
        "inbound_expected": False,
        "outbound_expected": False,
        "below_safe": False,
        "lead_min": 0,
        "lead_max": 0,
        "sort_column": "카테고리",
        "sort_order": "오름차순",
        "page_size": 30,
        "page": 1,
    }
    for suffix, value in defaults.items():
        st.session_state.setdefault(f"{filter_key}_{suffix}", value)

    with st.container(key=f"inventory_filter_{filter_key}_panel"):
        categories = inventory_category_toggle(category_options, filter_key)

        basic_cols = st.columns([1.55, 1.0, 0.7, 0.7, 2.05], gap="small")
        search = basic_cols[0].text_input("통합검색", placeholder="바코드 / 상품명 / 업체명 / 담당자", key=f"{filter_key}_search")
        suppliers = inventory_filter_multiselect(basic_cols[1], "업체명", supplier_options, key=f"{filter_key}_supplier_filter")
        statuses = inventory_filter_multiselect(basic_cols[2], "재고상태", status_options, key=f"{filter_key}_status_filter")
        with basic_cols[3]:
            st.write("")
            if st.button("검색", key=f"{filter_key}_search_button", type="primary", use_container_width=True):
                st.session_state[f"{filter_key}_page"] = 1
                st.rerun()
        with basic_cols[4]:
            st.write("")
            if st.button("초기화", key=f"{filter_key}_filter_reset", use_container_width=True):
                reset_inventory_filters(filter_key)
                st.rerun()

        with st.expander("고급 필터 ▼", expanded=False):
            adv_cols = st.columns(4, gap="small")
            managers = inventory_filter_multiselect(adv_cols[0], "담당자", manager_options, key=f"{filter_key}_manager_filter")
            stock_presence = adv_cols[1].selectbox("현재고 보유 여부", ["전체", "보유", "미보유"], key=f"{filter_key}_stock_presence")
            inbound_expected = adv_cols[2].checkbox("입고예정", key=f"{filter_key}_inbound_expected")
            outbound_expected = adv_cols[3].checkbox("출고예정", key=f"{filter_key}_outbound_expected")

            adv_cols2 = st.columns(4, gap="small")
            below_safe = adv_cols2[0].checkbox("안전재고 이하", key=f"{filter_key}_below_safe")
            lead_min = adv_cols2[1].number_input("리드타임 최소", min_value=0, step=1, key=f"{filter_key}_lead_min")
            lead_max = adv_cols2[2].number_input("리드타임 최대", min_value=0, step=1, key=f"{filter_key}_lead_max")
            sort_column = adv_cols2[3].selectbox("정렬 컬럼", [column for column in DAILY_COLUMNS if column != "선택"], key=f"{filter_key}_sort_column")

            adv_cols3 = st.columns([1, 1, 2, 2], gap="small")
            sort_order = adv_cols3[0].selectbox("정렬", ["오름차순", "내림차순"], key=f"{filter_key}_sort_order")
            page_size = adv_cols3[1].selectbox("페이지당 표시", [15, 30, 50, 100], key=f"{filter_key}_page_size")

        badges = active_inventory_filter_badges(
            search,
            categories,
            suppliers,
            managers,
            statuses,
            stock_presence,
            inbound_expected,
            outbound_expected,
            below_safe,
            lead_min,
            lead_max,
        )
        render_inventory_filter_badges(filter_key, badges)

    if st.session_state.get(f"{filter_key}_last_page_size") != page_size:
        st.session_state[f"{filter_key}_page"] = 1
    st.session_state[f"{filter_key}_last_page_size"] = page_size
    return {
        "categories": categories,
        "suppliers": suppliers,
        "managers": managers,
        "statuses": statuses,
        "search": search,
        "stock_presence": stock_presence,
        "inbound_expected": inbound_expected,
        "outbound_expected": outbound_expected,
        "below_safe": below_safe,
        "lead_min": int(lead_min or 0),
        "lead_max": int(lead_max or 0),
        "sort_column": sort_column,
        "sort_order": sort_order,
        "page_size": int(page_size),
        "page": int(st.session_state.get(f"{filter_key}_page", 1)),
    }


def apply_inventory_filters(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    if df.empty:
        return df
    filtered = df.copy()
    categories = filters.get("categories") or []
    suppliers = filters.get("suppliers") or []
    managers = filters.get("managers") or []
    statuses = filters.get("statuses") or []
    search = clean_cell(filters.get("search")).lower()
    if categories:
        filtered = filtered[filtered["카테고리"].isin(categories)]
    if suppliers:
        filtered = filtered[filtered["업체명"].isin(suppliers)]
    if managers:
        filtered = filtered[filtered["담당자"].isin(managers)]
    if statuses:
        filtered = filtered[filtered["재고상태"].isin(statuses)]
    if search:
        search_columns = ["바코드", "상품명", "업체명", "담당자"]
        search_text = filtered[search_columns].astype(str).agg(" ".join, axis=1).str.lower()
        filtered = filtered[search_text.str.contains(re.escape(search), na=False)]
    if filters.get("stock_presence") == "보유":
        filtered = filtered[filtered["현재고"].apply(to_int) > 0]
    elif filters.get("stock_presence") == "미보유":
        filtered = filtered[filtered["현재고"].apply(to_int) <= 0]
    if filters.get("inbound_expected") and "입고예정" in filtered.columns:
        filtered = filtered[filtered["입고예정"].apply(to_int) > 0]
    if filters.get("outbound_expected"):
        filtered = filtered[filtered["출고예정"].apply(to_int) > 0]
    if filters.get("below_safe") and {"현재고", "안전재고"}.issubset(filtered.columns):
        filtered = filtered[filtered["현재고"].apply(to_int) <= filtered["안전재고"].apply(to_int)]
    lead_min = int(filters.get("lead_min") or 0)
    lead_max = int(filters.get("lead_max") or 0)
    if lead_min:
        filtered = filtered[filtered["리드타임"].apply(to_int) >= lead_min]
    if lead_max:
        filtered = filtered[filtered["리드타임"].apply(to_int) <= lead_max]
    sort_column = filters.get("sort_column") if filters.get("sort_column") in filtered.columns else "카테고리"
    ascending = filters.get("sort_order") != "내림차순"
    return filtered.sort_values([sort_column, "상품명", "바코드"], ascending=[ascending, True, True], kind="stable").reset_index(drop=True)


def reset_inventory_filters(filter_key: str) -> None:
    for suffix in [
        "search",
        "category_filter",
        "supplier_filter",
        "manager_filter",
        "status_filter",
        "stock_presence",
        "inbound_expected",
        "outbound_expected",
        "below_safe",
        "lead_min",
        "lead_max",
        "sort_column",
        "sort_order",
        "page_size",
        "page",
        "last_page_size",
        "category_toggle",
        "category_toggle_widget_open",
    ]:
        st.session_state.pop(f"{filter_key}_{suffix}", None)


def active_inventory_filter_badges(search, categories, suppliers, managers, statuses, stock_presence, inbound_expected, outbound_expected, below_safe, lead_min, lead_max) -> list[tuple[str, str, str | None]]:
    badges: list[tuple[str, str, str | None]] = []
    if clean_cell(search):
        badges.append(("search", f"검색 : {search}", None))
    for code, values, label in [
        ("category_filter", categories, "카테고리"),
        ("supplier_filter", suppliers, "업체명"),
        ("manager_filter", managers, "담당자"),
        ("status_filter", statuses, "재고상태"),
    ]:
        for value in values or []:
            badges.append((code, f"{label} : {value}", value))
    if stock_presence != "전체":
        badges.append(("stock_presence", f"현재고 : {stock_presence}", None))
    if inbound_expected:
        badges.append(("inbound_expected", "입고예정 있음", None))
    if outbound_expected:
        badges.append(("outbound_expected", "출고예정 있음", None))
    if below_safe:
        badges.append(("below_safe", "안전재고 이하", None))
    if int(lead_min or 0):
        badges.append(("lead_min", f"리드타임 {int(lead_min)}일 이상", None))
    if int(lead_max or 0):
        badges.append(("lead_max", f"리드타임 {int(lead_max)}일 이하", None))
    return badges


def render_inventory_filter_badges(filter_key: str, badges: list[tuple[str, str, str | None]]) -> None:
    if not badges:
        return
    st.caption("적용 중인 필터")
    cols = st.columns(min(len(badges), 6), gap="small")
    for idx, (suffix, label, value) in enumerate(badges):
        with cols[idx % len(cols)]:
            if st.button(f"{label} ✕", key=f"{filter_key}_clear_{suffix}_{idx}", use_container_width=True):
                clear_inventory_filter(filter_key, suffix, value)
                st.rerun()


def clear_inventory_filter(filter_key: str, suffix: str, value: str | None = None) -> None:
    if suffix in {"search"}:
        st.session_state[f"{filter_key}_{suffix}"] = ""
    elif suffix in {"category_filter"}:
        st.session_state[f"{filter_key}_{suffix}"] = []
        st.session_state[f"{filter_key}_category_toggle"] = "전체"
        st.session_state.pop(f"{filter_key}_category_toggle_widget", None)
        st.session_state.pop(f"{filter_key}_category_toggle_widget_open", None)
    elif suffix in {"stock_presence"}:
        st.session_state[f"{filter_key}_{suffix}"] = "전체"
    elif suffix in {"inbound_expected", "outbound_expected", "below_safe"}:
        st.session_state[f"{filter_key}_{suffix}"] = False
    elif suffix in {"lead_min", "lead_max"}:
        st.session_state[f"{filter_key}_{suffix}"] = 0
    elif value is not None and isinstance(st.session_state.get(f"{filter_key}_{suffix}"), list):
        st.session_state[f"{filter_key}_{suffix}"] = [
            item for item in st.session_state[f"{filter_key}_{suffix}"] if item != value
        ]
    else:
        st.session_state[f"{filter_key}_{suffix}"] = []
    st.session_state[f"{filter_key}_page"] = 1


def paginate_inventory_df(df: pd.DataFrame, filters: dict) -> tuple[pd.DataFrame, int, int]:
    page_size = max(int(filters.get("page_size") or 30), 1)
    total_pages = max(ceil(len(df) / page_size), 1)
    page = min(max(int(filters.get("page") or 1), 1), total_pages)
    start = (page - 1) * page_size
    return df.iloc[start : start + page_size].reset_index(drop=True), page, total_pages


def style_inventory_dataframe(df: pd.DataFrame):
    status_styles = {
        "정상": "background-color: #DDEDE3; color: #21563B; font-weight: 700;",
        "주의": "background-color: #F7E7BD; color: #765216; font-weight: 700;",
        "부족": "background-color: #F2CBC4; color: #8A2A1F; font-weight: 700;",
        "품절": "background-color: #B8453A; color: #FFFFFF; font-weight: 700;",
    }

    def cell_style(value):
        return status_styles.get(clean_cell(value), "")

    styler = df.style
    if "재고상태" not in df.columns:
        return styler
    if hasattr(styler, "map"):
        return styler.map(cell_style, subset=["재고상태"])
    return styler.applymap(cell_style, subset=["재고상태"])


def render_inventory_html(markup: str) -> None:
    if hasattr(st, "html"):
        st.html(markup)
    else:
        st.markdown(markup, unsafe_allow_html=True)


INVENTORY_STATUS_HIDDEN_COLUMNS = {
    "선택",
    "SKU",
    "안전재고",
    "입고예정",
    "최근재고반영일",
    "최근2주 평균출고",
    "위치상세",
}


def inventory_status_output_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if not {"카테고리", "바코드", "상품명"}.issubset(df.columns):
        return df

    output_df = df.copy()
    if "1주 평균출고수량" not in output_df.columns and "최근2주 평균출고" in output_df.columns:
        output_df["1주 평균출고수량"] = output_df["최근2주 평균출고"]

    ordered = [column for column in INVENTORY_STATUS_DISPLAY_COLUMNS if column in output_df.columns]
    remaining = [
        column
        for column in output_df.columns
        if column not in INVENTORY_STATUS_DISPLAY_COLUMNS and column not in INVENTORY_STATUS_HIDDEN_COLUMNS
    ]
    if not ordered:
        return df
    return output_df[ordered + remaining]


def render_inventory_visible_table(df: pd.DataFrame, height: int = 520) -> None:
    if df is None or df.empty:
        st.info("현재 필터 조건에 해당하는 재고 데이터가 없습니다.")
        return
    safe_df = inventory_status_output_dataframe(df).fillna("").copy()
    for column in safe_df.columns:
        safe_df[column] = safe_df[column].map(lambda value: value.isoformat() if hasattr(value, "isoformat") else value)
    html = inventory_visible_table_html(safe_df)
    render_inventory_html(
        f"""
        <div class="inventory-visible-table-wrap" style="max-height:{int(height)}px;">
            {html}
        </div>
        """
    )


def render_plain_inventory_table(df: pd.DataFrame, height: int = 520, empty_message: str = "표시할 데이터가 없습니다.") -> None:
    if df is None or df.empty:
        st.info(empty_message)
        return
    safe_df = df.fillna("").copy()
    for column in safe_df.columns:
        safe_df[column] = safe_df[column].map(lambda value: value.isoformat() if hasattr(value, "isoformat") else value)
    html = inventory_visible_table_html(safe_df)
    st.markdown(
        f"""
        <div class="inventory-visible-table-wrap" style="max-height:{int(height)}px;">
            {html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def inventory_status_badge_html(value: str) -> str:
    label = clean_cell(value) or "미집계"
    tone = {
        "정상": "normal",
        "주의": "warning",
        "부족": "short",
        "품절": "soldout",
        "미집계": "unknown",
        "위치등록": "normal",
        "위치미등록": "warning",
    }.get(label, "unknown")
    return f'<span class="inventory-table-status {tone}">{escape(label)}</span>'


def inventory_visible_table_html(df: pd.DataFrame) -> str:
    headers = "".join(f"<th>{escape(str(column))}</th>" for column in df.columns)
    body_rows = []
    for _, row in df.iterrows():
        cells = []
        for column in df.columns:
            value = row.get(column, "")
            if column in {"재고상태", "위치상태"}:
                cells.append(f"<td>{inventory_status_badge_html(str(value))}</td>")
            else:
                cells.append(f"<td>{escape(str(value))}</td>")
        body_rows.append(f"<tr>{''.join(cells)}</tr>")
    return f'<table class="inventory-visible-table"><thead><tr>{headers}</tr></thead><tbody>{"".join(body_rows)}</tbody></table>'


def render_stock_upload_preview(
    source_type: str,
    work_date: date,
    preview_key: str,
    preview: dict,
    preview_df_key: str,
    applied_df_key: str,
    excluded_df_key: str,
) -> None:
    st.markdown("#### 재고 업로드 미리보기")
    metric_cols = st.columns(5, gap="small")
    metric_cols[0].metric("전체 행", f"{preview.get('total_rows', 0):,}")
    metric_cols[1].metric("정상 매칭", f"{preview.get('matched_count', 0):,}")
    metric_cols[2].metric("매칭/검증 실패", f"{preview.get('failed_count', 0):,}")
    metric_cols[3].metric("중복", f"{preview.get('duplicate_count', 0):,}")
    metric_cols[4].metric("0 처리", f"{preview.get('zeroed_count', 0):,}")
    st.caption(
        f"빈 바코드 {preview.get('empty_barcode_count', 0):,}건 / "
        f"숫자 오류 {preview.get('invalid_stock_count', 0):,}건 / "
        f"음수 재고 {preview.get('negative_stock_count', 0):,}건"
    )
    render_stock_upload_debug(preview)
    preview_df = st.session_state.get(preview_df_key)
    if not isinstance(preview_df, pd.DataFrame):
        preview_df = stock_preview_display_dataframe(preview)
        st.session_state[preview_df_key] = preview_df
        st.session_state["inventory_preview_df"] = preview_df
    if preview_df is None or preview_df.empty:
        st.warning("업로드한 엑셀에서 표시할 데이터를 찾지 못했습니다.")
    else:
        render_inventory_visible_table(preview_df, height=420)
    apply_col, cancel_col, spacer = st.columns([1.0, 1.0, 4.2], gap="small")
    with apply_col:
        if st.button("현재고 반영", type="primary", key=f"{preview_key}_apply", use_container_width=True):
            applied_df = stock_applied_display_dataframe(preview)
            excluded_df = stock_excluded_display_dataframe(preview)
            outcome = with_db(
                lambda db: services.apply_stock_upload_preview(
                    db,
                    source_type,
                    work_date,
                    preview,
                    current_user_name(),
                )
            )
            if outcome and outcome.get("ok", True):
                st.session_state[applied_df_key] = applied_df
                st.session_state[excluded_df_key] = excluded_df
                st.session_state["applied_inventory_df"] = applied_df
                st.session_state["excluded_inventory_df"] = excluded_df
                st.session_state.pop(preview_key, None)
                st.success(
                    f"현재고 반영 완료: {outcome.get('count', 0):,}건 / "
                    f"제외 {max(int(preview.get('total_rows', 0) or 0) - int(outcome.get('count', 0) or 0), 0):,}건"
                )
                if not applied_df.empty:
                    render_inventory_visible_table(applied_df, height=520)
                if not excluded_df.empty:
                    with st.expander(f"반영 제외 {len(excluded_df):,}건 및 사유", expanded=False):
                        render_inventory_visible_table(excluded_df, height=260)
            show_result(outcome)
    with cancel_col:
        if st.button("미리보기 취소", key=f"{preview_key}_cancel", use_container_width=True):
            st.session_state.pop(preview_key, None)
            st.session_state.pop(preview_df_key, None)
            st.session_state["uploaded_inventory_df"] = None
            st.session_state["inventory_preview_df"] = None
            st.rerun()
    with spacer:
        st.empty()


def stock_preview_display_dataframe(preview: dict) -> pd.DataFrame:
    preview_df = pd.DataFrame(preview.get("preview_rows", []))
    if preview_df.empty:
        return pd.DataFrame()
    columns = {
        "row_no": "엑셀 행",
        "product_code": "SKU",
        "category": "카테고리",
        "product_name": "상품명",
        "barcode": "바코드",
        "previous_stock": "기존 현재고",
        "new_stock": "변경 현재고",
        "new_available_stock": "변경 가용재고",
        "status": "검증결과",
        "matched": "반영대상",
    }
    display_df = preview_df.rename(columns=columns)
    ordered = ["엑셀 행", "SKU", "카테고리", "상품명", "바코드", "기존 현재고", "변경 현재고", "변경 가용재고", "검증결과", "반영대상"]
    return display_df[[column for column in ordered if column in display_df.columns]]


def stock_applied_display_dataframe(preview: dict) -> pd.DataFrame:
    preview_df = stock_preview_display_dataframe(preview)
    if preview_df.empty or "반영대상" not in preview_df.columns:
        return pd.DataFrame()
    applied = preview_df[preview_df["반영대상"].astype(bool)].copy()
    if applied.empty:
        return applied
    applied["반영 결과"] = "반영 완료"
    return applied


def stock_excluded_display_dataframe(preview: dict) -> pd.DataFrame:
    preview_df = stock_preview_display_dataframe(preview)
    if preview_df.empty or "반영대상" not in preview_df.columns:
        return pd.DataFrame()
    excluded = preview_df[~preview_df["반영대상"].astype(bool)].copy()
    if excluded.empty:
        return excluded
    excluded["제외 사유"] = excluded.get("검증결과", "제외")
    return excluded


def render_stock_upload_debug(preview: dict) -> None:
    debug = preview.get("debug") or {}
    if not debug:
        return
    raw_shape = debug.get("raw_shape") or (0, 0)
    normalized_shape = debug.get("normalized_shape") or (0, 0)
    with st.expander("엑셀 읽기 디버그 정보", expanded=False):
        st.write(f"읽은 방식: {debug.get('read_method') or '-'}")
        st.write(f"읽은 시트명: {debug.get('selected_sheet') or '-'}")
        if debug.get("sheet_names"):
            st.write("사용 가능한 시트:", ", ".join(map(str, debug.get("sheet_names", []))))
        if debug.get("non_empty_sheet_names"):
            st.write("데이터가 있는 시트:", ", ".join(map(str, debug.get("non_empty_sheet_names", []))))
        st.write(f"원본 dataframe: {raw_shape[0] if len(raw_shape) > 0 else 0:,}행 × {raw_shape[1] if len(raw_shape) > 1 else 0:,}열")
        st.write(f"정규화 dataframe: {normalized_shape[0] if len(normalized_shape) > 0 else 0:,}행 × {normalized_shape[1] if len(normalized_shape) > 1 else 0:,}열")
        st.write("인식된 컬럼명:", list(debug.get("normalized_columns", [])))
        raw_head = pd.DataFrame(debug.get("raw_head", []))
        normalized_head = pd.DataFrame(debug.get("normalized_head", []))
        if not raw_head.empty:
            st.caption("원본 첫 5행")
            st.dataframe(raw_head, hide_index=True, use_container_width=True, height=180)
        if not normalized_head.empty:
            st.caption("정규화 후 첫 5행")
            st.dataframe(normalized_head, hide_index=True, use_container_width=True, height=180)


def current_user_name() -> str:
    for key in ("user_name", "username", "user", "meeting_author"):
        value = clean_cell(st.session_state.get(key))
        if value:
            return value
    return "SYSTEM"


def record_inventory_output(source_type: str, work_date: date, output_type: str, filters: dict, item_count: int) -> None:
    with_db(lambda db: services.record_inventory_output(db, source_type, work_date, output_type, filters, item_count, current_user_name()))


def inventory_file_name(extension: str, df: pd.DataFrame, filters: dict) -> str:
    today_text = date.today().strftime("%Y%m%d")
    categories = filters.get("categories") or []
    if len(categories) == 1:
        scope = categories[0]
    elif any(
        [
            filters.get("categories"),
            filters.get("suppliers"),
            filters.get("managers"),
            filters.get("statuses"),
            clean_cell(filters.get("search")),
            (filters.get("stock_presence") or "전체") != "전체",
            filters.get("inbound_expected"),
            filters.get("outbound_expected"),
            filters.get("below_safe"),
            filters.get("lead_min"),
            filters.get("lead_max"),
        ]
    ):
        scope = "필터결과"
    else:
        scope = "전체"
    return f"SCM재고관리_{safe_file_part(scope)}_{today_text}.{extension}"


def safe_file_part(value: str) -> str:
    text = clean_cell(value) or "전체"
    return "".join(ch if ch not in '\\/:*?"<>|' else "_" for ch in text)


def register_inventory_pdf_fonts() -> tuple[str, str] | None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    font_dirs = [
        Path(r"C:\Windows\Fonts"),
        Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts",
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path(__file__).resolve().parents[1] / "assets" / "fonts",
    ]
    candidates = [
        ("MalgunGothic", "MalgunGothicBold", "malgun.ttf", "malgunbd.ttf"),
        ("NanumGothic", "NanumGothicBold", "NanumGothic.ttf", "NanumGothicBold.ttf"),
        ("NanumGothic", "NanumGothicBold", "NanumGothic-Regular.ttf", "NanumGothic-Bold.ttf"),
        ("NotoSansKR", "NotoSansKRBold", "NotoSansKR-Regular.ttf", "NotoSansKR-Bold.ttf"),
        ("NotoSansCJKkr", "NotoSansCJKkrBold", "NotoSansCJKkr-Regular.otf", "NotoSansCJKkr-Bold.otf"),
        ("UnDotum", "UnDotumBold", "UnDotum.ttf", "UnDotumBold.ttf"),
    ]
    for regular_name, bold_name, regular_file, bold_file in candidates:
        for font_dir in font_dirs:
            regular_path = next(font_dir.rglob(regular_file), None) if font_dir.exists() else None
            if regular_path is None:
                continue
            bold_path = next(font_dir.rglob(bold_file), None) if font_dir.exists() else None
            try:
                if regular_name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(regular_name, str(regular_path)))
                if bold_name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(bold_name, str(bold_path or regular_path)))
                return regular_name, bold_name
            except Exception:
                continue

    for regular_name, bold_name in [
        ("HYGothic-Medium", "HYGothic-Medium"),
        ("HYSMyeongJo-Medium", "HYGothic-Medium"),
        ("HeiseiKakuGo-W5", "HeiseiKakuGo-W5"),
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


def inventory_pdf_bytes(df: pd.DataFrame, source_type: str, work_date: date, filters: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font_pair = register_inventory_pdf_fonts()
    if font_pair is None:
        raise RuntimeError("사용 가능한 한글 PDF 폰트를 찾지 못했습니다.")
    font_name, bold_name = font_pair

    output = BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        leftMargin=9 * mm,
        rightMargin=9 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
    )
    styles = {
        "title": ParagraphStyle("inventory_title", fontName=bold_name, fontSize=16, leading=20, alignment=TA_CENTER),
        "meta": ParagraphStyle("inventory_meta", fontName=font_name, fontSize=8, leading=11, alignment=TA_LEFT),
        "cell": ParagraphStyle("inventory_cell", fontName=font_name, fontSize=7.2, leading=9, alignment=TA_LEFT),
        "center": ParagraphStyle("inventory_center", fontName=font_name, fontSize=7.2, leading=9, alignment=TA_CENTER),
        "right": ParagraphStyle("inventory_right", fontName=font_name, fontSize=7.2, leading=9, alignment=TA_RIGHT),
    }
    export_columns = INVENTORY_STATUS_DISPLAY_COLUMNS
    export_df = inventory_status_output_dataframe(df)
    export_df = export_df[[column for column in export_columns if column in export_df.columns]].copy()
    meta = [
        f"재고처: {source_type}",
        f"기준일자: {work_date:%Y-%m-%d}",
        f"생성일시: {pd.Timestamp.now():%Y-%m-%d %H:%M}",
        f"카테고리: {', '.join(filters.get('categories') or ['전체'])}",
        f"재고상태: {', '.join(filters.get('statuses') or ['전체'])}",
        f"상품 수: {len(export_df):,}",
    ]
    story = [Paragraph("SCM 재고관리", styles["title"]), Spacer(1, 4 * mm), Paragraph(" / ".join(meta), styles["meta"]), Spacer(1, 4 * mm)]
    table_data = [[Paragraph(column, styles["center"]) for column in export_df.columns]]
    numeric_columns = {"가용재고", "1주 평균출고수량", "출고예정", "현재고", "리드타임"}
    for _, row in export_df.iterrows():
        cells = []
        for column in export_df.columns:
            value = row.get(column, "")
            if column in numeric_columns and clean_cell(value) != "":
                style = styles["right"]
                text = f"{float(value):,.2f}" if column == "1주 평균출고수량" else f"{to_int(value):,}"
            elif column in {"바코드", "재고상태"}:
                style = styles["center"]
                text = clean_cell(value)
            else:
                style = styles["cell"]
                text = clean_cell(value)
            cells.append(Paragraph(text, style))
        table_data.append(cells)
    table = Table(
        table_data,
        repeatRows=1,
        colWidths=[17 * mm, 24 * mm, 46 * mm, 15 * mm, 20 * mm, 16 * mm, 15 * mm, 15 * mm, 16 * mm, 26 * mm, 22 * mm, 20 * mm, 14 * mm][: len(export_df.columns)],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0B6B60")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D7E8E4")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7FBFA")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(table)
    doc.build(story)
    return output.getvalue()


def fetch_inbound(source_type: str) -> list[dict]:
    return with_db(lambda db: [services.inbound_to_dict(row) for row in services.list_inbound(db, source_type)]) or []


def material_inventory_rows(db) -> list[MaterialInventoryItem]:
    if MaterialInventoryItem is None:
        return []
    return list(
        db.execute(
            select(MaterialInventoryItem).order_by(
                MaterialInventoryItem.related_product,
                MaterialInventoryItem.item_type,
                MaterialInventoryItem.item_name,
                MaterialInventoryItem.id,
            )
        ).scalars()
    )


def material_to_editor(rows: list) -> pd.DataFrame:
    columns = [
        "ID",
        "삭제",
        "카테고리",
        "유형",
        "연결제품",
        "품목코드",
        "품목명",
        "규격",
        "단위",
        "현재고",
        "안전재고",
        "부족수량",
        "보관위치",
        "공급처",
        "리드타임",
        "비고",
    ]
    data = []
    for row in rows:
        current_stock = int(row.current_stock or 0)
        safe_stock = int(row.safe_stock or 0)
        data.append(
            {
                "ID": row.id,
                "삭제": False,
                "카테고리": row.category,
                "유형": row.item_type or "자재",
                "연결제품": row.related_product,
                "품목코드": row.item_code,
                "품목명": row.item_name,
                "규격": row.spec,
                "단위": row.unit or "EA",
                "현재고": current_stock,
                "안전재고": safe_stock,
                "부족수량": max(safe_stock - current_stock, 0),
                "보관위치": row.location,
                "공급처": row.supplier,
                "리드타임": row.lead_time_days,
                "비고": row.memo,
            }
        )
    return pd.DataFrame(data, columns=columns)


def filter_material_editor_df(df: pd.DataFrame, keyword: str, type_filter: str, category_filter: str) -> pd.DataFrame:
    if df.empty:
        return df
    filtered = df.copy()
    if type_filter != "전체":
        filtered = filtered[filtered["유형"] == type_filter]
    if category_filter != "전체":
        filtered = filtered[filtered["카테고리"] == category_filter]
    keyword = clean_cell(keyword).lower()
    if keyword:
        search_cols = ["품목명", "품목코드", "연결제품", "공급처", "규격", "보관위치"]
        mask = filtered[search_cols].fillna("").astype(str).agg(" ".join, axis=1).str.lower().str.contains(keyword, regex=False)
        filtered = filtered[mask]
    return filtered


def save_material_inventory_rows(db, edited: pd.DataFrame) -> int:
    if MaterialInventoryItem is None or edited is None:
        return 0
    count = 0
    for record in edited.fillna("").to_dict("records"):
        row_id = to_int(record.get("ID"))
        item_name = clean_cell(record.get("품목명"))
        item_code = clean_cell(record.get("품목코드"))
        related_product = clean_cell(record.get("연결제품"))
        if bool(record.get("삭제", False)):
            if row_id:
                row = db.get(MaterialInventoryItem, row_id)
                if row:
                    db.delete(row)
                    count += 1
            continue
        if not item_name:
            continue
        row = db.get(MaterialInventoryItem, row_id) if row_id else None
        if row is None:
            row = db.execute(
                select(MaterialInventoryItem).where(
                    MaterialInventoryItem.item_code == item_code,
                    MaterialInventoryItem.item_name == item_name,
                    MaterialInventoryItem.related_product == related_product,
                )
            ).scalar_one_or_none()
        if row is None:
            row = MaterialInventoryItem(item_name=item_name)
            db.add(row)
        row.category = clean_cell(record.get("카테고리"))
        row.item_type = clean_cell(record.get("유형")) if clean_cell(record.get("유형")) in {"자재", "반제품"} else "자재"
        row.related_product = related_product
        row.item_code = item_code
        row.item_name = item_name
        row.spec = clean_cell(record.get("규격"))
        row.unit = clean_cell(record.get("단위")) or "EA"
        row.current_stock = to_int(record.get("현재고"))
        row.safe_stock = to_int(record.get("안전재고"))
        row.location = clean_cell(record.get("보관위치"))
        row.supplier = clean_cell(record.get("공급처"))
        row.lead_time_days = to_int(record.get("리드타임"))
        row.memo = clean_cell(record.get("비고"))
        count += 1
    db.commit()
    return count


def render_production_plan_editor() -> None:
    st.markdown("#### 생산계획")
    bom_options = with_db(lambda db: bom_product_options(db)) or []
    df = with_db(lambda db: production_plan_editor_df(db))
    if df is None:
        df = pd.DataFrame()
    if df.empty:
        df = pd.DataFrame(columns=["삭제", "생산계획번호", "완제품/BOM", "계획수량", "생산예정일", "상태", "비고"])
    with st.form("production_plan_form", clear_on_submit=False):
        edited = st.data_editor(
            df,
            num_rows="dynamic",
            hide_index=True,
            use_container_width=True,
            column_order=["삭제", "생산계획번호", "완제품/BOM", "계획수량", "생산예정일", "상태", "비고"],
            column_config={
                "삭제": st.column_config.CheckboxColumn("삭제", default=False),
                "완제품/BOM": st.column_config.SelectboxColumn("완제품/BOM", options=bom_options) if bom_options else st.column_config.TextColumn("완제품/BOM"),
                "계획수량": st.column_config.NumberColumn("계획수량", min_value=0, step=1),
                "생산예정일": st.column_config.DateColumn("생산예정일"),
                "상태": st.column_config.SelectboxColumn("상태", options=["계획", "확정", "완료", "취소"]),
            },
            key="production_plan_editor",
        )
        cols = st.columns([1.0, 5.0], gap="small")
        with cols[0]:
            submitted = st.form_submit_button("생산계획 저장", type="primary", use_container_width=True)
        with cols[1]:
            st.empty()
        if submitted:
            count = with_db(lambda db: save_production_plans(db, edited))
            st.success(f"생산계획 저장 완료: {count or 0}건")
            st.rerun()


def bom_product_options(db) -> list[str]:
    if CategoryBomItem is None:
        return []
    rows = db.execute(select(CategoryBomItem.category_name).distinct().order_by(CategoryBomItem.category_name)).all()
    return [row[0] for row in rows if row[0]]


def production_plan_editor_df(db) -> pd.DataFrame:
    if ProductionPlan is None:
        return pd.DataFrame()
    rows = list(db.execute(select(ProductionPlan).order_by(ProductionPlan.due_date.desc(), ProductionPlan.id.desc())).scalars())
    return pd.DataFrame(
        [
            {
                "삭제": False,
                "생산계획번호": row.plan_number,
                "완제품/BOM": row.product_name,
                "계획수량": row.plan_qty,
                "생산예정일": row.due_date,
                "상태": row.status,
                "비고": row.memo,
            }
            for row in rows
        ]
    )


def save_production_plans(db, edited: pd.DataFrame) -> int:
    if ProductionPlan is None:
        return 0
    count = 0
    for record in edited.fillna("").to_dict("records"):
        plan_number = clean_cell(record.get("생산계획번호"))
        product_name = clean_cell(record.get("완제품/BOM"))
        if not plan_number and not product_name:
            continue
        row = db.execute(select(ProductionPlan).where(ProductionPlan.plan_number == plan_number)).scalar_one_or_none() if plan_number else None
        if bool(record.get("삭제", False)):
            if row:
                db.delete(row)
                count += 1
            continue
        if not product_name:
            continue
        if row is None:
            row = ProductionPlan(plan_number=next_inventory_number(db, ProductionPlan, ProductionPlan.plan_number, "PLAN"))
            db.add(row)
        row.product_name = product_name
        row.plan_qty = to_int(record.get("계획수량"))
        row.due_date = parse_date_cell(record.get("생산예정일")) or date.today()
        row.status = clean_cell(record.get("상태")) or "계획"
        row.memo = clean_cell(record.get("비고"))
        count += 1
    db.commit()
    return count


def calculate_mrp_rows(db, source_type: str, work_date: date) -> list[dict]:
    if CategoryBomItem is None or ProductionPlan is None:
        return []
    plans = list(
        db.execute(
            select(ProductionPlan)
            .where(ProductionPlan.status.in_(["계획", "확정"]), ProductionPlan.plan_qty > 0)
            .order_by(ProductionPlan.due_date)
        ).scalars()
    )
    if not plans:
        return []

    bom_rows = list(db.execute(select(CategoryBomItem).order_by(CategoryBomItem.category_name, CategoryBomItem.sort_order)).scalars())
    bom_by_product: dict[str, list] = {}
    for row in bom_rows:
        if row.item_type == "완제품":
            continue
        bom_by_product.setdefault(row.category_name, []).append(row)

    stock_map = latest_stock_lookup(db, source_type, work_date)
    aggregated: dict[tuple[str, str, str, str], dict] = {}
    for plan in plans:
        for bom in bom_by_product.get(plan.product_name, []):
            need_qty = int(plan.plan_qty or 0) * max(int(bom.required_stock or 0), 1)
            key = (bom.item_name, bom.spec or "", bom.vendor or "", bom.barcode or "")
            row = aggregated.setdefault(
                key,
                {
                    "생산계획": "",
                    "완제품": plan.product_name,
                    "품목": bom.item_name,
                    "규격": bom.spec or "",
                    "거래처": bom.vendor or "",
                    "필요수량": 0,
                    "현재재고": stock_map.get((bom.item_name, bom.barcode or ""), stock_map.get((bom.item_name, ""), 0)),
                    "부족수량": 0,
                    "발주추천수량": 0,
                },
            )
            plan_label = f"{plan.plan_number}({plan.plan_qty:,})"
            row["생산계획"] = f'{row["생산계획"]}, {plan_label}' if row["생산계획"] else plan_label
            row["필요수량"] += need_qty
            row["부족수량"] = max(row["필요수량"] - int(row["현재재고"] or 0), 0)
            row["발주추천수량"] = row["부족수량"]
    return sorted(aggregated.values(), key=lambda row: row["부족수량"], reverse=True)


def latest_stock_lookup(db, source_type: str, work_date: date) -> dict[tuple[str, str], int]:
    rows = services.list_daily(db, source_type, work_date)
    lookup: dict[tuple[str, str], int] = {}
    for row in rows:
        stock = int(row.available_stock if row.available_stock is not None else row.current_stock or 0)
        barcode = services.normalize_barcode_text(row.barcode)
        lookup[(row.product_name, barcode)] = lookup.get((row.product_name, barcode), 0) + stock
        lookup[(row.product_name, "")] = lookup.get((row.product_name, ""), 0) + stock
    return lookup


def stock_history_item_options(db, source_type: str) -> list[str]:
    if InventoryDaily is None:
        return []
    rows = db.execute(
        select(InventoryDaily.product_name)
        .where(InventoryDaily.source_type == source_type)
        .distinct()
        .order_by(InventoryDaily.product_name)
    ).all()
    return [row[0] for row in rows if row[0]]


def stock_history_rows(db, source_type: str, item_name: str) -> list[dict]:
    if InventoryDaily is None:
        return []
    rows = list(
        db.execute(
            select(InventoryDaily)
            .where(InventoryDaily.source_type == source_type, InventoryDaily.product_name == item_name)
            .order_by(InventoryDaily.work_date)
        ).scalars()
    )
    return [
        {
            "기준일자": row.work_date,
            "상품명": row.product_name,
            "현재고": row.current_stock,
            "가용재고": row.available_stock,
            "안전재고": row.safe_stock,
            "출고수량": row.outbound_qty,
            "입고수량": row.inbound_qty,
            "재고상태": row.stock_status,
        }
        for row in rows
    ]


def purchase_recommendation_rows(db, source_type: str, work_date: date, include_leadtime: bool) -> list[dict]:
    rows = services.list_daily(db, source_type, work_date)
    result_rows = []
    for row in rows:
        current_stock = int(row.available_stock if row.available_stock is not None else row.current_stock or 0)
        safe_stock = int(row.safe_stock or 0)
        lead_time = int(row.inbound_cycle or 0)
        product = services.find_product_master(db, source_type, row.product_code, row.barcode, row.product_name)
        box_qty = int((product.box_qty or product.pack_qty or 0) if product else 0)
        avg_outbound = avg_daily_outbound(db, source_type, row.product_name, services.normalize_barcode_text(row.barcode), work_date, 14)
        leadtime_need = ceil(avg_outbound * lead_time) if include_leadtime and lead_time else 0
        reorder_point = safe_stock + leadtime_need
        shortage_qty = max(reorder_point - current_stock, 0)
        below_safe = current_stock <= safe_stock if safe_stock else current_stock <= 0
        if not below_safe and shortage_qty <= 0:
            continue
        base_recommend_qty = max(shortage_qty, max(safe_stock - current_stock, 0), 1)
        recommended_boxes = ceil(base_recommend_qty / box_qty) if box_qty else 0
        recommended_qty = recommended_boxes * box_qty if recommended_boxes else base_recommend_qty
        result_rows.append(
            {
                "SKU": row.product_code,
                "상품명": row.product_name,
                "규격": "",
                "현재재고": current_stock,
                "안전재고": safe_stock,
                "리드타임": lead_time,
                "리드타임 예상소요": leadtime_need,
                "부족수량": max(safe_stock - current_stock, 0),
                "박스입수": box_qty,
                "권장 박스수": recommended_boxes,
                "발주추천수량": recommended_qty,
                "발주권장": "권장" if shortage_qty > 0 or below_safe else "보류",
                "공급처": row.supplier,
                "재고상태": row.stock_status,
            }
        )
    return sorted(result_rows, key=lambda item: item["발주추천수량"], reverse=True)


def avg_daily_outbound(db, source_type: str, product_name: str, barcode: str, work_date: date, days: int) -> float:
    if InventoryDaily is None:
        return 0
    start_date = work_date - timedelta(days=days)
    query = select(func.sum(InventoryDaily.outbound_qty), func.count()).where(
        InventoryDaily.source_type == source_type,
        InventoryDaily.product_name == product_name,
        InventoryDaily.work_date >= start_date,
        InventoryDaily.work_date <= work_date,
    )
    if barcode:
        query = query.where(InventoryDaily.barcode == barcode)
    total, count = db.execute(query).one()
    return int(total or 0) / max(int(count or 0), 1)


def create_pr_from_recommendation_rows(db, edited: pd.DataFrame, source_type: str) -> int:
    if PurchaseRequest is None or edited is None or edited.empty or "PR생성" not in edited.columns:
        return 0
    count = 0
    for record in edited.to_dict("records"):
        if not bool(record.get("PR생성", False)):
            continue
        item_name = clean_cell(record.get("품목") or record.get("상품명"))
        item_code = clean_cell(record.get("SKU") or record.get("품목코드"))
        quantity = to_int(record.get("발주추천수량"))
        if not item_name or quantity <= 0 or has_open_purchase_request(db, item_name):
            continue
        db.add(
            PurchaseRequest(
                pr_number=next_inventory_number(db, PurchaseRequest, PurchaseRequest.pr_number, "PR"),
                department="자재/구매",
                item_code=item_code,
                item_name=item_name,
                spec=clean_cell(record.get("규격")),
                quantity=quantity,
                request_date=date.today(),
                requester="MRP",
                approval_status="상신",
                source_type=source_type,
                memo=f"{source_type} 자동 생성",
            )
        )
        count += 1
    db.commit()
    return count


def has_open_purchase_request(db, item_name: str) -> bool:
    if PurchaseRequest is None:
        return False
    exists = db.scalar(
        select(func.count()).where(
            PurchaseRequest.item_name == item_name,
            PurchaseRequest.linked_po_number == "",
            PurchaseRequest.approval_status.in_(["작성", "상신", "승인"]),
        )
    )
    return bool(exists)


def next_inventory_number(db, model, column, prefix: str) -> str:
    today_key = date.today().strftime("%Y%m%d")
    pattern = f"{prefix}-{today_key}-%"
    count = db.scalar(select(func.count()).where(column.like(pattern))) or 0
    return f"{prefix}-{today_key}-{int(count) + 1:03d}"


def parse_date_cell(value) -> date | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def product_sku_options(source_type: str) -> list[str]:
    rows = with_db(lambda db: services.active_product_options(db, source_type)) or []
    return [row.get("sku", "") for row in rows if row.get("sku")]


def daily_excel(source_type: str, work_date: date) -> bytes:
    return dataframe_to_excel(daily_to_editor(fetch_daily(source_type, work_date)))


def inbound_excel(source_type: str) -> bytes:
    return dataframe_to_excel(inbound_to_editor(fetch_inbound(source_type)))


def daily_to_editor(rows: list[dict]) -> pd.DataFrame:
    mapped = []
    for row in rows:
        category = clean_cell(row.get("category") or row.get("large_category") or row.get("medium_category") or row.get("small_category"))
        mapped.append(
            {
                "선택": False,
                "SKU": row.get("product_code", ""),
                "카테고리": category or "미분류",
                "바코드": row.get("barcode", ""),
                "상품명": row.get("product_name", ""),
                "가용재고": row.get("available_stock", 0),
                "1주 평균출고수량": row.get("avg_daily_outbound_1w", row.get("avg_daily_outbound_2w", 0)),
                "재고상태": clean_cell(row.get("stock_status")) or "미집계",
                "출고예정": row.get("pending_outbound_qty", 0),
                "현재고": row.get("current_stock", 0),
                "발주필요일": format_order_required_date(row),
                "박스/파렛트 단위": row.get("box_pallet_unit", ""),
                "재고위치": row.get("storage_location", ""),
                "업체명": row.get("supplier", ""),
                "담당자": row.get("manager", ""),
                "리드타임": row.get("inbound_cycle", 0) or 0,
            }
        )
    return pd.DataFrame(mapped, columns=DAILY_COLUMNS)


def inbound_to_editor(rows: list[dict]) -> pd.DataFrame:
    mapped = []
    for row in rows:
        mapped.append(
            {
                "삭제": False,
                "입고일자": row.get("inbound_date"),
                "SKU": row.get("product_code", ""),
                "바코드": row.get("barcode", ""),
                "상품명": row.get("product_name", ""),
                "공급처": row.get("vendor", ""),
                "입고수량": row.get("inbound_qty", 0),
                "입고구분": row.get("inbound_type", ""),
                "비고": row.get("memo", ""),
            }
        )
    return pd.DataFrame(mapped, columns=INBOUND_COLUMNS)


def daily_payload(df: pd.DataFrame, source_type: str, work_date: date) -> list[dict]:
    rows = []
    for _, row in df.iterrows():
        if bool(row.get("선택", False)) or bool(row.get("삭제", False)):
            continue
        product_name = clean_cell(row.get("상품명"))
        if not product_name:
            continue
        rows.append(
            {
                "source_type": source_type,
                "work_date": work_date.isoformat(),
                "category": clean_cell(row.get("카테고리")),
                "supplier": clean_cell(row.get("업체명")),
                "product_code": "",
                "product_name": product_name,
                "barcode": services.normalize_barcode_text(row.get("바코드")),
                "current_stock": to_int(row.get("현재고")),
                "available_stock": to_int(row.get("가용재고")),
                "safe_stock": to_int(row.get("안전재고")),
                "stock_status": clean_cell(row.get("재고상태")),
                "inbound_cycle": to_int(row.get("리드타임")) or None,
            }
        )
    return rows


def inbound_payload(df: pd.DataFrame, source_type: str) -> list[dict]:
    rows = []
    for _, row in df.iterrows():
        if bool(row.get("삭제", False)):
            continue
        product_name = clean_cell(row.get("상품명"))
        inbound_date = date_or_none(row.get("입고일자"))
        if not product_name or not inbound_date:
            continue
        rows.append(
            {
                "source_type": source_type,
                "inbound_date": inbound_date,
                "category": "",
                "product_code": clean_cell(row.get("SKU")),
                "product_name": product_name,
                "barcode": clean_cell(row.get("바코드")),
                "vendor": clean_cell(row.get("공급처")),
                "inbound_qty": to_int(row.get("입고수량")),
                "inbound_type": clean_cell(row.get("입고구분")),
                "memo": clean_cell(row.get("비고")),
            }
        )
    return rows


def upload_daily(label: str, upload_type: str, source_type: str, work_date: date, key: str) -> None:
    uploaded = st.file_uploader(
        label,
        type=["xlsx", "xls", "html"],
        key=f"{key}_{work_date}",
        label_visibility="collapsed",
    )
    if st.button(label.replace("업로드", "반영"), key=f"{key}_btn_{work_date}", use_container_width=True):
        if uploaded is None:
            st.warning("먼저 엑셀 파일을 업로드하세요.")
            return
        file_bytes = uploaded.getvalue()
        if upload_type == "stock":
            outcome = with_db(lambda db: import_upload_result("재고조회 엑셀 반영 완료", services.import_stock(db, source_type, work_date, file_bytes)))
            if outcome and outcome.get("ok", True):
                clear_inventory_editor_buffer(f"{source_type}_daily_editor_buffer_{work_date.isoformat()}")
            show_result(outcome)
        elif upload_type == "order":
            outcome = with_db(lambda db: import_upload_result(f"{work_date:%Y-%m-%d} 주문조회 엑셀 반영 완료", services.import_order(db, source_type, work_date, file_bytes)))
            if outcome and outcome.get("ok", True):
                st.session_state[f"{source_type}_daily_date_sync"] = work_date
                clear_inventory_editor_buffer(f"{source_type}_daily_editor_buffer_{work_date.isoformat()}")
            show_result(outcome)


def render_empty_action_slot() -> None:
    st.markdown('<div class="inventory-action-slot-empty"></div>', unsafe_allow_html=True)


def render_empty_upload_slot() -> None:
    st.markdown('<div class="inventory-upload-slot inventory-upload-slot-empty"></div>', unsafe_allow_html=True)


def clean_cell(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "nat", "none", "null"} else text


def to_int(value) -> int:
    text = clean_cell(value).replace(",", "")
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def date_or_none(value):
    if value in (None, ""):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def parse_date_or_today(value) -> date:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return date.today()
    return parsed.date()


def format_order_required_date(row: dict) -> str:
    avg_outbound = row.get("avg_daily_outbound_1w", row.get("avg_daily_outbound_2w", 0))
    try:
        avg_outbound_value = float(avg_outbound or 0)
    except (TypeError, ValueError):
        avg_outbound_value = 0.0
    if avg_outbound_value <= 0:
        return "-"

    available_stock = to_int(row.get("available_stock"))
    if available_stock <= 0:
        return "즉시 발주"

    lead_time_text = clean_cell(row.get("inbound_cycle"))
    if not lead_time_text:
        return "리드타임 미설정"

    order_days = row.get("order_needed_days")
    if order_days is None:
        return "리드타임 미설정"
    try:
        days = int(float(order_days))
    except (TypeError, ValueError):
        return "리드타임 미설정"
    if days <= 0:
        return "즉시 발주"
    return (parse_date_or_today(row.get("work_date")) + timedelta(days=days)).isoformat()


def dataframe_to_excel(df: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        export_df = df.drop(columns=["삭제", "선택"], errors="ignore").copy()
        sheet_name = "재고관리"
        start_row = 2
        export_df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=start_row)

        workbook = writer.book
        worksheet = writer.sheets[sheet_name]
        row_count, col_count = export_df.shape
        last_row = start_row + row_count
        last_col = max(col_count - 1, 0)

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
        subtitle_format = workbook.add_format(
            {
                "font_size": 9,
                "font_color": "#52716B",
                "align": "right",
                "valign": "vcenter",
            }
        )
        header_format = workbook.add_format(
            {
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": "#0B6B60",
                "border": 1,
                "border_color": "#D7E8E4",
                "align": "center",
                "valign": "vcenter",
            }
        )
        text_format = workbook.add_format(
            {
                "border": 1,
                "border_color": "#E5EFEA",
                "valign": "vcenter",
            }
        )
        center_format = workbook.add_format(
            {
                "border": 1,
                "border_color": "#E5EFEA",
                "align": "center",
                "valign": "vcenter",
            }
        )
        number_format = workbook.add_format(
            {
                "border": 1,
                "border_color": "#E5EFEA",
                "align": "right",
                "num_format": "#,##0",
                "valign": "vcenter",
            }
        )
        date_format = workbook.add_format(
            {
                "border": 1,
                "border_color": "#E5EFEA",
                "align": "center",
                "num_format": "yyyy-mm-dd",
                "valign": "vcenter",
            }
        )

        if col_count > 0:
            if last_col > 0:
                worksheet.merge_range(0, 0, 0, last_col, "SCM 재고관리", title_format)
                worksheet.merge_range(1, 0, 1, last_col, f"다운로드: {pd.Timestamp.now():%Y-%m-%d %H:%M}", subtitle_format)
            else:
                worksheet.write(0, 0, "SCM 재고관리", title_format)
                worksheet.write(1, 0, f"다운로드: {pd.Timestamp.now():%Y-%m-%d %H:%M}", subtitle_format)
            worksheet.set_row(0, 26)
            worksheet.set_row(start_row, 24)
            worksheet.freeze_panes(start_row + 1, 0)
            worksheet.autofilter(start_row, 0, last_row, last_col)

        numeric_columns = {"현재고", "보유재고", "가용재고", "안전재고", "입고예정", "출고예정", "리드타임", "정렬순서", "출고수량", "입고수량"}
        date_columns = {"입고일자", "기준일자"}
        center_columns = {"구분", "SKU", "바코드", "재고상태", "입고구분"}

        for col_idx, column in enumerate(export_df.columns):
            width = excel_column_width(export_df[column], column)
            column_format = text_format
            if column in numeric_columns:
                column_format = number_format
            elif column in date_columns:
                column_format = date_format
            elif column in center_columns:
                column_format = center_format
            worksheet.set_column(col_idx, col_idx, width, column_format)
            worksheet.write(start_row, col_idx, column, header_format)

        if row_count > 0 and "재고상태" in export_df.columns:
            status_col = export_df.columns.get_loc("재고상태")
            status_range = xl_range(start_row + 1, status_col, last_row, status_col)
            status_formats = {
                "정상": workbook.add_format({"bg_color": "#DDEDE3", "font_color": "#21563B"}),
                "주의": workbook.add_format({"bg_color": "#F7E7BD", "font_color": "#765216"}),
                "부족": workbook.add_format({"bg_color": "#F2CBC4", "font_color": "#8A2A1F"}),
                "품절": workbook.add_format({"bg_color": "#B8453A", "font_color": "#FFFFFF"}),
                "미출": workbook.add_format({"bg_color": "#F2CBC4", "font_color": "#8A2A1F"}),
                "입고필요": workbook.add_format({"bg_color": "#F7E7BD", "font_color": "#765216"}),
            }
            for status, fmt in status_formats.items():
                worksheet.conditional_format(
                    status_range,
                    {
                        "type": "cell",
                        "criteria": "==",
                        "value": f'"{status}"',
                        "format": fmt,
                    },
                )

        worksheet.set_landscape()
        worksheet.fit_to_pages(1, 0)
        worksheet.set_margins(left=0.3, right=0.3, top=0.5, bottom=0.5)
    return output.getvalue()


def excel_column_width(series: pd.Series, column: str) -> int:
    values = [len(str(column))]
    if not series.empty:
        values.extend(len(clean_cell(value)) for value in series.head(200))
    return min(max(max(values, default=10) + 4, 10), 42)


def xl_col(col_idx: int) -> str:
    name = ""
    col_idx += 1
    while col_idx:
        col_idx, remainder = divmod(col_idx - 1, 26)
        name = chr(65 + remainder) + name
    return name


def xl_range(first_row: int, first_col: int, last_row: int, last_col: int) -> str:
    return f"{xl_col(first_col)}{first_row + 1}:{xl_col(last_col)}{last_row + 1}"


def show_result(result) -> None:
    if not result:
        return
    if result.get("ok", True):
        st.success(f'{result.get("message", "처리 완료")} ({result.get("count", 0)}건)')
        st.rerun()
    else:
        st.warning(result.get("message", "처리하지 못했습니다."))


def clear_inventory_editor_buffer(key: str) -> None:
    st.session_state.pop(key, None)


def inject_inventory_css() -> None:
    st.markdown(
        """
        <style>
        .inventory-tab-title {
            color: #24384E;
            font-size: 1.15rem;
            font-weight: 850;
            margin: 0.5rem 0 0.65rem;
        }
        .st-key-inventory_nav_shell {
            background: transparent !important;
            border: 0 !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            margin: 0.15rem 0 0.9rem;
            padding: 0 !important;
        }
        .st-key-inventory_nav_shell [data-testid="stVerticalBlock"] {
            gap: 0.42rem;
        }
        .inventory-nav-caption {
            color: #536475;
            font-size: 0.76rem;
            font-weight: 850;
            letter-spacing: 0;
            margin: 0 0 0.14rem;
        }
        .st-key-inventory_nav_source,
        .st-key-inventory_nav_detail {
            border-left: 0 !important;
            margin-left: 0.9rem;
            padding-left: 0 !important;
        }
        .st-key-inventory_nav_detail {
            margin-left: 1.8rem;
        }
        .st-key-inventory_nav_shell div[data-testid="stPills"] div[role="radiogroup"],
        .st-key-inventory_nav_shell div[data-testid="stPills"] div[role="group"],
        .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] div[role="radiogroup"],
        .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] div[role="group"] {
            align-items: center !important;
            display: flex !important;
            flex-wrap: wrap !important;
            gap: 0.18rem 0.72rem !important;
            justify-content: flex-start !important;
        }
        .st-key-inventory_nav_shell div[data-testid="stPills"] label,
        .st-key-inventory_nav_shell div[data-testid="stPills"] button,
        .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label {
            background: transparent !important;
            border: 0 !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            flex: 0 0 auto !important;
            width: auto !important;
            min-width: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
        }
        .st-key-inventory_nav_shell div[data-testid="stPills"] label > div,
        .st-key-inventory_nav_shell div[data-testid="stPills"] button,
        .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label > div {
            background: transparent !important;
            border: 0 !important;
            border-bottom: 2px solid transparent !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            color: #46596A !important;
            min-height: 28px !important;
            padding: 0.18rem 0.06rem 0.24rem !important;
            white-space: nowrap !important;
        }
        .st-key-inventory_nav_shell div[data-testid="stPills"] label > div *,
        .st-key-inventory_nav_shell div[data-testid="stPills"] button *,
        .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label > div * {
            color: #46596A !important;
            -webkit-text-fill-color: #46596A !important;
            font-weight: 760 !important;
        }
        .st-key-inventory_nav_shell div[data-testid="stPills"] label:hover > div,
        .st-key-inventory_nav_shell div[data-testid="stPills"] button:hover,
        .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label:hover > div {
            background: transparent !important;
            border-bottom-color: #AEBCC8 !important;
        }
        .st-key-inventory_nav_shell div[data-testid="stPills"] label:has(input:checked) > div,
        .st-key-inventory_nav_shell div[data-testid="stPills"] label:has([aria-checked="true"]) > div,
        .st-key-inventory_nav_shell div[data-testid="stPills"] label[aria-checked="true"] > div,
        .st-key-inventory_nav_shell div[data-testid="stPills"] label[data-checked="true"] > div,
        .st-key-inventory_nav_shell div[data-testid="stPills"] button[aria-pressed="true"],
        .st-key-inventory_nav_shell div[data-testid="stPills"] [aria-selected="true"],
        .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label:has(input:checked) > div,
        .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label[aria-checked="true"] > div {
            background: transparent !important;
            border-bottom-color: #2F5D7C !important;
            color: #102033 !important;
            font-weight: 920 !important;
        }
        .st-key-inventory_nav_shell div[data-testid="stPills"] label:has(input:checked) > div *,
        .st-key-inventory_nav_shell div[data-testid="stPills"] label:has([aria-checked="true"]) > div *,
        .st-key-inventory_nav_shell div[data-testid="stPills"] label[aria-checked="true"] > div *,
        .st-key-inventory_nav_shell div[data-testid="stPills"] label[data-checked="true"] > div *,
        .st-key-inventory_nav_shell div[data-testid="stPills"] button[aria-pressed="true"] *,
        .st-key-inventory_nav_shell div[data-testid="stPills"] [aria-selected="true"] *,
        .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label:has(input:checked) > div *,
        .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label[aria-checked="true"] > div * {
            color: #102033 !important;
            -webkit-text-fill-color: #102033 !important;
            font-weight: 920 !important;
        }
        div[class*="st-key-inventory_control_"] {
            background: #EEF1F3;
            border: 1px solid #D3D9DE;
            border-radius: 7px;
            min-height: 190px;
            padding: 0.65rem 0.7rem 0.72rem;
        }
        div[class*="st-key-inventory_control_"] [data-testid="stVerticalBlock"] {
            gap: 0.38rem;
        }
        .inventory-control-label {
            align-items: center;
            color: #3D5368;
            display: flex;
            font-size: 0.84rem;
            font-weight: 800;
            height: 22px;
            line-height: 1.2;
            margin: 0;
            white-space: nowrap;
        }
        div[class*="st-key-inventory_control_"] [data-testid="stDateInput"] {
            min-height: 48px;
        }
        div[class*="st-key-inventory_control_"] [data-testid="stDateInput"] input {
            min-height: 48px;
        }
        div[class*="st-key-inventory_control_"] .stButton > button {
            min-height: 48px;
            height: 48px;
            white-space: normal;
        }
        .inventory-action-slot-empty {
            height: 48px;
            min-height: 48px;
        }
        div[class*="st-key-inventory_control_"] [data-testid="stFileUploader"] {
            margin-top: 0.44rem;
            min-height: 70px;
        }
        .st-key-inventory_dashboard_linked_panel {
            background: #EEF1F3;
            border: 1px solid #D3D9DE;
            border-radius: 8px;
            margin: 0.15rem 0 1.15rem;
            padding: 0.72rem 0.78rem;
        }
        .st-key-inventory_outbound_linked_panel {
            background: #EEF1F3;
            border: 1px solid #D3D9DE;
            border-radius: 8px;
            margin: 0.15rem 0 1.15rem;
            padding: 0.72rem 0.78rem;
        }
        .st-key-inventory_dashboard_linked_panel h3 {
            color: #24384E;
            font-size: 1.02rem;
            margin-bottom: 0.1rem;
        }
        .st-key-inventory_outbound_linked_panel h3 {
            color: #24384E;
            font-size: 1.02rem;
            margin-bottom: 0.1rem;
        }
        div[class*="st-key-inventory_control_"] [data-testid="stFileUploaderDropzone"] {
            min-height: 44px;
        }
        .inventory-upload-slot-empty {
            height: 118px;
            margin-top: 0.44rem;
        }
        div[class*="st-key-threepl_daily_summary_metrics"] [data-testid="stMetric"],
        div[class*="st-key-offline_daily_summary_metrics"] [data-testid="stMetric"],
        div[class*="st-key-warehouse_daily_summary_metrics"] [data-testid="stMetric"] {
            text-align: center !important;
        }
        div[class*="st-key-threepl_daily_summary_metrics"] [data-testid="stMetric"] label,
        div[class*="st-key-offline_daily_summary_metrics"] [data-testid="stMetric"] label,
        div[class*="st-key-warehouse_daily_summary_metrics"] [data-testid="stMetric"] label,
        div[class*="st-key-threepl_daily_summary_metrics"] [data-testid="stMetricLabel"],
        div[class*="st-key-offline_daily_summary_metrics"] [data-testid="stMetricLabel"],
        div[class*="st-key-warehouse_daily_summary_metrics"] [data-testid="stMetricLabel"],
        div[class*="st-key-threepl_daily_summary_metrics"] [data-testid="stMetricValue"],
        div[class*="st-key-offline_daily_summary_metrics"] [data-testid="stMetricValue"],
        div[class*="st-key-warehouse_daily_summary_metrics"] [data-testid="stMetricValue"] {
            justify-content: center !important;
            text-align: center !important;
            width: 100% !important;
        }
        div[class*="st-key-threepl_daily_summary_metrics"] [data-testid="stMetric"] [data-testid="stMetricValue"] > div,
        div[class*="st-key-offline_daily_summary_metrics"] [data-testid="stMetric"] [data-testid="stMetricValue"] > div,
        div[class*="st-key-warehouse_daily_summary_metrics"] [data-testid="stMetric"] [data-testid="stMetricValue"] > div {
            justify-content: center !important;
            text-align: center !important;
        }
        div[data-testid="stDataFrame"],
        div[data-testid="stDataEditor"] {
            width: 100% !important;
        }
        .inventory-visible-table-wrap {
            width: 100%;
            overflow: auto;
            background: #F1EEE8;
            border: 1px solid #D8D0C4;
            border-radius: 8px;
            margin: 0.2rem 0 0.75rem;
        }
        .inventory-visible-table {
            width: 100%;
            min-width: 1080px;
            border-collapse: collapse;
            color: #24384E;
            font-size: 0.82rem;
            table-layout: auto;
        }
        .inventory-visible-table th,
        .inventory-visible-table td {
            border-bottom: 1px solid #D8D0C4;
            border-right: 1px solid #E2DCD4;
            padding: 0.52rem 0.58rem;
            text-align: center;
            vertical-align: middle;
            white-space: nowrap;
            background: #FAF8F5;
        }
        .inventory-visible-table th {
            position: sticky;
            top: 0;
            z-index: 1;
            background: #E6E0D7;
            color: #26384A;
            font-weight: 850;
        }
        .inventory-visible-table tr:nth-child(even) td {
            background: #F4F1EB;
        }
        .inventory-visible-table td:last-child,
        .inventory-visible-table th:last-child {
            border-right: 0;
        }

        /* Enterprise inventory page redesign. Scoped to inventory page containers. */
        .stApp:has(.st-key-inventory_nav_shell) {
            background: #F7F8FA !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) [data-testid="stAppViewBlockContainer"] {
            max-width: 1540px !important;
            padding-top: 0.85rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell,
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_daily_header"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_update"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inbound_import_panel"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_table_actions"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_table_panel"] {
            background: #FFFFFF !important;
            border: 1px solid #E5E7EB !important;
            border-radius: 12px !important;
            box-shadow: 0 8px 20px rgba(17, 24, 39, 0.04) !important;
            color: #111827 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell {
            margin: 0 0 0.85rem !important;
            padding: 0.9rem 1rem 0.78rem !important;
        }
        .inventory-nav-head {
            align-items: center;
            border-bottom: 1px solid #EEF0F3;
            display: flex;
            justify-content: space-between;
            margin-bottom: 0.64rem;
            padding-bottom: 0.65rem;
        }
        .inventory-nav-head strong {
            color: #111827;
            display: block;
            font-size: 1.12rem;
            font-weight: 850;
            line-height: 1.1;
        }
        .inventory-nav-head span,
        .inventory-nav-head em {
            color: #6B7280;
            font-size: 0.74rem;
            font-style: normal;
            font-weight: 650;
        }
        .inventory-nav-caption {
            color: #6B7280 !important;
            font-size: 0.68rem !important;
            font-weight: 800 !important;
            margin: 0.24rem 0 0.1rem !important;
        }
        .st-key-inventory_nav_shell div[data-testid="stPills"] div[role="radiogroup"],
        .st-key-inventory_nav_shell div[data-testid="stPills"] div[role="group"],
        .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] div[role="radiogroup"],
        .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] div[role="group"] {
            gap: 0.1rem 0.7rem !important;
        }
        .st-key-inventory_nav_shell div[data-testid="stPills"] label > div,
        .st-key-inventory_nav_shell div[data-testid="stPills"] button,
        .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label > div {
            background: transparent !important;
            border: 0 !important;
            border-bottom: 2px solid transparent !important;
            border-radius: 0 !important;
            color: #4B5563 !important;
            min-height: 30px !important;
            padding: 0.18rem 0.02rem 0.28rem !important;
        }
        .st-key-inventory_nav_shell div[data-testid="stPills"] label:has(input:checked) > div,
        .st-key-inventory_nav_shell div[data-testid="stPills"] label[aria-checked="true"] > div,
        .st-key-inventory_nav_shell div[data-testid="stPills"] button[aria-pressed="true"],
        .st-key-inventory_nav_shell div[data-testid="stPills"] [aria-selected="true"],
        .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label:has(input:checked) > div,
        .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label[aria-checked="true"] > div {
            background: #F1F5F9 !important;
            border-bottom-color: #1E3A5F !important;
            color: #111827 !important;
        }
        .st-key-inventory_nav_shell div[data-testid="stPills"] label > div *,
        .st-key-inventory_nav_shell div[data-testid="stPills"] button *,
        .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label > div * {
            color: inherit !important;
            -webkit-text-fill-color: currentColor !important;
            font-weight: 780 !important;
        }

        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_daily_header"] {
            margin: 0 0 0.75rem !important;
            padding: 0.95rem 1rem !important;
        }
        .inventory-page-header span {
            color: #64748B;
            display: block;
            font-size: 0.72rem;
            font-weight: 800;
            margin-bottom: 0.24rem;
        }
        .inventory-page-header h1 {
            color: #111827;
            font-size: 1.55rem;
            font-weight: 900;
            letter-spacing: 0;
            line-height: 1.15;
            margin: 0;
        }
        .inventory-page-header p {
            color: #6B7280;
            font-size: 0.88rem;
            margin: 0.28rem 0 0;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_header"] [data-testid="stDateInput"] label p {
            color: #6B7280 !important;
            font-size: 0.7rem !important;
            font-weight: 800 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_header"] [data-testid="stDateInput"] input {
            background: #FFFFFF !important;
            border: 1px solid #D1D5DB !important;
            border-radius: 8px !important;
            color: #111827 !important;
            min-height: 36px !important;
        }

        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"] {
            margin: 0 0 0.72rem !important;
            padding: 0.95rem 1rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"] [data-testid="stHorizontalBlock"] {
            align-items: end !important;
            gap: 0.58rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"] label p,
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"] [data-testid="stWidgetLabel"] p {
            color: #6B7280 !important;
            font-size: 0.68rem !important;
            font-weight: 800 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"] input,
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"] [data-baseweb="select"] > div {
            background: #FFFFFF !important;
            border: 1px solid #D1D5DB !important;
            border-radius: 8px !important;
            color: #111827 !important;
            min-height: 38px !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"] [data-testid="stButton"] button,
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_update"] [data-testid="stButton"] button,
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inbound_import_panel"] [data-testid="stButton"] button,
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_table_actions"] [data-testid="stButton"] button,
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_table_panel"] [data-testid="stButton"] button {
            border-radius: 8px !important;
            font-weight: 800 !important;
            min-height: 38px !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"] [data-testid="stButton"] button[kind="primary"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_update"] [data-testid="stButton"] button[kind="primary"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inbound_import_panel"] [data-testid="stButton"] button[kind="primary"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_table_actions"] [data-testid="stButton"] button[kind="primary"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_table_panel"] [data-testid="stButton"] button[kind="primary"] {
            background: #1E3A5F !important;
            border-color: #1E3A5F !important;
            color: #FFFFFF !important;
        }

        .inventory-kpi-grid {
            display: grid;
            gap: 0.65rem;
            grid-template-columns: repeat(6, minmax(0, 1fr));
            margin: 0 0 0.72rem;
        }
        .inventory-kpi-card {
            background: #FFFFFF;
            border: 1px solid #E5E7EB;
            border-radius: 12px;
            box-shadow: 0 8px 20px rgba(17, 24, 39, 0.035);
            min-width: 0;
            padding: 0.8rem 0.9rem 0.72rem;
        }
        .inventory-kpi-label {
            align-items: center;
            color: #6B7280;
            display: flex;
            font-size: 0.72rem;
            font-weight: 800;
            gap: 0.38rem;
            line-height: 1;
            margin-bottom: 0.48rem;
            white-space: nowrap;
        }
        .inventory-kpi-label i {
            background: #94A3B8;
            border-radius: 999px;
            height: 8px;
            width: 8px;
        }
        .inventory-kpi-card.normal .inventory-kpi-label i { background: #16A34A; }
        .inventory-kpi-card.warning .inventory-kpi-label i { background: #D97706; }
        .inventory-kpi-card.short .inventory-kpi-label i { background: #DC2626; }
        .inventory-kpi-card.soldout .inventory-kpi-label i { background: #991B1B; }
        .inventory-kpi-card.unknown .inventory-kpi-label i { background: #64748B; }
        .inventory-kpi-card strong {
            color: #111827;
            display: block;
            font-size: clamp(1.45rem, 1.55vw, 2rem);
            font-weight: 900;
            letter-spacing: 0;
            line-height: 1;
        }
        .inventory-kpi-card em {
            color: #9CA3AF;
            display: block;
            font-size: 0.68rem;
            font-style: normal;
            font-weight: 750;
            margin-top: 0.28rem;
        }

        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_update"] {
            margin: 0 0 0.72rem !important;
            padding: 0.95rem 1rem 1rem !important;
        }
        .inventory-update-card {
            background: transparent !important;
            border: 0 !important;
            box-shadow: none !important;
            padding: 0 !important;
        }
        .inventory-card-heading {
            align-items: flex-start;
            display: flex;
            justify-content: space-between;
            margin-bottom: 0.72rem;
        }
        .inventory-card-heading h2,
        .inventory-table-title h2 {
            color: #111827;
            font-size: 1rem;
            font-weight: 900;
            line-height: 1.15;
            margin: 0;
        }
        .inventory-card-heading p,
        .inventory-table-title span {
            color: #6B7280;
            font-size: 0.78rem;
            margin: 0.2rem 0 0;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_inventory_update"] [data-testid="stFileUploaderDropzone"] {
            background: #F8FAFC !important;
            border: 1px dashed #CBD5E1 !important;
            border-radius: 10px !important;
            min-height: 76px !important;
            padding: 0.65rem !important;
        }

        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_table_actions"] {
            margin: 0 0 0.45rem !important;
            padding: 0.78rem 0.9rem !important;
        }
        .inventory-table-title {
            align-items: baseline;
            display: flex;
            gap: 0.65rem;
            min-height: 38px;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_inventory_table_actions"] [data-testid="stRadio"] label {
            border-radius: 8px !important;
        }

        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_table_panel"] {
            padding: 0.55rem 0.65rem 0.75rem !important;
        }
        .inventory-visible-table-wrap {
            background: #FFFFFF !important;
            border: 1px solid #E5E7EB !important;
            border-radius: 10px !important;
            margin: 0 !important;
        }
        .inventory-visible-table {
            color: #111827 !important;
            font-size: 0.8rem !important;
            min-width: 1180px !important;
        }
        .inventory-visible-table th,
        .inventory-visible-table td {
            background: #FFFFFF !important;
            border-bottom: 1px solid #EEF0F3 !important;
            border-right: 0 !important;
            color: #111827 !important;
            padding: 0.58rem 0.66rem !important;
            text-align: left !important;
        }
        .inventory-visible-table th {
            background: #F8FAFC !important;
            color: #475569 !important;
            font-size: 0.72rem !important;
            letter-spacing: 0;
            position: sticky;
            text-transform: none;
            top: 0;
            z-index: 2;
        }
        .inventory-visible-table tbody tr:hover td {
            background: #F8FBFF !important;
        }
        .inventory-table-status {
            align-items: center;
            border: 1px solid #CBD5E1;
            border-radius: 999px;
            display: inline-flex;
            font-size: 0.68rem;
            font-weight: 850;
            line-height: 1;
            padding: 0.24rem 0.48rem;
            white-space: nowrap;
        }
        .inventory-table-status.normal { background: #ECFDF3; border-color: #BBF7D0; color: #166534; }
        .inventory-table-status.warning { background: #FFFBEB; border-color: #FDE68A; color: #92400E; }
        .inventory-table-status.short { background: #FEF2F2; border-color: #FECACA; color: #991B1B; }
        .inventory-table-status.soldout { background: #7F1D1D; border-color: #7F1D1D; color: #FFFFFF !important; font-weight: 900 !important; }
        .inventory-table-status.unknown { background: #F1F5F9; border-color: #CBD5E1; color: #475569; }

        @media (max-width: 1180px) {
            .inventory-kpi-grid {
                grid-template-columns: repeat(3, minmax(0, 1fr));
            }
            .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"] [data-testid="stHorizontalBlock"] {
                flex-wrap: wrap !important;
            }
        }
        @media (max-width: 760px) {
            .inventory-nav-head,
            .inventory-table-title {
                align-items: flex-start;
                flex-direction: column;
            }
            .inventory-kpi-grid {
                grid-template-columns: 1fr 1fr;
            }
        }

        /* Final inventory lookup overrides: keep this page compact and table-first. */
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell {
            background: transparent !important;
            border: 0 !important;
            border-bottom: 1px solid #E5E7EB !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            margin: 0 0 0.62rem !important;
            padding: 0 0 0.34rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .inventory-nav-head,
        .stApp:has(.st-key-inventory_nav_shell) .inventory-nav-caption {
            display: none !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source {
            margin-left: 0.72rem !important;
            padding-left: 0 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail {
            margin-left: 1.44rem !important;
            padding-left: 0 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] label > div,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] button,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label > div {
            background: transparent !important;
            border-bottom: 2px solid transparent !important;
            border-radius: 0 !important;
            min-height: 27px !important;
            padding: 0.12rem 0.04rem 0.2rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] label:has(input:checked) > div,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] label[aria-checked="true"] > div,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] button[aria-pressed="true"],
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] [aria-selected="true"],
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label:has(input:checked) > div,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label[aria-checked="true"] > div {
            background: transparent !important;
            border-bottom-color: #1E3A5F !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_daily_header"] {
            background: transparent !important;
            border: 0 !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            margin: 0 0 0.52rem !important;
            padding: 0 !important;
        }
        .inventory-page-header h1 {
            font-size: 1.08rem !important;
            line-height: 1.2 !important;
            margin: 0.12rem 0 0 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"] {
            border-radius: 8px !important;
            margin: 0 0 0.58rem !important;
            padding: 0.72rem 0.82rem !important;
        }
        .st-key-inventory_kpi_native {
            margin: 0 0 0.62rem !important;
        }
        .st-key-inventory_kpi_native [data-testid="stMetric"] {
            background: #FFFFFF !important;
            border: 1px solid #E5E7EB !important;
            border-left: 4px solid #52697F !important;
            border-radius: 8px !important;
            min-height: 78px !important;
            padding: 0.62rem 0.72rem !important;
        }
        .st-key-inventory_kpi_native [data-testid="stMetricLabel"] p {
            color: #64748B !important;
            font-size: 0.72rem !important;
            font-weight: 850 !important;
        }
        .st-key-inventory_kpi_native [data-testid="stMetricValue"] {
            color: #111827 !important;
            font-size: 1.38rem !important;
            font-weight: 900 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_table_actions"] {
            border-radius: 8px !important;
            margin: 0 0 0.32rem !important;
            padding: 0.58rem 0.72rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_update"] {
            border-radius: 8px !important;
            margin: 0.58rem 0 0 !important;
            padding: 0.45rem 0.72rem !important;
        }
        .inventory-table-status.soldout {
            background: #7F1D1D !important;
            border-color: #7F1D1D !important;
            color: #FFFFFF !important;
            font-weight: 900 !important;
        }
        @media (max-width: 1280px) {
            .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"] [data-testid="stHorizontalBlock"],
            .stApp:has(.st-key-inventory_nav_shell) div[class*="_inventory_table_actions"] [data-testid="stHorizontalBlock"] {
                flex-wrap: wrap !important;
            }
        }
        @media (max-width: 1024px) {
            .st-key-inventory_kpi_native [data-testid="stHorizontalBlock"] {
                flex-wrap: wrap !important;
            }
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source,
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail {
                margin-left: 0 !important;
            }
        }

        /* Match the portal home background instead of isolated white panels. */
        .stApp:has(.st-key-inventory_nav_shell) {
            background:
                radial-gradient(circle at 48% 4%, rgba(18, 155, 139, 0.16), transparent 34%),
                radial-gradient(circle at 76% 48%, rgba(13, 107, 99, 0.1), transparent 30%),
                linear-gradient(135deg, #F8F7F4 0%, #FBFAF8 44%, #FAF8F5 100%) !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_table_actions"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_table_panel"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_update"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inbound_import_panel"] {
            background: #FAF8F5 !important;
            border-color: #D8D0C4 !important;
            box-shadow: 0 8px 18px rgba(52, 44, 34, 0.045) !important;
        }
        .st-key-inventory_kpi_native [data-testid="stMetric"] {
            background: #FAF8F5 !important;
            border-color: #D8D0C4 !important;
            border-left-color: #52697F !important;
            box-shadow: 0 8px 18px rgba(52, 44, 34, 0.04) !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"] input,
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"] [data-baseweb="select"] > div,
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_header"] [data-testid="stDateInput"] input {
            background: #FFFEFC !important;
            border-color: #CFC5B7 !important;
        }
        .inventory-visible-table-wrap {
            background: #F2EFEA !important;
            border-color: #D8D0C4 !important;
        }
        .inventory-visible-table th {
            background: #EDE8E1 !important;
            color: #26384A !important;
        }
        .inventory-visible-table td {
            background: #FAF8F5 !important;
            border-bottom-color: #E2DCD4 !important;
        }
        .inventory-visible-table tr:nth-child(even) td {
            background: #F4F1EB !important;
        }
        .inventory-visible-table tbody tr:hover td {
            background: #F1EEE8 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"] [data-testid="stButton"] button:not([kind="primary"]),
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_update"] [data-testid="stButton"] button:not([kind="primary"]),
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inbound_import_panel"] [data-testid="stButton"] button:not([kind="primary"]),
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_table_actions"] [data-testid="stButton"] button:not([kind="primary"]),
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_table_panel"] [data-testid="stButton"] button:not([kind="primary"]) {
            background: #E8E3DC !important;
            border-color: #CFC5B7 !important;
            color: #2F4051 !important;
        }

        /* Reference redesign: structured SCM inventory screen in portal colors. */
        .stApp:has(.st-key-inventory_nav_shell) [data-testid="stAppViewBlockContainer"] {
            max-width: 1680px !important;
            padding-left: clamp(1rem, 1.45vw, 1.55rem) !important;
            padding-right: clamp(1rem, 1.45vw, 1.55rem) !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell {
            background: transparent !important;
            border-bottom: 1px solid #D8D0C4 !important;
            margin-bottom: 1.42rem !important;
            padding-bottom: 0.9rem !important;
        }
        .inventory-module-rail {
            align-items: stretch;
            border-bottom: 1px solid #D8D0C4;
            display: grid;
            grid-template-columns: repeat(5, minmax(120px, 1fr));
            margin: 0 0 1rem;
        }
        .inventory-module-item {
            align-items: center;
            color: #2F4051;
            display: flex;
            font-size: 0.98rem;
            font-weight: 850;
            gap: 0.55rem;
            justify-content: center;
            min-height: 54px;
            padding: 0 0.7rem;
            position: relative;
        }
        .inventory-module-item i {
            color: #52697F;
            font-style: normal;
            font-weight: 900;
        }
        .inventory-module-item.active {
            color: #0F2B54;
        }
        .inventory-module-item.active::after {
            background: #0F2B54;
            bottom: -1px;
            content: "";
            height: 3px;
            left: 9%;
            position: absolute;
            right: 9%;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] div[role="radiogroup"],
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] div[role="group"],
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] div[role="radiogroup"],
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] div[role="group"] {
            gap: 0.55rem 1.05rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] label > div,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] button,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label > div {
            border: 0 !important;
            border-radius: 999px !important;
            min-height: 42px !important;
            padding: 0.52rem 1rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] label:has(input:checked) > div,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] label[aria-checked="true"] > div,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] button[aria-pressed="true"],
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] [aria-selected="true"],
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label:has(input:checked) > div,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label[aria-checked="true"] > div {
            background: #0F2B54 !important;
            color: #FFFFFF !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source div[data-testid="stPills"] label:has(input:checked) > div,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source div[data-testid="stPills"] label[aria-checked="true"] > div,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source div[data-testid="stPills"] button[aria-pressed="true"],
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source div[data-testid="stPills"] [aria-selected="true"],
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail div[data-testid="stPills"] label:has(input:checked) > div,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail div[data-testid="stPills"] label[aria-checked="true"] > div,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail div[data-testid="stPills"] button[aria-pressed="true"],
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail div[data-testid="stPills"] [aria-selected="true"] {
            background: transparent !important;
            color: #0F2B54 !important;
            font-weight: 900 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail {
            margin-left: 0 !important;
        }
        .inventory-page-header h1 {
            color: #0F2B54 !important;
            font-size: clamp(1.65rem, 1.75vw, 2.1rem) !important;
            font-weight: 950 !important;
            margin: 0 0 0.42rem !important;
        }
        .inventory-page-header p {
            color: #52697F !important;
            font-size: 0.94rem !important;
            margin: 0 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_update"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inbound_import_panel"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_table_actions"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_table_panel"] {
            border-radius: 8px !important;
            box-shadow: 0 12px 28px rgba(48, 40, 31, 0.08) !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"] {
            margin-bottom: 1.05rem !important;
            padding: 1.15rem 1.28rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_update"] {
            margin: 0 0 1.1rem !important;
            padding: 1.15rem 1.28rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inbound_import_panel"] {
            margin: 0 0 0.82rem !important;
            padding: 0.82rem 0.95rem !important;
        }
        .inventory-update-heading h2 {
            color: #0F2B54;
            font-size: 1.18rem;
            font-weight: 950;
            margin: 0 0 0.25rem;
        }
        .inventory-update-heading p {
            color: #52697F;
            font-size: 0.88rem;
            margin: 0 0 0.95rem;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_inventory_update"] [data-testid="stFileUploaderDropzone"] {
            background: #FFFEFC !important;
            border: 1px dashed #9FB3CA !important;
            border-radius: 8px !important;
            min-height: 96px !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_inbound_import_panel"] [data-testid="stFileUploaderDropzone"] {
            background: #FFFEFC !important;
            border: 1px dashed #9FB3CA !important;
            border-radius: 8px !important;
            min-height: 64px !important;
            padding: 0.48rem 0.6rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_inventory_update"] [data-testid="stAlert"] {
            background: #EEF3F7 !important;
            border-color: #D6E0EA !important;
            color: #0F2B54 !important;
        }
        .inventory-design-kpis {
            display: grid;
            gap: 1rem;
            grid-template-columns: repeat(6, minmax(0, 1fr));
            margin: 0 0 1.1rem;
        }
        .inventory-design-kpi {
            align-items: center;
            background: #FAF8F5;
            border: 1px solid #D8D0C4;
            border-radius: 8px;
            box-shadow: 0 12px 28px rgba(48, 40, 31, 0.075);
            display: flex;
            justify-content: space-between;
            min-height: 112px;
            padding: 1rem 1.2rem;
        }
        .inventory-design-kpi span {
            color: #0F2B54;
            display: block;
            font-size: 0.86rem;
            font-weight: 900;
            margin-bottom: 0.44rem;
        }
        .inventory-design-kpi strong {
            color: #0F2B54;
            display: block;
            font-size: 2rem;
            font-weight: 950;
            letter-spacing: 0;
            line-height: 1;
        }
        .inventory-design-kpi em {
            color: #52697F;
            display: block;
            font-size: 0.82rem;
            font-style: normal;
            margin-top: 0.42rem;
        }
        .inventory-design-kpi i {
            align-items: center;
            background: #E8EEF7;
            border-radius: 999px;
            color: #52697F;
            display: inline-flex;
            flex: 0 0 46px;
            font-size: 1.45rem;
            font-style: normal;
            font-weight: 900;
            height: 46px;
            justify-content: center;
            width: 46px;
        }
        .inventory-design-kpi.normal strong,
        .inventory-design-kpi.normal i { color: #26844A; }
        .inventory-design-kpi.warning strong,
        .inventory-design-kpi.warning i { color: #C05A1A; }
        .inventory-design-kpi.short strong,
        .inventory-design-kpi.short i { color: #4F7BC4; }
        .inventory-design-kpi.soldout strong,
        .inventory-design-kpi.soldout i { color: #D83939; }
        .inventory-design-kpi.unknown strong,
        .inventory-design-kpi.unknown i { color: #6B7280; }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_table_actions"] {
            margin: 0 0 1rem !important;
            padding: 1rem 1.28rem !important;
        }
        .inventory-table-title h2 {
            color: #0F2B54 !important;
            font-size: 1.2rem !important;
        }
        .inventory-table-title span {
            background: #EEF3F7;
            border-radius: 999px;
            color: #0F2B54 !important;
            font-weight: 800;
            padding: 0.22rem 0.65rem;
        }
        @media (max-width: 1366px) {
            .inventory-design-kpis {
                grid-template-columns: repeat(3, minmax(0, 1fr));
            }
        }
        @media (max-width: 900px) {
            .inventory-module-rail {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .inventory-design-kpis {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }

        /* Kill the Streamlit pill look in inventory navigation. */
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[class*="st-key-inventory_main_section_"] [data-testid="stButton"] button,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[class*="st-key-inventory_current_source_"] [data-testid="stButton"] button,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[class*="st-key-inventory_threepl_section_"] [data-testid="stButton"] button,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[class*="st-key-inventory_offline_section_"] [data-testid="stButton"] button,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[class*="st-key-inventory_warehouse_section_"] [data-testid="stButton"] button {
            background: transparent !important;
            border: 0 !important;
            border-bottom: 2px solid transparent !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            color: #2F4051 !important;
            min-height: 34px !important;
            padding: 0.24rem 0 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[class*="st-key-inventory_main_section_"] [data-testid="stButton"] button *,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[class*="st-key-inventory_current_source_"] [data-testid="stButton"] button *,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[class*="st-key-inventory_threepl_section_"] [data-testid="stButton"] button *,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[class*="st-key-inventory_offline_section_"] [data-testid="stButton"] button *,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[class*="st-key-inventory_warehouse_section_"] [data-testid="stButton"] button * {
            color: inherit !important;
            font-weight: 820 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[class*="_active"] [data-testid="stButton"] button {
            border-bottom-color: #0F2B54 !important;
            color: #0F2B54 !important;
            font-weight: 950 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source {
            border-bottom: 1px solid #D8D0C4 !important;
            margin: 0 0 0.55rem !important;
            padding: 0 0 0.42rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source [data-testid="stButton"] button {
            font-size: 1.04rem !important;
            min-height: 44px !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail {
            margin: 0 !important;
            padding: 0 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail [data-testid="stButton"] button {
            font-size: 0.96rem !important;
            min-height: 36px !important;
        }

        /* Final compact inventory navigation and filters. */
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell {
            background: #FAF8F5 !important;
            border: 1px solid #D8D0C4 !important;
            border-radius: 8px !important;
            box-shadow: 0 8px 18px rgba(52, 44, 34, 0.045) !important;
            margin: 0 0 0.64rem !important;
            padding: 0.46rem 0.62rem 0.42rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source {
            border-bottom: 1px solid #D8D0C4 !important;
            margin: 0 0 0.28rem !important;
            padding: 0 0 0.28rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source [data-testid="stHorizontalBlock"],
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail [data-testid="stHorizontalBlock"] {
            gap: 0.26rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail [data-testid="stHorizontalBlock"] {
            gap: 0.42rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source [data-testid="stButton"] button {
            background: #FFFEFC !important;
            border: 1px solid #D8D0C4 !important;
            border-left: 3px solid #8A9CAF !important;
            border-radius: 8px !important;
            box-shadow: 0 4px 12px rgba(48, 40, 31, 0.045) !important;
            color: #2F4051 !important;
            font-size: 0.8rem !important;
            font-weight: 850 !important;
            min-height: 30px !important;
            padding: 0.18rem 0.34rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source div[class*="_active"] [data-testid="stButton"] button {
            background: #EEF3F7 !important;
            border-color: #9FB3CA !important;
            border-left-color: #0F2B54 !important;
            color: #0F2B54 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail {
            margin: 0 !important;
            padding: 0 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail [data-testid="stButton"] button {
            background: transparent !important;
            border: 0 !important;
            border-bottom: 2px solid transparent !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            color: #52697F !important;
            font-size: 0.76rem !important;
            font-weight: 780 !important;
            min-height: 24px !important;
            padding: 0.04rem 0.02rem 0.12rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail div[class*="_active"] [data-testid="stButton"] button {
            border-bottom-color: #0F2B54 !important;
            color: #0F2B54 !important;
            font-weight: 900 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"] {
            margin-bottom: 0.72rem !important;
            padding: 0.72rem 0.82rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"] [data-testid="stPopover"] button,
        div[class*="product_master_"][class*="_categories_toggle_dropdown"] [data-testid="stPopover"] button {
            min-height: 34px !important;
            padding: 0.32rem 0.48rem !important;
            font-size: 0.78rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"] input,
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"] [data-baseweb="select"] > div {
            min-height: 34px !important;
        }
        .inventory-page-header h1 {
            font-size: 1.36rem !important;
        }
        .inventory-page-header p {
            font-size: 0.84rem !important;
        }

        /* Final inventory navigation: underline text tabs, no pill/card buttons, viewport-safe wrapping. */
        .stApp:has(.st-key-inventory_nav_shell) .inventory-text-tabs {
            align-items: flex-end !important;
            box-sizing: border-box !important;
            display: flex !important;
            flex-flow: row wrap !important;
            gap: 8px 16px !important;
            justify-content: flex-start !important;
            max-width: 100% !important;
            min-width: 0 !important;
            overflow: visible !important;
            padding: 0 !important;
            width: 100% !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .inventory-text-tab {
            align-items: center !important;
            background: transparent !important;
            border: 0 !important;
            border-bottom: 2px solid transparent !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            box-sizing: border-box !important;
            color: #52697F !important;
            display: inline-flex !important;
            flex: 0 0 auto !important;
            font-size: 0.9rem !important;
            font-weight: 820 !important;
            justify-content: center !important;
            line-height: 1.2 !important;
            min-height: 34px !important;
            min-width: 72px !important;
            padding: 0.22rem 0.04rem 0.28rem !important;
            text-align: center !important;
            text-decoration: none !important;
            white-space: nowrap !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .inventory-text-tab:hover {
            border-bottom-color: #9FB3CA !important;
            color: #2F4051 !important;
            text-decoration: none !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .inventory-text-tab.active {
            border-bottom-color: #0F2B54 !important;
            color: #0F2B54 !important;
            font-weight: 950 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source .inventory-text-tab {
            font-size: 0.96rem !important;
            min-height: 38px !important;
            min-width: 88px !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail .inventory-text-tab {
            min-width: 74px !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_header"] + div .inventory-text-tabs,
        .stApp:has(.st-key-inventory_nav_shell) .inventory-page-header + .inventory-text-tabs {
            margin-top: 0.58rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_header"] [data-testid="stDateInput"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_header"] [data-testid="stDateInput"] [data-baseweb="input"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_header"] [data-testid="stDateInput"] [data-baseweb="input"] > div,
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_header"] [data-testid="stDateInput"] input {
            background: #FAF8F5 !important;
            background-color: #FAF8F5 !important;
            border-color: #E4DED6 !important;
            box-shadow: none !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_header"] [data-testid="stDateInput"] input {
            color: #26384A !important;
            -webkit-text-fill-color: #26384A !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_date_wrapper"] {
            width: fit-content !important;
            max-width: 230px !important;
            margin: 0 0 0.72rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_date_wrapper"] [data-testid="stDateInput"] {
            width: 210px !important;
            max-width: 210px !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_date_wrapper"] [data-testid="stDateInput"] > div,
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_date_wrapper"] [data-testid="stDateInput"] [data-baseweb="input"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_date_wrapper"] [data-testid="stDateInput"] [data-baseweb="input"] > div {
            width: 210px !important;
            max-width: 210px !important;
            background: #FAF8F5 !important;
            background-color: #FAF8F5 !important;
            border-color: #E4DED6 !important;
            border-radius: 8px !important;
            box-shadow: none !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_date_wrapper"] [data-testid="stDateInput"] label p {
            margin-bottom: 0.22rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_date_wrapper"] [data-testid="stDateInput"] input {
            width: 210px !important;
            max-width: 210px !important;
            min-height: 36px !important;
            background: #FAF8F5 !important;
            background-color: #FAF8F5 !important;
            color: #26384A !important;
            -webkit-text-fill-color: #26384A !important;
            box-shadow: none !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_date_wrapper"] [data-testid="stDateInput"] input::-webkit-datetime-edit,
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_date_wrapper"] [data-testid="stDateInput"] input::-webkit-datetime-edit-fields-wrapper,
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_date_wrapper"] [data-testid="stDateInput"] input::-webkit-datetime-edit-year-field,
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_date_wrapper"] [data-testid="stDateInput"] input::-webkit-datetime-edit-month-field,
        .stApp:has(.st-key-inventory_nav_shell) div[class*="_daily_date_wrapper"] [data-testid="stDateInput"] input::-webkit-datetime-edit-day-field {
            background: transparent !important;
            background-color: transparent !important;
            color: #26384A !important;
            -webkit-text-fill-color: #26384A !important;
        }
        @media (max-width: 1024px) {
            .stApp:has(.st-key-inventory_nav_shell) .inventory-text-tabs {
                gap: 8px 14px !important;
            }
        }
        @media (max-width: 768px) {
            .stApp:has(.st-key-inventory_nav_shell) .inventory-text-tabs {
                gap: 7px 12px !important;
            }
            .stApp:has(.st-key-inventory_nav_shell) .inventory-text-tab {
                min-width: 76px !important;
            }
        }
        @media (max-width: 480px) {
            .stApp:has(.st-key-inventory_nav_shell) .inventory-text-tabs {
                gap: 7px 10px !important;
            }
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail .inventory-text-tab,
            .stApp:has(.st-key-inventory_nav_shell) .inventory-text-tabs-threepl_inventory_workflow .inventory-text-tab,
            .stApp:has(.st-key-inventory_nav_shell) .inventory-text-tabs-offline_inventory_workflow .inventory-text-tab,
            .stApp:has(.st-key-inventory_nav_shell) .inventory-text-tabs-warehouse_inventory_workflow .inventory-text-tab {
                min-width: calc((100% - 10px) / 2) !important;
            }
        }

        /* Viewport-safe inventory tabs. Keep every inventory navigation level on the same underline tab system. */
        .stApp:has(.st-key-inventory_nav_shell),
        .stApp:has(.st-key-inventory_nav_shell) [data-testid="stAppViewBlockContainer"],
        .stApp:has(.st-key-inventory_nav_shell) [data-testid="stMainBlockContainer"],
        .stApp:has(.st-key-inventory_nav_shell) .block-container {
            box-sizing: border-box !important;
            max-width: 100% !important;
            min-width: 0 !important;
            overflow-x: hidden !important;
            width: 100% !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail,
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_daily_header"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-inventory_filter_"][class*="_panel"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_table_actions"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_table_panel"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inventory_update"],
        .stApp:has(.st-key-inventory_nav_shell) div[class*="st-key-"][class*="_inbound_import_panel"] {
            box-sizing: border-box !important;
            max-width: 100% !important;
            min-width: 0 !important;
            width: 100% !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell {
            overflow-x: hidden !important;
            padding: clamp(0.42rem, 1.1vw, 0.7rem) clamp(0.48rem, 1.4vw, 0.9rem) !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source {
            border-bottom: 1px solid #D8D0C4 !important;
            margin: 0 0 0.42rem !important;
            padding: 0 0 0.42rem !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail {
            margin: 0 !important;
            padding: 0 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"],
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] {
            display: block !important;
            max-width: 100% !important;
            min-width: 0 !important;
            overflow: visible !important;
            width: 100% !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] div[role="radiogroup"],
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] div[role="group"],
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] div[role="radiogroup"],
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] div[role="group"] {
            align-items: flex-end !important;
            display: flex !important;
            flex-flow: row wrap !important;
            gap: 8px 16px !important;
            justify-content: flex-start !important;
            max-width: 100% !important;
            min-width: 0 !important;
            overflow: visible !important;
            width: 100% !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] label,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] button,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label {
            background: transparent !important;
            border: 0 !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            box-sizing: border-box !important;
            flex: 0 0 auto !important;
            margin: 0 !important;
            max-width: 100% !important;
            min-width: 72px !important;
            padding: 0 !important;
            white-space: nowrap !important;
            width: auto !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source div[data-testid="stPills"] label,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source div[data-testid="stPills"] button,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source div[data-testid="stSegmentedControl"] label {
            min-width: 86px !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail div[data-testid="stPills"] label,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail div[data-testid="stPills"] button,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail div[data-testid="stSegmentedControl"] label {
            min-width: 84px !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] label > div,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] button,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label > div {
            align-items: center !important;
            background: transparent !important;
            border: 0 !important;
            border-bottom: 2px solid transparent !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            box-sizing: border-box !important;
            color: #52697F !important;
            display: inline-flex !important;
            font-size: 0.88rem !important;
            font-weight: 780 !important;
            justify-content: center !important;
            line-height: 1.2 !important;
            min-height: 34px !important;
            overflow: visible !important;
            padding: 0.26rem 0.06rem 0.32rem !important;
            text-align: center !important;
            white-space: nowrap !important;
            width: 100% !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] label > div *,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] button *,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label > div * {
            color: inherit !important;
            font-size: inherit !important;
            font-weight: inherit !important;
            line-height: 1.2 !important;
            max-width: 100% !important;
            overflow: visible !important;
            text-overflow: clip !important;
            white-space: nowrap !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] label:hover > div,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] button:hover,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label:hover > div {
            background: transparent !important;
            border-bottom-color: #9FB3CA !important;
            color: #2F4051 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] label:has(input:checked) > div,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] label:has([aria-checked="true"]) > div,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] label[aria-checked="true"] > div,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] label[data-checked="true"] > div,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] button[aria-pressed="true"],
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] [aria-selected="true"],
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label:has(input:checked) > div,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label[aria-checked="true"] > div {
            background: transparent !important;
            border-bottom-color: #0F2B54 !important;
            color: #0F2B54 !important;
            font-weight: 900 !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source div[data-testid="stPills"] label > div,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source div[data-testid="stPills"] button,
        .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_source div[data-testid="stSegmentedControl"] label > div {
            font-size: 0.95rem !important;
            min-height: 38px !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .inventory-page-header,
        .stApp:has(.st-key-inventory_nav_shell) .inventory-page-header * {
            box-sizing: border-box !important;
            max-width: 100% !important;
            min-width: 0 !important;
            overflow-wrap: break-word !important;
        }
        .stApp:has(.st-key-inventory_nav_shell) .inventory-page-header h1,
        .stApp:has(.st-key-inventory_nav_shell) .inventory-page-header p {
            max-width: 100% !important;
        }
        @media (max-width: 1024px) {
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] div[role="radiogroup"],
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] div[role="group"],
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] div[role="radiogroup"],
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] div[role="group"] {
                gap: 8px 14px !important;
            }
        }
        @media (max-width: 768px) {
            .stApp:has(.st-key-inventory_nav_shell) [data-testid="stAppViewBlockContainer"] {
                padding-left: 0.75rem !important;
                padding-right: 0.75rem !important;
            }
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell {
                margin-bottom: 0.72rem !important;
            }
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail div[data-testid="stPills"] div[role="radiogroup"],
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail div[data-testid="stPills"] div[role="group"],
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail div[data-testid="stSegmentedControl"] div[role="radiogroup"],
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail div[data-testid="stSegmentedControl"] div[role="group"] {
                gap: 7px 12px !important;
            }
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] label,
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] button,
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label {
                min-width: 80px !important;
            }
        }
        @media (max-width: 480px) {
            .stApp:has(.st-key-inventory_nav_shell) [data-testid="stAppViewBlockContainer"] {
                padding-left: 0.56rem !important;
                padding-right: 0.56rem !important;
            }
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell {
                padding-left: 0.44rem !important;
                padding-right: 0.44rem !important;
            }
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] div[role="radiogroup"],
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] div[role="group"],
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] div[role="radiogroup"],
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] div[role="group"] {
                gap: 7px 10px !important;
            }
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail div[data-testid="stPills"] label,
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail div[data-testid="stPills"] button,
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_detail div[data-testid="stSegmentedControl"] label {
                min-width: calc((100% - 10px) / 2) !important;
            }
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] label > div,
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stPills"] button,
            .stApp:has(.st-key-inventory_nav_shell) .st-key-inventory_nav_shell div[data-testid="stSegmentedControl"] label > div {
                font-size: 0.82rem !important;
                min-height: 32px !important;
            }
            .stApp:has(.st-key-inventory_nav_shell) .inventory-page-header h1 {
                font-size: 1.12rem !important;
            }
            .stApp:has(.st-key-inventory_nav_shell) .inventory-page-header p {
                font-size: 0.78rem !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# Inventory page refresh overrides.
# The original blocks above are kept for compatibility, but the inventory lookup
# screen now uses the normalized Korean labels and the consolidated update flow.
SOURCE_TYPES = ["3PL", "오프라인", "창고"]
SOURCE_KEY_MAP = {"3PL": "threepl", "오프라인": "offline", "창고": "warehouse"}
DAILY_COLUMNS = ["선택", *INVENTORY_STATUS_DISPLAY_COLUMNS]
INVENTORY_CURRENT_SOURCES = ["3PL", "오프라인", "창고"]
INVENTORY_SOURCE_MAP = {"3PL": "3PL", "오프라인": "오프라인", "창고": "창고"}
INVENTORY_SOURCE_TABS = ["재고", "입고", "출고", "대시보드", "마스터"]


def master_title(source_type: str) -> str:
    return "창고 마스터" if source_type == "창고" else f"{source_type} 마스터"


def render_inventory_page_lazy() -> None:
    selected_source, selected_tab = render_inventory_navigation()
    source_type = INVENTORY_SOURCE_MAP.get(selected_source, "3PL")
    if selected_tab == "재고":
        render_daily_tab(source_type, selected_source)
    elif selected_tab == "입고":
        render_inbound_tab(source_type)
    elif selected_tab == "출고":
        render_outbound_tab(source_type)
    elif selected_tab == "대시보드":
        render_inventory_dashboard_tab(source_type)
    elif selected_tab == "마스터":
        product_master_page.render_master_tab(source_type, master_title(source_type))


def inventory_nav_token(value: str) -> str:
    token = re.sub(r"[^0-9A-Za-z]+", "_", str(value)).strip("_").lower()
    return token or "item"


def inventory_tab_query_key(key: str) -> str:
    return f"inventory_tab_{inventory_nav_token(key)}"


def inventory_tab_href(key: str, label: str) -> str:
    params = {}
    try:
        for name, value in st.query_params.items():
            params[name] = value[-1] if isinstance(value, list) and value else value
    except Exception:
        params = {}
    params[inventory_tab_query_key(key)] = label
    return "?" + urlencode(params, doseq=False)


def inventory_text_tab_selector(
    options: list[str],
    key: str,
    default: str,
    item_weight: float = 1.0,
    trailing_weight: float = 0.0,
    item_weights: list[float] | None = None,
) -> str:
    _ = (item_weight, trailing_weight, item_weights)
    labels = [str(option) for option in options]
    state_key = f"{key}_selected"
    query_selected = query_value(inventory_tab_query_key(key))
    current = query_selected if query_selected in labels else st.session_state.get(state_key) or default or (labels[0] if labels else "")
    if current not in labels and labels:
        current = labels[0]
    st.session_state[state_key] = current
    if not labels:
        return ""

    items = []
    for index, label in enumerate(labels):
        active = "active" if label == current else "idle"
        href = inventory_tab_href(key, label)
        items.append(
            f'<a class="inventory-text-tab {active}" href="{escape(href, quote=True)}" target="_self" data-index="{index}">{escape(label)}</a>'
        )
    st.markdown(
        f'<nav class="inventory-text-tabs inventory-text-tabs-{inventory_nav_token(key)}" aria-label="{escape(key)}">{"".join(items)}</nav>',
        unsafe_allow_html=True,
    )
    return current


def render_inventory_navigation() -> tuple[str, str]:
    with st.container(key="inventory_nav_shell"):
        with st.container(key="inventory_nav_source"):
            selected_source = inventory_text_tab_selector(
                INVENTORY_CURRENT_SOURCES,
                "inventory_current_source",
                default=st.session_state.get("inventory_active_source") or "3PL",
                item_weight=0.42,
                trailing_weight=7.8,
            )
        if selected_source not in INVENTORY_CURRENT_SOURCES:
            selected_source = "3PL"
        st.session_state["inventory_active_source"] = selected_source

        source_type = INVENTORY_SOURCE_MAP.get(selected_source, "3PL")
        with st.container(key="inventory_nav_detail"):
            selected_tab = inventory_text_tab_selector(
                INVENTORY_SOURCE_TABS,
                f"inventory_{source_key(source_type)}_section",
                default="재고",
                item_weights=[0.34, 0.34, 0.34, 0.62, 0.48],
                trailing_weight=8.0,
            )
    return selected_source, selected_tab


def render_source_inventory_tabs_lazy(source_type: str, selected_tab: str | None = None) -> None:
    selected_tab = selected_tab or "재고"
    if selected_tab in {"재고", "재고조회"}:
        render_daily_tab(source_type)
    elif selected_tab in {"입고", "입고내역"}:
        render_inbound_tab(source_type)
    elif selected_tab in {"출고", "출고내역"}:
        render_outbound_tab(source_type)
    elif selected_tab == "대시보드":
        render_inventory_dashboard_tab(source_type)
    elif selected_tab in {"마스터", "마스터관리"}:
        product_master_page.render_master_tab(source_type, master_title(source_type))


def daily_to_editor(rows: list[dict]) -> pd.DataFrame:
    mapped = []
    show_warehouse_locations = any(clean_cell(row.get("source_type")) == "창고" for row in rows)
    for row in rows:
        category = clean_cell(row.get("category") or row.get("large_category") or row.get("medium_category") or row.get("small_category"))
        mapped_row = {
            "선택": False,
            "SKU": row.get("product_code", ""),
            "카테고리": category or "미분류",
            "바코드": row.get("barcode", ""),
            "상품명": row.get("product_name", ""),
            "가용재고": row.get("available_stock", 0),
            "1주 평균출고수량": row.get("avg_daily_outbound_1w", row.get("avg_daily_outbound_2w", 0)),
            "재고상태": clean_cell(row.get("stock_status")) or "미집계",
            "출고예정": row.get("pending_outbound_qty", 0),
            "현재고": row.get("current_stock", 0),
            "발주필요일": format_order_required_date(row),
            "박스/파렛트 단위": row.get("box_pallet_unit", ""),
            "업체명": row.get("supplier", ""),
            "담당자": row.get("manager", ""),
            "리드타임": row.get("inbound_cycle", 0) or 0,
        }
        if show_warehouse_locations:
            mapped_row.update(
                {
                    "적재위치": row.get("storage_location", ""),
                    "위치배치수량": row.get("placed_quantity", 0),
                    "미배치수량": row.get("unplaced_quantity", 0),
                    "위치상태": row.get("location_status", "위치미등록"),
                    "위치상세": row.get("location_detail", ""),
                    "비고": row.get("memo", ""),
                }
            )
        mapped.append(mapped_row)
    columns = list(DAILY_COLUMNS)
    if "SKU" not in columns:
        columns.insert(1, "SKU")
    if show_warehouse_locations:
        columns.extend(["적재위치", "위치배치수량", "미배치수량", "위치상태", "위치상세", "비고"])
    return pd.DataFrame(mapped, columns=columns)


def inventory_output_signature(df: pd.DataFrame, filters: dict) -> tuple:
    if df is None or df.empty:
        row_marker = ("empty", 0)
    else:
        sample_columns = [column for column in ("바코드", "상품명", "현재고", "가용재고", "1주 평균출고수량") if column in df.columns]
        sample = tuple(tuple(clean_cell(value) for value in row) for row in df[sample_columns].head(5).fillna("").to_numpy())
        row_marker = (len(df), sample)
    filter_marker = tuple(sorted((str(key), str(value)) for key, value in (filters or {}).items()))
    return row_marker, filter_marker


def inventory_single_category_toggle(options: list[str], state_key: str, widget_key: str) -> str:
    choices = ["전체", *options]
    current = clean_cell(st.session_state.get(state_key)) or "전체"
    if current not in choices:
        current = "전체"
    if not options:
        st.session_state[state_key] = "전체"
        choices = ["전체"]
        current = "전체"

    st.session_state.pop(f"{widget_key}_open", None)
    if clean_cell(st.session_state.get(widget_key)) not in choices:
        st.session_state[widget_key] = current

    with st.container(key=f"{widget_key}_select"):
        selected = st.selectbox(
            "카테고리 선택",
            choices,
            index=choices.index(current),
            key=widget_key,
            disabled=not options,
        )
    selected = clean_cell(selected) or "전체"
    st.session_state[state_key] = selected
    return selected


def inventory_category_toggle(options: list[str], filter_key: str) -> list[str]:
    state_key = f"{filter_key}_category_toggle"
    previous = clean_cell(st.session_state.get(state_key)) or "전체"
    selected = inventory_single_category_toggle(options, state_key, f"{filter_key}_category_toggle_widget")
    if selected != previous:
        st.session_state[f"{filter_key}_page"] = 1
    categories = [] if selected == "전체" else [selected]
    st.session_state[f"{filter_key}_category_filter"] = categories
    return categories


def render_inventory_filters(source_type: str, df: pd.DataFrame) -> dict:
    category_options = fetch_master_category_options(source_type, df)
    supplier_options = sorted([value for value in df.get("업체명", pd.Series(dtype=str)).dropna().unique() if clean_cell(value)])
    manager_options = sorted([value for value in df.get("담당자", pd.Series(dtype=str)).dropna().unique() if clean_cell(value)])
    status_options = ["정상", "주의", "부족", "품절", "미집계"]
    filter_key = source_key(source_type)
    defaults = {
        "search": "",
        "category_filter": [],
        "supplier_filter": [],
        "manager_filter": [],
        "status_filter": [],
        "stock_presence": "전체",
        "inbound_expected": False,
        "outbound_expected": False,
        "below_safe": False,
        "lead_min": 0,
        "lead_max": 0,
        "sort_column": "카테고리",
        "sort_order": "오름차순",
        "page_size": 30,
        "page": 1,
    }
    for suffix, value in defaults.items():
        st.session_state.setdefault(f"{filter_key}_{suffix}", value)

    with st.container(key=f"inventory_filter_{filter_key}_panel"):
        cols = st.columns([2.35, 0.82, 1.05, 0.95, 0.58, 0.58], gap="small")
        search = cols[0].text_input("통합검색", placeholder="바코드 / 상품명 / 업체명 / 담당자", key=f"{filter_key}_search")
        with cols[1]:
            categories = inventory_category_toggle(category_options, filter_key)
        suppliers = inventory_filter_multiselect(cols[2], "업체명", supplier_options, key=f"{filter_key}_supplier_filter")
        statuses = inventory_filter_multiselect(cols[3], "재고상태", status_options, key=f"{filter_key}_status_filter")
        with cols[4]:
            st.write("")
            if st.button("검색", key=f"{filter_key}_search_button", type="primary", use_container_width=True):
                st.session_state[f"{filter_key}_page"] = 1
                st.rerun()
        with cols[5]:
            st.write("")
            if st.button("초기화", key=f"{filter_key}_filter_reset", use_container_width=True):
                reset_inventory_filters(filter_key)
                st.rerun()

        with st.expander("고급 필터", expanded=False):
            adv_cols = st.columns(4, gap="small")
            managers = inventory_filter_multiselect(adv_cols[0], "담당자", manager_options, key=f"{filter_key}_manager_filter")
            stock_presence = adv_cols[1].selectbox("현재고 보유 여부", ["전체", "보유", "미보유"], key=f"{filter_key}_stock_presence")
            inbound_expected = adv_cols[2].checkbox("입고예정", key=f"{filter_key}_inbound_expected")
            outbound_expected = adv_cols[3].checkbox("출고예정", key=f"{filter_key}_outbound_expected")
            adv_cols2 = st.columns(4, gap="small")
            below_safe = adv_cols2[0].checkbox("안전재고 이하", key=f"{filter_key}_below_safe")
            lead_min = adv_cols2[1].number_input("리드타임 최소", min_value=0, step=1, key=f"{filter_key}_lead_min")
            lead_max = adv_cols2[2].number_input("리드타임 최대", min_value=0, step=1, key=f"{filter_key}_lead_max")
            sort_column = adv_cols2[3].selectbox("정렬 컬럼", [column for column in DAILY_COLUMNS if column != "선택"], key=f"{filter_key}_sort_column")
            adv_cols3 = st.columns([1, 1, 2, 2], gap="small")
            sort_order = adv_cols3[0].selectbox("정렬", ["오름차순", "내림차순"], key=f"{filter_key}_sort_order")
            page_size = adv_cols3[1].selectbox("페이지당 표시", [15, 30, 50, 100], key=f"{filter_key}_page_size")

        render_inventory_filter_badges(
            filter_key,
            active_inventory_filter_badges(search, categories, suppliers, managers, statuses, stock_presence, inbound_expected, outbound_expected, below_safe, lead_min, lead_max),
        )

    if st.session_state.get(f"{filter_key}_last_page_size") != page_size:
        st.session_state[f"{filter_key}_page"] = 1
    st.session_state[f"{filter_key}_last_page_size"] = page_size
    return {
        "categories": categories,
        "suppliers": suppliers,
        "managers": managers,
        "statuses": statuses,
        "search": search,
        "stock_presence": stock_presence,
        "inbound_expected": inbound_expected,
        "outbound_expected": outbound_expected,
        "below_safe": below_safe,
        "lead_min": int(lead_min or 0),
        "lead_max": int(lead_max or 0),
        "sort_column": sort_column,
        "sort_order": sort_order,
        "page_size": int(page_size),
        "page": int(st.session_state.get(f"{filter_key}_page", 1)),
    }


def apply_inventory_filters(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    if df.empty:
        return df
    filtered = df.copy()
    categories = filters.get("categories") or []
    suppliers = filters.get("suppliers") or []
    managers = filters.get("managers") or []
    statuses = filters.get("statuses") or []
    search = clean_cell(filters.get("search")).lower()
    if categories:
        filtered = filtered[filtered["카테고리"].isin(categories)]
    if suppliers:
        filtered = filtered[filtered["업체명"].isin(suppliers)]
    if managers:
        filtered = filtered[filtered["담당자"].isin(managers)]
    if statuses:
        filtered = filtered[filtered["재고상태"].isin(statuses)]
    if search:
        search_columns = [column for column in ["SKU", "바코드", "상품명", "업체명", "재고위치", "적재위치", "비고", "담당자"] if column in filtered.columns]
        search_text = filtered[search_columns].astype(str).agg(" ".join, axis=1).str.lower()
        filtered = filtered[search_text.str.contains(re.escape(search), na=False)]
    if filters.get("stock_presence") == "보유":
        filtered = filtered[filtered["현재고"].apply(to_int) > 0]
    elif filters.get("stock_presence") == "미보유":
        filtered = filtered[filtered["현재고"].apply(to_int) <= 0]
    if filters.get("inbound_expected") and "입고예정" in filtered.columns:
        filtered = filtered[filtered["입고예정"].apply(to_int) > 0]
    if filters.get("outbound_expected"):
        filtered = filtered[filtered["출고예정"].apply(to_int) > 0]
    if filters.get("below_safe") and {"현재고", "안전재고"}.issubset(filtered.columns):
        filtered = filtered[filtered["현재고"].apply(to_int) <= filtered["안전재고"].apply(to_int)]
    lead_min = int(filters.get("lead_min") or 0)
    lead_max = int(filters.get("lead_max") or 0)
    if lead_min:
        filtered = filtered[filtered["리드타임"].apply(to_int) >= lead_min]
    if lead_max:
        filtered = filtered[filtered["리드타임"].apply(to_int) <= lead_max]
    sort_column = filters.get("sort_column") if filters.get("sort_column") in filtered.columns else "카테고리"
    ascending = filters.get("sort_order") != "내림차순"
    return filtered.sort_values([sort_column, "상품명", "바코드"], ascending=[ascending, True, True], kind="stable").reset_index(drop=True)


def reset_inventory_filters(filter_key: str) -> None:
    for suffix in [
        "search",
        "category_filter",
        "supplier_filter",
        "manager_filter",
        "status_filter",
        "stock_presence",
        "inbound_expected",
        "outbound_expected",
        "below_safe",
        "lead_min",
        "lead_max",
        "sort_column",
        "sort_order",
        "page_size",
        "page",
        "last_page_size",
        "category_toggle",
        "category_toggle_widget_open",
    ]:
        st.session_state.pop(f"{filter_key}_{suffix}", None)


def active_inventory_filter_badges(search, categories, suppliers, managers, statuses, stock_presence, inbound_expected, outbound_expected, below_safe, lead_min, lead_max) -> list[tuple[str, str, str | None]]:
    badges: list[tuple[str, str, str | None]] = []
    if clean_cell(search):
        badges.append(("search", f"검색 : {search}", None))
    for code, values, label in [
        ("category_filter", categories, "카테고리"),
        ("supplier_filter", suppliers, "업체명"),
        ("manager_filter", managers, "담당자"),
        ("status_filter", statuses, "재고상태"),
    ]:
        for value in values or []:
            badges.append((code, f"{label} : {value}", value))
    if stock_presence != "전체":
        badges.append(("stock_presence", f"현재고 : {stock_presence}", None))
    if inbound_expected:
        badges.append(("inbound_expected", "입고예정 있음", None))
    if outbound_expected:
        badges.append(("outbound_expected", "출고예정 있음", None))
    if below_safe:
        badges.append(("below_safe", "안전재고 이하", None))
    if int(lead_min or 0):
        badges.append(("lead_min", f"리드타임 {int(lead_min)}일 이상", None))
    if int(lead_max or 0):
        badges.append(("lead_max", f"리드타임 {int(lead_max)}일 이하", None))
    return badges


def render_inventory_filter_badges(filter_key: str, badges: list[tuple[str, str, str | None]]) -> None:
    if not badges:
        return
    st.caption("적용 중인 필터")
    cols = st.columns(min(len(badges), 6), gap="small")
    for idx, (suffix, label, value) in enumerate(badges):
        with cols[idx % len(cols)]:
            if st.button(f"{label} X", key=f"{filter_key}_clear_{suffix}_{idx}", use_container_width=True):
                clear_inventory_filter(filter_key, suffix, value)
                st.rerun()


def clear_inventory_filter(filter_key: str, suffix: str, value: str | None = None) -> None:
    if suffix == "search":
        st.session_state[f"{filter_key}_{suffix}"] = ""
    elif suffix == "category_filter":
        st.session_state[f"{filter_key}_{suffix}"] = []
        st.session_state[f"{filter_key}_category_toggle"] = "전체"
    elif suffix == "stock_presence":
        st.session_state[f"{filter_key}_{suffix}"] = "전체"
    elif suffix in {"inbound_expected", "outbound_expected", "below_safe"}:
        st.session_state[f"{filter_key}_{suffix}"] = False
    elif suffix in {"lead_min", "lead_max"}:
        st.session_state[f"{filter_key}_{suffix}"] = 0
    elif value is not None and isinstance(st.session_state.get(f"{filter_key}_{suffix}"), list):
        st.session_state[f"{filter_key}_{suffix}"] = [item for item in st.session_state[f"{filter_key}_{suffix}"] if item != value]
    else:
        st.session_state[f"{filter_key}_{suffix}"] = []
    st.session_state[f"{filter_key}_page"] = 1


def render_stock_upload_preview(
    source_type: str,
    work_date: date,
    preview_key: str,
    preview: dict,
    preview_df_key: str,
    applied_df_key: str,
    excluded_df_key: str,
) -> None:
    st.markdown('<div class="inventory-subsection-title">미리보기 결과</div>', unsafe_allow_html=True)
    metric_cols = st.columns(5, gap="small")
    metric_cols[0].metric("파일 행", f"{preview.get('total_rows', 0):,}")
    metric_cols[1].metric("마스터 매칭", f"{preview.get('matched_count', 0):,}")
    metric_cols[2].metric("미매칭/오류", f"{preview.get('failed_count', 0):,}")
    metric_cols[3].metric("중복", f"{preview.get('duplicate_count', 0):,}")
    metric_cols[4].metric("전체파일 0처리", f"{preview.get('zeroed_count', 0):,}")
    st.caption(
        f"빈 바코드 {preview.get('empty_barcode_count', 0):,}건 / "
        f"숫자 오류 {preview.get('invalid_stock_count', 0):,}건 / "
        f"음수 재고 {preview.get('negative_stock_count', 0):,}건"
    )
    render_stock_upload_debug(preview)
    preview_df = st.session_state.get(preview_df_key)
    if not isinstance(preview_df, pd.DataFrame):
        preview_df = stock_preview_display_dataframe(preview)
        st.session_state[preview_df_key] = preview_df
    render_inventory_visible_table(preview_df, height=360)
    apply_col, cancel_col, spacer = st.columns([1, 1, 4], gap="small")
    with apply_col:
        if st.button("재고 반영", type="primary", key=f"{preview_key}_apply", use_container_width=True):
            applied_df = stock_applied_display_dataframe(preview)
            excluded_df = stock_excluded_display_dataframe(preview)
            outcome = with_db(lambda db: services.apply_stock_upload_preview(db, source_type, work_date, preview, current_user_name()))
            if outcome and outcome.get("ok", True):
                clear_inventory_data_caches()
                st.session_state[applied_df_key] = applied_df
                st.session_state[excluded_df_key] = excluded_df
                st.session_state.pop(preview_key, None)
            show_result(outcome)
    with cancel_col:
        if st.button("미리보기 취소", key=f"{preview_key}_cancel", use_container_width=True):
            st.session_state.pop(preview_key, None)
            st.session_state.pop(preview_df_key, None)
            st.rerun()
    with spacer:
        st.empty()


def stock_preview_display_dataframe(preview: dict) -> pd.DataFrame:
    preview_df = pd.DataFrame(preview.get("preview_rows", []))
    if preview_df.empty:
        return pd.DataFrame()
    columns = {
        "row_no": "엑셀 행",
        "product_code": "SKU",
        "category": "카테고리",
        "product_name": "상품명",
        "barcode": "바코드",
        "storage_location": "재고위치",
        "previous_stock": "기존 현재고",
        "new_stock": "변경 현재고",
        "new_available_stock": "변경 가용재고",
        "status": "검증결과",
        "matched": "반영대상",
    }
    display_df = preview_df.rename(columns=columns)
    ordered = ["엑셀 행", "SKU", "카테고리", "상품명", "바코드", "재고위치", "기존 현재고", "변경 현재고", "변경 가용재고", "검증결과", "반영대상"]
    return display_df[[column for column in ordered if column in display_df.columns]]


def stock_applied_display_dataframe(preview: dict) -> pd.DataFrame:
    preview_df = stock_preview_display_dataframe(preview)
    if preview_df.empty or "반영대상" not in preview_df.columns:
        return pd.DataFrame()
    applied = preview_df[preview_df["반영대상"].astype(bool)].copy()
    if not applied.empty:
        applied["반영 결과"] = "반영 완료"
    return applied


def stock_excluded_display_dataframe(preview: dict) -> pd.DataFrame:
    preview_df = stock_preview_display_dataframe(preview)
    if preview_df.empty or "반영대상" not in preview_df.columns:
        return pd.DataFrame()
    excluded = preview_df[~preview_df["반영대상"].astype(bool)].copy()
    if not excluded.empty:
        excluded["제외 사유"] = excluded.get("검증결과", "제외")
    return excluded


def erp_unmatched_display_dataframe(outcome: dict | None) -> pd.DataFrame:
    rows = []
    for row in (outcome or {}).get("unmatched_rows", []):
        rows.append(
            {
                "바코드": clean_cell(row.get("barcode")),
                "상품명": clean_cell(row.get("product_name")),
                "업로드재고": row.get("uploaded_stock", ""),
                "미매칭 사유": clean_cell(row.get("reason")),
            }
        )
    return pd.DataFrame(rows)


def inventory_master_diagnostics_dataframe(result: dict) -> pd.DataFrame:
    rows = []
    for row in (result or {}).get("problem_rows", []):
        rows.append(
            {
                "상품코드": clean_cell(row.get("product_code")),
                "바코드": clean_cell(row.get("barcode")),
                "상품명": clean_cell(row.get("product_name")),
                "재고 카테고리": clean_cell(row.get("inventory_category")),
                "마스터 카테고리": clean_cell(row.get("master_category")),
                "매칭방식": clean_cell(row.get("match_method")),
                "원인": clean_cell(row.get("reason")),
            }
        )
    return pd.DataFrame(rows)


def inventory_apply_failure_dataframe(outcome: dict) -> pd.DataFrame:
    rows = []
    for row in (outcome or {}).get("failure_rows", []):
        rows.append(
            {
                "엑셀 행": clean_cell(row.get("row_no")),
                "상품코드": clean_cell(row.get("product_code")),
                "바코드": clean_cell(row.get("barcode")),
                "상품명": clean_cell(row.get("product_name")),
                "실재고": row.get("new_stock", ""),
                "실패사유": clean_cell(row.get("failure_reason")),
            }
        )
    return pd.DataFrame(rows)


def render_inventory_kpi_cards(cards: list[tuple[str, int, str, str]]) -> None:
    icons = {
        "neutral": "▦",
        "normal": "✓",
        "warning": "!",
        "short": "↓",
        "soldout": "−",
        "unknown": "?",
        "available": "▣",
    }
    items = []
    for label, value, unit, tone in cards:
        items.append(
            f"""
            <article class="inventory-design-kpi {escape(tone)}">
                <div>
                    <span>{escape(label)}</span>
                    <strong>{int(value or 0):,}</strong>
                    <em>{escape(unit)}</em>
                </div>
                <i>{escape(icons.get(tone, "?"))}</i>
            </article>
            """
        )
    render_inventory_html(f'<section class="inventory-design-kpis">{"".join(items)}</section>')


def render_inventory_update_panel(
    source_type: str,
    daily_date_key: str,
) -> date:
    with st.container(key=f"{source_key(source_type)}_inventory_update"):
        render_inventory_html(
            """
            <div class="inventory-update-heading">
                <div>
                    <h2>ERP 재고 업데이트</h2>
                    <p>ERP에서 추출한 Excel/CSV 파일로 선택 기준일의 재고 Snapshot을 바로 갱신합니다.</p>
                </div>
            </div>
            """
        )
        upload_cols = st.columns([0.92, 2.35, 0.95, 0.92, 1.35], gap="large")
        with upload_cols[0]:
            work_date = st.date_input("기준일자", value=st.session_state[daily_date_key], key=daily_date_key)

        upload_preview_key = f"{source_type}_stock_upload_preview_{work_date.isoformat()}"
        preview_df_key = f"{source_type}_inventory_preview_df_{work_date.isoformat()}"
        applied_df_key = f"{source_type}_applied_inventory_df_{work_date.isoformat()}"
        excluded_df_key = f"{source_type}_excluded_inventory_df_{work_date.isoformat()}"
        with upload_cols[1]:
            uploaded = st.file_uploader("파일 선택 또는 Drag & Drop", type=["xlsx", "xls", "csv"], key=f"{source_type}_stock_master_upload_{work_date}")
        with upload_cols[2]:
            upload_mode = st.radio("반영 범위", ["일부 재고", "전체 재고"], horizontal=False, key=f"{source_type}_stock_upload_mode")
        with upload_cols[3]:
            st.write("")
            if st.button("엑셀업로드", key=f"{source_type}_stock_upload_btn_{work_date}", type="primary", use_container_width=True):
                if uploaded is None:
                    st.warning("먼저 ERP 재고 Excel 파일을 선택하세요.")
                else:
                    mode = "full" if upload_mode == "전체 재고" else "partial"
                    with st.spinner("엑셀을 읽고 재고를 바로 반영하는 중입니다..."):
                        def upload_action(db):
                            preview = services.prepare_stock_upload_preview(db, source_type, work_date, uploaded.getvalue(), uploaded.name, mode)
                            if not preview or not preview.get("ok", True):
                                return preview
                            return services.apply_stock_upload_preview(db, source_type, work_date, preview, current_user_name())

                        outcome = with_db(upload_action)
                    for key in [upload_preview_key, preview_df_key, applied_df_key, excluded_df_key]:
                        st.session_state.pop(key, None)
                    show_result(outcome)
        with upload_cols[4]:
            st.info("엑셀업로드를 누르면 저장과 재고 계산이 바로 실행됩니다. 재고위치 컬럼이 있으면 창고 위치표에도 함께 반영됩니다.")
        return work_date


def stock_registration_dataframe(rows: list[dict], changes: dict[str, dict]) -> pd.DataFrame:
    records = []
    for row in rows:
        sku = clean_cell(row.get("product_code"))
        if not sku:
            continue
        current_stock = to_int(row.get("current_stock"))
        change = changes.get(sku, {})
        edited_stock = to_int(change.get("new_stock", current_stock))
        delta = edited_stock - current_stock
        records.append(
            {
                "내부 상품 ID": sku,
                "SKU": sku,
                "바코드": clean_cell(row.get("barcode")),
                "상품명": clean_cell(row.get("product_name")),
                "카테고리": clean_cell(row.get("category")),
                "보관위치": clean_cell(row.get("storage_location")),
                "업체명": clean_cell(row.get("supplier")),
                "현재고": edited_stock,
                "증감수량": delta,
                "비고": clean_cell(change.get("memo")),
                "_base_stock": current_stock,
                "_changed": delta != 0,
            }
        )
    return pd.DataFrame(records)


def stock_registration_filter_dataframe(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    if df.empty:
        return df
    filtered = df.copy()
    search = clean_cell(filters.get("search")).lower()
    if search:
        search_columns = ["SKU", "바코드", "상품명"]
        search_text = filtered[search_columns].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
        filtered = filtered[search_text.str.contains(re.escape(search), na=False)]
    category = clean_cell(filters.get("category"))
    if category and category != "전체":
        filtered = filtered[filtered["카테고리"] == category]
    location = clean_cell(filters.get("location"))
    if location and location != "전체":
        filtered = filtered[filtered["보관위치"] == location]
    if filters.get("zero_only"):
        filtered = filtered[filtered["현재고"].apply(to_int) == 0]
    if filters.get("changed_only"):
        filtered = filtered[filtered["_changed"].astype(bool)]
    return filtered.reset_index(drop=True)


def stock_registration_download_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    columns = ["내부 상품 ID", "SKU", "바코드", "상품명", "카테고리", "보관위치", "업체명", "현재고"]
    if df is None or df.empty:
        return pd.DataFrame(columns=columns)
    export_df = df.copy()
    if "_base_stock" in export_df.columns:
        export_df["현재고"] = export_df["_base_stock"].apply(to_int)
    return export_df[[column for column in columns if column in export_df.columns]].copy()


def stock_registration_template_excel(df: pd.DataFrame) -> bytes:
    output = BytesIO()
    export_df = stock_registration_download_dataframe(df)
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        sheet_name = "재고수정"
        export_df.to_excel(writer, index=False, sheet_name=sheet_name)
        workbook = writer.book
        worksheet = writer.sheets[sheet_name]
        row_count, col_count = export_df.shape
        if col_count:
            header_format = workbook.add_format({"bold": True, "bg_color": "#E6E0D7", "border": 1, "align": "center"})
            number_format = workbook.add_format({"num_format": "#,##0"})
            worksheet.freeze_panes(1, 0)
            worksheet.autofilter(0, 0, max(row_count, 1), col_count - 1)
            for col_idx, column in enumerate(export_df.columns):
                width = excel_column_width(export_df[column], column)
                worksheet.set_column(col_idx, col_idx, width, number_format if column == "현재고" else None)
                worksheet.write(0, col_idx, column, header_format)
    return output.getvalue()


def stock_registration_preview_dataframe(preview: dict) -> pd.DataFrame:
    rows = []
    for row in preview.get("preview_rows", []):
        if not row.get("matched"):
            continue
        previous_stock = to_int(row.get("previous_stock"))
        new_stock = to_int(row.get("new_stock"))
        rows.append(
            {
                "SKU": clean_cell(row.get("product_code")),
                "상품명": clean_cell(row.get("product_name")),
                "기존 재고": previous_stock,
                "변경 재고": new_stock,
                "증감수량": new_stock - previous_stock,
            }
        )
    return pd.DataFrame(rows, columns=["SKU", "상품명", "기존 재고", "변경 재고", "증감수량"])


def stock_registration_preview_from_changes(
    source_type: str,
    work_date: date,
    rows: list[dict],
    changes: dict[str, dict],
    method: str = "웹 직접수정",
) -> dict:
    rows_by_sku = {clean_cell(row.get("product_code")): row for row in rows if clean_cell(row.get("product_code"))}
    preview_rows = []
    for sku, change in changes.items():
        row = rows_by_sku.get(sku)
        if not row:
            continue
        previous_stock = to_int(row.get("current_stock"))
        new_stock = to_int(change.get("new_stock"))
        if new_stock == previous_stock:
            continue
        preview_rows.append(
            {
                "row_no": len(preview_rows) + 1,
                "product_code": sku,
                "category": clean_cell(row.get("category")),
                "product_name": clean_cell(row.get("product_name")),
                "barcode": clean_cell(row.get("barcode")),
                "storage_location": clean_cell(row.get("storage_location")),
                "previous_stock": previous_stock,
                "new_stock": new_stock,
                "new_available_stock": new_stock,
                "status": "정상",
                "matched": True,
                "memo": clean_cell(change.get("memo")),
                "change_method": clean_cell(change.get("method")) or method,
            }
        )
    return {
        "ok": True,
        "file_name": method,
        "upload_mode": method,
        "change_method": method,
        "timings": {"prepare_total": 0.0},
        "total_rows": len(preview_rows),
        "matched_count": len(preview_rows),
        "failed_count": 0,
        "duplicate_count": 0,
        "unmatched_count": 0,
        "empty_barcode_count": 0,
        "invalid_stock_count": 0,
        "negative_stock_count": 0,
        "zeroed_count": 0,
        "preview_rows": preview_rows,
    }


def stock_registration_filter_changed_preview(preview: dict, method: str) -> dict:
    next_preview = dict(preview or {})
    preview_rows = []
    changed_count = 0
    unchanged_count = 0
    for row in list(next_preview.get("preview_rows") or []):
        next_row = dict(row)
        previous_stock = to_int(next_row.get("previous_stock"))
        new_stock = to_int(next_row.get("new_stock"))
        if next_row.get("matched") and previous_stock == new_stock:
            next_row["matched"] = False
            next_row["status"] = "변경 없음"
            unchanged_count += 1
        elif next_row.get("matched"):
            changed_count += 1
        preview_rows.append(next_row)
    next_preview["preview_rows"] = preview_rows
    next_preview["matched_count"] = changed_count
    next_preview["unchanged_count"] = unchanged_count
    next_preview["upload_mode"] = method
    next_preview["change_method"] = method
    next_preview["file_name"] = clean_cell(next_preview.get("file_name")) or method
    return next_preview


def update_stock_registration_changes(
    changes: dict[str, dict],
    edited_df: pd.DataFrame,
    base_stock_by_sku: dict[str, int] | None = None,
) -> dict[str, dict]:
    next_changes = dict(changes or {})
    base_stock_by_sku = base_stock_by_sku or {}
    if edited_df is None or edited_df.empty:
        return next_changes
    for _, row in edited_df.iterrows():
        sku = clean_cell(row.get("SKU"))
        if not sku:
            continue
        current_stock = int(base_stock_by_sku.get(sku, to_int(row.get("_base_stock"))))
        edited_stock = to_int(row.get("현재고"))
        memo = clean_cell(row.get("비고"))
        if edited_stock != current_stock:
            next_changes[sku] = {"new_stock": edited_stock, "memo": memo, "method": "웹 직접수정"}
        else:
            next_changes.pop(sku, None)
    return next_changes


def merge_stock_registration_excel_changes(changes: dict[str, dict], preview: dict) -> dict[str, dict]:
    next_changes = dict(changes or {})
    for row in preview.get("preview_rows", []) or []:
        if not row.get("matched"):
            continue
        sku = clean_cell(row.get("product_code"))
        if not sku:
            continue
        previous_stock = to_int(row.get("previous_stock"))
        new_stock = to_int(row.get("new_stock"))
        if previous_stock == new_stock:
            next_changes.pop(sku, None)
            continue
        next_changes[sku] = {
            "new_stock": new_stock,
            "memo": clean_cell(row.get("memo")),
            "method": "엑셀 재고수정",
        }
    return next_changes


def render_stock_registration_panel(source_type: str, work_date: date, rows: list[dict]) -> bool:
    panel_key = f"{source_key(source_type)}_stock_registration_{work_date.isoformat()}"
    changes_key = f"{panel_key}_changes"
    preview_key = f"{panel_key}_preview"
    result_key = f"{panel_key}_result"
    upload_key = f"{panel_key}_upload"
    upload_signature_key = f"{panel_key}_upload_signature"
    current_download_key = f"{panel_key}_current_download"
    template_download_key = f"{panel_key}_template_download"
    page_key = f"{panel_key}_page"
    page_size_key = f"{panel_key}_page_size"
    changes = st.session_state.setdefault(changes_key, {})
    full_df = stock_registration_dataframe(rows, changes)
    categories = ["전체", *sorted(value for value in full_df.get("카테고리", pd.Series(dtype=str)).dropna().unique() if clean_cell(value))]
    locations = ["전체", *sorted(value for value in full_df.get("보관위치", pd.Series(dtype=str)).dropna().unique() if clean_cell(value))]

    st.markdown('<div class="inventory-subsection-title">재고 수정</div>', unsafe_allow_html=True)
    st.caption("현재 재고를 내려받아 수정 파일을 검증한 뒤, 변경내용 확인 후 최종 반영합니다.")

    download_filters = {
        "search": "",
        "category": "전체",
        "location": "전체",
        "zero_only": False,
        "changed_only": False,
    }
    download_signature = inventory_output_signature(full_df.drop(columns=["_changed"], errors="ignore"), download_filters)
    action_cols = st.columns([1.15, 1.15, 1.45, 3.2], gap="small")
    with action_cols[0]:
        current_payload = st.session_state.get(current_download_key)
        if st.button("현재 재고 다운로드", key=f"{panel_key}_current_download_prepare", use_container_width=True):
            st.session_state[current_download_key] = {
                "signature": download_signature,
                "bytes": dataframe_to_excel(stock_registration_download_dataframe(full_df)),
            }
            st.rerun()
        if isinstance(current_payload, dict) and current_payload.get("signature") == download_signature and current_payload.get("bytes"):
            st.download_button(
                "현재 재고 파일 저장",
                data=current_payload["bytes"],
                file_name=f"{source_type}_stock_current_{work_date:%Y%m%d}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key=f"{panel_key}_current_download_save",
            )
    with action_cols[1]:
        template_payload = st.session_state.get(template_download_key)
        if st.button("수정양식 다운로드", key=f"{panel_key}_template_download_prepare", use_container_width=True):
            st.session_state[template_download_key] = {
                "signature": download_signature,
                "bytes": stock_registration_template_excel(full_df),
            }
            st.rerun()
        if isinstance(template_payload, dict) and template_payload.get("signature") == download_signature and template_payload.get("bytes"):
            st.download_button(
                "수정양식 파일 저장",
                data=template_payload["bytes"],
                file_name=f"재고수정양식_{work_date:%Y%m%d}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key=f"{panel_key}_template_download_save",
            )
    with action_cols[2]:
        uploaded = st.file_uploader("수정파일 업로드", type=["xlsx", "xls", "csv"], key=upload_key)
        if uploaded is not None:
            file_bytes = uploaded.getvalue()
            upload_signature = (uploaded.name, len(file_bytes), abs(hash(file_bytes)))
            if st.session_state.get(upload_signature_key) != upload_signature:
                with st.spinner("수정파일을 검증하고 변경된 재고만 비교하는 중입니다..."):
                    preview = with_db(lambda db: services.prepare_stock_upload_preview(db, source_type, work_date, file_bytes, uploaded.name, "partial"))
                st.session_state[upload_signature_key] = upload_signature
                if preview and preview.get("ok", True):
                    changed_preview = stock_registration_filter_changed_preview(preview, "엑셀 재고수정")
                    combined_changes = merge_stock_registration_excel_changes(
                        st.session_state.get(changes_key, {}),
                        changed_preview,
                    )
                    combined_preview = stock_registration_preview_from_changes(source_type, work_date, rows, combined_changes, "엑셀 재고수정")
                    excluded_rows = [dict(row) for row in changed_preview.get("preview_rows", []) if not row.get("matched")]
                    if excluded_rows:
                        combined_preview["preview_rows"].extend(excluded_rows)
                        combined_preview["total_rows"] = len(combined_preview["preview_rows"])
                    combined_preview["failed_count"] = int(changed_preview.get("failed_count") or 0)
                    combined_preview["duplicate_count"] = int(changed_preview.get("duplicate_count") or 0)
                    combined_preview["unmatched_count"] = int(changed_preview.get("unmatched_count") or 0)
                    combined_preview["unchanged_count"] = int(changed_preview.get("unchanged_count") or 0)
                    st.session_state[changes_key] = combined_changes
                    st.session_state[preview_key] = combined_preview
                    st.rerun()
                else:
                    show_result(preview)
    with action_cols[3]:
        st.info("수정파일 업로드 후 검증 결과가 생성되면 변경내용 확인과 최종 수정 반영을 진행할 수 있습니다.")

    with st.expander("상품마스터 매칭/미분류 진단", expanded=False):
        diag_key = f"{panel_key}_master_diagnostics"
        if st.button("현재 재고-상품마스터 매칭 점검", key=f"{panel_key}_diagnostics_btn", use_container_width=True):
            st.session_state[diag_key] = with_db(lambda db: services.inventory_master_match_diagnostics(db, source_type, work_date))
        diagnostics = st.session_state.get(diag_key)
        if isinstance(diagnostics, dict):
            stats = diagnostics.get("stats", {})
            metric_cols = st.columns(7, gap="small")
            metric_cols[0].metric("전체 재고 상품", f"{int(stats.get('total_inventory_items') or 0):,}")
            metric_cols[1].metric("상품코드 정상 매칭", f"{int(stats.get('sku_match_count') or 0):,}")
            metric_cols[2].metric("바코드 fallback 매칭", f"{int(stats.get('barcode_fallback_match_count') or 0):,}")
            metric_cols[3].metric("마스터 없음", f"{int(stats.get('master_missing_count') or 0):,}")
            metric_cols[4].metric("카테고리 없음", f"{int(stats.get('category_empty_count') or 0):,}")
            metric_cols[5].metric("상품코드 중복", f"{int(stats.get('duplicate_product_code_count') or 0):,}")
            metric_cols[6].metric("바코드 중복", f"{int(stats.get('duplicate_barcode_count') or 0):,}")
            diagnostics_df = inventory_master_diagnostics_dataframe(diagnostics)
            if diagnostics_df.empty:
                st.success("상품마스터 매칭/카테고리 진단에서 문제가 발견되지 않았습니다.")
            else:
                render_inventory_visible_table(diagnostics_df, height=280)

    filter_cols = st.columns([0.9, 1.8, 1.0, 1.0, 0.9, 0.9], gap="small")
    with filter_cols[0]:
        st.date_input("기준일자", value=work_date, disabled=True, key=f"{panel_key}_date")
    with filter_cols[1]:
        search = st.text_input("검색", placeholder="SKU / 바코드 / 상품명", key=f"{panel_key}_search")
    with filter_cols[2]:
        category = st.selectbox("카테고리", categories, key=f"{panel_key}_category")
    with filter_cols[3]:
        location = st.selectbox("보관위치", locations, key=f"{panel_key}_location")
    with filter_cols[4]:
        zero_only = st.checkbox("재고 0", key=f"{panel_key}_zero_only")
    with filter_cols[5]:
        changed_only = st.checkbox("수정된 상품만", key=f"{panel_key}_changed_only")

    filtered_df = stock_registration_filter_dataframe(
        full_df,
        {
            "search": search,
            "category": category,
            "location": location,
            "zero_only": zero_only,
            "changed_only": changed_only,
        },
    )
    page_size = st.selectbox("페이지 표시", [30, 50, 100, 200], index=1, key=page_size_key)
    total_pages = max(ceil(len(filtered_df) / int(page_size or 50)), 1)
    current_page = min(max(int(st.session_state.get(page_key, 1) or 1), 1), total_pages)
    st.session_state[page_key] = current_page
    start = (current_page - 1) * int(page_size)
    page_df = filtered_df.iloc[start : start + int(page_size)].reset_index(drop=True)
    editor_df = page_df.drop(columns=["_changed"], errors="ignore").reset_index(drop=True)
    editor_visible_columns = ["SKU", "바코드", "상품명", "카테고리", "보관위치", "업체명", "현재고", "증감수량", "비고"]
    editor_df = editor_df[[column for column in [*editor_visible_columns, "_base_stock"] if column in editor_df.columns]]

    st.markdown("#### 수정 대상 재고")
    if page_df.empty:
        st.info("현재 필터 조건에 해당하는 재고 데이터가 없습니다.")
    else:
        display_df = editor_df.drop(columns=["_base_stock"], errors="ignore")
        render_plain_inventory_table(
            display_df,
            height=420,
            empty_message="현재 필터 조건에 해당하는 재고 데이터가 없습니다.",
        )

    changed_values = []
    for sku, change in changes.items():
        base_row = full_df[full_df["SKU"] == sku]
        if base_row.empty:
            continue
        current_stock = to_int(base_row.iloc[0].get("_base_stock"))
        new_stock = to_int(change.get("new_stock"))
        if current_stock != new_stock:
            changed_values.append(new_stock - current_stock)
    increase_qty = sum(value for value in changed_values if value > 0)
    decrease_qty = abs(sum(value for value in changed_values if value < 0))
    if changed_values:
        metric_cols = st.columns(3, gap="small")
        metric_cols[0].metric("수정 상품", f"{len(changed_values):,}개")
        metric_cols[1].metric("증가 예정", f"+{increase_qty:,}")
        metric_cols[2].metric("감소 예정", f"-{decrease_qty:,}")

    nav_cols = st.columns([0.72, 0.72, 1.6, 1.2, 3.4], gap="small")
    with nav_cols[0]:
        if st.button("이전", key=f"{panel_key}_prev", disabled=current_page <= 1, use_container_width=True):
            st.session_state[page_key] = max(current_page - 1, 1)
            st.rerun()
    with nav_cols[1]:
        if st.button("다음", key=f"{panel_key}_next", disabled=current_page >= total_pages, use_container_width=True):
            st.session_state[page_key] = min(current_page + 1, total_pages)
            st.rerun()
    with nav_cols[2]:
        st.caption(f"{current_page:,} / {total_pages:,} 페이지 · 필터 결과 {len(filtered_df):,}건 · 표시 {len(page_df):,}건")
    with nav_cols[3]:
        if st.button("변경내용 확인", key=f"{panel_key}_manual_preview", type="primary", use_container_width=True, disabled=not changed_values):
            st.session_state[preview_key] = stock_registration_preview_from_changes(source_type, work_date, rows, changes, "웹 직접수정")
    with nav_cols[4]:
        st.empty()

    preview = st.session_state.get(preview_key)
    if isinstance(preview, dict):
        preview_df = stock_registration_preview_dataframe(preview)
        st.markdown("#### 변경내역 미리보기")
        metric_cols = st.columns(5, gap="small")
        metric_cols[0].metric("총 데이터", f"{int(preview.get('total_rows') or 0):,}")
        metric_cols[1].metric("반영 대상", f"{int(preview.get('matched_count') or 0):,}")
        metric_cols[2].metric("미매칭/오류", f"{int(preview.get('failed_count') or 0):,}")
        metric_cols[3].metric("중복", f"{int(preview.get('duplicate_count') or 0):,}")
        metric_cols[4].metric("변경 없음", f"{int(preview.get('unchanged_count') or 0):,}")
        if preview_df.empty:
            st.info("반영할 재고 변경 내역이 없습니다.")
        else:
            render_inventory_visible_table(preview_df, height=260)
        excluded_df = stock_excluded_display_dataframe(preview)
        if isinstance(excluded_df, pd.DataFrame) and not excluded_df.empty:
            with st.expander(f"미매칭/오류/변경 없음 {len(excluded_df):,}건 확인", expanded=False):
                render_inventory_visible_table(excluded_df, height=260)
        apply_cols = st.columns([1.0, 1.0, 4.0], gap="small")
        with apply_cols[0]:
            if st.button("수정 반영", key=f"{panel_key}_apply", type="primary", use_container_width=True, disabled=preview_df.empty):
                outcome = with_db(lambda db: services.apply_manual_stock_adjustment_preview(db, source_type, work_date, preview, current_user_name()))
                st.session_state[result_key] = outcome
                if outcome and outcome.get("ok", True):
                    clear_inventory_data_caches()
                    st.session_state.pop(preview_key, None)
                    st.session_state.pop(changes_key, None)
                st.rerun()
        with apply_cols[1]:
            if st.button("미리보기 취소", key=f"{panel_key}_cancel", use_container_width=True):
                st.session_state.pop(preview_key, None)
                st.rerun()
    render_stock_upload_apply_result(st.session_state.get(result_key))
    return bool(st.session_state.get(result_key))


def render_lookup_erp_update_panel(source_type: str, work_date: date, daily_date_key: str) -> None:
    panel_key = f"{source_key(source_type)}_lookup_erp_update_{work_date.isoformat()}"
    result_key = f"{panel_key}_result"
    excluded_df_key = f"{panel_key}_excluded"
    processing_key = f"{panel_key}_processing"
    with st.container(key=panel_key):
        render_inventory_html(
            """
            <div class="inventory-update-heading">
                <div>
                    <h2>ERP 재고 업데이트</h2>
                    <p>업로드 파일의 바코드와 상품명이 모두 일치하는 상품의 현재고를 선택 기준일자에 바로 반영합니다.</p>
                </div>
            </div>
            """
        )
        upload_cols = st.columns([2.4, 1.15, 0.9, 2.2], gap="large")
        with upload_cols[0]:
            uploaded = st.file_uploader(
                "파일 선택 또는 Drag & Drop",
                type=["xlsx", "xls", "csv"],
                key=f"{panel_key}_file",
            )
        with upload_cols[1]:
            upload_mode = st.radio(
                "반영 범위",
                ["일부 재고", "전체 재고"],
                horizontal=False,
                key=f"{panel_key}_mode",
            )
        with upload_cols[2]:
            st.write("")
            disabled = uploaded is None or bool(st.session_state.get(processing_key))
            if st.button("재고 반영", key=f"{panel_key}_apply", type="primary", use_container_width=True, disabled=disabled):
                st.session_state[processing_key] = True
                st.session_state[f"_pending_{daily_date_key}"] = work_date
                try:
                    with st.status("ERP 재고 업데이트", expanded=True) as status:
                        st.write("파일 확인 중...")
                        file_bytes = uploaded.getvalue()
                        st.write("상품 매칭 중...")
                        mode = "full" if upload_mode == "전체 재고" else "partial"

                        def upload_action(db):
                            return services.apply_erp_stock_upload_file(
                                db,
                                source_type,
                                work_date,
                                file_bytes,
                                uploaded.name,
                                current_user_name(),
                                mode,
                            )

                        outcome = with_db(upload_action)
                        st.write(f"{int(outcome.get('count') or 0):,}건 재고 반영 중...")
                        if outcome and outcome.get("ok", True):
                            clear_inventory_data_caches()
                            st.write("재고조회 갱신 중...")
                            status.update(label="ERP 재고 업데이트 완료", state="complete")
                        else:
                            status.update(label="ERP 재고 업데이트 오류", state="error")
                    st.session_state[result_key] = outcome
                    st.session_state[excluded_df_key] = erp_unmatched_display_dataframe(outcome)
                finally:
                    st.session_state[processing_key] = False
                st.rerun()
        with upload_cols[3]:
            st.info("ERP 업데이트와 아래 재고조회는 같은 기준일자와 같은 현재고 데이터를 사용합니다.")
        render_stock_upload_apply_result(st.session_state.get(result_key), st.session_state.get(excluded_df_key))


def render_inventory_lookup_panel(source_type: str, work_date: date, rows: list[dict]) -> None:
    base_df = daily_to_editor(rows)
    filters = render_inventory_filters(source_type, base_df)
    location_column = "적재위치" if "적재위치" in base_df.columns else "재고위치" if "재고위치" in base_df.columns else ""
    location_options = ["전체"]
    if location_column:
        location_options.extend(
            sorted(value for value in base_df.get(location_column, pd.Series(dtype=str)).dropna().unique() if clean_cell(value))
        )
    extra_cols = st.columns([1.2, 0.8, 0.95, 3.2], gap="small")
    with extra_cols[0]:
        location_filter = st.selectbox("보관위치", location_options, key=f"{source_key(source_type)}_lookup_location_{work_date}")
    with extra_cols[1]:
        zero_only = st.checkbox("재고 0", key=f"{source_key(source_type)}_lookup_zero_only_{work_date}")
    with extra_cols[2]:
        changed_only = st.checkbox("수정된 상품만", key=f"{source_key(source_type)}_lookup_changed_only_{work_date}")
    with extra_cols[3]:
        st.empty()

    render_lookup_erp_update_panel(source_type, work_date, f"{source_type}_daily_date")

    filtered_df = apply_inventory_filters(base_df, filters)
    if location_column and clean_cell(location_filter) and location_filter != "전체":
        filtered_df = filtered_df[filtered_df[location_column] == location_filter]
    if zero_only:
        filtered_df = filtered_df[filtered_df["현재고"].apply(to_int) == 0]
    if changed_only:
        changes_key = f"{source_key(source_type)}_stock_registration_{work_date.isoformat()}_changes"
        changed_skus = set(st.session_state.get(changes_key, {}).keys())
        filtered_df = filtered_df[filtered_df.get("SKU", pd.Series(dtype=str)).isin(changed_skus)]
    paged_df, page, total_pages = paginate_inventory_df(filtered_df, filters)

    output_payload_key = f"{source_type}_daily_output_payload_{work_date.isoformat()}"
    output_scope_key = f"{source_type}_daily_download_scope_{work_date}"
    status_series = filtered_df.get("재고상태", pd.Series(dtype=str))
    render_inventory_kpi_cards(
        [
            ("전체 재고", len(filtered_df), "items", "neutral"),
            ("정상", int((status_series == "정상").sum()), "items", "normal"),
            ("주의", int((status_series == "주의").sum()), "items", "warning"),
            ("부족", int((status_series == "부족").sum()), "items", "short"),
            ("품절", int((status_series == "품절").sum()), "items", "soldout"),
            ("미집계", int((status_series == "미집계").sum()), "items", "unknown"),
        ]
    )

    with st.container(key=f"{source_key(source_type)}_inventory_table_actions"):
        toolbar_title, toolbar_scope, toolbar_pdf, toolbar_excel = st.columns([3.8, 1.15, 0.78, 0.78], gap="small")
        with toolbar_title:
            render_inventory_html(
                f'<div class="inventory-table-title"><h2>재고 현황</h2><span>{len(filtered_df):,}개 상품 · 전체 {len(base_df):,}개 · {page}/{total_pages} 페이지</span></div>'
            )
        with toolbar_scope:
            download_scope = st.selectbox(
                "출력 범위",
                ["현재 필터", "전체 데이터"],
                key=output_scope_key,
                label_visibility="collapsed",
            )
        output_source_df = filtered_df if download_scope == "현재 필터" else base_df
        output_filters = filters if download_scope == "현재 필터" else {}
        output_signature = inventory_output_signature(output_source_df, output_filters)
        payload = st.session_state.get(output_payload_key, {})
        if isinstance(payload, dict) and payload.get("signature") != output_signature:
            st.session_state.pop(output_payload_key, None)
            payload = {}
        with toolbar_pdf:
            if st.button("PDF", key=f"{source_type}_daily_pdf_prepare_{work_date}", use_container_width=True):
                output_df = inventory_status_output_dataframe(output_source_df.drop(columns=["선택"], errors="ignore"))
                payload = {
                    **payload,
                    "signature": output_signature,
                    "pdf": inventory_pdf_bytes(output_df, source_type, work_date, output_filters),
                    "pdf_filters": output_filters,
                    "pdf_count": len(output_df),
                }
                st.session_state[output_payload_key] = payload
            if isinstance(payload, dict) and payload.get("pdf"):
                st.download_button(
                    "PDF 저장",
                    data=payload["pdf"],
                    file_name=f"{source_type}_inventory_{work_date:%Y%m%d}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    key=f"{source_type}_daily_pdf_download_{work_date}",
                    on_click=record_inventory_output,
                    args=(source_type, work_date, "PDF", payload.get("pdf_filters", output_filters), int(payload.get("pdf_count") or len(output_source_df))),
                )
        with toolbar_excel:
            if st.button("Excel", key=f"{source_type}_daily_excel_prepare_{work_date}", use_container_width=True):
                output_df = inventory_status_output_dataframe(output_source_df.drop(columns=["선택"], errors="ignore"))
                payload = {
                    **payload,
                    "signature": output_signature,
                    "excel": dataframe_to_excel(output_df),
                    "excel_filters": output_filters,
                    "excel_count": len(output_df),
                }
                st.session_state[output_payload_key] = payload
            if isinstance(payload, dict) and payload.get("excel"):
                st.download_button(
                    "Excel 저장",
                    data=payload["excel"],
                    file_name=f"{source_type}_inventory_{work_date:%Y%m%d}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key=f"{source_type}_daily_download_{work_date}",
                    on_click=record_inventory_output,
                    args=(source_type, work_date, "EXCEL", payload.get("excel_filters", output_filters), int(payload.get("excel_count") or len(output_source_df))),
                )

    if filtered_df.empty:
        with st.container(key=f"{source_key(source_type)}_inventory_table_panel"):
            st.info("현재 필터 조건에 해당하는 재고 데이터가 없습니다.")
            if st.button("필터 전체 초기화", key=f"{source_type}_daily_empty_reset_{work_date}", use_container_width=True):
                reset_inventory_filters(source_key(source_type))
                st.rerun()
    else:
        with st.container(key=f"{source_key(source_type)}_inventory_table_panel"):
            render_inventory_visible_table(paged_df.drop(columns=["선택"], errors="ignore"), height=520)
            nav_prev, nav_info, nav_next, spacer = st.columns([0.8, 1, 0.8, 4.6], gap="small")
            filter_key = source_key(source_type)
            with nav_prev:
                if st.button("이전", key=f"{source_type}_daily_page_prev_{work_date}", disabled=page <= 1, use_container_width=True):
                    st.session_state[f"{filter_key}_page"] = max(page - 1, 1)
                    st.rerun()
            with nav_info:
                st.caption(f"{page:,} / {total_pages:,} 페이지")
            with nav_next:
                if st.button("다음", key=f"{source_type}_daily_page_next_{work_date}", disabled=page >= total_pages, use_container_width=True):
                    st.session_state[f"{filter_key}_page"] = min(page + 1, total_pages)
                    st.rerun()
            with spacer:
                st.empty()


def fetch_inventory_change_history(source_type: str, limit: int = 100) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if InventoryUploadHistory is None or InventoryOutputHistory is None or InventoryUploadSnapshot is None:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    def load(db):
        upload_rows = list(
            db.execute(
                select(InventoryUploadHistory)
                .where(InventoryUploadHistory.source_type == source_type)
                .order_by(InventoryUploadHistory.created_at.desc(), InventoryUploadHistory.id.desc())
                .limit(limit)
            ).scalars()
        )
        output_rows = list(
            db.execute(
                select(InventoryOutputHistory)
                .where(InventoryOutputHistory.source_type == source_type)
                .order_by(InventoryOutputHistory.created_at.desc(), InventoryOutputHistory.id.desc())
                .limit(limit)
            ).scalars()
        )
        snapshot_rows = list(
            db.execute(
                select(InventoryUploadSnapshot, InventoryUploadHistory.created_at, InventoryUploadHistory.upload_mode)
                .join(InventoryUploadHistory, InventoryUploadSnapshot.upload_history_id == InventoryUploadHistory.id)
                .where(InventoryUploadSnapshot.source_type == source_type)
                .order_by(InventoryUploadHistory.created_at.desc(), InventoryUploadSnapshot.id.desc())
                .limit(limit)
            ).all()
        )
        return upload_rows, snapshot_rows, output_rows

    uploads, snapshots, outputs = with_db(load) or ([], [], [])
    upload_df = pd.DataFrame(
        [
            {
                "처리일시": row.created_at,
                "기준일자": row.work_date,
                "구분": row.upload_mode,
                "파일명": row.file_name,
                "처리자": row.uploaded_by,
                "전체 행": row.total_rows,
                "반영": row.matched_count,
                "실패": row.failed_count,
                "중복": row.duplicate_count,
                "0 처리": row.zeroed_count,
            }
            for row in uploads
        ]
    )
    output_df = pd.DataFrame(
        [
            {
                "출력일시": row.created_at,
                "기준일자": row.work_date,
                "출력유형": row.output_type,
                "처리자": row.created_by,
                "상품 수": row.item_count,
            }
            for row in outputs
        ]
    )
    snapshot_df = pd.DataFrame(
        [
            {
                "수정일시": created_at,
                "기준일자": snapshot.work_date,
                "수정방식": upload_mode,
                "상품코드": snapshot.product_code,
                "바코드": snapshot.barcode,
                "상품명": snapshot.product_name,
                "기존재고": snapshot.previous_stock,
                "수정재고": snapshot.new_stock,
                "차이": int(snapshot.new_stock or 0) - int(snapshot.previous_stock or 0),
            }
            for snapshot, created_at, upload_mode in snapshots
        ]
    )
    return upload_df, snapshot_df, output_df


def render_inventory_change_history_panel(source_type: str) -> None:
    st.markdown('<div class="inventory-subsection-title">변경이력</div>', unsafe_allow_html=True)
    upload_df, snapshot_df, output_df = fetch_inventory_change_history(source_type)
    if upload_df.empty:
        st.info("표시할 재고 변경 이력이 없습니다.")
    else:
        st.markdown("#### 재고 반영/수정 이력")
        render_plain_inventory_table(upload_df, height=360, empty_message="표시할 재고 변경 이력이 없습니다.")
    if not snapshot_df.empty:
        with st.expander("상품별 실재고 수정 상세", expanded=True):
            render_plain_inventory_table(snapshot_df, height=360, empty_message="표시할 상품별 수정 상세가 없습니다.")
    if not output_df.empty:
        with st.expander("출력/다운로드 이력", expanded=False):
            render_plain_inventory_table(output_df, height=260, empty_message="표시할 출력 이력이 없습니다.")


def render_daily_tab(source_type: str, source_label: str | None = None) -> None:
    today = date.today()
    saved_work_dates = fetch_work_dates(source_type)
    default_work_date = saved_work_dates[0] if saved_work_dates else today
    daily_date_key = f"{source_type}_daily_date"
    pending_daily_date_key = f"_pending_{daily_date_key}"
    if pending_daily_date_key in st.session_state:
        st.session_state[daily_date_key] = st.session_state.pop(pending_daily_date_key)
    else:
        st.session_state.setdefault(daily_date_key, default_work_date)
    with st.container(key=f"{source_key(source_type)}_daily_header"):
        display_source = source_label or source_type
        render_inventory_html(
            f"""
            <div class="inventory-page-header">
                <h1>{escape(display_source)} 재고조회</h1>
                <p>조회 화면에서 ERP Snapshot 반영과 재고 확인을 한 번에 처리하고, 수동 재고 수정과 변경이력은 별도 흐름으로 관리합니다.</p>
            </div>
            """
        )

    workflow = inventory_text_tab_selector(
        ["재고 조회", "재고 수정", "변경이력"],
        f"{source_key(source_type)}_inventory_workflow",
        default="재고 조회",
        item_weight=0.72,
        trailing_weight=5.8,
    )

    if workflow == "변경이력":
        render_inventory_change_history_panel(source_type)
        return

    with st.container(key=f"{source_key(source_type)}_daily_date_wrapper"):
        work_date = st.date_input("기준일자", value=st.session_state[daily_date_key], key=daily_date_key)
    rows = fetch_master_inventory(source_type, work_date)
    if rows and saved_work_dates and work_date not in set(saved_work_dates):
        st.caption(f"{work_date:%Y-%m-%d} 기준 저장된 현재고가 없어 마스터 품목을 0재고로 표시합니다. 최신 저장일자는 {saved_work_dates[0]:%Y-%m-%d}입니다.")

    if workflow == "재고 수정":
        render_stock_registration_panel(source_type, work_date, rows)
        return

    render_inventory_lookup_panel(source_type, work_date, rows)


def inventory_pdf_bytes(df: pd.DataFrame, source_type: str, work_date: date, filters: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase import pdfmetrics
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font_name = "HYGoThic-Medium"
    bold_name = "HYGoThic-Medium"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(font_name))

    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=landscape(A4), leftMargin=8 * mm, rightMargin=8 * mm, topMargin=10 * mm, bottomMargin=10 * mm)
    styles = {
        "title": ParagraphStyle("inventory_title_v2", fontName=bold_name, fontSize=15, leading=19, alignment=TA_CENTER),
        "meta": ParagraphStyle("inventory_meta_v2", fontName=font_name, fontSize=8, leading=11, alignment=TA_LEFT),
        "cell": ParagraphStyle("inventory_cell_v2", fontName=font_name, fontSize=6.7, leading=8.2, alignment=TA_LEFT),
        "center": ParagraphStyle("inventory_center_v2", fontName=font_name, fontSize=6.7, leading=8.2, alignment=TA_CENTER),
        "right": ParagraphStyle("inventory_right_v2", fontName=font_name, fontSize=6.7, leading=8.2, alignment=TA_RIGHT),
    }
    export_columns = INVENTORY_STATUS_DISPLAY_COLUMNS
    export_df = df[[column for column in export_columns if column in df.columns]].copy()
    meta = [
        f"재고처: {source_type}",
        f"기준일자: {work_date:%Y-%m-%d}",
        f"생성일시: {pd.Timestamp.now():%Y-%m-%d %H:%M}",
        f"카테고리: {', '.join(filters.get('categories') or ['전체'])}",
        f"상품 수: {len(export_df):,}",
    ]
    story = [Paragraph("SCM 재고관리", styles["title"]), Spacer(1, 4 * mm), Paragraph(" / ".join(meta), styles["meta"]), Spacer(1, 4 * mm)]
    table_data = [[Paragraph(column, styles["center"]) for column in export_df.columns]]
    numeric_columns = {"가용재고", "1주 평균출고수량", "출고예정", "현재고", "리드타임"}
    for _, row in export_df.iterrows():
        cells = []
        for column in export_df.columns:
            value = row.get(column, "")
            if column in numeric_columns and clean_cell(value) != "":
                style = styles["right"]
                text = f"{float(value):,.2f}" if column == "1주 평균출고수량" else f"{to_int(value):,}"
            elif column in {"바코드", "재고상태"}:
                style = styles["center"]
                text = clean_cell(value)
            else:
                style = styles["cell"]
                text = clean_cell(value)
            cells.append(Paragraph(text, style))
        table_data.append(cells)
    widths = [17 * mm, 24 * mm, 46 * mm, 15 * mm, 20 * mm, 16 * mm, 15 * mm, 15 * mm, 16 * mm, 26 * mm, 22 * mm, 20 * mm, 14 * mm]
    table = Table(table_data, repeatRows=1, colWidths=widths[: len(export_df.columns)])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#516F8C")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D8D0C4")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F5F1")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 2.5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2.5),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ]
        )
    )
    story.append(table)
    doc.build(story)
    return output.getvalue()


def render_stock_upload_apply_result(outcome: dict | None, excluded_df: pd.DataFrame | None = None) -> None:
    if not isinstance(outcome, dict):
        return
    ok = outcome.get("ok", True)
    result_title = "재고 반영 완료" if ok else "재고 반영 오류"
    st.markdown(f'<div class="inventory-subsection-title">{result_title}</div>', unsafe_allow_html=True)
    if not ok and outcome.get("message"):
        st.error(str(outcome.get("message")))
    metric_cols = st.columns(6, gap="small")
    metric_cols[0].metric("총 데이터", f"{int(outcome.get('total_rows') or 0):,}건")
    success_count = outcome.get("count") if "count" in outcome else outcome.get("matched_count")
    metric_cols[1].metric("정상 반영", f"{int(success_count or 0):,}건")
    metric_cols[2].metric("미매칭", f"{int(outcome.get('unmatched_count') or 0):,}건")
    metric_cols[3].metric("중복", f"{int(outcome.get('duplicate_count') or 0):,}건")
    metric_cols[4].metric("오류", f"{int(outcome.get('error_count') or 0):,}건")
    metric_cols[5].metric("처리시간", f"{float(outcome.get('processing_seconds') or 0):,.1f}초")
    if isinstance(excluded_df, pd.DataFrame) and not excluded_df.empty:
        with st.expander(f"미매칭/중복/오류 {len(excluded_df):,}건 확인", expanded=False):
            render_inventory_visible_table(excluded_df, height=260)
    failure_df = inventory_apply_failure_dataframe(outcome)
    if not failure_df.empty:
        with st.expander(f"업데이트 실패 {len(failure_df):,}건 확인", expanded=True):
            render_inventory_visible_table(failure_df, height=260)


def render_inventory_update_panel(
    source_type: str,
    daily_date_key: str,
) -> date:
    with st.container(key=f"{source_key(source_type)}_inventory_update"):
        render_inventory_html(
            """
            <div class="inventory-update-heading">
                <div>
                    <h2>ERP 재고 업데이트</h2>
                    <p>ERP에서 추출한 Excel/CSV 파일로 선택 기준일의 재고 Snapshot을 바로 갱신합니다.</p>
                </div>
            </div>
            """
        )
        upload_cols = st.columns([0.92, 2.35, 0.95, 0.92, 1.35], gap="large")
        with upload_cols[0]:
            work_date = st.date_input("기준일자", value=st.session_state[daily_date_key], key=daily_date_key)

        upload_preview_key = f"{source_type}_stock_upload_preview_{work_date.isoformat()}"
        preview_df_key = f"{source_type}_inventory_preview_df_{work_date.isoformat()}"
        applied_df_key = f"{source_type}_applied_inventory_df_{work_date.isoformat()}"
        excluded_df_key = f"{source_type}_excluded_inventory_df_{work_date.isoformat()}"
        result_key = f"{source_type}_stock_upload_result_{work_date.isoformat()}"
        processing_key = f"{source_type}_stock_upload_processing_{work_date.isoformat()}"

        with upload_cols[1]:
            uploaded = st.file_uploader("파일 선택 또는 Drag & Drop", type=["xlsx", "xls", "csv"], key=f"{source_type}_stock_master_upload_{work_date}")
        with upload_cols[2]:
            upload_mode = st.radio("반영 범위", ["일부 재고", "전체 재고"], horizontal=False, key=f"{source_type}_stock_upload_mode")
        with upload_cols[3]:
            st.write("")
            disabled = uploaded is None or bool(st.session_state.get(processing_key))
            if st.button("재고 반영", key=f"{source_type}_stock_upload_btn_{work_date}", type="primary", use_container_width=True, disabled=disabled):
                mode = "full" if upload_mode == "전체 재고" else "partial"
                st.session_state[processing_key] = True
                try:
                    with st.spinner("ERP 재고를 반영하고 있습니다..."):
                        def upload_action(db):
                            preview = services.prepare_stock_upload_preview(db, source_type, work_date, uploaded.getvalue(), uploaded.name, mode)
                            if not preview or not preview.get("ok", True):
                                return preview, pd.DataFrame()
                            excluded_df = stock_excluded_display_dataframe(preview)
                            outcome = services.apply_erp_stock_upload_preview(db, source_type, work_date, preview, current_user_name())
                            return outcome, excluded_df

                        result_payload = with_db(upload_action)
                    outcome, excluded_df = result_payload if isinstance(result_payload, tuple) else (result_payload, pd.DataFrame())
                    for key in [upload_preview_key, preview_df_key, applied_df_key]:
                        st.session_state.pop(key, None)
                    st.session_state[result_key] = outcome
                    st.session_state[excluded_df_key] = excluded_df
                    if outcome and outcome.get("ok", True):
                        clear_inventory_data_caches()
                finally:
                    st.session_state[processing_key] = False
                st.rerun()
        with upload_cols[4]:
            st.info("파일 선택 후 재고 반영을 누르면 매칭·중복·유효성 검사를 내부 실행한 뒤 저장합니다.")
        render_stock_upload_apply_result(st.session_state.get(result_key), st.session_state.get(excluded_df_key))
        return work_date
