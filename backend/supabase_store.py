from __future__ import annotations

from datetime import date, datetime
import hashlib
import re
from types import SimpleNamespace
from typing import Any

from lib.supabase_client import get_supabase_client, supabase_status


SOURCE_TABLE = "inventory_items"
LAYOUT_TABLE = "warehouse_layouts"
TRANSACTION_TYPES = {"IN", "OUT", "INITIAL", "ADJUST_PLUS", "ADJUST_MINUS"}


def is_enabled() -> bool:
    return bool(supabase_status().connected)


def client():
    supabase, status = get_supabase_client()
    if supabase is None:
        raise RuntimeError(status.message)
    return supabase


def clean(value) -> str:
    return "" if value is None else str(value).strip()


def to_int(value) -> int:
    text = clean(value).replace(",", "")
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def parse_box_pallet_unit(value) -> tuple[int, int]:
    text = clean(value).upper().replace(",", "")
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


def format_box_pallet_unit(box_qty: int | None, pallet_qty: int | None) -> str:
    parts = []
    if int(box_qty or 0):
        parts.append(f"박스당 {int(box_qty or 0)}EA")
    if int(pallet_qty or 0):
        parts.append(f"파렛트당 {int(pallet_qty or 0)}BOX")
    return " / ".join(parts)


def iso(value) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def table_count(table_name: str) -> int | None:
    count, _ = table_count_probe(table_name)
    return count


def table_count_probe(table_name: str) -> tuple[int | None, str]:
    try:
        result = client().table(table_name).select("id", count="exact").limit(1).execute()
        return result.count, ""
    except Exception as exc:
        return None, str(exc)


def table_exists(table_name: str) -> bool:
    return table_count(table_name) is not None


def admin_status() -> dict:
    status = supabase_status()
    tables = [
        "suppliers",
        "inventory_items",
        "warehouse_locations",
        "warehouse_layouts",
        "inventory_transactions",
        "stock_counts",
        "audit_logs",
        "import_batches",
    ]
    counts = {table: None for table in tables}
    table_errors = {}
    if status.connected:
        for table in tables:
            count, error = table_count_probe(table)
            counts[table] = count
            if error:
                table_errors[table] = error
    recent = []
    recent_error = ""
    if status.connected:
        try:
            recent = (
                client()
                .table("inventory_transactions")
                .select("transaction_date,transaction_type,quantity,item_id,source_type,note")
                .order("created_at", desc=True)
                .limit(10)
                .execute()
                .data
                or []
            )
        except Exception as exc:
            recent = []
            recent_error = str(exc)
    return {
        "configured": status.configured,
        "connected": status.connected,
        "message": status.message,
        "source": status.source,
        "counts": counts,
        "table_errors": table_errors,
        "recent_transactions": recent,
        "recent_error": recent_error,
    }


def load_warehouse_layout_store() -> dict:
    rows = (
        client()
        .table(LAYOUT_TABLE)
        .select("building,floor,layout_data")
        .eq("is_active", True)
        .execute()
        .data
        or []
    )
    locations: dict[str, dict] = {}
    for row in rows:
        building = clean(row.get("building"))
        floor = clean(row.get("floor"))
        layout_data = row.get("layout_data") or {}
        if not building or not floor or not isinstance(layout_data, dict):
            continue
        locations.setdefault(building, {})[floor] = layout_data
    return {"version": 1, "locations": locations}


def save_warehouse_layout_store(payload: dict) -> int:
    locations = payload.get("locations") if isinstance(payload, dict) else None
    if not isinstance(locations, dict):
        return 0

    rows = []
    for building, floors in locations.items():
        if not isinstance(floors, dict):
            continue
        for floor, floor_data in floors.items():
            if not isinstance(floor_data, dict):
                continue
            if not (floor_data.get("racks") or floor_data.get("fixtures") or floor_data.get("floor_size")):
                continue
            rows.append(
                {
                    "building": clean(building),
                    "floor": clean(floor),
                    "layout_data": floor_data,
                    "is_active": True,
                }
            )
    if not rows:
        return 0
    client().table(LAYOUT_TABLE).upsert(rows, on_conflict="building,floor").execute()
    log_audit("warehouse_layout_upsert", "창고", {"count": len(rows)})
    return len(rows)


def namespace(row: dict) -> SimpleNamespace:
    return SimpleNamespace(**row)


def product_to_row(item: dict) -> dict:
    metadata = item.get("metadata") or {}
    box_qty = to_int(item.get("box_qty"))
    pack_qty = to_int(item.get("pack_qty"))
    return {
        "id": item.get("id"),
        "sku": clean(item.get("sku")),
        "barcode": clean(item.get("barcode")),
        "product_name": clean(item.get("product_name")),
        "category": clean(item.get("category")),
        "large_category": clean(item.get("category")),
        "medium_category": clean(item.get("medium_category")),
        "small_category": clean(item.get("small_category")),
        "brand": clean(item.get("brand")),
        "supplier": clean(item.get("supplier_name")),
        "pack_qty": pack_qty,
        "box_qty": box_qty,
        "box_pallet_unit": clean(metadata.get("box_pallet_unit")) or format_box_pallet_unit(box_qty, pack_qty),
        "default_lead_time": to_int(item.get("default_lead_time")),
        "min_stock": to_int(item.get("min_stock")),
        "sort_order": to_int(item.get("sort_order")),
        "is_active": "사용" if item.get("is_active", True) else "미사용",
        "memo": clean(item.get("memo") or metadata.get("memo") or metadata.get("manager")),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


def list_product_master(source_type: str, keyword: str = "", active_filter: str = "전체") -> list[SimpleNamespace]:
    query = client().table(SOURCE_TABLE).select("*").eq("source_type", source_type)
    if active_filter != "전체":
        query = query.eq("is_active", active_filter == "사용")
    rows = query.order("sort_order").order("category").order("product_name").execute().data or []
    keyword = clean(keyword).lower()
    if keyword:
        rows = [
            row
            for row in rows
            if keyword
            in " ".join(
                clean(row.get(field)).lower()
                for field in ("sku", "barcode", "product_name", "category", "brand", "supplier_name")
            )
        ]
    return [namespace(product_to_row(row)) for row in rows]


def active_product_options(source_type: str) -> list[dict]:
    return [vars(row) for row in list_product_master(source_type, "", "사용")]


def auto_sku(row: dict, used_skus: set[str], default_prefix: str) -> str:
    sku = clean(row.get("sku"))
    if sku:
        used_skus.add(sku)
        return sku
    barcode = clean(row.get("barcode"))
    product_name = clean(row.get("product_name"))
    base = barcode or product_name or default_prefix
    if base not in used_skus:
        used_skus.add(base)
        return base
    suffix = hashlib.sha1(f"{barcode}|{product_name}".encode("utf-8")).hexdigest()[:8]
    candidate = f"{base}-{suffix}"
    sequence = 2
    while candidate in used_skus:
        candidate = f"{base}-{suffix}-{sequence}"
        sequence += 1
    used_skus.add(candidate)
    return candidate


def supplier_id_for_name(name: str) -> str | None:
    name = clean(name)
    if not name:
        return None
    sb = client()
    existing = sb.table("suppliers").select("id").eq("name", name).limit(1).execute().data or []
    if existing:
        return existing[0]["id"]
    payload = {"name": name, "supplier_code": name}
    inserted = sb.table("suppliers").upsert(payload, on_conflict="name").execute().data or []
    return inserted[0]["id"] if inserted else None


def first_clean(row: dict, *keys: str) -> str:
    for key in keys:
        value = clean(row.get(key))
        if value:
            return value
    return ""


def normalize_product_payload(source_type: str, row: dict, used_skus: set[str]) -> dict:
    supplier_name = clean(row.get("supplier") or row.get("공급처") or row.get("업체명"))
    sku = auto_sku(row, used_skus, source_type)
    box_unit, pallet_unit = parse_box_pallet_unit(
        row.get("box_pallet_unit")
        or row.get("박스/파렛트 단위")
        or row.get("박스파렛트단위")
        or row.get("파렛트,박스단위")
    )
    pack_qty = pallet_unit or to_int(row.get("pack_qty") or row.get("입수"))
    box_qty = box_unit or to_int(row.get("box_qty") or row.get("박스입수"))
    memo = clean(row.get("memo") or row.get("비고") or row.get("담당자") or row.get("manager"))
    return {
        "source_type": source_type,
        "sku": sku,
        "barcode": clean(row.get("barcode") or row.get("바코드") or row.get("88바코드") or sku),
        "product_name": clean(row.get("product_name") or row.get("상품명")),
        "category": first_clean(
            row,
            "large_category",
            "category",
            "Category",
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
        ),
        "medium_category": first_clean(row, "medium_category", "중분류", "중분류명", "중 카테고리", "중카테고리"),
        "small_category": first_clean(row, "small_category", "소분류", "소분류명", "소 카테고리", "소카테고리"),
        "brand": clean(row.get("brand") or row.get("브랜드")),
        "supplier_id": supplier_id_for_name(supplier_name),
        "supplier_name": supplier_name,
        "pack_qty": pack_qty,
        "box_qty": box_qty,
        "default_lead_time": to_int(row.get("default_lead_time") or row.get("기본 리드타임") or row.get("리드타임")),
        "min_stock": to_int(row.get("min_stock") or row.get("최소재고") or row.get("안전재고")),
        "sort_order": to_int(row.get("sort_order") or row.get("정렬순서")),
        "is_active": clean(row.get("is_active") or row.get("사용여부") or "사용") != "미사용",
        "memo": memo,
    }


def keep_existing_product_payload(existing_row: dict | None, normalized: dict) -> dict:
    if not existing_row:
        return normalized
    next_payload = dict(normalized)
    for target, source in (
        ("category", "category"),
        ("medium_category", "medium_category"),
        ("small_category", "small_category"),
        ("memo", "memo"),
    ):
        if not clean(next_payload.get(target)) and clean(existing_row.get(source)):
            next_payload[target] = clean(existing_row.get(source))
    for target in ("pack_qty", "box_qty"):
        if to_int(next_payload.get(target)) <= 0 and to_int(existing_row.get(target)) > 0:
            next_payload[target] = to_int(existing_row.get(target))
    return next_payload


def bulk_save_product_master(source_type: str, rows: list[dict], chunk_size: int = 300, replace_existing: bool = False) -> dict:
    sb = client()
    existing = (
        sb.table(SOURCE_TABLE)
        .select("sku,barcode,product_name,category,medium_category,small_category,pack_qty,box_qty,memo")
        .eq("source_type", source_type)
        .execute()
        .data
        or []
    )
    used_skus = set() if replace_existing else {clean(row.get("sku")) for row in existing if clean(row.get("sku"))}
    existing_by_sku = {} if replace_existing else {clean(row.get("sku")): row for row in existing if clean(row.get("sku"))}
    existing_by_barcode_name = (
        {}
        if replace_existing
        else {
            (clean(row.get("barcode")), clean(row.get("product_name"))): row
            for row in existing
        }
    )
    payloads = []
    errors = []
    for index, row in enumerate(rows, start=1):
        normalized = normalize_product_payload(source_type, row, used_skus)
        existing_row = existing_by_barcode_name.get((normalized["barcode"], normalized["product_name"]))
        if existing_row:
            normalized["sku"] = clean(existing_row.get("sku")) or normalized["sku"]
        existing_row = existing_row or existing_by_sku.get(normalized["sku"])
        normalized = keep_existing_product_payload(existing_row, normalized)
        if not normalized["product_name"] or not normalized["sku"]:
            errors.append(f"{index}행: SKU/상품명 생성 실패")
            continue
        payloads.append(normalized)
    if errors:
        return {"ok": False, "message": "\n".join(errors[:5]), "count": 0}
    if payloads:
        deduped_payloads: dict[tuple[str, str], dict] = {}
        for payload in payloads:
            deduped_payloads[(clean(payload.get("source_type")), clean(payload.get("sku")))] = payload
        payloads = list(deduped_payloads.values())
    if payloads:
        safe_chunk_size = max(1, int(chunk_size or 300))
        if replace_existing:
            sb.table(SOURCE_TABLE).delete().eq("source_type", source_type).execute()
        for start in range(0, len(payloads), safe_chunk_size):
            chunk = payloads[start : start + safe_chunk_size]
            sb.table(SOURCE_TABLE).upsert(chunk, on_conflict="source_type,sku").execute()
    action = "product_master_replace" if replace_existing else "product_master_upsert"
    log_audit(action, source_type, {"count": len(payloads)})
    message = f"{source_type} 상품 마스터 Supabase 전체 교체 완료" if replace_existing else f"{source_type} 상품 마스터 Supabase 저장 완료"
    return {"ok": True, "message": message, "count": len(payloads)}


def find_product(source_type: str, sku: str = "", barcode: str = "", product_name: str = "") -> dict | None:
    sku = clean(sku)
    barcode = clean(barcode)
    product_name = clean(product_name)
    sb = client()
    if sku:
        rows = sb.table(SOURCE_TABLE).select("*").eq("source_type", source_type).eq("sku", sku).limit(1).execute().data or []
        if rows:
            return rows[0]
    if barcode and product_name:
        rows = (
            sb.table(SOURCE_TABLE)
            .select("*")
            .eq("source_type", source_type)
            .eq("barcode", barcode)
            .eq("product_name", product_name)
            .limit(1)
            .execute()
            .data
            or []
        )
        if rows:
            return rows[0]
    return None


def stock_summary_map(source_type: str | None = None) -> dict[str, dict]:
    query = client().table("inventory_stock_summary").select("*")
    if source_type:
        query = query.eq("source_type", source_type)
    rows = query.execute().data or []
    return {row["item_id"]: row for row in rows}


def list_work_dates(source_type: str | None = None) -> list[date]:
    query = client().table("inventory_transactions").select("transaction_date").order("transaction_date", desc=True)
    if source_type:
        query = query.eq("source_type", source_type)
    rows = query.limit(1000).execute().data or []
    dates = sorted({date.fromisoformat(row["transaction_date"]) for row in rows if row.get("transaction_date")}, reverse=True)
    return dates or [date.today()]


def daily_rows(source_type: str, work_date: date | None = None) -> list[dict]:
    items = list_product_master(source_type, "", "전체")
    summary = stock_summary_map(source_type)
    rows = []
    for item in items:
        data = vars(item)
        stock = summary.get(data["id"], {})
        current = to_int(stock.get("current_stock"))
        available = current
        safe = to_int(data.get("min_stock"))
        status = "품절" if available <= 0 else "부족" if safe and available <= safe else "주의" if safe and available <= safe + max(1, int(safe * 0.2)) else "정상"
        rows.append(
            {
                "id": data["id"],
                "source_type": source_type,
                "work_date": work_date or date.today(),
                "category": data.get("large_category", ""),
                "product_code": data.get("sku", ""),
                "product_name": data.get("product_name", ""),
                "barcode": data.get("barcode", ""),
                "supplier": data.get("supplier", ""),
                "current_stock": current,
                "available_stock": available,
                "safe_stock": safe,
                "stock_status": status,
                "outbound_qty": 0,
                "previous_inbound_date": None,
                "last_inbound_date": stock.get("last_transaction_date"),
                "inbound_qty": 0,
                "inbound_cycle": data.get("default_lead_time"),
                "memo": data.get("memo", ""),
            }
        )
    return rows


def list_daily(source_type: str, work_date: date | None = None) -> list[SimpleNamespace]:
    return [namespace(row) for row in daily_rows(source_type, work_date)]


def master_based_inventory_rows(source_type: str, work_date: date | None = None) -> list[dict]:
    rows = daily_rows(source_type, work_date)
    for row in rows:
        row["manager"] = row.get("memo", "")
        row["pending_inbound_qty"] = 0
        row["pending_outbound_qty"] = 0
        row["box_qty"] = 0
        row["pack_qty"] = 0
        row["box_pallet_unit"] = ""
        row["recommended_boxes"] = 0
        row["measured_lead_time"] = 0
        row["last_purchase_order_date"] = None
        row["last_purchase_inbound_date"] = row.get("last_inbound_date")
        row["last_po_number"] = ""
        row["sort_order"] = 0
        row["is_active"] = "사용"
    return rows


def create_import_batch(batch_type: str, source_type: str, file_name: str = "", total_rows: int = 0, preview: dict | None = None) -> str | None:
    payload = {
        "batch_type": batch_type,
        "source_type": source_type,
        "file_name": clean(file_name),
        "total_rows": int(total_rows or 0),
        "status": "APPLIED",
        "preview_json": preview or {},
    }
    rows = client().table("import_batches").insert(payload).execute().data or []
    return rows[0]["id"] if rows else None


def transaction_source_key(source_type: str, item_id: str, tx_date: date, tx_type: str, reference: str = "") -> str:
    return hashlib.sha1(f"{source_type}|{item_id}|{tx_date}|{tx_type}|{reference}".encode("utf-8")).hexdigest()


def add_transaction(
    *,
    source_type: str,
    item_id: str,
    transaction_type: str,
    quantity: int,
    transaction_date: date,
    reference: str = "",
    note: str = "",
    import_batch_id: str | None = None,
) -> None:
    if transaction_type not in TRANSACTION_TYPES:
        raise ValueError(f"허용되지 않은 거래 유형입니다: {transaction_type}")
    payload = {
        "source_type": source_type,
        "item_id": item_id,
        "transaction_type": transaction_type,
        "quantity": abs(int(quantity or 0)),
        "transaction_date": iso(transaction_date),
        "reference_no": clean(reference),
        "note": clean(note),
        "import_batch_id": import_batch_id,
        "source_key": transaction_source_key(source_type, item_id, transaction_date, transaction_type, reference or note),
    }
    client().table("inventory_transactions").upsert(payload, on_conflict="source_key").execute()


def apply_stock_rows(source_type: str, work_date: date, preview: dict) -> dict:
    rows = [row for row in preview.get("preview_rows", []) if row.get("matched")]
    batch_id = create_import_batch("stock_upload", source_type, preview.get("file_name", ""), preview.get("total_rows", 0), preview)
    summary = stock_summary_map(source_type)
    count = 0
    for row in rows:
        product = find_product(source_type, row.get("product_code"), row.get("barcode"), row.get("product_name"))
        if not product:
            continue
        current = to_int(summary.get(product["id"], {}).get("current_stock"))
        target = to_int(row.get("new_stock"))
        delta = target - current
        if delta == 0:
            continue
        tx_type = "ADJUST_PLUS" if delta > 0 else "ADJUST_MINUS"
        add_transaction(
            source_type=source_type,
            item_id=product["id"],
            transaction_type=tx_type,
            quantity=abs(delta),
            transaction_date=work_date,
            reference=f"stock-upload:{batch_id or ''}:{row.get('row_no', '')}",
            note="재고 업로드 조정",
            import_batch_id=batch_id,
        )
        count += 1
    log_audit("stock_upload_apply", source_type, {"count": count, "batch_id": batch_id})
    return {"ok": True, "message": "Supabase 재고 업로드 반영 완료", "count": count, "history_id": batch_id}


def list_inbound(source_type: str) -> list[SimpleNamespace]:
    rows = (
        client()
        .table("inventory_transactions")
        .select("*,inventory_items(sku,barcode,product_name,category,supplier_name)")
        .eq("source_type", source_type)
        .eq("transaction_type", "IN")
        .order("transaction_date", desc=True)
        .limit(1000)
        .execute()
        .data
        or []
    )
    result = []
    for row in rows:
        item = row.get("inventory_items") or {}
        result.append(
            namespace(
                {
                    "id": row.get("id"),
                    "source_type": source_type,
                    "inbound_date": row.get("transaction_date"),
                    "category": item.get("category", ""),
                    "product_code": item.get("sku", ""),
                    "product_name": item.get("product_name", ""),
                    "barcode": item.get("barcode", ""),
                    "inbound_qty": row.get("quantity", 0),
                    "vendor": item.get("supplier_name", ""),
                    "inbound_type": row.get("reference_no", ""),
                    "memo": row.get("note", ""),
                    "is_applied": True,
                }
            )
        )
    return result


def outbound_date_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return clean(value)[:10]


def outbound_row_matches_keyword(row: dict, keyword: str | None) -> bool:
    needle = clean(keyword).lower()
    if not needle:
        return True
    item = row.get("inventory_items") or {}
    haystack = " ".join(
        clean(value)
        for value in (
            item.get("sku"),
            item.get("barcode"),
            item.get("product_name"),
        )
    ).lower()
    return needle in haystack


def outbound_transaction_query(source_type: str, start_date=None, end_date=None):
    query = (
        client()
        .table("inventory_transactions")
        .select("*,inventory_items(sku,barcode,product_name,category,supplier_name,min_stock,default_lead_time)")
        .eq("source_type", source_type)
        .eq("transaction_type", "OUT")
    )
    start_text = outbound_date_text(start_date)
    end_text = outbound_date_text(end_date)
    if start_text:
        query = query.gte("transaction_date", start_text)
    if end_text:
        query = query.lte("transaction_date", end_text)
    return query.order("transaction_date", desc=True)


def count_outbound(source_type: str, start_date=None, end_date=None, keyword: str | None = None) -> int:
    response = outbound_transaction_query(source_type, start_date, end_date).limit(10000).execute()
    rows = response.data or []
    return sum(1 for row in rows if outbound_row_matches_keyword(row, keyword))


def list_outbound(
    source_type: str,
    start_date=None,
    end_date=None,
    keyword: str | None = None,
    limit: int | None = 1000,
    offset: int = 0,
) -> list[SimpleNamespace]:
    fetch_limit = 10000 if clean(keyword) or limit is None else max(int(offset or 0) + int(limit or 0), int(limit or 0))
    rows = outbound_transaction_query(source_type, start_date, end_date).limit(max(int(fetch_limit or 0), 0)).execute().data or []
    rows = [row for row in rows if outbound_row_matches_keyword(row, keyword)]
    if offset or limit is not None:
        start_index = max(int(offset or 0), 0)
        end_index = start_index + max(int(limit or 0), 0) if limit is not None else None
        rows = rows[start_index:end_index]
    result = []
    for row in rows:
        item = row.get("inventory_items") or {}
        result.append(
            namespace(
                {
                    "id": row.get("id"),
                    "source_type": source_type,
                    "work_date": row.get("transaction_date"),
                    "category": item.get("category", ""),
                    "product_code": item.get("sku", ""),
                    "product_name": item.get("product_name", ""),
                    "barcode": item.get("barcode", ""),
                    "supplier": item.get("supplier_name", ""),
                    "current_stock": 0,
                    "available_stock": 0,
                    "safe_stock": item.get("min_stock", 0),
                    "stock_status": "",
                    "outbound_qty": row.get("quantity", 0),
                    "previous_inbound_date": None,
                    "last_inbound_date": None,
                    "inbound_qty": 0,
                    "inbound_cycle": item.get("default_lead_time"),
                    "memo": row.get("note", ""),
                }
            )
        )
    return result


def bulk_save_inbound(source_type: str, rows: list[dict]) -> int:
    batch_id = create_import_batch("inbound_save", source_type, "", len(rows), {})
    count = 0
    for row in rows:
        product = find_product(source_type, row.get("product_code"), row.get("barcode"), row.get("product_name"))
        if not product:
            continue
        qty = to_int(row.get("inbound_qty"))
        if qty <= 0:
            continue
        add_transaction(
            source_type=source_type,
            item_id=product["id"],
            transaction_type="IN",
            quantity=qty,
            transaction_date=row.get("inbound_date") or date.today(),
            reference=clean(row.get("inbound_type")),
            note=clean(row.get("memo")),
            import_batch_id=batch_id,
        )
        count += 1
    log_audit("inbound_save", source_type, {"count": count, "batch_id": batch_id})
    return count


def apply_outbound(source_type: str, work_date: date, product_code: str, barcode: str, product_name: str, quantity: int, note: str = "") -> dict:
    product = find_product(source_type, product_code, barcode, product_name)
    if not product:
        return {"ok": False, "message": "출고할 품목을 찾지 못했습니다.", "count": 0}
    current = to_int(stock_summary_map(source_type).get(product["id"], {}).get("current_stock"))
    qty = to_int(quantity)
    if qty <= 0:
        return {"ok": False, "message": "출고수량은 1 이상이어야 합니다.", "count": 0}
    if qty > current:
        return {"ok": False, "message": f"현재고({current:,})보다 많은 수량은 출고할 수 없습니다.", "count": 0}
    add_transaction(
        source_type=source_type,
        item_id=product["id"],
        transaction_type="OUT",
        quantity=qty,
        transaction_date=work_date,
        reference="manual-outbound",
        note=note,
    )
    log_audit("outbound", source_type, {"item_id": product["id"], "quantity": qty})
    return {"ok": True, "message": "Supabase 출고 반영 완료", "count": 1}


def log_audit(action: str, source_type: str = "", details: dict | None = None) -> None:
    try:
        client().table("audit_logs").insert(
            {
                "action": action,
                "source_type": source_type,
                "details": details or {},
            }
        ).execute()
    except Exception:
        pass
