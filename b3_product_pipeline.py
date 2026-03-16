#!/usr/bin/env python3
"""EdisonHaus Product Pipeline — discovers CJ categories, fetches products, lists to Shopify."""

import builtins
def _no_input(*a, **k): raise RuntimeError("BLOCKED: interactive prompt")
builtins.input = _no_input

import os
import sys
import json
import time
import sqlite3
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests
import anthropic

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
SHOPIFY_STORE = "fgtyz6-bj.myshopify.com"
SHOPIFY_ACCESS_TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
SHOPIFY_API = f"https://{SHOPIFY_STORE}/admin/api/2024-01"
CJ_EMAIL = os.environ.get("CJ_EMAIL", "nicholas.jacksondesign@gmail.com")
CJ_API_KEY = os.environ.get("CJ_API_KEY", "")
CJ_BASE = "https://developers.cjdropshipping.com/api2.0/v1"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DB_PATH = Path("data/dropship.db")
CATEGORY_MAP_PATH = Path("data/category_map.json")
HEARTBEAT_PATH = Path("data/product_pipeline_heartbeat.json")
REPORTS_DIR = Path("reports")

REPORTS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

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
    "categories_mapped": 0,
    "collections_created": 0,
    "products_fetched": 0,
    "products_created": 0,
    "products_updated": 0,
    "products_skipped": 0,
    "total_live": 0,
    "errors": [],
}

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def api_call(method, url, retries=3, backoff=2, **kwargs):
    """HTTP call with exponential backoff."""
    for attempt in range(retries):
        try:
            resp = requests.request(method, url, timeout=30, **kwargs)
            if resp.status_code == 429:
                wait = backoff ** (attempt + 1)
                log.warning(f"Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            return resp
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise
            wait = backoff ** (attempt + 1)
            log.warning(f"Request failed ({e}), retrying in {wait}s...")
            time.sleep(wait)
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
        "categories_mapped": stats["categories_mapped"],
        "collections_created": stats["collections_created"],
        "products_fetched": stats["products_fetched"],
        "products_created": stats["products_created"],
        "products_updated": stats["products_updated"],
        "products_skipped": stats["products_skipped"],
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
        sell = cost * 4.0
    elif cost < 15.0:
        sell = cost * 3.0
    elif cost < 30.0:
        sell = cost * 2.5
    elif cost < 60.0:
        sell = cost * 2.2
    else:
        sell = cost * 2.0

    # Floor
    if sell < 14.99:
        sell = 14.99

    # Round up to nearest $x.99
    whole = int(sell)
    sell = float(whole) + 0.99
    if sell < 14.99:
        sell = 14.99

    margin = (sell - cost) / sell
    if margin < 0.40:
        return None
    return sell, margin


TAG_KEYWORDS = {
    "lamp", "light", "led", "pendant", "ceiling", "ambient", "fairy",
    "string", "neon", "strip", "solar", "smart", "pillow", "basket",
    "vase", "candle", "wall", "decor", "cozy", "warm", "rattan",
    "woven", "canvas", "tapestry",
}


def build_tags(product):
    tags = {"edisonhaus"}
    cat_name = product.get("categoryName", "")
    if cat_name:
        leaf = cat_name.split("/")[-1].strip()
        if leaf:
            tags.add(leaf.lower())
    pt = product.get("productType", "")
    if pt and pt.lower() not in ("", "na", "n/a", "none"):
        tags.add(pt.lower())
    mat = product.get("materialEn", "")
    if mat and mat.lower() not in ("", "na", "n/a", "none"):
        tags.add(mat.lower())
    title_words = set(product.get("productNameEn", "").lower().split())
    for kw in TAG_KEYWORDS:
        if kw in title_words:
            tags.add(kw)
    return ", ".join(sorted(tags))


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
            cj_category_id        TEXT,
            cj_category_name      TEXT,
            shopify_collection_id INTEGER,
            cost_usd              REAL,
            sell_price            REAL,
            profit_margin         REAL,
            image_url             TEXT,
            shopify_id            TEXT,
            shopify_variant_ids   TEXT,
            status                TEXT DEFAULT 'listed',
            last_synced           TEXT DEFAULT (datetime('now')),
            created_at            TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# PHASE 1 — CJ AUTH
# ---------------------------------------------------------------------------

def phase1_cj_auth():
    log.info("=== PHASE 1: CJ Authentication ===")
    resp = api_call("POST", f"{CJ_BASE}/authentication/getAccessToken",
                    json={"email": CJ_EMAIL, "password": CJ_API_KEY})
    if not resp or resp.status_code != 200:
        log.error(f"CJ auth failed: {resp.status_code if resp else 'no response'} {resp.text if resp else ''}")
        sys.exit(1)
    data = resp.json()
    if not data.get("result") or not data.get("data", {}).get("accessToken"):
        # CJ sometimes uses 'data' as a string token directly
        token = data.get("data", {}).get("accessToken") or data.get("data")
        if not token:
            log.error(f"CJ auth response missing token: {json.dumps(data)}")
            sys.exit(1)
    else:
        token = data["data"]["accessToken"]
    log.info("CJ auth successful, token cached.")
    write_heartbeat("phase1_complete")
    return token


# ---------------------------------------------------------------------------
# PHASE 2 — DISCOVER CATEGORIES
# ---------------------------------------------------------------------------

def phase2_discover_categories(cj_token):
    """
    Phase 2: Define EdisonHaus collections and create them in Shopify.
    Uses keyword-based product search — CJ category API returns empty data.
    """
    log.info("=== PHASE 2: Discover Categories ===")

    category_map = [
        {"shopify_collection_name": "LED & Ambient Lighting",
         "shopify_collection_handle": "led-ambient-lighting",
         "search_keywords": ["LED strip light", "fairy lights", "string lights",
                             "ambient light", "neon light", "galaxy projector",
                             "sunset lamp", "smart light strip"],
         "shopify_collection_id": None},
        {"shopify_collection_name": "Table & Desk Lamps",
         "shopify_collection_handle": "table-desk-lamps",
         "search_keywords": ["table lamp", "desk lamp", "bedside lamp", "reading lamp"],
         "shopify_collection_id": None},
        {"shopify_collection_name": "Pendant & Ceiling Lights",
         "shopify_collection_handle": "pendant-ceiling-lights",
         "search_keywords": ["pendant light", "chandelier", "ceiling light", "hanging lamp"],
         "shopify_collection_id": None},
        {"shopify_collection_name": "Wall Decor",
         "shopify_collection_handle": "wall-decor",
         "search_keywords": ["canvas wall art", "wall painting", "wall hanging",
                             "tapestry wall", "decorative painting"],
         "shopify_collection_id": None},
        {"shopify_collection_name": "Cozy Textiles",
         "shopify_collection_handle": "cozy-textiles",
         "search_keywords": ["throw pillow cover", "cushion cover", "sofa pillow cover"],
         "shopify_collection_id": None},
        {"shopify_collection_name": "Storage & Accents",
         "shopify_collection_handle": "storage-accents",
         "search_keywords": ["woven basket", "rattan basket", "candle holder",
                             "decorative vase", "storage basket"],
         "shopify_collection_id": None},
    ]

    stats["categories_mapped"] = len(category_map)
    log.info(f"Using {len(category_map)} collections.")

    created = 0
    for entry in category_map:
        handle = entry["shopify_collection_handle"]
        name   = entry["shopify_collection_name"]
        try:
            r = shopify_request("GET", f"/custom_collections.json?handle={handle}")
            cols = r.get("custom_collections", [])
            if cols:
                entry["shopify_collection_id"] = cols[0]["id"]
                log.info(f"  Found: {name} (id={cols[0]['id']})")
            else:
                r2 = shopify_request("POST", "/custom_collections.json",
                    json={"custom_collection": {"title": name, "handle": handle, "published": True}})
                entry["shopify_collection_id"] = r2["custom_collection"]["id"]
                created += 1
                log.info(f"  Created: {name} (id={entry['shopify_collection_id']})")
        except Exception as e:
            log.error(f"  Collection {name} failed: {e}")

    stats["collections_created"] = created
    CATEGORY_MAP_PATH.write_text(json.dumps(category_map, indent=2))
    write_heartbeat("phase2_complete")
    return category_map


def phase3_fetch_products(cj_token, category_map):
    """
    Phase 3: Fetch products from CJ using keyword search per collection.
    Fetches full product detail for every product found.
    """
    log.info("=== PHASE 3: Fetch Products from CJ ===")
    all_products = []
    seen_pids = set()

    for entry in category_map:
        coll_id  = entry.get("shopify_collection_id")
        coll_name = entry["shopify_collection_name"]
        keywords  = entry.get("search_keywords", [])

        log.info(f"Fetching products for: {coll_name}")

        for keyword in keywords:
            page = 1
            while True:
                resp = api_call("GET", f"{CJ_BASE}/product/list",
                    headers=cj_headers(cj_token),
                    params={"productNameEn": keyword, "pageNum": page, "pageSize": 50})
                if not resp or resp.status_code != 200:
                    log.warning(f"  Search failed: {keyword} page {page}")
                    break

                data = resp.json()
                products = (data.get("data", {}).get("list", [])
                           if isinstance(data.get("data"), dict)
                           else data.get("data", []))
                if not products:
                    break

                for p in products:
                    pid = str(p.get("pid") or p.get("productId") or "")
                    if not pid or pid in seen_pids:
                        continue
                    seen_pids.add(pid)

                    # Fetch full product detail
                    time.sleep(0.5)
                    detail_resp = api_call("GET", f"{CJ_BASE}/product/query",
                        headers=cj_headers(cj_token),
                        params={"pid": pid})
                    if not detail_resp or detail_resp.status_code != 200:
                        log.warning(f"  Detail fetch failed for pid={pid}")
                        continue

                    detail_data = detail_resp.json()
                    detail = (detail_data.get("data") or
                             (detail_data.get("data", {}) if isinstance(detail_data.get("data"), dict) else None))
                    if not detail:
                        continue

                    detail["_shopify_collection_id"] = coll_id
                    detail["_shopify_collection_handle"] = entry["shopify_collection_handle"]
                    all_products.append(detail)

                if len(products) < 50:
                    break
                page += 1
                time.sleep(0.3)

        log.info(f"  Total products so far: {len(all_products)}")

    log.info(f"Phase 3 complete. Total unique products fetched: {len(all_products)}")
    stats["products_fetched"] = len(all_products)
    write_heartbeat("phase3_complete")
    return all_products


def phase5_create_or_update(products, db, category_map):
    log.info(f"=== PHASE 5: Create/Update {len(products)} Shopify Products ===")
    report_rows = []
    cursor = db.cursor()
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Build collection handle lookup
    handle_to_id = {}
    for entry in category_map:
        h = entry.get("shopify_collection_handle")
        cid = entry.get("shopify_collection_id")
        if h and cid:
            handle_to_id[h] = cid

    for product in products:
        pid = product.get("pid") or product.get("productId", "")
        title = product.get("productNameEn", "Untitled")
        try:
            # Get variants
            variants = product.get("variants", [])
            if not variants:
                log.warning(f"No variants for {pid} ({title}), skipping")
                stats["products_skipped"] += 1
                report_rows.append({
                    "cj_id": pid, "title": title, "cost": 0, "sell_price": 0,
                    "margin": 0, "collection": "", "shopify_id": "",
                    "action": "skipped", "skip_reason": "no_variants",
                })
                continue

            # Find cheapest variant
            cheapest = min(variants, key=lambda v: float(v.get("variantSellPrice", 999999)))
            cost = float(cheapest.get("variantSellPrice", 0))
            vid = cheapest.get("vid", "")

            # Phase 4: Price calculation
            pricing = calculate_price(cost)
            if pricing is None:
                log.info(f"Skipping {pid} ({title}): cost={cost}, margin too low or zero cost")
                stats["products_skipped"] += 1
                report_rows.append({
                    "cj_id": pid, "title": title, "cost": cost, "sell_price": 0,
                    "margin": 0, "collection": "", "shopify_id": "",
                    "action": "skipped", "skip_reason": "low_margin_or_zero_cost",
                })
                continue
            sell_price, margin = pricing

            # Check if already in DB with shopify_id
            cursor.execute("SELECT shopify_id FROM products WHERE cj_id=? AND shopify_id IS NOT NULL", (str(pid),))
            row = cursor.fetchone()
            existing_shopify_id = row[0] if row else None

            # Build images
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

            # Build tags
            tags_str = build_tags(product)

            # Build variant payloads
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
                log.info(f"Skipping {pid}: no viable variants after pricing")
                stats["products_skipped"] += 1
                report_rows.append({
                    "cj_id": pid, "title": title, "cost": cost, "sell_price": sell_price,
                    "margin": round(margin, 4), "collection": "", "shopify_id": "",
                    "action": "skipped", "skip_reason": "no_viable_variants",
                })
                continue

            body_html = product.get("productDescription") or product.get("description", "")

            if existing_shopify_id:
                # UPDATE
                update_payload = {
                    "product": {
                        "id": int(existing_shopify_id),
                        "body_html": body_html,
                        "tags": tags_str,
                        "variants": variant_payloads,
                    }
                }
                resp = api_call("PUT",
                                f"{SHOPIFY_API}/products/{existing_shopify_id}.json",
                                headers=shopify_headers(), json=update_payload)
                if resp and resp.status_code == 200:
                    log.info(f"Updated Shopify product {existing_shopify_id} for {pid}")
                    stats["products_updated"] += 1
                    action = "updated"
                else:
                    log.warning(f"Failed to update {existing_shopify_id}: {resp.status_code if resp else 'none'}")
                    stats["errors"].append(f"update_failed:{pid}:{resp.status_code if resp else 'none'}")
                    action = "update_failed"
                shopify_id = existing_shopify_id
                shopify_variant_ids = ""
            else:
                # CREATE
                create_payload = {
                    "product": {
                        "title": title,
                        "body_html": body_html,
                        "vendor": "EdisonHaus",
                        "product_type": product.get("categoryName", ""),
                        "tags": tags_str,
                        "status": "active",
                        "images": images[:10],  # Shopify limit
                        "variants": variant_payloads,
                        "options": [{"name": "Option", "values": option_values}],
                    }
                }
                resp = api_call("POST", f"{SHOPIFY_API}/products.json",
                                headers=shopify_headers(), json=create_payload)
                if not resp or resp.status_code not in (200, 201):
                    log.warning(f"Failed to create product for {pid}: {resp.status_code if resp else 'none'} {resp.text[:200] if resp else ''}")
                    stats["errors"].append(f"create_failed:{pid}")
                    report_rows.append({
                        "cj_id": pid, "title": title, "cost": cost,
                        "sell_price": sell_price, "margin": round(margin, 4),
                        "collection": "", "shopify_id": "",
                        "action": "create_failed", "skip_reason": "",
                    })
                    time.sleep(0.5)
                    continue

                created = resp.json()["product"]
                shopify_id = str(created["id"])
                shopify_variant_ids = ",".join(str(v["id"]) for v in created.get("variants", []))
                stats["products_created"] += 1
                log.info(f"Created Shopify product {shopify_id} for {pid} ({title})")
                action = "created"

                # Write metafields
                metafields = [
                    {"namespace": "dropship", "key": "cj_product_id", "value": str(pid), "type": "single_line_text_field"},
                    {"namespace": "dropship", "key": "cj_variant_id", "value": str(vid), "type": "single_line_text_field"},
                    {"namespace": "dropship", "key": "cj_cost_price", "value": str(cost), "type": "single_line_text_field"},
                    {"namespace": "dropship", "key": "supplier", "value": "CJDropshipping", "type": "single_line_text_field"},
                ]
                for mf in metafields:
                    mf_resp = api_call("POST",
                                       f"{SHOPIFY_API}/products/{shopify_id}/metafields.json",
                                       headers=shopify_headers(),
                                       json={"metafield": mf})
                    if mf_resp and mf_resp.status_code in (200, 201):
                        log.info(f"  Metafield {mf['key']} set for {shopify_id}")
                    else:
                        log.warning(f"  Metafield {mf['key']} failed: {mf_resp.status_code if mf_resp else 'none'}")
                    time.sleep(0.2)

            # Phase 6: Assign to collection
            coll_id = product.get("_pipeline_collection_id")
            if not coll_id:
                # Try Claude fallback
                cat_id = product.get("_pipeline_category_id", "")
                cat_name_raw = product.get("_pipeline_category_name", "")
                handles_list = list(handle_to_id.keys())
                try:
                    msg = client.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=100,
                        messages=[{"role": "user", "content":
                            f"Product: {title}, CJ category: {cat_name_raw}, type: {product.get('productType', '')}. "
                            f"Collections available: {handles_list}. "
                            f"Return ONLY the single best-matching collection handle."}],
                    )
                    chosen_handle = msg.content[0].text.strip().strip('"').strip("'")
                    coll_id = handle_to_id.get(chosen_handle)
                except Exception as e:
                    log.warning(f"Claude collection fallback failed for {pid}: {e}")

            if coll_id:
                collect_resp = api_call("POST", f"{SHOPIFY_API}/collects.json",
                                        headers=shopify_headers(),
                                        json={"collect": {"product_id": int(shopify_id), "collection_id": int(coll_id)}})
                if collect_resp and collect_resp.status_code in (200, 201, 422):
                    log.info(f"  Assigned {shopify_id} to collection {coll_id}")
                else:
                    log.warning(f"  Collection assign failed: {collect_resp.status_code if collect_resp else 'none'}")

            # Phase 7: Persist to DB
            cursor.execute("""
                INSERT OR REPLACE INTO products
                (cj_id, cj_vid, title, cj_category_id, cj_category_name,
                 shopify_collection_id, cost_usd, sell_price, profit_margin,
                 image_url, shopify_id, shopify_variant_ids, status, last_synced)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'listed', datetime('now'))
            """, (
                str(pid), str(vid), title,
                str(product.get("_pipeline_category_id", "")),
                product.get("_pipeline_category_name", ""),
                coll_id, cost, sell_price, round(margin, 4),
                primary_img, shopify_id,
                shopify_variant_ids if not existing_shopify_id else "",
                ))
            db.commit()

            report_rows.append({
                "cj_id": pid, "title": title, "cost": cost,
                "sell_price": sell_price, "margin": round(margin, 4),
                "collection": product.get("_pipeline_collection_handle", ""),
                "shopify_id": shopify_id, "action": action, "skip_reason": "",
            })

            time.sleep(0.5)

        except Exception as e:
            log.error(f"Error processing product {pid}: {e}\n{traceback.format_exc()}")
            stats["errors"].append(f"product_error:{pid}:{str(e)[:80]}")
            report_rows.append({
                "cj_id": pid, "title": title, "cost": 0, "sell_price": 0,
                "margin": 0, "collection": "", "shopify_id": "",
                "action": "error", "skip_reason": str(e)[:100],
            })

    write_heartbeat("phase5_complete")
    return report_rows


# ---------------------------------------------------------------------------
# PHASE 8 — REPORT
# ---------------------------------------------------------------------------

def phase8_report(report_rows):
    log.info("=== PHASE 8: Write Report ===")
    report_path = REPORTS_DIR / f"product_pipeline_{datetime.now().strftime('%Y-%m-%d')}.json"
    report_path.write_text(json.dumps(report_rows, indent=2))
    log.info(f"Report written to {report_path} ({len(report_rows)} rows)")


# ---------------------------------------------------------------------------
# VERIFICATION
# ---------------------------------------------------------------------------

def verify(db):
    log.info("=== VERIFICATION ===")
    cursor = db.cursor()

    # 1. Product count
    resp = api_call("GET", f"{SHOPIFY_API}/products/count.json", headers=shopify_headers())
    if resp and resp.status_code == 200:
        count = resp.json().get("count", 0)
        log.info(f"Total Shopify products: {count}")
        stats["total_live"] = count

    # 2. Sample metafield check
    cursor.execute("SELECT shopify_id FROM products WHERE shopify_id IS NOT NULL ORDER BY RANDOM() LIMIT 5")
    samples = cursor.fetchall()
    for (sid,) in samples:
        mf_resp = api_call("GET",
                           f"{SHOPIFY_API}/products/{sid}/metafields.json?namespace=dropship",
                           headers=shopify_headers())
        if mf_resp and mf_resp.status_code == 200:
            mfs = mf_resp.json().get("metafields", [])
            has_vid = any(m["key"] == "cj_variant_id" for m in mfs)
            log.info(f"  Product {sid} cj_variant_id: {'PASS' if has_vid else 'FAIL'}")
        else:
            log.warning(f"  Product {sid} metafield check failed")
        time.sleep(0.3)

    # 3. Collection counts
    resp = api_call("GET", f"{SHOPIFY_API}/custom_collections.json?limit=250", headers=shopify_headers())
    if resp and resp.status_code == 200:
        collections = resp.json().get("custom_collections", [])
        log.info(f"\n{'Collection':<40} | Product Count")
        log.info("-" * 60)
        for coll in collections:
            cnt_resp = api_call("GET",
                                f"{SHOPIFY_API}/products/count.json?collection_id={coll['id']}",
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

    # Preflight: check required secrets
    missing = []
    for var in ("SHOPIFY_ACCESS_TOKEN", "CJ_API_KEY", "ANTHROPIC_API_KEY"):
        if not os.environ.get(var):
            missing.append(var)
    if missing:
        log.error(f"Missing required environment variables: {', '.join(missing)}")
        log.error("Set these secrets or run via GitHub Actions workflow_dispatch.")
        sys.exit(1)

    write_heartbeat("starting")

    try:
        # Phase 1
        cj_token = phase1_cj_auth()

        # Phase 2
        category_map = phase2_discover_categories(cj_token)

        # Phase 3
        products = phase3_fetch_products(cj_token, category_map)

        if not products:
            log.warning("No products fetched. Pipeline ending.")
            write_heartbeat("complete", "partial")
            return

        # Phase 5 (includes 4, 6, 7 inline)
        db = init_db()
        report_rows = phase5_create_or_update(products, db, category_map)

        # Phase 8
        phase8_report(report_rows)

        # Verification
        verify(db)

        db.close()

        final_status = "success" if not stats["errors"] else "partial"
        write_heartbeat("complete", final_status)

        log.info("=" * 60)
        log.info(f"Pipeline complete: {stats['products_created']} created, "
                 f"{stats['products_updated']} updated, {stats['products_skipped']} skipped, "
                 f"{len(stats['errors'])} errors")
        log.info("=" * 60)

    except Exception as e:
        log.error(f"Pipeline fatal error: {e}\n{traceback.format_exc()}")
        stats["errors"].append(f"fatal:{str(e)[:200]}")
        write_heartbeat("failed", "error")
        sys.exit(1)


if __name__ == "__main__":
    main()
