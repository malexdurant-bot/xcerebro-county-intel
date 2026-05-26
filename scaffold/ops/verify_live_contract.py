"""
verify_live_contract — v5.5.0 §7.1 live-URL verification contract.

Before any county dashboard is called "done," the build MUST verify the
LIVE URL (or a local mirror of the published artifact) against this
contract. The contract has two halves:

  STATIC checks — run without a browser. Verify the served artifact is
  fetchable, parseable, non-empty, and stale-label-clean. These checks
  CAN run in CI on a public Pages URL with stdlib urllib alone.

  INTERACTIVE checks — require a headless browser (Playwright preferred).
  Verify the rendered dashboard: cards present, filters click-test changes
  the visible count, no console errors. These are spec'd here but the
  Playwright implementation is invoked by the county build's own
  verify_live.py — this module only owns the CONTRACT.

This module is universal framework code: no county / state / vendor
literal.

§7.1 contract — every county dashboard MUST pass:

  STATIC.1   the URL fetches with 200 OK and a non-empty body
  STATIC.2   the body is HTML (Content-Type starts with text/html OR
             the body begins with <!DOCTYPE html or <html)
  STATIC.3   the body is NOT a JSON-parse-error (no DOCTYPE-as-JSON, no
             404 page served instead of the dashboard)
  STATIC.4   the data artifact (data.js / data.json — discovered from the
             HTML) is reachable
  STATIC.5   the data artifact is non-empty and parseable
  STATIC.6   the data artifact carries the required v5.5.0 dashboard
             contract fields on the first row
  STATIC.7   stale-label scanner finds zero foreign-county tokens in the
             served HTML / JS (excluding the county's own tokens)
  STATIC.8   no banner-prohibited token (PARTIAL_BUILD / SOURCE_LIMITED /
             Cloudflare / DEPLOY_OK / pipeline / recon) appears in the
             user-visible HTML

  INTERACTIVE.1   the dashboard renders at least one lead card
  INTERACTIVE.2   the lead card carries owner_name + property_full_address
                  (or honest fallback) — no UNDEFINED / NaN
  INTERACTIVE.3   clicking a distress-type filter changes the visible
                  row count
  INTERACTIVE.4   clicking the recency filter changes the visible row count
  INTERACTIVE.5   clicking the address-resolved filter changes the visible
                  row count
  INTERACTIVE.6   reset clears all filters and shows the original count
  INTERACTIVE.7   no uncaught JavaScript console errors during load + the
                  filter sequence

The two halves return one verdict each — STATIC_OK / STATIC_BLOCKED and
INTERACTIVE_OK / INTERACTIVE_BLOCKED. The county build is "done" only
when both return _OK.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from typing import Optional


STATIC_CHECKS: tuple[str, ...] = (
    "static.1_url_fetches_200_with_body",
    "static.2_body_is_html",
    "static.3_body_is_not_json_parse_error",
    "static.4_data_artifact_reachable",
    "static.5_data_artifact_parseable_non_empty",
    "static.6_data_carries_v5_5_0_dashboard_contract_fields",
    "static.7_no_stale_foreign_county_labels",
    "static.8_no_banner_prohibited_tokens",
)

INTERACTIVE_CHECKS: tuple[str, ...] = (
    "interactive.1_at_least_one_lead_card_renders",
    "interactive.2_owner_and_address_render_or_honest_fallback",
    "interactive.3_distress_type_filter_changes_visible_count",
    "interactive.4_recency_filter_changes_visible_count",
    "interactive.5_address_resolved_filter_changes_visible_count",
    "interactive.6_reset_restores_original_count",
    "interactive.7_no_uncaught_js_errors",
)


@dataclass(frozen=True, kw_only=True)
class LiveVerificationCheck:
    name: str
    status: str   # "PASS" | "FAIL" | "SKIPPED"
    detail: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class LiveVerificationResult:
    half: str    # "STATIC" | "INTERACTIVE"
    verdict: str # "STATIC_OK" / "STATIC_BLOCKED" / "INTERACTIVE_OK" / "INTERACTIVE_BLOCKED"
    checks: tuple[LiveVerificationCheck, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# STATIC half — no browser required. Implemented in stdlib so CI can run it
# against any public Pages URL.
# ---------------------------------------------------------------------------

def run_static_checks(
    *,
    dashboard_html: str,
    data_artifact_text: Optional[str],
    current_county_slug: Optional[str] = None,
) -> LiveVerificationResult:
    """Run the STATIC half against an in-memory copy of the served HTML +
    the linked data artifact. The county build's verify_live.py fetches
    these from the live URL and passes them in here — this function does
    no network I/O.

    Returns LiveVerificationResult half='STATIC'. The verdict is STATIC_OK
    iff every check is PASS or SKIPPED; any FAIL → STATIC_BLOCKED.
    """
    from scaffold.ops import stale_label_scanner
    from scaffold.pipeline import dashboard_contract as dc

    checks: list[LiveVerificationCheck] = []

    # static.1 — body non-empty
    checks.append(LiveVerificationCheck(
        name="static.1_url_fetches_200_with_body",
        status="PASS" if dashboard_html and dashboard_html.strip() else "FAIL",
        detail=None if dashboard_html else "empty body — URL did not return content",
    ))

    # static.2 — body is HTML
    is_html = bool(re.match(r"\s*(<!doctype html|<html)", dashboard_html or "", re.IGNORECASE))
    checks.append(LiveVerificationCheck(
        name="static.2_body_is_html",
        status="PASS" if is_html else "FAIL",
        detail=None if is_html else "body does not start with <!DOCTYPE html or <html",
    ))

    # static.3 — body is not a JSON parse error (DOCTYPE-as-JSON
    # symptom: caller asked for JSON and got HTML by mistake).
    looks_like_json_attempt = (dashboard_html or "").lstrip().startswith(("{", "["))
    checks.append(LiveVerificationCheck(
        name="static.3_body_is_not_json_parse_error",
        status="PASS" if is_html and not looks_like_json_attempt else "FAIL",
        detail=None if is_html else (
            "DOCTYPE-as-JSON symptom — the server returned HTML when caller "
            "expected JSON (404 page served as data.json is the classic bug)"
        ),
    ))

    # static.4 — data artifact reachable (the caller already fetched it; we
    # just check it's non-empty).
    checks.append(LiveVerificationCheck(
        name="static.4_data_artifact_reachable",
        status="PASS" if (data_artifact_text or "").strip() else "FAIL",
        detail=None if data_artifact_text else (
            "data artifact empty or not supplied — verify the data.js / "
            "data.json path the renderer loads is actually served"
        ),
    ))

    # static.5 — data artifact parseable.
    rows = _parse_data_artifact(data_artifact_text or "")
    checks.append(LiveVerificationCheck(
        name="static.5_data_artifact_parseable_non_empty",
        status="PASS" if rows else "FAIL",
        detail=None if rows else (
            "data artifact present but parseable rows could not be extracted "
            "(checked window.LEADS, top-level JSON array, .records key)"
        ),
    ))

    # static.6 — first row carries the v5.5.0 dashboard contract fields.
    if rows:
        missing = dc.validate_dashboard_row(rows[0])
        checks.append(LiveVerificationCheck(
            name="static.6_data_carries_v5_5_0_dashboard_contract_fields",
            status="PASS" if not missing else "FAIL",
            detail=None if not missing else "; ".join(missing[:5]),
        ))
    else:
        checks.append(LiveVerificationCheck(
            name="static.6_data_carries_v5_5_0_dashboard_contract_fields",
            status="SKIPPED",
            detail="data rows not parseable; check 5 already FAILED",
        ))

    # static.7 — stale-label scan on the HTML.
    patterns = dict(stale_label_scanner.FOREIGN_COUNTY_TOKENS)
    if current_county_slug:
        # Re-use the slug exemption map.
        slug_map = {
            "bexar_tx":   {"Bexar", "Bexar AD", "San Antonio"},
            "duval_fl":   {"Duval", "Jacksonville"},
            "greene_ny":  {"Greene", "Catskill"},
            "smith_tx":   {"Smith", "Tyler"},
            "ocean_nj":   {"Ocean"},
            "el_paso_tx": {"El Paso", "EPCAD"},
        }
        for k in slug_map.get(current_county_slug, set()):
            patterns.pop(k, None)
    stale_hits = [
        (token, m.group(0))
        for token, regex in patterns.items()
        for m in regex.finditer(dashboard_html or "")
    ]
    checks.append(LiveVerificationCheck(
        name="static.7_no_stale_foreign_county_labels",
        status="PASS" if not stale_hits else "FAIL",
        detail=None if not stale_hits else (
            f"{len(stale_hits)} foreign-county token(s) in the served "
            f"HTML: {[t for t, _ in stale_hits[:5]]}"
        ),
    ))

    # static.8 — banner-prohibited tokens in user-visible HTML.
    prohibited = [
        t for t in dc.BANNER_PROHIBITED_TOKENS
        if t in (dashboard_html or "")
    ]
    checks.append(LiveVerificationCheck(
        name="static.8_no_banner_prohibited_tokens",
        status="PASS" if not prohibited else "FAIL",
        detail=None if not prohibited else (
            f"banner-prohibited tokens leaked into user-visible HTML: "
            f"{prohibited[:5]}"
        ),
    ))

    failed = [c for c in checks if c.status == "FAIL"]
    return LiveVerificationResult(
        half="STATIC",
        verdict="STATIC_OK" if not failed else "STATIC_BLOCKED",
        checks=tuple(checks),
    )


def _parse_data_artifact(text: str) -> list[dict]:
    """Extract a row list from the served data artifact. Tries three forms:

      - window.LEADS = [ ... ];      (data.js)
      - top-level JSON array         (data.json — array)
      - object with key "records"    (dashboard.json)
    """
    text = text.strip()
    if not text:
        return []
    # data.js — window.LEADS = [...];
    m = re.search(
        r"window\.LEADS\s*=\s*(\[.*\])\s*;?\s*$", text, re.DOTALL,
    )
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, list):
                return [r for r in data if isinstance(r, dict)]
        except json.JSONDecodeError:
            pass
    # data.json — top-level array OR { "records": [...] }
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        recs = data.get("records")
        if isinstance(recs, list):
            return [r for r in recs if isinstance(r, dict)]
    return []


# ---------------------------------------------------------------------------
# INTERACTIVE half — implementation lives in the county verify_live.py.
# This module only owns the CONTRACT (the check name list) so the county's
# Playwright runner can iterate over it deterministically.
# ---------------------------------------------------------------------------

def declared_interactive_check_names() -> tuple[str, ...]:
    """Return the v5.5.0 interactive check names. The county's Playwright
    runner must produce a PASS/FAIL/SKIPPED verdict for each name; missing
    a check is a contract violation."""
    return INTERACTIVE_CHECKS


def reduce_interactive_verdict(
    checks: list[LiveVerificationCheck],
) -> LiveVerificationResult:
    """Reduce a county runner's per-check results to one INTERACTIVE_OK /
    INTERACTIVE_BLOCKED verdict. Used by the county verify_live.py after
    its Playwright run."""
    declared = set(INTERACTIVE_CHECKS)
    received = {c.name for c in checks}
    missing = declared - received
    if missing:
        return LiveVerificationResult(
            half="INTERACTIVE",
            verdict="INTERACTIVE_BLOCKED",
            checks=tuple(checks),
            notes=(
                f"county runner missing required interactive checks: "
                f"{sorted(missing)}",
            ),
        )
    failed = [c for c in checks if c.status == "FAIL"]
    return LiveVerificationResult(
        half="INTERACTIVE",
        verdict="INTERACTIVE_OK" if not failed else "INTERACTIVE_BLOCKED",
        checks=tuple(checks),
    )
