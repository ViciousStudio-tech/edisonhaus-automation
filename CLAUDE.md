# EdisonHaus Automation — Project Bible
> Claude Code reads this automatically. Always start here. Never ask for context.

---


## STANDING RULE — ALL CODE TASKS

Every Claude Code session must be started with:
```
claude --dangerously-skip-permissions
```

Every prompt written for Claude Code must include this at the top:
```
Run with --dangerously-skip-permissions. Do not ask for any permissions. Do not stop for confirmation. Execute every step to completion without pausing.
```

This is non-negotiable. No exceptions.

## THE BUSINESS

**EdisonHaus** is a Shopify dropshipping store selling warm ambient home lighting and décor.
- **Store URL:** https://edisonhaus.com  |  Shopify: fgtyz6-bj.myshopify.com
- **Status:** Built, password-protected. To go live: Shopify admin → Settings → Subscription → add business address → Preferences → toggle password off.
- **Plan:** Basic ($1/month until June 11, 2026)
- **Owner email:** bevicious@vicious.studio  |  Support: home@edisonhaus.com

Part of a 3-business passive income empire:
- Business 1: **DwellPicks** — Amazon Associates (LIVE at dwellpicks.com)
- Business 2: **EdisonHaus** — Shopify dropshipping (THIS REPO)
- Business 3: **Merch on Demand** — Amazon Merch (pending approval)

---

## CREDENTIALS

All secrets are in GitHub Actions secrets. Reference them as env vars — never hardcode.

| Secret Name | Used For |
|---|---|
| `SHOPIFY_ACCESS_TOKEN` | Shopify Admin API |
| `CJ_API_KEY` | CJ Dropshipping auth |
| `CJ_EMAIL` | CJ Dropshipping auth |
| `ANTHROPIC_API_KEY` | Claude AI scoring |
| `GH_PAT` | GitHub repo write access |

Shopify store: `fgtyz6-bj.myshopify.com`
CJ account ID: `CJ5227028`
CJ email: `nicholas.jacksondesign@gmail.com`

---

## SHOPIFY THEME & COLLECTIONS

**Active theme:** Tinker (ID: `143942549578`)
**Theme:** Warm Ambient Home Lighting & Decor

### 6 Canonical Collections

| Collection | Handle | Shopify ID |
|---|---|---|
| LED & Ambient Lighting | led-ambient-lighting | 305043898442 |
| Table & Desk Lamps | table-desk-lamps | 305043832906 |
| Pendant & Ceiling Lights | pendant-ceiling-lights | 305043865674 |
| Wall Décor | wall-decor | 305043931210 |
| Cozy Textiles | cozy-textiles | 305043963978 |
| Storage & Accents | storage-accents | 305043996746 |

### Menus
- Main menu GID: `gid://shopify/Menu/227099672650`
- Footer menu GID: `gid://shopify/Menu/227099705418`

---

## AUTOMATION PIPELINE

### Scripts & Schedules

| Script | Schedule | Purpose |
|---|---|---|
| `b3_product_finder.py` | Mon & Thu 6am UTC | Finds CJ products, scores with Claude AI, saves real variant IDs to DB |
| `b3_store_manager.py` | Daily | Lists DB products to Shopify with CJ metafields for auto-fulfillment |
| `b3_order_fulfiller.py` | Hourly | Routes paid Shopify orders to CJ automatically |
| `b3_ai_optimizer.py` | Sunday 9am UTC | Rewrites weak product descriptions |
| `watchdog.py` | Every 30 min | Monitors all pipelines |

### Database
- **Path:** `data/dropship.db` (SQLite, committed to repo after each run)
- **Products table key columns:** `cj_id`, `cj_vid`, `title`, `niche`, `collection_id`, `cost_usd`, `sell_price`, `profit_margin`, `shopify_id`, `status`

### How Auto-Fulfillment Works (critical — do not break this)
1. `b3_product_finder.py` calls CJ `/product/query?pid=X` to get the real `vid` (variant ID)
2. `b3_store_manager.py` lists to Shopify AND writes 3 metafields per product:
   - `dropship.cj_product_id`
   - `dropship.cj_variant_id` ← **required for order routing**
   - `dropship.supplier`
3. `b3_order_fulfiller.py` reads `dropship.cj_variant_id` metafield → calls CJ `createOrderV2`

**If `dropship.cj_variant_id` metafield is missing, orders go to manual queue. Every product needs it.**

---

## PRICING RULES (enforce these always)

- Price = CJ cost × AI markup, minimum 2.5×, floor $14.99
- 40% gross margin minimum — skip products below this
- **`compare_at_price` must NEVER be set** — no fake sale badges
- **No free shipping claims** — CJ charges ~$4–8/order to US, not covered
- SKU format: `CJ-{cj_id}`

---

## CJ DROPSHIPPING API

- Base URL: `https://developers.cjdropshipping.com/api2.0/v1`
- Auth: POST `/authentication/getAccessToken` with `{"email": CJ_EMAIL, "password": CJ_API_KEY}`
  - **Rate limit: 1 call per 300 seconds** — cache token for full run, never re-auth per product
- Product search: GET `/product/list?productNameEn=KEYWORD&pageNum=1&pageSize=30`
- Product detail: GET `/product/query?pid=PRODUCT_ID` → get `variants[0].vid` and `variantSellPrice`
- Place order: POST `/shopping/order/createOrderV2` — use `vid` not `pid`

---

## CODING STANDARDS — NON-NEGOTIABLE

```python
import builtins
def _no_input(*a, **k): raise RuntimeError("BLOCKED: interactive prompt")
builtins.input = _no_input
```

- No `input()` calls — scripts run non-interactively in GitHub Actions
- No confirmation prompts — log and continue
- Logs to `./reports/`
- Wrap all external calls in try/except
- Write heartbeat JSON even on failure
- Add `time.sleep()` between CJ API calls to respect rate limits

---

## STOREFRONT

### Pages
| Title | Handle | ID |
|---|---|---|
| About EdisonHaus | about-edisonhaus | 115580305482 |
| Contact Us | contact | 115574800458 |
| Refund Policy | refund-policy | 115657310282 |
| Shipping Policy | shipping-policy | 115657408586 |
| Terms of Service | terms-of-service | 115657375818 |

### Homepage Sections (in order)
1. Hero slideshow — 6 slides with real product photos, links to each collection
2. Brand marquee — scrolling ticker text
3. Featured LED & Ambient Lighting grid
4. Shop by Category bento grid (all 6 collections)
5. Table & Desk Lamps grid
6. Wall Décor grid

### Nav
Main menu: Shop All → LED & Ambient Lighting → Table & Desk Lamps → Pendant & Ceiling Lights → Wall Décor → Cozy Textiles → Storage & Accents
(Home link was removed — do not re-add it)

---

## HEARTBEAT FILES

| File | Written by |
|---|---|
| `b3_product_heartbeat.json` | b3_product_finder.py |
| `b3_store_heartbeat.json` | b3_store_manager.py |
| `b3_fulfillment_heartbeat.json` | b3_order_fulfiller.py |
| `b3_optimizer_heartbeat.json` | b3_ai_optimizer.py |

---

## PENDING ITEMS

1. **Go live** — Add business address in Shopify admin → Settings → Subscription, then toggle password off in Preferences
2. **Policy /policies/ pages** — Paste content into Shopify admin → Settings → Policies (API token lacks `write_legal_policies` scope so must be done manually)
3. **PA-API** — Apply for Amazon Product Advertising API for DwellPicks
4. **Merch on Demand** — Build after Amazon approval

---

## CURRENT STATE (2026-03-16)

- Products on Shopify: being repopulated by pipeline run #19 (was wiped to fix broken CJ linkage)
- All previous 119 products deleted — they had no CJ variant IDs, could not auto-fulfill
- Pipeline now correctly stores `cj_vid` and writes metafields — first clean batch incoming
- Orders: 0 (store password-protected, no traffic yet)

---

*Update this file whenever significant changes are made.*
*Last updated: 2026-03-16*
