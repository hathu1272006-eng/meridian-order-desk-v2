from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import secrets
import sqlite3
import time

from .reconciliation import evaluate, normalize


SESSION_SECONDS = 8 * 60 * 60
ROLES = {"operator", "reviewer", "auditor"}


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)


class NotFound(ValueError):
    pass


class Conflict(ValueError):
    pass


class Workbench:
    def __init__(self, path):
        self.path = path
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY, label TEXT NOT NULL, role TEXT NOT NULL
                    CHECK (role IN ('operator', 'reviewer', 'auditor')),
                    salt BLOB NOT NULL, password_hash BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES users(username),
                    csrf TEXT NOT NULL, expires INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS login_attempts (
                    id INTEGER PRIMARY KEY, attempted INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY, hash TEXT NOT NULL UNIQUE, period TEXT NOT NULL,
                    as_of TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
                    created_by TEXT NOT NULL REFERENCES users(username), intake INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cases (
                    id INTEGER PRIMARY KEY, baseline_id INTEGER NOT NULL REFERENCES runs(id),
                    channel TEXT NOT NULL, account TEXT NOT NULL, reference TEXT NOT NULL,
                    baseline TEXT NOT NULL, latest TEXT, retest_id INTEGER REFERENCES runs(id),
                    status TEXT NOT NULL CHECK (status IN ('open', 'in_review', 'closed')),
                    prepared_by TEXT REFERENCES users(username), reviewer TEXT REFERENCES users(username)
                );
                CREATE TABLE IF NOT EXISTS case_events (
                    id INTEGER PRIMARY KEY, case_id INTEGER NOT NULL REFERENCES cases(id),
                    action TEXT NOT NULL, actor TEXT NOT NULL REFERENCES users(username),
                    recorded_at TEXT NOT NULL, note TEXT NOT NULL, run_id INTEGER NOT NULL REFERENCES runs(id)
                );
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_user(self, username, label, role, password):
        if role not in ROLES or not 12 <= len(password) <= 128:
            raise ValueError("Vai trò không hợp lệ hoặc mật khẩu ngoài giới hạn 12–128 ký tự.")
        salt = secrets.token_bytes(16)
        with self.connection() as db:
            return db.execute("""
                INSERT INTO users VALUES (?, ?, ?, ?, ?) ON CONFLICT(username) DO NOTHING
            """, (username, label, role, salt, hash_password(password, salt))).rowcount == 1

    def login(self, username, password):
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 80 or len(password) > 128:
            return None
        clock = int(time.time())
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM login_attempts WHERE attempted < ?", (clock - 60,))
            if db.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0] >= 8:
                return None
            user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            candidate = hash_password(password, user["salt"] if user else bytes(16))
            if not user or not hmac.compare_digest(candidate, user["password_hash"]):
                db.execute("INSERT INTO login_attempts(attempted) VALUES (?)", (clock,))
                return None
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            db.execute("DELETE FROM sessions WHERE expires <= ?", (clock,))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                       (digest(token), username, csrf, clock + SESSION_SECONDS))
            return token

    def session(self, token):
        if not token or len(token) != 43:
            return None
        with self.connection() as db:
            row = db.execute("""
                SELECT users.username, users.label, users.role, sessions.csrf
                FROM sessions JOIN users USING(username)
                WHERE token_hash = ? AND expires > ?
            """, (digest(token), int(time.time()))).fetchone()
            return dict(row) if row else None

    def logout(self, token):
        with self.connection() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (digest(token),))

    def snapshot(self, db, data, actor, intake):
        payload = json_text(data)
        key = digest(payload)
        existing = db.execute("SELECT id, intake FROM runs WHERE hash = ?", (key,)).fetchone()
        if existing:
            if intake and not existing["intake"]:
                db.execute("UPDATE runs SET intake = 1 WHERE id = ?", (existing["id"],))
                return existing["id"], True
            return existing["id"], False
        cursor = db.execute("""
            INSERT INTO runs(hash, period, as_of, payload, created_at, created_by, intake)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (key, data["period"], data["as_of"], payload, now(), actor, intake))
        return cursor.lastrowid, True

    def intake(self, raw, actor):
        dataset = normalize(raw)
        results = evaluate(dataset)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            run_id, created = self.snapshot(db, dataset, actor, True)
            if created:
                for item in results:
                    if item["flags"]:
                        cursor = db.execute("""
                            INSERT INTO cases(baseline_id, channel, account, reference, baseline, status)
                            VALUES (?, ?, ?, ?, ?, 'open')
                        """, (run_id, item["channel"], item["account"], item["reference"], json_text(item)))
                        self.event(db, cursor.lastrowid, "detected", actor, "Quy tắc đối soát phát hiện ngoại lệ.", run_id)
        return {"run_id": run_id, "created": created, "rows": len(results),
                "findings": sum(bool(item["flags"]) for item in results),
                "pending": sum(item["pending"] for item in results)}

    def overview(self):
        with self.connection() as db:
            runs = db.execute("""
                SELECT id, period, as_of, created_at, created_by, hash FROM runs WHERE intake = 1 ORDER BY id DESC
            """).fetchall()
            cases = db.execute("""
                SELECT cases.id, channel, account, reference, status, prepared_by, reviewer, period, as_of,
                       (latest IS NOT NULL) AS has_retest, baseline FROM cases
                JOIN runs ON baseline_id = runs.id ORDER BY cases.id DESC
            """).fetchall()
        rows = []
        for row in cases:
            item = dict(row)
            item["flags"] = json.loads(item.pop("baseline"))["flags"]
            rows.append(item)
        return {"runs": [dict(row) for row in runs], "cases": rows}

    def case(self, case_id):
        with self.connection() as db:
            row = self.case_row(db, case_id)
            result = dict(row)
            for field in ("baseline", "latest"):
                result[field] = json.loads(result[field]) if result[field] else None
            result["events"] = [dict(event) for event in db.execute("""
                SELECT action, actor, recorded_at, note, run_id, runs.hash FROM case_events
                JOIN runs ON run_id = runs.id WHERE case_id = ? ORDER BY case_events.id
            """, (case_id,))]
        return result

    def run(self, run_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if not row:
                raise NotFound("Không tìm thấy ảnh chụp dữ liệu.")
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def case_row(self, db, case_id):
        row = db.execute("""
            SELECT cases.*, runs.period, runs.as_of FROM cases JOIN runs ON baseline_id = runs.id
            WHERE cases.id = ?
        """, (case_id,)).fetchone()
        if not row:
            raise NotFound("Không tìm thấy ngoại lệ.")
        return row

    def event(self, db, case_id, action, actor, note, run_id):
        db.execute("INSERT INTO case_events(case_id, action, actor, recorded_at, note, run_id) VALUES (?, ?, ?, ?, ?, ?)",
                   (case_id, action, actor, now(), note, run_id))

    def submit(self, case_id, note, actor):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.case_row(db, case_id)
            if row["status"] != "open":
                raise Conflict("Chỉ có thể gửi khắc phục khi ngoại lệ đang mở.")
            db.execute("UPDATE cases SET status = 'in_review', prepared_by = ? WHERE id = ?", (actor, case_id))
            self.event(db, case_id, "submitted", actor, note, row["retest_id"] or row["baseline_id"])
        return self.case(case_id)

    def retest(self, case_id, raw, note, actor):
        dataset = normalize(raw)
        results = evaluate(dataset)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.case_row(db, case_id)
            if row["status"] != "in_review" or actor == row["prepared_by"]:
                raise Conflict("Ngoại lệ cần ở trạng thái Chờ soát xét và do tài khoản khác kiểm tra.")
            baseline = json.loads(db.execute("SELECT payload FROM runs WHERE id = ?", (row["baseline_id"],)).fetchone()[0])
            if any(dataset[key] != baseline[key] for key in ("period", "as_of", "orders")):
                raise ValueError("Tái kiểm thử cần giữ nguyên kỳ, ngày chốt và dữ liệu đơn nguồn.")
            key = (row["channel"], row["account"], row["reference"])
            result = next((x for x in results if (x["channel"], x["account"], x["reference"]) == key), None)
            if result is None:
                # A payout referring to a nonexistent order was removed from the corrected source.
                result = {"channel": key[0], "account": key[1], "reference": key[2], "flags": [],
                          "expected": None, "actual": {}, "order": None, "payouts": [], "pending": False}
            run_id, _ = self.snapshot(db, dataset, actor, False)
            status = "open" if result["flags"] else "closed"
            db.execute("UPDATE cases SET status = ?, reviewer = ?, latest = ?, retest_id = ? WHERE id = ?",
                       (status, actor, json_text(result), run_id, case_id))
            self.event(db, case_id, "retest_failed" if result["flags"] else "retest_passed", actor, note, run_id)
        return self.case(case_id)
