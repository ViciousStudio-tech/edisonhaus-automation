#!/usr/bin/env python3
"""EdisonHaus — Daily product health check against CJ Dropshipping."""

import builtins
def _no_input(*a, **k): raise RuntimeError("BLOCKED")
builtins.input = _no_input

import os, sys, json, time, math, logging, subprocess
from datetime import datetime, timezone
from pathlib import Path
import requests

# ── Config ────────────────────────────────────────────────────────────────
SHOPIFY_BASE = "https://fgtyz6-bj.myshopify.com/admin/api/2024-01"
SHOPIFY_TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
CJ_API_KEY = os.environ.get("CJ_API_KEY", "")
CJ_BASE = "https://developers.cjdropshipping.com/api2.0/v1"
HB_PATH = Path("data/product_health_heartbeat.json")

Path("data").mkdir(parents=True, exist_ok=True)
Path("reports").mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(Path("reports") / f"product_health_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.log"),
              logging.StreamHandler(sys.stdout)])
log = logging.getLogger("product_health")

def shop_h():
    return {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}

# ── Pricing formula ───────────────────────────────────────────────────────
def calculate_sell_price(cost):
    if cost < 5:
        raw = cost * 2.5
    elif cost < 15:
        raw = cost * 2.2
    elif cost < 30:
        raw = cost * 2.0
    else:
        raw = cost * 1.8
    price = max(raw, 14.99)
    price = math.floor(price) + 0.99
    return price

def margin_ok(cost, sell_price):
    if sell_price <= 0:
        return False
    return (sell_price - cost) / sell_price >= 0.35

# ── Step 1: Fetch Shopify products with CJ metafields ────────────────────
def fetch_shopify_products():
    log.info("── Step 1: Fetch Shopify products with CJ metafields ──")
    products = []
    url = f"{SHOPIFY_BASE}/products.json?limit=250&status=active&fields=id,title,handle,variants,images"
    while url:
        r = requests.get(url, headers=shop_h(), timeout=30)
        if r.status_code != 200:
            log.error(f"Shopify fetch failed: {r.status_code}")
            break
        products.extend(r.json().get("products", []))
        link = r.headers.get("Link", "")
        url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]

    log.info(f"Fetched {len(products)} active products")

    # Enrich with CJ metafields
    cj_products = []
    for p in products:
        try:
            r = requests.get(f"{SHOPIFY_BASE}/products/{p['id']}/metafields.json",
                headers=shop_h(), timeout=15)
            if r.status_code != 200:
                continue
            mf = {m["key"]: m["value"] for m in r.json().get("metafields", [])
                  if m.get("namespace") == "dropship"}
            cj_vid = mf.get("cj_variant_id")
            if not cj_vid:
                continue
            p["cj_variant_id"] = cj_vid
            p["cj_product_id"] = mf.get("cj_product_id", "")
            cj_products.append(p)
            time.sleep(0.2)  # rate limit metafield fetches
        except Exception as e:
            log.warning(f"Metafield fetch error for {p['id']}: {e}")

    log.info(f"Found {len(cj_products)} products with CJ variant IDs")
    return cj_products

# ── Step 2: CJ authentication ────────────────────────────────────────────
def get_cj_token():
    log.info("── Step 2: Authenticate with CJ ──")
    r = requests.post(f"{CJ_BASE}/authentication/getAccessToken",
        json={"apiKey": CJ_API_KEY}, timeout=30)
    data = r.json()
    if data.get("result") is True:
        log.info("CJ authenticated")
        return data["data"]["accessToken"]
    raise Exception(f"CJ auth failed: {data.get('message')}")

# ── Step 3: Check CJ product status ──────────────────────────────────────
def check_cj_variant(cj_vid, cj_token):
    """Returns dict with status info or None on error."""
    try:
        r = requests.get(f"{CJ_BASE}/product/variant/query?vid={cj_vid}",
            headers={"CJ-Access-Token": cj_token}, timeout=15)
        if r.status_code == 429:
            log.warning("CJ rate limit hit — backing off 30s")
            time.sleep(30)
            r = requests.get(f"{CJ_BASE}/product/variant/query?vid={cj_vid}",
                headers={"CJ-Access-Token": cj_token}, timeout=15)
        data = r.json()
        if data.get("result") is True and data.get("data"):
            return data["data"]
        return None
    except Exception as e:
        log.warning(f"CJ variant query failed for {cj_vid}: {e}")
        return None

# ── Step 4: Update Shopify ────────────────────────────────────────────────
def draft_product(product_id):
    r = requests.put(f"{SHOPIFY_BASE}/products/{product_id}.json",
        headers=shop_h(),
        json={"product": {"id": product_id, "status": "draft"}},
        timeout=15)
    return r.status_code == 200

def update_price(product_id, variant_id, new_price):
    r = requests.put(f"{SHOPIFY_BASE}/products/{product_id}.json",
        headers=shop_h(),
        json={"product": {"id": product_id, "variants": [{"id": variant_id, "price": f"{new_price:.2f}"}]}},
        timeout=15)
    return r.status_code == 200

# ── Main ──────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("EdisonHaus Product Health Check")
    log.info("=" * 60)

    if not SHOPIFY_TOKEN:
        log.error("Missing SHOPIFY_ACCESS_TOKEN"); sys.exit(1)
    if not CJ_API_KEY:
        log.error("Missing CJ_API_KEY"); sys.exit(1)

    products = fetch_shopify_products()
    if not products:
        log.info("No CJ-sourced products found.")
        HB_PATH.write_text(json.dumps({
            "module": "product_health", "last_run": datetime.now(timezone.utc).isoformat(),
            "total_checked": 0, "ok": 0, "removed": 0, "price_updated": 0,
            "errors": 0, "removed_titles": [], "price_updated_titles": [], "status": "success"
        }, indent=2))
        return

    cj_token = get_cj_token()

    ok = 0
    removed = 0
    price_updated = 0
    errors = 0
    removed_titles = []
    price_updated_titles = []

    log.info(f"── Step 3-4: Check {len(products)} CJ products ──")
    for i, p in enumerate(products):
        title = p.get("title", "?")[:60]
        cj_vid = p["cj_variant_id"]
        variant = p.get("variants", [{}])[0]
        variant_id = variant.get("id")
        current_price = float(variant.get("price", 0))

        log.info(f"  [{i+1}/{len(products)}] {title}")

        cj_data = check_cj_variant(cj_vid, cj_token)
        time.sleep(0.3)

        if cj_data is None:
            errors += 1
            log.warning(f"    Could not fetch CJ data — skipping")
            continue

        product_status = (cj_data.get("productStatus") or "").upper()
        variant_stock = cj_data.get("variantStock", 0) or 0
        cj_cost = float(cj_data.get("variantSellPrice", 0) or 0)

        # Check discontinued / out of stock
        if product_status != "SALE" or variant_stock == 0:
            reason = "discontinued" if product_status != "SALE" else "out of stock"
            log.info(f"    REMOVED ({reason}) — drafting")
            if draft_product(p["id"]):
                removed += 1
                removed_titles.append(p.get("title", "?"))
            else:
                errors += 1
                log.error(f"    Failed to draft product {p['id']}")
            continue

        # Check price change (>$0.50 difference)
        # Compare CJ cost to what would produce current sell price
        new_sell = calculate_sell_price(cj_cost)

        if abs(new_sell - current_price) > 0.50:
            if not margin_ok(cj_cost, new_sell):
                log.info(f"    REMOVED (unprofitable at CJ cost ${cj_cost:.2f}) — drafting")
                if draft_product(p["id"]):
                    removed += 1
                    removed_titles.append(p.get("title", "?"))
                else:
                    errors += 1
                continue

            log.info(f"    PRICE_UPDATED: ${current_price:.2f} → ${new_sell:.2f} (CJ cost ${cj_cost:.2f})")
            if update_price(p["id"], variant_id, new_sell):
                price_updated += 1
                price_updated_titles.append(f"{p.get('title', '?')} (${current_price:.2f} → ${new_sell:.2f})")
            else:
                errors += 1
                log.error(f"    Failed to update price for {p['id']}")
            continue

        ok += 1

    # ── Step 5: Heartbeat ─────────────────────────────────────────────────
    total = ok + removed + price_updated + errors
    status = "success" if errors == 0 else "partial"
    HB_PATH.write_text(json.dumps({
        "module": "product_health",
        "last_run": datetime.now(timezone.utc).isoformat(),
        "total_checked": total,
        "ok": ok,
        "removed": removed,
        "price_updated": price_updated,
        "errors": errors,
        "removed_titles": removed_titles,
        "price_updated_titles": price_updated_titles,
        "status": status,
    }, indent=2))

    log.info(f"Done: {total} checked | {ok} OK | {removed} removed | {price_updated} price updated | {errors} errors")
    if removed_titles:
        log.info(f"Removed: {', '.join(removed_titles[:10])}")
    if price_updated_titles:
        log.info(f"Price updated: {', '.join(price_updated_titles[:10])}")

    # ── Step 6: Git commit ────────────────────────────────────────────────
    log.info("── Step 6: Git commit ──")
    try:
        def g(cmd):
            return subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        g(["git", "config", "user.name", "EdisonHaus Bot"])
        g(["git", "config", "user.email", "bot@edisonhaus.store"])
        g(["git", "add", "-f", "data/product_health_heartbeat.json"])
        g(["git", "stash"])
        g(["git", "pull", "--rebase", "origin", "main"])
        subprocess.run(["git", "stash", "pop"], capture_output=True, text=True)
        g(["git", "add", "-f", "data/product_health_heartbeat.json"])
        if subprocess.run(["git", "diff", "--staged", "--quiet"], capture_output=True).returncode != 0:
            date_str = datetime.now().strftime("%Y-%m-%d")
            g(["git", "commit", "-m", f"Product health check {date_str} — {removed} removed, {price_updated} updated [skip ci]"])
            g(["git", "push", "origin", "main"])
            log.info("  Committed & pushed")
        else:
            log.info("  No changes to commit")
    except Exception as e:
        log.warning(f"  Git fail: {e}")


if __name__ == "__main__":
    main()
