# Todo

## 2026-03-31 — Fix EdisonHaus Description Pipeline ✅
## 2026-03-31 — Create Product Health Check ✅
## 2026-04-01 — Fix CJ Rate Limit in Product Health ✅
## 2026-04-01 — Meta Product Catalog Setup ✅

## Completed Since Last Update
- [x] Verify Meta catalog upload completed (catalog ID: 1576514153432001)
- [x] Add Google Merchant Center return policy — GMC suspension lifted ~Apr 6, 233 products Limited status, Free Listings ACTIVE
- [x] Monitor Pinterest catalog sync — 232 products ingested, merchant Approved, EQS Good

## Open Items — Prioritized (April 22, 2026)

### P0 — Conversion Blockers (87 clicks → 0 orders)
- [ ] Enable Shop Pay + Apple Pay in Shopify Payments (manual — Nick does in admin)
- [ ] Install reviews app (Judge.me free tier) and seed initial reviews
- [ ] Add delivery-estimate block to product page template (8-18 day window)

### P1 — Silent Pipeline Failures
- [ ] Fix b3_product_health.py: 150/150 errors but exits 0. Must exit non-zero when heartbeat status != "success" or error_count > threshold
- [ ] Fix b3_product_pipeline.py: 255 fetched / 0 created / 30 create_fail errors. Shopify write path is broken — diagnose and fix
- [ ] Add pre-commit guard in amazon-associates-content-site that blocks any _posts/*.md commit if it contains amazon.com/s?k= links

### P2 — Channel Connections (each needs Nick's login/OAuth)
- [ ] Connect TikTok Shopify app (needs TikTok Business account login)
- [ ] Connect Microsoft Channel (needs Microsoft account login)
- [ ] Connect bank account in Shopify Payments
