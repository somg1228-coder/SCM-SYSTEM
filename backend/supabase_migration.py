from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from backend import supabase_store
from backend.models import InventoryDaily, InventoryInbound
from backend.services import (
    PRODUCT_MASTER_MODEL_BY_SOURCE,
    daily_to_dict,
    inbound_to_dict,
    normalize_product_master_row,
    product_master_to_dict,
)


BASE_DIR = Path(__file__).resolve().parents[1]


def discover_local_storage() -> list[dict]:
    patterns = ["*.csv", "*.xlsx", "*.xls", "*.json", "*.pkl", "*.pickle", "*.db"]
    rows = []
    for pattern in patterns:
        for path in BASE_DIR.rglob(pattern):
            if ".git" in path.parts or "__pycache__" in path.parts:
                continue
            kind = path.suffix.lower().lstrip(".")
            operational = path.parts[-2:] in [("data", path.name)] or "data" in path.parts
            rows.append(
                {
                    "path": str(path.relative_to(BASE_DIR)),
                    "kind": kind,
                    "size": path.stat().st_size,
                    "last_modified": path.stat().st_mtime,
                    "operational_candidate": operational,
                }
            )
    return sorted(rows, key=lambda row: (row["kind"], row["path"]))


def build_migration_preview(db) -> dict:
    source_counts = {}
    latest_daily_counts = {}
    inbound_counts = {}
    for source_type, model in PRODUCT_MASTER_MODEL_BY_SOURCE.items():
        source_counts[source_type] = int(db.scalar(select(func.count()).select_from(model)) or 0)
        latest_date = db.scalar(select(func.max(InventoryDaily.work_date)).where(InventoryDaily.source_type == source_type))
        latest_daily_counts[source_type] = {
            "latest_date": latest_date,
            "rows": int(
                db.scalar(
                    select(func.count()).where(
                        InventoryDaily.source_type == source_type,
                        InventoryDaily.work_date == latest_date,
                    )
                )
                or 0
            )
            if latest_date
            else 0,
        }
        inbound_counts[source_type] = int(db.scalar(select(func.count()).where(InventoryInbound.source_type == source_type)) or 0)
    storage_rows = discover_local_storage()
    return {
        "ok": True,
        "message": "Supabase 이관 미리보기 생성 완료",
        "product_master_counts": source_counts,
        "latest_daily_counts": latest_daily_counts,
        "inbound_counts": inbound_counts,
        "storage_rows": storage_rows,
    }


def migrate_product_masters(db) -> int:
    count = 0
    for source_type, model in PRODUCT_MASTER_MODEL_BY_SOURCE.items():
        rows = [normalize_product_master_row(product_master_to_dict(row)) for row in db.execute(select(model)).scalars()]
        if rows:
            result = supabase_store.bulk_save_product_master(source_type, rows)
            count += int(result.get("count") or 0)
    return count


def migrate_latest_stock(db) -> int:
    count = 0
    for source_type in PRODUCT_MASTER_MODEL_BY_SOURCE:
        latest_date = db.scalar(select(func.max(InventoryDaily.work_date)).where(InventoryDaily.source_type == source_type))
        if latest_date is None:
            continue
        rows = db.execute(
            select(InventoryDaily).where(
                InventoryDaily.source_type == source_type,
                InventoryDaily.work_date == latest_date,
            )
        ).scalars()
        batch_id = supabase_store.create_import_batch("sqlite_latest_stock", source_type, "data/scm.db", 0, {"latest_date": str(latest_date)})
        summary = supabase_store.stock_summary_map(source_type)
        for daily in rows:
            payload = daily_to_dict(daily)
            product = supabase_store.find_product(source_type, payload.get("product_code"), payload.get("barcode"), payload.get("product_name"))
            if not product:
                supabase_store.bulk_save_product_master(
                    source_type,
                    [
                        {
                            "sku": payload.get("product_code"),
                            "barcode": payload.get("barcode"),
                            "product_name": payload.get("product_name"),
                            "large_category": payload.get("category"),
                            "supplier": payload.get("supplier"),
                            "min_stock": payload.get("safe_stock"),
                        }
                    ],
                )
                product = supabase_store.find_product(source_type, payload.get("product_code"), payload.get("barcode"), payload.get("product_name"))
            if not product:
                continue
            current = int(summary.get(product["id"], {}).get("current_stock") or 0)
            target = int(payload.get("current_stock") or 0)
            delta = target - current
            if delta == 0:
                continue
            supabase_store.add_transaction(
                source_type=source_type,
                item_id=product["id"],
                transaction_type="ADJUST_PLUS" if delta > 0 else "ADJUST_MINUS",
                quantity=abs(delta),
                transaction_date=latest_date,
                reference=f"sqlite-latest-stock:{latest_date}:{payload.get('product_code') or payload.get('barcode')}",
                note="SQLite 최신 재고 이관",
                import_batch_id=batch_id,
            )
            count += 1
    return count


def migrate_inbound(db) -> int:
    count = 0
    for source_type in PRODUCT_MASTER_MODEL_BY_SOURCE:
        rows = [inbound_to_dict(row) for row in db.execute(select(InventoryInbound).where(InventoryInbound.source_type == source_type)).scalars()]
        count += supabase_store.bulk_save_inbound(source_type, rows)
    return count


def migrate_operational_data(db) -> dict[str, Any]:
    if not supabase_store.is_enabled():
        return {"ok": False, "message": "Supabase가 연결되어 있지 않습니다.", "count": 0}
    product_count = migrate_product_masters(db)
    stock_count = migrate_latest_stock(db)
    inbound_count = migrate_inbound(db)
    supabase_store.log_audit(
        "sqlite_migration",
        "",
        {
            "product_count": product_count,
            "stock_adjustment_count": stock_count,
            "inbound_count": inbound_count,
            "date": date.today().isoformat(),
        },
    )
    return {
        "ok": True,
        "message": "SQLite 운영 데이터 Supabase 이관 완료",
        "count": product_count + stock_count + inbound_count,
        "product_count": product_count,
        "stock_adjustment_count": stock_count,
        "inbound_count": inbound_count,
    }
