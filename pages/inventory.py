from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO

import pandas as pd
import streamlit as st
from sqlalchemy import delete, func, select

from pages import product_master as product_master_page

try:
    from backend.database import SessionLocal, init_db
    from backend.models import CategoryBomItem, InventoryDaily, MaterialInventoryItem, ProductionPlan, PurchaseRequest
    from backend import services
except (ModuleNotFoundError, RuntimeError) as exc:
    SessionLocal = None
    init_db = None
    CategoryBomItem = None
    InventoryDaily = None
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

DAILY_COLUMNS = [
    "삭제",
    "상품명",
    "현재고",
    "안전재고",
    "재고상태",
    "바코드",
    "리드타임",
]

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


def render_inventory_page() -> None:
    inject_inventory_css()
    product_master_page.inject_product_master_css()

    if not inventory_available():
        st.error(INVENTORY_IMPORT_ERROR or "재고관리 DB를 초기화하지 못했습니다. requirements.txt 설치 상태를 확인해주세요.")
        return

    sync_inventory_filter_from_query()

    current_tab, safe_tab, history_tab, mrp_tab, recommend_tab, material_tab = st.tabs(
        ["현재재고", "안전재고", "재고이력", "MRP", "발주추천", "자재/반제품"]
    )

    with current_tab:
        render_inventory_list_panel()
        render_outbound_history_linked_panel()
        tab_3pl, tab_offline, tab_warehouse = st.tabs(["3PL", "오프라인", "창고관리"])

        with tab_3pl:
            render_source_inventory_tabs("3PL")

        with tab_offline:
            render_source_inventory_tabs("오프라인")

        with tab_warehouse:
            render_source_inventory_tabs("창고")

    with safe_tab:
        render_safe_stock_tab()

    with history_tab:
        render_stock_history_tab()

    with mrp_tab:
        render_mrp_tab()

    with recommend_tab:
        render_purchase_recommendation_tab()

    with material_tab:
        render_material_inventory_tab()


def inventory_available() -> bool:
    if init_db is None or SessionLocal is None or services is None:
        return False
    try:
        init_db()
    except Exception as exc:
        global INVENTORY_IMPORT_ERROR
        INVENTORY_IMPORT_ERROR = f"재고관리 DB 초기화 실패: {exc}"
        return False
    return True


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


def render_source_inventory_tabs(source_type: str) -> None:
    stock_tab, inbound_tab, outbound_tab, dashboard_tab, master_tab = st.tabs(
        ["재고조회", "입고내역", "출고내역", "대시보드", "마스터 관리"]
    )
    with stock_tab:
        render_daily_tab(source_type)
    with inbound_tab:
        render_inbound_tab(source_type)
    with outbound_tab:
        render_outbound_tab(source_type)
    with dashboard_tab:
        render_inventory_dashboard_tab(source_type)
    with master_tab:
        product_master_page.render_master_tab(source_type, master_title(source_type))


def master_title(source_type: str) -> str:
    if source_type == "창고":
        return "창고관리 마스터"
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
    all_dates = []
    for source_type in SOURCE_TYPES:
        all_dates.extend(fetch_work_dates(source_type))
    return max(all_dates) if all_dates else date.today()


def render_inventory_list_panel() -> None:
    linked_filter = st.session_state.get("inventory_filter", "")
    default_filter = linked_filter if linked_filter in DASHBOARD_FILTER_LABELS else "all"
    default_date = dashboard_filter_work_date()
    if linked_filter:
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
    keyword = clean_cell(item_name).lower()
    if not keyword:
        return []
    rows = []
    for source_type in SOURCE_TYPES:
        source_rows = with_db(lambda db, source_type=source_type: [services.daily_to_dict(row) for row in services.list_outbound(db, source_type)]) or []
        for row in source_rows:
            work_date = parse_date_value(row.get("work_date"))
            if not work_date or work_date < start_date or work_date > end_date:
                continue
            haystack = " ".join(
                clean_cell(row.get(field)).lower()
                for field in ("product_name", "product_code", "barcode")
            )
            if keyword not in haystack:
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
        if stock_status == "입고필요":
            return True
        if stock_status in {"품절", "미출"}:
            return False
        return safe_stock > 0 and stock_for_status < safe_stock
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

    filter_cols = st.columns([1.2, 1.0, 1.0, 2.6], gap="small")
    keyword = filter_cols[0].text_input("검색", placeholder="품목명 / 품목코드 / 연결제품 / 공급처", key="material_inventory_keyword")
    type_filter = filter_cols[1].selectbox("유형", ["전체", "자재", "반제품"], key="material_inventory_type_filter")
    category_filter = filter_cols[2].selectbox("카테고리", ["전체", *category_options], key="material_inventory_category_filter")
    with filter_cols[3]:
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


def render_daily_tab(source_type: str) -> None:
    st.markdown(f'<div class="inventory-tab-title">{source_type} 재고조회</div>', unsafe_allow_html=True)
    today = date.today()
    default_date = today
    daily_date_key = f"{source_type}_daily_date_{today.isoformat()}"
    pending_daily_date_key = f"{source_type}_daily_date_sync"
    if pending_daily_date_key in st.session_state:
        st.session_state[daily_date_key] = st.session_state.pop(pending_daily_date_key)

    controls = st.columns([1.05, 0.9, 0.9, 0.9, 0.9], gap="small")
    with controls[0]:
        with st.container(key=f"inventory_control_{source_key(source_type)}_date"):
            st.markdown('<div class="inventory-control-label">날짜 선택</div>', unsafe_allow_html=True)
            work_date = st.date_input(
                "날짜 선택",
                value=default_date,
                key=daily_date_key,
                label_visibility="collapsed",
            )
            upload_daily("재고조회 엑셀 업로드", "stock", source_type, work_date, f"{source_type}_stock")
    with controls[1]:
        with st.container(key=f"inventory_control_{source_key(source_type)}_order"):
            st.markdown('<div class="inventory-control-label">날짜 선택</div>', unsafe_allow_html=True)
            order_date = st.date_input(
                "주문조회 날짜 선택",
                value=work_date,
                key=f"{source_type}_order_date_{today.isoformat()}",
                label_visibility="collapsed",
            )
            upload_daily("주문조회 엑셀 업로드", "order", source_type, order_date, f"{source_type}_order")
    with controls[2]:
        with st.container(key=f"inventory_control_{source_key(source_type)}_safe"):
            st.markdown('<div class="inventory-control-label">안전재고 계산</div>', unsafe_allow_html=True)
            if st.button("안전재고 계산", key=f"{source_type}_safe", use_container_width=True):
                show_result(with_db(lambda db: result("안전재고 계산 완료", services.calculate_safe_stock(db, source_type, work_date))))
            render_empty_upload_slot()
    with controls[3]:
        with st.container(key=f"inventory_control_{source_key(source_type)}_status"):
            st.markdown('<div class="inventory-control-label">재고상태 계산</div>', unsafe_allow_html=True)
            if st.button("재고상태 계산", key=f"{source_type}_status", use_container_width=True):
                show_result(with_db(lambda db: result("재고상태 계산 완료", services.update_status(db, source_type, work_date))))
            render_empty_upload_slot()
    with controls[4]:
        with st.container(key=f"inventory_control_{source_key(source_type)}_cycle"):
            st.markdown('<div class="inventory-control-label">입고주기 계산</div>', unsafe_allow_html=True)
            if st.button("입고주기 계산", key=f"{source_type}_cycle", use_container_width=True):
                show_result(with_db(lambda db: result("입고주기 계산 완료", services.calculate_inbound_cycle(db, source_type))))
            render_empty_upload_slot()

    df = daily_to_editor(fetch_daily(source_type, work_date))
    daily_buffer_key = f"{source_type}_daily_editor_buffer_{work_date.isoformat()}"
    if daily_buffer_key not in st.session_state:
        st.session_state[daily_buffer_key] = df
    edited = st.session_state[daily_buffer_key]
    with st.form(key=f"{source_type}_daily_editor_form_{work_date.isoformat()}", clear_on_submit=False):
        edited = st.data_editor(
            st.session_state[daily_buffer_key],
            num_rows="dynamic",
            hide_index=True,
            use_container_width=True,
            key=f"{source_type}_daily_editor_{work_date.isoformat()}",
            column_order=DAILY_COLUMNS,
            column_config={
                "삭제": st.column_config.CheckboxColumn("삭제", default=False),
                "상품명": st.column_config.TextColumn("상품명", width="large"),
                "현재고": st.column_config.NumberColumn("현재고", step=1),
                "안전재고": st.column_config.NumberColumn("안전재고", step=1),
                "재고상태": st.column_config.SelectboxColumn("재고상태", options=["", "미출", "품절", "입고필요"]),
                "바코드": st.column_config.TextColumn("바코드", width="medium"),
                "리드타임": st.column_config.NumberColumn("리드타임", min_value=0, step=1),
            },
        )
        if st.form_submit_button("저장", type="primary", use_container_width=True):
            rows = daily_payload(edited, source_type, work_date)
            outcome = with_db(lambda db: result("재고조회 저장 완료", services.bulk_save_daily(db, source_type, work_date, rows)))
            if outcome and outcome.get("ok", True):
                clear_inventory_editor_buffer(daily_buffer_key)
            else:
                st.session_state[daily_buffer_key] = edited
            show_result(outcome)

    actions = st.columns([1.05, 4.55], gap="small")
    with actions[0]:
        download_data = daily_excel(source_type, work_date)
        st.download_button(
            "엑셀 다운로드",
            data=download_data or dataframe_to_excel(st.session_state.get(daily_buffer_key, edited)),
            file_name=f"{source_type}_재고조회_{work_date:%Y%m%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key=f"{source_type}_daily_download",
        )
    with actions[1]:
        st.empty()


def render_inbound_tab(source_type: str) -> None:
    st.markdown(f'<div class="inventory-tab-title">{source_type} 입고내역</div>', unsafe_allow_html=True)
    upload_col, apply_col, download_col, spacer = st.columns([1.4, 0.95, 1.05, 3.2], gap="small")
    with upload_col:
        uploaded = st.file_uploader("입고내역 엑셀 업로드", type=["xlsx", "xls", "html"], key=f"{source_type}_inbound_file")
        if st.button("입고내역 엑셀 반영", key=f"{source_type}_inbound_import", use_container_width=True):
            if uploaded is None:
                st.warning("먼저 엑셀 파일을 업로드하세요.")
            else:
                outcome = with_db(lambda db: import_upload_result("입고내역 엑셀 반영 완료", services.import_inbound_excel(db, source_type, uploaded.getvalue())))
                if outcome and outcome.get("ok", True):
                    clear_inventory_editor_buffer(f"{source_type}_inbound_editor_buffer")
                show_result(outcome)
    with apply_col:
        apply_date = st.date_input("반영 기준일자", value=date.today(), key=f"{source_type}_inbound_apply_date")
        if st.button("재고현황에 반영", key=f"{source_type}_inbound_apply", type="primary", use_container_width=True):
            show_result(with_db(lambda db: result("재고현황 반영 완료", services.apply_inbound_to_stock(db, source_type, apply_date))))
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
    if inbound_buffer_key not in st.session_state:
        st.session_state[inbound_buffer_key] = df
    inbound_sku_options = sorted(set([*product_sku_options(source_type), *[value for value in df.get("SKU", pd.Series(dtype=str)).astype(str).tolist() if value]]))
    with st.form(key=f"{source_type}_inbound_editor_form", clear_on_submit=False):
        edited = st.data_editor(
            st.session_state[inbound_buffer_key],
            num_rows="dynamic",
            hide_index=True,
            use_container_width=True,
            key=f"{source_type}_inbound_editor",
            column_order=INBOUND_COLUMNS,
            column_config={
                "삭제": st.column_config.CheckboxColumn("삭제", default=False),
                "SKU": st.column_config.SelectboxColumn("SKU", options=inbound_sku_options) if inbound_sku_options else st.column_config.TextColumn("SKU"),
                "입고일자": st.column_config.DateColumn("입고일자"),
                "공급처": st.column_config.TextColumn("공급처", disabled=True),
                "입고수량": st.column_config.NumberColumn("입고수량", step=1),
            },
        )
        if st.form_submit_button("입고내역 저장", type="primary", use_container_width=True):
            rows = inbound_payload(edited, source_type)
            outcome = with_db(lambda db: result("입고내역 저장 완료", services.bulk_save_inbound(db, source_type, rows)))
            if outcome and outcome.get("ok", True):
                clear_inventory_editor_buffer(inbound_buffer_key)
            else:
                st.session_state[inbound_buffer_key] = edited
            show_result(outcome)


def render_outbound_tab(source_type: str) -> None:
    st.markdown(f'<div class="inventory-tab-title">{source_type} 출고내역</div>', unsafe_allow_html=True)
    linked_item = clean_cell(st.session_state.get("outbound_item_filter"))
    default_end = parse_date_value(st.session_state.get("outbound_end_date")) or date.today()
    default_start = parse_date_value(st.session_state.get("outbound_start_date")) or (default_end - timedelta(days=30))
    filter_cols = st.columns([1.35, 0.95, 0.95, 2.7], gap="small")
    item_filter = filter_cols[0].text_input("품목 필터", value=linked_item, placeholder="상품명 / SKU / 바코드", key=f"{source_type}_outbound_item_filter")
    start_date = filter_cols[1].date_input("시작일", value=default_start, key=f"{source_type}_outbound_start")
    end_date = filter_cols[2].date_input("종료일", value=default_end, key=f"{source_type}_outbound_end")
    with filter_cols[3]:
        st.caption("출고수량이 있는 기준일자별 품목 이력을 표시합니다.")

    rows = with_db(lambda db: [services.daily_to_dict(row) for row in services.list_outbound(db, source_type)]) or []
    df = pd.DataFrame(
        [
            {
                "기준일자": row.get("work_date"),
                "SKU": row.get("product_code", ""),
                "바코드": row.get("barcode", ""),
                "상품명": row.get("product_name", ""),
                "출고수량": row.get("outbound_qty", 0),
                "재고상태": row.get("stock_status", ""),
            }
            for row in rows
        ]
    )
    if not df.empty:
        df["기준일자"] = pd.to_datetime(df["기준일자"], errors="coerce")
        df = df.dropna(subset=["기준일자"])
        df = df[(df["기준일자"].dt.date >= start_date) & (df["기준일자"].dt.date <= end_date)]
        keyword = clean_cell(item_filter).lower()
        if keyword:
            mask = df[["상품명", "SKU", "바코드"]].fillna("").astype(str).agg(" ".join, axis=1).str.lower().str.contains(keyword, regex=False)
            df = df[mask]
        df["기준일자"] = df["기준일자"].dt.date
    if df.empty:
        st.info("선택한 기간의 출고 데이터가 없습니다.")
        return
    st.dataframe(df, hide_index=True, use_container_width=True)
    st.download_button(
        "출고내역 엑셀 다운로드",
        data=dataframe_to_excel(df),
        file_name=f"{source_type}_출고내역.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=False,
        key=f"{source_type}_outbound_download",
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
    st.bar_chart(df.set_index("구분")[label])


def fetch_work_dates(source_type: str) -> list[date]:
    data = with_db(lambda db: services.list_work_dates(db, source_type)) or []
    dates = pd.to_datetime(data, errors="coerce")
    return [value.date() for value in dates if not pd.isna(value)]


def fetch_daily(source_type: str, work_date: date) -> list[dict]:
    return with_db(lambda db: [services.daily_to_dict(row) for row in services.list_daily(db, source_type, work_date)]) or []


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
        lookup[(row.product_name, row.barcode or "")] = lookup.get((row.product_name, row.barcode or ""), 0) + stock
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
        avg_outbound = avg_daily_outbound(db, source_type, row.product_name, row.barcode or "", work_date, 14)
        leadtime_need = ceil(avg_outbound * lead_time) if include_leadtime and lead_time else 0
        reorder_point = safe_stock + leadtime_need
        shortage_qty = max(reorder_point - current_stock, 0)
        below_safe = current_stock <= safe_stock if safe_stock else current_stock <= 0
        if not below_safe and shortage_qty <= 0:
            continue
        result_rows.append(
            {
                "상품명": row.product_name,
                "규격": "",
                "현재재고": current_stock,
                "안전재고": safe_stock,
                "리드타임": lead_time,
                "리드타임 예상소요": leadtime_need,
                "부족수량": max(safe_stock - current_stock, 0),
                "발주추천수량": max(shortage_qty, max(safe_stock - current_stock, 0), 1),
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
        quantity = to_int(record.get("발주추천수량"))
        if not item_name or quantity <= 0 or has_open_purchase_request(db, item_name):
            continue
        db.add(
            PurchaseRequest(
                pr_number=next_inventory_number(db, PurchaseRequest, PurchaseRequest.pr_number, "PR"),
                department="자재/구매",
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
        mapped.append(
            {
                "삭제": False,
                "상품명": row.get("product_name", ""),
                "현재고": row.get("current_stock", 0),
                "안전재고": row.get("safe_stock", 0),
                "재고상태": row.get("stock_status", ""),
                "바코드": row.get("barcode", ""),
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
        if bool(row.get("삭제", False)):
            continue
        product_name = clean_cell(row.get("상품명"))
        if not product_name:
            continue
        rows.append(
            {
                "source_type": source_type,
                "work_date": work_date.isoformat(),
                "category": "",
                "supplier": "",
                "product_code": "",
                "product_name": product_name,
                "barcode": clean_cell(row.get("바코드")),
                "current_stock": to_int(row.get("현재고")),
                "available_stock": to_int(row.get("현재고")),
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
                "vendor": "",
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
    return str(value).strip()


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


def dataframe_to_excel(df: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        export_df = df.drop(columns=["삭제"], errors="ignore").copy()
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

        numeric_columns = {"현재고", "보유재고", "가용재고", "안전재고", "리드타임", "출고수량", "입고수량"}
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
                "품절": workbook.add_format({"bg_color": "#FFE4E4", "font_color": "#B42318"}),
                "미출": workbook.add_format({"bg_color": "#FFF1D6", "font_color": "#A15C00"}),
                "입고필요": workbook.add_format({"bg_color": "#FFF7CC", "font_color": "#7A5D00"}),
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
            color: white;
            font-size: 1.15rem;
            font-weight: 850;
            margin: 0.5rem 0 0.65rem;
        }
        div[class*="st-key-inventory_control_"] {
            background: rgba(5, 38, 34, 0.22);
            border: 1px solid rgba(87, 178, 165, 0.18);
            border-radius: 7px;
            min-height: 190px;
            padding: 0.65rem 0.7rem 0.72rem;
        }
        div[class*="st-key-inventory_control_"] [data-testid="stVerticalBlock"] {
            gap: 0.38rem;
        }
        .inventory-control-label {
            align-items: center;
            color: #f2fffb;
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
            background: rgba(7, 58, 52, 0.54);
            border: 1px solid rgba(87, 178, 165, 0.24);
            border-radius: 8px;
            margin: 0.15rem 0 1.15rem;
            padding: 0.72rem 0.78rem;
        }
        .st-key-inventory_outbound_linked_panel {
            background: rgba(7, 58, 52, 0.54);
            border: 1px solid rgba(87, 178, 165, 0.24);
            border-radius: 8px;
            margin: 0.15rem 0 1.15rem;
            padding: 0.72rem 0.78rem;
        }
        .st-key-inventory_dashboard_linked_panel h3 {
            color: #ffffff;
            font-size: 1.02rem;
            margin-bottom: 0.1rem;
        }
        .st-key-inventory_outbound_linked_panel h3 {
            color: #ffffff;
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
        div[data-testid="stDataFrame"],
        div[data-testid="stDataEditor"] {
            width: 100% !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
