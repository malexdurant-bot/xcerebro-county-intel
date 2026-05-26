#!/usr/bin/env python3
"""v5.5.0 §5 invariants — dashboard renderer contract + §5.11 stale-label scanner.

Pins:
  - The required-field set on every dashboard row (lead_id, owner_name,
    owner_type, signal_type, property_full_address, recorded_date,
    review_status, lead_origin_type, enrichment_status, event_source).
  - validate_dashboard_row() flags missing required fields.
  - validate_dashboard_row() flags over-flattened umbrella signal_type
    values (per §5.9 — chips must be per-canonical).
  - Standard-filter names are all present.
  - DEFAULT_FILTER_STATE is neutral (None / "" — never an all-checked dump).
  - Banner-prohibited tokens list catches PARTIAL_BUILD / SOURCE_LIMITED /
    Cloudflare / pipeline / etc.
  - The stale-label scanner FAILS on a temp file carrying a foreign-county
    token and PASSES on a clean file.
  - The scanner exempts the current-county slug's own tokens.

Run: python3 scaffold/tests/v5_5_0/test_dashboard_contract_and_stale_labels.py
Exit 0 = pass, non-zero = fail.
"""
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scaffold.ops import stale_label_scanner
from scaffold.pipeline import dashboard_contract as dc


def _row(**overrides) -> dict:
    base = {
        "lead_id": "lead_v55",
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
    # §5.9 dashboard row contract — required field set
    # =====================================================================
    required = dc.REQUIRED_DASHBOARD_FIELDS
    for f in ("lead_id", "owner_name", "owner_type", "signal_type",
              "property_full_address", "recorded_date", "review_status",
              "lead_origin_type", "enrichment_status", "event_source"):
        check(f"§5.9 REQUIRED_DASHBOARD_FIELDS includes {f!r}",
              f in required)

    # Compliant row → no violations.
    v = dc.validate_dashboard_row(_row())
    check("§5.9 compliant row → 0 violations", v == [])

    # Missing required field → one violation.
    bad = _row()
    del bad["lead_origin_type"]
    v = dc.validate_dashboard_row(bad)
    check("§5.9 missing lead_origin_type → 1 violation",
          len(v) == 1 and "lead_origin_type" in v[0])

    # Over-flattened signal_type → violation.
    v = dc.validate_dashboard_row(_row(signal_type="distress"))
    check("§5.9 over-flattened signal_type 'distress' → violation",
          any("over-flattened" in vi for vi in v))

    # Per-canonical signal_type → no violation.
    v = dc.validate_dashboard_row(_row(signal_type="Notice of Sale"))
    check("§5.9 per-canonical signal_type 'Notice of Sale' → 0 violations",
          v == [])

    # =====================================================================
    # §5.4 / §5.2 — standard filter set + neutral defaults
    # =====================================================================
    for name in ("distress_type", "owner_type", "recency",
                 "years_delinquent", "absentee_or_out_of_state",
                 "address_resolved", "review_status", "search", "reset"):
        check(f"§5.4 STANDARD_FILTERS includes {name!r}",
              name in dc.STANDARD_FILTERS)
    # §5.2 — filter defaults are neutral, NOT all-checked dumps.
    check("§5.2 DEFAULT_FILTER_STATE — distress_type starts None (unselected)",
          dc.DEFAULT_FILTER_STATE.get("distress_type") is None)
    check("§5.2 DEFAULT_FILTER_STATE — owner_type starts None (unselected)",
          dc.DEFAULT_FILTER_STATE.get("owner_type") is None)
    check("§5.2 DEFAULT_FILTER_STATE — search starts '' (empty), never "
          "pre-filled",
          dc.DEFAULT_FILTER_STATE.get("search") == "")

    # =====================================================================
    # §5.6 — default-hidden lead types
    # =====================================================================
    for lt in ("tax_default_low_priority", "civil_judgment",
               "abstract_of_judgment"):
        check(f"§5.6 DEFAULT_HIDDEN_LEAD_TYPES includes {lt!r}",
              lt in dc.DEFAULT_HIDDEN_LEAD_TYPES)

    # =====================================================================
    # §5.7 — banner-prohibited tokens
    # =====================================================================
    for tok in ("PARTIAL_BUILD", "SOURCE_LIMITED", "Cloudflare", "pipeline",
                "DEPLOY_OK"):
        check(f"§5.7 BANNER_PROHIBITED_TOKENS includes {tok!r}",
              tok in dc.BANNER_PROHIBITED_TOKENS)

    # =====================================================================
    # §5.11 — stale-label scanner
    # =====================================================================
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        # 1) Clean file — no foreign tokens.
        clean = tmp_dir / "clean.html"
        clean.write_text(
            "<h1>County Distress Dashboard</h1>\n"
            "<p>Records loaded from the county recorder.</p>\n",
            encoding="utf-8",
        )
        violations = stale_label_scanner.scan_paths([clean])
        check("§5.11 scanner: clean file → 0 violations",
              violations == [], f"got {violations}")

        # 2) File with foreign-county token.
        leaky = tmp_dir / "leaky.html"
        leaky.write_text(
            "<h1>El Paso Distress Dashboard</h1>\n"
            "<p>Records from EPCAD parcel master.</p>\n",
            encoding="utf-8",
        )
        violations = stale_label_scanner.scan_paths([leaky])
        check("§5.11 scanner: file with 'El Paso' + 'EPCAD' → "
              ">= 2 violations",
              len(violations) >= 2,
              f"got {[(v['token'], v['line']) for v in violations]}")

        # 3) Current-county exemption: when scanning the duval-fl county
        #    dashboard, "Duval" / "Jacksonville" are allowed; "El Paso"
        #    is still a foreign-county violation.
        duval = tmp_dir / "duval_dashboard.html"
        duval.write_text(
            "<h1>Duval County Distress Dashboard</h1>\n"
            "<p>Records from Jacksonville GIS.</p>\n"
            "<!-- but El Paso must still flag -->\n"
            "<p>EPCAD data not used here.</p>\n",
            encoding="utf-8",
        )
        violations = stale_label_scanner.scan_paths(
            [duval], current_county_slug="duval_fl",
        )
        tokens_hit = {v["token"] for v in violations}
        check("§5.11 scanner with --county duval_fl: 'Duval' is "
              "ALLOWED (county's own name)",
              "Duval" not in tokens_hit,
              f"tokens_hit={tokens_hit}")
        check("§5.11 scanner with --county duval_fl: 'Jacksonville' is "
              "ALLOWED (county's own city)",
              "Jacksonville" not in tokens_hit)
        check("§5.11 scanner with --county duval_fl: 'El Paso' / 'EPCAD' "
              "still flagged (foreign-county tokens)",
              "El Paso" in tokens_hit and "EPCAD" in tokens_hit)

    # =====================================================================
    # Scanner: universal-layer scope catches ALL counties (no slug)
    # =====================================================================
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "universal.md"
        f.write_text(
            "# Example county build\n"
            "Bexar TX, Duval FL, Greene NY were the seed counties.\n",
            encoding="utf-8",
        )
        violations = stale_label_scanner.scan_paths([f])
        tokens = {v["token"] for v in violations}
        check("§5.11 scanner universal-layer scope (no slug): 'Bexar', "
              "'Duval', 'Greene' all flagged",
              {"Bexar", "Duval", "Greene"}.issubset(tokens))

    # --- Report -----------------------------------------------------------
    failed = [c for c in checks if c[0] == "FAIL"]
    for s, l, d in checks:
        print(f"  [{s}] {l}")
        if s == "FAIL" and d:
            print(f"         detail: {d}")
    if failed:
        print(f"FAIL: dashboard contract + stale-label scanner — "
              f"{len(failed)}/{len(checks)} checks failed")
        return 1
    print(f"PASS: §5 dashboard contract + §5.11 stale-label scanner "
          f"(v5.5.0) — all {len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
