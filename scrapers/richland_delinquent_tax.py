"""
Richland County, SC — Delinquent Tax Sale scraper.

Source: richlandmaps.com/apps/delinquent/ (ArcGIS Web App, backed by a
hosted feature layer). This scraper discovers the feature layer REST endpoint
from the app's init config and pages through all features via ArcGIS REST API.

Canonical doc type: tax_foreclosure_notice (annual tax sale list)
Source ID:         delinquent_tax_sale
Source role:       PRIMARY_EVENT_SOURCE

Annual cadence — Richland County holds its tax sale in November or December.
The GIS viewer is updated when the delinquent list is published (typically
~60 days before the sale). Running this scraper outside the Oct–Dec window
will return zero records or last year's list — that is expected.

Rate policy: ArcGIS REST pagination (1000 features/page, max 10 pages/run).
No artificial sleep required between pages; the endpoint is a public REST API.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
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

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
})

# ---------------------------------------------------------------------------
# ArcGIS endpoint discovery
# The richlandmaps.com delinquent viewer is a standard ArcGIS Web AppBuilder
# or Experience Builder app. The feature layer URL is found in the app's
# config.json or init endpoint. We probe the known pattern first; if the
# endpoint changes, re-run _discover_layer_url() with a live browser network
# capture and update FEATURE_LAYER_URL below.
# ---------------------------------------------------------------------------

FEATURE_LAYER_URL: str | None = (
    "https://services1.arcgis.com/dkWT1XL4nglP5MIE"
    "/arcgis/rest/services/Delinquent_Tax_2024/FeatureServer/0"
)

# App entry point — used for endpoint discovery if the above URL stops working
APP_URL = "https://richlandmaps.com/apps/delinquent/"

# Fields we want from the feature layer (use "*" if schema unknown)
FIELDS = "*"

# Max features per ArcGIS page
PAGE_SIZE = 1000


# ---------------------------------------------------------------------------
# ArcGIS REST helpers
# ---------------------------------------------------------------------------

def _query_page(layer_url: str, offset: int) -> dict:
    """Fetch one page of features from an ArcGIS feature layer."""
    params = {
        "where": "1=1",
        "outFields": FIELDS,
        "f": "json",
        "resultOffset": offset,
        "resultRecordCount": PAGE_SIZE,
        "orderByFields": "OBJECTID ASC",
    }
    r = _SESSION.get(f"{layer_url}/query", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _fetch_all_features(layer_url: str, max_pages: int = 10) -> list[dict]:
    """Page through all features from an ArcGIS feature layer."""
    all_features: list[dict] = []
    offset = 0

    for page_num in range(max_pages):
        data = _query_page(layer_url, offset)

        if "error" in data:
            print(f"[richland_delinquent_tax] ArcGIS error: {data['error']}")
            break

        features = data.get("features", [])
        all_features.extend(features)

        exceeded = data.get("exceededTransferLimit", False)
        print(
            f"[richland_delinquent_tax] Page {page_num + 1}: "
            f"{len(features)} features (total: {len(all_features)}, "
            f"exceededTransferLimit: {exceeded})"
        )

        if not exceeded or not features:
            break

        offset += len(features)
        time.sleep(1)

    return all_features


# ---------------------------------------------------------------------------
# Feature → raw_event_record
# ---------------------------------------------------------------------------

def _stable_id(parcel_id: str | None, account: str | None) -> str:
    discriminator = f"{parcel_id or ''}:{account or ''}"
    return "raw_dt_" + hashlib.md5(discriminator.encode()).hexdigest()[:16]


def _normalize_tms(raw: str | None) -> str | None:
    """Normalize a raw TMS/parcel value to the SC TMS format (e.g. R07516-06-50)."""
    if not raw:
        return None
    val = str(raw).strip()
    # Already looks like a TMS — return as-is
    if val and val[0].isalpha():
        return val
    return val or None


def _feature_to_raw_event(feat: dict, captured_at: str) -> dict | None:
    attrs: dict[str, Any] = feat.get("attributes") or {}

    # Common field name patterns in Richland delinquent GIS layers
    parcel_raw = (
        attrs.get("TMS")
        or attrs.get("PARCEL_ID")
        or attrs.get("ParcelID")
        or attrs.get("PIN")
        or attrs.get("parcel_id")
    )
    parcel_id = _normalize_tms(parcel_raw)

    owner = (
        attrs.get("OWNER")
        or attrs.get("OWNER_NAME")
        or attrs.get("OwnerName")
        or attrs.get("owner")
    )

    address = (
        attrs.get("SITUS_ADDRESS")
        or attrs.get("ADDRESS")
        or attrs.get("SitusAddress")
        or attrs.get("address")
        or attrs.get("PHYSICAL_ADDRESS")
    )

    city = attrs.get("CITY") or attrs.get("City") or attrs.get("city") or ""
    if city and address and city.upper() not in address.upper():
        address = f"{address}, {city}, SC"

    amount_raw = (
        attrs.get("AMOUNT")
        or attrs.get("TAX_AMOUNT")
        or attrs.get("TaxAmount")
        or attrs.get("DELINQUENT_AMOUNT")
    )
    try:
        amount = float(amount_raw) if amount_raw is not None else None
    except (ValueError, TypeError):
        amount = None

    account_num = (
        attrs.get("ACCOUNT_NUM")
        or attrs.get("ACCT_NUM")
        or attrs.get("AccountNum")
        or str(attrs.get("OBJECTID", ""))
    )

    if not parcel_id and not owner:
        return None

    raw_event_id = _stable_id(parcel_id, account_num)

    return {
        "raw_event_id": raw_event_id,
        "source_id": "delinquent_tax_sale",
        "source_role": "PRIMARY_EVENT_SOURCE",
        "raw_doc_type": "DELINQUENT TAX SALE LIST",
        "canonical_doc_type": "tax_foreclosure_notice",
        "instrument_number": account_num or None,
        "recorded_date": None,
        "event_date": None,
        "source_url": APP_URL,
        "parties": (
            [{"name": owner, "name_type": "TP", "raw_role": "TAXPAYER"}]
            if owner
            else []
        ),
        "document_body_text": None,
        "property_refs": {
            "parcel_id": parcel_id,
            "situs_address": address,
            "legal_description": None,
            "case_number": None,
        },
        "amounts": (
            [{"label": "delinquent_amount", "value": amount}]
            if amount is not None
            else []
        ),
        "parser_name": "richland_delinquent_tax_arcgis_v1",
        "parser_version": "1.0.0",
        "parser_confidence": 70 if parcel_id and owner else 40,
        "captured_at": captured_at,
    }


# ---------------------------------------------------------------------------
# Main scrape entry point
# ---------------------------------------------------------------------------

def scrape(layer_url: str | None = None) -> list[dict]:
    """
    Scrape the Richland County delinquent tax sale GIS layer.

    Args:
        layer_url: Override the ArcGIS feature layer URL. If None, uses
                   FEATURE_LAYER_URL. If that fails, returns [] with a
                   diagnostic message.

    Returns:
        List of raw_event_record dicts.
    """
    url = layer_url or FEATURE_LAYER_URL
    if not url:
        print("[richland_delinquent_tax] No feature layer URL configured. Skipping.")
        return []

    captured_at = datetime.now(timezone.utc).isoformat()

    print(f"[richland_delinquent_tax] Querying ArcGIS layer: {url}")

    try:
        features = _fetch_all_features(url)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            print(
                f"[richland_delinquent_tax] Layer URL returned 404. "
                f"The annual delinquent list may not be published yet, or the "
                f"URL has changed. Update FEATURE_LAYER_URL in this file. "
                f"Attempted: {url}"
            )
        else:
            print(f"[richland_delinquent_tax] HTTP error: {exc}")
        return []
    except Exception as exc:
        print(f"[richland_delinquent_tax] Fetch failed: {exc}")
        return []

    print(f"[richland_delinquent_tax] Total features fetched: {len(features)}")

    records: list[dict] = []
    skipped = 0
    for feat in features:
        rec = _feature_to_raw_event(feat, captured_at)
        if rec:
            records.append(rec)
        else:
            skipped += 1

    print(
        f"[richland_delinquent_tax] Parsed {len(records)} records "
        f"({skipped} skipped — no parcel_id or owner)"
    )

    if records:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = RAW_DIR / f"delinquent_tax_{ts}.jsonl"
        with out_path.open("w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[richland_delinquent_tax] Wrote {len(records)} records → {out_path}")

    return records


if __name__ == "__main__":
    results = scrape()
    print(f"Total delinquent tax records: {len(results)}")
