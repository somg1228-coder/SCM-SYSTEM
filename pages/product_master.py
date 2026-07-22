from __future__ import annotations

from io import BytesIO

import pandas as pd
import streamlit as st

try:
    from backend.database import SessionLocal, init_db
    from backend import services
except (ModuleNotFoundError, RuntimeError) as exc:
    SessionLocal = None
    init_db = None
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
    "사용여부",
    "비고",
]

OFFLINE_MASTER_COLUMNS = [
    "미사용 처리",
    "카테고리",
    "상품명",
    "88바코드",
    "리드타임",
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
                offline_master_template_df()
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
                offline_master_to_editor(rows, keyword, active_filter)
                if uses_simple_master_form(source_type)
                else master_to_editor(rows)
            ).drop(columns=["미사용 처리"], errors="ignore")
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
            offline_master_to_editor(rows, keyword, active_filter)
            if uses_simple_master_form(source_type)
            else master_to_editor(rows)
        )
        editor_buffer_key = f"product_master_{key}_editor_buffer"
        if editor_buffer_key not in st.session_state:
            st.session_state[editor_buffer_key] = df
        editor_df = st.session_state[editor_buffer_key]
        with st.form(key=f"product_master_{key}_editor_form", clear_on_submit=False):
            edited = st.data_editor(
                editor_df,
                hide_index=True,
                use_container_width=True,
                num_rows="dynamic",
                height=470 if uses_simple_master_form(source_type) else 360,
                key=f"product_master_{key}_editor",
                column_order=OFFLINE_MASTER_COLUMNS if uses_simple_master_form(source_type) else MASTER_COLUMNS,
                column_config=offline_master_column_config() if uses_simple_master_form(source_type) else master_column_config(),
            )
            if st.form_submit_button("저장", type="primary", use_container_width=True):
                payload = offline_editor_to_payload(edited) if uses_simple_master_form(source_type) else editor_to_payload(edited)
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

            row3 = st.columns([0.8, 0.8, 0.9, 0.8, 0.9], gap="small")
            with row3[0]:
                pack_qty = st.number_input("입수", min_value=0, step=1, key=f"product_master_{key}_single_pack_qty")
            with row3[1]:
                box_qty = st.number_input("박스입수", min_value=0, step=1, key=f"product_master_{key}_single_box_qty")
            with row3[2]:
                default_lead_time = st.number_input("기본 리드타임", min_value=0, step=1, key=f"product_master_{key}_single_lead_time")
            with row3[3]:
                min_stock = st.number_input("최소재고", min_value=0, step=1, key=f"product_master_{key}_single_min_stock")
            with row3[4]:
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


def fetch_master(source_type: str, keyword: str, active_filter: str) -> list[dict]:
    return (
        with_db(
            lambda db: [
                services.product_master_to_dict(row)
                for row in services.list_product_master(db, source_type, keyword, active_filter)
            ]
        )
        or []
    )


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
        "사용여부": st.column_config.SelectboxColumn("사용여부", options=["사용", "미사용"]),
    }


def offline_master_column_config() -> dict:
    return {
        "미사용 처리": st.column_config.CheckboxColumn("미사용 처리", width=74, default=False),
        "카테고리": st.column_config.TextColumn("카테고리", width="medium"),
        "상품명": st.column_config.TextColumn("상품명", width="large"),
        "88바코드": st.column_config.TextColumn("88바코드", width="medium"),
        "리드타임": st.column_config.NumberColumn("리드타임", min_value=0, step=1),
        "사용여부": st.column_config.SelectboxColumn("사용여부", options=["사용", "미사용"]),
    }


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
                "사용여부": row.get("is_active", "사용"),
                "비고": row.get("memo", ""),
            }
        )
    return pd.DataFrame(data, columns=MASTER_COLUMNS)


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
                "사용여부": row.get("is_active", "사용"),
                "비고": row.get("memo", ""),
            }
        )
    return pd.DataFrame(data, columns=OFFLINE_MASTER_COLUMNS)


def offline_master_template_df() -> pd.DataFrame:
    return pd.DataFrame(columns=[column for column in OFFLINE_MASTER_COLUMNS if column != "미사용 처리"])


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
            color: #ffffff;
            font-size: 1.32rem;
            font-weight: 950;
            margin: 0.15rem 0 0.85rem;
        }
        .product-master-subtitle {
            color: #ffffff;
            font-size: 1.08rem;
            font-weight: 900;
            margin: 0.35rem 0 0.75rem;
        }
        .product-master-control-title,
        .product-master-form-title {
            color: #ffffff;
            font-size: 1.02rem;
            font-weight: 900;
            margin: 0.1rem 0 0.45rem;
        }
        div[class*="st-key-product_master_"][class*="_controls"] {
            background: rgba(7, 58, 52, 0.48);
            border: 1px solid rgba(87, 178, 165, 0.28);
            border-radius: 8px;
            margin: 0.2rem 0 0.75rem;
            padding: 0.82rem;
        }
        div[class*="st-key-product_master_"][class*="_editor_panel"] {
            background: rgba(7, 58, 52, 0.36);
            border: 1px solid rgba(87, 178, 165, 0.28);
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
            border-color: rgba(87, 178, 165, 0.28);
            margin: 0.25rem 0 0.75rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
