from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
import re


CHANNELS = ("amazon", "ebay", "aliexpress", "etsy", "taobao")
CURRENCIES = ("USD", "EUR", "ZAR", "AUD", "CNY")
CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")
MONEY = re.compile(r"(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,2})?")


def checked_code(value, label):
    if not isinstance(value, str) or not CODE.fullmatch(value):
        raise ValueError(f"{label} không hợp lệ.")
    return value


def checked_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Ngày phải có định dạng YYYY-MM-DD.")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError("Ngày không hợp lệ.") from None


def cents(value):
    if not isinstance(value, str) or not MONEY.fullmatch(value):
        raise ValueError("Số tiền phải là chuỗi có tối đa hai chữ số thập phân.")
    return int(Decimal(value) * 100)


def order_key(row):
    return row["channel"], row["account"], row["reference"]


def normalize_row(row):
    if not isinstance(row, dict):
        raise ValueError("Mỗi dòng dữ liệu phải là một đối tượng JSON.")
    if row.get("channel") not in CHANNELS or row.get("currency") not in CURRENCIES:
        raise ValueError("Sàn hoặc tiền tệ nguồn chưa được hỗ trợ.")
    return {
        "channel": row["channel"],
        "account": checked_code(row.get("account"), "Tài khoản sàn"),
        "reference": checked_code(row.get("reference"), "Mã đơn"),
        "currency": row["currency"],
    }


def normalize(dataset):
    if not isinstance(dataset, dict):
        raise ValueError("Tệp đối soát phải là một đối tượng JSON.")
    cut_off = checked_date(dataset.get("as_of"))
    orders, payouts = dataset.get("orders"), dataset.get("payouts")
    if not isinstance(orders, list) or not isinstance(payouts, list):
        raise ValueError("Tệp cần hai danh sách orders và payouts.")
    if not orders or len(orders) > 5000 or len(payouts) > 5000:
        raise ValueError("Cần ít nhất một đơn và tối đa 5.000 dòng cho mỗi danh sách.")

    order_rows = []
    for raw in orders:
        row = normalize_row(raw)
        gross, fee, refund = (cents(raw.get(field)) for field in ("gross", "fee", "refund"))
        if fee + refund > gross:
            raise ValueError("Tổng phí và hoàn tiền vượt tiền hàng trong phạm vi mô hình.")
        row.update(due=checked_date(raw.get("due")), gross=gross, fee=fee, refund=refund)
        order_rows.append(row)
    if len({order_key(row) for row in order_rows}) != len(order_rows):
        raise ValueError("Có đơn nguồn trùng khóa sàn, tài khoản và mã đơn.")

    payout_rows = []
    for raw in payouts:
        row = normalize_row(raw)
        row.update(line_id=checked_code(raw.get("line_id"), "Mã dòng chi trả"),
                   paid_on=checked_date(raw.get("paid_on")), amount=cents(raw.get("amount")))
        payout_rows.append(row)

    return {
        "period": checked_code(dataset.get("period"), "Mã kỳ"),
        "as_of": cut_off,
        "orders": sorted(order_rows, key=order_key),
        "payouts": sorted(payout_rows, key=lambda r: (*order_key(r), r["line_id"],
                                                     r["paid_on"], r["currency"], r["amount"])),
    }


def evaluate(dataset):
    orders = {order_key(row): row for row in dataset["orders"]}
    groups = defaultdict(list)
    duplicates = Counter()
    for row in dataset["payouts"]:
        if row["paid_on"] <= dataset["as_of"]:
            groups[order_key(row)].append(row)
            duplicates[row["channel"], row["account"], row["line_id"]] += 1

    result = []
    for key in sorted(orders.keys() | groups.keys()):
        order, payouts = orders.get(key), groups.get(key, [])
        expected = order["gross"] - order["fee"] - order["refund"] if order else None
        currency = order["currency"] if order else None
        currencies = defaultdict(int)
        for row in payouts:
            currencies[row["currency"]] += row["amount"]
        flags = []
        if any(duplicates[row["channel"], row["account"], row["line_id"]] > 1 for row in payouts):
            flags.append("duplicate_payout")
        if order is None:
            flags.append("unknown_order")
        elif any(code != currency for code in currencies):
            flags.append("currency_mismatch")
        elif order["due"] <= dataset["as_of"]:
            if not payouts and expected != 0:
                flags.append("missing_payout")
            elif currencies[currency] != expected:
                flags.append("amount_mismatch")
        elif currencies[currency] > expected:
            flags.append("amount_mismatch")
        result.append({
            "channel": key[0], "account": key[1], "reference": key[2], "currency": currency,
            "expected": expected, "actual": {code: amount for code, amount in currencies.items() if amount or payouts},
            "flags": flags, "order": order, "payouts": payouts,
            "pending": bool(order and order["due"] > dataset["as_of"] and not flags),
        })
    return result
