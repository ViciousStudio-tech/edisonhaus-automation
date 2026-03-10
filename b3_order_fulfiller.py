"""
Business 3 — Order Fulfiller
Checks Shopify for new orders, auto-places them on AliExpress, updates tracking.
Runs every 4 hours via GitHub Actions.
"""

import os
import json
import time
import sqlite3
import logging
import requests
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Env ────────────────────────────────────────────────────────────────────────
SHOPIFY_STORE        = os.environ.get("SHOPIFY_STORE", "fgtyz6-bj.myshopify.com")
SHOPIFY_ACCESS_TOKEN = os.environ["SHOPIFY_ACCESS_TOKEN"]
ALIEXPRESS_APP_KEY   = os.environ.get("ALIEXPRESS_APP_KEY", "")
ALIEXPRESS_SECRET    = os.environ.get("ALIEXPRESS_SECRET", "")
GMAIL_SENDER         = os.environ.get("GMAIL_SENDER", "")
DB_PATH              = "dropship.db"

SHOPIFY_HEADERS = {
    "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
    "Content-Type": "application/json"
}
SHOPIFY_BASE = f"https://{SHOPIFY_STORE}/admin/api/2024-10"

# ── DB ─────────────────────────────────────────────────────────────────────────
def init_orders_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            shopify_order_id   TEXT UNIQUE,
            shopify_order_num  TEXT,
            aliexpress_order_id TEXT,
            customer_email     TEXT,
            customer_name      TEXT,
            shipping_address   TEXT,
            line_items         TEXT,
            total_revenue      REAL,
            total_cost         REAL,
            profit             REAL,
            status             TEXT DEFAULT 'new',
            tracking_number    TEXT,
            fulfilled_at       TEXT,
            created_at         TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()

# ── Shopify: fetch unfulfilled orders ──────────────────────────────────────────
def get_unfulfilled_orders() -> list:
    since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        resp = requests.get(
            f"{SHOPIFY_BASE}/orders.json",
            headers=SHOPIFY_HEADERS,
            params={
                "fulfillment_status": "unfulfilled",
                "financial_status": "paid",
                "created_at_min": since,
                "limit": 50,
                "fields": "id,order_number,email,shipping_address,line_items,total_price,created_at"
            },
            timeout=15
        )
        return resp.json().get("orders", [])
    except Exception as e:
        log.error(f"Failed to fetch orders: {e}")
        return []

# ── Get AliExpress product URL from Shopify metafield ─────────────────────────
def get_aliexpress_url_for_variant(variant_id: str) -> str | None:
    """Look up the AliExpress URL stored in product metafields."""
    try:
        # Get product from variant
        resp = requests.get(
            f"{SHOPIFY_BASE}/variants/{variant_id}.json",
            headers=SHOPIFY_HEADERS, timeout=10
        )
        product_id = resp.json()["variant"]["product_id"]

        # Get metafields
        resp2 = requests.get(
            f"{SHOPIFY_BASE}/products/{product_id}/metafields.json",
            headers=SHOPIFY_HEADERS, timeout=10
        )
        for mf in resp2.json().get("metafields", []):
            if mf.get("key") == "aliexpress_url":
                return mf.get("value")
    except Exception as e:
        log.error(f"Metafield lookup failed: {e}")
    return None

# ── Place order on AliExpress (API or manual queue) ───────────────────────────
def place_aliexpress_order(order: dict, aliexpress_url: str) -> str | None:
    """
    Attempt to place order via AliExpress API.
    Falls back to queuing for manual placement if API unavailable.
    """
    if not ALIEXPRESS_APP_KEY:
        # Queue for manual fulfillment — log details clearly
        log.info(f"⚠️  MANUAL FULFILLMENT NEEDED:")
        log.info(f"   Customer: {order.get('shipping_address',{}).get('name')}")
        log.info(f"   Address: {order.get('shipping_address',{})}")
        log.info(f"   AliExpress URL: {aliexpress_url}")
        return "MANUAL_QUEUE"

    # AliExpress DS Order API
    shipping_addr = order.get("shipping_address", {})
    params = {
        "method": "aliexpress.ds.order.create",
        "app_key": ALIEXPRESS_APP_KEY,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "format": "json",
        "v": "2.0",
        "logistics_address": json.dumps({
            "contact_person": shipping_addr.get("name", ""),
            "address": shipping_addr.get("address1", ""),
            "address2": shipping_addr.get("address2", ""),
            "city": shipping_addr.get("city", ""),
            "province": shipping_addr.get("province", ""),
            "zip": shipping_addr.get("zip", ""),
            "country": shipping_addr.get("country_code", "US"),
            "phone_country": "+1",
            "mobile_no": shipping_addr.get("phone", "5555555555")
        }),
        "product_items": json.dumps([{
            "product_id": aliexpress_url.split("/item/")[-1].split(".")[0] if "/item/" in aliexpress_url else "",
            "sku_attr": "",
            "quantity": 1
        }])
    }

    try:
        resp = requests.post("https://gw.api.taobao.com/router/rest", params=params, timeout=20)
        result = resp.json()
        ae_order_id = result.get("aliexpress_ds_order_create_response", {}) \
                            .get("result", {}).get("ae_order_id")
        return ae_order_id
    except Exception as e:
        log.error(f"AliExpress order placement failed: {e}")
        return None

# ── Mark fulfilled on Shopify ──────────────────────────────────────────────────
def fulfill_shopify_order(shopify_order_id: str, tracking_num: str = ""):
    """Create fulfillment on Shopify."""
    try:
        # Get fulfillment order ID
        resp = requests.get(
            f"{SHOPIFY_BASE}/orders/{shopify_order_id}/fulfillment_orders.json",
            headers=SHOPIFY_HEADERS, timeout=10
        )
        fo_id = resp.json()["fulfillment_orders"][0]["id"]

        # Create fulfillment
        body = {
            "fulfillment": {
                "line_items_by_fulfillment_order": [{"fulfillment_order_id": fo_id}],
                "notify_customer": True
            }
        }
        if tracking_num and tracking_num != "MANUAL_QUEUE":
            body["fulfillment"]["tracking_info"] = {
                "number": tracking_num,
                "company": "AliExpress Standard Shipping"
            }

        resp2 = requests.post(
            f"{SHOPIFY_BASE}/fulfillments.json",
            headers=SHOPIFY_HEADERS, json=body, timeout=15
        )
        return resp2.status_code == 201
    except Exception as e:
        log.error(f"Shopify fulfillment error: {e}")
        return False

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    conn = sqlite3.connect(DB_PATH)
    init_orders_db(conn)

    orders = get_unfulfilled_orders()
    log.info(f"Found {len(orders)} unfulfilled paid orders")

    fulfilled = 0
    revenue = 0.0

    for order in orders:
        shopify_order_id = str(order["id"])

        # Skip if already processed
        existing = conn.execute(
            "SELECT id FROM orders WHERE shopify_order_id=?", (shopify_order_id,)
        ).fetchone()
        if existing:
            continue

        log.info(f"Processing order #{order.get('order_number')} — {order.get('email')}")

        # Process each line item
        for item in order.get("line_items", []):
            ae_url = get_aliexpress_url_for_variant(str(item["variant_id"]))
            ae_order_id = None
            if ae_url:
                ae_order_id = place_aliexpress_order(order, ae_url)

        # Calculate profit
        total_revenue = float(order.get("total_price", 0))
        # Estimated cost (30% of revenue as rough average)
        estimated_cost = total_revenue * 0.30
        profit = total_revenue - estimated_cost

        # Save to DB
        conn.execute("""
            INSERT OR IGNORE INTO orders
            (shopify_order_id, shopify_order_num, aliexpress_order_id,
             customer_email, customer_name, shipping_address,
             line_items, total_revenue, total_cost, profit, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            shopify_order_id,
            str(order.get("order_number", "")),
            ae_order_id or "",
            order.get("email", ""),
            order.get("shipping_address", {}).get("name", ""),
            json.dumps(order.get("shipping_address", {})),
            json.dumps(order.get("line_items", [])),
            total_revenue, estimated_cost, profit,
            "processing" if ae_order_id else "manual_needed"
        ))
        conn.commit()

        # Mark as processing on Shopify (customer sees order is being prepared)
        if ae_order_id and ae_order_id != "MANUAL_QUEUE":
            fulfill_shopify_order(shopify_order_id, ae_order_id)
            fulfilled += 1

        revenue += total_revenue
        time.sleep(0.5)

    log.info(f"Done. Processed {len(orders)} orders. Fulfilled: {fulfilled}. Revenue: ${revenue:.2f}")

    with open("b3_fulfillment_heartbeat.json", "w") as f:
        json.dump({
            "last_run": datetime.now().isoformat(),
            "orders_processed": len(orders),
            "auto_fulfilled": fulfilled,
            "revenue_usd": round(revenue, 2),
            "status": "ok"
        }, f)

if __name__ == "__main__":
    main()
