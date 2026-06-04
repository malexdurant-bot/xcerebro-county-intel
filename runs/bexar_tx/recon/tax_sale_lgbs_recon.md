# Recon: LGBS tax-sale source (`tax_sale_lgbs`) — Bexar, TX

**Date:** 2026-06-03 · **Mode:** Build-Mode recon, READ-ONLY (no scraper built, no
config source added). Authorized as the next step after the 2026-06-03 tax
access-path scoping (see `operator_notes.md`). This is the recommended PRIMARY
tax-distress source. Adding it to `config/counties/bexar_tx.json` and reclassifying
ACT `tax_collector` → ENRICHMENT remains an operator decision.

## What it is

`taxsales.lgbs.com` — Linebarger Goggan Blair & Sampson (LGBS), Bexar County's
delinquent-tax attorney. Publishes properties in **tax-foreclosure**: post-judgment
sales scheduled for the first-Tuesday courthouse auction, plus resale/struck-off.
These are genuine **event-based distress** records (a tax-foreclosure judgment +
scheduled sale), squarely within the §4 product rule — not enrichment.

## Access classification (5-layer gate, abbreviated)

- **Official origin:** vendor portal of the county's official delinquent-tax law firm (LGBS). Reliability grade **C** (vendor mirror of the official tax-suit/sale process); the underlying cause is an official District-Clerk tax suit.
- **Data access:** fully **public**, no login, **no CAPTCHA**. Clean Django-REST JSON API (not just an HTML SPA).
- **Source role:** PRIMARY_LEAD_SOURCE candidate (event-based tax-foreclosure distress).
- **Cost:** FREE. **Cadence:** continuous; sale lists publish ahead of the monthly first-Tuesday sale (recommend DAILY or WEEKLY refresh).
- **Rate limit:** unknown / not advertised — recommend polite throttling (the whole Bexar set is ~84 records over ~9 pages at limit=10, or one call at limit=100).

## API map (all GET, public JSON)

| Endpoint | Purpose |
|---|---|
| `/api/venues/suggest/?search=Bexar+County` | typeahead → venue id `1000000015`, centroid, bounds |
| `/api/venues/?name=BEXAR+COUNTY,+TX` | venue polygon (FeatureCollection) |
| `/api/sale_counties/?limit=60` | counties with active sales (Bexar present) |
| `/api/sale_status/?limit=60` | 8 statuses (see below) |
| `/api/filter_bar/?limit=1000` | county/date/status/precinct facets |
| `/api/property_sales/cluster/?county=BEXAR+COUNTY&in_bbox=…` | map clusters w/ `chartData` per sale_type |
| **`/api/property_sales/?county=BEXAR+COUNTY&state=TX&sale_type=…&in_bbox=…`** | **the lead feed** — paginated property records |

### `property_sales` query params (observed)
- `county=BEXAR COUNTY` (URL-encoded `BEXAR+COUNTY`), `state=TX`
- `sale_type=SALE,RESALE,STRUCK OFF,FUTURE SALE` (comma list; subset OK)
- `in_bbox=minLon,minLat,maxLon,maxLat` — Bexar bounds ≈ `-98.806,29.114,-98.117,29.760` (from the venue record); a wider bbox also works as long as `county=` filters
- `ordering=precinct,sale_nbr,uid`
- Pagination: DRF style — `limit` / `offset`, response carries `count` + `next` (absolute URL) + `previous` + `results[]`. **84 Bexar records** on 2026-06-03.

### `sale_status` values (8)
`Available for Future Sale`, `Cancelled`, `Sale Results Pending`,
`Scheduled for Sealed Bid Auction`, `Scheduled for Online Auction`, `Sold`,
`Struck off to Jurisdiction`, `Scheduled for Auction`.

## Record shape (`property_sales` result)

Fields: `uid, sale_id, venue_id, state, county, cause_nbr, precinct, sale_nbr,
sale_date, sale_date_only, property_loc, county_sale_list, sale_published,
sale_type, status, account_nbr, street_name, prop_address_one, prop_address_two,
prop_city, prop_state, prop_zipcode, value, minimum_bid, google_view, book_nbr,
sale_notes, has_photo, geometry`.

Sample (2026-06-03):
```json
{
  "uid": 1003792019, "sale_id": 1000329607, "venue_id": 1112,
  "county": "BEXAR COUNTY", "state": "TX",
  "cause_nbr": "2006TA102455", "sale_nbr": 1,
  "sale_date": "2026-06-02T10:00:00", "sale_date_only": "2026-06-02",
  "sale_type": "RESALE", "status": "Sale Results Pending",
  "account_nbr": "024240040021",
  "prop_address_one": "1017 TORREON", "prop_city": "SAN ANTONIO",
  "prop_state": "TX", "prop_zipcode": "78207",
  "value": "10970.00", "minimum_bid": "10866.25",
  "county_sale_list": "https://taxsalesib.lgbs.com/images/.../County_Sale_list_BEXAR_COUNTY_20260602",
  "geometry": {"type": "Point", "coordinates": [-98.524135, 29.421119]}
}
```

## Lead mapping (proposed, for the scraper-spec / translator)

| Canonical need | LGBS field |
|---|---|
| dedup / raw_record_id | `uid` (stable per property-sale) |
| instrument / case number | `cause_nbr` (tax-suit cause; `…TA…` = tax) |
| event (distress) date | `sale_date_only` (scheduled sale) |
| doc-type subtype | `sale_type` (SALE / RESALE / STRUCK OFF / FUTURE SALE) |
| property address | `prop_address_one` (+`_two`) / `prop_city` / `prop_state` / `prop_zipcode` |
| amounts | `value` (adjudged), `minimum_bid` (opening) |
| parcel link (enrichment) | `account_nbr` → BCAD / ACT lookup |
| geo | `geometry` (already a Point; lon,lat) |
| lifecycle | `status` (Sold / Cancelled / Struck off → suppress or down-rank) |
| source doc | `county_sale_list` (official county sale-list PDF) |

`parser_confidence`: high (structured JSON, geocoded, explicit fields).

## Open questions for the scraper spec (NOT decided here)

1. **New canonical doc type / pattern.** Tax-foreclosure sale isn't in the current
   registry vocab. Likely a new `tax` / `tax_foreclosure` lead pattern (cf. the
   probate/estate cluster precedent) — a cross-county framework-vocab decision.
2. **Dedup vs `foreclosure_notices_map`.** That source is the County Clerk's
   *mortgage + tax* foreclosure map; LGBS is *tax* foreclosures via the law firm.
   Overlap is possible (a property in both). Need a join key — `account_nbr` ↔
   BCAD parcel, or address — and a precedence rule, mirroring the deferred clerk
   FC-dedup.
3. **Lifecycle suppression.** `status in {Sold, Cancelled, Struck off to
   Jurisdiction}` — suppress, archive, or keep as a different signal?
4. **Refresh cadence + sale-cycle handling.** Sale lists rotate monthly; decide
   daily vs weekly and how to age out past sale_dates.
5. **Reliability grade.** Grade C (vendor mirror); does the build want to also
   tie each `cause_nbr` back to the District-Clerk tax suit (grade A) for proof?

## Next Build-Mode step (needs operator go-ahead)

Write the `tax_sale_lgbs` scraper spec from this recon (API client: `county=` +
`sale_type=` + bbox + DRF pagination → §4.32 `raw_payload` envelope keyed on
`uid`), resolve the 5 open questions, then build scraper + translator. Pairs with
the config change: ACT `tax_collector` → ENRICHMENT, add `tax_sale_lgbs` PRIMARY.
