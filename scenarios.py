"""Synthetic catalog and marketplace order fixtures."""

AMAZON = {
    "AmazonOrderId": "114-7826143-2059448",
    "SellerAccount": "meridian-us",
    "Marketplace": "US",
    "CurrencyCode": "VND",
    "OrderItems": [
        {"SellerSKU": "AMZ-ORG-SET-01", "QuantityOrdered": 2, "UnitPrice": 125000},
        {"SellerSKU": "AMZ-NOTE-A5-01", "QuantityOrdered": 3, "UnitPrice": 45000},
    ],
}
EBAY = {
    "orderId": "24-12637-58104",
    "sellerId": "meridian-us",
    "marketplaceId": "EBAY_US",
    "currency": "VND",
    "lineItems": [
        {"sku": "EB-ORG-SET-01", "quantity": 1, "unitPrice": 125000},
        {"sku": "EB-NOTE-A5-RU", "quantity": 2, "unitPrice": 45000},
    ],
}
EBAY_DE = {
    "orderId": "17-12908-44172", "sellerId": "meridian-eu", "marketplaceId": "EBAY_DE",
    "currency": "EUR",
    "lineItems": [
        {"sku": "EB-ORG-SET-01", "quantity": 1, "unitPrice": 14.95},
        {"sku": "EB-NOTE-A5-RU", "quantity": 2, "unitPrice": 5.50},
    ],
}
ETSY = {
    "receipt_id": "3491876254", "shop_slug": "MeridianPaperStudio", "ship_country": "AU", "currency_code": "AUD",
    "transactions": [
        {"sku": "ET-ORG-IV-01", "quantity": 1, "unit_price": 24.90},
        {"sku": "ET-NBK-A5-01", "quantity": 2, "unit_price": 8.50},
    ],
}
ALIEXPRESS = {
    "order_id": "8186429057123480", "seller_login": "meridian-global", "ship_to": "ZA", "currency": "ZAR",
    "order_items": [
        {"seller_sku": "AE-ORG-IV-01", "quantity": 2, "unit_price": 229.00},
        {"seller_sku": "AE-NBK-A5-01", "quantity": 1, "unit_price": 69.90},
    ],
}
TAOBAO = {
    "tid": "4816392705142081673", "seller_nick": "meridian-east-asia", "delivery_country": "JP",
    "settlement_currency": "CNY",
    "orders": [
        {"outer_sku_id": "TB-ORG-IV-01", "num": 1, "price": 69.00},
        {"outer_sku_id": "TB-NBK-A5-01", "num": 3, "price": 19.90},
    ],
}

CHANNELS = {
    "amazon": ("Amazon", ("SellerAccount", "AmazonOrderId", "Marketplace", "CurrencyCode", "OrderItems"), ("SellerSKU", "QuantityOrdered", "UnitPrice")),
    "ebay": ("eBay", ("sellerId", "orderId", "marketplaceId", "currency", "lineItems"), ("sku", "quantity", "unitPrice")),
    "etsy": ("Etsy", ("shop_slug", "receipt_id", "ship_country", "currency_code", "transactions"), ("sku", "quantity", "unit_price")),
    "aliexpress": ("AliExpress", ("seller_login", "order_id", "ship_to", "currency", "order_items"), ("seller_sku", "quantity", "unit_price")),
    "taobao": ("Taobao", ("seller_nick", "tid", "delivery_country", "settlement_currency", "orders"), ("outer_sku_id", "num", "price")),
}
MARKETS = {"US": "Mỹ", "DE": "Đức", "AU": "Úc", "ZA": "Nam Phi", "JP": "Nhật Bản"}
CURRENCY_PLACES = {"USD": 2, "EUR": 2, "AUD": 2, "ZAR": 2, "JPY": 0, "CNY": 2, "VND": 0}


def market_code(value):
    if isinstance(value, str) and value.startswith("EBAY_"):
        return value.removeprefix("EBAY_")
    return value


LOCAL_MARKET_SAMPLES = {
    "amazon": {**AMAZON, "AmazonOrderId": "114-7826143-2059449", "CurrencyCode": "USD",
               "OrderItems": [
                   {"SellerSKU": "AMZ-ORG-SET-01", "QuantityOrdered": 2, "UnitPrice": 14.95},
                   {"SellerSKU": "AMZ-NOTE-A5-01", "QuantityOrdered": 3, "UnitPrice": 5.50},
               ]},
    "ebay": EBAY_DE,
    "etsy": ETSY,
    "aliexpress": ALIEXPRESS,
    "taobao": TAOBAO,
}
TOTAL_MISMATCH_SAMPLE = {
    **LOCAL_MARKET_SAMPLES["amazon"],
    "AmazonOrderId": "114-6294081-5317620",
    "financial_summary": {"shipping": 4.99, "discount": 3.00, "tax": 2.30, "total": 52.19},
}
MISSING_MAPPING_SAMPLE = {
    **EBAY,
    "orderId": "24-12637-58105",
    "currency": "USD",
    "lineItems": [
        {"sku": "EB-ORG-SET-01", "quantity": 1, "unitPrice": 14.95},
        {"sku": "EB-NOTE-A5-RU", "quantity": 2, "unitPrice": 5.50},
    ],
}
CORRECTED_MAPPING_SAMPLE = {
    **MISSING_MAPPING_SAMPLE,
    "lineItems": [
        MISSING_MAPPING_SAMPLE["lineItems"][0],
        {**MISSING_MAPPING_SAMPLE["lineItems"][1], "sku": "EB-NOTE-A5-01"},
    ],
}
MAPPINGS = [
    ("amazon", "meridian-us", "AMZ-ORG-SET-01", "HK-ORG-001"),
    ("amazon", "meridian-us", "AMZ-NOTE-A5-01", "ST-NBK-001"),
    ("ebay", "meridian-us", "EB-ORG-SET-01", "HK-ORG-001"),
    ("ebay", "meridian-us", "EB-NOTE-A5-01", "ST-NBK-001"),
    ("ebay", "meridian-eu", "EB-ORG-SET-01", "HK-ORG-001"),
    ("ebay", "meridian-eu", "EB-NOTE-A5-RU", "ST-NBK-001"),
    ("etsy", "MeridianPaperStudio", "ET-ORG-IV-01", "HK-ORG-001"),
    ("etsy", "MeridianPaperStudio", "ET-NBK-A5-01", "ST-NBK-001"),
    ("aliexpress", "meridian-global", "AE-ORG-IV-01", "HK-ORG-001"),
    ("aliexpress", "meridian-global", "AE-NBK-A5-01", "ST-NBK-001"),
    ("taobao", "meridian-east-asia", "TB-ORG-IV-01", "HK-ORG-001"),
    ("taobao", "meridian-east-asia", "TB-NBK-A5-01", "ST-NBK-001"),
]
FAMILIES = [
    ("HK-ORG", "Drawer organizer", "Home & Kitchen"),
    ("HK-STO", "Storage container", "Home & Kitchen"),
    ("HK-MAT", "Kitchen mat", "Home & Kitchen"),
    ("HK-UTL", "Kitchen utensil set", "Home & Kitchen"),
    ("HK-RCK", "Countertop rack", "Home & Kitchen"),
    ("ST-NBK", "Ruled notebook", "Stationery"),
    ("ST-PLN", "Weekly planner", "Stationery"),
    ("ST-PEN", "Fine point pen set", "Stationery"),
    ("ST-FLR", "Document folder", "Stationery"),
    ("ST-NTD", "Desk notepad", "Stationery"),
]
VARIANTS = ["Ivory", "Slate", "Sage", "Navy", "Graphite"]
SPECIFICATIONS = {
    "HK-ORG": ["4-piece nesting set", "6-piece nesting set", "8-piece nesting set", "2 long trays", "3 mini trays",
               "Expandable 28–45 cm", "28 × 14 cm", "32 × 18 cm", "36 × 20 cm", "40 × 22 cm"],
    "HK-STO": ["700 mL single", "1 L single", "1.5 L single", "2 L single", "750 mL 2-pack",
               "1 L 3-pack", "1.2 L 2-pack", "2.5 L single", "3 L single", "3-size starter set"],
    "HK-MAT": ["45 × 75 cm", "45 × 90 cm", "45 × 120 cm", "50 × 80 cm", "50 × 100 cm",
               "50 × 140 cm", "60 × 90 cm", "60 × 120 cm", "60 × 150 cm", "2-piece runner set"],
    "HK-UTL": ["3-piece silicone", "5-piece silicone", "7-piece silicone", "9-piece silicone", "11-piece silicone",
               "4-piece acacia", "6-piece acacia", "Turner and spoon set", "Baking tools set", "Cookware starter kit"],
    "HK-RCK": ["Single-tier 28 cm", "Single-tier 35 cm", "Single-tier 42 cm", "Two-tier 28 cm", "Two-tier 35 cm",
               "Two-tier 42 cm", "Corner 20 cm", "Corner 28 cm", "Foldable 30 cm", "Expandable 32–50 cm"],
    "ST-NBK": ["A5, 96 pages", "A5, 160 pages", "A5, 192 pages", "A6, 96 pages", "B5, 120 pages",
               "B5, 160 pages", "A4, 120 pages", "A5, 2-pack", "A6, 3-pack", "A5 hardcover, 160 pages"],
    "ST-PLN": ["A5, 26 weeks", "A5, 52 weeks", "A6, 26 weeks", "B5, 52 weeks", "A4, 52 weeks",
               "A5 desktop pad", "A4 desktop pad", "A5 hardcover", "Pocket undated", "A5 refill pack"],
    "ST-PEN": ["0.38 mm, 3-pack", "0.38 mm, 6-pack", "0.5 mm, 3-pack", "0.5 mm, 6-pack", "0.5 mm, 12-pack",
               "0.7 mm, 3-pack", "0.7 mm, 6-pack", "0.7 mm, 12-pack", "0.38 mm, 12-pack", "Mixed nib set"],
    "ST-FLR": ["A4, 3 pockets", "A4, 6 pockets", "A4, 13 pockets", "A5, 6 pockets", "Letter, 6 pockets",
               "Letter, 13 pockets", "A4 snap folder, 2-pack", "A4 snap folder, 5-pack", "A4 expanding, 8 pockets", "A4 zipper, 3-pack"],
    "ST-NTD": ["A6, 50 sheets", "A6, 80 sheets", "A5, 50 sheets", "A5, 80 sheets", "A4, 50 sheets",
               "A4, 80 sheets", "A6 grid, 2-pack", "A5 grid, 2-pack", "A5 checklist, 2-pack", "A4 desk pad, 30 sheets"],
}
OPERATIONS_SKUS = tuple(f"{prefix}-001" for prefix, _, _ in FAMILIES)
MARKET_LISTING_PREFIXES = {"amazon": "AMZ", "ebay": "EB", "aliexpress": "AE", "etsy": "ET", "taobao": "TB"}
OPERATIONS_LISTINGS = {}
for channel, sample in LOCAL_MARKET_SAMPLES.items():
    _, (account_key, _, _, _, rows_key), (listing_key, _, _) = CHANNELS[channel]
    account = sample[account_key]
    listings = {
        "HK-ORG-001": sample[rows_key][0][listing_key],
        "ST-NBK-001": sample[rows_key][1][listing_key],
    }
    for sku in OPERATIONS_SKUS:
        if sku not in listings:
            listings[sku] = f"{MARKET_LISTING_PREFIXES[channel]}-{sku.split('-')[1]}-IV-01"
            MAPPINGS.append((channel, account, listings[sku], sku))
    OPERATIONS_LISTINGS[channel] = listings


def catalog():
    return [
        (f"{prefix}-{index:03d}",
         f"{name} · {SPECIFICATIONS[prefix][(index - 1) % 10]} · {VARIANTS[(index - 1) // 10]}",
         category)
        for prefix, name, category in FAMILIES
        for index in range(1, 51)
    ]
