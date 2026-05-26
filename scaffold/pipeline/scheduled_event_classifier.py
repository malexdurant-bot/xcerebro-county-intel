"""
scheduled_event_classifier — v5.5.0 §3.9 scheduled-event classification.

Scheduled-sale adapters (foreclosure auction calendars, tax-sale portals,
sheriff-sale calendars) must classify every record into ONE of five
categories, so the framework never publishes a past sale as an upcoming
lead and historical context attaches as a recurrence signal rather than as
a fake lead.

The §3.9 categories:

  UPCOMING_SALE
    Sale_date is in the future (>= today, < today + window). The classic
    actionable scheduled-event lead. Only UPCOMING_SALE records appear in
    upcoming-sale views.

  PAST_SALE
    Sale_date is in the past AND no post-sale title / surplus / redemption
    record is present. The distress event is closed — does NOT appear as
    an upcoming-sale lead. May attach as HISTORICAL_CONTEXT_ONLY to a
    current lead on the same property if/when one arises.

  POST_SALE_TITLE_EVENT
    Sale occurred AND a post-sale title instrument is now recorded —
    sheriff_deed / certificate_of_title / trustees_deed_upon_sale / tax_deed
    / struck_off. This IS a valid lead (the foreclosure cycle concluded,
    new owner / former owner are knowable, dispossession just happened).

  SURPLUS_EVENT
    Sale occurred AND surplus / excess proceeds are recorded
    (sheriff_sale_surplus). The lead subject is the FORMER owner — they
    are entitled to the surplus and are a high-value contact target.

  HISTORICAL_CONTEXT_ONLY
    Past-sale record with no post-sale outcome data AND the property
    already has a CURRENT distress lead (passed in via context). Attaches
    as a `prior_distress` stacking signal — recurrence is a strong
    motivation signal — and does NOT appear as its own lead.

The §3.9 canon also clarifies STATUS-BASED DISTRESS:

  Tax delinquency, code enforcement violations, ongoing liens, etc. are
  NOT scheduled events. They are ongoing conditions — current until
  resolved (paid, abated, released). They do NOT pass through this
  classifier; the §3.3 tax-default qualification gate (and parallel
  status-condition gates) handles them. This classifier is exclusively
  for date-keyed scheduled-event records.

This module is universal framework code: no county / state / vendor
literal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional


SCHEDULED_EVENT_CATEGORIES: tuple[str, ...] = (
    "UPCOMING_SALE",
    "PAST_SALE",
    "POST_SALE_TITLE_EVENT",
    "SURPLUS_EVENT",
    "HISTORICAL_CONTEXT_ONLY",
)
"""§3.9 categories — only UPCOMING_SALE / POST_SALE_TITLE_EVENT /
SURPLUS_EVENT may be ORIGINATING leads. PAST_SALE and
HISTORICAL_CONTEXT_ONLY are not displayed as upcoming-sale leads."""

# Doc types that ARE status-based ongoing conditions, not scheduled events.
# This classifier rejects them — they belong to the §3.3 / status-condition
# gate, not here.
STATUS_BASED_DOC_TYPES: frozenset[str] = frozenset({
    # Tax-status family
    "tax_default",
    "tax_default_low_priority",
    "tax_delinquency",
    # Code / municipal — ongoing until abated
    "code_violation_notice",
    "municipal_lien",
    "demolition_order",
    "condemnation_notice",
    # Liens — ongoing until released
    "federal_tax_lien",
    "state_tax_lien",
    "mechanics_lien",
    "construction_lien",
    "judgment_lien",
    "hoa_lien",
    "hospital_lien",
    "water_lien",
})

# Doc types that ARE post-sale title events (concluded foreclosure cycle).
POST_SALE_TITLE_DOC_TYPES: frozenset[str] = frozenset({
    "certificate_of_title",
    "sheriff_deed",
    "trustees_deed_upon_sale",
    "tax_deed",
})

# Doc types that ARE surplus events (post-sale excess-proceeds records).
SURPLUS_DOC_TYPES: frozenset[str] = frozenset({
    "sheriff_sale_surplus",
})

# Default actionable forward horizon for upcoming-sale views.
DEFAULT_FORWARD_HORIZON_DAYS: int = 90


@dataclass(frozen=True, kw_only=True)
class ScheduledEventClassification:
    """One §3.9 verdict on one scheduled-event candidate record."""

    category: str  # one of SCHEDULED_EVENT_CATEGORIES
    canonical_doc_type: str
    sale_date: Optional[date]
    is_lead_originating: bool  # True when category may originate a lead
    notes: tuple[str, ...] = field(default_factory=tuple)


def classify_scheduled_event(
    record: dict,
    *,
    as_of: Optional[date] = None,
    forward_horizon_days: int = DEFAULT_FORWARD_HORIZON_DAYS,
    has_current_distress_on_property: bool = False,
) -> ScheduledEventClassification:
    """Classify one scheduled-event candidate record per §3.9.

    Record shape (a thin contract — the adapter populates it):
      canonical_doc_type   str (required)
      sale_date            ISO 8601 date string (required for scheduled events)

    Args:
      record: the candidate record.
      as_of: cutoff date — defaults to today. Records with sale_date >=
        as_of are UPCOMING_SALE; otherwise PAST_SALE.
      forward_horizon_days: optional cap on UPCOMING_SALE window
        (default 90 days). Records with sale_date > as_of + horizon are
        still UPCOMING_SALE (the daily refresh's pull-window is the gate,
        not the classifier — but the classifier records the horizon for
        operator visibility).
      has_current_distress_on_property: per §3.9 — when a PAST_SALE record
        is checked against a property that already has a CURRENT distress
        lead, it returns HISTORICAL_CONTEXT_ONLY instead of PAST_SALE so
        the caller knows to attach it as a recurrence signal.

    Returns ScheduledEventClassification.
    """
    if not isinstance(record, dict):
        return ScheduledEventClassification(
            category="PAST_SALE",
            canonical_doc_type="",
            sale_date=None,
            is_lead_originating=False,
            notes=("record_is_not_a_dict",),
        )

    canonical_doc_type = (record.get("canonical_doc_type") or "").strip().lower()
    as_of = as_of or date.today()
    sale_date = _parse_iso_date(record.get("sale_date"))

    # Status-based distress is NOT a scheduled event — explicit rejection.
    if canonical_doc_type in STATUS_BASED_DOC_TYPES:
        return ScheduledEventClassification(
            category="HISTORICAL_CONTEXT_ONLY",
            canonical_doc_type=canonical_doc_type,
            sale_date=sale_date,
            is_lead_originating=False,
            notes=(
                f"{canonical_doc_type} is a status-based ongoing condition, "
                "not a scheduled event. Use the §3.3 tax-default gate or the "
                "parallel status-condition gate, not this classifier.",
            ),
        )

    # POST_SALE_TITLE_EVENT — a recorded title instrument concluding the
    # foreclosure cycle. Lead-originating in its own right.
    if canonical_doc_type in POST_SALE_TITLE_DOC_TYPES:
        return ScheduledEventClassification(
            category="POST_SALE_TITLE_EVENT",
            canonical_doc_type=canonical_doc_type,
            sale_date=sale_date,
            is_lead_originating=True,
            notes=(
                "Post-sale title instrument — the foreclosure cycle concluded. "
                "The former owner is the lead subject (recently dispossessed).",
            ),
        )

    # SURPLUS_EVENT — post-sale excess-proceeds record.
    if canonical_doc_type in SURPLUS_DOC_TYPES:
        return ScheduledEventClassification(
            category="SURPLUS_EVENT",
            canonical_doc_type=canonical_doc_type,
            sale_date=sale_date,
            is_lead_originating=True,
            notes=(
                "Surplus / excess-proceeds event. The former owner is the "
                "lead subject (entitled to the surplus).",
            ),
        )

    # UPCOMING vs PAST — sale_date is the gate.
    if sale_date is None:
        # Scheduled event with no sale_date is unclassifiable — route to
        # HISTORICAL_CONTEXT_ONLY (not lead-originating) and note it.
        return ScheduledEventClassification(
            category="HISTORICAL_CONTEXT_ONLY",
            canonical_doc_type=canonical_doc_type,
            sale_date=None,
            is_lead_originating=False,
            notes=(
                "Scheduled event missing sale_date — cannot classify as "
                "upcoming or past. Adapter should populate sale_date or "
                "route the record to REVIEW_REQUIRED.",
            ),
        )

    if sale_date >= as_of:
        # Sale is in the future — UPCOMING_SALE.
        in_window = sale_date <= (as_of + timedelta(days=forward_horizon_days))
        return ScheduledEventClassification(
            category="UPCOMING_SALE",
            canonical_doc_type=canonical_doc_type,
            sale_date=sale_date,
            is_lead_originating=True,
            notes=() if in_window else (
                f"Sale date {sale_date.isoformat()} beyond "
                f"{forward_horizon_days}-day actionable horizon. The daily "
                "refresh's pull-window enforces the horizon, not this "
                "classifier.",
            ),
        )

    # Sale is in the past — PAST_SALE or HISTORICAL_CONTEXT_ONLY.
    if has_current_distress_on_property:
        return ScheduledEventClassification(
            category="HISTORICAL_CONTEXT_ONLY",
            canonical_doc_type=canonical_doc_type,
            sale_date=sale_date,
            is_lead_originating=False,
            notes=(
                "Past sale on a property that already has a current distress "
                "lead. Attaches as prior_distress recurrence signal, NOT as "
                "its own upcoming-sale lead.",
            ),
        )

    return ScheduledEventClassification(
        category="PAST_SALE",
        canonical_doc_type=canonical_doc_type,
        sale_date=sale_date,
        is_lead_originating=False,
        notes=(
            "Past sale with no post-sale title / surplus record — distress "
            "resolved. NOT an upcoming-sale lead; the dashboard MUST NOT "
            "display it as one (per §3.9 / §4.6).",
        ),
    )


def _parse_iso_date(value) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None
