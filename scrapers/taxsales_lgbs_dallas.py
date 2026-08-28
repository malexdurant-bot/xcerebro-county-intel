"""
Dallas County — LGBS Tax Sales API (covers BOTH sheriff_sales AND
tax_foreclosure_resales from a single source).

Source discovery note (2026-08-23): the county's Public Works Property
Division page (dallascounty.org/departments/pubworks/property-division.php)
links to two things: a static "struck-off list" PDF (the originally recon'd
tax_foreclosure_resales source), and a "List of Tax Sale Properties" map at
taxsales.lgbs.com — a public tax-sale listing site run by Linebarger Goggan
Blair & Sampson, LLP (a major Texas tax-lien law firm that operates this same
map for many TX counties). The map is backed by a genuinely public,
unauthenticated JSON REST API:

    GET https://taxsales.lgbs.com/api/property_sales/
        ?county=DALLAS+COUNTY&state=TX
        &sale_type=SALE,RESALE,STRUCK+OFF,FUTURE+SALE
        &ordering=precinct,sale_nbr,uid
        &limit=200&offset=<n>

No API key, no login, no CAPTCHA, no Playwright needed — confirmed live
2026-08-23 with plain `requests`. Standard DRF-style pagination
(count/next/previous/results).

This single feed resolves TWO previously separate, harder sources:
  - sale_type=STRUCK OFF  -> tax_foreclosure_resales (78 properties live
    2026-08-23) — a live, structured superset of the static struck-off PDF,
    with full street address, appraised value, and minimum bid, none of
    which the PDF necessarily exposes in a machine-readable way.
  - sale_type in (SALE, RESALE, FUTURE SALE) -> sheriff_sales (31 + 0 + 442
    = 473 properties live 2026-08-23). The originally recon'd sheriff_sales
    source (dallas.texas.sheriffsaleauctions.com) returned HTTP 403 to a
    plain fetch and was marked BLOCKED, requiring Playwright. This LGBS API
    is the upstream data feed for that same RealAuction platform (each
    record's `property_loc` field is a direct link into
    dallas.texas.sheriffsaleauctions.com) and exposes richer data (full
    address, minimum bid, appraised value, sale status) than the auction
    site's own page would show without a browser session — so the 403
    blocker on sheriffsaleauctions.com is now moot; this API is the actual
    source of record for that data.

Each result also carries a `geometry` (lat/lon) and a `county_sale_list`
link to a per-county sale-list image/PDF, both preserved in raw_payload as
enrichment.

Requires: pip install requests (already a framework dependency elsewhere)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]

API_URL = "https://taxsales.lgbs.com/api/property_sales/"
COUNTY = "DALLAS COUNTY"
STATE = "TX"
SALE_TYPES = "SALE,RESALE,STRUCK OFF,FUTURE SALE"
PAGE_LIMIT = 200

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# sale_type -> which framework source this record belongs to
SALE_TYPE_TO_SOURCE = {
    "STRUCK OFF": "tax_foreclosure_resales",
    "SALE": "sheriff_sales",
    "RESALE": "sheriff_sales",
    "FUTURE SALE": "sheriff_sales",
}


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def fetch_all_listings(verbose: bool) -> list[dict]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    results: list[dict] = []
    offset = 0
    while True:
        params = {
            "county": COUNTY,
            "state": STATE,
            "sale_type": SALE_TYPES,
            "ordering": "precinct,sale_nbr,uid",
            "limit": PAGE_LIMIT,
            "offset": offset,
        }
        resp = session.get(API_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        page = data.get("results", [])
        results.extend(page)
        if verbose:
            print(f"  [Dallas LGBS] offset {offset}: +{len(page)} (total so far {len(results)} / {data.get('count')})", flush=True)
        if not data.get("next") or not page:
            break
        offset += PAGE_LIMIT
    return results


def _raw_record_id(listing: dict) -> str:
    uid = listing.get("uid")
    return f"dallas_lgbs_{uid}"


def _to_wrapped_record(listing: dict) -> dict | None:
    sale_type = (listing.get("sale_type") or "").strip().upper()
    source_id = SALE_TYPE_TO_SOURCE.get(sale_type)
    if source_id is None:
        return None

    addr1 = (listing.get("prop_address_one") or "").strip()
    addr2 = (listing.get("prop_address_two") or "").strip()
    address = f"{addr1} {addr2}".strip() or None

    raw_payload = {
        "address": address,
        "city": (listing.get("prop_city") or "").strip() or None,
        "state": listing.get("prop_state"),
        "zip": listing.get("prop_zipcode"),
        "account_nbr": listing.get("account_nbr"),
        "cause_nbr": listing.get("cause_nbr"),
        "sale_type": sale_type,
        "sale_status": listing.get("status"),
        "sale_date": listing.get("sale_date_only") or listing.get("sale_date"),
        "sale_nbr": listing.get("sale_nbr"),
        "precinct": listing.get("precinct") or None,
        "appraised_value": listing.get("value"),
        "minimum_bid": listing.get("minimum_bid"),
        "sale_notes": listing.get("sale_notes") or None,
        "book_nbr": listing.get("book_nbr"),
        "auction_listing_url": listing.get("property_loc"),
        "county_sale_list_url": listing.get("county_sale_list"),
        "latitude": (listing.get("geometry") or {}).get("coordinates", [None, None])[1],
        "longitude": (listing.get("geometry") or {}).get("coordinates", [None, None])[0],
    }

    return {
        "raw_record_id": _raw_record_id(listing),
        "source_id": source_id,
        "source_url": f"{API_URL}?uid={listing.get('uid')}",
        "source_fetched_at": _now_iso(),
        "parser_confidence": 100,
        "raw_payload": raw_payload,
    }


def _load_prior(path: Path) -> dict:
    if not path.exists():
        return {}
    out: dict = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = rec.get("raw_record_id")
            if rid:
                out[rid] = rec
    return out


def merge_with_prior(current: list[dict], prior: dict) -> list[dict]:
    now = _now_iso()
    merged: list[dict] = []
    seen_ids = set()
    for rec in current:
        rid = rec["raw_record_id"]
        seen_ids.add(rid)
        prev = prior.get(rid)
        if prev is None:
            rec["first_seen_at"] = now
            rec["last_seen_at"] = now
            rec["change_status"] = "NEW_RECORD"
        else:
            rec["first_seen_at"] = prev.get("first_seen_at", now)
            rec["last_seen_at"] = now
            rec["change_status"] = (
                "SAME" if prev.get("raw_payload") == rec["raw_payload"] else "UPDATED"
            )
        merged.append(rec)
    for rid, prev in prior.items():
        if rid not in seen_ids:
            prev["change_status"] = "DISAPPEARED"  # sold, withdrawn, or resolved
            merged.append(prev)
    return merged


def _write_jsonl(records: list[dict], output_path: Path) -> dict:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prior = _load_prior(output_path)
    merged = merge_with_prior(records, prior)

    tmp = output_path.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for rec in merged:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(output_path)

    return {
        "output_path": str(output_path),
        "records_pulled": len(records),
        "prior_count": len(prior),
        "total_after_merge": len(merged),
        "new_record_count": sum(1 for r in merged if r["change_status"] == "NEW_RECORD"),
        "same_record_count": sum(1 for r in merged if r["change_status"] == "SAME"),
        "updated_record_count": sum(1 for r in merged if r["change_status"] == "UPDATED"),
        "disappeared_record_count": sum(1 for r in merged if r["change_status"] == "DISAPPEARED"),
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_scraper(out_dir: Path, verbose: bool = True) -> dict:
    listings = fetch_all_listings(verbose)

    by_source: dict[str, list[dict]] = {"tax_foreclosure_resales": [], "sheriff_sales": []}
    skipped = 0
    for listing in listings:
        wrapped = _to_wrapped_record(listing)
        if wrapped is None:
            skipped += 1
            continue
        by_source[wrapped["source_id"]].append(wrapped)

    stats = {}
    for source_id, records in by_source.items():
        out_path = out_dir / f"{source_id}.jsonl"
        stats[source_id] = _write_jsonl(records, out_path)

    return {
        "api_url": API_URL,
        "total_listings_fetched": len(listings),
        "skipped_unknown_sale_type": skipped,
        **stats,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Dallas County LGBS tax sales API scraper. Covers BOTH "
            "tax_foreclosure_resales (STRUCK OFF) and sheriff_sales "
            "(SALE/RESALE/FUTURE SALE) from one public JSON API — no "
            "Playwright, no auth."
        )
    )
    parser.add_argument("--out-dir", default=None,
                         help="Output directory for tax_foreclosure_resales.jsonl and "
                              "sheriff_sales.jsonl. Default: data/raw/")
    args = parser.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else REPO_ROOT / "data" / "raw"
    stats = run_scraper(out_dir)
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
