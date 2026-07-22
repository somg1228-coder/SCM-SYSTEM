from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    __table_args__ = (
        UniqueConstraint("work_date", "product_name", name="uq_inventory_work_date_product"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    work_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    current_stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    safe_stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    outbound_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    inbound_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    previous_inbound_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_inbound_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    inbound_cycle: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memo: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
