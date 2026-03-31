# Todo

## 2026-03-31 — Fix EdisonHaus Description Pipeline

- [ ] 1. Fix `fetch_missing()` detection: strip HTML tags before checking length
- [ ] 2. Add `fetch_cj_description()` to pull real CJ text before Claude fallback
- [ ] 3. Update `generate_description()` to accept optional CJ description
- [ ] 4. Replace `google_category()` numeric IDs with full taxonomy strings
- [ ] 5. Fix feed description fallback to strip HTML and check for real text
- [ ] 6. Verify: run fetch_missing() before/after — expect 0 → ~210
- [ ] 7. Commit all changes
- [ ] 8. Trigger Promo Feed Generator workflow via API
- [ ] 9. Trigger Description Writer workflow via API
- [ ] 10. Write lesson to tasks/lessons.md
- [ ] 11. Show final proof
