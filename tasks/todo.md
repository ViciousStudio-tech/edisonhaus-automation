# Todo

## 2026-03-31 — Fix EdisonHaus Description Pipeline

- [x] 1. Fix `fetch_missing()` detection: strip HTML tags before checking length
- [x] 2. Add `fetch_cj_description()` to pull real CJ text before Claude fallback
- [x] 3. Update `generate_description()` to accept optional CJ description
- [x] 4. Replace `google_category()` numeric IDs with full taxonomy strings
- [x] 5. Fix feed description fallback to strip HTML and check for real text
- [x] 6. Verify: run fetch_missing() before/after — expect 0 → ~210
- [x] 7. Commit all changes
- [x] 8. Trigger Promo Feed Generator workflow via API
- [x] 9. Trigger Description Writer workflow via API
- [x] 10. Write lesson to tasks/lessons.md
- [x] 11. Show final proof
