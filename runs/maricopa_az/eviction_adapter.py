"""
Maricopa County Justice Court Evictions — JSONL → raw_event_record converter.

Converts records from data/raw/justice_court_evictions.jsonl (written by
scrapers/justice_court_evictions_maricopa.py) into raw_event_records for the
staged pipeline.

=== Data-quality constraints ===

The justice court portal search results listing provides:
  case_number      — court case identifier ✓
  party_name       — one party name from the case ✓ (role unknown)
  case_detail_url  — link to CaseInfo.aspx detail page ✓

The following are NOT available from the listing:
  plaintiff        — landlord/property owner (NOT determinable from listing)
  defendant        — tenant (NOT determinable from listing)
  property_address — not in listing; needs detail-page parse
  filing_date      — not in listing; needs detail-page parse
  case_type        — not in listing; cannot confirm FED vs TR/CR/etc.

=== §17 routing ===

The eviction_filing §17 rule expects a PL (plaintiff / landlord) party as the
lead subject. Because party role is indeterminate from the listing, party_name
is tagged as name_type "OTHER" — the engine finds no PL, and routes every
record to REVIEW_REQUIRED "owner_not_on_document". This is correct per
requirement 6 (do not treat tenants as property owners unless confirmed).

With approve_needs_review=True, REVIEW_REQUIRED records still appear in the
dashboard as eviction leads for operator review.

=== Multi-signal stacking ===

Multi-signal stacking with NOTS / Treasurer is NOT possible from listing data
because no property address or APN is available. property_refs.parcel_id is
None for all eviction records → leads are UNRESOLVED → no APN aggregation key.

=== Future enhancement ===

Follow case_detail_url (CaseInfo.aspx) to parse:
  - plaintiff / plaintiff role → enables PL tagging → §17 resolves debtor
  - property address → enables APNResolver.resolve_name() lookup → enables
    parcel enrichment and multi-signal stacking with NOTS/Treasurer

=== Fetch strategy ===

The scraper requires Playwright (Google reCAPTCHA v3 on the portal) and a
search term. Use business-name search with known landlord entities:
  python scrapers/justice_court_evictions_maricopa.py --business-name "GREYSTAR" ...
Or fetch_evictions.cmd with operator-supplied landlord business names.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).parent
_REPO_ROOT = _THIS_DIR.parents[1]
for _p in (str(_REPO_ROOT), str(_THIS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# canonical_doc_type for all eviction records from this portal
_CANONICAL_DOC_TYPE = "eviction_filing"


# ---------------------------------------------------------------------------
# JSONL loader
# ---------------------------------------------------------------------------


def load_eviction_jsonl(path: Path, max_records: int) -> list[dict]:
    """Load up to max_records ACTIVE (non-DISAPPEARED) eviction records."""
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
            if len(records) >= max_records:
                break
    return records


# ---------------------------------------------------------------------------
# Converter
# ---------------------------------------------------------------------------


def build_eviction_raw_events(
    raw_records: list[dict],
) -> list[dict]:
    """Convert eviction records to raw_event_records.

    Party role is unknown from listing data → party_name tagged as OTHER.
    §17 eviction_filing expects PL (landlord); finding none, it routes every
    record to REVIEW_REQUIRED "owner_not_on_document". This is the correct
    behavior given the data available (requirement 6 / §17.K).

    No assessor lookup: property address and APN are not available from the
    listing. property_refs.parcel_id is None for all records.

    Returns list of raw_event_records (no parcel_by_apn — all leads UNRESOLVED).
    """
    raw_events: list[dict] = []

    for i, raw_rec in enumerate(raw_records):
        payload = raw_rec.get("raw_payload") or {}
        case_number = (payload.get("case_number") or "").strip() or None
        party_name = (payload.get("party_name") or "").strip() or None
        filing_date = (payload.get("filing_date") or "").strip() or None
        source_url = raw_rec.get("source_url") or (payload.get("case_detail_url") or "")

        # Tag party as OTHER — role (plaintiff/defendant) not determinable from
        # listing data. Per §6 of the task: do NOT assume landlord role.
        # §17 eviction_filing finds no PL → REVIEW_REQUIRED "owner_not_on_document".
        parties: list[dict] = []
        if party_name:
            parties = [{"name": party_name, "name_type": "OTHER"}]

        print(f"  [EVICT {i+1}] case={case_number}  party_len={len(party_name or '')}  role=OTHER→REVIEW_REQUIRED")

        raw_event: dict = {
            "raw_event_id": raw_rec["raw_record_id"],
            "source_id": "justice_court_evictions",
            "source_role": "PRIMARY_EVENT_SOURCE",
            "raw_doc_type": payload.get("case_type"),
            "canonical_doc_type": _CANONICAL_DOC_TYPE,
            "instrument_number": None,
            "recorded_date": filing_date,
            "source_url": source_url or "https://justicecourts.maricopa.gov/app/courtrecords",
            "parties": parties,
            "property_refs": {
                "parcel_id": None,
                "situs_address": None,
                "legal_description": None,
                "case_number": case_number,
            },
            "document_body_text": None,
            "parser_confidence": raw_rec.get("parser_confidence"),
            "captured_at": raw_rec.get("source_fetched_at"),
        }
        raw_events.append(raw_event)

    print(f"  Eviction records: {len(raw_events)} → all route to REVIEW_REQUIRED (no PL party available)")
    return raw_events
