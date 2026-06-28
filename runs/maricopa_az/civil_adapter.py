"""
Maricopa County Superior Court Civil Cases — JSONL → raw_event_record converter.

Converts records from data/raw/superior_court_civil.jsonl (written by
scrapers/superior_court_civil_maricopa.py) into raw_event_records for the
staged pipeline.

=== Data-quality constraints ===

The superior court portal search results listing provides:
  case_number      — court case identifier ✓
  party_name       — one party name ✓ (role and identity may be protected)
  case_detail_url  — link to detail page ✓

The following are NOT available from the listing:
  plaintiff        — NOT determinable from listing (role unknown)
  defendant        — NOT determinable from listing (role unknown)
  property_address — not in listing; requires detail-page parse
  filing_date      — not in listing; requires detail-page parse
  case_type        — field is None from listing; inferred from case_number prefix

=== Case number prefix → canonical_doc_type ===

Maricopa Superior Court case number prefixes encode the division/case type:
  LP  → lis_pendens     (Lis Pendens — property-attached instrument)
  FC  → judgment_lien   (Judicial Foreclosure judgment — property-attached)
  CV  → judgment_lien   (Civil Judgment / Abstract of Judgment)
  MC  → judgment_lien   (Miscellaneous Civil — civil judgment bucket)
  SC  → judgment_lien   (Small Claims judgment)
  TR/CR/PB/DR/DO/MH → noise (traffic, criminal, probate, etc. — skip)
  unknown prefix      → judgment_lien with review_required classification

=== §17 routing ===

§17 rules for lis_pendens and judgment_lien both expect a DF (defendant) party.
Because party role is indeterminate from the listing, party_name is tagged as
name_type "OTHER" — the engine finds no DF and routes every record to
REVIEW_REQUIRED "owner_not_on_document". This is correct per requirement 6
(do not infer ownership from party name without confirmed role).

Portal records may also carry "Information is protected and will not be
displayed" as the party_name. These are emitted with parties=[] (no usable
party), which also routes to REVIEW_REQUIRED.

=== Multi-signal stacking ===

Not possible from listing data — no property address or APN is available.
property_refs.parcel_id is None for all civil records.

=== Future enhancement ===

Follow case_detail_url to parse:
  - plaintiff / defendant role → enables DF tagging → §17 resolves debtor
  - property address → enables APNResolver lookup → enables parcel enrichment
    and multi-signal stacking with NOTS / Treasurer
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).parent
_REPO_ROOT = _THIS_DIR.parents[1]
for _p in (str(_REPO_ROOT), str(_THIS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Case number prefix mapping
# ---------------------------------------------------------------------------

_PREFIX_TO_DOC_TYPE: dict[str, str] = {
    "LP": "lis_pendens",
    "FC": "judgment_lien",
    "CV": "judgment_lien",
    "MC": "judgment_lien",
    "SC": "judgment_lien",
}

_NOISE_PREFIXES: frozenset[str] = frozenset(["TR", "CR", "PB", "DR", "DO", "NV", "MH"])

_CASE_PREFIX_RE = re.compile(r"^([A-Z]+)", re.IGNORECASE)

# Portal privacy placeholder — not a real party name
_PROTECTED_PARTY_MARKER = "information is protected"


def _infer_doc_type(case_number: str) -> tuple[str | None, str]:
    """Return (canonical_doc_type, classification) from case_number prefix.

    classification: 'usable' | 'review_required' | 'noise' | 'unsupported'
    """
    if not case_number:
        return None, "unsupported"
    m = _CASE_PREFIX_RE.match(case_number.strip().upper())
    if not m:
        return None, "unsupported"
    prefix = m.group(1)
    if prefix in _NOISE_PREFIXES:
        return None, "noise"
    doc_type = _PREFIX_TO_DOC_TYPE.get(prefix)
    if doc_type:
        return doc_type, "usable"
    # Unknown but non-noise prefix — include as judgment_lien under review
    return "judgment_lien", "review_required"


def _is_protected_party(name: str) -> bool:
    """Return True if the portal replaced party_name with a privacy notice."""
    return _PROTECTED_PARTY_MARKER in name.lower()


# ---------------------------------------------------------------------------
# JSONL loader
# ---------------------------------------------------------------------------


def load_civil_jsonl(path: Path, max_records: int) -> list[dict]:
    """Load up to max_records ACTIVE (non-DISAPPEARED) civil records."""
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


def build_civil_raw_events(raw_records: list[dict], verbose: bool = False) -> list[dict]:
    """Convert civil case records to raw_event_records.

    Case type is inferred from the case_number prefix (LP/FC/CV/MC/SC →
    lis_pendens or judgment_lien). Party role is unknown → tagged as OTHER.
    §17 finds no DF → REVIEW_REQUIRED "owner_not_on_document" for all records.
    Portal privacy placeholders are stripped (parties=[]).

    No assessor lookup: property address and APN are not available from
    the listing. property_refs.parcel_id is None for all records.

    Returns list of raw_event_records (no parcel_by_apn — all UNRESOLVED).
    """
    raw_events: list[dict] = []
    class_counts: dict[str, int] = {}

    for i, raw_rec in enumerate(raw_records):
        payload = raw_rec.get("raw_payload") or {}
        case_number = (payload.get("case_number") or "").strip() or None
        party_name = (payload.get("party_name") or "").strip() or None
        filing_date = (payload.get("filing_date") or "").strip() or None
        source_url = (
            raw_rec.get("source_url")
            or payload.get("case_detail_url")
            or "https://www.superiorcourt.maricopa.gov/docket/civilcourtcases/casesearch.asp"
        )

        canonical_doc_type, classification = _infer_doc_type(case_number or "")
        class_counts[classification] = class_counts.get(classification, 0) + 1

        if classification == "noise":
            if verbose:
                print(f"  [CIVIL {i+1}] classification=NOISE → skip")
            continue

        # Build party list — skip portal privacy placeholders
        parties: list[dict] = []
        if party_name and not _is_protected_party(party_name):
            parties = [{"name": party_name, "name_type": "OTHER"}]
            party_tag = "OTHER→REVIEW_REQUIRED"
        elif party_name and _is_protected_party(party_name):
            party_tag = "PROTECTED→parties=[]→REVIEW_REQUIRED"
        else:
            party_tag = "no_party→REVIEW_REQUIRED"

        if verbose:
            print(
                f"  [CIVIL {i+1}] doc_type={canonical_doc_type}  "
                f"class={classification}  party={party_tag}"
            )

        raw_event: dict = {
            "raw_event_id": raw_rec["raw_record_id"],
            "source_id": "superior_court_civil",
            "source_role": "PRIMARY_EVENT_SOURCE",
            "raw_doc_type": payload.get("case_type"),
            "canonical_doc_type": canonical_doc_type,
            "instrument_number": None,
            "recorded_date": filing_date,
            "source_url": source_url,
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

    usable = class_counts.get("usable", 0)
    review = class_counts.get("review_required", 0)
    noise = class_counts.get("noise", 0)
    unsup = class_counts.get("unsupported", 0)
    print(
        f"  Civil records: {len(raw_events)} emitted "
        f"(usable={usable}, review_required={review}, noise={noise}, unsupported={unsup}) "
        f"→ all route to REVIEW_REQUIRED (no DF party / no APN available)"
    )
    return raw_events
