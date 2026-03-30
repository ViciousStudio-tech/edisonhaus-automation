#!/usr/bin/env python3
"""EdisonHaus — Google Merchant Center XML + Meta CSV product feed generator."""

import builtins
def _no_input(*a, **k): raise RuntimeError("BLOCKED")
builtins.input = _no_input

import os, sys, json, time, csv, io, logging, subprocess, traceback, re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree, indent
import requests

ET.register_namespace('g', 'http://base.google.com/ns/1.0')

# ── Config ────────────────────────────────────────────────────────────────
SHOPIFY_BASE = "https://fgtyz6-bj.myshopify.com/admin/api/2024-01"
SHOPIFY_TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
SITE_URL = "https://edisonhaus.com"
FEEDS_DIR = Path("feeds")
HB_PATH = Path("data/feed_generator_heartbeat.json")
GOOGLE_FEED = FEEDS_DIR / "google_feed.xml"
META_FEED = FEEDS_DIR / "meta_feed.csv"
INDEX_HTML = FEEDS_DIR / "index.html"

FEEDS_DIR.mkdir(parents=True, exist_ok=True)
Path("data").mkdir(parents=True, exist_ok=True)
Path("reports").mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(Path("reports") / f"feed_gen_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.log"),
              logging.StreamHandler(sys.stdout)])
log = logging.getLogger("feed_gen")

def shop_h():
    return {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}

def strip_html(html):
    if not html: return ""
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:5000]

# ── Google category mapping ───────────────────────────────────────────────
def google_category(product_type, title=""):
    pt = (product_type or "").lower()
    t = (title or "").lower()
    combined = pt + " " + t
    if any(w in combined for w in ["pendant", "ceiling", "chandelier"]):
        return "4492"
    if any(w in combined for w in ["led", "strip", "fairy", "string"]):
        return "2636"
    if any(w in combined for w in ["lamp", "lamps"]):
        return "2706"
    if any(w in combined for w in ["wall", "tapestry", "canvas", "art"]):
        return "500044"
    if any(w in combined for w in ["pillow", "cushion", "textile", "blanket"]):
        return "505821"
    if any(w in combined for w in ["storage", "basket", "vase", "candle", "accent"]):
        return "6869"
    return "2706"

# ── Step 1: Fetch all products ────────────────────────────────────────────
def fetch_products():
    log.info("── Step 1: Fetch all Shopify products ──")
    products = []
    url = f"{SHOPIFY_BASE}/products.json?limit=250&status=active"
    while url:
        r = requests.get(url, headers=shop_h(), timeout=30)
        if r.status_code != 200:
            log.error(f"Fetch failed: {r.status_code}"); break
        products.extend(r.json().get("products", []))
        link = r.headers.get("Link", "")
        url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
    log.info(f"Fetched {len(products)} active products")
    return products

# ── Step 2: Google Merchant Center XML ────────────────────────────────────
def generate_google_feed(products):
    log.info("── Step 2: Generate Google feed ──")
    NS = "http://base.google.com/ns/1.0"

    rss = Element("rss", version="2.0")
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = "EdisonHaus"
    SubElement(channel, "link").text = SITE_URL
    SubElement(channel, "description").text = "Warm Ambient Home Lighting & Decor"

    count = 0
    for p in products:
        if not p.get("variants"): continue
        v = p["variants"][0]
        handle = p.get("handle", "")
        images = p.get("images", [])
        if not images: continue

        title = p.get("title", "")
        desc = strip_html(p.get("body_html", ""))
        if len(desc) < 20:
            desc = f"Shop {title} at EdisonHaus. Premium warm ambient home lighting and decor for cozy living spaces. Free shipping on orders over 50 dollars."

        item = SubElement(channel, "item")
        SubElement(item, f"{{{NS}}}id").text = str(p["id"])
        SubElement(item, f"{{{NS}}}title").text = title
        SubElement(item, f"{{{NS}}}description").text = desc
        SubElement(item, f"{{{NS}}}link").text = f"{SITE_URL}/products/{handle}"
        SubElement(item, f"{{{NS}}}image_link").text = images[0]["src"]
        if len(images) > 1:
            SubElement(item, f"{{{NS}}}additional_image_link").text = images[1]["src"]
        SubElement(item, f"{{{NS}}}price").text = f"{v['price']} USD"
        SubElement(item, f"{{{NS}}}availability").text = "in_stock"
        SubElement(item, f"{{{NS}}}condition").text = "new"
        SubElement(item, f"{{{NS}}}brand").text = "EdisonHaus"
        SubElement(item, f"{{{NS}}}product_type").text = p.get("product_type", "")
        SubElement(item, f"{{{NS}}}google_product_category").text = google_category(p.get("product_type", ""), title)
        SubElement(item, f"{{{NS}}}identifier_exists").text = "no"
        count += 1

    tree = ElementTree(rss)
    indent(tree, space="  ")
    tree.write(str(GOOGLE_FEED), encoding="unicode", xml_declaration=True)
    log.info(f"Google feed: {count} products → {GOOGLE_FEED}")
    return count

# ── Step 3: Meta CSV feed ─────────────────────────────────────────────────
def generate_meta_feed(products):
    log.info("── Step 3: Generate Meta feed ──")
    rows = []
    for p in products:
        handle = p.get("handle", "")
        images = p.get("images", [])
        img = images[0]["src"] if images else ""
        desc = strip_html(p.get("body_html", ""))
        for v in p.get("variants", []):
            rows.append({
                "id": str(v["id"]),
                "title": p.get("title", ""),
                "description": desc,
                "availability": "in stock",
                "condition": "new",
                "price": f"{v['price']} USD",
                "link": f"{SITE_URL}/products/{handle}",
                "image_link": img,
                "brand": "EdisonHaus",
                "product_type": p.get("product_type", ""),
            })

    with open(META_FEED, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id","title","description","availability","condition","price","link","image_link","brand","product_type"])
        w.writeheader()
        w.writerows(rows)
    log.info(f"Meta feed: {len(rows)} rows → {META_FEED}")
    return len(rows)

# ── Step 4: Index HTML ────────────────────────────────────────────────────
def generate_index():
    log.info("── Step 4: Generate index.html ──")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    google_url = "https://viciousstudio-tech.github.io/edisonhaus-automation/feeds/google_feed.xml"
    meta_url = "https://viciousstudio-tech.github.io/edisonhaus-automation/feeds/meta_feed.csv"
    INDEX_HTML.write_text(f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>EdisonHaus Product Feeds</title>
<style>body{{font-family:system-ui;max-width:600px;margin:40px auto;padding:0 20px}}a{{color:#2563eb}}</style></head>
<body>
<h1>EdisonHaus Product Feeds</h1>
<p>Last updated: {ts}</p>
<ul>
<li><a href="{google_url}">Google Merchant Center Feed (XML)</a></li>
<li><a href="{meta_url}">Meta Product Catalog Feed (CSV)</a></li>
</ul>
<p>Store: <a href="https://edisonhaus.com">edisonhaus.com</a></p>
</body></html>""")

# ── Main ──────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("EdisonHaus Product Feed Generator")
    log.info("=" * 60)
    if not SHOPIFY_TOKEN:
        log.error("Missing SHOPIFY_ACCESS_TOKEN"); sys.exit(1)

    products = fetch_products()
    if not products:
        log.warning("No products found"); return

    g_count = generate_google_feed(products)
    m_count = generate_meta_feed(products)
    generate_index()

    # Heartbeat
    HB_PATH.write_text(json.dumps({
        "module": "promo_feed_generator",
        "last_run": datetime.now(timezone.utc).isoformat(),
        "products_in_feed": g_count,
        "google_feed_url": "https://viciousstudio-tech.github.io/edisonhaus-automation/feeds/google_feed.xml",
        "meta_feed_url": "https://viciousstudio-tech.github.io/edisonhaus-automation/feeds/meta_feed.csv",
        "status": "success",
    }, indent=2))
    log.info(f"Done: {g_count} products in Google feed, {m_count} rows in Meta feed")

    # Git commit
    try:
        def g(cmd): return subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        g(["git", "config", "user.name", "EdisonHaus Bot"])
        g(["git", "config", "user.email", "bot@edisonhaus.store"])
        g(["git", "add", "-f", "feeds/", "data/feed_generator_heartbeat.json"])
        g(["git", "stash"])
        g(["git", "pull", "--rebase", "origin", "main"])
        subprocess.run(["git", "stash", "pop"], capture_output=True, text=True)
        g(["git", "add", "-f", "feeds/", "data/feed_generator_heartbeat.json"])
        if subprocess.run(["git", "diff", "--staged", "--quiet"], capture_output=True).returncode != 0:
            g(["git", "commit", "-m", f"Feeds: updated {datetime.now().strftime('%Y-%m-%d')} [skip ci]"])
            g(["git", "push", "origin", "main"])
            log.info("  Committed & pushed")
    except Exception as e:
        log.warning(f"  Git fail: {e}")

if __name__ == "__main__":
    main()
