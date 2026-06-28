"""
Unit tests for probate_parcel_matcher.py

All tests use synthetic mock fetch functions injected into APNResolver.
No real network calls are made. No PII in test data.

Run standalone:
    python -X utf8 runs/maricopa_az/test_probate_parcel_matcher.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
THIS_DIR = Path(__file__).parent
for _p in (str(THIS_DIR), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from apn_resolver import APNResolver
from probate_parcel_matcher import (
    match_decedent,
    match_record,
    MATCH_CONFIRMED,
    MATCH_POSSIBLE,
    MATCH_AMBIGUOUS,
    MATCH_NONE,
)

# ---------------------------------------------------------------------------
# Synthetic fixture helpers
# ---------------------------------------------------------------------------


def _feature(apn_dash: str, owner_name: str, address: str = "100 TEST ST") -> dict:
    """Build a synthetic assessor ArcGIS feature (no real data)."""
    apn_raw = apn_dash.replace("-", "")
    return {
        "attributes": {
            "APN": apn_raw,
            "APN_DASH": apn_dash,
            "OWNER_NAME": owner_name,
            "PHYSICAL_ADDRESS": address,
            "PHYSICAL_CITY": "PHOENIX",
            "PHYSICAL_ZIP": "85001",
            "MAIL_ADDR1": address,
            "MAIL_CITY": "PHOENIX",
            "MAIL_STATE": "AZ",
            "MAIL_ZIP": "85001",
            "FCV_CUR": 180000,
            "SALE_PRICE": 140000,
            "SALE_DATE": "2005-06-01",
            "CONST_YEAR": 1978,
            "PUC": "R1",
        },
        "geometry": None,
    }


def _make_fetch(where_to_features: dict) -> Callable:
    """Mock fetch_fn: matches WHERE clause substrings to pre-set feature lists."""
    def fetch_fn(where: str, n: int) -> list[dict]:
        for pattern, features in where_to_features.items():
            if pattern in where:
                return features
        return []
    return fetch_fn


def _resolver(where_to_features: dict) -> APNResolver:
    return APNResolver(fetch_fn=_make_fetch(where_to_features))


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

_passed = 0
_failed = 0


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


def test_exact_decedent_owner_match() -> None:
    """first_last strategy, 1 result → CONFIRMED_DECEDENT_OWNER_MATCH."""
    # Assessor stores "JOHN DOE" for first+last LIKE '%JOHN%DOE%'
    r = _resolver({"JOHN%DOE": [_feature("111-11-1111", "JOHN DOE")]})
    result = match_decedent("John Doe", r)
    _assert(result["match_confidence"] == MATCH_CONFIRMED, "exact match → CONFIRMED")
    _assert(result["apn"] is not None, "APN returned")
    _assert(result["strategy_used"] == "first_last", "strategy=first_last")
    _assert(result["reject_reason"] is None, "no reject_reason")


def test_reversed_name_format() -> None:
    """Assessor stores LAST FIRST (reversed). reversed strategy → CONFIRMED."""
    # Assessor stores "MARTINEZ LUCIA" — reversed from "Lucia Martinez"
    r = _resolver({"MARTINEZ%LUCIA": [_feature("222-22-2222", "MARTINEZ LUCIA")]})
    result = match_decedent("Lucia Martinez", r)
    _assert(result["match_confidence"] == MATCH_CONFIRMED, "reversed name → CONFIRMED")
    _assert(result["strategy_used"] == "reversed", "strategy=reversed")


def test_partial_name_match_first_second_is_possible() -> None:
    """first_second strategy (3-token name, skips last) → POSSIBLE, not CONFIRMED."""
    # "Maria Elena Garcia" — first_last (%MARIA%GARCIA%) misses
    # first_second (%MARIA%ELENA%) hits 1 result
    r = _resolver({
        "MARIA%GARCIA": [],   # first_last misses
        "GARCIA%MARIA": [],   # reversed misses
        "MARIA%ELENA": [_feature("333-33-3333", "MARIA ELENA GARCIA")],
    })
    result = match_decedent("Maria Elena Garcia", r)
    _assert(result["match_confidence"] == MATCH_POSSIBLE, "first_second → POSSIBLE (not CONFIRMED)")
    _assert(result["strategy_used"] == "first_second", "strategy=first_second")


def test_common_name_collision_is_ambiguous() -> None:
    """Multiple assessor results for a common name → AMBIGUOUS_OWNER_MATCH."""
    # 3 different parcels match "JOHN SMITH" — too many candidates
    features = [
        _feature("444-44-4444", "JOHN SMITH"),
        _feature("444-44-4445", "JOHN R SMITH"),
        _feature("444-44-4446", "JOHN SMITH JR"),
    ]
    r = _resolver({"JOHN%SMITH": features})
    result = match_decedent("John Smith", r)
    _assert(result["match_confidence"] == MATCH_AMBIGUOUS, "3 results → AMBIGUOUS")
    _assert(result["candidate_count"] == 3, "candidate_count=3")
    _assert(result["apn"] is None, "no APN on AMBIGUOUS")


def test_no_match_returns_no_owner_match() -> None:
    """Assessor returns 0 results for decedent name → NO_OWNER_MATCH."""
    r = _resolver({})  # all queries return []
    result = match_decedent("Unique Uncommon", r)
    _assert(result["match_confidence"] == MATCH_NONE, "0 results → NO_OWNER_MATCH")
    _assert(result["apn"] is None, "no APN")
    _assert(result["reject_reason"] == "no_assessor_hit", "reason=no_assessor_hit")


def test_single_token_strategy_rejected() -> None:
    """single_token match (last-name-only) must be rejected for probate."""
    # "Margaret Jones" — first token "MARGARET" is 8 chars, qualifies for single_token
    # first_last and reversed miss; single_token hits 1 result
    r = _resolver({
        "MARGARET%JONES": [],   # first_last misses
        "JONES%MARGARET": [],   # reversed misses
        "MARGARET": [_feature("555-55-5555", "MARGARET JONES")],  # single_token hits
    })
    result = match_decedent("Margaret Jones", r)
    _assert(result["match_confidence"] == MATCH_NONE, "single_token rejected → NO_OWNER_MATCH")
    _assert(result["reject_reason"] == "single_token_rejected", "reason=single_token_rejected")


def test_entity_owned_parcel_not_matched() -> None:
    """Individual decedent must not match an entity-owned (LLC) parcel."""
    r = _resolver({"JOHN%DOE": [_feature("666-66-6666", "JOHN DOE LLC")]})
    result = match_decedent("John Doe", r)
    _assert(result["match_confidence"] == MATCH_NONE, "entity owner → NO_OWNER_MATCH")
    _assert(result["reject_reason"] == "entity_owner_on_assessor", "reason=entity_owner")


def test_petitioner_only_does_not_resolve() -> None:
    """Record with no confirmed decedent (petitioner-only) → NO_OWNER_MATCH.

    match_record() must never pass petitioner_name to the resolver.
    """
    detail = {
        "is_estate_case": False,       # no decedent confirmed → not estate
        "is_noise_case": False,
        "decedent_name": None,
        "petitioner_name": "Jane Smith",  # petitioner present, decedent absent
    }
    # Even if Jane Smith's name would match an assessor parcel, it must not resolve
    r = _resolver({"JANE%SMITH": [_feature("777-77-7777", "JANE SMITH")]})
    result = match_record(detail, r)
    _assert(result["match_confidence"] == MATCH_NONE, "petitioner-only → NO_OWNER_MATCH")
    _assert(result["reject_reason"] == "not_an_estate_case", "reason=not_an_estate_case")


def test_only_decedent_used_not_petitioner() -> None:
    """When both decedent and petitioner exist, only decedent is looked up.

    If decedent has no match but petitioner would match, result is NO_OWNER_MATCH.
    """
    detail = {
        "is_estate_case": True,
        "is_noise_case": False,
        "decedent_name": "Robert Jones",    # decedent: no assessor match
        "petitioner_name": "Alice Smith",   # petitioner: would match if used
    }
    def fetch_fn(where: str, n: int) -> list[dict]:
        if "SMITH" in where:
            return [_feature("888-88-8888", "ALICE SMITH")]
        return []   # Jones/Robert get nothing

    r = APNResolver(fetch_fn=fetch_fn)
    result = match_record(detail, r)
    _assert(result["match_confidence"] == MATCH_NONE, "decedent no-match → NO even though petitioner would match")
    _assert(result["reject_reason"] == "no_assessor_hit", "resolver tried decedent, not petitioner")


def test_attorney_never_used_for_match() -> None:
    """Attorney name in parties list must never be passed to the resolver.

    match_record() reads only decedent_name. If the decedent doesn't match
    but the attorney would, result must be NO_OWNER_MATCH.
    """
    detail = {
        "is_estate_case": True,
        "is_noise_case": False,
        "decedent_name": "Frank Brown",     # no match
        "petitioner_name": "Carol Brown",
        "parties": [
            {
                "name": "Frank Brown",
                "relationship": "Decedent",
                "sex": "Male",
                "attorney": "Williams & Associates LLC",  # would match if used
            }
        ],
    }
    def fetch_fn(where: str, n: int) -> list[dict]:
        if "WILLIAMS" in where:
            return [_feature("999-99-9999", "WILLIAMS AND ASSOCIATES LLC")]
        return []

    r = APNResolver(fetch_fn=fetch_fn)
    result = match_record(detail, r)
    _assert(result["match_confidence"] == MATCH_NONE, "attorney never used → NO_OWNER_MATCH")


def test_estate_of_name_on_assessor_matches() -> None:
    """Assessor showing 'ESTATE OF JOHN DOE' should match decedent 'John Doe'."""
    # first_last strategy: OWNER_NAME LIKE '%JOHN%DOE%' would hit 'ESTATE OF JOHN DOE'
    r = _resolver({"JOHN%DOE": [_feature("100-10-1000", "ESTATE OF JOHN DOE")]})
    result = match_decedent("John Doe", r)
    # ESTATE is not in the entity reject list — this is a valid probate match
    _assert(result["match_confidence"] == MATCH_CONFIRMED, "ESTATE OF match → CONFIRMED")
    _assert(result["apn"] is not None, "APN returned for estate-of match")


def test_empty_decedent_name_no_crash() -> None:
    """Empty or whitespace decedent name returns NO_OWNER_MATCH, no crash."""
    r = _resolver({})
    _assert(match_decedent("", r)["match_confidence"] == MATCH_NONE, "empty string → NO")
    _assert(match_decedent("   ", r)["match_confidence"] == MATCH_NONE, "whitespace → NO")
    _assert(match_record({}, r)["match_confidence"] == MATCH_NONE, "empty detail → NO")


def test_noise_case_not_processed() -> None:
    """Guardianship/noise records (is_estate_case=False) must not resolve."""
    detail = {
        "is_estate_case": False,
        "is_noise_case": True,
        "decedent_name": None,
        "petitioner_name": "Guardian Person",
    }
    r = _resolver({"GUARDIAN": [_feature("111-00-0001", "GUARDIAN PERSON")]})
    result = match_record(detail, r)
    _assert(result["match_confidence"] == MATCH_NONE, "noise case → NO_OWNER_MATCH")


def test_corp_owner_rejected() -> None:
    """CORP entity keyword in OWNER_NAME → rejected."""
    r = _resolver({"ALICE%JOHNSON": [_feature("200-20-2000", "ALICE JOHNSON CORP")]})
    result = match_decedent("Alice Johnson", r)
    _assert(result["match_confidence"] == MATCH_NONE, "CORP owner → NO")
    _assert(result["reject_reason"] == "entity_owner_on_assessor", "reason correct")


def test_trust_owner_not_rejected() -> None:
    """TRUST in OWNER_NAME is NOT auto-rejected (decedent may hold in trust)."""
    r = _resolver({"ALICE%JOHNSON": [_feature("300-30-3000", "ALICE JOHNSON REVOCABLE TRUST")]})
    result = match_decedent("Alice Johnson", r)
    # Trust not in entity reject list → proceeds to CONFIRMED
    _assert(result["match_confidence"] == MATCH_CONFIRMED, "TRUST owner → CONFIRMED (not rejected)")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    print("=== test_probate_parcel_matcher ===")
    print()

    tests = [
        test_exact_decedent_owner_match,
        test_reversed_name_format,
        test_partial_name_match_first_second_is_possible,
        test_common_name_collision_is_ambiguous,
        test_no_match_returns_no_owner_match,
        test_single_token_strategy_rejected,
        test_entity_owned_parcel_not_matched,
        test_petitioner_only_does_not_resolve,
        test_only_decedent_used_not_petitioner,
        test_attorney_never_used_for_match,
        test_estate_of_name_on_assessor_matches,
        test_empty_decedent_name_no_crash,
        test_noise_case_not_processed,
        test_corp_owner_rejected,
        test_trust_owner_not_rejected,
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
