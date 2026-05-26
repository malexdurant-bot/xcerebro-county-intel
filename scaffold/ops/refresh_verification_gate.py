"""
refresh_verification_gate — v5.5.0 §6.4 / §6.5 pre-publish gate.

The daily-refresh workflow must call this gate AFTER building the new
dashboard payload but BEFORE replacing the live Pages artifact. The gate's
job is canon:

  - Reject empty / near-empty boards.
  - Reject all-Unknown boards when enrichment was possible.
  - Preserve last-good: a failed gate means do-not-publish (the previous
    artifact stays live), NOT publish-the-broken-one.

§6.4 thresholds (canonical defaults — operators may tighten via county
config). The defaults are intentionally conservative so a county build
fails LOUDLY rather than publishes a broken board:

  MIN_LEAD_COUNT:               1
  MIN_RESOLVED_OWNER_FRACTION:  0.5  (50% of leads must have a known owner)
  MIN_RESOLVED_ADDRESS_FRACTION: 0.5 (50% must have property_full_address)
  MIN_ACTIONABLE_FRACTION:      0.5  (50% must reach APPROVED_FOR_DASHBOARD,
                                      not REVIEW_REQUIRED)

The gate also defines BOUNDED-PULL-WINDOW expectations per distress type
(§6.6), which a refresh workflow consumes to decide what to re-fetch:

  scheduled-event types → FORWARD window (today through horizon, default
    90 days). Past sale dates not pulled as upcoming leads.
  recorded-event types  → SHORT BACKWARD window (recently recorded by
    county recorded date, default 60 days).
  status types          → NO date window — full current roll.

This module is universal framework code: no county / state / vendor literal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional


# §6.4 canonical defaults (operator may tighten via county config).
DEFAULT_MIN_LEAD_COUNT = 1
DEFAULT_MIN_RESOLVED_OWNER_FRACTION = 0.5
DEFAULT_MIN_RESOLVED_ADDRESS_FRACTION = 0.5
DEFAULT_MIN_ACTIONABLE_FRACTION = 0.5

# §6.6 bounded-pull-window registry.
DEFAULT_FORWARD_HORIZON_DAYS = 90
DEFAULT_BACKWARD_HORIZON_DAYS = 60


SCHEDULED_EVENT_PULL_TYPES: frozenset[str] = frozenset({
    "notice_of_sale", "notice_of_default", "notice_of_substitute_trustee_sale",
    "sheriff_sale", "trustees_deed_upon_sale", "tax_deed",
    "tax_sale_certificate", "tax_foreclosure_notice",
})

RECORDED_EVENT_PULL_TYPES: frozenset[str] = frozenset({
    "lis_pendens", "civil_judgment", "abstract_of_judgment",
    "judgment_lien", "mechanics_lien", "construction_lien",
    "federal_tax_lien", "state_tax_lien",
    "letters_testamentary", "letters_of_administration",
    "determination_of_heirship", "muniment_of_title",
    "affidavit_of_heirship", "executors_deed", "administrators_deed",
    "code_violation_notice", "municipal_lien",
    "eviction_filing", "writ_of_possession",
    "divorce_filing", "final_decree_of_divorce", "marital_property_division",
    "bankruptcy_petition", "condemnation_notice", "demolition_order",
    "certificate_of_title", "sheriff_sale_surplus",
})

STATUS_PULL_TYPES: frozenset[str] = frozenset({
    "tax_default", "tax_default_low_priority", "tax_delinquency",
})


@dataclass(frozen=True, kw_only=True)
class RefreshVerificationResult:
    """Verdict from the §6.4 gate."""

    verdict: str  # "PUBLISH" | "DO_NOT_PUBLISH"
    reason: Optional[str]
    counts: dict[str, int] = field(default_factory=dict)
    fractions: dict[str, float] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


def verify_refresh_publishable(
    scored_leads: list,
    *,
    min_lead_count: int = DEFAULT_MIN_LEAD_COUNT,
    min_resolved_owner_fraction: float = DEFAULT_MIN_RESOLVED_OWNER_FRACTION,
    min_resolved_address_fraction: float = DEFAULT_MIN_RESOLVED_ADDRESS_FRACTION,
    min_actionable_fraction: float = DEFAULT_MIN_ACTIONABLE_FRACTION,
    enrichment_join_unavailable: bool = False,
) -> RefreshVerificationResult:
    """The §6.4 publish gate. Returns DO_NOT_PUBLISH on any threshold miss.

    The §6.5 last-good preservation rule is the caller's responsibility:
    if this gate returns DO_NOT_PUBLISH, the workflow must NOT overwrite
    the live Pages artifact — the last-good board stays live.
    """
    counts: dict[str, int] = {}
    fractions: dict[str, float] = {}
    notes: list[str] = []

    n = len(scored_leads or [])
    counts["lead_count"] = n

    if n < min_lead_count:
        return RefreshVerificationResult(
            verdict="DO_NOT_PUBLISH",
            reason=f"lead_count {n} < min_lead_count {min_lead_count} "
                   "(§6.4 — empty board never publishes)",
            counts=counts,
        )

    n_actionable = sum(
        1 for sl in scored_leads
        if sl.get("lead_status") == "APPROVED_FOR_DASHBOARD"
    )
    n_known_owner = sum(
        1 for sl in scored_leads
        if (sl.get("owner_name") or "")
        and "unidentified party" not in str(sl.get("owner_name", "")).lower()
        and "UNKNOWN" not in str(sl.get("owner_name", "")).upper()
    )
    n_with_address = sum(
        1 for sl in scored_leads
        if (
            sl.get("parcel_display") or {}
        ).get("situs_address")
    )
    counts["actionable"] = n_actionable
    counts["known_owner"] = n_known_owner
    counts["with_address"] = n_with_address

    actionable_frac = n_actionable / n if n else 0.0
    owner_frac = n_known_owner / n if n else 0.0
    address_frac = n_with_address / n if n else 0.0
    fractions["actionable"] = round(actionable_frac, 4)
    fractions["known_owner"] = round(owner_frac, 4)
    fractions["with_address"] = round(address_frac, 4)

    if actionable_frac < min_actionable_fraction:
        return RefreshVerificationResult(
            verdict="DO_NOT_PUBLISH",
            reason=f"actionable_fraction {actionable_frac:.2%} < "
                   f"{min_actionable_fraction:.2%} (§6.4 — too many "
                   f"REVIEW_REQUIRED leads to publish)",
            counts=counts, fractions=fractions,
        )

    # §6.4 enrichment-sanity floors — only enforced when enrichment is
    # expected (i.e. the operator did not declare a county-wide enrichment
    # outage via enrichment_join_unavailable).
    if not enrichment_join_unavailable:
        if owner_frac < min_resolved_owner_fraction:
            return RefreshVerificationResult(
                verdict="DO_NOT_PUBLISH",
                reason=f"resolved_owner_fraction {owner_frac:.2%} < "
                       f"{min_resolved_owner_fraction:.2%} (§6.4 — "
                       f"all-Unknown / dead board)",
                counts=counts, fractions=fractions,
            )
        if address_frac < min_resolved_address_fraction:
            return RefreshVerificationResult(
                verdict="DO_NOT_PUBLISH",
                reason=f"resolved_address_fraction {address_frac:.2%} < "
                       f"{min_resolved_address_fraction:.2%} (§6.4 — "
                       f"enrichment failed to attach addresses)",
                counts=counts, fractions=fractions,
            )
    else:
        notes.append(
            "enrichment_join_unavailable=True — owner / address floors "
            "skipped per §4.3 operator override carve-out."
        )

    return RefreshVerificationResult(
        verdict="PUBLISH",
        reason=None,
        counts=counts, fractions=fractions,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# §6.6 bounded-pull-window registry helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class PullWindow:
    canonical_doc_type: str
    direction: str            # "FORWARD" | "BACKWARD" | "NONE"
    start: Optional[date]
    end: Optional[date]
    horizon_days: Optional[int]


def pull_window_for(
    canonical_doc_type: str,
    *,
    as_of: Optional[date] = None,
    forward_horizon_days: int = DEFAULT_FORWARD_HORIZON_DAYS,
    backward_horizon_days: int = DEFAULT_BACKWARD_HORIZON_DAYS,
) -> PullWindow:
    """Return the §6.6 bounded pull window for a given canonical_doc_type.

    - Scheduled-event types → FORWARD window [as_of, as_of + horizon].
      Past sale dates are NOT pulled as upcoming leads.
    - Recorded-event types → BACKWARD window [as_of - horizon, as_of].
    - Status types → NO window (direction='NONE') — pull the full current
      roll/status set.
    - Unknown types → BACKWARD window (conservative default).
    """
    as_of = as_of or date.today()
    cdt = (canonical_doc_type or "").strip().lower()
    if cdt in SCHEDULED_EVENT_PULL_TYPES:
        return PullWindow(
            canonical_doc_type=cdt,
            direction="FORWARD",
            start=as_of,
            end=as_of + timedelta(days=forward_horizon_days),
            horizon_days=forward_horizon_days,
        )
    if cdt in STATUS_PULL_TYPES:
        return PullWindow(
            canonical_doc_type=cdt,
            direction="NONE",
            start=None, end=None, horizon_days=None,
        )
    # Default to recorded-event treatment (incl. unknown types).
    return PullWindow(
        canonical_doc_type=cdt,
        direction="BACKWARD",
        start=as_of - timedelta(days=backward_horizon_days),
        end=as_of,
        horizon_days=backward_horizon_days,
    )
