from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

from app import Orders
from server.__main__ import make_handler
from server.reconciliation import evaluate, normalize
from server.workbench import Conflict, Workbench, json_text


SAMPLES = Path(__file__).parent / "samples" / "audit"
BASELINE = json.loads((SAMPLES / "baseline.json").read_text(encoding="utf-8"))
RETEST = json.loads((SAMPLES / "retest.json").read_text(encoding="utf-8"))
DEMO_PASSWORD = "test-suite-only-password"


class ReconciliationChecks(unittest.TestCase):
    def test_baseline_flags_and_cutoff(self):
        data = evaluate(normalize(BASELINE))
        flagged = {x["reference"]: x["flags"] for x in data if x["flags"]}
        self.assertEqual(flagged, {
            "114-4384545-7122128": ["missing_payout"],
            "114-4386682-7120747": ["duplicate_payout"],
            "114-4388819-7119366": ["currency_mismatch"],
            "17-13288-61203": ["amount_mismatch"],
        })
        self.assertEqual(sum(x["pending"] for x in data), 2)
        self.assertEqual(sum(bool(x["flags"]) for x in evaluate(normalize(RETEST))), 0)

    def test_partial_payment_and_zero_expected(self):
        data = deepcopy(BASELINE)
        data["orders"][0].update(gross="0.00", fee="0.00")
        data["payouts"] = [x for x in data["payouts"] if x["reference"] != data["orders"][0]["reference"]]
        case = evaluate(normalize(data))[0]
        self.assertEqual((case["expected"], case["actual"], case["flags"]), (0, {}, []))
        data["orders"][0]["gross"] = "41.40"
        data["orders"][0]["due"] = (datetime.fromisoformat(data["as_of"]) + timedelta(days=1)).date().isoformat()
        data["payouts"].insert(0, BASELINE["payouts"][0])
        case = evaluate(normalize(data))[0]
        self.assertTrue(case["pending"])
        self.assertEqual(case["actual"], {"USD": 2000})

    def test_rejects_duplicate_order_precision_and_invalid_cutoff(self):
        for change in (
            lambda x: x["orders"].append(x["orders"][0]),
            lambda x: x["payouts"][0].update(amount="2.001"),
            lambda x: x.update(as_of="2026-11-31"),
        ):
            data = deepcopy(BASELINE)
            change(data)
            with self.assertRaises(ValueError):
                normalize(data)


class HttpChecks(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.store = Workbench(root / "audit.sqlite3")
        for role in ("operator", "reviewer", "auditor"):
            self.store.add_user(role, role, role, DEMO_PASSWORD)
        handler = make_handler(self.store, Orders(root / "orders.sqlite3", None), 0)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def request(self, path, data=None, credential=None, changes=None):
        headers = {}
        if data is not None:
            headers.update({"Content-Type": "application/json", "Origin": self.origin, "X-Meridian-Client": "workbench"})
        if credential:
            headers.update({"Cookie": credential[0], "X-Meridian-CSRF": credential[1]})
        headers.update(changes or {})
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        try:
            connection.request("GET" if data is None else "POST", path,
                               body=None if data is None else json_text(data).encode(), headers=headers)
            response = connection.getresponse()
            raw = response.read()
            payload = json.loads(raw) if raw and "application/json" in response.getheader("Content-Type", "") else raw
            return response.status, payload, response.getheader("Set-Cookie")
        finally:
            connection.close()

    def login_as(self, role):
        status, _, cookie = self.request("/api/login", {"username": role, "password": DEMO_PASSWORD})
        self.assertEqual(status, 204)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        cookie = cookie.split(";")[0]
        status, session, _ = self.request("/api/session", credential=(cookie, ""))
        self.assertEqual((status, session["role"]), (200, role))
        return cookie, session["csrf"]

    def test_requires_login_role_and_csrf(self):
        self.assertEqual(self.request("/api/audit")[0], 401)
        self.assertEqual(self.request("/api/overview")[0], 401)
        self.assertEqual(self.request("/", changes={"Host": "untrusted.test"})[0], 400)
        reviewer = self.login_as("reviewer")
        self.assertEqual(self.request("/api/audit/intake", BASELINE, reviewer)[0], 403)
        operator = self.login_as("operator")
        self.assertEqual(self.request("/api/audit/intake", BASELINE, operator, {"X-Meridian-CSRF": "wrong"})[0], 403)
        self.assertEqual(self.request("/api/audit/intake", BASELINE, operator, {"Origin": "http://example.test"})[0], 403)
        self.assertEqual(self.request("/orders", credential=operator)[0], 200)
        self.assertEqual(self.request("/api/logout", {}, operator)[0], 204)
        self.assertEqual(self.request("/api/session", credential=operator)[0], 401)

    def test_seed_submit_review_and_export(self):
        operator, reviewer, auditor = map(self.login_as, ("operator", "reviewer", "auditor"))
        status, intake, _ = self.request("/api/audit/intake", BASELINE, operator)
        self.assertEqual((status, intake["findings"], intake["pending"]), (201, 4, 2))
        reversed_data = dict(BASELINE, orders=BASELINE["orders"][::-1], payouts=BASELINE["payouts"][::-1])
        status, existing, _ = self.request("/api/audit/intake", reversed_data, operator)
        self.assertEqual((status, existing["created"], existing["run_id"]), (200, False, intake["run_id"]))
        _, listed, _ = self.request("/api/audit", credential=auditor)
        self.assertEqual(len(listed["cases"]), 4)
        match = next(x for x in listed["cases"] if x["flags"] == ["missing_payout"])
        path = "/api/audit/cases/" + str(match["id"])
        review = {"note": "Đối chiếu tệp nguồn kỳ đầu", "dataset": BASELINE}
        self.assertEqual(self.request(path + "/retest", review, reviewer)[0], 409)
        self.assertEqual(self.request(path + "/submit", {"note": "Đã bổ sung SET-06"}, auditor)[0], 403)
        self.assertEqual(self.request(path + "/submit", {"note": "Đã bổ sung SET-06"}, operator)[1]["status"], "in_review")
        self.assertEqual(self.request(path + "/retest", review, operator)[0], 403)
        with self.assertRaises(Conflict):
            self.store.retest(match["id"], BASELINE, "Same identity", "operator")
        wrong = deepcopy(RETEST)
        wrong["orders"][0].update(gross="46.41")
        self.assertEqual(self.request(path + "/retest", dict(review, dataset=wrong), reviewer)[0], 400)
        self.assertEqual(self.request(path + "/retest", review, reviewer)[1]["status"], "open")
        self.request(path + "/submit", {"note": "Đã rà lại dữ liệu chi trả"}, operator)
        status, closed, _ = self.request(path + "/retest", dict(review, dataset=RETEST), reviewer)
        self.assertEqual((status, closed["status"], closed["latest"]["actual"]), (200, "closed", {"USD": 1500}))
        self.assertEqual(self.request(path + "/submit", {"note": "Gửi lại hồ sơ đã đóng"}, operator)[0], 409)
        _, evidence, _ = self.request(path + "/evidence", credential=auditor)
        self.assertEqual([event["actor"] for event in evidence["case"]["events"]],
                         ["operator", "operator", "reviewer", "operator", "reviewer"])
        self.assertEqual(len(evidence["snapshots"]), 2)
        for snapshot in evidence["snapshots"]:
            self.assertEqual(snapshot["hash"], sha256(json_text(snapshot["payload"]).encode()).hexdigest())


if __name__ == "__main__":
    unittest.main(verbosity=2)
