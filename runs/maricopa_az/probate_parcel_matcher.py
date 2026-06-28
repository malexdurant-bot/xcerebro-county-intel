"""
Decedent-to-parcel matcher for Maricopa probate enrichment v1.

Uses the APNResolver (apn_resolver.py) to query the Maricopa Assessor ArcGIS
owner-name index for confirmed probate decedents, applying probate-specific
confidence rules that are stricter than the general NOTS resolver.

=== Probate confidence levels ===

  CONFIRMED_DECEDENT_OWNER_MATCH  — first+last or reversed strategy, exactly 1
                                     parcel result, non-entity OWNER_NAME
  POSSIBLE_DECEDENT_OWNER_MATCH   — partial-name strategy (first+second or
                                     second+last), exactly 1 result, non-entity
  AMBIGUOUS_OWNER_MATCH           — 2–4 parcels returned by any strategy;
                                     candidates preserved for operator review
  NO_OWNER_MATCH                  — 0 results, single-token only, entity owner,
                                     or no confirmed decedent on input record

=== Guards ===

  single_token strategy            → NO (last-name-only is too broad to confirm
                                     an individual decedent as a property owner)
  entity OWNER_NAME on assessor    → NO (individual decedent ≠ LLC / CORP / LP;
                                     TRUST and ESTATE names are NOT excluded here
                                     because decedents commonly hold property in
                                     trust or the assessor may already show
                                     "ESTATE OF {NAME}" after death recording)
  no confirmed decedent in record  → NO (never infer from petitioner or attorney;
                                     match_record() enforces this at the entry point)

=== How strategy_used maps to probate confidence ===

  first_last  → CONFIRMED  (assessor FIRST LAST or similar full-name format)
  reversed    → CONFIRMED  (assessor LAST FIRST format — common in Maricopa)
  first_second → POSSIBLE  (omits last name; higher false-positive risk)
  second_last  → POSSIBLE  (omits first name; higher false-positive risk)
  single_token → REJECTED  (last-name-only is insufficient)

Note: the APNResolver classifies first_second as CONFIRMED in its own confidence
model, but probate matching is stricter and downgrades it to POSSIBLE.

=== What is NOT done here ===

  - Address-based parcel lookup (not available from probate detail page)
  - APN passed through to pipeline adapter (v1 reports aggregate counts only)
  - Trust / ESTATE name parsing (v2 enhancement)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

_THIS_DIR = Path(__file__).parent
_REPO_ROOT = _THIS_DIR.parents[1]
for _p in (str(_THIS_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from apn_resolver import (  # noqa: E402
    APNResolver,
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_UNRESOLVED,
)

# ---------------------------------------------------------------------------
# Public confidence constants
# ---------------------------------------------------------------------------

MATCH_CONFIRMED = "CONFIRMED_DECEDENT_OWNER_MATCH"
MATCH_POSSIBLE = "POSSIBLE_DECEDENT_OWNER_MATCH"
MATCH_AMBIGUOUS = "AMBIGUOUS_OWNER_MATCH"
MATCH_NONE = "NO_OWNER_MATCH"

# ---------------------------------------------------------------------------
# Strategy → probate confidence mapping
# ---------------------------------------------------------------------------

_PROBATE_CONFIRMED_STRATEGIES = frozenset(["first_last", "reversed"])
_PROBATE_POSSIBLE_STRATEGIES = frozenset(["first_second", "second_last"])
_PROBATE_REJECTED_STRATEGIES = frozenset(["single_token"])

# ---------------------------------------------------------------------------
# Entity-owner guard
# TRUST and ESTATE are intentionally excluded from this set: a decedent may
# hold property through a living trust or the assessor may already reflect
# "ESTATE OF {NAME}" on the record after the death is recorded.
# ---------------------------------------------------------------------------

_ENTITY_FRAGMENTS = frozenset([
    "llc", "l.l.c.", "inc", "incorporated", "corp", "corporation",
    "ltd", " lp", "l.p.", "partnership", "partners",
    "foundation", "association", "assoc", "church", "company", " co.",
])


def _is_entity_owner(owner_name: str) -> bool:
    """Return True if OWNER_NAME looks like a corporate/institutional entity."""
    low = owner_name.lower()
    return any(kw in low for kw in _ENTITY_FRAGMENTS)


# ---------------------------------------------------------------------------
# Result constructors
# ---------------------------------------------------------------------------


def _no_match(reason: str) -> dict:
    return {
        "match_confidence": MATCH_NONE,
        "apn": None,
        "situs_address": None,
        "owner_name_on_record": None,
        "strategy_used": None,
        "candidate_count": 0,
        "reject_reason": reason,
    }


def _match(confidence: str, attrs: dict, strategy: str) -> dict:
    return {
        "match_confidence": confidence,
        "apn": attrs.get("APN"),
        "situs_address": attrs.get("PHYSICAL_ADDRESS"),
        "owner_name_on_record": attrs.get("OWNER_NAME"),
        "strategy_used": strategy,
        "candidate_count": 0,
        "reject_reason": None,
    }


def _ambiguous(strategy: Optional[str], candidate_count: int) -> dict:
    return {
        "match_confidence": MATCH_AMBIGUOUS,
        "apn": None,
        "situs_address": None,
        "owner_name_on_record": None,
        "strategy_used": strategy,
        "candidate_count": candidate_count,
        "reject_reason": None,
    }


# ---------------------------------------------------------------------------
# Core match functions
# ---------------------------------------------------------------------------


def match_decedent(decedent_name: str, resolver: APNResolver) -> dict:
    """Match a confirmed decedent name against the assessor parcel owner index.

    Only call this with a confirmed decedent_name. Never pass petitioner,
    attorney, or other party names — use match_record() which enforces
    that guard at the record level.

    Returns a match result dict with keys:
      match_confidence, apn, situs_address, owner_name_on_record,
      strategy_used, candidate_count, reject_reason
    """
    name = (decedent_name or "").strip()
    if not name:
        return _no_match("empty_decedent_name")

    result = resolver.resolve([name])
    confidence = result.get("confidence", CONFIDENCE_UNRESOLVED)
    strategy = result.get("strategy_used")
    attrs = result.get("attrs") or {}

    if confidence == CONFIDENCE_UNRESOLVED:
        return _no_match("no_assessor_hit")

    # Single-token result: last-name-only is insufficient for probate
    if strategy in _PROBATE_REJECTED_STRATEGIES:
        return _no_match("single_token_rejected")

    if confidence == CONFIDENCE_AMBIGUOUS:
        return _ambiguous(strategy, len(result.get("candidates") or []))

    # Single confirmed/possible result — apply entity guard
    owner_name = attrs.get("OWNER_NAME") or ""
    if owner_name and _is_entity_owner(owner_name):
        return _no_match("entity_owner_on_assessor")

    # Map strategy → probate confidence (stricter than resolver's own mapping)
    if strategy in _PROBATE_CONFIRMED_STRATEGIES:
        return _match(MATCH_CONFIRMED, attrs, strategy)
    if strategy in _PROBATE_POSSIBLE_STRATEGIES:
        return _match(MATCH_POSSIBLE, attrs, strategy)

    # Unknown strategy — treat conservatively as POSSIBLE
    return _match(MATCH_POSSIBLE, attrs, strategy or "unknown")


def match_record(detail: dict, resolver: APNResolver) -> dict:
    """Match a probate detail record dict to a parcel.

    Entry-point guard: only processes records with is_estate_case=True and a
    confirmed decedent_name. This is the function to call from the adapter or
    enrichment runner — it ensures petitioner and attorney names are never
    passed to the resolver.

    Parameters
    ----------
    detail:
        The 'detail' sub-dict from superior_court_probate_detail.jsonl.
    resolver:
        An APNResolver instance (with cache and fetch_fn injected).

    Returns
    -------
    Match result dict (same schema as match_decedent).
    """
    if not detail.get("is_estate_case"):
        return _no_match("not_an_estate_case")

    decedent_name = (detail.get("decedent_name") or "").strip()
    if not decedent_name:
        return _no_match("no_confirmed_decedent")

    return match_decedent(decedent_name, resolver)
