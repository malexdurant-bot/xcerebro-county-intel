"""
Shelby County Trustee Tax Sale — JSONL -> raw_event_record converter.

Reads data/raw/trustee_tax_sale_shelby.jsonl (written by
scrapers/trustee_tax_sale_shelby.py) and converts each active record
into a raw_event_record conforming to the framework schema.

All tax sale properties map to canonical_doc_type "tax_sale_certificate".
In Tennessee, the county files a lawsuit against delinquent taxpayers and
then auctions the property.  Being listed in the TaxSaleExtract.csv means:
  1. The property owner is delinquent on property taxes.
  2. A lawsuit has been filed (Delinquent Realty Lawsuit, filed 2026-03-31).
  3. The property is scheduled for auction (ZeusAuction.com).

Owner name comes from the ArcGIS CurrentParcels service (via ParcelResolver),
not from the CSV itself.  If the ArcGIS lookup fails for a parcel, parties
is empty and §17 routes the record to REVIEW_REQUIRED "owner_not_on_document".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

_THIS_DIR = Path(__file__).parent
_REPO_ROOT = _THIS_DIR.parents[1]
for _p in (str(_REPO_ROOT), str(_THIS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from parcel_resolver import ParcelResolver  # noqa: E402 — local module

_CANONICAL_DOC_TYPE = "tax_sale_certificate"
_SOURCE_ID = "trustee_tax_sale"
_SOURCE_URL_BASE = "http://gis.register.shelby.tn.us/?parcelid="


# ---------------------------------------------------------------------------
# JSONL loader
# ---------------------------------------------------------------------------

def load_tax_sale_jsonl(path: Path, max_records: Optional[int] = None) -> list[dict]:
    """Load ACTIVE (non-DISAPPEARED) tax sale records from JSONL."""
    records: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("change_status") == "DISAPPEARED":
                continue
            records.append(rec)
            if max_records is not None and len(records) >= max_records:
                break
    return records


# ---------------------------------------------------------------------------
# Converter
# ---------------------------------------------------------------------------

def build_tax_sale_raw_events(
    raw_records: list[dict],
    resolver: ParcelResolver,
    verbose: bool = False,
) -> tuple[list[dict], dict[str, dict]]:
    """Convert tax sale records to raw_event_records + parcel_by_id mapping.

    For each record:
      1. Extract PARCELID from raw_payload.
      2. Query ArcGIS via resolver -> owner name + parcel dict.
      3. If resolved: add TP-tagged party; store parcel dict for enrichment.
      4. Build raw_event_record (parties empty if parcel not found).

    Returns:
        (raw_events, parcel_by_parcelid)
    """
    raw_events: list[dict] = []
    parcel_by_id: dict[str, dict] = {}

    arcgis_hit = arcgis_miss = 0

    for i, raw_rec in enumerate(raw_records):
        payload = raw_rec.get("raw_payload") or {}
        parcel_id = (payload.get("parcel_id") or "").strip()
        situs_address = (payload.get("situs_address") or "").strip() or None
        tax_sale_code = payload.get("tax_sale_code") or None
        gis_link = payload.get("gis_link") or None

        # Build source URL for this parcel
        source_url = gis_link or (
            _SOURCE_URL_BASE + parcel_id if parcel_id else "https://www.shelbycountytrustee.com/191/Tax-Sale-Schedule"
        )

        captured_at = raw_rec.get("source_fetched_at")

        if verbose:
            print(f"  [TAX_SALE {i+1}] {parcel_id}", end="")

        # ArcGIS lookup by PARCELID
        parcel_dict: Optional[dict] = None
        parties: list[dict] = []
        resolved_parcel_id = parcel_id or None

        if parcel_id:
            parcel_dict = resolver.resolve_parcel_dict(parcel_id)

        if parcel_dict:
            owner_name = (parcel_dict.get("owner_name") or "").strip()
            if owner_name:
                parties = [{"name": owner_name, "name_type": "TP"}]
                arcgis_hit += 1
                if verbose:
                    print(f"  -> {owner_name}", end="")
            parcel_by_id[parcel_id] = parcel_dict

            # Use parcel_id from ArcGIS if it returned a clean ID
            arcgis_parcel_id = (parcel_dict.get("parcel_id") or "").strip()
            if arcgis_parcel_id:
                resolved_parcel_id = arcgis_parcel_id
        else:
            arcgis_miss += 1
            if verbose:
                print("  -> miss", end="")

        if verbose:
            print()

        raw_event: dict = {
            "raw_event_id": raw_rec["raw_record_id"],
            "source_id": _SOURCE_ID,
            "source_role": "PRIMARY_EVENT_SOURCE",
            "raw_doc_type": tax_sale_code,
            "canonical_doc_type": _CANONICAL_DOC_TYPE,
            "instrument_number": None,
            "recorded_date": None,
            "source_url": source_url,
            "parties": parties,
            "property_refs": {
                "parcel_id": resolved_parcel_id,
                "situs_address": situs_address,
                "legal_description": None,
                "case_number": None,
            },
            "document_body_text": None,
            "parser_confidence": raw_rec.get("parser_confidence", "CONFIRMED"),
            "captured_at": captured_at,
        }
        raw_events.append(raw_event)

    print(f"  ArcGIS parcel hits: {arcgis_hit}/{len(raw_records)}, misses: {arcgis_miss}")
    return raw_events, parcel_by_id
