from __future__ import annotations

import csv
from datetime import date, timedelta
from io import BytesIO, StringIO
import unittest

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import InventoryDaily, OfflineProductMaster, ThirdpartyProductMaster, WarehouseProductMaster
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
                ("SKU-E", "8809722100942", "?앷린嫄댁“? ?≪꽭?쒕━ E?먰삎 ?ㅼ슜?꾧구??15mm)"),
                ("SKU-S", "8809722100001", "?앷린嫄댁“? ?≪꽭?쒕━ S?먭퀬由?),
                ("SKU-U", "8809722100002", "?앷린嫄댁“? ?≪꽭?쒕━ U?먰삎 ?ㅼ슜?꾧구??7mm)"),
                ("SKU-BOARD", "8809722100003", "?앷린嫄댁“? ?≪꽭?쒕━ ?꾨쭏?됱＜嫄몄씠"),
                ("SKU-BASIC-BASKET", "8809722100004", "?앷린嫄댁“? ?≪꽭?쒕━ 諛붿뒪耳?湲곕낯,7mm)"),
                ("SKU-ATTACH-BASKET", "8809722100005", "?앷린嫄댁“? ?≪꽭?쒕━ 諛붿뒪耳?遺李⑹떇??15mm)"),
                ("SKU-CAP-BASIC", "8809722100006", "?앷린嫄댁“? ?≪꽭?쒕━ ?ㅻ━肄섏벙(湲곕낯)"),
                ("SKU-CAP-MINI", "8809722100007", "?앷린嫄댁“? ?≪꽭?쒕━ ?ㅻ━肄섏벙(誘몃땲)"),
                ("SKU-PLATE", "8809722100008", "?앷린嫄댁“? ?≪꽭?쒕━ ?묒떆苑귥씠"),
                ("SKU-PAN", "8809722100164", "濡쒗듃 / [濡쒓릿] ?꾨씪?댄뙩 ?뺣━?"),
                ("SKU-SHORT", "1747", "吏㏃? 肄붾뱶 ?곹뭹"),
            ]
            for sort_order, (sku, barcode, name) in enumerate(masters, start=1):
                db.add(
                    ThirdpartyProductMaster(
                        sku=sku,
                        barcode=barcode,
                        product_name=name,
                        large_category="3PL",
                        supplier="?뚯뒪??,
                        sort_order=sort_order,
                        is_active="?ъ슜",
                    )
                )
            db.commit()

            rows = [
                ("", "8809722102922", "?앷린嫄댁“? ?≪꽭?쒕━ E?먰삎 ?ㅼ슜?꾧구??15mm)", 118, 0, 0),
                ("", "8809722102923", "?앷린嫄댁“?   ?≪꽭?쒕━ S?먭퀬由?, 71, 2, 1),
                ("", "8809722102924", "?앷린嫄댁“? ?≪꽭?쒕━ U?먰삎 ?ㅼ슜?꾧구??7mm)", 60, 1, 0),
                ("", "8809722102925", "?앷린嫄댁“? ?≪꽭?쒕━ ?꾨쭏?됱＜嫄몄씠", 74, 2, 1),
                ("", "8809722102926", "?앷린嫄댁“? ?≪꽭?쒕━ 諛붿뒪耳?湲곕낯,7mm)", 305, 1, 1),
                ("", "8809722102927", "?앷린嫄댁“? ?≪꽭?쒕━ 諛붿뒪耳?遺李⑹떇??15mm)", 312, 1, 0),
                ("", "8809722102928", "?앷린嫄댁“? ?≪꽭?쒕━ ?ㅻ━肄섏벙(湲곕낯)", 135, 6, 1),
                ("", "8809722102929", "?앷린嫄댁“? ?≪꽭?쒕━ ?ㅻ━肄섏벙(誘몃땲)", 438, 0, 0),
                ("", "8809722102930", "?앷린嫄댁“? ?≪꽭?쒕━ ?묒떆苑귥씠", 78, 3, 1),
                ("", "8809722100164", "濡쒗듃 / [濡쒓릿] ?꾨씪?댄뙩 ?뺣━? ?몃줈??, 60, 1, 0),
                ("", "01747", "吏㏃? 肄붾뱶 ?곹뭹", 11, 0, 1),
            ]
            csv_buffer = StringIO()
            writer = csv.writer(csv_buffer)
            writer.writerow(["?곹뭹肄붾뱶", "諛붿퐫??, "?곹뭹紐?, "媛?⑹옱怨?, "?≪옣", "?묒닔"])
            writer.writerows(rows)
            result = services.apply_erp_stock_upload_file(
                db,
                "3PL",
                work_date,
                csv_buffer.getvalue().encode("utf-8-sig"),
                file_name="erp-stock.csv",
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["matched_count"], 9)
            self.assertEqual(result["unmatched_count"], 2)

            expected = {
                "SKU-E": (118, 0),
                "SKU-U": (60, 1),
                "SKU-BOARD": (74, 3),
                "SKU-BASIC-BASKET": (305, 2),
                "SKU-ATTACH-BASKET": (312, 1),
                "SKU-CAP-BASIC": (135, 7),
                "SKU-CAP-MINI": (438, 0),
                "SKU-PLATE": (78, 4),
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

            inventory_rows = services.master_based_inventory_rows(db, "3PL", work_date)
            by_sku = {row["product_code"]: row for row in inventory_rows}
            self.assertEqual(by_sku["SKU-CAP-BASIC"]["available_stock"], 135)
            self.assertEqual(by_sku["SKU-CAP-BASIC"]["pending_outbound_qty"], 7)
        finally:
            db.close()

    def test_inventory_matching_uses_exact_product_name_for_all_inventory_sources(self) -> None:
        cases = [
            ("3PL", ThirdpartyProductMaster),
            ("?ㅽ봽?쇱씤", OfflineProductMaster),
            ("李쎄퀬", WarehouseProductMaster),
        ]
        for source_type, model in cases:
            with self.subTest(source_type=source_type):
                product = model(sku=f"{source_type}-SKU", barcode="SAME-BARCODE", product_name="Exact Product")
                maps = services.product_lookup_maps([product])

                self.assertIsNone(
                    services.match_product_from_maps(
                        "",
                        "SAME-BARCODE",
                        "Different Product",
                        *maps,
                        source_type=source_type,
                    )
                )
                self.assertIs(
                    services.match_product_from_maps(
                        "",
                        "DIFFERENT-BARCODE",
                        "Exact Product",
                        *maps,
                        source_type=source_type,
                    ),
                    product,
                )

    def test_duplicate_master_name_matches_preferred_master_by_name_only(self) -> None:
        work_date = date(2026, 8, 26)
        db = self.Session()
        try:
            db.add_all(
                [
                    ThirdpartyProductMaster(sku="DUP-1", barcode="1001", product_name="以묐났 ?곹뭹", is_active="?ъ슜"),
                    ThirdpartyProductMaster(sku="DUP-2", barcode="1002", product_name="以묐났   ?곹뭹", is_active="?ъ슜"),
                ]
            )
            db.commit()

            csv_text = "?곹뭹紐?媛?⑹옱怨??≪옣,?묒닔\n以묐났 ?곹뭹,5,1,1"
            result = services.apply_erp_stock_upload_file(db, "3PL", work_date, csv_text.encode("utf-8-sig"), "dup.csv")

            self.assertTrue(result["ok"])
            self.assertEqual(result["matched_count"], 1)
            self.assertEqual(result["unmatched_count"], 0)
            daily_rows = db.execute(select(InventoryDaily).where(InventoryDaily.source_type == "3PL")).scalars().all()
            self.assertEqual(len(daily_rows), 1)
            self.assertEqual(daily_rows[0].product_code, "DUP-1")
        finally:
            db.close()

    def test_outbound_average_uses_sku_when_name_or_barcode_changed(self) -> None:
        work_date = date(2026, 8, 26)
        db = self.Session()
        try:
            product = ThirdpartyProductMaster(
                sku="SKU-HISTORY",
                barcode="8809722102922",
                product_name="理쒖떊 ?곹뭹紐?,
                is_active="?ъ슜",
            )
            db.add(product)
            for offset, outbound in ((1, 7), (2, 3), (5, 5)):
                db.add(
                    InventoryDaily(
                        source_type="3PL",
                        work_date=work_date - timedelta(days=offset),
                        product_code="SKU-HISTORY",
                        product_name="?덉쟾 ?곹뭹紐?,
                        barcode="8809722100942",
                        current_stock=100,
                        available_stock=100,
                        outbound_qty=outbound,
                    )
                )
            db.commit()

            averages = services.recent_outbound_average_by_product(db, "3PL", work_date, [product], business_day_count=5)

            self.assertNotIn("SKU-HISTORY", averages)
        finally:
            db.close()

    def test_threepl_master_excel_replaces_existing_master_by_uploaded_file(self) -> None:
        work_date = date(2026, 8, 26)
        db = self.Session()
        try:
            db.add_all(
                [
                    ThirdpartyProductMaster(sku="01467", barcode="8809722101727", product_name="怨쇨굅 ?숈씪諛붿퐫??蹂꾨룄 ?곹뭹紐?, is_active="?ъ슜"),
                    ThirdpartyProductMaster(sku="1467", barcode="8809722101727", product_name="濡쒖폆紐곕┛/諛곗닔援?而ㅻ쾭 嫄곗튂?(?ъ???+臾쇰┛ 諛곗닔援щ쭩 ?좏삎", is_active="?ъ슜"),
                    ThirdpartyProductMaster(sku="OLD", barcode="999", product_name="?댁쟾 ?뚯씪?먮쭔 ?덈뜕 ?곹뭹", is_active="?ъ슜"),
                ]
            )
            db.add(
                InventoryDaily(
                    source_type="3PL",
                    work_date=work_date,
                    product_code="OLD",
                    product_name="?댁쟾 ?뚯씪?먮쭔 ?덈뜕 ?곹뭹",
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
                        "移댄뀒怨좊━": "二쇰갑",
                        "諛붿퐫??: "8809722101727",
                        "?곹뭹紐?: "濡쒖폆紐곕┛/諛곗닔援?而ㅻ쾭 嫄곗튂?(?ъ???+臾쇰┛ 諛곗닔援щ쭩 ?좏삎",
                        "?낆껜紐?: "濡쒖폆",
                        "諛뺤뒪/?뚮젢???⑥쐞": "0",
                        "?대떦??: "",
                        "由щ뱶???: "0",
                    },
                    {
                        "SKU": "2000",
                        "移댄뀒怨좊━": "二쇰갑",
                        "諛붿퐫??: "8800000002000",
                        "?곹뭹紐?: "???뚯씪 ?좉퇋 ?곹뭹",
                        "?낆껜紐?: "?좉퇋",
                        "諛뺤뒪/?뚮젢???⑥쐞": "0",
                        "?대떦??: "",
                        "由щ뱶???: "0",
                    },
                ]
            )
            buffer = BytesIO()
            upload_df.to_excel(buffer, index=False, sheet_name="3PL 留덉뒪??)

            preview = services.prepare_product_master_shared_import_preview(db, "3PL", buffer.getvalue())
            self.assertTrue(preview["ok"])
            self.assertEqual(preview["summary"]["湲곗〈 留덉뒪??], 3)
            self.assertEqual(preview["summary"]["??留덉뒪???뚯씪"], 2)
            self.assertEqual(preview["summary"]["??젣 ?덉젙"], 3)

            result = services.apply_product_master_shared_import_preview(db, "3PL", preview, sync_inventory=False)
            self.assertTrue(result["ok"])
            self.assertEqual(result["summary"]["湲곗〈 留덉뒪??], 3)
            self.assertEqual(result["summary"]["??留덉뒪???뚯씪"], 2)
            self.assertEqual(result["summary"]["??젣"], 3)
            self.assertEqual(result["summary"]["理쒖쥌 留덉뒪??], 2)

            masters = db.execute(select(ThirdpartyProductMaster).order_by(ThirdpartyProductMaster.sku)).scalars().all()
            self.assertEqual([row.sku for row in masters], ["1467", "2000"])
            self.assertNotIn("01467", [row.sku for row in masters])
            self.assertEqual(db.execute(select(InventoryDaily)).scalars().all()[0].product_code, "OLD")
        finally:
            db.close()

    def test_threepl_full_name_variants_are_not_omitted_from_inventory_output(self) -> None:
        work_date = date(2026, 8, 26)
        db = self.Session()
        try:
            db.add_all(
                [
                    ThirdpartyProductMaster(
                        sku="01467",
                        barcode="8809722101727",
                        product_name="vertical fullset",
                        sort_order=1,
                    ),
                    ThirdpartyProductMaster(
                        sku="1467",
                        barcode="8809722101727",
                        product_name="vertical fullset gift excluded",
                        sort_order=2,
                    ),
                ]
            )
            db.add(
                InventoryDaily(
                    source_type="3PL",
                    work_date=work_date,
                    product_code="1467",
                    product_name="vertical fullset gift excluded",
                    barcode="8809722101727",
                    current_stock=30,
                    available_stock=30,
                    outbound_qty=2,
                )
            )
            db.commit()

            rows = services.master_based_inventory_rows(db, "3PL", work_date)
            by_name = {row["product_name"]: row for row in rows}

            self.assertEqual(len(rows), 2)
            self.assertIn("vertical fullset", by_name)
            self.assertIn("vertical fullset gift excluded", by_name)
            self.assertEqual(by_name["vertical fullset"]["available_stock"], 0)
            self.assertEqual(by_name["vertical fullset gift excluded"]["available_stock"], 30)
            self.assertEqual(by_name["vertical fullset gift excluded"]["pending_outbound_qty"], 2)
        finally:
            db.close()

    def test_threepl_file_identity_uses_full_product_name_with_normalized_sku(self) -> None:
        base = {
            "sku": "01467",
            "barcode": "8809722101727",
            "product_name": "vertical fullset",
        }
        same_product = {
            "sku": "1467",
            "barcode": "8809722101727",
            "product_name": "vertical fullset",
        }
        gift_excluded = {
            "sku": "1467",
            "barcode": "8809722101727",
            "product_name": "vertical fullset gift excluded",
        }

        self.assertEqual(services.threepl_master_file_identity(base), services.threepl_master_file_identity(same_product))
        self.assertNotEqual(services.threepl_master_file_identity(base), services.threepl_master_file_identity(gift_excluded))

    def test_threepl_master_replacement_keeps_existing_master_when_file_validation_fails(self) -> None:
        db = self.Session()
        try:
            db.add(ThirdpartyProductMaster(sku="OLD", barcode="999", product_name="湲곗〈 ?곹뭹", is_active="?ъ슜"))
            db.commit()

            upload_df = pd.DataFrame(
                [
                    {
                        "SKU": "2000",
                        "移댄뀒怨좊━": "二쇰갑",
                        "諛붿퐫??: "8800000002000",
                        "?곹뭹紐?: "???뚯씪 ?좉퇋 ?곹뭹",
                        "?낆껜紐?: "?좉퇋",
                        "諛뺤뒪/?뚮젢???⑥쐞": "0",
                        "?대떦??: "",
                        "由щ뱶???: "0",
                    },
                    {
                        "SKU": "3000",
                        "移댄뀒怨좊━": "二쇰갑",
                        "諛붿퐫??: "8800000003000",
                        "?곹뭹紐?: "",
                        "?낆껜紐?: "?ㅻ쪟",
                        "諛뺤뒪/?뚮젢???⑥쐞": "0",
                        "?대떦??: "",
                        "由щ뱶???: "0",
                    },
                ]
            )
            buffer = BytesIO()
            upload_df.to_excel(buffer, index=False, sheet_name="3PL 留덉뒪??)

            result = services.import_product_master_excel(db, "3PL", buffer.getvalue())
            self.assertFalse(result["ok"])

            masters = db.execute(select(ThirdpartyProductMaster)).scalars().all()
            self.assertEqual(len(masters), 1)
            self.assertEqual(masters[0].sku, "OLD")
        finally:
            db.close()

    def test_threepl_master_replacement_uses_last_uploaded_duplicate_sku_row(self) -> None:
        db = self.Session()
        try:
            db.add(ThirdpartyProductMaster(sku="OLD", barcode="999", product_name="湲곗〈 ?곹뭹", is_active="?ъ슜"))
            db.commit()

            upload_df = pd.DataFrame(
                [
                    {
                        "SKU": "DUP",
                        "移댄뀒怨좊━": "??젣????,
                        "諛붿퐫??: "111",
                        "?곹뭹紐?: "癒쇱? ?섏삩 ?곹뭹",
                        "?낆껜紐?: "?댁쟾",
                        "諛뺤뒪/?뚮젢???⑥쐞": "0",
                        "?대떦??: "",
                        "由щ뱶???: "0",
                    },
                    {
                        "SKU": "DUP",
                        "移댄뀒怨좊━": "理쒖쥌 ??,
                        "諛붿퐫??: "222",
                        "?곹뭹紐?: "留덉?留??곹뭹",
                        "?낆껜紐?: "理쒖쥌",
                        "諛뺤뒪/?뚮젢???⑥쐞": "0",
                        "?대떦??: "",
                        "由щ뱶???: "0",
                    },
                ]
            )
            buffer = BytesIO()
            upload_df.to_excel(buffer, index=False, sheet_name="3PL 留덉뒪??)

            result = services.import_product_master_excel(db, "3PL", buffer.getvalue())
            self.assertFalse(result["ok"])
            self.assertIn("반영 가능한", result.get("message", ""))

            masters = db.execute(select(ThirdpartyProductMaster)).scalars().all()
            self.assertEqual(len(masters), 1)
            self.assertEqual(masters[0].sku, "OLD")
        finally:
            db.close()

    def test_offline_master_excel_replaces_existing_master_by_uploaded_file(self) -> None:
        db = self.Session()
        try:
            db.add_all(
                [
                    OfflineProductMaster(sku="OLD-1", barcode="111", product_name="湲곗〈 ?ㅽ봽?쇱씤 1", is_active="?ъ슜"),
                    OfflineProductMaster(sku="OLD-2", barcode="222", product_name="湲곗〈 ?ㅽ봽?쇱씤 2", is_active="?ъ슜"),
                ]
            )
            db.commit()

            upload_df = pd.DataFrame(
                [
                    {
                        "SKU": "NEW-1",
                        "移댄뀒怨좊━": "?ㅽ봽?쇱씤",
                        "?곹뭹紐?: "???ㅽ봽?쇱씤 ?곹뭹",
                        "?낆껜紐?: "留ㅼ옣",
                        "諛뺤뒪/?뚮젢???⑥쐞": "0",
                        "?대떦??: "",
                        "由щ뱶???: "0",
                    }
                ]
            )
            buffer = BytesIO()
            upload_df.to_excel(buffer, index=False)

            result = services.import_product_master_excel(db, "?ㅽ봽?쇱씤", buffer.getvalue())
            self.assertTrue(result["ok"], result.get("message"))
            self.assertEqual(result["summary"]["湲곗〈 留덉뒪??], 2)
            self.assertEqual(result["summary"]["??젣"], 2)
            self.assertEqual(result["summary"]["理쒖쥌 留덉뒪??], 1)

            masters = db.execute(select(OfflineProductMaster)).scalars().all()
            self.assertEqual(len(masters), 1)
            self.assertEqual(masters[0].sku, "NEW-1")
            self.assertEqual(masters[0].product_name, "???ㅽ봽?쇱씤 ?곹뭹")
            self.assertEqual(masters[0].barcode, "")
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
            "二쇱쓽",
        )
        self.assertEqual(
            services.inventory_stock_status_for_snapshot(
                True,
                available_stock=4,
                current_stock=4,
                safe_stock=50,
                pending_outbound_qty=5,
            ),
            "遺議?,
        )

    def test_saved_daily_status_is_recalculated_from_available_and_pending_outbound(self) -> None:
        work_date = date(2026, 8, 26)
        db = self.Session()
        try:
            row = InventoryDaily(
                source_type="3PL",
                work_date=work_date,
                product_code="SAFE",
                product_name="safe stock row",
                barcode="8800000000001",
                current_stock=30,
                available_stock=30,
                safe_stock=50,
                outbound_qty=5,
                stock_status="\ubd80\uc871",
            )
            db.add(row)
            db.commit()

            daily = services.daily_to_dict(row)
            summary = services.dashboard_summary(db, work_date, "3PL")

            self.assertEqual(daily["stock_status"], "\uc8fc\uc758")
            self.assertEqual(summary["short_count"], 0)
            self.assertEqual(summary["soldout_count"], 0)
            self.assertEqual(summary["need_inbound_count"], 1)
        finally:
            db.close()

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

    def test_threepl_master_import_deduplicates_updates_deletes_and_syncs_daily_rows(self) -> None:
        db = self.Session()
        try:
            db.add_all(
                [
                    ThirdpartyProductMaster(sku="OLD-SKU", barcode="111", product_name="2단 세로 풀세트", large_category="OLD"),
                    ThirdpartyProductMaster(sku="STALE", barcode="999", product_name="old stale product", large_category="OLD"),
                ]
            )
            db.commit()

            upload_df = pd.DataFrame(
                [
                    {
                        services.SHARED_MASTER_FORM_COLUMNS[0]: "3PL",
                        "바코드": "222",
                        services.SHARED_MASTER_FORM_COLUMNS[1]: "2단 세로 풀세트",
                        services.SHARED_MASTER_FORM_COLUMNS[2]: "vendor",
                        services.SHARED_MASTER_FORM_COLUMNS[3]: "0",
                        services.SHARED_MASTER_FORM_COLUMNS[4]: "manager",
                        services.SHARED_MASTER_FORM_COLUMNS[5]: "3",
                    },
                    {
                        "SKU": "NEW-SKU",
                        services.SHARED_MASTER_FORM_COLUMNS[0]: "3PL",
                        "바코드": "333",
                        services.SHARED_MASTER_FORM_COLUMNS[1]: "new product",
                        services.SHARED_MASTER_FORM_COLUMNS[2]: "vendor",
                        services.SHARED_MASTER_FORM_COLUMNS[3]: "0",
                        services.SHARED_MASTER_FORM_COLUMNS[4]: "manager",
                        services.SHARED_MASTER_FORM_COLUMNS[5]: "4",
                    },
                    {
                        "SKU": "NEW-SKU",
                        services.SHARED_MASTER_FORM_COLUMNS[0]: "3PL",
                        "바코드": "333",
                        services.SHARED_MASTER_FORM_COLUMNS[1]: "new product",
                        services.SHARED_MASTER_FORM_COLUMNS[2]: "vendor",
                        services.SHARED_MASTER_FORM_COLUMNS[3]: "0",
                        services.SHARED_MASTER_FORM_COLUMNS[4]: "manager",
                        services.SHARED_MASTER_FORM_COLUMNS[5]: "4",
                    },
                ]
            )
            buffer = BytesIO()
            upload_df.to_excel(buffer, index=False, sheet_name=services.THREEPL_MASTER_SHEET_NAME)

            result = services.import_product_master_excel(db, "3PL", buffer.getvalue())

            self.assertTrue(result["ok"], result.get("message"))
            self.assertEqual(result["summary"]["기존 마스터"], 2)
            self.assertEqual(result["summary"]["새 마스터 파일"], 2)
            self.assertEqual(result["summary"]["유지/갱신"], 1)
            self.assertEqual(result["summary"]["신규"], 1)
            self.assertEqual(result["summary"]["삭제"], 1)
            self.assertEqual(result["summary"]["중복 제거"], 1)
            self.assertEqual(result["summary"]["DB 재조회 마스터 수"], 2)
            self.assertEqual(result["summary"]["InventoryDaily 신규 생성"], 2)

            masters = db.execute(select(ThirdpartyProductMaster).order_by(ThirdpartyProductMaster.product_name)).scalars().all()
            self.assertEqual(len(masters), 2)
            by_name = {row.product_name: row for row in masters}
            self.assertEqual(by_name["2단 세로 풀세트"].sku, "OLD-SKU")
            self.assertEqual(by_name["2단 세로 풀세트"].barcode, "222")
            self.assertIn("new product", by_name)
            self.assertNotIn("old stale product", by_name)

            daily_rows = db.execute(select(InventoryDaily).where(InventoryDaily.source_type == "3PL")).scalars().all()
            daily_by_name = {row.product_name: row for row in daily_rows}
            self.assertIn("2단 세로 풀세트", daily_by_name)
            self.assertIn("new product", daily_by_name)
            self.assertEqual(daily_by_name["2단 세로 풀세트"].barcode, "222")
        finally:
            db.close()

    def test_threepl_master_import_marks_same_sku_different_names_as_conflict_without_canceling_all(self) -> None:
        db = self.Session()
        try:
            db.add_all(
                [
                    ThirdpartyProductMaster(sku="KEEP", barcode="100", product_name="keep product"),
                    ThirdpartyProductMaster(sku="OLD-CONFLICT", barcode="200", product_name="conflict product a"),
                ]
            )
            db.commit()

            upload_df = pd.DataFrame(
                [
                    {
                        "SKU": "KEEP",
                        services.SHARED_MASTER_FORM_COLUMNS[0]: "3PL",
                        "바코드": "101",
                        services.SHARED_MASTER_FORM_COLUMNS[1]: "keep product",
                        services.SHARED_MASTER_FORM_COLUMNS[2]: "vendor",
                        services.SHARED_MASTER_FORM_COLUMNS[3]: "0",
                        services.SHARED_MASTER_FORM_COLUMNS[4]: "",
                        services.SHARED_MASTER_FORM_COLUMNS[5]: "0",
                    },
                    {
                        "SKU": "DUP-SKU",
                        services.SHARED_MASTER_FORM_COLUMNS[0]: "3PL",
                        "바코드": "201",
                        services.SHARED_MASTER_FORM_COLUMNS[1]: "conflict product a",
                        services.SHARED_MASTER_FORM_COLUMNS[2]: "vendor",
                        services.SHARED_MASTER_FORM_COLUMNS[3]: "0",
                        services.SHARED_MASTER_FORM_COLUMNS[4]: "",
                        services.SHARED_MASTER_FORM_COLUMNS[5]: "0",
                    },
                    {
                        "SKU": "DUP-SKU",
                        services.SHARED_MASTER_FORM_COLUMNS[0]: "3PL",
                        "바코드": "202",
                        services.SHARED_MASTER_FORM_COLUMNS[1]: "conflict product b",
                        services.SHARED_MASTER_FORM_COLUMNS[2]: "vendor",
                        services.SHARED_MASTER_FORM_COLUMNS[3]: "0",
                        services.SHARED_MASTER_FORM_COLUMNS[4]: "",
                        services.SHARED_MASTER_FORM_COLUMNS[5]: "0",
                    },
                ]
            )
            buffer = BytesIO()
            upload_df.to_excel(buffer, index=False, sheet_name=services.THREEPL_MASTER_SHEET_NAME)

            result = services.import_product_master_excel(db, "3PL", buffer.getvalue())

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


if __name__ == "__main__":
    unittest.main()
