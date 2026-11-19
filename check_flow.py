import copy
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from app import Odoo, Orders, handler_for
from scenarios import AMAZON, CORRECTED_MAPPING_SAMPLE, EBAY, LOCAL_MARKET_SAMPLES, MISSING_MAPPING_SAMPLE, TOTAL_MISMATCH_SAMPLE, market_code


TEST_CURRENCIES = {1: "VND", 2: "USD", 3: "EUR", 4: "ZAR", 5: "AUD", 6: "CNY"}


class SimulatedOdoo(Odoo):
    def __init__(self):
        self.quotations = []
        self.interrupted = False
        self.sending = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def call(self, model, method, body):
        if model == "product.product":
            sku = body["domain"][0][2]
            return [{"id": {"HK-ORG-001": 11, "ST-NBK-001": 12}[sku]}]
        if model == "res.currency":
            criterion = body["domain"][0]
            if criterion[0] == "name":
                return [{"id": identifier} for identifier, name in TEST_CURRENCIES.items() if name == criterion[2]]
            return [{"name": TEST_CURRENCIES[criterion[2]]}]
        if model == "product.pricelist":
            return [{"id": body["domain"][0][2] + 20}]
        if model == "sale.order" and method == "create":
            values = body["vals_list"][0]
            self.sending.set()
            if not self.release.wait(3):
                raise TimeoutError("Test did not release the create operation")
            currency_id = 1 if values["pricelist_id"] == 1 else values["pricelist_id"] - 20
            currency = [currency_id, "Currency display name"]
            record = {"id": len(self.quotations) + 10, "state": "draft", "name": f"S{len(self.quotations) + 10:05}",
                      "client_order_ref": values["client_order_ref"], "lines": values["order_line"], "currency_id": currency}
            self.quotations.append(record)
            if self.interrupted:
                raise TimeoutError("Simulated lost response after Odoo commit")
            return [record["id"]]
        if model == "sale.order":
            if body["domain"][0][0] == "name":
                if body["domain"] != [["name", "=", "S00003"]]:
                    raise AssertionError(body)
                return [{"partner_id": {"id": 9}, "currency_id": {"id": 1, "display_name": "Vietnamese đồng"},
                         "pricelist_id": [1, "Public Pricelist"]}]
            return [q for q in self.quotations if q["client_order_ref"] == body["domain"][0][2]]
        if model == "sale.order.line":
            order = next(q for q in self.quotations if q["id"] == body["domain"][0][2])
            return [{"product_id": [values["product_id"], "Product"], "product_uom_qty": values["product_uom_qty"],
                     "price_unit": values["price_unit"]} for _, _, values in order["lines"]]
        raise AssertionError((model, method))

class FlowChecks(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.odoo = SimulatedOdoo()
        self.orders = Orders(Path(directory.name) / "test.sqlite3", self.odoo)

    def test_tc01_tc08_amazon_creates_verified_quotation(self):
        order_id, result = self.orders.ingest("amazon", AMAZON)
        order = self.orders.fetch(order_id)
        self.assertEqual((result, order["status"], order["quotation"]), ("new", "synced", "S00010"))
        self.assertEqual(len(order["resolved"]), 2)

    def test_tc02_tc03_tc10_ebay_reports_and_recovers_missing_mapping(self):
        order_id, _ = self.orders.ingest("ebay", EBAY)
        order = self.orders.fetch(order_id)
        self.assertEqual(order["status"], "exception")
        self.assertIn("EB-NOTE-A5-RU", order["detail"])
        self.assertEqual(self.odoo.quotations, [])
        self.orders.map_sku("ebay", "meridian-us", "EB-NOTE-A5-RU", "ST-NBK-001")
        self.orders.process(order_id)
        order = self.orders.fetch(order_id)
        self.assertEqual(order["status"], "synced")
        self.assertGreaterEqual(len(order["events"]), 4)

    def test_tc04_invalid_quantity_never_reaches_odoo(self):
        raw = copy.deepcopy(AMAZON)
        raw["OrderItems"][0]["QuantityOrdered"] = -2
        order_id, _ = self.orders.ingest("amazon", raw)
        self.assertEqual(self.orders.fetch(order_id)["status"], "exception")
        self.assertEqual(self.odoo.quotations, [])

    def test_tc05_duplicate_reuses_quotation(self):
        order_id, _ = self.orders.ingest("amazon", AMAZON)
        self.assertEqual(self.orders.ingest("amazon", AMAZON), (order_id, "duplicate"))
        self.assertEqual(len(self.odoo.quotations), 1)

    def test_tc06_changed_order_is_conflict(self):
        order_id, _ = self.orders.ingest("amazon", AMAZON)
        changed = copy.deepcopy(AMAZON)
        changed["OrderItems"][0]["QuantityOrdered"] = 4
        self.assertEqual(self.orders.ingest("amazon", changed), (order_id, "conflict"))
        self.assertEqual(self.orders.fetch(order_id)["payload"]["lines"][0]["quantity"], 2)
        self.assertEqual(len(self.odoo.quotations), 1)

    def test_tc07_concurrent_posts_generate_one_quotation(self):
        self.odoo.release.clear()
        with ThreadPoolExecutor(2) as pool:
            initial = pool.submit(self.orders.ingest, "amazon", AMAZON)
            try:
                self.assertTrue(self.odoo.sending.wait(2))
                order_id, result = pool.submit(self.orders.ingest, "amazon", AMAZON).result(2)
                self.assertEqual(result, "duplicate")
            finally:
                self.odoo.release.set()
            self.assertEqual(initial.result(2), (order_id, "new"))
        self.assertEqual(len(self.odoo.quotations), 1)

    def test_tc09_interrupted_create_recovers_through_lookup(self):
        self.odoo.interrupted = True
        order_id, _ = self.orders.ingest("amazon", AMAZON)
        self.assertEqual(self.orders.fetch(order_id)["status"], "review")
        self.orders.process(order_id)
        self.assertEqual(self.orders.fetch(order_id)["status"], "synced")
        self.assertEqual(len(self.odoo.quotations), 1)

    def test_five_marketplaces_and_destinations_reach_odoo_adapter(self):
        for key, destination, currency in (("amazon", "US", "USD"), ("ebay", "DE", "EUR"), ("etsy", "AU", "AUD"),
                                           ("aliexpress", "ZA", "ZAR"), ("taobao", "JP", "CNY")):
            with self.subTest(sample=key):
                order_id, _ = self.orders.ingest(key, LOCAL_MARKET_SAMPLES[key])
                order = self.orders.fetch(order_id)
                self.assertEqual(market_code(order["payload"]["market"]), destination)
                self.assertEqual(order["status"], "synced")
                self.assertEqual(TEST_CURRENCIES[self.odoo.quotations[-1]["currency_id"][0]], currency)
        self.assertEqual(len(self.odoo.quotations), 5)

    def test_foreign_currency_uses_configured_pricelist_and_preserves_decimals(self):
        order_id, _ = self.orders.ingest("amazon", LOCAL_MARKET_SAMPLES["amazon"])
        self.assertEqual(self.orders.fetch(order_id)["status"], "synced")
        quote = self.odoo.quotations[0]
        self.assertEqual(quote["currency_id"][0], 2)
        self.assertEqual(quote["lines"][0][2]["price_unit"], 14.95)

    def test_destination_is_independent_of_platform_and_currency(self):
        amazon = {**LOCAL_MARKET_SAMPLES["amazon"], "destination_country": "DE"}
        order_id, _ = self.orders.ingest("amazon", amazon)
        order = self.orders.fetch(order_id)
        self.assertEqual((order["channel"], order["payload"]["market"], order["payload"]["currency"]),
                         ("amazon", "DE", "USD"))
        self.assertEqual(order["status"], "synced")

    def test_missing_currency_stops_before_odoo_create(self):
        order_id, _ = self.orders.ingest("taobao", {**LOCAL_MARKET_SAMPLES["taobao"], "settlement_currency": "JPY",
                                                    "orders": [{"outer_sku_id": "TB-ORG-IV-01", "num": 1, "price": 1500}]})
        order = self.orders.fetch(order_id)
        self.assertEqual(order["status"], "exception")
        self.assertIn("JPY", order["detail"])
        self.assertEqual(self.odoo.quotations, [])

    def test_source_total_mismatch_stops_before_odoo_create(self):
        order_id, _ = self.orders.ingest("amazon", TOTAL_MISMATCH_SAMPLE)
        order = self.orders.fetch(order_id)
        self.assertEqual(order["status"], "exception")
        self.assertIn("Tổng nguồn 52,19 USD", order["detail"])
        self.assertIn("tính lại 50,69 USD", order["detail"])
        self.assertIn("lệch +1,50 USD", order["detail"])
        self.assertIsNone(order["odoo_id"])
        self.assertEqual(self.odoo.quotations, [])

    def test_equal_total_with_unmapped_components_stays_on_hold(self):
        sample = copy.deepcopy(TOTAL_MISMATCH_SAMPLE)
        sample["financial_summary"]["total"] = 50.69
        order_id, _ = self.orders.ingest("amazon", sample)
        order = self.orders.fetch(order_id)
        self.assertEqual(order["status"], "exception")
        self.assertIn("Tổng nguồn đã khớp", order["detail"])
        self.assertEqual(self.odoo.quotations, [])

    def test_exception_approval_hands_off_only_after_explicit_send(self):
        order_id, _ = self.orders.ingest("ebay", MISSING_MAPPING_SAMPLE)
        self.orders.assign(order_id, "Mai Linh")
        self.orders.submit_revision(order_id, CORRECTED_MAPPING_SAMPLE, "Đã nhận mã SKU đính chính.")
        pending = self.orders.fetch(order_id)
        revision_id = pending["case"]["revisions"][0]["id"]
        self.assertEqual(pending["raw"], MISSING_MAPPING_SAMPLE)
        self.assertEqual(pending["case"]["revisions"][0]["changes"],
                         [{"field": "lineItems[2].sku", "before": "EB-NOTE-A5-RU", "after": "EB-NOTE-A5-01"}])
        item = self.orders.overview()["orders"][0]
        self.assertEqual((item["assignee"], item["revision_status"]), ("Mai Linh", "pending"))
        with self.assertRaisesRegex(ValueError, "trước khi bàn giao"):
            self.orders.process(order_id)
        with self.assertRaisesRegex(ValueError, "khác người gửi"):
            self.orders.decide_revision(order_id, revision_id, "mai linh", "approve", None)
        self.orders.decide_revision(order_id, revision_id, "Minh An", "approve", None)
        approved = self.orders.fetch(order_id)
        self.assertEqual(approved["status"], "ready")
        self.assertEqual(approved["case"]["original"], MISSING_MAPPING_SAMPLE)
        self.assertEqual(approved["raw"], CORRECTED_MAPPING_SAMPLE)
        self.assertEqual(len(self.odoo.quotations), 0)
        self.orders.process(order_id)
        self.orders.process(order_id)
        self.assertEqual(self.orders.fetch(order_id)["status"], "synced")
        self.assertEqual(len(self.odoo.quotations), 1)

    def test_review_invalid_and_returned_versions_stay_blocked(self):
        order_id, _ = self.orders.ingest("amazon", TOTAL_MISMATCH_SAMPLE)
        self.orders.assign(order_id, "Mai Linh")
        candidate = copy.deepcopy(TOTAL_MISMATCH_SAMPLE)
        candidate["financial_summary"]["total"] = 50.69
        self.orders.submit_revision(order_id, candidate, "Đã cập nhật tổng nguồn.")
        version = self.orders.fetch(order_id)["case"]["revisions"][0]["id"]
        self.orders.decide_revision(order_id, version, "Minh An", "approve", None)
        order = self.orders.fetch(order_id)
        self.assertEqual(order["case"]["revisions"][0]["status"], "invalid")
        self.assertIn("cấu hình ánh xạ", order["detail"])
        self.orders.submit_revision(order_id, candidate, "Gửi lại để xác minh các khoản phí.")
        version = self.orders.fetch(order_id)["case"]["revisions"][0]["id"]
        self.orders.decide_revision(order_id, version, "Minh An", "reject", "Cần chứng từ phí vận chuyển.")
        order = self.orders.fetch(order_id)
        self.assertEqual(order["case"]["revisions"][0]["status"], "rejected")
        self.assertEqual(order["raw"], TOTAL_MISMATCH_SAMPLE)
        with self.assertRaises(ValueError):
            self.orders.process(order_id)
        self.assertEqual(len(self.odoo.quotations), 0)

    def test_revision_rejects_changed_identity_and_second_pending_version(self):
        order_id, _ = self.orders.ingest("ebay", MISSING_MAPPING_SAMPLE)
        self.orders.assign(order_id, "Mai Linh")
        other = {**CORRECTED_MAPPING_SAMPLE, "orderId": "24-00000-58105"}
        with self.assertRaisesRegex(ValueError, "cùng tài khoản bán"):
            self.orders.submit_revision(order_id, other, "Sai mã.")
        self.orders.submit_revision(order_id, CORRECTED_MAPPING_SAMPLE, "Bản đầu.")
        with self.assertRaisesRegex(ValueError, "đang chờ xác nhận"):
            self.orders.submit_revision(order_id, CORRECTED_MAPPING_SAMPLE, "Bản thứ hai.")

    def test_assigned_missing_sku_can_approve_mapping_without_editing_source(self):
        order_id, _ = self.orders.ingest("ebay", MISSING_MAPPING_SAMPLE)
        self.orders.assign(order_id, "Hải Nam")
        self.orders.map_sku("ebay", "meridian-us", "EB-NOTE-A5-RU", "ST-NBK-001")
        self.orders.submit_revision(order_id, None, "Đối chiếu danh mục.", mapping_only=True)
        proposal = self.orders.fetch(order_id)["case"]["revisions"][0]
        self.assertTrue(proposal["mapping_only"])
        self.assertEqual(proposal["changes"], [])
        self.assertEqual(proposal["lines"][1]["sku"], "ST-NBK-001")
        self.orders.decide_revision(order_id, proposal["id"], "Minh An", "approve", None)
        self.orders.process(order_id)
        result = self.orders.fetch(order_id)
        self.assertEqual((result["status"], result["raw"]), ("synced", MISSING_MAPPING_SAMPLE))
        self.assertEqual(len(self.odoo.quotations), 1)

    def test_mapping_change_after_proposal_is_rejected_at_review(self):
        order_id, _ = self.orders.ingest("ebay", MISSING_MAPPING_SAMPLE)
        self.orders.assign(order_id, "Hải Nam")
        self.orders.map_sku("ebay", "meridian-us", "EB-NOTE-A5-RU", "ST-NBK-001")
        self.orders.submit_revision(order_id, None, "Đã tìm SKU.", mapping_only=True)
        proposal = self.orders.fetch(order_id)["case"]["revisions"][0]
        self.orders.map_sku("ebay", "meridian-us", "EB-NOTE-A5-RU", "HK-ORG-001")
        self.orders.decide_revision(order_id, proposal["id"], "Minh An", "approve", None)
        result = self.orders.fetch(order_id)
        self.assertEqual(result["case"]["revisions"][0]["status"], "invalid")
        self.assertEqual(self.odoo.quotations, [])

    def test_mapping_drift_after_approval_requires_new_approval_before_handoff(self):
        order_id, _ = self.orders.ingest("ebay", MISSING_MAPPING_SAMPLE)
        self.orders.assign(order_id, "Hải Nam")
        self.orders.submit_revision(order_id, CORRECTED_MAPPING_SAMPLE, "Đính chính SKU.")
        approved = self.orders.fetch(order_id)["case"]["revisions"][0]
        self.orders.decide_revision(order_id, approved["id"], "Minh An", "approve", None)
        self.orders.map_sku("ebay", "meridian-us", "EB-NOTE-A5-01", "HK-ORG-001")
        self.orders.process(order_id)
        blocked = self.orders.fetch(order_id)
        self.assertEqual(blocked["status"], "exception")
        self.assertIn("dòng 2 (ST-NBK-001 → HK-ORG-001)", blocked["detail"])
        self.assertEqual(blocked["case"]["revisions"][0]["status"], "stale")
        self.assertEqual(self.odoo.quotations, [])
        self.orders.submit_revision(order_id, None, "Duyệt lại SKU đã đổi.", mapping_only=True)
        new = self.orders.fetch(order_id)["case"]["revisions"][0]
        self.orders.decide_revision(order_id, new["id"], "Minh An", "approve", None)
        self.orders.process(order_id)
        self.assertEqual(self.orders.fetch(order_id)["status"], "synced")
        self.assertEqual(len(self.odoo.quotations), 1)

    def test_cancel_after_sync_retains_quote_and_does_not_send_again(self):
        order_id, _ = self.orders.ingest("amazon", AMAZON)
        cancellation = {"AmazonOrderId": AMAZON["AmazonOrderId"], "SellerAccount": AMAZON["SellerAccount"],
                        "OrderStatus": "Canceled"}
        preview = self.orders.preview("amazon", cancellation)
        self.assertTrue(preview["cancellation"])
        self.assertEqual(preview["existing"]["quotation"], "S00010")
        self.assertIsNone(self.orders.fetch(order_id)["cancellation"])
        self.assertEqual(self.orders.ingest("amazon", cancellation), (order_id, "canceled"))
        self.assertEqual(self.orders.ingest("amazon", cancellation), (order_id, "canceled"))
        result = self.orders.fetch(order_id)
        self.assertEqual(result["cancellation"]["raw"], cancellation)
        self.assertEqual(result["quotation"], "S00010")
        self.assertEqual(sum("Đã ghi nhận đơn bị hủy" in item["message"] for item in result["events"]), 1)
        with self.assertRaisesRegex(ValueError, "đã hủy"):
            self.orders.process(order_id)
        self.assertEqual(len(self.odoo.quotations), 1)

    def test_cancellation_from_other_channel_blocks_first_send_and_later_payload(self):
        cancellation = {**MISSING_MAPPING_SAMPLE, "source_status": "Cancelled"}
        order_id, result = self.orders.ingest("ebay", cancellation)
        self.assertEqual(result, "canceled")
        self.assertEqual(self.orders.ingest("ebay", MISSING_MAPPING_SAMPLE), (order_id, "canceled"))
        self.assertEqual(self.orders.overview()["orders"][0]["canceled"], 1)
        self.assertEqual(self.odoo.quotations, [])

    def test_http_catalog_and_sample(self):
        with ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.orders, 0)) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(base, timeout=3) as response:
                    self.assertIn(b"Meridian", response.read())
                with urlopen(base + "/api/overview", timeout=3) as response:
                    self.assertEqual(len(json.load(response)["products"]), 500)
                with urlopen(base + "/samples/ebay.json", timeout=3) as response:
                    self.assertEqual(json.load(response), LOCAL_MARKET_SAMPLES["ebay"])
                with urlopen(base + "/samples/total-mismatch.json", timeout=3) as response:
                    self.assertEqual(json.load(response), TOTAL_MISMATCH_SAMPLE)
                for source in LOCAL_MARKET_SAMPLES:
                    with urlopen(base + f"/samples/operations/{source}-exception.json", timeout=3) as response:
                        sample = json.load(response)
                        self.assertIsNotNone(self.orders.preview(source, sample)["error"])
                def post(path, payload):
                    request = Request(base + path, json.dumps(payload).encode(), {"Content-Type": "application/json"})
                    with urlopen(request, timeout=3) as response:
                        return json.load(response)

                preview = post("/api/orders/preview", {"channel": "amazon", "order": LOCAL_MARKET_SAMPLES["amazon"]})
                self.assertIsNone(preview["error"])
                self.assertEqual(preview["order"]["lines"][0]["sku"], "HK-ORG-001")
                self.assertEqual(self.orders.overview()["orders"], [])
                self.assertEqual(self.odoo.quotations, [])
                order = post("/api/orders", {"channel": "ebay", "order": MISSING_MAPPING_SAMPLE})["order"]
                self.assertEqual(order["status"], "exception")
                post("/api/mappings", {"channel": "ebay", "account": "meridian-us",
                                       "listing": "EB-NOTE-A5-RU", "sku": "ST-NBK-001"})
                updated = post(f"/api/orders/{order['id']}/retry", {})
                self.assertEqual(updated["status"], "synced")
                raw = {**MISSING_MAPPING_SAMPLE, "orderId": "24-12637-58106"}
                raw["lineItems"] = [raw["lineItems"][0], {**raw["lineItems"][1], "sku": "EB-NOTE-A5-TYPO"}]
                case = post("/api/orders", {"channel": "ebay", "order": raw})["order"]
                path = f"/api/orders/{case['id']}"
                post(path + "/assign", {"assignee": "Mai Linh"})
                correction = {**CORRECTED_MAPPING_SAMPLE, "orderId": raw["orderId"]}
                case = post(path + "/revision", {"order": correction, "reason": "Đính chính SKU"})
                case = post(path + "/decision", {"revision_id": case["case"]["revisions"][0]["id"],
                                                  "reviewer": "Minh An", "decision": "approve"})
                self.assertEqual(case["status"], "ready")
                self.assertEqual(post(path + "/retry", {})["status"], "synced")
            finally:
                server.shutdown()
                thread.join()

    def test_preview_reports_invalid_and_conflicting_payload_without_mutation(self):
        unsupported = self.orders.preview("amazon", TOTAL_MISMATCH_SAMPLE)
        self.assertIn("lệch +1,50 USD", unsupported["error"])
        self.assertEqual(self.orders.overview()["orders"], [])
        order_id, _ = self.orders.ingest("amazon", AMAZON)
        events_before = self.orders.fetch(order_id)["events"]
        existing = self.orders.preview("amazon", AMAZON)
        self.assertTrue(existing["existing"]["matches"])
        changed = copy.deepcopy(AMAZON)
        changed["OrderItems"][0]["QuantityOrdered"] += 1
        conflict = self.orders.preview("amazon", changed)
        self.assertFalse(conflict["existing"]["matches"])
        self.assertEqual(self.orders.fetch(order_id)["events"], events_before)
        self.assertEqual(len(self.odoo.quotations), 1)

    def test_operations_dataset_matches_manifest_and_catalog(self):
        root = Path(__file__).resolve().parent / "samples" / "operations"
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        master = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest), 33)
        self.assertEqual(len({row["sku"] for row in master}), 500)
        normal, exceptions, odoo_ready = 0, 0, 0
        for entry in manifest[:30]:
            data = json.loads((root / entry["file"]).read_text(encoding="utf-8"))
            preview = self.orders.preview(entry["channel"], data)
            self.assertEqual(preview["order"]["delivery"]["postal_code"], data["delivery"]["postal_code"])
            self.assertEqual(preview["order"]["placed_at"], data["placed_at"])
            self.assertEqual(preview["order"]["currency"], entry["currency"])
            if entry["expected"] == "Hợp lệ trong Meridian":
                self.assertIsNone(preview["error"])
                normal += 1
            else:
                self.assertTrue(preview["error"], entry["file"])
                exceptions += 1
            odoo_ready += entry["odoo_skus_already_provisioned"]
        self.assertEqual((normal, exceptions, odoo_ready), (25, 5, 10))
        self.assertEqual(self.orders.overview()["orders"], [])
        self.assertEqual(self.odoo.quotations, [])
        def fixture(name):
            return json.loads((root / "orders" / name).read_text(encoding="utf-8"))

        imported, _ = self.orders.ingest("amazon", fixture("amazon-01.json"))
        self.assertEqual(self.orders.ingest("amazon", fixture("amazon-01-replay.json")), (imported, "duplicate"))
        self.assertEqual(self.orders.ingest("amazon", fixture("amazon-01-cancellation.json")), (imported, "canceled"))
        self.assertIsNotNone(self.orders.fetch(imported)["quotation"])
        taobao, _ = self.orders.ingest("taobao", fixture("taobao-06.json"))
        self.orders.assign(taobao, "Hải Nam")
        self.orders.submit_revision(taobao, fixture("taobao-06-correction.json"), "Khôi phục mã listing đã đối chiếu.")
        revision = self.orders.fetch(taobao)["case"]["revisions"][0]
        self.orders.decide_revision(taobao, revision["id"], "Minh An", "approve", None)
        self.orders.process(taobao)
        self.assertEqual(self.orders.fetch(taobao)["status"], "synced")
        self.assertEqual(len(self.odoo.quotations), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
