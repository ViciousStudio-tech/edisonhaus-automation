# Passive Income Empire — Claude Code Project Bible
> Applies to: edisonhaus-automation + amazon-associates-content-site
> Claude Code reads this automatically. Always start here. Never ask for context.
> Last updated: auto-updates on every session change via tasks/todo.md

---

## The Empire

**Owner:** Nick Jackson | bevicious@vicious.studio | Vicious Studio, NYC
**Mac Mini:** Primary machine at /Volumes/Vicious

Three-business passive income empire:

| Business | Status | Repo |
|---|---|---|
| **EdisonHaus** | ✅ LIVE at edisonhaus.com | ViciousStudio-tech/edisonhaus-automation |
| **DwellPicks** | ✅ LIVE at dwellpicks.com (articles need rebuild) | ViciousStudio-tech/amazon-associates-content-site |
| **Merch on Demand** | ⏳ Awaiting Amazon approval | No repo yet |

---

## EXECUTION RULES — NON-NEGOTIABLE

```
Run with --dangerously-skip-permissions. No confirmations. No stopping. Execute everything to completion.
```

- **Never run scripts locally if they need API secrets** — always via GitHub Actions
- **Never ask for permission** at any step
- **Never stop mid-task** — log failures and continue
- **Never report success without verified proof** — check GitHub commits, live APIs, actual data
- **Never use .env files** — all secrets are in GitHub Actions secrets
- **Never commit if verification fails** — only push when 100% confirmed correct
- **Commit incrementally** — every 10 articles or meaningful checkpoint, not all at once at the end

---

## CREDENTIALS

All secrets live in GitHub Actions. Reference as env vars only. Never hardcode.

### EdisonHaus Repo Secrets
| Secret | Purpose |
|---|---|
| `SHOPIFY_ACCESS_TOKEN` | [see GitHub Secrets: SHOPIFY_ACCESS_TOKEN] |
| `ANTHROPIC_API_KEY` | Claude AI for descriptions/scoring |
| `CJ_API_KEY` | CJ Dropshipping auth |
| `GH_PAT` | [see GitHub Secrets: GH_PAT] |

**Shopify store:** fgtyz6-bj.myshopify.com
**CJ Account:** CJ5227028 | nicholas.jacksondesign@gmail.com
**Theme ID:** 143942549578

### DwellPicks Repo Secrets
| Secret | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude AI for article generation |
| `AMAZON_ASSOCIATE_TAG` | viciousstudio-20 |
| `SERPAPI_KEY` | [see GitHub Secrets: SERPAPI_KEY] (250/mo free, resets April 30) |
| `GH_PAT` | GitHub write access |
| `GMAIL_APP_PASSWORD` | Daily digest email |
| `GMAIL_SENDER` | Sender address |
| `GMAIL_TO` | nicholas.jacksondesign@gmail.com |

**Amazon Associate tag:** viciousstudio-20
**DwellPicks site:** dwellpicks.com

---

## EDISONHAUS — CURRENT STATE

### Store
- Live at edisonhaus.com (password removed March 2026)
- 233 active products across 7 collections
- Shopify Basic plan ($1/month until June 11, 2026)
- Payment gateway: Bank of America connected via Shopify Payments
- Support email: home@edisonhaus.com

### Collections
| Collection | Shopify ID |
|---|---|
| LED & Ambient Lighting | 305132175434 |
| Table & Desk Lamps | 305132109898 |
| Pendant & Ceiling Lights | 305132142666 |
| Wall Decor | 305132208202 |
| Cozy Textiles | 305132240970 |
| Storage & Accents | 305132273738 |
| New Arrivals | 305132306506 |

### Automation Pipelines (all run via GitHub Actions)
| Workflow | Schedule | Last Status |
|---|---|---|
| B3 Product Finder | Mon & Thu 6am UTC | ✅ Running |
| B3 Product Pipeline | Mon & Thu 9am UTC | ✅ Running |
| B3 Description Writer | Tue & Fri 5am EST | ✅ Running |
| B3 Store Manager | Daily | ✅ Running |
| B3 Order Fulfiller | Hourly | ✅ Running |
| B3 AI Optimizer | Sundays | ✅ Running |
| B3 Daily Digest | Daily | ✅ Running |
| Promo Feed Generator | Daily 5am UTC | ✅ Running |
| Watchdog Monitor | Every 30 min | ✅ Running |

### Product Feeds (auto-updated daily via Promo Feed Generator)
- Google XML: https://viciousstudio-tech.github.io/edisonhaus-automation/feeds/google_feed.xml
- Meta CSV: https://viciousstudio-tech.github.io/edisonhaus-automation/feeds/meta_feed.csv
- Pinterest XML: https://viciousstudio-tech.github.io/edisonhaus-automation/feeds/pinterest_feed.xml

### Pinterest Feed Status
- ✅ ns0: namespace bug FIXED (March 30, 2026)
- ✅ All 232 items have descriptions
- ✅ All items have numeric GPC category IDs
- April 4, 2026 deadline — resolved

### Menus
- Main menu GID: gid://shopify/Menu/227099672650
- Footer menu GID: gid://shopify/Menu/227099705418

### Pricing Formula
- cost < $5 → ×2.5 | cost < $15 → ×2.2 | cost < $30 → ×2.0 | cost ≥ $30 → ×1.8
- Floor: $14.99 | Round to $x.99 | Skip if margin < 35%

### CJ Dropshipping API
- Auth: POST https://developers.cjdropshipping.com/api2.0/v1/authentication/getAccessToken
- Body: {"apiKey": "[CJ_API_KEY]"}
- Rate limit: 1 auth call per 300 seconds — always cache the token

---

## DWELLPICKS — CURRENT STATE

### Site
- Live at dwellpicks.com (Jekyll on GitHub Pages)
- 102 articles in _posts/ — currently being rebuilt with real Amazon products
- Articles use affiliate tag: viciousstudio-20
- Amazon Associates W-9 filed (March 31, 2026)

### Article Rebuild Status (as of March 31, 2026)
- ❌ 0/102 articles rebuilt with real ASINs
- Current workflow running: "Rebuild V2 — Anthropic API Only"
- SerpApi free tier EXHAUSTED — 250/250 searches used, resets April 30
- Current approach: Anthropic API generates product data from existing search queries

### Automation Pipelines
| Workflow | Schedule | Status |
|---|---|---|
| Weekly Content Pipeline | Sundays 8am UTC | ✅ Running |
| Monitor — Every 6 Hours | Every 6 hours | ✅ Running |

### Article Standards — NON-NEGOTIABLE
- Every product link must go to a real Amazon product page: amazon.com/dp/[ASIN]?tag=viciousstudio-20
- NO amazon.com/s?k= search URLs allowed anywhere
- NO loremflickr, unsplash, or placeholder images
- Every article image must be a real Amazon product image URL
- All 6 verification checks must pass before committing any article:
  1. All ASINs in article exist in source product data
  2. All source ASINs appear in article
  3. Zero amazon.com/s?k= URLs
  4. Zero placeholder images
  5. All product URLs contain tag=viciousstudio-20
  6. No duplicate ASINs

---

## WHERE TASKS RUN

| Task Type | Runs In |
|---|---|
| Article rebuild (102 articles) | GitHub Actions only |
| Feed generation | GitHub Actions only |
| Any script using secrets | GitHub Actions only |
| Git commits + pushes | GitHub Actions only (via GH_PAT) |
| Code edits + file changes | Local, then push |
| Quick read-only verification | Local OK |

**Git config for GitHub Actions commits:**
```bash
git config user.name "EdisonHaus Bot" (or "DwellPicks Bot")
git config user.email "bot@edisonhaus.com" (or "bot@dwellpicks.com")
git push https://x-access-token:${{ secrets.GH_PAT }}@github.com/ViciousStudio-tech/[repo].git HEAD:main
```

---

## DAILY DIGEST — REQUIRED HEALTH CHECKS

The daily digest email must include ALL of these. If any check is missing, add it:

- ✅/❌ EdisonHaus store accessible (HTTP 200, no password page)
- ✅/❌ Pinterest feed: item count, zero ns0: occurrences, zero empty descriptions
- ✅/❌ Google Merchant Center feed: item count, last updated timestamp
- ✅/❌ All GitHub Actions workflows: last run time + pass/fail
- ✅/❌ Shopify: product count, order count (last 24h)
- ✅/❌ DwellPicks: article count, last pipeline run
- 🚨 Any workflow that hasn't run in 2× its expected interval = ALERT

---

## SELF-IMPROVEMENT

After any correction from Nick, immediately:
1. Write what went wrong to `tasks/lessons.md`
2. Write the rule that prevents it
3. Add that rule to this CLAUDE.md if it's project-specific
4. Add it to ~/.claude/CLAUDE.md if it's universal

### Lessons Learned So Far
- **Never run scripts locally if secrets are in GitHub Actions** — scripts fail silently or burn free API credits with no output saved
- **Never report a job done by checking GitHub before the job has had time to finish** — check actual commit timestamps vs workflow start time
- **Incremental commits every 10 items** — never wait until the end; if the job fails, nothing is saved
- **Background jobs (`&`) don't work for Claude Code** — the log goes to a file Claude.ai can't read; always use GitHub Actions or run interactively
- **Pinterest feed ns0: bug** — Python ElementTree requires ET.register_namespace('g', 'http://base.google.com/ns/1.0') at module level before any element creation
- **Never use Unsplash source URLs** — that API is dead (503). Never use loremflickr. Only real product images.
- **Amazon direct scraping always gets blocked** — CAPTCHA on virtually every request. Use SerpApi or official API only.
- **Always verify links before providing them** — never give URLs that haven't been confirmed to work

---

## CODING STANDARDS

```python
import builtins
def _no_input(*args, **kwargs):
    raise RuntimeError("BLOCKED: interactive prompt attempted.")
builtins.input = _no_input
```

- No `input()` calls — all scripts run non-interactively
- No confirmation prompts — log everything, never ask
- Log to `./reports/` — all output goes here
- Error handling — wrap all external calls in try/except
- Rate limits — CJ auth: 1 call per 300s. Add time.sleep() between CJ API calls.
