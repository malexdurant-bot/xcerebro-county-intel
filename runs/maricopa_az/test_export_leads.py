"""
Unit tests for export_leads.py filter and dedup logic.

Verifies:
  - is_hot: score >= 80, ENRICHED, not REVIEW_REQUIRED
  - is_strong: 65 <= score < 80, ENRICHED, not REVIEW_REQUIRED
  - is_probate_confirmed_property: probate source + APN + ENRICHED
  - is_tax_foreclosure_overlap: both treasurer and recorder sources present
  - is_review_required: REVIEW_REQUIRED in lead_status or enrichment_status
  - _dedup_by_apn: highest-score row per APN survives
  - skiptrace_ready excludes REVIEW_REQUIRED and leads without APN
  - POSSIBLE/AMBIGUOUS probate parcel matches (no APN) never enter skiptrace or
    probate_confirmed_property exports

No file I/O. No network calls. No PII. All data synthetic.

Run:
    python -X utf8 runs/maricopa_az/test_export_leads.py
"""
from __future__ import annotations

import sys
from pathlib import Path

THIS_DIR = Path(__file__).parent
REPO_ROOT = THIS_DIR.parents[1]
for _p in (str(THIS_DIR), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from export_leads import (
    _dedup_by_apn,
    _flat_row,
    is_hot,
    is_probate_confirmed_property,
    is_review_required,
    is_skiptrace_ready,
    is_strong,
    is_tax_foreclosure_overlap,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_TS = "2026-06-28T00:00:00+00:00"


def _lead(
    score: int = 70,
    enrichment_status: str = "ENRICHED",
    lead_status: str = "APPROVED_FOR_DASHBOARD",
    source_ids: list | None = None,
    primary_parcel_id: str | None = "1234567890",
    patterns: list | None = None,
    attributes: list | None = None,
    tier: str = "Workable",
    owner_name: str = "SYNTHETIC OWNER",
) -> dict:
    return {
        "lead_id": f"lead_parcel_{primary_parcel_id or 'none'}",
        "score": score,
        "tier": tier,
        "lead_status": lead_status,
        "enrichment_status": enrichment_status,
        "primary_parcel_id": primary_parcel_id,
        "source_ids": source_ids if source_ids is not None else ["recorder_maricopa"],
        "patterns": patterns or ["nots"],
        "attributes": attributes or ["absentee"],
        "owner_name": owner_name,
        "stack_depth": len(source_ids if source_ids is not None else ["recorder_maricopa"]),
        "parcel_display": {
            "situs_address": "100 TEST ST",
            "situs_city": "PHOENIX",
            "situs_state": "AZ",
            "owner_mailing_address": "200 MAIL DR",
            "owner_mailing_city": "SCOTTSDALE",
            "owner_mailing_state": "AZ",
            "owner_mailing_zip": "85251",
        },
    }


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

_passed = _failed = 0


def _assert(cond: bool, msg: str) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {msg}")
    else:
        _failed += 1
        print(f"  FAIL  {msg}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_hot_leads_filter() -> None:
    """score >= 80, ENRICHED, not REVIEW_REQUIRED → hot."""
    _assert(is_hot(_lead(score=80)), "score=80 qualifies as hot")
    _assert(is_hot(_lead(score=95)), "score=95 qualifies as hot")
    _assert(not is_hot(_lead(score=79)), "score=79 is below hot threshold")
    _assert(not is_hot(_lead(score=85, enrichment_status="UNENRICHED")), "UNENRICHED not hot")
    _assert(not is_hot(_lead(score=85, lead_status="REVIEW_REQUIRED")), "REVIEW_REQUIRED not hot")
    _assert(not is_hot(_lead(score=0)), "score=0 not hot")


def test_strong_leads_filter() -> None:
    """65 <= score < 80, ENRICHED, not REVIEW_REQUIRED → strong."""
    _assert(is_strong(_lead(score=65)), "score=65 qualifies as strong")
    _assert(is_strong(_lead(score=79)), "score=79 qualifies as strong")
    _assert(not is_strong(_lead(score=80)), "score=80 is hot, not strong")
    _assert(not is_strong(_lead(score=64)), "score=64 below strong threshold")
    _assert(not is_strong(_lead(score=70, enrichment_status="UNENRICHED")), "UNENRICHED not strong")
    _assert(not is_strong(_lead(score=70, lead_status="REVIEW_REQUIRED")), "REVIEW_REQUIRED not strong")


def test_probate_confirmed_property_filter() -> None:
    """Probate source + confirmed APN + ENRICHED → probate_confirmed_property."""
    probate = _lead(
        source_ids=["superior_court_probate"],
        primary_parcel_id="9876543210",
        enrichment_status="ENRICHED",
    )
    _assert(is_probate_confirmed_property(probate), "probate + APN + ENRICHED qualifies")

    no_apn = _lead(source_ids=["superior_court_probate"], primary_parcel_id=None)
    _assert(not is_probate_confirmed_property(no_apn), "probate + no APN → not confirmed property")

    not_probate = _lead(source_ids=["recorder_maricopa"], primary_parcel_id="1111")
    _assert(not is_probate_confirmed_property(not_probate), "non-probate source excluded")

    unenriched = _lead(
        source_ids=["superior_court_probate"],
        primary_parcel_id="9999",
        enrichment_status="UNENRICHED",
    )
    _assert(not is_probate_confirmed_property(unenriched), "UNENRICHED probate excluded")

    stacked = _lead(
        source_ids=["superior_court_probate", "recorder_maricopa"],
        primary_parcel_id="5555",
        enrichment_status="ENRICHED",
    )
    _assert(is_probate_confirmed_property(stacked), "probate+NOTS stacked with APN qualifies")


def test_tax_foreclosure_overlap_filter() -> None:
    """Both treasurer_tax_lien and recorder_maricopa in source_ids → overlap."""
    both = _lead(source_ids=["treasurer_tax_lien", "recorder_maricopa"])
    _assert(is_tax_foreclosure_overlap(both), "both sources → overlap")

    tax_only = _lead(source_ids=["treasurer_tax_lien"])
    _assert(not is_tax_foreclosure_overlap(tax_only), "tax only → not overlap")

    nots_only = _lead(source_ids=["recorder_maricopa"])
    _assert(not is_tax_foreclosure_overlap(nots_only), "NOTS only → not overlap")

    with_probate = _lead(
        source_ids=["treasurer_tax_lien", "recorder_maricopa", "superior_court_probate"]
    )
    _assert(is_tax_foreclosure_overlap(with_probate), "tax + NOTS + probate → overlap")

    empty = _lead(source_ids=[])
    _assert(not is_tax_foreclosure_overlap(empty), "empty source_ids → not overlap")


def test_review_required_filter() -> None:
    """REVIEW_REQUIRED in lead_status or enrichment_status."""
    rr_status = _lead(lead_status="REVIEW_REQUIRED", enrichment_status="UNENRICHED")
    _assert(is_review_required(rr_status), "lead_status=REVIEW_REQUIRED")

    rr_enrich = _lead(lead_status="APPROVED_FOR_DASHBOARD", enrichment_status="REVIEW_REQUIRED")
    _assert(is_review_required(rr_enrich), "enrichment_status=REVIEW_REQUIRED")

    both_rr = _lead(lead_status="REVIEW_REQUIRED", enrichment_status="REVIEW_REQUIRED")
    _assert(is_review_required(both_rr), "both REVIEW_REQUIRED")

    approved = _lead(lead_status="APPROVED_FOR_DASHBOARD", enrichment_status="ENRICHED")
    _assert(not is_review_required(approved), "APPROVED + ENRICHED → not review_required")


def test_skiptrace_ready_dedup() -> None:
    """Multiple leads with same APN → only highest-score row survives after dedup."""
    lead_a = _lead(score=70, primary_parcel_id="APNX01")
    lead_b = _lead(score=90, primary_parcel_id="APNX01")  # same APN, higher score
    lead_c = _lead(score=65, primary_parcel_id="APNX02")

    _assert(is_skiptrace_ready(lead_a), "lead_a qualifies for skiptrace")
    _assert(is_skiptrace_ready(lead_b), "lead_b qualifies for skiptrace")
    _assert(is_skiptrace_ready(lead_c), "lead_c qualifies for skiptrace")

    rows = [_flat_row(l, _TS) for l in [lead_a, lead_b, lead_c]]
    deduped = _dedup_by_apn(rows)

    scores_by_apn = {r["apn"]: r["score"] for r in deduped}
    _assert(len(deduped) == 2, "2 unique APNs after dedup")
    _assert(scores_by_apn.get("APNX01") == 90, "APNX01 keeps highest score (90, not 70)")
    _assert("APNX02" in scores_by_apn, "APNX02 present")


def test_skiptrace_excludes_review_required() -> None:
    """REVIEW_REQUIRED, UNENRICHED, and no-APN leads excluded from skiptrace_ready."""
    rr = _lead(lead_status="REVIEW_REQUIRED", enrichment_status="ENRICHED", primary_parcel_id="9999")
    _assert(not is_skiptrace_ready(rr), "REVIEW_REQUIRED excluded from skiptrace")

    unenriched = _lead(enrichment_status="UNENRICHED", primary_parcel_id="8888")
    _assert(not is_skiptrace_ready(unenriched), "UNENRICHED excluded from skiptrace")

    no_apn = _lead(primary_parcel_id=None)
    _assert(not is_skiptrace_ready(no_apn), "no APN excluded from skiptrace")

    ok = _lead(score=68, primary_parcel_id="7777")
    _assert(is_skiptrace_ready(ok), "ENRICHED + approved + APN qualifies")


def test_possible_ambiguous_probate_not_in_skiptrace_or_confirmed_property() -> None:
    """POSSIBLE/AMBIGUOUS probate parcel matches have primary_parcel_id=None.

    probate_adapter.py only sets property_refs.parcel_id for CONFIRMED matches.
    This test confirms that POSSIBLE/AMBIGUOUS probate leads never enter
    skiptrace_ready or probate_confirmed_property exports via the APN path.
    """
    possible_lead = _lead(
        source_ids=["superior_court_probate"],
        primary_parcel_id=None,
        enrichment_status="UNENRICHED",
    )
    _assert(
        not is_skiptrace_ready(possible_lead),
        "POSSIBLE probate (no APN) excluded from skiptrace_ready",
    )
    _assert(
        not is_probate_confirmed_property(possible_lead),
        "POSSIBLE probate (no APN) excluded from probate_confirmed_property",
    )

    ambiguous_lead = _lead(
        source_ids=["superior_court_probate"],
        primary_parcel_id=None,
        enrichment_status="UNENRICHED",
    )
    _assert(
        not is_skiptrace_ready(ambiguous_lead),
        "AMBIGUOUS probate (no APN) excluded from skiptrace_ready",
    )
    _assert(
        not is_probate_confirmed_property(ambiguous_lead),
        "AMBIGUOUS probate (no APN) excluded from probate_confirmed_property",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    print("=== test_export_leads ===")
    print()

    tests = [
        test_hot_leads_filter,
        test_strong_leads_filter,
        test_probate_confirmed_property_filter,
        test_tax_foreclosure_overlap_filter,
        test_review_required_filter,
        test_skiptrace_ready_dedup,
        test_skiptrace_excludes_review_required,
        test_possible_ambiguous_probate_not_in_skiptrace_or_confirmed_property,
    ]

    for fn in tests:
        print(f"[{fn.__name__}]")
        fn()
        print()

    total = _passed + _failed
    print(f"Results: {_passed}/{total} passed  ({_failed} failed)")
    if _failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
