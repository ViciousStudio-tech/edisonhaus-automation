"""
Business 3 — Dropship Product Finder (CJDropshipping)
EdisonHaus theme: warm home, ambient lighting, cozy décor, organised living.
Every product must fit the brand. Off-theme products are rejected by AI scoring.
Runs via GitHub Actions 2x/week.
"""

import os, json, time, sqlite3, logging, requests, random
from datetime import datetime
from pathlib import Path
import anthropic

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Env ────────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
CJ_API_KEY        = os.environ.get("CJ_API_KEY", "")
PRODUCTS_PER_RUN  = int(os.environ.get("PRODUCTS_PER_RUN", "50"))
DB_PATH           = os.environ.get("DB_PATH", "data/dropship.db")

Path("data").mkdir(exist_ok=True)
HEARTBEAT = Path("b3_product_heartbeat.json")
CJ_BASE   = "https://developers.cjdropshipping.com/api2.0/v1"

# ── EdisonHaus Theme Definition ────────────────────────────────────────────────
# Brand: warm, cosy, ambient, home-focused. Think Edison bulbs, soft lighting,
# organised kitchens, calm bedrooms, hygge living spaces.
#
# Each niche has:
#   - search_terms: what to send to CJ
#   - collection_id: the Shopify collection this niche lives in
#   - collection_handle: human-readable name for logging
#   - reject_keywords: words that instantly disqualify a product

NICHES = [
    {
        "name": "ambient lighting",
        "search_terms": ["fairy lights", "string lights warm white", "Edison bulb lamp", "bedside table lamp", "LED candle light"],
        "collection_id": 304927375434,
        "collection_handle": "Trending Now",
        "reject_keywords": ["car", "bicycle", "earring", "jewelry", "pet", "baby", "gym", "sport", "outdoor", "camping"],
    },
    {
        "name": "warm home decor",
        "search_terms": ["ceramic vase minimalist", "boho macrame wall hanging", "candle holder aesthetic", "rattan basket decor", "woven throw blanket"],
        "collection_id": 304927375434,
        "collection_handle": "Trending Now",
        "reject_keywords": ["car", "bicycle", "earring", "jewelry", "pet", "gym", "sport", "outdoor", "camping", "baby"],
    },
    {
        "name": "kitchen organisation",
        "search_terms": ["bamboo drawer organizer", "spice rack turntable", "over sink dish rack", "magnetic knife strip", "pantry storage container"],
        "collection_id": 304927309898,
        "collection_handle": "Home & Kitchen",
        "reject_keywords": ["car", "bicycle", "earring", "jewelry", "pet", "gym", "sport", "outdoor", "camping", "baby"],
    },
    {
        "name": "home storage solutions",
        "search_terms": ["clear stackable storage box", "fabric storage cube", "vacuum compression bag", "over door organizer", "closet divider organizer"],
        "collection_id": 304927309898,
        "collection_handle": "Home & Kitchen",
        "reject_keywords": ["car", "bicycle", "earring", "jewelry", "pet", "gym", "sport", "outdoor", "camping", "baby"],
    },
    {
        "name": "cosy bedroom decor",
        "search_terms": ["LED moon lamp night light", "neon sign room decor", "aesthetic wall art print", "Nordic poster set", "bedside organizer tray"],
        "collection_id": 304927375434,
        "collection_handle": "Trending Now",
        "reject_keywords": ["car", "bicycle", "earring", "jewelry", "pet", "gym", "outdoor", "camping", "baby", "kitchen"],
    },
    {
        "name": "wellness & self care",
        "search_terms": ["heating eye mask sleep", "aromatherapy diffuser home", "massage roller tool", "yoga block set", "meditation cushion"],
        "collection_id": 304927342666,
        "collection_handle": "Fitness & Wellness",
        "reject_keywords": ["car", "bicycle", "earring", "jewelry", "pet", "outdoor", "camping", "baby", "kitchen"],
    },
    {
        "name": "outdoor home living",
        "search_terms": ["solar garden light", "outdoor string lights patio", "portable camping lantern", "waterproof outdoor rug", "folding camping chair compact"],
        "collection_id": 304927440970,
        "collection_handle": "Outdoor & Travel",
        "reject_keywords": ["earring", "jewelry", "kitchen", "gym equipment", "baby", "indoor only"],
    },
    {
        "name": "baby & nursery",
        "search_terms": ["nursery night light", "baby room decor", "kids storage organizer", "toddler wall art", "baby mobile crib"],
        "collection_id": 304927408202,
        "collection_handle": "Baby & Kids",
        "reject_keywords": ["car", "bicycle", "earring", "jewelry", "gym", "camping", "kitchen", "adult"],
    },
]

FEATURED_COLLECTION_ID = 304920887370  # All products also go here

# ── CJDropshipping Auth ────────────────────────────────────────────────────────
def cj_get_token() -> str | None:
    if not CJ_API_KEY:
        log.warning("CJ_API_KEY not set — skipping live search")
        return None
    try:
        resp = requests.post(
            f"{CJ_BASE}/authentication/getAccessToken",
            json={"apiKey": CJ_API_KEY},
            timeout=15
        )
        data = resp.json()
        if data.get("result") is True:
            token = data["data"]["accessToken"]
            log.info("CJ auth: token obtained")
            return token
        log.error(f"CJ auth failed: {data.get('message')}")
        return None
    except Exception as e:
        log.error(f"CJ auth error: {e}")
        return None

# ── CJDropshipping Product Search ─────────────────────────────────────────────
def cj_search_products(token: str, keyword: str) -> list:
    if not token:
        return []
    try:
        resp = requests.get(
            f"{CJ_BASE}/product/list",
            headers={"CJ-Access-Token": token},
            params={"productName": keyword, "pageNum": 1, "pageSize": 30},
            timeout=15
        )
        data = resp.json()
        if data.get("result") is True:
            products = data.get("data", {}).get("list", [])
            log.info(f"  CJ returned {len(products)} products for '{keyword}'")
            return products
        log.warning(f"  CJ search failed for '{keyword}': {data.get('message')}")
        return []
    except Exception as e:
        log.error(f"CJ search error: {e}")
        return []

# ── Database ───────────────────────────────────────────────────────────────────
def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            cj_id          TEXT UNIQUE,
            title          TEXT,
            niche          TEXT,
            collection_id  INTEGER,
            cost_usd       REAL,
            sell_price     REAL,
            profit_margin  REAL,
            image_url      TEXT,
            product_url    TEXT,
            ai_description TEXT,
            ai_tags        TEXT,
            ai_score       INTEGER DEFAULT 0,
            shopify_id     TEXT,
            status         TEXT DEFAULT 'pending',
            created_at     TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn

# ── Claude AI scoring ──────────────────────────────────────────────────────────
BRAND_BRIEF = """
EdisonHaus is a Shopify store selling warm, aesthetic home products.
The brand vibe: Edison bulb warmth, cosy hygge living, organised minimalist spaces.
The customer: 25-40, rents or owns their first home, cares about how their space looks and feels.
Products must feel at home in a lifestyle Instagram post. Think soft textures, warm lighting, clean organisation.
"""

def ai_score_and_describe(client, product: dict, niche: dict) -> dict:
    title    = product.get("productNameEn") or product.get("productName") or "Unknown"
    raw      = product.get("sellPrice") or (product.get("variants") or [{}])[0].get("variantSellPrice", 10)
    cost     = float(str(raw).split("--")[0].strip() if "--" in str(raw) else raw)
    category = product.get("categoryName", niche["name"])
    rejects  = ", ".join(niche["reject_keywords"])

    prompt = f"""You are a product buyer for EdisonHaus, a home lifestyle store.

{BRAND_BRIEF}

Evaluate this product:
Title: {title}
Niche: {niche["name"]}
Category: {category}
Cost price: ${cost:.2f}

Automatic REJECT if the title or category suggests: {rejects}

Return ONLY valid JSON — no markdown, no explanation:
{{
  "score": <1-10, where 10 = perfect fit for EdisonHaus>,
  "sell_price": <recommended USD retail price, 2.5x cost minimum $14.99>,
  "description": <90-word Shopify product description, warm and benefit-focused, written for EdisonHaus>,
  "tags": <6 comma-separated SEO tags relevant to home/lifestyle>,
  "skip": <true if this product does NOT fit EdisonHaus brand, false if it fits>,
  "skip_reason": <one sentence reason if skip is true, else empty string>
}}"""

    for attempt in range(3):
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}]
            )
            raw_text = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
            return json.loads(raw_text)
        except Exception as e:
            log.warning(f"Claude attempt {attempt+1} failed: {e}")
            time.sleep(2 ** attempt)
    return {"score": 0, "sell_price": cost * 2.5, "description": "", "tags": "", "skip": True, "skip_reason": "AI error"}

# ── Save to DB ─────────────────────────────────────────────────────────────────
def save_product(conn, product: dict, ai: dict, niche: dict) -> bool:
    raw   = product.get("sellPrice") or (product.get("variants") or [{}])[0].get("variantSellPrice", 10)
    cost  = float(str(raw).split("--")[0].strip() if "--" in str(raw) else raw)
    sell  = float(ai.get("sell_price", cost * 2.5))
    margin = round((sell - cost) / max(sell, 0.01) * 100, 1)
    image  = product.get("productImage") or product.get("imageUrl") or ""
    cj_id  = str(product.get("pid") or product.get("productId") or "")

    try:
        conn.execute("""
            INSERT OR IGNORE INTO products
            (cj_id, title, niche, collection_id, cost_usd, sell_price, profit_margin,
             image_url, product_url, ai_description, ai_tags, ai_score)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            cj_id,
            (product.get("productNameEn") or "")[:200],
            niche["name"],
            niche["collection_id"],
            round(cost, 2),
            round(sell, 2),
            margin,
            image,
            f"https://app.cjdropshipping.com/product-detail.html?id={cj_id}",
            ai.get("description", ""),
            ai.get("tags", ""),
            int(ai.get("score", 0))
        ))
        conn.commit()
        return True
    except Exception as e:
        log.error(f"DB save error: {e}")
        return False

# ── Heartbeat ──────────────────────────────────────────────────────────────────
def write_heartbeat(products_found: int, status: str = "success"):
    HEARTBEAT.write_text(json.dumps({
        "module": "b3_product_finder",
        "last_run": datetime.now().isoformat(),
        "products_found": products_found,
        "status": status
    }, indent=2))

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("B3 Product Finder — EdisonHaus Theme Engine")
    log.info("=" * 60)

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        conn   = init_db()
        token  = cj_get_token()

        total_saved = 0
        skipped     = 0

        # Shuffle niches so each run hits different ones first
        niches_this_run = NICHES[:]
        random.shuffle(niches_this_run)

        for niche in niches_this_run:
            if total_saved >= PRODUCTS_PER_RUN:
                break

            log.info(f"\n── Niche: {niche['name']} → {niche['collection_handle']} ──")

            # Rotate through search terms for this niche
            search_terms = niche["search_terms"][:]
            random.shuffle(search_terms)

            for term in search_terms:
                if total_saved >= PRODUCTS_PER_RUN:
                    break

                products = cj_search_products(token, term)

                for product in products:
                    if total_saved >= PRODUCTS_PER_RUN:
                        break

                    # Skip out-of-stock
                    if product.get("isStock") == "NO":
                        continue

                    # Skip placeholder images
                    img = product.get("productImage") or ""
                    if "4a4a4a" in img or "placehold" in img or not img:
                        continue

                    # Quick title filter — reject obviously off-theme before spending AI tokens
                    title_lower = (product.get("productNameEn") or "").lower()
                    if any(kw in title_lower for kw in niche["reject_keywords"]):
                        log.info(f"  ✗ Pre-filter reject: {title_lower[:60]}")
                        skipped += 1
                        continue

                    # AI scoring
                    ai = ai_score_and_describe(client, product, niche)

                    if ai.get("skip"):
                        log.info(f"  ✗ AI reject: {title_lower[:55]} — {ai.get('skip_reason','')}")
                        skipped += 1
                        continue

                    score = int(ai.get("score", 0))
                    if score < 7:
                        log.info(f"  ✗ Low score ({score}/10): {title_lower[:55]}")
                        skipped += 1
                        continue

                    if save_product(conn, product, ai, niche):
                        total_saved += 1
                        log.info(f"  ✓ [{score}/10] {(product.get('productNameEn') or '')[:55]} | ${ai.get('sell_price', 0):.2f} → {niche['collection_handle']}")

                    time.sleep(0.5)

                time.sleep(1.5)

        pending = conn.execute("SELECT COUNT(*) FROM products WHERE status='pending'").fetchone()[0]
        log.info(f"\nDone. Saved {total_saved} new | Rejected {skipped} off-theme | Total pending: {pending}")
        write_heartbeat(total_saved)
        conn.close()

    except Exception as e:
        log.error(f"Product finder failed: {e}")
        write_heartbeat(0, status=f"error: {e}")
        raise

if __name__ == "__main__":
    main()
