"""
Unit tests for runs/maricopa_az/apn_resolver.py.

All tests use a synthetic fetch_fn — no live network calls.
Fixtures use obviously-fake names (DOE, SMITH) and fake APNs.
"""

from __future__ import annotations

import sys
from pathlib import Path

_THIS_DIR = Path(__file__).parent
_REPO_ROOT = _THIS_DIR.parents[1]
for _p in (str(_REPO_ROOT), str(_THIS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from apn_resolver import (
    APNResolver,
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_CONFIRMED,
    CONFIDENCE_POSSIBLE,
    CONFIDENCE_UNRESOLVED,
    _build_strategies,
    _safe_token,
)

# ---------------------------------------------------------------------------
# Fake parcel factory
# ---------------------------------------------------------------------------


def _parcel(apn: str, owner: str) -> dict:
    return {"attributes": {"APN": apn, "OWNER_NAME": owner, "PHYSICAL_ADDRESS": "123 FAKE ST"}}


# ---------------------------------------------------------------------------
# _safe_token
# ---------------------------------------------------------------------------


def test_safe_token_strips_punctuation():
    assert _safe_token("O'Brien") == "OBRIEN"


def test_safe_token_drops_short_result():
    # single char after stripping should be kept by _safe_token itself
    # (length guard is in _build_strategies)
    assert _safe_token("A") == "A"


# ---------------------------------------------------------------------------
# _build_strategies
# ---------------------------------------------------------------------------


def test_build_strategies_two_tokens():
    strats = dict(_build_strategies(["John", "Doe"]))
    assert "first_last" in strats
    assert "reversed" in strats
    assert "first_second" not in strats   # only 2 tokens


def test_build_strategies_three_tokens():
    strats = dict(_build_strategies(["Mary", "Jane", "Smith"]))
    assert "first_second" in strats
    assert "second_last" in strats


def test_build_strategies_single_long_token():
    strats = dict(_build_strategies(["Johnson"]))
    assert "single_token" in strats


def test_build_strategies_single_short_token():
    strats = dict(_build_strategies(["Jo"]))
    # "Jo" is 2 chars — qualifies as a safe token but < 6 chars so no single_token
    assert not any(n == "single_token" for n, _ in _build_strategies(["Jo"]))


def test_build_strategies_empty():
    assert _build_strategies([]) == []


def test_build_strategies_deduplicates():
    # Two identical tokens produce duplicate WHERE clauses; should be collapsed.
    strats = _build_strategies(["Smith", "Smith"])
    clauses = [c for _, c in strats]
    assert len(clauses) == len(set(clauses))


# ---------------------------------------------------------------------------
# Confirmed match — first_last strategy, single result
# ---------------------------------------------------------------------------


def test_confirmed_single_result():
    feat = _parcel("123-45-678", "DOE JOHN A")

    def fetch(where, n=6):
        if "DOE" in where and "JOHN" in where:
            return [feat]
        return []

    r = APNResolver(fetch_fn=fetch).resolve(["John Doe"])
    assert r["confidence"] == CONFIDENCE_CONFIRMED
    assert r["apn"] == "123-45-678"
    assert r["strategy_used"] == "first_last"
    assert r["matched_name"] == "John Doe"
    assert r["candidates"] == []


# ---------------------------------------------------------------------------
# Reversed-name match — assessor stores "DOE JOHN" so first_last misses,
# reversed hits (token[-1]+token[0] = DOE+JOHN)
# ---------------------------------------------------------------------------


def test_reversed_name_match():
    feat = _parcel("111-22-333", "DOE JOHN")

    def fetch(where, n=6):
        # first_last tries LIKE '%JOHN%DOE%' — no match
        # reversed tries LIKE '%DOE%JOHN%' — match
        if "%DOE%JOHN%" in where:
            return [feat]
        return []

    r = APNResolver(fetch_fn=fetch).resolve(["John Doe"])
    assert r["confidence"] == CONFIDENCE_CONFIRMED
    assert r["strategy_used"] == "reversed"
    assert r["apn"] == "111-22-333"


# ---------------------------------------------------------------------------
# Ambiguous — 2–4 results, candidates preserved
# ---------------------------------------------------------------------------


def test_ambiguous_multiple_candidates():
    parcels = [
        _parcel("100-00-001", "SMITH JOHN A"),
        _parcel("100-00-002", "SMITH JOHN B"),
    ]

    def fetch(where, n=6):
        return parcels

    r = APNResolver(fetch_fn=fetch).resolve(["John Smith"])
    assert r["confidence"] == CONFIDENCE_AMBIGUOUS
    assert r["apn"] is None
    assert len(r["candidates"]) == 2


# ---------------------------------------------------------------------------
# Co-owner fallback — first name is UNRESOLVED, second is CONFIRMED
# ---------------------------------------------------------------------------


def test_coowner_fallback():
    feat = _parcel("200-00-001", "JONES MARY")

    def fetch(where, n=6):
        # Only "MARY JONES" resolves; "JOHN UNKNOWN" never matches.
        if "MARY" in where and "JONES" in where:
            return [feat]
        return []

    r = APNResolver(fetch_fn=fetch).resolve(["John Unknown", "Mary Jones"])
    assert r["confidence"] == CONFIDENCE_CONFIRMED
    assert r["apn"] == "200-00-001"
    assert r["matched_name"] == "Mary Jones"


# ---------------------------------------------------------------------------
# Co-owner: first name AMBIGUOUS, second name CONFIRMED — prefer CONFIRMED
# ---------------------------------------------------------------------------


def test_coowner_confirmed_beats_ambiguous():
    ambig_parcels = [_parcel(f"300-00-00{i}", f"DOE JANE {i}") for i in range(2)]
    confirmed_parcel = _parcel("400-00-001", "ROE RICHARD")

    calls: list[str] = []

    def fetch(where, n=6):
        calls.append(where)
        if "JANE" in where:
            return ambig_parcels
        if "RICHARD" in where and "ROE" in where:
            return [confirmed_parcel]
        return []

    r = APNResolver(fetch_fn=fetch).resolve(["Jane Doe", "Richard Roe"])
    assert r["confidence"] == CONFIDENCE_CONFIRMED
    assert r["apn"] == "400-00-001"
    assert r["matched_name"] == "Richard Roe"


# ---------------------------------------------------------------------------
# Unresolved — nothing matches
# ---------------------------------------------------------------------------


def test_unresolved_when_no_match():
    def fetch(where, n=6):
        return []

    r = APNResolver(fetch_fn=fetch).resolve(["Ghost Name"])
    assert r["confidence"] == CONFIDENCE_UNRESOLVED
    assert r["apn"] is None
    assert r["candidates"] == []


# ---------------------------------------------------------------------------
# Empty / edge inputs — no crash
# ---------------------------------------------------------------------------


def test_empty_name_list():
    r = APNResolver(fetch_fn=lambda w, n=6: []).resolve([])
    assert r["confidence"] == CONFIDENCE_UNRESOLVED


def test_blank_name_skipped():
    feat = _parcel("500-00-001", "REAL PERSON")

    def fetch(where, n=6):
        if "REAL" in where:
            return [feat]
        return []

    r = APNResolver(fetch_fn=fetch).resolve(["", "Real Person"])
    assert r["confidence"] == CONFIDENCE_CONFIRMED


def test_single_token_name_no_crash():
    def fetch(where, n=6):
        return []

    r = APNResolver(fetch_fn=fetch).resolve(["Madonna"])
    # Single short token — no strategies → UNRESOLVED (not a crash)
    assert r["confidence"] in (CONFIDENCE_UNRESOLVED, CONFIDENCE_POSSIBLE)


def test_punctuation_heavy_name_no_crash():
    def fetch(where, n=6):
        return []

    r = APNResolver(fetch_fn=fetch).resolve(["O'Brien-McTavish Jr."])
    assert r["confidence"] in (CONFIDENCE_CONFIRMED, CONFIDENCE_POSSIBLE,
                                CONFIDENCE_AMBIGUOUS, CONFIDENCE_UNRESOLVED)


# ---------------------------------------------------------------------------
# Cache — fetcher only called once for repeated queries
# ---------------------------------------------------------------------------


def test_cache_deduplicates_queries():
    call_count = [0]
    feat = _parcel("600-00-001", "CACHE TEST")

    def fetch(where, n=6):
        call_count[0] += 1
        return [feat]

    resolver = APNResolver(fetch_fn=fetch)
    resolver.resolve(["Cache Test"])
    resolver.resolve(["Cache Test"])  # same WHERE → cache hit
    assert call_count[0] == 1, f"expected 1 fetch call, got {call_count[0]}"


# ---------------------------------------------------------------------------
# Disk cache round-trip
# ---------------------------------------------------------------------------


def test_disk_cache_roundtrip(tmp_path):
    feat = _parcel("700-00-001", "DISK CACHE TEST")
    call_count = [0]

    def fetch(where, n=6):
        call_count[0] += 1
        return [feat]

    cache_file = tmp_path / "cache.json"

    r1 = APNResolver(cache_path=cache_file, fetch_fn=fetch)
    r1.resolve(["Disk Cache"])
    r1.save_cache()
    assert cache_file.exists()

    # Second resolver reads from disk — fetch should NOT be called again.
    r2 = APNResolver(cache_path=cache_file, fetch_fn=fetch)
    r2.resolve(["Disk Cache"])
    assert call_count[0] == 1, f"expected 1 fetch call total, got {call_count[0]}"


# ---------------------------------------------------------------------------
# Too-many-results (>= _PAGE_SIZE=6) treated as too-broad — not AMBIGUOUS
# ---------------------------------------------------------------------------


def test_too_many_results_skipped():
    six_parcels = [_parcel(f"800-00-00{i}", f"SMITH JOHN {i}") for i in range(6)]

    def fetch(where, n=6):
        return six_parcels  # exactly _PAGE_SIZE → treated as too many

    r = APNResolver(fetch_fn=fetch).resolve(["John Smith"])
    # All strategies return 6 results → skipped → UNRESOLVED
    assert r["confidence"] == CONFIDENCE_UNRESOLVED


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in tests:
        name = fn.__name__
        # tmp_path tests need a real tmp dir
        if "tmp_path" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
            import tempfile, pathlib
            with tempfile.TemporaryDirectory() as d:
                try:
                    fn(pathlib.Path(d))
                    print(f"  PASS  {name}")
                    passed += 1
                except Exception:
                    print(f"  FAIL  {name}")
                    traceback.print_exc()
                    failed += 1
        else:
            try:
                fn()
                print(f"  PASS  {name}")
                passed += 1
            except Exception:
                print(f"  FAIL  {name}")
                traceback.print_exc()
                failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
