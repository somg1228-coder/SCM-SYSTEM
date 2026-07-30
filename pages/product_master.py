from __future__ import annotations

from io import BytesIO
from math import ceil
import re

import pandas as pd
import streamlit as st

try:
    from backend.database import SessionLocal, init_db, log_sqlite_writability
    from backend import services
except (ModuleNotFoundError, RuntimeError) as exc:
    SessionLocal = None
    init_db = None
    log_sqlite_writability = None
    services = None
    PRODUCT_MASTER_IMPORT_ERROR = str(exc)
else:
    PRODUCT_MASTER_IMPORT_ERROR = ""


SOURCE_TABS = [
    ("3PL", "3PL 마스터"),
    ("오프라인", "오프라인 마스터"),
    ("창고", "창고관리 마스터"),
]

SOURCE_KEY_MAP = {
    "오프라인": "offline",
    "3PL": "threepl",
    "창고": "warehouse",
}

MASTER_COLUMNS = [
    "미사용 처리",
    "SKU",
    "바코드",
    "상품명",
    "카테고리",
    "브랜드",
    "공급처",
    "입수",
    "박스입수",
    "기본 리드타임",
    "최소재고",
    "정렬순서",
    "사용여부",
    "비고",
]

THREEPL_MASTER_COLUMNS = [
    "카테고리",
    "바코드",
    "상품명",
    "업체명",
    "박스/파렛트 단위",
    "담당자",
    "리드타임",
]

THREEPL_MASTER_INTERNAL_COLUMNS = ["SKU", "브랜드", "안전재고", "정렬순서", "사용여부"]

OFFLINE_MASTER_COLUMNS = [
    "미사용 처리",
    "카테고리",
    "상품명",
    "88바코드",
    "리드타임",
    "정렬순서",
    "사용여부",
    "비고",
]


def render_product_master_page() -> None:
    inject_product_master_css()
    st.markdown('<div class="product-master-title">마스터 관리</div>', unsafe_allow_html=True)

    if not product_master_available():
        st.error(PRODUCT_MASTER_IMPORT_ERROR or "상품 마스터 DB를 초기화하지 못했습니다.")
        return

    tabs = st.tabs([label for _, label in SOURCE_TABS])
    for tab, (source_type, title) in zip(tabs, SOURCE_TABS, strict=True):
        with tab:
            render_master_tab(source_type, title)


def render_master_tab(source_type: str, title: str) -> None:
    key = source_key(source_type)
    st.markdown(f'<div class="product-master-subtitle">{title}</div>', unsafe_allow_html=True)

    if uses_threepl_master_form(source_type):
        render_threepl_master_tab(source_type, title, key)
        return

    with st.container(key=f"product_master_{key}_controls"):
        st.markdown('<div class="product-master-control-title">마스터 기준 관리</div>', unsafe_allow_html=True)
        keyword_col, active_col, upload_col, import_col, template_col, download_col = st.columns(
            [1.35, 0.75, 1.15, 0.72, 0.95, 0.95],
            gap="small",
        )
        with keyword_col:
            keyword = st.text_input(
                "검색",
                placeholder="상품명 / 카테고리 / 바코드 / SKU / 브랜드 / 공급처",
                key=f"product_master_{key}_keyword",
            )
        with active_col:
            active_filter = st.selectbox(
                "사용여부",
                ["전체", "사용", "미사용"],
                key=f"product_master_{key}_active_filter",
            )

        rows = fetch_master(source_type, keyword, active_filter)

        with upload_col:
            uploaded = st.file_uploader(
                "엑셀 업로드",
                type=["xlsx", "xls", "html"],
                key=f"product_master_{key}_upload",
                help="마스터 양식의 상품명, 바코드, 카테고리, 리드타임 등 기준 정보를 업로드할 수 있습니다.",
            )
        with import_col:
            st.write("")
            if st.button("엑셀 반영", key=f"product_master_{key}_import_btn", use_container_width=True):
                if uploaded is None:
                    st.warning(f"먼저 {title} 엑셀을 업로드하세요.")
                else:
                    outcome = with_db(
                        lambda db: services.import_product_master_excel(
                            db,
                            source_type,
                            uploaded.getvalue(),
                        )
                    )
                    if outcome and outcome.get("ok", True):
                        clear_master_editor_buffer(key)
                    show_result(outcome)
        with template_col:
            st.write("")
            template_df = (
                threepl_master_template_df()
                if uses_threepl_master_form(source_type)
                else offline_master_template_df()
                if uses_simple_master_form(source_type)
                else services.product_master_template_df()
            )
            st.download_button(
                "양식 다운로드",
                data=master_excel(template_df, title),
                file_name=f"{title}_양식.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key=f"product_master_{key}_template_download",
            )
        with download_col:
            st.write("")
            download_df = (
                threepl_master_to_editor(rows)
                if uses_threepl_master_form(source_type)
                else offline_master_to_editor(rows, keyword, active_filter)
                if uses_simple_master_form(source_type)
                else master_to_editor(rows)
            ).drop(columns=["미사용 처리"], errors="ignore")
            if uses_threepl_master_form(source_type):
                download_df = download_df[THREEPL_MASTER_COLUMNS]
            st.download_button(
                "마스터 다운로드",
                data=master_excel(download_df, title),
                file_name=f"{title}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key=f"product_master_{key}_download",
            )

    with st.container(key=f"product_master_{key}_editor_panel"):
        st.markdown('<div class="product-master-form-title">마스터 작성 폼</div>', unsafe_allow_html=True)
        if uses_simple_master_form(source_type):
            st.caption("엑셀 양식 기준으로 수정하고, 필요한 상품은 표 하단에 바로 추가하세요.")
        else:
            st.caption("엑셀 반영 후 바로 수정하거나, 단품 상품 추가를 펼쳐 개별 상품을 등록하세요.")
            render_single_product_form(source_type, key)

        df = (
            threepl_master_to_editor(rows)
            if uses_threepl_master_form(source_type)
            else offline_master_to_editor(rows, keyword, active_filter)
            if uses_simple_master_form(source_type)
            else master_to_editor(rows)
        )
        editor_buffer_key = f"product_master_{key}_editor_buffer"
        if editor_buffer_key not in st.session_state:
            st.session_state[editor_buffer_key] = df
        elif uses_threepl_master_form(source_type) and any(column not in st.session_state[editor_buffer_key].columns for column in THREEPL_MASTER_COLUMNS):
            st.session_state[editor_buffer_key] = df
        editor_df = st.session_state[editor_buffer_key]
        with st.form(key=f"product_master_{key}_editor_form", clear_on_submit=False):
            edited = st.data_editor(
                editor_df,
                hide_index=True,
                use_container_width=True,
                num_rows="dynamic",
                height=470 if uses_simple_master_form(source_type) or uses_threepl_master_form(source_type) else 360,
                key=f"product_master_{key}_editor",
                column_order=master_column_order(source_type),
                column_config=master_column_config_for_source(source_type),
                disabled=master_disabled_columns(source_type),
            )
            if st.form_submit_button("저장", type="primary", use_container_width=True):
                payload = editor_payload_for_source(source_type, edited)
                outcome = with_db(lambda db: services.bulk_save_product_master(db, source_type, payload))
                if outcome and outcome.get("ok", True):
                    clear_master_editor_buffer(key)
                else:
                    st.session_state[editor_buffer_key] = edited
                show_result(outcome)

        sync_col, spacer = st.columns([1.05, 6.1], gap="small")
        with sync_col:
            if st.button("재고 데이터 동기화", key=f"product_master_{key}_sync", use_container_width=True):
                show_result(
                    with_db(
                        lambda db: {
                            "ok": True,
                            "message": f"{title} 기준 재고 데이터 동기화 완료",
                            "count": services.sync_inventory_from_product_master(db, source_type),
                        }
                    )
                )
        with spacer:
            st.empty()


def render_threepl_master_tab(source_type: str, title: str, key: str) -> None:
    all_rows = fetch_master(source_type, "", "전체")
    all_df = threepl_master_to_editor(all_rows)
    filters = render_threepl_master_filters(key, all_df)
    filtered_df = apply_threepl_master_filters(all_df, filters)
    sorted_df = sort_threepl_master(filtered_df, filters)
    page_df, page, total_pages = paginate_dataframe(sorted_df, filters["page_size"], filters["page"])
    preview_key = f"product_master_{key}_import_preview"
    result_key = f"product_master_{key}_import_result"

    with st.container(key=f"product_master_{key}_controls"):
        st.markdown('<div class="product-master-control-title">마스터 기준 관리</div>', unsafe_allow_html=True)
        upload_col, import_col, template_col, download_col, count_col = st.columns(
            [1.35, 0.82, 0.95, 0.95, 1.15],
            gap="small",
        )
        with upload_col:
            uploaded = st.file_uploader(
                "엑셀 업로드",
                type=["xlsx", "xls", "html"],
                key=f"product_master_{key}_upload",
                help="3PL 마스터 기준정보를 업로드할 수 있습니다.",
            )
        with import_col:
            st.write("")
            if st.button("미리보기", key=f"product_master_{key}_import_preview_btn", use_container_width=True):
                if uploaded is None:
                    st.warning(f"먼저 {title} 엑셀을 업로드하세요.")
                else:
                    if show_sqlite_write_status("3PL 마스터 업로드 미리보기"):
                        preview = with_db(lambda db: services.prepare_threepl_master_import_preview(db, uploaded.getvalue()))
                        if preview and preview.get("ok", True):
                            st.session_state[preview_key] = preview
                            st.session_state.pop(result_key, None)
                        else:
                            st.session_state[result_key] = preview
                        st.rerun()
        with template_col:
            st.write("")
            st.download_button(
                "양식 다운로드",
                data=threepl_master_excel(threepl_master_template_df()),
                file_name=f"{title}_양식.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key=f"product_master_{key}_template_download",
            )
        with download_col:
            st.write("")
            download_df = sorted_df[THREEPL_MASTER_COLUMNS] if not sorted_df.empty else threepl_master_template_df()
            st.download_button(
                "마스터 다운로드",
                data=threepl_master_excel(download_df),
                file_name=f"{title}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key=f"product_master_{key}_download",
            )
        with count_col:
            st.write("")
            st.caption(f"검색 결과 {len(sorted_df):,}개 / 전체 {len(all_df):,}개")

    render_threepl_import_result(result_key)
    render_threepl_import_preview(source_type, key, preview_key, result_key)

    with st.container(key=f"product_master_{key}_editor_panel"):
        st.markdown('<div class="product-master-form-title">마스터 작성 폼</div>', unsafe_allow_html=True)
        st.caption("평상시 관리 품목의 기준정보만 관리합니다. 현재고, 안전재고, 재고상태는 재고관리 화면에서 확인하세요.")

        if sorted_df.empty:
            st.info("조건에 맞는 품목이 없습니다. 필터를 초기화하거나 새 품목을 표 하단에 추가하세요.")
            if st.button("필터 전체 초기화", key=f"product_master_{key}_empty_reset", use_container_width=True):
                reset_threepl_master_filters(key)
                st.rerun()

        with st.form(key=f"product_master_{key}_editor_form", clear_on_submit=False):
            edited = st.data_editor(
                page_df,
                hide_index=True,
                use_container_width=True,
                num_rows="dynamic",
                height=470,
                key=f"product_master_{key}_editor_{page}_{len(sorted_df)}",
                column_order=THREEPL_MASTER_COLUMNS,
                column_config=threepl_master_column_config(),
                disabled=[],
            )
            submitted = st.form_submit_button("저장", type="primary", use_container_width=True)
        if submitted:
            payload = threepl_editor_to_payload(edited)
            outcome = with_db(lambda db: services.bulk_save_product_master(db, source_type, payload))
            if outcome and outcome.get("ok", True):
                clear_master_editor_buffer(key)
            show_result(outcome)

        nav_prev, nav_info, nav_next, sync_col, spacer = st.columns([0.8, 1.0, 0.8, 1.15, 3.55], gap="small")
        with nav_prev:
            if st.button("이전", key=f"product_master_{key}_page_prev", disabled=page <= 1, use_container_width=True):
                st.session_state[f"product_master_{key}_page"] = max(page - 1, 1)
                st.rerun()
        with nav_info:
            st.caption(f"{page:,} / {total_pages:,} 페이지")
        with nav_next:
            if st.button("다음", key=f"product_master_{key}_page_next", disabled=page >= total_pages, use_container_width=True):
                st.session_state[f"product_master_{key}_page"] = min(page + 1, total_pages)
                st.rerun()
        with sync_col:
            if st.button("재고 데이터 동기화", key=f"product_master_{key}_sync", use_container_width=True):
                show_result(
                    with_db(
                        lambda db: {
                            "ok": True,
                            "message": f"{title} 기준 재고 데이터 동기화 완료",
                            "count": services.sync_inventory_from_product_master(db, source_type),
                        }
                    )
                )
        with spacer:
            st.empty()


def render_threepl_import_preview(source_type: str, key: str, preview_key: str, result_key: str) -> None:
    preview = st.session_state.get(preview_key)
    if not preview:
        return
    st.markdown("#### 3PL 마스터 업로드 미리보기")
    render_threepl_import_summary(preview)
    render_threepl_import_details(preview)
    apply_col, cancel_col, spacer = st.columns([1.0, 1.0, 4.0], gap="small")
    applyable_count = sum(1 for detail in preview.get("details", []) if detail.get("_apply"))
    with apply_col:
        if st.button("미리보기 내용 반영", type="primary", key=f"product_master_{key}_preview_apply", disabled=applyable_count == 0, use_container_width=True):
            if show_sqlite_write_status("3PL 마스터 업로드 반영"):
                result = with_db(lambda db: services.apply_threepl_master_import_preview(db, preview))
                st.session_state[result_key] = result
                st.session_state.pop(preview_key, None)
                clear_master_editor_buffer(key)
                st.rerun()
    with cancel_col:
        if st.button("취소", key=f"product_master_{key}_preview_cancel", use_container_width=True):
            st.session_state.pop(preview_key, None)
            st.rerun()
    with spacer:
        st.caption("기준정보만 반영되며 현재고, 안전재고, 입출고 내역, 이력 데이터는 초기화하지 않습니다.")


def render_threepl_import_result(result_key: str) -> None:
    result = st.session_state.get(result_key)
    if not result:
        return
    if result.get("ok", True):
        st.success(f'{result.get("message", "처리 완료")} ({result.get("count", 0)}건)')
    else:
        st.warning(result.get("message", "처리하지 못했습니다."))
    render_threepl_import_summary(result)
    render_threepl_import_details(result)
    if st.button("업로드 결과 닫기", key=f"{result_key}_close"):
        st.session_state.pop(result_key, None)
        st.rerun()


def render_threepl_import_summary(payload: dict) -> None:
    summary = payload.get("summary") or {}
    if not summary:
        return
    labels = [
        "전체 엑셀 행 수",
        "신규 등록 수",
        "기존 품목 업데이트 수",
        "변경 없음 수",
        "파일 내부 중복 수",
        "경고 수",
        "실패 수",
    ]
    cols = st.columns(len(labels), gap="small")
    for col, label in zip(cols, labels, strict=True):
        col.metric(label, f"{int(summary.get(label, 0) or 0):,}")


def render_threepl_import_details(payload: dict) -> None:
    details = payload.get("details") or []
    if not details:
        return
    display_rows = [
        {
            "행 번호": detail.get("행 번호", ""),
            "바코드": detail.get("바코드", detail.get("SKU", "")),
            "상품명": detail.get("상품명", ""),
            "처리 유형": detail.get("처리 유형", ""),
            "변경 항목": detail.get("변경 항목", ""),
            "처리 결과": detail.get("처리 결과", ""),
        }
        for detail in details
    ]
    st.dataframe(pd.DataFrame(display_rows), hide_index=True, use_container_width=True, height=260)


def render_single_product_form(source_type: str, key: str) -> None:
    with st.expander("단품 상품 추가", expanded=False):
        with st.form(key=f"product_master_{key}_single_form", clear_on_submit=True):
            row1 = st.columns([1.0, 1.0, 2.2], gap="small")
            with row1[0]:
                sku = st.text_input("SKU", key=f"product_master_{key}_single_sku")
            with row1[1]:
                barcode = st.text_input("바코드", key=f"product_master_{key}_single_barcode")
            with row1[2]:
                product_name = st.text_input("상품명", key=f"product_master_{key}_single_product_name")

            row2 = st.columns([1.0, 1.0, 1.0, 1.7], gap="small")
            with row2[0]:
                category = st.text_input("카테고리", key=f"product_master_{key}_single_category")
            with row2[1]:
                brand = st.text_input("브랜드", key=f"product_master_{key}_single_brand")
            with row2[2]:
                supplier = st.text_input("공급처", key=f"product_master_{key}_single_supplier")
            with row2[3]:
                memo = st.text_input("비고", key=f"product_master_{key}_single_memo")

            row3 = st.columns([0.8, 0.8, 0.9, 0.8, 0.8, 0.9], gap="small")
            with row3[0]:
                pack_qty = st.number_input("입수", min_value=0, step=1, key=f"product_master_{key}_single_pack_qty")
            with row3[1]:
                box_qty = st.number_input("박스입수", min_value=0, step=1, key=f"product_master_{key}_single_box_qty")
            with row3[2]:
                default_lead_time = st.number_input("기본 리드타임", min_value=0, step=1, key=f"product_master_{key}_single_lead_time")
            with row3[3]:
                min_stock = st.number_input("최소재고", min_value=0, step=1, key=f"product_master_{key}_single_min_stock")
            with row3[4]:
                sort_order = st.number_input("정렬순서", min_value=0, step=1, key=f"product_master_{key}_single_sort_order")
            with row3[5]:
                is_active = st.selectbox("사용여부", ["사용", "미사용"], key=f"product_master_{key}_single_is_active")

            submitted = st.form_submit_button("단품 추가", type="primary", use_container_width=True)
            if submitted:
                row = {
                    "SKU": sku,
                    "바코드": barcode,
                    "상품명": product_name,
                    "카테고리": category,
                    "브랜드": brand,
                    "공급처": supplier,
                    "입수": pack_qty,
                    "박스입수": box_qty,
                    "기본 리드타임": default_lead_time,
                    "최소재고": min_stock,
                    "정렬순서": sort_order,
                    "사용여부": is_active,
                    "비고": memo,
                }
                outcome = with_db(lambda db: services.add_product_master(db, source_type, row))
                if outcome and outcome.get("ok", True):
                    clear_master_editor_buffer(key)
                show_result(outcome)


def clear_master_editor_buffer(key: str) -> None:
    st.session_state.pop(f"product_master_{key}_editor_buffer", None)


def source_key(source_type: str) -> str:
    return SOURCE_KEY_MAP.get(source_type, source_type)


def uses_simple_master_form(source_type: str) -> bool:
    return source_type in {"오프라인", "창고"}


def uses_threepl_master_form(source_type: str) -> bool:
    return source_type == "3PL"


def format_box_pallet_unit(box_qty, pallet_qty) -> str:
    box_value = to_int_value(box_qty)
    pallet_value = to_int_value(pallet_qty)
    parts = []
    if box_value:
        parts.append(f"박스당 {box_value}EA")
    if pallet_value:
        parts.append(f"파렛트당 {pallet_value}BOX")
    return " / ".join(parts)


def parse_box_pallet_unit(value) -> tuple[int, int]:
    text = clean_value(value)
    if not text:
        return 0, 0
    normalized = text.upper().replace(",", "")
    box_qty = 0
    pallet_qty = 0

    box_patterns = [
        r"(?:박스당|박스|BOX)\s*(\d+)\s*EA",
        r"(\d+)\s*EA",
    ]
    pallet_patterns = [
        r"(?:파렛트당|파렛트|PALLET|PL)\D*(\d+)\s*BOX",
        r"/[^/]*(\d+)\s*BOX",
    ]
    for pattern in box_patterns:
        match = re.search(pattern, normalized)
        if match:
            box_qty = to_int_value(match.group(1))
            break
    for pattern in pallet_patterns:
        match = re.search(pattern, normalized)
        if match:
            pallet_qty = to_int_value(match.group(1))
            break
    if not box_qty or not pallet_qty:
        numbers = [to_int_value(number) for number in re.findall(r"\d+", normalized)]
        if numbers and not box_qty:
            box_qty = numbers[0]
        if len(numbers) > 1 and not pallet_qty:
            pallet_qty = numbers[-1]
    return box_qty, pallet_qty


def render_threepl_master_filters(key: str, df: pd.DataFrame) -> dict:
    defaults = {
        "keyword": "",
        "categories": [],
        "suppliers": [],
        "managers": [],
        "lead_times": [],
        "active": "전체",
        "sort_column": "카테고리",
        "sort_order": "오름차순",
        "page_size": 30,
        "page": 1,
    }
    for name, value in defaults.items():
        st.session_state.setdefault(f"product_master_{key}_{name}", value)

    category_options = sorted(clean_value(value) for value in df.get("카테고리", pd.Series(dtype=str)).dropna().unique() if clean_value(value))
    supplier_options = sorted(clean_value(value) for value in df.get("업체명", pd.Series(dtype=str)).dropna().unique() if clean_value(value))
    manager_options = sorted(clean_value(value) for value in df.get("담당자", pd.Series(dtype=str)).dropna().unique() if clean_value(value))
    lead_time_options = sorted({to_int_value(value) for value in df.get("리드타임", pd.Series(dtype=int)).dropna().unique() if to_int_value(value) > 0})

    with st.expander("검색 및 필터", expanded=True):
        row1 = st.columns([1.5, 1.0, 1.0, 1.0], gap="small")
        keyword = row1[0].text_input(
            "통합 검색",
            placeholder="바코드 / 상품명 / 업체명 / 담당자",
            key=f"product_master_{key}_keyword",
        )
        categories = row1[1].multiselect("카테고리", category_options, key=f"product_master_{key}_categories")
        suppliers = row1[2].multiselect("업체명", supplier_options, key=f"product_master_{key}_suppliers")
        managers = row1[3].multiselect("담당자", manager_options, key=f"product_master_{key}_managers")

        row2 = st.columns([1.0, 1.0, 1.0, 0.8, 0.7, 0.9], gap="small")
        lead_times = row2[0].multiselect("리드타임", lead_time_options, key=f"product_master_{key}_lead_times")
        active = row2[1].selectbox("사용상태", ["전체", "사용 중", "비활성"], key=f"product_master_{key}_active")
        sort_column = row2[2].selectbox("정렬 컬럼", THREEPL_MASTER_COLUMNS, key=f"product_master_{key}_sort_column")
        sort_order = row2[3].selectbox("정렬", ["오름차순", "내림차순"], key=f"product_master_{key}_sort_order")
        page_size = row2[4].selectbox("표시 개수", [15, 30, 50, 100], key=f"product_master_{key}_page_size")
        with row2[5]:
            st.write("")
            if st.button("전체 초기화", key=f"product_master_{key}_filter_reset", use_container_width=True):
                reset_threepl_master_filters(key)
                st.rerun()

        badges = active_threepl_master_filter_badges(
            keyword,
            categories,
            suppliers,
            managers,
            lead_times,
            active,
        )
        render_clearable_filter_badges(f"product_master_{key}", badges)

    page_key = f"product_master_{key}_page"
    if page_key not in st.session_state or st.session_state.get(f"product_master_{key}_last_page_size") != page_size:
        st.session_state[page_key] = 1
    st.session_state[f"product_master_{key}_last_page_size"] = page_size

    return {
        "keyword": keyword,
        "categories": categories,
        "suppliers": suppliers,
        "managers": managers,
        "lead_times": lead_times,
        "active": active,
        "sort_column": sort_column,
        "sort_order": sort_order,
        "page_size": int(page_size),
        "page": int(st.session_state.get(page_key, 1)),
    }


def active_threepl_master_filter_badges(keyword, categories, suppliers, managers, lead_times, active) -> list[tuple[str, str]]:
    badges: list[tuple[str, str]] = []
    if clean_value(keyword):
        badges.append(("keyword", f"검색: {keyword}"))
    for code, values, label in [
        ("categories", categories, "카테고리"),
        ("suppliers", suppliers, "업체명"),
        ("managers", managers, "담당자"),
        ("lead_times", lead_times, "리드타임"),
    ]:
        for value in values or []:
            badges.append((code, f"{label}: {value}"))
    if active != "전체":
        badges.append(("active", f"사용상태: {active}"))
    return badges


def render_clearable_filter_badges(prefix: str, badges: list[tuple[str, str]]) -> None:
    if not badges:
        return
    st.caption("적용 중인 필터")
    cols = st.columns(min(len(badges), 6), gap="small")
    for idx, (code, label) in enumerate(badges):
        with cols[idx % len(cols)]:
            if st.button(f"× {label}", key=f"{prefix}_clear_{code}_{idx}", use_container_width=True):
                clear_filter_key(prefix, code)
                st.rerun()


def clear_filter_key(prefix: str, code: str) -> None:
    key_map = {
        "keyword": "keyword",
        "categories": "categories",
        "suppliers": "suppliers",
        "managers": "managers",
        "lead_times": "lead_times",
        "active": "active",
    }
    suffix = key_map.get(code)
    if not suffix:
        return
    session_key = f"{prefix}_{suffix}"
    st.session_state[session_key] = "" if suffix == "keyword" else "전체" if suffix == "active" else []


def reset_threepl_master_filters(key: str) -> None:
    for suffix in [
        "keyword",
        "categories",
        "suppliers",
        "managers",
        "lead_times",
        "active",
        "sort_column",
        "sort_order",
        "page_size",
        "page",
        "last_page_size",
    ]:
        st.session_state.pop(f"product_master_{key}_{suffix}", None)


def apply_threepl_master_filters(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    if df.empty:
        return df
    filtered = df.copy()
    keyword = clean_value(filters.get("keyword")).lower()
    if keyword:
        search_columns = ["바코드", "상품명", "업체명", "담당자"]
        search_text = filtered[search_columns].astype(str).agg(" ".join, axis=1).str.lower()
        filtered = filtered[search_text.str.contains(re.escape(keyword), na=False)]
    if filters.get("categories"):
        filtered = filtered[filtered["카테고리"].isin(filters["categories"])]
    if filters.get("suppliers"):
        filtered = filtered[filtered["업체명"].isin(filters["suppliers"])]
    if filters.get("managers"):
        filtered = filtered[filtered["담당자"].isin(filters["managers"])]
    if filters.get("lead_times"):
        filtered = filtered[filtered["리드타임"].apply(to_int_value).isin([to_int_value(value) for value in filters["lead_times"]])]
    active = filters.get("active")
    if active in {"사용 중", "비활성"}:
        expected = "사용" if active == "사용 중" else "미사용"
        filtered = filtered[filtered["사용여부"].fillna("사용").astype(str).eq(expected)]
    return filtered.reset_index(drop=True)


def sort_threepl_master(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    if df.empty:
        return df
    sort_column = filters.get("sort_column") if filters.get("sort_column") in df.columns else "카테고리"
    ascending = filters.get("sort_order") != "내림차순"
    return df.sort_values([sort_column, "상품명", "바코드"], ascending=[ascending, True, True], kind="stable").reset_index(drop=True)


def paginate_dataframe(df: pd.DataFrame, page_size: int, page: int) -> tuple[pd.DataFrame, int, int]:
    safe_page_size = max(int(page_size or 30), 1)
    total_pages = max(ceil(len(df) / safe_page_size), 1)
    safe_page = min(max(int(page or 1), 1), total_pages)
    start = (safe_page - 1) * safe_page_size
    return df.iloc[start : start + safe_page_size].reset_index(drop=True), safe_page, total_pages


def product_master_available() -> bool:
    if init_db is None or SessionLocal is None or services is None:
        return False
    try:
        init_db()
    except Exception as exc:
        global PRODUCT_MASTER_IMPORT_ERROR
        PRODUCT_MASTER_IMPORT_ERROR = f"상품 마스터 DB 초기화 실패: {exc}"
        return False
    return True


def with_db(action):
    if SessionLocal is None:
        return None
    db = SessionLocal()
    try:
        return action(db)
    except Exception as exc:
        db.rollback()
        return {"ok": False, "message": f"처리 실패: {exc}", "count": 0}
    finally:
        db.close()


def show_sqlite_write_status(context: str) -> bool:
    if log_sqlite_writability is None:
        return True
    try:
        report = log_sqlite_writability(context)
    except Exception as exc:
        st.error(f"SQLite DB 쓰기 권한 확인 실패: {exc}")
        return False
    if not report.get("db_path"):
        return True
    file_ok = bool(report.get("db_file_writable"))
    dir_ok = bool(report.get("db_dir_writable"))
    sqlite_ok = bool(report.get("sqlite_writeable", file_ok and dir_ok))
    status = "가능" if file_ok and dir_ok and sqlite_ok else "불가"
    st.caption(
        f"DB 경로: {report.get('db_path')} / "
        f"파일 쓰기: {'가능' if file_ok else '불가'} / "
        f"폴더 쓰기: {'가능' if dir_ok else '불가'} / "
        f"SQLite 실제 쓰기: {'가능' if sqlite_ok else '불가'} / "
        f"읽기전용 URL 옵션: {'있음' if report.get('readonly_url_option') else '없음'}"
    )
    if not file_ok or not dir_ok or not sqlite_ok:
        st.error(f"SQLite DB 쓰기 상태가 {status}입니다. DB 파일과 data 폴더 권한을 확인해주세요.")
        return False
    return True


def fetch_master(source_type: str, keyword: str, active_filter: str) -> list[dict]:
    def action(db):
        return [
            services.product_master_to_dict(row)
            for row in services.list_product_master(db, source_type, keyword, active_filter)
        ]

    return with_db(action) or []


def master_column_config() -> dict:
    return {
        "미사용 처리": st.column_config.CheckboxColumn("미사용 처리", width=74, default=False),
        "SKU": st.column_config.TextColumn("SKU", width="medium"),
        "바코드": st.column_config.TextColumn("바코드", width="medium"),
        "상품명": st.column_config.TextColumn("상품명", width="large"),
        "카테고리": st.column_config.TextColumn("카테고리", width="medium"),
        "브랜드": st.column_config.TextColumn("브랜드", width="medium"),
        "공급처": st.column_config.TextColumn("공급처", width="medium"),
        "입수": st.column_config.NumberColumn("입수", min_value=0, step=1),
        "박스입수": st.column_config.NumberColumn("박스입수", min_value=0, step=1),
        "기본 리드타임": st.column_config.NumberColumn("기본 리드타임", min_value=0, step=1),
        "최소재고": st.column_config.NumberColumn("최소재고", min_value=0, step=1),
        "정렬순서": st.column_config.NumberColumn("정렬순서", min_value=0, step=1),
        "사용여부": st.column_config.SelectboxColumn("사용여부", options=["사용", "미사용"]),
    }


def threepl_master_column_config() -> dict:
    return {
        "카테고리": st.column_config.TextColumn("카테고리", width="medium"),
        "바코드": st.column_config.TextColumn("바코드", width="medium"),
        "상품명": st.column_config.TextColumn("상품명", width="large"),
        "업체명": st.column_config.TextColumn("업체명", width="medium"),
        "박스/파렛트 단위": st.column_config.TextColumn("박스/파렛트 단위", width="medium"),
        "담당자": st.column_config.TextColumn("담당자", width="medium"),
        "리드타임": st.column_config.NumberColumn("리드타임", min_value=0, step=1),
    }


def offline_master_column_config() -> dict:
    return {
        "미사용 처리": st.column_config.CheckboxColumn("미사용 처리", width=74, default=False),
        "카테고리": st.column_config.TextColumn("카테고리", width="medium"),
        "상품명": st.column_config.TextColumn("상품명", width="large"),
        "88바코드": st.column_config.TextColumn("88바코드", width="medium"),
        "리드타임": st.column_config.NumberColumn("리드타임", min_value=0, step=1),
        "정렬순서": st.column_config.NumberColumn("정렬순서", min_value=0, step=1),
        "사용여부": st.column_config.SelectboxColumn("사용여부", options=["사용", "미사용"]),
    }


def master_column_order(source_type: str) -> list[str]:
    if uses_threepl_master_form(source_type):
        return THREEPL_MASTER_COLUMNS
    return OFFLINE_MASTER_COLUMNS if uses_simple_master_form(source_type) else MASTER_COLUMNS


def master_column_config_for_source(source_type: str) -> dict:
    if uses_threepl_master_form(source_type):
        return threepl_master_column_config()
    return offline_master_column_config() if uses_simple_master_form(source_type) else master_column_config()


def master_disabled_columns(source_type: str) -> list[str]:
    return []


def editor_payload_for_source(source_type: str, edited: pd.DataFrame) -> list[dict]:
    if uses_threepl_master_form(source_type):
        return threepl_editor_to_payload(edited)
    return offline_editor_to_payload(edited) if uses_simple_master_form(source_type) else editor_to_payload(edited)


def master_to_editor(rows: list[dict]) -> pd.DataFrame:
    data = []
    for row in rows:
        data.append(
            {
                "미사용 처리": False,
                "SKU": row.get("sku", ""),
                "바코드": row.get("barcode", ""),
                "상품명": row.get("product_name", ""),
                "카테고리": row.get("large_category", ""),
                "브랜드": row.get("brand", ""),
                "공급처": row.get("supplier", ""),
                "입수": row.get("pack_qty", 0),
                "박스입수": row.get("box_qty", 0),
                "기본 리드타임": row.get("default_lead_time", 0),
                "최소재고": row.get("min_stock", 0),
                "정렬순서": row.get("sort_order", 0),
                "사용여부": row.get("is_active", "사용"),
                "비고": row.get("memo", ""),
            }
        )
    return pd.DataFrame(data, columns=MASTER_COLUMNS)


def threepl_master_to_editor(rows: list[dict]) -> pd.DataFrame:
    data = []
    for row in rows:
        data.append(
            {
                "카테고리": row.get("large_category", ""),
                "바코드": row.get("barcode", ""),
                "상품명": row.get("product_name", ""),
                "업체명": row.get("supplier", ""),
                "박스/파렛트 단위": row.get("box_pallet_unit") or format_box_pallet_unit(row.get("box_qty", 0), row.get("pack_qty", 0)),
                "담당자": row.get("memo", ""),
                "리드타임": row.get("default_lead_time", 0),
                "SKU": row.get("sku", ""),
                "브랜드": row.get("brand", ""),
                "안전재고": row.get("min_stock", 0),
                "정렬순서": row.get("sort_order", 0),
                "사용여부": row.get("is_active", "사용"),
            }
        )
    return pd.DataFrame(data, columns=[*THREEPL_MASTER_COLUMNS, *THREEPL_MASTER_INTERNAL_COLUMNS])


def offline_master_to_editor(rows: list[dict], keyword: str = "", active_filter: str = "전체") -> pd.DataFrame:
    data = []
    for row in rows:
        product_name = row.get("product_name", "")
        barcode = row.get("barcode", "")
        data.append(
            {
                "미사용 처리": False,
                "카테고리": row.get("large_category", ""),
                "상품명": product_name,
                "88바코드": barcode,
                "리드타임": row.get("default_lead_time", 0),
                "정렬순서": row.get("sort_order", 0),
                "사용여부": row.get("is_active", "사용"),
                "비고": row.get("memo", ""),
            }
        )
    return pd.DataFrame(data, columns=OFFLINE_MASTER_COLUMNS)


def offline_master_template_df() -> pd.DataFrame:
    return pd.DataFrame(columns=[column for column in OFFLINE_MASTER_COLUMNS if column != "미사용 처리"])


def threepl_master_template_df() -> pd.DataFrame:
    return pd.DataFrame(columns=THREEPL_MASTER_COLUMNS)


def offline_stock_matches_keyword(stock: dict, keyword: str) -> bool:
    needle = clean_value(keyword).lower()
    if not needle:
        return True
    haystack = " ".join(
        clean_value(stock.get(field))
        for field in ("category", "product_name", "barcode", "product_code", "stock_status")
    ).lower()
    return needle in haystack


def latest_offline_stock_lookup() -> dict[tuple[str, str], dict]:
    def action(db):
        dates = services.list_work_dates(db, "오프라인")
        if not dates:
            return {}
        latest_date = dates[0]
        return {
            (row.product_name, row.barcode or ""): services.daily_to_dict(row)
            for row in services.list_daily(db, "오프라인", latest_date)
        }

    return with_db(action) or {}


def editor_to_payload(df: pd.DataFrame) -> list[dict]:
    rows = []
    for _, row in df.fillna("").iterrows():
        if (
            not str(row.get("SKU", "")).strip()
            and not str(row.get("바코드", "")).strip()
            and not str(row.get("상품명", "")).strip()
        ):
            continue
        payload = row.to_dict()
        if bool(payload.get("미사용 처리", False)):
            payload["사용여부"] = "미사용"
        payload.pop("미사용 처리", None)
        rows.append(payload)
    return rows


def threepl_editor_to_payload(df: pd.DataFrame) -> list[dict]:
    rows = []
    for _, row in df.fillna("").iterrows():
        product_name = clean_value(row.get("상품명"))
        barcode = clean_value(row.get("바코드"))
        sku = clean_value(row.get("SKU")) or barcode or product_name
        if not product_name and not barcode and not sku:
            continue
        box_qty, pallet_qty = parse_box_pallet_unit(row.get("박스/파렛트 단위"))
        payload = {
            "SKU": sku,
            "바코드": barcode or sku,
            "상품명": product_name,
            "카테고리": clean_value(row.get("카테고리")),
            "브랜드": clean_value(row.get("브랜드")),
            "공급처": clean_value(row.get("업체명")),
            "입수": pallet_qty,
            "박스입수": box_qty,
            "기본 리드타임": to_int_value(row.get("리드타임") or row.get("기본 리드타임")),
            "최소재고": to_int_value(row.get("안전재고")),
            "정렬순서": to_int_value(row.get("정렬순서")),
            "사용여부": clean_value(row.get("사용여부")) or "사용",
            "비고": clean_value(row.get("담당자")),
        }
        if bool(row.get("미사용 처리", False)):
            payload["사용여부"] = "미사용"
        rows.append(payload)
    return rows


def offline_editor_to_payload(df: pd.DataFrame) -> list[dict]:
    rows = []
    for _, row in df.fillna("").iterrows():
        product_name = clean_value(row.get("상품명"))
        barcode = clean_value(row.get("88바코드"))
        if not product_name and not barcode:
            continue
        if not product_name:
            continue
        payload = {
            "SKU": barcode or product_name,
            "바코드": barcode or product_name,
            "상품명": product_name,
            "카테고리": clean_value(row.get("카테고리")),
            "기본 리드타임": to_int_value(row.get("리드타임")),
            "정렬순서": to_int_value(row.get("정렬순서")),
            "사용여부": clean_value(row.get("사용여부")) or "사용",
            "비고": clean_value(row.get("비고")),
        }
        if bool(row.get("미사용 처리", False)):
            payload["사용여부"] = "미사용"
        rows.append(payload)
    return rows


def clean_value(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "nat", "none"} else text


def to_int_value(value) -> int:
    text = clean_value(value).replace(",", "")
    digits = "".join(ch for ch in text if ch.isdigit() or ch in {".", "-"})
    if not digits:
        return 0
    try:
        return int(float(digits))
    except ValueError:
        return 0


def format_date_value(value) -> str:
    if value in (None, ""):
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    return str(value)


def format_lead_time(value) -> str:
    days = to_int_value(value)
    return f"{days}일" if days else ""


def expected_inbound_value(stock: dict) -> str:
    last_inbound = stock.get("last_inbound_date")
    inbound_cycle = to_int_value(stock.get("inbound_cycle"))
    if not last_inbound or not inbound_cycle:
        return ""
    try:
        expected = pd.to_datetime(last_inbound, errors="coerce") + pd.Timedelta(days=inbound_cycle)
    except (TypeError, ValueError):
        return ""
    if pd.isna(expected):
        return ""
    return expected.date().isoformat()


def master_excel(df: pd.DataFrame, title: str) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        sheet_name = title[:31]
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
        header = workbook.add_format(
            {
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": "#0B6B60",
                "border": 1,
                "align": "center",
                "valign": "vcenter",
            }
        )
        cell = workbook.add_format({"border": 1, "border_color": "#E5EFEA", "valign": "vcenter"})
        number = workbook.add_format(
            {
                "border": 1,
                "border_color": "#E5EFEA",
                "num_format": "#,##0",
                "align": "right",
                "valign": "vcenter",
            }
        )
        last_col = max(len(df.columns) - 1, 0)
        if last_col:
            worksheet.merge_range(0, 0, 0, last_col, title, title_format)
        else:
            worksheet.write(0, 0, title, title_format)
        numeric_columns = {
            "입수",
            "박스입수",
            "기본 리드타임",
            "최소재고",
            "정렬순서",
            "현재고",
            "안전재고",
            "전일 판매량",
            "가용재고",
            "입고수량",
        }
        for idx, column in enumerate(df.columns):
            worksheet.write(1, idx, column, header)
            width = min(max(len(str(column)) + 8, 12), 34)
            fmt = number if column in numeric_columns else cell
            worksheet.set_column(idx, idx, width, fmt)
        worksheet.freeze_panes(2, 0)
        if len(df.columns):
            worksheet.autofilter(1, 0, max(len(df) + 1, 1), last_col)
    return output.getvalue()


def threepl_master_excel(df: pd.DataFrame) -> bytes:
    export_df = df[THREEPL_MASTER_COLUMNS] if not df.empty else pd.DataFrame(columns=THREEPL_MASTER_COLUMNS)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        sheet_name = "3PL 마스터"
        export_df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=1)
        workbook = writer.book
        worksheet = writer.sheets[sheet_name]
        title_format = workbook.add_format(
            {
                "bold": True,
                "font_size": 14,
                "font_color": "#FFFFFF",
                "bg_color": "#07544B",
                "align": "center",
                "valign": "vcenter",
                "border": 1,
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
        cell_format = workbook.add_format({"border": 1, "border_color": "#C9D7D1", "valign": "vcenter"})
        number_format = workbook.add_format(
            {
                "border": 1,
                "border_color": "#C9D7D1",
                "num_format": "#,##0",
                "align": "right",
                "valign": "vcenter",
            }
        )
        last_col = len(THREEPL_MASTER_COLUMNS) - 1
        worksheet.merge_range(0, 0, 0, last_col, "3PL 마스터", title_format)
        worksheet.set_row(0, 24)
        worksheet.set_row(1, 21)
        numeric_columns = {"리드타임"}
        widths = {
            "카테고리": 16,
            "바코드": 18,
            "상품명": 34,
            "업체명": 20,
            "박스/파렛트 단위": 28,
            "담당자": 16,
            "리드타임": 12,
        }
        for idx, column in enumerate(THREEPL_MASTER_COLUMNS):
            worksheet.write(1, idx, column, header_format)
            worksheet.set_column(idx, idx, widths.get(column, 16), number_format if column in numeric_columns else cell_format)
        worksheet.freeze_panes(2, 0)
        worksheet.autofilter(1, 0, max(len(export_df) + 1, 1), last_col)
    return output.getvalue()


def show_result(result) -> None:
    if not result:
        return
    if result.get("ok", True):
        st.success(f'{result.get("message", "처리 완료")} ({result.get("count", 0)}건)')
        st.rerun()
    else:
        st.warning(result.get("message", "처리하지 못했습니다."))


def inject_product_master_css() -> None:
    st.markdown(
        """
        <style>
        .product-master-title {
            color: #172033;
            font-size: 1.32rem;
            font-weight: 950;
            margin: 0.15rem 0 0.85rem;
        }
        .product-master-subtitle {
            color: #26384A;
            font-size: 1.08rem;
            font-weight: 900;
            margin: 0.35rem 0 0.75rem;
        }
        .product-master-control-title,
        .product-master-form-title {
            color: #26384A;
            font-size: 1.02rem;
            font-weight: 900;
            margin: 0.1rem 0 0.45rem;
        }
        div[class*="st-key-product_master_"][class*="_controls"] {
            background: #EEF3F7;
            border: 1px solid #C9D5DF;
            border-radius: 8px;
            margin: 0.2rem 0 0.75rem;
            padding: 0.82rem;
        }
        div[class*="st-key-product_master_"][class*="_editor_panel"] {
            background: #F1F4F2;
            border: 1px solid #CDD7D1;
            border-radius: 8px;
            padding: 0.85rem;
        }
        div[class*="st-key-product_master_"][class*="_controls"] [data-testid="stFileUploaderDropzone"] {
            min-height: 42px !important;
            padding: 0.36rem 0.55rem !important;
        }
        div[class*="st-key-product_master_"][class*="_controls"] [data-testid="stFileUploaderDropzone"] > div {
            padding: 0 !important;
        }
        div[class*="st-key-product_master_"][class*="_controls"] [data-testid="stFileUploaderDropzone"] small {
            display: none !important;
        }
        div[class*="st-key-product_master_"][class*="_editor_panel"] [data-testid="stExpander"] {
            border-color: #C9D5DF;
            margin: 0.25rem 0 0.75rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
