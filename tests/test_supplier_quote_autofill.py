from __future__ import annotations

from io import BytesIO
import unittest

import pandas as pd

from backend.models import PurchaseRequest
from pages import purchase


class SupplierQuoteAutofillTest(unittest.TestCase):
    def test_extracts_supplier_fields_from_excel_quote(self) -> None:
        buffer = BytesIO()
        rows = [
            ["견적서", "", "", ""],
            ["공급사", "주식회사 청명팩", "사업자등록번호", "123-45-67890"],
            ["담당자", "김민수", "TEL", "010-1234-5678"],
            ["E-mail", "sales@cm-pack.co.kr", "결제조건", "월말 정산"],
            ["납기", "7일", "MOQ", "500개"],
            ["품명", "규격", "수량", "단가"],
            ["PLA 용기 500ml", "500ml", "1,000", "351"],
        ]
        pd.DataFrame(rows).to_excel(buffer, index=False, header=False)

        result = purchase.extract_supplier_info_from_quote(buffer.getvalue(), "청명팩_견적서.xlsx")
        fields = result["fields"]

        self.assertEqual(fields["supplier_name"], "주식회사 청명팩")
        self.assertEqual(fields["business_number"], "123-45-67890")
        self.assertEqual(fields["manager"], "김민수")
        self.assertEqual(fields["phone"], "010-1234-5678")
        self.assertEqual(fields["email"], "sales@cm-pack.co.kr")
        self.assertEqual(fields["handled_items"], "PLA 용기 500ml")
        self.assertEqual(fields["moq_terms"], "500개")
        self.assertEqual(fields["avg_lead_time_days"], 7)
        self.assertEqual(fields["avg_unit_price_text"], "351W")
        self.assertEqual(fields["payment_terms"], "월말 정산")

    def test_extracts_supplier_fields_from_text_quote(self) -> None:
        text = """
        QUOTATION
        Vendor: ABC Materials Co., Ltd.
        Business No: 9876543210
        Contact: Jane Kim
        Phone: 02-3456-7890
        Email: quote@abc-materials.com
        Item: Stainless bracket
        Lead time: 2 weeks
        Unit Price: USD 12.5
        Payment Terms: Net 30
        """

        result = purchase.extract_supplier_info_from_quote(text.encode("utf-8"), "quote.txt")
        fields = result["fields"]

        self.assertEqual(fields["supplier_name"], "ABC Materials Co., Ltd")
        self.assertEqual(fields["business_number"], "987-65-43210")
        self.assertEqual(fields["manager"], "Jane Kim")
        self.assertEqual(fields["phone"], "02-3456-7890")
        self.assertEqual(fields["email"], "quote@abc-materials.com")
        self.assertEqual(fields["handled_items"], "Stainless bracket")
        self.assertEqual(fields["avg_lead_time_days"], 14)
        self.assertEqual(fields["avg_unit_price_text"], "12.5$")
        self.assertEqual(fields["payment_terms"], "Net 30")

    def test_extracts_rfq_fields_for_selected_pr_from_excel_quote(self) -> None:
        pr = PurchaseRequest(pr_number="PR-20260909-001", item_code="SKU-001", item_name="PLA 용기 500ml", spec="500ml", quantity=100)
        buffer = BytesIO()
        rows = [
            ["견적서", "", "", ""],
            ["공급사", "주식회사 청명팩", "담당자", "김민수"],
            ["TEL", "010-1234-5678", "E-mail", "sales@cm-pack.co.kr"],
            ["결제조건", "월말 정산", "견적 유효기간", "2026-10-31"],
            ["배송비", "3,000", "MOQ", "500개"],
            ["납기", "7일", "", ""],
            ["품명", "규격", "수량", "단가"],
            ["종이컵", "12oz", "1,000", "999"],
            ["PLA 용기 500ml", "500ml", "1,000", "351"],
        ]
        pd.DataFrame(rows).to_excel(buffer, index=False, header=False)

        result = purchase.extract_rfq_quote_info_from_quote(buffer.getvalue(), "청명팩_견적서.xlsx", pr)
        fields = result["fields"]

        self.assertEqual(fields["supplier_name"], "주식회사 청명팩")
        self.assertEqual(fields["supplier_manager"], "김민수")
        self.assertEqual(fields["supplier_phone"], "010-1234-5678")
        self.assertEqual(fields["supplier_email"], "sales@cm-pack.co.kr")
        self.assertEqual(fields["unit_price"], 351)
        self.assertEqual(fields["currency"], "KRW")
        self.assertEqual(fields["moq"], 500)
        self.assertEqual(fields["lead_time_days"], 7)
        self.assertEqual(fields["shipping_fee"], 3000)
        self.assertEqual(fields["payment_terms"], "월말 정산")
        self.assertEqual(fields["quote_valid_until"].isoformat(), "2026-10-31")

    def test_extracts_rfq_fields_from_text_quote(self) -> None:
        pr = PurchaseRequest(pr_number="PR-20260909-002", item_code="BRK-1", item_name="Stainless bracket", spec="", quantity=20)
        text = """
        QUOTATION
        Supplier: ABC Materials Co., Ltd.
        Contact: Jane Kim
        Phone: 02-3456-7890
        Email: quote@abc-materials.com
        Item: Stainless bracket
        Unit Price: USD 12.5
        MOQ: 10
        Lead time: 2 weeks
        Shipping fee: 25
        Validity: 30 days
        Payment Terms: Net 30
        """

        result = purchase.extract_rfq_quote_info_from_quote(text.encode("utf-8"), "quote.txt", pr)
        fields = result["fields"]

        self.assertEqual(fields["supplier_name"], "ABC Materials Co., Ltd")
        self.assertEqual(fields["supplier_manager"], "Jane Kim")
        self.assertEqual(fields["unit_price"], 12.5)
        self.assertEqual(fields["currency"], "USD")
        self.assertEqual(fields["moq"], 10)
        self.assertEqual(fields["lead_time_days"], 14)
        self.assertEqual(fields["shipping_fee"], 25)
        self.assertEqual(fields["payment_terms"], "Net 30")
        self.assertIsNotNone(fields["quote_valid_until"])


if __name__ == "__main__":
    unittest.main()
