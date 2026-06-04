# Scraper spec: `tax_sale_lgbs` — Bexar, TX (v1 — AWAITING OPERATOR REVIEW)

**Date:** 2026-06-03 · **Status:** DRAFT for operator review. No code built. Derived
from `recon/tax_sale_lgbs_recon.md`. The 5 open questions below carry **PROPOSED**
resolutions — confirm/adjust each before the scraper + translator are written.

## Source contract (from recon)

- **Lead feed:** `GET https://taxsales.lgbs.com/api/property_sales/`
  - params: `county=BEXAR COUNTY` · `state=TX` · `sale_type=SALE,RESALE,STRUCK OFF,FUTURE SALE` · `in_bbox=-98.806,29.114,-98.117,29.760` (Bexar bounds) · `ordering=precinct,sale_nbr,uid` · `limit`/`offset`
  - response: DRF page — `count`, `next` (abs URL), `previous`, `results[]`. ~84 Bexar records (2026-06-03).
- **Auth:** none. **CAPTCHA:** none. **Format:** clean JSON (no SPA automation needed — plain HTTP client, unlike the clerk Playwright path).
- **Record fields:** `uid, sale_id, venue_id, county, state, cause_nbr, precinct, sale_nbr, sale_date(_only), sale_type, status, account_nbr, prop_address_one/_two, prop_city, prop_state, prop_zipcode, value, minimum_bid, geometry, county_sale_list, sale_notes, book_nbr, has_photo`.

## Scraper design (`scrapers/tax_sale_lgbs.py`)

- **Access pattern:** `open_api` — paginated HTTP GET (`requests`/`urllib`), no browser. Follow `next` until exhausted; cap defensively.
- **Modes:** single `full_pull` (the active Bexar sale set is small, ~84). No cursor needed — the API always returns the current active set; freshness comes from re-pulling.
- **Dedup key:** `uid` (stable per property-sale). Re-pull replaces the active set; write is **idempotent by `uid`** (overwrite/merge, not append — avoids the clerk append-overlap problem entirely).
- **Output:** `data/raw/tax_sale_lgbs.jsonl`, each record wrapped in the §4.32 envelope: `{raw_record_id: "tax_sale_lgbs_<uid>", source_id, source_url, source_fetched_at, parser_confidence: 98, raw_payload: <api record>}`.
- **Run metadata + raw-HTML/JSON audit** per the framework convention (mirror `clerk_recordings` run-metadata shape).
- **Error handling:** retry/backoff on 5xx/timeout; on `count==0` record `NO_RESULTS` (not a halt); on schema drift (missing `uid`/`cause_nbr`) record `PARSER_CHANGED`.

## Translator design (`scaffold/pipeline/translators/tax_sale_lgbs.py`)

- `field_map`: `doc_number ← cause_nbr`, `address ← prop_address_one`, `city ← prop_city`, `zip ← prop_zipcode`, `filing_date ← sale_date_only`, `amount ← minimum_bid` (or `value`).
- `account_nbr` → carried for the BCAD/parcel enrichment join.
- `geometry` → carried (already a Point; lon,lat).
- doc-type mapping: `sale_type` → canonical (see Q1).

---

## Open questions — PROPOSED resolutions (confirm each)

### Q1 — Canonical doc type / lead pattern
**PROPOSED:** Reuse the existing **`TAX_FORECLOSURE_NOTICE`** canonical type (`lead_pattern: tax`) — the same one `foreclosure_notices_map` emits for its Tax layer. Carry `sale_type` (SALE/RESALE/STRUCK OFF/FUTURE SALE) as the **subtype label** for display, not as new canonical types. → **No framework-vocab change, no FRAMEWORK_VERSION bump.** (Alternative if you want sale-stage scoring granularity: add 4 new canonical types — heavier, cross-county.)

### Q2 — Dedup vs `foreclosure_notices_map`
**PROPOSED:** They overlap on **tax** foreclosures (the Clerk map's Tax layer and LGBS are the same underlying events). Dedup at the §19 aggregation key on **`account_nbr` ↔ BCAD parcel / normalized address**; when both present for one property, **LGBS wins** (richer: `cause_nbr`, `sale_date`, adjudged `value`, `minimum_bid`, geocode) and the foreclosure-map Tax record is suppressed/merged into the same lead (stacks, not double-counts). Mortgage-layer foreclosure-map records are unaffected.

### Q3 — Lifecycle suppression by `status`
**PROPOSED:**
- **Active lead:** `Scheduled for Auction`, `Scheduled for Online Auction`, `Scheduled for Sealed Bid Auction`, `Available for Future Sale`, `Sale Results Pending`.
- **Keep (different stage):** `Struck off to Jurisdiction` → county-owned resale opportunity (still actionable for investors) — keep, tag as post-sale.
- **Suppress:** `Sold` (no longer acquirable pre-sale), `Cancelled`.

### Q4 — Refresh cadence
**PROPOSED:** **DAILY** — one paginated pull (~84 records) is cheap and keeps the first-Tuesday cycle fresh; idempotent by `uid` so daily re-pulls don't accumulate. (Weekly is acceptable if you prefer fewer hits.)

### Q5 — Reliability grade / proof
**PROPOSED:** Grade **C** (official vendor mirror) for v1. Future enhancement: resolve each `cause_nbr` to the District-Clerk tax suit (grade A proof) — defer.

---

## Config changes (PROPOSED — atomic writer + `operator_override_audit`)

1. Add `sources.tax_sale_lgbs`: PRIMARY_LEAD_SOURCE, P0/P1, grade C, `translator: tax_sale_lgbs`, `scraper_module: scrapers/tax_sale_lgbs.py`, `parcel_id_prefix: BX-TS-`, the `field_map` above, `translator_config` (sale_type→subtype map, lifecycle suppression set, dedup-against `foreclosure_notices_map`).
2. Reclassify `sources.tax_collector` (ACT) → `ENRICHMENT` / `source_role: ENRICHMENT` (it can't discover delinquents). Record both as operator overrides.

## Test plan
- Offline parser test against a captured `property_sales` page fixture (all fields extract; envelope shape).
- Translator unit test: `sale_type`→subtype, lifecycle suppression, dedup flag.
- Keystone end-to-end: LGBS raw → translator → §17–§20 → seam yields `tax`-pattern scored leads, §20 DEPLOY_OK, dedup vs a synthetic overlapping foreclosure-map record.
- Gate suite (golden path + county-agnostic regression) stays green.

## Build order (after approval)
1. Confirm Q1–Q5 + config plan → 2. scraper + offline fixture test → 3. translator + tests → 4. config via atomic writer → 5. first live pull → 6. combined rebuild + gate → 7. promote.
