"""
Richland County, SC — Delinquent Tax Sale scraper.

Source: richlandmaps.com/apps/delinquent/ — a custom Leaflet-based "RCGeo Tax
Sale Parcel Viewer" app (NOT ArcGIS, despite what an earlier version of this
file assumed). The app's vector layer loads from a small JSON API:

    GET https://richlandmaps.com/apps/api/layers/load.php
        ?layer=delinquent
        &fields=owner,tms,address,balance,due1,due2,due3,due4,other1,other2
        &where=
        &roi=SRID=4326;POLYGON((west south, west north, east north, east south, west south))
        &zoom=10
        &geomtype=wkt
        &limit=10000

`roi` is a county-wide bounding polygon (EWKT) — the front-end normally sends
the current map viewport bounds; we send the full county extent (from the
app's own MAP_CONFIG.base_themes bounds) to get every parcel in one call.
Endpoint/params confirmed live 2026-08-22 by reading rclib/app-layers.js
(AppLayers[...].refresh_data — builds the query string) and
rclib/app-utils.js (AppUtils.GetMapBoundsEWKTPolygon — builds the roi string).

Canonical doc type: tax_foreclosure_notice (annual tax sale list)
Source ID:         delinquent_tax_sale
Source role:       PRIMARY_EVENT_SOURCE

Annual cadence — Richland County holds its tax sale in November or December.
The layer is "updated nightly prior to the tax sale" per the app's own layer
label; outside that window it may return a small carryover list (confirmed
16 parcels live in August) or zero. That is expected, not a failure.

Incremental behaviour: this endpoint returns the CURRENT full snapshot, not
an event log, so this scraper keeps a small `seen.json` cursor of parcel
IDs (TMS) already emitted and only returns newly-appeared parcels on each
incremental run. `--full` (or incremental=False) re-emits every parcel
currently on the list, ignoring the cursor.
"""

from __future__ import annotations

import hashlib
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "richland_delinquent_tax"
RAW_DIR.mkdir(parents=True, exist_ok=True)
SEEN_PATH = RAW_DIR / "seen.json"

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://richlandmaps.com/apps/delinquent/",
})

APP_URL = "https://richlandmaps.com/apps/delinquent/"
LOAD_URL = "https://richlandmaps.com/apps/api/layers/load.php"

# County extent, from APP.MAP_CONFIG.base_themes[...].bounds in the live app
# (rclib/app-init.js), covers all of Richland County in one query.
_COUNTY_SOUTH = 33.74341568
_COUNTY_WEST = -81.34567040
_COUNTY_NORTH = 34.27008380
_COUNTY_EAST = -80.59771797

FIELDS = "owner,tms,address,balance,due1,due2,due3,due4,other1,other2"


def _county_roi() -> str:
    s, w, n, e = _COUNTY_SOUTH, _COUNTY_WEST, _COUNTY_NORTH, _COUNTY_EAST
    return (
        f"SRID=4326;POLYGON(({w} {s}, {w} {n}, {e} {n}, {e} {s}, {w} {s}))"
    )


# ---------------------------------------------------------------------------
# Cursor helpers
# ---------------------------------------------------------------------------

def _load_seen() -> set[str]:
    if SEEN_PATH.exists():
        return set(json.loads(SEEN_PATH.read_text(encoding="utf-8")))
    return set()


def _save_seen(seen: set[str]) -> None:
    SEEN_PATH.write_text(json.dumps(sorted(seen), indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _fetch_layer() -> list[dict]:
    """Fetch the current delinquent-tax parcel list from the live layer API."""
    params = {
        "layer": "delinquent",
        "fields": FIELDS,
        "where": "",
        "roi": _county_roi(),
        "zoom": "10",
        "geomtype": "wkt",
        "limit": "10000",
    }
    r = _SESSION.get(LOAD_URL, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data.get("data", [])


# ---------------------------------------------------------------------------
# Feature → raw_event_record
# ---------------------------------------------------------------------------

def _stable_id(tms: str) -> str:
    return "raw_dt_" + hashlib.md5(tms.encode()).hexdigest()[:16]


def _clean(val: Any) -> str | None:
    if val is None:
        return None
    s = html.unescape(str(val)).strip()
    return s or None


def _feature_to_raw_event(item: dict, captured_at: str) -> dict | None:
    tms = _clean(item.get("tms"))
    owner = _clean(item.get("owner"))
    address = _clean(item.get("address"))

    if not tms:
        return None

    try:
        balance = float(item["balance"]) if item.get("balance") not in (None, "") else None
    except (ValueError, TypeError):
        balance = None

    # due1..due4 / other1..other2 carry delinquent tax-year codes (e.g. "24" =
    # TY2024); occasionally other1/other2 carry a second owner name instead
    # of a year on multi-owner parcels. Collect whichever look like years.
    tax_years = [
        v for v in (item.get(f"due{i}") for i in range(1, 5))
        if v and str(v).strip().isdigit()
    ]

    raw_event_id = _stable_id(tms)

    return {
        "raw_event_id": raw_event_id,
        "source_id": "delinquent_tax_sale",
        "source_role": "PRIMARY_EVENT_SOURCE",
        "raw_doc_type": "DELINQUENT TAX SALE LIST",
        "canonical_doc_type": "tax_foreclosure_notice",
        "instrument_number": tms,
        "recorded_date": None,
        "event_date": None,
        "source_url": APP_URL,
        "parties": (
            [{"name": owner, "name_type": "TP", "raw_role": "TAXPAYER"}]
            if owner
            else []
        ),
        "document_body_text": (
            f"TMS: {tms}\nOWNER: {owner}\nADDRESS: {address}\n"
            f"BALANCE: {balance}\nDELINQUENT TAX YEARS: {', '.join(tax_years)}"
            if owner or address
            else None
        ),
        "property_refs": {
            "parcel_id": tms,
            "situs_address": address,
            "legal_description": None,
            "case_number": None,
        },
        "amounts": (
            [{"label": "delinquent_amount", "value": balance}]
            if balance is not None
            else []
        ),
        "parser_name": "richland_delinquent_tax_rcgeo_v2",
        "parser_version": "2.0.0",
        "parser_confidence": 85 if (tms and owner and address) else 60,
        "captured_at": captured_at,
    }


# ---------------------------------------------------------------------------
# Main scrape entry point
# ---------------------------------------------------------------------------

def scrape(incremental: bool = True) -> list[dict]:
    """
    Scrape the Richland County delinquent tax parcel list.

    Args:
        incremental: If True (default), only return parcels not already
                      seen in a prior run (tracked in data/raw/.../seen.json).
                      If False, return every parcel currently on the list.

    Returns:
        List of raw_event_record dicts.
    """
    captured_at = datetime.now(timezone.utc).isoformat()

    print(f"[richland_delinquent_tax] Querying live layer: {LOAD_URL}")
    try:
        features = _fetch_layer()
    except requests.HTTPError as exc:
        print(f"[richland_delinquent_tax] HTTP error: {exc}")
        return []
    except Exception as exc:
        print(f"[richland_delinquent_tax] Fetch failed: {exc}")
        return []

    print(f"[richland_delinquent_tax] Total parcels on list: {len(features)}")

    all_records: list[dict] = []
    skipped = 0
    for feat in features:
        rec = _feature_to_raw_event(feat, captured_at)
        if rec:
            all_records.append(rec)
        else:
            skipped += 1

    seen = _load_seen() if incremental else set()
    if incremental:
        records = [r for r in all_records if r["instrument_number"] not in seen]
    else:
        records = all_records

    print(
        f"[richland_delinquent_tax] Parsed {len(all_records)} parcels "
        f"({skipped} skipped — no TMS); {len(records)} new this run"
    )

    new_seen = seen | {r["instrument_number"] for r in all_records}
    _save_seen(new_seen)

    if records:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = RAW_DIR / f"delinquent_tax_{ts}.jsonl"
        with out_path.open("w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[richland_delinquent_tax] Wrote {len(records)} records → {out_path}")

    return records


if __name__ == "__main__":
    results = scrape(incremental="--full" not in sys.argv)
    print(f"Total delinquent tax records: {len(results)}")
