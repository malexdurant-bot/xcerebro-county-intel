"""
Unit tests for local_parcel_index.py

Tests LocalParcelIndex.fetch_fn (WHERE-clause interpreter) and end-to-end
integration with APNResolver + probate_parcel_matcher. All data synthetic.
No network calls. No PII.

Run:
    python -X utf8 runs/maricopa_az/test_local_parcel_index.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

THIS_DIR = Path(__file__).parent
REPO_ROOT = THIS_DIR.parents[1]
for _p in (str(THIS_DIR), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from apn_resolver import APNResolver
from local_parcel_index import LocalParcelIndex
from probate_parcel_matcher import (
    match_decedent,
    MATCH_CONFIRMED,
    MATCH_POSSIBLE,
    MATCH_AMBIGUOUS,
    MATCH_NONE,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _attr(apn_dash: str, owner: str, puc: str = "R1") -> dict:
    """Synthetic flat attrs dict matching pull_parcel_owner_index output format."""
    return {
        "APN": apn_dash.replace("-", ""),
        "APN_DASH": apn_dash,
        "OWNER_NAME": owner,
        "PHYSICAL_ADDRESS": "100 TEST ST",
        "PHYSICAL_CITY": "PHOENIX",
        "PHYSICAL_ZIP": "85001",
        "MAIL_ADDR1": "100 TEST ST",
        "MAIL_CITY": "PHOENIX",
        "MAIL_STATE": "AZ",
        "MAIL_ZIP": "85001",
        "FCV_CUR": "200000",
        "SALE_PRICE": "150000",
        "SALE_DATE": "2010-01-01",
        "CONST_YEAR": "1985",
        "PUC": puc,
        "INCAREOF": "",
    }


def _make_index(records: list[dict]) -> LocalParcelIndex:
    """Write records to a temp JSONL file, load into LocalParcelIndex."""
    idx = LocalParcelIndex()
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        tmp_path = Path(fh.name)
    try:
        idx.load(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return idx


def _resolver(records: list[dict]) -> APNResolver:
    idx = _make_index(records)
    return APNResolver(fetch_fn=idx.fetch_fn)


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
# fetch_fn unit tests — WHERE-clause interpreter
# ---------------------------------------------------------------------------


def test_fetch_fn_first_last() -> None:
    """'%JOHN%DOE%' matches 'JOHN DOE'."""
    idx = _make_index([_attr("111-11-1111", "JOHN DOE")])
    results = idx.fetch_fn("OWNER_NAME LIKE '%JOHN%DOE%'", 6)
    _assert(len(results) == 1, "first_last: 1 result")
    _assert(results[0]["attributes"]["OWNER_NAME"] == "JOHN DOE", "correct record returned")


def test_fetch_fn_reversed() -> None:
    """'%DOE%JOHN%' matches 'DOE JOHN' (assessor LAST FIRST format)."""
    idx = _make_index([_attr("222-22-2222", "DOE JOHN")])
    results = idx.fetch_fn("OWNER_NAME LIKE '%DOE%JOHN%'", 6)
    _assert(len(results) == 1, "reversed: 1 result")
    _assert(results[0]["attributes"]["OWNER_NAME"] == "DOE JOHN", "correct record")


def test_fetch_fn_order_enforced() -> None:
    """'%DOE%JOHN%' must NOT match 'JOHN DOE' — token order is enforced."""
    idx = _make_index([_attr("333-33-3333", "JOHN DOE")])
    results = idx.fetch_fn("OWNER_NAME LIKE '%DOE%JOHN%'", 6)
    _assert(len(results) == 0, "'JOHN DOE' does not match '%DOE%JOHN%'")


def test_fetch_fn_estate_of_transparent() -> None:
    """'%JOHN%DOE%' matches 'ESTATE OF JOHN DOE' — prefix is transparent."""
    idx = _make_index([_attr("444-44-4444", "ESTATE OF JOHN DOE")])
    results = idx.fetch_fn("OWNER_NAME LIKE '%JOHN%DOE%'", 6)
    _assert(len(results) == 1, "ESTATE OF prefix does not block first_last match")


def test_fetch_fn_no_false_positive() -> None:
    """'%JOHN%DOE%' does not match 'JANE DOE'."""
    idx = _make_index([_attr("555-55-5555", "JANE DOE")])
    results = idx.fetch_fn("OWNER_NAME LIKE '%JOHN%DOE%'", 6)
    _assert(len(results) == 0, "different first name: no match")


def test_fetch_fn_multiple_results() -> None:
    """Common name pattern returns all matching records (up to n)."""
    records = [
        _attr("600-00-0001", "GARCIA MARIA"),
        _attr("600-00-0002", "GARCIA MARIA ELENA"),
        _attr("600-00-0003", "GARCIA MARIA ISABEL"),
    ]
    idx = _make_index(records)
    results = idx.fetch_fn("OWNER_NAME LIKE '%GARCIA%MARIA%'", 6)
    _assert(len(results) == 3, "all 3 Garcia-Maria records returned")


def test_fetch_fn_n_cap_respected() -> None:
    """fetch_fn stops at n results."""
    records = [_attr(f"700-00-{i:04d}", f"SMITH JOHN {i}") for i in range(10)]
    idx = _make_index(records)
    results = idx.fetch_fn("OWNER_NAME LIKE '%SMITH%JOHN%'", 6)
    _assert(len(results) == 6, "n=6 cap respected")


def test_fetch_fn_single_token() -> None:
    """'%JOHNSON%' single-token pattern matches all records containing JOHNSON."""
    idx = _make_index([
        _attr("800-00-0001", "ALICE JOHNSON"),
        _attr("800-00-0002", "JOHNSON ROBERT"),
    ])
    results = idx.fetch_fn("OWNER_NAME LIKE '%JOHNSON%'", 6)
    _assert(len(results) == 2, "single-token hits both JOHNSON records")


def test_fetch_fn_apn_dash_exact() -> None:
    """APN_DASH exact match returns the correct record."""
    idx = _make_index([
        _attr("900-01-0001", "LOOKUP TEST A"),
        _attr("900-01-0002", "LOOKUP TEST B"),
    ])
    results = idx.fetch_fn("APN_DASH = '900-01-0001'", 6)
    _assert(len(results) == 1, "APN_DASH lookup: 1 result")
    _assert(results[0]["attributes"]["APN_DASH"] == "900-01-0001", "correct APN_DASH")


def test_fetch_fn_two_token_does_not_fall_through_to_single() -> None:
    """Two-token WHERE clause is matched by _LIKE_MULTI, not _LIKE_SINGLE."""
    # Only record contains JOHN (not DOE) — two-token search should miss.
    idx = _make_index([_attr("910-00-0001", "JOHN KELLY")])
    results = idx.fetch_fn("OWNER_NAME LIKE '%JOHN%DOE%'", 6)
    _assert(len(results) == 0, "two-token search misses; does not degrade to '%JOHN%'")


# ---------------------------------------------------------------------------
# End-to-end tests: LocalParcelIndex → APNResolver → probate_parcel_matcher
# ---------------------------------------------------------------------------


def test_e2e_confirmed_first_last() -> None:
    """'John Doe' → local index 'JOHN DOE' → CONFIRMED via first_last."""
    r = _resolver([_attr("111-11-1111", "JOHN DOE")])
    result = match_decedent("John Doe", r)
    _assert(result["match_confidence"] == MATCH_CONFIRMED, "e2e: CONFIRMED")
    _assert(result["apn"] is not None, "APN populated")
    _assert(result["strategy_used"] == "first_last", "strategy=first_last")


def test_e2e_confirmed_reversed() -> None:
    """'Lucia Martinez' → assessor 'MARTINEZ LUCIA' → CONFIRMED via reversed."""
    r = _resolver([_attr("222-22-2222", "MARTINEZ LUCIA")])
    result = match_decedent("Lucia Martinez", r)
    _assert(result["match_confidence"] == MATCH_CONFIRMED, "reversed → CONFIRMED")
    _assert(result["strategy_used"] == "reversed", "strategy=reversed")


def test_e2e_estate_of_confirmed() -> None:
    """'ESTATE OF JOHN DOE' on assessor → CONFIRMED (ESTATE not in entity reject list)."""
    r = _resolver([_attr("333-33-3333", "ESTATE OF JOHN DOE")])
    result = match_decedent("John Doe", r)
    _assert(result["match_confidence"] == MATCH_CONFIRMED, "ESTATE OF → CONFIRMED")
    _assert(result["apn"] is not None, "APN returned")


def test_e2e_trust_not_rejected() -> None:
    """TRUST in OWNER_NAME is NOT in the entity reject list — CONFIRMED."""
    r = _resolver([_attr("444-44-4444", "ALICE JOHNSON REVOCABLE TRUST")])
    result = match_decedent("Alice Johnson", r)
    _assert(result["match_confidence"] == MATCH_CONFIRMED, "TRUST → CONFIRMED (not entity)")


def test_e2e_llc_rejected() -> None:
    """LLC in OWNER_NAME → entity guard → NO_OWNER_MATCH."""
    r = _resolver([_attr("555-55-5555", "JOHN DOE LLC")])
    result = match_decedent("John Doe", r)
    _assert(result["match_confidence"] == MATCH_NONE, "LLC → NO_OWNER_MATCH")
    _assert(result["reject_reason"] == "entity_owner_on_assessor", "reason=entity")


def test_e2e_common_name_ambiguous() -> None:
    """3 matching parcels for same name pattern → AMBIGUOUS."""
    records = [
        _attr("600-00-0001", "GARCIA MARIA"),
        _attr("600-00-0002", "GARCIA MARIA ELENA"),
        _attr("600-00-0003", "GARCIA MARIA LUISA"),
    ]
    r = _resolver(records)
    result = match_decedent("Maria Garcia", r)
    _assert(result["match_confidence"] == MATCH_AMBIGUOUS, "3 matches → AMBIGUOUS")
    _assert(result["apn"] is None, "no APN on AMBIGUOUS")
    _assert(result["candidate_count"] >= 2, "candidates preserved")


def test_e2e_single_token_rejected() -> None:
    """Only single_token strategy hits → rejected for probate → NO_OWNER_MATCH.

    Index: 'MARGARET ANN KELLY'
    Search: 'Margaret Jones'
      first_last  %MARGARET%JONES%  → miss (JONES not in 'MARGARET ANN KELLY')
      reversed    %JONES%MARGARET%  → miss
      single_token %MARGARET%       → hit (MARGARET present)
    → single_token → rejected → NO_OWNER_MATCH
    """
    r = _resolver([_attr("800-00-0001", "MARGARET ANN KELLY")])
    result = match_decedent("Margaret Jones", r)
    _assert(result["match_confidence"] == MATCH_NONE, "single_token only → NO_OWNER_MATCH")
    _assert(result["reject_reason"] == "single_token_rejected", "reject_reason correct")


def test_e2e_first_second_possible() -> None:
    """3-token name where first_last misses → first_second hits → POSSIBLE.

    Index: 'MARIA ELENA' (no SANTOS surname)
    Search: 'Maria Elena Santos'
      first_last  %MARIA%SANTOS%   → miss
      reversed    %SANTOS%MARIA%   → miss
      first_second %MARIA%ELENA%   → hit 1 → APNResolver CONFIRMED (first_second in set)
      → probate_parcel_matcher downgrades to POSSIBLE
    """
    r = _resolver([_attr("900-00-0001", "MARIA ELENA")])
    result = match_decedent("Maria Elena Santos", r)
    _assert(result["match_confidence"] == MATCH_POSSIBLE, "first_second → POSSIBLE (probate downgrade)")
    _assert(result["strategy_used"] == "first_second", "strategy=first_second")


def test_e2e_no_match() -> None:
    """Decedent name produces 0 assessor hits → NO_OWNER_MATCH."""
    r = _resolver([_attr("910-00-0001", "COMPLETELY DIFFERENT")])
    result = match_decedent("Unique Uncommon", r)
    _assert(result["match_confidence"] == MATCH_NONE, "0 hits → NO_OWNER_MATCH")
    _assert(result["reject_reason"] == "no_assessor_hit", "reason=no_assessor_hit")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    print("=== test_local_parcel_index ===")
    print()

    tests = [
        test_fetch_fn_first_last,
        test_fetch_fn_reversed,
        test_fetch_fn_order_enforced,
        test_fetch_fn_estate_of_transparent,
        test_fetch_fn_no_false_positive,
        test_fetch_fn_multiple_results,
        test_fetch_fn_n_cap_respected,
        test_fetch_fn_single_token,
        test_fetch_fn_apn_dash_exact,
        test_fetch_fn_two_token_does_not_fall_through_to_single,
        test_e2e_confirmed_first_last,
        test_e2e_confirmed_reversed,
        test_e2e_estate_of_confirmed,
        test_e2e_trust_not_rejected,
        test_e2e_llc_rejected,
        test_e2e_common_name_ambiguous,
        test_e2e_single_token_rejected,
        test_e2e_first_second_possible,
        test_e2e_no_match,
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
