from __future__ import annotations

from datetime import date, timedelta
from html.parser import HTMLParser
from io import BytesIO, StringIO
import json
from math import ceil
import re
from statistics import median

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

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
    WarehouseProductMaster,
)


HTML_TABLE_FALLBACK_MESSAGE = "엑셀 형식이 HTML 기반이라 read_html로 처리했습니다"
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
    "분류",
    "상품분류",
    "업체명",
    "박스/파렛트 단위",
    "담당자",
    "현재고",
    "가용재고",
    "안전재고",
    "재고상태",
    "리드타임",
    "기본창고-정상",
}
PRODUCT_MASTER_COLUMNS = [
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

PRODUCT_MASTER_MODEL_BY_SOURCE = {
    "오프라인": OfflineProductMaster,
    "3PL": ThirdpartyProductMaster,
    "창고": WarehouseProductMaster,
}

PURCHASE_METRIC_SOURCE_ORDER = ["창고", "3PL", "오프라인"]
STOCK_WARNING_RATIO = 0.2


def product_master_model(source_type: str):
    return PRODUCT_MASTER_MODEL_BY_SOURCE.get(source_type, ThirdpartyProductMaster)


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
    return str(value).strip()


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


def is_business_day(day: date, holidays: set[date] | None = None) -> bool:
    holiday_set = holidays or set()
    return day.weekday() < 5 and day not in holiday_set


def normalize_html_cell(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


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
        name = normalize_html_cell(str(header)) or f"column_{index}"
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
    current_headers = {normalize_html_cell(str(column)) for column in df.columns}
    if KNOWN_IMPORT_HEADERS.intersection(current_headers):
        df.columns = unique_headers([normalize_html_cell(str(column)) for column in df.columns])
        return df

    scan_limit = min(len(df), 10)
    for index in range(scan_limit):
        row_values = [
            "" if pd.isna(value) else normalize_html_cell(str(value))
            for value in df.iloc[index].tolist()
        ]
        if KNOWN_IMPORT_HEADERS.intersection(set(row_values)):
            normalized = df.iloc[index + 1 :].reset_index(drop=True).copy()
            normalized.columns = unique_headers(row_values)
            return normalized
    return df


def has_known_import_headers(df: pd.DataFrame) -> bool:
    headers = {normalize_html_cell(str(column)) for column in df.columns}
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
        sheets = pd.read_excel(uploaded_file, sheet_name=None, engine="openpyxl")
        if not sheets:
            raise ValueError("엑셀 시트를 찾지 못했습니다.")
        selected_sheet = ""
        selected_raw = pd.DataFrame()
        selected_df = pd.DataFrame()
        non_empty_sheets = []
        fallback_raw = pd.DataFrame()
        fallback_name = ""
        for sheet_name, sheet_df in sheets.items():
            if sheet_df is None or sheet_df.dropna(how="all").empty:
                continue
            non_empty_sheets.append(sheet_name)
            if fallback_raw.empty:
                fallback_name = sheet_name
                fallback_raw = sheet_df
            candidate = normalize_import_headers(sheet_df)
            if has_known_import_headers(candidate):
                selected_sheet = sheet_name
                selected_raw = sheet_df
                selected_df = candidate
                break
        if selected_df.empty:
            selected_sheet = fallback_name or next(iter(sheets.keys()))
            selected_raw = fallback_raw if not fallback_raw.empty else next(iter(sheets.values()))
            selected_df = normalize_import_headers(selected_raw)
        df = selected_df
        df.attrs["read_method"] = "excel"
        df.attrs["sheet_names"] = list(sheets.keys())
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

        statement_date = parse_date(raw_df.iat[1, 0] if raw_df.shape[0] > 1 and raw_df.shape[1] > 0 else None) or date.today()
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
    normalized = {str(column).strip().replace(" ", ""): column for column in df.columns}
    for candidate in candidates:
        key = candidate.strip().replace(" ", "")
        if key in normalized:
            return normalized[key]
    raise ValueError(f"필수 컬럼을 찾지 못했습니다: {', '.join(candidates)}")


def list_work_dates(db: Session, source_type: str | None = None) -> list[date]:
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
        "large_category": row.large_category,
        "medium_category": row.medium_category,
        "small_category": row.small_category,
        "brand": row.brand,
        "supplier": row.supplier,
        "pack_qty": row.pack_qty,
        "box_qty": row.box_qty,
        "default_lead_time": row.default_lead_time,
        "min_stock": row.min_stock,
        "sort_order": row.sort_order,
        "is_active": row.is_active,
        "memo": row.memo,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def list_product_master(db: Session, source_type: str, keyword: str = "", active_filter: str = "전체") -> list:
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
    model = product_master_model(source_type)
    rows = db.execute(select(model).where(model.is_active == "사용").order_by(model.sort_order, model.large_category, model.product_name, model.sku)).scalars()
    return [product_master_to_dict(row) for row in rows]


def find_product_master(db: Session, source_type: str, sku: str = "", barcode: str = "", product_name: str = ""):
    model = product_master_model(source_type)
    sku = clean_text(sku)
    barcode = clean_text(barcode)
    product_name = clean_text(product_name)
    if sku:
        row = db.execute(select(model).where(model.sku == sku)).scalar_one_or_none()
        if row:
            return row
    if barcode and product_name:
        row = db.execute(select(model).where(model.barcode == barcode, model.product_name == product_name)).scalar_one_or_none()
        if row:
            return row
    if barcode:
        row = db.execute(select(model).where(model.barcode == barcode).order_by(model.sku)).scalars().first()
        if row:
            return row
    if product_name:
        return db.execute(select(model).where(model.product_name == product_name)).scalar_one_or_none()
    return None


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


def purchase_inventory_metrics(db: Session, source_type: str | None = None) -> dict[tuple[str, str], dict]:
    source_types = [source_type] if source_type else PURCHASE_METRIC_SOURCE_ORDER
    products_by_key: dict[tuple[str, str], object] = {}
    sku_lookup: dict[str, list[tuple[str, object]]] = {}
    name_lookup: dict[str, list[tuple[str, object]]] = {}
    barcode_lookup: dict[str, list[tuple[str, object]]] = {}

    for product_source in source_types:
        model = product_master_model(product_source)
        for product in db.execute(select(model)).scalars():
            key = (product_source, product.sku)
            products_by_key[key] = product
            if product.sku:
                sku_lookup.setdefault(clean_text(product.sku).lower(), []).append((product_source, product))
            if product.barcode:
                barcode_lookup.setdefault(clean_text(product.barcode).lower(), []).append((product_source, product))
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


def latest_inbound_metrics(db: Session, source_type: str | None = None) -> dict[tuple[str, str], dict]:
    query = select(InventoryInbound)
    if source_type:
        query = query.where(InventoryInbound.source_type == source_type)
    metrics: dict[tuple[str, str], dict] = {}
    for inbound in db.execute(query.order_by(InventoryInbound.inbound_date)).scalars():
        product = find_product_master(db, inbound.source_type, inbound.product_code, inbound.barcode, inbound.product_name)
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
    item.product_code = product.sku
    item.barcode = product.barcode
    item.product_name = product.product_name
    item.category = product.large_category
    item.supplier = product.supplier
    if product.min_stock and not item.safe_stock:
        item.safe_stock = product.min_stock


def apply_product_master_to_inbound(item: InventoryInbound, product) -> None:
    if not product:
        return
    item.product_code = product.sku
    item.barcode = product.barcode
    item.product_name = product.product_name
    item.category = product.large_category
    item.vendor = product.supplier or item.vendor


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
    barcode = clean_text(data.get("바코드") or data.get("88바코드") or data.get("옵션바코드") or data.get("barcode"))
    if not sku and barcode:
        sku = barcode
    if not barcode and sku:
        barcode = sku
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
        "large_category": clean_text(
            data.get("카테고리")
            or data.get("카테고리명")
            or data.get("상품카테고리")
            or data.get("상품 카테고리")
            or data.get("대분류")
            or data.get("대분류명")
            or data.get("대 카테고리")
            or data.get("대카테고리")
            or data.get("분류")
            or data.get("상품분류")
            or data.get("category")
            or data.get("large_category")
            or data.get("largeCategory")
        ),
        "medium_category": "",
        "small_category": "",
        "brand": clean_text(data.get("브랜드") or data.get("brand")),
        "supplier": clean_text(data.get("공급처") or data.get("업체명") or data.get("거래처") or data.get("supplier")),
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


def prepare_product_master_import_rows(df: pd.DataFrame, source_type: str = "") -> tuple[list[dict], list[str]]:
    rows = []
    warnings = []
    seen_skus: set[str] = set()
    seen_barcode_products: set[tuple[str, str]] = set()
    skipped_empty = 0
    skipped_duplicate = 0

    for index, record in enumerate(df.fillna("").to_dict("records"), start=1):
        row = normalize_product_master_row(record)
        if source_type in {"오프라인", "창고"} and row["product_name"]:
            if not row["sku"]:
                row["sku"] = row["barcode"] or row["product_name"]
            if not row["barcode"]:
                row["barcode"] = row["sku"] or row["product_name"]
        if not row["sku"] and not row["barcode"] and not row["product_name"]:
            skipped_empty += 1
            continue
        if not row["sku"] or not row["barcode"] or not row["product_name"]:
            warnings.append(f"{index}행: SKU/바코드/상품명 중 누락된 값이 있어 제외했습니다.")
            continue
        if row["sku"] in seen_skus:
            skipped_duplicate += 1
            warnings.append(f"{index}행: 중복 SKU라 제외했습니다. ({row['sku']})")
            continue
        barcode_product_key = (row["barcode"], row["product_name"])
        if barcode_product_key in seen_barcode_products:
            skipped_duplicate += 1
            warnings.append(f"{index}행: 중복 바코드/상품명이라 제외했습니다. ({row['barcode']} / {row['product_name']})")
            continue
        seen_skus.add(row["sku"])
        seen_barcode_products.add(barcode_product_key)
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
    seen_barcode_products: set[tuple[str, str]] = set()
    for index, row in enumerate(normalized, start=1):
        if not row["sku"] or not row["barcode"] or not row["product_name"]:
            errors.append(f"{index}행: SKU, 바코드, 상품명은 필수입니다.")
        if row["sku"] in seen_skus:
            errors.append(f"{index}행: 중복 SKU입니다. ({row['sku']})")
        barcode_product_key = (row["barcode"], row["product_name"])
        if source_type != "3PL" and barcode_product_key in seen_barcode_products:
            errors.append(f"{index}행: 중복 바코드/상품명입니다. ({row['barcode']} / {row['product_name']})")
        seen_skus.add(row["sku"])
        seen_barcode_products.add(barcode_product_key)

    sku_rows = db.execute(select(model.sku, model.barcode, model.product_name)).all()
    existing_sku_to_barcode = {sku: barcode for sku, barcode, _product_name in sku_rows}
    existing_barcode_product_to_sku = {(barcode, product_name): sku for sku, barcode, product_name in sku_rows}
    for index, row in enumerate(normalized, start=1):
        existing_other_sku = existing_barcode_product_to_sku.get((row["barcode"], row["product_name"]))
        if source_type != "3PL" and existing_other_sku and existing_other_sku != row["sku"]:
            errors.append(f"{index}행: 같은 바코드/상품명이 다른 SKU에 이미 등록되어 있습니다. ({row['barcode']} / {row['product_name']})")
        existing_other_barcode = existing_sku_to_barcode.get(row["sku"])
        if source_type != "3PL" and existing_other_barcode and existing_other_barcode != row["barcode"]:
            errors.append(f"{index}행: 기존 SKU의 바코드와 다릅니다. ({row['sku']})")
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


def threepl_master_basis_data(row: dict) -> dict:
    return {
        "sku": clean_text(row.get("sku")),
        "barcode": clean_text(row.get("barcode")),
        "product_name": clean_text(row.get("product_name")),
        "large_category": clean_text(row.get("large_category")),
        "medium_category": "",
        "small_category": "",
        "brand": clean_text(row.get("brand")),
        "supplier": clean_text(row.get("supplier")),
        "pack_qty": to_int(row.get("pack_qty")),
        "box_qty": to_int(row.get("box_qty")),
        "default_lead_time": to_int(row.get("default_lead_time")),
        "memo": clean_text(row.get("memo")),
    }


def threepl_master_display_value(row: dict | object, field: str) -> str:
    if field == "box_pallet_unit":
        if isinstance(row, dict):
            return format_box_pallet_unit(row.get("box_qty"), row.get("pack_qty"))
        return format_box_pallet_unit(getattr(row, "box_qty", 0), getattr(row, "pack_qty", 0))
    value = row.get(field) if isinstance(row, dict) else getattr(row, field, "")
    return str(to_int(value)) if field == "default_lead_time" else clean_text(value)


def describe_threepl_master_changes(product, row: dict) -> list[str]:
    changes = []
    for field, label in THREEPL_MASTER_IMPORT_FIELDS:
        before = threepl_master_display_value(product, field)
        after = threepl_master_display_value(row, field)
        if before != after:
            changes.append(f"{label}: {before or '-'} → {after or '-'}")
    return changes


def threepl_master_identity(row: dict) -> str:
    return clean_text(row.get("sku")) or clean_text(row.get("barcode")) or clean_text(row.get("product_name"))


def find_threepl_master_for_import(existing_by_sku: dict[str, object], existing_by_barcode: dict[str, object], row: dict):
    sku = clean_text(row.get("sku"))
    barcode = clean_text(row.get("barcode"))
    if sku and sku in existing_by_sku:
        return existing_by_sku[sku]
    if barcode and barcode in existing_by_barcode:
        return existing_by_barcode[barcode]
    return None


def prepare_threepl_master_import_preview(db: Session, file_bytes: bytes) -> dict:
    df = read_excel(file_bytes)
    total_rows = len(df)
    parsed_rows: list[dict] = []
    details: list[dict] = []
    identity_row_numbers: dict[str, list[int]] = {}
    warnings = 0
    failures = 0

    for index, record in enumerate(df.fillna("").to_dict("records"), start=2):
        row = normalize_product_master_row(record)
        row_number = index
        if not row["sku"] and not row["barcode"] and not row["product_name"]:
            continue
        identity = threepl_master_identity(row)
        if not identity:
            failures += 1
            details.append(
                {
                    "행 번호": row_number,
                    "바코드": row["barcode"],
                    "상품명": row["product_name"],
                    "처리 유형": "확인 필요",
                    "변경 항목": "바코드/상품명 누락",
                    "처리 결과": "실패",
                    "_apply": False,
                    "_data": row,
                }
            )
            continue
        row["sku"] = clean_text(row.get("sku")) or identity
        parsed_rows.append({"row_number": row_number, "data": row})
        identity_row_numbers.setdefault(identity, []).append(row_number)

    duplicate_identities = {identity: rows for identity, rows in identity_row_numbers.items() if len(rows) > 1}
    duplicate_row_count = sum(len(rows) - 1 for rows in duplicate_identities.values())
    last_row_by_identity = {threepl_master_identity(item["data"]): item for item in parsed_rows}
    model = product_master_model("3PL")
    existing_by_sku = {row.sku: row for row in db.execute(select(model)).scalars()}
    existing_by_barcode = {row.barcode: row for row in existing_by_sku.values() if row.barcode}
    barcode_to_skus: dict[str, set[str]] = {}
    for product in existing_by_sku.values():
        if product.barcode:
            barcode_to_skus.setdefault(product.barcode, set()).add(product.sku)

    new_count = 0
    update_count = 0
    unchanged_count = 0
    for item in parsed_rows:
        row = item["data"]
        sku = row["sku"]
        identity = threepl_master_identity(row)
        row_number = item["row_number"]
        if last_row_by_identity.get(identity) is not item:
            warnings += 1
            details.append(
                {
                    "행 번호": row_number,
                    "바코드": row["barcode"],
                    "상품명": row["product_name"],
                    "처리 유형": "확인 필요",
                    "변경 항목": f"파일 내부 중복 바코드/상품명: {', '.join(map(str, duplicate_identities.get(identity, [])))}",
                    "처리 결과": "제외 - 마지막 행 정보 적용",
                    "_apply": False,
                    "_data": row,
                }
            )
            continue

        product = find_threepl_master_for_import(existing_by_sku, existing_by_barcode, row)
        if product is not None:
            row["sku"] = product.sku
            sku = row["sku"]
        row_warnings = []
        if row.get("barcode"):
            other_skus = sorted(s for s in barcode_to_skus.get(row["barcode"], set()) if s != sku)
            if other_skus:
                row_warnings.append(f"바코드 중복 SKU: {', '.join(other_skus)}")
        if identity in duplicate_identities:
            row_warnings.append(f"파일 내부 중복 - 마지막 행 적용: {', '.join(map(str, duplicate_identities[identity]))}")
        if row_warnings:
            warnings += len(row_warnings)

        if product is None:
            if not row["product_name"]:
                failures += 1
                action = "확인 필요"
                changes = ["신규 등록 상품명 누락"]
                result = "실패"
                apply_row = False
            else:
                new_count += 1
                action = "신규 등록"
                changes = [label for _field, label in THREEPL_MASTER_IMPORT_FIELDS]
                result = "등록 예정"
                apply_row = True
        else:
            changes = describe_threepl_master_changes(product, row)
            if changes:
                update_count += 1
                action = "기존 정보 변경"
                result = "업데이트 예정"
            else:
                unchanged_count += 1
                action = "변경 없음"
                result = "유지"
            apply_row = True

        if row_warnings:
            result = f"{result} / 경고: {'; '.join(row_warnings)}"
        details.append(
            {
                "행 번호": row_number,
                "바코드": row["barcode"],
                "상품명": row["product_name"],
                "처리 유형": action,
                "변경 항목": "\n".join(changes) if changes else "-",
                "처리 결과": result,
                "_apply": apply_row,
                "_data": row,
            }
        )

    return {
        "ok": True,
        "message": "3PL 마스터 업로드 미리보기 생성 완료",
        "summary": {
            "전체 엑셀 행 수": total_rows,
            "신규 등록 수": new_count,
            "기존 품목 업데이트 수": update_count,
            "변경 없음 수": unchanged_count,
            "파일 내부 중복 수": duplicate_row_count,
            "경고 수": warnings,
            "실패 수": failures,
        },
        "details": details,
        "used_html": df.attrs.get("read_method") == "html",
    }


def apply_threepl_master_import_preview(db: Session, preview: dict) -> dict:
    model = product_master_model("3PL")
    details = preview.get("details") or []
    result_details = []
    count = 0
    existing_products = list(db.execute(select(model)).scalars())
    existing_by_sku = {row.sku: row for row in existing_products}
    existing_by_barcode = {row.barcode: row for row in existing_products if row.barcode}
    for detail in details:
        if not detail.get("_apply"):
            result_details.append(detail)
            continue
        row = detail.get("_data") or {}
        data = threepl_master_basis_data(row)
        sku = data.pop("sku")
        if not sku:
            sku = clean_text(data.get("barcode")) or clean_text(data.get("product_name"))
        if not sku:
            result_details.append(detail)
            continue
        product = find_threepl_master_for_import(existing_by_sku, existing_by_barcode, {"sku": sku, **data})
        if product is None:
            product = model(
                sku=sku,
                min_stock=0,
                sort_order=0,
                is_active="사용",
                **data,
            )
            db.add(product)
            existing_by_sku[sku] = product
            if data.get("barcode"):
                existing_by_barcode[data["barcode"]] = product
        else:
            for key, value in data.items():
                setattr(product, key, value)
            if data.get("barcode"):
                existing_by_barcode[data["barcode"]] = product
        count += 1
        result_detail = dict(detail)
        result_text = clean_text(result_detail.get("처리 결과"))
        if result_detail.get("처리 유형") == "신규 등록":
            result_text = result_text.replace("등록 예정", "등록 완료")
        elif result_detail.get("처리 유형") == "기존 정보 변경":
            result_text = result_text.replace("업데이트 예정", "업데이트 완료")
        elif result_detail.get("처리 유형") == "변경 없음":
            result_text = "변경 없음"
        result_detail["처리 결과"] = result_text
        result_details.append(result_detail)
    db.commit()
    sync_inventory_from_product_master(db, "3PL")
    summary = dict(preview.get("summary") or {})
    return {
        "ok": True,
        "message": "3PL 마스터 기준정보 일괄 등록/업데이트 완료",
        "count": count,
        "summary": summary,
        "details": result_details,
    }


def bulk_save_product_master(db: Session, source_type: str, rows: list[dict]) -> dict:
    model = product_master_model(source_type)
    normalized, errors = validate_product_master_rows(db, source_type, rows)
    if errors:
        return {"ok": False, "message": "\n".join(errors[:5]), "count": 0}
    count = 0
    for row in normalized:
        product = db.execute(select(model).where(model.sku == row["sku"])).scalar_one_or_none()
        if product is None:
            product = model(**row)
            db.add(product)
        else:
            for key, value in row.items():
                setattr(product, key, value)
        count += 1
    db.commit()
    sync_inventory_from_product_master(db, source_type)
    return {"ok": True, "message": f"{source_type} 상품 마스터 저장 완료", "count": count}


def add_product_master(db: Session, source_type: str, row: dict) -> dict:
    model = product_master_model(source_type)
    normalized, errors = validate_product_master_rows(db, source_type, [row])
    if errors:
        return {"ok": False, "message": "\n".join(errors[:5]), "count": 0}

    data = normalized[0]
    existing_by_sku = db.execute(select(model).where(model.sku == data["sku"])).scalar_one_or_none()
    if existing_by_sku:
        return {"ok": False, "message": f"이미 등록된 SKU입니다. ({data['sku']})", "count": 0}
    if source_type != "3PL":
        existing_by_barcode_product = db.execute(
            select(model).where(model.barcode == data["barcode"], model.product_name == data["product_name"])
        ).scalar_one_or_none()
        if existing_by_barcode_product:
            return {"ok": False, "message": f"이미 등록된 바코드/상품명입니다. ({data['barcode']} / {data['product_name']})", "count": 0}

    db.add(model(**data))
    db.commit()
    sync_inventory_from_product_master(db, source_type)
    return {"ok": True, "message": f"{source_type} 상품 단품 추가 완료", "count": 1}


def import_product_master_excel(db: Session, source_type: str, file_bytes: bytes) -> dict:
    if source_type == "3PL":
        preview = prepare_threepl_master_import_preview(db, file_bytes)
        if preview.get("summary", {}).get("실패 수", 0) and not any(detail.get("_apply") for detail in preview.get("details", [])):
            preview.update({"ok": False, "message": "반영 가능한 3PL 마스터 행이 없습니다.", "count": 0})
            return preview
        return apply_threepl_master_import_preview(db, preview)

    df = read_excel(file_bytes)
    rename_map = {}
    for column in df.columns:
        normalized = str(column).strip().replace(" ", "")
        for target in PRODUCT_MASTER_COLUMNS:
            if normalized == target.replace(" ", ""):
                rename_map[column] = target
    df = df.rename(columns=rename_map)
    rows, warnings = prepare_product_master_import_rows(df, source_type)
    if not rows:
        return {"ok": False, "message": "등록 가능한 상품이 없습니다. SKU/상품코드, 바코드, 상품명 컬럼을 확인해주세요.", "count": 0}
    result = bulk_save_product_master(db, source_type, rows)
    extra_messages = []
    if warnings:
        extra_messages.append(" / ".join(warnings[:5]))
    if result.get("ok", True) and df.attrs.get("read_method") == "html":
        result["used_html"] = True
        extra_messages.append(HTML_TABLE_FALLBACK_MESSAGE)
    if extra_messages:
        result["message"] = f"{result['message']} - {' / '.join(extra_messages)}"
    return result


def sync_inventory_from_product_master(db: Session, source_type: str | None = None) -> int:
    count = 0
    daily_query = select(InventoryDaily)
    inbound_query = select(InventoryInbound)
    if source_type:
        daily_query = daily_query.where(InventoryDaily.source_type == source_type)
        inbound_query = inbound_query.where(InventoryInbound.source_type == source_type)
    for item in db.execute(daily_query).scalars():
        product = find_product_master(db, item.source_type, item.product_code, item.barcode, item.product_name)
        if product:
            apply_product_master_to_daily(item, product)
            count += 1
    for item in db.execute(inbound_query).scalars():
        product = find_product_master(db, item.source_type, item.product_code, item.barcode, item.product_name)
        if product:
            apply_product_master_to_inbound(item, product)
            count += 1

    sync_sources = [source_type] if source_type else list(PRODUCT_MASTER_MODEL_BY_SOURCE.keys())
    for product_source in sync_sources:
        target_date = db.scalar(
            select(func.max(InventoryDaily.work_date)).where(InventoryDaily.source_type == product_source)
        ) or date.today()
        model = product_master_model(product_source)
        products = db.execute(
            select(model).order_by(model.sort_order, model.large_category, model.product_name, model.sku)
        ).scalars()
        for product in products:
            item = ensure_daily_for_product(db, product_source, target_date, product)
            stock_value = int(item.available_stock if item.available_stock is not None else item.current_stock or 0)
            item.stock_status = stock_status_for_values(stock_value, int(product.min_stock or 0))
            count += 1
    db.commit()
    return count


def list_daily(db: Session, source_type: str, work_date: date) -> list[InventoryDaily]:
    return list(
        db.execute(
            select(InventoryDaily)
            .where(InventoryDaily.source_type == source_type, InventoryDaily.work_date == work_date)
            .order_by(InventoryDaily.category, InventoryDaily.product_name, InventoryDaily.barcode)
        ).scalars()
    )


def daily_row_for_product(db: Session, source_type: str, work_date: date, product) -> InventoryDaily | None:
    if product.sku:
        row = db.execute(
            select(InventoryDaily).where(
                InventoryDaily.source_type == source_type,
                InventoryDaily.work_date == work_date,
                InventoryDaily.product_code == product.sku,
            )
        ).scalars().first()
        if row:
            return row
    if product.barcode:
        row = db.execute(
            select(InventoryDaily).where(
                InventoryDaily.source_type == source_type,
                InventoryDaily.work_date == work_date,
                InventoryDaily.barcode == product.barcode,
            )
        ).scalars().first()
        if row:
            return row
    return db.execute(
        select(InventoryDaily).where(
            InventoryDaily.source_type == source_type,
            InventoryDaily.work_date == work_date,
            InventoryDaily.product_name == product.product_name,
        )
    ).scalars().first()


def ensure_daily_for_product(db: Session, source_type: str, work_date: date, product) -> InventoryDaily:
    row = daily_row_for_product(db, source_type, work_date, product)
    if row is None:
        row = InventoryDaily(
            source_type=source_type,
            work_date=work_date,
            product_code=product.sku,
            product_name=product.product_name,
            barcode=product.barcode,
            category=product.large_category,
            supplier=product.supplier,
            safe_stock=product.min_stock,
            inbound_cycle=product.default_lead_time or None,
        )
        db.add(row)
        db.flush()
    apply_product_master_to_daily(row, product)
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
    pallet_match = re.search(r"(?:파렛트당|파렛트|PALLET|PL)\D*(\d+)\s*BOX", text) or re.search(r"/[^/]*(\d+)\s*BOX", text)
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
            InventoryInbound.barcode == product.barcode,
        )
    ).scalar()
    return int(value or 0)


def master_based_inventory_rows(db: Session, source_type: str, work_date: date, active_only: bool = False) -> list[dict]:
    model = product_master_model(source_type)
    query = select(model)
    if active_only:
        query = query.where(model.is_active == "사용")
    products = list(db.execute(query.order_by(model.sort_order, model.large_category, model.product_name, model.sku)).scalars())
    purchase_metrics = purchase_inventory_metrics(db, source_type)
    inbound_metrics = latest_inbound_metrics(db, source_type)
    rows = []
    for product in products:
        daily = daily_row_for_product(db, source_type, work_date, product)
        current_stock = int(daily.current_stock or 0) if daily else 0
        safe_stock = int(product.min_stock or 0)
        status = stock_status_for_values(current_stock, safe_stock)
        purchase_metric = purchase_metrics.get((source_type, product.sku), {})
        inbound_metric = inbound_metrics.get((source_type, product.sku), {})
        measured_lead_time = int(purchase_metric.get("avg_lead_time") or 0)
        box_qty = int(product.box_qty or product.pack_qty or 0)
        pending_inbound_qty = pending_inbound_qty_for_product(db, source_type, work_date, product)
        pending_outbound_qty = int(daily.outbound_qty or 0) if daily else 0
        available_stock = current_stock + pending_inbound_qty - pending_outbound_qty
        shortage_qty = max(safe_stock - current_stock, 0)
        rows.append(
            {
                "source_type": source_type,
                "work_date": work_date,
                "category": product.large_category,
                "product_code": product.sku,
                "product_name": product.product_name,
                "supplier": product.supplier,
                "manager": product.memo,
                "current_stock": current_stock,
                "available_stock": available_stock,
                "safe_stock": safe_stock,
                "pending_inbound_qty": pending_inbound_qty,
                "pending_outbound_qty": pending_outbound_qty,
                "stock_status": status,
                "barcode": product.barcode,
                "inbound_cycle": measured_lead_time or product.default_lead_time or 0,
                "box_qty": box_qty,
                "pack_qty": int(product.pack_qty or 0),
                "box_pallet_unit": format_box_pallet_unit(product.box_qty, product.pack_qty),
                "recommended_boxes": ceil(shortage_qty / box_qty) if box_qty and shortage_qty > 0 else 0,
                "measured_lead_time": measured_lead_time,
                "last_purchase_order_date": purchase_metric.get("last_order_date"),
                "last_purchase_inbound_date": purchase_metric.get("last_inbound_date") or inbound_metric.get("last_inbound_date"),
                "last_po_number": purchase_metric.get("last_po_number", ""),
                "sort_order": product.sort_order or 0,
                "is_active": product.is_active,
            }
        )
    return rows


def read_inventory_upload_file(file_bytes: bytes, file_name: str = "") -> pd.DataFrame:
    if file_name.lower().endswith(".csv"):
        for encoding in ("utf-8-sig", "cp949", "utf-8"):
            try:
                raw_df = pd.read_csv(BytesIO(file_bytes), encoding=encoding)
                df = normalize_import_headers(raw_df)
                df.attrs["read_method"] = "csv"
                df.attrs["selected_sheet"] = f"CSV ({encoding})"
                df.attrs["raw_shape"] = tuple(raw_df.shape)
                df.attrs["raw_columns"] = [str(column) for column in raw_df.columns]
                df.attrs["raw_head"] = raw_df.head(5).fillna("").astype(str).to_dict("records")
                df.attrs["normalized_shape"] = tuple(df.shape)
                df.attrs["normalized_columns"] = [str(column) for column in df.columns]
                df.attrs["normalized_head"] = df.head(5).fillna("").astype(str).to_dict("records")
                return df
            except UnicodeDecodeError:
                continue
        raw_df = pd.read_csv(BytesIO(file_bytes))
        df = normalize_import_headers(raw_df)
        df.attrs["read_method"] = "csv"
        df.attrs["selected_sheet"] = "CSV"
        df.attrs["raw_shape"] = tuple(raw_df.shape)
        df.attrs["raw_columns"] = [str(column) for column in raw_df.columns]
        df.attrs["raw_head"] = raw_df.head(5).fillna("").astype(str).to_dict("records")
        df.attrs["normalized_shape"] = tuple(df.shape)
        df.attrs["normalized_columns"] = [str(column) for column in df.columns]
        df.attrs["normalized_head"] = df.head(5).fillna("").astype(str).to_dict("records")
        return df
    return read_excel(file_bytes)


def to_int_strict(value) -> tuple[int, bool]:
    text = clean_text(value).replace(",", "")
    if not text:
        return 0, False
    try:
        number = int(float(text))
    except ValueError:
        return 0, False
    return number, number >= 0


def prepare_stock_upload_preview(
    db: Session,
    source_type: str,
    work_date: date,
    file_bytes: bytes,
    file_name: str = "",
    upload_mode: str = "partial",
) -> dict:
    df = read_inventory_upload_file(file_bytes, file_name)
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
        "normalized_records": df.fillna("").to_dict("records"),
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
            "empty_barcode_count": 0,
            "invalid_stock_count": 0,
            "negative_stock_count": 0,
            "zeroed_count": 0,
            "preview_rows": [],
            "debug": upload_debug,
            "missing_columns": ["현재고"],
        }
    try:
        current_col = find_column(df, ["보유재고", "현재고", "재고수량", "재고", "기본창고-정상", "정상재고"])
    except ValueError:
        try:
            current_col = find_column(df, ["가용재고", "판매가능재고"])
        except ValueError as exc:
            raise ValueError(f"필수 컬럼을 찾지 못했습니다: 현재고 / 인식된 컬럼: {', '.join(map(str, df.columns))}") from exc
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

    products = list(db.execute(select(product_master_model(source_type))).scalars())
    by_sku = {product.sku: product for product in products if product.sku}
    by_name = {product.product_name: product for product in products if product.product_name}
    by_barcode: dict[str, object] = {}
    duplicate_master_barcodes: set[str] = set()
    for product in products:
        if not product.barcode:
            continue
        if product.barcode in by_barcode:
            duplicate_master_barcodes.add(product.barcode)
        by_barcode[product.barcode] = product

    seen_barcodes: set[str] = set()
    duplicate_barcodes: set[str] = set()
    preview_rows = []
    uploaded_product_keys: set[str] = set()
    matched_count = failed_count = duplicate_count = empty_barcode_count = invalid_stock_count = negative_stock_count = 0

    for index, row in enumerate(df.fillna("").to_dict("records"), start=1):
        barcode = clean_text(row.get(barcode_col)) if barcode_col else ""
        product_code = clean_text(row.get(product_code_col)) if product_code_col else ""
        product_name = clean_text(row.get(name_col)) if name_col else ""
        current_stock, stock_ok = to_int_strict(row.get(current_col))
        errors = []
        if not barcode:
            empty_barcode_count += 1
        elif barcode in seen_barcodes:
            duplicate_barcodes.add(barcode)
            duplicate_count += 1
            errors.append("중복 바코드")
        else:
            seen_barcodes.add(barcode)
        if barcode in duplicate_master_barcodes:
            errors.append("마스터 중복 바코드")
        if not stock_ok:
            if clean_text(row.get(current_col)).replace(",", "").startswith("-"):
                negative_stock_count += 1
                errors.append("음수 재고")
            else:
                invalid_stock_count += 1
                errors.append("숫자가 아닌 재고")

        product = by_barcode.get(barcode) if barcode else None
        if product is None and product_code:
            product = by_sku.get(product_code)
        if product is None and product_name:
            product = by_name.get(product_name)
        if product is None:
            errors.append("매칭 실패")

        previous_stock = 0
        if product:
            daily = daily_row_for_product(db, source_type, work_date, product)
            previous_stock = int(daily.current_stock or 0) if daily else 0

        if errors:
            failed_count += 1
        else:
            matched_count += 1
            uploaded_product_keys.add(product.sku)

        preview_rows.append(
            {
                "row_no": index,
                "product_code": product.sku if product else product_code,
                "category": product.large_category if product else "",
                "product_name": product.product_name if product else product_name,
                "barcode": product.barcode if product else barcode,
                "previous_stock": previous_stock,
                "new_stock": current_stock,
                "status": "정상" if not errors else ", ".join(errors),
                "matched": bool(product) and not errors,
            }
        )

    zero_rows = []
    if upload_mode == "full":
        for product in products:
            if product.sku in uploaded_product_keys:
                continue
            daily = daily_row_for_product(db, source_type, work_date, product)
            previous_stock = int(daily.current_stock or 0) if daily else 0
            if previous_stock == 0:
                continue
            zero_rows.append(
                {
                    "row_no": "",
                    "product_code": product.sku,
                    "category": product.large_category,
                    "product_name": product.product_name,
                    "barcode": product.barcode,
                    "previous_stock": previous_stock,
                    "new_stock": 0,
                    "status": "전체 파일 누락 0 처리",
                    "matched": True,
                    "zero_missing": True,
                }
            )
    preview_rows.extend(zero_rows)
    return {
        "ok": True,
        "file_name": file_name,
        "upload_mode": upload_mode,
        "debug": upload_debug,
        "total_rows": len(df.index),
        "matched_count": matched_count,
        "failed_count": failed_count,
        "duplicate_count": duplicate_count,
        "empty_barcode_count": empty_barcode_count,
        "invalid_stock_count": invalid_stock_count,
        "negative_stock_count": negative_stock_count,
        "zeroed_count": len(zero_rows),
        "preview_rows": preview_rows,
    }


def apply_stock_upload_preview(
    db: Session,
    source_type: str,
    work_date: date,
    preview: dict,
    uploaded_by: str = "",
) -> dict:
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

    count = 0
    for row in rows:
        product = find_product_master(db, source_type, clean_text(row.get("product_code")), clean_text(row.get("barcode")), clean_text(row.get("product_name")))
        if not product:
            continue
        item = ensure_daily_for_product(db, source_type, work_date, product)
        previous_stock = int(item.current_stock or 0)
        new_stock = to_int(row.get("new_stock"))
        db.add(
            InventoryUploadSnapshot(
                upload_history_id=history.id,
                source_type=source_type,
                work_date=work_date,
                product_code=product.sku,
                barcode=product.barcode,
                product_name=product.product_name,
                previous_stock=previous_stock,
                new_stock=new_stock,
            )
        )
        item.current_stock = new_stock
        item.available_stock = new_stock
        item.stock_status = stock_status_for_values(new_stock, product.min_stock or 0)
        count += 1
    db.commit()
    return {"ok": True, "message": "재고 업로드 반영 완료", "count": count, "history_id": history.id}


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
    return list(
        db.execute(
            select(InventoryInbound)
            .where(InventoryInbound.source_type == source_type)
            .order_by(InventoryInbound.inbound_date.desc(), InventoryInbound.id.desc())
        ).scalars()
    )


def list_outbound(db: Session, source_type: str) -> list[InventoryDaily]:
    return list(
        db.execute(
            select(InventoryDaily)
            .where(InventoryDaily.source_type == source_type, InventoryDaily.outbound_qty != 0)
            .order_by(InventoryDaily.work_date.desc(), InventoryDaily.product_name)
        ).scalars()
    )


def create_date(db: Session, source_type: str, work_date: date | None = None) -> dict:
    target_date = work_date or date.today()
    if not is_business_day(target_date):
        return {"ok": False, "message": "주말/공휴일은 생성하지 않습니다.", "count": 0}

    exists = db.scalar(
        select(func.count()).where(InventoryDaily.source_type == source_type, InventoryDaily.work_date == target_date)
    )
    if exists:
        return {"ok": False, "message": "이미 해당 기준일자 데이터가 있습니다.", "count": 0}

    previous_date = db.scalar(
        select(func.max(InventoryDaily.work_date)).where(
            InventoryDaily.source_type == source_type,
            InventoryDaily.work_date < target_date,
        )
    )
    if previous_date is None:
        return {"ok": True, "message": "복사할 직전 기준일자가 없어 빈 날짜로 시작합니다.", "count": 0}

    count = 0
    previous_rows = list_daily(db, source_type, previous_date)
    for row in previous_rows:
        db.add(
            InventoryDaily(
                source_type=source_type,
                work_date=target_date,
                category=row.category,
                product_code=row.product_code,
                product_name=row.product_name,
                barcode=row.barcode,
                supplier=row.supplier,
                current_stock=0,
                available_stock=0,
                safe_stock=row.safe_stock,
                stock_status="",
                outbound_qty=0,
                previous_inbound_date=row.previous_inbound_date,
                last_inbound_date=row.last_inbound_date,
                inbound_qty=0,
                inbound_cycle=row.inbound_cycle,
                memo=row.memo,
            )
        )
        count += 1
    db.commit()
    update_status(db, source_type, target_date)
    return {"ok": True, "message": "오늘 데이터 생성 완료", "count": count}


def row_data(row) -> dict:
    if hasattr(row, "model_dump"):
        return row.model_dump()
    return dict(row)


def bulk_save_daily(db: Session, source_type: str, work_date: date, rows: list[dict]) -> int:
    existing_rows = {
        (row.product_name, row.barcode or ""): daily_to_dict(row)
        for row in list_daily(db, source_type, work_date)
    }
    db.execute(delete(InventoryDaily).where(InventoryDaily.source_type == source_type, InventoryDaily.work_date == work_date))
    seen: set[tuple[str, str]] = set()
    count = 0
    for row in rows:
        data = row_data(row)
        sku = clean_text(data.get("product_code"))
        product = find_product_master(db, source_type, sku, clean_text(data.get("barcode")), clean_text(data.get("product_name")))
        if product:
            data["product_code"] = product.sku
            data["barcode"] = product.barcode
            data["product_name"] = product.product_name
            data["category"] = product.large_category
            data["supplier"] = product.supplier
        product_name = clean_text(data.get("product_name"))
        barcode = clean_text(data.get("barcode"))
        key = (product_name, barcode)
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
    count = 0
    for row in rows:
        data = row_data(row)
        product = find_product_master(db, source_type, clean_text(data.get("product_code")), clean_text(data.get("barcode")), clean_text(data.get("product_name")))
        if product:
            data["product_code"] = product.sku
            data["barcode"] = product.barcode
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
        data["barcode"] = clean_text(data.get("barcode"))
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
        apply_product_master_to_inbound(item, product or find_product_master(db, source_type, item.product_code, item.barcode, item.product_name))
        db.add(item)
        count += 1
    db.commit()
    return count


def get_or_create_daily(db: Session, source_type: str, work_date: date, product_name: str, barcode: str = "") -> InventoryDaily:
    item = db.execute(
        select(InventoryDaily).where(
            InventoryDaily.source_type == source_type,
            InventoryDaily.work_date == work_date,
            InventoryDaily.product_name == product_name,
            InventoryDaily.barcode == barcode,
        )
    ).scalar_one_or_none()
    if item:
        return item
    item = InventoryDaily(source_type=source_type, work_date=work_date, product_name=product_name, barcode=barcode)
    db.add(item)
    db.flush()
    return item


def import_stock(db: Session, source_type: str, work_date: date, file_bytes: bytes) -> dict:
    df = read_excel(file_bytes)
    name_col = find_column(df, ["상품명", "품목"])
    try:
        current_col = find_column(df, ["보유재고", "현재고", "재고수량", "재고", "기본창고-정상", "정상재고"])
    except ValueError:
        current_col = None
    try:
        available_col = find_column(df, ["가용재고", "판매가능재고"])
    except ValueError:
        available_col = current_col
    if current_col is None and available_col is None:
        raise ValueError("필수 컬럼을 찾지 못했습니다: 보유재고, 현재고, 가용재고")
    try:
        product_code_col = find_column(df, ["SKU", "상품코드", "품목코드", "상품번호"])
    except ValueError:
        product_code_col = None
    try:
        barcode_col = find_column(df, ["88바코드", "바코드", "옵션바코드"])
    except ValueError:
        barcode_col = None
    try:
        safe_col = find_column(df, ["안전재고", "최소재고", "경고수량", "위험수량", "safe_stock"])
    except ValueError:
        safe_col = None
    try:
        status_col = find_column(df, ["재고상태", "상태", "stock_status"])
    except ValueError:
        status_col = None
    try:
        lead_time_col = find_column(df, ["리드타임", "리드 타임", "leadtime", "lead_time", "제조기간", "입고주기", "입고 주기"])
    except ValueError:
        lead_time_col = None

    count = 0
    for _, row in df.iterrows():
        product_name = clean_text(row.get(name_col))
        if not product_name:
            continue
        barcode = clean_text(row.get(barcode_col)) if barcode_col else ""
        product_code = clean_text(row.get(product_code_col)) if product_code_col else ""
        item = get_or_create_daily(db, source_type, work_date, product_name, barcode)
        if product_code:
            item.product_code = product_code
        apply_product_master_to_daily(item, find_product_master(db, source_type, item.product_code, item.barcode, item.product_name))
        if current_col:
            item.current_stock = to_int(row.get(current_col))
        elif available_col:
            item.current_stock = to_int(row.get(available_col))
        item.available_stock = to_int(row.get(available_col)) if available_col else item.current_stock
        if safe_col:
            item.safe_stock = to_int(row.get(safe_col))
        if status_col:
            item.stock_status = clean_text(row.get(status_col))
        if lead_time_col:
            item.inbound_cycle = to_int(row.get(lead_time_col)) or None
        count += 1
    db.commit()
    if status_col is None:
        update_status(db, source_type, work_date)
    return import_result(count, df)


def import_order(db: Session, source_type: str, work_date: date, file_bytes: bytes) -> dict:
    df = read_excel(file_bytes)
    name_col = find_column(df, ["상품명", "품목"])
    qty_col = find_column(df, ["상품수량", "수량"])
    cs_col = find_column(df, ["CS"])
    try:
        product_code_col = find_column(df, ["SKU", "상품코드", "품목코드", "상품번호"])
    except ValueError:
        product_code_col = None
    try:
        barcode_col = find_column(df, ["바코드", "옵션바코드"])
    except ValueError:
        barcode_col = None

    sums: dict[tuple[str, str], int] = {}
    product_codes: dict[tuple[str, str], str] = {}
    filtered = df[df[cs_col].astype(str).str.contains("정상", na=False)]
    for _, row in filtered.iterrows():
        product_name = clean_text(row.get(name_col))
        if not product_name:
            continue
        barcode = clean_text(row.get(barcode_col)) if barcode_col else ""
        key = (product_name, barcode)
        sums[key] = sums.get(key, 0) + to_int(row.get(qty_col))
        if product_code_col:
            product_codes[key] = clean_text(row.get(product_code_col))

    for item in list_daily(db, source_type, work_date):
        item.outbound_qty = 0
    for (product_name, barcode), qty in sums.items():
        item = get_or_create_daily(db, source_type, work_date, product_name, barcode)
        item.product_code = product_codes.get((product_name, barcode), item.product_code)
        apply_product_master_to_daily(item, find_product_master(db, source_type, item.product_code, item.barcode, item.product_name))
        item.outbound_qty = qty
    db.commit()
    calculate_safe_stock(db, source_type, work_date)
    update_status(db, source_type, work_date)
    return import_result(len(sums), df)


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
                barcode=clean_text(row.get(barcode_col)) if barcode_col else "",
                inbound_qty=to_int(row.get(qty_col)),
                vendor=clean_text(row.get(vendor_col)) if vendor_col else "",
                inbound_type=clean_text(row.get(type_col)) if type_col else "",
                is_applied=False,
            )
        apply_product_master_to_inbound(item, find_product_master(db, source_type, item.product_code, item.barcode, item.product_name))
        db.add(item)
        count += 1
    db.commit()
    return import_result(count, df)


def apply_inbound_to_stock(db: Session, source_type: str, work_date: date) -> int:
    inbound_rows = list(
        db.execute(
            select(InventoryInbound).where(
                InventoryInbound.source_type == source_type,
                InventoryInbound.inbound_date == work_date,
                InventoryInbound.is_applied == False,  # noqa: E712
            )
        ).scalars()
    )
    count = 0
    for inbound in inbound_rows:
        item = get_or_create_daily(db, source_type, work_date, inbound.product_name, inbound.barcode)
        if not item.category:
            item.category = inbound.category
        if not item.product_code:
            item.product_code = inbound.product_code
        if not item.supplier:
            item.supplier = inbound.vendor
        apply_product_master_to_daily(item, find_product_master(db, source_type, item.product_code, item.barcode, item.product_name))
        item.inbound_qty += inbound.inbound_qty
        item.current_stock += inbound.inbound_qty
        item.available_stock += inbound.inbound_qty
        if item.last_inbound_date and item.last_inbound_date != inbound.inbound_date:
            item.previous_inbound_date = item.last_inbound_date
        item.last_inbound_date = inbound.inbound_date
        inbound.is_applied = True
        count += 1
    db.commit()
    update_status(db, source_type, work_date)
    calculate_inbound_cycle(db, source_type)
    return count


def week_range(target: date, weeks_ago: int) -> tuple[date, date]:
    current_monday = target - timedelta(days=target.weekday())
    start = current_monday - timedelta(days=7 * weeks_ago)
    return start, start + timedelta(days=6)


def outbound_sum_by_product(db: Session, source_type: str, start: date, end: date) -> dict[tuple[str, str], int]:
    rows = db.execute(
        select(InventoryDaily.product_name, InventoryDaily.barcode, func.sum(InventoryDaily.outbound_qty))
        .where(
            InventoryDaily.source_type == source_type,
            InventoryDaily.work_date >= start,
            InventoryDaily.work_date <= end,
        )
        .group_by(InventoryDaily.product_name, InventoryDaily.barcode)
    ).all()
    return {(name, barcode or ""): int(total or 0) for name, barcode, total in rows}


def calculate_safe_stock(db: Session, source_type: str, work_date: date) -> int:
    last_start, last_end = week_range(work_date, 1)
    prev_start, prev_end = week_range(work_date, 2)
    last_map = outbound_sum_by_product(db, source_type, last_start, last_end)
    prev_map = outbound_sum_by_product(db, source_type, prev_start, prev_end)
    count = 0
    for item in list_daily(db, source_type, work_date):
        key = (item.product_name, item.barcode or "")
        base = max(last_map.get(key, 0), prev_map.get(key, 0))
        safe_stock = ceil(base * 6 / 5)
        item.safe_stock = safe_stock
        product = find_product_master(db, source_type, item.product_code, item.barcode, item.product_name)
        if product:
            product.min_stock = safe_stock
        count += 1
    db.commit()
    return count


def update_status(db: Session, source_type: str, work_date: date) -> int:
    count = 0
    for item in list_daily(db, source_type, work_date):
        product = find_product_master(db, source_type, item.product_code, item.barcode, item.product_name)
        safe_stock = int(product.min_stock or 0) if product else int(item.safe_stock or 0)
        stock_value = int(item.available_stock if item.available_stock is not None else item.current_stock or 0)
        item.stock_status = stock_status_for_values(stock_value, safe_stock)
        count += 1
    db.commit()
    return count


def calculate_inbound_cycle(db: Session, source_type: str) -> int:
    keys = db.execute(
        select(InventoryDaily.product_name, InventoryDaily.barcode)
        .where(InventoryDaily.source_type == source_type)
        .distinct()
    ).all()
    count = 0
    for product_name, barcode in keys:
        dates = sorted(
            {
                row.last_inbound_date
                for row in db.execute(
                    select(InventoryDaily).where(
                        InventoryDaily.source_type == source_type,
                        InventoryDaily.product_name == product_name,
                        InventoryDaily.barcode == (barcode or ""),
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
                InventoryDaily.barcode == (barcode or ""),
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
        "current_stock": row.current_stock,
        "available_stock": row.available_stock,
        "safe_stock": row.safe_stock,
        "stock_status": row.stock_status,
        "outbound_qty": row.outbound_qty,
        "previous_inbound_date": row.previous_inbound_date,
        "last_inbound_date": row.last_inbound_date,
        "inbound_qty": row.inbound_qty,
        "inbound_cycle": row.inbound_cycle,
        "memo": row.memo,
        "updated_at": row.updated_at,
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
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def excel_bytes(df: pd.DataFrame, sheet_name: str) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name[:31])
    return output.getvalue()


def dashboard_summary(db: Session, work_date: date, source_type: str | None = None) -> dict:
    query = select(InventoryDaily).where(InventoryDaily.work_date == work_date)
    if source_type and source_type != "전체":
        query = query.where(InventoryDaily.source_type == source_type)
    rows = list(db.execute(query).scalars())
    return {
        "sku_count": len(rows),
        "current_stock": sum(row.current_stock for row in rows),
        "available_stock": sum(row.available_stock for row in rows),
        "need_inbound_count": sum(1 for row in rows if row.stock_status in {"부족", "주의", "입고필요"}),
        "soldout_count": sum(1 for row in rows if row.stock_status == "품절"),
        "short_count": sum(1 for row in rows if row.stock_status in {"부족", "품절", "미출"}),
        "outbound_qty": sum(row.outbound_qty for row in rows),
        "inbound_qty": sum(row.inbound_qty for row in rows),
    }


def dashboard_chart(db: Session, work_date: date, source_type: str | None = None) -> dict:
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
        rows = list(db.execute(select(InventoryDaily).where(*base_filters)).scalars())
        grouped_values: dict[str, int] = {}
        for row in rows:
            product = find_product_master(db, row.source_type, row.product_code, row.barcode, row.product_name)
            label = (product.large_category if product else row.category) or row.category or "미분류"
            grouped_values[label] = grouped_values.get(label, 0) + int(getattr(row, value_attr) or 0)
        return [{"label": label, "value": value} for label, value in sorted(grouped_values.items())]

    def trend(value_column):
        rows = db.execute(
            select(InventoryDaily.work_date, func.sum(value_column))
            .where(*trend_filters)
            .group_by(InventoryDaily.work_date)
            .order_by(InventoryDaily.work_date)
        ).all()
        return [{"date": str(day), "value": int(value or 0)} for day, value in rows]

    top_rows = db.execute(
        select(InventoryDaily.product_name, InventoryDaily.current_stock, InventoryDaily.safe_stock)
        .where(*base_filters, InventoryDaily.stock_status.in_(["부족", "주의", "입고필요"]))
        .order_by((InventoryDaily.safe_stock - InventoryDaily.current_stock).desc())
        .limit(10)
    ).all()

    return {
        "stock_by_source": grouped(InventoryDaily.source_type, InventoryDaily.current_stock, [InventoryDaily.work_date == work_date]),
        "stock_by_category": grouped_by_master_category("current_stock"),
        "outbound_by_category": grouped_by_master_category("outbound_qty"),
        "stock_trend": trend(InventoryDaily.current_stock),
        "outbound_trend": trend(InventoryDaily.outbound_qty),
        "need_inbound_top10": [
            {"product_name": name, "current_stock": int(current or 0), "safe_stock": int(safe or 0)}
            for name, current, safe in top_rows
        ],
    }
