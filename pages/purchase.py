from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape
from io import BytesIO
import json
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
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
    from backend.database import SessionLocal, init_db, reset_sqlite_engine_after_write_error
    from backend import models as backend_models

    InventoryDaily = backend_models.InventoryDaily
    InventoryInbound = backend_models.InventoryInbound
    PurchaseDocument = backend_models.PurchaseDocument
    PurchaseOrder = backend_models.PurchaseOrder
    PurchaseRequest = backend_models.PurchaseRequest
    RfqQuote = backend_models.RfqQuote
    Supplier = backend_models.Supplier
    SupplierEvaluation = getattr(backend_models, "SupplierEvaluation", None)
    SupplierEvaluationCriteria = getattr(backend_models, "SupplierEvaluationCriteria", None)
    SupplierEvaluationHistory = getattr(backend_models, "SupplierEvaluationHistory", None)
    SupplierEvaluationItem = getattr(backend_models, "SupplierEvaluationItem", None)
    SupplierEvaluationCriteriaVersion = getattr(backend_models, "SupplierEvaluationCriteriaVersion", None)
    SupplierEvaluationCategory = getattr(backend_models, "SupplierEvaluationCategory", None)
    SupplierSpecialRule = getattr(backend_models, "SupplierSpecialRule", None)
    SupplierApprovalHistory = getattr(backend_models, "SupplierApprovalHistory", None)
    SupplierGradeRule = getattr(backend_models, "SupplierGradeRule", None)
    PurchaseBudgetStore = getattr(backend_models, "PurchaseBudgetStore", None)
except (ModuleNotFoundError, ImportError, AttributeError, RuntimeError) as exc:
    SessionLocal = None
    init_db = None
    reset_sqlite_engine_after_write_error = None
    services = None
    InventoryInbound = None
    InventoryDaily = None
    PurchaseDocument = None
    PurchaseBudgetStore = None
    PurchaseOrder = None
    PurchaseRequest = None
    RfqQuote = None
    Supplier = None
    SupplierEvaluation = None
    SupplierEvaluationCriteria = None
    SupplierEvaluationHistory = None
    SupplierEvaluationItem = None
    SupplierEvaluationCriteriaVersion = None
    SupplierEvaluationCategory = None
    SupplierSpecialRule = None
    SupplierApprovalHistory = None
    SupplierGradeRule = None
    PURCHASE_IMPORT_ERROR = str(exc)
else:
    PURCHASE_IMPORT_ERROR = ""


PR_STATUS = ["작성", "상신", "승인", "반려"]
PO_PROGRESS = ["발주대기", "발주완료", "입고진행", "종결", "취소"]
PO_INBOUND = ["입고대기", "부분입고", "입고완료"]
CURRENCIES = ["KRW", "USD"]
SUPPLIER_TRANSACTION_STATUSES = ["거래중", "거래중지", "신규", "휴면", "거래종료"]
SUPPLIER_GRADES = ["S", "A", "B", "C", "D", "미평가"]
SUPPLIER_GRADE_LABELS = {
    "S": "최우수",
    "A": "우수",
    "B": "일반",
    "C": "개선 필요",
    "D": "거래 재검토",
    "미평가": "미평가",
}
EVALUATION_STATUSES = ["임시저장", "평가완료", "승인대기", "최종승인", "반려"]
CONFIRMED_EVALUATION_STATUSES = {"평가완료", "최종승인"}
RATING_OPTIONS = ["매우 우수", "우수", "보통", "미흡", "매우 미흡", "해당 없음"]
RATING_RATIO = {"매우 우수": 1.0, "우수": 0.8, "보통": 0.6, "미흡": 0.4, "매우 미흡": 0.2, "해당 없음": None}
SPECIAL_FLAG_OPTIONS = [
    "중대한 품질사고 발생",
    "반복적인 납기지연",
    "계약 위반",
    "허위서류 제출",
    "안전 또는 법규 위반",
    "장기 미응답",
    "거래중단 검토 대상",
    "우수 협력사 추천 대상",
]
AUTO_WARNING_FLAGS = {
    "중대한 품질사고 발생",
    "반복적인 납기지연",
    "계약 위반",
    "허위서류 제출",
    "안전 또는 법규 위반",
    "거래중단 검토 대상",
}
DEFAULT_EVALUATION_CATEGORIES = [
    ("품질관리", 30.0, ["불량률 관리", "반품 및 교환 발생", "규격 및 사양 준수", "품질 안정성", "품질 개선조치 대응", "품질 관련 인증 보유"]),
    ("납기관리", 25.0, ["납기 준수율", "납기 지연 발생", "긴급 발주 대응", "납품 수량 정확도", "일정 변경 대응", "입고 일정 협조도"]),
    ("가격 및 거래조건", 20.0, ["가격 경쟁력", "견적 정확도", "단가 변동의 합리성", "결제조건", "원가절감 협조", "추가 비용 발생 여부"]),
    ("업무 대응 및 서비스", 15.0, ["문의 응답 속도", "담당자 업무 협조도", "문제 발생 시 대응력", "커뮤니케이션 정확성", "서류 제출 정확도", "클레임 처리 만족도"]),
    ("공급 및 경영 안정성", 10.0, ["공급 안정성", "생산 또는 공급 능력", "재무 및 경영상태", "계약 준수", "법규 및 안전 준수", "지속 거래 가능성"]),
]
DEFAULT_GRADE_RULES = [
    ("S", 95.0, 100.0, "최우수"),
    ("A", 85.0, 94.9999, "우수"),
    ("B", 75.0, 84.9999, "일반"),
    ("C", 65.0, 74.9999, "개선 필요"),
    ("D", 0.0, 64.9999, "거래 재검토"),
]
IMPROVEMENT_STATUSES = ["해당 없음", "개선 요청", "개선 진행 중", "개선 완료", "미이행"]
EVALUATION_CYCLES = ["매월", "분기별", "반기별", "연 1회", "직접 지정"]
DEFAULT_SPECIAL_RULES = {
    "중대한 품질사고 발생": ("C", True, True, True),
    "반복적인 납기지연": ("C", True, True, True),
    "계약 위반": ("D", True, True, True),
    "허위서류 제출": ("D", True, True, True),
    "안전 또는 법규 위반": ("D", True, True, True),
    "장기 미응답": ("", True, True, False),
    "거래중단 검토 대상": ("D", True, True, True),
    "우수 협력사 추천 대상": ("", False, False, False),
}
PRICE_DECIMAL_OPTIONS = [0, 1, 2, 3, 4, 5]
PRICE_DECIMAL_COLUMNS = {"단가", "공급가액", "부가세", "총금액", "배송비", "발주금액", "총 구매비용", "구매금액"}
PR_EDITOR_COLUMNS = [
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
]
PO_EDITOR_COLUMNS = [
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
]
PRICE_HISTORY_COLUMNS = ["날짜", "품목", "업체", "단가", "통화", "수량", "발주금액", "발주번호"]
COMPANY_NAME = "SCM 물류운영포털"
DEFAULT_DELIVERY_PLACE = "로긴 물류센터"
PDF_MIME = "application/pdf"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MALGUN_FONT = "Malgun"
MALGUN_BOLD_FONT = "Malgun-Bold"
PURCHASE_BUDGET_STORE_NAME = "purchase_budgets.json"
BUDGET_SUBTABS = ["연간 예산", "분기 예산", "월별 예산", "예산 사용현황", "예산 승인"]
BUDGET_CATEGORIES = ["원재료", "포장재", "소모품", "설비", "기타"]
BUDGET_FORM_CATEGORIES = ["전체"] + BUDGET_CATEGORIES
BUDGET_STATUSES = ["작성", "승인요청", "승인", "반려", "마감"]
BUDGET_APPROVAL_STATUSES = ["요청", "승인대기", "승인", "반려"]
BUDGET_APPROVED_PO_PROGRESS = {"발주완료", "입고진행", "종결"}


def render_purchase_page() -> None:
    inject_purchase_css()
    st.markdown('<div class="purchase-title">구매관리</div>', unsafe_allow_html=True)
    st.caption("PR → RFQ → PO → 입고 → 재고반영 흐름으로 연결되는 ERP형 구매 업무 화면입니다.")

    if not purchase_available():
        st.error(PURCHASE_IMPORT_ERROR or "구매관리 DB를 초기화하지 못했습니다.")
        return

    pr_tab, rfq_tab, po_tab, supplier_tab, budget_tab, price_tab, kpi_tab, doc_tab = st.tabs(
        ["구매요청(PR)", "견적관리(RFQ)", "발주관리(PO)", "협력사관리", "예산관리", "단가이력", "구매 KPI", "문서/다운로드"]
    )
    with pr_tab:
        render_pr_tab()
    with rfq_tab:
        render_rfq_tab()
    with po_tab:
        render_po_tab()
    with supplier_tab:
        render_supplier_tab()
    with budget_tab:
        render_budget_tab()
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
        if reset_sqlite_engine_after_write_error is not None and reset_sqlite_engine_after_write_error(exc):
            db.close()
            db = SessionLocal()
            try:
                return action(db)
            except Exception as retry_exc:
                db.rollback()
                st.error(f"처리 실패: {retry_exc}")
                return None
        st.error(f"처리 실패: {exc}")
        return None
    finally:
        db.close()


def render_pr_tab() -> None:
    st.markdown('<div class="purchase-section-title">구매요청 등록</div>', unsafe_allow_html=True)
    with st.form("purchase_pr_form", clear_on_submit=True):
        cols = st.columns([1.0, 0.95, 1.75, 1.05], gap="small")
        department = cols[0].text_input("요청부서", placeholder="예: 생산팀")
        item_code = cols[1].text_input("품목코드", placeholder="SKU")
        item_name = cols[2].text_input("품목", placeholder="구매 요청 품목")
        spec = cols[3].text_input("규격", placeholder="규격/사양")
        qty_cols = st.columns([0.72, 0.72, 0.62, 0.95, 0.95, 0.82, 1.35], gap="small")
        quantity = qty_cols[0].number_input("수량", min_value=1, step=1, value=1)
        qty_cols[1].selectbox(
            "단가 소수점",
            PRICE_DECIMAL_OPTIONS,
            index=PRICE_DECIMAL_OPTIONS.index(selected_price_decimal_places()),
            key="purchase_price_decimal_places",
        )
        unit = qty_cols[2].text_input("단위", value="EA")
        request_date = qty_cols[3].date_input("요청일", value=date.today())
        reply_due_date = qty_cols[4].date_input("견적 회신 요청일", value=date.today() + timedelta(days=3))
        approval_status = qty_cols[5].selectbox("승인상태", PR_STATUS, index=0)
        requester = qty_cols[6].text_input("요청자", placeholder="담당자")
        doc_cols = st.columns([0.95, 1.2, 1.0, 1.65, 1.35], gap="small")
        desired_due_date = doc_cols[0].date_input("희망납기일", value=date.today() + timedelta(days=14))
        delivery_place = doc_cols[1].text_input("납품장소", value=DEFAULT_DELIVERY_PLACE)
        approver = doc_cols[2].text_input("승인자", placeholder="승인 담당자")
        request_notes = doc_cols[3].text_input("요청사항", placeholder="포장/시험성적서/배송 조건 등")
        memo = doc_cols[4].text_input("비고", placeholder="요청 사유 또는 특이사항")
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
    st.markdown('<div class="purchase-section-title">구매요청 목록</div>', unsafe_allow_html=True)
    if rows:
        df = pd.DataFrame(rows)
        df.insert(0, "선택", False)
        df = df.reindex(columns=PR_EDITOR_COLUMNS)
    else:
        st.caption("등록된 구매요청이 없어도 목록 구조는 유지됩니다. 재고관리 MRP/발주추천에서도 PR을 생성할 수 있습니다.")
        df = pd.DataFrame(columns=PR_EDITOR_COLUMNS)
    edited = st.data_editor(
        df,
        hide_index=True,
        use_container_width=True,
        column_order=PR_EDITOR_COLUMNS,
        column_config={
            "선택": st.column_config.CheckboxColumn("선택", default=False),
            "승인상태": st.column_config.SelectboxColumn("승인상태", options=PR_STATUS),
        },
        disabled=["구매요청번호", "발주번호"],
        key="purchase_pr_editor",
        height=340,
    )
    action_cols = st.columns([0.95, 0.95, 4.5], gap="small")
    with action_cols[0]:
        if st.button("선택 승인", type="primary", use_container_width=True, key="purchase_pr_approve", disabled=not rows):
            count = with_db(lambda db: approve_selected_pr(db, selected_numbers(edited, "구매요청번호")))
            if count:
                st.success(f"승인 완료: {count}건")
                st.rerun()
    with action_cols[1]:
        if st.button("상태 저장", use_container_width=True, key="purchase_pr_save", disabled=not rows):
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
    supplier_names = with_db(lambda db: [row.supplier_name for row in list_suppliers(db) if row.supplier_name]) or []
    supplier_options = [""] + sorted({clean_text(row.get("업체")) for row in rows if clean_text(row.get("업체"))} | set(supplier_names))

    summary_cols = st.columns(4, gap="small")
    total_amounts = amount_totals_by_currency(rows, "발주금액", "통화")
    waiting = sum(1 for row in rows if row.get("입고상태") != "입고완료")
    delayed = sum(1 for row in rows if row.get("납기예정일") and pd.to_datetime(row["납기예정일"]).date() < date.today() and row.get("입고상태") != "입고완료")
    summary_cols[0].metric("발주건수", f"{len(rows):,}")
    summary_cols[1].metric("발주금액", format_currency_totals(total_amounts))
    summary_cols[2].metric("입고대기", f"{waiting:,}건")
    summary_cols[3].metric("지연건수", f"{delayed:,}건")

    st.markdown('<div class="purchase-section-title">PO 확인 리스트</div>', unsafe_allow_html=True)
    if rows:
        df = pd.DataFrame(rows)
        df.insert(0, "삭제", False)
        df.insert(0, "입고완료처리", False)
        df = df.reindex(columns=PO_EDITOR_COLUMNS)
    else:
        st.caption("아직 생성된 발주는 없지만, PO 생성 후 확인/상태변경/입고처리가 이 목록에서 진행됩니다.")
        df = pd.DataFrame(columns=PO_EDITOR_COLUMNS)
    edited = st.data_editor(
        df,
        hide_index=True,
        use_container_width=True,
        column_order=PO_EDITOR_COLUMNS,
        column_config={
            "입고완료처리": st.column_config.CheckboxColumn("입고완료처리", default=False),
            "삭제": st.column_config.CheckboxColumn("삭제", default=False),
            "업체": st.column_config.SelectboxColumn("업체", options=supplier_options),
            "단가": st.column_config.NumberColumn("단가", min_value=0.0, step=price_step(), format=price_input_format()),
            "통화": st.column_config.SelectboxColumn("통화", options=CURRENCIES),
            "입고상태": st.column_config.SelectboxColumn("입고상태", options=PO_INBOUND),
            "진행상태": st.column_config.SelectboxColumn("진행상태", options=PO_PROGRESS),
            "발주금액": st.column_config.NumberColumn("발주금액", min_value=0.0, format=price_input_format()),
        },
        disabled=["발주번호", "구매요청번호", "발주금액"],
        key="purchase_po_editor",
        height=340,
    )
    cols = st.columns([1.0, 1.0, 1.0, 3.2], gap="small")
    with cols[0]:
        if st.button("PO 상태 저장", use_container_width=True, key="purchase_po_save", disabled=not rows):
            count = with_db(lambda db: save_po_editor(db, edited))
            st.success(f"PO 변경사항 저장 완료: {count or 0}건")
            st.rerun()
    with cols[1]:
        if st.button("선택 입고 완료", type="primary", use_container_width=True, key="purchase_po_receive", disabled=not rows):
            count = with_db(lambda db: receive_selected_po(db, selected_numbers(edited, "발주번호")))
            if count:
                st.success(f"입고 완료 및 창고 현재고 반영: {count}건")
                st.rerun()
    with cols[2]:
        if st.button("선택 삭제", use_container_width=True, key="purchase_po_delete", disabled=not rows):
            count = with_db(lambda db: delete_selected_pos(db, selected_by_flag(edited, "발주번호", "삭제")))
            if count:
                st.success(f"발주 삭제 완료: {count}건")
                st.rerun()
    with cols[3]:
        st.caption("입고 완료 처리 시 창고 입고내역이 생성되고 같은 기준일자의 현재고/가용재고에 자동 반영됩니다.")


def render_supplier_tab() -> None:
    ensure_supplier_evaluation_setup()
    subtab_options = ["협력사 목록", "협력사 평가", "평가 기준 관리", "등급 이력"]
    query_subtab = query_value("supplier_subtab")
    selected_subtab = st.radio(
        "협력사관리 내부 하위 탭",
        subtab_options,
        horizontal=True,
        index=safe_index(subtab_options, query_subtab, 0),
        label_visibility="collapsed",
        key="supplier_subtab_nav",
    )
    st.query_params["supplier_subtab"] = selected_subtab
    if selected_subtab == "협력사 목록":
        render_supplier_list_tab()
    elif selected_subtab == "협력사 평가":
        if not supplier_evaluation_models_available():
            st.error("협력사 평가 DB 모델이 아직 배포되지 않았습니다. backend/models.py 변경사항까지 배포한 뒤 다시 실행해주세요.")
            return
        render_supplier_evaluation_tab()
    elif selected_subtab == "평가 기준 관리":
        if not supplier_evaluation_models_available():
            st.error("평가 기준 관리 DB 모델이 아직 배포되지 않았습니다. backend/models.py 변경사항까지 배포한 뒤 다시 실행해주세요.")
            return
        render_supplier_criteria_tab()
    elif selected_subtab == "등급 이력":
        if not supplier_evaluation_models_available():
            st.error("등급 이력 DB 모델이 아직 배포되지 않았습니다. backend/models.py 변경사항까지 배포한 뒤 다시 실행해주세요.")
            return
        render_supplier_history_tab()


def render_budget_tab() -> None:
    selected_subtab = st.radio(
        "예산관리 내부 하위 탭",
        BUDGET_SUBTABS,
        horizontal=True,
        index=safe_index(BUDGET_SUBTABS, query_value("budget_subtab"), 0),
        label_visibility="collapsed",
        key="budget_subtab_nav",
    )
    st.query_params["budget_subtab"] = selected_subtab
    store = load_purchase_budget_store()
    usage_rows = with_db(lambda db: purchase_budget_usage_rows(db)) or []
    if selected_subtab == "연간 예산":
        render_annual_budget_tab(store, usage_rows)
    elif selected_subtab == "분기 예산":
        render_quarter_budget_tab(store, usage_rows)
    elif selected_subtab == "월별 예산":
        render_monthly_budget_tab(store, usage_rows)
    elif selected_subtab == "예산 사용현황":
        render_budget_usage_tab(store, usage_rows)
    elif selected_subtab == "예산 승인":
        render_budget_approval_tab(store)


def purchase_budget_store_path() -> Path:
    path = Path(__file__).resolve().parents[1] / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path / PURCHASE_BUDGET_STORE_NAME


def empty_purchase_budget_store() -> dict:
    return {"version": 1, "budgets": [], "approvals": []}


def load_purchase_budget_store() -> dict:
    db_payload = load_purchase_budget_store_from_db()
    if db_payload is not None:
        return db_payload

    path = purchase_budget_store_path()
    if not path.exists():
        return empty_purchase_budget_store()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_purchase_budget_store()
    if not isinstance(payload, dict):
        return empty_purchase_budget_store()
    payload.setdefault("version", 1)
    if not isinstance(payload.get("budgets"), list):
        payload["budgets"] = []
    if not isinstance(payload.get("approvals"), list):
        payload["approvals"] = []
    save_purchase_budget_store_to_db(payload)
    return payload


def save_purchase_budget_store(store: dict) -> None:
    payload = empty_purchase_budget_store()
    if isinstance(store, dict):
        payload["budgets"] = [row for row in store.get("budgets", []) if isinstance(row, dict)]
        payload["approvals"] = [row for row in store.get("approvals", []) if isinstance(row, dict)]
    if save_purchase_budget_store_to_db(payload):
        return
    purchase_budget_store_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_purchase_budget_store_from_db() -> dict | None:
    if PurchaseBudgetStore is None or SessionLocal is None:
        return None
    try:
        db = SessionLocal()
        row = db.execute(select(PurchaseBudgetStore).where(PurchaseBudgetStore.store_key == PURCHASE_BUDGET_STORE_NAME)).scalar_one_or_none()
        if row is None or not isinstance(row.payload, dict):
            return None
        payload = empty_purchase_budget_store()
        payload.update(row.payload)
        if not isinstance(payload.get("budgets"), list):
            payload["budgets"] = []
        if not isinstance(payload.get("approvals"), list):
            payload["approvals"] = []
        return payload
    except Exception:
        return None
    finally:
        try:
            db.close()
        except Exception:
            pass


def save_purchase_budget_store_to_db(payload: dict) -> bool:
    if PurchaseBudgetStore is None or SessionLocal is None:
        return False
    db = SessionLocal()
    try:
        row = db.execute(select(PurchaseBudgetStore).where(PurchaseBudgetStore.store_key == PURCHASE_BUDGET_STORE_NAME)).scalar_one_or_none()
        if row is None:
            row = PurchaseBudgetStore(store_key=PURCHASE_BUDGET_STORE_NAME, payload=payload)
            db.add(row)
        else:
            row.payload = payload
            row.updated_at = datetime.utcnow()
        db.commit()
        saved = db.execute(select(PurchaseBudgetStore).where(PurchaseBudgetStore.store_key == PURCHASE_BUDGET_STORE_NAME)).scalar_one_or_none()
        return saved is not None and isinstance(saved.payload, dict)
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()


def budget_record_id(year: int, department: str, category: str) -> str:
    return f"{int(year)}::{clean_text(department) or '공통'}::{clean_text(category) or '전체'}"


def default_budget_record() -> dict:
    today = date.today().isoformat()
    return {
        "id": budget_record_id(date.today().year, "공통", "전체"),
        "year": date.today().year,
        "department": "공통",
        "category": "전체",
        "manager": "",
        "registered_at": today,
        "updated_at": today,
        "status": "작성",
        "details": {
            category: {"budget_amount": 0.0, "memo": ""}
            for category in BUDGET_CATEGORIES
        },
        "quarterly": {f"Q{index}": 0.0 for index in range(1, 5)},
        "monthly": {f"{index}월": 0.0 for index in range(1, 13)},
    }


def normalized_budget_record(record: dict | None) -> dict:
    base = default_budget_record()
    if isinstance(record, dict):
        base.update(record)
    base["year"] = int(to_float(base.get("year")) or date.today().year)
    base["department"] = clean_text(base.get("department")) or "공통"
    base["category"] = clean_text(base.get("category")) or "전체"
    base["status"] = base.get("status") if base.get("status") in BUDGET_STATUSES else "작성"
    base["id"] = base.get("id") or budget_record_id(base["year"], base["department"], base["category"])
    details = base.get("details") if isinstance(base.get("details"), dict) else {}
    base["details"] = {
        category: {
            "budget_amount": max(to_float(details.get(category, {}).get("budget_amount")), 0.0),
            "memo": clean_text(details.get(category, {}).get("memo")),
        }
        for category in BUDGET_CATEGORIES
    }
    quarterly = base.get("quarterly") if isinstance(base.get("quarterly"), dict) else {}
    base["quarterly"] = {f"Q{index}": max(to_float(quarterly.get(f"Q{index}")), 0.0) for index in range(1, 5)}
    monthly = base.get("monthly") if isinstance(base.get("monthly"), dict) else {}
    base["monthly"] = {f"{index}월": max(to_float(monthly.get(f"{index}월")), 0.0) for index in range(1, 13)}
    return base


def budget_options(store: dict) -> list[dict]:
    records = [normalized_budget_record(row) for row in store.get("budgets", []) if isinstance(row, dict)]
    return sorted(records, key=lambda row: (int(row.get("year", 0)), row.get("department", ""), row.get("category", "")), reverse=True)


def budget_option_label(record: dict) -> str:
    return f"{record.get('year')} / {record.get('department')} / {record.get('category')} / {record.get('status')}"


def select_budget_record(store: dict, key: str) -> dict:
    records = budget_options(store)
    labels = ["신규 예산 작성"] + [budget_option_label(row) for row in records]
    selected_label = st.selectbox("예산 선택", labels, key=key)
    if selected_label == "신규 예산 작성":
        return default_budget_record()
    return records[labels.index(selected_label) - 1]


def upsert_budget_record(store: dict, record: dict) -> None:
    record = normalized_budget_record(record)
    record["id"] = budget_record_id(record["year"], record["department"], record["category"])
    budgets = [normalized_budget_record(row) for row in store.get("budgets", []) if isinstance(row, dict)]
    replaced = False
    for index, existing in enumerate(budgets):
        if existing.get("id") == record["id"]:
            budgets[index] = record
            replaced = True
            break
    if not replaced:
        budgets.append(record)
    store["budgets"] = budgets
    save_purchase_budget_store(store)


def render_annual_budget_tab(store: dict, usage_rows: list[dict]) -> None:
    selected = select_budget_record(store, "budget_annual_select")
    record = normalized_budget_record(selected)
    year = int(record["year"])
    department = record["department"]
    category = record["category"]
    usage_by_category = budget_usage_by_category(usage_rows, year, department)

    st.markdown('<div class="purchase-section-title">예산 기본정보</div>', unsafe_allow_html=True)
    info_cols = st.columns([0.7, 1.0, 0.9, 0.9, 0.85, 0.85, 0.8], gap="small")
    year_value = info_cols[0].number_input("예산연도", min_value=2020, max_value=2100, value=year, step=1, key=f"budget_year_{record['id']}")
    department_value = info_cols[1].text_input("부서", value=department, key=f"budget_department_{record['id']}")
    category_value = info_cols[2].selectbox("카테고리", BUDGET_FORM_CATEGORIES, index=safe_index(BUDGET_FORM_CATEGORIES, category, 0), key=f"budget_category_{record['id']}")
    manager_value = info_cols[3].text_input("담당자", value=str(record.get("manager", "")), key=f"budget_manager_{record['id']}")
    registered_at = info_cols[4].date_input("등록일", value=parse_date(record.get("registered_at")) or date.today(), key=f"budget_registered_{record['id']}")
    updated_at = info_cols[5].date_input("수정일", value=parse_date(record.get("updated_at")) or date.today(), key=f"budget_updated_{record['id']}")
    status_value = info_cols[6].selectbox("상태", BUDGET_STATUSES, index=safe_index(BUDGET_STATUSES, record.get("status"), 0), key=f"budget_status_{record['id']}")

    saved_total_budget = budget_record_total(record) if category_value == "전체" else to_float(record["details"].get(category_value, {}).get("budget_amount"))
    saved_total_used = sum(usage_by_category.values()) if category_value == "전체" else usage_by_category.get(category_value, 0.0)
    st.markdown('<div class="purchase-section-title">예산 KPI</div>', unsafe_allow_html=True)
    render_budget_kpi_cards(saved_total_budget, saved_total_used)

    st.markdown('<div class="purchase-section-title">예산 상세</div>', unsafe_allow_html=True)
    details = {}
    for budget_category in BUDGET_CATEGORIES:
        with st.expander(budget_category, expanded=True):
            saved_detail = record["details"].get(budget_category, {})
            auto_used = usage_by_category.get(budget_category, 0.0)
            budget_amount = st.number_input(
                "예산금액",
                min_value=0.0,
                step=100000.0,
                value=max(to_float(saved_detail.get("budget_amount")), 0.0),
                format=price_input_format(),
                key=f"budget_detail_amount_{record['id']}_{budget_category}",
            )
            usage_cols = st.columns([1.0, 1.0, 1.0, 1.5], gap="small")
            remaining = budget_amount - auto_used
            usage_rate = budget_usage_rate(auto_used, budget_amount)
            usage_cols[0].number_input("사용금액", value=float(auto_used), min_value=0.0, format=price_input_format(), disabled=True, key=f"budget_detail_used_{record['id']}_{budget_category}")
            usage_cols[1].number_input("잔여예산", value=float(remaining), format=price_input_format(), disabled=True, key=f"budget_detail_remaining_{record['id']}_{budget_category}")
            usage_cols[2].text_input("사용률", value=f"{usage_rate:.1f}%", disabled=True, key=f"budget_detail_rate_{record['id']}_{budget_category}")
            memo = usage_cols[3].text_input("비고", value=clean_text(saved_detail.get("memo")), key=f"budget_detail_memo_{record['id']}_{budget_category}")
            details[budget_category] = {"budget_amount": float(budget_amount), "memo": memo}

    action_cols = st.columns([1.0, 4.0], gap="small")
    if action_cols[0].button("예산 저장", type="primary", use_container_width=True, key=f"budget_annual_save_{record['id']}"):
        record.update(
            {
                "year": int(year_value),
                "department": department_value,
                "category": category_value,
                "manager": manager_value,
                "registered_at": registered_at.isoformat(),
                "updated_at": updated_at.isoformat(),
                "status": status_value,
                "details": details,
            }
        )
        upsert_budget_record(store, record)
        st.success("연간 예산을 저장했습니다.")
        st.rerun()
    action_cols[1].caption("사용금액은 승인/진행된 PO 금액을 기준으로 자동 반영됩니다.")


def render_quarter_budget_tab(store: dict, usage_rows: list[dict]) -> None:
    record = normalized_budget_record(select_budget_record(store, "budget_quarter_select"))
    st.markdown('<div class="purchase-section-title">분기 예산</div>', unsafe_allow_html=True)
    selected_quarter = st.radio("평가분기", ["Q1", "Q2", "Q3", "Q4"], horizontal=True, key=f"budget_quarter_radio_{record['id']}")
    quarter_usage = budget_usage_amount(usage_rows, record["year"], record["department"], record["category"], quarter=selected_quarter)
    cols = st.columns([1.0, 1.0, 1.0, 1.0], gap="small")
    quarter_budget = cols[0].number_input("계획예산", min_value=0.0, step=100000.0, value=float(record["quarterly"].get(selected_quarter, 0.0)), format=price_input_format(), key=f"quarter_budget_{record['id']}_{selected_quarter}")
    cols[1].number_input("실사용금액", value=float(quarter_usage), min_value=0.0, format=price_input_format(), disabled=True, key=f"quarter_used_{record['id']}_{selected_quarter}")
    cols[2].number_input("잔액", value=float(quarter_budget - quarter_usage), format=price_input_format(), disabled=True, key=f"quarter_remaining_{record['id']}_{selected_quarter}")
    cols[3].text_input("사용률", value=f"{budget_usage_rate(quarter_usage, quarter_budget):.1f}%", disabled=True, key=f"quarter_rate_{record['id']}_{selected_quarter}")
    render_budget_kpi_cards(sum(record["quarterly"].values()) - record["quarterly"].get(selected_quarter, 0) + quarter_budget, budget_usage_amount(usage_rows, record["year"], record["department"], record["category"]))
    if st.button("분기 예산 저장", type="primary", use_container_width=True, key=f"quarter_budget_save_{record['id']}_{selected_quarter}"):
        record["quarterly"][selected_quarter] = float(quarter_budget)
        record["updated_at"] = date.today().isoformat()
        upsert_budget_record(store, record)
        st.success("분기 예산을 저장했습니다.")
        st.rerun()


def render_monthly_budget_tab(store: dict, usage_rows: list[dict]) -> None:
    record = normalized_budget_record(select_budget_record(store, "budget_month_select"))
    st.markdown('<div class="purchase-section-title">월별 예산</div>', unsafe_allow_html=True)
    rows = []
    for month in range(1, 13):
        month_name = f"{month}월"
        planned = float(record["monthly"].get(month_name, 0.0))
        used = budget_usage_amount(usage_rows, record["year"], record["department"], record["category"], month=month)
        rows.append(
            {
                "월": month_name,
                "계획예산": planned,
                "실사용금액": used,
                "잔액": planned - used,
                "사용률": budget_usage_rate(used, planned),
            }
        )
    edited = st.data_editor(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
        column_config={
            "계획예산": st.column_config.NumberColumn("계획예산", min_value=0.0, step=100000.0, format=price_input_format()),
            "실사용금액": st.column_config.NumberColumn("실사용금액", format=price_input_format()),
            "잔액": st.column_config.NumberColumn("잔액", format=price_input_format()),
            "사용률": st.column_config.ProgressColumn("사용률", min_value=0, max_value=100, format="%.1f%%"),
        },
        disabled=["월", "실사용금액", "잔액", "사용률"],
        height=430,
        key=f"budget_month_editor_{record['id']}",
    )
    if st.button("월별 예산 저장", type="primary", use_container_width=True, key=f"budget_month_save_{record['id']}"):
        for row in edited.to_dict("records"):
            record["monthly"][str(row.get("월"))] = max(to_float(row.get("계획예산")), 0.0)
        record["updated_at"] = date.today().isoformat()
        upsert_budget_record(store, record)
        st.success("월별 예산을 저장했습니다.")
        st.rerun()


def render_budget_usage_tab(store: dict, usage_rows: list[dict]) -> None:
    st.markdown('<div class="purchase-section-title">예산 사용현황</div>', unsafe_allow_html=True)
    filter_cols = st.columns([0.72, 0.72, 0.72, 1.0, 1.0, 1.35], gap="small")
    budget_records = budget_options(store)
    year_options = ["전체"] + sorted(
        {str(row.get("year")) for row in budget_records if row.get("year")}
        | {str(row.get("연도")) for row in usage_rows if row.get("연도")},
        reverse=True,
    )
    year_filter = filter_cols[0].selectbox("연도", year_options, key="budget_usage_year_filter")
    quarter_filter = filter_cols[1].selectbox("분기", ["전체", "Q1", "Q2", "Q3", "Q4"], key="budget_usage_quarter_filter")
    month_filter = filter_cols[2].selectbox("월", ["전체"] + [f"{index}월" for index in range(1, 13)], key="budget_usage_month_filter")
    department_options = ["전체"] + sorted(
        {clean_text(row.get("department")) for row in budget_records if clean_text(row.get("department"))}
        | {clean_text(row.get("부서")) for row in usage_rows if clean_text(row.get("부서"))}
    )
    department_filter = filter_cols[3].selectbox("부서", department_options, key="budget_usage_department_filter")
    category_filter = filter_cols[4].selectbox("카테고리", ["전체"] + BUDGET_CATEGORIES, key="budget_usage_category_filter")
    keyword = clean_text(filter_cols[5].text_input("검색", placeholder="부서, 카테고리, 상태", key="budget_usage_keyword"))
    month_number = int(month_filter.replace("월", "")) if month_filter != "전체" else None
    budget_rows = budget_usage_status_rows(store, usage_rows, quarter_filter, month_number)
    filtered = filter_budget_usage_rows(budget_rows, year_filter, quarter_filter, month_filter, department_filter, category_filter, keyword)
    if not filtered:
        st.info("검색 조건에 맞는 예산 사용현황이 없습니다.")
    df = pd.DataFrame(filtered, columns=["연도", "부서", "카테고리", "예산", "사용", "잔액", "사용률", "상태"])
    st.dataframe(style_budget_usage_dataframe(df), hide_index=True, use_container_width=True, height=380)


def render_budget_approval_tab(store: dict) -> None:
    st.markdown('<div class="purchase-section-title">예산 승인</div>', unsafe_allow_html=True)
    approvals = [row for row in store.get("approvals", []) if isinstance(row, dict)]
    with st.form("budget_approval_form", clear_on_submit=True):
        cols = st.columns([1.0, 0.9, 0.9, 0.85, 1.0, 1.5, 0.8], gap="small")
        approval_number = cols[0].text_input("승인번호", value=next_budget_approval_number(approvals), key="budget_approval_number")
        requester = cols[1].text_input("요청자", key="budget_approval_requester")
        approver = cols[2].text_input("승인자", key="budget_approval_approver")
        approval_date = cols[3].date_input("승인일", value=date.today(), key="budget_approval_date")
        change_amount = cols[4].number_input("변경금액", step=100000.0, value=0.0, format=price_input_format(), key="budget_approval_amount")
        change_reason = cols[5].text_input("변경사유", key="budget_approval_reason")
        status = cols[6].selectbox("상태", BUDGET_APPROVAL_STATUSES, key="budget_approval_status")
        if st.form_submit_button("예산 승인 저장", type="primary", use_container_width=True):
            approvals.append(
                {
                    "승인번호": approval_number,
                    "요청자": requester,
                    "승인자": approver,
                    "승인일": approval_date.isoformat(),
                    "변경금액": float(change_amount),
                    "변경사유": change_reason,
                    "상태": status,
                }
            )
            store["approvals"] = approvals
            save_purchase_budget_store(store)
            st.success("예산 승인 정보를 저장했습니다.")
            st.rerun()
    if approvals:
        st.dataframe(center_aligned_dataframe(pd.DataFrame(approvals)), hide_index=True, use_container_width=True, height=260)
    else:
        st.caption("저장된 예산 승인 이력이 없습니다.")


def render_supplier_list_tab() -> None:
    rows = with_db(lambda db: [supplier_to_dict(row) for row in list_suppliers(db)]) or []
    supplier_by_name = {str(row.get("업체명", "")).strip(): row for row in rows if str(row.get("업체명", "")).strip()}
    filter_cols = st.columns([1.2, 0.9, 0.8, 0.8, 0.8, 1.2], gap="small")
    name_keyword = clean_text(filter_cols[0].text_input("협력사명 검색", placeholder="협력사명, 담당자, 연락처, 이메일"))
    code_keyword = clean_text(filter_cols[1].text_input("업체코드 검색", placeholder="예: SUP-0001"))
    status_filter = filter_cols[2].selectbox("거래 상태", ["전체"] + SUPPLIER_TRANSACTION_STATUSES, key="supplier_status_filter")
    grade_filter = filter_cols[3].selectbox("현재 등급", ["전체"] + SUPPLIER_GRADES, key="supplier_grade_filter")
    special_filter = filter_cols[4].selectbox("특별관리 여부", ["전체", "예", "아니오"], key="supplier_special_filter")
    eval_period = filter_cols[5].date_input("최근 평가기간", value=(), key="supplier_eval_period_filter")
    filtered_rows = filter_supplier_rows(rows, name_keyword, code_keyword, status_filter, grade_filter, special_filter, eval_period)
    filtered_supplier_names = [
        str(row.get("업체명", "")).strip()
        for row in filtered_rows
        if str(row.get("업체명", "")).strip()
    ]
    st.caption("RFQ 등록 업체는 자동으로 협력사에 추가되며, 평균납기/평균단가는 발주 이력 기준으로 갱신됩니다.")
    st.caption(f"현재 등록 협력사 {len(rows)}개 / 검색 결과 {len(filtered_rows)}개")

    st.markdown('<div class="purchase-section-title">협력사 등록/수정</div>', unsafe_allow_html=True)
    edit_options = ["신규 협력사 등록"] + filtered_supplier_names
    edit_target = st.selectbox("수정할 협력사", edit_options, key="purchase_supplier_edit_select")
    selected_supplier_name = "" if edit_target == "신규 협력사 등록" else edit_target
    selected_supplier = supplier_by_name.get(selected_supplier_name, {})
    form_key_suffix = selected_supplier_name or "new"
    with st.form("purchase_supplier_form", clear_on_submit=True):
        code_cols = st.columns([0.8, 1.2, 0.9, 0.9], gap="small")
        supplier_code = code_cols[0].text_input(
            "업체코드",
            value=str(selected_supplier.get("업체코드", "")),
            placeholder="자동 생성",
            key=f"purchase_supplier_code_{form_key_suffix}",
        )
        business_number = code_cols[1].text_input(
            "사업자등록번호",
            value=str(selected_supplier.get("사업자등록번호", "")),
            key=f"purchase_supplier_business_{form_key_suffix}",
        )
        transaction_status = code_cols[2].selectbox(
            "거래 상태",
            SUPPLIER_TRANSACTION_STATUSES,
            index=safe_index(SUPPLIER_TRANSACTION_STATUSES, selected_supplier.get("거래 상태"), 0),
            key=f"purchase_supplier_status_{form_key_suffix}",
        )
        next_evaluation_date = code_cols[3].date_input(
            "다음 평가예정일",
            value=parse_date(selected_supplier.get("다음 평가예정일")) or date.today(),
            key=f"purchase_supplier_next_eval_{form_key_suffix}",
        )
        cols = st.columns([1.2, 0.9, 1.0, 1.2], gap="small")
        supplier_name = cols[0].text_input(
            "협력사명",
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
                        supplier_code=supplier_code,
                        supplier_name=supplier_name,
                        business_number=business_number,
                        original_supplier_name=selected_supplier_name,
                        manager=manager,
                        phone=phone,
                        email=email,
                        handled_items=handled_items,
                        moq_terms=moq_terms,
                        transaction_status=transaction_status,
                        next_evaluation_date=next_evaluation_date,
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
    elif not filtered_rows:
        st.info("검색 조건에 맞는 협력사가 없습니다.")
    else:
        render_supplier_table(filtered_rows)
        detail_names = [str(row.get("업체명", "")).strip() for row in filtered_rows if str(row.get("업체명", "")).strip()]
        if detail_names:
            selected_detail = st.selectbox("상세보기 협력사", detail_names, key="supplier_detail_select")
            render_supplier_detail_panel(next((row for row in filtered_rows if row.get("업체명") == selected_detail), {}))
        if filtered_supplier_names:
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


def render_supplier_table(rows: list[dict]) -> None:
    columns = [
        "업체코드",
        "협력사명",
        "사업자등록번호",
        "담당자",
        "연락처",
        "이메일",
        "주요 품목",
        "거래 상태",
        "현재 등급",
        "최근 평가점수",
        "최근 평가일",
        "다음 평가예정일",
        "특별관리 여부",
        "관리",
    ]
    head = "".join(f"<th>{escape(column)}</th>" for column in columns)
    body_rows = []
    for row in rows:
        cells = []
        for column in columns:
            value = row.get(column, "")
            if value is None:
                value = ""
            if column == "현재 등급":
                grade = str(value or "미평가")
                supplier_id = row.get("ID", "")
                href = f"?page=%EA%B5%AC%EB%A7%A4%EA%B4%80%EB%A6%AC&supplier_subtab=%EB%93%B1%EA%B8%89%20%EC%9D%B4%EB%A0%A5&supplier_id={supplier_id}"
                cells.append(
                    f'<td><a class="supplier-grade-badge grade-{escape(grade)}" href="{href}" target="_self">'
                    f"{escape(grade)} {escape(SUPPLIER_GRADE_LABELS.get(grade, grade))}</a></td>"
                )
                continue
            if column == "특별관리 여부":
                reason = str(row.get("특별관리 사유", "") or value or "")
                if str(value).startswith("⚠"):
                    cells.append(f'<td><span class="supplier-warning-badge" title="{escape(reason)}">주의</span></td>')
                else:
                    cells.append("<td>아니오</td>")
                continue
            cells.append(f"<td>{escape(str(value))}</td>")
        body_rows.append(f"<tr>{''.join(cells)}</tr>")
    st.markdown(
        f"""
        <div class="supplier-table-wrap">
            <table class="supplier-table">
                <thead><tr>{head}</tr></thead>
                <tbody>{''.join(body_rows)}</tbody>
            </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_supplier_detail_panel(row: dict) -> None:
    supplier_id = to_int(row.get("ID"))
    if not supplier_id:
        return
    detail = with_db(lambda db: supplier_detail_payload(db, supplier_id)) or {}
    st.markdown('<div class="purchase-section-title">협력사 상세</div>', unsafe_allow_html=True)
    cols = st.columns(4, gap="small")
    cols[0].metric("현재 등급", f"{row.get('현재 등급', '미평가')} {SUPPLIER_GRADE_LABELS.get(row.get('현재 등급'), '')}")
    cols[1].metric("최근 평가점수", str(row.get("최근 평가점수") or "-"))
    cols[2].metric("최근 평가일", str(row.get("최근 평가일") or "-"))
    cols[3].metric("특별관리", str(row.get("특별관리 여부") or "아니오"))
    info_cols = st.columns(2, gap="small")
    with info_cols[0]:
        st.markdown(
            f"""
            <div class="supplier-detail-box">
                <b>기본정보</b><br>
                업체코드: {escape(str(row.get("업체코드", "")))}<br>
                협력사명: {escape(str(row.get("협력사명", row.get("업체명", ""))))}<br>
                사업자등록번호: {escape(str(row.get("사업자등록번호", "")))}<br>
                담당자: {escape(str(row.get("담당자", "")))} / {escape(str(row.get("연락처", "")))}<br>
                주요 품목: {escape(str(row.get("주요 품목", "")))}
            </div>
            """,
            unsafe_allow_html=True,
        )
    with info_cols[1]:
        latest = detail.get("latest")
        if latest:
            st.markdown(
                f"""
                <div class="supplier-detail-box">
                    <b>최근 평가</b><br>
                    품질 {latest.get("quality_score", 0):.1f} / 납기 {latest.get("delivery_score", 0):.1f} /
                    가격 {latest.get("price_score", 0):.1f}<br>
                    대응 {latest.get("service_score", 0):.1f} / 안정성 {latest.get("stability_score", 0):.1f}<br>
                    의견: {escape(str(latest.get("overall_comment", "")) or "-")}<br>
                    특별관리: {escape(", ".join(latest.get("special_flags", [])) or "-")}
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.info("아직 확정 평가 결과가 없습니다.")
    history_rows = detail.get("history", [])
    if history_rows:
        st.dataframe(pd.DataFrame(history_rows), hide_index=True, use_container_width=True, height=180)


def render_supplier_evaluation_tab() -> None:
    suppliers = with_db(lambda db: list_suppliers(db)) or []
    if not suppliers:
        st.info("평가할 협력사를 먼저 등록해주세요.")
        return
    supplier_options = {supplier_label(row): row.id for row in suppliers}
    selected_label = st.selectbox("협력사 선택", list(supplier_options), key="supplier_eval_supplier")
    supplier_id = supplier_options[selected_label]
    supplier = next(row for row in suppliers if row.id == supplier_id)
    criteria = with_db(lambda db: active_supplier_criteria(db)) or default_criteria_payload()
    existing = with_db(lambda db: list_supplier_evaluations(db, supplier_id)) or []
    eval_options = {"새 평가 작성": 0}
    eval_options.update({evaluation_option_label(row): row.id for row in existing})
    selected_eval_label = st.selectbox("불러올 평가", list(eval_options), key=f"supplier_eval_load_{supplier_id}")
    evaluation_id = eval_options[selected_eval_label]
    loaded = with_db(lambda db: supplier_evaluation_detail(db, evaluation_id)) if evaluation_id else {}
    loaded_eval = loaded.get("evaluation", {}) if loaded else {}
    loaded_items = loaded.get("items", {}) if loaded else {}

    default_year = int(loaded_eval.get("evaluation_year") or date.today().year)
    default_quarter = loaded_eval.get("evaluation_quarter") or f"Q{((date.today().month - 1) // 3) + 1}"
    default_start = parse_date(loaded_eval.get("period_start")) or date.today().replace(month=((date.today().month - 1) // 3) * 3 + 1, day=1)
    default_end = parse_date(loaded_eval.get("period_end")) or date.today()
    default_eval_date = parse_date(loaded_eval.get("evaluation_date")) or date.today()
    default_next_date = parse_date(loaded_eval.get("next_evaluation_date")) or calculate_next_evaluation_date(default_eval_date, "분기별")
    key_suffix = evaluation_id or f"new_{supplier_id}"

    st.markdown('<div class="purchase-section-title">평가 기본정보</div>', unsafe_allow_html=True)
    st.caption(
        f"업체코드: {supplier.supplier_code or next_supplier_code(None, supplier.id)} / "
        f"주요 거래품목: {supplier.handled_items or '-'} / 담당자: {supplier.manager or '-'}"
    )
    input_cols = st.columns([0.7, 0.7, 0.78, 0.78, 0.78, 0.9], gap="small")
    eval_year = input_cols[0].number_input("평가연도", min_value=2020, max_value=2100, value=default_year, step=1, key=f"eval_year_{key_suffix}")
    eval_quarter = input_cols[1].selectbox("평가분기", ["Q1", "Q2", "Q3", "Q4"], index=safe_index(["Q1", "Q2", "Q3", "Q4"], default_quarter, 0), key=f"eval_quarter_{key_suffix}")
    period_start = input_cols[2].date_input("평가기간 시작일", value=default_start, key=f"eval_start_{key_suffix}")
    period_end = input_cols[3].date_input("평가기간 종료일", value=default_end, key=f"eval_end_{key_suffix}")
    evaluation_date = input_cols[4].date_input("평가일", value=default_eval_date, key=f"eval_date_{key_suffix}")
    evaluator = input_cols[5].text_input("평가자", value=str(loaded_eval.get("evaluator", "")), key=f"eval_evaluator_{key_suffix}")
    status_cols = st.columns([0.9, 0.9, 0.9, 1.2], gap="small")
    current_status = loaded_eval.get("status") or "임시저장"
    status = status_cols[0].selectbox("평가 상태", EVALUATION_STATUSES, index=safe_index(EVALUATION_STATUSES, current_status, 0), key=f"eval_status_{key_suffix}")
    cycle = status_cols[1].selectbox("평가주기", EVALUATION_CYCLES, index=1, key=f"eval_cycle_{key_suffix}")
    next_evaluation_date = status_cols[2].date_input("다음 평가예정일", value=default_next_date, key=f"eval_next_{key_suffix}")
    completion_placeholder = status_cols[3].empty()

    selected_ratings = {}
    item_comments = {}
    st.markdown('<div class="purchase-section-title">평가 항목</div>', unsafe_allow_html=True)
    for category in criteria:
        with st.expander(f"{category['category_name']} / 기본 배점 {category['category_weight']:.0f}점", expanded=True):
            for item in category["items"]:
                cols = st.columns([1.45, 0.75, 1.35], gap="small")
                key = criteria_item_key(category["category_name"], item["item_name"])
                previous_item = loaded_items.get(key, {})
                cols[0].caption(f"{item['item_name']} · {float(item.get('item_weight') or 0):.2f}점")
                selected_ratings[key] = cols[1].selectbox(
                    "평가값",
                    RATING_OPTIONS,
                    index=safe_index(RATING_OPTIONS, previous_item.get("selected_rating", "보통"), 2),
                    key=f"eval_rating_{key_suffix}_{key}",
                    label_visibility="collapsed",
                )
                item_comments[key] = cols[2].text_input(
                    "의견",
                    value=str(previous_item.get("comment", "")),
                    key=f"eval_comment_{key_suffix}_{key}",
                    label_visibility="collapsed",
                )

    completed_count = sum(1 for value in selected_ratings.values() if value)
    total_count = max(len(selected_ratings), 1)
    completion_placeholder.metric("입력 완료율", f"{completed_count / total_count * 100:.0f}%")

    special_flags = st.multiselect(
        "특별관리 항목",
        SPECIAL_FLAG_OPTIONS,
        default=[flag for flag in loaded_eval.get("special_flags", []) if flag in SPECIAL_FLAG_OPTIONS],
        key=f"supplier_eval_special_flags_{key_suffix}",
    )
    loaded_special_reasons = loaded_eval.get("special_reasons", {}) or {}
    special_reasons = {}
    if special_flags:
        st.caption("특별관리 항목을 선택하면 사유를 반드시 남겨야 합니다.")
        for flag in special_flags:
            special_reasons[flag] = st.text_input(
                f"{flag} 사유",
                value=str(loaded_special_reasons.get(flag, "")),
                key=f"eval_special_reason_{key_suffix}_{criteria_item_key('special', flag)}",
            )

    st.markdown('<div class="purchase-section-title">종합의견 및 개선관리</div>', unsafe_allow_html=True)
    comment_cols = st.columns(2, gap="small")
    overall_comment = comment_cols[0].text_area("종합 평가의견", value=str(loaded_eval.get("overall_comment", "")), key=f"supplier_eval_overall_comment_{key_suffix}")
    excellent_points = comment_cols[1].text_area("우수사항", value=str(loaded_eval.get("excellent_points", "")), key=f"supplier_eval_excellent_{key_suffix}")
    issue_cols = st.columns(2, gap="small")
    problem_points = issue_cols[0].text_area("문제점", value=str(loaded_eval.get("problem_points", "")), key=f"supplier_eval_problem_{key_suffix}")
    improvement_request = issue_cols[1].text_area("개선 요청사항", value=str(loaded_eval.get("improvement_request", "")), key=f"supplier_eval_improvement_request_{key_suffix}")
    improve_cols = st.columns([0.9, 0.9, 0.9, 1.2], gap="small")
    improvement_owner = improve_cols[0].text_input("개선 담당자", value=str(loaded_eval.get("improvement_owner", "")), key=f"eval_improvement_owner_{key_suffix}")
    improvement_due_date = improve_cols[1].date_input(
        "개선 완료 예정일",
        value=parse_date(loaded_eval.get("improvement_due_date")) or (date.today() + timedelta(days=30)),
        key=f"eval_improvement_due_{key_suffix}",
    )
    improvement_status = improve_cols[2].selectbox(
        "개선 진행상태",
        IMPROVEMENT_STATUSES,
        index=safe_index(IMPROVEMENT_STATUSES, loaded_eval.get("improvement_status"), 0),
        key=f"eval_improvement_status_{key_suffix}",
    )
    attachment_ref = improve_cols[3].text_input("첨부파일 또는 관련 문서", value=str(loaded_eval.get("attachment_ref", "")), key=f"eval_attachment_{key_suffix}")
    internal_memo = st.text_area("내부 비고", value=str(loaded_eval.get("internal_memo", "")), key=f"eval_internal_memo_{key_suffix}")
    rejection_reason = st.text_input("반려 사유", value=str(loaded_eval.get("rejection_reason", "")), key=f"eval_rejection_reason_{key_suffix}")

    score_payload = calculate_supplier_scores(criteria, selected_ratings, special_flags)
    summary_cols = st.columns(10, gap="small")
    summary_cols[0].metric("품질", f"{score_payload['quality_score']:.1f}")
    summary_cols[1].metric("납기", f"{score_payload['delivery_score']:.1f}")
    summary_cols[2].metric("가격", f"{score_payload['price_score']:.1f}")
    summary_cols[3].metric("대응", f"{score_payload['service_score']:.1f}")
    summary_cols[4].metric("안정성", f"{score_payload['stability_score']:.1f}")
    summary_cols[5].metric("평가대상 배점", f"{score_payload['applicable_weight']:.1f}")
    summary_cols[6].metric("취득점수", f"{score_payload['earned_score']:.1f}")
    summary_cols[7].metric("환산 총점", f"{score_payload['total_score']:.1f}")
    summary_cols[8].metric("산출 등급", score_payload["base_grade"])
    summary_cols[9].metric("최종 등급", score_payload["final_grade"])
    if score_payload.get("grade_limit_reason"):
        st.warning(f"특별관리 등급 제한 적용: {score_payload['grade_limit_reason']}")

    button_cols = st.columns(6, gap="small")
    actions = [
        ("임시저장", "임시저장"),
        ("평가완료", "평가완료"),
        ("승인요청", "승인대기"),
        ("최종승인", "최종승인"),
        ("반려", "반려"),
        ("취소", "취소"),
    ]
    requested_status = ""
    for index, (label, target_status) in enumerate(actions):
        if button_cols[index].button(label, type="primary" if label in {"임시저장", "평가완료"} else "secondary", use_container_width=True, key=f"supplier_eval_action_{key_suffix}_{label}"):
            requested_status = target_status
    if requested_status == "":
        return
    if requested_status == "취소":
        st.rerun()
    validation_errors = validate_supplier_evaluation_input(
        requested_status,
        evaluator,
        period_start,
        period_end,
        selected_ratings,
        special_flags,
        special_reasons,
        score_payload,
        improvement_request,
        improvement_due_date,
        rejection_reason,
    )
    if validation_errors:
        st.warning("필수값을 확인해주세요: " + ", ".join(validation_errors))
        return
    if cycle != "직접 지정" and requested_status in CONFIRMED_EVALUATION_STATUSES:
        next_evaluation_date = calculate_next_evaluation_date(evaluation_date, cycle)
    saved = with_db(
        lambda db: save_supplier_evaluation(
            db,
            supplier_id=supplier_id,
            evaluation_id=evaluation_id or None,
            evaluation_year=int(eval_year),
            evaluation_quarter=eval_quarter,
            period_start=period_start,
            period_end=period_end,
            evaluation_date=evaluation_date,
            evaluator=evaluator,
            next_evaluation_date=next_evaluation_date,
            status=requested_status,
            overall_comment=overall_comment,
            excellent_points=excellent_points,
            problem_points=problem_points,
            improvement_request=improvement_request,
            improvement_owner=improvement_owner,
            improvement_due_date=improvement_due_date,
            improvement_status=improvement_status,
            attachment_ref=attachment_ref,
            internal_memo=internal_memo,
            rejection_reason=rejection_reason,
            special_flags=special_flags,
            special_reasons=special_reasons,
            criteria=criteria,
            selected_ratings=selected_ratings,
            item_comments=item_comments,
            score_payload=score_payload,
        )
    )
    if saved:
        st.success(f"협력사 평가를 {requested_status} 상태로 저장했습니다.")
        st.rerun()


def render_supplier_criteria_tab() -> None:
    criteria = with_db(lambda db: active_supplier_criteria(db)) or default_criteria_payload()
    st.caption("평가기준 변경은 새 평가부터 적용되며, 과거 평가에는 저장 당시 기준 버전이 유지됩니다.")
    rows = []
    for category_index, category in enumerate(criteria, start=1):
        for item_index, item in enumerate(category["items"], start=1):
            rows.append(
                {
                    "대분류 순서": category.get("category_order") or category_index,
                    "대분류": category["category_name"],
                    "대분류 배점": category["category_weight"],
                    "세부 순서": item.get("item_order") or item_index,
                    "세부 평가항목": item["item_name"],
                    "세부 배점": item["item_weight"],
                    "평가 설명": item.get("item_description", ""),
                    "필수": item.get("is_required", True),
                    "사용": item.get("is_active", True),
                }
            )
    left_cols = st.columns([1.0, 1.0, 1.0], gap="small")
    active_category_total = sum(float(category.get("category_weight") or 0) for category in criteria)
    left_cols[0].metric("사용 대분류 배점 합계", f"{active_category_total:.1f}")
    left_cols[1].metric("기준 버전", current_criteria_version(criteria))
    version_note = left_cols[2].text_input("새 버전 메모", value="", key="supplier_criteria_version_note")
    edited = st.data_editor(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
        num_rows="dynamic",
        key="supplier_criteria_editor",
    )
    st.markdown('<div class="purchase-section-title">등급 기준</div>', unsafe_allow_html=True)
    rules = with_db(lambda db: list_grade_rules(db)) or default_grade_rules_payload()
    rules_df = st.data_editor(pd.DataFrame(rules), hide_index=True, use_container_width=True, key="supplier_grade_rules_editor")
    st.markdown('<div class="purchase-section-title">특별관리 및 자동 등급 제한 설정</div>', unsafe_allow_html=True)
    special_rules = with_db(lambda db: list_special_rules(db)) or default_special_rules_payload()
    special_rules_df = st.data_editor(pd.DataFrame(special_rules), hide_index=True, use_container_width=True, key="supplier_special_rules_editor")
    auto_enabled = st.checkbox("자동 등급 제한 전체 활성화", value=True, key="supplier_auto_downgrade")
    if st.button("평가 기준 저장", type="primary", use_container_width=True, key="supplier_criteria_save"):
        total_weight = edited[edited["사용"].map(truthy)].groupby("대분류", dropna=False)["대분류 배점"].first().astype(float).sum() if not edited.empty else 0
        item_errors = validate_criteria_item_weights(edited)
        if round(total_weight, 4) != 100:
            st.warning(f"대분류 배점 합계가 100%여야 합니다. 현재 {total_weight:.1f}%입니다.")
        elif item_errors:
            st.warning("세부항목 배점을 확인해주세요: " + " / ".join(item_errors))
        elif not validate_grade_rules_df(rules_df):
            st.warning("등급 기준은 0점부터 100점까지 공백/중복 없이 연결되어야 합니다.")
        elif not validate_special_rules_df(special_rules_df):
            st.warning("특별관리 최고등급은 S/A/B/C/D 중 하나이거나 비워두어야 합니다.")
        else:
            with_db(lambda db: save_supplier_criteria(db, edited, rules_df, special_rules_df, auto_enabled, version_note))
            st.success("평가 기준을 저장했습니다.")
            st.rerun()


def render_supplier_history_tab() -> None:
    rows = with_db(lambda db: supplier_history_rows(db)) or []
    suppliers = with_db(lambda db: [supplier_to_dict(row) for row in list_suppliers(db)]) or []
    if not rows:
        st.info("저장된 평가 이력이 없습니다.")
        if suppliers:
            st.metric("미평가 협력사 수", len([row for row in suppliers if row.get("현재 등급") == "미평가"]))
        return
    df = pd.DataFrame(rows)
    today = date.today()
    kpi_cols = st.columns(9, gap="small")
    kpi_cols[0].metric("전체 평가 건수", len(df))
    kpi_cols[1].metric("미평가 협력사", len([row for row in suppliers if row.get("현재 등급") == "미평가"]))
    for index, grade in enumerate(["S", "A", "B", "C", "D"], start=2):
        kpi_cols[index].metric(f"{grade}등급", len([row for row in suppliers if row.get("현재 등급") == grade]))
    kpi_cols[7].metric("특별관리", len([row for row in suppliers if str(row.get("특별관리 여부", "")).startswith("⚠")]))
    overdue_count = len([row for row in suppliers if parse_date(row.get("다음 평가예정일")) and parse_date(row.get("다음 평가예정일")) < today])
    kpi_cols[8].metric("평가기한 초과", overdue_count)
    linked_supplier_id = to_int(query_value("supplier_id"))
    linked_supplier_name = ""
    if linked_supplier_id:
        linked_supplier_name = with_db(lambda db: (db.get(Supplier, linked_supplier_id).supplier_name if db.get(Supplier, linked_supplier_id) else "")) or ""
    cols = st.columns([1.1, 0.8, 0.7, 0.7, 0.7, 0.75, 0.85, 0.8], gap="small")
    name_kw = clean_text(cols[0].text_input("협력사명", value=linked_supplier_name, key="history_name_filter"))
    code_kw = clean_text(cols[1].text_input("업체코드", key="history_code_filter"))
    year_filter = cols[2].selectbox("평가연도", ["전체"] + sorted(df["평가연도"].dropna().astype(str).unique().tolist()), key="history_year_filter")
    quarter_filter = cols[3].selectbox("평가분기", ["전체"] + sorted(df["평가분기"].dropna().astype(str).unique().tolist()), key="history_quarter_filter")
    status_filter = cols[4].selectbox("평가 상태", ["전체"] + EVALUATION_STATUSES, key="history_status_filter")
    grade_filter = cols[5].selectbox("등급", ["전체"] + SUPPLIER_GRADES, key="history_grade_filter")
    special_filter = cols[6].selectbox("특별관리", ["전체", "예", "아니오"], key="history_special_filter")
    evaluator_kw = clean_text(cols[7].text_input("평가자", key="history_evaluator_filter"))
    extra_cols = st.columns([0.9, 0.9, 0.9, 0.9, 1.0], gap="small")
    date_range = extra_cols[0].date_input("평가일 기간", value=(), key="history_date_filter")
    change_filter = extra_cols[1].selectbox("등급 변화", ["전체", "최초평가", "상승", "유지", "하락"], key="history_change_filter")
    version_filter = extra_cols[2].selectbox("평가기준 버전", ["전체"] + sorted(df["평가기준 버전"].dropna().astype(str).unique().tolist()), key="history_version_filter")
    if extra_cols[3].button("필터 초기화", use_container_width=True, key="history_filter_reset"):
        st.query_params["supplier_subtab"] = "등급 이력"
        st.rerun()
    filtered = df.copy()
    if name_kw:
        filtered = filtered[filtered["협력사명"].astype(str).str.contains(name_kw, case=False, na=False)]
    if code_kw:
        filtered = filtered[filtered["업체코드"].astype(str).str.contains(code_kw, case=False, na=False)]
    if year_filter != "전체":
        filtered = filtered[filtered["평가연도"].astype(str) == year_filter]
    if quarter_filter != "전체":
        filtered = filtered[filtered["평가분기"].astype(str) == quarter_filter]
    if status_filter != "전체":
        filtered = filtered[filtered["평가 상태"] == status_filter]
    if grade_filter != "전체":
        filtered = filtered[filtered["현재 등급"] == grade_filter]
    if special_filter != "전체":
        filtered = filtered[filtered["특별관리 여부"] == special_filter]
    if change_filter != "전체":
        filtered = filtered[filtered["등급 변화"] == change_filter]
    if version_filter != "전체":
        filtered = filtered[filtered["평가기준 버전"].astype(str) == version_filter]
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
        filtered = filtered[(pd.to_datetime(filtered["평가일"]) >= pd.Timestamp(start_date)) & (pd.to_datetime(filtered["평가일"]) <= pd.Timestamp(end_date))]
    if evaluator_kw:
        filtered = filtered[filtered["평가자"].astype(str).str.contains(evaluator_kw, case=False, na=False)]
    display_columns = [
        "평가번호", "업체코드", "협력사명", "평가기간", "평가일", "품질점수", "납기점수", "가격점수", "대응점수", "안정성점수",
        "환산 총점", "점수 산출등급", "최종등급", "이전등급", "등급 변화", "특별관리 여부", "평가 상태", "평가자", "평가기준 버전", "상세보기", "수정", "비활성 처리",
    ]
    st.dataframe(filtered[[column for column in display_columns if column in filtered.columns]], hide_index=True, use_container_width=True, height=320)
    export_cols = st.columns([1.0, 1.0, 2.0], gap="small")
    export_cols[0].download_button(
        "엑셀 다운로드",
        data=dataframe_to_excel_bytes(filtered),
        file_name=f"supplier_evaluation_history_{date.today():%Y%m%d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
    selected_eval_id = None
    if not filtered.empty:
        selected_eval_id = export_cols[1].selectbox("상세보기", filtered["평가번호"].astype(int).tolist(), key="history_detail_eval_id")
    if selected_eval_id:
        render_supplier_evaluation_detail(selected_eval_id)
        inactive_cols = st.columns([1.5, 0.9, 2.0], gap="small")
        inactive_reason = inactive_cols[0].text_input("비활성 처리 사유", key=f"history_inactive_reason_{selected_eval_id}")
        inactive_actor = inactive_cols[1].text_input("처리자", key=f"history_inactive_actor_{selected_eval_id}")
        if inactive_cols[2].button("선택 평가 비활성 처리", use_container_width=True, key=f"history_inactive_{selected_eval_id}"):
            if not clean_text(inactive_reason):
                st.warning("비활성 처리 사유를 입력해주세요.")
            else:
                with_db(lambda db: deactivate_supplier_evaluation(db, selected_eval_id, inactive_reason, inactive_actor))
                st.success("평가를 비활성 처리하고 협력사 현재 등급을 다시 계산했습니다.")
                st.rerun()
    supplier_names = sorted(filtered["협력사명"].dropna().astype(str).unique().tolist())
    if supplier_names:
        selected = st.selectbox("차트 협력사", supplier_names, key="history_chart_supplier")
        chart_df = filtered[filtered["협력사명"] == selected].copy()
        chart_df["평가일"] = pd.to_datetime(chart_df["평가일"], errors="coerce")
        st.line_chart(chart_df.dropna(subset=["평가일"]).set_index("평가일")["총점"])
        category_cols = ["품질점수", "납기점수", "가격점수", "대응점수", "안정성점수"]
        st.bar_chart(chart_df.set_index("평가기간")[category_cols])


def render_price_history_tab() -> None:
    item_options = with_db(lambda db: list_price_history_items(db)) or []
    st.markdown('<div class="purchase-section-title">단가이력 조회</div>', unsafe_allow_html=True)
    if item_options:
        selected_item = st.selectbox("품목", item_options, key="purchase_price_item")
        rows = with_db(lambda db: price_history_rows(db, selected_item)) or []
    else:
        st.text_input("품목", value="데이터 없음", disabled=True, key="purchase_price_item_empty")
        rows = []

    df = pd.DataFrame(rows, columns=PRICE_HISTORY_COLUMNS)
    st.markdown('<div class="purchase-section-title">단가 추이</div>', unsafe_allow_html=True)
    if df.empty:
        st.markdown('<div class="purchase-empty-chart">발주 이력이 생성되면 품목별 단가 추이가 표시됩니다.</div>', unsafe_allow_html=True)
    else:
        chart_df = df.copy()
        chart_df["날짜"] = pd.to_datetime(chart_df["날짜"], errors="coerce")
        st.line_chart(chart_df.dropna(subset=["날짜"]).set_index("날짜")["단가"])

    st.markdown('<div class="purchase-section-title">단가이력 상세</div>', unsafe_allow_html=True)
    if df.empty:
        st.caption("아직 표시할 단가 이력이 없습니다.")
    st.dataframe(center_aligned_dataframe(df), hide_index=True, use_container_width=True, height=330)


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


def purchase_budget_usage_rows(db: Session) -> list[dict]:
    pr_by_number = {row.pr_number: row for row in list_purchase_requests(db)}
    rows = []
    for po in list_purchase_orders(db):
        if po.progress_status not in BUDGET_APPROVED_PO_PROGRESS:
            continue
        order_date = po.order_date or date.today()
        pr = pr_by_number.get(po.pr_number)
        rows.append(
            {
                "연도": order_date.year,
                "분기": f"Q{((order_date.month - 1) // 3) + 1}",
                "월": order_date.month,
                "부서": clean_text(getattr(pr, "department", "")) or "공통",
                "카테고리": classify_purchase_budget_category(po.item_name, getattr(pr, "item_code", ""), po.spec),
                "예산": 0.0,
                "사용": max(to_float(po.order_amount), 0.0),
                "발주번호": po.po_number,
                "품목": po.item_name,
                "상태": po.progress_status,
            }
        )
    return rows


def classify_purchase_budget_category(*values) -> str:
    text = " ".join(clean_text(value).lower() for value in values if clean_text(value))
    if any(token in text for token in ["원재료", "원료", "재료", "raw material", "material", "자재", "레진", "수지", "원단", "강판", "알루미늄"]):
        return "원재료"
    if any(token in text for token in ["box", "carton", "label", "tape", "film", "wrap", "박스", "포장", "라벨", "테이프", "필름", "랩핑", "파렛트"]):
        return "포장재"
    if any(token in text for token in ["설비", "장비", "machine", "equipment", "수리", "부품", "금형", "공구"]):
        return "설비"
    if any(token in text for token in ["소모", "장갑", "마스크", "테이프", "청소", "문구", "consumable"]):
        return "소모품"
    return "기타"


def budget_usage_rate(used: float, budget: float) -> float:
    if budget <= 0:
        return 0.0 if used <= 0 else 100.0
    return used / budget * 100


def budget_usage_status(rate: float, remaining: float) -> str:
    if rate > 100 or remaining < 0:
        return "예산 초과"
    if rate >= 100:
        return "소진"
    if rate >= 80:
        return "주의"
    return "정상"


def budget_usage_amount(
    usage_rows: list[dict],
    year: int,
    department: str = "전체",
    category: str = "전체",
    quarter: str = "전체",
    month: int | None = None,
) -> float:
    total = 0.0
    for row in usage_rows:
        if int(row.get("연도") or 0) != int(year):
            continue
        if department not in {"", "전체", "공통"} and row.get("부서") != department:
            continue
        if category not in {"", "전체"} and row.get("카테고리") != category:
            continue
        if quarter not in {"", "전체"} and row.get("분기") != quarter:
            continue
        if month is not None and int(row.get("월") or 0) != int(month):
            continue
        total += to_float(row.get("사용"))
    return total


def budget_usage_by_category(usage_rows: list[dict], year: int, department: str) -> dict[str, float]:
    result = {category: 0.0 for category in BUDGET_CATEGORIES}
    for category in BUDGET_CATEGORIES:
        result[category] = budget_usage_amount(usage_rows, year, department, category)
    return result


def budget_record_total(record: dict) -> float:
    record = normalized_budget_record(record)
    return sum(to_float(detail.get("budget_amount")) for detail in record["details"].values())


def render_budget_kpi_cards(total_budget: float, total_used: float) -> None:
    remaining = total_budget - total_used
    rate = budget_usage_rate(total_used, total_budget)
    cols = st.columns(4, gap="small")
    cols[0].metric("총 예산", format_decimal_display(total_budget))
    cols[1].metric("총 사용금액", format_decimal_display(total_used))
    cols[2].metric("잔여예산", format_decimal_display(remaining))
    cols[3].metric("예산 사용률", f"{rate:.1f}%")
    if rate > 100 or remaining < 0:
        st.error("예산 초과")
    elif rate >= 100:
        st.error("예산 소진")
    elif rate >= 80:
        st.warning("예산 사용률이 80% 이상입니다.")


def budget_usage_status_rows(store: dict, usage_rows: list[dict], quarter: str = "전체", month: int | None = None) -> list[dict]:
    rows = []
    budgets = budget_options(store)
    if budgets:
        for record in budgets:
            record = normalized_budget_record(record)
            categories = BUDGET_CATEGORIES if record.get("category") == "전체" else [record.get("category")]
            for category in categories:
                budget = budget_period_amount(record, category, quarter, month)
                used = budget_usage_amount(usage_rows, record["year"], record["department"], category, quarter=quarter, month=month)
                remaining = budget - used
                rate = budget_usage_rate(used, budget)
                rows.append(
                    {
                        "연도": record["year"],
                        "분기": quarter,
                        "월": f"{month}월" if month else "전체",
                        "부서": record["department"],
                        "카테고리": category,
                        "예산": budget,
                        "사용": used,
                        "잔액": remaining,
                        "사용률": rate,
                        "상태": budget_usage_status(rate, remaining),
                    }
                )
        return rows
    grouped: dict[tuple, float] = {}
    for usage in usage_rows:
        if quarter not in {"", "전체"} and usage.get("분기") != quarter:
            continue
        if month is not None and int(usage.get("월") or 0) != int(month):
            continue
        key = (usage.get("연도"), usage.get("부서"), usage.get("카테고리"))
        grouped[key] = grouped.get(key, 0.0) + to_float(usage.get("사용"))
    for (year, department, category), used in grouped.items():
        rows.append(
            {
                "연도": year,
                "분기": quarter,
                "월": f"{month}월" if month else "전체",
                "부서": department,
                "카테고리": category,
                "예산": 0.0,
                "사용": used,
                "잔액": -used,
                "사용률": 100.0 if used else 0.0,
                "상태": "예산 초과" if used else "정상",
            }
        )
    return rows


def budget_period_amount(record: dict, category: str, quarter: str = "전체", month: int | None = None) -> float:
    record = normalized_budget_record(record)
    annual_amount = to_float(record["details"].get(category, {}).get("budget_amount"))
    if month is not None:
        monthly_total = to_float(record["monthly"].get(f"{month}월"))
        if record.get("category") != "전체" and monthly_total:
            return monthly_total
        return annual_amount / 12
    if quarter not in {"", "전체"}:
        quarterly_total = to_float(record["quarterly"].get(quarter))
        if record.get("category") != "전체" and quarterly_total:
            return quarterly_total
        return annual_amount / 4
    return annual_amount


def filter_budget_usage_rows(
    rows: list[dict],
    year_filter: str,
    quarter_filter: str,
    month_filter: str,
    department_filter: str,
    category_filter: str,
    keyword: str,
) -> list[dict]:
    keyword = clean_text(keyword).lower()
    result = []
    for row in rows:
        if year_filter != "전체" and str(row.get("연도")) != year_filter:
            continue
        if quarter_filter != "전체" and row.get("분기") != quarter_filter:
            continue
        if month_filter != "전체" and row.get("월") != month_filter:
            continue
        if department_filter != "전체" and row.get("부서") != department_filter:
            continue
        if category_filter != "전체" and row.get("카테고리") != category_filter:
            continue
        haystack = " ".join(clean_text(row.get(column)).lower() for column in ["부서", "카테고리", "상태"])
        if keyword and keyword not in haystack:
            continue
        result.append(row)
    return result


def style_budget_usage_dataframe(df: pd.DataFrame):
    if df.empty:
        return df
    display_df = df.copy()
    for column in ["예산", "사용", "잔액"]:
        if column in display_df.columns:
            display_df[column] = display_df[column].map(format_decimal_display)
    if "사용률" in display_df.columns:
        display_df["사용률"] = display_df["사용률"].map(lambda value: f"{to_float(value):.1f}%")

    def style_status(row):
        rate = to_float(str(row.get("사용률", "0")).replace("%", ""))
        remaining = to_float(str(row.get("잔액", "0")).replace(",", ""))
        if row.get("상태") == "예산 초과" or rate >= 100 or remaining < 0:
            color = "background-color: #f0dcdd; color: #7f2d34; font-weight: 850;"
        elif rate >= 80:
            color = "background-color: #f2e4d8; color: #7c451e; font-weight: 850;"
        else:
            color = "background-color: #dcebe2; color: #1f5132; font-weight: 850;"
        return [color if column in {"사용률", "상태"} else "" for column in row.index]

    return display_df.style.apply(style_status, axis=1).set_properties(**{"text-align": "center"}).set_table_styles(
        [{"selector": "th", "props": [("text-align", "center")]}]
    )


def next_budget_approval_number(approvals: list[dict]) -> str:
    today = date.today()
    prefix = f"BA-{today:%Y%m%d}"
    numbers = []
    for row in approvals:
        number = clean_text(row.get("승인번호"))
        if number.startswith(prefix):
            try:
                numbers.append(int(number.rsplit("-", 1)[-1]))
            except ValueError:
                continue
    return f"{prefix}-{max(numbers or [0]) + 1:03d}"


def filter_supplier_rows(
    rows: list[dict],
    name_keyword: str = "",
    code_keyword: str = "",
    status_filter: str = "전체",
    grade_filter: str = "전체",
    special_filter: str = "전체",
    eval_period=(),
) -> list[dict]:
    name_keyword = clean_text(name_keyword).lower()
    code_keyword = clean_text(code_keyword).lower()
    period_values = list(eval_period) if isinstance(eval_period, tuple) else []
    start_date = period_values[0] if len(period_values) >= 1 else None
    end_date = period_values[1] if len(period_values) >= 2 else None
    search_columns = ["업체명", "협력사명", "취급품목", "주요 품목", "MOQ 조건", "결제조건", "담당자", "연락처", "이메일", "비고"]
    filtered = []
    for row in rows:
        if name_keyword and not any(name_keyword in clean_text(row.get(column)).lower() for column in search_columns):
            continue
        if code_keyword and code_keyword not in clean_text(row.get("업체코드")).lower():
            continue
        if status_filter != "전체" and row.get("거래 상태") != status_filter:
            continue
        if grade_filter != "전체" and row.get("현재 등급") != grade_filter:
            continue
        special_yes = str(row.get("특별관리 여부", "")).startswith("⚠")
        if special_filter == "예" and not special_yes:
            continue
        if special_filter == "아니오" and special_yes:
            continue
        latest_date = parse_date(row.get("최근 평가일"))
        if start_date and (latest_date is None or latest_date < start_date):
            continue
        if end_date and (latest_date is None or latest_date > end_date):
            continue
        filtered.append(row)
    return filtered


def render_kpi_tab() -> None:
    payload = with_db(lambda db: purchase_kpi(db)) or {}
    metrics = [
        ("총 구매금액", format_currency_totals(payload.get("total_amounts_by_currency", {}))),
        ("발주건수", f"{int(payload.get('po_count', 0)):,}건"),
        ("평균납기", f"{payload.get('avg_lead_time', 0):.1f}일"),
        ("납기준수율", f"{payload.get('on_time_rate', 0):.1f}%"),
        ("단가절감률", f"{payload.get('saving_rate', 0):.1f}%"),
        ("지연건수", f"{int(payload.get('delayed_count', 0)):,}건"),
    ]
    for start in range(0, len(metrics), 3):
        cols = st.columns(3, gap="small")
        for col, (label, value) in zip(cols, metrics[start : start + 3]):
            col.metric(label, value)

    monthly = pd.DataFrame(payload.get("monthly", []))
    supplier = pd.DataFrame(payload.get("supplier", []))
    chart_cols = st.columns(2, gap="small")
    with chart_cols[0]:
        st.markdown("#### 월별 구매금액")
        if monthly.empty:
            st.markdown('<div class="purchase-empty-chart">발주 데이터가 생성되면 월별 구매금액이 표시됩니다.</div>', unsafe_allow_html=True)
        else:
            st.bar_chart(monthly.set_index("월")["구매금액"])
    with chart_cols[1]:
        st.markdown("#### 협력사별 구매금액")
        if supplier.empty:
            st.markdown('<div class="purchase-empty-chart">발주 데이터가 생성되면 협력사별 구매금액이 표시됩니다.</div>', unsafe_allow_html=True)
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


def list_supplier_evaluations(db: Session, supplier_id: int) -> list[SupplierEvaluation]:
    if SupplierEvaluation is None:
        return []
    return list(
        db.execute(
            select(SupplierEvaluation)
            .where(
                SupplierEvaluation.supplier_id == supplier_id,
                SupplierEvaluation.is_deleted == False,  # noqa: E712
            )
            .order_by(SupplierEvaluation.evaluation_date.desc(), SupplierEvaluation.id.desc())
        ).scalars()
    )


def supplier_evaluation_detail(db: Session, evaluation_id: int) -> dict:
    if SupplierEvaluation is None or SupplierEvaluationItem is None or not evaluation_id:
        return {}
    evaluation = db.get(SupplierEvaluation, evaluation_id)
    if evaluation is None:
        return {}
    items = {}
    for item in db.execute(select(SupplierEvaluationItem).where(SupplierEvaluationItem.evaluation_id == evaluation_id)).scalars():
        items[criteria_item_key(item.category_name, item.item_name)] = {
            "selected_rating": item.selected_rating,
            "comment": item.comment,
            "item_score": item.item_score,
            "item_weight": item.item_weight,
        }
    return {"evaluation": evaluation_to_payload(evaluation), "items": items}


def ensure_supplier_evaluation_setup() -> None:
    with_db(lambda db: seed_supplier_evaluation_defaults(db))


def supplier_evaluation_models_available() -> bool:
    return all(
        model is not None
        for model in (
            SupplierEvaluation,
            SupplierEvaluationCriteria,
            SupplierEvaluationHistory,
            SupplierEvaluationItem,
            SupplierGradeRule,
            SupplierEvaluationCriteriaVersion,
            SupplierEvaluationCategory,
            SupplierSpecialRule,
            SupplierApprovalHistory,
        )
    )


def seed_supplier_evaluation_defaults(db: Session) -> None:
    if not supplier_evaluation_models_available():
        return
    if SupplierEvaluationCriteriaVersion is not None and not db.execute(select(SupplierEvaluationCriteriaVersion.id)).first():
        db.add(
            SupplierEvaluationCriteriaVersion(
                version_code="v1",
                version_name="V1: 최초 평가기준",
                status="사용 중",
                note="기본 협력사 평가 기준",
                is_active=True,
            )
        )
    if not db.execute(select(SupplierEvaluationCriteria.id)).first():
        for category_order, (category_name, category_weight, items) in enumerate(DEFAULT_EVALUATION_CATEGORIES, start=1):
            if SupplierEvaluationCategory is not None:
                db.add(
                    SupplierEvaluationCategory(
                        criteria_version="v1",
                        category_order=category_order,
                        category_name=category_name,
                        category_weight=category_weight,
                        is_active=True,
                    )
                )
            item_weight = round(category_weight / max(len(items), 1), 4)
            for item_order, item_name in enumerate(items, start=1):
                db.add(
                    SupplierEvaluationCriteria(
                        criteria_version="v1",
                        category_order=category_order,
                        category_name=category_name,
                        category_weight=category_weight,
                        item_order=item_order,
                        item_name=item_name,
                        item_weight=item_weight,
                        item_description="",
                        is_required=True,
                        is_active=True,
                    )
                )
    if not db.execute(select(SupplierGradeRule.id)).first():
        for grade, minimum, maximum, label in DEFAULT_GRADE_RULES:
            db.add(SupplierGradeRule(grade=grade, minimum_score=minimum, maximum_score=maximum, label=label, is_active=True))
    if SupplierSpecialRule is not None and not db.execute(select(SupplierSpecialRule.id)).first():
        for flag_name in SPECIAL_FLAG_OPTIONS:
            max_grade, show_warning, reason_required, limit_enabled = DEFAULT_SPECIAL_RULES.get(flag_name, ("", False, False, False))
            db.add(
                SupplierSpecialRule(
                    flag_name=flag_name,
                    is_active=True,
                    show_warning=show_warning,
                    reason_required=reason_required,
                    grade_limit_enabled=limit_enabled,
                    max_grade=max_grade,
                    reflect_to_supplier=True,
                )
            )
    for supplier in db.execute(select(Supplier)).scalars():
        if not supplier.supplier_code:
            supplier.supplier_code = next_supplier_code(db, supplier.id)
        if not supplier.current_grade:
            supplier.current_grade = "미평가"
        if not supplier.transaction_status:
            supplier.transaction_status = "거래중"
    db.commit()


def active_supplier_criteria(db: Session) -> list[dict]:
    if SupplierEvaluationCriteria is None:
        return default_criteria_payload()
    rows = list(
        db.execute(
            select(SupplierEvaluationCriteria)
            .where(SupplierEvaluationCriteria.is_active == True)  # noqa: E712
            .order_by(SupplierEvaluationCriteria.category_order, SupplierEvaluationCriteria.id, SupplierEvaluationCriteria.item_order)
        ).scalars()
    )
    if not rows:
        return default_criteria_payload()
    categories: dict[str, dict] = {}
    for row in rows:
        category = categories.setdefault(
            row.category_name,
            {"category_name": row.category_name, "category_weight": float(row.category_weight or 0), "items": []},
        )
        category["criteria_version"] = row.criteria_version or category.get("criteria_version") or "v1"
        category["category_order"] = int(getattr(row, "category_order", 0) or 0)
        category["items"].append(
            {
                "id": row.id,
                "item_name": row.item_name,
                "item_weight": float(row.item_weight or 0),
                "item_description": getattr(row, "item_description", "") or "",
                "is_required": bool(getattr(row, "is_required", True)),
                "item_order": int(getattr(row, "item_order", 0) or 0),
                "is_active": bool(row.is_active),
            }
        )
    return list(categories.values())


def default_criteria_payload() -> list[dict]:
    return [
        {
            "category_name": category_name,
            "category_weight": category_weight,
            "criteria_version": "v1",
            "category_order": category_index + 1,
            "items": [
                {
                    "id": index + 1,
                    "item_name": item_name,
                    "item_weight": round(category_weight / len(items), 4),
                    "item_description": "",
                    "is_required": True,
                    "item_order": index + 1,
                    "is_active": True,
                }
                for index, item_name in enumerate(items)
            ],
        }
        for category_index, (category_name, category_weight, items) in enumerate(DEFAULT_EVALUATION_CATEGORIES)
    ]


def list_special_rules(db: Session) -> list[dict]:
    if SupplierSpecialRule is None:
        return default_special_rules_payload()
    rows = list(db.execute(select(SupplierSpecialRule).order_by(SupplierSpecialRule.id)).scalars())
    if not rows:
        return default_special_rules_payload()
    return [
        {
            "항목": row.flag_name,
            "사용": bool(row.is_active),
            "경고 표시": bool(row.show_warning),
            "사유 필수": bool(row.reason_required),
            "등급 제한": bool(row.grade_limit_enabled),
            "최고등급": row.max_grade or "",
            "목록 반영": bool(row.reflect_to_supplier),
        }
        for row in rows
    ]


def default_special_rules_payload() -> list[dict]:
    rows = []
    for flag_name in SPECIAL_FLAG_OPTIONS:
        max_grade, show_warning, reason_required, limit_enabled = DEFAULT_SPECIAL_RULES.get(flag_name, ("", False, False, False))
        rows.append(
            {
                "항목": flag_name,
                "사용": True,
                "경고 표시": show_warning,
                "사유 필수": reason_required,
                "등급 제한": limit_enabled,
                "최고등급": max_grade,
                "목록 반영": True,
            }
        )
    return rows


def list_grade_rules(db: Session) -> list[dict]:
    if SupplierGradeRule is None:
        return default_grade_rules_payload()
    rows = list(db.execute(select(SupplierGradeRule).where(SupplierGradeRule.is_active == True).order_by(SupplierGradeRule.minimum_score.desc())).scalars())  # noqa: E712
    if not rows:
        return default_grade_rules_payload()
    return [
        {
            "등급": row.grade,
            "최소점수": float(row.minimum_score or 0),
            "최대점수": float(row.maximum_score or 0),
            "라벨": row.label or SUPPLIER_GRADE_LABELS.get(row.grade, row.grade),
            "사용": bool(row.is_active),
        }
        for row in rows
    ]


def default_grade_rules_payload() -> list[dict]:
    return [{"등급": grade, "최소점수": minimum, "최대점수": maximum, "라벨": label, "사용": True} for grade, minimum, maximum, label in DEFAULT_GRADE_RULES]


def calculate_supplier_scores(criteria: list[dict], selected_ratings: dict[str, str], special_flags: list[str]) -> dict:
    category_scores = {}
    earned_score = 0.0
    applicable_weight = 0.0
    for category in criteria:
        category_score_sum = 0.0
        category_applicable = 0.0
        for item in category.get("items", []):
            item_weight = float(item.get("item_weight") or 0)
            rating = selected_ratings.get(criteria_item_key(category["category_name"], item["item_name"]), "보통")
            ratio = RATING_RATIO.get(rating)
            if ratio is None:
                continue
            item_score = item_weight * ratio
            category_score_sum += item_score
            category_applicable += item_weight
        earned_score += category_score_sum
        applicable_weight += category_applicable
        category_score = category_score_sum / category_applicable * float(category.get("category_weight") or 0) if category_applicable else 0.0
        category_scores[category["category_name"]] = round(category_score, 4)
    total_score = round((earned_score / applicable_weight * 100) if applicable_weight else 0.0, 2)
    base_grade = grade_for_score(total_score)
    downgrade = apply_auto_downgrade(base_grade, special_flags)
    final_grade = downgrade["grade"]
    special_warning = bool(AUTO_WARNING_FLAGS.intersection(set(special_flags)) or final_grade in {"C", "D"})
    return {
        "quality_score": category_scores.get("품질관리", 0.0),
        "delivery_score": category_scores.get("납기관리", 0.0),
        "price_score": category_scores.get("가격 및 거래조건", 0.0),
        "service_score": category_scores.get("업무 대응 및 서비스", 0.0),
        "stability_score": category_scores.get("공급 및 경영 안정성", 0.0),
        "applicable_weight": round(applicable_weight, 4),
        "earned_score": round(earned_score, 4),
        "total_score": total_score,
        "base_grade": base_grade,
        "final_grade": final_grade,
        "grade_limit_reason": downgrade["reason"],
        "special_warning": special_warning,
    }


def save_supplier_evaluation(
    db: Session,
    supplier_id: int,
    evaluation_id: int | None,
    evaluation_year: int,
    evaluation_quarter: str,
    period_start: date,
    period_end: date,
    evaluation_date: date,
    evaluator: str,
    next_evaluation_date: date | None,
    status: str,
    overall_comment: str,
    excellent_points: str,
    problem_points: str,
    improvement_request: str,
    improvement_owner: str,
    improvement_due_date: date | None,
    improvement_status: str,
    attachment_ref: str,
    internal_memo: str,
    rejection_reason: str,
    special_flags: list[str],
    special_reasons: dict[str, str],
    criteria: list[dict],
    selected_ratings: dict[str, str],
    item_comments: dict[str, str],
    score_payload: dict,
) -> SupplierEvaluation:
    if not supplier_evaluation_models_available():
        raise RuntimeError("협력사 평가 DB 모델이 준비되지 않았습니다.")
    supplier = db.get(Supplier, supplier_id)
    if supplier is None:
        raise ValueError("협력사를 찾을 수 없습니다.")
    before_payload = supplier_snapshot(supplier)
    previous_status = ""
    evaluation = db.get(SupplierEvaluation, evaluation_id) if evaluation_id else None
    if evaluation is None:
        evaluation = db.execute(
            select(SupplierEvaluation).where(
                SupplierEvaluation.supplier_id == supplier_id,
                SupplierEvaluation.evaluation_year == evaluation_year,
                SupplierEvaluation.evaluation_quarter == evaluation_quarter,
                SupplierEvaluation.period_start == period_start,
                SupplierEvaluation.period_end == period_end,
                SupplierEvaluation.status == "임시저장",
                SupplierEvaluation.is_deleted == False,  # noqa: E712
            )
        ).scalars().first()
    before_eval_payload = evaluation_to_payload(evaluation) if evaluation else {}
    if evaluation is None:
        evaluation = SupplierEvaluation(supplier_id=supplier_id, previous_grade=supplier.current_grade or "미평가")
        db.add(evaluation)
        action_type = "CREATE"
    else:
        previous_status = evaluation.status
        action_type = "UPDATE"
    evaluation.evaluation_year = evaluation_year
    evaluation.evaluation_quarter = evaluation_quarter
    evaluation.period_start = period_start
    evaluation.period_end = period_end
    evaluation.evaluation_date = evaluation_date
    evaluation.evaluator = clean_text(evaluator)
    evaluation.next_evaluation_date = next_evaluation_date
    evaluation.status = status if status in EVALUATION_STATUSES else "임시저장"
    evaluation.quality_score = score_payload["quality_score"]
    evaluation.delivery_score = score_payload["delivery_score"]
    evaluation.price_score = score_payload["price_score"]
    evaluation.service_score = score_payload["service_score"]
    evaluation.stability_score = score_payload["stability_score"]
    evaluation.applicable_weight = score_payload.get("applicable_weight", 0)
    evaluation.earned_score = score_payload.get("earned_score", 0)
    evaluation.total_score = score_payload["total_score"]
    evaluation.base_grade = score_payload.get("base_grade", score_payload["final_grade"])
    evaluation.final_grade = score_payload["final_grade"]
    evaluation.grade_limit_reason = clean_text(score_payload.get("grade_limit_reason"))
    evaluation.special_flags = json.dumps(special_flags, ensure_ascii=False)
    evaluation.special_reasons = json.dumps(special_reasons, ensure_ascii=False)
    evaluation.special_warning = bool(score_payload["special_warning"])
    evaluation.overall_comment = clean_text(overall_comment)
    evaluation.excellent_points = clean_text(excellent_points)
    evaluation.problem_points = clean_text(problem_points)
    evaluation.improvement_request = clean_text(improvement_request)
    evaluation.improvement_owner = clean_text(improvement_owner)
    evaluation.improvement_due_date = improvement_due_date
    evaluation.improvement_status = improvement_status if improvement_status in IMPROVEMENT_STATUSES else "해당 없음"
    evaluation.attachment_ref = clean_text(attachment_ref)
    evaluation.internal_memo = clean_text(internal_memo)
    evaluation.rejection_reason = clean_text(rejection_reason)
    evaluation.criteria_version = current_criteria_version(criteria)
    evaluation.updated_by = clean_text(evaluator)
    if not evaluation.created_by:
        evaluation.created_by = clean_text(evaluator)
    db.flush()
    db.execute(delete(SupplierEvaluationItem).where(SupplierEvaluationItem.evaluation_id == evaluation.id))
    for category_index, category in enumerate(criteria, start=1):
        for item_index, item in enumerate(category.get("items", []), start=1):
            key = criteria_item_key(category["category_name"], item["item_name"])
            rating = selected_ratings.get(key, "보통")
            ratio = RATING_RATIO.get(rating)
            item_weight = float(item.get("item_weight") or 0)
            db.add(
                SupplierEvaluationItem(
                    evaluation_id=evaluation.id,
                    category_id=category_index,
                    item_id=to_int(item.get("id")) or item_index,
                    category_name=category["category_name"],
                    item_name=item["item_name"],
                    selected_rating=rating,
                    item_score=0.0 if ratio is None else round(item_weight * ratio, 4),
                    item_weight=item_weight,
                    not_applicable=ratio is None,
                    comment=clean_text(item_comments.get(key)),
                )
            )
    if evaluation.status in CONFIRMED_EVALUATION_STATUSES:
        refresh_supplier_current_grade(db, supplier)
    elif previous_status in CONFIRMED_EVALUATION_STATUSES:
        refresh_supplier_current_grade(db, supplier)
    after_payload = supplier_snapshot(supplier)
    db.add(
        SupplierEvaluationHistory(
            evaluation_id=evaluation.id,
            supplier_id=supplier_id,
            action_type=action_type,
            before_data=json.dumps({"supplier": before_payload, "evaluation": before_eval_payload}, ensure_ascii=False, default=str),
            after_data=json.dumps({"supplier": after_payload, "evaluation": evaluation_to_payload(evaluation)}, ensure_ascii=False, default=str),
            changed_by=clean_text(evaluator),
            change_reason="평가 저장",
        )
    )
    if SupplierApprovalHistory is not None and previous_status != evaluation.status:
        db.add(
            SupplierApprovalHistory(
                evaluation_id=evaluation.id,
                supplier_id=supplier_id,
                action_type=evaluation.status,
                status_from=previous_status,
                status_to=evaluation.status,
                reason=clean_text(rejection_reason),
                actor=clean_text(evaluator),
            )
        )
    db.commit()
    db.refresh(evaluation)
    return evaluation


def refresh_supplier_current_grade(db: Session, supplier: Supplier) -> None:
    if SupplierEvaluation is None:
        return
    latest = db.execute(
        select(SupplierEvaluation)
        .where(
            SupplierEvaluation.supplier_id == supplier.id,
            SupplierEvaluation.status.in_(CONFIRMED_EVALUATION_STATUSES),
            SupplierEvaluation.is_deleted == False,  # noqa: E712
        )
        .order_by(SupplierEvaluation.evaluation_date.desc(), SupplierEvaluation.id.desc())
    ).scalars().first()
    if latest is None:
        supplier.current_grade = "미평가"
        supplier.latest_score = 0
        supplier.latest_evaluation_date = None
        supplier.special_management = False
        supplier.special_reason = ""
        return
    flags = parse_json_list(latest.special_flags)
    supplier.current_grade = latest.final_grade or "미평가"
    supplier.rating = supplier.current_grade
    supplier.latest_score = float(latest.total_score or 0)
    supplier.latest_evaluation_date = latest.evaluation_date
    supplier.next_evaluation_date = getattr(latest, "next_evaluation_date", None)
    supplier.special_management = bool(latest.special_warning)
    supplier.special_reason = ", ".join(warning_reasons(flags, latest.final_grade))


def supplier_detail_payload(db: Session, supplier_id: int) -> dict:
    if SupplierEvaluation is None:
        return {"latest": None, "history": []}
    latest = db.execute(
        select(SupplierEvaluation)
        .where(
            SupplierEvaluation.supplier_id == supplier_id,
            SupplierEvaluation.status.in_(CONFIRMED_EVALUATION_STATUSES),
            SupplierEvaluation.is_deleted == False,  # noqa: E712
        )
        .order_by(SupplierEvaluation.evaluation_date.desc(), SupplierEvaluation.id.desc())
    ).scalars().first()
    history = supplier_history_rows(db, supplier_id=supplier_id)
    return {"latest": evaluation_to_payload(latest) if latest else None, "history": history}


def supplier_history_rows(db: Session, supplier_id: int | None = None) -> list[dict]:
    if SupplierEvaluation is None:
        return []
    stmt = select(SupplierEvaluation, Supplier).join(Supplier, Supplier.id == SupplierEvaluation.supplier_id).where(SupplierEvaluation.is_deleted == False)  # noqa: E712
    if supplier_id:
        stmt = stmt.where(SupplierEvaluation.supplier_id == supplier_id)
    rows = list(db.execute(stmt.order_by(SupplierEvaluation.evaluation_date.desc(), SupplierEvaluation.id.desc())).all())
    result = []
    for evaluation, supplier in rows:
        flags = parse_json_list(evaluation.special_flags)
        warning = bool(getattr(evaluation, "special_warning", False))
        result.append(
            {
                "평가번호": evaluation.id,
                "협력사명": supplier.supplier_name,
                "업체코드": supplier.supplier_code or next_supplier_code(None, supplier.id),
                "평가연도": evaluation.evaluation_year,
                "평가분기": evaluation.evaluation_quarter,
                "평가기간": f"{evaluation.period_start or '-'} ~ {evaluation.period_end or '-'}",
                "평가 시작일": evaluation.period_start,
                "평가 종료일": evaluation.period_end,
                "평가일": evaluation.evaluation_date,
                "품질점수": round(evaluation.quality_score or 0, 1),
                "납기점수": round(evaluation.delivery_score or 0, 1),
                "가격점수": round(evaluation.price_score or 0, 1),
                "대응점수": round(evaluation.service_score or 0, 1),
                "안정성점수": round(evaluation.stability_score or 0, 1),
                "평가대상 배점": round(getattr(evaluation, "applicable_weight", 0) or 0, 1),
                "취득점수": round(getattr(evaluation, "earned_score", 0) or 0, 1),
                "환산 총점": round(evaluation.total_score or 0, 1),
                "총점": round(evaluation.total_score or 0, 1),
                "점수 산출등급": getattr(evaluation, "base_grade", "") or evaluation.final_grade or "미평가",
                "최종등급": evaluation.final_grade or "미평가",
                "이전 등급": evaluation.previous_grade or "미평가",
                "이전등급": evaluation.previous_grade or "미평가",
                "현재 등급": evaluation.final_grade or "미평가",
                "등급 변화": grade_change_label(evaluation.previous_grade, evaluation.final_grade),
                "특별관리 여부": "예" if warning else "아니오",
                "특별관리 항목": ", ".join(flags),
                "평가 상태": evaluation.status,
                "평가자": evaluation.evaluator,
                "평가기준 버전": evaluation.criteria_version,
                "상세 평가ID": evaluation.id,
                "상세보기": "상세",
                "수정": "협력사 평가 탭에서 불러오기",
                "비활성 처리": "가능",
            }
        )
    return result


def render_supplier_evaluation_detail(evaluation_id: int) -> None:
    detail = with_db(lambda db: supplier_evaluation_full_detail(db, evaluation_id)) or {}
    evaluation = detail.get("evaluation", {})
    items = detail.get("items", [])
    approvals = detail.get("approvals", [])
    histories = detail.get("histories", [])
    if not evaluation:
        st.info("평가 상세정보를 찾을 수 없습니다.")
        return
    st.markdown('<div class="purchase-section-title">평가 상세보기</div>', unsafe_allow_html=True)
    top_cols = st.columns(6, gap="small")
    top_cols[0].metric("환산 총점", f"{evaluation.get('total_score', 0):.1f}")
    top_cols[1].metric("산출등급", evaluation.get("base_grade", "미평가"))
    top_cols[2].metric("최종등급", evaluation.get("final_grade", "미평가"))
    top_cols[3].metric("평가대상 배점", f"{evaluation.get('applicable_weight', 0):.1f}")
    top_cols[4].metric("취득점수", f"{evaluation.get('earned_score', 0):.1f}")
    top_cols[5].metric("상태", evaluation.get("status", ""))
    if evaluation.get("grade_limit_reason"):
        st.warning(f"등급 제한 사유: {evaluation.get('grade_limit_reason')}")
    info_df = pd.DataFrame(
        [
            {"항목": "협력사", "내용": evaluation.get("supplier_name", "")},
            {"항목": "업체코드", "내용": evaluation.get("supplier_code", "")},
            {"항목": "평가기간", "내용": f"{evaluation.get('period_start')} ~ {evaluation.get('period_end')}"},
            {"항목": "평가일", "내용": evaluation.get("evaluation_date")},
            {"항목": "평가자", "내용": evaluation.get("evaluator", "")},
            {"항목": "평가기준 버전", "내용": evaluation.get("criteria_version", "")},
            {"항목": "특별관리", "내용": ", ".join(evaluation.get("special_flags", [])) or "-"},
            {"항목": "종합 평가의견", "내용": evaluation.get("overall_comment", "")},
            {"항목": "우수사항", "내용": evaluation.get("excellent_points", "")},
            {"항목": "문제점", "내용": evaluation.get("problem_points", "")},
            {"항목": "개선 요청사항", "내용": evaluation.get("improvement_request", "")},
            {"항목": "개선 진행상태", "내용": evaluation.get("improvement_status", "")},
            {"항목": "첨부파일", "내용": evaluation.get("attachment_ref", "")},
        ]
    )
    st.dataframe(info_df, hide_index=True, use_container_width=True, height=260)
    if items:
        st.dataframe(pd.DataFrame(items), hide_index=True, use_container_width=True, height=260)
    history_cols = st.columns(2, gap="small")
    with history_cols[0]:
        st.caption("승인/반려 이력")
        st.dataframe(pd.DataFrame(approvals), hide_index=True, use_container_width=True, height=180)
    with history_cols[1]:
        st.caption("변경 이력")
        st.dataframe(pd.DataFrame(histories), hide_index=True, use_container_width=True, height=180)
    download_cols = st.columns([1.0, 1.0, 2.0], gap="small")
    download_cols[0].download_button(
        "평가표 PDF",
        data=supplier_evaluation_pdf_bytes(detail),
        file_name=f"supplier_evaluation_{evaluation_id}.pdf",
        mime="application/pdf",
        use_container_width=True,
    )
    detail_df = pd.DataFrame(items or [evaluation])
    download_cols[1].download_button(
        "상세 엑셀",
        data=dataframe_to_excel_bytes(detail_df),
        file_name=f"supplier_evaluation_{evaluation_id}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )


def supplier_evaluation_full_detail(db: Session, evaluation_id: int) -> dict:
    if SupplierEvaluation is None or SupplierEvaluationItem is None:
        return {}
    row = db.execute(
        select(SupplierEvaluation, Supplier)
        .join(Supplier, Supplier.id == SupplierEvaluation.supplier_id)
        .where(SupplierEvaluation.id == evaluation_id)
    ).first()
    if not row:
        return {}
    evaluation, supplier = row
    payload = evaluation_to_payload(evaluation)
    payload.update(
        {
            "supplier_name": supplier.supplier_name,
            "supplier_code": supplier.supplier_code or next_supplier_code(None, supplier.id),
            "handled_items": supplier.handled_items,
            "manager": supplier.manager,
        }
    )
    items = [
        {
            "대분류": item.category_name,
            "세부항목": item.item_name,
            "선택값": item.selected_rating,
            "항목 배점": item.item_weight,
            "취득점수": item.item_score,
            "해당 없음": "예" if item.not_applicable else "아니오",
            "평가의견": item.comment,
        }
        for item in db.execute(select(SupplierEvaluationItem).where(SupplierEvaluationItem.evaluation_id == evaluation_id)).scalars()
    ]
    approvals = []
    if SupplierApprovalHistory is not None:
        approvals = [
            {
                "처리": row.action_type,
                "이전상태": row.status_from,
                "변경상태": row.status_to,
                "사유": row.reason,
                "처리자": row.actor,
                "처리일시": row.acted_at,
            }
            for row in db.execute(select(SupplierApprovalHistory).where(SupplierApprovalHistory.evaluation_id == evaluation_id).order_by(SupplierApprovalHistory.acted_at.desc())).scalars()
        ]
    histories = [
        {
            "처리": row.action_type,
            "변경자": row.changed_by,
            "변경사유": getattr(row, "change_reason", ""),
            "변경일시": row.changed_at,
        }
        for row in db.execute(select(SupplierEvaluationHistory).where(SupplierEvaluationHistory.evaluation_id == evaluation_id).order_by(SupplierEvaluationHistory.changed_at.desc())).scalars()
    ]
    return {"evaluation": payload, "items": items, "approvals": approvals, "histories": histories}


def deactivate_supplier_evaluation(db: Session, evaluation_id: int, reason: str, actor: str) -> None:
    if SupplierEvaluation is None:
        return
    evaluation = db.get(SupplierEvaluation, evaluation_id)
    if evaluation is None:
        raise ValueError("평가를 찾을 수 없습니다.")
    supplier = db.get(Supplier, evaluation.supplier_id)
    before_payload = evaluation_to_payload(evaluation)
    evaluation.is_deleted = True
    evaluation.inactive_reason = clean_text(reason)
    evaluation.inactive_at = datetime.utcnow()
    evaluation.updated_by = clean_text(actor)
    if supplier is not None:
        refresh_supplier_current_grade(db, supplier)
    db.add(
        SupplierEvaluationHistory(
            evaluation_id=evaluation.id,
            supplier_id=evaluation.supplier_id,
            action_type="DEACTIVATE",
            before_data=json.dumps(before_payload, ensure_ascii=False, default=str),
            after_data=json.dumps(evaluation_to_payload(evaluation), ensure_ascii=False, default=str),
            changed_by=clean_text(actor),
            change_reason=clean_text(reason),
        )
    )
    db.commit()


def dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="평가이력")
    return output.getvalue()


def supplier_evaluation_pdf_bytes(detail: dict) -> bytes:
    register_pdf_fonts()
    output = BytesIO()
    evaluation = detail.get("evaluation", {})
    items = detail.get("items", [])
    doc = SimpleDocTemplate(output, pagesize=A4, rightMargin=12 * mm, leftMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("EvalTitle", parent=styles["Title"], fontName=MALGUN_BOLD_FONT, fontSize=15, alignment=TA_CENTER)
    normal = ParagraphStyle("EvalNormal", parent=styles["BodyText"], fontName=MALGUN_FONT, fontSize=9, leading=13)
    story = [Paragraph("Supplier Evaluation Report", title), Spacer(1, 6 * mm)]
    summary = [
        ["협력사", evaluation.get("supplier_name", ""), "업체코드", evaluation.get("supplier_code", "")],
        ["평가기간", f"{evaluation.get('period_start')} ~ {evaluation.get('period_end')}", "평가일", str(evaluation.get("evaluation_date", ""))],
        ["총점", f"{evaluation.get('total_score', 0):.1f}", "최종등급", evaluation.get("final_grade", "")],
        ["평가자", evaluation.get("evaluator", ""), "상태", evaluation.get("status", "")],
    ]
    story.append(Table(summary, colWidths=[28 * mm, 55 * mm, 28 * mm, 55 * mm], style=basic_pdf_table_style()))
    story.append(Spacer(1, 5 * mm))
    item_rows = [["대분류", "세부항목", "선택값", "배점", "취득", "의견"]]
    for item in items:
        item_rows.append([item.get("대분류", ""), item.get("세부항목", ""), item.get("선택값", ""), item.get("항목 배점", 0), item.get("취득점수", 0), item.get("평가의견", "")])
    story.append(Table(item_rows, repeatRows=1, colWidths=[28 * mm, 42 * mm, 22 * mm, 18 * mm, 18 * mm, 42 * mm], style=basic_pdf_table_style()))
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(f"종합의견: {evaluation.get('overall_comment', '') or '-'}", normal))
    story.append(Paragraph(f"개선 요청사항: {evaluation.get('improvement_request', '') or '-'}", normal))
    doc.build(story)
    return output.getvalue()


def basic_pdf_table_style() -> TableStyle:
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e7e1d8")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cfc5b7")),
            ("FONTNAME", (0, 0), (-1, -1), MALGUN_FONT),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]
    )


def save_supplier_criteria(
    db: Session,
    edited: pd.DataFrame,
    rules_df: pd.DataFrame,
    special_rules_df: pd.DataFrame,
    auto_enabled: bool,
    version_note: str,
) -> None:
    if SupplierEvaluationCriteria is None or SupplierGradeRule is None:
        return
    version = f"v{datetime.now():%Y%m%d%H%M%S}"
    if SupplierEvaluationCriteriaVersion is not None:
        for row in db.execute(select(SupplierEvaluationCriteriaVersion).where(SupplierEvaluationCriteriaVersion.status == "사용 중")).scalars():
            row.status = "사용 종료"
            row.is_active = False
        db.add(
            SupplierEvaluationCriteriaVersion(
                version_code=version,
                version_name=f"{version}: 평가기준 변경",
                status="사용 중",
                note=clean_text(version_note),
                is_active=True,
            )
        )
    for criterion in db.execute(select(SupplierEvaluationCriteria)).scalars():
        criterion.is_active = False
    if SupplierEvaluationCategory is not None:
        for category in db.execute(select(SupplierEvaluationCategory)).scalars():
            category.is_active = False
        category_rows = (
            edited[edited["사용"].map(truthy)]
            .fillna("")
            .sort_values(["대분류 순서", "대분류"])
            .groupby("대분류", dropna=False)
            .first()
            .reset_index()
        )
        for record in category_rows.to_dict("records"):
            category_name = clean_text(record.get("대분류"))
            if not category_name:
                continue
            db.add(
                SupplierEvaluationCategory(
                    criteria_version=version,
                    category_order=to_int(record.get("대분류 순서")),
                    category_name=category_name,
                    category_weight=max(to_float(record.get("대분류 배점")), 0),
                    is_active=True,
                )
            )
    for record in edited.fillna("").to_dict("records"):
        category_name = clean_text(record.get("대분류"))
        item_name = clean_text(record.get("세부 평가항목"))
        if not category_name or not item_name:
            continue
        db.add(
            SupplierEvaluationCriteria(
                criteria_version=version,
                category_order=to_int(record.get("대분류 순서")),
                category_name=category_name,
                category_weight=max(to_float(record.get("대분류 배점")), 0),
                item_order=to_int(record.get("세부 순서")),
                item_name=item_name,
                item_weight=max(to_float(record.get("세부 배점")), 0),
                item_description=clean_text(record.get("평가 설명")),
                is_required=truthy(record.get("필수")),
                is_active=truthy(record.get("사용")),
            )
        )
    for rule in db.execute(select(SupplierGradeRule)).scalars():
        rule.is_active = False
    for record in rules_df.fillna("").to_dict("records"):
        grade = clean_text(record.get("등급"))
        if not grade:
            continue
        db.add(
            SupplierGradeRule(
                grade=grade,
                minimum_score=max(to_float(record.get("최소점수")), 0),
                maximum_score=min(max(to_float(record.get("최대점수")), 0), 100),
                label=clean_text(record.get("라벨")) or SUPPLIER_GRADE_LABELS.get(grade, grade),
                is_active=truthy(record.get("사용")),
                auto_downgrade_enabled=auto_enabled,
                major_quality_max_grade=special_rule_limit(special_rules_df, "중대한 품질사고 발생", "C"),
                contract_violation_max_grade=special_rule_limit(special_rules_df, "계약 위반", "D"),
            )
        )
    if SupplierSpecialRule is not None:
        existing = {row.flag_name: row for row in db.execute(select(SupplierSpecialRule)).scalars()}
        for record in special_rules_df.fillna("").to_dict("records"):
            flag_name = clean_text(record.get("항목"))
            if not flag_name:
                continue
            row = existing.get(flag_name)
            if row is None:
                row = SupplierSpecialRule(flag_name=flag_name)
                db.add(row)
            row.is_active = truthy(record.get("사용"))
            row.show_warning = truthy(record.get("경고 표시"))
            row.reason_required = truthy(record.get("사유 필수"))
            row.grade_limit_enabled = auto_enabled and truthy(record.get("등급 제한"))
            row.max_grade = clean_text(record.get("최고등급"))
            row.reflect_to_supplier = truthy(record.get("목록 반영"))
    db.commit()


def validate_grade_rules_df(df: pd.DataFrame) -> bool:
    intervals = []
    for record in df.fillna("").to_dict("records"):
        if not truthy(record.get("사용")):
            continue
        minimum = to_float(record.get("최소점수"))
        maximum = to_float(record.get("최대점수"))
        if minimum < 0 or maximum > 100 or minimum >= maximum:
            return False
        intervals.append((minimum, maximum))
    intervals.sort(key=lambda row: row[0])
    if not intervals or abs(intervals[0][0] - 0) > 0.0001 or abs(intervals[-1][1] - 100) > 0.0001:
        return False
    for previous, current in zip(intervals, intervals[1:]):
        if abs(current[0] - previous[1]) > 0.01:
            return False
    return True


def validate_criteria_item_weights(df: pd.DataFrame) -> list[str]:
    if df.empty:
        return ["평가항목 없음"]
    errors = []
    active = df[df["사용"].map(truthy)].fillna("")
    for category_name, group in active.groupby("대분류", dropna=False):
        category_weight = float(group["대분류 배점"].iloc[0] or 0)
        item_total = sum(to_float(value) for value in group["세부 배점"])
        if round(category_weight, 4) != round(item_total, 4):
            errors.append(f"{category_name} 대분류 {category_weight:.1f}점 / 세부 {item_total:.1f}점")
    return errors


def validate_special_rules_df(df: pd.DataFrame) -> bool:
    for record in df.fillna("").to_dict("records"):
        grade = clean_text(record.get("최고등급"))
        if grade and grade not in {"S", "A", "B", "C", "D"}:
            return False
    return True


def special_rule_limit(df: pd.DataFrame, flag_name: str, fallback: str) -> str:
    for record in df.fillna("").to_dict("records"):
        if clean_text(record.get("항목")) == flag_name:
            return clean_text(record.get("최고등급")) or fallback
    return fallback


def grade_for_score(score: float) -> str:
    if SupplierGradeRule is not None and SessionLocal is not None:
        try:
            db = SessionLocal()
            rules = list(db.execute(select(SupplierGradeRule).where(SupplierGradeRule.is_active == True)).scalars())  # noqa: E712
            db.close()
            for rule in rules:
                if float(rule.minimum_score or 0) <= score <= float(rule.maximum_score or 100):
                    return rule.grade
        except Exception:
            pass
    for grade, minimum, maximum, _label in DEFAULT_GRADE_RULES:
        if minimum <= score <= maximum:
            return grade
    return "미평가"


def apply_auto_downgrade(grade: str, special_flags: list[str]) -> dict:
    enabled = True
    limits = {
        "중대한 품질사고 발생": "C",
        "반복적인 납기지연": "C",
        "계약 위반": "D",
        "허위서류 제출": "D",
        "안전 또는 법규 위반": "D",
        "거래중단 검토 대상": "D",
    }
    if SupplierGradeRule is not None and SessionLocal is not None:
        try:
            db = SessionLocal()
            rule = db.execute(select(SupplierGradeRule).where(SupplierGradeRule.is_active == True)).scalars().first()  # noqa: E712
            if rule is not None:
                enabled = bool(rule.auto_downgrade_enabled)
                limits["중대한 품질사고 발생"] = rule.major_quality_max_grade or limits["중대한 품질사고 발생"]
                limits["반복적인 납기지연"] = rule.major_quality_max_grade or limits["반복적인 납기지연"]
                limits["계약 위반"] = rule.contract_violation_max_grade or limits["계약 위반"]
                limits["허위서류 제출"] = rule.contract_violation_max_grade or limits["허위서류 제출"]
                limits["안전 또는 법규 위반"] = rule.contract_violation_max_grade or limits["안전 또는 법규 위반"]
                limits["거래중단 검토 대상"] = rule.contract_violation_max_grade or limits["거래중단 검토 대상"]
            if SupplierSpecialRule is not None:
                for row in db.execute(select(SupplierSpecialRule).where(SupplierSpecialRule.is_active == True)).scalars():  # noqa: E712
                    if row.grade_limit_enabled and row.max_grade:
                        limits[row.flag_name] = row.max_grade
            db.close()
        except Exception:
            pass
    if not enabled:
        return {"grade": grade, "reason": ""}
    grade_order = ["D", "C", "B", "A", "S"]
    max_grade = ""
    limit_flags = []
    for flag in special_flags:
        limit = limits.get(flag, "")
        if not limit:
            continue
        if not max_grade or grade_order.index(limit) < grade_order.index(max_grade):
            max_grade = limit
        limit_flags.append(f"{flag}: 최고 {limit}등급")
    if not max_grade:
        return {"grade": grade, "reason": ""}
    final_grade = grade if grade_order.index(grade) <= grade_order.index(max_grade) else max_grade
    reason = ", ".join(limit_flags) if final_grade != grade else ""
    return {"grade": final_grade, "reason": reason}


def warning_reasons(flags: list[str], grade: str) -> list[str]:
    reasons = [flag for flag in flags if flag in AUTO_WARNING_FLAGS]
    if grade in {"C", "D"}:
        reasons.append(f"{grade}등급")
    return reasons


def parse_json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def parse_json_dict(value: str) -> dict:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def evaluation_to_payload(evaluation: SupplierEvaluation | None) -> dict:
    if evaluation is None:
        return {}
    return {
        "id": evaluation.id,
        "supplier_id": evaluation.supplier_id,
        "evaluation_year": evaluation.evaluation_year,
        "evaluation_quarter": evaluation.evaluation_quarter,
        "period_start": evaluation.period_start,
        "period_end": evaluation.period_end,
        "evaluation_date": evaluation.evaluation_date,
        "next_evaluation_date": getattr(evaluation, "next_evaluation_date", None),
        "evaluator": evaluation.evaluator,
        "status": evaluation.status,
        "quality_score": float(evaluation.quality_score or 0),
        "delivery_score": float(evaluation.delivery_score or 0),
        "price_score": float(evaluation.price_score or 0),
        "service_score": float(evaluation.service_score or 0),
        "stability_score": float(evaluation.stability_score or 0),
        "applicable_weight": float(getattr(evaluation, "applicable_weight", 0) or 0),
        "earned_score": float(getattr(evaluation, "earned_score", 0) or 0),
        "total_score": float(evaluation.total_score or 0),
        "base_grade": getattr(evaluation, "base_grade", "") or evaluation.final_grade,
        "final_grade": evaluation.final_grade,
        "grade_limit_reason": getattr(evaluation, "grade_limit_reason", "") or "",
        "special_flags": parse_json_list(evaluation.special_flags),
        "special_reasons": parse_json_dict(getattr(evaluation, "special_reasons", "")),
        "overall_comment": evaluation.overall_comment,
        "excellent_points": getattr(evaluation, "excellent_points", "") or "",
        "problem_points": getattr(evaluation, "problem_points", "") or "",
        "improvement_request": evaluation.improvement_request,
        "improvement_owner": getattr(evaluation, "improvement_owner", "") or "",
        "improvement_due_date": evaluation.improvement_due_date,
        "improvement_status": getattr(evaluation, "improvement_status", "") or "해당 없음",
        "attachment_ref": getattr(evaluation, "attachment_ref", "") or "",
        "internal_memo": getattr(evaluation, "internal_memo", "") or "",
        "rejection_reason": getattr(evaluation, "rejection_reason", "") or "",
        "criteria_version": evaluation.criteria_version,
    }


def supplier_snapshot(supplier: Supplier) -> dict:
    return {
        "supplier_id": supplier.id,
        "current_grade": supplier.current_grade,
        "latest_score": supplier.latest_score,
        "latest_evaluation_date": supplier.latest_evaluation_date,
        "special_management": supplier.special_management,
        "special_reason": supplier.special_reason,
    }


def current_criteria_version(criteria: list[dict]) -> str:
    versions = [clean_text(category.get("criteria_version")) for category in criteria if clean_text(category.get("criteria_version"))]
    if versions:
        return versions[0]
    ids = [str(item.get("id", "")) for category in criteria for item in category.get("items", []) if item.get("id")]
    return "v1" if not ids else f"criteria-{min(ids)}-{max(ids)}-{len(ids)}"


def criteria_item_key(category_name: str, item_name: str) -> str:
    return f"{clean_text(category_name)}::{clean_text(item_name)}".replace(" ", "_")


def evaluation_option_label(row: SupplierEvaluation) -> str:
    return f"#{row.id} {row.evaluation_year} {row.evaluation_quarter} · {row.evaluation_date} · {row.status} · {row.final_grade}"


def calculate_next_evaluation_date(base_date: date, cycle: str) -> date:
    if cycle == "매월":
        return add_months(base_date, 1)
    if cycle == "반기별":
        return add_months(base_date, 6)
    if cycle == "연 1회":
        return add_months(base_date, 12)
    return add_months(base_date, 3)


def add_months(value: date, months: int) -> date:
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    day = min(value.day, days_in_month(year, month))
    return date(year, month, day)


def days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - timedelta(days=1)).day


def validate_supplier_evaluation_input(
    target_status: str,
    evaluator: str,
    period_start: date,
    period_end: date,
    selected_ratings: dict[str, str],
    special_flags: list[str],
    special_reasons: dict[str, str],
    score_payload: dict,
    improvement_request: str,
    improvement_due_date: date | None,
    rejection_reason: str,
) -> list[str]:
    errors = []
    if not evaluator.strip():
        errors.append("평가자")
    if period_start > period_end:
        errors.append("평가기간")
    if target_status != "임시저장":
        missing_items = [key for key, value in selected_ratings.items() if not value]
        if missing_items:
            errors.append(f"미입력 평가항목 {len(missing_items)}개")
    for flag in special_flags:
        if not clean_text(special_reasons.get(flag)):
            errors.append(f"{flag} 사유")
    if target_status in {"평가완료", "승인대기", "최종승인"} and score_payload.get("final_grade") in {"C", "D"}:
        if not clean_text(improvement_request):
            errors.append("C/D등급 개선 요청사항")
        if improvement_due_date is None:
            errors.append("C/D등급 개선 완료 예정일")
    if target_status == "반려" and not clean_text(rejection_reason):
        errors.append("반려 사유")
    return errors


def grade_change_label(previous: str, current: str) -> str:
    order = {"D": 1, "C": 2, "B": 3, "A": 4, "S": 5}
    previous = previous or "미평가"
    current = current or "미평가"
    if previous == "미평가":
        return "최초평가"
    if order.get(current, 0) > order.get(previous, 0):
        return "상승"
    if order.get(current, 0) < order.get(previous, 0):
        return "하락"
    return "유지"


def supplier_label(row: Supplier) -> str:
    code = row.supplier_code or next_supplier_code(None, row.id)
    return f"{row.supplier_name} ({code})"


def next_supplier_code(db: Session | None, supplier_id: int | None = None) -> str:
    if supplier_id:
        return f"SUP-{int(supplier_id):04d}"
    if db is None:
        return "SUP-0000"
    max_id = db.scalar(select(func.max(Supplier.id))) or 0
    return f"SUP-{int(max_id) + 1:04d}"


def safe_index(options: list, value, default: int = 0) -> int:
    try:
        return options.index(value)
    except ValueError:
        return default


def query_value(name: str) -> str:
    value = st.query_params.get(name)
    if isinstance(value, list):
        return value[0] if value else ""
    return value or ""


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
    applied_sources: set[str] = set()
    for po_number in po_numbers:
        po = db.execute(select(PurchaseOrder).where(PurchaseOrder.po_number == po_number)).scalar_one_or_none()
        if not po or po.inbound_status == "입고완료":
            continue
        pr = db.execute(select(PurchaseRequest).where(PurchaseRequest.pr_number == po.pr_number)).scalar_one_or_none()
        item_code = pr.item_code if pr else ""
        inventory_source = "창고"
        product = None
        if services is not None:
            inventory_source, product = services.find_product_master_any(
                db,
                sku=item_code,
                product_name=po.item_name,
                preferred_source="창고",
            )
        po.inbound_status = "입고완료"
        po.progress_status = "종결"
        po.actual_inbound_date = date.today()
        db.add(
            InventoryInbound(
                source_type=inventory_source,
                inbound_date=date.today(),
                category=product.large_category if product else "",
                product_code=product.sku if product else item_code,
                product_name=product.product_name if product else po.item_name,
                barcode=product.barcode if product else "",
                inbound_qty=int(po.quantity or 0),
                vendor=(product.supplier if product and product.supplier else po.supplier_name),
                inbound_type="PO 입고",
                memo=po.po_number,
                is_applied=False,
            )
        )
        applied_sources.add(inventory_source)
        count += 1
    db.commit()
    if count and services is not None:
        for source_type in applied_sources:
            services.apply_inbound_to_stock(db, source_type, date.today())
            services.sync_purchase_metrics_to_inventory(db, source_type, date.today())
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
    supplier_code: str,
    supplier_name: str,
    business_number: str,
    manager: str,
    phone: str,
    email: str,
    handled_items: str,
    moq_terms: str,
    transaction_status: str,
    next_evaluation_date: date | None,
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
        db.flush()
    elif row.supplier_name != clean_name:
        duplicate = db.execute(select(Supplier).where(Supplier.supplier_name == clean_name)).scalar_one_or_none()
        if duplicate is not None and duplicate.id != row.id:
            raise ValueError(f"{clean_name} 협력사가 이미 등록되어 있습니다.")
        row.supplier_name = clean_name
    row.supplier_code = clean_text(supplier_code) or row.supplier_code or next_supplier_code(db, row.id)
    row.business_number = clean_text(business_number)
    row.manager = clean_text(manager)
    row.phone = clean_text(phone)
    row.email = clean_text(email)
    row.handled_items = normalize_item_list(handled_items)
    row.moq_terms = clean_text(moq_terms)
    row.transaction_status = transaction_status if transaction_status in SUPPLIER_TRANSACTION_STATUSES else "거래중"
    row.next_evaluation_date = next_evaluation_date
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
    has_history = False
    if SupplierEvaluation is not None:
        has_history = db.execute(select(SupplierEvaluation.id).where(SupplierEvaluation.supplier_id == row.id)).first() is not None
    if has_history:
        row.transaction_status = "거래종료"
        row.memo = append_unique_items(row.memo, "평가이력 보존으로 삭제 대신 거래종료 처리")
    else:
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
        ((row.actual_inbound_date or row.expected_inbound_date) - row.order_date).days
        for row in rows
        if (row.actual_inbound_date or row.expected_inbound_date) and row.order_date
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
    grade = getattr(row, "current_grade", "") or getattr(row, "rating", "") or "미평가"
    latest_raw_score = getattr(row, "latest_score", 0)
    latest_score = "" if not latest_raw_score else f"{float(latest_raw_score):.1f}"
    special_reason = getattr(row, "special_reason", "") or ""
    supplier_id = getattr(row, "id", 0)
    return {
        "삭제": False,
        "ID": supplier_id,
        "업체코드": getattr(row, "supplier_code", "") or next_supplier_code(None, supplier_id),
        "업체명": getattr(row, "supplier_name", ""),
        "협력사명": getattr(row, "supplier_name", ""),
        "사업자등록번호": getattr(row, "business_number", ""),
        "취급품목": getattr(row, "handled_items", ""),
        "주요 품목": getattr(row, "handled_items", ""),
        "MOQ 조건": getattr(row, "moq_terms", ""),
        "담당자": getattr(row, "manager", ""),
        "연락처": getattr(row, "phone", ""),
        "이메일": getattr(row, "email", ""),
        "거래 상태": getattr(row, "transaction_status", "거래중"),
        "현재 등급": grade,
        "최근 평가점수": latest_score,
        "최근 평가일": getattr(row, "latest_evaluation_date", None),
        "다음 평가예정일": getattr(row, "next_evaluation_date", None),
        "특별관리 여부": f"⚠ {special_reason}" if getattr(row, "special_management", False) else "아니오",
        "특별관리 사유": special_reason,
        "관리": "상세/이력",
        "평균납기": getattr(row, "avg_lead_time_days", 0),
        "평균단가": format_compact_price(getattr(row, "avg_unit_price", 0), getattr(row, "avg_unit_price_currency", "KRW")),
        "결제조건": getattr(row, "payment_terms", ""),
        "비고": getattr(row, "memo", ""),
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
    global MALGUN_FONT, MALGUN_BOLD_FONT
    if MALGUN_FONT in pdfmetrics.getRegisteredFontNames():
        return
    font_dirs = [
        Path(r"C:\Windows\Fonts"),
        Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts",
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path(__file__).resolve().parents[1] / "assets" / "fonts",
    ]
    candidates = [
        ("Malgun", "Malgun-Bold", "malgun.ttf", "malgunbd.ttf"),
        ("NanumGothic", "NanumGothic-Bold", "NanumGothic.ttf", "NanumGothicBold.ttf"),
        ("NanumGothic", "NanumGothic-Bold", "NanumGothic-Regular.ttf", "NanumGothic-Bold.ttf"),
        ("NotoSansKR", "NotoSansKR-Bold", "NotoSansKR-Regular.ttf", "NotoSansKR-Bold.ttf"),
        ("NotoSansCJKkr", "NotoSansCJKkr-Bold", "NotoSansCJKkr-Regular.otf", "NotoSansCJKkr-Bold.otf"),
    ]
    for regular_name, bold_name, regular_file, bold_file in candidates:
        for font_dir in font_dirs:
            regular_path = next(font_dir.rglob(regular_file), None) if font_dir.exists() else None
            if regular_path is None:
                continue
            bold_path = next(font_dir.rglob(bold_file), None) if font_dir.exists() else regular_path
            try:
                if regular_name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(regular_name, str(regular_path)))
                if bold_name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(bold_name, str(bold_path or regular_path)))
                MALGUN_FONT = regular_name
                MALGUN_BOLD_FONT = bold_name
                return
            except Exception:
                continue
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("HYGothic-Medium"))
        MALGUN_FONT = "HYGothic-Medium"
        MALGUN_BOLD_FONT = "HYGothic-Medium"
    except Exception:
        MALGUN_FONT = "Helvetica"
        MALGUN_BOLD_FONT = "Helvetica-Bold"


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
            color: #24303c;
            font-size: 1.45rem;
            font-weight: 800;
            margin: 0.2rem 0 0.1rem;
        }
        .purchase-section-title {
            color: #3f596f;
            font-size: 1.02rem;
            font-weight: 800;
            margin: 1rem 0 0.45rem;
        }
        [data-testid="stTabs"] [data-baseweb="tab-list"] {
            background: transparent !important;
            border-bottom: 1px solid #d7dde2 !important;
            gap: 0.25rem !important;
        }
        [data-testid="stTabs"] [data-baseweb="tab"] {
            background: transparent !important;
            border-radius: 7px 7px 0 0 !important;
            color: #52606e !important;
            font-weight: 720 !important;
            padding: 0.55rem 0.75rem !important;
        }
        [data-testid="stTabs"] [data-baseweb="tab"]:hover {
            background: #e9eef1 !important;
            color: #24303c !important;
        }
        [data-testid="stTabs"] [aria-selected="true"] {
            background: #e3e9ed !important;
            color: #3f596f !important;
            font-weight: 800 !important;
        }
        [data-testid="stTabs"] [data-baseweb="tab-highlight"] {
            background-color: #536d84 !important;
        }
        div[role="radiogroup"][aria-label="협력사관리 내부 하위 탭"],
        div[role="radiogroup"][aria-label="예산관리 내부 하위 탭"] {
            display: flex;
            flex-wrap: nowrap;
            gap: 0.25rem;
            overflow-x: auto;
            background: #f1eee8;
            border: 1px solid #d7d0c5;
            border-radius: 8px;
            padding: 0.25rem;
            margin: 0.4rem 0 0.8rem;
        }
        div[role="radiogroup"][aria-label="협력사관리 내부 하위 탭"] label,
        div[role="radiogroup"][aria-label="예산관리 내부 하위 탭"] label {
            min-width: max-content;
            background: transparent !important;
            border-radius: 6px;
            padding: 0.22rem 0.55rem;
        }
        div[role="radiogroup"][aria-label="협력사관리 내부 하위 탭"] label:has(input:checked),
        div[role="radiogroup"][aria-label="예산관리 내부 하위 탭"] label:has(input:checked) {
            background: #e7e1d8 !important;
            color: #304257 !important;
            font-weight: 800 !important;
        }
        [data-testid="stTabs"] [data-testid="stTabs"] [data-baseweb="tab-list"] {
            margin-top: 0.2rem !important;
            background: #f1eee8 !important;
            border: 1px solid #d7d0c5 !important;
            border-radius: 8px !important;
            padding: 0.22rem !important;
            overflow-x: auto !important;
            flex-wrap: nowrap !important;
        }
        [data-testid="stTabs"] [data-testid="stTabs"] [data-baseweb="tab"] {
            font-size: 0.88rem !important;
            padding: 0.42rem 0.7rem !important;
            color: #66727d !important;
            border-radius: 6px !important;
            white-space: nowrap !important;
        }
        [data-testid="stTabs"] [data-testid="stTabs"] [aria-selected="true"] {
            background: #e7e1d8 !important;
            color: #304257 !important;
        }
        .supplier-table-wrap {
            width: 100%;
            max-height: 330px;
            overflow: auto;
            background: #f1eee8;
            border: 1px solid #d7d0c5;
            border-radius: 8px;
            box-shadow: 0 8px 22px rgba(34, 45, 56, 0.055);
        }
        .supplier-table {
            width: 100%;
            min-width: 980px;
            border-collapse: collapse;
            table-layout: fixed;
            color: #24303c;
            font-size: 0.84rem;
        }
        .supplier-table th,
        .supplier-table td {
            border-bottom: 1px solid #d7d0c5;
            padding: 0.66rem 0.72rem;
            text-align: center;
            vertical-align: middle;
            word-break: keep-all;
            overflow-wrap: anywhere;
            background: #f8f5ef;
        }
        .supplier-table th {
            position: sticky;
            top: 0;
            z-index: 1;
            background: #e7e1d8;
            color: #304257;
            font-weight: 800;
        }
        .supplier-table td {
            color: #24303c;
            font-weight: 650;
        }
        .supplier-table tbody tr:last-child td {
            border-bottom: 0;
        }
        .supplier-grade-badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 76px;
            border-radius: 999px;
            padding: 0.22rem 0.55rem;
            border: 1px solid #cfc5b7;
            background: #e7e1d8;
            color: #304257 !important;
            font-weight: 850;
            text-decoration: none !important;
        }
        .supplier-grade-badge.grade-S { background: #dcebe2; border-color: #9dbda9; color: #1f5132 !important; }
        .supplier-grade-badge.grade-A { background: #e3ebf4; border-color: #9fb4cf; color: #284b72 !important; }
        .supplier-grade-badge.grade-B { background: #eee9d7; border-color: #cbbd80; color: #6a5719 !important; }
        .supplier-grade-badge.grade-C { background: #f2e4d8; border-color: #d5aa84; color: #7c451e !important; }
        .supplier-grade-badge.grade-D { background: #f0dcdd; border-color: #c88c91; color: #7f2d34 !important; }
        .supplier-grade-badge.grade-미평가 { background: #e7e1d8; border-color: #cfc5b7; color: #5f6975 !important; }
        .supplier-warning-badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 52px;
            border-radius: 999px;
            padding: 0.2rem 0.5rem;
            border: 1px solid #c88c91;
            background: #f0dcdd;
            color: #7f2d34;
            font-weight: 850;
            cursor: help;
        }
        .supplier-detail-box {
            min-height: 138px;
            background: #f1eee8;
            border: 1px solid #d7d0c5;
            border-radius: 8px;
            padding: 0.9rem 1rem;
            color: #24303c;
            line-height: 1.65;
            font-weight: 650;
        }
        [data-testid="stForm"],
        [data-testid="stDataFrame"],
        [data-testid="stDataEditor"],
        [data-testid="stTable"] table {
            background: #f1eee8 !important;
            border: 1px solid #d7d0c5 !important;
            border-radius: 8px !important;
            box-shadow: 0 8px 22px rgba(34, 45, 56, 0.055) !important;
        }
        [data-testid="stForm"] {
            padding: 1rem !important;
        }
        [data-testid="stForm"] [data-testid="stVerticalBlock"],
        [data-testid="stForm"] [data-testid="stHorizontalBlock"] {
            gap: 0.55rem !important;
        }
        [data-testid="stTextInput"] input,
        [data-testid="stNumberInput"] input,
        [data-testid="stDateInput"] input,
        [data-testid="stSelectbox"] [data-baseweb="select"] > div,
        [data-testid="stMultiSelect"] [data-baseweb="select"] > div,
        [data-testid="stFileUploaderDropzone"] {
            background: #f8f5ef !important;
            border-color: #cec5b7 !important;
            color: #24303c !important;
            -webkit-text-fill-color: #24303c !important;
            min-height: 38px !important;
        }
        [data-testid="stTextInput"] input::placeholder,
        [data-testid="stNumberInput"] input::placeholder {
            color: #7a8590 !important;
            -webkit-text-fill-color: #7a8590 !important;
            opacity: 1 !important;
        }
        [data-testid="stSelectbox"] [data-baseweb="select"] span,
        [data-testid="stMultiSelect"] [data-baseweb="select"] span,
        [data-testid="stDateInput"] input,
        [data-testid="stFileUploaderDropzone"] * {
            color: #24303c !important;
            -webkit-text-fill-color: #24303c !important;
        }
        [data-testid="stWidgetLabel"] p,
        [data-testid="stCaptionContainer"],
        .stCaptionContainer {
            color: #5f6975 !important;
            -webkit-text-fill-color: #5f6975 !important;
            font-weight: 650 !important;
        }
        .stButton > button,
        .stDownloadButton > button,
        .stFormSubmitButton > button {
            min-height: 38px !important;
            border-radius: 999px !important;
            background: #e7e1d8 !important;
            border-color: #cfc5b7 !important;
            color: #304257 !important;
            -webkit-text-fill-color: #304257 !important;
            font-weight: 800 !important;
            filter: saturate(0.86) brightness(0.98);
            box-shadow: none !important;
            white-space: normal !important;
        }
        .stButton > button *,
        .stDownloadButton > button *,
        .stFormSubmitButton > button * {
            color: inherit !important;
            -webkit-text-fill-color: inherit !important;
            opacity: 1 !important;
        }
        .stButton > button[kind="primary"],
        .stFormSubmitButton > button[kind="primary"],
        .stDownloadButton > button[kind="primary"] {
            background: #516d86 !important;
            border-color: #435d73 !important;
            color: #faf8f5 !important;
            -webkit-text-fill-color: #faf8f5 !important;
        }
        .stButton > button:disabled,
        .stFormSubmitButton > button:disabled,
        .stDownloadButton > button:disabled {
            background: #dfd9cf !important;
            border-color: #cbc1b4 !important;
            color: #8a8277 !important;
            -webkit-text-fill-color: #8a8277 !important;
            opacity: 1 !important;
        }
        [data-testid="stMetric"] {
            background: #f1eee8 !important;
            border: 1px solid #d7d0c5 !important;
            border-radius: 8px !important;
            padding: 0.72rem 0.85rem !important;
            min-height: 82px !important;
            box-shadow: 0 6px 16px rgba(72, 63, 52, 0.04) !important;
        }
        [data-testid="stMetric"] * {
            color: #24303c !important;
            -webkit-text-fill-color: #24303c !important;
        }
        [data-testid="stDataFrame"] *,
        [data-testid="stDataEditor"] *,
        [data-testid="stTable"] * {
            color: #24303c !important;
            -webkit-text-fill-color: #24303c !important;
        }
        [data-testid="stDataFrame"] canvas,
        [data-testid="stDataEditor"] canvas {
            background: #f4f1eb !important;
        }
        .purchase-empty-chart {
            min-height: 220px;
            border: 1px solid #d7d0c5;
            border-radius: 8px;
            background: #f1eee8;
            color: #6a7480;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            box-shadow: inset 0 0 0 1px rgba(255,255,255,0.32);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
