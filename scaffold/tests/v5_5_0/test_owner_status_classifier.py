#!/usr/bin/env python3
"""v5.5.0 §3.5 invariants — estate-titled owner origination canon.

Covers inclusions, exclusions, LIFE ESTATE split, dedupe, and the order-of-
checks short-circuit (LIFE ESTATE before company-suffix, company-suffix
before estate-inclusion).

Run: python3 scaffold/tests/v5_5_0/test_owner_status_classifier.py
Exit 0 = pass, non-zero = fail.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scaffold.pipeline import owner_status_classifier as osc


def main() -> int:
    checks: list = []

    def check(label, ok, detail=""):
        checks.append(("PASS" if ok else "FAIL", label, detail))

    # =====================================================================
    # INCLUSIONS — these should all classify as estate_titled_owner.
    # =====================================================================
    estate_inclusions = [
        ("ESTATE OF JOHN DOE", "ESTATE OF"),
        ("EST OF MARY SMITH", "EST OF"),
        ("HEIRS OF HAROLD JONES", "HEIRS OF"),
        ("HEIR OF SAM TAYLOR", "HEIR OF"),  # singular variant
        ("DOE JANE A ESTATE", "ESTATE"),    # trailing
        ("SMITH JOHN (DECD)", "(DECD)"),
        ("JONES MARY DECEASED", "DECEASED"),
        ("TAYLOR ROBERT, DECEASED", "DECEASED"),
    ]
    for name, expected_match_token in estate_inclusions:
        r = osc.classify_owner_status(name, parcel_id="SYN-001")
        ok = (r.lead_type == "estate_titled_owner"
              and r.is_estate is True
              and r.is_life_estate is False
              and r.is_company is False
              and expected_match_token in (r.matched_inclusion or ""))
        check(f"§3.5 inclusion {name!r} → estate_titled_owner", ok,
              f"got lead_type={r.lead_type!r} matched={r.matched_inclusion!r}")

    # =====================================================================
    # EXCLUSIONS — company / business name suffixes
    # =====================================================================
    company_exclusions = [
        "REAL ESTATE HOMES LLC",      # company w/ "estate" inside
        "ESTATES OF THE PARK LLC",
        "ESTATE PROPERTIES INC",
        "DOE HOLDINGS LLC",
        "JONES REALTY",
        "SMITH FAMILY HOMES INC",
        "ACME PROPERTIES LP",
        "TRUST COMPANY OF EXAMPLE",   # corporate trust co.
        "EXAMPLE BANK",
        "WORKFORCE AUTHORITY",
        "SUNSHINE ESTATES",            # plural "estates" = business
    ]
    for name in company_exclusions:
        r = osc.classify_owner_status(name)
        ok = (r.lead_type == "not_estate"
              and r.is_estate is False
              and r.is_company is True)
        check(f"§3.5 exclusion (company suffix) {name!r} → not_estate", ok,
              f"got lead_type={r.lead_type!r} matched={r.matched_exclusion!r}")

    # =====================================================================
    # REAL ESTATE — stripped before estate-pattern check
    # =====================================================================
    r = osc.classify_owner_status("REAL ESTATE HOLDINGS")
    check("§3.5: 'REAL ESTATE HOLDINGS' (no estate inclusion after strip) → "
          "not_estate (HOLDINGS company suffix catches it first)",
          r.lead_type == "not_estate" and r.is_company is True)

    r = osc.classify_owner_status("REAL ESTATE")
    check("§3.5: 'REAL ESTATE' alone (no company suffix, no inclusion "
          "remaining after strip) → not_estate",
          r.lead_type == "not_estate" and r.is_estate is False)

    # =====================================================================
    # LIFE ESTATE — must precede company-suffix + estate-inclusion checks
    # =====================================================================
    life_estate_cases = [
        "DOE JOHN LIFE ESTATE",
        "JONES MARY LIFE ESTATE",
        "TAYLOR LIFE ESTATE",
    ]
    for name in life_estate_cases:
        r = osc.classify_owner_status(name, parcel_id="SYN-LIFE")
        ok = (r.lead_type == "life_estate"
              and r.is_estate is False
              and r.is_life_estate is True
              and r.matched_inclusion == "life_estate")
        check(f"§3.5 LIFE ESTATE {name!r} → life_estate (NOT probate)", ok,
              f"got lead_type={r.lead_type!r}")

    # =====================================================================
    # Edge cases
    # =====================================================================
    r = osc.classify_owner_status("")
    check("§3.5: empty owner_name → not_estate",
          r.lead_type == "not_estate")
    r = osc.classify_owner_status(None)
    check("§3.5: None owner_name → not_estate",
          r.lead_type == "not_estate")
    r = osc.classify_owner_status("DOE JOHN A")
    check("§3.5: plain individual name (no estate marker) → not_estate",
          r.lead_type == "not_estate" and r.is_estate is False)

    # =====================================================================
    # Order of checks: LIFE ESTATE > company-suffix > inclusion
    # =====================================================================
    # A company suffix + an estate-inclusion in the same string: company wins.
    r = osc.classify_owner_status("ESTATE OF DOE LLC")
    check("§3.5 ordering: 'ESTATE OF DOE LLC' — company suffix overrides "
          "estate inclusion (LLC indicates business)",
          r.lead_type == "not_estate" and r.is_company is True)
    # LIFE ESTATE + company suffix: LIFE ESTATE wins (the life-tenant
    # signal is structurally important — even if the title carries an
    # LLC, the life-tenant relationship is the one being flagged).
    r = osc.classify_owner_status("DOE LIFE ESTATE LLC")
    check("§3.5 ordering: 'DOE LIFE ESTATE LLC' — LIFE ESTATE wins over "
          "LLC suffix (the life-tenant relationship is the signal)",
          r.lead_type == "life_estate" and r.is_life_estate is True)

    # =====================================================================
    # Dedupe — §3.5: one row per (owner_name, parcel_id); not_estate filtered
    # =====================================================================
    classifications = [
        osc.classify_owner_status("ESTATE OF DOE", parcel_id="P1"),
        osc.classify_owner_status("ESTATE OF DOE", parcel_id="P1"),  # dupe
        osc.classify_owner_status("ESTATE OF DOE", parcel_id="P2"),  # diff parcel
        osc.classify_owner_status("HEIRS OF SMITH", parcel_id="P1"),  # diff owner
        osc.classify_owner_status("DOE LLC", parcel_id="P3"),  # not_estate — filtered
        osc.classify_owner_status("DOE LIFE ESTATE", parcel_id="P4"),  # life_estate — kept
    ]
    deduped = osc.dedupe_estate_classifications(classifications)
    check("§3.5 dedupe: 6 classifications → 4 distinct (owner, parcel) "
          "keys (one dupe collapsed; not_estate filtered)",
          len(deduped) == 4,
          f"got {len(deduped)}: {[(c.owner_name, c.parcel_id, c.lead_type) for c in deduped]}")
    lead_types = {c.lead_type for c in deduped}
    check("§3.5 dedupe: deduped set includes estate_titled_owner AND "
          "life_estate (the two lead-bearing types)",
          lead_types == {"estate_titled_owner", "life_estate"})
    check("§3.5 dedupe: deduped set does NOT include 'not_estate' "
          "(filtered as not-a-lead)",
          "not_estate" not in lead_types)

    # --- Canonical types ------------------------------------------------
    for lt in osc.OWNER_STATUS_LEAD_TYPES:
        check(f"§3.5 canonical lead_type {lt!r} declared",
              lt in ("estate_titled_owner", "life_estate", "not_estate"))

    # --- Report ----------------------------------------------------------
    failed = [c for c in checks if c[0] == "FAIL"]
    for s, l, d in checks:
        print(f"  [{s}] {l}")
        if s == "FAIL" and d:
            print(f"         detail: {d}")
    if failed:
        print(f"FAIL: owner-status classifier — {len(failed)}/"
              f"{len(checks)} checks failed")
        return 1
    print(f"PASS: §3.5 estate-titled owner origination (v5.5.0) — "
          f"all {len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
