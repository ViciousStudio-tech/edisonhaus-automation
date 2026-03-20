#!/usr/bin/env python3
"""EdisonHaus — Clean up CJ product titles via Anthropic API."""

import builtins
def _no_input(*a, **k): raise RuntimeError("BLOCKED")
builtins.input = _no_input

import os, sys, json, time, re, logging, subprocess, traceback
from datetime import datetime, timezone
from pathlib import Path
import requests

# ── Config ────────────────────────────────────────────────────────────────
SHOPIFY_BASE = "https://fgtyz6-bj.myshopify.com/admin/api/2024-01"
SHOPIFY_TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
HB_PATH = Path("data/title_cleaner_heartbeat.json")
Path("data").mkdir(parents=True, exist_ok=True)
Path("reports").mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(Path("reports") / f"title_cleaner_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.log"),
              logging.StreamHandler(sys.stdout)])
log = logging.getLogger("title_cleaner")

def shop_h():
    return {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}

# ── Step 1: Fetch all products ────────────────────────────────────────────
def fetch_products():
    log.info("── Step 1: Fetch all products ──")
    products = []
    url = f"{SHOPIFY_BASE}/products.json?limit=250&fields=id,title,handle"
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
    log.info(f"Fetched {len(products)} products")
    return products

# ── Step 2: Identify titles needing cleanup ───────────────────────────────
def needs_cleaning(title):
    if not title:
        return False
    # Starts with numbers (e.g. "13 In 1", "22 Modern", "76 Wooden")
    if re.match(r'^\d+\s', title):
        return True
    # ALL CAPS words mid-title (3+ chars)
    words = title.split()
    if any(len(w) >= 3 and w.isupper() and w not in ("LED", "USB", "RGB", "DIY", "UV") for w in words[1:]):
        return True
    # Chinese-style compound hyphenated descriptors
    if re.search(r'[A-Z][a-z]+-[a-z]+-[a-z]+', title):
        return True
    # Wabi-sabi, Hemp-rope, etc.
    if re.search(r'(?i)(wabi.?sabi|hemp.?rope|instagram.?style|ins style)', title):
        return True
    # Keyword stuffing: 3+ commas
    if title.count(',') >= 3:
        return True
    # Too long
    if len(title) > 80:
        return True
    # Awkward patterns
    if re.search(r'(?i)(light.?luxury|for women|for men|niche design|high.?end feel)', title):
        return True
    # Model numbers / codes (e.g. "L5024-600-C")
    if re.search(r'[A-Z]\d{3,}', title):
        return True
    # "Pickup Only" or shipping-related text in title
    if re.search(r'(?i)(pickup only|self pickup|wholesale)', title):
        return True
    return False

# ── Step 3: Generate clean title via Anthropic ────────────────────────────
def clean_title(old_title):
    r = requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 30,
            "system": (
                "You are a product naming expert for EdisonHaus, a warm ambient home lighting "
                "and decor store. Rewrite the given product title into a clean, natural English "
                "retail product name. Rules: max 60 characters, keep the core product type and "
                "key feature, natural grammar, title case, no keyword stuffing, no model numbers, "
                "no brand names other than EdisonHaus. Return ONLY the new title, nothing else."
            ),
            "messages": [{"role": "user", "content": f"Rewrite this product title: {old_title}"}],
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise Exception(f"Anthropic {r.status_code}: {r.text[:200]}")
    text = r.json()["content"][0]["text"].strip()
    # Strip markdown, quotes
    text = text.strip('`"\'')
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# ── Main ──────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("EdisonHaus Title Cleaner")
    log.info("=" * 60)
    if not SHOPIFY_TOKEN:
        log.error("Missing SHOPIFY_ACCESS_TOKEN"); sys.exit(1)
    if not ANTHROPIC_KEY:
        log.error("Missing ANTHROPIC_API_KEY"); sys.exit(1)

    products = fetch_products()
    dirty = [p for p in products if needs_cleaning(p["title"])]
    log.info(f"── Step 2: {len(dirty)} of {len(products)} titles need cleaning ──")

    updated = 0
    failed = 0
    rewrites = []

    log.info("── Steps 3-4: Clean and update titles ──")
    for i, p in enumerate(dirty):
        old = p["title"]
        try:
            new = clean_title(old)
            time.sleep(1)

            # Validate
            if len(new) < 10 or len(new) > 65:
                log.warning(f"  Bad length ({len(new)}): '{new}' — keeping original")
                failed += 1
                continue

            # PUT only title
            r = requests.put(f"{SHOPIFY_BASE}/products/{p['id']}.json",
                headers=shop_h(),
                json={"product": {"id": p["id"], "title": new}},
                timeout=30)
            if r.status_code == 200:
                updated += 1
                rewrites.append({"old": old, "new": new})
                log.info(f"  [{updated}] {old[:50]} → {new}")
            else:
                failed += 1
                log.warning(f"  PUT failed for {p['id']}: {r.status_code}")
            time.sleep(0.5)
        except Exception as e:
            failed += 1
            log.error(f"  Error on {p['id']}: {e}")

    # Step 7: Heartbeat
    HB_PATH.write_text(json.dumps({
        "module": "b3_title_cleaner",
        "last_run": datetime.now(timezone.utc).isoformat(),
        "products_checked": len(products),
        "products_needing_cleanup": len(dirty),
        "products_updated": updated,
        "products_failed": failed,
        "status": "success" if failed == 0 else "partial",
        "sample_rewrites": rewrites[:10],
    }, indent=2))

    log.info("=" * 60)
    log.info(f"Done: {len(products)} checked, {len(dirty)} needed cleanup, {updated} updated, {failed} failed")
    log.info("=" * 60)

    # Print table
    log.info(f"\n{'OLD TITLE':<55} → NEW TITLE")
    log.info("-" * 100)
    for r in rewrites[:10]:
        log.info(f"  {r['old'][:53]:<55} → {r['new']}")

    # Step 8: Git commit
    try:
        def g(cmd): return subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        g(["git", "config", "user.name", "EdisonHaus Bot"])
        g(["git", "config", "user.email", "bot@edisonhaus.store"])
        g(["git", "add", "b3_title_cleaner.py", "b3_product_pipeline.py",
           ".github/workflows/b3_title_cleaner.yml", "data/title_cleaner_heartbeat.json"])
        g(["git", "stash"])
        g(["git", "pull", "--rebase", "origin", "main"])
        subprocess.run(["git", "stash", "pop"], capture_output=True, text=True)
        g(["git", "add", "b3_title_cleaner.py", "b3_product_pipeline.py",
           ".github/workflows/b3_title_cleaner.yml", "data/title_cleaner_heartbeat.json"])
        if subprocess.run(["git", "diff", "--staged", "--quiet"], capture_output=True).returncode != 0:
            g(["git", "commit", "-m", "feat: product title cleaner [skip ci]"])
            g(["git", "push", "origin", "main"])
            log.info("  Committed & pushed")
    except Exception as e:
        log.warning(f"  Git fail: {e}")


if __name__ == "__main__":
    main()
