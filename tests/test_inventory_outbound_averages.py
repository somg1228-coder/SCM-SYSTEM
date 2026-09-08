from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO
import unittest

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend import services
from backend.database import Base
from backend.models import InventoryDaily, InventoryInbound, OfflineProductMaster, ThirdpartyProductMaster


class InventoryOutboundAverageTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def test_weekly_outbound_average_uses_last_month_total(self) -> None:
        work_date = date(2026, 8, 26)
        db = self.Session()
        try:
            product = OfflineProductMaster(
                sku="SKU-WEEKLY",
                barcode="1234567890123",
                product_name="Weekly Average Product",
                is_active="사용",
            )
            db.add(product)
            for offset, outbound in ((0, 30), (6, 20), (14, 40), (29, 30), (30, 999)):
                db.add(
                    InventoryDaily(
                        source_type="3PL",
                        work_date=work_date - timedelta(days=offset),
                        product_code="SKU-WEEKLY",
                        product_name="Weekly Average Product",
                        barcode="1234567890123",
                        current_stock=100,
                        available_stock=100,
                        outbound_qty=outbound,
                    )
                )
            db.commit()

            weekly = services.recent_outbound_weekly_average_by_product(db, "3PL", work_date, [product])
            daily = services.recent_outbound_daily_average_by_product(db, "3PL", work_date, [product])

            self.assertEqual(weekly["SKU-WEEKLY"], 28)
            self.assertEqual(daily["SKU-WEEKLY"], 4)
        finally:
            db.close()

    def test_offline_inbound_uses_exact_product_name_stock_history(self) -> None:
        work_date = date(2026, 8, 26)
        db = self.Session()
        try:
            product = ThirdpartyProductMaster(
                sku="SKU-EXACT",
                barcode="1234567890123",
                product_name="Exact Product",
                is_active="사용",
            )
            db.add(product)
            db.add_all(
                [
                    InventoryDaily(
                        source_type="오프라인",
                        work_date=work_date - timedelta(days=2),
                        product_code="SKU-EXACT",
                        product_name="Exact Product",
                        barcode="1234567890123",
                        current_stock=10,
                        available_stock=10,
                    ),
                    InventoryDaily(
                        source_type="오프라인",
                        work_date=work_date - timedelta(days=1),
                        product_code="SKU-EXACT",
                        product_name="Old Product Name",
                        barcode="1234567890123",
                        current_stock=999,
                        available_stock=999,
                    ),
                    InventoryDaily(
                        source_type="오프라인",
                        work_date=work_date + timedelta(days=1),
                        product_code="SKU-EXACT",
                        product_name="Old Product Name",
                        barcode="1234567890123",
                        current_stock=999,
                        available_stock=999,
                    ),
                    InventoryDaily(
                        source_type="오프라인",
                        work_date=work_date + timedelta(days=1),
                        product_code="SKU-EXACT",
                        product_name="Exact Product",
                        barcode="1234567890123",
                        current_stock=20,
                        available_stock=20,
                    ),
                    InventoryInbound(
                        source_type="오프라인",
                        inbound_date=work_date,
                        product_code="",
                        product_name="Exact Product",
                        barcode="",
                        inbound_qty=5,
                        is_applied=False,
                    ),
                ]
            )
            db.commit()

            services.apply_inbound_to_stock(db, "오프라인", work_date)

            exact_today = db.execute(
                select(InventoryDaily).where(
                    InventoryDaily.source_type == "오프라인",
                    InventoryDaily.work_date == work_date,
                    InventoryDaily.product_name == "Exact Product",
                )
            ).scalar_one()
            old_future = db.execute(
                select(InventoryDaily).where(
                    InventoryDaily.source_type == "오프라인",
                    InventoryDaily.work_date == work_date + timedelta(days=1),
                    InventoryDaily.product_name == "Old Product Name",
                )
            ).scalar_one()
            exact_future = db.execute(
                select(InventoryDaily).where(
                    InventoryDaily.source_type == "오프라인",
                    InventoryDaily.work_date == work_date + timedelta(days=1),
                    InventoryDaily.product_name == "Exact Product",
                )
            ).scalar_one()

            self.assertEqual(exact_today.current_stock, 15)
            self.assertEqual(old_future.current_stock, 999)
            self.assertEqual(exact_future.current_stock, 25)
        finally:
            db.close()

    def test_excel_stock_adjustment_preview_reports_unmatched_rows(self) -> None:
        work_date = date(2026, 8, 26)
        db = self.Session()
        try:
            db.add(
                ThirdpartyProductMaster(
                    sku="SKU-BULK",
                    barcode="1234567890123",
                    product_name="Bulk Product",
                    is_active="사용",
                )
            )
            db.commit()
            excel_bytes = self._excel_bytes(
                [
                    {"SKU": "SKU-BULK", "바코드": "1234567890123", "상품명": "Bulk Product", "현재고": 38, "안전재고": 5},
                    {"SKU": "", "바코드": "", "상품명": "Missing Product", "현재고": 5, "안전재고": 1},
                ]
            )

            preview = services.prepare_excel_stock_adjustment_preview(db, "3PL", work_date, excel_bytes, "bulk.xlsx")

            self.assertEqual(preview["total_rows"], 2)
            self.assertEqual(preview["matched_count"], 1)
            self.assertEqual(preview["failed_count"], 1)
            self.assertEqual(preview["unmatched_count"], 1)
            self.assertEqual(preview["preview_rows"][1]["status"], "상품명 매칭 실패")
        finally:
            db.close()

    def test_excel_stock_adjustment_replaces_stock_and_preserves_negative_quantity(self) -> None:
        work_date = date(2026, 8, 26)
        db = self.Session()
        try:
            product = ThirdpartyProductMaster(
                sku="SKU-BULK",
                barcode="1234567890123",
                product_name="Bulk Product",
                min_stock=3,
                is_active="사용",
            )
            db.add(product)
            db.add(
                InventoryDaily(
                    source_type="3PL",
                    work_date=work_date,
                    product_code="SKU-BULK",
                    product_name="Bulk Product",
                    barcode="1234567890123",
                    current_stock=20,
                    available_stock=20,
                    safe_stock=3,
                )
            )
            db.commit()
            excel_bytes = self._excel_bytes(
                [{"SKU": "SKU-BULK", "바코드": "1234567890123", "상품명": "Bulk Product", "현재고": -2, "안전재고": 7}]
            )

            preview = services.prepare_excel_stock_adjustment_preview(db, "3PL", work_date, excel_bytes, "bulk.xlsx")
            result = services.apply_manual_stock_adjustment_preview(db, "3PL", work_date, preview, "tester")
            daily = db.execute(
                select(InventoryDaily).where(
                    InventoryDaily.source_type == "3PL",
                    InventoryDaily.work_date == work_date,
                    InventoryDaily.product_name == "Bulk Product",
                )
            ).scalar_one()
            product = db.execute(select(ThirdpartyProductMaster).where(ThirdpartyProductMaster.sku == "SKU-BULK")).scalar_one()

            self.assertTrue(result["ok"])
            self.assertEqual(daily.current_stock, -2)
            self.assertEqual(daily.available_stock, -2)
            self.assertEqual(daily.safe_stock, 7)
            self.assertEqual(daily.stock_status, "품절")
            self.assertEqual(product.min_stock, 7)
        finally:
            db.close()

    @staticmethod
    def _excel_bytes(rows: list[dict]) -> bytes:
        output = BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            pd.DataFrame(rows).to_excel(writer, index=False, sheet_name="재고수정")
        return output.getvalue()

    def test_master_based_inventory_rows_separates_weekly_display_and_daily_order_rate(self) -> None:
        work_date = date(2026, 8, 26)
        db = self.Session()
        try:
            product = ThirdpartyProductMaster(
                sku="SKU-WEEKLY",
                barcode="1234567890123",
                product_name="Weekly Average Product",
                default_lead_time=1,
                min_stock=10,
                is_active="사용",
            )
            db.add(product)
            db.add(
                InventoryDaily(
                    source_type="3PL",
                    work_date=work_date,
                    product_code="SKU-WEEKLY",
                    product_name="Weekly Average Product",
                    barcode="1234567890123",
                    current_stock=100,
                    available_stock=100,
                    outbound_qty=30,
                )
            )
            db.add(
                InventoryDaily(
                    source_type="3PL",
                    work_date=work_date - timedelta(days=6),
                    product_code="SKU-WEEKLY",
                    product_name="Weekly Average Product",
                    barcode="1234567890123",
                    current_stock=100,
                    available_stock=100,
                    outbound_qty=90,
                )
            )
            db.commit()

            rows = services.master_based_inventory_rows(db, "3PL", work_date)

            self.assertEqual(rows[0]["avg_weekly_outbound"], 28)
            self.assertEqual(rows[0]["avg_daily_outbound"], 4)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
