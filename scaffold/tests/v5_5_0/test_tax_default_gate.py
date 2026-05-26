#!/usr/bin/env python3
"""v5.5.0 §3.3 invariants — tax-default qualification gate.

Pins the gate's behavior across all 5 criteria + the dedupe rule + the
canonical-type emission.

Run: python3 scaffold/tests/v5_5_0/test_tax_default_gate.py
Exit 0 = pass, non-zero = fail.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scaffold.pipeline import tax_default_gate as gate


def _row(**overrides) -> dict:
    """A fully-qualified candidate row — overrides drop fields to test the gate."""
    base = {
        "source_role": "PRIMARY_DEFAULT_SOURCE",
        "source_id": "synth_tax_collector",
        "source_url": "synthetic://tax-collector/SYN-001",
        "raw_record_id": "raw_td_001",
        "captured_at": "2026-05-14T12:00:00Z",
        "default_condition": "delinquent",
        "parcel_id": "SYN-001",
        "account_number": "ACC-001",
        "situs_address": "100 SYNTHETIC LANE",
        "balance": 4250.00,
        "years_delinquent": 3,
    }
    base.update(overrides)
    return base


def main() -> int:
    checks: list = []

    def check(label, ok, detail=""):
        checks.append(("PASS" if ok else "FAIL", label, detail))

    # =====================================================================
    # Five-criteria gate
    # =====================================================================

    # All 5 criteria met → QUALIFIED + tax_default.
    r = gate.qualify_tax_default(_row())
    check("§3.3 all 5 criteria met → QUALIFIED",
          r.qualification_status == "QUALIFIED",
          f"got {r.qualification_status!r} — criteria={r.criteria}")
    check("§3.3 QUALIFIED with default_condition=delinquent + balance>500 + "
          "years>1 → lead_type tax_default",
          r.lead_type == "tax_default")

    # (a) — fails when source is not an official role.
    r = gate.qualify_tax_default(_row(source_role="ENRICHMENT_SOURCE"))
    check("§3.3 (a) enrichment-source role → NOT_QUALIFIED",
          r.qualification_status == "NOT_QUALIFIED",
          f"got {r.qualification_status!r}")
    check("§3.3 (a) criterion 'a_official_source' is False",
          r.criteria.get("a_official_source") is False)

    # (b) — fails when no default condition flag is set (generic roll row).
    r = gate.qualify_tax_default(_row(default_condition=None))
    check("§3.3 (b) no default_condition flag → NOT_QUALIFIED "
          "(generic roll row is enrichment, not a lead)",
          r.qualification_status == "NOT_QUALIFIED")
    check("§3.3 (b) criterion 'b_default_condition' is False",
          r.criteria.get("b_default_condition") is False)

    # (c) — fails when no property tie.
    r = gate.qualify_tax_default(_row(
        parcel_id=None, account_number=None, tax_map_id=None,
        situs_address=None, legal_description=None,
    ))
    check("§3.3 (c) no property tie → NOT_QUALIFIED (debtor-only row)",
          r.qualification_status == "NOT_QUALIFIED")
    check("§3.3 (c) criterion 'c_property_tie' is False",
          r.criteria.get("c_property_tie") is False)

    # (d) — missing source proof while a/b/c hold → REVIEW_REQUIRED.
    r = gate.qualify_tax_default(_row(source_url=""))
    check("§3.3 (d) source-proof incomplete (a/b/c hold) → REVIEW_REQUIRED "
          "(not silently dropped)",
          r.qualification_status == "REVIEW_REQUIRED")
    check("§3.3 (d) lead_type 'review_required'",
          r.lead_type == "review_required")
    check("§3.3 (d) review_reason names the missing field(s)",
          "source_url" in (r.review_reason or ""))

    # (e) — explicit generic-roll-enrichment flag → NOT_QUALIFIED.
    r = gate.qualify_tax_default(_row(is_generic_roll_enrichment=True))
    check("§3.3 (e) is_generic_roll_enrichment=True → NOT_QUALIFIED",
          r.qualification_status == "NOT_QUALIFIED")
    check("§3.3 (e) criterion 'e_not_generic_roll' is False",
          r.criteria.get("e_not_generic_roll") is False)

    # =====================================================================
    # Lead-type emission across default_condition values
    # =====================================================================

    for cond, expected in (
        ("tax_sale_pending", "tax_sale"),
        ("tax_sale_struck_off", "tax_sale"),
        ("tax_sale_sold", "tax_sale"),
        ("tax_foreclosure_filed", "tax_foreclosure"),
        ("tax_certificate_issued", "tax_certificate"),
        ("collection_active", "tax_default"),
        ("redemption_period", "tax_default"),
    ):
        r = gate.qualify_tax_default(_row(default_condition=cond))
        check(f"§3.3 default_condition={cond!r} → lead_type {expected!r}",
              r.lead_type == expected, f"got {r.lead_type!r}")

    # Low-priority: small balance + recent onset → tax_default_low_priority.
    r = gate.qualify_tax_default(_row(balance=125.00, years_delinquent=1))
    check("§3.3 low-priority: small balance + 1 year delinquent → "
          "tax_default_low_priority (§5.6 default-hidden)",
          r.lead_type == "tax_default_low_priority",
          f"got {r.lead_type!r}")

    # =====================================================================
    # Dedupe — §3.3: one lead per (account, parcel) per current default
    # =====================================================================

    # Two rows on same account_number → collapse, higher-severity wins.
    row_default = _row(default_condition="delinquent", raw_record_id="r_a")
    row_sale = _row(default_condition="tax_sale_pending", raw_record_id="r_b")
    res_default = gate.qualify_tax_default(row_default)
    res_sale = gate.qualify_tax_default(row_sale)
    deduped = gate.dedupe_tax_default_results([
        (row_default, res_default),
        (row_sale, res_sale),
    ])
    check("§3.3 dedupe: SAME account_number, different default conditions → "
          "two distinct keys (dedupe keys ON default_condition too, so a "
          "currently-delinquent + a tax-sale-pending row on the same "
          "account remain as TWO distinct leads)",
          len(deduped) == 2)

    # Identical account + condition → 1 row.
    row_a = _row(raw_record_id="r_a")
    row_b = _row(raw_record_id="r_b")  # same account_number, same condition
    deduped = gate.dedupe_tax_default_results([
        (row_a, gate.qualify_tax_default(row_a)),
        (row_b, gate.qualify_tax_default(row_b)),
    ])
    check("§3.3 dedupe: same (account_number, default_condition) on two rows "
          "→ collapses to 1 lead (operator pain point: never 1-per-tax-year)",
          len(deduped) == 1)

    # Three rows, two duplicates + one high-severity → 1 lead, severity wins.
    rows_with_results = []
    for cond, rid in (
        ("delinquent", "rA"),
        ("delinquent", "rB"),  # dedupe target
        ("tax_foreclosure_filed", "rC"),  # higher-severity, different key
    ):
        r = _row(default_condition=cond, raw_record_id=rid)
        rows_with_results.append((r, gate.qualify_tax_default(r)))
    deduped = gate.dedupe_tax_default_results(rows_with_results)
    check("§3.3 dedupe: 3 rows → 2 distinct dedupe keys → 2 leads "
          "(the foreclosure row is a different default condition, "
          "the two delinquent rows collapse to one)",
          len(deduped) == 2)

    # NOT_QUALIFIED rows are NOT included in the dedupe output.
    rows_with_results = [
        (_row(), gate.qualify_tax_default(_row())),
        (_row(source_role="REFERENCE_SOURCE"),
         gate.qualify_tax_default(_row(source_role="REFERENCE_SOURCE"))),
    ]
    deduped = gate.dedupe_tax_default_results(rows_with_results)
    check("§3.3 dedupe: NOT_QUALIFIED rows are NOT emitted as leads "
          "(only QUALIFIED + REVIEW_REQUIRED rows continue)",
          len(deduped) == 1)

    # =====================================================================
    # Canonical-type contract
    # =====================================================================

    for lt in ("tax_default", "tax_default_low_priority", "tax_foreclosure",
               "tax_sale", "tax_certificate", "review_required"):
        check(f"§3.3 canonical lead_type {lt!r} is in TAX_DEFAULT_LEAD_TYPES",
              lt in gate.TAX_DEFAULT_LEAD_TYPES)

    # --- Report ----------------------------------------------------------
    failed = [c for c in checks if c[0] == "FAIL"]
    for s, l, d in checks:
        print(f"  [{s}] {l}")
        if s == "FAIL" and d:
            print(f"         detail: {d}")
    if failed:
        print(f"FAIL: tax-default gate — {len(failed)}/{len(checks)} "
              f"checks failed")
        return 1
    print(f"PASS: §3.3 tax-default qualification gate (v5.5.0) — "
          f"all {len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
