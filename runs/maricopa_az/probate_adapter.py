"""
Maricopa County Superior Court Probate Cases — JSONL → raw_event_record converter.

Converts records from data/raw/superior_court_probate.jsonl (base listing) and
optionally data/raw/superior_court_probate_detail.jsonl (enriched detail) into
raw_event_records for the staged pipeline.

=== Data-quality constraints — listing layer ===

The superior court probate portal search results listing provides:
  case_number      — court case identifier ✓  (e.g. PB2024-001234)
  party_text       — one party name from the listing ✓ (role ambiguous)
  case_detail_url  — link to detail page ✓

Missing from listing: filing_date, case_type, party role, property address, APN.

=== Data-quality constraints — detail layer ===

When data/raw/superior_court_probate_detail.jsonl is present, the adapter merges
enriched fields for each record:

  case_type_raw     — "Probate", "Guardianship", "Conservatorship", etc.
  decedent_name     — party with Relationship == "Decedent"
  petitioner_name   — party with Relationship == "Petitioner"
  personal_representative — party with Relationship in PR/Executor/Administrator
  earliest_filing_date    — minimum filing date across Case Documents
  case_subtype_inferred   — letters_testamentary / letters_of_administration /
                            determination_of_heirship | muniment_of_title
  is_estate_case    — True if decedent found AND not a guardianship/conservatorship
  is_noise_case     — True for Guardianship, Conservatorship, Mental Health

=== Parcel matching — CONFIRMED only ===

When parcel_match_by_id is provided (from local_parcel_index + probate_parcel_matcher),
the adapter populates property_refs.parcel_id exclusively for
CONFIRMED_DECEDENT_OWNER_MATCH results. All other confidence levels
(POSSIBLE, AMBIGUOUS, NO_OWNER_MATCH) leave parcel_id None.

Match metadata (confidence, strategy, candidate_count) is attached to each
raw_event as parcel_match for review without containing PII.

  CONFIRMED → parcel_id set; lead can participate in APN stacking
  POSSIBLE  → parcel_id None; parcel_match has confidence + strategy for review
  AMBIGUOUS → parcel_id None; parcel_match has candidate_count for review
  NO_MATCH  → parcel_id None; parcel_match has reject_reason

Petitioner, attorney, and filer names are NEVER used to create parcel links.
This guard is enforced in probate_parcel_matcher.match_record().

=== Canonical doc type mapping ===

All PB-prefix records use letters_testamentary as default when case subtype is
unknown. When detail is available and case_subtype_inferred is set, that value
is used instead (more specific and correct).

  PB + detail subtype → letters_testamentary | letters_of_administration |
                         determination_of_heirship | muniment_of_title
  PB (no detail)      → letters_testamentary (default)
  Non-PB prefix       → noise → skip

=== §17 routing ===

The framework's §17 probate rule uses debtor_source "DOCUMENT_BODY". When
detail is available and a decedent_name is confirmed, the adapter synthesizes:
    document_body_text = "DECEDENT: {decedent_name}"
This enables the DOCUMENT_BODY extractor to resolve the debtor.

Without a confirmed decedent (or with no detail file), document_body_text=None
→ DOCUMENT_BODY extraction fails → REVIEW_REQUIRED "owner_not_on_document".

=== Noise filtering ===

Guardianship and Conservatorship cases are NOT property-distress signals. When
detail confirms is_noise_case=True, the record is skipped entirely (not emitted
as a raw_event). This prevents guardianship cases from appearing in the dashboard.
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

from probate_parcel_matcher import (  # noqa: E402 — local module
    MATCH_CONFIRMED,
    MATCH_POSSIBLE,
    MATCH_AMBIGUOUS,
    MATCH_NONE,
)

# ---------------------------------------------------------------------------
# Case number prefix mapping
# ---------------------------------------------------------------------------

_CASE_PREFIX_RE = re.compile(r"^([A-Z]+)", re.IGNORECASE)

_PROTECTED_PARTY_PATTERNS = (
    re.compile(r"\bminor\b.*\bDOB\b", re.IGNORECASE),
    re.compile(r"information is protected", re.IGNORECASE),
)

_DEFAULT_CANONICAL_DOC_TYPE = "letters_testamentary"
_VALID_SUBTYPES = frozenset([
    "letters_testamentary",
    "letters_of_administration",
    "determination_of_heirship",
    "muniment_of_title",
])


def _is_protected_party(name: str) -> bool:
    return any(pat.search(name) for pat in _PROTECTED_PARTY_PATTERNS)


def _infer_doc_type_from_prefix(case_number: str) -> tuple[str | None, str]:
    """Return (canonical_doc_type, classification) from case_number prefix."""
    if not case_number:
        return None, "unsupported"
    m = _CASE_PREFIX_RE.match(case_number.strip().upper())
    if not m:
        return None, "unsupported"
    prefix = m.group(1)
    if prefix == "PB":
        return _DEFAULT_CANONICAL_DOC_TYPE, "usable"
    return None, "noise"


# ---------------------------------------------------------------------------
# JSONL loaders
# ---------------------------------------------------------------------------


def load_probate_jsonl(path: Path, max_records: int) -> list[dict]:
    """Load up to max_records ACTIVE (non-DISAPPEARED) base listing records."""
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


def load_probate_detail_jsonl(path: Path) -> dict[str, dict]:
    """Load all detail records, indexed by raw_record_id."""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as fh:
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
                out[rid] = rec.get("detail") or {}
    return out


# ---------------------------------------------------------------------------
# Converter
# ---------------------------------------------------------------------------


def build_probate_raw_events(
    raw_records: list[dict],
    detail_by_id: dict[str, dict] | None = None,
    parcel_match_by_id: dict[str, dict] | None = None,
    verbose: bool = False,
) -> list[dict]:
    """Convert probate case records to raw_event_records.

    When detail_by_id is provided (from superior_court_probate_detail.jsonl):
      - Noise cases (Guardianship/Conservatorship) are skipped entirely.
      - Confirmed decedent name populates document_body_text for §17 resolution.
      - Confirmed case subtype replaces the default canonical_doc_type.
      - Earliest filing date populates recorded_date.

    When parcel_match_by_id is provided (from LocalParcelIndex + probate_parcel_matcher):
      - CONFIRMED_DECEDENT_OWNER_MATCH: property_refs.parcel_id is populated.
      - POSSIBLE / AMBIGUOUS / NO_OWNER_MATCH: parcel_id remains None.
      - All cases: parcel_match metadata (confidence, strategy, candidate_count)
        is attached to the raw_event for review purposes only.
      - Petitioner/attorney names NEVER populate parcel_id (enforced in matcher).

    Without detail: all records route to REVIEW_REQUIRED (DOCUMENT_BODY=None).

    Returns list of raw_event_records.
    """
    if detail_by_id is None:
        detail_by_id = {}
    if parcel_match_by_id is None:
        parcel_match_by_id = {}

    raw_events: list[dict] = []
    class_counts: dict[str, int] = {
        "usable": 0,
        "noise_skipped": 0,
        "review_required_no_detail": 0,
        "review_required_no_decedent": 0,
        "unsupported": 0,
        "parcel_confirmed": 0,
        "parcel_possible": 0,
        "parcel_ambiguous": 0,
    }

    for i, raw_rec in enumerate(raw_records):
        payload = raw_rec.get("raw_payload") or {}
        case_number = (payload.get("case_number") or "").strip() or None
        party_name = (
            payload.get("decedent_name")
            or payload.get("petitioner_name")
            or ""
        ).strip() or None
        source_url = (
            raw_rec.get("source_url")
            or payload.get("case_detail_url")
            or "https://www.superiorcourt.maricopa.gov/docket/ProbateCourtCases/caseSearch.asp"
        )

        canonical_doc_type, classification = _infer_doc_type_from_prefix(case_number or "")

        if classification in ("noise", "unsupported"):
            class_counts["unsupported" if classification == "unsupported" else "noise_skipped"] += 1
            if verbose:
                print(f"  [PROBATE {i+1}] classification={classification.upper()} → skip")
            continue

        # Merge detail if available
        detail = detail_by_id.get(raw_rec.get("raw_record_id") or "")

        if detail:
            # Skip confirmed noise cases (Guardianship / Conservatorship)
            if detail.get("is_noise_case"):
                class_counts["noise_skipped"] += 1
                if verbose:
                    print(f"  [PROBATE {i+1}] case_type={detail.get('case_type_raw')!r}  NOISE → skip")
                continue

            # Use detail-confirmed subtype if available
            inferred_subtype = detail.get("case_subtype_inferred") or ""
            if inferred_subtype in _VALID_SUBTYPES:
                canonical_doc_type = inferred_subtype

            # Confirmed decedent → populate document_body_text for §17 DOCUMENT_BODY extraction
            decedent_name = detail.get("decedent_name") or ""
            document_body_text = f"DECEDENT: {decedent_name}" if decedent_name else None

            filing_date = detail.get("earliest_filing_date") or None

            if decedent_name:
                class_counts["usable"] += 1
                party_tag = f"DECEDENT confirmed (body text set)"
            else:
                class_counts["review_required_no_decedent"] += 1
                party_tag = f"no decedent in detail → REVIEW_REQUIRED"
        else:
            document_body_text = None
            filing_date = (payload.get("filing_date") or "").strip() or None
            class_counts["review_required_no_detail"] += 1
            party_tag = "no detail → REVIEW_REQUIRED"

        # Build structured party list from listing party_name (role still unknown)
        # Only used as supplemental context — §17 reads document_body_text for debtor
        parties: list[dict] = []
        if party_name and not _is_protected_party(party_name):
            parties = [{"name": party_name, "name_type": "OTHER"}]

        # Parcel match — only CONFIRMED populates parcel_id
        rid = raw_rec.get("raw_record_id") or ""
        match = parcel_match_by_id.get(rid)
        parcel_id: str | None = None
        situs_address: str | None = None
        parcel_match_meta: dict = {}

        if match:
            conf = match.get("match_confidence", MATCH_NONE)
            parcel_match_meta = {
                "match_confidence": conf,
                "strategy_used": match.get("strategy_used"),
                "candidate_count": match.get("candidate_count", 0),
                "reject_reason": match.get("reject_reason"),
            }
            if conf == MATCH_CONFIRMED:
                parcel_id = match.get("apn")
                situs_address = match.get("situs_address")
                class_counts["parcel_confirmed"] += 1
            elif conf == MATCH_POSSIBLE:
                class_counts["parcel_possible"] += 1
            elif conf == MATCH_AMBIGUOUS:
                class_counts["parcel_ambiguous"] += 1

        if verbose:
            print(
                f"  [PROBATE {i+1}] doc_type={canonical_doc_type}  {party_tag}"
                + (f"  parcel={parcel_match_meta.get('match_confidence', 'no_match')}" if match else "")
            )

        raw_event: dict = {
            "raw_event_id": raw_rec["raw_record_id"],
            "source_id": "superior_court_probate",
            "source_role": "PRIMARY_EVENT_SOURCE",
            "raw_doc_type": payload.get("case_type"),
            "canonical_doc_type": canonical_doc_type,
            "instrument_number": None,
            "recorded_date": filing_date,
            "source_url": source_url,
            "parties": parties,
            "property_refs": {
                "parcel_id": parcel_id,
                "situs_address": situs_address,
                "legal_description": None,
                "case_number": case_number,
            },
            "document_body_text": document_body_text,
            "parser_confidence": raw_rec.get("parser_confidence"),
            "captured_at": raw_rec.get("source_fetched_at"),
            "parcel_match": parcel_match_meta or None,
        }
        raw_events.append(raw_event)

    usable = class_counts["usable"]
    noise = class_counts["noise_skipped"]
    rr = class_counts["review_required_no_detail"] + class_counts["review_required_no_decedent"]
    unsup = class_counts["unsupported"]
    pc = class_counts["parcel_confirmed"]
    pp = class_counts["parcel_possible"]
    pa = class_counts["parcel_ambiguous"]
    print(
        f"  Probate records: {len(raw_events)} emitted "
        f"(usable={usable}, review_required={rr}, noise_skipped={noise}, unsupported={unsup})"
    )
    if parcel_match_by_id:
        print(
            f"  Parcel matches: confirmed={pc}, possible={pp}, ambiguous={pa}"
        )
    return raw_events
