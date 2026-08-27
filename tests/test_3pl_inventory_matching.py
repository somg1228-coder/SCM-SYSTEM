from __future__ import annotations

import csv
from datetime import date, timedelta
from io import BytesIO, StringIO
import unittest

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import InventoryDaily, ThirdpartyProductMaster
from backend import services


class ThreeplInventoryMatchingTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def test_erp_stock_upload_matches_unique_names_and_saves_daily_by_sku(self) -> None:
        work_date = date(2026, 8, 26)
        db = self.Session()
        try:
            masters = [
                ("SKU-E", "8809722100942", "식기건조대 액세서리 E자형 다용도걸이(15mm)"),
                ("SKU-S", "8809722100001", "식기건조대 액세서리 S자고리"),
                ("SKU-U", "8809722100002", "식기건조대 액세서리 U자형 다용도걸이(7mm)"),
                ("SKU-BOARD", "8809722100003", "식기건조대 액세서리 도마행주걸이"),
                ("SKU-BASIC-BASKET", "8809722100004", "식기건조대 액세서리 바스켓(기본,7mm)"),
                ("SKU-ATTACH-BASKET", "8809722100005", "식기건조대 액세서리 바스켓(부착식용,15mm)"),
                ("SKU-CAP-BASIC", "8809722100006", "식기건조대 액세서리 실리콘캡(기본)"),
                ("SKU-CAP-MINI", "8809722100007", "식기건조대 액세서리 실리콘캡(미니)"),
                ("SKU-PLATE", "8809722100008", "식기건조대 액세서리 접시꽂이"),
                ("SKU-PAN", "8809722100164", "로트 / [로긴] 후라이팬 정리대"),
                ("SKU-SHORT", "1747", "짧은 코드 상품"),
            ]
            for sort_order, (sku, barcode, name) in enumerate(masters, start=1):
                db.add(
                    ThirdpartyProductMaster(
                        sku=sku,
                        barcode=barcode,
                        product_name=name,
                        large_category="3PL",
                        supplier="테스트",
                        sort_order=sort_order,
                        is_active="사용",
                    )
                )
            db.commit()

            rows = [
                ("", "8809722102922", "식기건조대 액세서리 E자형 다용도걸이(15mm)", 118, 0, 0),
                ("", "8809722102923", "식기건조대   액세서리 S자고리", 71, 2, 1),
                ("", "8809722102924", "식기건조대 액세서리 U자형 다용도걸이(7mm)", 60, 1, 0),
                ("", "8809722102925", "식기건조대 액세서리 도마행주걸이", 74, 2, 1),
                ("", "8809722102926", "식기건조대 액세서리 바스켓(기본,7mm)", 305, 1, 1),
                ("", "8809722102927", "식기건조대 액세서리 바스켓(부착식용,15mm)", 312, 1, 0),
                ("", "8809722102928", "식기건조대 액세서리 실리콘캡(기본)", 135, 6, 1),
                ("", "8809722102929", "식기건조대 액세서리 실리콘캡(미니)", 438, 0, 0),
                ("", "8809722102930", "식기건조대 액세서리 접시꽂이", 78, 3, 1),
                ("", "8809722100164", "로트 / [로긴] 후라이팬 정리대 세로형", 60, 1, 0),
                ("", "01747", "짧은 코드 상품", 11, 0, 1),
            ]
            csv_buffer = StringIO()
            writer = csv.writer(csv_buffer)
            writer.writerow(["상품코드", "바코드", "상품명", "가용재고", "송장", "접수"])
            writer.writerows(rows)
            result = services.apply_erp_stock_upload_file(
                db,
                "3PL",
                work_date,
                csv_buffer.getvalue().encode("utf-8-sig"),
                file_name="erp-stock.csv",
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["matched_count"], 11)
            self.assertEqual(result["unmatched_count"], 0)

            expected = {
                "SKU-E": (118, 0),
                "SKU-S": (71, 3),
                "SKU-U": (60, 1),
                "SKU-BOARD": (74, 3),
                "SKU-BASIC-BASKET": (305, 2),
                "SKU-ATTACH-BASKET": (312, 1),
                "SKU-CAP-BASIC": (135, 7),
                "SKU-CAP-MINI": (438, 0),
                "SKU-PLATE": (78, 4),
                "SKU-PAN": (60, 1),
                "SKU-SHORT": (11, 1),
            }
            saved_rows = {
                row.product_code: row
                for row in db.execute(
                    select(InventoryDaily).where(
                        InventoryDaily.source_type == "3PL",
                        InventoryDaily.work_date == work_date,
                    )
                ).scalars()
            }
            for sku, (available_stock, outbound_qty) in expected.items():
                self.assertIn(sku, saved_rows)
                self.assertEqual(saved_rows[sku].available_stock, available_stock)
                self.assertEqual(saved_rows[sku].current_stock, available_stock)
                self.assertEqual(saved_rows[sku].outbound_qty, outbound_qty)

            corrected = db.execute(select(ThirdpartyProductMaster).where(ThirdpartyProductMaster.sku == "SKU-E")).scalar_one()
            self.assertEqual(corrected.barcode, "8809722102922")
            short_code = db.execute(select(ThirdpartyProductMaster).where(ThirdpartyProductMaster.sku == "SKU-SHORT")).scalar_one()
            self.assertEqual(short_code.barcode, "01747")

            inventory_rows = services.master_based_inventory_rows(db, "3PL", work_date)
            by_sku = {row["product_code"]: row for row in inventory_rows}
            self.assertEqual(by_sku["SKU-CAP-BASIC"]["available_stock"], 135)
            self.assertEqual(by_sku["SKU-CAP-BASIC"]["pending_outbound_qty"], 7)
            self.assertEqual(by_sku["SKU-PAN"]["available_stock"], 60)
            self.assertEqual(by_sku["SKU-PAN"]["pending_outbound_qty"], 1)
        finally:
            db.close()

    def test_duplicate_master_name_matches_preferred_master_by_name_only(self) -> None:
        work_date = date(2026, 8, 26)
        db = self.Session()
        try:
            db.add_all(
                [
                    ThirdpartyProductMaster(sku="DUP-1", barcode="1001", product_name="중복 상품", is_active="사용"),
                    ThirdpartyProductMaster(sku="DUP-2", barcode="1002", product_name="중복   상품", is_active="사용"),
                ]
            )
            db.commit()

            csv_text = "상품명,가용재고,송장,접수\n중복 상품,5,1,1"
            result = services.apply_erp_stock_upload_file(db, "3PL", work_date, csv_text.encode("utf-8-sig"), "dup.csv")

            self.assertTrue(result["ok"])
            self.assertEqual(result["matched_count"], 1)
            self.assertEqual(result["unmatched_count"], 0)
            daily_rows = db.execute(select(InventoryDaily).where(InventoryDaily.source_type == "3PL")).scalars().all()
            self.assertEqual(len(daily_rows), 1)
            self.assertEqual(daily_rows[0].product_code, "DUP-2")
        finally:
            db.close()

    def test_outbound_average_uses_sku_when_name_or_barcode_changed(self) -> None:
        work_date = date(2026, 8, 26)
        db = self.Session()
        try:
            product = ThirdpartyProductMaster(
                sku="SKU-HISTORY",
                barcode="8809722102922",
                product_name="최신 상품명",
                is_active="사용",
            )
            db.add(product)
            for offset, outbound in ((1, 7), (2, 3), (5, 5)):
                db.add(
                    InventoryDaily(
                        source_type="3PL",
                        work_date=work_date - timedelta(days=offset),
                        product_code="SKU-HISTORY",
                        product_name="예전 상품명",
                        barcode="8809722100942",
                        current_stock=100,
                        available_stock=100,
                        outbound_qty=outbound,
                    )
                )
            db.commit()

            averages = services.recent_outbound_average_by_product(db, "3PL", work_date, [product], business_day_count=5)

            self.assertEqual(averages["SKU-HISTORY"], 3.0)
        finally:
            db.close()

    def test_threepl_master_excel_replaces_existing_master_by_uploaded_file(self) -> None:
        work_date = date(2026, 8, 26)
        db = self.Session()
        try:
            db.add_all(
                [
                    ThirdpartyProductMaster(sku="01467", barcode="8809722101727", product_name="로켓몰린/배수구 커버 거치대(사은품)+물린 배수구망 신형", is_active="사용"),
                    ThirdpartyProductMaster(sku="1467", barcode="8809722101727", product_name="로켓몰린/배수구 커버 거치대(사은품)+물린 배수구망 신형", is_active="사용"),
                    ThirdpartyProductMaster(sku="OLD", barcode="999", product_name="이전 파일에만 있던 상품", is_active="사용"),
                ]
            )
            db.add(
                InventoryDaily(
                    source_type="3PL",
                    work_date=work_date,
                    product_code="OLD",
                    product_name="이전 파일에만 있던 상품",
                    barcode="999",
                    current_stock=10,
                    available_stock=10,
                )
            )
            db.commit()

            upload_df = pd.DataFrame(
                [
                    {
                        "SKU": "1467",
                        "카테고리": "주방",
                        "바코드": "8809722101727",
                        "상품명": "로켓몰린/배수구 커버 거치대(사은품)+물린 배수구망 신형",
                        "업체명": "로켓",
                        "박스/파렛트 단위": "0",
                        "담당자": "",
                        "리드타임": "0",
                    },
                    {
                        "SKU": "2000",
                        "카테고리": "주방",
                        "바코드": "8800000002000",
                        "상품명": "새 파일 신규 상품",
                        "업체명": "신규",
                        "박스/파렛트 단위": "0",
                        "담당자": "",
                        "리드타임": "0",
                    },
                ]
            )
            buffer = BytesIO()
            upload_df.to_excel(buffer, index=False, sheet_name="3PL 마스터")

            preview = services.prepare_product_master_shared_import_preview(db, "3PL", buffer.getvalue())
            self.assertTrue(preview["ok"])
            self.assertEqual(preview["summary"]["기존 마스터"], 3)
            self.assertEqual(preview["summary"]["새 마스터 파일"], 2)
            self.assertEqual(preview["summary"]["삭제 예정"], 2)

            result = services.apply_product_master_shared_import_preview(db, "3PL", preview, sync_inventory=False)
            self.assertTrue(result["ok"])
            self.assertEqual(result["summary"]["기존 마스터"], 3)
            self.assertEqual(result["summary"]["새 마스터 파일"], 2)
            self.assertEqual(result["summary"]["삭제"], 2)
            self.assertEqual(result["summary"]["최종 마스터"], 2)

            masters = db.execute(select(ThirdpartyProductMaster).order_by(ThirdpartyProductMaster.sku)).scalars().all()
            self.assertEqual([row.sku for row in masters], ["1467", "2000"])
            self.assertNotIn("01467", [row.sku for row in masters])
            self.assertEqual(db.execute(select(InventoryDaily)).scalars().all()[0].product_code, "OLD")
        finally:
            db.close()

    def test_threepl_master_replacement_keeps_existing_master_when_file_validation_fails(self) -> None:
        db = self.Session()
        try:
            db.add(ThirdpartyProductMaster(sku="OLD", barcode="999", product_name="기존 상품", is_active="사용"))
            db.commit()

            upload_df = pd.DataFrame(
                [
                    {
                        "SKU": "2000",
                        "카테고리": "주방",
                        "바코드": "8800000002000",
                        "상품명": "새 파일 신규 상품",
                        "업체명": "신규",
                        "박스/파렛트 단위": "0",
                        "담당자": "",
                        "리드타임": "0",
                    },
                    {
                        "SKU": "3000",
                        "카테고리": "주방",
                        "바코드": "8800000003000",
                        "상품명": "",
                        "업체명": "오류",
                        "박스/파렛트 단위": "0",
                        "담당자": "",
                        "리드타임": "0",
                    },
                ]
            )
            buffer = BytesIO()
            upload_df.to_excel(buffer, index=False, sheet_name="3PL 마스터")

            result = services.import_product_master_excel(db, "3PL", buffer.getvalue())
            self.assertFalse(result["ok"])

            masters = db.execute(select(ThirdpartyProductMaster)).scalars().all()
            self.assertEqual(len(masters), 1)
            self.assertEqual(masters[0].sku, "OLD")
        finally:
            db.close()

    def test_inventory_status_uses_available_stock_against_pending_outbound(self) -> None:
        self.assertEqual(
            services.inventory_stock_status_for_snapshot(
                True,
                available_stock=30,
                current_stock=30,
                safe_stock=50,
                pending_outbound_qty=5,
            ),
            "주의",
        )
        self.assertEqual(
            services.inventory_stock_status_for_snapshot(
                True,
                available_stock=4,
                current_stock=4,
                safe_stock=50,
                pending_outbound_qty=5,
            ),
            "부족",
        )

    def test_order_needed_days_does_not_immediate_order_only_because_safe_stock_is_high(self) -> None:
        self.assertGreater(
            services.order_needed_days(
                current_stock=30,
                safe_stock=50,
                avg_daily_outbound=5,
                lead_time_days=2,
                pending_outbound_qty=5,
            ),
            0,
        )
        self.assertEqual(
            services.order_needed_days(
                current_stock=4,
                safe_stock=50,
                avg_daily_outbound=5,
                lead_time_days=2,
                pending_outbound_qty=5,
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
