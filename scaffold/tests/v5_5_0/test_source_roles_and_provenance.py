#!/usr/bin/env python3
"""v5.5.0 §0.1 / §3.8 invariants — extended source roles + lead-origination
provenance fields on scored_lead.

Pins:
  - SOURCE_ROLES tuple carries all 8 v5.5.0 values (PRIMARY_EVENT_SOURCE,
    PRIMARY_DEFAULT_SOURCE, PRIMARY_OWNER_STATUS_SOURCE,
    SUPPORTING_EVENT_SOURCE, ENRICHMENT_SOURCE, REFERENCE_SOURCE,
    BLOCKED_SOURCE, REJECTED_SOURCE).
  - The four event-stream schemas (raw_event_record, debtor_resolved_record,
    leads_base_record — matched_lead_record has no source_role property)
    accept ALL 8 enum values.
  - scored_lead_record schema accepts the new optional provenance fields:
    lead_origin_type, event_source, owner_source, enrichment_source,
    qualification_status, qualification_evidence.
  - Backward compatibility: a v5.4.x scored_lead without the new fields
    still validates.

Run: python3 scaffold/tests/v5_5_0/test_source_roles_and_provenance.py
Exit 0 = pass, non-zero = fail.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jsonschema import Draft202012Validator

from scaffold.pipeline.contracts import records, schema_path


def _base_scored_lead(**overrides) -> dict:
    rec = {
        "scored_lead_id": "scored_v55_001",
        "lead_id": "lead_v55_001",
        "primary_parcel_id": "P1",
        "owner_name": "TEST_OWNER",
        "owner_type": "INDIVIDUAL",
        "score": 50,
        "tier": "Workable",
        "score_reasons": [],
        "deal_paths": [],
        "title_complexity_score": 0,
        "title_complexity_tier": "None",
        "title_complexity_contributors": [],
        "pattern_set": ["lien"],
        "patterns": ["lien"],
        "display_patterns": ["lien"],
        "stack_depth": 1,
        "recent_flag": False,
        "attributes": [],
        "review_flags": [],
        "lead_status": "APPROVED_FOR_DASHBOARD",
        "enrichment_status": "UNENRICHED",
        "evidence_ids": ["ev1"],
        "source_ids": ["clerk"],
    }
    rec.update(overrides)
    return rec


def main() -> int:
    checks: list = []

    def check(label, ok, detail=""):
        checks.append(("PASS" if ok else "FAIL", label, detail))

    # =====================================================================
    # SOURCE_ROLES — 8-value v5.5.0 tuple
    # =====================================================================
    sr = records.SOURCE_ROLES
    for role in ("PRIMARY_EVENT_SOURCE", "PRIMARY_DEFAULT_SOURCE",
                 "PRIMARY_OWNER_STATUS_SOURCE", "SUPPORTING_EVENT_SOURCE",
                 "ENRICHMENT_SOURCE", "REFERENCE_SOURCE",
                 "BLOCKED_SOURCE", "REJECTED_SOURCE"):
        check(f"§0.1 SOURCE_ROLES includes {role!r}", role in sr)
    check("§0.1 SOURCE_ROLES tuple length is 8 (v5.4.0 5 + v5.5.0 3 new)",
          len(sr) == 8, f"got {len(sr)}: {sr}")

    # =====================================================================
    # Event-stream schemas — all 8 enum values accepted
    # =====================================================================
    for schema_name in ("raw_event_record", "debtor_resolved_record",
                        "leads_base_record"):
        schema = json.loads(schema_path(schema_name).read_text())
        enum = (
            schema.get("properties", {}).get("source_role", {}).get("enum", [])
        )
        for role in records.SOURCE_ROLES:
            check(f"§0.1 {schema_name}.schema.json source_role enum "
                  f"includes {role!r}", role in enum)

    # =====================================================================
    # New scored_lead provenance fields — schema + dataclass
    # =====================================================================
    validator = Draft202012Validator(
        json.loads(schema_path("scored_lead_record").read_text())
    )

    # v5.4.x scored_lead (no v5.5.0 fields) still validates.
    rec = _base_scored_lead()
    errors = list(validator.iter_errors(rec))
    check("§0.1 backward compat: v5.4.x scored_lead (no v5.5.0 provenance "
          "fields) still validates",
          not errors,
          f"errors={[e.message for e in errors][:2]}")

    # v5.5.0 scored_lead WITH all new fields validates.
    rec = _base_scored_lead(
        lead_origin_type="TAX_DEFAULT",
        event_source="tax_collector",
        owner_source="parcel_master",
        enrichment_source="parcel_master",
        qualification_status="QUALIFIED",
        qualification_evidence={
            "a_official_source": True, "b_default_condition": True,
            "c_property_tie": True, "d_source_proof": True,
            "e_not_generic_roll": True,
        },
    )
    errors = list(validator.iter_errors(rec))
    check("§0.1 v5.5.0 scored_lead WITH lead_origin_type / event_source / "
          "owner_source / enrichment_source / qualification_status / "
          "qualification_evidence all populated → validates",
          not errors,
          f"errors={[e.message for e in errors][:2]}")

    # Each LEAD_ORIGIN_TYPES value individually validates.
    for lot in records.LEAD_ORIGIN_TYPES:
        rec = _base_scored_lead(lead_origin_type=lot)
        errors = list(validator.iter_errors(rec))
        check(f"§3.8 scored_lead lead_origin_type={lot!r} validates",
              not errors)

    # Each QUALIFICATION_STATUSES value individually validates.
    for qs in records.QUALIFICATION_STATUSES:
        rec = _base_scored_lead(qualification_status=qs)
        errors = list(validator.iter_errors(rec))
        check(f"§3.8 scored_lead qualification_status={qs!r} validates",
              not errors)

    # An invalid lead_origin_type is REJECTED.
    rec = _base_scored_lead(lead_origin_type="MADE_UP_TYPE")
    errors = list(validator.iter_errors(rec))
    check("§3.8 scored_lead lead_origin_type='MADE_UP_TYPE' is rejected "
          "(enum constraint)",
          bool(errors))

    # =====================================================================
    # Dataclass construction with v5.5.0 fields
    # =====================================================================
    obj = records.ScoredLeadRecord(
        scored_lead_id="sl", lead_id="lead",
        primary_parcel_id="P1", owner_name="ESTATE OF DOE",
        owner_type="ESTATE", score=55, tier="Workable",
        score_reasons=(), deal_paths=(), title_complexity_score=0,
        title_complexity_tier="None", title_complexity_contributors=(),
        pattern_set=("estate",), patterns=("estate",),
        display_patterns=("estate",), stack_depth=1, recent_flag=False,
        attributes=(), review_flags=(),
        lead_status="APPROVED_FOR_DASHBOARD",
        enrichment_status="UNENRICHED",
        evidence_ids=(), source_ids=("parcel_master",),
        lead_origin_type="OWNER_STATUS",
        event_source="parcel_master",
        owner_source="parcel_master",
        enrichment_source=None,
        qualification_status="QUALIFIED",
        qualification_evidence={"is_estate": True},
    )
    check("§3.8 dataclass: ScoredLeadRecord constructs with all v5.5.0 "
          "provenance fields populated",
          obj.lead_origin_type == "OWNER_STATUS"
          and obj.qualification_status == "QUALIFIED"
          and obj.qualification_evidence == {"is_estate": True})

    # --- Report ----------------------------------------------------------
    failed = [c for c in checks if c[0] == "FAIL"]
    for s, l, d in checks:
        print(f"  [{s}] {l}")
        if s == "FAIL" and d:
            print(f"         detail: {d}")
    if failed:
        print(f"FAIL: source-role enum + provenance — {len(failed)}/"
              f"{len(checks)} checks failed")
        return 1
    print(f"PASS: §0.1 / §3.8 source-role enum + provenance (v5.5.0) — "
          f"all {len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
