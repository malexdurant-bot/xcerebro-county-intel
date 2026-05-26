"""
owner_status_classifier — v5.5.0 §3.5 estate-titled owner origination canon.

The §3.5 canon (operator-supplied, distilled from the three-county build):

  An estate-titled owner ORIGINATES a probate lead through the
  PRIMARY_OWNER_STATUS_SOURCE channel — NOT through §17. The estate
  language is a status visible on the parcel-master / appraiser /
  records, not an event filing.

  Inclusions (the owner string carries one of):

    "ESTATE OF <name>"            — explicit estate naming
    "EST OF <name>"               — abbreviation
    "<name> ESTATE" (trailing)    — common appraiser convention
    "HEIRS OF <name>"             — heirs collective
    "<name> (DECD)"               — deceased annotation
    "<name> DECEASED"             — explicit decedent
    "<name>, DECEASED"            — comma variant

  Exclusions — NOT probate even if the name contains "estate":

    company suffixes — LLC, INC, CORP, CO, COMPANY, REALTY, PROPERTIES,
    HOMES, ESTATES, HOLDINGS (plural ESTATES is real-estate-business)
    LIFE ESTATE — a living life-tenant ownership form, not probate. Tagged
    separately as `life_estate`.

  Dedupe — one row per (owner_name, parcel_id).

This module is universal framework code: it carries no county / state /
vendor literal. The county-agnostic scanner enforces that.

Relationship to owner_name_patterns.py: the existing emitter is the
v5.1.2-beta loose-regex estate / living-trust pattern detector. This module
is the v5.5.0 STRICT classifier that uses the canon inclusion + exclusion
rules and the §3.5 lead-type assignments. The two can coexist: the loose
emitter remains as a signal source on standalone parcels, while this
classifier is the authoritative lead-origination gate for parcel-master /
appraiser sources tagged PRIMARY_OWNER_STATUS_SOURCE.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Inclusion + exclusion regex registry — canonized per §3.5.
# ---------------------------------------------------------------------------

# Company / business-name suffixes — if ANY of these match anywhere in the
# owner string, the owner is NOT an individual estate. The list is canon:
# operator-supplied based on the three-county build observations.
COMPANY_SUFFIX_TOKENS: tuple[str, ...] = (
    "LLC", "L.L.C.", "INC", "INCORPORATED", "CORP", "CORPORATION",
    "P.C.", "PC", "P.A.", "PA", "PLLC", "P.L.L.C.",
    "LP", "LLP", "LTD", "LIMITED",
    "CO", "COMPANY",
    "REALTY", "PROPERTIES", "HOMES", "HOLDINGS", "GROUP", "ENTERPRISES",
    "ESTATES",   # plural — real-estate-business convention
    "PARTNERS", "ASSOCIATES",
    "TRUST CO", "TRUST COMPANY", "TRUST CORP",
    "BANK",
    "AUTHORITY",  # municipal entity
)

# Compile a word-boundary regex over the suffix list. Word boundaries
# guarantee "REALTY" inside "REALTY-WORLD" is matched as a token, not as
# part of "MARYESTATE-WORLD".
_COMPANY_SUFFIX_RE = re.compile(
    r"(?<![A-Za-z])(?:"
    + "|".join(re.escape(s) for s in COMPANY_SUFFIX_TOKENS)
    + r")(?![A-Za-z])",
    re.IGNORECASE,
)

# "REAL ESTATE" — must be stripped before any ESTATE pattern check, because
# "REAL ESTATE GROUP" / "REAL ESTATE LLC" must never trigger estate
# classification (per §3.5 / §17.F).
_REAL_ESTATE_RE = re.compile(r"\bREAL\s+ESTATE\b", re.IGNORECASE)

# "LIFE ESTATE" — a living life-tenant ownership form. NOT probate.
# Tagged separately as `life_estate`.
_LIFE_ESTATE_RE = re.compile(r"\bLIFE\s+ESTATE\b", re.IGNORECASE)

# Estate-titled inclusion patterns, evaluated AFTER company-suffix and
# real-estate exclusion. Each match indicates probate-lead candidacy.
_ESTATE_INCLUSION_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\bESTATE\s+OF\b", re.IGNORECASE),
    re.compile(r"\bEST\s+OF\b", re.IGNORECASE),
    re.compile(r"\bHEIRS?\s+OF\b", re.IGNORECASE),
    # Trailing " ESTATE" — common appraiser convention.
    re.compile(r"\bESTATE\s*$", re.IGNORECASE),
    # "(DECD)" / "DECEASED" / ", DECEASED" — explicit decedent markers.
    re.compile(r"\(\s*DECD\s*\)", re.IGNORECASE),
    re.compile(r"\bDECEASED\b", re.IGNORECASE),
)


# Canonical lead types this classifier emits.
OWNER_STATUS_LEAD_TYPES: tuple[str, ...] = (
    "estate_titled_owner",  # probate-candidate parcel-master row
    "life_estate",          # living life-tenant — NOT probate
    "not_estate",           # company / generic owner — no lead from owner status
)


@dataclass(frozen=True, kw_only=True)
class OwnerStatusClassification:
    """One classification verdict on one (owner_name, parcel_id) tuple."""

    owner_name: str
    parcel_id: Optional[str]
    lead_type: str          # one of OWNER_STATUS_LEAD_TYPES
    is_estate: bool          # True only when lead_type == "estate_titled_owner"
    is_life_estate: bool     # True only when lead_type == "life_estate"
    is_company: bool         # True when the owner string carries a company suffix
    matched_inclusion: Optional[str] = None  # which §3.5 inclusion rule fired
    matched_exclusion: Optional[str] = None  # which §3.5 exclusion rule fired
    notes: tuple[str, ...] = field(default_factory=tuple)


def classify_owner_status(
    owner_name: Optional[str], parcel_id: Optional[str] = None,
) -> OwnerStatusClassification:
    """Classify an owner_name + parcel_id per the §3.5 estate-titled rules.

    Order of checks (CRITICAL — each gate short-circuits):
      1. LIFE ESTATE → `life_estate` (not probate).
      2. Company suffix → `not_estate` (LLC / INC / CORP / REALTY / etc.).
      3. "REAL ESTATE" (without LIFE prefix) → `not_estate` (real-estate
         business name).
      4. Estate inclusion pattern → `estate_titled_owner`.
      5. Default → `not_estate`.
    """
    name = (owner_name or "").strip()

    if not name:
        return OwnerStatusClassification(
            owner_name="", parcel_id=parcel_id,
            lead_type="not_estate",
            is_estate=False, is_life_estate=False, is_company=False,
            matched_exclusion="empty_owner_name",
        )

    # (1) LIFE ESTATE check — must precede any other estate check.
    if _LIFE_ESTATE_RE.search(name):
        return OwnerStatusClassification(
            owner_name=name, parcel_id=parcel_id,
            lead_type="life_estate",
            is_estate=False, is_life_estate=True, is_company=False,
            matched_inclusion="life_estate",
            notes=("Life-tenant ownership form. NOT probate. Tagged "
                   "separately so the dashboard distinguishes it from "
                   "decedent estates.",),
        )

    # (2) Company suffix check — skip estate detection if the name carries
    # any business suffix token. This catches "REAL ESTATE HOMES LLC",
    # "ABC ESTATES INC", "DOE HOLDINGS LLC", etc.
    company_match = _COMPANY_SUFFIX_RE.search(name)
    if company_match:
        return OwnerStatusClassification(
            owner_name=name, parcel_id=parcel_id,
            lead_type="not_estate",
            is_estate=False, is_life_estate=False, is_company=True,
            matched_exclusion=f"company_suffix:{company_match.group(0).upper()}",
        )

    # (3) "REAL ESTATE" string (no company suffix caught it) → strip
    # before any ESTATE-OF / trailing-ESTATE check.
    text_for_estate_check = _REAL_ESTATE_RE.sub(" ", name)

    # (4) Estate inclusion patterns.
    for pat in _ESTATE_INCLUSION_PATTERNS:
        m = pat.search(text_for_estate_check)
        if m:
            return OwnerStatusClassification(
                owner_name=name, parcel_id=parcel_id,
                lead_type="estate_titled_owner",
                is_estate=True, is_life_estate=False, is_company=False,
                matched_inclusion=m.group(0).upper(),
            )

    # (5) Default — not an estate.
    return OwnerStatusClassification(
        owner_name=name, parcel_id=parcel_id,
        lead_type="not_estate",
        is_estate=False, is_life_estate=False, is_company=False,
    )


def dedupe_estate_classifications(
    classifications: list[OwnerStatusClassification],
) -> list[OwnerStatusClassification]:
    """Per §3.5: dedupe to one row per (owner_name, parcel_id). Two
    identical (owner_name, parcel_id) pairs collapse to one classification;
    non-estate classifications are filtered out (they are not leads)."""
    seen: dict[tuple, OwnerStatusClassification] = {}
    for c in classifications:
        if c.lead_type == "not_estate":
            continue  # filtered: not_estate is not a lead
        key = (
            (c.owner_name or "").strip().upper(),
            c.parcel_id,
        )
        if key not in seen:
            seen[key] = c
    return list(seen.values())
