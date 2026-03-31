# Lessons

## 2026-03-31 — Img-only descriptions passed as "not missing"
MISTAKE: Description writer reported "0 missing" for 210 products that had only `<img>` tags in body_html
ROOT CAUSE: Checked `len(body_html) < 50` instead of stripping HTML tags first — img-only HTML is 163+ chars
RULE: Always strip HTML tags before checking if a description field has real text content. `len(html)` is never a valid proxy for "has meaningful description."
