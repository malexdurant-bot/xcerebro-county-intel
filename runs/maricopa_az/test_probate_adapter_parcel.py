"""
Unit tests for probate_adapter.py parcel match integration.

Verifies that:
  - CONFIRMED_DECEDENT_OWNER_MATCH → property_refs.parcel_id populated
  - POSSIBLE / AMBIGUOUS / NO_OWNER_MATCH → parcel_id None
  - parcel_match metadata (confidence, strategy, candidate_count) always attached
  - noise cases are skipped before parcel lookup (no raw_event emitted)
  - only CONFIRMED produces a parcel_id that can participate in APN stacking

No network calls. No PII. All data synthetic.

Run:
    python -X utf8 runs/maricopa_az/test_probate_adapter_parcel.py
"""
from __future__ import annotations

import sys
from pathlib import Path

THIS_DIR = Path(__file__).parent
REPO_ROOT = THIS_DIR.parents[1]
for _p in (str(THIS_DIR), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from probate_adapter import build_probate_raw_events
from probate_parcel_matcher import (
    MATCH_CONFIRMED,
    MATCH_POSSIBLE,
    MATCH_AMBIGUOUS,
    MATCH_NONE,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _base_rec(rid: str, case_number: str = "PB2024-999999") -> dict:
    return {
        "raw_record_id": rid,
        "source_id": "superior_court_probate",
        "source_fetched_at": "2026-06-28T00:00:00Z",
        "change_status": "NEW_RECORD",
        "parser_confidence": 90,
        "source_url": "https://example.test/probate",
        "raw_payload": {
            "case_number": case_number,
            "decedent_name": "Test Decedent",
            "petitioner_name": "Test Petitioner",
            "case_detail_url": "https://example.test/probate/case",
            "case_type": None,
            "filing_date": None,
        },
    }


def _detail(
    decedent: str = "Test Decedent",
    is_estate: bool = True,
    is_noise: bool = False,
) -> dict:
    return {
        "is_estate_case": is_estate,
        "is_noise_case": is_noise,
        "decedent_name": decedent if is_estate else None,
        "petitioner_name": "Test Petitioner",
        "case_subtype_inferred": "letters_testamentary",
        "earliest_filing_date": "2026-01-15",
        "case_type_raw": "Probate" if is_estate else "Guardianship",
    }


def _confirmed(apn: str = "1111111111") -> dict:
    return {
        "match_confidence": MATCH_CONFIRMED,
        "apn": apn,
        "situs_address": "100 MAIN ST",
        "owner_name_on_record": "DECEDENT TEST",
        "strategy_used": "first_last",
        "candidate_count": 0,
        "reject_reason": None,
    }


def _possible() -> dict:
    return {
        "match_confidence": MATCH_POSSIBLE,
        "apn": "2222222222",
        "situs_address": "200 OAK AVE",
        "owner_name_on_record": "TEST DECEDENT MIDDLE",
        "strategy_used": "second_last",
        "candidate_count": 0,
        "reject_reason": None,
    }


def _ambiguous(count: int = 3) -> dict:
    return {
        "match_confidence": MATCH_AMBIGUOUS,
        "apn": None,
        "situs_address": None,
        "owner_name_on_record": None,
        "strategy_used": "first_last",
        "candidate_count": count,
        "reject_reason": None,
    }


def _no_match(reason: str = "no_assessor_hit") -> dict:
    return {
        "match_confidence": MATCH_NONE,
        "apn": None,
        "situs_address": None,
        "owner_name_on_record": None,
        "strategy_used": None,
        "candidate_count": 0,
        "reject_reason": reason,
    }


def _run(
    rid: str,
    match: dict | None = None,
    is_noise: bool = False,
    is_estate: bool = True,
    case_number: str = "PB2024-999999",
) -> list[dict]:
    rec = _base_rec(rid, case_number)
    detail = _detail(is_estate=is_estate, is_noise=is_noise)
    detail_by_id = {rid: detail}
    parcel_match_by_id = {rid: match} if match is not None else {}
    return build_probate_raw_events([rec], detail_by_id=detail_by_id, parcel_match_by_id=parcel_match_by_id)


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


def test_confirmed_populates_parcel_id() -> None:
    """CONFIRMED match → property_refs.parcel_id set to APN."""
    events = _run("rid_001", match=_confirmed("1111111111"))
    _assert(len(events) == 1, "1 raw_event emitted")
    _assert(events[0]["property_refs"]["parcel_id"] == "1111111111", "parcel_id = APN")
    _assert(events[0]["property_refs"]["situs_address"] == "100 MAIN ST", "situs_address set")


def test_possible_does_not_populate_parcel_id() -> None:
    """POSSIBLE match → parcel_id must remain None."""
    events = _run("rid_002", match=_possible())
    _assert(len(events) == 1, "1 raw_event emitted")
    _assert(events[0]["property_refs"]["parcel_id"] is None, "POSSIBLE: parcel_id None")
    _assert(events[0]["property_refs"]["situs_address"] is None, "POSSIBLE: situs_address None")


def test_ambiguous_does_not_populate_parcel_id() -> None:
    """AMBIGUOUS match (3 candidates) → parcel_id must remain None."""
    events = _run("rid_003", match=_ambiguous(3))
    _assert(len(events) == 1, "1 raw_event emitted")
    _assert(events[0]["property_refs"]["parcel_id"] is None, "AMBIGUOUS: parcel_id None")


def test_no_match_parcel_id_none() -> None:
    """NO_OWNER_MATCH → parcel_id None."""
    events = _run("rid_004", match=_no_match())
    _assert(len(events) == 1, "1 raw_event emitted")
    _assert(events[0]["property_refs"]["parcel_id"] is None, "NO_MATCH: parcel_id None")


def test_no_parcel_match_dict_parcel_id_none() -> None:
    """No parcel_match_by_id provided → parcel_id None (graceful default)."""
    rec = _base_rec("rid_005")
    detail = _detail()
    events = build_probate_raw_events([rec], detail_by_id={"rid_005": detail})
    _assert(len(events) == 1, "1 raw_event emitted without match dict")
    _assert(events[0]["property_refs"]["parcel_id"] is None, "no match dict → parcel_id None")


def test_confirmed_parcel_match_metadata_correct() -> None:
    """CONFIRMED match → parcel_match metadata has confidence and strategy."""
    events = _run("rid_006", match=_confirmed())
    pm = events[0].get("parcel_match") or {}
    _assert(pm.get("match_confidence") == MATCH_CONFIRMED, "parcel_match.confidence = CONFIRMED")
    _assert(pm.get("strategy_used") == "first_last", "parcel_match.strategy = first_last")
    _assert(pm.get("candidate_count") == 0, "parcel_match.candidate_count = 0")
    _assert(pm.get("reject_reason") is None, "parcel_match.reject_reason = None")


def test_possible_parcel_match_metadata_correct() -> None:
    """POSSIBLE match → parcel_match has confidence; parcel_id absent."""
    events = _run("rid_007", match=_possible())
    pm = events[0].get("parcel_match") or {}
    _assert(pm.get("match_confidence") == MATCH_POSSIBLE, "parcel_match.confidence = POSSIBLE")
    _assert(pm.get("strategy_used") == "second_last", "parcel_match.strategy = second_last")
    _assert(events[0]["property_refs"]["parcel_id"] is None, "parcel_id absent for POSSIBLE")


def test_ambiguous_candidate_count_in_metadata() -> None:
    """AMBIGUOUS match → candidate_count in parcel_match for review."""
    events = _run("rid_008", match=_ambiguous(5))
    pm = events[0].get("parcel_match") or {}
    _assert(pm.get("match_confidence") == MATCH_AMBIGUOUS, "parcel_match.confidence = AMBIGUOUS")
    _assert(pm.get("candidate_count") == 5, "parcel_match.candidate_count = 5")
    _assert(events[0]["property_refs"]["parcel_id"] is None, "parcel_id absent for AMBIGUOUS")


def test_noise_case_skipped_before_parcel_lookup() -> None:
    """Noise case (is_noise_case=True) is skipped — no raw_event, parcel_match unused."""
    events = _run("rid_009", match=_confirmed("9999999999"), is_noise=True, is_estate=False,
                  case_number="PB2024-000001")
    _assert(len(events) == 0, "noise case: 0 raw_events emitted")


def test_only_confirmed_gets_parcel_id_in_mixed_batch() -> None:
    """Batch with CONFIRMED and POSSIBLE: only CONFIRMED gets parcel_id."""
    rec_a = _base_rec("rid_010a", "PB2024-000010")
    rec_b = _base_rec("rid_010b", "PB2024-000011")
    detail_a = _detail("Decedent Alpha")
    detail_b = _detail("Decedent Beta")
    events = build_probate_raw_events(
        [rec_a, rec_b],
        detail_by_id={"rid_010a": detail_a, "rid_010b": detail_b},
        parcel_match_by_id={
            "rid_010a": _confirmed("1010101010"),
            "rid_010b": _possible(),
        },
    )
    _assert(len(events) == 2, "2 raw_events emitted")
    _assert(events[0]["property_refs"]["parcel_id"] == "1010101010", "rec_a CONFIRMED: parcel_id set")
    _assert(events[1]["property_refs"]["parcel_id"] is None, "rec_b POSSIBLE: parcel_id None")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    print("=== test_probate_adapter_parcel ===")
    print()

    tests = [
        test_confirmed_populates_parcel_id,
        test_possible_does_not_populate_parcel_id,
        test_ambiguous_does_not_populate_parcel_id,
        test_no_match_parcel_id_none,
        test_no_parcel_match_dict_parcel_id_none,
        test_confirmed_parcel_match_metadata_correct,
        test_possible_parcel_match_metadata_correct,
        test_ambiguous_candidate_count_in_metadata,
        test_noise_case_skipped_before_parcel_lookup,
        test_only_confirmed_gets_parcel_id_in_mixed_batch,
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
