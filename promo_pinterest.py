#!/usr/bin/env python3
"""EdisonHaus — Pinterest auto-poster. Posts product pins daily."""

import builtins
def _no_input(*a, **k): raise RuntimeError("BLOCKED")
builtins.input = _no_input

import os, sys, json, time, logging, subprocess, traceback, re
from datetime import datetime, timezone
from pathlib import Path
import requests

# ── Config ────────────────────────────────────────────────────────────────
SHOPIFY_BASE = "https://fgtyz6-bj.myshopify.com/admin/api/2024-01"
SHOPIFY_TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
PINTEREST_TOKEN = os.environ.get("PINTEREST_ACCESS_TOKEN", "")
PINTEREST_API = "https://api.pinterest.com/v5"
SITE_URL = "https://edisonhaus.com"
POSTED_PATH = Path("data/pinterest_posted.json")
HB_PATH = Path("data/pinterest_heartbeat.json")
PINS_PER_RUN = 10

Path("data").mkdir(parents=True, exist_ok=True)
Path("reports").mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(Path("reports") / f"pinterest_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.log"),
              logging.StreamHandler(sys.stdout)])
log = logging.getLogger("pinterest")

def shop_h():
    return {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}

def pin_h():
    return {"Authorization": f"Bearer {PINTEREST_TOKEN}", "Content-Type": "application/json"}

def _req(method, url, retries=3, **kw):
    for i in range(retries):
        try:
            r = requests.request(method, url, timeout=30, **kw)
            if r.status_code == 429:
                w = 10 * (i + 1)
                log.warning(f"429 on {url}, sleep {w}s")
                time.sleep(w); continue
            return r
        except requests.RequestException as e:
            if i == retries - 1: raise
            time.sleep(5 * (i + 1))
    return None

# ── Board mapping ─────────────────────────────────────────────────────────
BOARD_NAMES = [
    "Cozy Ambient Lighting Ideas",
    "LED & Strip Light Inspiration",
    "Table & Desk Lamp Decor",
    "Pendant & Ceiling Light Ideas",
    "Wall Decor & Art",
    "Cozy Home Accents",
]

def match_board(product_type, tags):
    pt = (product_type or "").lower()
    tg = (tags or "").lower()
    combined = pt + " " + tg
    if any(w in combined for w in ["led", "strip", "fairy", "string", "neon"]):
        return "LED & Strip Light Inspiration"
    if any(w in combined for w in ["table", "desk", "bedside", "reading"]):
        return "Table & Desk Lamp Decor"
    if any(w in combined for w in ["pendant", "ceiling", "chandelier", "hanging"]):
        return "Pendant & Ceiling Light Ideas"
    if any(w in combined for w in ["wall", "tapestry", "canvas", "painting", "decor"]):
        return "Wall Decor & Art"
    if any(w in combined for w in ["textile", "pillow", "cushion", "storage", "basket", "vase", "candle", "rattan"]):
        return "Cozy Home Accents"
    return "Cozy Ambient Lighting Ideas"

# ── Step 1: Fetch products ────────────────────────────────────────────────
def fetch_products():
    log.info("── Step 1: Fetch Shopify products ──")
    products = []
    url = f"{SHOPIFY_BASE}/products.json?limit=250&status=active&fields=id,title,handle,body_html,images,product_type,tags"
    while url:
        r = _req("GET", url, headers=shop_h())
        if not r or r.status_code != 200: break
        for p in r.json().get("products", []):
            if p.get("images"):
                products.append(p)
        link = r.headers.get("Link", "")
        url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
    log.info(f"Fetched {len(products)} products with images")
    return products

# ── Step 2: Load posted tracker ───────────────────────────────────────────
def load_posted():
    if POSTED_PATH.exists():
        return json.loads(POSTED_PATH.read_text())
    return {}

def save_posted(posted):
    POSTED_PATH.write_text(json.dumps(posted, indent=2))

# ── Step 3: Get or create boards ──────────────────────────────────────────
def get_boards():
    log.info("── Step 3: Get Pinterest boards ──")
    boards = {}
    r = _req("GET", f"{PINTEREST_API}/boards?page_size=25", headers=pin_h())
    if r and r.status_code == 200:
        for b in r.json().get("items", []):
            boards[b["name"]] = b["id"]
            log.info(f"  Board: {b['name']} ({b['id']})")
    # Create missing boards
    for name in BOARD_NAMES:
        if name not in boards:
            log.info(f"  Creating board: {name}")
            r = _req("POST", f"{PINTEREST_API}/boards", headers=pin_h(),
                      json={"name": name, "privacy": "PUBLIC"})
            if r and r.status_code == 201:
                boards[name] = r.json()["id"]
                log.info(f"    Created: {r.json()['id']}")
            else:
                log.warning(f"    Failed to create board: {r.status_code if r else 'none'}")
            time.sleep(1)
    return boards

# ── Step 5: Generate description + post pins ──────────────────────────────
def generate_pin_desc(title):
    r = requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={
            "model": "claude-sonnet-4-20250514", "max_tokens": 150,
            "system": ("You are a Pinterest SEO expert for EdisonHaus, a warm ambient home lighting "
                "and decor store. Write a Pinterest pin description optimised for search. Include "
                "3-5 relevant keywords naturally. End with a soft call to action. Max 150 characters. "
                "Warm, cozy, aspirational tone. No hashtags."),
            "messages": [{"role": "user", "content": f"Write a Pinterest pin description for this product: {title}"}],
        }, timeout=30)
    if r.status_code != 200:
        raise Exception(f"Anthropic {r.status_code}: {r.text[:200]}")
    text = r.json()["content"][0]["text"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"): text = text[:-3]
        text = text.strip()
    return text

def post_pins(products, boards, posted):
    log.info("── Step 5: Post pins ──")
    pins_posted = 0
    errors = []

    for p in products:
        if pins_posted >= PINS_PER_RUN:
            break
        pid = str(p["id"])
        board_name = match_board(p.get("product_type", ""), p.get("tags", ""))
        board_id = boards.get(board_name)
        if not board_id:
            continue

        # Check if already posted
        if pid in posted.get(board_id, []):
            continue

        try:
            desc = generate_pin_desc(p["title"])
            time.sleep(1)

            handle = p.get("handle", "")
            img_url = p["images"][0]["src"]
            pin_data = {
                "board_id": board_id,
                "title": p["title"][:100],
                "description": desc,
                "media_source": {"source_type": "image_url", "url": img_url},
                "link": f"{SITE_URL}/products/{handle}",
            }
            r = _req("POST", f"{PINTEREST_API}/pins", headers=pin_h(), json=pin_data)
            if r and r.status_code == 201:
                posted.setdefault(board_id, []).append(pid)
                pins_posted += 1
                log.info(f"  [{pins_posted}] Pinned: {p['title'][:50]} → {board_name}")
            else:
                msg = r.text[:200] if r else "no response"
                log.warning(f"  Pin failed for {pid}: {r.status_code if r else 'none'} {msg}")
                errors.append(f"pin_fail:{pid}")

            time.sleep(2)
        except Exception as e:
            log.error(f"  Error pinning {pid}: {e}")
            errors.append(f"pin_error:{pid}:{str(e)[:60]}")

    return pins_posted, errors

# ── Main ──────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("EdisonHaus Pinterest Auto-Poster")
    log.info("=" * 60)

    if not SHOPIFY_TOKEN:
        log.error("Missing SHOPIFY_ACCESS_TOKEN"); sys.exit(1)
    if not PINTEREST_TOKEN:
        log.error("Missing PINTEREST_ACCESS_TOKEN — add it to GitHub secrets"); sys.exit(1)
    if not ANTHROPIC_KEY:
        log.error("Missing ANTHROPIC_API_KEY"); sys.exit(1)

    products = fetch_products()
    if not products:
        log.warning("No products found"); return

    posted = load_posted()
    boards = get_boards()
    pins_posted, errors = post_pins(products, boards, posted)
    save_posted(posted)

    # Heartbeat
    total = sum(len(v) for v in posted.values())
    board_counts = {}
    for name, bid in boards.items():
        board_counts[name] = len(posted.get(bid, []))
    HB_PATH.write_text(json.dumps({
        "module": "promo_pinterest",
        "last_run": datetime.now(timezone.utc).isoformat(),
        "pins_posted_today": pins_posted,
        "total_pins_posted": total,
        "boards": board_counts,
        "status": "success" if not errors else "partial",
        "errors": errors[:20],
    }, indent=2))
    log.info(f"Done: {pins_posted} pins posted today, {total} total")

    # Git commit
    try:
        def g(cmd): return subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        g(["git", "config", "user.name", "EdisonHaus Bot"])
        g(["git", "config", "user.email", "bot@edisonhaus.store"])
        g(["git", "add", "-f", "data/pinterest_posted.json", "data/pinterest_heartbeat.json"])
        g(["git", "stash"])
        g(["git", "pull", "--rebase", "origin", "main"])
        subprocess.run(["git", "stash", "pop"], capture_output=True, text=True)
        g(["git", "add", "-f", "data/pinterest_posted.json", "data/pinterest_heartbeat.json"])
        if subprocess.run(["git", "diff", "--staged", "--quiet"], capture_output=True).returncode != 0:
            g(["git", "commit", "-m", f"Pinterest: daily pins {datetime.now().strftime('%Y-%m-%d')} [skip ci]"])
            g(["git", "push", "origin", "main"])
            log.info("  Committed & pushed")
    except Exception as e:
        log.warning(f"  Git fail: {e}")

if __name__ == "__main__":
    main()
