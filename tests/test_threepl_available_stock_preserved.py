from __future__ import annotations

from datetime import date
from io import StringIO
import csv
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import InventoryDaily, ThirdpartyProductMaster
from backend import services


class ThreeplAvailableStockPreservedTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def test_erp_upload_keeps_uploaded_available_stock_without_outbound_subtraction(self) -> None:
        work_date = date(2026, 9, 15)
        db = self.Session()
        try:
            db.add(
                ThirdpartyProductMaster(
                    sku="SKU-001",
                    barcode="8800000000001",
                    product_name="plain product",
                    large_category="3PL",
                    supplier="vendor",
                    is_active="사용",
                )
            )
            db.commit()

            csv_buffer = StringIO()
            writer = csv.writer(csv_buffer)
            writer.writerow(["SKU", "barcode", "product_name", "available_stock", "invoice", "receipt"])
            writer.writerow(["SKU-001", "8800000000001", "plain product", 100, 5, 2])

            result = services.apply_erp_stock_upload_file(
                db,
                "3PL",
                work_date,
                csv_buffer.getvalue().encode("utf-8-sig"),
                file_name="threepl-stock.csv",
            )

            self.assertTrue(result["ok"], result)
            saved = db.execute(
                select(InventoryDaily).where(
                    InventoryDaily.source_type == "3PL",
                    InventoryDaily.work_date == work_date,
                    InventoryDaily.product_code == "SKU-001",
                )
            ).scalar_one()

            self.assertEqual(saved.current_stock, 100)
            self.assertEqual(saved.available_stock, 100)
            self.assertEqual(saved.outbound_qty, 7)

            daily = services.daily_to_dict(saved)
            self.assertEqual(daily["available_stock"], 100)
            self.assertEqual(daily["outbound_qty"], 7)

            inventory_rows = services.master_based_inventory_rows(db, "3PL", work_date)
            by_sku = {row["product_code"]: row for row in inventory_rows}
            self.assertEqual(by_sku["SKU-001"]["available_stock"], 100)
            self.assertEqual(by_sku["SKU-001"]["pending_outbound_qty"], 7)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
