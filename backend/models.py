from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Float, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


SOURCE_TYPES = ("3PL", "오프라인", "창고")


class InventoryDaily(Base):
    __tablename__ = "inventory_daily"
    __table_args__ = (
        UniqueConstraint("source_type", "work_date", "product_name", "barcode", name="uq_inventory_daily_source_date_item"),
        CheckConstraint("source_type IN ('3PL', '오프라인', '창고')", name="ck_inventory_daily_source_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_type: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    work_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    product_code: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    barcode: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    supplier: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    current_stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    safe_stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stock_status: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    outbound_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    previous_inbound_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_inbound_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    inbound_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    inbound_cycle: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memo: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class OfflineProductMaster(Base):
    __tablename__ = "offline_product_master"
    __table_args__ = (
        UniqueConstraint("sku", name="uq_offline_product_master_sku"),
        UniqueConstraint("barcode", "product_name", name="uq_offline_product_master_barcode_product_name"),
        CheckConstraint("is_active IN ('사용', '미사용')", name="ck_offline_product_master_is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    sku: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    barcode: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    large_category: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    medium_category: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    small_category: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    brand: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    supplier: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    pack_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    box_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    default_lead_time: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    min_stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[str] = mapped_column(String(20), default="사용", nullable=False)
    memo: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class ThirdpartyProductMaster(Base):
    __tablename__ = "thirdparty_product_master"
    __table_args__ = (
        UniqueConstraint("sku", name="uq_thirdparty_product_master_sku"),
        UniqueConstraint("barcode", "product_name", name="uq_thirdparty_product_master_barcode_product_name"),
        CheckConstraint("is_active IN ('사용', '미사용')", name="ck_thirdparty_product_master_is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    sku: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    barcode: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    large_category: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    medium_category: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    small_category: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    brand: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    supplier: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    pack_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    box_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    default_lead_time: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    min_stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[str] = mapped_column(String(20), default="사용", nullable=False)
    memo: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class WarehouseProductMaster(Base):
    __tablename__ = "warehouse_product_master"
    __table_args__ = (
        UniqueConstraint("sku", name="uq_warehouse_product_master_sku"),
        UniqueConstraint("barcode", "product_name", name="uq_warehouse_product_master_barcode_product_name"),
        CheckConstraint("is_active IN ('사용', '미사용')", name="ck_warehouse_product_master_is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    sku: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    barcode: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    large_category: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    medium_category: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    small_category: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    brand: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    supplier: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    pack_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    box_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    default_lead_time: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    min_stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[str] = mapped_column(String(20), default="사용", nullable=False)
    memo: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class InventoryInbound(Base):
    __tablename__ = "inventory_inbound"
    __table_args__ = (
        CheckConstraint("source_type IN ('3PL', '오프라인', '창고')", name="ck_inventory_inbound_source_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_type: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    inbound_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    product_code: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    barcode: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    inbound_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    vendor: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    inbound_type: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    memo: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    is_applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class CategoryBomItem(Base):
    __tablename__ = "category_bom_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    category_name: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    item_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    item_type: Mapped[str] = mapped_column(String(40), default="부품", nullable=False)
    manager: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    vendor: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    required_stock: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    barcode: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    spec: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    barcode_spec: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    memo: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class PurchaseRequest(Base):
    __tablename__ = "purchase_requests"
    __table_args__ = (UniqueConstraint("pr_number", name="uq_purchase_requests_pr_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    pr_number: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    department: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    item_code: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    item_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    spec: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unit: Mapped[str] = mapped_column(String(40), default="EA", nullable=False)
    request_date: Mapped[date] = mapped_column(Date, default=date.today, index=True, nullable=False)
    reply_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    desired_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    delivery_place: Mapped[str] = mapped_column(String(160), default="로긴 물류센터", nullable=False)
    request_notes: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    requester: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    approver: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    approval_status: Mapped[str] = mapped_column(String(40), default="작성", nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), default="수기", nullable=False)
    linked_po_number: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    memo: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class RfqQuote(Base):
    __tablename__ = "rfq_quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    pr_number: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    quote_number: Mapped[str] = mapped_column(String(40), default="", index=True, nullable=False)
    item_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    supplier_name: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    supplier_manager: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    supplier_phone: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    supplier_email: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="KRW", nullable=False)
    moq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lead_time_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    shipping_fee: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payment_terms: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    quote_valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    memo: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    is_recommended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    selection_reason: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (UniqueConstraint("po_number", name="uq_purchase_orders_po_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    po_number: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    pr_number: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    supplier_name: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    item_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    spec: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="KRW", nullable=False)
    shipping_fee: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    order_date: Mapped[date] = mapped_column(Date, default=date.today, index=True, nullable=False)
    expected_inbound_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_inbound_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    inbound_status: Mapped[str] = mapped_column(String(40), default="입고대기", nullable=False)
    progress_status: Mapped[str] = mapped_column(String(40), default="발주완료", nullable=False)
    order_amount: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    memo: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("supplier_name", name="uq_suppliers_supplier_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    supplier_name: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    handled_items: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    moq_terms: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    manager: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    email: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    avg_lead_time_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    avg_unit_price: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    avg_unit_price_currency: Mapped[str] = mapped_column(String(10), default="KRW", nullable=False)
    payment_terms: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    rating: Mapped[str] = mapped_column(String(40), default="B", nullable=False)
    memo: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class ProductionPlan(Base):
    __tablename__ = "production_plans"
    __table_args__ = (UniqueConstraint("plan_number", name="uq_production_plans_plan_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    plan_number: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    plan_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, default=date.today, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="계획", nullable=False)
    memo: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class MaterialInventoryItem(Base):
    __tablename__ = "material_inventory_items"
    __table_args__ = (
        UniqueConstraint("item_code", "item_name", "related_product", name="uq_material_inventory_item_identity"),
        CheckConstraint("item_type IN ('자재', '반제품')", name="ck_material_inventory_item_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    category: Mapped[str] = mapped_column(String(120), default="", index=True, nullable=False)
    item_type: Mapped[str] = mapped_column(String(40), default="자재", index=True, nullable=False)
    related_product: Mapped[str] = mapped_column(String(255), default="", index=True, nullable=False)
    item_code: Mapped[str] = mapped_column(String(120), default="", index=True, nullable=False)
    item_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    spec: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    unit: Mapped[str] = mapped_column(String(40), default="EA", nullable=False)
    current_stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    safe_stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    location: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    supplier: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    lead_time_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    memo: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class PurchaseDocument(Base):
    __tablename__ = "purchase_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_type: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    document_number: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    creator: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    pr_number: Mapped[str] = mapped_column(String(40), default="", index=True, nullable=False)
    quote_number: Mapped[str] = mapped_column(String(40), default="", index=True, nullable=False)
    po_number: Mapped[str] = mapped_column(String(40), default="", index=True, nullable=False)
    supplier_name: Mapped[str] = mapped_column(String(160), default="", index=True, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_mime: Mapped[str] = mapped_column(String(120), default="application/octet-stream", nullable=False)
    file_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True, nullable=False)
