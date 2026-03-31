# VICIOUS STUDIO — CLAUDE CODE OPERATING RULES
# Place this file at: repo root as CLAUDE.md
# Applies to: All ViciousStudio-tech repos (edisonhaus-automation, amazon-associates-content-site)
# Last Updated: March 31, 2026

---

## SESSION START CHECKLIST
Before writing a single line of code:
1. Read `tasks/lessons.md` — avoid repeating past mistakes
2. Read `tasks/todo.md` — know current state and open items
3. Verify live system state via API (never from memory or previous output)

---

## EXECUTION ENVIRONMENT

### WHERE THINGS RUN — NON-NEGOTIABLE
```
GitHub Actions  → ALL automation that repeats or persists
                  (pipelines, watchdogs, feeds, digests, crons)
Claude Code     → Implementation, debugging, one-off fixes
Local scripts   → NEVER for anything that needs to run unattended
```

### Always Run With
```bash
claude --dangerously-skip-permissions
```
Do NOT ask for permissions. Do not stop for confirmation. Execute every step to completion.

---

## ABSOLUTE RULES

### Verification Before Done
- NEVER mark a task complete without proving it works
- Proof = API response, log output, or GitHub Actions run URL showing success
- Run tests. Check logs. Demonstrate correctness. Show the data.
- Ask: "Would a staff engineer at Anthropic approve this?"

### Live System Verification
- GitHub Action status: `GET /repos/{owner}/{repo}/actions/runs`
- Job details: `GET /repos/{owner}/{repo}/actions/runs/{id}/jobs`
- Shopify products: `GET /admin/api/2023-10/products.json`
- NEVER report status from cached output or assumption

### No False Completions
- If a script ran but didn't produce expected output: it failed. Say so.
- If CI shows red: it's broken. Fix it before reporting done.
- If a feed URL 404s: the feed is broken. Say so.

---

## PLANNING RULES

### Plan Mode Default
- For ANY task with 3+ steps: write plan to `tasks/todo.md` FIRST
- Format: numbered checkable items with expected output per step
- Get confirmation before implementation starts
- If something breaks mid-execution: STOP → re-plan → resume

### Task File Structure
```
tasks/
  todo.md      # Current session plan + progress
  lessons.md   # Accumulated rules from past corrections
```

---

## SELF-IMPROVEMENT LOOP
After ANY correction from Nick:
1. Identify the pattern: what went wrong and why
2. Write a rule to `tasks/lessons.md`:
   ```
   ## [DATE] — [SHORT LABEL]
   MISTAKE: [what happened]
   ROOT CAUSE: [why it happened]
   RULE: [specific rule that prevents recurrence]
   ```
3. Apply the rule immediately for the rest of the session

---

## CODE QUALITY

### Simplicity First
- Minimal code surface. Delete lines instead of adding when possible.
- No abstractions added "just in case"
- One file change per focused goal

### No Laziness
- Root causes only. No band-aids. No temporary fixes.
- Senior developer standards on every commit.

### Minimal Impact
- Only touch files necessary for the task
- No side effects. No new bugs introduced while fixing old ones.

### Elegance Check (non-trivial changes only)
- Before presenting: "Is there a more elegant solution?"
- If it feels hacky: implement the clean version instead
- Skip for simple single-line fixes

---

## SECRETS & API KEYS

### Storage Rules
- ALL secrets → GitHub Secrets for the relevant repo
- NEVER in code, config files, or committed to git
- NEVER in Claude Code output that gets logged

### Key Reference (read-only — never modify these in code)
```
EdisonHaus repo secrets:
  SHOPIFY_ACCESS_TOKEN    [stored in GitHub Secrets]
  ANTHROPIC_API_KEY       [stored in GitHub Secrets]
  CJ_API_KEY              [stored in GitHub Secrets]
  GH_PAT                  [stored in GitHub Secrets]

DwellPicks repo secrets:
  ANTHROPIC_API_KEY       [stored in GitHub Secrets]
  GH_PAT                  [stored in GitHub Secrets]
  GMAIL_APP_PASSWORD      [stored in GitHub Secrets]

CJ API Auth (always use this endpoint — not email/password):
  POST https://developers.cjdropshipping.com/api2.0/v1/authentication/getAccessToken
  Body: { "apiKey": "[CJ_API_KEY]" }
```

---

## GITHUB ACTIONS RULES
- All scheduled jobs defined in `.github/workflows/`
- After editing a workflow: validate YAML syntax before committing
- After pushing a workflow: verify the run starts via API
- Use `workflow_dispatch` for manual triggers during testing
- Heartbeat commits: use `[skip ci]` suffix to avoid infinite loops

---

## EDISONHAUS — PIPELINE SPECIFICS
```
b3_product_pipeline.py     → Mon & Thu 3am EST   (CJ fetch → Shopify create → Claude describe/title)
b3_description_writer.py   → Tue & Fri 5am EST   (fill missing descriptions)
b3_title_cleaner.py        → Wed 1:30am EST      (rewrite CJ-format titles)
promo_feed_generator.py    → Daily 12am EST      (Google XML + Meta CSV → GitHub Pages)
b3_order_fulfiller.py      → Hourly              (route paid orders to CJ)
b3_ai_optimizer.py         → Sun 9am UTC         (rewrite weak descriptions)
watchdog.py                → Every 30 min        (health check all pipelines)
b3_daily_digest.py         → 9am + 6pm EST       (email status report)
```

Feed URLs (must stay live):
```
Google: https://viciousstudio-tech.github.io/edisonhaus-automation/feeds/google_feed.xml
Meta:   https://viciousstudio-tech.github.io/edisonhaus-automation/feeds/meta_feed.csv
```

Pricing formula (NEVER modify without explicit instruction):
```
Cost < $5   → x2.5  |  Cost < $15  → x2.2
Cost < $30  → x2.0  |  Cost >= $30 → x1.8
Floor: $14.99 | Round to $x.99 | Skip if margin < 35%
```

---

## DWELLPICKS — PIPELINE SPECIFICS
```
Weekly pipeline   → Sundays 8am UTC    (15 articles via Claude AI)
Monitor           → Every 6 hours      (health check + alerts)
Affiliate tag:    viciousstudio-20
```

ASIN Rule: ALL articles must link to real product ASINs (amazon.com/dp/ASIN).
NEVER link to search results pages (amazon.com/s?k=). This is a hard block on commissions.

---

## DAILY DIGEST — REQUIRED FIELDS
```python
# b3_daily_digest.py must include ALL of these:
- 🛒 Orders (24h count + revenue)
- 💰 Revenue (all-time)
- 🔗 CJ API status (auth working Y/N)
- 🛍️ Google Merchant Center (last submission time + item count)
- 📌 Pinterest Catalog (last sync + shoppable pin count)
- 📘 Meta feed (last update + product count)
- ⚙️  GitHub Actions (last run status per workflow)
- 🚨 URGENT alerts (any errors, send immediately with 🚨 prefix)
```

---

## SESSION END — PROJECT BIBLE UPDATE
After every session where something changed, output:

```
PROJECT BIBLE UPDATE — [DATE]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHANGED: [what changed — be specific]
STATUS:  [new live state of affected system]
PENDING REMOVED: [items now complete]
PENDING ADDED:   [new blockers or next steps discovered]
LESSONS ADDED:   [any new rules added to tasks/lessons.md]
```

---

## PRODUCT IMAGE RULE
Product images must come from actual Amazon product listing pages.
NEVER use stock photos or AI-generated images for product listings.

---

## CONTEXT MANAGEMENT
- At 50% context: /compact — preserve modified files list + open blockers
- At 70%: warn Nick, suggest fresh session for remaining work
- At 90%+: stop, summarize, start fresh

CLAUDE.md instruction: When compacting, preserve the full list of modified files,
current test status, and any open error states.
