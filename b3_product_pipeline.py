#!/usr/bin/env python3
"""EdisonHaus Product Pipeline — keyword-based CJ product search → Shopify listing."""

import builtins
def _no_input(*a, **k): raise RuntimeError("BLOCKED: interactive prompt")
builtins.input = _no_input

import os
import sys
import json
import time
import sqlite3
import logging
import subprocess
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
SHOPIFY_STORE = "fgtyz6-bj.myshopify.com"
SHOPIFY_ACCESS_TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
SHOPIFY_BASE = f"https://{SHOPIFY_STORE}/admin/api/2024-01"
CJ_EMAIL = os.environ.get("CJ_EMAIL", "")
CJ_API_KEY = os.environ.get("CJ_API_KEY", "")
CJ_BASE = "https://developers.cjdropshipping.com/api2.0/v1"
DB_PATH = Path("data/dropship.db")
HEARTBEAT_PATH = Path("data/product_pipeline_heartbeat.json")
REPORTS_DIR = Path("reports")

REPORTS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# COLLECTIONS & KEYWORDS
# ---------------------------------------------------------------------------
COLLECTIONS = {
    "led-ambient-lighting": {
        "title": "LED & Ambient Lighting",
        "keywords": [
            "LED strip light", "fairy lights", "string lights",
            "neon light", "galaxy projector", "sunset lamp",
        ],
    },
    "table-desk-lamps": {
        "title": "Table & Desk Lamps",
        "keywords": [
            "table lamp", "desk lamp", "bedside lamp", "reading lamp",
        ],
    },
    "pendant-ceiling-lights": {
        "title": "Pendant & Ceiling Lights",
        "keywords": [
            "pendant light", "chandelier", "ceiling light", "hanging lamp",
        ],
    },
    "wall-decor": {
        "title": "Wall Decor",
        "keywords": [
            "canvas wall art", "wall painting", "tapestry", "decorative painting",
        ],
    },
    "cozy-textiles": {
        "title": "Cozy Textiles",
        "keywords": [
            "throw pillow cover", "cushion cover",
        ],
    },
    "storage-accents": {
        "title": "Storage & Accents",
        "keywords": [
            "woven basket", "rattan basket", "candle holder", "decorative vase",
        ],
    },
}

# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------
log_file = REPORTS_DIR / f"product_pipeline_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("pipeline")

# ---------------------------------------------------------------------------
# STATS
# ---------------------------------------------------------------------------
stats = {
    "products_fetched": 0,
    "products_created": 0,
    "products_updated": 0,
    "products_skipped": 0,
    "collections_created": 0,
    "total_live": 0,
    "errors": [],
}

# ---------------------------------------------------------------------------
# GLOBAL CJ RATE LIMITER
# ---------------------------------------------------------------------------
_last_cj_call = 0.0
CJ_MIN_INTERVAL = 2.0  # seconds between CJ API calls
CJ_DAILY_LIMIT = 950   # CJ free tier = 1000/day, leave headroom
_cj_call_count = 0


def cj_throttle():
    """Ensure minimum interval between CJ API calls. Track daily budget."""
    global _last_cj_call, _cj_call_count
    _cj_call_count += 1
    if _cj_call_count > CJ_DAILY_LIMIT:
        log.warning(f"CJ daily limit reached ({_cj_call_count}/{CJ_DAILY_LIMIT}). Stopping CJ calls.")
        return False
    elapsed = time.time() - _last_cj_call
    if elapsed < CJ_MIN_INTERVAL:
        time.sleep(CJ_MIN_INTERVAL - elapsed)
    _last_cj_call = time.time()
    return True


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def api_call(method, url, retries=3, is_cj=False, **kwargs):
    """HTTP call with retry. CJ calls respect daily limit and rate windows."""
    for attempt in range(retries):
        try:
            if is_cj:
                if not cj_throttle():
                    return None  # daily limit reached
            resp = requests.request(method, url, timeout=(10, 30), **kwargs)
            if resp.status_code == 429:
                body = resp.json() if resp.text else {}
                msg = body.get("message", "")
                if "daily" in msg.lower() or "1000" in msg:
                    log.error(f"CJ DAILY LIMIT HIT: {msg}")
                    return None  # don't retry, daily limit is hard
                if is_cj:
                    wait = 120 * (attempt + 1)  # 120, 240, 360
                    log.warning(f"CJ 429 rate limited, sleeping {wait}s (attempt {attempt+1}/{retries})")
                    time.sleep(wait)
                    continue
                else:
                    wait = 5 * (2 ** attempt)
                    log.warning(f"429 rate limited, sleeping {wait}s")
                    time.sleep(wait)
                    continue
            return resp
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise
            wait = 10 * (attempt + 1)
            log.warning(f"Request failed ({e}), retrying in {wait}s")
            time.sleep(wait)
    log.warning(f"All {retries} retries exhausted for {method} {url}")
    return None


def shopify_headers():
    return {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json",
    }


def cj_headers(token):
    return {"CJ-Access-Token": token, "Content-Type": "application/json"}


def write_heartbeat(phase="in_progress", status="running"):
    hb = {
        "module": "b3_product_pipeline",
        "last_run": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "products_fetched": stats["products_fetched"],
        "products_created": stats["products_created"],
        "products_updated": stats["products_updated"],
        "products_skipped": stats["products_skipped"],
        "collections_created": stats["collections_created"],
        "total_live": stats["total_live"],
        "status": status,
        "errors": stats["errors"][-50:],
    }
    HEARTBEAT_PATH.write_text(json.dumps(hb, indent=2))


def calculate_price(cost):
    """Apply markup tiers. Returns (sell_price, margin) or None if skip."""
    if cost <= 0:
        return None
    if cost < 5.0:
        sell = cost * 2.5
    elif cost < 15.0:
        sell = cost * 2.2
    elif cost < 30.0:
        sell = cost * 2.0
    elif cost < 60.0:
        sell = cost * 1.8
    else:
        sell = cost * 1.7

    if sell < 14.99:
        sell = 14.99

    whole = int(sell)
    sell = float(whole) + 0.99
    if sell < 14.99:
        sell = 14.99

    margin = (sell - cost) / sell
    if margin < 0.35:
        return None
    return sell, margin


# ---------------------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------------------

def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            cj_id                 TEXT UNIQUE,
            cj_vid                TEXT,
            title                 TEXT,
            shopify_id            TEXT,
            cost_usd              REAL,
            sell_price            REAL,
            profit_margin         REAL,
            shopify_collection_id INTEGER,
            image_url             TEXT,
            status                TEXT DEFAULT 'listed',
            last_synced           TEXT DEFAULT (datetime('now')),
            created_at            TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# STEP 1 — CJ AUTH
# ---------------------------------------------------------------------------

def step1_cj_auth():
    log.info("=== STEP 1: CJ Authentication ===")
    for attempt in range(2):
        resp = api_call("POST", f"{CJ_BASE}/authentication/getAccessToken",
                        json={"email": CJ_EMAIL, "password": CJ_API_KEY},
                        is_cj=True)
        if resp and resp.status_code == 200:
            data = resp.json()
            token = None
            if isinstance(data.get("data"), dict):
                token = data["data"].get("accessToken")
            elif isinstance(data.get("data"), str):
                token = data["data"]
            if token:
                log.info("CJ auth successful, token cached.")
                write_heartbeat("step1_complete")
                return token
            log.error(f"CJ auth response missing token: {json.dumps(data)}")
        else:
            log.error(f"CJ auth failed: {resp.status_code if resp else 'no response'} {resp.text if resp else ''}")

        if attempt == 0:
            log.info("Waiting 310 seconds before retry (CJ auth rate limit)...")
            time.sleep(310)
        else:
            log.error("CJ auth failed after retry. Aborting.")
            sys.exit(1)


# ---------------------------------------------------------------------------
# STEP 2 — SEARCH CJ FOR PRODUCTS BY KEYWORD
# ---------------------------------------------------------------------------

def step2_search_products(cj_token):
    log.info("=== STEP 2: Search CJ Products by Keyword ===")
    results = {}
    seen_pids = set()

    MAX_PAGES_PER_KEYWORD = 1  # CJ search is broad; 1 page per keyword is plenty

    for handle, coll_info in COLLECTIONS.items():
        results[handle] = []
        for keyword in coll_info["keywords"]:
            log.info(f"  Searching: '{keyword}' for {handle}")
            page = 1
            while page <= MAX_PAGES_PER_KEYWORD:
                resp = api_call("GET", f"{CJ_BASE}/product/list",
                                headers=cj_headers(cj_token),
                                params={"productNameEn": keyword, "pageNum": page, "pageSize": 20},
                                is_cj=True)
                if not resp or resp.status_code != 200:
                    log.warning(f"  Search failed for '{keyword}' page {page}: {resp.status_code if resp else 'none'}")
                    break

                data = resp.json()
                products = []
                if isinstance(data.get("data"), dict):
                    products = data["data"].get("list", [])
                elif isinstance(data.get("data"), list):
                    products = data["data"]

                if not products:
                    break

                for p in products:
                    pid = p.get("pid") or p.get("productId")
                    if pid and pid not in seen_pids:
                        seen_pids.add(pid)
                        results[handle].append(pid)

                log.info(f"    Page {page}: {len(products)} results, {len(results[handle])} unique for {handle}")

                if len(products) < 20:
                    break
                page += 1

    total = sum(len(v) for v in results.values())
    log.info(f"Found {total} unique products across {len(COLLECTIONS)} collections.")
    for handle, pids in results.items():
        log.info(f"  {handle}: {len(pids)} products")
    write_heartbeat("step2_complete")
    return results


# ---------------------------------------------------------------------------
# STEP 3 — FETCH FULL PRODUCT DETAIL
# ---------------------------------------------------------------------------

def step3_fetch_details(cj_token, pid_map):
    log.info("=== STEP 3: Fetch Product Details from CJ ===")
    details = {}
    total_pids = sum(len(v) for v in pid_map.values())
    log.info(f"  Fetching details for {total_pids} products (this will take a while)...")

    for handle, pids in pid_map.items():
        details[handle] = []
        for i, pid in enumerate(pids):
            try:
                resp = api_call("GET", f"{CJ_BASE}/product/query",
                                headers=cj_headers(cj_token),
                                params={"pid": pid},
                                is_cj=True)
                if not resp or resp.status_code != 200:
                    log.warning(f"  Detail fetch failed for {pid}")
                    stats["errors"].append(f"detail_failed:{pid}")
                    continue

                detail = resp.json().get("data")
                if not detail:
                    log.warning(f"  No detail data for {pid}")
                    continue

                details[handle].append(detail)
                stats["products_fetched"] += 1

                if stats["products_fetched"] % 25 == 0:
                    log.info(f"  ... fetched {stats['products_fetched']}/{total_pids} details")
            except Exception as e:
                log.warning(f"  Error fetching {pid}: {e}")
                stats["errors"].append(f"detail_error:{pid}:{str(e)[:80]}")

    log.info(f"Fetched details for {stats['products_fetched']} products. CJ calls used: {_cj_call_count}/{CJ_DAILY_LIMIT}")
    write_heartbeat("step3_complete")
    return details


# ---------------------------------------------------------------------------
# STEP 7 HELPER — ENSURE COLLECTION EXISTS
# ---------------------------------------------------------------------------

def ensure_collection(handle, title):
    """Get or create a Shopify custom collection. Returns collection ID."""
    resp = api_call("GET",
                    f"{SHOPIFY_BASE}/custom_collections.json?handle={handle}",
                    headers=shopify_headers())
    if resp and resp.status_code == 200:
        existing = resp.json().get("custom_collections", [])
        if existing:
            return existing[0]["id"]

    payload = {
        "custom_collection": {
            "title": title,
            "handle": handle,
            "published": True,
        }
    }
    resp = api_call("POST", f"{SHOPIFY_BASE}/custom_collections.json",
                    headers=shopify_headers(), json=payload)
    if resp and resp.status_code in (200, 201):
        coll_id = resp.json()["custom_collection"]["id"]
        stats["collections_created"] += 1
        log.info(f"  Created collection '{handle}' (ID: {coll_id})")
        return coll_id

    log.error(f"  Failed to create collection '{handle}': {resp.status_code if resp else 'none'}")
    return None


# ---------------------------------------------------------------------------
# STEPS 4-8 — PRICE, CREATE/UPDATE, METAFIELDS, COLLECTION, DB
# ---------------------------------------------------------------------------

def process_products(detail_map, db):
    log.info("=== STEPS 4-8: Price, Create, Metafields, Collections, DB ===")
    cursor = db.cursor()
    report_rows = []

    # Resolve collection IDs upfront
    collection_ids = {}
    for handle, coll_info in COLLECTIONS.items():
        coll_id = ensure_collection(handle, coll_info["title"])
        collection_ids[handle] = coll_id
        time.sleep(0.3)

    for handle, products in detail_map.items():
        coll_id = collection_ids.get(handle)
        coll_title = COLLECTIONS[handle]["title"]
        log.info(f"Processing {len(products)} products for {coll_title}...")

        for product in products:
            pid = product.get("pid") or product.get("productId", "")
            title = product.get("productNameEn", "Untitled")
            try:
                variants = product.get("variants", [])
                if not variants:
                    stats["products_skipped"] += 1
                    report_rows.append({
                        "cj_id": pid, "title": title, "cost": 0, "sell_price": 0,
                        "margin": 0, "collection": handle, "shopify_id": "",
                        "action": "skipped", "skip_reason": "no_variants",
                    })
                    continue

                cheapest = min(variants, key=lambda v: float(v.get("variantSellPrice", 999999)))
                cost = float(cheapest.get("variantSellPrice", 0))
                vid = cheapest.get("vid", "")

                pricing = calculate_price(cost)
                if pricing is None:
                    stats["products_skipped"] += 1
                    report_rows.append({
                        "cj_id": pid, "title": title, "cost": cost, "sell_price": 0,
                        "margin": 0, "collection": handle, "shopify_id": "",
                        "action": "skipped", "skip_reason": "low_margin_or_zero_cost",
                    })
                    continue
                sell_price, margin = pricing

                cursor.execute(
                    "SELECT shopify_id, cost_usd FROM products WHERE cj_id=? AND shopify_id IS NOT NULL",
                    (str(pid),))
                row = cursor.fetchone()
                existing_shopify_id = row[0] if row else None
                existing_cost = row[1] if row else None

                if existing_shopify_id:
                    # UPDATE only if cost changed
                    if existing_cost is not None and abs(float(existing_cost) - cost) < 0.01:
                        report_rows.append({
                            "cj_id": pid, "title": title, "cost": cost,
                            "sell_price": sell_price, "margin": round(margin, 4),
                            "collection": handle, "shopify_id": existing_shopify_id,
                            "action": "unchanged", "skip_reason": "",
                        })
                        continue

                    variant_payloads = []
                    for v in variants:
                        v_cost = float(v.get("variantSellPrice", 0))
                        v_pricing = calculate_price(v_cost)
                        if v_pricing is None:
                            continue
                        v_sell, _ = v_pricing
                        variant_payloads.append({
                            "price": str(v_sell),
                            "sku": f"{product.get('productSkuEn', 'CJ')}-{v.get('vid', '')}",
                            "option1": v.get("variantNameEn", "Default"),
                            "weight": product.get("productWeight", 0),
                            "weight_unit": "g",
                            "inventory_management": None,
                            "fulfillment_service": "manual",
                            "requires_shipping": True,
                            "taxable": True,
                        })

                    resp = api_call("PUT",
                                    f"{SHOPIFY_BASE}/products/{existing_shopify_id}.json",
                                    headers=shopify_headers(),
                                    json={"product": {"id": int(existing_shopify_id), "variants": variant_payloads}})
                    if resp and resp.status_code == 200:
                        log.info(f"  Updated {existing_shopify_id}: cost {existing_cost}->{cost}")
                        stats["products_updated"] += 1
                        action = "updated"
                    else:
                        log.warning(f"  Update failed {existing_shopify_id}: {resp.status_code if resp else 'none'}")
                        stats["errors"].append(f"update_failed:{pid}")
                        action = "update_failed"
                    shopify_id = existing_shopify_id
                    time.sleep(0.5)

                else:
                    # CREATE
                    images = []
                    primary_img = product.get("productImage", "")
                    if primary_img:
                        images.append({"src": primary_img})
                    for img_url in (product.get("productImageSet") or product.get("productImages") or []):
                        if isinstance(img_url, str) and img_url and img_url != primary_img:
                            images.append({"src": img_url})
                        elif isinstance(img_url, dict):
                            url = img_url.get("imageUrl", img_url.get("url", ""))
                            if url and url != primary_img:
                                images.append({"src": url})

                    variant_payloads = []
                    option_values = []
                    for v in variants:
                        v_cost = float(v.get("variantSellPrice", 0))
                        v_pricing = calculate_price(v_cost)
                        if v_pricing is None:
                            continue
                        v_sell, _ = v_pricing
                        v_name = v.get("variantNameEn", "Default")
                        sku_prefix = product.get("productSkuEn", "CJ")
                        variant_payloads.append({
                            "price": str(v_sell),
                            "sku": f"{sku_prefix}-{v.get('vid', '')}",
                            "option1": v_name,
                            "weight": product.get("productWeight", 0),
                            "weight_unit": "g",
                            "inventory_management": None,
                            "fulfillment_service": "manual",
                            "requires_shipping": True,
                            "taxable": True,
                        })
                        if v_name not in option_values:
                            option_values.append(v_name)

                    if not variant_payloads:
                        stats["products_skipped"] += 1
                        report_rows.append({
                            "cj_id": pid, "title": title, "cost": cost,
                            "sell_price": sell_price, "margin": round(margin, 4),
                            "collection": handle, "shopify_id": "",
                            "action": "skipped", "skip_reason": "no_viable_variants",
                        })
                        continue

                    body_html = product.get("productDescription") or product.get("description", "")
                    tags_str = f"EdisonHaus,{coll_title}"

                    create_payload = {
                        "product": {
                            "title": title,
                            "body_html": body_html,
                            "vendor": "EdisonHaus",
                            "product_type": coll_title,
                            "tags": tags_str,
                            "status": "active",
                            "images": images[:10],
                            "variants": variant_payloads,
                            "options": [{"name": "Option", "values": option_values}],
                        }
                    }

                    resp = api_call("POST", f"{SHOPIFY_BASE}/products.json",
                                    headers=shopify_headers(), json=create_payload)
                    if not resp or resp.status_code not in (200, 201):
                        log.warning(f"  Create failed {pid}: {resp.status_code if resp else 'none'} {resp.text[:200] if resp else ''}")
                        stats["errors"].append(f"create_failed:{pid}")
                        report_rows.append({
                            "cj_id": pid, "title": title, "cost": cost,
                            "sell_price": sell_price, "margin": round(margin, 4),
                            "collection": handle, "shopify_id": "",
                            "action": "create_failed", "skip_reason": "",
                        })
                        time.sleep(0.5)
                        continue

                    created = resp.json()["product"]
                    shopify_id = str(created["id"])
                    stats["products_created"] += 1
                    log.info(f"  Created {shopify_id} for {pid} ({title[:50]})")
                    action = "created"

                    # STEP 6: WRITE METAFIELDS
                    for mf in [
                        {"namespace": "dropship", "key": "cj_product_id", "value": str(pid), "type": "single_line_text_field"},
                        {"namespace": "dropship", "key": "cj_variant_id", "value": str(vid), "type": "single_line_text_field"},
                        {"namespace": "dropship", "key": "cj_cost_price", "value": str(cost), "type": "single_line_text_field"},
                        {"namespace": "dropship", "key": "supplier", "value": "CJDropshipping", "type": "single_line_text_field"},
                    ]:
                        mf_resp = api_call("POST",
                                           f"{SHOPIFY_BASE}/products/{shopify_id}/metafields.json",
                                           headers=shopify_headers(),
                                           json={"metafield": mf})
                        if mf_resp and mf_resp.status_code in (200, 201):
                            log.info(f"    Metafield {mf['key']} set")
                        else:
                            log.warning(f"    Metafield {mf['key']} failed: {mf_resp.status_code if mf_resp else 'none'}")
                        time.sleep(0.2)

                    time.sleep(0.5)

                # STEP 7: ASSIGN TO COLLECTION
                if coll_id and shopify_id:
                    collect_resp = api_call("POST", f"{SHOPIFY_BASE}/collects.json",
                                            headers=shopify_headers(),
                                            json={"collect": {"product_id": int(shopify_id), "collection_id": int(coll_id)}})
                    if collect_resp and collect_resp.status_code in (200, 201, 422):
                        log.info(f"    Assigned to {handle}")
                    else:
                        log.warning(f"    Collection assign failed: {collect_resp.status_code if collect_resp else 'none'}")
                    time.sleep(0.3)

                # STEP 8: SAVE TO DB
                cursor.execute("""
                    INSERT OR REPLACE INTO products
                    (cj_id, cj_vid, title, shopify_id, cost_usd, sell_price,
                     profit_margin, shopify_collection_id, image_url, status, last_synced)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'listed', datetime('now'))
                """, (
                    str(pid), str(vid), title, shopify_id, cost, sell_price,
                    round(margin, 4), coll_id,
                    product.get("productImage", ""),
                ))
                db.commit()

                report_rows.append({
                    "cj_id": pid, "title": title, "cost": cost,
                    "sell_price": sell_price, "margin": round(margin, 4),
                    "collection": handle, "shopify_id": shopify_id,
                    "action": action, "skip_reason": "",
                })

            except Exception as e:
                log.error(f"  Error processing {pid}: {e}\n{traceback.format_exc()}")
                stats["errors"].append(f"product_error:{pid}:{str(e)[:80]}")
                report_rows.append({
                    "cj_id": pid, "title": title, "cost": 0, "sell_price": 0,
                    "margin": 0, "collection": handle, "shopify_id": "",
                    "action": "error", "skip_reason": str(e)[:100],
                })

    write_heartbeat("step8_complete")
    return report_rows


# ---------------------------------------------------------------------------
# STEP 9 — GIT COMMIT
# ---------------------------------------------------------------------------

def step9_commit():
    log.info("=== STEP 9: Git Commit ===")
    try:
        def run_git(cmd):
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                log.warning(f"  git {' '.join(cmd[1:])}: {result.stderr.strip()}")
            return result.returncode

        subprocess.run(["git", "config", "user.name", "EdisonHaus Bot"],
                       capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "bot@edisonhaus.store"],
                       capture_output=True, text=True)
        run_git(["git", "add", "-f", "data/"])
        run_git(["git", "stash"])
        run_git(["git", "pull", "--rebase", "origin", "main"])
        run_git(["git", "stash", "pop"])
        run_git(["git", "add", "-f", "data/"])

        date_str = datetime.now().strftime("%Y-%m-%d")
        result = subprocess.run(["git", "diff", "--staged", "--quiet"],
                                capture_output=True, text=True)
        if result.returncode != 0:
            run_git(["git", "commit", "-m", f"Product pipeline {date_str} [skip ci]"])
            run_git(["git", "push", "origin", "main"])
            log.info("  Committed and pushed.")
        else:
            log.info("  No changes to commit.")
    except Exception as e:
        log.warning(f"  Git commit failed: {e}")
        stats["errors"].append(f"git_commit:{str(e)[:80]}")


# ---------------------------------------------------------------------------
# VERIFICATION
# ---------------------------------------------------------------------------

def verify(db):
    log.info("=== VERIFICATION ===")
    cursor = db.cursor()

    resp = api_call("GET", f"{SHOPIFY_BASE}/products/count.json", headers=shopify_headers())
    if resp and resp.status_code == 200:
        count = resp.json().get("count", 0)
        log.info(f"Total Shopify products: {count}")
        stats["total_live"] = count

    cursor.execute("SELECT shopify_id FROM products WHERE shopify_id IS NOT NULL ORDER BY RANDOM() LIMIT 5")
    samples = cursor.fetchall()
    for (sid,) in samples:
        mf_resp = api_call("GET",
                           f"{SHOPIFY_BASE}/products/{sid}/metafields.json?namespace=dropship",
                           headers=shopify_headers())
        if mf_resp and mf_resp.status_code == 200:
            mfs = mf_resp.json().get("metafields", [])
            has_vid = any(m["key"] == "cj_variant_id" for m in mfs)
            log.info(f"  Product {sid} cj_variant_id: {'PASS' if has_vid else 'FAIL'}")
        else:
            log.warning(f"  Product {sid} metafield check failed")
        time.sleep(0.3)

    resp = api_call("GET", f"{SHOPIFY_BASE}/custom_collections.json?limit=250", headers=shopify_headers())
    if resp and resp.status_code == 200:
        collections = resp.json().get("custom_collections", [])
        log.info(f"\n{'Collection':<40} | Count")
        log.info("-" * 55)
        for coll in collections:
            cnt_resp = api_call("GET",
                                f"{SHOPIFY_BASE}/products/count.json?collection_id={coll['id']}",
                                headers=shopify_headers())
            cnt = cnt_resp.json().get("count", "?") if cnt_resp and cnt_resp.status_code == 200 else "?"
            log.info(f"  {coll['title']:<38} | {cnt}")
            time.sleep(0.3)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 60)
    log.info("EdisonHaus Product Pipeline — Starting")
    log.info("=" * 60)

    missing = []
    for var in ("SHOPIFY_ACCESS_TOKEN", "CJ_API_KEY", "CJ_EMAIL"):
        if not os.environ.get(var):
            missing.append(var)
    if missing:
        log.error(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)

    write_heartbeat("starting")

    try:
        cj_token = step1_cj_auth()

        pid_map = step2_search_products(cj_token)

        total_pids = sum(len(v) for v in pid_map.values())
        if total_pids == 0:
            log.warning("No products found. Pipeline ending.")
            write_heartbeat("complete", "partial")
            return

        detail_map = step3_fetch_details(cj_token, pid_map)

        db = init_db()
        report_rows = process_products(detail_map, db)

        report_path = REPORTS_DIR / f"product_pipeline_{datetime.now().strftime('%Y-%m-%d')}.json"
        report_path.write_text(json.dumps(report_rows, indent=2))
        log.info(f"Report: {report_path} ({len(report_rows)} rows)")

        verify(db)
        db.close()

        final_status = "success" if not stats["errors"] else "partial"
        write_heartbeat("complete", final_status)

        log.info("=" * 60)
        log.info(f"Pipeline complete: {stats['products_created']} created, "
                 f"{stats['products_updated']} updated, {stats['products_skipped']} skipped, "
                 f"{len(stats['errors'])} errors")
        log.info("=" * 60)

        step9_commit()

    except Exception as e:
        log.error(f"Pipeline fatal error: {e}\n{traceback.format_exc()}")
        stats["errors"].append(f"fatal:{str(e)[:200]}")
        write_heartbeat("failed", "error")
        sys.exit(1)


if __name__ == "__main__":
    main()
