# Lessons

## 2026-03-31 — Img-only descriptions passed as "not missing"
MISTAKE: Description writer reported "0 missing" for 210 products that had only `<img>` tags in body_html
ROOT CAUSE: Checked `len(body_html) < 50` instead of stripping HTML tags first — img-only HTML is 163+ chars
RULE: Always strip HTML tags before checking if a description field has real text content. `len(html)` is never a valid proxy for "has meaningful description."

## 2026-04-01 — CJ API rate limit on bulk variant queries
MISTAKE: b3_product_health.py used 0.3s sleep between CJ calls and only 30s backoff on 429. All 176 products errored out.
ROOT CAUSE: CJ API rate limits aggressively on bulk variant queries — 0.3s is too fast and 30s backoff insufficient for recovery.
RULE: Use 1.5s sleep between CJ API calls and 60s backoff on 429 with 3 retries. Cap bulk CJ queries to 150 per run to avoid hitting daily limits.

## 2026-04-01 — Meta catalog requires catalog_management permission
MISTAKE: First two token attempts failed — ads_management and business_management alone are NOT enough to create catalogs.
ROOT CAUSE: The `owned_product_catalogs` POST endpoint requires the `catalog_management` scope specifically. Must be added in Graph API Explorer before generating the token.
RULE: Meta catalog creation requires `catalog_management` permission. EdisonHaus Catalog app ID: 981305987795652. Business ID: 335075399074526. Feed upload endpoint requires explicit `url` param. Token saved as META_ACCESS_TOKEN in GitHub Secrets.

## 2026-04-01 — Watchdog false-positive on GPC taxonomy strings
MISTAKE: Watchdog flagged non-numeric google_product_category as errors after we intentionally switched from numeric IDs to full taxonomy strings for Pinterest compliance.
ROOT CAUSE: Validation logic used `text.isdigit()` instead of just checking non-empty. Both numeric IDs and taxonomy strings are valid GPC values.
RULE: GPC field can be either numeric ID or full taxonomy string — both are valid. Only validate that the field is non-empty.
