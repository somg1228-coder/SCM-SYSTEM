from __future__ import annotations

from datetime import date
from io import StringIO
import unittest

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import InventoryDaily, InventoryInbound, InventoryOutputHistory, OfflineProductMaster
from backend import services


class OfflineInventoryFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def test_offline_inbound_and_outbound_are_cumulative_and_idempotent(self) -> None:
        db = self.Session()
        try:
            product = OfflineProductMaster(
                sku="OFF-A",
                barcode="111222333",
                product_name="Alpha product",
                large_category="Offline",
                supplier="Vendor",
                min_stock=10,
                is_active="사용",
            )
            db.add(product)
            db.add(
                InventoryDaily(
                    source_type="오프라인",
                    work_date=date(2026, 9, 1),
                    product_code="OFF-A",
                    barcode="111222333",
                    product_name="Alpha product",
                    category="Offline",
                    supplier="Vendor",
                    current_stock=100,
                    available_stock=100,
                    safe_stock=10,
                    stock_status="정상",
                )
            )
            db.add(
                InventoryInbound(
                    source_type="오프라인",
                    inbound_date=date(2026, 9, 2),
                    product_code="OFF-A",
                    barcode="111222333",
                    product_name="Alpha product",
                    inbound_qty=20,
                    vendor="Vendor",
                    inbound_type="purchase",
                    is_applied=False,
                )
            )
            db.commit()

            self.assertEqual(services.apply_inbound_to_stock(db, "오프라인", date(2026, 9, 2)), 1)
            self.assertEqual(services.apply_inbound_to_stock(db, "오프라인", date(2026, 9, 2)), 0)

            day2 = db.execute(
                select(InventoryDaily).where(
                    InventoryDaily.source_type == "오프라인",
                    InventoryDaily.work_date == date(2026, 9, 2),
                    InventoryDaily.product_name == "Alpha product",
                )
            ).scalar_one()
            self.assertEqual(day2.current_stock, 120)
            self.assertEqual(day2.available_stock, 120)

            outbound_df = pd.DataFrame(
                [
                    {
                        "출고일자": "2026-09-03",
                        "주문번호": "ORD-1",
                        "바코드": "111222333",
                        "상품명": "Different uploaded name",
                        "출고수량": 15,
                    }
                ]
            )
            buffer = StringIO()
            outbound_df.to_csv(buffer, index=False)
            file_bytes = buffer.getvalue().encode("utf-8-sig")

            preview = services.prepare_offline_outbound_upload_preview(db, "오프라인", date(2026, 9, 3), file_bytes, "outbound.csv")
            self.assertTrue(preview["ok"])
            self.assertEqual(preview["matched_count"], 1)
            self.assertEqual(preview["preview_rows"][0]["match_method"], "바코드")

            applied = services.apply_offline_outbound_preview(db, preview, "tester")
            self.assertTrue(applied["ok"])
            self.assertEqual(applied["count"], 1)
            applied_again = services.apply_offline_outbound_preview(db, preview, "tester")
            self.assertEqual(applied_again["count"], 0)
            self.assertEqual(applied_again["duplicate_count"], 1)

            day3 = db.execute(
                select(InventoryDaily).where(
                    InventoryDaily.source_type == "오프라인",
                    InventoryDaily.work_date == date(2026, 9, 3),
                    InventoryDaily.product_name == "Alpha product",
                )
            ).scalar_one()
            self.assertEqual(day3.current_stock, 105)
            self.assertEqual(day3.available_stock, 105)
            self.assertEqual(day3.outbound_qty, 15)

            histories = list(
                db.execute(
                    select(InventoryOutputHistory).where(
                        InventoryOutputHistory.source_type == "오프라인",
                        InventoryOutputHistory.output_type == services.OFFLINE_OUTBOUND_OUTPUT_TYPE,
                    )
                ).scalars()
            )
            self.assertEqual(len(histories), 1)
            self.assertTrue(histories[0].is_applied)

            duplicate_preview = services.prepare_offline_outbound_upload_preview(db, "오프라인", date(2026, 9, 3), file_bytes, "outbound.csv")
            self.assertEqual(duplicate_preview["matched_count"], 0)
            self.assertEqual(duplicate_preview["duplicate_count"], 1)
            self.assertEqual(duplicate_preview["preview_rows"][0]["status"], "기반영")
        finally:
            db.close()

    def test_offline_outbound_sales_list_columns_are_supported(self) -> None:
        outbound_df = pd.DataFrame(
            [
                {
                    "상품코드": "220005232843",
                    "상품명": "로긴 모니카 미니 건조대 2단",
                    "일자": "20260820",
                    "매출수량": "2",
                }
            ]
        )
        buffer = StringIO()
        outbound_df.to_csv(buffer, index=False)

        parsed = services.parse_offline_outbound_file(buffer.getvalue().encode("utf-8-sig"), "salesList.csv")

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed.iloc[0]["product_code"], "220005232843")
        self.assertEqual(parsed.iloc[0]["work_date"], date(2026, 8, 20))
        self.assertEqual(parsed.iloc[0]["outbound_qty"], 2)

    def test_offline_outbound_negative_sales_qty_is_return(self) -> None:
        db = self.Session()
        try:
            product = OfflineProductMaster(
                sku="RET-1",
                barcode="",
                product_name="Return product",
                large_category="Offline",
                supplier="Vendor",
                min_stock=0,
                is_active="사용",
            )
            db.add(product)
            db.add(
                InventoryDaily(
                    source_type="오프라인",
                    work_date=date(2026, 8, 22),
                    product_code="RET-1",
                    product_name="Return product",
                    current_stock=10,
                    available_stock=10,
                    stock_status="정상",
                )
            )
            db.commit()

            outbound_df = pd.DataFrame(
                [
                    {
                        "상품코드": "RET-1",
                        "상품명": "Return product",
                        "일자": "20260823",
                        "매출수량": "-1",
                    }
                ]
            )
            buffer = StringIO()
            outbound_df.to_csv(buffer, index=False)

            preview = services.prepare_offline_outbound_upload_preview(db, "오프라인", date(2026, 8, 23), buffer.getvalue().encode("utf-8-sig"), "salesList.csv")
            self.assertEqual(preview["matched_count"], 1)
            self.assertEqual(preview["preview_rows"][0]["status"], "반품대상")

            applied = services.apply_offline_outbound_preview(db, preview, "tester")
            self.assertTrue(applied["ok"])
            self.assertEqual(applied["count"], 1)

            day3 = db.execute(
                select(InventoryDaily).where(
                    InventoryDaily.source_type == "오프라인",
                    InventoryDaily.work_date == date(2026, 8, 23),
                    InventoryDaily.product_name == "Return product",
                )
            ).scalar_one()
            self.assertEqual(day3.current_stock, 11)
            self.assertEqual(day3.available_stock, 11)
            self.assertEqual(day3.outbound_qty, -1)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
