#!/usr/bin/env python3
"""v5.5.0 §4 invariants — semantic_verify new check classes 13-16.

Pins:
  - Check 13 §4.1: TAX_DEFAULT scored_leads must carry QUALIFIED status
    with §3.3 five-criteria evidence; gaps → INVALID.
  - Check 14 §4.2: eventless scored_leads (no source_ids / evidence_ids /
    event_source) → INVALID (inflated board).
  - Check 15 §4.3: DEAD-BOARD rule — all-Unknown owner board when parcel
    keys exist and enrichment was possible → INVALID;
    enrichment_join_unavailable=True override → AMBIGUOUS; healthy mix →
    VALID.
  - Check 16 §4.6: scheduled-event scored_leads with primary_event_date in
    the past → INVALID; POST_SALE_TITLE_EVENT / SURPLUS_EVENT / TAX_DEFAULT
    / OWNER_STATUS origins exempt; SKIPPED without as_of.

Run: python3 scaffold/tests/v5_5_0/test_semantic_verify_v550_checks.py
Exit 0 = pass, non-zero = fail.
"""
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scaffold.pipeline import semantic_verify as sv


def _matched_lead(*, lead_id="lead_v55", parcel_id="P1",
                  owner_name="TEST_OWNER", parcel_resolution_status="RESOLVED"):
    """Minimal schema-valid matched_lead for §20 mechanical pre-check."""
    return {
        "lead_id": lead_id,
        "primary_parcel_id": parcel_id,
        "owner_name": owner_name,
        "owner_type": "INDIVIDUAL" if "TEST_OWNER" in owner_name else "UNKNOWN",
        "filer_entity": None,
        "review_reason": None,
        "parcel_resolution_status": parcel_resolution_status,
        "enrichment_status": "UNENRICHED",
        "signals": [{
            "aggregation_key": {
                "parcel_id": parcel_id, "canonical_doc_type": "hospital_lien",
                "signal_type": "Hospital Lien",
            },
            "signal_type": "Hospital Lien",
            "canonical_doc_type": "hospital_lien",
            "count": 1, "instrument_numbers": ["I1"],
            "source_urls": ["https://example.test/I1"],
            "evidence_ids": ["ev1"], "source_ids": ["clerk"],
            "earliest_recorded_date": "2026-04-01",
            "latest_recorded_date": "2026-04-01",
            "recorded_date_range": ["2026-04-01", "2026-04-01"],
        }],
        "source_ids": ["clerk"],
        "evidence_ids": ["ev1"],
    }


def _scored(*, lead_id="lead_v55", parcel_id="P1",
            owner_name="TEST_OWNER",
            lead_origin_type=None, qualification_status="QUALIFIED",
            qualification_evidence=None,
            enrichment_status="UNENRICHED",
            primary_event_date=None,
            source_ids=("clerk",), evidence_ids=("ev1",),
            event_source="clerk"):
    return {
        "scored_lead_id": f"scored_{lead_id}",
        "lead_id": lead_id,
        "primary_parcel_id": parcel_id,
        "owner_name": owner_name,
        "owner_type": "INDIVIDUAL",
        "score": 50, "tier": "Workable",
        "score_reasons": [], "deal_paths": [],
        "title_complexity_score": 0, "title_complexity_tier": "None",
        "title_complexity_contributors": [],
        "pattern_set": ["lien"], "patterns": ["lien"],
        "display_patterns": ["lien"], "stack_depth": 1, "recent_flag": False,
        "attributes": [], "review_flags": [],
        "lead_status": "APPROVED_FOR_DASHBOARD",
        "enrichment_status": enrichment_status,
        "evidence_ids": list(evidence_ids), "source_ids": list(source_ids),
        "primary_event_date": primary_event_date,
        "lead_origin_type": lead_origin_type,
        "event_source": event_source,
        "owner_source": event_source,
        "qualification_status": qualification_status,
        "qualification_evidence": qualification_evidence,
    }


def _status_of(report: dict, check_n: int) -> str:
    for r in report["checks"]:
        if r["check"] == check_n:
            return r["status"]
    return "UNKNOWN"


def main() -> int:
    checks: list = []

    def check(label, ok, detail=""):
        checks.append(("PASS" if ok else "FAIL", label, detail))

    # =====================================================================
    # Check 13 — TAX_DEFAULT qualification
    # =====================================================================
    qualified_evidence = {
        "a_official_source": True, "b_default_condition": True,
        "c_property_tie": True, "d_source_proof": True,
        "e_not_generic_roll": True,
    }

    # SKIPPED when no scored_leads supplied.
    rep = sv.run_semantic_verification([_matched_lead()])
    check("§4.1 check 13 SKIPPED when scored_leads is None",
          _status_of(rep, 13) == "SKIPPED")

    # VALID — TAX_DEFAULT with QUALIFIED + full evidence.
    rep = sv.run_semantic_verification(
        [_matched_lead()],
        scored_leads=[_scored(
            lead_origin_type="TAX_DEFAULT",
            qualification_status="QUALIFIED",
            qualification_evidence=qualified_evidence,
        )],
    )
    check("§4.1 check 13 VALID — TAX_DEFAULT + QUALIFIED + 5-criteria evidence",
          _status_of(rep, 13) == "VALID")

    # INVALID — TAX_DEFAULT WITHOUT QUALIFIED status.
    rep = sv.run_semantic_verification(
        [_matched_lead()],
        scored_leads=[_scored(
            lead_origin_type="TAX_DEFAULT",
            qualification_status="NOT_QUALIFIED",
            qualification_evidence=qualified_evidence,
        )],
    )
    check("§4.1 check 13 INVALID — TAX_DEFAULT without QUALIFIED",
          _status_of(rep, 13) == "INVALID")
    check("§4.1 check 13 INVALID → verdict DEPLOY_BLOCKED",
          rep["verdict"] == "DEPLOY_BLOCKED")

    # INVALID — TAX_DEFAULT with QUALIFIED but missing one criterion.
    incomplete = dict(qualified_evidence)
    incomplete["d_source_proof"] = False
    rep = sv.run_semantic_verification(
        [_matched_lead()],
        scored_leads=[_scored(
            lead_origin_type="TAX_DEFAULT",
            qualification_status="QUALIFIED",
            qualification_evidence=incomplete,
        )],
    )
    check("§4.1 check 13 INVALID — TAX_DEFAULT marked QUALIFIED but the "
          "§3.3 five-criteria evidence is incomplete (d_source_proof False)",
          _status_of(rep, 13) == "INVALID")

    # Non-TAX_DEFAULT origin → check 13 has nothing to flag → VALID.
    rep = sv.run_semantic_verification(
        [_matched_lead()],
        scored_leads=[_scored(lead_origin_type="RECORDED_EVENT")],
    )
    check("§4.1 check 13 VALID when no scored_leads are TAX_DEFAULT "
          "(check has nothing to flag)",
          _status_of(rep, 13) == "VALID")

    # =====================================================================
    # Check 14 — eventless-lead rejection
    # =====================================================================
    # VALID — proper event source + evidence.
    rep = sv.run_semantic_verification(
        [_matched_lead()],
        scored_leads=[_scored(event_source="clerk", source_ids=("clerk",),
                              evidence_ids=("ev1",))],
    )
    check("§4.2 check 14 VALID — scored_lead carries event_source + "
          "source_ids + evidence_ids",
          _status_of(rep, 14) == "VALID")

    # INVALID — empty source_ids.
    rep = sv.run_semantic_verification(
        [_matched_lead()],
        scored_leads=[_scored(
            event_source=None, source_ids=(), evidence_ids=(),
        )],
    )
    check("§4.2 check 14 INVALID — scored_lead with no source / evidence",
          _status_of(rep, 14) == "INVALID")

    # =====================================================================
    # Check 15 — dead-board rule
    # =====================================================================
    # VALID — healthy mix (some ENRICHED, real owners).
    rep = sv.run_semantic_verification(
        [_matched_lead()],
        scored_leads=[
            _scored(lead_id="A", owner_name="TEST_OWNER_A",
                    enrichment_status="ENRICHED"),
            _scored(lead_id="B", owner_name="TEST_OWNER_B",
                    enrichment_status="UNENRICHED"),
        ],
    )
    check("§4.3 check 15 VALID — healthy enrichment mix",
          _status_of(rep, 15) == "VALID")

    # INVALID — DEAD BOARD: 0 ENRICHED + real parcels + 0 known owners.
    rep = sv.run_semantic_verification(
        [_matched_lead()],
        scored_leads=[
            _scored(lead_id="A", owner_name="hospital_lien against unidentified party",
                    parcel_id="P1", enrichment_status="UNENRICHED"),
            _scored(lead_id="B", owner_name="UNKNOWN",
                    parcel_id="P2", enrichment_status="UNENRICHED"),
        ],
    )
    check("§4.3 check 15 INVALID — DEAD BOARD (parcel keys exist, 0 ENRICHED, "
          "all owners unknown — enrichment was possible and not done)",
          _status_of(rep, 15) == "INVALID")

    # AMBIGUOUS override — operator declares enrichment_join_unavailable.
    rep = sv.run_semantic_verification(
        [_matched_lead()],
        scored_leads=[
            _scored(lead_id="A", owner_name="UNKNOWN", parcel_id="P1",
                    enrichment_status="UNENRICHED"),
        ],
        enrichment_join_unavailable=True,
    )
    check("§4.3 check 15 AMBIGUOUS when operator declares "
          "enrichment_join_unavailable=True (§4.3 carve-out — operator "
          "override)",
          _status_of(rep, 15) == "AMBIGUOUS")

    # =====================================================================
    # Check 16 — no-past-sale-as-upcoming
    # =====================================================================
    AS_OF = date(2026, 5, 14)

    # SKIPPED when no as_of supplied.
    rep = sv.run_semantic_verification(
        [_matched_lead()],
        scored_leads=[_scored(
            lead_origin_type="RECORDED_EVENT",
            primary_event_date="2024-01-01",
        )],
    )
    check("§4.6 check 16 SKIPPED when no as_of supplied",
          _status_of(rep, 16) == "SKIPPED")

    # VALID — future event date.
    rep = sv.run_semantic_verification(
        [_matched_lead()],
        scored_leads=[_scored(
            lead_origin_type="RECORDED_EVENT",
            primary_event_date="2026-07-01",
        )],
        as_of=AS_OF,
    )
    check("§4.6 check 16 VALID — future primary_event_date",
          _status_of(rep, 16) == "VALID")

    # INVALID — past scheduled-event lead.
    rep = sv.run_semantic_verification(
        [_matched_lead()],
        scored_leads=[_scored(
            lead_origin_type="RECORDED_EVENT",
            primary_event_date="2024-01-01",
        )],
        as_of=AS_OF,
    )
    check("§4.6 check 16 INVALID — scheduled-event lead with past "
          "primary_event_date (PAST_SALE leaked into upcoming board)",
          _status_of(rep, 16) == "INVALID")

    # POST_SALE_TITLE_EVENT origin → exempt from check 16 (past dates fine).
    rep = sv.run_semantic_verification(
        [_matched_lead()],
        scored_leads=[_scored(
            lead_origin_type="POST_SALE_TITLE_EVENT",
            primary_event_date="2024-12-15",
        )],
        as_of=AS_OF,
    )
    check("§4.6 check 16 VALID — POST_SALE_TITLE_EVENT origin exempt "
          "(post-sale leads correctly carry past sale dates)",
          _status_of(rep, 16) == "VALID")

    # TAX_DEFAULT origin → exempt.
    rep = sv.run_semantic_verification(
        [_matched_lead()],
        scored_leads=[_scored(
            lead_origin_type="TAX_DEFAULT",
            qualification_status="QUALIFIED",
            qualification_evidence=qualified_evidence,
            primary_event_date="2023-01-01",
        )],
        as_of=AS_OF,
    )
    check("§4.6 check 16 VALID — TAX_DEFAULT origin exempt (ongoing "
          "condition, past delinquency-onset date is fine)",
          _status_of(rep, 16) == "VALID")

    # --- Combined: 13/14/15/16 all run + verdict union ------------------
    rep = sv.run_semantic_verification(
        [_matched_lead()],
        scored_leads=[
            _scored(lead_origin_type="TAX_DEFAULT",
                    qualification_status="QUALIFIED",
                    qualification_evidence=qualified_evidence,
                    enrichment_status="ENRICHED"),
        ],
        as_of=AS_OF,
    )
    check("§4 combined: all four new checks 13/14/15/16 appear in report",
          {13, 14, 15, 16}.issubset({r["check"] for r in rep["checks"]}))

    # --- Report -----------------------------------------------------------
    failed = [c for c in checks if c[0] == "FAIL"]
    for s, l, d in checks:
        print(f"  [{s}] {l}")
        if s == "FAIL" and d:
            print(f"         detail: {d}")
    if failed:
        print(f"FAIL: §20 v5.5.0 checks 13-16 — {len(failed)}/{len(checks)} "
              f"checks failed")
        return 1
    print(f"PASS: §20 v5.5.0 checks 13-16 (v5.5.0 §4) — all {len(checks)} "
          f"checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
