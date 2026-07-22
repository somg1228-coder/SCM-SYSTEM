from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO
from math import ceil
from statistics import median
from typing import BinaryIO, Iterable

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from models import InventoryItem


DISPLAY_COLUMNS = [
    "id",
    "카테고리",
    "상품명",
    "현재고",
    "안전재고",
    "상태",
    "출고수량",
    "입고주기",
    "비고",
]


def normalize_name(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def to_int(value) -> int:
    text = normalize_name(value).replace(",", "")
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def parse_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def is_business_day(day: date, holidays: Iterable[date] | None = None) -> bool:
    holiday_set = set(holidays or [])
    return day.weekday() < 5 and day not in holiday_set


def get_work_dates(db: Session) -> list[date]:
    rows = db.execute(
        select(InventoryItem.work_date)
        .distinct()
        .order_by(InventoryItem.work_date.desc())
    ).scalars()
    return list(rows)


def create_today_data(db: Session, target_date: date | None = None) -> dict:
    work_date = target_date or date.today()
    if not is_business_day(work_date):
        return {"created": False, "message": "주말/공휴일은 생성하지 않습니다.", "work_date": work_date, "count": 0}

    exists = db.scalar(select(func.count()).where(InventoryItem.work_date == work_date))
    if exists:
        return {"created": False, "message": "이미 해당 날짜 데이터가 있습니다.", "work_date": work_date, "count": 0}

    latest_date = db.scalar(select(func.max(InventoryItem.work_date)))
    if latest_date and latest_date >= work_date:
        return {"created": False, "message": "오늘 이후 데이터가 있어 생성을 중단했습니다.", "work_date": work_date, "count": 0}

    previous_date = db.scalar(select(func.max(InventoryItem.work_date)).where(InventoryItem.work_date < work_date))
    if previous_date is None:
        db.commit()
        return {"created": True, "message": "복사할 직전 데이터가 없어 빈 날짜만 준비했습니다.", "work_date": work_date, "count": 0}

    previous_rows = db.execute(
        select(InventoryItem).where(InventoryItem.work_date == previous_date).order_by(InventoryItem.id)
    ).scalars()

    count = 0
    for item in previous_rows:
        db.add(
            InventoryItem(
                work_date=work_date,
                category=item.category,
                product_name=item.product_name,
                current_stock=0,
                safe_stock=item.safe_stock,
                status="",
                outbound_qty=0,
                inbound_qty=item.inbound_qty,
                previous_inbound_date=item.previous_inbound_date,
                last_inbound_date=item.last_inbound_date,
                inbound_cycle=item.inbound_cycle,
                memo=item.memo,
            )
        )
        count += 1
    db.commit()
    update_status(db, work_date)
    return {"created": True, "message": "오늘 데이터 생성 완료", "work_date": work_date, "count": count}


def get_items(db: Session, work_date: date) -> list[InventoryItem]:
    return list(
        db.execute(
            select(InventoryItem)
            .where(InventoryItem.work_date == work_date)
            .order_by(InventoryItem.category, InventoryItem.product_name)
        ).scalars()
    )


def item_to_display_dict(item: InventoryItem) -> dict:
    return {
        "id": item.id,
        "카테고리": item.category,
        "상품명": item.product_name,
        "현재고": item.current_stock,
        "안전재고": item.safe_stock,
        "상태": item.status,
        "출고수량": item.outbound_qty,
        "입고주기": item.inbound_cycle,
        "비고": item.memo,
    }


def items_to_dataframe(items: list[InventoryItem]) -> pd.DataFrame:
    if not items:
        return pd.DataFrame(columns=DISPLAY_COLUMNS)
    return pd.DataFrame([item_to_display_dict(item) for item in items], columns=DISPLAY_COLUMNS)


def save_display_records(db: Session, work_date: date, records: list[dict]) -> int:
    db.execute(delete(InventoryItem).where(InventoryItem.work_date == work_date))
    count = 0
    seen_products: set[str] = set()
    for row in records:
        product_name = normalize_name(row.get("상품명"))
        if not product_name or product_name in seen_products:
            continue
        seen_products.add(product_name)
        db.add(
            InventoryItem(
                work_date=work_date,
                category=normalize_name(row.get("카테고리")),
                product_name=product_name,
                current_stock=to_int(row.get("현재고")),
                safe_stock=to_int(row.get("안전재고")),
                status=normalize_name(row.get("상태")),
                outbound_qty=to_int(row.get("출고수량")),
                inbound_cycle=to_int(row.get("입고주기")) or None,
                memo=normalize_name(row.get("비고")),
            )
        )
        count += 1
    db.commit()
    update_status(db, work_date)
    return count


def read_excel(uploaded_file: BinaryIO | bytes) -> pd.DataFrame:
    if isinstance(uploaded_file, bytes):
        return pd.read_excel(BytesIO(uploaded_file))
    return pd.read_excel(uploaded_file)


def find_column(df: pd.DataFrame, candidates: list[str]) -> str:
    normalized = {str(column).strip().replace(" ", ""): column for column in df.columns}
    for candidate in candidates:
        key = candidate.strip().replace(" ", "")
        if key in normalized:
            return normalized[key]
    raise ValueError(f"필수 컬럼을 찾지 못했습니다: {', '.join(candidates)}")


def product_map(db: Session, work_date: date) -> dict[str, InventoryItem]:
    return {
        item.product_name: item
        for item in db.execute(select(InventoryItem).where(InventoryItem.work_date == work_date)).scalars()
    }


def get_or_create_item(db: Session, work_date: date, product_name: str) -> InventoryItem:
    mapping = product_map(db, work_date)
    item = mapping.get(product_name)
    if item:
        return item
    item = InventoryItem(work_date=work_date, product_name=product_name)
    db.add(item)
    db.flush()
    return item


def import_stock(db: Session, work_date: date, uploaded_file: BinaryIO | bytes) -> int:
    df = read_excel(uploaded_file)
    name_col = find_column(df, ["상품명"])
    stock_col = find_column(df, ["가용재고"])
    count = 0
    for _, row in df.iterrows():
        product_name = normalize_name(row.get(name_col))
        if not product_name:
            continue
        item = get_or_create_item(db, work_date, product_name)
        item.current_stock = to_int(row.get(stock_col))
        count += 1
    db.commit()
    update_status(db, work_date)
    return count


def import_order(db: Session, work_date: date, uploaded_file: BinaryIO | bytes) -> int:
    df = read_excel(uploaded_file)
    name_col = find_column(df, ["상품명"])
    qty_col = find_column(df, ["상품수량"])
    cs_col = find_column(df, ["CS"])
    filtered = df[df[cs_col].astype(str).str.contains("정상", na=False)]
    sums: dict[str, int] = {}
    for _, row in filtered.iterrows():
        product_name = normalize_name(row.get(name_col))
        if not product_name:
            continue
        sums[product_name] = sums.get(product_name, 0) + to_int(row.get(qty_col))

    for item in get_items(db, work_date):
        item.outbound_qty = 0
    for product_name, qty in sums.items():
        item = get_or_create_item(db, work_date, product_name)
        item.outbound_qty = qty
    db.commit()
    calculate_safe_stock(db, work_date)
    update_status(db, work_date)
    return len(sums)


def import_inbound(db: Session, work_date: date, uploaded_file: BinaryIO | bytes) -> int:
    df = read_excel(uploaded_file)
    name_col = find_column(df, ["품목", "상품명"])
    qty_col = find_column(df, ["수량", "입고수량"])
    sums: dict[str, int] = {}
    for _, row in df.iterrows():
        product_name = normalize_name(row.get(name_col))
        if not product_name:
            continue
        sums[product_name] = sums.get(product_name, 0) + to_int(row.get(qty_col))

    for product_name, qty in sums.items():
        item = get_or_create_item(db, work_date, product_name)
        if item.last_inbound_date and item.last_inbound_date != work_date:
            item.previous_inbound_date = item.last_inbound_date
        item.last_inbound_date = work_date
        item.inbound_qty = qty
    db.commit()
    calculate_inbound_cycle(db)
    return len(sums)


def apply_inbound(db: Session, work_date: date) -> int:
    count = 0
    for item in get_items(db, work_date):
        if item.last_inbound_date == work_date and item.inbound_qty:
            item.current_stock += item.inbound_qty
            item.inbound_qty = 0
            count += 1
    db.commit()
    update_status(db, work_date)
    return count


def week_range(target: date, weeks_ago: int) -> tuple[date, date]:
    current_monday = target - timedelta(days=target.weekday())
    start = current_monday - timedelta(days=7 * weeks_ago)
    return start, start + timedelta(days=6)


def calculate_safe_stock(db: Session, work_date: date) -> int:
    last_start, last_end = week_range(work_date, 1)
    prev_start, prev_end = week_range(work_date, 2)
    last_map = outbound_sum_by_product(db, last_start, last_end)
    prev_map = outbound_sum_by_product(db, prev_start, prev_end)
    products = {item.product_name for item in get_items(db, work_date)}
    count = 0
    for item in get_items(db, work_date):
        base = max(last_map.get(item.product_name, 0), prev_map.get(item.product_name, 0))
        item.safe_stock = ceil(base * 6 / 5)
        count += 1
    db.commit()
    update_status(db, work_date)
    return count


def outbound_sum_by_product(db: Session, start: date, end: date) -> dict[str, int]:
    rows = db.execute(
        select(InventoryItem.product_name, func.sum(InventoryItem.outbound_qty))
        .where(InventoryItem.work_date >= start, InventoryItem.work_date <= end)
        .group_by(InventoryItem.product_name)
    ).all()
    return {product_name: int(total or 0) for product_name, total in rows}


def update_status(db: Session, work_date: date, show_normal: bool = False) -> int:
    count = 0
    for item in get_items(db, work_date):
        if item.current_stock < 0:
            item.status = "미출"
        elif item.current_stock == 0:
            item.status = "품절"
        elif item.current_stock < item.safe_stock:
            item.status = "입고필요"
        else:
            item.status = "정상" if show_normal else ""
        count += 1
    db.commit()
    return count


def calculate_inbound_cycle(db: Session) -> int:
    product_names = db.execute(select(InventoryItem.product_name).distinct()).scalars()
    count = 0
    for product_name in product_names:
        dates = sorted(
            {
                item.last_inbound_date
                for item in db.execute(
                    select(InventoryItem)
                    .where(InventoryItem.product_name == product_name)
                    .order_by(InventoryItem.work_date)
                ).scalars()
                if item.last_inbound_date
            }
        )
        diffs = [
            (dates[index] - dates[index - 1]).days
            for index in range(1, len(dates))
            if 1 <= (dates[index] - dates[index - 1]).days <= 90
        ]
        cycle = round(median(diffs)) if diffs else None
        for item in db.execute(select(InventoryItem).where(InventoryItem.product_name == product_name)).scalars():
            item.inbound_cycle = cycle
        count += 1
    db.commit()
    return count


def trend_dataframe(db: Session, metric: str) -> pd.DataFrame:
    value_column = InventoryItem.current_stock if metric == "stock" else InventoryItem.outbound_qty
    rows = db.execute(
        select(InventoryItem.work_date, InventoryItem.category, func.sum(value_column))
        .group_by(InventoryItem.work_date, InventoryItem.category)
        .order_by(InventoryItem.work_date)
    ).all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["날짜", "카테고리", "합계"])
    df["카테고리"] = df["카테고리"].replace("", "미분류")
    pivot = df.pivot_table(index="날짜", columns="카테고리", values="합계", aggfunc="sum", fill_value=0)
    return pivot.sort_index()


def dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="재고관리")
    return output.getvalue()
