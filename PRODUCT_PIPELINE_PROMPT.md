# CLAUDE CODE — EDISONHAUS PRODUCT PIPELINE
# Open this repo in Claude Code and say: "Run the product pipeline prompt"

====================================================================
TASK: Build b3_product_pipeline.py and its GitHub Actions workflow.
Run it immediately after building. No confirmations. No pauses.
====================================================================

ENVIRONMENT — read from os.environ, these are set as GitHub secrets:
  SHOPIFY_STORE        = fgtyz6-bj.myshopify.com   (hardcode this)
  SHOPIFY_ACCESS_TOKEN = os.environ["SHOPIFY_ACCESS_TOKEN"]
  CJ_EMAIL             = os.environ["CJ_EMAIL"]
  CJ_API_KEY           = os.environ["CJ_API_KEY"]
  ANTHROPIC_API_KEY    = os.environ["ANTHROPIC_API_KEY"]

  CJ API base:     https://developers.cjdropshipping.com/api2.0/v1
  Shopify API:     https://fgtyz6-bj.myshopify.com/admin/api/2024-01
  DB:              data/dropship.db

====================================================================
MANDATORY CODE STANDARDS
====================================================================

Every script must start with:
  import builtins
  def _no_input(*a, **k): raise RuntimeError("BLOCKED: interactive prompt")
  builtins.input = _no_input

Rules:
- No input() calls. No confirmation prompts. Run to completion.
- NEVER invent product data. All content comes from CJ API responses.
- Retry every external API call: exponential backoff, max 3 attempts.
- Cache CJ auth token for entire run. Never re-auth per product.
- CJ auth rate limit: max 1 call per 300 seconds.
- One bad product never stops the run. Catch per-product, log, continue.
- Log everything to ./reports/ with timestamps.
- Write JSON heartbeat after every phase.

====================================================================
PHASE 1 — CJ AUTH
====================================================================

POST /authentication/getAccessToken
Body: {"email": CJ_EMAIL, "password": CJ_API_KEY}
Extract: data.accessToken → CJ_TOKEN
Header for all CJ calls: CJ-Access-Token: {CJ_TOKEN}
Abort with clear error if auth fails.

====================================================================
PHASE 2 — DISCOVER CATEGORIES FROM CJ DATA
====================================================================

2a. Fetch CJ category tree:
    GET /product/getCategory
    Returns full hierarchy. Parse into flat list {id, name, parentId}.

2b. Call Anthropic API (claude-sonnet-4-20250514):
    Pass the full category list. Prompt:

    "You are a merchandising strategist for EdisonHaus, a Shopify
    store selling warm ambient home lighting and home decor. Analyse
    this CJ Dropshipping category list. Return every category that
    contains products for: all lighting types (LED, ambient, pendant,
    ceiling, table, desk, floor, fairy, string, strip, neon, solar,
    smart bulbs), home decor (wall art, canvas, tapestries, vases,
    candle holders, decorative items), cozy textiles (throw pillow
    covers, cushion covers), storage accents (baskets, rattan
    organisers). Return ONLY valid JSON array, no markdown:
    [{cj_category_id, cj_category_name, shopify_collection_name,
    shopify_collection_handle}]
    Handle must be lowercase-hyphenated-url-safe. Group related CJ
    sub-categories under one Shopify collection where logical."

    Parse JSON. Write to data/category_map.json.

2c. Create Shopify collections:
    For each entry: GET /custom_collections.json?handle={handle}
    If not found: POST /custom_collections.json
      {title, handle, published: true}
    Store returned shopify_collection_id in category_map.json.
    Rewrite data/category_map.json with IDs populated.

====================================================================
PHASE 3 — FETCH PRODUCTS FROM CJ (REAL DATA ONLY)
====================================================================

For each category in category_map.json, paginate:
  GET /product/list?categoryId={id}&pageNum={n}&pageSize=50
  Loop until results < 50.

For EVERY product pid, fetch full detail:
  GET /product/query?pid={pid}
  Sleep 0.5s between calls.

Extract from detail response — use verbatim, never rewrite:
  productNameEn       → title
  productDescription  → body_html (clean HTML only, no rewriting)
  categoryName        → product_type
  categoryId          → for collection lookup
  productImage        → primary image URL
  productImages[]     → additional image URLs
  productWeight       → weight (grams)
  productSkuEn        → SKU prefix
  materialEn          → material (for tags)
  productType         → type string (for tags)
  variants[]:
    vid               → CJ variant ID (CRITICAL for fulfillment)
    variantSellPrice  → cost price
    variantNameEn     → display name
    variantImage      → variant image URL

====================================================================
PHASE 4 — PRICE CALCULATION
====================================================================

cost = float(cheapest variantSellPrice)
Skip product if cost <= 0.

Markup tiers (first match applies):
  cost < 5.00    → sell = cost * 4.0
  cost < 15.00   → sell = cost * 3.0
  cost < 30.00   → sell = cost * 2.5
  cost < 60.00   → sell = cost * 2.2
  cost >= 60.00  → sell = cost * 2.0

Floor: $14.99 minimum.
Round up to nearest $x.99 (e.g. $23.47 → $23.99).
Skip if (sell - cost) / sell < 0.40 (margin < 40%).
NEVER set compare_at_price.

====================================================================
PHASE 5 — CREATE OR UPDATE SHOPIFY PRODUCTS
====================================================================

Check DB: SELECT shopify_id FROM products WHERE cj_id=? AND shopify_id IS NOT NULL
If found → UPDATE. If not → CREATE.

CREATE — POST /products.json:
{
  "product": {
    "title": productNameEn,           ← from CJ verbatim
    "body_html": productDescription,  ← from CJ, HTML-cleaned only
    "vendor": "EdisonHaus",
    "product_type": categoryName,     ← from CJ verbatim
    "tags": build_tags(product),
    "status": "active",
    "images": [{"src": url} for all CJ images],
    "variants": [{
      "price": str(sell_price),
      "sku": f"{productSkuEn}-{vid}",
      "option1": variantNameEn,       ← from CJ verbatim
      "weight": productWeight,
      "weight_unit": "g",
      "inventory_management": null,
      "fulfillment_service": "manual",
      "requires_shipping": true,
      "taxable": true
    } for each variant],
    "options": [{"name": "Option",
                 "values": [v.variantNameEn for v in variants]}]
  }
}

build_tags(product) — from CJ data only:
  Start with ["EdisonHaus"]
  Add: leaf categoryName (split on "/")
  Add: productType if not empty/NA
  Add: materialEn if not empty/NA
  Scan productNameEn, add any matching words found:
    lamp, light, led, pendant, ceiling, ambient, fairy, string,
    neon, strip, solar, smart, pillow, basket, vase, candle,
    wall, decor, cozy, warm, rattan, woven, canvas, tapestry
  Deduplicate, lowercase, comma-joined string.

After CREATE, write 4 metafields on the product:
  namespace=dropship, key=cj_product_id,  value=pid
  namespace=dropship, key=cj_variant_id,  value=vid (cheapest variant)
  namespace=dropship, key=cj_cost_price,  value=str(cost)
  namespace=dropship, key=supplier,       value="CJDropshipping"

UPDATE — PUT /products/{shopify_id}.json:
  Update: variants prices (recalculated from current CJ cost)
  Update: body_html if changed
  Update: tags (rebuilt)
  Do not touch metafields or fulfillment fields that already exist.
  Log old_price → new_price for any price change.

====================================================================
PHASE 6 — ASSIGN TO CORRECT COLLECTION
====================================================================

Look up category_map.json by product's categoryId.
If exact match: use that shopify_collection_id.
If no match: call Claude:
  "Product: {title}, CJ category: {cat}, type: {type}.
   Collections available: {list handles from category_map}.
   Return ONLY the single best-matching collection handle."
  Use returned handle to get collection ID.

POST /collects.json: {"collect": {"product_id":..., "collection_id":...}}
422 = already assigned, treat as success.

====================================================================
PHASE 7 — PERSIST TO DATABASE
====================================================================

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
);

INSERT OR REPLACE on every run.

====================================================================
PHASE 8 — HEARTBEAT + REPORT
====================================================================

Write data/product_pipeline_heartbeat.json:
{
  "module": "b3_product_pipeline",
  "last_run": "<ISO>",
  "phase": "complete",
  "categories_mapped": N,
  "collections_created": N,
  "products_fetched": N,
  "products_created": N,
  "products_updated": N,
  "products_skipped": N,
  "total_live": N,
  "status": "success"|"partial",
  "errors": []
}

Write reports/product_pipeline_YYYY-MM-DD.json:
  One row per product: cj_id, title, cost, sell_price, margin,
  collection, shopify_id, action, skip_reason.

====================================================================
VERIFICATION (run at end of main)
====================================================================

1. GET /products/count.json → log count.
2. Sample 5 shopify_ids from DB, check each has cj_variant_id metafield.
   Log PASS/FAIL per product.
3. GET /custom_collections.json → log each collection + product count.
4. Print summary table: Collection | Product Count.

====================================================================
GITHUB ACTIONS WORKFLOW: .github/workflows/b3_product_pipeline.yml
====================================================================

name: B3 Product Pipeline
on:
  schedule:
    - cron: '0 3 * * 1,4'
  workflow_dispatch:
jobs:
  run-pipeline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          token: ${{ secrets.GH_PAT }}
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install anthropic requests
      - name: Run pipeline
        env:
          SHOPIFY_ACCESS_TOKEN: ${{ secrets.SHOPIFY_ACCESS_TOKEN }}
          CJ_API_KEY:           ${{ secrets.CJ_API_KEY }}
          CJ_EMAIL:             ${{ secrets.CJ_EMAIL }}
          ANTHROPIC_API_KEY:    ${{ secrets.ANTHROPIC_API_KEY }}
        run: python b3_product_pipeline.py
      - name: Commit results
        run: |
          git config user.name "EdisonHaus Bot"
          git config user.email "bot@edisonhaus.store"
          mkdir -p data reports
          git add -f data/ reports/ 2>/dev/null || true
          git stash
          git pull --rebase origin main
          git stash pop || true
          git add -f data/ reports/ 2>/dev/null || true
          git diff --staged --quiet || \
            git commit -m "Product pipeline $(date +%Y-%m-%d) [skip ci]"
          git push origin main

====================================================================
ABSOLUTE RULES — NO EXCEPTIONS
====================================================================
NEVER invent product names, descriptions, specs, categories or prices.
NEVER set compare_at_price.
NEVER stop on a single product failure.
NEVER ask for confirmation.
ALL product content = CJ API response data only.
