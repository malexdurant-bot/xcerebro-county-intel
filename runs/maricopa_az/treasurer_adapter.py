"""
Maricopa County Treasurer — JSONL → raw_event_record converter.

Reads data/raw/treasurer_tax_lien.jsonl (written by
scrapers/treasurer_tax_lien_maricopa.py) and converts each active record
into a raw_event_record conforming to the framework's schema, using the
assessor ArcGIS MapServer to resolve owner names from APNs.

All three treasurer layers map to canonical_doc_type "tax_sale_certificate":
  tax_lien_bad_investment  — lien sold at auction; buyer defaulted
  tax_lien_unsold          — lien offered but not sold; county holds it
  tax_delinquent_parcel    — delinquent pre-auction (approaching lien sale)

The taxpayer (TP) party comes from an assessor lookup by APN_DASH. If the
assessor returns no result, parties is empty and §17 routes the record to
REVIEW_REQUIRED "owner_not_on_document" — the lead still appears when
approve_needs_review=True.

Assessor lookups are cached through the shared APNResolver instance, so APN
hits from the NOTS name-resolution pass and the treasurer APN-resolution pass
share the same on-disk cache.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable, Optional

_THIS_DIR = Path(__file__).parent
_REPO_ROOT = _THIS_DIR.parents[1]
for _p in (str(_REPO_ROOT), str(_THIS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from apn_resolver import APNResolver  # noqa: E402 — local module

# canonical_doc_type for every treasurer layer
_CANONICAL_DOC_TYPE = "tax_sale_certificate"

# layer_name → human-readable label for log output
_LAYER_LABEL: dict[str, str] = {
    "BadInvestment": "bad-investment lien",
    "UnsoldLien": "unsold lien",
    "DelinquentTax": "delinquent parcel",
}


# ---------------------------------------------------------------------------
# JSONL loader
# ---------------------------------------------------------------------------


def load_treasurer_jsonl(path: Path, max_records: Optional[int] = None) -> list[dict]:
    """Load ACTIVE (non-DISAPPEARED) treasurer records. Pass max_records to cap."""
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


def build_treasurer_raw_events(
    raw_records: list[dict],
    resolver: APNResolver,
    parcel_dict_from_attrs: Callable[[dict], dict],
    verbose: bool = False,
) -> tuple[list[dict], dict[str, dict]]:
    """Convert treasurer records to raw_event_records + parcel_by_apn mapping.

    For each record:
      1. Query assessor by APN_DASH (falling back to APN) → owner name + enrichment.
      2. If resolved: add TP-tagged party; store parcel dict for enrichment_provider.
      3. Build raw_event_record (parties empty if APN not found in assessor).

    Returns (raw_events, parcel_by_apn).
    """
    raw_events: list[dict] = []
    parcel_by_apn: dict[str, dict] = {}

    assessor_hit = assessor_miss = 0

    for i, raw_rec in enumerate(raw_records):
        payload = raw_rec.get("raw_payload") or {}
        apn = (payload.get("apn") or "").strip()
        apn_dash = (payload.get("apn_dash") or "").strip()
        layer_name = payload.get("layer_name", "?")
        layer_label = _LAYER_LABEL.get(layer_name, layer_name)
        deed_number = (payload.get("deed_number") or "").strip() or None
        source_url = raw_rec.get("source_url") or f"https://treasurer.maricopa.gov/Parcel/Index/{apn}"
        captured_at = raw_rec.get("source_fetched_at")
        apn_display = apn_dash or apn or "???"

        if verbose:
            print(f"  [TREAS {i+1}] {layer_label}", end="")

        # Assessor lookup by APN (direct — no name guessing)
        attrs: Optional[dict] = resolver.resolve_by_apn(apn_dash=apn_dash, apn=apn)

        parties: list[dict] = []
        resolved_apn: Optional[str] = apn_dash or apn or None

        if attrs:
            owner_name = (attrs.get("OWNER_NAME") or "").strip()
            if owner_name:
                parties = [{"name": owner_name, "name_type": "TP"}]
                assessor_hit += 1
                if verbose:
                    print("  → owner resolved", end="")

            # Use APN from assessor for consistency
            assessor_apn = (attrs.get("APN_DASH") or attrs.get("APN") or "").strip()
            if assessor_apn:
                resolved_apn = assessor_apn

            parcel = parcel_dict_from_attrs(attrs)
            if resolved_apn:
                parcel_by_apn[resolved_apn] = parcel
        else:
            assessor_miss += 1
            if verbose:
                print("  → assessor miss", end="")

        if verbose:
            print()

        raw_event: dict = {
            "raw_event_id": raw_rec["raw_record_id"],
            "source_id": "treasurer_tax_lien",
            "source_role": "PRIMARY_EVENT_SOURCE",
            "raw_doc_type": payload.get("category_hint"),
            "canonical_doc_type": _CANONICAL_DOC_TYPE,
            "instrument_number": deed_number,
            "recorded_date": None,
            "source_url": source_url,
            "parties": parties,
            "property_refs": {
                "parcel_id": resolved_apn,
                "situs_address": (payload.get("situs_address") or "").strip() or None,
                "legal_description": None,
                "case_number": None,
            },
            "document_body_text": None,
            "parser_confidence": raw_rec.get("parser_confidence"),
            "captured_at": captured_at,
        }
        raw_events.append(raw_event)

    print(f"  Assessor hits: {assessor_hit}/{len(raw_records)}, misses: {assessor_miss}")
    return raw_events, parcel_by_apn
