from __future__ import annotations

from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

try:
    from backend import services
    from backend.database import SessionLocal, init_db
    from backend.models import InventoryDaily, InventoryInbound, PurchaseDocument, PurchaseOrder, PurchaseRequest, RfqQuote, Supplier
except (ModuleNotFoundError, RuntimeError) as exc:
    SessionLocal = None
    init_db = None
    services = None
    InventoryInbound = None
    InventoryDaily = None
    PurchaseDocument = None
    PurchaseOrder = None
    PurchaseRequest = None
    RfqQuote = None
    Supplier = None
    PURCHASE_IMPORT_ERROR = str(exc)
else:
    PURCHASE_IMPORT_ERROR = ""


PR_STATUS = ["작성", "상신", "승인", "반려"]
PO_PROGRESS = ["발주대기", "발주완료", "입고진행", "종결", "취소"]
PO_INBOUND = ["입고대기", "부분입고", "입고완료"]
CURRENCIES = ["KRW", "USD"]
PRICE_DECIMAL_OPTIONS = [0, 1, 2, 3, 4, 5]
PRICE_DECIMAL_COLUMNS = {"단가", "공급가액", "부가세", "총금액", "배송비", "발주금액", "총 구매비용", "구매금액"}
COMPANY_NAME = "SCM 물류운영포털"
DEFAULT_DELIVERY_PLACE = "로긴 물류센터"
PDF_MIME = "application/pdf"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MALGUN_FONT = "Malgun"
MALGUN_BOLD_FONT = "Malgun-Bold"


def render_purchase_page() -> None:
    inject_purchase_css()
    st.markdown('<div class="purchase-title">구매관리</div>', unsafe_allow_html=True)
    st.caption("PR → RFQ → PO → 입고 → 재고반영 흐름으로 연결되는 ERP형 구매 업무 화면입니다.")
    setting_cols = st.columns([0.85, 5.15], gap="small")
    setting_cols[0].selectbox(
        "단가 소수점",
        PRICE_DECIMAL_OPTIONS,
        index=PRICE_DECIMAL_OPTIONS.index(selected_price_decimal_places()),
        key="purchase_price_decimal_places",
    )

    if not purchase_available():
        st.error(PURCHASE_IMPORT_ERROR or "구매관리 DB를 초기화하지 못했습니다.")
        return

    pr_tab, rfq_tab, po_tab, supplier_tab, price_tab, kpi_tab, doc_tab = st.tabs(
        ["구매요청(PR)", "견적관리(RFQ)", "발주관리(PO)", "협력사관리", "단가이력", "구매 KPI", "문서/다운로드"]
    )
    with pr_tab:
        render_pr_tab()
    with rfq_tab:
        render_rfq_tab()
    with po_tab:
        render_po_tab()
    with supplier_tab:
        render_supplier_tab()
    with price_tab:
        render_price_history_tab()
    with kpi_tab:
        render_kpi_tab()
    with doc_tab:
        render_document_tab()


def purchase_available() -> bool:
    if init_db is None or SessionLocal is None:
        return False
    try:
        init_db()
    except Exception as exc:
        global PURCHASE_IMPORT_ERROR
        PURCHASE_IMPORT_ERROR = f"구매관리 DB 초기화 실패: {exc}"
        return False
    return True


def with_db(action):
    if SessionLocal is None:
        st.error(PURCHASE_IMPORT_ERROR or "DB 세션을 만들 수 없습니다.")
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


def render_pr_tab() -> None:
    st.markdown('<div class="purchase-section-title">구매요청 등록</div>', unsafe_allow_html=True)
    with st.form("purchase_pr_form", clear_on_submit=True):
        cols = st.columns([1.0, 0.9, 1.35, 1.0, 0.65, 0.55, 0.9, 0.9, 0.75], gap="small")
        department = cols[0].text_input("요청부서", placeholder="예: 생산팀")
        item_code = cols[1].text_input("품목코드", placeholder="SKU")
        item_name = cols[2].text_input("품목", placeholder="구매 요청 품목")
        spec = cols[3].text_input("규격", placeholder="규격/사양")
        quantity = cols[4].number_input("수량", min_value=1, step=1, value=1)
        unit = cols[5].text_input("단위", value="EA")
        request_date = cols[6].date_input("요청일", value=date.today())
        requester = cols[7].text_input("요청자", placeholder="담당자")
        approval_status = cols[8].selectbox("승인상태", PR_STATUS, index=0)
        doc_cols = st.columns([0.9, 0.9, 1.25, 1.25, 1.4], gap="small")
        reply_due_date = doc_cols[0].date_input("견적 회신 요청일", value=date.today() + timedelta(days=3))
        desired_due_date = doc_cols[1].date_input("희망납기일", value=date.today() + timedelta(days=14))
        delivery_place = doc_cols[2].text_input("납품장소", value=DEFAULT_DELIVERY_PLACE)
        approver = doc_cols[3].text_input("승인자", placeholder="승인 담당자")
        request_notes = doc_cols[4].text_input("요청사항", placeholder="포장/시험성적서/배송 조건 등")
        memo = st.text_input("비고", placeholder="요청 사유 또는 특이사항")
        if st.form_submit_button("구매요청 저장", type="primary", use_container_width=True):
            if not item_name.strip():
                st.warning("품목을 입력하세요.")
            else:
                result = with_db(
                    lambda db: create_purchase_request(
                        db,
                        department=department,
                        item_code=item_code,
                        item_name=item_name,
                        spec=spec,
                        quantity=int(quantity),
                        unit=unit,
                        request_date=request_date,
                        reply_due_date=reply_due_date,
                        desired_due_date=desired_due_date,
                        delivery_place=delivery_place,
                        request_notes=request_notes,
                        requester=requester,
                        approver=approver,
                        approval_status=approval_status,
                        source_type="수기",
                        memo=memo,
                    )
                )
                if result:
                    st.success(f"{result.pr_number} 구매요청을 저장했습니다.")
                    st.rerun()

    rows = with_db(lambda db: [pr_to_dict(row) for row in list_purchase_requests(db)]) or []
    if not rows:
        st.info("등록된 구매요청이 없습니다. 재고관리 MRP/발주추천에서도 PR을 생성할 수 있습니다.")
        return

    st.markdown('<div class="purchase-section-title">구매요청 목록</div>', unsafe_allow_html=True)
    df = pd.DataFrame(rows)
    df.insert(0, "선택", False)
    edited = st.data_editor(
        df,
        hide_index=True,
        use_container_width=True,
        column_order=[
            "선택",
            "구매요청번호",
            "요청부서",
            "품목코드",
            "품목",
            "규격",
            "수량",
            "단위",
            "요청일",
            "견적회신요청일",
            "희망납기일",
            "납품장소",
            "요청자",
            "승인자",
            "승인상태",
            "발주번호",
            "비고",
        ],
        column_config={
            "선택": st.column_config.CheckboxColumn("선택", default=False),
            "승인상태": st.column_config.SelectboxColumn("승인상태", options=PR_STATUS),
        },
        disabled=["구매요청번호", "발주번호"],
        key="purchase_pr_editor",
    )
    action_cols = st.columns([0.95, 0.95, 4.5], gap="small")
    with action_cols[0]:
        if st.button("선택 승인", type="primary", use_container_width=True, key="purchase_pr_approve"):
            count = with_db(lambda db: approve_selected_pr(db, selected_numbers(edited, "구매요청번호")))
            if count:
                st.success(f"승인 완료: {count}건")
                st.rerun()
    with action_cols[1]:
        if st.button("상태 저장", use_container_width=True, key="purchase_pr_save"):
            count = with_db(lambda db: save_pr_editor(db, edited))
            st.success(f"구매요청 변경사항 저장 완료: {count or 0}건")
            st.rerun()
    with action_cols[2]:
        st.caption("승인된 PR은 RFQ 탭에서 견적 비교 후 PO 생성이 가능합니다.")


def render_rfq_tab() -> None:
    approved_prs = with_db(lambda db: list_approved_pr_without_po(db)) or []
    supplier_rows = with_db(lambda db: [supplier_to_dict(row) for row in list_suppliers(db)]) or []
    supplier_by_name = {clean_text(row.get("업체명")): row for row in supplier_rows if clean_text(row.get("업체명"))}
    supplier_options = ["직접 입력"] + list(supplier_by_name)
    pr_options = [row.pr_number for row in approved_prs]
    pr_map = {row.pr_number: row for row in approved_prs}

    st.markdown('<div class="purchase-section-title">견적 요청 대상 PR</div>', unsafe_allow_html=True)
    if not pr_options:
        st.info("발주 전환 가능한 승인 PR이 없습니다.")
    else:
        selected_pr_number = st.selectbox(
            "승인 PR",
            options=pr_options,
            format_func=lambda number: f"{number} / {pr_map[number].item_name} / {pr_map[number].quantity:,}개",
            key="purchase_rfq_pr_select",
        )
        pr = pr_map[selected_pr_number]
        st.caption(f"품목: {pr.item_name} / 규격: {pr.spec or '-'} / 요청수량: {pr.quantity:,}개")
        selected_registered_supplier = st.selectbox(
            "등록 협력사 불러오기",
            supplier_options,
            key="purchase_rfq_registered_supplier",
        )
        supplier_defaults = supplier_by_name.get(selected_registered_supplier, {})
        default_price, default_currency = parse_compact_price(supplier_defaults.get("평균단가"))
        with st.form("purchase_rfq_form", clear_on_submit=True):
            cols = st.columns([1.1, 0.82, 0.9, 1.05, 0.72, 0.62, 0.56, 0.56, 0.72], gap="small")
            supplier_name = cols[0].text_input("업체명", value=str(supplier_defaults.get("업체명", "")), placeholder="협력사명")
            supplier_manager = cols[1].text_input("담당자", value=str(supplier_defaults.get("담당자", "")))
            supplier_phone = cols[2].text_input("연락처", value=str(supplier_defaults.get("연락처", "")))
            supplier_email = cols[3].text_input("이메일", value=str(supplier_defaults.get("이메일", "")))
            unit_price = cols[4].number_input("단가", min_value=0.0, step=price_step(), value=default_price, format=price_input_format())
            currency = cols[5].selectbox("통화", CURRENCIES, index=currency_index(default_currency), format_func=currency_label)
            moq = cols[6].number_input("MOQ", min_value=0, step=1, value=parse_moq_quantity(supplier_defaults.get("MOQ 조건")))
            lead_time_days = cols[7].number_input("납기", min_value=0, step=1, value=to_int(supplier_defaults.get("평균납기")))
            shipping_fee = cols[8].number_input("배송비", min_value=0, step=100, value=0)
            doc_cols = st.columns([1.2, 0.9, 2.2], gap="small")
            payment_terms = doc_cols[0].text_input("결제조건", value=str(supplier_defaults.get("결제조건", "")), placeholder="예: 월말 정산")
            quote_valid_until = doc_cols[1].date_input("견적 유효기간", value=date.today() + timedelta(days=30))
            memo = doc_cols[2].text_input("품질/거래조건 메모", placeholder="조건/특이사항")
            if st.form_submit_button("견적 저장", type="primary", use_container_width=True):
                if not supplier_name.strip():
                    st.warning("업체명을 입력하세요.")
                else:
                    with_db(
                        lambda db: create_quote(
                            db,
                            pr,
                            supplier_name=supplier_name,
                            supplier_manager=supplier_manager,
                            supplier_phone=supplier_phone,
                            supplier_email=supplier_email,
                            unit_price=to_float(unit_price),
                            currency=currency,
                            moq=int(moq),
                            lead_time_days=int(lead_time_days),
                            shipping_fee=int(shipping_fee),
                            payment_terms=payment_terms,
                            quote_valid_until=quote_valid_until,
                            memo=memo,
                        )
                    )
                    st.success("견적을 저장하고 추천업체를 갱신했습니다.")
                    st.rerun()

    quotes = with_db(lambda db: quote_comparison_rows(db)) or []
    st.markdown('<div class="purchase-section-title">업체별 견적 비교</div>', unsafe_allow_html=True)
    if not quotes:
        st.info("저장된 견적이 없습니다.")
        return
    quote_df = pd.DataFrame(quotes)
    quote_df.insert(0, "삭제", False)
    quote_df.insert(0, "선정", quote_df["선정 여부"].eq("선정") if "선정 여부" in quote_df.columns else False)
    edited_quotes = st.data_editor(
        quote_df,
        hide_index=True,
        use_container_width=True,
        height=330,
        column_config={
            "선정": st.column_config.CheckboxColumn("선정", default=False),
            "삭제": st.column_config.CheckboxColumn("삭제", default=False),
            "단가": st.column_config.NumberColumn("단가", min_value=0.0, step=price_step(), format=price_input_format()),
            "통화": st.column_config.SelectboxColumn("통화", options=CURRENCIES),
            "MOQ": st.column_config.NumberColumn("MOQ", min_value=0, step=1),
            "납기": st.column_config.NumberColumn("납기", min_value=0, step=1),
            "배송비": st.column_config.NumberColumn("배송비", min_value=0, step=1, format=price_input_format()),
            "공급가액": st.column_config.NumberColumn("공급가액", min_value=0.0, format=price_input_format()),
            "부가세": st.column_config.NumberColumn("부가세", min_value=0.0, format=price_input_format()),
            "총금액": st.column_config.NumberColumn("총금액", min_value=0.0, format=price_input_format()),
            "총 구매비용": st.column_config.NumberColumn("총 구매비용", min_value=0.0, format=price_input_format()),
            "견적 유효기간": st.column_config.DateColumn("견적 유효기간"),
            "선정 사유": st.column_config.TextColumn("선정 사유", width="large"),
            "품질/거래조건 메모": st.column_config.TextColumn("품질/거래조건 메모", width="large"),
        },
        disabled=[
            column
            for column in quote_df.columns
            if column
            not in {
                "선정",
                "삭제",
                "업체명",
                "단가",
                "통화",
                "MOQ",
                "납기",
                "배송비",
                "결제조건",
                "견적 유효기간",
                "선정 사유",
                "품질/거래조건 메모",
            }
        ],
        key="purchase_rfq_compare_editor",
    )
    select_cols = st.columns([1.0, 1.2, 1.1, 3.4], gap="small")
    with select_cols[0]:
        if st.button("견적 변경 저장", type="primary", use_container_width=True, key="purchase_quote_save"):
            count = with_db(lambda db: save_quote_editor(db, edited_quotes))
            st.success(f"견적 변경사항 저장 완료: {count or 0}건")
            st.rerun()
    rfq_doc_options = sorted({row["구매요청번호"] for row in quotes})
    with select_cols[1]:
        selected_doc_pr = st.selectbox("문서 대상 PR", rfq_doc_options, key="purchase_rfq_doc_pr")
    supplier_options = [row["업체명"] for row in quotes if row["구매요청번호"] == selected_doc_pr]
    with select_cols[2]:
        selected_doc_supplier = st.selectbox("업체", supplier_options, key="purchase_rfq_doc_supplier")
    with select_cols[3]:
        doc_creator = st.text_input("문서 작성자", value="구매담당", key="purchase_rfq_doc_creator")

    doc_cols = st.columns([0.9, 1.0, 0.9, 1.0, 2.4], gap="small")
    with doc_cols[0]:
        if st.button("RFQ PDF 생성", type="primary", use_container_width=True, key="rfq_pdf_generate"):
            generated = with_db(lambda db: generate_rfq_pdf_document(db, selected_doc_pr, selected_doc_supplier, doc_creator))
            if generated:
                st.session_state["purchase_last_rfq_pdf"] = generated
                st.success(f"{generated['file_name']} 생성 완료")
    with doc_cols[1]:
        rfq_pdf = st.session_state.get("purchase_last_rfq_pdf")
        if rfq_pdf and rfq_pdf.get("pr_number") == selected_doc_pr and rfq_pdf.get("supplier_name") == selected_doc_supplier:
            st.download_button(
                "RFQ PDF 다운로드",
                data=rfq_pdf["bytes"],
                file_name=rfq_pdf["file_name"],
                mime=PDF_MIME,
                use_container_width=True,
                key=f"rfq_pdf_download_{selected_doc_pr}_{selected_doc_supplier}",
            )
    with doc_cols[2]:
        if st.button("비교표 PDF 생성", type="primary", use_container_width=True, key="comparison_pdf_generate"):
            generated = with_db(lambda db: generate_comparison_pdf_document(db, selected_doc_pr, doc_creator))
            if generated:
                st.session_state["purchase_last_comparison_pdf"] = generated
                st.success(f"{generated['file_name']} 생성 완료")
    with doc_cols[3]:
        comparison_pdf = st.session_state.get("purchase_last_comparison_pdf")
        if comparison_pdf and comparison_pdf.get("pr_number") == selected_doc_pr:
            st.download_button(
                "비교표 PDF 다운로드",
                data=comparison_pdf["bytes"],
                file_name=comparison_pdf["file_name"],
                mime=PDF_MIME,
                use_container_width=True,
                key=f"comparison_pdf_download_{selected_doc_pr}",
            )
    with doc_cols[4]:
        st.caption("PDF 다운로드 시 문서 이력이 버전별로 저장되어 문서/다운로드 탭에서 재다운로드할 수 있습니다.")

    eligible = [row for row in quotes if row.get("추천") == "추천" and row.get("발주번호", "") == ""]
    if eligible:
        po_pr_options = sorted({row["구매요청번호"] for row in eligible})
        cols = st.columns([1.2, 1.0, 4.0], gap="small")
        selected_po_pr = cols[0].selectbox("PO 생성 PR", po_pr_options, key="purchase_rfq_po_pr")
        with cols[1]:
            st.write("")
            if st.button("선정/추천견적 PO 생성", type="primary", use_container_width=True):
                created = with_db(lambda db: create_po_from_selected_quote(db, selected_po_pr))
                if created:
                    st.success(f"{created.po_number} 발주를 생성했습니다.")
                    st.rerun()
        with cols[2]:
            st.caption("동일 PR의 견적 중 총 구매비용이 가장 낮은 업체가 추천업체로 표시됩니다.")


def render_po_tab() -> None:
    rows = with_db(lambda db: [po_to_dict(row) for row in list_purchase_orders(db)]) or []
    if not rows:
        st.info("생성된 발주가 없습니다. RFQ 탭에서 승인 PR의 추천 견적으로 PO를 생성하세요.")
        return
    supplier_names = with_db(lambda db: [row.supplier_name for row in list_suppliers(db) if row.supplier_name]) or []
    supplier_options = sorted({clean_text(row.get("업체")) for row in rows if clean_text(row.get("업체"))} | set(supplier_names))

    summary_cols = st.columns(4, gap="small")
    total_amounts = amount_totals_by_currency(rows, "발주금액", "통화")
    waiting = sum(1 for row in rows if row.get("입고상태") != "입고완료")
    delayed = sum(1 for row in rows if row.get("납기예정일") and pd.to_datetime(row["납기예정일"]).date() < date.today() and row.get("입고상태") != "입고완료")
    summary_cols[0].metric("발주건수", f"{len(rows):,}")
    summary_cols[1].metric("발주금액", format_currency_totals(total_amounts))
    summary_cols[2].metric("입고대기", f"{waiting:,}건")
    summary_cols[3].metric("지연건수", f"{delayed:,}건")

    df = pd.DataFrame(rows)
    df.insert(0, "삭제", False)
    df.insert(0, "입고완료처리", False)
    edited = st.data_editor(
        df,
        hide_index=True,
        use_container_width=True,
        column_order=[
            "입고완료처리",
            "삭제",
            "발주번호",
            "구매요청번호",
            "업체",
            "품목",
            "규격",
            "수량",
            "단가",
            "통화",
            "발주일",
            "납기예정일",
            "입고상태",
            "진행상태",
            "발주금액",
        ],
        column_config={
            "입고완료처리": st.column_config.CheckboxColumn("입고완료처리", default=False),
            "삭제": st.column_config.CheckboxColumn("삭제", default=False),
            "업체": st.column_config.SelectboxColumn("업체", options=supplier_options),
            "단가": st.column_config.NumberColumn("단가", min_value=0.0, step=price_step(), format=price_input_format()),
            "통화": st.column_config.SelectboxColumn("통화", options=CURRENCIES),
            "발주금액": st.column_config.NumberColumn("발주금액", min_value=0.0, format=price_input_format()),
        },
        disabled=["발주번호", "구매요청번호", "발주금액"],
        key="purchase_po_editor",
    )
    cols = st.columns([1.0, 1.0, 1.0, 3.2], gap="small")
    with cols[0]:
        if st.button("PO 상태 저장", use_container_width=True, key="purchase_po_save"):
            count = with_db(lambda db: save_po_editor(db, edited))
            st.success(f"PO 변경사항 저장 완료: {count or 0}건")
            st.rerun()
    with cols[1]:
        if st.button("선택 입고 완료", type="primary", use_container_width=True, key="purchase_po_receive"):
            count = with_db(lambda db: receive_selected_po(db, selected_numbers(edited, "발주번호")))
            if count:
                st.success(f"입고 완료 및 창고 현재고 반영: {count}건")
                st.rerun()
    with cols[2]:
        if st.button("선택 삭제", use_container_width=True, key="purchase_po_delete"):
            count = with_db(lambda db: delete_selected_pos(db, selected_by_flag(edited, "발주번호", "삭제")))
            if count:
                st.success(f"발주 삭제 완료: {count}건")
                st.rerun()
    with cols[3]:
        st.caption("입고 완료 처리 시 창고 입고내역이 생성되고 같은 기준일자의 현재고/가용재고에 자동 반영됩니다.")


def render_supplier_tab() -> None:
    rows = with_db(lambda db: [supplier_to_dict(row) for row in list_suppliers(db)]) or []
    supplier_by_name = {str(row.get("업체명", "")).strip(): row for row in rows if str(row.get("업체명", "")).strip()}
    search_keyword = clean_text(st.text_input("협력사 검색", placeholder="업체명, 취급품목, MOQ, 담당자, 연락처, 이메일로 검색"))
    filtered_rows = filter_supplier_rows(rows, search_keyword)
    filtered_supplier_names = [
        str(row.get("업체명", "")).strip()
        for row in filtered_rows
        if str(row.get("업체명", "")).strip()
    ]
    st.caption("RFQ 등록 업체는 자동으로 협력사에 추가되며, 평균납기/평균단가는 발주 이력 기준으로 갱신됩니다.")

    st.markdown('<div class="purchase-section-title">협력사 등록/수정</div>', unsafe_allow_html=True)
    edit_options = ["신규 협력사 등록"] + filtered_supplier_names
    edit_target = st.selectbox("수정할 협력사", edit_options, key="purchase_supplier_edit_select")
    selected_supplier_name = "" if edit_target == "신규 협력사 등록" else edit_target
    selected_supplier = supplier_by_name.get(selected_supplier_name, {})
    form_key_suffix = selected_supplier_name or "new"
    with st.form("purchase_supplier_form", clear_on_submit=True):
        cols = st.columns([1.2, 0.9, 1.0, 1.2], gap="small")
        supplier_name = cols[0].text_input(
            "업체명",
            value=str(selected_supplier.get("업체명", "")),
            placeholder="협력사명",
            key=f"purchase_supplier_name_{form_key_suffix}",
        )
        manager = cols[1].text_input(
            "담당자",
            value=str(selected_supplier.get("담당자", "")),
            key=f"purchase_supplier_manager_{form_key_suffix}",
        )
        phone = cols[2].text_input(
            "연락처",
            value=str(selected_supplier.get("연락처", "")),
            key=f"purchase_supplier_phone_{form_key_suffix}",
        )
        email = cols[3].text_input(
            "이메일",
            value=str(selected_supplier.get("이메일", "")),
            key=f"purchase_supplier_email_{form_key_suffix}",
        )
        detail_cols = st.columns([0.7, 1.0, 0.62, 2.0], gap="small")
        avg_lead_time = detail_cols[0].number_input(
            "평균납기",
            min_value=0,
            step=1,
            value=to_int(selected_supplier.get("평균납기")),
            key=f"purchase_supplier_lead_{form_key_suffix}",
        )
        avg_unit_price_text = detail_cols[1].text_input(
            "평균단가",
            value=str(selected_supplier.get("평균단가", "")),
            placeholder="예: 351W 또는 351$",
            key=f"purchase_supplier_price_{form_key_suffix}",
        )
        payment_terms = detail_cols[2].text_input(
            "결제조건",
            value=str(selected_supplier.get("결제조건", "")),
            placeholder="예: 월말정산",
            key=f"purchase_supplier_payment_{form_key_suffix}",
        )
        handled_items = detail_cols[3].text_input(
            "취급품목",
            value=str(selected_supplier.get("취급품목", "")),
            placeholder="예: 포장재, 사출품, 전장부품",
            key=f"purchase_supplier_items_{form_key_suffix}",
        )
        extra_cols = st.columns([1.2, 1.8], gap="small")
        moq_terms = extra_cols[0].text_input(
            "MOQ 조건",
            value=str(selected_supplier.get("MOQ 조건", "")),
            placeholder="예: 500개 / 1박스 / 품목별 상이",
            key=f"purchase_supplier_moq_{form_key_suffix}",
        )
        memo = extra_cols[1].text_input(
            "비고",
            value=str(selected_supplier.get("비고", "")),
            key=f"purchase_supplier_memo_{form_key_suffix}",
        )
        if st.form_submit_button("협력사 등록/수정", type="primary", use_container_width=True):
            if not supplier_name.strip():
                st.warning("업체명을 입력하세요.")
            else:
                parsed_avg_price, parsed_avg_currency = parse_compact_price(avg_unit_price_text)
                result = with_db(
                    lambda db: upsert_supplier(
                        db,
                        supplier_name=supplier_name,
                        original_supplier_name=selected_supplier_name,
                        manager=manager,
                        phone=phone,
                        email=email,
                        handled_items=handled_items,
                        moq_terms=moq_terms,
                        avg_lead_time_days=int(avg_lead_time or 0),
                        avg_unit_price=parsed_avg_price,
                        avg_unit_price_currency=parsed_avg_currency,
                        payment_terms=payment_terms,
                        memo=memo,
                    )
                )
                if result:
                    st.success(f"{supplier_name} 협력사를 저장했습니다.")
                    st.rerun()

    st.markdown('<div class="purchase-section-title">협력사 목록</div>', unsafe_allow_html=True)
    if not rows:
        st.info("등록된 협력사가 없습니다.")
        return
    if not filtered_rows:
        st.info("검색 조건에 맞는 협력사가 없습니다.")
        return

    df = pd.DataFrame(filtered_rows).drop(columns=["삭제"], errors="ignore")
    st.dataframe(center_aligned_dataframe(df), hide_index=True, use_container_width=True, height=330)
    if not filtered_supplier_names:
        return
    delete_cols = st.columns([1.2, 1.0, 3.0], gap="small")
    selected_supplier = delete_cols[0].selectbox("삭제할 협력사", filtered_supplier_names, key="purchase_supplier_delete_select")
    with delete_cols[1]:
        st.write("")
        if st.button("선택 삭제", use_container_width=True, key="purchase_supplier_delete"):
            with_db(lambda db: delete_supplier(db, selected_supplier))
            st.success(f"{selected_supplier} 협력사를 삭제했습니다.")
            st.rerun()
    with delete_cols[2]:
        st.caption("목록에서 수정할 업체를 선택하면 위 등록/수정 폼에 기존 정보가 자동으로 채워집니다.")


def render_price_history_tab() -> None:
    item_options = with_db(lambda db: list_price_history_items(db)) or []
    if not item_options:
        st.info("발주 이력이 없어 단가이력을 표시할 수 없습니다.")
        return
    selected_item = st.selectbox("품목", item_options, key="purchase_price_item")
    rows = with_db(lambda db: price_history_rows(db, selected_item)) or []
    df = pd.DataFrame(rows)
    if df.empty:
        st.info("선택 품목의 단가 이력이 없습니다.")
        return
    chart_df = df.copy()
    chart_df["날짜"] = pd.to_datetime(chart_df["날짜"], errors="coerce")
    st.line_chart(chart_df.dropna(subset=["날짜"]).set_index("날짜")["단가"])
    st.dataframe(center_aligned_dataframe(df), hide_index=True, use_container_width=True)


def center_aligned_dataframe(df: pd.DataFrame):
    formatters = {
        column: format_decimal_display
        for column in df.columns
        if column in PRICE_DECIMAL_COLUMNS
    }
    return df.style.format(formatters, na_rep="").set_properties(**{"text-align": "center"}).set_table_styles(
        [{"selector": "th", "props": [("text-align", "center")]}]
    )


def format_price_columns_for_display(df: pd.DataFrame) -> pd.DataFrame:
    display_df = df.copy()
    for column in display_df.columns:
        if column in PRICE_DECIMAL_COLUMNS:
            display_df[column] = display_df[column].map(format_decimal_display)
    return display_df


def filter_supplier_rows(rows: list[dict], keyword: str) -> list[dict]:
    keyword = clean_text(keyword).lower()
    if not keyword:
        return rows
    search_columns = ["업체명", "취급품목", "MOQ 조건", "결제조건", "담당자", "연락처", "이메일", "비고"]
    return [
        row
        for row in rows
        if any(keyword in clean_text(row.get(column)).lower() for column in search_columns)
    ]


def render_kpi_tab() -> None:
    payload = with_db(lambda db: purchase_kpi(db)) or {}
    cols = st.columns(6, gap="small")
    metrics = [
        ("총 구매금액", format_currency_totals(payload.get("total_amounts_by_currency", {}))),
        ("발주건수", f"{int(payload.get('po_count', 0)):,}건"),
        ("평균납기", f"{payload.get('avg_lead_time', 0):.1f}일"),
        ("납기준수율", f"{payload.get('on_time_rate', 0):.1f}%"),
        ("단가절감률", f"{payload.get('saving_rate', 0):.1f}%"),
        ("지연건수", f"{int(payload.get('delayed_count', 0)):,}건"),
    ]
    for col, (label, value) in zip(cols, metrics):
        col.metric(label, value)

    monthly = pd.DataFrame(payload.get("monthly", []))
    supplier = pd.DataFrame(payload.get("supplier", []))
    chart_cols = st.columns(2, gap="small")
    with chart_cols[0]:
        st.markdown("#### 월별 구매금액")
        if monthly.empty:
            st.info("표시할 데이터가 없습니다.")
        else:
            st.bar_chart(monthly.set_index("월")["구매금액"])
    with chart_cols[1]:
        st.markdown("#### 협력사별 구매금액")
        if supplier.empty:
            st.info("표시할 데이터가 없습니다.")
        else:
            st.bar_chart(supplier.set_index("업체")["구매금액"])


def render_document_tab() -> None:
    st.markdown('<div class="purchase-section-title">Excel 양식 다운로드</div>', unsafe_allow_html=True)
    template_cols = st.columns(4, gap="small")
    templates = [
        ("업체 견적 회신용 빈 양식", quote_reply_template_excel(), "업체_견적_회신용_양식.xlsx"),
        ("견적 비교표", quote_comparison_template_excel(), "견적_비교표_양식.xlsx"),
        ("구매 품목 일괄등록 양식", purchase_item_template_excel(), "구매_품목_일괄등록_양식.xlsx"),
        ("발주 일괄등록 양식", po_bulk_template_excel(), "발주_일괄등록_양식.xlsx"),
    ]
    for index, (column, (label, data, file_name)) in enumerate(zip(template_cols, templates)):
        if column.button(f"{label} 생성", use_container_width=True, key=f"template_generate_{index}"):
            generated = with_db(
                lambda db, label=label, data=data, file_name=file_name: generate_excel_document(
                    db,
                    document_type="Excel 양식",
                    document_number=f"TPL-{safe_filename(label)}",
                    creator="구매담당",
                    file_name=file_name,
                    file_bytes=data,
                )
            )
            if generated:
                st.session_state[f"purchase_template_{index}"] = generated
        generated = st.session_state.get(f"purchase_template_{index}")
        if generated:
            column.download_button(label, data=generated["bytes"], file_name=generated["file_name"], mime=XLSX_MIME, use_container_width=True, key=f"template_download_{index}")

    st.markdown('<div class="purchase-section-title">현재 데이터 Excel 내보내기</div>', unsafe_allow_html=True)
    export_cols = st.columns([0.9, 0.9, 1.0, 1.0, 1.0, 1.5], gap="small")
    export_target = export_cols[0].selectbox(
        "내보내기 대상",
        ["견적비교", "발주내역", "단가이력", "MRP 발주추천"],
        key="purchase_export_target",
    )
    export_range = export_cols[1].selectbox(
        "범위",
        ["전체 데이터", "현재 검색 결과", "지정한 기간", "지정한 공급업체", "지정한 품목", "선택한 행"],
        key="purchase_export_range",
    )
    start_date = export_cols[2].date_input("시작일", value=date.today() - timedelta(days=30), key="purchase_export_start")
    end_date = export_cols[3].date_input("종료일", value=date.today(), key="purchase_export_end")
    supplier_filter = export_cols[4].text_input("공급업체", key="purchase_export_supplier")
    item_filter = export_cols[5].text_input("품목", key="purchase_export_item")
    export_df = with_db(lambda db: purchase_export_dataframe(db, export_target, export_range, start_date, end_date, supplier_filter, item_filter))
    if export_df is None:
        export_df = pd.DataFrame()
    st.dataframe(export_df, hide_index=True, use_container_width=True, height=260)
    export_cols = st.columns([1.0, 1.0, 3.8], gap="small")
    with export_cols[0]:
        if st.button("Excel 생성", type="primary", use_container_width=True, key="purchase_export_generate"):
            file_name = f"{safe_filename(export_target)}_{date.today():%Y%m%d}.xlsx"
            generated = with_db(
                lambda db: generate_excel_document(
                    db,
                    document_type=f"{export_target} Excel",
                    document_number=f"EXP-{safe_filename(export_target)}-{date.today():%Y%m%d}",
                    creator="구매담당",
                    file_name=file_name,
                    file_bytes=styled_excel(export_df, export_target, f"{export_target} 내보내기"),
                )
            )
            if generated:
                st.session_state["purchase_last_export_excel"] = generated
                st.success(f"{file_name} 생성 완료")
    with export_cols[1]:
        generated = st.session_state.get("purchase_last_export_excel")
        if generated:
            st.download_button(
                "Excel 다운로드",
                data=generated["bytes"],
                file_name=generated["file_name"],
                mime=XLSX_MIME,
                use_container_width=True,
            )
    with export_cols[2]:
        st.caption("선택한 행 범위는 Streamlit 테이블의 체크 상태를 서버가 직접 읽을 수 없어 현재 검색 결과와 동일하게 처리됩니다.")

    st.markdown('<div class="purchase-section-title">문서 관리</div>', unsafe_allow_html=True)
    history_rows = with_db(lambda db: document_history_rows(db)) or []
    if not history_rows:
        st.info("생성된 문서 이력이 없습니다.")
        return
    history_df = pd.DataFrame(history_rows)
    st.dataframe(history_df.drop(columns=["문서ID"], errors="ignore"), hide_index=True, use_container_width=True, height=300)
    doc_ids = [row["문서ID"] for row in history_rows]
    cols = st.columns([1.0, 1.0, 1.0, 3.4], gap="small")
    selected_doc_id = cols[0].selectbox("문서", doc_ids, format_func=lambda doc_id: history_label(history_rows, doc_id), key="purchase_doc_history_select")
    document = with_db(lambda db: db.get(PurchaseDocument, selected_doc_id)) if PurchaseDocument is not None else None
    with cols[1]:
        if document:
            st.download_button(
                "재다운로드",
                data=document.file_bytes,
                file_name=document.file_name,
                mime=document.file_mime,
                use_container_width=True,
                key=f"purchase_doc_redownload_{selected_doc_id}",
            )
    with cols[2]:
        if st.button("재생성", type="primary", use_container_width=True, key="purchase_doc_regenerate"):
            regenerated = with_db(lambda db: regenerate_document(db, selected_doc_id))
            if regenerated:
                st.session_state["purchase_regenerated_document"] = regenerated
                st.success(f"{regenerated['file_name']} 재생성 완료")
    with cols[3]:
        regenerated = st.session_state.get("purchase_regenerated_document")
        if regenerated:
            st.download_button(
                "재생성 파일 다운로드",
                data=regenerated["bytes"],
                file_name=regenerated["file_name"],
                mime=regenerated["mime"],
                use_container_width=True,
            )


def list_purchase_requests(db: Session) -> list[PurchaseRequest]:
    return list(db.execute(select(PurchaseRequest).order_by(PurchaseRequest.request_date.desc(), PurchaseRequest.id.desc())).scalars())


def list_approved_pr_without_po(db: Session) -> list[PurchaseRequest]:
    return list(
        db.execute(
            select(PurchaseRequest)
            .where(PurchaseRequest.approval_status == "승인", PurchaseRequest.linked_po_number == "")
            .order_by(PurchaseRequest.request_date.desc(), PurchaseRequest.id.desc())
        ).scalars()
    )


def list_purchase_orders(db: Session) -> list[PurchaseOrder]:
    return list(db.execute(select(PurchaseOrder).order_by(PurchaseOrder.order_date.desc(), PurchaseOrder.id.desc())).scalars())


def list_suppliers(db: Session) -> list[Supplier]:
    return list(db.execute(select(Supplier).order_by(Supplier.supplier_name)).scalars())


def create_purchase_request(
    db: Session,
    department: str,
    item_code: str,
    item_name: str,
    spec: str,
    quantity: int,
    unit: str,
    request_date: date,
    reply_due_date: date | None,
    desired_due_date: date | None,
    delivery_place: str,
    request_notes: str,
    requester: str,
    approver: str,
    approval_status: str = "작성",
    source_type: str = "수기",
    memo: str = "",
) -> PurchaseRequest:
    row = PurchaseRequest(
        pr_number=next_number(db, PurchaseRequest, PurchaseRequest.pr_number, "PR"),
        department=clean_text(department),
        item_code=clean_text(item_code),
        item_name=clean_text(item_name),
        spec=clean_text(spec),
        quantity=max(int(quantity or 0), 0),
        unit=clean_text(unit) or "EA",
        request_date=request_date,
        reply_due_date=reply_due_date,
        desired_due_date=desired_due_date,
        delivery_place=clean_text(delivery_place) or DEFAULT_DELIVERY_PLACE,
        request_notes=clean_text(request_notes),
        requester=clean_text(requester),
        approver=clean_text(approver),
        approval_status=approval_status if approval_status in PR_STATUS else "작성",
        source_type=clean_text(source_type) or "수기",
        memo=clean_text(memo),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def create_quote(
    db: Session,
    pr: PurchaseRequest,
    supplier_name: str,
    supplier_manager: str,
    supplier_phone: str,
    supplier_email: str,
    unit_price: float,
    currency: str,
    moq: int,
    lead_time_days: int,
    shipping_fee: int,
    payment_terms: str,
    quote_valid_until: date | None,
    memo: str,
) -> RfqQuote:
    db_pr = db.execute(select(PurchaseRequest).where(PurchaseRequest.pr_number == pr.pr_number)).scalar_one()
    row = RfqQuote(
        pr_number=db_pr.pr_number,
        quote_number=next_number(db, RfqQuote, RfqQuote.quote_number, "QT"),
        item_name=db_pr.item_name,
        supplier_name=clean_text(supplier_name),
        supplier_manager=clean_text(supplier_manager),
        supplier_phone=clean_text(supplier_phone),
        supplier_email=clean_text(supplier_email),
        unit_price=max(to_float(unit_price), 0.0),
        currency=normalize_currency(currency),
        moq=max(moq, 0),
        lead_time_days=max(lead_time_days, 0),
        shipping_fee=max(shipping_fee, 0),
        payment_terms=clean_text(payment_terms),
        quote_valid_until=quote_valid_until,
        memo=clean_text(memo),
    )
    db.add(row)
    upsert_supplier_from_quote(db, row)
    db.flush()
    refresh_recommended_quote(db, db_pr.pr_number)
    db.commit()
    db.refresh(row)
    return row


def create_po_from_recommended_quote(db: Session, pr_number: str) -> PurchaseOrder | None:
    pr = db.execute(select(PurchaseRequest).where(PurchaseRequest.pr_number == pr_number)).scalar_one_or_none()
    if not pr or pr.linked_po_number:
        return None
    quote = db.execute(
        select(RfqQuote)
        .where(RfqQuote.pr_number == pr_number, RfqQuote.is_recommended == True)  # noqa: E712
        .order_by(RfqQuote.is_selected.desc(), RfqQuote.unit_price)
    ).scalars().first()
    if not quote:
        refresh_recommended_quote(db, pr_number)
        quote = db.execute(
            select(RfqQuote)
            .where(RfqQuote.pr_number == pr_number, RfqQuote.is_recommended == True)  # noqa: E712
            .order_by(RfqQuote.is_selected.desc(), RfqQuote.unit_price)
        ).scalars().first()
    if not quote:
        return None
    order_qty = max(int(pr.quantity or 0), int(quote.moq or 0), 1)
    po = PurchaseOrder(
        po_number=next_number(db, PurchaseOrder, PurchaseOrder.po_number, "PO"),
        pr_number=pr.pr_number,
        supplier_name=quote.supplier_name,
        item_name=pr.item_name,
        spec=pr.spec,
        quantity=order_qty,
        unit_price=quote.unit_price,
        currency=normalize_currency(quote.currency),
        shipping_fee=quote.shipping_fee,
        order_date=date.today(),
        expected_inbound_date=date.today() + timedelta(days=int(quote.lead_time_days or 0)),
        inbound_status="입고대기",
        progress_status="발주완료",
        order_amount=quote_total(quote, order_qty),
    )
    db.add(po)
    db.flush()
    pr.linked_po_number = po.po_number
    update_supplier_purchase_average(db, quote.supplier_name)
    db.commit()
    db.refresh(po)
    return po


def create_po_from_selected_quote(db: Session, pr_number: str) -> PurchaseOrder | None:
    pr = db.execute(select(PurchaseRequest).where(PurchaseRequest.pr_number == pr_number)).scalar_one_or_none()
    if not pr or pr.linked_po_number:
        return None
    selected = db.execute(
        select(RfqQuote).where(RfqQuote.pr_number == pr_number, RfqQuote.is_selected == True)  # noqa: E712
    ).scalar_one_or_none()
    if selected:
        selected.is_recommended = True
        db.commit()
    return create_po_from_recommended_quote(db, pr_number)


def receive_selected_po(db: Session, po_numbers: list[str]) -> int:
    count = 0
    for po_number in po_numbers:
        po = db.execute(select(PurchaseOrder).where(PurchaseOrder.po_number == po_number)).scalar_one_or_none()
        if not po or po.inbound_status == "입고완료":
            continue
        po.inbound_status = "입고완료"
        po.progress_status = "종결"
        po.actual_inbound_date = date.today()
        db.add(
            InventoryInbound(
                source_type="창고",
                inbound_date=date.today(),
                product_name=po.item_name,
                barcode="",
                inbound_qty=int(po.quantity or 0),
                vendor=po.supplier_name,
                inbound_type="PO 입고",
                memo=po.po_number,
                is_applied=False,
            )
        )
        count += 1
    db.commit()
    if count and services is not None:
        services.apply_inbound_to_stock(db, "창고", date.today())
    return count


def approve_selected_pr(db: Session, pr_numbers: list[str]) -> int:
    count = 0
    for pr in db.execute(select(PurchaseRequest).where(PurchaseRequest.pr_number.in_(pr_numbers))).scalars():
        if pr.approval_status != "승인":
            pr.approval_status = "승인"
            count += 1
    db.commit()
    return count


def save_pr_editor(db: Session, edited: pd.DataFrame) -> int:
    count = 0
    for record in edited.fillna("").to_dict("records"):
        pr_number = clean_text(record.get("구매요청번호"))
        if not pr_number:
            continue
        row = db.execute(select(PurchaseRequest).where(PurchaseRequest.pr_number == pr_number)).scalar_one_or_none()
        if not row:
            continue
        row.department = clean_text(record.get("요청부서"))
        row.item_code = clean_text(record.get("품목코드"))
        row.item_name = clean_text(record.get("품목"))
        row.spec = clean_text(record.get("규격"))
        row.quantity = to_int(record.get("수량"))
        row.unit = clean_text(record.get("단위")) or "EA"
        row.request_date = parse_date(record.get("요청일")) or row.request_date
        row.reply_due_date = parse_date(record.get("견적회신요청일"))
        row.desired_due_date = parse_date(record.get("희망납기일"))
        row.delivery_place = clean_text(record.get("납품장소")) or DEFAULT_DELIVERY_PLACE
        row.request_notes = clean_text(record.get("요청사항"))
        row.requester = clean_text(record.get("요청자"))
        row.approver = clean_text(record.get("승인자"))
        row.approval_status = clean_text(record.get("승인상태")) or row.approval_status
        row.memo = clean_text(record.get("비고"))
        count += 1
    db.commit()
    return count


def save_po_editor(db: Session, edited: pd.DataFrame) -> int:
    count = 0
    for record in edited.fillna("").to_dict("records"):
        po_number = clean_text(record.get("발주번호"))
        row = db.execute(select(PurchaseOrder).where(PurchaseOrder.po_number == po_number)).scalar_one_or_none()
        if not row:
            continue
        row.supplier_name = clean_text(record.get("업체")) or row.supplier_name
        row.item_name = clean_text(record.get("품목")) or row.item_name
        row.spec = clean_text(record.get("규격"))
        row.quantity = to_int(record.get("수량"))
        row.unit_price = max(to_float(record.get("단가")), 0.0)
        row.currency = normalize_currency(record.get("통화"))
        row.order_date = parse_date(record.get("발주일")) or row.order_date
        row.expected_inbound_date = parse_date(record.get("납기예정일"))
        row.inbound_status = clean_text(record.get("입고상태")) or row.inbound_status
        row.progress_status = clean_text(record.get("진행상태")) or row.progress_status
        row.order_amount = float(row.quantity or 0) * to_float(row.unit_price) + to_float(row.shipping_fee)
        count += 1
    db.commit()
    return count


def delete_selected_pos(db: Session, po_numbers: list[str]) -> int:
    count = 0
    for po_number in po_numbers:
        po = db.execute(select(PurchaseOrder).where(PurchaseOrder.po_number == po_number)).scalar_one_or_none()
        if not po:
            continue
        if po.inbound_status == "입고완료":
            raise ValueError(f"{po.po_number} 발주는 입고완료 상태라 삭제할 수 없습니다.")
        pr = db.execute(select(PurchaseRequest).where(PurchaseRequest.pr_number == po.pr_number)).scalar_one_or_none()
        if pr and pr.linked_po_number == po.po_number:
            pr.linked_po_number = ""
        db.delete(po)
        count += 1
    db.commit()
    return count


def upsert_supplier(
    db: Session,
    supplier_name: str,
    manager: str,
    phone: str,
    email: str,
    handled_items: str,
    moq_terms: str,
    avg_lead_time_days: int,
    avg_unit_price: float,
    avg_unit_price_currency: str,
    payment_terms: str,
    memo: str,
    original_supplier_name: str = "",
) -> Supplier:
    clean_name = clean_text(supplier_name)
    original_name = clean_text(original_supplier_name)
    lookup_name = original_name or clean_name
    row = db.execute(select(Supplier).where(Supplier.supplier_name == lookup_name)).scalar_one_or_none()
    if row is None and original_name:
        row = db.execute(select(Supplier).where(Supplier.supplier_name == clean_name)).scalar_one_or_none()
    if row is None:
        row = Supplier(supplier_name=clean_name)
        db.add(row)
    elif row.supplier_name != clean_name:
        duplicate = db.execute(select(Supplier).where(Supplier.supplier_name == clean_name)).scalar_one_or_none()
        if duplicate is not None and duplicate.id != row.id:
            raise ValueError(f"{clean_name} 협력사가 이미 등록되어 있습니다.")
        row.supplier_name = clean_name
    row.manager = clean_text(manager)
    row.phone = clean_text(phone)
    row.email = clean_text(email)
    row.handled_items = normalize_item_list(handled_items)
    row.moq_terms = clean_text(moq_terms)
    row.avg_lead_time_days = max(int(avg_lead_time_days or 0), 0)
    row.avg_unit_price = max(to_float(avg_unit_price), 0.0)
    row.avg_unit_price_currency = normalize_currency(avg_unit_price_currency)
    row.payment_terms = clean_text(payment_terms)
    row.memo = clean_text(memo)
    db.commit()
    db.refresh(row)
    return row


def delete_supplier(db: Session, supplier_name: str) -> bool:
    clean_name = clean_text(supplier_name)
    row = db.execute(select(Supplier).where(Supplier.supplier_name == clean_name)).scalar_one_or_none()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def save_suppliers(db: Session, edited: pd.DataFrame) -> int:
    seen = set()
    count = 0
    for record in edited.fillna("").to_dict("records"):
        supplier_name = clean_text(record.get("업체명"))
        if not supplier_name:
            continue
        row = db.execute(select(Supplier).where(Supplier.supplier_name == supplier_name)).scalar_one_or_none()
        if truthy(record.get("삭제")):
            if row:
                db.delete(row)
            continue
        if not row:
            row = Supplier(supplier_name=supplier_name)
            db.add(row)
        row.handled_items = normalize_item_list(record.get("취급품목"))
        row.moq_terms = clean_text(record.get("MOQ 조건"))
        row.manager = clean_text(record.get("담당자"))
        row.phone = clean_text(record.get("연락처"))
        row.email = clean_text(record.get("이메일"))
        row.avg_lead_time_days = to_int(record.get("평균납기"))
        row.avg_unit_price, row.avg_unit_price_currency = parse_compact_price(record.get("평균단가"))
        row.payment_terms = clean_text(record.get("결제조건"))
        row.memo = clean_text(record.get("비고"))
        seen.add(supplier_name)
        count += 1
    db.commit()
    return count


def save_quote_editor(db: Session, edited: pd.DataFrame) -> int:
    affected_prs: set[str] = set()
    grouped: dict[str, list[dict]] = {}
    count = 0
    for record in edited.fillna("").to_dict("records"):
        pr_number = clean_text(record.get("구매요청번호"))
        quote_number = clean_text(record.get("견적번호"))
        if pr_number:
            grouped.setdefault(pr_number, []).append(record)
        if not quote_number:
            continue
        quote = db.execute(select(RfqQuote).where(RfqQuote.quote_number == quote_number)).scalar_one_or_none()
        if not quote:
            continue
        affected_prs.add(quote.pr_number)
        if truthy(record.get("삭제")):
            linked_po = db.execute(
                select(PurchaseOrder).where(
                    PurchaseOrder.pr_number == quote.pr_number,
                    PurchaseOrder.supplier_name == quote.supplier_name,
                )
            ).scalar_one_or_none()
            if linked_po:
                raise ValueError(f"{quote.quote_number} 견적은 {linked_po.po_number} 발주와 연결되어 먼저 발주를 삭제해야 합니다.")
            db.delete(quote)
            count += 1
            continue
        quote.supplier_name = clean_text(record.get("업체명")) or quote.supplier_name
        quote.unit_price = max(to_float(record.get("단가")), 0.0)
        quote.currency = normalize_currency(record.get("통화"))
        quote.moq = max(to_int(record.get("MOQ")), 0)
        quote.lead_time_days = max(to_int(record.get("납기")), 0)
        quote.shipping_fee = max(to_int(record.get("배송비")), 0)
        quote.payment_terms = clean_text(record.get("결제조건"))
        quote.quote_valid_until = parse_date(record.get("견적 유효기간"))
        quote.memo = clean_text(record.get("품질/거래조건 메모"))
        quote.selection_reason = clean_text(record.get("선정 사유"))
        upsert_supplier_from_quote(db, quote)
        count += 1

    for pr_number, records in grouped.items():
        if not any(not truthy(record.get("삭제")) for record in records):
            continue
        selected_quote_number = ""
        selected_reason = ""
        for record in records:
            if truthy(record.get("선정")) and not truthy(record.get("삭제")):
                selected_quote_number = clean_text(record.get("견적번호"))
                selected_reason = clean_text(record.get("선정 사유"))
                break
        quotes = list(db.execute(select(RfqQuote).where(RfqQuote.pr_number == pr_number)).scalars())
        for quote in quotes:
            quote.is_selected = bool(selected_quote_number and quote.quote_number == selected_quote_number)
            quote.selection_reason = selected_reason if quote.is_selected else ""
        affected_prs.add(pr_number)

    db.flush()
    for pr_number in affected_prs:
        refresh_recommended_quote(db, pr_number)
    db.commit()
    return count


def save_quote_selection(db: Session, edited: pd.DataFrame) -> int:
    count = 0
    grouped: dict[str, list[dict]] = {}
    for record in edited.fillna("").to_dict("records"):
        pr_number = clean_text(record.get("구매요청번호"))
        if pr_number:
            grouped.setdefault(pr_number, []).append(record)
    for pr_number, records in grouped.items():
        selected_supplier = ""
        selected_reason = ""
        for record in records:
            if truthy(record.get("선정")):
                selected_supplier = clean_text(record.get("업체명"))
                selected_reason = clean_text(record.get("선정 사유"))
                break
        quotes = list(db.execute(select(RfqQuote).where(RfqQuote.pr_number == pr_number)).scalars())
        for quote in quotes:
            quote.is_selected = bool(selected_supplier and quote.supplier_name == selected_supplier)
            quote.selection_reason = selected_reason if quote.is_selected else ""
            count += 1
        refresh_recommended_quote(db, pr_number)
    db.commit()
    return count


def refresh_recommended_quote(db: Session, pr_number: str) -> None:
    pr = db.execute(select(PurchaseRequest).where(PurchaseRequest.pr_number == pr_number)).scalar_one_or_none()
    quotes = list(db.execute(select(RfqQuote).where(RfqQuote.pr_number == pr_number)).scalars())
    if not pr or not quotes:
        return
    for quote in quotes:
        quote.is_recommended = False
    selected = next((quote for quote in quotes if quote.is_selected), None)
    best = selected or min(quotes, key=lambda quote: quote_total(quote, pr.quantity))
    best.is_recommended = True


def quote_total(quote: RfqQuote, request_qty: int) -> float:
    order_qty = max(int(request_qty or 0), int(quote.moq or 0), 1)
    return order_qty * to_float(quote.unit_price) + to_float(quote.shipping_fee)


def quote_comparison_rows(db: Session) -> list[dict]:
    pr_map = {row.pr_number: row for row in db.execute(select(PurchaseRequest)).scalars()}
    po_by_pr = {row.pr_number: row.po_number for row in db.execute(select(PurchaseOrder)).scalars()}
    rows = []
    for quote in db.execute(select(RfqQuote).order_by(RfqQuote.pr_number.desc(), RfqQuote.unit_price)).scalars():
        pr = pr_map.get(quote.pr_number)
        request_qty = int(pr.quantity or 0) if pr else 0
        order_qty = max(request_qty, int(quote.moq or 0), 1)
        rows.append(
            {
                "구매요청번호": quote.pr_number,
                "견적번호": quote.quote_number or "",
                "품목": quote.item_name,
                "업체명": quote.supplier_name,
                "단가": quote.unit_price,
                "통화": normalize_currency(quote.currency),
                "요청수량": request_qty,
                "공급가액": order_qty * to_float(quote.unit_price),
                "부가세": round(order_qty * to_float(quote.unit_price) * 0.1, 4),
                "총금액": quote_total(quote, request_qty) + round(order_qty * to_float(quote.unit_price) * 0.1, 4),
                "MOQ": quote.moq,
                "납기": quote.lead_time_days,
                "배송비": quote.shipping_fee,
                "결제조건": quote.payment_terms,
                "견적 유효기간": quote.quote_valid_until,
                "발주수량": order_qty,
                "총 구매비용": quote_total(quote, request_qty),
                "추천": "추천" if quote.is_recommended else "",
                "선정 여부": "선정" if quote.is_selected else "",
                "선정 사유": quote.selection_reason,
                "발주번호": po_by_pr.get(quote.pr_number, ""),
                "품질/거래조건 메모": quote.memo,
            }
        )
    return rows


def upsert_supplier_from_quote(db: Session, quote: RfqQuote) -> None:
    supplier = db.execute(select(Supplier).where(Supplier.supplier_name == quote.supplier_name)).scalar_one_or_none()
    if supplier is None:
        supplier = Supplier(supplier_name=quote.supplier_name)
        db.add(supplier)
    if quote.supplier_manager:
        supplier.manager = quote.supplier_manager
    if quote.supplier_phone:
        supplier.phone = quote.supplier_phone
    if quote.supplier_email:
        supplier.email = quote.supplier_email
    supplier.handled_items = append_unique_items(supplier.handled_items, quote.item_name)
    if quote.moq and not supplier.moq_terms:
        supplier.moq_terms = f"{quote.item_name}: {int(quote.moq):,}개"
    if quote.payment_terms:
        supplier.payment_terms = quote.payment_terms
    if quote.lead_time_days and not supplier.avg_lead_time_days:
        supplier.avg_lead_time_days = quote.lead_time_days
    if quote.unit_price and not supplier.avg_unit_price:
        supplier.avg_unit_price = quote.unit_price
        supplier.avg_unit_price_currency = normalize_currency(quote.currency)


def update_supplier_purchase_average(db: Session, supplier_name: str) -> None:
    supplier = db.execute(select(Supplier).where(Supplier.supplier_name == supplier_name)).scalar_one_or_none()
    if not supplier:
        return
    rows = list(db.execute(select(PurchaseOrder).where(PurchaseOrder.supplier_name == supplier_name)).scalars())
    if not rows:
        return
    latest_currency = normalize_currency(rows[-1].currency)
    currency_rows = [row for row in rows if normalize_currency(row.currency) == latest_currency]
    supplier.avg_unit_price = round(sum(to_float(row.unit_price) for row in currency_rows) / len(currency_rows), 4)
    supplier.avg_unit_price_currency = latest_currency
    supplier.handled_items = append_unique_items(supplier.handled_items, *(row.item_name for row in rows))
    lead_times = [
        (row.expected_inbound_date - row.order_date).days
        for row in rows
        if row.expected_inbound_date and row.order_date
    ]
    if lead_times:
        supplier.avg_lead_time_days = round(sum(lead_times) / len(lead_times))


def pr_to_dict(row: PurchaseRequest) -> dict:
    return {
        "구매요청번호": row.pr_number,
        "요청부서": row.department,
        "품목코드": row.item_code,
        "품목": row.item_name,
        "규격": row.spec,
        "수량": row.quantity,
        "단위": row.unit,
        "요청일": row.request_date,
        "견적회신요청일": row.reply_due_date,
        "희망납기일": row.desired_due_date,
        "납품장소": row.delivery_place,
        "요청사항": row.request_notes,
        "요청자": row.requester,
        "승인자": row.approver,
        "승인상태": row.approval_status,
        "발주번호": row.linked_po_number,
        "비고": row.memo,
    }


def po_to_dict(row: PurchaseOrder) -> dict:
    return {
        "발주번호": row.po_number,
        "구매요청번호": row.pr_number,
        "업체": row.supplier_name,
        "품목": row.item_name,
        "규격": row.spec,
        "수량": row.quantity,
        "단가": row.unit_price,
        "통화": normalize_currency(row.currency),
        "발주일": row.order_date,
        "납기예정일": row.expected_inbound_date,
        "입고상태": row.inbound_status,
        "진행상태": row.progress_status,
        "발주금액": row.order_amount,
    }


def supplier_to_dict(row: Supplier) -> dict:
    return {
        "삭제": False,
        "업체명": row.supplier_name,
        "취급품목": row.handled_items,
        "MOQ 조건": row.moq_terms,
        "담당자": row.manager,
        "연락처": row.phone,
        "이메일": row.email,
        "평균납기": row.avg_lead_time_days,
        "평균단가": format_compact_price(row.avg_unit_price, row.avg_unit_price_currency),
        "결제조건": row.payment_terms,
        "비고": row.memo,
    }


def list_price_history_items(db: Session) -> list[str]:
    return [
        row[0]
        for row in db.execute(select(PurchaseOrder.item_name).distinct().order_by(PurchaseOrder.item_name)).all()
        if row[0]
    ]


def price_history_rows(db: Session, item_name: str) -> list[dict]:
    rows = list(
        db.execute(select(PurchaseOrder).where(PurchaseOrder.item_name == item_name).order_by(PurchaseOrder.order_date)).scalars()
    )
    return [
        {
            "날짜": row.order_date,
            "품목": row.item_name,
            "업체": row.supplier_name,
            "단가": row.unit_price,
            "통화": normalize_currency(row.currency),
            "수량": row.quantity,
            "발주금액": row.order_amount,
            "발주번호": row.po_number,
        }
        for row in rows
    ]


def purchase_kpi(db: Session) -> dict:
    rows = list(db.execute(select(PurchaseOrder)).scalars())
    total_amounts_by_currency: dict[str, float] = {}
    lead_times = [
        (row.expected_inbound_date - row.order_date).days
        for row in rows
        if row.expected_inbound_date and row.order_date
    ]
    completed = [row for row in rows if row.inbound_status == "입고완료" and row.actual_inbound_date]
    on_time = [row for row in completed if row.expected_inbound_date and row.actual_inbound_date <= row.expected_inbound_date]
    delayed_count = sum(
        1
        for row in rows
        if row.expected_inbound_date and row.expected_inbound_date < date.today() and row.inbound_status != "입고완료"
    )
    saving_rate = calculate_saving_rate(rows)
    monthly = {}
    supplier = {}
    for row in rows:
        currency = normalize_currency(row.currency)
        total_amounts_by_currency[currency] = total_amounts_by_currency.get(currency, 0.0) + to_float(row.order_amount)
        month = row.order_date.strftime("%Y-%m") if row.order_date else ""
        if month:
            monthly[month] = monthly.get(month, 0) + to_float(row.order_amount)
        supplier[row.supplier_name] = supplier.get(row.supplier_name, 0) + to_float(row.order_amount)
    return {
        "total_amount": sum(total_amounts_by_currency.values()),
        "total_amounts_by_currency": total_amounts_by_currency,
        "po_count": len(rows),
        "avg_lead_time": sum(lead_times) / len(lead_times) if lead_times else 0,
        "on_time_rate": len(on_time) * 100 / len(completed) if completed else 0,
        "saving_rate": saving_rate,
        "delayed_count": delayed_count,
        "monthly": [{"월": key, "구매금액": value} for key, value in sorted(monthly.items())],
        "supplier": [{"업체": key, "구매금액": value} for key, value in sorted(supplier.items(), key=lambda item: item[1], reverse=True)],
    }


def calculate_saving_rate(rows: list[PurchaseOrder]) -> float:
    by_item: dict[str, list[PurchaseOrder]] = {}
    for row in rows:
        by_item.setdefault(row.item_name, []).append(row)
    savings = []
    for item_rows in by_item.values():
        ordered = sorted(item_rows, key=lambda row: row.order_date or date.min)
        if len(ordered) < 2:
            continue
        first = to_float(ordered[0].unit_price)
        last = to_float(ordered[-1].unit_price)
        if first > 0:
            savings.append((first - last) * 100 / first)
    return sum(savings) / len(savings) if savings else 0


def generate_rfq_pdf_document(db: Session, pr_number: str, supplier_name: str, creator: str) -> dict | None:
    pr = db.execute(select(PurchaseRequest).where(PurchaseRequest.pr_number == pr_number)).scalar_one_or_none()
    quote = db.execute(
        select(RfqQuote).where(RfqQuote.pr_number == pr_number, RfqQuote.supplier_name == supplier_name)
    ).scalar_one_or_none()
    if not pr or not quote:
        return None
    document_number = f"RFQ-{pr.pr_number.replace('PR-', '')}"
    pdf_bytes = rfq_pdf_bytes(pr, quote, document_number)
    file_name = f"RFQ_{safe_filename(document_number)}_{safe_filename(supplier_name)}_{date.today():%Y%m%d}.pdf"
    save_document(
        db,
        document_type="견적요청서(RFQ)",
        document_number=document_number,
        creator=creator,
        pr_number=pr.pr_number,
        quote_number=quote.quote_number,
        po_number="",
        supplier_name=supplier_name,
        file_name=file_name,
        file_mime=PDF_MIME,
        file_bytes=pdf_bytes,
    )
    return {"bytes": pdf_bytes, "file_name": file_name, "pr_number": pr_number, "supplier_name": supplier_name, "mime": PDF_MIME}


def generate_comparison_pdf_document(db: Session, pr_number: str, creator: str) -> dict | None:
    pr = db.execute(select(PurchaseRequest).where(PurchaseRequest.pr_number == pr_number)).scalar_one_or_none()
    quotes = list(db.execute(select(RfqQuote).where(RfqQuote.pr_number == pr_number).order_by(RfqQuote.unit_price)).scalars())
    if not pr or not quotes:
        return None
    document_number = f"CMP-{pr.pr_number.replace('PR-', '')}"
    pdf_bytes = comparison_pdf_bytes(pr, quotes, document_number)
    file_name = f"견적비교표_{safe_filename(document_number)}_{date.today():%Y%m%d}.pdf"
    selected_quote = next((quote for quote in quotes if quote.is_selected), None)
    save_document(
        db,
        document_type="견적비교표",
        document_number=document_number,
        creator=creator,
        pr_number=pr.pr_number,
        quote_number=selected_quote.quote_number if selected_quote else "",
        po_number="",
        supplier_name=selected_quote.supplier_name if selected_quote else "",
        file_name=file_name,
        file_mime=PDF_MIME,
        file_bytes=pdf_bytes,
    )
    return {"bytes": pdf_bytes, "file_name": file_name, "pr_number": pr_number, "supplier_name": "", "mime": PDF_MIME}


def regenerate_document(db: Session, document_id: int) -> dict | None:
    document = db.get(PurchaseDocument, document_id) if PurchaseDocument is not None else None
    if not document:
        return None
    if document.document_type == "견적요청서(RFQ)":
        return generate_rfq_pdf_document(db, document.pr_number, document.supplier_name, document.creator)
    if document.document_type == "견적비교표":
        return generate_comparison_pdf_document(db, document.pr_number, document.creator)
    save_document(
        db,
        document_type=document.document_type,
        document_number=document.document_number,
        creator=document.creator,
        pr_number=document.pr_number,
        quote_number=document.quote_number,
        po_number=document.po_number,
        supplier_name=document.supplier_name,
        file_name=document.file_name,
        file_mime=document.file_mime,
        file_bytes=document.file_bytes,
    )
    return {"bytes": document.file_bytes, "file_name": document.file_name, "mime": document.file_mime}


def save_document(
    db: Session,
    document_type: str,
    document_number: str,
    creator: str,
    pr_number: str,
    quote_number: str,
    po_number: str,
    supplier_name: str,
    file_name: str,
    file_mime: str,
    file_bytes: bytes,
) -> PurchaseDocument:
    version = (
        db.scalar(
            select(func.max(PurchaseDocument.version)).where(
                PurchaseDocument.document_type == document_type,
                PurchaseDocument.document_number == document_number,
            )
        )
        or 0
    ) + 1
    row = PurchaseDocument(
        document_type=document_type,
        document_number=document_number,
        version=version,
        creator=clean_text(creator),
        pr_number=clean_text(pr_number),
        quote_number=clean_text(quote_number),
        po_number=clean_text(po_number),
        supplier_name=clean_text(supplier_name),
        file_name=file_name,
        file_mime=file_mime,
        file_bytes=file_bytes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def generate_excel_document(
    db: Session,
    document_type: str,
    document_number: str,
    creator: str,
    file_name: str,
    file_bytes: bytes,
    pr_number: str = "",
    quote_number: str = "",
    po_number: str = "",
    supplier_name: str = "",
) -> dict:
    save_document(
        db,
        document_type=document_type,
        document_number=document_number,
        creator=creator,
        pr_number=pr_number,
        quote_number=quote_number,
        po_number=po_number,
        supplier_name=supplier_name,
        file_name=file_name,
        file_mime=XLSX_MIME,
        file_bytes=file_bytes,
    )
    return {"bytes": file_bytes, "file_name": file_name, "mime": XLSX_MIME}


def document_history_rows(db: Session) -> list[dict]:
    if PurchaseDocument is None:
        return []
    rows = list(db.execute(select(PurchaseDocument).order_by(PurchaseDocument.created_at.desc(), PurchaseDocument.id.desc())).scalars())
    return [
        {
            "문서ID": row.id,
            "문서 종류": row.document_type,
            "문서번호": row.document_number,
            "버전": row.version,
            "생성일시": row.created_at,
            "생성자": row.creator,
            "관련 구매요청번호": row.pr_number,
            "관련 견적번호": row.quote_number,
            "관련 발주번호": row.po_number,
            "업체명": row.supplier_name,
            "파일명": row.file_name,
        }
        for row in rows
    ]


def history_label(rows: list[dict], doc_id: int) -> str:
    row = next((item for item in rows if item["문서ID"] == doc_id), {})
    return f"{row.get('문서번호', doc_id)} v{row.get('버전', '')} / {row.get('파일명', '')}"


def rfq_pdf_bytes(pr: PurchaseRequest, quote: RfqQuote, document_number: str) -> bytes:
    styles = pdf_styles()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
    )
    story = [
        document_header("견적요청서", document_number),
        Spacer(1, 6),
        info_table(
            [
                ("회사명", COMPANY_NAME),
                ("작성일", date.today().isoformat()),
                ("견적 회신 요청일", format_date(pr.reply_due_date)),
                ("공급업체명", quote.supplier_name),
                ("공급업체 담당자", quote.supplier_manager),
                ("연락처", quote.supplier_phone),
                ("이메일", quote.supplier_email),
                ("작성자", pr.requester),
                ("승인자", pr.approver),
            ],
            styles,
        ),
        Spacer(1, 8),
        Paragraph("요청 품목", styles["section"]),
        data_table(
            [
                ["품목코드", "품목명", "규격", "요청수량", "단위", "희망납기일", "납품장소"],
                [pr.item_code, pr.item_name, pr.spec, f"{pr.quantity:,}", pr.unit, format_date(pr.desired_due_date), pr.delivery_place],
            ],
            [24 * mm, 40 * mm, 28 * mm, 22 * mm, 16 * mm, 26 * mm, 38 * mm],
        ),
        Spacer(1, 8),
        Paragraph("요청사항", styles["section"]),
        Paragraph(clean_text(pr.request_notes or pr.memo) or "-", styles["body"]),
        Spacer(1, 8),
        Paragraph("공급업체 회신란", styles["section"]),
        data_table(
            [
                ["공급단가", "통화", "MOQ", "예상 납기", "배송비", "결제조건", "견적 유효기간"],
                [
                    money_or_blank(quote.unit_price, quote.currency),
                    normalize_currency(quote.currency),
                    f"{quote.moq:,}" if quote.moq else "",
                    f"{quote.lead_time_days}일" if quote.lead_time_days else "",
                    money_or_blank(quote.shipping_fee, quote.currency),
                    quote.payment_terms,
                    format_date(quote.quote_valid_until),
                ],
                ["품질 또는 거래조건 메모", span_text(quote.memo, 6), "", "", "", "", ""],
            ],
            [26 * mm, 16 * mm, 18 * mm, 22 * mm, 26 * mm, 38 * mm, 32 * mm],
        ),
        Spacer(1, 10),
        signature_table(["작성자", "승인자", "공급업체 확인"]),
    ]
    doc.build(story, onFirstPage=pdf_footer, onLaterPages=pdf_footer)
    return buffer.getvalue()


def comparison_pdf_bytes(pr: PurchaseRequest, quotes: list[RfqQuote], document_number: str) -> bytes:
    styles = pdf_styles()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=10 * mm,
        leftMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
    )
    rows = [["업체명", "단가", "통화", "요청수량", "공급가액", "부가세", "총금액", "MOQ", "납기", "배송비", "결제조건", "유효기간", "메모", "선정", "선정 사유"]]
    min_price = min((to_float(quote.unit_price) for quote in quotes if to_float(quote.unit_price) > 0), default=0)
    for quote in quotes:
        currency = normalize_currency(quote.currency)
        supply = to_float(quote.unit_price) * int(pr.quantity or 0)
        vat = round(supply * 0.1, 4)
        total = supply + vat + to_float(quote.shipping_fee)
        rows.append(
            [
                f"{quote.supplier_name}{' (최저단가)' if min_price and quote.unit_price == min_price else ''}",
                money_or_blank(quote.unit_price, currency),
                currency,
                f"{pr.quantity:,}",
                money_or_blank(supply, currency),
                money_or_blank(vat, currency),
                money_or_blank(total, currency),
                f"{quote.moq:,}" if quote.moq else "",
                f"{quote.lead_time_days}일" if quote.lead_time_days else "",
                money_or_blank(quote.shipping_fee, currency),
                quote.payment_terms,
                format_date(quote.quote_valid_until),
                quote.memo,
                "선정" if quote.is_selected else "",
                quote.selection_reason,
            ]
        )
    story = [
        document_header("견적비교표", document_number),
        Spacer(1, 6),
        info_table(
            [
                ("구매요청번호", pr.pr_number),
                ("품목", pr.item_name),
                ("규격", pr.spec),
                ("요청수량", f"{pr.quantity:,} {pr.unit}"),
                ("작성일", date.today().isoformat()),
                ("작성자", pr.requester),
            ],
            styles,
        ),
        Spacer(1, 8),
        data_table(rows, [28 * mm, 20 * mm, 14 * mm, 18 * mm, 23 * mm, 20 * mm, 24 * mm, 16 * mm, 16 * mm, 22 * mm, 24 * mm, 22 * mm, 34 * mm, 16 * mm, 30 * mm], font_size=7),
    ]
    doc.build(story, onFirstPage=pdf_footer, onLaterPages=pdf_footer)
    return buffer.getvalue()


def register_pdf_fonts() -> None:
    if MALGUN_FONT in pdfmetrics.getRegisteredFontNames():
        return
    fonts_dir = Path("C:/Windows/Fonts")
    pdfmetrics.registerFont(TTFont(MALGUN_FONT, str(fonts_dir / "malgun.ttf")))
    pdfmetrics.registerFont(TTFont(MALGUN_BOLD_FONT, str(fonts_dir / "malgunbd.ttf")))


def pdf_styles() -> dict:
    register_pdf_fonts()
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("KoreanTitle", parent=base["Title"], fontName=MALGUN_BOLD_FONT, fontSize=20, leading=26, alignment=TA_CENTER),
        "section": ParagraphStyle("KoreanSection", parent=base["Heading3"], fontName=MALGUN_BOLD_FONT, fontSize=10, leading=14, spaceAfter=4),
        "body": ParagraphStyle("KoreanBody", parent=base["BodyText"], fontName=MALGUN_FONT, fontSize=9, leading=13, alignment=TA_LEFT),
        "small": ParagraphStyle("KoreanSmall", parent=base["BodyText"], fontName=MALGUN_FONT, fontSize=8, leading=11),
    }


def document_header(title: str, document_number: str):
    styles = pdf_styles()
    header = Table(
        [["", Paragraph(title, styles["title"]), Paragraph(f"문서번호<br/>{document_number}", styles["small"])]],
        colWidths=[42 * mm, 98 * mm, 42 * mm],
    )
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (1, 0), "CENTER"),
                ("ALIGN", (2, 0), (2, 0), "LEFT"),
            ]
        )
    )
    return header


def info_table(items: list[tuple[str, str]], styles: dict):
    rows = []
    for index in range(0, len(items), 3):
        cells = items[index : index + 3]
        row = []
        for label, value in cells:
            row.extend([Paragraph(label, styles["small"]), Paragraph(clean_text(value) or "-", styles["small"])])
        while len(row) < 6:
            row.extend(["", ""])
        rows.append(row)
    table = Table(rows, colWidths=[22 * mm, 42 * mm, 24 * mm, 42 * mm, 24 * mm, 42 * mm])
    table.setStyle(base_table_style(header_columns=[0, 2, 4]))
    return table


def data_table(rows: list[list], col_widths: list, font_size: int = 8):
    styles = pdf_styles()
    wrapped = [
        [Paragraph(clean_text(cell), styles["small"]) if isinstance(cell, str) and len(cell) > 18 else cell for cell in row]
        for row in rows
    ]
    table = Table(wrapped, colWidths=col_widths, repeatRows=1)
    style = base_table_style()
    style.add("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d9f4ef"))
    style.add("FONTNAME", (0, 0), (-1, -1), MALGUN_FONT)
    style.add("FONTSIZE", (0, 0), (-1, -1), font_size)
    table.setStyle(style)
    return table


def base_table_style(header_columns: list[int] | None = None) -> TableStyle:
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#7aa9a2")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (0, 0), (-1, -1), MALGUN_FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for column in header_columns or []:
        commands.append(("BACKGROUND", (column, 0), (column, -1), colors.HexColor("#d9f4ef")))
        commands.append(("FONTNAME", (column, 0), (column, -1), MALGUN_BOLD_FONT))
    return TableStyle(commands)


def signature_table(labels: list[str]):
    table = Table([labels, ["", "", ""]], colWidths=[42 * mm, 42 * mm, 42 * mm], rowHeights=[9 * mm, 18 * mm])
    table.setStyle(base_table_style())
    return table


def pdf_footer(canvas, doc) -> None:
    canvas.saveState()
    register_pdf_fonts()
    canvas.setFont(MALGUN_FONT, 7)
    canvas.drawRightString(A4[0] - 14 * mm if doc.pagesize == A4 else landscape(A4)[0] - 10 * mm, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def quote_reply_template_excel() -> bytes:
    columns = ["공급업체명*", "담당자*", "연락처", "이메일", "품목코드*", "품목명*", "규격", "요청수량*", "단위", "공급단가*", "통화*", "MOQ", "예상 납기", "배송비", "결제조건", "견적 유효기간", "품질/거래조건 메모"]
    sample = [["케이리빙", "홍길동", "010-0000-0000", "buyer@example.com", "SKU-001", "와이어 바스켓", "300x200", 100, "EA", 2500.25, "KRW", 50, 7, 3000, "월말정산", date.today() + timedelta(days=30), "검수 기준 준수"]]
    return template_excel("업체 견적 회신용 빈 양식", columns, sample, required_prefix="*")


def quote_comparison_template_excel() -> bytes:
    columns = ["업체명*", "단가*", "통화*", "요청수량*", "공급가액", "부가세", "총금액", "MOQ", "납기", "배송비", "결제조건", "견적 유효기간", "품질/거래조건 메모", "선정 여부", "선정 사유"]
    sample = [["케이리빙", 2500.25, "KRW", 100, "=B4*D4", "=E4*0.1", "=E4+F4+J4", 50, 7, 3000, "월말정산", date.today() + timedelta(days=30), "조건 양호", "Y", "최저단가"]]
    return template_excel("견적 비교표", columns, sample, required_prefix="*")


def purchase_item_template_excel() -> bytes:
    columns = ["요청부서*", "품목코드", "품목명*", "규격", "수량*", "단위", "요청일*", "견적 회신 요청일", "희망납기일", "납품장소", "요청사항", "요청자", "승인자"]
    sample = [["생산팀", "SKU-001", "와이어 바스켓", "300x200", 100, "EA", date.today(), date.today() + timedelta(days=3), date.today() + timedelta(days=14), DEFAULT_DELIVERY_PLACE, "검수 필요", "구매담당", "팀장"]]
    return template_excel("구매 품목 일괄등록 양식", columns, sample, required_prefix="*")


def po_bulk_template_excel() -> bytes:
    columns = ["발주번호", "구매요청번호*", "업체*", "품목*", "규격", "수량*", "단가*", "통화*", "배송비", "발주일*", "납기예정일", "입고상태", "진행상태"]
    sample = [["", "PR-20260721-001", "케이리빙", "와이어 바스켓", "300x200", 100, 2500.25, "KRW", 3000, date.today(), date.today() + timedelta(days=7), "입고대기", "발주완료"]]
    return template_excel("발주 일괄등록 양식", columns, sample, required_prefix="*")


def template_excel(title: str, columns: list[str], sample_rows: list[list], required_prefix: str = "*") -> bytes:
    df = pd.DataFrame(sample_rows, columns=columns)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter", date_format="yyyy-mm-dd", datetime_format="yyyy-mm-dd") as writer:
        sheet_name = "양식"
        df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=2)
        workbook = writer.book
        worksheet = writer.sheets[sheet_name]
        title_fmt = workbook.add_format({"bold": True, "font_size": 16, "font_color": "#064e3b"})
        guide_fmt = workbook.add_format({"font_color": "#475569"})
        header_fmt = workbook.add_format({"bold": True, "bg_color": "#d9f4ef", "border": 1})
        required_fmt = workbook.add_format({"bold": True, "bg_color": "#ffe4e6", "border": 1})
        number_fmt = workbook.add_format({"num_format": "#,##0"})
        money_fmt = workbook.add_format({"num_format": "#,##0.####"})
        worksheet.write(0, 0, title, title_fmt)
        worksheet.write(1, 0, "별표(*) 항목은 필수 입력입니다. 공급가액/부가세/총금액은 수식으로 자동 계산됩니다.", guide_fmt)
        for col_idx, column in enumerate(columns):
            worksheet.write(2, col_idx, column, required_fmt if required_prefix in column else header_fmt)
            width = max(len(str(column)) + 4, 14)
            worksheet.set_column(col_idx, col_idx, min(width, 28))
            if any(token in column for token in ["수량", "MOQ", "납기"]):
                worksheet.set_column(col_idx, col_idx, 13, number_fmt)
            if any(token in column for token in ["단가", "금액", "배송비"]):
                worksheet.set_column(col_idx, col_idx, 14, money_fmt)
        worksheet.freeze_panes(3, 0)
        worksheet.autofilter(2, 0, 2 + max(len(df), 1), len(columns) - 1)
        guide = workbook.add_worksheet("작성방법")
        guide.write(0, 0, "작성방법", title_fmt)
        guide.write(2, 0, "1. 필수 입력 항목은 반드시 입력하세요.")
        guide.write(3, 0, "2. 수량과 금액은 숫자로 입력하고, 통화는 KRW 또는 USD로 입력하세요.")
        guide.write(4, 0, "3. 견적 비교표의 공급가액, 부가세, 총금액 수식은 아래 행으로 복사해서 사용할 수 있습니다.")
        guide.write(5, 0, "4. 작성 완료 후 구매관리 화면에서 업로드/등록 업무에 활용하세요.")
    return output.getvalue()


def styled_excel(df: pd.DataFrame, sheet_name: str, title: str) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter", date_format="yyyy-mm-dd", datetime_format="yyyy-mm-dd") as writer:
        export_df = df.copy() if not df.empty else pd.DataFrame(columns=["데이터 없음"])
        export_df.to_excel(writer, index=False, sheet_name=sheet_name[:31], startrow=2)
        workbook = writer.book
        worksheet = writer.sheets[sheet_name[:31]]
        title_fmt = workbook.add_format({"bold": True, "font_size": 16, "font_color": "#064e3b"})
        header_fmt = workbook.add_format({"bold": True, "bg_color": "#d9f4ef", "border": 1})
        number_fmt = workbook.add_format({"num_format": "#,##0"})
        money_fmt = workbook.add_format({"num_format": "#,##0.####"})
        worksheet.write(0, 0, title, title_fmt)
        worksheet.write(1, 0, f"생성일: {datetime.now():%Y-%m-%d %H:%M}")
        for col_idx, column in enumerate(export_df.columns):
            worksheet.write(2, col_idx, column, header_fmt)
            values = [str(value) for value in export_df[column].head(100).tolist()]
            worksheet.set_column(col_idx, col_idx, min(max([len(str(column)), *[len(value) for value in values]]) + 3, 32))
            if any(token in str(column) for token in ["수량", "MOQ", "납기"]):
                worksheet.set_column(col_idx, col_idx, 13, number_fmt)
            if any(token in str(column) for token in ["단가", "금액", "공급가액", "부가세", "배송비"]):
                worksheet.set_column(col_idx, col_idx, 15, money_fmt)
        worksheet.freeze_panes(3, 0)
        worksheet.autofilter(2, 0, 2 + max(len(export_df), 1), max(len(export_df.columns) - 1, 0))
        guide = workbook.add_worksheet("작성방법")
        guide.write(0, 0, "내보내기 안내", title_fmt)
        guide.write(2, 0, "구매관리 화면에 저장된 실제 데이터를 기준으로 생성된 파일입니다.")
    return output.getvalue()


def purchase_export_dataframe(
    db: Session,
    target: str,
    range_type: str,
    start_date: date,
    end_date: date,
    supplier_filter: str,
    item_filter: str,
) -> pd.DataFrame:
    if target == "견적비교":
        df = pd.DataFrame(quote_comparison_rows(db))
        date_column = None
    elif target == "발주내역":
        df = pd.DataFrame([po_to_dict(row) for row in list_purchase_orders(db)])
        date_column = "발주일"
    elif target == "단가이력":
        rows = []
        for item in list_price_history_items(db):
            rows.extend(price_history_rows(db, item))
        df = pd.DataFrame(rows)
        date_column = "날짜"
    else:
        df = mrp_recommendation_export_df(db)
        date_column = "기준일자"
    if df.empty:
        return df
    if range_type == "지정한 기간" and date_column and date_column in df.columns:
        dates = pd.to_datetime(df[date_column], errors="coerce")
        df = df[(dates.dt.date >= start_date) & (dates.dt.date <= end_date)]
    if range_type == "지정한 공급업체" and supplier_filter:
        supplier_cols = [col for col in df.columns if col in {"업체", "업체명", "공급처"}]
        if supplier_cols:
            df = df[df[supplier_cols[0]].astype(str).str.contains(supplier_filter, case=False, na=False)]
    if range_type == "지정한 품목" and item_filter:
        item_cols = [col for col in df.columns if col in {"품목", "품목명", "상품명"}]
        if item_cols:
            df = df[df[item_cols[0]].astype(str).str.contains(item_filter, case=False, na=False)]
    return df


def mrp_recommendation_export_df(db: Session) -> pd.DataFrame:
    if InventoryDaily is None:
        return pd.DataFrame()
    latest_date = db.scalar(select(func.max(InventoryDaily.work_date)).where(InventoryDaily.source_type == "창고"))
    if not latest_date:
        return pd.DataFrame()
    rows = list(db.execute(select(InventoryDaily).where(InventoryDaily.source_type == "창고", InventoryDaily.work_date == latest_date)).scalars())
    data = []
    for row in rows:
        current = int(row.available_stock if row.available_stock is not None else row.current_stock or 0)
        safe = int(row.safe_stock or 0)
        shortage = max(safe - current, 0)
        if shortage <= 0 and row.stock_status != "입고필요":
            continue
        data.append(
            {
                "기준일자": latest_date,
                "상품명": row.product_name,
                "현재재고": current,
                "안전재고": safe,
                "부족수량": shortage,
                "발주추천수량": max(shortage, 1),
                "공급처": row.supplier,
                "재고상태": row.stock_status,
            }
        )
    return pd.DataFrame(data)


def format_date(value) -> str:
    if not value:
        return ""
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return clean_text(value)
    return parsed.date().isoformat()


def money_or_blank(value, currency: str = "KRW") -> str:
    amount = to_float(value)
    return format_currency_amount(amount, currency) if amount else ""


def span_text(value, _span: int) -> str:
    return clean_text(value)


def safe_filename(value: str) -> str:
    text = clean_text(value) or "document"
    for char in '\\/:*?"<>|':
        text = text.replace(char, "_")
    return text.replace(" ", "_")


def next_number(db: Session, model, column, prefix: str) -> str:
    today_key = date.today().strftime("%Y%m%d")
    pattern = f"{prefix}-{today_key}-%"
    count = db.scalar(select(func.count()).where(column.like(pattern))) or 0
    return f"{prefix}-{today_key}-{int(count) + 1:03d}"


def selected_numbers(df: pd.DataFrame, column: str) -> list[str]:
    if df is None or df.empty or "선택" not in df.columns and "입고완료처리" not in df.columns:
        return []
    selected_column = "선택" if "선택" in df.columns else "입고완료처리"
    return [clean_text(row.get(column)) for row in df.to_dict("records") if truthy(row.get(selected_column))]


def selected_by_flag(df: pd.DataFrame, column: str, flag_column: str) -> list[str]:
    if df is None or df.empty or flag_column not in df.columns:
        return []
    return [clean_text(row.get(column)) for row in df.to_dict("records") if truthy(row.get(flag_column))]


def clean_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_item_list(value) -> str:
    seen = set()
    items = []
    text = clean_text(value).replace("，", ",").replace("/", ",").replace("\n", ",")
    for raw_item in text.split(","):
        item = clean_text(raw_item)
        if item and item not in seen:
            seen.add(item)
            items.append(item)
    return ", ".join(items)


def append_unique_items(existing, *new_items) -> str:
    merged = normalize_item_list(existing)
    for item in new_items:
        merged = normalize_item_list(f"{merged}, {clean_text(item)}")
    return merged


def to_int(value) -> int:
    text = clean_text(value).replace(",", "")
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def to_float(value) -> float:
    text = clean_text(value).replace(",", "")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def selected_price_decimal_places() -> int:
    value = to_int(st.session_state.get("purchase_price_decimal_places", 1))
    return value if value in PRICE_DECIMAL_OPTIONS else 1


def price_step() -> float:
    places = selected_price_decimal_places()
    return 1.0 if places <= 0 else 10 ** -places


def price_input_format() -> str:
    return f"%.{selected_price_decimal_places()}f"


def format_decimal_display(value) -> str:
    amount = round(to_float(value), selected_price_decimal_places())
    if amount == 0:
        return "0"
    if float(amount).is_integer():
        return f"{int(amount):,}"
    return f"{amount:,.{selected_price_decimal_places()}f}".rstrip("0").rstrip(".")


def parse_compact_price(value) -> tuple[float, str]:
    text = clean_text(value).upper().replace(",", "")
    if not text:
        return 0.0, "KRW"
    currency = "USD" if "$" in text or "USD" in text else "KRW"
    for token in ["KRW", "USD", "WON", "원", "W", "$", "\\"]:
        text = text.replace(token, "")
    numeric = "".join(char for char in text if char.isdigit() or char in ".-")
    return max(to_float(numeric), 0.0), currency


def parse_moq_quantity(value) -> int:
    text = clean_text(value).replace(",", "")
    digits = []
    for char in text:
        if char.isdigit():
            digits.append(char)
        elif digits:
            break
    return to_int("".join(digits))


def compact_currency_symbol(currency: str) -> str:
    return "$" if normalize_currency(currency) == "USD" else "W"


def format_compact_price(value, currency: str = "KRW") -> str:
    amount = to_float(value)
    if not amount:
        return ""
    return f"{format_decimal_display(amount)}{compact_currency_symbol(currency)}"


def normalize_currency(value) -> str:
    currency = clean_text(value).upper()
    return currency if currency in CURRENCIES else "KRW"


def currency_index(value) -> int:
    return CURRENCIES.index(normalize_currency(value))


def currency_label(value: str) -> str:
    labels = {"KRW": "KRW (원)", "USD": "USD ($)"}
    return labels.get(normalize_currency(value), value)


def format_number(value) -> str:
    amount = round(to_float(value), selected_price_decimal_places())
    if amount.is_integer():
        return f"{int(amount):,}"
    return f"{amount:,.{selected_price_decimal_places()}f}".rstrip("0").rstrip(".")


def format_currency_amount(value, currency: str = "KRW") -> str:
    return f"{format_number(value)} {normalize_currency(currency)}"


def amount_totals_by_currency(rows: list[dict], amount_column: str, currency_column: str) -> dict[str, float]:
    totals: dict[str, float] = {}
    for row in rows:
        currency = normalize_currency(row.get(currency_column))
        totals[currency] = totals.get(currency, 0.0) + to_float(row.get(amount_column))
    return totals


def format_currency_totals(totals: dict[str, float]) -> str:
    if not totals:
        return "0 KRW"
    return " / ".join(
        format_currency_amount(amount, currency)
        for currency, amount in sorted(totals.items())
    )


def parse_date(value) -> date | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return clean_text(value).lower() in {"true", "1", "y", "yes", "선택"}


def inject_purchase_css() -> None:
    st.markdown(
        """
        <style>
        .purchase-title {
            color: #ecfeff;
            font-size: 1.45rem;
            font-weight: 800;
            margin: 0.2rem 0 0.1rem;
        }
        .purchase-section-title {
            color: #b9fff8;
            font-size: 1.02rem;
            font-weight: 800;
            margin: 1rem 0 0.45rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
