from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Integer, JSON, LargeBinary, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


SOURCE_TYPES = ("3PL", "오프라인", "창고")


class InventoryDaily(Base):
    __tablename__ = "inventory_daily"
    __table_args__ = (
        UniqueConstraint("source_type", "work_date", "product_name", name="uq_inventory_daily_source_date_item"),
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
        UniqueConstraint("product_name", name="uq_offline_product_master_product_name"),
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
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    location_registered: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
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
        UniqueConstraint("product_name", name="uq_thirdparty_product_master_product_name"),
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
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    location_registered: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
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
        UniqueConstraint("product_name", name="uq_warehouse_product_master_product_name"),
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
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    location_registered: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    is_active: Mapped[str] = mapped_column(String(20), default="사용", nullable=False)
    memo: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class WarehouseLayout(Base):
    __tablename__ = "warehouse_layouts"
    __table_args__ = (UniqueConstraint("building", "floor", name="uq_warehouse_layouts_building_floor"),)

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        index=True,
        default=lambda: str(uuid4()),
    )
    building: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    floor: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    layout_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default=func.now(), onupdate=datetime.utcnow, nullable=False)


class WarehouseRack(Base):
    __tablename__ = "warehouse_racks"
    __table_args__ = (UniqueConstraint("layout_id", "rack_code", name="uq_warehouse_racks_layout_rack_code"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    layout_id: Mapped[str] = mapped_column(String(36), ForeignKey("warehouse_layouts.id", ondelete="CASCADE"), index=True, nullable=False)
    rack_code: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    rack_name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    x: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    y: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    z: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    rotation: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    width: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    depth: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    height: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    shelf_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    rack_type: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rack_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default=func.now(), onupdate=datetime.utcnow, nullable=False)


class WarehouseInventoryPosition(Base):
    __tablename__ = "warehouse_inventory_positions"
    __table_args__ = (UniqueConstraint("rack_id", "shelf_no", "sku", "item_name", name="uq_warehouse_inventory_positions_rack_item"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True, index=True)
    rack_id: Mapped[str] = mapped_column(String(64), ForeignKey("warehouse_racks.id", ondelete="CASCADE"), index=True, nullable=False)
    shelf_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    sku: Mapped[str] = mapped_column(String(120), index=True, default="", nullable=False)
    item_name: Mapped[str] = mapped_column(String(255), index=True, default="", nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    position_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default=func.now(), onupdate=datetime.utcnow, nullable=False)


class ScheduleWeek(Base):
    __tablename__ = "schedule_weeks"
    __table_args__ = (UniqueConstraint("week_start", name="uq_schedule_weeks_week_start"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    week_start: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[str] = mapped_column(Text, default="", nullable=False)
    comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ScheduleHighlight(Base):
    __tablename__ = "schedule_highlights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    week_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    checked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ScheduleSlot(Base):
    __tablename__ = "schedule_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    week_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    time_label: Mapped[str] = mapped_column(Text, nullable=False)
    mon: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tue: Mapped[str] = mapped_column(Text, default="", nullable=False)
    wed: Mapped[str] = mapped_column(Text, default="", nullable=False)
    thu: Mapped[str] = mapped_column(Text, default="", nullable=False)
    fri: Mapped[str] = mapped_column(Text, default="", nullable=False)


class MeetingReport(Base):
    __tablename__ = "meeting_reports"
    __table_args__ = (UniqueConstraint("meeting_date", name="uq_meeting_reports_meeting_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    meeting_date: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    author: Mapped[str] = mapped_column(Text, nullable=False)
    event_detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    issue_delay: Mapped[str] = mapped_column(Text, default="", nullable=False)
    issue_inventory: Mapped[str] = mapped_column(Text, default="", nullable=False)
    issue_special: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class MeetingMeta(Base):
    __tablename__ = "meeting_meta"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class MeetingProductionRequest(Base):
    __tablename__ = "meeting_production_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    report_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    production_code: Mapped[str] = mapped_column(Text, nullable=False)
    product_name: Mapped[str] = mapped_column(Text, nullable=False)
    current_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    request_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    due_date: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    memo: Mapped[str] = mapped_column(Text, default="", nullable=False)


class MeetingEvent(Base):
    __tablename__ = "meeting_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    report_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    event_name: Mapped[str] = mapped_column(Text, nullable=False)
    period: Mapped[str] = mapped_column(Text, nullable=False)
    affected_products: Mapped[str] = mapped_column(Text, nullable=False)
    request_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[str] = mapped_column(Text, nullable=False)
    memo: Mapped[str] = mapped_column(Text, default="", nullable=False)
    event_month: Mapped[str] = mapped_column(String(20), default="", index=True, nullable=False)


class MeetingActionItem(Base):
    __tablename__ = "meeting_action_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    report_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    owner: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    due_date: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_date: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)


class ReturnCase(Base):
    __tablename__ = "cases"
    __table_args__ = (UniqueConstraint("case_id", name="uq_cases_case_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    case_id: Mapped[str | None] = mapped_column(Text, index=True, nullable=True)
    category: Mapped[str | None] = mapped_column(Text, index=True, nullable=True)
    barcode: Mapped[str | None] = mapped_column(Text, index=True, nullable=True)
    product: Mapped[str | None] = mapped_column(Text, index=True, nullable=True)
    cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str | None] = mapped_column(Text, nullable=True)
    repair_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    prevention: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_image: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    case_image: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    case_image_original: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    repair_image: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    repair_image_original: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


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


class InventoryUploadHistory(Base):
    __tablename__ = "inventory_upload_histories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_type: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    work_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    upload_mode: Mapped[str] = mapped_column(String(20), default="partial", nullable=False)
    total_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    matched_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    zeroed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class InventoryUploadSnapshot(Base):
    __tablename__ = "inventory_upload_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    upload_history_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    work_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    product_code: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    barcode: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    previous_stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    new_stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class InventoryOutputHistory(Base):
    __tablename__ = "inventory_output_histories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_type: Mapped[str] = mapped_column(String(20), default="", index=True, nullable=False)
    work_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    output_type: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    created_by: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    filter_json: Mapped[str] = mapped_column(Text, default="", nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    external_key: Mapped[str] = mapped_column(String(255), default="", index=True, nullable=False)
    order_no: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    shipment_no: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    invoice_no: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    product_code: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), default="", index=True, nullable=False)
    barcode: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    outbound_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    memo: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


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
    supplier_code: Mapped[str] = mapped_column(String(40), default="", index=True, nullable=False)
    supplier_name: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    business_number: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    handled_items: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    moq_terms: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    manager: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    email: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    transaction_status: Mapped[str] = mapped_column(String(40), default="거래중", index=True, nullable=False)
    current_grade: Mapped[str] = mapped_column(String(20), default="미평가", index=True, nullable=False)
    latest_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    latest_evaluation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_evaluation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    special_management: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    special_reason: Mapped[str] = mapped_column(String(500), default="", nullable=False)
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


class SupplierEvaluation(Base):
    __tablename__ = "supplier_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    supplier_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    evaluation_year: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    evaluation_quarter: Mapped[str] = mapped_column(String(10), default="Q1", index=True, nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    evaluation_date: Mapped[date] = mapped_column(Date, default=date.today, index=True, nullable=False)
    evaluator: Mapped[str] = mapped_column(String(120), default="", index=True, nullable=False)
    next_evaluation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="임시저장", index=True, nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    delivery_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    price_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    service_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    stability_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    applicable_weight: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    earned_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    total_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    base_grade: Mapped[str] = mapped_column(String(20), default="미평가", index=True, nullable=False)
    final_grade: Mapped[str] = mapped_column(String(20), default="미평가", index=True, nullable=False)
    grade_limit_reason: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    previous_grade: Mapped[str] = mapped_column(String(20), default="미평가", nullable=False)
    special_flags: Mapped[str] = mapped_column(Text, default="", nullable=False)
    special_reasons: Mapped[str] = mapped_column(Text, default="", nullable=False)
    special_warning: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    overall_comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    excellent_points: Mapped[str] = mapped_column(Text, default="", nullable=False)
    problem_points: Mapped[str] = mapped_column(Text, default="", nullable=False)
    improvement_request: Mapped[str] = mapped_column(Text, default="", nullable=False)
    improvement_owner: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    improvement_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    improvement_status: Mapped[str] = mapped_column(String(40), default="해당 없음", index=True, nullable=False)
    attachment_ref: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    internal_memo: Mapped[str] = mapped_column(Text, default="", nullable=False)
    rejection_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    criteria_version: Mapped[str] = mapped_column(String(40), default="v1", nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    inactive_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    inactive_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    updated_by: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class SupplierEvaluationItem(Base):
    __tablename__ = "supplier_evaluation_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    evaluation_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    category_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    item_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    category_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    item_name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    selected_rating: Mapped[str] = mapped_column(String(40), default="보통", nullable=False)
    item_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    item_weight: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    not_applicable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    comment: Mapped[str] = mapped_column(String(500), default="", nullable=False)


class SupplierEvaluationCriteria(Base):
    __tablename__ = "supplier_evaluation_criteria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    criteria_version: Mapped[str] = mapped_column(String(40), default="v1", index=True, nullable=False)
    category_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    category_name: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    category_weight: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    item_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    item_name: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    item_weight: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    item_description: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class SupplierGradeRule(Base):
    __tablename__ = "supplier_grade_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    grade: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    minimum_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    maximum_score: Mapped[float] = mapped_column(Float, default=100, nullable=False)
    label: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_downgrade_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    major_quality_max_grade: Mapped[str] = mapped_column(String(20), default="C", nullable=False)
    contract_violation_max_grade: Mapped[str] = mapped_column(String(20), default="D", nullable=False)


class SupplierEvaluationCriteriaVersion(Base):
    __tablename__ = "supplier_evaluation_criteria_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    version_code: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    version_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="사용 중", index=True, nullable=False)
    note: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SupplierEvaluationCategory(Base):
    __tablename__ = "supplier_evaluation_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    criteria_version: Mapped[str] = mapped_column(String(40), default="v1", index=True, nullable=False)
    category_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    category_name: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    category_weight: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class SupplierSpecialRule(Base):
    __tablename__ = "supplier_special_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    flag_name: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    show_warning: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reason_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    grade_limit_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_grade: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    reflect_to_supplier: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class SupplierEvaluationHistory(Base):
    __tablename__ = "supplier_evaluation_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    evaluation_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    supplier_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    action_type: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    before_data: Mapped[str] = mapped_column(Text, default="", nullable=False)
    after_data: Mapped[str] = mapped_column(Text, default="", nullable=False)
    changed_by: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    change_reason: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True, nullable=False)


class SupplierApprovalHistory(Base):
    __tablename__ = "supplier_approval_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    evaluation_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    supplier_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    action_type: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    status_from: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    status_to: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    actor: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    acted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True, nullable=False)


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


class PurchaseBudgetStore(Base):
    __tablename__ = "purchase_budget_stores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    store_key: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default=func.now(), onupdate=datetime.utcnow, nullable=False)
