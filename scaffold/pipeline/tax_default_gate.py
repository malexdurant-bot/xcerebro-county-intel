"""
tax_default_gate — v5.5.0 §3.3 tax-default qualification gate.

Tax-default rows can ORIGINATE leads — but only when ALL FIVE qualification
criteria are met. Generic tax-roll data is enrichment, NOT a lead. Without
the gate, the framework's prime directive ("never present a fact you can't
source") is violated by tax-roll dumps presented as leads.

The five-criteria gate (canon §3.3):

  (a) Source is OFFICIAL or OFFICIALLY AUTHORIZED — the county tax
      collector / treasurer office, an officially-authorized collection
      vendor, or the official tax-sale / tax-deed portal. Generic
      "scraped tax-roll" data alone does NOT count.
  (b) Record proves a REAL DEFAULT condition — delinquency, collection,
      tax-sale-certificate, tax-deed, struck-off, redemption, or
      tax-foreclosure status. Just being on the tax roll is NOT a
      default.
  (c) Record TIES TO REAL PROPERTY — through account, parcel, address,
      legal description, tax map, owner, or property reference. A
      debtor-only entry (taxpayer name with no property tie) does NOT
      qualify.
  (d) Record has SOURCE PROOF — source name, URL, record id, timestamp.
      An unsourced row routes review_required.
  (e) Row is NOT MERELY GENERIC ROLL ENRICHMENT. The current-year roll
      with no default flag is enrichment. A row marked delinquent /
      certificate-issued / sale-scheduled / sold IS a default condition.

The gate also handles DEDUPE per §3.3: one lead per (account, parcel) per
current default condition. NOT one per tax year, NOT per fee line, NOT per
balance entry — the operator pain point that motivated the gate.

Canonical types this gate emits (lower snake case — §3.3):

  tax_default                — current delinquency proven
  tax_default_low_priority   — current delinquency but low balance /
                               recent-onset (1-year-tiny-balance per §5.6)
  tax_foreclosure            — tax foreclosure proceeding underway
  tax_sale                   — tax sale event (struck-off / sold / pending)
  tax_certificate            — tax-sale certificate issued / outstanding
  review_required            — qualification incomplete

This module is universal framework code: it contains no county / state /
vendor literal. The county-agnostic regression scanner enforces that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Qualification rule constants.
# ---------------------------------------------------------------------------

# (a) — Source-role values that count as official or officially authorized
# for tax-default origination. These are the §0.1 enum values from the
# v5.5.0 hardening's extended source-role taxonomy.
OFFICIAL_TAX_DEFAULT_ROLES: frozenset[str] = frozenset({
    "PRIMARY_DEFAULT_SOURCE",
    "PRIMARY_EVENT_SOURCE",  # e.g. tax-sale event portal
})

# (b) — Default-condition markers. A record proves a real default condition
# when ANY of these conditions hold (the adapter sets one or more):
DEFAULT_CONDITION_FLAGS: frozenset[str] = frozenset({
    "delinquent",
    "tax_certificate_issued",
    "tax_foreclosure_filed",
    "tax_sale_pending",
    "tax_sale_struck_off",
    "tax_sale_sold",
    "redemption_period",
    "collection_active",
})

# (c) — Property-tie identifiers. At least ONE must be non-empty.
PROPERTY_TIE_FIELDS: tuple[str, ...] = (
    "parcel_id",
    "account_number",
    "tax_map_id",
    "situs_address",
    "legal_description",
)

# (d) — Source-proof fields. ALL must be non-empty (no proof, no lead).
SOURCE_PROOF_FIELDS: tuple[str, ...] = (
    "source_id",
    "source_url",
    "raw_record_id",
    "captured_at",
)

# Canonical qualification outcomes.
QUALIFICATION_STATUSES: tuple[str, ...] = (
    "QUALIFIED",
    "REVIEW_REQUIRED",
    "NOT_QUALIFIED",
)

# Canonical lead types this gate emits.
TAX_DEFAULT_LEAD_TYPES: tuple[str, ...] = (
    "tax_default",
    "tax_default_low_priority",
    "tax_foreclosure",
    "tax_sale",
    "tax_certificate",
    "review_required",
)

# Default low-priority thresholds — operator-supplied for now; canonized so
# the gate is deterministic. A row qualifies as low-priority when balance
# <= LOW_PRIORITY_BALANCE_THRESHOLD AND years_delinquent <= 1.
LOW_PRIORITY_BALANCE_THRESHOLD: float = 500.0
LOW_PRIORITY_MAX_YEARS_DELINQUENT: int = 1


# ---------------------------------------------------------------------------
# Result shape.
# ---------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class TaxDefaultQualificationResult:
    """The output of qualify_tax_default(row). Carries the qualification
    verdict plus the per-criterion outcomes for audit / review-routing."""

    qualification_status: str  # one of QUALIFICATION_STATUSES
    lead_type: Optional[str]   # one of TAX_DEFAULT_LEAD_TYPES (None if NOT_QUALIFIED)
    review_reason: Optional[str]
    criteria: dict[str, bool] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------

def qualify_tax_default(row: dict) -> TaxDefaultQualificationResult:
    """Run the §3.3 five-criteria gate on one tax-default candidate row.

    The row dict shape (a thin contract — the adapter populates it):

      source_role          str (one of the v5.5.0 source roles)
      source_id            str
      source_url           str
      raw_record_id        str
      captured_at          ISO 8601 str
      default_condition    str (one of DEFAULT_CONDITION_FLAGS) — set by
                           the adapter from its own raw flags
      parcel_id            str | None
      account_number       str | None
      tax_map_id           str | None
      situs_address        str | None
      legal_description    str | None
      balance              float | None  (optional — informs low-priority)
      years_delinquent     int | None    (optional — informs low-priority)

    Returns TaxDefaultQualificationResult. The criteria dict carries the
    per-criterion (a/b/c/d/e) booleans so the reviewer / §20 can audit.
    """
    if not isinstance(row, dict):
        return TaxDefaultQualificationResult(
            qualification_status="NOT_QUALIFIED",
            lead_type=None,
            review_reason="row_is_not_a_dict",
            criteria={},
        )

    criteria: dict[str, bool] = {}

    # (a) — Official / authorized source role.
    source_role = row.get("source_role")
    criteria["a_official_source"] = source_role in OFFICIAL_TAX_DEFAULT_ROLES

    # (b) — Real default condition.
    default_condition = row.get("default_condition")
    criteria["b_default_condition"] = (
        default_condition in DEFAULT_CONDITION_FLAGS
    )

    # (c) — Property tie.
    criteria["c_property_tie"] = any(
        _nonempty(row.get(f)) for f in PROPERTY_TIE_FIELDS
    )

    # (d) — Source proof.
    criteria["d_source_proof"] = all(
        _nonempty(row.get(f)) for f in SOURCE_PROOF_FIELDS
    )

    # (e) — Not merely generic roll enrichment. The b-criterion already
    # excludes "on the roll but no default flag" — so e is satisfied
    # whenever (b) is satisfied AND the row is not explicitly tagged as
    # generic-roll-enrichment (an adapter that knows it's emitting
    # enrichment sets `is_generic_roll_enrichment=True`).
    criteria["e_not_generic_roll"] = (
        criteria["b_default_condition"]
        and not bool(row.get("is_generic_roll_enrichment"))
    )

    # All five must hold for QUALIFIED.
    if all(criteria.values()):
        return TaxDefaultQualificationResult(
            qualification_status="QUALIFIED",
            lead_type=_lead_type_for(default_condition, row),
            review_reason=None,
            criteria=criteria,
        )

    # If (a)+(b)+(c) hold but (d) source-proof is missing, route review_required
    # (the row likely belongs to a real default — the operator needs to
    # produce the proof, not the framework to drop the lead silently).
    if (
        criteria["a_official_source"]
        and criteria["b_default_condition"]
        and criteria["c_property_tie"]
        and not criteria["d_source_proof"]
    ):
        missing = [f for f in SOURCE_PROOF_FIELDS if not _nonempty(row.get(f))]
        return TaxDefaultQualificationResult(
            qualification_status="REVIEW_REQUIRED",
            lead_type="review_required",
            review_reason=f"source_proof_incomplete: missing {missing}",
            criteria=criteria,
        )

    # Otherwise NOT_QUALIFIED — the row is enrichment / unsourced / not a
    # real default. The adapter should treat it as enrichment, not a lead.
    failing = [k for k, v in criteria.items() if not v]
    return TaxDefaultQualificationResult(
        qualification_status="NOT_QUALIFIED",
        lead_type=None,
        review_reason=f"failed_criteria: {failing}",
        criteria=criteria,
    )


def dedupe_tax_default_results(
    rows_with_results: list[tuple[dict, TaxDefaultQualificationResult]],
) -> list[tuple[dict, TaxDefaultQualificationResult]]:
    """Apply the §3.3 dedupe rule: one lead per (account, parcel) per
    current default condition. Two rows that share the same dedupe key
    collapse into one — the higher-severity row wins.

    The dedupe key (in priority order):
      1. account_number, if non-empty
      2. parcel_id, if non-empty
      3. (situs_address, default_condition) as fallback

    Severity ranking for the same dedupe key (highest first):
      tax_sale, tax_foreclosure, tax_certificate, tax_default,
      tax_default_low_priority, review_required
    """
    severity = {
        "tax_sale": 6,
        "tax_foreclosure": 5,
        "tax_certificate": 4,
        "tax_default": 3,
        "tax_default_low_priority": 2,
        "review_required": 1,
    }

    bucket: dict[tuple, tuple[dict, TaxDefaultQualificationResult]] = {}
    for row, result in rows_with_results:
        if result.qualification_status == "NOT_QUALIFIED":
            continue  # NOT_QUALIFIED rows aren't leads; they don't dedupe in
        key = _dedupe_key(row, result)
        existing = bucket.get(key)
        if existing is None:
            bucket[key] = (row, result)
            continue
        # Both rows present — pick higher-severity, then more-recent.
        _, existing_result = existing
        if (severity.get(result.lead_type, 0)
                > severity.get(existing_result.lead_type, 0)):
            bucket[key] = (row, result)
    return list(bucket.values())


# ---------------------------------------------------------------------------
# Internal helpers.
# ---------------------------------------------------------------------------

def _nonempty(value) -> bool:
    """True when `value` is a non-empty string or a non-None scalar."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _lead_type_for(
    default_condition: Optional[str], row: dict,
) -> str:
    """Map a (default_condition, balance, years_delinquent) tuple to one of
    the §3.3 canonical lead types."""
    if default_condition == "tax_sale_pending" or default_condition == "tax_sale_struck_off" \
            or default_condition == "tax_sale_sold":
        return "tax_sale"
    if default_condition == "tax_foreclosure_filed":
        return "tax_foreclosure"
    if default_condition == "tax_certificate_issued":
        return "tax_certificate"
    # default_condition is "delinquent" / "collection_active" / "redemption_period"
    # — fold the low-priority case (small balance, recent) here.
    balance = row.get("balance")
    years_delinquent = row.get("years_delinquent")
    if (
        isinstance(balance, (int, float))
        and balance <= LOW_PRIORITY_BALANCE_THRESHOLD
        and isinstance(years_delinquent, int)
        and 0 <= years_delinquent <= LOW_PRIORITY_MAX_YEARS_DELINQUENT
    ):
        return "tax_default_low_priority"
    return "tax_default"


def _dedupe_key(row: dict, result: TaxDefaultQualificationResult) -> tuple:
    """Compute the dedupe key per §3.3. Falls back through account_number →
    parcel_id → (situs_address, default_condition)."""
    account = row.get("account_number")
    if isinstance(account, str) and account.strip():
        return ("account", account.strip(), row.get("default_condition"))
    parcel = row.get("parcel_id")
    if isinstance(parcel, str) and parcel.strip():
        return ("parcel", parcel.strip(), row.get("default_condition"))
    situs = (row.get("situs_address") or "").strip()
    return ("situs", situs, row.get("default_condition"))
