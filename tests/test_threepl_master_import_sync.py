from __future__ import annotations

from io import BytesIO
import time
import unittest

import pandas as pd
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from backend import services
from backend.database import Base
from backend.models import InventoryDaily, ThirdpartyProductMaster


class ThreeplMasterImportSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def upload_bytes(self, rows: list[dict]) -> bytes:
        buffer = BytesIO()
        pd.DataFrame(rows).to_excel(buffer, index=False, sheet_name=services.THREEPL_MASTER_SHEET_NAME)
        return buffer.getvalue()

    def row(self, product_name: str, barcode: str, sku: str = "", category: str = "3PL") -> dict:
        data = {
            services.SHARED_MASTER_FORM_COLUMNS[0]: category,
            "바코드": barcode,
            services.SHARED_MASTER_FORM_COLUMNS[1]: product_name,
            services.SHARED_MASTER_FORM_COLUMNS[2]: "vendor",
            services.SHARED_MASTER_FORM_COLUMNS[3]: "0",
            services.SHARED_MASTER_FORM_COLUMNS[4]: "manager",
            services.SHARED_MASTER_FORM_COLUMNS[5]: "3",
        }
        if sku:
            data["SKU"] = sku
        return data

    def test_deduplicates_updates_deletes_and_syncs_inventory_daily(self) -> None:
        db = self.Session()
        try:
            db.add_all(
                [
                    ThirdpartyProductMaster(sku="OLD-SKU", barcode="111", product_name="2단 세로 풀세트", large_category="OLD"),
                    ThirdpartyProductMaster(sku="STALE", barcode="999", product_name="old stale product", large_category="OLD"),
                ]
            )
            db.commit()

            result = services.import_product_master_excel(
                db,
                "3PL",
                self.upload_bytes(
                    [
                        self.row("2단 세로 풀세트", "222"),
                        self.row("new product", "333", sku="NEW-SKU"),
                        self.row("new product", "333", sku="NEW-SKU"),
                    ]
                ),
            )

            self.assertTrue(result["ok"], result.get("message"))
            self.assertEqual(result["summary"]["기존 마스터"], 2)
            self.assertEqual(result["summary"]["새 마스터 파일"], 2)
            self.assertEqual(result["summary"]["유지/갱신"], 1)
            self.assertEqual(result["summary"]["신규"], 1)
            self.assertEqual(result["summary"]["삭제"], 1)
            self.assertEqual(result["summary"]["중복 제거"], 1)
            self.assertEqual(result["summary"]["DB 재조회 마스터 수"], 2)
            self.assertNotIn("InventoryDaily 신규 생성", result["summary"])

            masters = db.execute(select(ThirdpartyProductMaster)).scalars().all()
            by_name = {row.product_name: row for row in masters}
            self.assertEqual(len(masters), 2)
            self.assertEqual(by_name["2단 세로 풀세트"].sku, "OLD-SKU")
            self.assertEqual(by_name["2단 세로 풀세트"].barcode, "222")
            self.assertIn("new product", by_name)
            self.assertNotIn("old stale product", by_name)

            daily_rows = db.execute(select(InventoryDaily).where(InventoryDaily.source_type == "3PL")).scalars().all()
            self.assertEqual(daily_rows, [])
        finally:
            db.close()

    def test_same_sku_different_names_are_conflicts_without_canceling_all(self) -> None:
        db = self.Session()
        try:
            db.add_all(
                [
                    ThirdpartyProductMaster(sku="KEEP", barcode="100", product_name="keep product"),
                    ThirdpartyProductMaster(sku="OLD-CONFLICT", barcode="200", product_name="conflict product a"),
                ]
            )
            db.commit()

            result = services.import_product_master_excel(
                db,
                "3PL",
                self.upload_bytes(
                    [
                        self.row("keep product", "101", sku="KEEP"),
                        self.row("conflict product a", "201", sku="DUP-SKU"),
                        self.row("conflict product b", "202", sku="DUP-SKU"),
                    ]
                ),
            )

            self.assertTrue(result["ok"], result.get("message"))
            self.assertEqual(result["summary"]["충돌"], 2)
            conflict_details = [detail for detail in result["details"] if detail.get("처리 유형") == "충돌"]
            self.assertEqual(len(conflict_details), 2)
            self.assertIn("충돌 행", conflict_details[0])

            masters = db.execute(select(ThirdpartyProductMaster)).scalars().all()
            by_name = {row.product_name: row for row in masters}
            self.assertEqual(by_name["keep product"].barcode, "101")
            self.assertEqual(by_name["conflict product a"].sku, "OLD-CONFLICT")
            self.assertNotIn("conflict product b", by_name)
        finally:
            db.close()

    def test_import_289_rows_replaces_285_without_inventory_sync_under_30_seconds(self) -> None:
        db = self.Session()
        try:
            db.add_all(
                [
                    ThirdpartyProductMaster(
                        sku=f"OLD-{index:03d}",
                        barcode=f"8800000{index:05d}",
                        product_name=f"product {index:03d}",
                        default_lead_time=1,
                    )
                    for index in range(285)
                ]
            )
            db.add(
                InventoryDaily(
                    source_type="3PL",
                    work_date=pd.Timestamp("2026-08-28").date(),
                    product_code="OLD-001",
                    product_name="product 001",
                    barcode="880000000001",
                    inbound_cycle=1,
                )
            )
            db.commit()

            rows = [
                self.row(
                    product_name=f"product {index:03d}",
                    barcode=f"9900000{index:05d}",
                    sku=f"NEW-{index:03d}",
                )
                for index in range(289)
            ]
            inventory_daily_sql = []

            def capture_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
                if "inventory_daily" in statement.lower():
                    inventory_daily_sql.append(statement)

            event.listen(db.get_bind(), "before_cursor_execute", capture_sql)
            started_at = time.perf_counter()
            result = services.import_product_master_excel(db, "3PL", self.upload_bytes(rows))
            elapsed = time.perf_counter() - started_at
            event.remove(db.get_bind(), "before_cursor_execute", capture_sql)

            self.assertTrue(result["ok"], result.get("message"))
            self.assertLess(elapsed, 30)
            self.assertEqual(inventory_daily_sql, [])
            self.assertEqual(db.scalar(select(func.count()).select_from(ThirdpartyProductMaster)), 289)
            self.assertEqual(result["summary"]["DB 재조회 마스터 수"], 289)
            daily_row = db.execute(select(InventoryDaily)).scalar_one()
            self.assertEqual(daily_row.product_code, "OLD-001")
            self.assertEqual(daily_row.inbound_cycle, 1)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
