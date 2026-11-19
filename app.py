import argparse
import getpass
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from scenarios import CHANNELS, CORRECTED_MAPPING_SAMPLE, CURRENCY_PLACES, LOCAL_MARKET_SAMPLES, MAPPINGS, MARKETS, MISSING_MAPPING_SAMPLE, TOTAL_MISMATCH_SAMPLE, catalog, market_code


ROOT = Path(__file__).resolve().parent
ODOO_URL = os.getenv("ODOO_URL", "").strip().rstrip("/")
ODOO_FIXTURE_QUOTE = os.getenv("ODOO_FIXTURE_QUOTE", "S00003")
CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def changes_between(previous, current, path=""):
    if type(previous) is dict and type(current) is dict:
        return [
            change
            for key in sorted(previous.keys() | current.keys())
            for change in changes_between(previous.get(key), current.get(key), f"{path}.{key}" if path else key)
        ]
    if type(previous) is list and type(current) is list and len(previous) == len(current):
        return [
            change
            for number, (before, after) in enumerate(zip(previous, current), 1)
            for change in changes_between(before, after, f"{path}[{number}]")
        ]
    if encode(previous) == encode(current):
        return []
    return [{"field": path, "before": previous, "after": current}]


def required_text(value, label, max_length):
    if not isinstance(value, str) or not 0 < len(value.strip()) <= max_length:
        raise ValueError(f"{label} phải có từ 1 đến {max_length} ký tự.")
    return value.strip()


def normalize(channel, raw):
    if not isinstance(raw, dict):
        raise ValueError("Tệp JSON phải chứa một đơn hàng.")
    if not isinstance(channel, str) or channel not in CHANNELS:
        raise ValueError("Chọn một trong năm sàn được hỗ trợ.")
    _, source_fields, keys = CHANNELS[channel]
    account, ref, market, currency, lines = (raw.get(field) for field in source_fields)
    status_field = {"amazon": "OrderStatus", "ebay": "orderCancelStatus", "taobao": "status",
                    "etsy": "status", "aliexpress": "order_status"}[channel]
    source_status = raw.get("source_status", raw.get(status_field))
    if isinstance(source_status, str):
        source_status = source_status.strip().upper()
        if source_status in {"CANCELED", "CANCELLED", "CANCEL_COMPLETE"}:
            source_status = "CANCELED"
    if source_status == "CANCELED" and lines is None:
        lines = []
    market = raw.get("destination_country", market)
    if not all(isinstance(v, str) and CODE.fullmatch(v) for v in (account, ref)):
        raise ValueError("Tài khoản bán và mã đơn nguồn phải theo định dạng hợp lệ.")
    if not isinstance(lines, list) or len(lines) > 100 or not all(isinstance(line, dict) for line in lines):
        raise ValueError("Danh sách dòng hàng phải là mảng JSON tối đa 100 dòng.")
    order = {
        "channel": channel, "account": account, "reference": ref, "market": market, "currency": currency,
        "lines": [dict(zip(("listing", "quantity", "price"), (line.get(k) for k in keys))) for line in lines],
    }
    if "financial_summary" in raw:
        order["financial"] = raw["financial_summary"]
    if isinstance(raw.get("placed_at"), str):
        order["placed_at"] = raw["placed_at"].strip()
    if isinstance(raw.get("delivery"), dict):
        order["delivery"] = {field: raw["delivery"][field].strip()
                             for field in ("city", "postal_code", "service")
                             if isinstance(raw["delivery"].get(field), str)}
    if isinstance(source_status, str) and source_status:
        order["source_status"] = source_status
    return order


def parse_amount(value, currency, label, limit=100000000):
    if type(value) not in (int, float) or not 0 <= value <= limit:
        raise ValueError(f"{label} phải là số tiền {currency} hợp lệ.")
    amount = Decimal(str(value))
    rounded = amount.quantize(Decimal(1).scaleb(-CURRENCY_PLACES[currency]))
    if amount != rounded:
        raise ValueError(f"{label} vượt độ chính xác của tiền tệ {currency}.")
    return rounded


def validate_totals(financial, lines, currency):
    labels = {"shipping": "Phí giao hàng", "discount": "Giảm giá", "tax": "Thuế nguồn", "total": "Tổng nguồn"}
    if not isinstance(financial, dict) or any(key not in financial for key in labels):
        raise ValueError("Chi tiết đối soát cần phí giao hàng, giảm giá, thuế và tổng tiền nguồn.")
    amounts = {key: parse_amount(financial[key], currency, label, 100000000000000) for key, label in labels.items()}
    subtotal = sum(Decimal(str(line["price"])) * line["quantity"] for line in lines)
    calculated = subtotal + amounts["shipping"] - amounts["discount"] + amounts["tax"]
    difference = amounts["total"] - calculated
    if difference:
        digits = CURRENCY_PLACES[currency]
        def format_amount(value, signed=False):
            return f"{value:{'+' if signed else ''},.{digits}f}".translate(str.maketrans(",.", ".,"))
        raise ValueError(f"Tổng nguồn {format_amount(amounts['total'])} {currency}; "
                         f"tính lại {format_amount(calculated)} {currency}; lệch {format_amount(difference, signed=True)} {currency}.")
    if any(amounts[key] for key in ("shipping", "discount", "tax")):
        raise ValueError("Tổng nguồn đã khớp. Cần cấu hình ánh xạ phí giao hàng, giảm giá và thuế trước khi gửi Odoo.")


def relational_id(value):
    if isinstance(value, dict):
        return value["id"]
    return value if type(value) is int else value[0]


class OdooRejected(Exception):
    pass


class Odoo:
    def __init__(self, key):
        self.key = key

    def call(self, model, method, body):
        request = Request(
            f"{ODOO_URL}/json/2/{model}/{method}", encode(body).encode(),
            {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=15) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code >= 500 or exc.code == 408:
                raise RuntimeError(f"Odoo HTTP {exc.code}") from exc
            raise OdooRejected(f"Odoo từ chối yêu cầu ({model}/{method}, HTTP {exc.code}).") from exc

    def search(self, model, field, value, fields, limit=2):
        return self.call(model, "search_read", {"domain": [[field, "=", value]], "fields": fields, "limit": limit})

    def partner_and_pricelist(self, currency_code="VND"):
        source = self.search("sale.order", "name", ODOO_FIXTURE_QUOTE, ["partner_id", "pricelist_id", "currency_id"])
        if len(source) != 1:
            raise OdooRejected(f"Không tìm được đúng một báo giá thử {ODOO_FIXTURE_QUOTE} qua API.")
        fixture = source[0]
        currency = self.currency_code(fixture["currency_id"])
        if not fixture["partner_id"]:
            raise OdooRejected(f"Báo giá thử {ODOO_FIXTURE_QUOTE} chưa có khách hàng.")
        values = {"partner_id": relational_id(fixture["partner_id"])}
        if currency == currency_code:
            if fixture["pricelist_id"]:
                values["pricelist_id"] = relational_id(fixture["pricelist_id"])
            return values
        currencies = self.search("res.currency", "name", currency_code, ["id"], 1)
        if not currencies:
            raise OdooRejected(f"Tiền tệ {currency_code} chưa được bật trong Odoo.")
        pricelists = self.search("product.pricelist", "currency_id", currencies[0]["id"], ["id"])
        if len(pricelists) != 1:
            raise OdooRejected(f"Cần đúng một bảng giá Odoo đang hoạt động cho {currency_code}.")
        return {**values, "pricelist_id": pricelists[0]["id"]}

    def currency_code(self, currency):
        if not currency:
            return None
        matches = self.call("res.currency", "search_read", {
            "domain": [["id", "=", relational_id(currency)]],
            "fields": ["name"],
            "limit": 1,
            "context": {"active_test": False},
        })
        return matches[0]["name"] if matches else None

    def provision_products(self):
        for sku, name, _ in catalog():
            if sku not in ("HK-ORG-001", "ST-NBK-001"):
                continue
            matches = self.search("product.product", "default_code", sku, ["id"])
            if len(matches) > 1:
                raise OdooRejected(f"SKU {sku} bị trùng trên Odoo.")
            if not matches:
                self.call("product.template", "create", {"vals_list": [
                    {"name": name, "default_code": sku, "sale_ok": True},
                ]})
            matches = self.search("product.product", "default_code", sku, ["id"])
            if len(matches) != 1:
                raise OdooRejected(f"Odoo không trả về đúng một biến thể cho {sku}.")
            print(f"SKU {sku} ready (product ID {matches[0]['id']})")

    def resolve(self, lines):
        resolved = []
        for line in lines:
            products = self.search("product.product", "default_code", line["sku"], ["id"])
            if len(products) != 1:
                raise OdooRejected(f"Odoo không có đúng một biến thể cho SKU {line['sku']}.")
            resolved.append({**line, "product_id": products[0]["id"]})
        return resolved

    def lookup(self, ref):
        return self.search("sale.order", "client_order_ref", ref, ["id", "name", "state", "client_order_ref", "currency_id"])

    def create(self, values):
        return self.call("sale.order", "create", {"vals_list": [values]})[0]

    def verify(self, quotation, reference, lines, currency):
        actual = self.search("sale.order.line", "order_id", quotation["id"],
                             ["product_id", "product_uom_qty", "price_unit"], 101)
        actual_items = sorted((relational_id(row["product_id"]), row["product_uom_qty"], Decimal(str(row["price_unit"]))) for row in actual)
        expected = sorted((row["product_id"], row["quantity"], Decimal(str(row["price"]))) for row in lines)
        currency_name = self.currency_code(quotation["currency_id"])
        if quotation["client_order_ref"] != reference or quotation["state"] != "draft" or currency_name != currency or actual_items != expected:
            raise ValueError("Báo giá Odoo không khớp mã nguồn, tiền tệ, trạng thái hoặc dòng hàng.")
        return actual_items


class Orders:
    def __init__(self, database, odoo):
        self.database = database
        self.odoo = odoo
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS products (sku TEXT PRIMARY KEY, name TEXT, category TEXT);
                CREATE TABLE IF NOT EXISTS mappings (channel TEXT, account TEXT, listing TEXT, sku TEXT REFERENCES products,
                    PRIMARY KEY(channel, account, listing));
                CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, channel TEXT, account TEXT, reference TEXT,
                    fingerprint TEXT, payload TEXT, raw TEXT, status TEXT, detail TEXT DEFAULT '', resolved TEXT,
                    odoo_id INTEGER, quotation TEXT, UNIQUE(channel, account, reference));
                CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders,
                    at TEXT, message TEXT);
                CREATE TABLE IF NOT EXISTS exception_cases (order_id INTEGER PRIMARY KEY REFERENCES orders,
                    assignee TEXT NOT NULL, original_raw TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions (
                    id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES exception_cases(order_id),
                    raw TEXT NOT NULL, previous_raw TEXT NOT NULL, submitted_by TEXT NOT NULL,
                    reason TEXT NOT NULL, status TEXT NOT NULL, reviewed_by TEXT, decision TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_revision
                    ON revisions(order_id) WHERE status='pending';
                CREATE TABLE IF NOT EXISTS revision_snapshots (revision_id INTEGER PRIMARY KEY REFERENCES revisions,
                    lines TEXT NOT NULL, mapping_only INTEGER NOT NULL DEFAULT 0, currency TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS source_cancellations (order_id INTEGER PRIMARY KEY REFERENCES orders,
                    raw TEXT NOT NULL, received_at TEXT NOT NULL);
            """)
            if not db.execute("SELECT 1 FROM products LIMIT 1").fetchone():
                db.executemany("INSERT INTO products VALUES (?, ?, ?)", catalog())
            else:
                db.executemany("UPDATE products SET name=? WHERE sku=? AND name LIKE '% / Series __'",
                               ((name, sku) for sku, name, _ in catalog()))
            db.executemany("INSERT OR IGNORE INTO mappings VALUES (?, ?, ?, ?)", MAPPINGS)
            for order in db.execute("SELECT id FROM orders WHERE status='sending'").fetchall():
                self.status(db, order["id"], "review", "Ứng dụng đã khởi động lại trong lúc gửi. Cần đối soát Odoo.")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def event(self, db, order_id, message):
        db.execute("INSERT INTO events(order_id, at, message) VALUES (?, ?, ?)",
                   (order_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), message))

    def status(self, db, order_id, status, detail, quotation=None):
        if quotation:
            db.execute("UPDATE orders SET status=?, detail=?, odoo_id=?, quotation=? WHERE id=?",
                       (status, detail, quotation["id"], quotation["name"], order_id))
        else:
            db.execute("UPDATE orders SET status=?, detail=? WHERE id=?", (status, detail, order_id))
        self.event(db, order_id, detail)

    def fetch(self, order_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if row is None:
                raise ValueError("Không tìm thấy đơn.")
            order = dict(row)
            order["odoo_url"] = f"{ODOO_URL}/odoo/sales/{order['odoo_id']}" if ODOO_URL and order["odoo_id"] else None
            for field in ("payload", "raw", "resolved"):
                order[field] = json.loads(order[field]) if order[field] else None
            order["events"] = [dict(row) for row in db.execute(
                "SELECT at, message FROM events WHERE order_id=? ORDER BY id", (order_id,))]
            cancellation = db.execute("SELECT received_at,raw FROM source_cancellations WHERE order_id=?", (order_id,)).fetchone()
            order["cancellation"] = {"received_at": cancellation["received_at"], "raw": json.loads(cancellation["raw"])} if cancellation else None
            case = db.execute("SELECT * FROM exception_cases WHERE order_id=?", (order_id,)).fetchone()
            order["case"] = None
            if case:
                revisions = []
                for row in db.execute("""SELECT revisions.*, revision_snapshots.lines, revision_snapshots.mapping_only,
                    revision_snapshots.currency FROM revisions LEFT JOIN revision_snapshots ON revision_snapshots.revision_id=revisions.id
                    WHERE revisions.order_id=? ORDER BY revisions.id DESC""", (order_id,)):
                    revision = dict(row)
                    previous = json.loads(revision.pop("previous_raw"))
                    current = json.loads(revision.pop("raw"))
                    revision["changes"] = changes_between(previous, current)
                    revision["lines"] = json.loads(revision["lines"]) if revision["lines"] else None
                    revisions.append(revision)
                order["case"] = {"assignee": case["assignee"], "original": json.loads(case["original_raw"]),
                                 "revisions": revisions}
            return order

    def assign(self, order_id, assignee):
        assignee = required_text(assignee, "Người phụ trách", 80)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            order = db.execute("SELECT status,raw FROM orders WHERE id=?", (order_id,)).fetchone()
            if not order or order["status"] != "exception" or self.is_canceled(db, order_id):
                raise ValueError("Chỉ giao người xử lý khi đơn đang ở Ngoại lệ.")
            db.execute("INSERT INTO exception_cases VALUES (?,?,?) "
                       "ON CONFLICT(order_id) DO UPDATE SET assignee=excluded.assignee",
                       (order_id, assignee, order["raw"]))
            self.event(db, order_id, f"Đã giao ngoại lệ cho {assignee}.")

    def submit_revision(self, order_id, raw, reason, mapping_only=False):
        reason = required_text(reason, "Lý do sửa", 300)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            order = db.execute("""SELECT orders.*, exception_cases.assignee FROM orders
                JOIN exception_cases ON exception_cases.order_id=orders.id WHERE orders.id=?""", (order_id,)).fetchone()
            if not order or order["status"] != "exception" or self.is_canceled(db, order_id):
                raise ValueError("Hãy giao người xử lý trước khi gửi bản sửa cho đơn ngoại lệ.")
            if mapping_only:
                raw = json.loads(order["raw"])
            candidate = normalize(order["channel"], raw)
            if (candidate["account"], candidate["reference"]) != (order["account"], order["reference"]):
                raise ValueError("Bản sửa phải có cùng tài khoản bán và mã đơn nguồn.")
            if db.execute("SELECT 1 FROM revisions WHERE order_id=? AND status='pending'", (order_id,)).fetchone():
                raise ValueError("Cần quyết định bản đang chờ xác nhận trước khi nộp bản mới.")
            if encode(candidate) == order["payload"] and not mapping_only:
                raise ValueError("Bản sửa chưa thay đổi trường nghiệp vụ nào.")
            lines = self.validate(db, candidate) if mapping_only else None
            cursor = db.execute("""INSERT INTO revisions(order_id,raw,previous_raw,submitted_by,reason,status)
                VALUES (?,?,?,?,?,'pending')""", (order_id, encode(raw), order["raw"], order["assignee"], reason))
            if lines:
                db.execute("INSERT INTO revision_snapshots VALUES (?,?,1,?)",
                           (cursor.lastrowid, encode(lines), candidate["currency"]))
            self.event(db, order_id, f"Đã nhận bản sửa #{cursor.lastrowid} từ {order['assignee']}. Chờ xác nhận.")

    def decide_revision(self, order_id, revision_id, reviewer, decision, note):
        reviewer = required_text(reviewer, "Người xác nhận", 80)
        if type(revision_id) is not int or decision not in ("approve", "reject"):
            raise ValueError("Phiên bản hoặc quyết định không hợp lệ.")
        if decision == "reject":
            note = required_text(note, "Lý do trả lại", 300)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            revision = db.execute("""SELECT revisions.*, orders.channel, orders.status AS order_status,
                    revision_snapshots.lines AS proposed
                FROM revisions JOIN orders ON orders.id=revisions.order_id
                LEFT JOIN revision_snapshots ON revision_snapshots.revision_id=revisions.id
                WHERE revisions.id=? AND revisions.order_id=? AND revisions.status='pending'""",
                                  (revision_id, order_id)).fetchone()
            if not revision or revision["order_status"] != "exception" or self.is_canceled(db, order_id):
                raise ValueError("Bản sửa này không còn chờ xác nhận.")
            if reviewer.casefold() == revision["submitted_by"].casefold():
                raise ValueError("Người xác nhận cần khác người gửi bản sửa.")
            if decision == "reject":
                result, message = "rejected", f"{reviewer} trả lại bản sửa #{revision_id}. {note}"
            else:
                candidate = normalize(revision["channel"], json.loads(revision["raw"]))
                try:
                    lines = self.validate(db, candidate)
                    if revision["proposed"] and revision["proposed"] != encode(lines):
                        raise ValueError("Ánh xạ đã đổi sau khi gửi bản đề xuất. Hãy gửi phiên bản mới.")
                except ValueError as exc:
                    result = "invalid"
                    message = f"{reviewer} đã xác nhận bản sửa #{revision_id}. Kiểm tra lại chưa đạt: {exc}"
                    self.status(db, order_id, "exception", message)
                else:
                    payload = encode(candidate)
                    fingerprint = hashlib.sha256(payload.encode()).hexdigest()
                    db.execute("INSERT OR IGNORE INTO revision_snapshots VALUES (?,?,0,?)",
                               (revision_id, encode(lines), candidate["currency"]))
                    db.execute("UPDATE orders SET raw=?,payload=?,fingerprint=?,resolved=? WHERE id=?",
                               (revision["raw"], payload, fingerprint, encode(lines), order_id))
                    result = "approved"
                    message = f"{reviewer} đã xác nhận bản sửa #{revision_id}. Kiểm tra lại đạt, sẵn sàng bàn giao Odoo."
                    self.status(db, order_id, "ready", message)
            db.execute("UPDATE revisions SET status=?,reviewed_by=?,decision=? WHERE id=?",
                       (result, reviewer, message, revision_id))
            if decision == "reject":
                self.event(db, order_id, message)

    @staticmethod
    def is_canceled(db, order_id):
        return db.execute("SELECT 1 FROM source_cancellations WHERE order_id=?", (order_id,)).fetchone() is not None

    def ingest(self, channel, raw):
        order = normalize(channel, raw)
        payload = encode(order)
        signature = hashlib.sha256(payload.encode()).hexdigest()
        identity = (channel, order["account"], order["reference"])
        with self.connection() as db:
            cursor = db.execute("INSERT OR IGNORE INTO orders(channel,account,reference,fingerprint,payload,raw,status)"
                                " VALUES (?,?,?,?,?,?,'received')", (*identity, signature, payload, encode(raw)))
            row = db.execute("SELECT id, fingerprint FROM orders WHERE channel=? AND account=? AND reference=?", identity).fetchone()
            order_id = row["id"]
            if order.get("source_status") == "CANCELED":
                receipt = db.execute("INSERT OR IGNORE INTO source_cancellations VALUES (?,?,?)",
                                     (order_id, encode(raw), datetime.now(timezone.utc).isoformat(timespec="seconds")))
                if receipt.rowcount:
                    self.event(db, order_id, "Đã ghi nhận đơn bị hủy trên sàn. Kiểm tra chứng từ Odoo liên quan.")
                return order_id, "canceled"
            if self.is_canceled(db, order_id):
                return order_id, "canceled"
            if not cursor.rowcount:
                if row["fingerprint"] != signature:
                    self.event(db, order_id, "Từ chối đơn cùng mã nguồn vì nội dung đã thay đổi.")
                    return order_id, "conflict"
                self.event(db, order_id, "Nhận lại cùng dữ liệu. Dùng bản ghi hiện có.")
                return order_id, "duplicate"
            self.event(db, order_id, "Đã nhận và chuẩn hóa đơn.")
        self.process(order_id)
        return order_id, "new"

    def preview(self, channel, raw):
        payload = normalize(channel, raw)
        signature = hashlib.sha256(encode(payload).encode()).hexdigest()
        with self.connection() as db:
            db.execute("BEGIN")
            existing = db.execute("SELECT id,fingerprint,status,quotation FROM orders WHERE channel=? AND account=? AND reference=?",
                                  (channel, payload["account"], payload["reference"])).fetchone()
            existing_order = None
            if existing:
                existing_order = {"id": existing["id"], "status": existing["status"], "quotation": existing["quotation"],
                                  "matches": existing["fingerprint"] == signature,
                                  "canceled": self.is_canceled(db, existing["id"])}
            error = None
            try:
                if payload.get("source_status") != "CANCELED":
                    self.validate(db, payload)
            except ValueError as exc:
                error = str(exc)
            rows = []
            for line in payload["lines"]:
                listing = line["listing"]
                mapping = None
                if isinstance(listing, str):
                    mapping = db.execute("SELECT sku FROM mappings WHERE channel=? AND account=? AND listing=?",
                                         (channel, payload["account"], listing)).fetchone()
                rows.append({**line, "sku": mapping["sku"] if mapping else None})
        return {"order": {**payload, "lines": rows}, "error": error, "existing": existing_order,
                "cancellation": payload.get("source_status") == "CANCELED"}

    def map_sku(self, channel, account, listing, sku):
        if not isinstance(channel, str) or channel not in CHANNELS or not all(isinstance(v, str) and CODE.fullmatch(v) for v in (account, listing, sku)):
            raise ValueError("Ánh xạ có mã không hợp lệ.")
        with self.connection() as db:
            if not db.execute("SELECT 1 FROM products WHERE sku=?", (sku,)).fetchone():
                raise ValueError("SKU chưa có trong danh mục.")
            db.execute("INSERT INTO mappings VALUES (?,?,?,?) ON CONFLICT(channel,account,listing) DO UPDATE SET sku=excluded.sku",
                       (channel, account, listing, sku))

    def validate(self, db, payload):
        if payload.get("source_status") == "CANCELED":
            raise ValueError("Đơn đã hủy trên sàn. Hãy dùng bước ghi nhận hủy trước khi bàn giao.")
        currency = payload["currency"]
        if not isinstance(currency, str) or currency not in CURRENCY_PLACES:
            raise ValueError("Tiền tệ nằm ngoài phạm vi kiểm thử.")
        market = market_code(payload["market"])
        if not isinstance(market, str) or market not in MARKETS:
            raise ValueError("Thiếu hoặc sai thị trường của đơn mẫu.")
        if not payload["lines"]:
            raise ValueError("Đơn phải có ít nhất một dòng sản phẩm.")
        lines = []
        for index, line in enumerate(payload["lines"], 1):
            listing, quantity, price = (line[k] for k in ("listing", "quantity", "price"))
            if not isinstance(listing, str) or not CODE.fullmatch(listing):
                raise ValueError(f"Dòng {index} có mã sản phẩm không hợp lệ.")
            if type(quantity) is not int or not 0 < quantity <= 10000:
                raise ValueError(f"Dòng {index} phải có số lượng nguyên dương không quá 10.000.")
            parse_amount(price, currency, f"Đơn giá dòng {index}")
        if "financial" in payload:
            validate_totals(payload["financial"], payload["lines"], currency)
        for index, line in enumerate(payload["lines"], 1):
            listing = line["listing"]
            mapping = db.execute("SELECT sku FROM mappings WHERE channel=? AND account=? AND listing=?",
                                 (payload["channel"], payload["account"], listing)).fetchone()
            if not mapping:
                raise ValueError(f"Dòng {index} chưa ánh xạ mã {listing}.")
            lines.append({**line, "sku": mapping["sku"]})
        return lines

    def process(self, order_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if order is None:
                raise ValueError("Không tìm thấy đơn.")
            if self.is_canceled(db, order_id):
                raise ValueError("Đơn đã hủy trên sàn. Kiểm tra chứng từ liên quan trước khi xử lý tiếp.")
            previous = order["status"]
            if previous in ("sending", "synced"):
                return
            case = db.execute("SELECT 1 FROM exception_cases WHERE order_id=?", (order_id,)).fetchone()
            revision = db.execute("""SELECT revisions.id, revisions.status, revision_snapshots.lines
                FROM revisions LEFT JOIN revision_snapshots ON revision_snapshots.revision_id=revisions.id
                WHERE order_id=? ORDER BY revisions.id DESC LIMIT 1""", (order_id,)).fetchone() if case else None
            if previous == "exception":
                if case and (not revision or revision["status"] != "approved"):
                    raise ValueError("Hãy nhận và xác nhận bản sửa trước khi bàn giao Odoo.")
            try:
                lines = json.loads(order["resolved"]) if previous == "review" else self.validate(db, json.loads(order["payload"]))
                if previous != "review" and case and (not revision or revision["lines"] != encode(lines)):
                    previous_lines = json.loads(revision["lines"]) if revision and revision["lines"] else []
                    changes = [f"dòng {i} ({before['sku']} → {after['sku']})"
                               for i, (before, after) in enumerate(zip(previous_lines, lines), 1) if before != after]
                    detail = "Cần xác nhận lại bộ dòng trước khi gửi"
                    if changes:
                        detail += ": " + ", ".join(changes)
                    if revision:
                        db.execute("UPDATE revisions SET status='stale' WHERE id=?", (revision["id"],))
                    self.status(db, order_id, "exception", detail + ".")
                    return
            except ValueError as exc:
                if previous != "review" and revision and revision["status"] == "approved":
                    db.execute("UPDATE revisions SET status='stale' WHERE id=?", (revision["id"],))
                self.status(db, order_id, "exception", str(exc))
                return
            if self.odoo is None:
                self.status(db, order_id, "ready", "Dữ liệu hợp lệ. Backend chưa được cấp khóa Odoo.")
                return
            db.execute("UPDATE orders SET resolved=? WHERE id=?", (encode(lines), order_id))
            self.status(db, order_id, "sending", "Đang đối chiếu với Odoo.")

        reference = f"{order['channel'].upper()}/{order['account']}/{order['reference']}"
        currency = json.loads(order["payload"])["currency"]
        ambiguous = previous == "review"
        quotation = None
        try:
            if any("product_id" not in line for line in lines):
                lines = self.odoo.resolve(lines)
            with self.connection() as db:
                db.execute("UPDATE orders SET resolved=? WHERE id=?", (encode(lines), order_id))
            found = self.odoo.lookup(reference)
            if len(found) > 1:
                ambiguous = True
                raise ValueError("Có nhiều báo giá Odoo cùng mã nguồn. Cần đối soát.")
            if not found:
                if ambiguous:
                    raise ValueError("Chưa tìm thấy báo giá. Cần kiểm tra Odoo trước khi tạo lại.")
                values = self.odoo.partner_and_pricelist(currency)
                values.update(client_order_ref=reference, order_line=[
                    [0, 0, {"product_id": line["product_id"], "product_uom_qty": line["quantity"], "price_unit": line["price"]}]
                    for line in lines
                ])
                with self.connection() as db:
                    if self.is_canceled(db, order_id):
                        raise ValueError("Đã nhận thông tin hủy trong lúc chuẩn bị gửi Odoo.")
                ambiguous = True
                try:
                    created = self.odoo.create(values)
                except OdooRejected:
                    ambiguous = False
                    raise
                found = self.odoo.lookup(reference)
                if len(found) != 1 or found[0]["id"] != created:
                    raise ValueError("Chưa đối chiếu được ID báo giá Odoo.")
            ambiguous = True
            quotation = found[0]
            self.odoo.verify(quotation, reference, lines, currency)
            status, detail = "synced", f"Đã đối chiếu báo giá {quotation['name']} với {len(lines)} dòng."
        except (ValueError, OdooRejected) as exc:
            status, detail = ("review" if ambiguous else "exception"), str(exc)
        except Exception as exc:
            status, detail = ("review" if ambiguous else "exception"), f"Lỗi tích hợp Odoo ({type(exc).__name__})."
        with self.connection() as db:
            self.status(db, order_id, status, detail, quotation if status == "synced" else None)

    def overview(self):
        with self.connection() as db:
            return {
                "orders": [dict(row) for row in db.execute("""
                    SELECT orders.id,channel,account,reference,orders.status,quotation,detail,
                        json_extract(payload,'$.market') AS market,
                        json_extract(payload,'$.currency') AS currency,
                        exception_cases.assignee,
                        EXISTS(SELECT 1 FROM source_cancellations WHERE order_id=orders.id) AS canceled,
                        (SELECT status FROM revisions WHERE order_id=orders.id ORDER BY id DESC LIMIT 1) AS revision_status
                    FROM orders LEFT JOIN exception_cases ON exception_cases.order_id=orders.id
                    ORDER BY orders.id DESC
                """)],
                "products": [dict(row) for row in db.execute("SELECT * FROM products ORDER BY sku")],
                "mappings": [dict(row) for row in db.execute("SELECT * FROM mappings ORDER BY channel,account,listing")],
                "live": self.odoo is not None,
            }


def handler_for(orders, port):
    class Handler(BaseHTTPRequestHandler):
        def prepare_order_action(self, body):
            return body

        def respond(self, value, status=200, content_type="application/json"):
            data = value if isinstance(value, bytes) else encode(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                self.respond((ROOT / "index.html").read_bytes(), content_type="text/html")
            elif path == "/api/overview":
                self.respond(orders.overview())
            elif path == "/samples/total-mismatch.json":
                self.respond(TOTAL_MISMATCH_SAMPLE)
            elif path == "/samples/missing-mapping.json":
                self.respond(MISSING_MAPPING_SAMPLE)
            elif path == "/samples/corrected-mapping.json":
                self.respond(CORRECTED_MAPPING_SAMPLE)
            elif re.fullmatch(r"/samples/operations/[a-z]+(?:-exception)?\.json", path):
                channel, _, fixture = Path(path).stem.partition("-")
                index = "06" if fixture == "exception" else "01"
                sample = ROOT / "samples" / "operations" / "orders" / f"{channel}-{index}.json"
                if channel in CHANNELS and sample.is_file():
                    self.respond(sample.read_bytes())
                else:
                    self.respond({"error": "Không tìm thấy tệp đợt vận hành."}, 404)
            elif path == "/samples/operations/meridian-operations.zip":
                archive = ROOT / "samples" / "operations" / "meridian-operations.zip"
                if archive.is_file():
                    self.respond(archive.read_bytes(), content_type="application/zip")
                else:
                    self.respond({"error": "Không tìm thấy bộ dữ liệu đợt vận hành."}, 404)
            elif re.fullmatch(r"/samples/[a-z-]+\.json", path):
                sample_id = Path(path).stem
                if sample_id in LOCAL_MARKET_SAMPLES:
                    self.respond(LOCAL_MARKET_SAMPLES[sample_id])
                else:
                    self.respond({"error": "Không có mẫu cho nguồn đã chọn."}, 404)
            elif re.fullmatch(r"/api/orders/\d+", path):
                try:
                    self.respond(orders.fetch(int(path.split("/")[-1])))
                except ValueError as exc:
                    self.respond({"error": str(exc)}, 404)
            else:
                self.respond({"error": "Không tìm thấy đường dẫn."}, 404)

        def do_POST(self):
            active_port = port or self.server.server_port
            if self.headers.get("Origin") not in (None, f"http://127.0.0.1:{active_port}", f"http://localhost:{active_port}"):
                self.respond({"error": "Nguồn yêu cầu không được phép."}, 403)
                return
            try:
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("Chỉ nhận nội dung JSON.")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1000000:
                    raise ValueError("Kích thước yêu cầu không hợp lệ.")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("Yêu cầu không hợp lệ.")
                body = self.prepare_order_action(body)
                if self.path == "/api/orders/preview":
                    self.respond(orders.preview(body.get("channel"), body.get("order")))
                elif self.path == "/api/orders":
                    order_id, result = orders.ingest(body.get("channel"), body.get("order"))
                    self.respond({"result": result, "order": orders.fetch(order_id)}, 409 if result == "conflict" else 200)
                elif self.path == "/api/mappings":
                    orders.map_sku(*(body.get(key) for key in ("channel", "account", "listing", "sku")))
                    self.respond({"ok": True})
                elif re.fullmatch(r"/api/orders/\d+/retry", self.path):
                    order_id = int(self.path.split("/")[3])
                    orders.process(order_id)
                    self.respond(orders.fetch(order_id))
                elif re.fullmatch(r"/api/orders/\d+/(assign|revision|decision)", self.path):
                    _, _, _, order_id, action = self.path.split("/")
                    order_id = int(order_id)
                    if action == "assign":
                        orders.assign(order_id, body.get("assignee"))
                    elif action == "revision":
                        orders.submit_revision(order_id, body.get("order"), body.get("reason"),
                                               mapping_only=body.get("mapping_only") is True)
                    else:
                        orders.decide_revision(order_id, body.get("revision_id"), body.get("reviewer"),
                                               body.get("decision"), body.get("note"))
                    self.respond(orders.fetch(order_id))
                else:
                    self.respond({"error": "Không tìm thấy đường dẫn."}, 404)
            except (ValueError, UnicodeError) as exc:
                self.respond({"error": str(exc)}, 400)

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--seed-odoo", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=ROOT / "orders.sqlite3")
    args = parser.parse_args()
    odoo = None
    if args.live or args.seed_odoo:
        base = urlparse(ODOO_URL)
        if base.scheme != "https" or not base.hostname or base.username or base.password or base.path or base.query or base.fragment:
            parser.error("Set ODOO_URL to the HTTPS origin of your Odoo test instance.")
        key = os.getenv("ODOO_API_KEY") or getpass.getpass("Odoo RPC API key: ").strip()
        if not key:
            parser.error("An Odoo RPC key is required.")
        odoo = Odoo(key)
    if args.seed_odoo:
        odoo.provision_products()
        if not args.live:
            return
    if args.live:
        try:
            odoo.partner_and_pricelist()
        except OdooRejected as exc:
            parser.exit(1, f"Odoo preflight: {exc}\n")
        print(f"Odoo Sales ready: {ODOO_FIXTURE_QUOTE} (VND)", flush=True)
    orders = Orders(args.db, odoo)
    print(f"Meridian Order Desk: http://127.0.0.1:{args.port}", flush=True)
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(orders, args.port)) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
