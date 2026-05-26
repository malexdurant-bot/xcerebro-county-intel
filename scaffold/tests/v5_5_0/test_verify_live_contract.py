#!/usr/bin/env python3
"""v5.5.0 §7.1 invariants — live-URL verification contract.

Pins the STATIC half (no browser required, runnable in CI) end-to-end:
fetches against in-memory HTML + data artifact, verifies the eight static
checks fire correctly. Pins the INTERACTIVE contract's declared check
list — the county Playwright runner must produce verdicts for each.

Run: python3 scaffold/tests/v5_5_0/test_verify_live_contract.py
Exit 0 = pass, non-zero = fail.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scaffold.ops import verify_live_contract as vlc


def _row_v55(**overrides) -> dict:
    base = {
        "lead_id": "L1",
        "owner_name": "TEST_OWNER",
        "owner_type": "INDIVIDUAL",
        "signal_type": "Hospital Lien",
        "property_full_address": "100 Synthetic Lane",
        "recorded_date": "2026-05-01",
        "review_status": "APPROVED_FOR_DASHBOARD",
        "lead_origin_type": "RECORDED_EVENT",
        "enrichment_status": "ENRICHED",
        "event_source": "clerk",
    }
    base.update(overrides)
    return base


def main() -> int:
    checks: list = []

    def check(label, ok, detail=""):
        checks.append(("PASS" if ok else "FAIL", label, detail))

    # =====================================================================
    # STATIC half — happy path
    # =====================================================================
    import json
    good_html = (
        '<!DOCTYPE html>\n<html>\n'
        '<head><title>County Distress Dashboard</title></head>\n'
        '<body>\n<div id="root"></div>\n'
        '<script src="data.js"></script>\n</body>\n</html>'
    )
    good_data = f'window.LEADS = {json.dumps([_row_v55(), _row_v55(lead_id="L2")])};'

    r = vlc.run_static_checks(
        dashboard_html=good_html,
        data_artifact_text=good_data,
    )
    check("§7.1 STATIC happy path → STATIC_OK",
          r.verdict == "STATIC_OK",
          f"got {r.verdict!r}; failed: "
          f"{[c.name for c in r.checks if c.status == 'FAIL']}")
    check("§7.1 STATIC: all 8 declared checks ran",
          len(r.checks) == len(vlc.STATIC_CHECKS))

    # =====================================================================
    # STATIC.2 — body isn't HTML
    # =====================================================================
    r = vlc.run_static_checks(
        dashboard_html='{"error": "not html"}',
        data_artifact_text=good_data,
    )
    static_2 = next(c for c in r.checks if c.name == "static.2_body_is_html")
    check("§7.1 STATIC.2: a JSON body (not HTML) → FAIL",
          static_2.status == "FAIL")
    check("§7.1 STATIC: any FAIL → verdict STATIC_BLOCKED",
          r.verdict == "STATIC_BLOCKED")

    # =====================================================================
    # STATIC.4 / STATIC.5 — data artifact missing or unparseable
    # =====================================================================
    r = vlc.run_static_checks(
        dashboard_html=good_html,
        data_artifact_text=None,
    )
    static_4 = next(c for c in r.checks if c.name == "static.4_data_artifact_reachable")
    check("§7.1 STATIC.4: missing data artifact → FAIL",
          static_4.status == "FAIL")

    r = vlc.run_static_checks(
        dashboard_html=good_html,
        data_artifact_text="<<this is not parseable>>",
    )
    static_5 = next(c for c in r.checks if c.name == "static.5_data_artifact_parseable_non_empty")
    check("§7.1 STATIC.5: unparseable data artifact → FAIL",
          static_5.status == "FAIL")

    # =====================================================================
    # STATIC.6 — first row missing a required field
    # =====================================================================
    bad_row = _row_v55()
    del bad_row["lead_origin_type"]
    r = vlc.run_static_checks(
        dashboard_html=good_html,
        data_artifact_text=f"window.LEADS = {json.dumps([bad_row])};",
    )
    static_6 = next(c for c in r.checks
                    if c.name == "static.6_data_carries_v5_5_0_dashboard_contract_fields")
    check("§7.1 STATIC.6: row missing lead_origin_type → FAIL with detail",
          static_6.status == "FAIL" and "lead_origin_type" in (static_6.detail or ""))

    # =====================================================================
    # STATIC.7 — stale foreign-county labels
    # =====================================================================
    stale_html = good_html.replace(
        "County Distress", "El Paso County Distress"
    )
    r = vlc.run_static_checks(
        dashboard_html=stale_html,
        data_artifact_text=good_data,
        current_county_slug="duval_fl",
    )
    static_7 = next(c for c in r.checks
                    if c.name == "static.7_no_stale_foreign_county_labels")
    check("§7.1 STATIC.7: 'El Paso' in served HTML (county=duval_fl) → FAIL "
          "(foreign-county token)",
          static_7.status == "FAIL")

    # Same HTML but scanning AS el_paso_tx → 'El Paso' is allowed.
    r = vlc.run_static_checks(
        dashboard_html=stale_html,
        data_artifact_text=good_data,
        current_county_slug="el_paso_tx",
    )
    static_7 = next(c for c in r.checks
                    if c.name == "static.7_no_stale_foreign_county_labels")
    check("§7.1 STATIC.7: 'El Paso' in served HTML (county=el_paso_tx) → "
          "PASS (county's own name)",
          static_7.status == "PASS")

    # =====================================================================
    # STATIC.8 — banner-prohibited tokens leak into HTML
    # =====================================================================
    leaky_html = good_html.replace(
        "<title>", "<title>PARTIAL_BUILD: "
    )
    r = vlc.run_static_checks(
        dashboard_html=leaky_html,
        data_artifact_text=good_data,
    )
    static_8 = next(c for c in r.checks
                    if c.name == "static.8_no_banner_prohibited_tokens")
    check("§7.1 STATIC.8: 'PARTIAL_BUILD' leaked into HTML → FAIL "
          "(§5.7 banner-prohibited token)",
          static_8.status == "FAIL" and "PARTIAL_BUILD" in (static_8.detail or ""))

    # =====================================================================
    # STATIC data artifact parsing — top-level array, records key
    # =====================================================================
    r = vlc.run_static_checks(
        dashboard_html=good_html,
        data_artifact_text=json.dumps([_row_v55()]),  # top-level array
    )
    check("§7.1 STATIC accepts data.json top-level array",
          r.verdict == "STATIC_OK")
    r = vlc.run_static_checks(
        dashboard_html=good_html,
        data_artifact_text=json.dumps({"records": [_row_v55()]}),
    )
    check("§7.1 STATIC accepts dashboard.json with 'records' key",
          r.verdict == "STATIC_OK")

    # =====================================================================
    # INTERACTIVE contract — declared check names
    # =====================================================================
    interactive_names = vlc.declared_interactive_check_names()
    for expected in (
        "interactive.1_at_least_one_lead_card_renders",
        "interactive.3_distress_type_filter_changes_visible_count",
        "interactive.6_reset_restores_original_count",
        "interactive.7_no_uncaught_js_errors",
    ):
        check(f"§7.1 INTERACTIVE contract declares {expected!r}",
              expected in interactive_names)
    check("§7.1 INTERACTIVE contract declares 7 checks",
          len(interactive_names) == 7)

    # Reduce_interactive_verdict — missing check → INTERACTIVE_BLOCKED.
    partial = [
        vlc.LiveVerificationCheck(name=n, status="PASS")
        for n in interactive_names[:3]
    ]
    r = vlc.reduce_interactive_verdict(partial)
    check("§7.1 reduce_interactive_verdict: county runner missing checks → "
          "INTERACTIVE_BLOCKED",
          r.verdict == "INTERACTIVE_BLOCKED"
          and any("missing required interactive checks" in n for n in r.notes))

    # All present + all PASS → INTERACTIVE_OK.
    full = [
        vlc.LiveVerificationCheck(name=n, status="PASS")
        for n in interactive_names
    ]
    r = vlc.reduce_interactive_verdict(full)
    check("§7.1 reduce_interactive_verdict: all 7 checks PASS → "
          "INTERACTIVE_OK",
          r.verdict == "INTERACTIVE_OK")

    # One FAIL → INTERACTIVE_BLOCKED.
    one_fail = full[:6] + [
        vlc.LiveVerificationCheck(name=interactive_names[6], status="FAIL"),
    ]
    r = vlc.reduce_interactive_verdict(one_fail)
    check("§7.1 reduce_interactive_verdict: 1 FAIL → INTERACTIVE_BLOCKED",
          r.verdict == "INTERACTIVE_BLOCKED")

    # --- Report -----------------------------------------------------------
    failed = [c for c in checks if c[0] == "FAIL"]
    for s, l, d in checks:
        print(f"  [{s}] {l}")
        if s == "FAIL" and d:
            print(f"         detail: {d}")
    if failed:
        print(f"FAIL: verify_live contract — {len(failed)}/{len(checks)} "
              f"checks failed")
        return 1
    print(f"PASS: §7.1 live-URL verification contract (v5.5.0) — "
          f"all {len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
