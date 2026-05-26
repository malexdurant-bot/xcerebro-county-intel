#!/usr/bin/env python3
"""v5.5.0 §4.4 fix regression — leads_base_writer keeps parcel_id on REVIEW_REQUIRED.

Prior defect (formerly leads_base_writer.py line 218): the writer zeroed the
aggregation-key `parcel_id` whenever `parcel_resolution_status` was
REVIEW_REQUIRED, which included cases where the raw event had a real
parcel_id but §17 routed REVIEW_REQUIRED because the owner wasn't named on
the document. This blocked downstream enrichment that needs to join on the
parcel — exactly the §13.14 enrichment-decoupling contract that says
enrichment MAY still attach to a REVIEW_REQUIRED lead.

This test pins the v5.5.0 fix: a REVIEW_REQUIRED debtor-resolved record that
carries a real parcel_id MUST emit a leads_base record whose aggregation_key
also carries that parcel_id.

Run: python3 scaffold/tests/v5_5_0/test_leads_base_writer_review_required_keeps_parcel_id.py
Exit 0 = pass, non-zero = fail.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scaffold.pipeline import leads_base_writer


def _drr(*, debtor_resolution_status, parcel_id, owner_name="REVIEW_PLACEHOLDER"):
    """A schema-shape debtor_resolved_record fixture."""
    return {
        "raw_event_id": "raw_v55_001",
        "source_id": "synth_clerk_recordings",
        "source_role": "PRIMARY_EVENT_SOURCE",
        "canonical_doc_type": "tax_foreclosure_notice",
        "source_url": "synthetic://clerk/v55-001",
        "recorded_date": "2026-05-01",
        "instrument_number": "INST-V55-001",
        "event_date": None,
        "property_refs": {
            "parcel_id": parcel_id,
            "situs_address": "100 SYNTHETIC LANE",
            "legal_description": None,
            "case_number": "CASE-001",
        },
        "owner_name": owner_name,
        "owner_type": "UNKNOWN" if debtor_resolution_status == "REVIEW_REQUIRED" else "INDIVIDUAL",
        "filer_entity": None,
        "debtor_resolution_status": debtor_resolution_status,
        "review_reason": "owner_not_on_document" if debtor_resolution_status == "REVIEW_REQUIRED" else None,
        "expected_debtor_name_type": None,
        "debtor_extraction_method": "REVIEW_ROUTED" if debtor_resolution_status == "REVIEW_REQUIRED" else "STRUCTURED_NAME_TYPE",
        "evidence_ids": ["ev_v55_001"],
    }


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        checks.append(("PASS" if ok else "FAIL", label, detail))

    labels = {"tax_foreclosure_notice": "Tax Foreclosure Notice"}

    # --- Case 1 — REVIEW_REQUIRED + real parcel_id → key keeps the parcel ---
    drr = _drr(debtor_resolution_status="REVIEW_REQUIRED", parcel_id="SYN-001")
    base = leads_base_writer.build_base_record(
        drr, signal_type_labels=labels, evidence_ledger=None,
    )
    check("REVIEW_REQUIRED with real parcel_id: leads_base.parcel_resolution_status "
          "== REVIEW_REQUIRED (the verdict surface)",
          base.get("parcel_resolution_status") == "REVIEW_REQUIRED",
          f"got {base.get('parcel_resolution_status')!r}")
    check("v5.5.0 §4.4 FIX — REVIEW_REQUIRED + real parcel_id: the "
          "aggregation_key.parcel_id is the REAL parcel_id, not null "
          "(downstream enrichment can still attach to it per §13.14)",
          base.get("aggregation_key", {}).get("parcel_id") == "SYN-001",
          f"got aggregation_key={base.get('aggregation_key')!r}")
    check("REVIEW_REQUIRED with real parcel_id: property_refs.parcel_id is the "
          "real parcel_id (carried forward, never dropped)",
          base.get("property_refs", {}).get("parcel_id") == "SYN-001")

    # --- Case 2 — RESOLVED + real parcel_id → key keeps the parcel (unchanged) ---
    drr = _drr(
        debtor_resolution_status="RESOLVED", parcel_id="SYN-001",
        owner_name="TEST_OWNER_001",
    )
    base = leads_base_writer.build_base_record(
        drr, signal_type_labels=labels, evidence_ledger=None,
    )
    check("RESOLVED + real parcel_id (unchanged behavior): "
          "parcel_resolution_status == RESOLVED",
          base.get("parcel_resolution_status") == "RESOLVED")
    check("RESOLVED + real parcel_id (unchanged behavior): "
          "aggregation_key.parcel_id is the real parcel_id",
          base.get("aggregation_key", {}).get("parcel_id") == "SYN-001")

    # --- Case 3 — REVIEW_REQUIRED + NO parcel_id → key stays null (no fake) ---
    drr = _drr(debtor_resolution_status="REVIEW_REQUIRED", parcel_id=None)
    base = leads_base_writer.build_base_record(
        drr, signal_type_labels=labels, evidence_ledger=None,
    )
    check("REVIEW_REQUIRED with NO parcel_id: aggregation_key.parcel_id is "
          "null (no fabricated key — only carries what the raw event actually had)",
          base.get("aggregation_key", {}).get("parcel_id") is None)

    # --- Case 4 — RESOLVED + NO parcel_id → status UNRESOLVED (existing rule) ---
    drr = _drr(
        debtor_resolution_status="RESOLVED", parcel_id=None,
        owner_name="TEST_OWNER_002",
    )
    base = leads_base_writer.build_base_record(
        drr, signal_type_labels=labels, evidence_ledger=None,
    )
    check("RESOLVED + NO parcel_id (existing rule): parcel_resolution_status "
          "== UNRESOLVED",
          base.get("parcel_resolution_status") == "UNRESOLVED")
    check("RESOLVED + NO parcel_id (existing rule): aggregation_key.parcel_id "
          "is null",
          base.get("aggregation_key", {}).get("parcel_id") is None)

    # --- Report ---
    failed = [c for c in checks if c[0] == "FAIL"]
    for status, label, detail in checks:
        print(f"  [{status}] {label}")
        if status == "FAIL" and detail:
            print(f"         detail: {detail}")
    if failed:
        print(f"FAIL: leads_base_writer §4.4 fix — {len(failed)}/"
              f"{len(checks)} checks failed")
        return 1
    print(f"PASS: leads_base_writer §4.4 fix (v5.5.0) — all "
          f"{len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
