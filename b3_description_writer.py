#!/usr/bin/env python3
"""EdisonHaus — Fill missing product descriptions via Anthropic API."""

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
HB_PATH = Path("data/description_writer_heartbeat.json")
Path("data").mkdir(parents=True, exist_ok=True)
Path("reports").mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(Path("reports") / f"desc_writer_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.log"),
              logging.StreamHandler(sys.stdout)])
log = logging.getLogger("desc_writer")

def shop_h():
    return {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}

# ── Step 1: Fetch all products missing descriptions ──────────────────────
def fetch_missing():
    log.info("── Step 1: Fetch products missing descriptions ──")
    missing = []
    url = f"{SHOPIFY_BASE}/products.json?limit=250&fields=id,title,body_html"
    while url:
        r = requests.get(url, headers=shop_h(), timeout=30)
        if r.status_code != 200:
            log.error(f"Fetch failed: {r.status_code}")
            break
        for p in r.json().get("products", []):
            html = (p.get("body_html") or "").strip()
            if len(html) < 50:
                missing.append({"id": p["id"], "title": p["title"], "body_html": html})
        # Cursor pagination via Link header
        link = r.headers.get("Link", "")
        url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
    log.info(f"Found {len(missing)} products missing descriptions (out of total fetched)")
    return missing

# ── Step 2: Generate description via Anthropic ───────────────────────────
def generate_description(title):
    r = requests.post("https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 400,
            "system": (
                "You are a product copywriter for EdisonHaus, a warm ambient home lighting "
                "and decor store. Write a compelling product description using only <p> and "
                "<ul><li> HTML tags. No headers, no bold, no other tags. 80-120 words. Focus "
                "on ambiance, style, and home decor appeal. Do not invent specific measurements "
                "or specs. Do not mention other brand names."
            ),
            "messages": [{"role": "user", "content": f"Write a product description for: {title}"}],
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise Exception(f"Anthropic {r.status_code}: {r.text[:200]}")
    text = r.json()["content"][0]["text"].strip()
    # Strip markdown fences
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    if "<p>" not in text:
        raise Exception(f"Response missing <p> tag: {text[:100]}")
    return text

# ── Step 3: Update only body_html on Shopify ─────────────────────────────
def update_body_html(product_id, body_html):
    r = requests.put(f"{SHOPIFY_BASE}/products/{product_id}.json",
        headers=shop_h(),
        json={"product": {"id": product_id, "body_html": body_html}},
        timeout=30)
    return r.status_code == 200, r

# ── Main ──────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("EdisonHaus Description Writer")
    log.info("=" * 60)

    if not SHOPIFY_TOKEN:
        log.error("Missing SHOPIFY_ACCESS_TOKEN"); sys.exit(1)
    if not ANTHROPIC_KEY:
        log.error("Missing ANTHROPIC_API_KEY"); sys.exit(1)

    missing = fetch_missing()
    if not missing:
        log.info("No products need descriptions.")
        HB_PATH.write_text(json.dumps({
            "module": "description_writer",
            "last_run": datetime.now(timezone.utc).isoformat(),
            "products_found_missing": 0, "products_updated": 0,
            "products_failed": 0, "status": "success"
        }, indent=2))
        return

    updated = 0
    failed = 0
    updated_products = []

    log.info(f"── Step 2-3: Generate and update {len(missing)} descriptions ──")
    for i, p in enumerate(missing):
        try:
            log.info(f"  [{i+1}/{len(missing)}] {p['title'][:60]}")
            desc = generate_description(p["title"])
            time.sleep(1)

            ok, resp = update_body_html(p["id"], desc)
            if ok:
                updated += 1
                updated_products.append({"id": p["id"], "title": p["title"], "body_html": desc})
                log.info(f"    Updated: {p['title'][:50]}")
            else:
                failed += 1
                log.warning(f"    Shopify PUT failed: {resp.status_code}")
            time.sleep(0.5)
        except Exception as e:
            failed += 1
            log.error(f"    Error: {e}")

    # Step 5: Heartbeat
    status = "success" if failed == 0 else "partial"
    HB_PATH.write_text(json.dumps({
        "module": "description_writer",
        "last_run": datetime.now(timezone.utc).isoformat(),
        "products_found_missing": len(missing),
        "products_updated": updated,
        "products_failed": failed,
        "status": status,
    }, indent=2))
    log.info(f"Done: {updated} updated, {failed} failed out of {len(missing)}")

    # Verification: print 3 random samples
    import random
    samples = random.sample(updated_products, min(3, len(updated_products)))
    log.info("── Verification samples ──")
    for s in samples:
        log.info(f"  {s['title'][:50]}")
        log.info(f"    {s['body_html'][:100]}...")

    # Step 6: Git commit
    log.info("── Step 6: Git commit ──")
    try:
        def g(cmd):
            return subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        g(["git", "config", "user.name", "EdisonHaus Bot"])
        g(["git", "config", "user.email", "bot@edisonhaus.store"])
        g(["git", "add", "b3_product_pipeline.py", "data/description_writer_heartbeat.json"])
        g(["git", "stash"])
        g(["git", "pull", "--rebase", "origin", "main"])
        subprocess.run(["git", "stash", "pop"], capture_output=True, text=True)
        g(["git", "add", "b3_product_pipeline.py", "data/description_writer_heartbeat.json"])
        if subprocess.run(["git", "diff", "--staged", "--quiet"], capture_output=True).returncode != 0:
            g(["git", "commit", "-m", "feat: auto product descriptions [skip ci]"])
            g(["git", "push", "origin", "main"])
            log.info("  Committed & pushed")
        else:
            log.info("  No changes to commit")
    except Exception as e:
        log.warning(f"  Git fail: {e}")


if __name__ == "__main__":
    main()
