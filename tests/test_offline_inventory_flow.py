from __future__ import annotations

from datetime import date
from io import BytesIO, StringIO
import unittest

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import InventoryDaily, InventoryInbound, InventoryOutputHistory, InventoryUploadHistory, OfflineProductMaster, ThirdpartyProductMaster
from backend import services


class OfflineInventoryFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def test_homecc_inbound_template_import_uses_filename_date_and_master_match(self) -> None:
        db = self.Session()
        try:
            db.add(
                OfflineProductMaster(
                    sku="HCC-2",
                    barcode="8800000000002",
                    product_name="로긴 욕실선반 사각-2단",
                    large_category="홈CC",
                    supplier="홈CC",
                    is_active="사용",
                )
            )
            db.commit()

            buffer = BytesIO()
            sheet1 = pd.DataFrame(
                [
                    ["홈CC 상품입고", None],
                    ["상품명", "수량"],
                    ["로긴 욕실선반 사각-2단", 2],
                ]
            )
            sheet2 = pd.DataFrame([["홈씨씨등록상품명", "바코드", "출고수량"]])
            with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
                sheet1.to_excel(writer, index=False, header=False, sheet_name="Sheet1")
                sheet2.to_excel(writer, index=False, header=False, sheet_name="Sheet2")

            result = services.import_inbound_excel(
                db,
                "오프라인",
                buffer.getvalue(),
                "홈CC입고내역서_260723.xlsx",
            )

            self.assertEqual(result["count"], 1)
            row = db.execute(select(InventoryInbound)).scalar_one()
            self.assertEqual(row.inbound_date, date(2026, 7, 23))
            self.assertEqual(row.product_code, "HCC-2")
            self.assertEqual(row.barcode, "8800000000002")
            self.assertEqual(row.product_name, "로긴 욕실선반 사각-2단")
            self.assertEqual(row.inbound_qty, 2)
            self.assertEqual(row.vendor, "홈CC")
            self.assertEqual(row.inbound_type, "홈CC 상품입고")
        finally:
            db.close()

    def test_import_inbound_excel_and_apply_stock_updates_daily_on_inbound_date(self) -> None:
        db = self.Session()
        try:
            db.add(
                OfflineProductMaster(
                    sku="IN-AUTO",
                    barcode="8800000000999",
                    product_name="Auto inbound product",
                    large_category="Offline",
                    supplier="Vendor",
                    min_stock=3,
                    is_active="사용",
                )
            )
            db.add(
                InventoryDaily(
                    source_type="오프라인",
                    work_date=date(2026, 9, 8),
                    product_code="IN-AUTO",
                    barcode="8800000000999",
                    product_name="Auto inbound product",
                    category="Offline",
                    supplier="Vendor",
                    current_stock=10,
                    available_stock=10,
                    safe_stock=3,
                    stock_status="정상",
                )
            )
            db.commit()

            buffer = BytesIO()
            inbound_df = pd.DataFrame(
                [
                    {
                        "입고일자": "2026-09-09",
                        "SKU": "IN-AUTO",
                        "바코드": "8800000000999",
                        "상품명": "Auto inbound product",
                        "수량": 4,
                    }
                ]
            )
            with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
                inbound_df.to_excel(writer, index=False)

            outcome = services.import_inbound_excel_and_apply_stock(db, "오프라인", buffer.getvalue(), "inbound.xlsx")

            self.assertEqual(outcome["count"], 1)
            self.assertEqual(outcome["stock_apply_count"], 1)
            inbound = db.execute(select(InventoryInbound)).scalar_one()
            self.assertTrue(inbound.is_applied)
            daily = db.execute(
                select(InventoryDaily).where(
                    InventoryDaily.source_type == "오프라인",
                    InventoryDaily.work_date == date(2026, 9, 9),
                    InventoryDaily.product_name == "Auto inbound product",
                )
            ).scalar_one()
            self.assertEqual(daily.current_stock, 14)
            self.assertEqual(daily.available_stock, 14)
            self.assertEqual(daily.inbound_qty, 4)
            self.assertEqual(daily.last_inbound_date, date(2026, 9, 9))
        finally:
            db.close()

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
            self.assertEqual(day3.current_stock, 120)
            self.assertEqual(day3.available_stock, 105)
            self.assertEqual(day3.outbound_qty, 15)

            carried_rows = services.master_based_inventory_rows(db, "오프라인", date(2026, 9, 4))
            carried = next(row for row in carried_rows if row["product_code"] == "OFF-A")
            self.assertEqual(carried["current_stock"], 120)
            self.assertEqual(carried["available_stock"], 120)
            self.assertEqual(carried["pending_outbound_qty"], 0)
            self.assertEqual(carried["last_inventory_update_date"], date(2026, 9, 3))
            self.assertTrue(carried["is_carried_inventory_snapshot"])

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

    def test_offline_outbound_apply_without_matched_rows_returns_error(self) -> None:
        db = self.Session()
        try:
            preview = {
                "ok": True,
                "total_rows": 1,
                "matched_count": 0,
                "unmatched_count": 1,
                "duplicate_count": 0,
                "error_count": 0,
                "preview_rows": [
                    {
                        "matched": False,
                        "product_code": "MISSING",
                        "product_name": "Missing product",
                        "outbound_qty": 3,
                        "status": "미매칭 상품",
                    }
                ],
            }

            applied = services.apply_offline_outbound_preview(db, preview, "tester")

            self.assertFalse(applied["ok"])
            self.assertEqual(applied["count"], 0)
            self.assertEqual(applied["total_rows"], 1)
            self.assertEqual(applied["unmatched_count"], 1)
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
                },
                {
                    "상품코드": "51",
                    "상품명": "",
                    "일자": "",
                    "매출수량": "",
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
            self.assertEqual(day3.current_stock, 10)
            self.assertEqual(day3.available_stock, 10)
            self.assertEqual(day3.outbound_qty, -1)

            rows = services.master_based_inventory_rows(db, "오프라인", date(2026, 8, 23))
            target = next(row for row in rows if row["product_code"] == "RET-1")
            self.assertEqual(target["return_qty"], 1)

            next_rows = services.master_based_inventory_rows(db, "오프라인", date(2026, 8, 24))
            next_target = next(row for row in next_rows if row["product_code"] == "RET-1")
            self.assertEqual(next_target["current_stock"], 10)
            self.assertEqual(next_target["available_stock"], 10)
            self.assertEqual(next_target["return_qty"], 0)
        finally:
            db.close()

    def test_offline_outbound_file_date_is_ignored(self) -> None:
        db = self.Session()
        try:
            product = OfflineProductMaster(
                sku="DATE-1",
                barcode="",
                product_name="Date ignored product",
                large_category="Offline",
                supplier="Vendor",
                min_stock=0,
                is_active="사용",
            )
            db.add(product)
            db.add(
                InventoryDaily(
                    source_type="오프라인",
                    work_date=date(2026, 9, 4),
                    product_code="DATE-1",
                    product_name="Date ignored product",
                    current_stock=10,
                    available_stock=10,
                    stock_status="정상",
                )
            )
            db.commit()

            outbound_df = pd.DataFrame(
                [
                    {
                        "상품코드": "DATE-1",
                        "상품명": "Date ignored product",
                        "일자": "20260820",
                        "매출수량": "3",
                    }
                ]
            )
            buffer = StringIO()
            outbound_df.to_csv(buffer, index=False)

            preview = services.prepare_offline_outbound_upload_preview(db, "오프라인", date(2026, 9, 5), buffer.getvalue().encode("utf-8-sig"), "salesList.csv")
            self.assertEqual(preview["preview_rows"][0]["work_date"], date(2026, 9, 5))

            applied = services.apply_offline_outbound_preview(db, preview, "tester")
            self.assertTrue(applied["ok"])
            self.assertEqual(applied["count"], 1)

            selected_day = db.execute(
                select(InventoryDaily).where(
                    InventoryDaily.source_type == "오프라인",
                    InventoryDaily.work_date == date(2026, 9, 5),
                    InventoryDaily.product_name == "Date ignored product",
                )
            ).scalar_one()
            self.assertEqual(selected_day.current_stock, 10)
            self.assertEqual(selected_day.available_stock, 7)

            file_day = db.execute(
                select(InventoryDaily).where(
                    InventoryDaily.source_type == "오프라인",
                    InventoryDaily.work_date == date(2026, 8, 20),
                    InventoryDaily.product_name == "Date ignored product",
                )
            ).scalar_one_or_none()
            self.assertIsNone(file_day)
        finally:
            db.close()

    def test_offline_outbound_can_make_available_stock_negative(self) -> None:
        offline = "\uc624\ud504\ub77c\uc778"
        db = self.Session()
        try:
            product = OfflineProductMaster(
                sku="NEG-1",
                barcode="",
                product_name="Negative stock product",
                large_category="Offline",
                supplier="Vendor",
                min_stock=0,
                is_active="\uc0ac\uc6a9",
            )
            db.add(product)
            db.add(
                InventoryDaily(
                    source_type=offline,
                    work_date=date(2026, 9, 4),
                    product_code="NEG-1",
                    product_name="Negative stock product",
                    current_stock=0,
                    available_stock=0,
                    stock_status="\uc815\uc0c1",
                )
            )
            db.commit()

            outbound_df = pd.DataFrame(
                [
                    {
                        "sku": "NEG-1",
                        "product_name": "Negative stock product",
                        "date": "2026-09-05",
                        "qty": "3",
                    }
                ]
            )
            buffer = StringIO()
            outbound_df.to_csv(buffer, index=False)

            preview = services.prepare_offline_outbound_upload_preview(
                db,
                offline,
                date(2026, 9, 5),
                buffer.getvalue().encode("utf-8-sig"),
                "outbound.csv",
            )
            self.assertEqual(preview["matched_count"], 1)

            applied = services.apply_offline_outbound_preview(db, preview, "tester")
            self.assertTrue(applied["ok"])
            self.assertEqual(applied["count"], 1)

            selected_day = db.execute(
                select(InventoryDaily).where(
                    InventoryDaily.source_type == offline,
                    InventoryDaily.work_date == date(2026, 9, 5),
                    InventoryDaily.product_name == "Negative stock product",
                )
            ).scalar_one()
            self.assertEqual(selected_day.current_stock, 0)
            self.assertEqual(selected_day.available_stock, -3)

            rows = services.master_based_inventory_rows(db, offline, date(2026, 9, 5))
            target = next(row for row in rows if row["product_code"] == "NEG-1")
            self.assertEqual(target["current_stock"], 0)
            self.assertEqual(target["available_stock"], -3)
        finally:
            db.close()

    def test_offline_stock_carries_forward_after_confirmed_outbound(self) -> None:
        offline = "\uc624\ud504\ub77c\uc778"
        db = self.Session()
        try:
            product = OfflineProductMaster(
                sku="CARRY-1",
                barcode="8800000000201",
                product_name="Carry forward product",
                large_category="Offline",
                supplier="Vendor",
                min_stock=0,
                is_active="\uc0ac\uc6a9",
            )
            db.add(product)
            db.add(
                InventoryDaily(
                    source_type=offline,
                    work_date=date(2026, 9, 8),
                    product_code="CARRY-1",
                    barcode="8800000000201",
                    product_name="Carry forward product",
                    current_stock=100,
                    available_stock=100,
                    stock_status="\uc815\uc0c1",
                )
            )
            db.commit()

            outbound_df = pd.DataFrame([{"sku": "CARRY-1", "product_name": "Carry forward product", "qty": "15"}])
            buffer = StringIO()
            outbound_df.to_csv(buffer, index=False)
            preview = services.prepare_offline_outbound_upload_preview(db, offline, date(2026, 9, 9), buffer.getvalue().encode("utf-8-sig"), "outbound.csv")

            applied = services.apply_offline_outbound_preview(db, preview, "tester")

            self.assertTrue(applied["ok"])
            day9_rows = services.master_based_inventory_rows(db, offline, date(2026, 9, 9))
            day9 = next(row for row in day9_rows if row["product_code"] == "CARRY-1")
            self.assertEqual(day9["current_stock"], 100)
            self.assertEqual(day9["available_stock"], 85)
            self.assertEqual(day9["pending_outbound_qty"], 15)
            day10_rows = services.master_based_inventory_rows(db, offline, date(2026, 9, 10))
            day10 = next(row for row in day10_rows if row["product_code"] == "CARRY-1")
            self.assertEqual(day10["current_stock"], 100)
            self.assertEqual(day10["available_stock"], 100)
            self.assertEqual(day10["pending_outbound_qty"], 0)
            self.assertTrue(day10["is_carried_inventory_snapshot"])
        finally:
            db.close()

    def test_offline_dashboard_uses_carried_inventory_snapshot(self) -> None:
        offline = "\uc624\ud504\ub77c\uc778"
        db = self.Session()
        try:
            db.add(
                OfflineProductMaster(
                    sku="DASH-CARRY-1",
                    barcode="8800000000401",
                    product_name="Dashboard carry product",
                    large_category="Offline",
                    supplier="Vendor",
                    min_stock=0,
                    is_active="\uc0ac\uc6a9",
                )
            )
            db.add(
                InventoryDaily(
                    source_type=offline,
                    work_date=date(2026, 9, 8),
                    product_code="DASH-CARRY-1",
                    barcode="8800000000401",
                    product_name="Dashboard carry product",
                    current_stock=37,
                    available_stock=37,
                    stock_status="\uc815\uc0c1",
                )
            )
            db.commit()

            summary = services.dashboard_summary(db, date(2026, 9, 9), offline)
            chart = services.dashboard_chart(db, date(2026, 9, 9), offline)

            self.assertEqual(summary["sku_count"], 1)
            self.assertEqual(summary["current_stock"], 37)
            self.assertEqual(summary["available_stock"], 37)
            self.assertIn({"label": offline, "value": 37}, chart["stock_by_source"])
            self.assertIn({"date": "2026-09-09", "value": 37}, chart["stock_trend"])
        finally:
            db.close()

    def test_offline_lookup_ignores_empty_today_placeholder_and_carries_previous_stock(self) -> None:
        offline = "\uc624\ud504\ub77c\uc778"
        db = self.Session()
        try:
            db.add(
                OfflineProductMaster(
                    sku="PLACEHOLDER-1",
                    barcode="8800000000501",
                    product_name="Placeholder carry product",
                    large_category="Offline",
                    supplier="Vendor",
                    min_stock=0,
                    is_active="\uc0ac\uc6a9",
                )
            )
            db.add(
                InventoryDaily(
                    source_type=offline,
                    work_date=date(2026, 9, 9),
                    product_code="PLACEHOLDER-1",
                    barcode="8800000000501",
                    product_name="Placeholder carry product",
                    current_stock=64,
                    available_stock=64,
                    stock_status="\uc815\uc0c1",
                )
            )
            db.add(
                InventoryDaily(
                    source_type=offline,
                    work_date=date(2026, 9, 14),
                    product_code="PLACEHOLDER-1",
                    barcode="8800000000501",
                    product_name="Placeholder carry product",
                    current_stock=0,
                    available_stock=0,
                    stock_status="\ud488\uc808",
                )
            )
            db.commit()

            rows = services.master_based_inventory_rows(db, offline, date(2026, 9, 14))
            target = next(row for row in rows if row["product_code"] == "PLACEHOLDER-1")

            self.assertEqual(target["current_stock"], 64)
            self.assertEqual(target["available_stock"], 64)
            self.assertEqual(target["last_inventory_update_date"], date(2026, 9, 9))
            self.assertTrue(target["is_carried_inventory_snapshot"])
        finally:
            db.close()

    def test_offline_lookup_carries_previous_stock_without_sku(self) -> None:
        offline = "\uc624\ud504\ub77c\uc778"
        db = self.Session()
        try:
            db.add(
                OfflineProductMaster(
                    sku="",
                    barcode="8800000000601",
                    product_name="Barcode only carry product",
                    large_category="Offline",
                    supplier="Vendor",
                    min_stock=0,
                    is_active="\uc0ac\uc6a9",
                )
            )
            db.add(
                InventoryDaily(
                    source_type=offline,
                    work_date=date(2026, 9, 9),
                    product_code="",
                    barcode="8800000000601",
                    product_name="Barcode only carry product",
                    current_stock=25,
                    available_stock=25,
                    stock_status="\uc815\uc0c1",
                )
            )
            db.commit()

            rows = services.master_based_inventory_rows(db, offline, date(2026, 9, 14))
            target = next(row for row in rows if row["barcode"] == "8800000000601")

            self.assertEqual(target["current_stock"], 25)
            self.assertEqual(target["available_stock"], 25)
            self.assertEqual(target["last_inventory_update_date"], date(2026, 9, 9))
            self.assertTrue(target["is_carried_inventory_snapshot"])
        finally:
            db.close()

    def test_offline_outbound_uses_previous_stock_when_today_row_is_empty_placeholder(self) -> None:
        offline = "\uc624\ud504\ub77c\uc778"
        db = self.Session()
        try:
            db.add(
                OfflineProductMaster(
                    sku="PLACEHOLDER-OUT-1",
                    barcode="8800000000502",
                    product_name="Placeholder outbound product",
                    large_category="Offline",
                    supplier="Vendor",
                    min_stock=0,
                    is_active="\uc0ac\uc6a9",
                )
            )
            db.add(
                InventoryDaily(
                    source_type=offline,
                    work_date=date(2026, 9, 9),
                    product_code="PLACEHOLDER-OUT-1",
                    barcode="8800000000502",
                    product_name="Placeholder outbound product",
                    current_stock=80,
                    available_stock=80,
                    stock_status="\uc815\uc0c1",
                )
            )
            db.add(
                InventoryDaily(
                    source_type=offline,
                    work_date=date(2026, 9, 14),
                    product_code="PLACEHOLDER-OUT-1",
                    barcode="8800000000502",
                    product_name="Placeholder outbound product",
                    current_stock=0,
                    available_stock=0,
                    stock_status="\ud488\uc808",
                )
            )
            db.commit()

            outbound_df = pd.DataFrame([{"sku": "PLACEHOLDER-OUT-1", "product_name": "Placeholder outbound product", "qty": "13"}])
            buffer = StringIO()
            outbound_df.to_csv(buffer, index=False)
            preview = services.prepare_offline_outbound_upload_preview(
                db,
                offline,
                date(2026, 9, 14),
                buffer.getvalue().encode("utf-8-sig"),
                "outbound.csv",
            )
            applied = services.apply_offline_outbound_preview(db, preview, "tester")

            self.assertTrue(applied["ok"])
            daily = db.execute(
                select(InventoryDaily).where(
                    InventoryDaily.source_type == offline,
                    InventoryDaily.work_date == date(2026, 9, 14),
                    InventoryDaily.product_name == "Placeholder outbound product",
                )
            ).scalar_one()
            self.assertEqual(daily.current_stock, 80)
            self.assertEqual(daily.available_stock, 67)
            self.assertEqual(daily.outbound_qty, 13)
        finally:
            db.close()

    def test_threepl_pending_outbound_does_not_recalculate_uploaded_available_stock(self) -> None:
        db = self.Session()
        try:
            db.add(
                ThirdpartyProductMaster(
                    sku="PEND-1",
                    barcode="8800000000301",
                    product_name="Pending outbound product",
                    large_category="3PL",
                    supplier="Vendor",
                    min_stock=10,
                    is_active="\uc0ac\uc6a9",
                )
            )
            db.add(
                ThirdpartyProductMaster(
                    sku="PEND-2",
                    barcode="8800000000302",
                    product_name="Small pending outbound product",
                    large_category="3PL",
                    supplier="Vendor",
                    min_stock=0,
                    is_active="\uc0ac\uc6a9",
                )
            )
            db.add(
                InventoryDaily(
                    source_type="3PL",
                    work_date=date(2026, 9, 9),
                    product_code="PEND-1",
                    barcode="8800000000301",
                    product_name="Pending outbound product",
                    current_stock=100,
                    available_stock=100,
                    outbound_qty=12,
                    safe_stock=10,
                    stock_status="\uc815\uc0c1",
                )
            )
            small_daily = InventoryDaily(
                source_type="3PL",
                work_date=date(2026, 9, 9),
                product_code="PEND-2",
                barcode="8800000000302",
                product_name="Small pending outbound product",
                current_stock=4,
                available_stock=4,
                outbound_qty=2,
                safe_stock=0,
                stock_status="\uc815\uc0c1",
            )
            db.add(small_daily)
            db.commit()

            rows = services.master_based_inventory_rows(db, "3PL", date(2026, 9, 9))
            target = next(row for row in rows if row["product_code"] == "PEND-1")
            small_target = next(row for row in rows if row["product_code"] == "PEND-2")

            self.assertEqual(target["current_stock"], 100)
            self.assertEqual(target["pending_outbound_qty"], 12)
            self.assertEqual(target["available_stock"], 100)
            self.assertEqual(small_target["current_stock"], 4)
            self.assertEqual(small_target["pending_outbound_qty"], 2)
            self.assertEqual(small_target["available_stock"], 4)
            self.assertEqual(services.daily_to_dict(small_daily)["available_stock"], 4)
        finally:
            db.close()

    def test_stock_upload_history_mode_is_shortened_for_bulk_adjustment(self) -> None:
        offline = "\uc624\ud504\ub77c\uc778"
        db = self.Session()
        try:
            db.add(
                OfflineProductMaster(
                    sku="BULK-1",
                    barcode="8800000000101",
                    product_name="Bulk adjusted product",
                    large_category="Offline",
                    supplier="Vendor",
                    min_stock=0,
                    is_active="\uc0ac\uc6a9",
                )
            )
            db.commit()

            preview = {
                "file_name": "bulk.xlsx",
                "upload_mode": "excel_bulk_stock_adjustment",
                "total_rows": 1,
                "matched_count": 1,
                "failed_count": 0,
                "duplicate_count": 0,
                "zeroed_count": 0,
                "preview_rows": [
                    {
                        "row_no": 1,
                        "matched": True,
                        "product_code": "BULK-1",
                        "barcode": "8800000000101",
                        "product_name": "Bulk adjusted product",
                        "category": "Offline",
                        "new_stock": 42,
                        "new_available_stock": 42,
                    }
                ],
            }

            result = services.apply_stock_upload_preview(db, offline, date(2026, 9, 9), preview, "tester")

            self.assertTrue(result["ok"])
            history = db.execute(select(InventoryUploadHistory)).scalar_one()
            self.assertEqual(history.upload_mode, "excel_bulk_adjust")
            self.assertLessEqual(len(history.upload_mode), 20)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
