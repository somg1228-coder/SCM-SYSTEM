from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import logging
from html.parser import HTMLParser
from io import BytesIO, StringIO
import json
from math import ceil
from pathlib import Path
import re
from statistics import median
import time
import unicodedata
from decimal import Decimal, InvalidOperation

import pandas as pd
from sqlalchemy import delete, func, inspect as sqlalchemy_inspect, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database import ensure_postgresql_runtime_columns, record_save_failure, record_save_success
from backend.models import (
    InventoryDaily,
    InventoryInbound,
    InventoryOutputHistory,
    InventoryUploadHistory,
    InventoryUploadSnapshot,
    OfflineProductMaster,
    PurchaseOrder,
    PurchaseRequest,
    ThirdpartyProductMaster,
    WarehouseInventoryPosition,
    WarehouseLayout,
    WarehouseProductMaster,
    WarehouseRack,
)

try:
    from backend import supabase_store
except Exception:
    supabase_store = None

INVENTORY_UPDATE_LOG_PATH = Path(__file__).resolve().parents[1] / "data" / "inventory_update_perf.log"
INVENTORY_RENDER_LOG_PATH = Path(__file__).resolve().parents[1] / "data" / "inventory_render_perf.log"
INVENTORY_LOGGER = logging.getLogger("scm.inventory_update")
INVENTORY_TRACE_BARCODE = "8809722102830"


def use_legacy_supabase_rest_store() -> bool:
    return False


HTML_TABLE_FALLBACK_MESSAGE = "엑셀 형식이 HTML 기반이라 read_html로 처리했습니다"
ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")
KNOWN_IMPORT_HEADERS = {
    "상품명",
    "상품코드",
    "SKU",
    "바코드",
    "88바코드",
    "카테고리",
    "카테고리명",
    "상품카테고리",
    "상품 카테고리",
    "대분류",
    "대분류명",
    "중분류",
    "중분류명",
    "소분류",
    "소분류명",
    "분류",
    "상품분류",
    "업체명",
    "재고위치",
    "위치상태",
    "보관위치",
    "박스/파렛트 단위",
    "담당자",
    "현재고",
    "가용재고",
    "안전재고",
    "재고상태",
    "리드타임",
    "기본창고-정상",
}
IMPORT_HEADER_ALIASES = {
    "카테고리": ("카테고리", "카테고리명", "상품카테고리", "상품 카테고리", "대분류", "대분류명", "대 카테고리", "대카테고리", "분류", "상품분류", "category", "Category", "large_category", "largeCategory"),
    "중분류": ("중분류", "중분류명", "중 카테고리", "중카테고리", "medium_category", "mediumCategory"),
    "소분류": ("소분류", "소분류명", "소 카테고리", "소카테고리", "small_category", "smallCategory"),
    "바코드": ("바코드", "88바코드", "옵션바코드", "barcode"),
    "상품명": ("상품명", "품목", "품목명", "product_name"),
    "업체명": ("업체명", "공급처", "거래처", "supplier"),
    "재고위치": ("재고위치", "재고 위치", "보관위치", "보관 위치", "창고위치", "창고 위치", "로케이션", "랙위치", "랙 위치", "location", "storage_location"),
    "위치상태": ("위치상태", "위치 상태", "location_status", "location_registered"),
    "박스/파렛트 단위": ("박스/파렛트 단위", "박스파렛트단위", "파렛트,박스단위"),
    "담당자": ("담당자", "비고", "memo"),
    "리드타임": ("리드타임", "기본 리드타임", "제조기간", "default_lead_time"),
    "SKU": ("SKU", "sku", "상품코드", "품목코드", "상품번호", "대표상품코드"),
}
PRODUCT_MASTER_COLUMNS = [
    "SKU",
    "바코드",
    "상품명",
    "카테고리",
    "브랜드",
    "공급처",
    "재고위치",
    "위치상태",
    "입수",
    "박스입수",
    "기본 리드타임",
    "최소재고",
    "정렬순서",
    "사용여부",
    "비고",
]

SHARED_MASTER_FORM_COLUMNS = [
    "카테고리",
    "상품명",
    "업체명",
    "박스/파렛트 단위",
    "담당자",
    "리드타임",
]

SHARED_MASTER_REQUIRED_COLUMNS = set(SHARED_MASTER_FORM_COLUMNS)
SHARED_MASTER_OPTIONAL_COLUMNS = {"바코드", "위치상태"}
SHARED_MASTER_KNOWN_COLUMNS = SHARED_MASTER_REQUIRED_COLUMNS | SHARED_MASTER_OPTIONAL_COLUMNS
THREEPL_MASTER_SHEET_NAME = "3PL 마스터"

PRODUCT_MASTER_MODEL_BY_SOURCE = {
    "오프라인": OfflineProductMaster,
    "3PL": ThirdpartyProductMaster,
    "창고": WarehouseProductMaster,
}

PURCHASE_METRIC_SOURCE_ORDER = ["창고", "3PL", "오프라인"]
STOCK_WARNING_RATIO = 0.2
STOCK_CURRENT_COLUMN_CANDIDATES = ["수정재고", "변경재고", "실사재고", "조정재고", "보유재고", "현재고", "재고수량", "재고", "기본창고-정상", "정상재고", "수량", "상품수량"]
STOCK_AVAILABLE_COLUMN_CANDIDATES = ["가용재고", "판매가능재고", "판매 가능 재고", "수량", "상품수량"]
ERP_AVAILABLE_STOCK_COLUMN_CANDIDATES = ["가용재고", "판매가능재고", "판매 가능 재고", "available_stock"]
ERP_INVOICE_COLUMN_CANDIDATES = ["송장", "송장수량", "송장 수량", "invoice"]
ERP_RECEIVED_COLUMN_CANDIDATES = ["접수", "접수수량", "접수 수량", "accepted_qty", "receipt"]
STOCK_CATEGORY_COLUMN_CANDIDATES = ["카테고리", "카테고리명", "상품카테고리", "상품 카테고리", "대분류", "대분류명", "분류", "상품분류", "category", "large_category"]
STOCK_LOCATION_COLUMN_CANDIDATES = ["재고위치", "재고 위치", "보관위치", "보관 위치", "창고위치", "창고 위치", "로케이션", "랙위치", "랙 위치", "location", "storage_location"]
OFFLINE_OUTBOUND_OUTPUT_TYPE = "OFFLINE_OUTBOUND"
OFFLINE_OUTBOUND_DATE_COLUMN_CANDIDATES = ["출고일자", "출고일", "발송일", "배송일", "판매일", "주문일", "일자", "date"]
OFFLINE_OUTBOUND_QTY_COLUMN_CANDIDATES = ["출고수량", "출고 수량", "판매수량", "판매 수량", "매출수량", "매출 수량", "주문수량", "주문 수량", "수량", "qty", "quantity"]
OFFLINE_OUTBOUND_ORDER_COLUMN_CANDIDATES = ["주문번호", "주문 번호", "주문ID", "주문 ID", "order_no", "order_id"]
OFFLINE_OUTBOUND_SHIPMENT_COLUMN_CANDIDATES = ["출고번호", "출고 번호", "배송번호", "배송 번호", "shipment_no", "shipment_id"]
OFFLINE_OUTBOUND_INVOICE_COLUMN_CANDIDATES = ["송장번호", "송장 번호", "운송장번호", "운송장 번호", "invoice_no", "tracking_no"]
OFFLINE_OUTBOUND_SKU_COLUMN_CANDIDATES = ["SKU", "상품코드", "품목코드", "상품번호", "옵션코드", "sku"]
OFFLINE_OUTBOUND_BARCODE_COLUMN_CANDIDATES = ["바코드", "옵션바코드", "88바코드", "barcode"]
OFFLINE_OUTBOUND_NAME_COLUMN_CANDIDATES = ["상품명", "품목", "품목명", "제품명", "product_name"]


def product_master_model(source_type: str):
    return PRODUCT_MASTER_MODEL_BY_SOURCE.get(source_type, ThirdpartyProductMaster)


def product_master_model_fields(model) -> set[str]:
    return {column.key for column in model.__table__.columns}


def product_master_model_data(model, row: dict) -> dict:
    fields = product_master_model_fields(model)
    return {key: value for key, value in row.items() if key in fields}


def model_has_field(model, field_name: str) -> bool:
    return field_name in model.__table__.columns


def normalize_location_registered(value) -> bool:
    text = clean_text(value).replace(" ", "").lower()
    if not text:
        return False
    return text in {
        "위치등록",
        "등록",
        "등록됨",
        "있음",
        "위치있음",
        "y",
        "yes",
        "true",
        "1",
        "o",
        "ok",
    }


def location_status_label(value) -> str:
    return "위치등록" if bool(value) else "위치미등록"


def product_master_lookup(db: Session, source_type: str) -> dict[str, dict[str, object]]:
    if use_legacy_supabase_rest_store():
        rows = supabase_store.list_product_master(source_type, "", "전체")
    else:
        model = product_master_model(source_type)
        rows = list(db.execute(select(model).order_by(model.id)).scalars())

    lookup: dict[str, dict[str, object]] = {
        "_source_type": source_type,
        "sku": {},
        "barcode": {},
        "barcode_name": {},
        "name": {},
        "barcode_list": {},
        "name_list": {},
    }
    for product in rows or []:
        sku = normalize_product_code_text(getattr(product, "sku", ""))
        barcode = normalize_product_barcode_match_key(getattr(product, "barcode", ""))
        product_name = normalize_product_name_match_key(getattr(product, "product_name", ""))
        if sku:
            lookup["sku"].setdefault(sku, product)
        if barcode:
            lookup["barcode"].setdefault(barcode, product)
            lookup["barcode_list"].setdefault(barcode, []).append(product)
        if barcode and product_name:
            lookup["barcode_name"].setdefault(f"{barcode}|{product_name}", product)
        if product_name:
            lookup["name"].setdefault(product_name, product)
            lookup["name_list"].setdefault(product_name, []).append(product)
    return lookup


def find_product_master_from_lookup(
    lookup: dict[str, dict[str, object]],
    sku: str = "",
    barcode: str = "",
    product_name: str = "",
):
    sku = normalize_product_code_text(sku)
    barcode = normalize_product_barcode_match_key(barcode)
    product_name = normalize_product_name_match_key(product_name)
    source_type = clean_text(lookup.get("_source_type"))
    if product_name_only_match(source_type):
        if not product_name:
            return None
        product = preferred_master_product(lookup.get("name_list", {}).get(product_name, []))
        return product
    product = lookup["sku"].get(sku)
    if product is None and product_name:
        product = preferred_master_product(lookup.get("name_list", {}).get(product_name, []))
    return product


class HTMLTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table_depth = 0
        self._current_table: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None
        self._current_colspan = 1

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._current_table = []
        elif tag == "tr" and self._table_depth:
            self._current_row = []
        elif tag in {"td", "th"} and self._table_depth and self._current_row is not None:
            self._current_cell = []
            self._current_colspan = self._read_colspan(attrs)
        elif tag == "br" and self._current_cell is not None:
            self._current_cell.append("\n")

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._current_cell is not None and self._current_row is not None:
            value = normalize_html_cell("".join(self._current_cell))
            for _ in range(max(self._current_colspan, 1)):
                self._current_row.append(value)
            self._current_cell = None
            self._current_colspan = 1
        elif tag == "tr" and self._current_row is not None:
            if any(cell for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._table_depth:
            if self._table_depth == 1 and self._current_table:
                self.tables.append(self._current_table)
            self._table_depth -= 1

    @staticmethod
    def _read_colspan(attrs) -> int:
        for name, value in attrs:
            if name.lower() == "colspan":
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return 1
        return 1


def clean_text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = unicodedata.normalize("NFKC", str(value))
    text = ZERO_WIDTH_RE.sub("", text)
    return text.strip()


def normalize_barcode_text(value) -> str:
    text = clean_text(value).replace(",", "")
    if not text:
        return ""
    if re.fullmatch(r"\d+", text):
        return text
    if re.fullmatch(r"\d+\.0+", text):
        return text.split(".", 1)[0]
    try:
        numeric = Decimal(text)
    except InvalidOperation:
        return text
    if numeric == numeric.to_integral_value():
        return format(numeric.quantize(Decimal(1)), "f")
    return text


def normalize_barcode_identifier(value) -> str:
    text = clean_text(value).replace(",", "")
    if not text:
        return ""
    if re.fullmatch(r"\d+\.0+", text):
        return text.split(".", 1)[0]
    if re.search(r"[eE]", text):
        try:
            numeric = Decimal(text)
        except InvalidOperation:
            return text
        if numeric == numeric.to_integral_value():
            return format(numeric.quantize(Decimal(1)), "f")
    return text


def normalize_product_name_match_key(value) -> str:
    return clean_text(value)


def normalize_product_barcode_match_key(value) -> str:
    return normalize_barcode_text(value)


def normalize_code_text(value, uppercase: bool = True) -> str:
    text = clean_text(value).replace(",", "")
    if not text:
        return ""
    text = text.strip().strip("'\"`")
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".", 1)[0]
    else:
        try:
            numeric = Decimal(text)
        except InvalidOperation:
            pass
        else:
            if numeric == numeric.to_integral_value():
                text = format(numeric.quantize(Decimal(1)), "f")
    text = re.sub(r"\s+", "", text)
    return text.upper() if uppercase else text


def normalize_product_code_text(value) -> str:
    return normalize_erp_product_code(value)


def normalize_erp_product_code(value) -> str:
    text = clean_text(value).replace(",", "")
    if not text:
        return ""
    text = text.strip().strip("'\"`")
    if re.fullmatch(r"\d+", text):
        return text.lstrip("0") or "0"
    return normalize_code_text(text, uppercase=True)


def normalize_inventory_upload_code_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    code_keys = {import_header_key(name) for name in ("SKU", "상품코드", "품목코드", "상품번호")}
    barcode_keys = {import_header_key(name) for name in ("88바코드", "바코드", "옵션바코드", "barcode")}
    next_df = df.copy()
    for column in next_df.columns:
        key = import_header_key(column)
        if key in code_keys:
            next_df[column] = next_df[column].map(normalize_product_code_text)
        elif key in barcode_keys:
            next_df[column] = next_df[column].map(normalize_barcode_text)
    next_df.attrs.update(getattr(df, "attrs", {}))
    return next_df


def normalize_product_master_barcode_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    next_df = df.copy()
    for column in next_df.columns:
        if normalize_import_header_name(str(column)) == "바코드":
            next_df[column] = next_df[column].fillna("").astype(str).map(normalize_barcode_identifier)
    next_df.attrs.update(getattr(df, "attrs", {}))
    return next_df


def import_header_key(value) -> str:
    return re.sub(r"[\s_/\-·.,:()\[\]{}]+", "", clean_text(value)).lower()


IMPORT_HEADER_ALIAS_MAP = {
    import_header_key(alias): standard
    for standard, aliases in IMPORT_HEADER_ALIASES.items()
    for alias in aliases
}


def normalize_import_header_name(value) -> str:
    text = normalize_html_cell(str(value))
    return IMPORT_HEADER_ALIAS_MAP.get(import_header_key(text), text)


def to_int(value) -> int:
    text = clean_text(value).replace(",", "")
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        number = re.search(r"-?\d+(?:\.\d+)?", text)
        if not number:
            return 0
        try:
            return int(float(number.group(0)))
        except ValueError:
            return 0


def to_box_unit_int(value) -> int:
    text = clean_text(value).replace(",", "")
    if not text:
        return 0
    box_match = re.search(r"박스\s*(\d+(?:\.\d+)?)", text)
    if box_match:
        return int(float(box_match.group(1)))
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if numbers:
        return int(float(numbers[-1]))
    return to_int(text)


def parse_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def parse_excel_serial_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    text = clean_text(value)
    if not text:
        return None
    try:
        serial = float(text)
    except ValueError:
        return parse_date(value)
    if serial <= 0:
        return None
    parsed = pd.to_datetime(serial, unit="D", origin="1899-12-30", errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def is_business_day(day: date, holidays: set[date] | None = None) -> bool:
    holiday_set = holidays or set()
    return day.weekday() < 5 and day not in holiday_set


def normalize_html_cell(value: str) -> str:
    return re.sub(r"\s+", " ", clean_text(value)).strip()


def decode_html_bytes(file_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return file_bytes.decode("utf-8", errors="replace")


def unique_headers(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result = []
    for index, header in enumerate(headers, start=1):
        name = normalize_import_header_name(str(header)) or f"column_{index}"
        seen[name] = seen.get(name, 0) + 1
        result.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return result


def table_rows_to_dataframe(rows: list[list[str]]) -> pd.DataFrame:
    meaningful_rows = [row for row in rows if any(str(cell).strip() for cell in row)]
    if not meaningful_rows:
        return pd.DataFrame()

    header_index = 0
    for index, row in enumerate(meaningful_rows):
        if KNOWN_IMPORT_HEADERS.intersection(set(row)):
            header_index = index
            break

    headers = unique_headers(meaningful_rows[header_index])
    width = len(headers)
    data_rows = []
    for row in meaningful_rows[header_index + 1 :]:
        padded = (row + [""] * width)[:width]
        if any(str(cell).strip() for cell in padded):
            data_rows.append(padded)
    return pd.DataFrame(data_rows, columns=headers)


def normalize_import_headers(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    current_headers = {normalize_import_header_name(str(column)) for column in df.columns}
    if KNOWN_IMPORT_HEADERS.intersection(current_headers):
        df.columns = unique_headers([normalize_html_cell(str(column)) for column in df.columns])
        return df

    scan_limit = min(len(df), 10)
    for index in range(scan_limit):
        row_values = [
            "" if pd.isna(value) else normalize_html_cell(str(value))
            for value in df.iloc[index].tolist()
        ]
        normalized_row_values = [normalize_import_header_name(value) for value in row_values]
        if KNOWN_IMPORT_HEADERS.intersection(set(normalized_row_values)):
            normalized = df.iloc[index + 1 :].reset_index(drop=True).copy()
            normalized.columns = unique_headers(row_values)
            return normalized
    return df


def has_known_import_headers(df: pd.DataFrame) -> bool:
    headers = {normalize_import_header_name(str(column)) for column in df.columns}
    return bool(KNOWN_IMPORT_HEADERS.intersection(headers))


def read_html_with_stdlib(file_bytes: bytes) -> pd.DataFrame:
    parser = HTMLTableParser()
    parser.feed(decode_html_bytes(file_bytes))
    if not parser.tables:
        raise ValueError("HTML table을 찾지 못했습니다.")

    table = max(parser.tables, key=lambda rows: len(rows) * max((len(row) for row in rows), default=0))
    df = normalize_import_headers(table_rows_to_dataframe(table))
    if df.empty:
        raise ValueError("HTML table에 읽을 데이터가 없습니다.")
    df.attrs["read_method"] = "html"
    df.attrs["read_message"] = HTML_TABLE_FALLBACK_MESSAGE
    return df


def read_excel(file_bytes: bytes) -> pd.DataFrame:
    uploaded_file = BytesIO(file_bytes)
    try:
        workbook = pd.ExcelFile(uploaded_file, engine="openpyxl")
        sheet_names = list(workbook.sheet_names)
        if not sheet_names:
            raise ValueError("엑셀 시트를 찾지 못했습니다.")
        selected_sheet = ""
        selected_raw = pd.DataFrame()
        selected_df = pd.DataFrame()
        non_empty_sheets = []
        fallback_name = ""
        for sheet_name in sheet_names:
            probe = workbook.parse(sheet_name=sheet_name, nrows=12, dtype=str)
            if probe is None or probe.dropna(how="all").empty:
                continue
            non_empty_sheets.append(sheet_name)
            if not fallback_name:
                fallback_name = sheet_name
            candidate = normalize_import_headers(probe)
            if has_known_import_headers(candidate):
                selected_sheet = sheet_name
                break
        if not selected_sheet:
            selected_sheet = fallback_name or sheet_names[0]
        selected_raw = workbook.parse(sheet_name=selected_sheet, dtype=str)
        selected_df = normalize_import_headers(selected_raw)
        if selected_df.empty:
            selected_df = normalize_import_headers(selected_raw)
        df = selected_df
        df.attrs["read_method"] = "excel"
        df.attrs["sheet_names"] = sheet_names
        df.attrs["non_empty_sheet_names"] = non_empty_sheets
        df.attrs["selected_sheet"] = selected_sheet
        df.attrs["raw_shape"] = tuple(selected_raw.shape)
        df.attrs["raw_columns"] = [str(column) for column in selected_raw.columns]
        df.attrs["raw_head"] = selected_raw.head(5).fillna("").astype(str).to_dict("records")
        df.attrs["normalized_shape"] = tuple(df.shape)
        df.attrs["normalized_columns"] = [str(column) for column in df.columns]
        df.attrs["normalized_head"] = df.head(5).fillna("").astype(str).to_dict("records")
        return df
    except Exception as excel_error:
        uploaded_file.seek(0)
        try:
            workbook = pd.ExcelFile(uploaded_file, engine="xlrd")
            sheet_names = list(workbook.sheet_names)
            if not sheet_names:
                raise ValueError("엑셀 시트를 찾지 못했습니다.")
            selected_sheet = ""
            selected_raw = pd.DataFrame()
            selected_df = pd.DataFrame()
            non_empty_sheets = []
            fallback_name = ""
            for sheet_name in sheet_names:
                probe = workbook.parse(sheet_name=sheet_name, nrows=12, dtype=str)
                if probe is None or probe.dropna(how="all").empty:
                    continue
                non_empty_sheets.append(sheet_name)
                if not fallback_name:
                    fallback_name = sheet_name
                candidate = normalize_import_headers(probe)
                if has_known_import_headers(candidate):
                    selected_sheet = sheet_name
                    break
            if not selected_sheet:
                selected_sheet = fallback_name or sheet_names[0]
            selected_raw = workbook.parse(sheet_name=selected_sheet, dtype=str)
            selected_df = normalize_import_headers(selected_raw)
            if selected_df.empty:
                selected_df = normalize_import_headers(selected_raw)
            df = selected_df
            df.attrs["read_method"] = "excel_xls"
            df.attrs["sheet_names"] = sheet_names
            df.attrs["non_empty_sheet_names"] = non_empty_sheets
            df.attrs["selected_sheet"] = selected_sheet
            df.attrs["raw_shape"] = tuple(selected_raw.shape)
            df.attrs["raw_columns"] = [str(column) for column in selected_raw.columns]
            df.attrs["raw_head"] = selected_raw.head(5).fillna("").astype(str).to_dict("records")
            df.attrs["normalized_shape"] = tuple(df.shape)
            df.attrs["normalized_columns"] = [str(column) for column in df.columns]
            df.attrs["normalized_head"] = df.head(5).fillna("").astype(str).to_dict("records")
            return df
        except Exception:
            uploaded_file.seek(0)
        try:
            tables = pd.read_html(uploaded_file)
        except Exception as html_error:
            uploaded_file.seek(0)
            try:
                tables = pd.read_html(StringIO(decode_html_bytes(uploaded_file.getvalue())))
            except Exception:
                try:
                    return read_html_with_stdlib(file_bytes)
                except Exception as parser_error:
                    raise ValueError("파일을 읽지 못했습니다. 엑셀 또는 HTML table 형식인지 확인해주세요.") from parser_error
        if not tables:
            raise ValueError("HTML table을 찾지 못했습니다.") from excel_error

        df = normalize_import_headers(tables[0])
        df.attrs["read_method"] = "html"
        df.attrs["read_message"] = HTML_TABLE_FALLBACK_MESSAGE
        df.attrs["selected_sheet"] = "HTML table"
        df.attrs["raw_shape"] = tuple(tables[0].shape)
        df.attrs["raw_columns"] = [str(column) for column in tables[0].columns]
        df.attrs["raw_head"] = tables[0].head(5).fillna("").astype(str).to_dict("records")
        df.attrs["normalized_shape"] = tuple(df.shape)
        df.attrs["normalized_columns"] = [str(column) for column in df.columns]
        df.attrs["normalized_head"] = df.head(5).fillna("").astype(str).to_dict("records")
        return df


def read_threepl_master_excel(file_bytes: bytes) -> pd.DataFrame:
    try:
        workbook = pd.ExcelFile(BytesIO(file_bytes), engine="openpyxl")
    except Exception:
        return read_excel(file_bytes)
    if not workbook.sheet_names:
        return pd.DataFrame()

    sheet_name = THREEPL_MASTER_SHEET_NAME if THREEPL_MASTER_SHEET_NAME in workbook.sheet_names else workbook.sheet_names[0]
    raw_df = workbook.parse(sheet_name=sheet_name, header=None, dtype=object)
    if raw_df is None or raw_df.dropna(how="all").empty:
        return pd.DataFrame()

    required_headers = {normalize_import_header_name(column) for column in SHARED_MASTER_FORM_COLUMNS}
    header_index = None
    header_values: list[str] = []
    scan_limit = min(len(raw_df), 20)
    for index in range(scan_limit):
        values = ["" if pd.isna(value) else normalize_html_cell(str(value)) for value in raw_df.iloc[index].tolist()]
        normalized_values = {normalize_import_header_name(value) for value in values if clean_text(value)}
        if required_headers.issubset(normalized_values):
            header_index = index
            last_header_position = max((position for position, value in enumerate(values) if clean_text(value)), default=0)
            header_values = values[: last_header_position + 1]
            break

    if header_index is None:
        df = normalize_import_headers(raw_df)
        df.attrs["read_method"] = "excel"
        df.attrs["selected_sheet"] = sheet_name
        df.attrs["sheet_names"] = list(workbook.sheet_names)
        df.attrs["header_row_number"] = None
        return df

    headers = unique_headers(header_values)
    width = len(headers)
    data_df = raw_df.iloc[header_index + 1 :].copy()
    data_df = data_df.iloc[:, :width]
    data_df.columns = headers
    data_df = data_df.dropna(how="all").reset_index(drop=True)
    data_df.attrs["read_method"] = "excel"
    data_df.attrs["selected_sheet"] = sheet_name
    data_df.attrs["sheet_names"] = list(workbook.sheet_names)
    data_df.attrs["header_row_number"] = header_index + 1
    data_df.attrs["raw_shape"] = tuple(raw_df.shape)
    data_df.attrs["raw_columns"] = [str(value) for value in header_values]
    data_df.attrs["raw_head"] = raw_df.head(5).fillna("").astype(str).to_dict("records")
    data_df.attrs["normalized_shape"] = tuple(data_df.shape)
    data_df.attrs["normalized_columns"] = [str(column) for column in data_df.columns]
    data_df.attrs["normalized_head"] = data_df.head(5).fillna("").astype(str).to_dict("records")
    return data_df


def read_seonghyun_inbound_statement(file_bytes: bytes) -> pd.DataFrame | None:
    uploaded_file = BytesIO(file_bytes)
    try:
        sheets = pd.read_excel(uploaded_file, sheet_name=None, header=None, engine="openpyxl")
    except Exception:
        return None

    for raw_df in sheets.values():
        if raw_df.empty:
            continue
        text_cells = {clean_text(value).replace(" ", "") for value in raw_df.to_numpy().ravel() if clean_text(value)}
        if "거래명세서" not in text_cells:
            continue

        header_index = None
        product_col = None
        qty_col = None
        for index, row in raw_df.iterrows():
            values = [clean_text(value).replace(" ", "") for value in row.tolist()]
            if "품목" in values and "수량" in values:
                header_index = index
                product_col = values.index("품목")
                qty_col = values.index("수량")
                break
        if header_index is None or product_col is None or qty_col is None:
            continue

        statement_date_value = raw_df.iat[1, 0] if raw_df.shape[0] > 1 and raw_df.shape[1] > 0 else None
        statement_date = parse_excel_serial_date(statement_date_value) or parse_date(statement_date_value) or date.today()
        rows = []
        current_month = statement_date.month
        current_day = statement_date.day
        for _, row in raw_df.iloc[header_index + 1 :].iterrows():
            product_name = clean_text(row.iloc[product_col] if product_col < len(row) else "")
            qty = to_int(row.iloc[qty_col] if qty_col < len(row) else "")
            if not product_name or qty <= 0:
                continue
            month_value = clean_text(row.iloc[0] if len(row) > 0 else "")
            day_value = clean_text(row.iloc[1] if len(row) > 1 else "")
            if month_value:
                current_month = to_int(month_value) or current_month
            if day_value:
                current_day = to_int(day_value) or current_day
            try:
                inbound_date = date(statement_date.year, current_month, current_day)
            except ValueError:
                inbound_date = statement_date
            rows.append(
                {
                    "입고일자": inbound_date,
                    "품목": product_name,
                    "수량": qty,
                    "거래처": "성현물류",
                    "입고구분": "거래명세서",
                }
            )

        if rows:
            df = pd.DataFrame(rows)
            df.attrs["read_method"] = "seonghyun_statement"
            return df
    return None


def import_result(count: int, df: pd.DataFrame) -> dict:
    return {
        "count": count,
        "used_html": df.attrs.get("read_method") == "html",
        "message": df.attrs.get("read_message", ""),
    }


def find_column(df: pd.DataFrame, candidates: list[str]) -> str:
    normalized = {import_header_key(column): column for column in df.columns}
    for candidate in candidates:
        key = import_header_key(candidate)
        if key in normalized:
            return normalized[key]
    raise ValueError(f"필수 컬럼을 찾지 못했습니다: {', '.join(candidates)}")


def list_work_dates(db: Session, source_type: str | None = None) -> list[date]:
    if use_legacy_supabase_rest_store():
        return supabase_store.list_work_dates(source_type)
    query = select(InventoryDaily.work_date).distinct()
    if source_type:
        query = query.where(InventoryDaily.source_type == source_type)
    return list(db.execute(query.order_by(InventoryDaily.work_date.desc())).scalars())


def product_master_to_dict(row) -> dict:
    return {
        "id": row.id,
        "sku": row.sku,
        "barcode": row.barcode,
        "product_name": row.product_name,
        "category": row.large_category,
        "large_category": row.large_category,
        "medium_category": row.medium_category,
        "small_category": row.small_category,
        "brand": row.brand,
        "supplier": row.supplier,
        "storage_location": getattr(row, "storage_location", ""),
        "location_registered": bool(getattr(row, "location_registered", False)),
        "location_status": location_status_label(getattr(row, "location_registered", False)),
        "pack_qty": row.pack_qty,
        "box_qty": row.box_qty,
        "box_pallet_unit": format_box_pallet_unit(row.box_qty, row.pack_qty) or "0",
        "default_lead_time": row.default_lead_time,
        "min_stock": row.min_stock,
        "sort_order": row.sort_order,
        "is_active": row.is_active,
        "memo": row.memo,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def list_product_master(db: Session, source_type: str, keyword: str = "", active_filter: str = "전체") -> list:
    if use_legacy_supabase_rest_store():
        return supabase_store.list_product_master(source_type, keyword, active_filter)
    model = product_master_model(source_type)
    query = select(model)
    if active_filter != "전체":
        query = query.where(model.is_active == active_filter)
    keyword = clean_text(keyword)
    if keyword:
        like = f"%{keyword}%"
        query = query.where(
            (model.sku.like(like))
            | (model.barcode.like(like))
            | (model.product_name.like(like))
            | (model.large_category.like(like))
            | (model.brand.like(like))
            | (model.supplier.like(like))
        )
    return list(db.execute(query.order_by(model.sort_order, model.large_category, model.product_name, model.sku)).scalars())


def active_product_options(db: Session, source_type: str) -> list[dict]:
    if use_legacy_supabase_rest_store():
        return supabase_store.active_product_options(source_type)
    model = product_master_model(source_type)
    rows = db.execute(select(model).where(model.is_active == "사용").order_by(model.sort_order, model.large_category, model.product_name, model.sku)).scalars()
    return [product_master_to_dict(row) for row in rows]


def list_product_master_categories(db: Session, source_type: str) -> list[str]:
    if use_legacy_supabase_rest_store():
        rows = supabase_store.list_product_master(source_type, "", "사용")
        return sorted({clean_text(getattr(row, "large_category", "")) for row in rows if clean_text(getattr(row, "large_category", ""))})
    model = product_master_model(source_type)
    rows = db.execute(
        select(model.large_category)
        .where(model.is_active == "사용", model.large_category != "")
        .distinct()
        .order_by(model.large_category)
    ).scalars()
    return [clean_text(row) for row in rows if clean_text(row)]


def find_product_master(db: Session, source_type: str, sku: str = "", barcode: str = "", product_name: str = ""):
    if use_legacy_supabase_rest_store():
        product = supabase_store.find_product(source_type, sku, barcode, product_name)
        return supabase_store.namespace(supabase_store.product_to_row(product)) if product else None
    model = product_master_model(source_type)
    products = list(db.execute(select(model).order_by(model.id)).scalars())
    return match_product_from_maps(sku, barcode, product_name, *product_lookup_maps(products), source_type=source_type)


def purchase_metric_source_order(preferred_source: str = "") -> list[str]:
    order = []
    if preferred_source in PURCHASE_METRIC_SOURCE_ORDER:
        order.append(preferred_source)
    for source_type in PURCHASE_METRIC_SOURCE_ORDER:
        if source_type not in order:
            order.append(source_type)
    return order


def find_product_master_any(
    db: Session,
    sku: str = "",
    barcode: str = "",
    product_name: str = "",
    preferred_source: str = "창고",
) -> tuple[str, object] | tuple[str, None]:
    for source_type in purchase_metric_source_order(preferred_source):
        product = find_product_master(db, source_type, sku, barcode, product_name)
        if product:
            return source_type, product
    return preferred_source or "창고", None


def purchase_inventory_metrics(db: Session, source_type: str | None = None, products: list | None = None) -> dict[tuple[str, str], dict]:
    source_types = [source_type] if source_type else PURCHASE_METRIC_SOURCE_ORDER
    products_by_key: dict[tuple[str, str], object] = {}
    sku_lookup: dict[str, list[tuple[str, object]]] = {}
    name_lookup: dict[str, list[tuple[str, object]]] = {}
    barcode_lookup: dict[str, list[tuple[str, object]]] = {}

    for product_source in source_types:
        if products is not None and source_type and product_source == source_type:
            source_products = products
        else:
            model = product_master_model(product_source)
            source_products = db.execute(select(model)).scalars()
        for product in source_products:
            key = (product_source, product.sku)
            products_by_key[key] = product
            if product.sku:
                sku_lookup.setdefault(clean_text(product.sku).lower(), []).append((product_source, product))
            if product.barcode:
                barcode_lookup.setdefault(normalize_barcode_text(product.barcode).lower(), []).append((product_source, product))
            if product.product_name:
                name_lookup.setdefault(clean_text(product.product_name).lower(), []).append((product_source, product))

    if not products_by_key:
        return {}

    pr_rows = db.execute(select(PurchaseRequest.pr_number, PurchaseRequest.item_code)).all()
    pr_item_codes = {pr_number: clean_text(item_code) for pr_number, item_code in pr_rows if pr_number}
    metrics: dict[tuple[str, str], dict] = {}
    completed_pos = db.execute(
        select(PurchaseOrder).where(
            PurchaseOrder.order_date.is_not(None),
            PurchaseOrder.actual_inbound_date.is_not(None),
        ).order_by(PurchaseOrder.actual_inbound_date, PurchaseOrder.id)
    ).scalars()

    for po in completed_pos:
        item_code = pr_item_codes.get(po.pr_number, "")
        lookup_key = clean_text(item_code).lower()
        matched = sku_lookup.get(lookup_key, []) if lookup_key else []
        if not matched and lookup_key:
            matched = barcode_lookup.get(lookup_key, [])
        if not matched:
            matched = name_lookup.get(clean_text(po.item_name).lower(), [])
        if not matched:
            continue

        lead_time = max((po.actual_inbound_date - po.order_date).days, 0)
        for product_source, product in matched:
            key = (product_source, product.sku)
            metric = metrics.setdefault(
                key,
                {
                    "lead_times": [],
                    "last_order_date": None,
                    "last_inbound_date": None,
                    "last_po_number": "",
                    "last_supplier": "",
                    "last_quantity": 0,
                },
            )
            metric["lead_times"].append(lead_time)
            last_inbound_date = metric.get("last_inbound_date")
            if last_inbound_date is None or po.actual_inbound_date >= last_inbound_date:
                metric["last_order_date"] = po.order_date
                metric["last_inbound_date"] = po.actual_inbound_date
                metric["last_po_number"] = po.po_number
                metric["last_supplier"] = po.supplier_name
                metric["last_quantity"] = int(po.quantity or 0)

    for key, metric in metrics.items():
        lead_times = metric.get("lead_times", [])
        metric["avg_lead_time"] = round(sum(lead_times) / len(lead_times)) if lead_times else 0
        metric["recent_lead_time"] = lead_times[-1] if lead_times else 0
        metric["sample_count"] = len(lead_times)
    return metrics


def product_lookup_maps(products: list) -> tuple[dict[str, object], dict[tuple[str, str], object], dict[str, list], dict[str, list]]:
    by_sku = {normalize_product_code_text(product.sku): product for product in products if normalize_product_code_text(product.sku)}
    by_barcode_name = {
        (normalize_product_barcode_match_key(product.barcode), normalize_product_name_match_key(product.product_name)): product
        for product in products
        if normalize_product_barcode_match_key(product.barcode) and normalize_product_name_match_key(product.product_name)
    }
    by_barcode: dict[str, list] = {}
    by_name: dict[str, list] = {}
    for product in products:
        barcode = normalize_product_barcode_match_key(product.barcode)
        name = normalize_product_name_match_key(product.product_name)
        if barcode:
            by_barcode.setdefault(barcode, []).append(product)
        if name:
            by_name.setdefault(name, []).append(product)
    return by_sku, by_barcode_name, by_barcode, by_name


def product_name_only_match(source_type: str) -> bool:
    return clean_text(source_type) in PRODUCT_MASTER_MODEL_BY_SOURCE


def product_name_first_match(source_type: str) -> bool:
    return product_name_only_match(source_type)


def match_product_detail_from_maps(
    product_code: str,
    barcode: str,
    product_name: str,
    by_sku: dict[str, object],
    by_barcode_name: dict[tuple[str, str], object],
    by_barcode: dict[str, list],
    by_name: dict[str, list],
    source_type: str = "",
) -> tuple[object | None, str]:
    sku = normalize_product_code_text(product_code)
    barcode = normalize_product_barcode_match_key(barcode)
    product_name = normalize_product_name_match_key(product_name)
    product = None
    if product_name_only_match(source_type):
        if not product_name:
            return None, ""
        product = preferred_master_product(by_name.get(product_name, []))
        return (product, "상품명") if product is not None else (None, "")
    product = by_sku.get(sku)
    if product is not None:
        return product, "상품코드"
    if product_name:
        product = preferred_master_product(by_name.get(product_name, []))
        if product is not None:
            return product, "상품명"
    return None, ""


def match_product_from_maps(
    product_code: str,
    barcode: str,
    product_name: str,
    by_sku: dict[str, object],
    by_barcode_name: dict[tuple[str, str], object],
    by_barcode: dict[str, list],
    by_name: dict[str, list],
    source_type: str = "",
):
    product, _ = match_product_detail_from_maps(
        product_code,
        barcode,
        product_name,
        by_sku,
        by_barcode_name,
        by_barcode,
        by_name,
        source_type=source_type,
    )
    return product


def match_3pl_product(products: list, product_code: str = "", barcode: str = "", product_name: str = ""):
    return match_product_from_maps(product_code, barcode, product_name, *product_lookup_maps(products), source_type="3PL")


def latest_inbound_metrics(db: Session, source_type: str | None = None, products: list | None = None) -> dict[tuple[str, str], dict]:
    query = select(InventoryInbound)
    if source_type:
        query = query.where(InventoryInbound.source_type == source_type)
    lookup_by_source: dict[str, tuple[dict[str, object], dict[tuple[str, str], object], dict[str, list], dict[str, list]]] = {}
    if products is not None and source_type:
        lookup_by_source[source_type] = product_lookup_maps(products)
    metrics: dict[tuple[str, str], dict] = {}
    for inbound in db.execute(query.order_by(InventoryInbound.inbound_date)).scalars():
        maps = lookup_by_source.get(inbound.source_type)
        product = (
            match_product_from_maps(inbound.product_code, inbound.barcode, inbound.product_name, *maps, source_type=inbound.source_type)
            if maps is not None
            else find_product_master(db, inbound.source_type, inbound.product_code, inbound.barcode, inbound.product_name)
        )
        if not product:
            continue
        key = (inbound.source_type, product.sku)
        metric = metrics.setdefault(key, {"last_inbound_date": None, "last_inbound_qty": 0})
        if metric["last_inbound_date"] is None or inbound.inbound_date >= metric["last_inbound_date"]:
            metric["last_inbound_date"] = inbound.inbound_date
            metric["last_inbound_qty"] = int(inbound.inbound_qty or 0)
    return metrics


def sync_purchase_metrics_to_inventory(
    db: Session,
    source_type: str | None = None,
    work_date: date | None = None,
) -> int:
    metrics = purchase_inventory_metrics(db, source_type)
    if not metrics:
        return 0
    source_types = [source_type] if source_type else PURCHASE_METRIC_SOURCE_ORDER
    count = 0

    for product_source in source_types:
        model = product_master_model(product_source)
        for product in db.execute(select(model)).scalars():
            metric = metrics.get((product_source, product.sku))
            if not metric:
                continue
            avg_lead_time = int(metric.get("avg_lead_time") or 0)
            if avg_lead_time and int(product.default_lead_time or 0) != avg_lead_time:
                product.default_lead_time = avg_lead_time
                count += 1

    daily_query = select(InventoryDaily)
    if source_type:
        daily_query = daily_query.where(InventoryDaily.source_type == source_type)
    if work_date:
        daily_query = daily_query.where(InventoryDaily.work_date == work_date)
    for daily in db.execute(daily_query).scalars():
        product = find_product_master(db, daily.source_type, daily.product_code, daily.barcode, daily.product_name)
        if not product:
            continue
        metric = metrics.get((daily.source_type, product.sku))
        if not metric:
            continue
        avg_lead_time = int(metric.get("avg_lead_time") or 0)
        if avg_lead_time and int(daily.inbound_cycle or 0) != avg_lead_time:
            daily.inbound_cycle = avg_lead_time
            count += 1
        if metric.get("last_inbound_date") and daily.last_inbound_date != metric["last_inbound_date"]:
            daily.last_inbound_date = metric["last_inbound_date"]
            count += 1

    db.commit()
    return count


def product_master_operational_metrics(db: Session, source_type: str) -> dict[str, dict]:
    dates = list_work_dates(db, source_type)
    latest_date = dates[0] if dates else None
    latest_rows = list_daily(db, source_type, latest_date) if latest_date else []
    latest_by_sku = {row.product_code: row for row in latest_rows if row.product_code}
    purchase_metrics = purchase_inventory_metrics(db, source_type)
    inbound_metrics = latest_inbound_metrics(db, source_type)
    result: dict[str, dict] = {}

    model = product_master_model(source_type)
    for product in db.execute(select(model)).scalars():
        daily = latest_by_sku.get(product.sku)
        avg_outbound = 0
        if latest_date:
            avg_outbound = ceil(
                sum(
                    int(row.outbound_qty or 0)
                    for row in db.execute(
                        select(InventoryDaily).where(
                            InventoryDaily.source_type == source_type,
                            InventoryDaily.product_code == product.sku,
                            InventoryDaily.work_date >= latest_date - timedelta(days=14),
                            InventoryDaily.work_date <= latest_date,
                        )
                    ).scalars()
                )
                / 14
            )
        key = (source_type, product.sku)
        purchase_metric = purchase_metrics.get(key, {})
        inbound_metric = inbound_metrics.get(key, {})
        result[product.sku] = {
            "avg_outbound_qty": avg_outbound,
            "stock_status": daily.stock_status if daily else "",
            "last_inbound_date": purchase_metric.get("last_inbound_date") or inbound_metric.get("last_inbound_date"),
            "avg_lead_time": purchase_metric.get("avg_lead_time", 0),
        }
    return result


def apply_product_master_to_daily(item: InventoryDaily, product) -> None:
    if not product:
        return
    state = sqlalchemy_inspect(item)
    if not (state.transient or state.pending):
        return
    item.product_code = product.sku
    item.barcode = normalize_barcode_text(product.barcode)
    item.product_name = product.product_name
    item.category = product_category_text(product) or item.category
    item.supplier = product.supplier
    if model_has_field(InventoryDaily, "storage_location"):
        item.storage_location = getattr(product, "storage_location", "") or getattr(item, "storage_location", "")
    if product.min_stock and not item.safe_stock:
        item.safe_stock = product.min_stock


def apply_product_master_to_inbound(item: InventoryInbound, product) -> None:
    if not product:
        return
    item.product_code = product.sku
    item.barcode = normalize_barcode_text(product.barcode)
    item.product_name = product.product_name
    item.category = product_category_text(product) or item.category
    item.vendor = product.supplier or item.vendor


def product_category_text(product) -> str:
    if not product:
        return ""
    return (
        clean_text(getattr(product, "large_category", ""))
        or clean_text(getattr(product, "medium_category", ""))
        or clean_text(getattr(product, "small_category", ""))
    )


def product_master_preference_score(product) -> tuple[int, int, int, int, int]:
    return (
        1 if product_category_text(product) else 0,
        1 if clean_text(getattr(product, "is_active", "")) == "사용" else 0,
        1 if clean_text(getattr(product, "supplier", "")) else 0,
        1 if clean_text(getattr(product, "product_name", "")) else 0,
        int(getattr(product, "id", 0) or 0),
    )


def preferred_master_product(products: list[object]) -> object | None:
    if not products:
        return None
    return max(products, key=product_master_preference_score)


def product_master_template_df() -> pd.DataFrame:
    return pd.DataFrame(columns=PRODUCT_MASTER_COLUMNS)


def product_master_dataframe(rows: list) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "SKU": row.sku,
                "바코드": row.barcode,
                "상품명": row.product_name,
                "카테고리": row.large_category,
                "브랜드": row.brand,
                "공급처": row.supplier,
                "재고위치": getattr(row, "storage_location", ""),
                "위치상태": location_status_label(getattr(row, "location_registered", False)),
                "입수": row.pack_qty,
                "박스입수": row.box_qty,
                "기본 리드타임": row.default_lead_time,
                "최소재고": row.min_stock,
                "정렬순서": row.sort_order,
                "사용여부": row.is_active,
                "비고": row.memo,
            }
            for row in rows
        ],
        columns=PRODUCT_MASTER_COLUMNS,
    )


def first_clean_value(data: dict, *keys: str) -> str:
    for key in keys:
        value = clean_text(data.get(key))
        if value:
            return value
    return ""


def normalize_product_master_row(row: dict) -> dict:
    data = row_data(row)
    sku = clean_text(
        data.get("SKU")
        or data.get("sku")
        or data.get("상품코드")
        or data.get("품목코드")
        or data.get("상품번호")
        or data.get("대표상품코드")
    )
    barcode = normalize_barcode_identifier(data.get("바코드") or data.get("88바코드") or data.get("옵션바코드") or data.get("barcode"))
    product_name = clean_text(data.get("상품명") or data.get("품목") or data.get("product_name"))
    is_active = clean_text(data.get("사용여부") or data.get("is_active") or "사용")
    if is_active == "사용 중":
        is_active = "사용"
    elif is_active == "비활성":
        is_active = "미사용"
    combined_box_qty, combined_pallet_qty = parse_box_pallet_unit(data.get("박스/파렛트 단위") or data.get("박스파렛트단위"))
    return {
        "sku": sku,
        "barcode": barcode,
        "product_name": product_name,
        "large_category": first_clean_value(
            data,
            "카테고리",
            "카테고리명",
            "상품카테고리",
            "상품 카테고리",
            "대분류",
            "대분류명",
            "대 카테고리",
            "대카테고리",
            "분류",
            "상품분류",
            "category",
            "Category",
            "large_category",
            "largeCategory",
        ),
        "medium_category": first_clean_value(
            data,
            "중분류",
            "중분류명",
            "중 카테고리",
            "중카테고리",
            "medium_category",
            "mediumCategory",
        ),
        "small_category": first_clean_value(
            data,
            "소분류",
            "소분류명",
            "소 카테고리",
            "소카테고리",
            "small_category",
            "smallCategory",
        ),
        "brand": clean_text(data.get("브랜드") or data.get("brand")),
        "supplier": clean_text(data.get("공급처") or data.get("업체명") or data.get("거래처") or data.get("supplier")),
        "storage_location": clean_text(
            data.get("재고위치")
            or data.get("재고 위치")
            or data.get("보관위치")
            or data.get("보관 위치")
            or data.get("창고위치")
            or data.get("창고 위치")
            or data.get("로케이션")
            or data.get("location")
            or data.get("storage_location")
        ),
        "location_registered": normalize_location_registered(
            data.get("위치상태")
            or data.get("위치 상태")
            or data.get("location_status")
            or data.get("location_registered")
        ),
        "pack_qty": combined_pallet_qty or to_int(data.get("입수") or data.get("pack_qty")),
        "box_qty": combined_box_qty or to_int(data.get("박스입수") or data.get("box_qty")) or to_box_unit_int(data.get("파렛트,박스단위")),
        "default_lead_time": to_int(
            data.get("기본 리드타임")
            or data.get("리드타임")
            or data.get("제조기간")
            or data.get("default_lead_time")
        ),
        "min_stock": to_int(
            data.get("최소재고")
            or data.get("안전재고")
            or data.get("경고수량")
            or data.get("위험수량")
            or data.get("min_stock")
        ),
        "sort_order": to_int(data.get("정렬순서") or data.get("sort_order") or data.get("순서")),
        "is_active": is_active if is_active in {"사용", "미사용"} else "사용",
        "memo": clean_text(data.get("비고") or data.get("담당자") or data.get("memo")),
    }


PRODUCT_MASTER_CATEGORY_FIELDS = ("large_category", "medium_category", "small_category")
PRODUCT_MASTER_TEXT_REFERENCE_FIELDS = (*PRODUCT_MASTER_CATEGORY_FIELDS, "memo")
PRODUCT_MASTER_NUMBER_REFERENCE_FIELDS = ("pack_qty", "box_qty")
PRODUCT_MASTER_CATEGORY_HEADERS = {"카테고리", "중분류", "소분류"}


def fill_down_product_master_categories(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    category_columns = [
        column
        for column in df.columns
        if normalize_import_header_name(str(column)) in PRODUCT_MASTER_CATEGORY_HEADERS
    ]
    if not category_columns:
        return df
    df = df.copy()
    product_columns = [
        column
        for column in df.columns
        if normalize_import_header_name(str(column))
        in {"SKU", "바코드", "상품명", "업체명", "재고위치", "위치상태", "박스/파렛트 단위", "담당자", "리드타임"}
    ]
    last_values = {column: "" for column in category_columns}
    for index in df.index:
        has_product_data = any(clean_text(df.at[index, column]) for column in product_columns)
        for column in category_columns:
            current_category = clean_text(df.at[index, column])
            if current_category:
                last_values[column] = current_category
            elif last_values[column] and has_product_data:
                df.at[index, column] = last_values[column]
    return df


def fill_down_threepl_master_categories(df: pd.DataFrame) -> pd.DataFrame:
    return fill_down_product_master_categories(df)


def keep_existing_product_master_categories(product, row: dict) -> dict:
    if product is None:
        return row
    next_row = dict(row)
    changed = False
    for field in PRODUCT_MASTER_TEXT_REFERENCE_FIELDS:
        if clean_text(next_row.get(field)):
            continue
        existing_value = clean_text(getattr(product, field, ""))
        if existing_value:
            next_row[field] = existing_value
            changed = True
    for field in PRODUCT_MASTER_NUMBER_REFERENCE_FIELDS:
        if to_int(next_row.get(field)) > 0:
            continue
        existing_value = to_int(getattr(product, field, 0))
        if existing_value > 0:
            next_row[field] = existing_value
            changed = True
    return next_row if changed else row


def keep_existing_threepl_category(product, row: dict) -> dict:
    return keep_existing_product_master_categories(product, row)


def prepare_product_master_import_rows(df: pd.DataFrame, source_type: str = "") -> tuple[list[dict], list[str]]:
    df = fill_down_product_master_categories(normalize_product_master_barcode_columns(df))
    rows = []
    warnings = []
    seen_skus: set[str] = set()
    seen_product_names: set[str] = set()
    skipped_empty = 0
    skipped_duplicate = 0

    for index, record in enumerate(df.fillna("").to_dict("records"), start=1):
        explicit_sku = explicit_sku_value(record)
        row = normalize_product_master_row(record)
        if source_type in {"오프라인", "창고"} and row["product_name"]:
            if not explicit_sku:
                row["sku"] = ""
        if not row["sku"] and not row["barcode"] and not row["product_name"]:
            skipped_empty += 1
            continue
        if not row["product_name"]:
            warnings.append(f"{index}행: 상품명이 누락되어 제외했습니다.")
            continue
        if row["sku"] and row["sku"] in seen_skus:
            skipped_duplicate += 1
            warnings.append(f"{index}행: 중복 SKU라 제외했습니다. ({row['sku']})")
            continue
        product_name_key = normalize_product_name_match_key(row["product_name"])
        if product_name_key in seen_product_names:
            skipped_duplicate += 1
            warnings.append(f"{index}행: 중복 상품명이라 제외했습니다. ({row['product_name']})")
            continue
        if row["sku"]:
            seen_skus.add(row["sku"])
        seen_product_names.add(product_name_key)
        rows.append(row)

    if skipped_empty:
        warnings.append(f"빈 행 {skipped_empty}건 제외")
    if skipped_duplicate:
        warnings.append(f"파일 내 중복 {skipped_duplicate}건 제외")
    return rows, warnings


def validate_product_master_rows(db: Session, source_type: str, rows: list[dict]) -> tuple[list[dict], list[str]]:
    model = product_master_model(source_type)
    normalized = [normalize_product_master_row(row) for row in rows]
    errors = []
    seen_skus: set[str] = set()
    seen_product_names: set[str] = set()
    sku_rows = db.execute(select(model.sku, model.barcode, model.product_name)).all()
    existing_product_name_to_sku = {normalize_product_name_match_key(product_name): sku for sku, _barcode, product_name in sku_rows if normalize_product_name_match_key(product_name)}
    used_skus = {clean_text(sku) for sku, _barcode, _product_name in sku_rows if clean_text(sku)}

    for index, row in enumerate(normalized, start=1):
        product_name_key = normalize_product_name_match_key(row["product_name"])
        if not row["sku"] and product_name_key:
            existing_sku = existing_product_name_to_sku.get(product_name_key)
            row["sku"] = existing_sku or auto_product_master_sku(row, used_skus, default_prefix=source_type)
        if not row["sku"] or not row["product_name"]:
            errors.append(f"{index}행: SKU, 상품명은 필수입니다.")
        if row["sku"] in seen_skus:
            errors.append(f"{index}행: 중복 SKU입니다. ({row['sku']})")
        if product_name_key and product_name_key in seen_product_names:
            errors.append(f"{index}행: 중복 상품명입니다. ({row['product_name']})")
        seen_skus.add(row["sku"])
        if product_name_key:
            seen_product_names.add(product_name_key)

    for index, row in enumerate(normalized, start=1):
        product_name_key = normalize_product_name_match_key(row["product_name"])
        existing_other_sku = existing_product_name_to_sku.get(product_name_key)
        if existing_other_sku and existing_other_sku != row["sku"]:
            errors.append(f"{index}행: 같은 상품명이 다른 SKU에 이미 등록되어 있습니다. ({row['product_name']})")
    return normalized, errors


THREEPL_MASTER_IMPORT_FIELDS = [
    ("large_category", "카테고리"),
    ("barcode", "바코드"),
    ("product_name", "상품명"),
    ("supplier", "업체명"),
    ("box_pallet_unit", "박스/파렛트 단위"),
    ("memo", "담당자"),
    ("default_lead_time", "리드타임"),
]


def explicit_sku_value(row: dict) -> str:
    data = row_data(row)
    return clean_text(
        data.get("SKU")
        or data.get("sku")
        or data.get("상품코드")
        or data.get("품목코드")
        or data.get("상품번호")
        or data.get("대표상품코드")
    )


def threepl_master_basis_data(row: dict) -> dict:
    data = {
        "sku": clean_text(row.get("sku")),
        "barcode": normalize_barcode_identifier(row.get("barcode")),
        "product_name": clean_text(row.get("product_name")),
        "large_category": clean_text(row.get("large_category")),
        "medium_category": clean_text(row.get("medium_category")),
        "small_category": clean_text(row.get("small_category")),
        "brand": clean_text(row.get("brand")),
        "supplier": clean_text(row.get("supplier")),
        "storage_location": clean_text(row.get("storage_location")),
        "location_registered": bool(row.get("location_registered")),
        "pack_qty": to_int(row.get("pack_qty")),
        "box_qty": to_int(row.get("box_qty")),
        "default_lead_time": to_int(row.get("default_lead_time")),
        "min_stock": to_int(row.get("min_stock")),
        "sort_order": to_int(row.get("sort_order")),
        "is_active": clean_text(row.get("is_active")) or "사용",
        "memo": clean_text(row.get("memo")),
    }
    return data


def threepl_master_display_value(row: dict | object, field: str) -> str:
    if field == "box_pallet_unit":
        if isinstance(row, dict):
            box_qty = to_int(row.get("box_qty"))
            pack_qty = to_int(row.get("pack_qty"))
        else:
            box_qty = to_int(getattr(row, "box_qty", 0))
            pack_qty = to_int(getattr(row, "pack_qty", 0))
        return format_box_pallet_unit(box_qty, pack_qty) or "0"
    if field == "location_registered":
        value = row.get(field) if isinstance(row, dict) else getattr(row, field, False)
        return location_status_label(value)
    value = row.get(field) if isinstance(row, dict) else getattr(row, field, "")
    return str(to_int(value)) if field == "default_lead_time" else clean_text(value)


def describe_threepl_master_changes(product, row: dict) -> list[str]:
    changes = []
    before_sku = clean_text(getattr(product, "sku", ""))
    after_sku = clean_text(row.get("sku"))
    if before_sku != after_sku:
        changes.append(f"SKU: {before_sku or '-'} → {after_sku or '-'}")
    for field, label in THREEPL_MASTER_IMPORT_FIELDS:
        before = threepl_master_display_value(product, field)
        after = threepl_master_display_value(row, field)
        if before != after:
            changes.append(f"{label}: {before or '-'} → {after or '-'}")
    return changes


def threepl_master_identity(row: dict) -> str:
    product_name = clean_text(row.get("product_name"))
    return f"name:{product_name}" if product_name else ""


def threepl_master_file_identity(row: dict) -> tuple[str, ...]:
    product_name = normalize_product_name_match_key(row.get("product_name"))
    return ("name", product_name) if product_name else ("empty",)


def threepl_master_file_identity_label(identity: tuple[str, ...]) -> str:
    return "상품명"


def product_db_id(product) -> int:
    return int(getattr(product, "id", 0) or 0)


def preferred_unclaimed_master_product(products: list[object], claimed_ids: set[int] | None = None) -> object | None:
    claimed_ids = claimed_ids or set()
    candidates = [product for product in products if product_db_id(product) not in claimed_ids]
    return preferred_master_product(candidates)


def match_threepl_replacement_product(existing_products: list[object], row: dict, claimed_ids: set[int] | None = None):
    claimed_ids = claimed_ids or set()
    product_name = normalize_product_name_match_key(row.get("product_name"))

    if product_name:
        name_matches = [
            product
            for product in existing_products
            if normalize_product_name_match_key(getattr(product, "product_name", "")) == product_name
        ]
        product = preferred_unclaimed_master_product(name_matches, claimed_ids)
        if product is not None:
            return product

    return None


def threepl_auto_sku(row: dict, used_skus: set[str]) -> str:
    return auto_product_master_sku(row, used_skus, default_prefix="3PL")


def auto_product_master_sku(row: dict, used_skus: set[str], default_prefix: str = "SKU") -> str:
    sku = clean_text(row.get("sku"))
    if sku:
        used_skus.add(sku)
        return sku

    product_name = clean_text(row.get("product_name"))
    base = product_name
    if base and base not in used_skus:
        used_skus.add(base)
        return base

    identity = product_name or base
    suffix = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:8]
    candidate = f"{base or default_prefix}-{suffix}"
    sequence = 2
    while candidate in used_skus:
        candidate = f"{base or default_prefix}-{suffix}-{sequence}"
        sequence += 1
    used_skus.add(candidate)
    return candidate


def validate_shared_master_headers(df: pd.DataFrame) -> tuple[bool, list[str], list[str]]:
    normalized_columns = {normalize_import_header_name(str(column)) for column in df.columns}
    missing = [column for column in SHARED_MASTER_FORM_COLUMNS if column not in normalized_columns]
    unexpected = [
        str(column)
        for column in df.columns
        if normalize_import_header_name(str(column)) not in SHARED_MASTER_KNOWN_COLUMNS
        and clean_text(column)
    ]
    return not missing, missing, unexpected


def find_master_by_product_name(existing_by_product_name: dict[str, object], row: dict):
    product_name_key = normalize_product_name_match_key(row.get("product_name"))
    if not product_name_key:
        return None
    return existing_by_product_name.get(product_name_key)


def find_master_for_shared_import(
    source_type: str,
    existing_by_product_name: dict[str, object],
    row: dict,
    existing_by_sku: dict[str, object] | None = None,
):
    _ = source_type
    sku_key = normalize_erp_product_code(row.get("sku"))
    product = find_master_by_product_name(existing_by_product_name, row)
    if product is not None:
        return product
    if existing_by_sku is not None and sku_key and not normalize_product_name_match_key(row.get("product_name")):
        product = existing_by_sku.get(sku_key)
        if product is not None:
            return product
    return None


def merge_threepl_uploaded_row_with_existing(product, row: dict) -> dict:
    if product is None:
        return row
    next_row = dict(row)
    uploaded_barcode = normalize_barcode_identifier(row.get("barcode"))
    existing_barcode = normalize_barcode_identifier(getattr(product, "barcode", ""))
    next_row["sku"] = clean_text(getattr(product, "sku", ""))
    next_row["barcode"] = uploaded_barcode or existing_barcode
    next_row["product_name"] = clean_text(row.get("product_name")) or clean_text(getattr(product, "product_name", ""))
    for field in ("brand", "storage_location"):
        if not clean_text(next_row.get(field)):
            next_row[field] = clean_text(getattr(product, field, ""))
    for field in ("min_stock", "sort_order"):
        if field in next_row and to_int(next_row.get(field)) <= 0:
            next_row[field] = to_int(getattr(product, field, 0))
    next_row["is_active"] = clean_text(row.get("is_active")) or clean_text(getattr(product, "is_active", "")) or "사용"
    next_row["location_registered"] = bool(getattr(product, "location_registered", False))
    return next_row


def product_master_apply_detail(
    row_number,
    row: dict,
    action: str,
    changes: list[str] | str,
    result: str,
    apply_row: bool,
    extra: dict | None = None,
) -> dict:
    detail = {
        "행 번호": row_number,
        "SKU": clean_text(row.get("sku")),
        "바코드": normalize_barcode_identifier(row.get("barcode")),
        "상품명": clean_text(row.get("product_name")),
        "처리 유형": action,
        "변경 항목": "\n".join(changes) if isinstance(changes, list) else clean_text(changes),
        "처리 결과": result,
        "_apply": apply_row,
        "_data": row,
    }
    if extra:
        detail.update(extra)
    return detail


def group_upload_sku_conflicts(parsed_rows: list[dict]) -> tuple[dict[str, list[dict]], set[int]]:
    rows_by_sku: dict[str, list[dict]] = {}
    for item in parsed_rows:
        sku_key = normalize_erp_product_code(item["data"].get("sku"))
        if sku_key:
            rows_by_sku.setdefault(sku_key, []).append(item)

    conflicts: dict[str, list[dict]] = {}
    conflict_row_numbers: set[int] = set()
    for sku_key, items in rows_by_sku.items():
        names = {
            normalize_product_name_match_key(item["data"].get("product_name"))
            for item in items
            if normalize_product_name_match_key(item["data"].get("product_name"))
        }
        if len(items) > 1 and len(names) > 1:
            conflicts[sku_key] = items
            conflict_row_numbers.update(int(item["row_number"]) for item in items)
    return conflicts, conflict_row_numbers


def product_master_existing_maps(products: list[object]) -> tuple[dict[str, object], dict[str, object]]:
    by_name: dict[str, object] = {}
    by_sku: dict[str, object] = {}
    for product in products:
        name_key = normalize_product_name_match_key(getattr(product, "product_name", ""))
        sku_key = normalize_erp_product_code(getattr(product, "sku", ""))
        if name_key:
            by_name.setdefault(name_key, product)
        if sku_key:
            by_sku.setdefault(sku_key, product)
    return by_name, by_sku


def prepare_product_master_shared_import_preview(
    db: Session,
    source_type: str,
    file_bytes: bytes,
    include_existing_products: bool = False,
) -> dict:
    started_at = time.perf_counter()
    raw_df = read_threepl_master_excel(file_bytes) if source_type == "3PL" else read_excel(file_bytes)
    df = fill_down_threepl_master_categories(normalize_product_master_barcode_columns(raw_df))
    total_rows = len(df)
    parsed_rows: list[dict] = []
    details: list[dict] = []
    product_name_row_numbers: dict[str, list[int]] = {}
    warnings = 0
    failures = 0

    headers_ok, missing_columns, unexpected_columns = validate_shared_master_headers(df)
    if not headers_ok:
        return {
            "ok": False,
            "message": f"마스터 양식에서 다음 컬럼을 찾을 수 없습니다: {', '.join(missing_columns)}",
            "summary": {
                "전체 엑셀 행 수": total_rows,
                "신규 등록 수": 0,
                "기존 품목 업데이트 수": 0,
                "변경 없음 수": 0,
                "파일 내부 중복 수": 0,
                "경고 수": len(unexpected_columns),
                "실패 수": len(missing_columns),
                "오류 수": len(missing_columns),
                "미매칭 수": 0,
                "처리시간": round(time.perf_counter() - started_at, 2),
            },
            "details": [],
            "missing_columns": missing_columns,
            "unexpected_columns": unexpected_columns,
            "used_html": df.attrs.get("read_method") == "html",
        }
    warnings += len(unexpected_columns)

    header_row_number = int(df.attrs.get("header_row_number") or 1)
    for index, record in enumerate(df.fillna("").to_dict("records"), start=header_row_number + 1):
        explicit_sku = explicit_sku_value(record)
        row = normalize_product_master_row(record)
        if not explicit_sku:
            row["sku"] = ""
        row_number = index
        if not row["sku"] and not row["barcode"] and not row["product_name"]:
            continue
        missing_required = not row["product_name"]
        if missing_required:
            failures += 1
            missing_message = "상품명 누락"
            details.append(
                {
                    "행 번호": row_number,
                    "바코드": row["barcode"],
                    "상품명": row["product_name"],
                    "처리 유형": "확인 필요",
                    "변경 항목": missing_message,
                    "처리 결과": "실패",
                    "_apply": False,
                    "_data": row,
                }
            )
            continue
        parsed_rows.append({"row_number": row_number, "data": row})
        product_name_key = normalize_product_name_match_key(row["product_name"])
        product_name_row_numbers.setdefault(product_name_key, []).append(row_number)

    model = product_master_model(source_type)
    existing_products = list(db.execute(select(model).order_by(model.id)).scalars())
    existing_by_product_name, _existing_by_sku = product_master_existing_maps(existing_products)
    sku_conflicts, conflict_row_numbers = group_upload_sku_conflicts(parsed_rows)
    last_row_by_product_name = {
        normalize_product_name_match_key(item["data"]["product_name"]): item
        for item in parsed_rows
        if int(item["row_number"]) not in conflict_row_numbers
    }

    duplicate_row_numbers: set[int] = set()
    for name_key, rows in product_name_row_numbers.items():
        non_conflict_rows = [row_number for row_number in rows if row_number not in conflict_row_numbers]
        if len(non_conflict_rows) > 1:
            duplicate_row_numbers.update(non_conflict_rows[:-1])
    for sku_key, items in sku_conflicts.items():
        warnings += 1
        conflict_rows = [
            {
                "행 번호": item["row_number"],
                "SKU": item["data"].get("sku", ""),
                "바코드": item["data"].get("barcode", ""),
                "상품명": item["data"].get("product_name", ""),
            }
            for item in items
        ]
        for item in items:
            details.append(
                product_master_apply_detail(
                    item["row_number"],
                    item["data"],
                    "충돌",
                    f"동일 SKU에 서로 다른 상품명이 있습니다. 충돌 SKU: {sku_key}",
                    "충돌 - 해당 행 제외",
                    False,
                    {
                        "충돌 SKU": sku_key,
                        "충돌 행": conflict_rows,
                    },
                )
            )

    duplicate_row_count = len(duplicate_row_numbers)
    used_skus: set[str] = set()
    used_sku_keys: set[str] = set()
    new_count = 0
    update_count = 0
    unchanged_count = 0
    duplicate_count = 0
    error_count = failures
    apply_name_keys: set[str] = set()
    conflict_name_keys = {
        normalize_product_name_match_key(item["data"].get("product_name"))
        for items in sku_conflicts.values()
        for item in items
    }
    for item in parsed_rows:
        if int(item["row_number"]) in conflict_row_numbers:
            continue
        row = item["data"]
        row_number = item["row_number"]
        product_name_key = normalize_product_name_match_key(row["product_name"])
        duplicate_item_rows = [
            row_no for row_no in product_name_row_numbers.get(product_name_key, []) if row_no not in conflict_row_numbers
        ]
        if duplicate_item_rows and last_row_by_product_name.get(product_name_key) is not item:
            warnings += 1
            duplicate_count += 1
            details.append(
                product_master_apply_detail(
                    row_number,
                    row,
                    "중복 제거",
                    f"동일 상품 중복 행: {', '.join(map(str, duplicate_item_rows))}",
                    "제외 - 마지막 행 정보 적용",
                    False,
                )
            )
            continue

        existing_product = existing_by_product_name.get(product_name_key)
        if not clean_text(row.get("sku")) and existing_product is not None:
            row["sku"] = clean_text(getattr(existing_product, "sku", ""))
        elif not clean_text(row.get("sku")):
            row["sku"] = auto_product_master_sku(row, used_skus, default_prefix=source_type)

        sku_key = normalize_erp_product_code(row["sku"])
        if sku_key and sku_key in used_sku_keys:
            warnings += 1
            error_count += 1
            details.append(
                product_master_apply_detail(
                    row_number,
                    row,
                    "충돌",
                    f"반영 직전 중복 SKU가 있습니다. ({row['sku']})",
                    "충돌 - 해당 행 제외",
                    False,
                )
            )
            continue
        if sku_key:
            used_sku_keys.add(sku_key)
        if clean_text(row.get("sku")):
            used_skus.add(clean_text(row.get("sku")))
        row_warnings = []
        warning_groups = []
        if duplicate_item_rows:
            warning_groups.append(f"상품명 {', '.join(map(str, duplicate_item_rows))}")
        if warning_groups:
            row_warnings.append(f"파일 내부 중복 - 마지막 행 적용: {' / '.join(warning_groups)}")
        if row_warnings:
            warnings += len(row_warnings)

        if not row["sku"] or not row["product_name"]:
            failures += 1
            error_count += 1
            action = "확인 필요"
            changes = ["SKU 또는 상품명 생성 실패"]
            result = "실패"
            apply_row = False
        elif existing_product is not None:
            changes = describe_threepl_master_changes(existing_product, row)
            apply_name_keys.add(product_name_key)
            if changes:
                update_count += 1
                action = "UPDATE"
                result = "갱신 예정"
            else:
                unchanged_count += 1
                action = "유지"
                result = "변경 없음"
            apply_row = True
        else:
            new_count += 1
            apply_name_keys.add(product_name_key)
            action = "INSERT"
            changes = [label for _field, label in THREEPL_MASTER_IMPORT_FIELDS]
            result = "등록 예정"
            apply_row = True

        if row_warnings:
            result = f"{result} / 경고: {'; '.join(row_warnings)}"
        details.append(
            product_master_apply_detail(
                row_number,
                row,
                action,
                changes if changes else "-",
                result,
                apply_row,
            )
        )

    applyable_count = sum(1 for detail in details if detail.get("_apply"))
    delete_count = sum(
        1
        for product in existing_products
        if normalize_product_name_match_key(getattr(product, "product_name", "")) not in apply_name_keys
        and normalize_product_name_match_key(getattr(product, "product_name", "")) not in conflict_name_keys
    )
    replacement_summary = {
        "기존 마스터": len(existing_products),
        "새 마스터 파일": applyable_count,
        "유지/갱신": unchanged_count + update_count,
        "신규": new_count,
        "삭제": delete_count,
        "중복 제거": duplicate_row_count,
        "충돌": len(conflict_row_numbers),
        "최종 마스터": len(existing_products) - delete_count + new_count,
        "추가 예정": new_count,
        "변경 예정": update_count,
        "삭제 예정": delete_count,
    }

    result = {
        "ok": True,
        "message": f"{source_type} 마스터 업로드 검증 완료",
        "summary": {
            **replacement_summary,
            "전체 엑셀 행 수": total_rows,
            "신규 등록 수": new_count,
            "기존 품목 업데이트 수": update_count,
            "변경 없음 수": unchanged_count,
            "파일 내부 중복 수": duplicate_row_count,
            "중복 수": duplicate_count,
            "미매칭 수": 0,
            "경고 수": warnings,
            "실패 수": failures,
            "오류 수": error_count,
            "처리시간": round(time.perf_counter() - started_at, 2),
        },
        "details": details,
        "source_type": source_type,
        "used_html": df.attrs.get("read_method") == "html",
    }
    if include_existing_products:
        result["_existing_products"] = existing_products
    return result


def prepare_threepl_master_import_preview(db: Session, file_bytes: bytes) -> dict:
    return prepare_product_master_shared_import_preview(db, "3PL", file_bytes)


def apply_product_master_replacement_preview(db: Session, source_type: str, preview: dict, sync_inventory: bool = False) -> dict:
    started_at = time.perf_counter()
    if not preview.get("ok", True):
        preview.setdefault("count", 0)
        return preview

    summary = dict(preview.get("summary") or {})
    details = preview.get("details") or []
    apply_details = [detail for detail in details if detail.get("_apply")]
    if not apply_details:
        return {
            "ok": False,
            "message": f"반영 가능한 {source_type} 마스터 행이 없습니다.",
            "count": 0,
            "summary": summary,
            "details": details,
        }

    model = product_master_model(source_type)
    existing_products = preview.get("_existing_products")
    if existing_products is None:
        existing_products = list(db.execute(select(model).order_by(model.id)).scalars())
    existing_by_product_name, _existing_by_sku = product_master_existing_maps(existing_products)
    used_sku_keys: set[str] = set()
    used_product_names: set[str] = set()
    assignments: list[tuple[dict, dict, object | None]] = []
    assignment_details: list[dict] = []
    conflict_name_keys = {
        normalize_product_name_match_key(detail.get("상품명"))
        for detail in details
        if clean_text(detail.get("처리 유형")) == "충돌"
    }

    for detail in apply_details:
        row = detail.get("_data") or {}
        data = product_master_model_data(model, threepl_master_basis_data(row))
        product_name_key = normalize_product_name_match_key(data.get("product_name"))
        product = existing_by_product_name.get(product_name_key)
        if not clean_text(data.get("sku")) and product is not None:
            data["sku"] = clean_text(getattr(product, "sku", ""))
        if not clean_text(data.get("sku")):
            data["sku"] = auto_product_master_sku(data, set(), default_prefix=source_type)

        sku = clean_text(data.get("sku"))
        sku_key = normalize_erp_product_code(sku)
        product_name_key = normalize_product_name_match_key(data.get("product_name"))
        if not sku or not product_name_key:
            result_detail = dict(detail)
            result_detail["처리 유형"] = "확인 필요"
            result_detail["처리 결과"] = "실패 - SKU 또는 상품명 생성 실패"
            result_detail["_apply"] = False
            result_detail.pop("_data", None)
            assignment_details.append(result_detail)
            continue
        if sku_key in used_sku_keys or product_name_key in used_product_names:
            result_detail = dict(detail)
            result_detail["처리 유형"] = "충돌"
            result_detail["처리 결과"] = "충돌 - 해당 행 제외"
            result_detail["변경 항목"] = f"반영 직전 중복 identity 감지: SKU={sku}, 상품명={data.get('product_name')}"
            result_detail["_apply"] = False
            result_detail.pop("_data", None)
            assignment_details.append(result_detail)
            conflict_name_keys.add(product_name_key)
            continue
        used_sku_keys.add(sku_key)
        used_product_names.add(product_name_key)
        assignments.append((data, detail, product))

    result_details = []
    matched_existing_ids = {product_db_id(product) for _data, _detail, product in assignments if product is not None}
    delete_products = [
        product
        for product in existing_products
        if product_db_id(product) not in matched_existing_ids
        and normalize_product_name_match_key(getattr(product, "product_name", "")) not in conflict_name_keys
    ]
    delete_ids = [product_db_id(product) for product in delete_products if product_db_id(product)]
    if delete_ids:
        db.execute(delete(model).where(model.id.in_(delete_ids)))

    inserted_count = 0
    updated_count = 0
    unchanged_count = 0
    insert_mappings = []
    update_mappings = []
    temporary_sku_mappings = []
    now = datetime.utcnow()
    for data, detail, product in assignments:
        if product is None:
            insert_data = dict(data)
            insert_data.setdefault("created_at", now)
            insert_data.setdefault("updated_at", now)
            insert_mappings.append(insert_data)
            inserted_count += 1
            action = "INSERT"
            change_text = "새 마스터 파일 기준 신규 생성"
            result_text = "등록 완료"
        else:
            changes = describe_threepl_master_changes(product, data)
            update_data = {"id": product_db_id(product), **data, "updated_at": now}
            if clean_text(getattr(product, "sku", "")) != clean_text(data.get("sku")):
                temporary_sku_mappings.append({"id": product_db_id(product), "sku": f"__import_tmp_{product_db_id(product)}"})
            update_mappings.append(update_data)
            if changes:
                updated_count += 1
                action = "UPDATE"
                change_text = "\n".join(changes)
                result_text = "갱신 완료"
            else:
                unchanged_count += 1
                action = "유지"
                change_text = "-"
                result_text = "변경 없음"
        result_detail = dict(detail)
        result_detail["SKU"] = data.get("sku", "")
        result_detail["바코드"] = data.get("barcode", "")
        result_detail["상품명"] = data.get("product_name", "")
        result_detail["처리 유형"] = action
        result_detail["변경 항목"] = change_text
        result_detail["처리 결과"] = result_text
        result_detail.pop("_data", None)
        result_details.append(result_detail)

    if temporary_sku_mappings:
        db.bulk_update_mappings(model, temporary_sku_mappings)
        db.flush()
    if update_mappings:
        db.bulk_update_mappings(model, update_mappings)
    if insert_mappings:
        db.bulk_insert_mappings(model, insert_mappings)

    for detail in details:
        if detail.get("_apply"):
            continue
        result_detail = dict(detail)
        result_detail.pop("_data", None)
        result_details.append(result_detail)

    for result_detail in assignment_details:
        result_details.append(result_detail)

    for product in delete_products:
        result_details.append(
            {
                "행 번호": "",
                "SKU": clean_text(getattr(product, "sku", "")),
                "바코드": clean_text(getattr(product, "barcode", "")),
                "상품명": clean_text(getattr(product, "product_name", "")),
                "처리 유형": "삭제",
                "변경 항목": "새 마스터 파일에 없음",
                "처리 결과": "삭제 완료",
            }
        )

    db.commit()
    final_count = db.execute(select(func.count()).select_from(model)).scalar_one()
    sync_summary = sync_inventory_from_product_master(db, source_type, return_summary=True) if sync_inventory else {}
    synced_count = int(sync_summary.get("count") or 0) if sync_summary else 0

    duplicate_removed = int(summary.get("중복 제거") or summary.get("파일 내부 중복 수") or 0)
    summary.update(
        {
            "기존 마스터": len(existing_products),
            "새 마스터 파일": len(assignments),
            "유지/갱신": unchanged_count + updated_count,
            "신규": inserted_count,
            "삭제": len(delete_products),
            "중복 제거": duplicate_removed,
            "최종 마스터": int(final_count or 0),
            "추가 예정": inserted_count,
            "변경 예정": updated_count,
            "삭제 예정": len(delete_products),
            "신규 등록 수": inserted_count,
            "기존 품목 업데이트 수": updated_count,
            "변경 없음 수": unchanged_count,
            "정상 반영 수": len(assignments),
            "DB 재조회 마스터 수": int(final_count or 0),
            "처리시간": round(time.perf_counter() - started_at, 2),
        }
    )
    if sync_summary:
        summary.update(
            {
                "재고 동기화 마스터 상품 수": int(sync_summary.get("마스터 상품 수") or 0),
                "InventoryDaily 생성 대상": int(sync_summary.get("InventoryDaily 생성 대상") or 0),
                "InventoryDaily 신규 생성": int(sync_summary.get("신규 생성") or 0),
                "InventoryDaily 기존 유지": int(sync_summary.get("기존 유지") or 0),
                "InventoryDaily 실패": int(sync_summary.get("실패") or 0),
            }
        )
    return {
        "ok": True,
        "message": f"{source_type} 마스터를 업로드 파일 기준으로 동기화 완료",
        "count": len(assignments),
        "synced_count": synced_count,
        "sync_summary": sync_summary,
        "summary": summary,
        "details": result_details,
        "_clear_inventory_cache": True,
    }


def apply_threepl_master_replacement_preview(db: Session, preview: dict) -> dict:
    return apply_product_master_replacement_preview(db, "3PL", preview, sync_inventory=False)


def apply_product_master_shared_import_preview(db: Session, source_type: str, preview: dict, sync_inventory: bool = False) -> dict:
    if use_legacy_supabase_rest_store():
        rows = [detail.get("_data") or {} for detail in preview.get("details", []) if detail.get("_apply")]
        result = supabase_store.bulk_save_product_master(source_type, rows, replace_existing=True)
        summary = dict(preview.get("summary") or {})
        result["summary"] = summary
        result["details"] = preview.get("details", [])
        return result

    return apply_product_master_replacement_preview(db, source_type, preview, sync_inventory=sync_inventory)


def apply_threepl_master_import_preview(db: Session, preview: dict, sync_inventory: bool = False) -> dict:
    return apply_product_master_shared_import_preview(db, "3PL", preview, sync_inventory)


def bulk_save_product_master(db: Session, source_type: str, rows: list[dict], sync_inventory: bool = True) -> dict:
    if use_legacy_supabase_rest_store():
        normalized = [normalize_product_master_row(row) for row in rows]
        return supabase_store.bulk_save_product_master(source_type, normalized)

    model = product_master_model(source_type)
    normalized, errors = validate_product_master_rows(db, source_type, rows)
    if errors:
        return {"ok": False, "message": "\n".join(errors[:5]), "count": 0}
    count = 0
    existing_products = list(db.execute(select(model)).scalars())
    existing_by_sku = {
        normalize_erp_product_code(product.sku): product
        for product in existing_products
        if normalize_erp_product_code(product.sku)
    }
    existing_by_product_name = {
        normalize_product_name_match_key(product.product_name): product
        for product in existing_products
        if normalize_product_name_match_key(product.product_name)
    }
    for row in normalized:
        sku_key = normalize_erp_product_code(row["sku"])
        product_name_key = normalize_product_name_match_key(row["product_name"])
        product = existing_by_product_name.get(product_name_key)
        if product is None and not product_name_key:
            product = existing_by_sku.get(sku_key)
        if product is None:
            product = model(**product_master_model_data(model, row))
            db.add(product)
            existing_by_sku[sku_key] = product
            if product_name_key:
                existing_by_product_name[product_name_key] = product
        else:
            row = keep_existing_product_master_categories(product, row)
            for key, value in product_master_model_data(model, row).items():
                setattr(product, key, value)
            existing_by_sku[sku_key] = product
            if product_name_key:
                existing_by_product_name[product_name_key] = product
        count += 1
    db.commit()
    synced_count = sync_inventory_from_product_master(db, source_type) if sync_inventory else 0
    message = f"{source_type} 상품 마스터 저장 완료"
    if not sync_inventory:
        message = f"{message} - 재고현황은 최신 마스터 기준으로 바로 조회됩니다"
    return {"ok": True, "message": message, "count": count, "synced_count": synced_count}


def add_product_master(db: Session, source_type: str, row: dict) -> dict:
    if use_legacy_supabase_rest_store():
        return supabase_store.bulk_save_product_master(source_type, [normalize_product_master_row(row)])

    model = product_master_model(source_type)
    normalized, errors = validate_product_master_rows(db, source_type, [row])
    if errors:
        return {"ok": False, "message": "\n".join(errors[:5]), "count": 0}

    data = normalized[0]
    sku_key = normalize_erp_product_code(data["sku"])
    existing_by_sku = next(
        (
            product
            for product in db.execute(select(model)).scalars()
            if normalize_erp_product_code(product.sku) == sku_key
        ),
        None,
    )
    if existing_by_sku:
        return {"ok": False, "message": f"이미 등록된 SKU입니다. ({existing_by_sku.sku})", "count": 0}
    existing_by_product_name = db.execute(
        select(model).where(model.product_name == data["product_name"])
    ).scalar_one_or_none()
    if existing_by_product_name:
        return {"ok": False, "message": f"이미 등록된 상품명입니다. ({data['product_name']})", "count": 0}

    db.add(model(**product_master_model_data(model, data)))
    db.commit()
    sync_inventory_from_product_master(db, source_type)
    return {"ok": True, "message": f"{source_type} 상품 단품 추가 완료", "count": 1}


def product_master_merge_field_names(model) -> list[str]:
    excluded = {"id", "sku", "created_at", "updated_at"}
    return [column.key for column in model.__table__.columns if column.key not in excluded]


def product_master_value_filled(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(clean_text(value))
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return True


def product_master_completeness_score(product) -> tuple[int, int, int]:
    model = type(product)
    fields = product_master_merge_field_names(model)
    filled = sum(1 for field in fields if product_master_value_filled(getattr(product, field, None)))
    text_length = sum(len(clean_text(getattr(product, field, ""))) for field in fields if isinstance(getattr(product, field, None), str))
    return filled, text_length, -int(getattr(product, "id", 0) or 0)


def product_master_duplicate_groups(db: Session, source_type: str | None = None) -> list[dict]:
    sources = [source_type] if source_type else list(PRODUCT_MASTER_MODEL_BY_SOURCE.keys())
    groups: list[dict] = []
    for source in sources:
        model = product_master_model(source)
        by_normalized_sku: dict[str, list[object]] = {}
        for product in db.execute(select(model).order_by(model.id)).scalars():
            sku_key = normalize_erp_product_code(product.sku)
            if not sku_key:
                continue
            by_normalized_sku.setdefault(sku_key, []).append(product)
        for sku_key, products in by_normalized_sku.items():
            if len(products) < 2:
                continue
            master = max(products, key=product_master_completeness_score)
            groups.append(
                {
                    "source_type": source,
                    "normalized_sku": sku_key,
                    "count": len(products),
                    "master_id": getattr(master, "id", None),
                    "master_sku": clean_text(getattr(master, "sku", "")),
                    "duplicate_skus": [clean_text(getattr(product, "sku", "")) for product in products],
                    "rows": products,
                }
            )
    return groups


def merge_product_master_duplicate_group(db: Session, source_type: str, products: list[object]) -> dict:
    if len(products) < 2:
        return {"removed_count": 0, "master_sku": clean_text(getattr(products[0], "sku", "")) if products else ""}
    model = product_master_model(source_type)
    master = max(products, key=product_master_completeness_score)
    duplicates = [product for product in products if product is not master and getattr(product, "id", None) != getattr(master, "id", None)]
    fields = product_master_merge_field_names(model)
    merged_fields = []
    for duplicate in duplicates:
        for field in fields:
            master_value = getattr(master, field, None)
            duplicate_value = getattr(duplicate, field, None)
            if not product_master_value_filled(master_value) and product_master_value_filled(duplicate_value):
                setattr(master, field, duplicate_value)
                merged_fields.append(field)

    duplicate_skus = [clean_text(getattr(product, "sku", "")) for product in duplicates if clean_text(getattr(product, "sku", ""))]
    master_sku = clean_text(getattr(master, "sku", ""))
    if duplicate_skus and master_sku:
        db.execute(
            update(InventoryDaily)
            .where(InventoryDaily.source_type == source_type, InventoryDaily.product_code.in_(duplicate_skus))
            .values(product_code=master_sku)
        )
        db.execute(
            update(InventoryInbound)
            .where(InventoryInbound.source_type == source_type, InventoryInbound.product_code.in_(duplicate_skus))
            .values(product_code=master_sku)
        )
        db.execute(
            update(InventoryUploadSnapshot)
            .where(InventoryUploadSnapshot.source_type == source_type, InventoryUploadSnapshot.product_code.in_(duplicate_skus))
            .values(product_code=master_sku)
        )

    for duplicate in duplicates:
        db.delete(duplicate)
    return {
        "removed_count": len(duplicates),
        "master_id": getattr(master, "id", None),
        "master_sku": master_sku,
        "merged_fields": sorted(set(merged_fields)),
        "removed_skus": duplicate_skus,
    }


def merge_duplicate_erp_product_masters(db: Session, source_type: str | None = None, dry_run: bool = False) -> dict:
    groups = product_master_duplicate_groups(db, source_type)
    duplicate_rows_before = sum(max(int(group["count"]) - 1, 0) for group in groups)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "duplicate_group_count": len(groups),
            "duplicate_rows_before": duplicate_rows_before,
            "removed_count": 0,
            "groups": [
                {
                    key: value
                    for key, value in group.items()
                    if key != "rows"
                }
                for group in groups
            ],
        }

    merge_results = [
        merge_product_master_duplicate_group(db, group["source_type"], group["rows"])
        for group in groups
    ]
    removed_count = sum(int(result.get("removed_count") or 0) for result in merge_results)
    db.commit()
    return {
        "ok": True,
        "dry_run": False,
        "duplicate_group_count": len(groups),
        "duplicate_rows_before": duplicate_rows_before,
        "removed_count": removed_count,
        "groups": [
            {
                key: value
                for key, value in group.items()
                if key != "rows"
            }
            for group in groups
        ],
        "merge_results": merge_results,
    }


def import_product_master_excel(db: Session, source_type: str, file_bytes: bytes) -> dict:
    preview = prepare_product_master_shared_import_preview(db, source_type, file_bytes, include_existing_products=True)
    if not preview.get("ok", True):
        preview.setdefault("count", 0)
        return preview
    if not any(detail.get("_apply") for detail in preview.get("details", [])):
        preview.update({"ok": False, "message": f"반영 가능한 {source_type} 마스터 행이 없습니다.", "count": 0})
        return preview
    return apply_product_master_shared_import_preview(db, source_type, preview, sync_inventory=False)


def merge_inventory_daily_rows(target: InventoryDaily, duplicate: InventoryDaily) -> InventoryDaily:
    if target.id == duplicate.id:
        return target
    for field in ("current_stock", "available_stock", "outbound_qty", "inbound_qty"):
        setattr(target, field, int(getattr(target, field) or 0) + int(getattr(duplicate, field) or 0))
    target.safe_stock = max(int(target.safe_stock or 0), int(duplicate.safe_stock or 0))
    target.inbound_cycle = target.inbound_cycle or duplicate.inbound_cycle
    dates = [value for value in (target.last_inbound_date, duplicate.last_inbound_date) if value]
    if dates:
        target.last_inbound_date = max(dates)
    previous_dates = [value for value in (target.previous_inbound_date, duplicate.previous_inbound_date) if value]
    if previous_dates:
        target.previous_inbound_date = max(previous_dates)
    if duplicate.memo and duplicate.memo not in (target.memo or ""):
        target.memo = f"{target.memo}\n{duplicate.memo}".strip()
    return target


def find_inventory_daily_unique_conflict(
    db: Session,
    source_type: str,
    work_date: date,
    product_name: str,
    barcode: str = "",
    exclude_id: int | None = None,
) -> InventoryDaily | None:
    """Return an existing daily row with the same unique inventory identity."""
    target_name = clean_text(product_name)
    query = select(InventoryDaily).where(
        InventoryDaily.source_type == source_type,
        InventoryDaily.work_date == work_date,
        InventoryDaily.product_name == target_name,
    )
    if exclude_id is not None:
        query = query.where(InventoryDaily.id != exclude_id)
    with db.no_autoflush:
        return db.execute(query.order_by(InventoryDaily.id)).scalars().first()


def resolve_inventory_daily_edit_target(
    db: Session,
    daily: InventoryDaily | None,
    source_type: str,
    work_date: date,
    product_name: str,
    barcode: str = "",
) -> tuple[InventoryDaily, bool]:
    """Choose the row to update for an inventory edit without violating the daily unique key."""
    conflict = find_inventory_daily_unique_conflict(
        db,
        source_type,
        work_date,
        product_name,
        barcode,
        exclude_id=daily.id if daily is not None else None,
    )
    if conflict is not None:
        if daily is not None and daily.id != conflict.id:
            db.delete(daily)
        return conflict, True
    if daily is None:
        daily = InventoryDaily(source_type=source_type, work_date=work_date)
        db.add(daily)
    return daily, False


def replace_inventory_daily_lookup_row(
    lookup: dict[str, InventoryDaily],
    sku: str,
    row: InventoryDaily,
    replaced: InventoryDaily | None = None,
) -> None:
    normalized_sku = normalize_product_code_text(sku)
    if replaced is not None and replaced is not row:
        replaced_id = replaced.id
        for key, candidate in list(lookup.items()):
            if candidate is replaced or (replaced_id is not None and candidate.id == replaced_id):
                lookup.pop(key, None)
    row_id = row.id
    for key, candidate in list(lookup.items()):
        if key != normalized_sku and (candidate is row or (row_id is not None and candidate.id == row_id)):
            lookup.pop(key, None)
    if normalized_sku:
        lookup[normalized_sku] = row


def inventory_daily_upsert_statement(db: Session, values: dict):
    dialect_name = db.get_bind().dialect.name
    insert_fn = postgresql_insert if dialect_name == "postgresql" else sqlite_insert
    statement = insert_fn(InventoryDaily).values(**values)
    update_values = {
        key: statement.excluded[key]
        for key in values
        if key not in {"source_type", "work_date", "product_name"}
    }
    return statement.on_conflict_do_update(
        index_elements=["source_type", "work_date", "product_name"],
        set_=update_values,
    )


def inventory_daily_bulk_upsert_statement(db: Session, values: list[dict]):
    dialect_name = db.get_bind().dialect.name
    insert_fn = postgresql_insert if dialect_name == "postgresql" else sqlite_insert
    statement = insert_fn(InventoryDaily).values(values)
    update_values = {
        key: statement.excluded[key]
        for key in values[0]
        if key not in {"source_type", "work_date", "product_name"}
    }
    return statement.on_conflict_do_update(
        index_elements=["source_type", "work_date", "product_name"],
        set_=update_values,
    )


def execute_inventory_daily_bulk_upsert(db: Session, values: list[dict], chunk_size: int = 500) -> int:
    count = 0
    for start in range(0, len(values), chunk_size):
        chunk = values[start : start + chunk_size]
        if not chunk:
            continue
        db.execute(inventory_daily_bulk_upsert_statement(db, chunk))
        count += len(chunk)
    return count


def delete_stale_inventory_daily_product_rows(
    db: Session,
    source_type: str,
    work_date: date,
    product_code: str,
    product_name: str,
    barcode: str,
) -> int:
    sku = normalize_product_code_text(product_code)
    if not sku:
        return 0
    result = db.execute(
        delete(InventoryDaily).where(
            InventoryDaily.source_type == source_type,
            InventoryDaily.work_date == work_date,
            InventoryDaily.product_code == sku,
            InventoryDaily.product_name != clean_text(product_name),
        )
    )
    return int(result.rowcount or 0)


def previous_inventory_daily_stock(
    db: Session,
    source_type: str,
    work_date: date,
    product_code: str,
    product_name: str,
    barcode: str,
) -> int:
    final_row = find_inventory_daily_unique_conflict(db, source_type, work_date, product_name, barcode)
    if final_row is not None:
        return int(final_row.current_stock or 0)
    sku = normalize_product_code_text(product_code)
    if not sku:
        return 0
    with db.no_autoflush:
        stale_row = db.execute(
            select(InventoryDaily)
            .where(
                InventoryDaily.source_type == source_type,
                InventoryDaily.work_date == work_date,
                InventoryDaily.product_code == sku,
            )
            .order_by(InventoryDaily.id)
        ).scalars().first()
    return int(stale_row.current_stock or 0) if stale_row is not None else 0


def apply_product_master_to_daily_without_unique_conflict(
    db: Session,
    item: InventoryDaily,
    product,
    deleted_ids: set[int] | None = None,
) -> InventoryDaily:
    if not product:
        return item
    target_name = product.product_name
    conflict = None
    if target_name:
        with db.no_autoflush:
            conflict = db.execute(
                select(InventoryDaily)
                .where(
                    InventoryDaily.source_type == item.source_type,
                    InventoryDaily.work_date == item.work_date,
                    InventoryDaily.product_name == target_name,
                    InventoryDaily.id != item.id,
                )
                .order_by(InventoryDaily.id)
            ).scalars().first()
    if conflict is not None:
        merge_inventory_daily_rows(conflict, item)
        db.delete(item)
        if deleted_ids is not None and item.id is not None:
            deleted_ids.add(item.id)
        item = conflict
    apply_product_master_to_daily(item, product)
    return item


def sync_inventory_from_product_master(db: Session, source_type: str | None = None, return_summary: bool = False) -> int | dict:
    if use_legacy_supabase_rest_store():
        sources = [source_type] if source_type else list(PRODUCT_MASTER_MODEL_BY_SOURCE.keys())
        count = sum(len(supabase_store.master_based_inventory_rows(source, date.today())) for source in sources)
        if return_summary:
            return {
                "ok": True,
                "count": count,
                "마스터 상품 수": count,
                "InventoryDaily 생성 대상": count,
                "신규 생성": 0,
                "기존 유지": count,
                "실패": 0,
            }
        return count

    updated_count = 0
    inbound_updated_count = 0
    created_count = 0
    kept_count = 0
    failed_count = 0
    master_count = 0
    target_date = date.today()
    sources = [source_type] if source_type else list(PRODUCT_MASTER_MODEL_BY_SOURCE.keys())
    lookup_by_source: dict[str, tuple[dict[str, object], dict[tuple[str, str], object], dict[str, list], dict[str, list]]] = {}
    for source in sources:
        model = product_master_model(source)
        products = list(db.execute(select(model)).scalars())
        master_count += len(products)
        lookup_by_source[source] = product_lookup_maps(products)

    daily_query = select(InventoryDaily)
    inbound_query = select(InventoryInbound)
    if source_type:
        daily_query = daily_query.where(InventoryDaily.source_type == source_type)
        inbound_query = inbound_query.where(InventoryInbound.source_type == source_type)
    for item in list(db.execute(daily_query).scalars()):
        maps = lookup_by_source.get(item.source_type)
        if not maps:
            continue
        product = match_product_from_maps(item.product_code, item.barcode, item.product_name, *maps, source_type=item.source_type)
        if product:
            values = {
                "source_type": item.source_type,
                "work_date": item.work_date,
                "category": product_category_text(product) or clean_text(item.category),
                "product_code": product.sku,
                "product_name": clean_text(product.product_name),
                "barcode": normalize_barcode_text(product.barcode),
                "supplier": product.supplier,
                "current_stock": int(item.current_stock or 0),
                "available_stock": int(item.available_stock or 0),
                "safe_stock": int(item.safe_stock or product.min_stock or 0),
                "stock_status": item.stock_status,
                "outbound_qty": int(item.outbound_qty or 0),
                "previous_inbound_date": item.previous_inbound_date,
                "last_inbound_date": item.last_inbound_date,
                "inbound_qty": int(item.inbound_qty or 0),
                "inbound_cycle": item.inbound_cycle or product.default_lead_time or None,
                "memo": clean_text(item.memo),
                "updated_at": datetime.utcnow(),
            }
            if model_has_field(InventoryDaily, "storage_location"):
                values["storage_location"] = clean_text(getattr(product, "storage_location", "")) or clean_text(getattr(item, "storage_location", ""))
            db.execute(inventory_daily_upsert_statement(db, values))
            updated_count += 1
    for item in list(db.execute(inbound_query).scalars()):
        maps = lookup_by_source.get(item.source_type)
        if not maps:
            continue
        product = match_product_from_maps(item.product_code, item.barcode, item.product_name, *maps, source_type=item.source_type)
        if product:
            apply_product_master_to_inbound(item, product)
            inbound_updated_count += 1

    for source in sources:
        model = product_master_model(source)
        products = list(db.execute(select(model).order_by(model.id)).scalars())
        for product in products:
            try:
                with db.begin_nested():
                    existed = daily_row_for_product(db, source, target_date, product) is not None
                    row = ensure_daily_for_product(db, source, target_date, product)
                    row.product_code = product.sku
                    row.product_name = clean_text(product.product_name)
                    row.barcode = normalize_barcode_text(product.barcode)
                    row.category = product_category_text(product) or row.category
                    row.supplier = product.supplier
                    if model_has_field(InventoryDaily, "storage_location"):
                        row.storage_location = clean_text(getattr(product, "storage_location", "")) or clean_text(getattr(row, "storage_location", ""))
                    row.safe_stock = int(product.min_stock or 0)
                    row.inbound_cycle = product.default_lead_time or None
                    row.stock_status = inventory_stock_status_for_daily_row(row)
                    db.flush()
                if existed:
                    kept_count += 1
                else:
                    created_count += 1
            except Exception:
                failed_count += 1

    db.commit()
    count = updated_count + inbound_updated_count + created_count + kept_count
    summary = {
        "ok": failed_count == 0,
        "count": count,
        "마스터 상품 수": master_count,
        "InventoryDaily 생성 대상": master_count,
        "신규 생성": created_count,
        "기존 유지": kept_count,
        "실패": failed_count,
        "기존 재고 갱신": updated_count,
        "입고 데이터 갱신": inbound_updated_count,
        "기준일": target_date.isoformat(),
    }
    return summary if return_summary else count


def list_daily(db: Session, source_type: str, work_date: date) -> list[InventoryDaily]:
    if use_legacy_supabase_rest_store():
        return supabase_store.list_daily(source_type, work_date)
    return list(
        db.execute(
            select(InventoryDaily)
            .where(InventoryDaily.source_type == source_type, InventoryDaily.work_date == work_date)
            .order_by(InventoryDaily.category, InventoryDaily.product_name, InventoryDaily.barcode)
        ).scalars()
    )


def daily_row_for_product(db: Session, source_type: str, work_date: date, product) -> InventoryDaily | None:
    product_name = clean_text(product.product_name)
    if not product_name:
        return None
    return db.execute(
        select(InventoryDaily)
        .where(
            InventoryDaily.source_type == source_type,
            InventoryDaily.work_date == work_date,
            InventoryDaily.product_name == product_name,
        )
        .order_by(InventoryDaily.id)
    ).scalars().first()


def ensure_daily_for_product(db: Session, source_type: str, work_date: date, product) -> InventoryDaily:
    row = daily_row_for_product(db, source_type, work_date, product)
    if row is None:
        row = InventoryDaily(
            source_type=source_type,
            work_date=work_date,
            product_code=product.sku,
            product_name=product.product_name,
            barcode=normalize_barcode_text(product.barcode),
            category=product.large_category,
            supplier=product.supplier,
            safe_stock=product.min_stock,
            inbound_cycle=product.default_lead_time or None,
        )
        db.add(row)
        db.flush()
    row = apply_product_master_to_daily_without_unique_conflict(db, row, product)
    row.safe_stock = product.min_stock
    row.inbound_cycle = product.default_lead_time or None
    return row


def stock_warning_limit(safe_stock: int, warning_ratio: float = STOCK_WARNING_RATIO) -> int:
    if safe_stock <= 0:
        return 0
    return safe_stock + ceil(safe_stock * warning_ratio)


def stock_status_for_values(current_stock: int, safe_stock: int, warning_ratio: float = STOCK_WARNING_RATIO) -> str:
    if current_stock <= 0:
        return "품절"
    if safe_stock > 0 and current_stock <= safe_stock:
        return "부족"
    if safe_stock > 0 and current_stock <= stock_warning_limit(safe_stock, warning_ratio):
        return "주의"
    return "정상"


def stock_status_for_snapshot(has_snapshot: bool, available_stock, current_stock, safe_stock: int) -> str:
    if not has_snapshot:
        return "미집계"
    stock_value = int(available_stock if available_stock is not None else current_stock or 0)
    return stock_status_for_values(stock_value, safe_stock)


def inventory_stock_status_for_snapshot(
    has_snapshot: bool,
    available_stock,
    current_stock,
    safe_stock: int,
    pending_outbound_qty: int = 0,
) -> str:
    if not has_snapshot:
        return "미집계"
    stock_value = int(available_stock if available_stock is not None else current_stock or 0)
    pending_outbound = max(int(pending_outbound_qty or 0), 0)
    if stock_value <= 0:
        return "품절"
    if pending_outbound > 0 and stock_value < pending_outbound:
        return "부족"
    if safe_stock > 0 and stock_value <= stock_warning_limit(safe_stock):
        return "주의"
    return "정상"


def inventory_stock_status_for_daily_row(row: InventoryDaily) -> str:
    return inventory_stock_status_for_snapshot(
        True,
        getattr(row, "available_stock", None),
        getattr(row, "current_stock", 0),
        int(getattr(row, "safe_stock", 0) or 0),
        int(getattr(row, "outbound_qty", 0) or 0),
    )


def format_box_pallet_unit(box_qty: int | None, pallet_qty: int | None) -> str:
    parts = []
    if int(box_qty or 0):
        parts.append(f"박스당 {int(box_qty or 0)}EA")
    if int(pallet_qty or 0):
        parts.append(f"파렛트당 {int(pallet_qty or 0)}BOX")
    return " / ".join(parts)


def parse_box_pallet_unit(value) -> tuple[int, int]:
    text = clean_text(value).upper().replace(",", "")
    if not text:
        return 0, 0
    box_qty = 0
    pallet_qty = 0
    box_prefix_match = re.search(r"\d+\s*(?:박스|BOX)\s*(\d+)\s*(?:EA|개)?", text)
    if box_prefix_match:
        box_qty = to_int(box_prefix_match.group(1))
    box_match = re.search(r"(?:박스당|박스|BOX)\s*(\d+)\s*EA", text) or re.search(r"(\d+)\s*EA", text)
    pallet_match = re.search(r"(?:파렛트당|파렛트|PALLET|PL)\D*(\d+)\s*BOX", text) or re.search(r"/[^/]*?(\d+)\s*BOX", text)
    if box_match and not box_qty:
        box_qty = to_int(box_match.group(1))
    if pallet_match:
        pallet_qty = to_int(pallet_match.group(1))
    if not box_qty or not pallet_qty:
        numbers = [to_int(number) for number in re.findall(r"\d+", text)]
        if numbers and not box_qty:
            box_qty = numbers[0]
        if len(numbers) > 1 and not pallet_qty:
            pallet_qty = numbers[-1]
    return box_qty, pallet_qty


def pending_inbound_qty_for_product(db: Session, source_type: str, work_date: date, product) -> int:
    value = db.execute(
        select(func.sum(InventoryInbound.inbound_qty)).where(
            InventoryInbound.source_type == source_type,
            InventoryInbound.inbound_date >= work_date,
            InventoryInbound.is_applied == False,  # noqa: E712
            InventoryInbound.product_name == product.product_name,
        )
    ).scalar()
    return int(value or 0)


def inventory_master_rows_for_display(source_type: str, products: list[object]) -> list[object]:
    result = []
    groups: dict[str, list[object]] = {}
    group_order: list[str] = []
    for product in products:
        product_name = normalize_product_name_match_key(getattr(product, "product_name", ""))
        if not product_name:
            result.append(product)
            continue
        if product_name not in groups:
            group_order.append(product_name)
        groups.setdefault(product_name, []).append(product)

    preferred_by_identity = {
        identity: preferred_master_product(matches)
        for identity, matches in groups.items()
    }
    for identity in group_order:
        product = preferred_by_identity.get(identity)
        if product is not None:
            result.append(product)
    return result


def product_daily_lookup_key(product) -> str:
    product_name = normalize_product_name_match_key(getattr(product, "product_name", ""))
    return product_name or normalize_product_code_text(getattr(product, "sku", ""))


def daily_rows_by_product(db: Session, source_type: str, work_date: date, products: list) -> dict[str, InventoryDaily]:
    rows = list(
        db.execute(
            select(InventoryDaily).where(
                InventoryDaily.source_type == source_type,
                InventoryDaily.work_date == work_date,
            ).order_by(InventoryDaily.id)
        ).scalars()
    )
    by_name: dict[str, list[InventoryDaily]] = {}
    for row in rows:
        name = normalize_product_name_match_key(row.product_name)
        if name:
            by_name.setdefault(name, []).append(row)

    matched: dict[str, InventoryDaily] = {}
    product_name_counts: dict[str, int] = {}
    for product in products:
        name_key = normalize_product_name_match_key(product.product_name)
        if name_key:
            product_name_counts[name_key] = product_name_counts.get(name_key, 0) + 1
    for product in products:
        product_sku = normalize_product_code_text(product.sku)
        product_name = normalize_product_name_match_key(product.product_name)
        row = None
        if row is None and product_name and product_name_counts.get(product_name, 0) == 1 and len(by_name.get(product_name, [])) == 1:
            row = by_name[product_name][0]
        if row is not None and product_sku:
            matched[product_daily_lookup_key(product)] = row
    trace_barcode = normalize_barcode_text(INVENTORY_TRACE_BARCODE)
    trace_products = [product for product in products if normalize_barcode_text(getattr(product, "barcode", "")) == trace_barcode]
    trace_daily_rows = [row for row in rows if normalize_barcode_text(getattr(row, "barcode", "")) == trace_barcode]
    write_inventory_trace(
        INVENTORY_RENDER_LOG_PATH,
        "INVENTORY SNAPSHOT JOIN TRACE",
        {
            "조회 table": getattr(InventoryDaily, "__tablename__", "inventory_daily"),
            "source_type": source_type,
            "work_date": work_date,
            "barcode": trace_barcode,
            "product_name": [clean_text(getattr(product, "product_name", "")) for product in trace_products],
            "inventory_rows_found": len(rows),
            "master_rows": len(products),
            "joined_rows": len(matched),
            "trace_master_rows": [product_trace_row(product) for product in trace_products],
            "trace_inventory_rows": [inventory_trace_row(row) for row in trace_daily_rows],
            "trace_joined_skus": [
                normalize_product_code_text(getattr(product, "sku", ""))
                for product in trace_products
                if normalize_product_code_text(getattr(product, "sku", "")) in matched
            ],
            "join_key": "exact product_name only",
        },
    )
    return matched


def strict_daily_rows_by_product(db: Session, source_type: str, work_date: date, products: list) -> dict[str, InventoryDaily]:
    rows = list(
        db.execute(
            select(InventoryDaily).where(
                InventoryDaily.source_type == source_type,
                InventoryDaily.work_date == work_date,
            )
        ).scalars()
    )
    by_name = {normalize_product_name_match_key(row.product_name): row for row in rows if normalize_product_name_match_key(row.product_name)}
    matched: dict[str, InventoryDaily] = {}
    for product in products:
        sku = normalize_product_code_text(product.sku)
        if not sku:
            continue
        name = normalize_product_name_match_key(product.product_name)
        row = by_name.get(name) if name else None
        if row is not None:
            matched[sku] = row
    return matched


def latest_daily_rows_by_product(db: Session, source_type: str, work_date: date, products: list) -> dict[str, InventoryDaily]:
    skus = [clean_text(product.sku) for product in products if clean_text(product.sku)]
    if not skus:
        return {}

    sku_set = set(skus)
    row_number = func.row_number().over(
        partition_by=InventoryDaily.product_code,
        order_by=(InventoryDaily.work_date.desc(), InventoryDaily.id.desc()),
    ).label("row_number")
    latest_by_sku = (
        select(InventoryDaily.id, InventoryDaily.product_code, row_number)
        .where(
            InventoryDaily.source_type == source_type,
            InventoryDaily.work_date <= work_date,
            InventoryDaily.product_code.in_(skus),
        )
        .subquery()
    )
    matched: dict[str, InventoryDaily] = {}
    sku_rows = list(
        db.execute(
            select(InventoryDaily)
            .join(latest_by_sku, InventoryDaily.id == latest_by_sku.c.id)
            .where(latest_by_sku.c.row_number == 1)
        ).scalars()
    )
    for row in sku_rows:
        sku = clean_text(row.product_code)
        if sku in sku_set:
            matched[sku] = row
    if len(matched) == len(sku_set):
        return matched

    remaining_products = [product for product in products if clean_text(product.sku) and clean_text(product.sku) not in matched]
    if not remaining_products:
        return matched

    cutoff_date = work_date - timedelta(days=120)
    rows = list(
        db.execute(
            select(InventoryDaily)
            .where(
                InventoryDaily.source_type == source_type,
                InventoryDaily.work_date <= work_date,
                InventoryDaily.work_date >= cutoff_date,
            )
            .order_by(InventoryDaily.work_date.desc(), InventoryDaily.id.desc())
        ).scalars()
    )
    by_sku, by_barcode_name, by_barcode, by_name = product_lookup_maps(remaining_products)
    for row in rows:
        product = match_product_from_maps(row.product_code, row.barcode, row.product_name, by_sku, by_barcode_name, by_barcode, by_name, source_type=source_type)
        if product is None:
            continue
        sku = clean_text(product.sku)
        if sku and sku not in matched:
            matched[sku] = row
    return matched


def recent_business_days(target: date, count: int = 10) -> list[date]:
    days: list[date] = []
    current = target
    while len(days) < count:
        if is_business_day(current):
            days.append(current)
        current -= timedelta(days=1)
    return days


def recent_outbound_average_by_product(
    db: Session,
    source_type: str,
    work_date: date,
    products: list,
    business_day_count: int = 10,
) -> dict[str, float]:
    days = recent_business_days(work_date, business_day_count)
    if not days:
        return {}
    by_sku, by_barcode_name, by_barcode, by_name = product_lookup_maps(products)
    totals: dict[str, int] = {}
    rows = list(
        db.execute(
            select(InventoryDaily)
            .where(
                InventoryDaily.source_type == source_type,
                InventoryDaily.work_date.in_(days),
                InventoryDaily.outbound_qty != 0,
            )
        ).scalars()
    )
    for row in rows:
        product = match_product_from_maps(row.product_code, row.barcode, row.product_name, by_sku, by_barcode_name, by_barcode, by_name, source_type=source_type)
        if product is None:
            continue
        sku = clean_text(product.sku)
        if sku:
            totals[sku] = totals.get(sku, 0) + int(row.outbound_qty or 0)
    divisor = max(len(days), 1)
    return {sku: round(total / divisor, 2) for sku, total in totals.items()}


def order_needed_days(
    current_stock: int,
    safe_stock: int,
    avg_daily_outbound: float,
    lead_time_days: int = 0,
    pending_outbound_qty: int = 0,
) -> int | None:
    if avg_daily_outbound <= 0:
        return None
    stock_after_pending = int(current_stock or 0) - max(int(pending_outbound_qty or 0), 0)
    if stock_after_pending <= 0:
        return 0
    days_until_stockout = stock_after_pending / avg_daily_outbound
    return max(ceil(days_until_stockout - max(int(lead_time_days or 0), 0)), 0)


def pending_inbound_qty_by_product(db: Session, source_type: str, work_date: date, products: list) -> dict[str, int]:
    by_sku, by_barcode_name, by_barcode, by_name = product_lookup_maps(products)

    aggregate_rows = db.execute(
        select(
            InventoryInbound.product_code,
            InventoryInbound.barcode,
            InventoryInbound.product_name,
            func.sum(InventoryInbound.inbound_qty),
        )
        .where(
            InventoryInbound.source_type == source_type,
            InventoryInbound.inbound_date >= work_date,
            InventoryInbound.is_applied == False,  # noqa: E712
        )
        .group_by(InventoryInbound.product_code, InventoryInbound.barcode, InventoryInbound.product_name)
    ).all()

    pending: dict[str, int] = {}
    for product_code, barcode, product_name, quantity in aggregate_rows:
        product = match_product_from_maps(product_code, barcode, product_name, by_sku, by_barcode_name, by_barcode, by_name, source_type=source_type)
        if product is None:
            continue
        matched_sku = clean_text(product.sku)
        if matched_sku:
            pending[matched_sku] = pending.get(matched_sku, 0) + int(quantity or 0)
    return pending


def canonical_warehouse_rack_code(value: object, fallback_index: int = 1) -> str:
    text = clean_text(value).upper()
    match = re.fullmatch(r"R-(\d{1,3})", text)
    if match:
        return f"A-{int(match.group(1)):02d}"
    match = re.fullmatch(r"([A-Z])-(\d{1,3})", text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}"
    if text:
        return text
    return f"A-{max(1, int(fallback_index or 1)):02d}"


def warehouse_position_label(layout: WarehouseLayout, rack: WarehouseRack, position: WarehouseInventoryPosition) -> tuple[str, str]:
    building = clean_text(getattr(layout, "building", ""))
    floor = clean_text(getattr(layout, "floor", ""))
    rack_label = canonical_warehouse_rack_code(
        clean_text(rack.rack_code) or clean_text(rack.rack_name) or clean_text(position.rack_id)
    )
    location_prefix = " ".join(part for part in (building, floor) if part)
    short_label = " / ".join(part for part in (location_prefix, rack_label) if part)
    detail_label = " / ".join(
        part
        for part in (
            building,
            floor,
            rack_label,
        )
        if part
    )
    return short_label, detail_label or short_label


def warehouse_position_status(current_stock: int, placed_qty: int, has_locations: bool) -> str:
    return "위치등록" if has_locations else "위치미등록"


def warehouse_inventory_position_summaries(db: Session) -> dict[str, dict]:
    rows = db.execute(
        select(WarehouseInventoryPosition, WarehouseRack, WarehouseLayout)
        .join(WarehouseRack, WarehouseRack.id == WarehouseInventoryPosition.rack_id)
        .join(WarehouseLayout, WarehouseLayout.id == WarehouseRack.layout_id)
        .where(WarehouseLayout.is_active.is_(True))
        .order_by(
            WarehouseLayout.building,
            WarehouseLayout.floor,
            WarehouseRack.sort_order,
            WarehouseRack.rack_code,
            WarehouseInventoryPosition.shelf_no,
            WarehouseInventoryPosition.sort_order,
        )
    ).all()
    summaries: dict[str, dict] = {}
    for position, rack, layout in rows:
        key = clean_text(position.sku)
        if not key:
            continue
        quantity = max(0, int(position.quantity or 0))
        if quantity <= 0:
            continue
        short_label, detail_label = warehouse_position_label(layout, rack, position)
        if not short_label:
            continue
        summary = summaries.setdefault(
            key,
            {
                "placed_quantity": 0,
                "locations": [],
                "details": [],
                "seen": set(),
            },
        )
        summary["placed_quantity"] += quantity
        if short_label not in summary["seen"]:
            summary["locations"].append(short_label)
            summary["seen"].add(short_label)
        summary["details"].append({"location": detail_label, "quantity": quantity})

    for summary in summaries.values():
        locations = summary["locations"]
        display_location = f"{locations[0]} 외 {len(locations) - 1}곳" if len(locations) > 1 else locations[0] if locations else "위치미등록"
        summary["display_location"] = display_location
        summary["location_count"] = len(locations)
        summary["detail_text"] = "; ".join(
            f"{detail['location']} / {int(detail['quantity']):,}개"
            for detail in summary["details"]
        )
        summary.pop("seen", None)
    return summaries


def warehouse_storage_location_by_sku(db: Session) -> dict[str, str]:
    summaries = warehouse_inventory_position_summaries(db)
    return {sku: clean_text(summary.get("display_location")) for sku, summary in summaries.items()}


def ensure_daily_snapshots_from_latest(db: Session, source_type: str, work_date: date, products: list | None = None) -> int:
    if use_legacy_supabase_rest_store():
        return 0

    model = product_master_model(source_type)
    if products is None:
        products = list(db.execute(select(model).order_by(model.sort_order, model.product_name, model.sku)).scalars())
    existing_by_sku = daily_rows_by_product(db, source_type, work_date, products)
    missing_products = [
        product
        for product in products
        if normalize_product_code_text(product.sku)
        and product_daily_lookup_key(product) not in existing_by_sku
        and normalize_product_code_text(product.sku) not in existing_by_sku
    ]
    if not missing_products:
        db.commit()
        return 0

    previous_by_sku = latest_daily_rows_by_product(db, source_type, work_date - timedelta(days=1), missing_products)
    created = 0
    for product in missing_products:
        sku = normalize_product_code_text(product.sku)
        if not sku:
            continue

        previous = previous_by_sku.get(sku)
        row = InventoryDaily(
            source_type=source_type,
            work_date=work_date,
            category=product.large_category,
            product_code=product.sku,
            product_name=product.product_name,
            barcode=normalize_barcode_text(product.barcode),
            supplier=product.supplier,
            current_stock=int(previous.current_stock or 0) if previous else 0,
            available_stock=int(previous.available_stock if previous and previous.available_stock is not None else previous.current_stock if previous else 0),
            safe_stock=int(previous.safe_stock or product.min_stock or 0) if previous else int(product.min_stock or 0),
            outbound_qty=0,
            inbound_qty=0,
            previous_inbound_date=previous.previous_inbound_date if previous else None,
            last_inbound_date=previous.last_inbound_date if previous else None,
            inbound_cycle=int(previous.inbound_cycle or product.default_lead_time or 0) or None if previous else int(product.default_lead_time or 0) or None,
            memo=previous.memo if previous else "",
        )
        row.stock_status = inventory_stock_status_for_daily_row(row)
        db.add(row)
        created += 1
    if created:
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return 0
    else:
        db.commit()
    return created


def offline_return_qty_by_product(db: Session, work_date: date) -> dict[str, int]:
    rows = db.execute(
        select(InventoryOutputHistory.product_code, func.sum(InventoryOutputHistory.outbound_qty))
        .where(
            InventoryOutputHistory.source_type == "오프라인",
            InventoryOutputHistory.work_date == work_date,
            InventoryOutputHistory.output_type == OFFLINE_OUTBOUND_OUTPUT_TYPE,
            InventoryOutputHistory.is_applied == True,  # noqa: E712
            InventoryOutputHistory.outbound_qty < 0,
        )
        .group_by(InventoryOutputHistory.product_code)
    ).all()
    return {
        normalize_product_code_text(product_code): abs(int(total or 0))
        for product_code, total in rows
        if normalize_product_code_text(product_code)
    }


def master_based_inventory_rows(db: Session, source_type: str, work_date: date, active_only: bool = False) -> list[dict]:
    if use_legacy_supabase_rest_store():
        rows = supabase_store.master_based_inventory_rows(source_type, work_date)
        if active_only:
            rows = [row for row in rows if row.get("is_active") == "사용"]
        return rows

    model = product_master_model(source_type)
    query = select(model)
    if active_only:
        query = query.where(model.is_active == "사용")
    products = list(db.execute(query.order_by(model.sort_order, model.large_category, model.product_name, model.sku)).scalars())
    products = inventory_master_rows_for_display(source_type, products)
    daily_by_product = daily_rows_by_product(db, source_type, work_date, products)
    carried_daily_by_sku = latest_daily_rows_by_product(db, source_type, work_date, products) if source_type == "오프라인" else {}

    # Purchase/order history is intentionally not loaded during the normal page render.
    # The dedicated sync button still updates those metrics when the user asks for it.
    purchase_metrics: dict[tuple[str, str], dict] = {}
    inbound_metrics: dict[tuple[str, str], dict] = {}

    avg_outbound_by_sku = recent_outbound_average_by_product(db, source_type, work_date, products, business_day_count=5)
    pending_by_sku = pending_inbound_qty_by_product(db, source_type, work_date, products)
    location_summaries = warehouse_inventory_position_summaries(db) if source_type == "창고" else {}
    offline_return_qty_by_sku = offline_return_qty_by_product(db, work_date) if source_type == "오프라인" else {}

    rows = []
    for product in products:
        product_sku = clean_text(product.sku)
        product_sku_key = normalize_product_code_text(product.sku)
        product_barcode = normalize_barcode_text(product.barcode)
        location_summary = location_summaries.get(product_sku) or location_summaries.get(product_sku_key) or location_summaries.get(product_barcode) or {}
        daily = daily_by_product.get(product_daily_lookup_key(product)) or daily_by_product.get(product_sku_key)
        if daily is None and source_type == "오프라인":
            daily = carried_daily_by_sku.get(product_sku_key)
        has_snapshot = daily is not None
        has_exact_snapshot = has_snapshot and getattr(daily, "work_date", None) == work_date
        current_stock = int(daily.current_stock or 0) if has_snapshot else 0
        available_stock = int(daily.available_stock if daily and daily.available_stock is not None else current_stock) if has_snapshot else 0
        placed_quantity = int(location_summary.get("placed_quantity") or 0)
        actual_locations = bool(location_summary.get("location_count") or placed_quantity)
        master_location_registered = bool(getattr(product, "location_registered", False))
        has_locations = actual_locations if source_type == "창고" else master_location_registered
        unplaced_quantity = max(current_stock - placed_quantity, 0)

        safe_stock = int(product.min_stock or 0)
        pending_outbound_qty = int(daily.outbound_qty or 0) if has_exact_snapshot else 0

        status = inventory_stock_status_for_snapshot(
            has_snapshot,
            daily.available_stock if daily is not None else None,
            daily.current_stock if daily is not None else None,
            safe_stock,
            pending_outbound_qty,
        )

        purchase_metric = purchase_metrics.get((source_type, product.sku), {})
        inbound_metric = inbound_metrics.get((source_type, product.sku), {})
        measured_lead_time = int(purchase_metric.get("avg_lead_time") or 0)
        lead_time = measured_lead_time or product.default_lead_time or 0
        box_qty = int(product.box_qty or product.pack_qty or 0)
        pending_inbound_qty = int(pending_by_sku.get(product_sku, pending_by_sku.get(product_sku_key, 0)) or 0)

        shortage_qty = max(safe_stock - available_stock, 0)
        avg_outbound = float(avg_outbound_by_sku.get(product_sku, avg_outbound_by_sku.get(product_sku_key, 0)) or 0)

        needed_days = order_needed_days(available_stock, safe_stock, avg_outbound, lead_time, pending_outbound_qty)

        category = product_category_text(product)
        category_diagnostic = "" if category else "CATEGORY_EMPTY"
        supplier = product.supplier
        manager = product.memo
        box_pallet_unit = format_box_pallet_unit(product.box_qty, product.pack_qty) or "0"

        rows.append(
            {
                "source_type": source_type,
                "work_date": work_date,
                "category": category,
                "category_diagnostic": category_diagnostic,
                "large_category": clean_text(getattr(product, "large_category", "")),
                "medium_category": clean_text(getattr(product, "medium_category", "")),
                "small_category": clean_text(getattr(product, "small_category", "")),
                "product_code": product.sku,
                "product_name": product.product_name,
                "supplier": supplier,
                "manager": manager,
                "current_stock": current_stock,
                "available_stock": available_stock,
                "safe_stock": safe_stock,
                "pending_inbound_qty": pending_inbound_qty,
                "pending_outbound_qty": pending_outbound_qty,
                "return_qty": int(offline_return_qty_by_sku.get(product_sku_key, 0) or 0),
                "stock_status": status,
                "barcode": product_barcode,
                "storage_location": (
                    clean_text(location_summary.get("display_location")) or "위치미등록"
                    if source_type == "창고"
                    else clean_text(getattr(product, "storage_location", ""))
                ),
                "placed_quantity": placed_quantity,
                "unplaced_quantity": unplaced_quantity,
                "location_status": warehouse_position_status(current_stock, placed_quantity, has_locations),
                "location_detail": clean_text(location_summary.get("detail_text")),
                "inbound_cycle": lead_time,
                "box_qty": box_qty,
                "pack_qty": int(product.pack_qty or 0),
                "box_pallet_unit": box_pallet_unit,
                "recommended_boxes": ceil(shortage_qty / box_qty) if box_qty and shortage_qty > 0 else 0,
                "avg_daily_outbound_1w": avg_outbound,
                "avg_daily_outbound_2w": avg_outbound,
                "order_needed_days": needed_days,
                "last_inventory_update_date": daily.work_date if has_snapshot else None,
                "has_inventory_snapshot": has_snapshot,
                "is_carried_inventory_snapshot": has_snapshot and not has_exact_snapshot,
                "measured_lead_time": measured_lead_time,
                "last_purchase_order_date": purchase_metric.get("last_order_date"),
                "last_purchase_inbound_date": purchase_metric.get("last_inbound_date") or inbound_metric.get("last_inbound_date"),
                "last_po_number": purchase_metric.get("last_po_number", ""),
                "memo": clean_text(getattr(daily, "memo", "")) if has_snapshot else "",
                "sort_order": product.sort_order or 0,
                "is_active": product.is_active,
            }
        )
    return rows


def inventory_master_match_diagnostics(db: Session, source_type: str, work_date: date) -> dict:
    model = product_master_model(source_type)
    products = list(db.execute(select(model)).scalars())
    inventory_rows = list(
        db.execute(
            select(InventoryDaily).where(
                InventoryDaily.source_type == source_type,
                InventoryDaily.work_date == work_date,
            )
        ).scalars()
    )

    products_by_name: dict[str, list[object]] = {}
    for product in products:
        product_name = normalize_product_name_match_key(product.product_name)
        if product_name:
            products_by_name.setdefault(product_name, []).append(product)

    duplicate_names = {key for key, values in products_by_name.items() if len(values) > 1}
    stats = {
        "total_inventory_items": len(inventory_rows),
        "product_name_match_count": 0,
        "master_missing_count": 0,
        "category_empty_count": 0,
        "duplicate_product_name_count": 0,
    }
    problem_rows = []

    for row in inventory_rows:
        product_name = normalize_product_name_match_key(row.product_name)
        product = None
        match_method = ""
        reason = ""
        if product_name:
            name_matches = products_by_name.get(product_name, [])
            if len(name_matches) == 1:
                product = name_matches[0]
                match_method = "상품명"
                stats["product_name_match_count"] += 1
            elif len(name_matches) > 1 or product_name in duplicate_names:
                reason = "DUPLICATE_PRODUCT_NAME"
                stats["duplicate_product_name_count"] += 1
            else:
                reason = "MASTER_NOT_FOUND"
                stats["master_missing_count"] += 1
        else:
            reason = "MASTER_NOT_FOUND"
            stats["master_missing_count"] += 1

        master_category = ""
        if product is not None:
            master_category = clean_text(product.large_category) or clean_text(product.medium_category) or clean_text(product.small_category)
            if not master_category:
                reason = "CATEGORY_EMPTY"
                stats["category_empty_count"] += 1

        if reason:
            problem_rows.append(
                {
                    "product_code": clean_text(row.product_code),
                    "barcode": normalize_barcode_text(row.barcode),
                    "product_name": clean_text(row.product_name),
                    "inventory_category": clean_text(row.category),
                    "master_category": master_category,
                    "match_method": match_method,
                    "reason": reason,
                }
            )

    return {"ok": True, "stats": stats, "problem_rows": problem_rows}


def read_inventory_upload_file(file_bytes: bytes, file_name: str = "") -> pd.DataFrame:
    if file_name.lower().endswith(".csv"):
        for encoding in ("utf-8-sig", "cp949", "utf-8"):
            try:
                raw_df = pd.read_csv(BytesIO(file_bytes), encoding=encoding, dtype=str)
                df = normalize_import_headers(raw_df)
                df.attrs["read_method"] = "csv"
                df.attrs["selected_sheet"] = f"CSV ({encoding})"
                df.attrs["raw_shape"] = tuple(raw_df.shape)
                df.attrs["raw_columns"] = [str(column) for column in raw_df.columns]
                df.attrs["raw_head"] = raw_df.head(5).fillna("").astype(str).to_dict("records")
                df.attrs["normalized_shape"] = tuple(df.shape)
                df.attrs["normalized_columns"] = [str(column) for column in df.columns]
                df.attrs["normalized_head"] = df.head(5).fillna("").astype(str).to_dict("records")
                df = normalize_inventory_upload_code_columns(df)
                df.attrs["normalized_head"] = df.head(5).fillna("").astype(str).to_dict("records")
                return df
            except UnicodeDecodeError:
                continue
        raw_df = pd.read_csv(BytesIO(file_bytes), dtype=str)
        df = normalize_import_headers(raw_df)
        df.attrs["read_method"] = "csv"
        df.attrs["selected_sheet"] = "CSV"
        df.attrs["raw_shape"] = tuple(raw_df.shape)
        df.attrs["raw_columns"] = [str(column) for column in raw_df.columns]
        df.attrs["raw_head"] = raw_df.head(5).fillna("").astype(str).to_dict("records")
        df.attrs["normalized_shape"] = tuple(df.shape)
        df.attrs["normalized_columns"] = [str(column) for column in df.columns]
        df.attrs["normalized_head"] = df.head(5).fillna("").astype(str).to_dict("records")
        df = normalize_inventory_upload_code_columns(df)
        df.attrs["normalized_head"] = df.head(5).fillna("").astype(str).to_dict("records")
        return df
    df = read_excel(file_bytes)
    df = normalize_inventory_upload_code_columns(df)
    df.attrs["normalized_head"] = df.head(5).fillna("").astype(str).to_dict("records")
    return df


def optional_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    try:
        return find_column(df, candidates)
    except ValueError:
        return None


def parse_offline_outbound_file(file_bytes: bytes, file_name: str = "") -> pd.DataFrame:
    df = read_inventory_upload_file(file_bytes, file_name)
    if df is None or df.empty:
        return pd.DataFrame()

    qty_col = find_column(df, OFFLINE_OUTBOUND_QTY_COLUMN_CANDIDATES)
    date_col = optional_column(df, OFFLINE_OUTBOUND_DATE_COLUMN_CANDIDATES)
    order_col = optional_column(df, OFFLINE_OUTBOUND_ORDER_COLUMN_CANDIDATES)
    shipment_col = optional_column(df, OFFLINE_OUTBOUND_SHIPMENT_COLUMN_CANDIDATES)
    invoice_col = optional_column(df, OFFLINE_OUTBOUND_INVOICE_COLUMN_CANDIDATES)
    sku_col = optional_column(df, OFFLINE_OUTBOUND_SKU_COLUMN_CANDIDATES)
    barcode_col = optional_column(df, OFFLINE_OUTBOUND_BARCODE_COLUMN_CANDIDATES)
    name_col = optional_column(df, OFFLINE_OUTBOUND_NAME_COLUMN_CANDIDATES)
    if not any([sku_col, barcode_col, name_col]):
        raise ValueError("출고파일에서 SKU, 바코드, 상품명 중 하나 이상의 상품 식별 컬럼이 필요합니다.")

    rows = []
    for index, row in df.iterrows():
        outbound_qty = to_int(row.get(qty_col))
        product_code = normalize_product_code_text(row.get(sku_col)) if sku_col else ""
        barcode = normalize_barcode_text(row.get(barcode_col)) if barcode_col else ""
        product_name = clean_text(row.get(name_col)) if name_col else ""
        order_no = clean_text(row.get(order_col)) if order_col else ""
        shipment_no = clean_text(row.get(shipment_col)) if shipment_col else ""
        invoice_no = clean_text(row.get(invoice_col)) if invoice_col else ""
        outbound_date = parse_date(row.get(date_col)) if date_col else None
        if product_code and not any([barcode, product_name, order_no, shipment_no, invoice_no, outbound_date, outbound_qty]):
            continue
        if not any([product_code, barcode, product_name, order_no, shipment_no, invoice_no, outbound_qty]):
            continue
        rows.append(
            {
                "row_no": int(index) + 2,
                "work_date": outbound_date,
                "product_code": product_code,
                "barcode": barcode,
                "product_name": product_name,
                "outbound_qty": outbound_qty,
                "order_no": order_no,
                "shipment_no": shipment_no,
                "invoice_no": invoice_no,
            }
        )
    return pd.DataFrame(rows)


def offline_outbound_base_key(row: dict, file_name: str = "") -> str:
    order_no = clean_text(row.get("order_no"))
    shipment_no = clean_text(row.get("shipment_no"))
    invoice_no = clean_text(row.get("invoice_no"))
    if order_no:
        return f"ORDER:{order_no}"
    if shipment_no:
        return f"SHIPMENT:{shipment_no}"
    if invoice_no:
        return f"INVOICE:{invoice_no}"
    fallback = "|".join(
        [
            clean_text(file_name),
            clean_text(row.get("row_no")),
            clean_text(row.get("work_date")),
            normalize_product_code_text(row.get("product_code")),
            normalize_barcode_text(row.get("barcode")),
            clean_text(row.get("product_name")),
            clean_text(row.get("outbound_qty")),
        ]
    )
    return "FILE:" + hashlib.sha256(fallback.encode("utf-8")).hexdigest()


def match_offline_product_for_outbound(lookup: dict[str, dict[str, object]], product_code: str = "", barcode: str = "", product_name: str = ""):
    barcode_key = normalize_product_barcode_match_key(barcode)
    if barcode_key:
        product = preferred_master_product(lookup.get("barcode_list", {}).get(barcode_key, []))
        if product is not None:
            return product, "바코드"

    name_key = normalize_product_name_match_key(product_name)
    if name_key:
        product = preferred_master_product(lookup.get("name_list", {}).get(name_key, []))
        if product is not None:
            return product, "상품명"

    sku_key = normalize_product_code_text(product_code)
    if sku_key:
        product = lookup.get("sku", {}).get(sku_key)
        if product is not None:
            return product, "SKU"
    return None, ""


def applied_offline_outbound_keys(db: Session, keys: set[tuple[str, str]]) -> set[tuple[str, str]]:
    if not keys:
        return set()
    external_keys = {external_key for external_key, _ in keys if external_key}
    product_codes = {product_code for _, product_code in keys if product_code}
    if not external_keys or not product_codes:
        return set()
    rows = db.execute(
        select(InventoryOutputHistory.external_key, InventoryOutputHistory.product_code).where(
            InventoryOutputHistory.source_type == "오프라인",
            InventoryOutputHistory.output_type == OFFLINE_OUTBOUND_OUTPUT_TYPE,
            InventoryOutputHistory.is_applied == True,  # noqa: E712
            InventoryOutputHistory.external_key.in_(list(external_keys)),
            InventoryOutputHistory.product_code.in_(list(product_codes)),
        )
    ).all()
    return {(clean_text(external_key), normalize_product_code_text(product_code)) for external_key, product_code in rows}


def prepare_offline_outbound_upload_preview(
    db: Session,
    source_type: str,
    work_date: date,
    file_bytes: bytes,
    file_name: str = "",
) -> dict:
    if source_type != "오프라인":
        return {"ok": False, "message": "오프라인 출고파일은 오프라인 재고에서만 반영할 수 있습니다.", "count": 0}

    ensure_postgresql_runtime_columns()
    started_at = time.perf_counter()
    df = parse_offline_outbound_file(file_bytes, file_name)
    lookup = product_master_lookup(db, source_type)
    preview_rows = []
    pending_keys: set[tuple[str, str]] = set()
    seen_keys: set[tuple[str, str]] = set()
    duplicate_count = 0
    unmatched_count = 0
    error_count = 0
    matched_count = 0

    for raw_row in df.fillna("").to_dict("records"):
        outbound_date = parse_date(raw_row.get("work_date")) or work_date
        qty = to_int(raw_row.get("outbound_qty"))
        product, match_method = match_offline_product_for_outbound(
            lookup,
            raw_row.get("product_code", ""),
            raw_row.get("barcode", ""),
            raw_row.get("product_name", ""),
        )
        product_code = normalize_product_code_text(getattr(product, "sku", "")) if product else normalize_product_code_text(raw_row.get("product_code"))
        external_key = offline_outbound_base_key({**raw_row, "work_date": outbound_date}, file_name)
        dedupe_key = (external_key, product_code)
        status = "반영대상"
        matched = product is not None and qty != 0
        duplicate = False

        if qty == 0:
            matched = False
            status = "출고수량 오류"
            error_count += 1
        elif product is None:
            matched = False
            status = "미매칭 상품"
            unmatched_count += 1
        elif dedupe_key in seen_keys:
            matched = False
            duplicate = True
            status = "파일 내 중복"
            duplicate_count += 1
        else:
            if qty < 0:
                status = "반품대상"
            seen_keys.add(dedupe_key)
            pending_keys.add(dedupe_key)
            matched_count += 1

        preview_rows.append(
            {
                "row_no": raw_row.get("row_no"),
                "work_date": outbound_date,
                "product_code": clean_text(getattr(product, "sku", "")) if product else clean_text(raw_row.get("product_code")),
                "barcode": normalize_barcode_text(getattr(product, "barcode", "")) if product else normalize_barcode_text(raw_row.get("barcode")),
                "product_name": clean_text(getattr(product, "product_name", "")) if product else clean_text(raw_row.get("product_name")),
                "uploaded_product_code": clean_text(raw_row.get("product_code")),
                "uploaded_barcode": normalize_barcode_text(raw_row.get("barcode")),
                "uploaded_product_name": clean_text(raw_row.get("product_name")),
                "outbound_qty": qty,
                "order_no": clean_text(raw_row.get("order_no")),
                "shipment_no": clean_text(raw_row.get("shipment_no")),
                "invoice_no": clean_text(raw_row.get("invoice_no")),
                "external_key": external_key,
                "match_method": match_method,
                "matched": matched,
                "duplicate": duplicate,
                "already_applied": False,
                "status": status,
            }
        )

    already_applied = applied_offline_outbound_keys(db, pending_keys)
    if already_applied:
        for row in preview_rows:
            dedupe_key = (clean_text(row.get("external_key")), normalize_product_code_text(row.get("product_code")))
            if dedupe_key in already_applied and row.get("matched"):
                row["matched"] = False
                row["already_applied"] = True
                row["status"] = "기반영"
                duplicate_count += 1
                matched_count -= 1

    return {
        "ok": True,
        "message": "오프라인 출고파일 확인 완료",
        "file_name": file_name,
        "total_rows": len(preview_rows),
        "matched_count": max(matched_count, 0),
        "unmatched_count": unmatched_count,
        "duplicate_count": duplicate_count,
        "error_count": error_count,
        "count": max(matched_count, 0),
        "processing_seconds": round(time.perf_counter() - started_at, 2),
        "preview_rows": preview_rows,
    }


def apply_offline_outbound_preview(db: Session, preview: dict, uploaded_by: str = "") -> dict:
    if use_legacy_supabase_rest_store():
        return {"ok": False, "message": "Supabase REST 저장소에서는 오프라인 출고 반영을 지원하지 않습니다.", "count": 0}

    started_at = time.perf_counter()
    source_type = "오프라인"
    candidate_rows = [
        row
        for row in list((preview or {}).get("preview_rows") or [])
        if row.get("matched") and to_int(row.get("outbound_qty")) != 0
    ]
    candidate_keys = {
        (clean_text(row.get("external_key")), normalize_product_code_text(row.get("product_code")))
        for row in candidate_rows
        if clean_text(row.get("external_key")) and normalize_product_code_text(row.get("product_code"))
    }
    already_applied = applied_offline_outbound_keys(db, candidate_keys)

    count = 0
    duplicate_count = int((preview or {}).get("duplicate_count") or 0)
    failure_rows = []
    lookup = product_master_lookup(db, source_type)
    for row in candidate_rows:
        external_key = clean_text(row.get("external_key"))
        product_code = normalize_product_code_text(row.get("product_code"))
        dedupe_key = (external_key, product_code)
        if dedupe_key in already_applied:
            duplicate_count += 1
            continue

        product, _ = match_offline_product_for_outbound(
            lookup,
            row.get("product_code", ""),
            row.get("barcode", ""),
            row.get("product_name", ""),
        )
        if product is None:
            failure_rows.append(
                {
                    "row_no": row.get("row_no", ""),
                    "product_code": row.get("product_code", ""),
                    "barcode": row.get("barcode", ""),
                    "product_name": row.get("product_name", ""),
                    "new_stock": "",
                    "failure_reason": "상품마스터 재조회 실패",
                }
            )
            continue

        outbound_qty = to_int(row.get("outbound_qty"))
        target_date = parse_date(row.get("work_date")) or date.today()
        daily = ensure_offline_daily_row(
            db,
            target_date,
            product,
            product_code=getattr(product, "sku", ""),
            product_name=getattr(product, "product_name", ""),
            barcode=getattr(product, "barcode", ""),
        )
        previous_stock = int(daily.current_stock or 0)
        previous_available = int(daily.available_stock if daily.available_stock is not None else previous_stock)
        daily.outbound_qty = int(daily.outbound_qty or 0) + outbound_qty
        daily.current_stock = previous_stock - outbound_qty
        daily.available_stock = previous_available - outbound_qty
        daily.stock_status = inventory_stock_status_for_daily_row(daily)
        propagate_offline_daily_delta(
            db,
            target_date,
            daily.product_code,
            daily.product_name,
            daily.barcode,
            -outbound_qty,
        )
        db.add(
            InventoryOutputHistory(
                source_type=source_type,
                work_date=target_date,
                output_type=OFFLINE_OUTBOUND_OUTPUT_TYPE,
                created_by=clean_text(uploaded_by) or "SYSTEM",
                filter_json=json.dumps(row, ensure_ascii=False, default=str),
                item_count=1,
                file_name=clean_text((preview or {}).get("file_name")),
                external_key=external_key,
                order_no=clean_text(row.get("order_no")),
                shipment_no=clean_text(row.get("shipment_no")),
                invoice_no=clean_text(row.get("invoice_no")),
                product_code=clean_text(getattr(product, "sku", "")),
                product_name=clean_text(getattr(product, "product_name", "")),
                barcode=normalize_barcode_text(getattr(product, "barcode", "")),
                outbound_qty=outbound_qty,
                is_applied=True,
                memo="오프라인 출고파일 반영",
            )
        )
        already_applied.add(dedupe_key)
        count += 1

    db.commit()
    return {
        "ok": len(failure_rows) == 0,
        "message": "오프라인 출고 재고 반영 완료" if not failure_rows else "오프라인 출고 일부 반영 실패",
        "count": count,
        "total_rows": int((preview or {}).get("total_rows") or 0),
        "matched_count": count,
        "unmatched_count": int((preview or {}).get("unmatched_count") or 0),
        "duplicate_count": duplicate_count,
        "error_count": int((preview or {}).get("error_count") or 0) + len(failure_rows),
        "apply_failed_count": len(failure_rows),
        "failure_rows": failure_rows,
        "processing_seconds": round(time.perf_counter() - started_at, 2),
    }


def to_int_strict(value) -> tuple[int, bool]:
    text = clean_text(value).replace(",", "")
    if not text:
        return 0, False
    try:
        number = int(float(text))
    except ValueError:
        return 0, False
    return number, True


def mark_inventory_update_stage(timings: dict[str, float], name: str, started_at: float) -> None:
    timings[name] = round(time.perf_counter() - started_at, 4)


def inventory_update_log_text(
    timings: dict[str, float],
    total_seconds: float | None = None,
    row_counts: dict[str, int] | None = None,
) -> str:
    ordered = [
        ("excel_read", "excel_read"),
        ("dataframe_normalize", "dataframe_normalize"),
        ("master_load", "master_load"),
        ("product_matching", "product_matching"),
        ("inventory_prepare", "inventory_prepare"),
        ("inventory_bulk_upsert", "inventory_bulk_upsert"),
        ("snapshot_save", "snapshot_save"),
        ("history_save", "history_save"),
        ("excel_parsing", "excel_parsing"),
        ("db_master_loading", "db_master_loading"),
        ("db_inventory_loading", "db_inventory_loading"),
        ("product_name_matching_validation", "product_name_matching_validation"),
        ("validation", "validation"),
        ("inventory_db_save", "inventory_db_save"),
        ("inventory_calculation", "inventory_calculation"),
        ("db_verification", "db_verification"),
        ("total", "TOTAL"),
    ]
    values = dict(timings or {})
    counts = dict(row_counts or {})
    if total_seconds is not None:
        values["total"] = round(total_seconds, 4)
    lines = ["[ERP INVENTORY UPDATE]"]
    for key, label in ordered:
        if key in values:
            suffix = f" rows={int(counts[key])}" if key in counts else ""
            lines.append(f"{label:<24}: elapsed_seconds={float(values[key] or 0):.3f}{suffix}")
    return "\n".join(lines)


def inventory_render_log_text(
    source_type: str,
    work_date: date,
    timings: dict[str, float],
    total_seconds: float | None = None,
    row_count: int = 0,
    query_count: int = 0,
    context: str = "",
) -> str:
    ordered = [
        ("erp_save_complete", "ERP save complete"),
        ("inventory_master_load", "Inventory master load"),
        ("snapshot_seed", "Snapshot seed"),
        ("inventory_snapshot_load", "Inventory snapshot load"),
        ("inventory_history_load", "Inventory history load"),
        ("outbound_load", "Outbound load"),
        ("pending_inbound_load", "Pending inbound load"),
        ("storage_location_load", "Storage location load"),
        ("safety_stock_calculation", "Safety stock calculation"),
        ("available_stock_calculation", "Available stock calc"),
        ("stock_status_calculation", "Status calculation"),
        ("order_needed_days_calculation", "Order date calculation"),
        ("category_supplier_manager_mapping", "Category/supplier mapping"),
        ("summary_card_calculation", "Summary card calculation"),
        ("pdf_data_generation", "PDF data generation"),
        ("excel_data_generation", "Excel data generation"),
        ("dataframe_creation", "DataFrame creation"),
        ("table_rendering", "Table rendering"),
        ("total", "TOTAL"),
    ]
    values = dict(timings or {})
    if total_seconds is not None:
        values["total"] = round(total_seconds, 4)
    lines = [
        "[INVENTORY RENDER]",
        f"Context               : {context or '-'}",
        f"Source / work date    : {source_type} / {work_date}",
        f"Rows                  : {int(row_count or 0)}",
        f"DB query count        : {int(query_count or 0)}",
    ]
    for key, label in ordered:
        if key in values:
            lines.append(f"{label:<24}: {float(values[key] or 0):.3f} sec")
    return "\n".join(lines)


def log_inventory_render_performance(
    source_type: str,
    work_date: date,
    timings: dict[str, float],
    total_seconds: float | None = None,
    row_count: int = 0,
    query_count: int = 0,
    context: str = "",
) -> str:
    text = inventory_render_log_text(source_type, work_date, timings, total_seconds, row_count, query_count, context)
    INVENTORY_LOGGER.info("\n%s", text)
    try:
        INVENTORY_RENDER_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with INVENTORY_RENDER_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{text}\n\n")
    except OSError:
        pass
    return text


def log_inventory_update_performance(
    timings: dict[str, float],
    total_seconds: float | None = None,
    row_counts: dict[str, int] | None = None,
) -> str:
    text = inventory_update_log_text(timings, total_seconds, row_counts)
    INVENTORY_LOGGER.info("\n%s", text)
    try:
        INVENTORY_UPDATE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with INVENTORY_UPDATE_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{text}\n\n")
    except OSError:
        pass
    return text


def inventory_trace_row(row) -> dict:
    if row is None:
        return {}
    return {
        "id": getattr(row, "id", None),
        "source_type": getattr(row, "source_type", ""),
        "work_date": getattr(row, "work_date", None),
        "product_code": getattr(row, "product_code", ""),
        "barcode": normalize_barcode_text(getattr(row, "barcode", "")),
        "product_name": clean_text(getattr(row, "product_name", "")),
        "current_stock": getattr(row, "current_stock", None),
        "available_stock": getattr(row, "available_stock", None),
        "outbound_qty": getattr(row, "outbound_qty", None),
        "stock_status": getattr(row, "stock_status", ""),
    }


def product_trace_row(product) -> dict:
    if product is None:
        return {}
    return {
        "id": getattr(product, "id", None),
        "sku": getattr(product, "sku", ""),
        "normalized_sku": normalize_product_code_text(getattr(product, "sku", "")),
        "barcode": normalize_barcode_text(getattr(product, "barcode", "")),
        "product_name": clean_text(getattr(product, "product_name", "")),
        "category": clean_text(getattr(product, "large_category", ""))
        or clean_text(getattr(product, "medium_category", ""))
        or clean_text(getattr(product, "small_category", "")),
    }


def write_inventory_trace(path: Path, title: str, payload: dict) -> None:
    text = f"[{title}]\n{json.dumps(payload, ensure_ascii=False, default=str, indent=2)}"
    INVENTORY_LOGGER.info("\n%s", text)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{text}\n\n")
    except OSError:
        pass


def prepare_stock_upload_preview(
    db: Session,
    source_type: str,
    work_date: date,
    file_bytes: bytes,
    file_name: str = "",
    upload_mode: str = "partial",
) -> dict:
    total_started_at = time.perf_counter()
    timings: dict[str, float] = {}
    stage_started_at = time.perf_counter()
    df = read_inventory_upload_file(file_bytes, file_name)
    mark_inventory_update_stage(timings, "excel_parsing", stage_started_at)
    upload_debug = {
        "read_method": df.attrs.get("read_method", ""),
        "read_message": df.attrs.get("read_message", ""),
        "sheet_names": df.attrs.get("sheet_names", []),
        "non_empty_sheet_names": df.attrs.get("non_empty_sheet_names", []),
        "selected_sheet": df.attrs.get("selected_sheet", ""),
        "raw_shape": df.attrs.get("raw_shape", tuple(df.shape)),
        "raw_columns": df.attrs.get("raw_columns", []),
        "raw_head": df.attrs.get("raw_head", []),
        "normalized_shape": df.attrs.get("normalized_shape", tuple(df.shape)),
        "normalized_columns": df.attrs.get("normalized_columns", [str(column) for column in df.columns]),
        "normalized_head": df.attrs.get("normalized_head", df.head(5).fillna("").astype(str).to_dict("records")),
        "normalized_sample": df.head(20).fillna("").to_dict("records"),
    }
    if df is None or df.empty:
        return {
            "ok": False,
            "message": "업로드한 엑셀에서 표시할 데이터를 찾지 못했습니다.",
            "file_name": file_name,
            "upload_mode": upload_mode,
            "total_rows": 0,
            "matched_count": 0,
            "failed_count": 0,
            "duplicate_count": 0,
            "invalid_stock_count": 0,
            "negative_stock_count": 0,
            "zeroed_count": 0,
            "preview_rows": [],
            "timings": timings,
            "debug": upload_debug,
            "missing_columns": ["현재고"],
        }
    try:
        current_col = find_column(df, STOCK_CURRENT_COLUMN_CANDIDATES)
    except ValueError:
        try:
            current_col = find_column(df, STOCK_AVAILABLE_COLUMN_CANDIDATES)
        except ValueError as exc:
            raise ValueError(f"필수 컬럼을 찾지 못했습니다: 현재고 / 인식된 컬럼: {', '.join(map(str, df.columns))}") from exc
    try:
        available_col = find_column(df, STOCK_AVAILABLE_COLUMN_CANDIDATES)
    except ValueError:
        available_col = current_col
    try:
        location_col = find_column(df, STOCK_LOCATION_COLUMN_CANDIDATES)
    except ValueError:
        location_col = None
    try:
        category_col = find_column(df, STOCK_CATEGORY_COLUMN_CANDIDATES)
    except ValueError:
        category_col = None
    try:
        product_code_col = find_column(df, ["SKU", "상품코드", "품목코드", "상품번호"])
    except ValueError:
        product_code_col = None
    try:
        barcode_col = find_column(df, ["88바코드", "바코드", "옵션바코드"])
    except ValueError:
        barcode_col = None
    try:
        name_col = find_column(df, ["상품명", "품목"])
    except ValueError:
        name_col = None

    stage_started_at = time.perf_counter()
    products = list(db.execute(select(product_master_model(source_type))).scalars())
    product_maps = product_lookup_maps(products)
    mark_inventory_update_stage(timings, "db_master_loading", stage_started_at)

    stage_started_at = time.perf_counter()
    daily_by_sku = strict_daily_rows_by_product(db, source_type, work_date, products)
    mark_inventory_update_stage(timings, "db_inventory_loading", stage_started_at)

    seen_upload_keys: set[str] = set()
    preview_rows = []
    uploaded_product_keys: set[str] = set()
    matched_count = failed_count = duplicate_count = invalid_stock_count = negative_stock_count = unmatched_count = 0

    stage_started_at = time.perf_counter()
    for index, row in enumerate(df.fillna("").to_dict("records"), start=1):
        barcode = normalize_barcode_text(row.get(barcode_col)) if barcode_col else ""
        product_code = normalize_product_code_text(row.get(product_code_col)) if product_code_col else ""
        product_name = clean_text(row.get(name_col)) if name_col else ""
        uploaded_category = clean_text(row.get(category_col)) if category_col else ""
        storage_location = clean_text(row.get(location_col)) if location_col else ""
        current_stock, stock_ok = to_int_strict(row.get(current_col))
        available_raw = row.get(available_col) if available_col else ""
        available_stock, available_ok = (
            to_int_strict(available_raw) if clean_text(available_raw) else (current_stock, stock_ok)
        )
        errors = []
        upload_key = product_name or f"row_{index}"
        if upload_key in seen_upload_keys:
            duplicate_count += 1
        else:
            seen_upload_keys.add(upload_key)
        if not stock_ok:
            if clean_text(row.get(current_col)).replace(",", "").startswith("-"):
                negative_stock_count += 1
                errors.append("음수 재고")
            else:
                invalid_stock_count += 1
                errors.append("숫자가 아닌 재고")
        if not available_ok:
            if clean_text(row.get(available_col)).replace(",", "").startswith("-"):
                negative_stock_count += 1
                errors.append("음수 가용재고")
            else:
                invalid_stock_count += 1
                errors.append("숫자가 아닌 가용재고")

        product, match_method = match_product_detail_from_maps(
            product_code,
            barcode,
            product_name,
            *product_maps,
            source_type=source_type,
        )
        if product is None:
            unmatched_count += 1
            errors.append("상품명 매칭 실패")

        previous_stock = 0
        matched_category = uploaded_category
        if product:
            daily = daily_by_sku.get(normalize_product_code_text(product.sku))
            previous_stock = int(daily.current_stock or 0) if daily else 0
            matched_category = product_category_text(product) or uploaded_category

        if errors:
            failed_count += 1
        else:
            matched_count += 1
            uploaded_product_keys.add(product.sku)

        preview_rows.append(
            {
                "row_no": index,
                "product_code": product.sku if product else product_code,
                "category": matched_category,
                "product_name": product.product_name if product else product_name,
                "barcode": normalize_barcode_text(product.barcode) if product else barcode,
                "storage_location": storage_location or (getattr(product, "storage_location", "") if product else ""),
                "previous_stock": previous_stock,
                "new_stock": current_stock,
                "new_available_stock": available_stock,
                "status": "정상" if not errors else ", ".join(errors),
                "matched": bool(product) and not errors,
                "match_method": match_method,
                "failure_reason": "" if not errors else ", ".join(errors),
            }
        )

    zero_rows = []
    if upload_mode == "full":
        for product in products:
            if product.sku in uploaded_product_keys:
                continue
            daily = daily_by_sku.get(clean_text(product.sku))
            previous_stock = int(daily.current_stock or 0) if daily else 0
            if previous_stock == 0:
                continue
            zero_rows.append(
                {
                    "row_no": "",
                    "product_code": product.sku,
                    "category": product.large_category,
                    "product_name": product.product_name,
                    "barcode": normalize_barcode_text(product.barcode),
                    "storage_location": getattr(product, "storage_location", ""),
                    "previous_stock": previous_stock,
                    "new_stock": 0,
                    "new_available_stock": 0,
                    "status": "전체 파일 누락 0 처리",
                    "matched": True,
                    "zero_missing": True,
                }
            )
    preview_rows.extend(zero_rows)
    elapsed = time.perf_counter() - stage_started_at
    timings["product_name_matching_validation"] = round(elapsed, 4)
    timings["validation"] = round(elapsed, 4)
    timings["prepare_total"] = round(time.perf_counter() - total_started_at, 4)
    return {
        "ok": True,
        "file_name": file_name,
        "upload_mode": upload_mode,
        "debug": upload_debug,
        "timings": timings,
        "total_rows": len(df.index),
        "matched_count": matched_count,
        "failed_count": failed_count,
        "duplicate_count": duplicate_count,
        "unmatched_count": unmatched_count,
        "invalid_stock_count": invalid_stock_count,
        "negative_stock_count": negative_stock_count,
        "zeroed_count": len(zero_rows),
        "preview_rows": preview_rows,
    }


def normalize_erp_stock_barcode(value) -> str:
    return normalize_barcode_text(value)


def normalize_erp_stock_product_name(value) -> str:
    return normalize_product_name_match_key(value)


def erp_stock_match_key(barcode, product_name) -> tuple[str, str]:
    return (normalize_product_barcode_match_key(barcode), normalize_erp_stock_product_name(product_name))


def read_erp_stock_upload_file(file_bytes: bytes, file_name: str = "") -> pd.DataFrame:
    suffix = Path(clean_text(file_name).lower()).suffix
    if suffix == ".csv":
        for encoding in ("utf-8-sig", "cp949", "euc-kr"):
            try:
                raw_df = pd.read_csv(BytesIO(file_bytes), dtype=str, encoding=encoding)
                return normalize_import_headers(raw_df)
            except UnicodeDecodeError:
                continue
        return normalize_import_headers(pd.read_csv(BytesIO(file_bytes), dtype=str))
    return read_excel(file_bytes)


def apply_erp_stock_upload_file(
    db: Session,
    source_type: str,
    work_date: date,
    file_bytes: bytes,
    file_name: str = "",
    uploaded_by: str = "",
    progress_callback=None,
) -> dict:
    total_started_at = time.perf_counter()
    timings: dict[str, float] = {}
    row_counts: dict[str, int] = {}

    def progress(message: str) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(message)
        except Exception:
            pass

    stage_started_at = time.perf_counter()
    progress("엑셀 읽는 중")
    df = read_erp_stock_upload_file(file_bytes, file_name)
    row_counts["excel_read"] = len(df.index) if df is not None else 0
    mark_inventory_update_stage(timings, "excel_read", stage_started_at)
    if df is None or df.empty:
        return {
            "ok": False,
            "message": "업로드한 파일에서 재고 데이터를 찾지 못했습니다.",
            "total_rows": 0,
            "matched_count": 0,
            "unmatched_count": 0,
            "duplicate_count": 0,
            "error_count": 0,
            "count": 0,
            "processing_seconds": round(time.perf_counter() - total_started_at, 2),
            "unmatched_rows": [],
            "failure_rows": [],
            "timings": timings,
        }

    stage_started_at = time.perf_counter()
    progress("파일 확인 중")
    try:
        stock_col = find_column(df, ERP_AVAILABLE_STOCK_COLUMN_CANDIDATES)
        invoice_col = find_column(df, ERP_INVOICE_COLUMN_CANDIDATES)
        received_col = find_column(df, ERP_RECEIVED_COLUMN_CANDIDATES)
        try:
            product_code_col = find_column(df, ["SKU", "상품코드", "품목코드", "상품번호", "대표상품코드"])
        except ValueError:
            product_code_col = None
        try:
            barcode_col = find_column(df, ["88바코드", "바코드", "옵션바코드", "barcode"])
        except ValueError:
            barcode_col = None
        try:
            name_col = find_column(df, ["상품명", "품목", "품목명", "product_name"])
        except ValueError:
            name_col = None
        try:
            category_col = find_column(df, STOCK_CATEGORY_COLUMN_CANDIDATES)
        except ValueError:
            category_col = None
    except ValueError as exc:
        mark_inventory_update_stage(timings, "dataframe_normalize", stage_started_at)
        return {
            "ok": False,
            "message": clean_text(exc),
            "total_rows": len(df.index),
            "matched_count": 0,
            "unmatched_count": len(df.index),
            "duplicate_count": 0,
            "error_count": 1,
            "count": 0,
            "processing_seconds": round(time.perf_counter() - total_started_at, 2),
            "unmatched_rows": [],
            "failure_rows": [{"failure_reason": clean_text(exc)}],
            "timings": timings,
        }
    row_counts["dataframe_normalize"] = len(df.index)
    mark_inventory_update_stage(timings, "dataframe_normalize", stage_started_at)

    stage_started_at = time.perf_counter()
    progress("상품 마스터 불러오는 중")
    model = product_master_model(source_type)
    products = list(db.execute(select(model)).scalars())
    product_maps = product_lookup_maps(products)
    row_counts["master_load"] = len(products)
    mark_inventory_update_stage(timings, "master_load", stage_started_at)

    stage_started_at = time.perf_counter()
    progress("상품 매칭 중")
    unmatched_rows = []
    matched_by_sku: dict[str, dict] = {}
    trace_barcode = normalize_erp_stock_barcode(INVENTORY_TRACE_BARCODE)
    trace_upload_rows = []
    for row_no, row in enumerate(df.fillna("").to_dict("records"), start=1):
        product_code = normalize_erp_product_code(row.get(product_code_col)) if product_code_col else ""
        barcode = normalize_erp_stock_barcode(row.get(barcode_col))
        product_name = normalize_erp_stock_product_name(row.get(name_col))
        uploaded_category = clean_text(row.get(category_col)) if category_col else ""
        uploaded_stock, stock_ok = to_int_strict(row.get(stock_col))
        invoice_raw = row.get(invoice_col)
        received_raw = row.get(received_col)
        invoice_count, invoice_ok = to_int_strict(invoice_raw)
        received_count, received_ok = to_int_strict(received_raw)
        if not clean_text(invoice_raw):
            invoice_count, invoice_ok = 0, True
        if not clean_text(received_raw):
            received_count, received_ok = 0, True
        outbound_qty = invoice_count + received_count if invoice_ok and received_ok else 0
        product, match_method = match_product_detail_from_maps(
            product_code,
            barcode,
            product_name,
            *product_maps,
            source_type=source_type,
        )
        if barcode == trace_barcode:
            trace_upload_rows.append(
                {
                    "row_no": row_no,
                    "product_code": product_code,
                    "barcode": barcode,
                    "product_name": product_name,
                    "available_stock": uploaded_stock if stock_ok else row.get(stock_col, ""),
                    "송장": invoice_count if invoice_ok else row.get(invoice_col, ""),
                    "접수": received_count if received_ok else row.get(received_col, ""),
                    "outbound_qty": outbound_qty,
                    "matched_product": product_trace_row(product),
                }
            )
        reason = ""
        if not product_name:
            reason = "상품명 없음"
        elif not stock_ok:
            reason = "가용재고 오류"
        elif not invoice_ok:
            reason = "송장 수량 오류"
        elif not received_ok:
            reason = "접수 수량 오류"
        elif product is None:
            reason = "상품명 매칭 실패"
        if reason:
            unmatched_rows.append(
                {
                    "row_no": row_no,
                    "product_code": product_code,
                    "barcode": barcode,
                    "product_name": product_name,
                    "uploaded_stock": row.get(stock_col, ""),
                    "new_stock": row.get(stock_col, ""),
                    "송장": row.get(invoice_col, ""),
                    "접수": row.get(received_col, ""),
                    "reason": reason,
                    "failure_reason": reason,
                }
            )
            continue
        matched_by_sku[normalize_erp_product_code(product.sku)] = {
            "row_no": row_no,
            "barcode": barcode,
            "product_name": product_name,
            "stock": uploaded_stock,
            "outbound_qty": outbound_qty,
            "category": uploaded_category,
            "product": product,
            "match_method": match_method,
        }
    row_counts["product_matching"] = len(df.index)
    mark_inventory_update_stage(timings, "product_matching", stage_started_at)

    apply_by_sku = matched_by_sku

    history = None
    failure_rows = []
    try:
        stage_started_at = time.perf_counter()
        progress("재고 데이터 준비 중")
        now = datetime.utcnow()
        updated_count = 0
        inserted_count = 0
        snapshots = []
        existing_daily_rows = list(
            db.execute(
                select(InventoryDaily).where(
                    InventoryDaily.source_type == source_type,
                    InventoryDaily.work_date == work_date,
                )
            ).scalars()
        )
        existing_by_identity = {
            clean_text(row.product_name): row
            for row in existing_daily_rows
        }
        existing_by_sku: dict[str, list[InventoryDaily]] = {}
        for daily in existing_daily_rows:
            daily_sku = normalize_product_code_text(daily.product_code)
            if daily_sku:
                existing_by_sku.setdefault(daily_sku, []).append(daily)
        stale_daily_ids: set[int] = set()
        upsert_values_by_identity: dict[str, dict] = {}
        for product_sku, matched in apply_by_sku.items():
            product = matched["product"]
            new_stock = int(matched["stock"] or 0)
            outbound_qty = int(matched.get("outbound_qty") or 0)
            uploaded_category = clean_text(matched.get("category"))
            if uploaded_category and not product_category_text(product):
                product.large_category = uploaded_category
            category = product_category_text(product) or uploaded_category
            product_barcode = normalize_erp_stock_barcode(product.barcode)
            product_name = clean_text(product.product_name)
            identity = product_name
            final_row = existing_by_identity.get(identity)
            if final_row is not None:
                previous_stock = int(final_row.current_stock or 0)
            else:
                previous_candidates = existing_by_sku.get(product_sku, [])
                previous_stock = int(previous_candidates[0].current_stock or 0) if previous_candidates else 0
            for stale in existing_by_sku.get(product_sku, []):
                stale_identity = clean_text(stale.product_name)
                if stale.id is not None and stale_identity != identity:
                    stale_daily_ids.add(stale.id)
            values = {
                "source_type": source_type,
                "work_date": work_date,
                "category": category,
                "product_code": product.sku,
                "product_name": product_name,
                "barcode": product_barcode,
                "supplier": product.supplier,
                "current_stock": new_stock,
                "available_stock": new_stock,
                "safe_stock": int(product.min_stock or 0),
                "stock_status": inventory_stock_status_for_snapshot(
                    True,
                    new_stock,
                    new_stock,
                    int(product.min_stock or 0),
                    outbound_qty,
                ),
                "outbound_qty": outbound_qty,
                "inbound_cycle": product.default_lead_time or None,
                "updated_at": now,
            }
            upsert_values_by_identity[identity] = values
            if final_row is not None:
                updated_count += 1
            else:
                inserted_count += 1
            snapshots.append(
                {
                    "source_type": source_type,
                    "work_date": work_date,
                    "product_code": product.sku,
                    "barcode": normalize_erp_stock_barcode(product.barcode),
                    "product_name": product.product_name,
                    "previous_stock": previous_stock,
                    "new_stock": new_stock,
                }
            )
        row_counts["inventory_prepare"] = len(apply_by_sku)
        mark_inventory_update_stage(timings, "inventory_prepare", stage_started_at)

        stage_started_at = time.perf_counter()
        progress("재고 DB 반영 중")
        if stale_daily_ids:
            db.execute(delete(InventoryDaily).where(InventoryDaily.id.in_(list(stale_daily_ids))))
        upsert_groups: dict[tuple[str, ...], list[dict]] = {}
        for values in upsert_values_by_identity.values():
            upsert_groups.setdefault(tuple(values.keys()), []).append(values)
        for values in upsert_groups.values():
            execute_inventory_daily_bulk_upsert(db, values)
        db.flush()
        row_counts["inventory_bulk_upsert"] = len(upsert_values_by_identity)
        mark_inventory_update_stage(timings, "inventory_bulk_upsert", stage_started_at)

        stage_started_at = time.perf_counter()
        progress("이력 저장 중")
        history = InventoryUploadHistory(
            source_type=source_type,
            work_date=work_date,
            file_name=clean_text(file_name),
            uploaded_by=clean_text(uploaded_by) or "SYSTEM",
            upload_mode="ERP 재고 업데이트",
            total_rows=len(df.index),
            matched_count=len(apply_by_sku),
            failed_count=len(unmatched_rows),
            duplicate_count=0,
            zeroed_count=0,
        )
        db.add(history)
        db.flush()
        row_counts["history_save"] = 1
        mark_inventory_update_stage(timings, "history_save", stage_started_at)

        stage_started_at = time.perf_counter()
        if snapshots:
            for snapshot in snapshots:
                snapshot["upload_history_id"] = history.id
            db.execute(InventoryUploadSnapshot.__table__.insert(), snapshots)
        db.flush()
        row_counts["snapshot_save"] = len(snapshots)
        mark_inventory_update_stage(timings, "snapshot_save", stage_started_at)

        stage_started_at = time.perf_counter()
        db.expire_all()
        verification_rows = list(
            db.execute(
                select(InventoryDaily).where(
                    InventoryDaily.source_type == source_type,
                    InventoryDaily.work_date == work_date,
                )
            ).scalars()
        )
        verified_by_sku = {
            normalize_product_code_text(row.product_code): row
            for row in verification_rows
            if normalize_product_code_text(row.product_code)
        }
        for product_sku, matched in apply_by_sku.items():
            verified = verified_by_sku.get(product_sku)
            expected_stock = int(matched["stock"] or 0)
            expected_outbound_qty = int(matched.get("outbound_qty") or 0)
            if (
                verified is None
                or int(verified.current_stock or 0) != expected_stock
                or int(verified.available_stock or 0) != expected_stock
                or int(verified.outbound_qty or 0) != expected_outbound_qty
            ):
                failure_rows.append(
                    {
                        "row_no": matched.get("row_no", ""),
                        "product_code": matched["product"].sku,
                        "barcode": matched.get("barcode", ""),
                        "product_name": matched.get("product_name", ""),
                        "new_stock": expected_stock,
                        "outbound_qty": expected_outbound_qty,
                        "failure_reason": "DB 재조회 검증 실패",
                    }
                )
        mark_inventory_update_stage(timings, "db_verification", stage_started_at)
        trace_master_rows = [
            product_trace_row(product)
            for product in products
            if normalize_erp_stock_barcode(getattr(product, "barcode", "")) == trace_barcode
        ]
        trace_verified_rows = [
            inventory_trace_row(row)
            for row in verification_rows
            if normalize_erp_stock_barcode(getattr(row, "barcode", "")) == trace_barcode
        ]
        write_inventory_trace(
            INVENTORY_UPDATE_LOG_PATH,
            "ERP INVENTORY SAVE TRACE",
            {
                "저장 table": getattr(InventoryDaily, "__tablename__", "inventory_daily"),
                "source_type": source_type,
                "work_date": work_date,
                "barcode": trace_barcode,
                "product_name": [row.get("product_name", "") for row in trace_upload_rows],
                "available_stock": [row.get("available_stock") for row in trace_upload_rows],
                "송장": [row.get("송장") for row in trace_upload_rows],
                "접수": [row.get("접수") for row in trace_upload_rows],
                "outbound_qty": [row.get("outbound_qty") for row in trace_upload_rows],
                "실제 upsert 건수": updated_count + inserted_count,
                "uploaded_rows": len(df.index),
                "matched_rows": len(matched_by_sku),
                "upserted_rows": updated_count + inserted_count,
                "inventory_rows_found": len(verification_rows),
                "joined_rows": len(verified_by_sku),
                "unmatched_rows": len(unmatched_rows),
                "updated_rows": updated_count,
                "inserted_rows": inserted_count,
                "trace_upload_rows": trace_upload_rows,
                "trace_master_rows": trace_master_rows,
                "trace_saved_rows": trace_verified_rows,
                "save_key": "matched product sku stored to inventory_daily.product_code; barcode is stored as canonical text",
            },
        )
        if failure_rows:
            raise RuntimeError(f"ERP 재고 DB 검증 실패 {len(failure_rows)}건")
        db.commit()
    except Exception as exc:
        db.rollback()
        record_save_failure(f"erp inventory upload {source_type} {work_date}", exc)
        if not failure_rows:
            failure_rows.append({"failure_reason": clean_text(exc)})
        return {
            "ok": False,
            "message": "ERP 재고 반영 검증에 실패했습니다.",
            "count": 0,
            "history_id": history.id if history else None,
            "total_rows": len(df.index),
            "matched_count": len(apply_by_sku),
            "unmatched_count": len(unmatched_rows),
            "duplicate_count": 0,
            "apply_failed_count": len(failure_rows),
            "failure_rows": failure_rows,
            "error_count": len(failure_rows),
            "zeroed_count": 0,
            "processing_seconds": round(time.perf_counter() - total_started_at, 2),
            "timings": timings,
            "unmatched_rows": unmatched_rows,
        }

    processing_seconds = time.perf_counter() - total_started_at
    timings["apply_total"] = round(processing_seconds, 4)
    log_inventory_update_performance(timings, processing_seconds, row_counts)
    record_save_success(f"erp inventory upload {source_type} {work_date}")
    return {
        "ok": True,
        "message": "ERP 재고 반영 완료",
        "count": len(apply_by_sku),
        "history_id": history.id if history else None,
        "total_rows": len(df.index),
        "matched_count": len(apply_by_sku),
        "unmatched_count": len(unmatched_rows),
        "duplicate_count": 0,
        "apply_failed_count": 0,
        "failure_rows": [],
        "error_count": 0,
        "zeroed_count": 0,
        "processing_seconds": round(processing_seconds, 2),
        "timings": timings,
        "unmatched_rows": unmatched_rows,
    }


def apply_stock_upload_preview(
    db: Session,
    source_type: str,
    work_date: date,
    preview: dict,
    uploaded_by: str = "",
) -> dict:
    if use_legacy_supabase_rest_store():
        return supabase_store.apply_stock_rows(source_type, work_date, preview)

    total_started_at = time.perf_counter()
    timings = dict(preview.get("timings") or {})
    count = 0
    history = None
    try:
        rows = [row for row in preview.get("preview_rows", []) if row.get("matched")]
        history = InventoryUploadHistory(
            source_type=source_type,
            work_date=work_date,
            file_name=clean_text(preview.get("file_name")),
            uploaded_by=clean_text(uploaded_by) or "SYSTEM",
            upload_mode=clean_text(preview.get("upload_mode")) or "partial",
            total_rows=int(preview.get("total_rows") or 0),
            matched_count=int(preview.get("matched_count") or 0),
            failed_count=int(preview.get("failed_count") or 0),
            duplicate_count=int(preview.get("duplicate_count") or 0),
            zeroed_count=int(preview.get("zeroed_count") or 0),
        )
        db.add(history)
        db.flush()

        stage_started_at = time.perf_counter()
        products = list(db.execute(select(product_master_model(source_type))).scalars())
        products_by_sku = {normalize_product_code_text(product.sku): product for product in products if normalize_product_code_text(product.sku)}
        product_maps = product_lookup_maps(products)
        existing_daily_rows = list(
            db.execute(
                select(InventoryDaily).where(
                    InventoryDaily.source_type == source_type,
                    InventoryDaily.work_date == work_date,
                )
            ).scalars()
        )
        existing_by_identity = {
            clean_text(row.product_name): row
            for row in existing_daily_rows
        }
        existing_by_sku: dict[str, list[InventoryDaily]] = {}
        for daily in existing_daily_rows:
            daily_sku = normalize_product_code_text(daily.product_code)
            if daily_sku:
                existing_by_sku.setdefault(daily_sku, []).append(daily)
        touched_skus: set[str] = set()
        snapshots = []
        apply_failures = []
        stale_daily_ids: set[int] = set()
        upsert_values_by_identity: dict[str, dict] = {}
        expected_by_sku: dict[str, dict] = {}
        offline_future_deltas: list[tuple[str, str, str, int]] = []
        for row in rows:
            row_barcode = normalize_barcode_text(row.get("barcode"))
            row_product_name = clean_text(row.get("product_name"))
            product = match_product_from_maps(
                row.get("product_code", ""),
                row_barcode,
                row_product_name,
                *product_maps,
                source_type=source_type,
            )
            product_sku = normalize_product_code_text(getattr(product, "sku", ""))
            try:
                if not product:
                    raise ValueError("상품마스터 재조회 실패")
                uploaded_category = clean_text(row.get("category"))
                if uploaded_category and not product_category_text(product):
                    product.large_category = uploaded_category
                new_stock = to_int(row.get("new_stock"))
                new_available_stock = to_int(row.get("new_available_stock")) if "new_available_stock" in row else new_stock
                storage_location = clean_text(row.get("storage_location")) or getattr(product, "storage_location", "")
                if storage_location and hasattr(product, "storage_location"):
                    product.storage_location = storage_location
                product_barcode = normalize_barcode_text(product.barcode)
                product_name = clean_text(product.product_name)
                identity = product_name
                outbound_qty = max(new_stock - new_available_stock, 0)
                existing_final = existing_by_identity.get(identity)
                if existing_final is not None:
                    previous_stock = int(existing_final.current_stock or 0)
                else:
                    previous_candidates = existing_by_sku.get(product_sku, [])
                    previous = latest_daily_row_before(db, source_type, work_date, product.sku, product_name, product_barcode) if source_type == "오프라인" else None
                    previous_stock = int(previous.current_stock or 0) if previous is not None else int(previous_candidates[0].current_stock or 0) if previous_candidates else 0
                for stale in existing_by_sku.get(product_sku, []):
                    stale_identity = clean_text(stale.product_name)
                    if stale.id is not None and stale_identity != identity:
                        stale_daily_ids.add(stale.id)
                values = {
                    "source_type": source_type,
                    "work_date": work_date,
                    "category": product_category_text(product) or uploaded_category,
                    "product_code": product.sku,
                    "product_name": product_name,
                    "barcode": product_barcode,
                    "supplier": product.supplier,
                    "current_stock": new_stock,
                    "available_stock": new_available_stock,
                    "safe_stock": int(product.min_stock or 0),
                    "stock_status": inventory_stock_status_for_snapshot(
                        True,
                        new_available_stock,
                        new_stock,
                        int(product.min_stock or 0),
                        outbound_qty,
                    ),
                    "outbound_qty": outbound_qty,
                    "inbound_cycle": product.default_lead_time or None,
                    "updated_at": datetime.utcnow(),
                }
                if "memo" in row:
                    values["memo"] = clean_text(row.get("memo"))
                if model_has_field(InventoryDaily, "storage_location"):
                    values["storage_location"] = storage_location
                upsert_values_by_identity[identity] = values
                expected_by_sku[product_sku] = {
                    "row": row,
                    "current_stock": new_stock,
                    "available_stock": new_available_stock,
                }
                snapshots.append(
                    {
                        "upload_history_id": history.id,
                        "source_type": source_type,
                        "work_date": work_date,
                        "product_code": product.sku,
                        "barcode": normalize_barcode_text(product.barcode),
                        "product_name": product.product_name,
                        "previous_stock": previous_stock,
                        "new_stock": new_stock,
                    }
                )
                if source_type == "오프라인":
                    delta = int(new_stock or 0) - int(previous_stock or 0)
                    if delta:
                        offline_future_deltas.append((product.sku, product_name, product_barcode, delta))
                touched_skus.add(product_sku)
                count += 1
            except Exception as row_exc:
                failure = {
                    "row_no": row.get("row_no", ""),
                    "product_code": row.get("product_code", ""),
                    "barcode": row.get("barcode", ""),
                    "product_name": row.get("product_name", ""),
                    "new_stock": row.get("new_stock", ""),
                    "failure_reason": clean_text(row_exc),
                }
                apply_failures.append(failure)
                INVENTORY_LOGGER.exception("Inventory row update failed: %s", failure)
        if stale_daily_ids:
            db.execute(delete(InventoryDaily).where(InventoryDaily.id.in_(list(stale_daily_ids))))
        upsert_groups: dict[tuple[str, ...], list[dict]] = {}
        for values in upsert_values_by_identity.values():
            upsert_groups.setdefault(tuple(values.keys()), []).append(values)
        for values in upsert_groups.values():
            execute_inventory_daily_bulk_upsert(db, values)
        for product_code, product_name, barcode, delta in offline_future_deltas:
            propagate_offline_daily_delta(db, work_date, product_code, product_name, barcode, delta)
        db.flush()
        refreshed_daily_rows = list(
            db.execute(
                select(InventoryDaily).where(
                    InventoryDaily.source_type == source_type,
                    InventoryDaily.work_date == work_date,
                    InventoryDaily.product_code.in_(list(touched_skus)),
                )
            ).scalars()
        ) if touched_skus else []
        daily_by_sku = {
            normalize_product_code_text(row.product_code): row
            for row in refreshed_daily_rows
            if normalize_product_code_text(row.product_code)
        }
        for sku, expected in expected_by_sku.items():
            verified = daily_by_sku.get(sku)
            if (
                verified is None
                or int(verified.current_stock or 0) != int(expected["current_stock"] or 0)
                or int(verified.available_stock or 0) != int(expected["available_stock"] or 0)
            ):
                row = expected["row"]
                apply_failures.append(
                    {
                        "row_no": row.get("row_no", ""),
                        "product_code": row.get("product_code", ""),
                        "barcode": row.get("barcode", ""),
                        "product_name": row.get("product_name", ""),
                        "new_stock": row.get("new_stock", ""),
                        "failure_reason": "DB 재조회 검증 실패",
                    }
                )
        if snapshots:
            db.execute(InventoryUploadSnapshot.__table__.insert(), snapshots)
        history.matched_count = count
        if apply_failures:
            history.failed_count = int(history.failed_count or 0) + len(apply_failures)
        db.flush()
        mark_inventory_update_stage(timings, "inventory_db_save", stage_started_at)

        stage_started_at = time.perf_counter()
        recalculate_uploaded_inventory_rows(db, source_type, work_date, products_by_sku, daily_by_sku, touched_skus)
        db.flush()
        mark_inventory_update_stage(timings, "inventory_calculation", stage_started_at)

        db.commit()
    except Exception as exc:
        db.rollback()
        record_save_failure(f"inventory upload {source_type} {work_date}", exc)
        raise
    apply_seconds = time.perf_counter() - total_started_at
    timings["apply_total"] = round(apply_seconds, 4)
    total_seconds = float(timings.get("prepare_total") or 0) + apply_seconds
    log_inventory_update_performance(timings, total_seconds)
    apply_failed_count = len(apply_failures)
    error_count = int(preview.get("invalid_stock_count") or 0) + int(preview.get("negative_stock_count") or 0) + apply_failed_count
    record_save_success(f"inventory upload {source_type} {work_date}")
    return {
        "ok": True,
        "message": "재고 반영 완료",
        "count": count,
        "history_id": history.id,
        "total_rows": int(preview.get("total_rows") or 0),
        "matched_count": int(preview.get("matched_count") or 0),
        "unmatched_count": int(preview.get("unmatched_count") or 0),
        "duplicate_count": int(preview.get("duplicate_count") or 0),
        "apply_failed_count": apply_failed_count,
        "failure_rows": apply_failures,
        "error_count": error_count,
        "zeroed_count": int(preview.get("zeroed_count") or 0),
        "processing_seconds": round(total_seconds, 2),
        "timings": timings,
    }


def apply_erp_stock_upload_preview(
    db: Session,
    source_type: str,
    work_date: date,
    preview: dict,
    uploaded_by: str = "",
) -> dict:
    """Apply an ERP snapshot upload after the common parsing/matching preview."""
    next_preview = dict(preview or {})
    next_preview["change_method"] = "ERP 재고 업데이트"
    return apply_stock_upload_preview(db, source_type, work_date, next_preview, uploaded_by)


def apply_manual_stock_adjustment_preview(
    db: Session,
    source_type: str,
    work_date: date,
    preview: dict,
    uploaded_by: str = "",
) -> dict:
    """Apply a reviewed manual stock adjustment preview."""
    next_preview = dict(preview or {})
    next_preview["change_method"] = clean_text(next_preview.get("change_method")) or "재고 수정"
    next_preview["upload_mode"] = clean_text(next_preview.get("upload_mode")) or "manual_adjustment"
    return apply_stock_upload_preview(db, source_type, work_date, next_preview, uploaded_by)


def recalculate_uploaded_inventory_rows(
    db: Session,
    source_type: str,
    work_date: date,
    products_by_sku: dict[str, object],
    daily_by_sku: dict[str, InventoryDaily],
    touched_skus: set[str],
) -> int:
    touched_products = [products_by_sku[sku] for sku in touched_skus if sku in products_by_sku]
    if not touched_products:
        return 0
    avg_map = recent_outbound_average_by_product(db, source_type, work_date, touched_products)
    count = 0
    for product in touched_products:
        sku = normalize_product_code_text(product.sku)
        item = daily_by_sku.get(sku)
        if item is None:
            continue
        avg_daily = float(avg_map.get(clean_text(product.sku), avg_map.get(sku, 0)) or 0)
        lead_time_days = int(product.default_lead_time or item.inbound_cycle or 1)
        safe_stock = ceil(avg_daily * max(lead_time_days, 1) * 1.2)
        item.safe_stock = safe_stock
        product.min_stock = safe_stock
        item.stock_status = inventory_stock_status_for_daily_row(item)
        count += 1
    return count


def verify_stock_upload_saved(db: Session, source_type: str, work_date: date, targets: list[dict]) -> dict:
    db.expire_all()
    rows = list(
        db.execute(
            select(InventoryDaily).where(
                InventoryDaily.source_type == source_type,
                InventoryDaily.work_date == work_date,
            )
        ).scalars()
    )
    by_name = {normalize_product_name_match_key(row.product_name): row for row in rows if normalize_product_name_match_key(row.product_name)}
    failed = []
    for target in targets:
        item = by_name.get(normalize_product_name_match_key(target["product_name"]))
        if (
            item is None
            or int(item.current_stock or 0) != int(target["current_stock"] or 0)
            or int(item.available_stock or 0) != int(target["available_stock"] or 0)
        ):
            failed.append(target)

    if failed:
        return {
            "ok": False,
            "message": f"재고 업로드는 commit 되었지만 DB 재조회 검증 실패 {len(failed):,}건이 있습니다.",
            "verified_count": len(targets) - len(failed),
            "failed_count": len(failed),
        }
    return {"ok": True, "message": "DB 저장 검증 완료", "verified_count": len(targets), "failed_count": 0}


def record_inventory_output(
    db: Session,
    source_type: str,
    work_date: date,
    output_type: str,
    filters: dict,
    item_count: int,
    created_by: str = "",
) -> None:
    db.add(
        InventoryOutputHistory(
            source_type=source_type,
            work_date=work_date,
            output_type=output_type,
            created_by=clean_text(created_by) or "SYSTEM",
            filter_json=json.dumps(filters, ensure_ascii=False, default=str),
            item_count=item_count,
        )
    )
    db.commit()


def list_inbound(db: Session, source_type: str) -> list[InventoryInbound]:
    if use_legacy_supabase_rest_store():
        return supabase_store.list_inbound(source_type)
    return list(
        db.execute(
            select(InventoryInbound)
            .where(InventoryInbound.source_type == source_type)
            .order_by(InventoryInbound.inbound_date.desc(), InventoryInbound.id.desc())
        ).scalars()
    )


def _outbound_query(
    source_type: str,
    start_date: date | None = None,
    end_date: date | None = None,
    keyword: str | None = None,
):
    query = select(InventoryDaily).where(InventoryDaily.source_type == source_type, InventoryDaily.outbound_qty != 0)
    if start_date:
        query = query.where(InventoryDaily.work_date >= start_date)
    if end_date:
        query = query.where(InventoryDaily.work_date <= end_date)
    normalized_keyword = clean_text(keyword).lower()
    if normalized_keyword:
        pattern = f"%{normalized_keyword}%"
        query = query.where(
            or_(
                func.lower(func.coalesce(InventoryDaily.product_code, "")).like(pattern),
                func.lower(func.coalesce(InventoryDaily.barcode, "")).like(pattern),
                func.lower(func.coalesce(InventoryDaily.product_name, "")).like(pattern),
            )
        )
    return query


def count_outbound(
    db: Session,
    source_type: str,
    start_date: date | None = None,
    end_date: date | None = None,
    keyword: str | None = None,
) -> int:
    if use_legacy_supabase_rest_store():
        return supabase_store.count_outbound(source_type, start_date=start_date, end_date=end_date, keyword=keyword)
    count_query = select(func.count()).select_from(_outbound_query(source_type, start_date, end_date, keyword).subquery())
    return int(db.execute(count_query).scalar() or 0)


def list_outbound(
    db: Session,
    source_type: str,
    start_date: date | None = None,
    end_date: date | None = None,
    keyword: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[InventoryDaily]:
    if use_legacy_supabase_rest_store():
        return supabase_store.list_outbound(
            source_type,
            start_date=start_date,
            end_date=end_date,
            keyword=keyword,
            limit=limit,
            offset=offset,
        )
    query = _outbound_query(source_type, start_date, end_date, keyword).order_by(
        InventoryDaily.work_date.desc(),
        InventoryDaily.product_name,
        InventoryDaily.id.desc(),
    )
    if offset:
        query = query.offset(max(int(offset), 0))
    if limit is not None:
        query = query.limit(max(int(limit), 0))
    return list(db.execute(query).scalars())


def create_date(db: Session, source_type: str, work_date: date | None = None) -> dict:
    if use_legacy_supabase_rest_store():
        return {"ok": True, "message": "Supabase는 거래 기준으로 현재고를 계산하므로 별도 기준일자 생성이 필요 없습니다.", "count": 0}

    target_date = work_date or date.today()
    count = ensure_daily_snapshots_from_latest(db, source_type, target_date)
    update_status(db, source_type, target_date)
    return {"ok": True, "message": "기준일자 Snapshot 생성/승계 완료", "count": count}


def row_data(row) -> dict:
    if hasattr(row, "model_dump"):
        return row.model_dump()
    return dict(row)


def bulk_save_daily(db: Session, source_type: str, work_date: date, rows: list[dict]) -> int:
    existing_rows = {
        row.product_name: daily_to_dict(row)
        for row in list_daily(db, source_type, work_date)
    }
    db.execute(delete(InventoryDaily).where(InventoryDaily.source_type == source_type, InventoryDaily.work_date == work_date))
    seen: set[tuple[str, str]] = set()
    count = 0
    for row in rows:
        data = row_data(row)
        sku = clean_text(data.get("product_code"))
        product = find_product_master(db, source_type, sku, normalize_barcode_text(data.get("barcode")), clean_text(data.get("product_name")))
        if product:
            data["product_code"] = product.sku
            data["barcode"] = normalize_barcode_text(product.barcode)
            data["product_name"] = product.product_name
            data["category"] = product.large_category
            data["supplier"] = product.supplier
        product_name = clean_text(data.get("product_name"))
        barcode = normalize_barcode_text(data.get("barcode"))
        key = product_name
        if not product_name or key in seen:
            continue
        seen.add(key)
        data.pop("id", None)
        data["source_type"] = source_type
        data["work_date"] = work_date
        data["product_name"] = product_name
        data["barcode"] = barcode
        existing = existing_rows.get(key, {})
        data["category"] = clean_text(data.get("category")) or existing.get("category", "")
        data["product_code"] = clean_text(data.get("product_code")) or existing.get("product_code", "")
        data["supplier"] = clean_text(data.get("supplier")) or existing.get("supplier", "")
        if model_has_field(InventoryDaily, "storage_location"):
            data["storage_location"] = clean_text(
                data.get("storage_location")
                or data.get("재고위치")
                or data.get("보관위치")
                or data.get("location")
            ) or existing.get("storage_location", "")
        else:
            data.pop("storage_location", None)
            data.pop("재고위치", None)
            data.pop("보관위치", None)
            data.pop("location", None)
        data["current_stock"] = to_int(data.get("current_stock"))
        data["available_stock"] = to_int(data.get("available_stock"))
        data["safe_stock"] = to_int(data.get("safe_stock"))
        data["outbound_qty"] = to_int(data.get("outbound_qty")) if "outbound_qty" in data else existing.get("outbound_qty", 0)
        data["inbound_qty"] = to_int(data.get("inbound_qty")) if "inbound_qty" in data else existing.get("inbound_qty", 0)
        data["inbound_cycle"] = (to_int(data.get("inbound_cycle")) or None) if "inbound_cycle" in data else existing.get("inbound_cycle")
        data["previous_inbound_date"] = parse_date(data.get("previous_inbound_date")) if "previous_inbound_date" in data else existing.get("previous_inbound_date")
        data["last_inbound_date"] = parse_date(data.get("last_inbound_date")) if "last_inbound_date" in data else existing.get("last_inbound_date")
        data["memo"] = clean_text(data.get("memo")) or existing.get("memo", "")
        item = InventoryDaily(**data)
        apply_product_master_to_daily(item, product or find_product_master(db, source_type, item.product_code, item.barcode, item.product_name))
        db.add(item)
        count += 1
    db.commit()
    update_status(db, source_type, work_date)
    calculate_inbound_cycle(db, source_type)
    return count


def bulk_save_inbound(db: Session, source_type: str, rows: list[dict]) -> int:
    if use_legacy_supabase_rest_store():
        return supabase_store.bulk_save_inbound(source_type, rows)

    existing_rows = {
        (
            row.inbound_date,
            row.product_name,
            row.barcode or "",
            row.inbound_qty,
            row.inbound_type or "",
            row.memo or "",
        ): inbound_to_dict(row)
        for row in list_inbound(db, source_type)
    }
    db.execute(delete(InventoryInbound).where(InventoryInbound.source_type == source_type))
    lookup = product_master_lookup(db, source_type)
    count = 0
    for row in rows:
        data = row_data(row)
        product = find_product_master_from_lookup(
            lookup,
            clean_text(data.get("product_code")),
            normalize_barcode_text(data.get("barcode")),
            clean_text(data.get("product_name")),
        )
        if product:
            data["product_code"] = product.sku
            data["barcode"] = normalize_barcode_text(product.barcode)
            data["product_name"] = product.product_name
            data["category"] = product.large_category
            data["vendor"] = product.supplier or clean_text(data.get("vendor"))
        product_name = clean_text(data.get("product_name"))
        if not product_name:
            continue
        data.pop("id", None)
        data["source_type"] = source_type
        data["product_name"] = product_name
        data["product_code"] = clean_text(data.get("product_code"))
        data["barcode"] = normalize_barcode_text(data.get("barcode"))
        data["inbound_date"] = parse_date(data.get("inbound_date")) or date.today()
        data["inbound_qty"] = to_int(data.get("inbound_qty"))
        data["inbound_type"] = clean_text(data.get("inbound_type"))
        data["memo"] = clean_text(data.get("memo"))
        key = (
            data["inbound_date"],
            product_name,
            data["barcode"],
            data["inbound_qty"],
            data["inbound_type"],
            data["memo"],
        )
        data["vendor"] = clean_text(data.get("vendor")) or clean_text(data.get("supplier"))
        data["is_applied"] = bool(data.get("is_applied")) if "is_applied" in data else bool(existing_rows.get(key, {}).get("is_applied", False))
        item = InventoryInbound(**data)
        apply_product_master_to_inbound(
            item,
            product or find_product_master_from_lookup(lookup, item.product_code, item.barcode, item.product_name),
        )
        db.add(item)
        count += 1
    db.commit()
    return count


def get_or_create_daily(db: Session, source_type: str, work_date: date, product_name: str, barcode: str = "") -> InventoryDaily:
    barcode = normalize_barcode_text(barcode)
    item = db.execute(
        select(InventoryDaily).where(
            InventoryDaily.source_type == source_type,
            InventoryDaily.work_date == work_date,
            InventoryDaily.product_name == product_name,
        )
    ).scalar_one_or_none()
    if item:
        return item
    item = InventoryDaily(source_type=source_type, work_date=work_date, product_name=product_name, barcode=barcode)
    db.add(item)
    db.flush()
    return item


def latest_daily_row_before(
    db: Session,
    source_type: str,
    work_date: date,
    product_code: str = "",
    product_name: str = "",
    barcode: str = "",
) -> InventoryDaily | None:
    sku = normalize_product_code_text(product_code)
    name = clean_text(product_name)
    barcode_text = normalize_barcode_text(barcode)
    filters = [
        InventoryDaily.source_type == source_type,
        InventoryDaily.work_date < work_date,
    ]
    identity_filters = []
    if name:
        identity_filters.append(InventoryDaily.product_name == name)
    if sku:
        identity_filters.append(InventoryDaily.product_code == sku)
    if barcode_text:
        identity_filters.append(InventoryDaily.barcode == barcode_text)
    if identity_filters:
        filters.append(or_(*identity_filters))
    return db.execute(
        select(InventoryDaily)
        .where(*filters)
        .order_by(InventoryDaily.work_date.desc(), InventoryDaily.id.desc())
    ).scalars().first()


def ensure_offline_daily_row(
    db: Session,
    work_date: date,
    product=None,
    product_code: str = "",
    product_name: str = "",
    barcode: str = "",
    category: str = "",
    supplier: str = "",
) -> InventoryDaily:
    if product is not None:
        product_code = clean_text(getattr(product, "sku", "")) or product_code
        product_name = clean_text(getattr(product, "product_name", "")) or product_name
        barcode = normalize_barcode_text(getattr(product, "barcode", "")) or barcode
        category = product_category_text(product) or category
        supplier = clean_text(getattr(product, "supplier", "")) or supplier

    product_name = clean_text(product_name)
    barcode = normalize_barcode_text(barcode)
    item = db.execute(
        select(InventoryDaily).where(
            InventoryDaily.source_type == "오프라인",
            InventoryDaily.work_date == work_date,
            InventoryDaily.product_name == product_name,
        )
    ).scalar_one_or_none()
    if item is not None:
        return item

    previous = latest_daily_row_before(db, "오프라인", work_date, product_code, product_name, barcode)
    item = InventoryDaily(
        source_type="오프라인",
        work_date=work_date,
        product_code=clean_text(product_code),
        product_name=product_name,
        barcode=barcode,
        category=clean_text(category),
        supplier=clean_text(supplier),
        current_stock=int(previous.current_stock or 0) if previous else 0,
        available_stock=int(previous.available_stock if previous and previous.available_stock is not None else previous.current_stock if previous else 0),
        safe_stock=int(previous.safe_stock or getattr(product, "min_stock", 0) or 0) if previous else int(getattr(product, "min_stock", 0) or 0),
        outbound_qty=0,
        inbound_qty=0,
        previous_inbound_date=previous.previous_inbound_date if previous else None,
        last_inbound_date=previous.last_inbound_date if previous else None,
        inbound_cycle=int(previous.inbound_cycle or getattr(product, "default_lead_time", 0) or 0) or None if previous else int(getattr(product, "default_lead_time", 0) or 0) or None,
        memo=previous.memo if previous else "",
    )
    apply_product_master_to_daily(item, product)
    item.stock_status = inventory_stock_status_for_daily_row(item)
    db.add(item)
    db.flush()
    return item


def propagate_offline_daily_delta(
    db: Session,
    work_date: date,
    product_code: str,
    product_name: str,
    barcode: str,
    delta: int,
) -> int:
    if not delta:
        return 0
    sku = normalize_product_code_text(product_code)
    name = clean_text(product_name)
    barcode_text = normalize_barcode_text(barcode)
    identity_filters = []
    if name:
        identity_filters.append(InventoryDaily.product_name == name)
    if sku:
        identity_filters.append(InventoryDaily.product_code == sku)
    if barcode_text:
        identity_filters.append(InventoryDaily.barcode == barcode_text)
    if not identity_filters:
        return 0
    rows = list(
        db.execute(
            select(InventoryDaily).where(
                InventoryDaily.source_type == "오프라인",
                InventoryDaily.work_date > work_date,
                or_(*identity_filters),
            )
        ).scalars()
    )
    for row in rows:
        row.current_stock = int(row.current_stock or 0) + int(delta)
        row.available_stock = int(row.available_stock if row.available_stock is not None else row.current_stock or 0) + int(delta)
        row.stock_status = inventory_stock_status_for_daily_row(row)
    return len(rows)


def import_stock(db: Session, source_type: str, work_date: date, file_bytes: bytes) -> dict:
    preview = prepare_stock_upload_preview(db, source_type, work_date, file_bytes)
    return apply_stock_upload_preview(db, source_type, work_date, preview)


def import_order(db: Session, source_type: str, work_date: date, file_bytes: bytes) -> dict:
    _ = (db, source_type, work_date, file_bytes)
    return {
        "ok": False,
        "message": "주문조회/출고 엑셀 별도 반영은 종료되었습니다. ERP 재고조회 파일의 송장+접수 값으로 출고예정을 반영하세요.",
        "count": 0,
    }


def import_inbound_excel(db: Session, source_type: str, file_bytes: bytes) -> dict:
    df = read_seonghyun_inbound_statement(file_bytes) if source_type == "3PL" else None
    if df is None:
        df = read_excel(file_bytes)
    date_col = None
    try:
        date_col = find_column(df, ["입고일자", "입고일", "일자"])
    except ValueError:
        pass
    name_col = find_column(df, ["품목", "상품명"])
    qty_col = find_column(df, ["수량", "입고수량"])
    try:
        product_code_col = find_column(df, ["SKU", "상품코드", "품목코드", "상품번호"])
    except ValueError:
        product_code_col = None
    try:
        barcode_col = find_column(df, ["바코드", "옵션바코드"])
    except ValueError:
        barcode_col = None
    try:
        category_col = find_column(df, ["카테고리", "분류"])
    except ValueError:
        category_col = None
    try:
        vendor_col = find_column(df, ["거래처", "공급처"])
    except ValueError:
        vendor_col = None
    try:
        type_col = find_column(df, ["입고구분", "구분"])
    except ValueError:
        type_col = None

    if use_legacy_supabase_rest_store():
        rows = []
        for _, row in df.iterrows():
            product_name = clean_text(row.get(name_col))
            if not product_name:
                continue
            rows.append(
                {
                    "inbound_date": parse_date(row.get(date_col)) if date_col else date.today(),
                    "category": clean_text(row.get(category_col)) if category_col else "",
                    "product_code": clean_text(row.get(product_code_col)) if product_code_col else "",
                    "product_name": product_name,
                    "barcode": normalize_barcode_text(row.get(barcode_col)) if barcode_col else "",
                    "inbound_qty": to_int(row.get(qty_col)),
                    "vendor": clean_text(row.get(vendor_col)) if vendor_col else "",
                    "inbound_type": clean_text(row.get(type_col)) if type_col else "",
                    "memo": "",
                }
            )
        return import_result(supabase_store.bulk_save_inbound(source_type, rows), df)

    lookup = product_master_lookup(db, source_type)
    count = 0
    for _, row in df.iterrows():
        product_name = clean_text(row.get(name_col))
        if not product_name:
            continue
        inbound_date = parse_date(row.get(date_col)) if date_col else date.today()
        item = InventoryInbound(
                source_type=source_type,
                inbound_date=inbound_date or date.today(),
                category=clean_text(row.get(category_col)) if category_col else "",
                product_code=clean_text(row.get(product_code_col)) if product_code_col else "",
                product_name=product_name,
                barcode=normalize_barcode_text(row.get(barcode_col)) if barcode_col else "",
                inbound_qty=to_int(row.get(qty_col)),
                vendor=clean_text(row.get(vendor_col)) if vendor_col else "",
                inbound_type=clean_text(row.get(type_col)) if type_col else "",
                is_applied=False,
            )
        apply_product_master_to_inbound(
            item,
            find_product_master_from_lookup(lookup, item.product_code, item.barcode, item.product_name),
        )
        db.add(item)
        count += 1
    db.commit()
    return import_result(count, df)


def apply_inbound_to_stock(db: Session, source_type: str, work_date: date) -> int:
    if use_legacy_supabase_rest_store():
        return 0

    inbound_rows = list(
        db.execute(
            select(InventoryInbound).where(
                InventoryInbound.source_type == source_type,
                InventoryInbound.inbound_date == work_date,
                InventoryInbound.is_applied == False,  # noqa: E712
            )
        ).scalars()
    )
    if not inbound_rows:
        return 0

    lookup = product_master_lookup(db, source_type)
    inbound_groups: dict[str, dict] = {}
    touched_keys: set[str] = set()
    for inbound in inbound_rows:
        product = find_product_master_from_lookup(lookup, inbound.product_code, inbound.barcode, inbound.product_name)
        product_name = clean_text(getattr(product, "product_name", "")) or clean_text(inbound.product_name)
        barcode = normalize_barcode_text(getattr(product, "barcode", "")) or normalize_barcode_text(inbound.barcode)
        key = product_name
        group = inbound_groups.setdefault(
            key,
            {
                "product": product,
                "product_code": clean_text(getattr(product, "sku", "")) or clean_text(inbound.product_code),
                "product_name": product_name,
                "barcode": barcode,
                "category": clean_text(getattr(product, "large_category", "")) or clean_text(inbound.category),
                "supplier": clean_text(getattr(product, "supplier", "")) or clean_text(inbound.vendor),
                "safe_stock": int(getattr(product, "min_stock", 0) or 0),
                "qty": 0,
                "last_inbound_date": inbound.inbound_date,
            },
        )
        group["qty"] += int(inbound.inbound_qty or 0)
        if inbound.inbound_date and (not group["last_inbound_date"] or inbound.inbound_date > group["last_inbound_date"]):
            group["last_inbound_date"] = inbound.inbound_date
        touched_keys.add(key)

    existing_daily = {
        row.product_name: row
        for row in db.execute(
            select(InventoryDaily).where(
                InventoryDaily.source_type == source_type,
                InventoryDaily.work_date == work_date,
                InventoryDaily.product_name.in_(list(inbound_groups.keys())),
            )
        ).scalars()
    }

    count = 0
    for key, group in inbound_groups.items():
        if source_type == "오프라인":
            item = ensure_offline_daily_row(
                db,
                work_date,
                group["product"],
                product_code=group["product_code"],
                product_name=group["product_name"],
                barcode=group["barcode"],
                category=group["category"],
                supplier=group["supplier"],
            )
        else:
            item = existing_daily.get(key)
            if item is None:
                item = InventoryDaily(
                    source_type=source_type,
                    work_date=work_date,
                    product_name=group["product_name"],
                    barcode=group["barcode"],
                )
                db.add(item)
        if not item.category:
            item.category = group["category"]
        if not item.product_code:
            item.product_code = group["product_code"]
        if not item.supplier:
            item.supplier = group["supplier"]
        apply_product_master_to_daily(item, group["product"])
        qty = int(group["qty"] or 0)
        item.inbound_qty = int(item.inbound_qty or 0) + qty
        item.current_stock = int(item.current_stock or 0) + qty
        item.available_stock = int(item.available_stock or 0) + qty
        if group["safe_stock"] and not item.safe_stock:
            item.safe_stock = group["safe_stock"]
        if item.last_inbound_date and item.last_inbound_date != group["last_inbound_date"]:
            item.previous_inbound_date = item.last_inbound_date
        item.last_inbound_date = group["last_inbound_date"]
        item.stock_status = inventory_stock_status_for_daily_row(item)
        if source_type == "오프라인":
            propagate_offline_daily_delta(
                db,
                work_date,
                item.product_code,
                item.product_name,
                item.barcode,
                qty,
            )
        count += 1

    for inbound in inbound_rows:
        inbound.is_applied = True
    db.commit()
    calculate_inbound_cycle_for_keys(db, source_type, touched_keys)
    return count


def calculate_inbound_cycle_for_keys(db: Session, source_type: str, keys: set[str]) -> int:
    if not keys:
        return 0
    rows = list(
        db.execute(
            select(InventoryDaily).where(
                InventoryDaily.source_type == source_type,
                InventoryDaily.product_name.in_(list(keys)),
            )
        ).scalars()
    )
    grouped: dict[str, list[InventoryDaily]] = {}
    for row in rows:
        grouped.setdefault(row.product_name, []).append(row)

    count = 0
    for product_rows in grouped.values():
        dates = sorted({row.last_inbound_date for row in product_rows if row.last_inbound_date})
        diffs = [
            (dates[index] - dates[index - 1]).days
            for index in range(1, len(dates))
            if 1 <= (dates[index] - dates[index - 1]).days <= 90
        ]
        cycle = round(median(diffs)) if diffs else None
        for row in product_rows:
            row.inbound_cycle = cycle
        count += 1
    db.commit()
    return count


def week_range(target: date, weeks_ago: int) -> tuple[date, date]:
    current_monday = target - timedelta(days=target.weekday())
    start = current_monday - timedelta(days=7 * weeks_ago)
    return start, start + timedelta(days=6)


def outbound_sum_by_product(db: Session, source_type: str, start: date, end: date) -> dict[tuple[str, str], int]:
    rows = db.execute(
        select(InventoryDaily.product_name, func.sum(InventoryDaily.outbound_qty))
        .where(
            InventoryDaily.source_type == source_type,
            InventoryDaily.work_date >= start,
            InventoryDaily.work_date <= end,
        )
        .group_by(InventoryDaily.product_name)
    ).all()
    return {(name, ""): int(total or 0) for name, total in rows}


def calculate_safe_stock(db: Session, source_type: str, work_date: date) -> int:
    products = list(db.execute(select(product_master_model(source_type))).scalars())
    avg_map = recent_outbound_average_by_product(db, source_type, work_date, products)
    product_by_sku = {clean_text(product.sku): product for product in products if clean_text(product.sku)}
    count = 0
    for item in list_daily(db, source_type, work_date):
        product = find_product_master(db, source_type, item.product_code, item.barcode, item.product_name)
        if product is None:
            continue
        avg_daily = float(avg_map.get(clean_text(product.sku), 0) or 0)
        lead_time_days = int(product.default_lead_time or item.inbound_cycle or 1)
        safe_stock = ceil(avg_daily * max(lead_time_days, 1) * 1.2)
        item.safe_stock = safe_stock
        product_by_sku.get(clean_text(product.sku), product).min_stock = safe_stock
        count += 1
    db.commit()
    return count


def update_status(db: Session, source_type: str, work_date: date) -> int:
    count = 0
    for item in list_daily(db, source_type, work_date):
        product = find_product_master(db, source_type, item.product_code, item.barcode, item.product_name)
        safe_stock = int(product.min_stock or 0) if product else int(item.safe_stock or 0)
        item.safe_stock = safe_stock
        item.stock_status = inventory_stock_status_for_daily_row(item)
        count += 1
    db.commit()
    return count


def calculate_inbound_cycle(db: Session, source_type: str) -> int:
    keys = db.execute(
        select(InventoryDaily.product_name)
        .where(InventoryDaily.source_type == source_type)
        .distinct()
    ).all()
    count = 0
    for (product_name,) in keys:
        dates = sorted(
            {
                row.last_inbound_date
                for row in db.execute(
                    select(InventoryDaily).where(
                        InventoryDaily.source_type == source_type,
                        InventoryDaily.product_name == product_name,
                    )
                ).scalars()
                if row.last_inbound_date
            }
        )
        diffs = [
            (dates[index] - dates[index - 1]).days
            for index in range(1, len(dates))
            if 1 <= (dates[index] - dates[index - 1]).days <= 90
        ]
        cycle = round(median(diffs)) if diffs else None
        for row in db.execute(
            select(InventoryDaily).where(
                InventoryDaily.source_type == source_type,
                InventoryDaily.product_name == product_name,
            )
        ).scalars():
            row.inbound_cycle = cycle
        count += 1
    db.commit()
    return count


def dataframe_for_daily(rows: list[InventoryDaily]) -> pd.DataFrame:
    return pd.DataFrame([daily_to_dict(row) for row in rows])


def dataframe_for_inbound(rows: list[InventoryInbound]) -> pd.DataFrame:
    return pd.DataFrame([inbound_to_dict(row) for row in rows])


def daily_to_dict(row: InventoryDaily) -> dict:
    return {
        "id": row.id,
        "source_type": row.source_type,
        "work_date": row.work_date,
        "category": row.category,
        "product_code": row.product_code,
        "product_name": row.product_name,
        "barcode": row.barcode,
        "supplier": row.supplier,
        "storage_location": getattr(row, "storage_location", ""),
        "current_stock": row.current_stock,
        "available_stock": row.available_stock,
        "safe_stock": row.safe_stock,
        "stock_status": inventory_stock_status_for_daily_row(row),
        "outbound_qty": row.outbound_qty,
        "previous_inbound_date": row.previous_inbound_date,
        "last_inbound_date": row.last_inbound_date,
        "inbound_qty": row.inbound_qty,
        "inbound_cycle": row.inbound_cycle,
        "memo": row.memo,
        "updated_at": getattr(row, "updated_at", None),
    }


def inbound_to_dict(row: InventoryInbound) -> dict:
    return {
        "id": row.id,
        "source_type": row.source_type,
        "inbound_date": row.inbound_date,
        "category": row.category,
        "product_code": row.product_code,
        "product_name": row.product_name,
        "barcode": row.barcode,
        "inbound_qty": row.inbound_qty,
        "vendor": row.vendor,
        "inbound_type": row.inbound_type,
        "memo": row.memo,
        "is_applied": row.is_applied,
        "created_at": getattr(row, "created_at", None),
        "updated_at": getattr(row, "updated_at", None),
    }


def excel_bytes(df: pd.DataFrame, sheet_name: str) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name[:31])
    return output.getvalue()


def dashboard_summary(db: Session, work_date: date, source_type: str | None = None) -> dict:
    if use_legacy_supabase_rest_store():
        sources = [source_type] if source_type and source_type != "전체" else list(PRODUCT_MASTER_MODEL_BY_SOURCE.keys())
        rows = []
        for source in sources:
            rows.extend(supabase_store.daily_rows(source, work_date))
        statuses = [
            inventory_stock_status_for_snapshot(
                True,
                row.get("available_stock"),
                row.get("current_stock"),
                int(row.get("safe_stock") or 0),
                int(row.get("outbound_qty") or 0),
            )
            for row in rows
        ]
        return {
            "sku_count": len(rows),
            "current_stock": sum(int(row.get("current_stock") or 0) for row in rows),
            "available_stock": sum(int(row.get("available_stock") or 0) for row in rows),
            "need_inbound_count": sum(1 for status in statuses if status in {"부족", "주의", "입고필요"}),
            "soldout_count": sum(1 for status in statuses if status == "품절"),
            "short_count": sum(1 for status in statuses if status in {"부족", "품절", "미출"}),
            "outbound_qty": 0,
            "inbound_qty": 0,
        }

    filters = [InventoryDaily.work_date == work_date]
    if source_type and source_type != "전체":
        filters.append(InventoryDaily.source_type == source_type)
    rows = list(db.execute(select(InventoryDaily).where(*filters)).scalars())
    statuses = [inventory_stock_status_for_daily_row(row) for row in rows]
    return {
        "sku_count": len(rows),
        "current_stock": sum(int(row.current_stock or 0) for row in rows),
        "available_stock": sum(int(row.available_stock if row.available_stock is not None else row.current_stock or 0) for row in rows),
        "outbound_qty": sum(int(row.outbound_qty or 0) for row in rows),
        "inbound_qty": sum(int(row.inbound_qty or 0) for row in rows),
        "need_inbound_count": sum(1 for status in statuses if status in {"부족", "주의", "입고필요"}),
        "soldout_count": sum(1 for status in statuses if status == "품절"),
        "short_count": sum(1 for status in statuses if status in {"부족", "품절", "미출"}),
    }


def dashboard_chart(db: Session, work_date: date, source_type: str | None = None) -> dict:
    if use_legacy_supabase_rest_store():
        sources = [source_type] if source_type and source_type != "전체" else list(PRODUCT_MASTER_MODEL_BY_SOURCE.keys())
        rows = []
        for source in sources:
            rows.extend(supabase_store.daily_rows(source, work_date))
        category_totals: dict[str, int] = {}
        for row in rows:
            label = row.get("category") or "미분류"
            category_totals[label] = category_totals.get(label, 0) + int(row.get("current_stock") or 0)
        need_rows = sorted(
            [
                row
                for row in rows
                if inventory_stock_status_for_snapshot(
                    True,
                    row.get("available_stock"),
                    row.get("current_stock"),
                    int(row.get("safe_stock") or 0),
                    int(row.get("outbound_qty") or 0),
                )
                in {"부족", "주의", "입고필요"}
            ],
            key=lambda row: int(row.get("safe_stock") or 0) - int(row.get("available_stock") or row.get("current_stock") or 0),
            reverse=True,
        )[:10]
        return {
            "stock_by_source": [
                {"label": source, "value": sum(int(row.get("current_stock") or 0) for row in rows if row.get("source_type") == source)}
                for source in sources
            ],
            "stock_by_category": [{"label": label, "value": value} for label, value in sorted(category_totals.items())],
            "outbound_by_category": [],
            "stock_trend": [{"date": str(work_date), "value": sum(int(row.get("current_stock") or 0) for row in rows)}],
            "outbound_trend": [],
            "need_inbound_top10": [
                {
                    "product_name": row.get("product_name"),
                    "current_stock": int(row.get("current_stock") or 0),
                    "safe_stock": int(row.get("safe_stock") or 0),
                }
                for row in need_rows
            ],
        }

    base_filters = [InventoryDaily.work_date == work_date]
    trend_filters = []
    if source_type and source_type != "전체":
        base_filters.append(InventoryDaily.source_type == source_type)
        trend_filters.append(InventoryDaily.source_type == source_type)

    def grouped(label_column, value_column, filters):
        rows = db.execute(
            select(label_column, func.sum(value_column)).where(*filters).group_by(label_column)
        ).all()
        return [{"label": str(label or "미분류"), "value": int(value or 0)} for label, value in rows]

    def grouped_by_master_category(value_attr: str) -> list[dict]:
        value_column = getattr(InventoryDaily, value_attr)
        rows = db.execute(
            select(InventoryDaily.category, func.sum(value_column))
            .where(*base_filters)
            .group_by(InventoryDaily.category)
        ).all()
        return [{"label": str(label or "미분류"), "value": int(value or 0)} for label, value in rows]

    def trend(value_column):
        rows = db.execute(
            select(InventoryDaily.work_date, func.sum(value_column))
            .where(*trend_filters)
            .group_by(InventoryDaily.work_date)
            .order_by(InventoryDaily.work_date)
        ).all()
        return [{"date": str(day), "value": int(value or 0)} for day, value in rows]

    daily_rows = list(db.execute(select(InventoryDaily).where(*base_filters)).scalars())
    top_rows = sorted(
        [
            row
            for row in daily_rows
            if inventory_stock_status_for_daily_row(row) in {"부족", "주의", "입고필요"}
        ],
        key=lambda row: int(row.safe_stock or 0) - int(row.available_stock if row.available_stock is not None else row.current_stock or 0),
        reverse=True,
    )[:10]

    return {
        "stock_by_source": grouped(InventoryDaily.source_type, InventoryDaily.current_stock, [InventoryDaily.work_date == work_date]),
        "stock_by_category": grouped_by_master_category("current_stock"),
        "outbound_by_category": grouped_by_master_category("outbound_qty"),
        "stock_trend": trend(InventoryDaily.current_stock),
        "outbound_trend": trend(InventoryDaily.outbound_qty),
        "need_inbound_top10": [
            {"product_name": row.product_name, "current_stock": int(row.current_stock or 0), "safe_stock": int(row.safe_stock or 0)}
            for row in top_rows
        ],
    }

