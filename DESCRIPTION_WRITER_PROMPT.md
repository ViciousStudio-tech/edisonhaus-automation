# CLAUDE CODE — EDISONHAUS PRODUCT DESCRIPTION WRITER
# Run with: claude --dangerously-skip-permissions
# Then type: read DESCRIPTION_WRITER_PROMPT.md and execute every instruction in it

Run with --dangerously-skip-permissions. Do not ask for any permissions.
Do not stop for confirmation. Execute every step to completion without pausing.

====================================================================
TASK
====================================================================

Write missing product descriptions for EdisonHaus Shopify products.
Use the Anthropic API to generate each description from the product
title and any existing content. Update ONLY the body_html field.
Change absolutely nothing else — not the title, price, images,
variants, tags, collections, metafields, or any other field.

Read CLAUDE.md first for full project context.

====================================================================
CREDENTIALS
====================================================================

SHOPIFY_STORE        = "fgtyz6-bj.myshopify.com"
SHOPIFY_BASE         = "https://fgtyz6-bj.myshopify.com/admin/api/2024-01"
SHOPIFY_ACCESS_TOKEN = os.environ["SHOPIFY_ACCESS_TOKEN"]
ANTHROPIC_API_KEY    = os.environ["ANTHROPIC_API_KEY"]

====================================================================
MANDATORY CODE RULES
====================================================================

At the top of the script:
  import builtins
  def _no_input(*a, **k): raise RuntimeError("BLOCKED")
  builtins.input = _no_input

- No input() calls. No confirmation prompts.
- Only update body_html. Touch nothing else on any product.
- One product failure never stops the run.
- Sleep 0.5s between Shopify API calls.
- Sleep 1s between Anthropic API calls.
- Log every product updated.
- Write a heartbeat JSON when done.

====================================================================
STEP 1 — FETCH ALL PRODUCTS MISSING DESCRIPTIONS
====================================================================

Paginate through ALL Shopify products:
GET {SHOPIFY_BASE}/products.json?limit=250&fields=id,title,body_html&page_info={cursor}
Use Link header for pagination until no more pages.

Collect products where body_html is empty or less than 50 characters.
Log total found.

====================================================================
STEP 2 — GENERATE DESCRIPTION WITH ANTHROPIC API
====================================================================

For each product missing a description, call the Anthropic API:

Model: claude-sonnet-4-20250514
Max tokens: 400

System prompt:
  "You are a product copywriter for EdisonHaus, a warm ambient home
  lighting and decor store. Write a compelling product description
  in plain HTML using only <p> and <ul><li> tags. Do not use
  headers, bold, or any other HTML tags. Keep it between 80-120
  words. Focus on ambiance, style, and home decor appeal. Do not
  make up specific specs or measurements. Do not mention brand names
  other than EdisonHaus."

User message:
  "Write a product description for: {product_title}"

Extract the text content from the response.
Strip any markdown fences if present.
Validate it contains at least one <p> tag.

====================================================================
STEP 3 — UPDATE ONLY body_html ON SHOPIFY
====================================================================

For each generated description:

PUT {SHOPIFY_BASE}/products/{product_id}.json
Body:
  {
    "product": {
      "id": product_id,
      "body_html": generated_description
    }
  }

This updates ONLY body_html. Nothing else is in the payload so
nothing else can change.

On 200 response: log "Updated: {title}"
On any error: log the error, skip, continue to next product.
Sleep 0.5s after each Shopify call.

====================================================================
STEP 4 — UPDATE b3_product_pipeline.py TO RUN THIS AUTOMATICALLY
====================================================================

Open b3_product_pipeline.py and add a new phase at the end of
main() called phase6_fill_descriptions(products):

  - Takes the list of newly created products from phase 5
  - For any product in that list where body_html is empty or < 50 chars
  - Calls the same Anthropic description generation logic above
  - Updates only body_html via PUT /products/{id}.json
  - Logs results

This means every new product pulled from CJ automatically gets a
description written if CJ did not provide one.

Do not change anything else in b3_product_pipeline.py except adding
this new phase and calling it from main() after phase 5.

====================================================================
STEP 5 — WRITE HEARTBEAT
====================================================================

Write data/description_writer_heartbeat.json:
{
  "module": "description_writer",
  "last_run": "<ISO timestamp>",
  "products_found_missing": N,
  "products_updated": N,
  "products_failed": N,
  "status": "success" or "partial"
}

====================================================================
STEP 6 — COMMIT TO GITHUB
====================================================================

Use subprocess git commands:
  git config user.name "EdisonHaus Bot"
  git config user.email "bot@edisonhaus.store"
  git add b3_product_pipeline.py data/description_writer_heartbeat.json
  git stash
  git pull --rebase origin main
  git stash pop || true
  git add b3_product_pipeline.py data/description_writer_heartbeat.json
  git diff --staged --quiet || git commit -m "feat: auto product descriptions [skip ci]"
  git push origin main

====================================================================
VERIFICATION
====================================================================

After the run, pick 3 random updated products and print:
  - Product title
  - First 100 chars of new body_html
  - Confirm body_html is valid HTML with <p> tags

====================================================================
ABSOLUTE RULES
====================================================================

ONLY update body_html.
NEVER change title, price, images, variants, tags, or metafields.
NEVER stop on a single product failure.
NEVER ask for confirmation.
