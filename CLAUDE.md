# Passive Income Empire — Claude Code Project Bible
> Applies to: edisonhaus-automation AND amazon-associates-content-site
> Claude Code reads this automatically every session. Always start here.
> This file auto-updates — every session change gets committed here.

---

## The Empire — At A Glance

**Owner:** Nick Jackson | Vicious Studio NYC | bevicious@vicious.studio
**Working machine:** Mac Mini at /Volumes/Vicious
**GitHub org:** ViciousStudio-tech

| Business | URL | Status | Repo |
|---|---|---|---|
| EdisonHaus | edisonhaus.com | ✅ Live | edisonhaus-automation |
| DwellPicks | dwellpicks.com | ✅ Live (articles rebuilding) | amazon-associates-content-site |
| Merch on Demand | merch.amazon.com | ⏳ Awaiting approval | — |

---

## Non-Negotiable Execution Rules

```
Run with --dangerously-skip-permissions. No confirmations. No stopping.
No asking questions. Execute everything to completion.
```

- **Never ask for permission** at any step
- **Never run scripts locally** if they require secrets — GitHub Actions only
- **Never commit from local** for automated tasks — the workflow handles that
- **Never report success** without showing verified data from the live system
- **Never use .env files** — all secrets are GitHub Actions secrets
- **Never commit if verification fails** — only push when 100% confirmed correct
- **Commit incrementally** — every 10 items or meaningful checkpoint, never all at once at the end
- **Always state execution location explicitly** in every prompt: "Run this via GitHub Actions" or "Run locally"

---

## Where Tasks Run

| Task | Runs In |
|---|---|
| Article rebuilds, batch jobs | GitHub Actions only |
| Feed generation (Google, Meta, Pinterest) | GitHub Actions only |
| Any script using API keys/tokens | GitHub Actions only |
| Git commits and pushes | GitHub Actions only (via GH_PAT) |
| Code edits, CLAUDE.md updates | Local, then `git push` |
| Quick read-only verification | Local OK |

**Git config for Actions commits:**
```bash
git config user.name "EdisonHaus Bot"  # or "DwellPicks Bot"
git config user.email "bot@edisonhaus.com"
git push https://x-access-token:${{ secrets.GH_PAT }}@github.com/ViciousStudio-tech/[repo].git HEAD:main
```

---

## Credentials & Keys

All secrets live in GitHub Actions. Reference as `${{ secrets.NAME }}` in workflows.
**Never hardcode. Never write to .env files.**

### EdisonHaus Repo — GitHub Secrets
| Secret | What It Is |
|---|---|
| `SHOPIFY_ACCESS_TOKEN` | Shopify Admin API token |
| `ANTHROPIC_API_KEY` | Claude AI for scoring and descriptions |
| `CJ_API_KEY` | CJ Dropshipping API key — rate limited 1 call/300s |
| `GH_PAT` | GitHub write access for bot commits |

**Shopify store:** fgtyz6-bj.myshopify.com | **Theme ID:** 143942549578
**CJ account:** CJ5227028 | nicholas.jacksondesign@gmail.com

### DwellPicks Repo — GitHub Secrets
| Secret | What It Is |
|---|---|
| `ANTHROPIC_API_KEY` | Claude AI for article generation |
| `AMAZON_ASSOCIATE_TAG` | viciousstudio-20 |
| `SERPAPI_KEY` | Amazon product search (250/mo free — resets April 30) |
| `GH_PAT` | GitHub write access for bot commits |
| `GMAIL_APP_PASSWORD` | Daily digest sender |
| `GMAIL_TO` | nicholas.jacksondesign@gmail.com |

**Associate tag:** viciousstudio-20 | **Site:** dwellpicks.com

---

## EdisonHaus — Current State

### Store
- ✅ Live at edisonhaus.com (password removed March 2026)
- ✅ 233 active products | 7 collections
- ✅ Shopify Basic plan ($1/month until June 11, 2026)
- ✅ Payment gateway: Bank of America via Shopify Payments
- ✅ Support email: home@edisonhaus.com
- ❌ 0 orders — no paid traffic yet

### Collections
| Handle | Shopify ID |
|---|---|
| LED & Ambient Lighting | 305132175434 |
| Table & Desk Lamps | 305132109898 |
| Pendant & Ceiling Lights | 305132142666 |
| Wall Decor | 305132208202 |
| Cozy Textiles | 305132240970 |
| Storage & Accents | 305132273738 |
| New Arrivals | 305132306506 |

### Automation Pipelines
| Workflow | Schedule | Status |
|---|---|---|
| B3 Product Finder | Mon/Thu 7am UTC | ✅ |
| B3 Product Pipeline | Mon/Thu 9am UTC | ✅ |
| B3 Description Writer | Tue/Fri 5am EST | ✅ |
| B3 Store Manager | Daily | ✅ |
| B3 Order Fulfiller | Hourly | ✅ |
| B3 AI Optimizer | Sundays | ✅ |
| B3 Daily Digest | Daily 9am EST | ✅ |
| Promo Feed Generator | Daily 5am UTC | ✅ |
| Watchdog Monitor | Every 30 min | ✅ |

### Product Feeds (live — auto-updated daily)
- Google XML: https://viciousstudio-tech.github.io/edisonhaus-automation/feeds/google_feed.xml
- Meta CSV: https://viciousstudio-tech.github.io/edisonhaus-automation/feeds/meta_feed.csv

### Pinterest Feed Status
- ✅ ns0: namespace bug fixed (March 30, 2026)
- ✅ All 232 items have descriptions and numeric GPC IDs
- April 4 deadline — resolved

### Things That Will Bite You
- CJ auth endpoint rate limited: 1 call per 300 seconds — always cache the token
- Shopify API pagination: use Link header for next page cursor, don't assume all products in one call
- Pinterest feed requires `ET.register_namespace('g', 'http://base.google.com/ns/1.0')` at module level — without it ns0: breaks the feed silently
- Google/Meta feeds use the same promo_feed_generator.py — changes affect both
- Store manager script uses SQLite at data/dropship.db — never drop/recreate schema mid-run

---

## DwellPicks — Current State

### Site
- ✅ Live at dwellpicks.com (Jekyll on GitHub Pages)
- ✅ 102 articles in _posts/
- ✅ Amazon Associates W-9 filed (March 31, 2026)
- ❌ 0/102 articles rebuilt with real ASINs — rebuild in progress
- ⚠️ SerpApi free tier exhausted (250/250 used) — resets April 30, 2026
- Current rebuild: using Anthropic API to generate product data from existing search queries

### Article Standards — Absolute Rules
- Every product link: `amazon.com/dp/[ASIN]?tag=viciousstudio-20` — no exceptions
- **Zero** `amazon.com/s?k=` search URLs allowed anywhere in any article
- **Zero** loremflickr, unsplash, or placeholder images — real Amazon product images only
- **Zero** hallucinated product names — every product must come from real scraped/API data

### 6 Verification Checks — Must ALL pass before any commit
1. Every ASIN in article exists in source product data
2. Every source ASIN appears in article
3. Zero `amazon.com/s?k=` URLs
4. Zero placeholder images (loremflickr/unsplash/placeholder)
5. All product URLs contain `tag=viciousstudio-20`
6. No duplicate ASINs in same article

### Automation Pipelines
| Workflow | Schedule | Status |
|---|---|---|
| Weekly Content Pipeline | Sundays 8am UTC | ✅ |
| Monitor — Every 6 Hours | Every 6 hours | ✅ |
| Rebuild V2 (article rebuild) | Manual trigger | 🔄 In progress |

---

## Daily Digest — Required Health Checks

Every daily digest email MUST include all of these. If any is missing, add it:
- ✅/❌ edisonhaus.com accessible (HTTP 200, no password page)
- ✅/❌ Pinterest feed: item count, zero ns0: occurrences, zero empty descriptions
- ✅/❌ Google/Meta feeds: item count, last updated timestamp
- ✅/❌ All GitHub Actions workflows: last run time + pass/fail for each
- ✅/❌ Shopify: product count, orders (last 24h), revenue (last 24h)
- ✅/❌ DwellPicks: article count, last pipeline run
- 🚨 Any workflow not run within 2× its expected interval = immediate alert

---

## Coding Standards

```python
import builtins
def _no_input(*args, **kwargs):
    raise RuntimeError("BLOCKED: interactive prompt attempted.")
builtins.input = _no_input
```

- No `input()` calls — all scripts run non-interactively
- No confirmation prompts — log everything, never ask
- Log to `./reports/` — timestamped output files
- Wrap all external API calls in try/except with logging
- CJ auth: 1 call per 300s — cache the token, never re-auth per product

### Anchor Comments
Use these throughout the codebase as Claude Code waypoints:
```python
# AIDEV-NOTE: [what this does and why it exists]
# AIDEV-TODO: [what needs to change]
# AIDEV-WARNING: [what will break if you change this wrong]
```

---

## Pricing Formula (EdisonHaus)
- cost < $5 → ×2.5 | cost < $15 → ×2.2 | cost < $30 → ×2.0 | cost ≥ $30 → ×1.8
- Floor: $14.99 | Round to $x.99 | Skip if margin < 35%
- No compare_at_price (no fake sale badges) | No free shipping claims

---

## Self-Improvement — Lessons Learned

After any correction from Nick, immediately update this section AND tasks/lessons.md.

- **Never run scripts locally when secrets are in GitHub Actions** → scripts fail silently, burn free API credits, save nothing
- **Never report a job done by checking GitHub before the job has had time to finish** → check actual commit timestamps vs workflow start time
- **Commit every 10 items, not all at once at the end** → if job fails midway, all work is lost
- **Background jobs (`&`) don't work for Claude Code** → log goes to a file Claude.ai can't read; use GitHub Actions or run interactively
- **Pinterest ns0: bug** → requires `ET.register_namespace('g', 'http://base.google.com/ns/1.0')` at module level before any element creation
- **Never use Unsplash source URLs** → API is dead (503). Never loremflickr. Only real product images.
- **Amazon direct scraping always gets blocked** → CAPTCHA on virtually every request. Use SerpApi or official API only.
- **Always verify links before providing them** → never give URLs that haven't been confirmed to work
- **DwellPicks article writer was generating fake products** → was instructed to use `amazon.com/s?k=` search URLs from the start — fundamental design flaw
- **SerpApi free tier is only 250 searches/month** → burning searches on local test runs (not GitHub Actions) wastes the entire monthly budget with nothing saved
