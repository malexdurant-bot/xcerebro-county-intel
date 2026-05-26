"""
dashboard_contract — v5.5.0 §5 renderer canon.

The framework's dashboard renderer (dashboard.js / dashboard.html) must read
a specific set of fields off each row in `dashboard.json`. A county adapter
that emits the wrong field names is a CONTRACT VIOLATION — not a silent
dead-filter on the dashboard.

This module is the executable contract: it declares the required and
optional fields, exposes a `validate_dashboard_row()` check the renderer
test consumes, and exposes the canonical filter-default values so the
universal dashboard template stays consistent across counties.

Per §5 canon (distilled from the three-county build):

  - Card layout, no spreadsheet default.
  - Distress-type filter lists ONLY genuine distress types (per §3.6).
  - Filters start UNSELECTED / neutral; clients click-to-select.
  - Default landing view = all records, address-resolved sorts above
    no-address; no distress type pinned to the top.
  - Recency keys off the COUNTY RECORDED date, never the scrape date.
  - NO build-status banner on the client view; the banner node may only
    exist to surface a "could not load data" error.
  - No stale labels: county name + source names must be correct.
  - Field-mapping is a contract — wrong field names are a violation,
    not a silent dead-filter.

This module is universal framework code: no county / state / vendor literal.
"""

from __future__ import annotations

from typing import Optional


# ---------------------------------------------------------------------------
# §5.9 — the dashboard row contract. Every county's dashboard.json `records`
# array MUST carry these field names on every row. Renderer reads them by
# name; a county adapter emitting different names is a contract violation.
# ---------------------------------------------------------------------------

REQUIRED_DASHBOARD_FIELDS: frozenset[str] = frozenset({
    "lead_id",
    "owner_name",
    "owner_type",
    "signal_type",            # per-canonical distress type chip
    "property_full_address",  # address resolved through enrichment; "" when not
    "recorded_date",          # county recorded/filed date — NOT scrape date
    "review_status",          # APPROVED_FOR_DASHBOARD / REVIEW_REQUIRED
    "lead_origin_type",       # §3.8 — RECORDED_EVENT/TAX_DEFAULT/OWNER_STATUS/...
    "enrichment_status",      # ENRICHED / UNENRICHED
    "event_source",           # source_id of the originating event
})
"""Required field names on every dashboard row (§5.9). A row missing any
of these is a contract violation; renderer treats the row as malformed."""

OPTIONAL_DASHBOARD_FIELDS: frozenset[str] = frozenset({
    "scored_lead_id",
    "primary_parcel_id",
    "absentee_owner",
    "out_of_state_owner",
    "years_delinquent",
    "balance",
    "prior_distress",         # §3.9 recurrence-signal stack
    "title_complexity_tier",
    "deal_paths",
    "assessed_value",
    "last_sale_price",
    "last_sale_date",
    "year_built",
    "owner_source",
    "enrichment_source",
    "qualification_status",
    # Address-resolution split — drives the §5.3 address-resolved sort.
    "address_resolved",       # bool — non-empty property_full_address
})


# §5.3 default sort key: tuple ordering — `address_resolved=True` comes before
# `address_resolved=False`, then recorded_date desc.
def default_sort_key(row: dict) -> tuple:
    addr = bool((row.get("property_full_address") or "").strip())
    rec = row.get("recorded_date") or ""
    return (not addr, rec * -1 if isinstance(rec, int) else "")  # not-addr first → addr-resolved on top


# §5.4 standard filters (canonical names). Every county's dashboard must
# expose all of these; missing filters are a contract violation.
STANDARD_FILTERS: tuple[str, ...] = (
    "distress_type",
    "owner_type",
    "recency",            # NEW = same-day; "last_30_days" range
    "years_delinquent",   # client-controlled 1/2/3/4/5+
    "absentee_or_out_of_state",
    "address_resolved",
    "review_status",
    "search",
    "reset",
)

# §5.2 — filters START UNSELECTED (neutral). The dashboard template must NOT
# pre-check filters that "dump everything"; the operator clicks-to-select.
DEFAULT_FILTER_STATE: dict[str, Optional[str]] = {
    "distress_type": None,
    "owner_type": None,
    "recency": None,
    "years_delinquent": None,
    "absentee_or_out_of_state": None,
    "address_resolved": None,
    "review_status": None,
    "search": "",
}

# §5.6 — leads that are default-HIDDEN but toggle-able (never deleted).
DEFAULT_HIDDEN_LEAD_TYPES: frozenset[str] = frozenset({
    "tax_default_low_priority",  # §3.3 / §5.6 — small balance / recent onset
    "civil_judgment",            # §3.6 — debtor-only, no property attachment
    "abstract_of_judgment",      # ditto until property attachment proven
})


# §5.5 — recency is keyed off the COUNTY RECORDED date, never the scrape
# date. Source-of-truth field name on every dashboard row:
RECENCY_FIELD_NAME = "recorded_date"


# §5.7 — the build-status banner is NOT shown on the client view. The
# banner DOM node may exist only to surface a "could not load data" error.
# This list of tokens MUST NOT appear in the rendered client banner / chip /
# label text. The renderer test enforces it.
BANNER_PROHIBITED_TOKENS: tuple[str, ...] = (
    "PARTIAL_BUILD",
    "SOURCE_LIMITED",
    "Cloudflare",
    "reCAPTCHA",
    "stealth-Playwright",
    "stealth Playwright",
    "DEPLOY_OK",
    "DEPLOY_BLOCKED",
    "NEEDS_OPERATOR_REVIEW",
    "DEPLOY_FAILED",
    "pipeline",
    "recon",
)


def validate_dashboard_row(row: dict) -> list[str]:
    """Return a list of contract violations for a dashboard row.

    Empty list = the row is contract-compliant.
    """
    violations: list[str] = []
    if not isinstance(row, dict):
        return ["row is not a dict"]
    for field in REQUIRED_DASHBOARD_FIELDS:
        if field not in row:
            violations.append(
                f"missing required field {field!r} "
                f"(§5.9 dashboard contract violation — renderer reads this "
                f"field by name; the county adapter must emit it)"
            )
    # signal_type must be a per-canonical chip — not over-flattened
    # umbrella values like "distress" or "lien" (§5.9).
    sig = row.get("signal_type")
    if isinstance(sig, str) and sig.strip().lower() in {
        "distress", "all", "any", "generic",
    }:
        violations.append(
            f"signal_type {sig!r} is an over-flattened umbrella value — "
            "§5.9 requires per-canonical signal_type chips"
        )
    return violations
