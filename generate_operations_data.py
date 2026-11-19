"""Build the repeatable Meridian operations dataset from synthetic business fixtures."""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from scenarios import CHANNELS, LOCAL_MARKET_SAMPLES, MAPPINGS, OPERATIONS_LISTINGS, OPERATIONS_SKUS, catalog


ROOT = Path(__file__).resolve().parent / "samples" / "operations"
ORDER_DIR = ROOT / "orders"
ROUTES = {
    "amazon": ("US", "-08:00", ("Seattle", "98109"), ("Portland", "97205"), "Standard Ground"),
    "ebay": ("DE", "+01:00", ("Berlin", "10115"), ("Hamburg", "20095"), "DHL Paket"),
    "aliexpress": ("ZA", "+02:00", ("Cape Town", "8001"), ("Johannesburg", "2001"), "Standard delivery"),
    "etsy": ("AU", "+11:00", ("Sydney", "2000"), ("Melbourne", "3000"), "Parcel Post"),
    "taobao": ("JP", "+09:00", ("Tokyo", "100-0001"), ("Osaka", "530-0001"), "Consolidated cross-border parcel"),
}
CURRENCY_PRICES = {
    "USD": ("14.95", "18.50", "27.90", "18.95", "37.50", "5.50", "12.75", "6.95", "9.50", "7.90"),
    "EUR": ("14.95", "17.95", "24.90", "18.50", "34.90", "5.50", "11.90", "6.50", "8.95", "7.50"),
    "ZAR": ("229", "299", "469", "329", "649", "69.90", "179", "109", "139", "99.90"),
    "AUD": ("24.90", "29.90", "44.50", "32.90", "59.50", "8.50", "19.90", "10.90", "14.50", "11.90"),
    "CNY": ("69", "89", "129", "109", "159", "19.90", "49.90", "29.90", "36.90", "24.90"),
}
PRICE_INDEX = {sku: rank for rank, sku in enumerate(OPERATIONS_SKUS)}
SKU_PAIRS = (
    ("HK-ORG-001", "ST-NBK-001"),
    ("ST-NBK-001",),
    ("HK-STO-001", "ST-PEN-001"),
    ("HK-MAT-001", "HK-UTL-001"),
    ("HK-RCK-001", "ST-PLN-001", "ST-FLR-001", "ST-NTD-001"),
)


def source_reference(channel, index):
    if channel == "amazon":
        return f"114-{4380271 + 2137 * index:07d}-{7124890 - 1381 * index:07d}"
    if channel == "ebay":
        return f"17-{13281 + 7 * index:05d}-{60374 + 829 * index:05d}"
    bases = {"aliexpress": 8194725083617440, "etsy": 3517624801, "taobao": 4836721095872411862}
    return str(bases[channel] + 3127 * index)


def make_order(channel, index, channel_index, skus):
    _, (account, reference, market, currency, rows), (listing, quantity, price) = CHANNELS[channel]
    example = LOCAL_MARKET_SAMPLES[channel]
    country, offset, first_city, second_city, service = ROUTES[channel]
    moment = datetime(2026, 11, 12, 9, 24) + timedelta(days=index - 1, minutes=channel_index * 43 + index * 16)
    city, postal_code = first_city if index % 2 else second_city
    amounts = CURRENCY_PRICES[example[currency]]
    return {
        account: example[account], reference: source_reference(channel, index), market: example[market],
        currency: example[currency], "destination_country": country, "placed_at": moment.isoformat(timespec="minutes") + offset,
        "source_status": "PAID",
        "delivery": {"city": city, "postal_code": postal_code, "service": service},
        rows: [
            {listing: OPERATIONS_LISTINGS[channel][sku], quantity: 1 + (index + channel_index + line_index) % 3,
             price: float(Decimal(amounts[PRICE_INDEX[sku]]))}
            for line_index, sku in enumerate(skus)
        ],
    }


def make_exception(channel, channel_index):
    order = make_order(channel, 6, channel_index, SKU_PAIRS[0])
    _, (_, _, _, _, rows), (listing, quantity, price) = CHANNELS[channel]
    if channel in ("amazon", "taobao"):
        order[rows][1][listing] += "-R2"
        return order, "Thiếu ánh xạ listing sau khi đổi mã biến thể"
    if channel == "etsy":
        order[rows][1][quantity] = 0
        return order, "Dòng hàng thiếu số lượng hợp lệ"
    if channel == "aliexpress":
        order[rows][1][price] = -0.01
        return order, "Đơn giá dòng hàng âm"
    subtotal = sum(Decimal(str(line[price])) * line[quantity] for line in order[rows])
    order["financial_summary"] = {"shipping": 4.95, "discount": 2.00, "tax": 1.75, "total": float(subtotal + Decimal("6.20"))}
    return order, "Tổng nguồn cao hơn số tính lại 1,50 EUR"


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def generate():
    ORDER_DIR.mkdir(parents=True, exist_ok=True)
    fixtures = []
    entries = []

    def add(channel, index, order, expected, odoo_ready=False, suffix=""):
        _, (_, ref_key, _, currency_key, rows_key), _ = CHANNELS[channel]
        name = f"{channel}-{index:02d}{suffix}.json"
        fixtures.append(write_json(ORDER_DIR / name, order))
        entries.append({
            "file": f"orders/{name}", "platform": CHANNELS[channel][0], "channel": channel, "order_reference": order[ref_key],
            "destination_country": order.get("destination_country", ROUTES[channel][0]), "currency": order.get(currency_key),
            "lines": len(order.get(rows_key, [])), "expected": expected, "odoo_skus_already_provisioned": odoo_ready,
        })

    for channel_index, channel in enumerate(ROUTES):
        for index, skus in enumerate(SKU_PAIRS, 1):
            add(channel, index, make_order(channel, index, channel_index, skus),
                "Hợp lệ trong Meridian", odoo_ready=index in (1, 2))
        order, expected = make_exception(channel, channel_index)
        add(channel, 6, order, expected)

    amazon = make_order("amazon", 1, 0, SKU_PAIRS[0])
    add("amazon", 1, amazon, "Nhận lại đơn có cùng mã và nội dung", True, "-replay")
    add("amazon", 1, {"AmazonOrderId": amazon["AmazonOrderId"], "SellerAccount": amazon["SellerAccount"],
                      "source_status": "CANCELED"}, "Ghi nhận hủy, giữ tham chiếu của đơn đã có", suffix="-cancellation")
    amended, _ = make_exception("taobao", 4)
    amended["orders"][1]["outer_sku_id"] = OPERATIONS_LISTINGS["taobao"]["ST-NBK-001"]
    add("taobao", 6, amended, "Bản sửa cùng mã để nộp qua hồ sơ ngoại lệ", True, "-correction")

    master = [
        write_json(ROOT / "catalog.json", [dict(sku=sku, name=name, category=category) for sku, name, category in catalog()]),
        write_json(ROOT / "listings.json", [dict(channel=s, account=a, listing=l, sku=sku) for s, a, l, sku in MAPPINGS]),
        write_json(ROOT / "manifest.json", entries),
    ]
    with ZipFile(ROOT / "meridian-operations.zip", "w", ZIP_DEFLATED) as archive:
        for path in [ROOT / "README.md", *master, *fixtures]:
            archive.write(path, path.relative_to(ROOT))
    print(f"{len(entries)} order files, {len(catalog())} SKUs, {len(MAPPINGS)} mappings")


if __name__ == "__main__":
    generate()
