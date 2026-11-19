import argparse
import getpass
import hmac
import json
import os
import re
import secrets
from http.cookies import CookieError, SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from app import ODOO_URL, Odoo, OdooRejected, Orders, handler_for
from .workbench import Conflict, NotFound, SESSION_SECONDS, Workbench


ROOT = Path(__file__).resolve().parent.parent
COOKIE = "meridian_v2_session"
ASSETS = {
    "/": ("audit.html", "text/html"),
    "/client/audit.css": ("audit.css", "text/css"),
    "/client/audit.js": ("audit.js", "text/javascript"),
}
DEMO_FILES = {
    "/samples/audit/baseline.json": "baseline.json",
    "/samples/audit/retest.json": "retest.json",
}


def required_note(body):
    note = body.get("note")
    if not isinstance(note, str) or not 5 <= len(note.strip()) <= 1000:
        raise ValueError("Ghi chú cần từ 5 đến 1.000 ký tự.")
    return note.strip()


def make_handler(workbench, orders, port):
    class Handler(handler_for(orders, port)):
        def end_headers(self):
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            super().end_headers()

        def token(self):
            cookies = SimpleCookie()
            try:
                cookies.load(self.headers.get("Cookie", ""))
            except CookieError:
                return ""
            return cookies[COOKIE].value if COOKIE in cookies else ""

        def cookie(self, value):
            age = SESSION_SECONDS if value else 0
            self.send_response(204)
            self.send_header("Set-Cookie", f"{COOKIE}={value}; Path=/; Max-Age={age}; HttpOnly; SameSite=Strict")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def local_hosts(self):
            return {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

        def reject(self, status, message):
            self.respond({"error": message}, status)

        def require(self, *roles):
            self.user = workbench.session(self.token())
            if not self.user:
                self.reject(401, "Bạn cần đăng nhập.")
                return False
            if roles and self.user["role"] not in roles:
                self.reject(403, "Vai trò hiện tại không có quyền thực hiện thao tác này.")
                return False
            return True

        def body(self):
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Chỉ nhận nội dung JSON.")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 1_000_000:
                raise ValueError("Kích thước yêu cầu không hợp lệ.")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("Yêu cầu không hợp lệ.")
            return body

        def prepare_order_action(self, body):
            if self.path.endswith("/decision"):
                return dict(body, reviewer=self.user["username"])
            return body

        def do_GET(self):
            if self.headers.get("Host") not in self.local_hosts():
                self.reject(400, "Máy chủ chỉ phục vụ địa chỉ cục bộ.")
                return
            path = urlparse(self.path).path
            if path in ASSETS:
                name, media_type = ASSETS[path]
                self._audit_csp = path == "/"
                self.respond((ROOT / "client" / name).read_bytes(), content_type=media_type)
                return
            if not self.require():
                return
            try:
                if path == "/api/session":
                    self.respond(self.user)
                elif path == "/api/audit":
                    self.respond(workbench.overview())
                elif path in DEMO_FILES:
                    self.respond((ROOT / "samples" / "audit" / DEMO_FILES[path]).read_bytes())
                elif re.fullmatch(r"/api/audit/cases/[0-9]{1,12}(?:/evidence)?", path):
                    parts = path.split("/")
                    record = workbench.case(int(parts[4]))
                    if len(parts) == 5:
                        self.respond(record)
                    else:
                        run_ids = sorted({event["run_id"] for event in record["events"]})
                        self.respond({"case": record, "snapshots": [workbench.run(run_id) for run_id in run_ids]})
                else:
                    if path == "/orders":
                        self.path = "/"
                    super().do_GET()
            except NotFound as exc:
                self.reject(404, str(exc))

        def send_response(self, code, message=None):
            super().send_response(code, message)
            if getattr(self, "_audit_csp", False):
                self.send_header("Content-Security-Policy",
                                 "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

        def do_POST(self):
            host = self.headers.get("Host")
            if host not in self.local_hosts() or self.headers.get("Origin") != f"http://{host}":
                self.reject(403, "Nguồn yêu cầu không được phép.")
                return
            path = urlparse(self.path).path
            try:
                if path == "/api/login":
                    if self.headers.get("X-Meridian-Client") != "workbench":
                        self.reject(403, "Yêu cầu đăng nhập không hợp lệ.")
                        return
                    body = self.body()
                    token = workbench.login(body.get("username"), body.get("password"))
                    if not token:
                        self.reject(401, "Thông tin đăng nhập không đúng hoặc cần thử lại sau một phút.")
                        return
                    current_token = self.token()
                    if workbench.session(current_token):
                        workbench.logout(current_token)
                    self.cookie(token)
                    return
                if not self.require():
                    return
                if not hmac.compare_digest(self.headers.get("X-Meridian-CSRF", "").encode(), self.user["csrf"].encode()):
                    self.reject(403, "Phiên thao tác đã thay đổi. Hãy tải lại trang.")
                    return
                self.dispatch(path)
            except NotFound as exc:
                self.reject(404, str(exc))
            except Conflict as exc:
                self.reject(409, str(exc))
            except (ValueError, UnicodeError) as exc:
                self.reject(400, str(exc))

        def dispatch(self, path):
            actor = self.user["username"]
            role = self.user["role"]
            if path == "/api/logout":
                workbench.logout(self.token())
                self.cookie("")
            elif path == "/api/audit/intake":
                if role == "operator":
                    result = workbench.intake(self.body(), actor)
                    self.respond(result, 201 if result["created"] else 200)
                else:
                    self.reject(403, "Chỉ tài khoản vận hành được nạp kỳ đối soát.")
            elif match := re.fullmatch(r"/api/audit/cases/([0-9]{1,12})/(submit|retest)", path):
                case_id, action = int(match[1]), match[2]
                expected_role = "operator" if action == "submit" else "reviewer"
                if role != expected_role:
                    self.reject(403, "Vai trò hiện tại không có quyền xử lý bước này.")
                    return
                body = self.body()
                note = required_note(body)
                result = (workbench.submit(case_id, note, actor) if action == "submit"
                          else workbench.retest(case_id, body.get("dataset"), note, actor))
                self.respond(result)
            elif path.startswith("/api/audit"):
                self.reject(404, "Không tìm thấy thao tác.")
            elif role == ("reviewer" if path.endswith("/decision") else "operator"):
                super().do_POST()
            else:
                self.reject(403, "Tài khoản hiện tại chỉ được thực hiện các thao tác theo vai trò.")

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Meridian v2 - Payout reconciliation and control review")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--audit-db", type=Path, default=ROOT / "audit.sqlite3")
    parser.add_argument("--orders-db", type=Path, default=ROOT / "orders.sqlite3")
    parser.add_argument("--seed-demo", action="store_true")
    parser.add_argument("--odoo", action="store_true")
    args = parser.parse_args()
    if args.audit_db.resolve() == args.orders_db.resolve():
        parser.error("Cần hai tệp SQLite riêng cho đối soát và nghiệp vụ đơn hàng.")
    odoo = None
    if args.odoo:
        base = urlparse(ODOO_URL)
        if base.scheme != "https" or not base.hostname or base.username or base.password or base.path or base.query or base.fragment:
            parser.error("Đặt ODOO_URL thành địa chỉ HTTPS của môi trường thử nghiệm.")
        key = os.getenv("ODOO_API_KEY") or getpass.getpass("Odoo API key: ").strip()
        if not key:
            parser.error("Cần khóa Odoo API.")
        odoo = Odoo(key)
        try:
            odoo.partner_and_pricelist()
        except (OdooRejected, OSError) as exc:
            parser.exit(1, f"Không kết nối được Odoo: {exc}\n")
    workbench = Workbench(args.audit_db)
    for role, label in (
        ("operator", "Vận hành"),
        ("reviewer", "Soát xét"),
        ("auditor", "Kiểm toán"),
    ):
        if args.seed_demo:
            password = secrets.token_urlsafe(18)
            if workbench.add_user(role, label, role, password):
                print(f"Tài khoản demo {role}: {password}", flush=True)
    with ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(workbench, Orders(args.orders_db, odoo), args.port)) as server:
        print(f"Meridian v2: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
