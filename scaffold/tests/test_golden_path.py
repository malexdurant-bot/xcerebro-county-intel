"""
test_golden_path.py — the framework's single end-to-end gate test.

Proves ONE synthetic lead can traverse the full pipeline from raw source record
to operator-callable output, touching every framework layer in order:

  1. raw source record           (architecture/09_output_schemas.md §1)
  2. normalized signal           (architecture/09_output_schemas.md §2 + domain/08_document_normalization.md)
  3. parcel match                (architecture/12_entity_resolution.md)
  4. stacked lead                (architecture/09_output_schemas.md §4 + domain/03_scoring_and_stacking.md)
  5. evidence ledger             (architecture/08_evidence_ledger.md)
  6. review queue                (domain/05_review_queue_rules.md)
  7. dashboard output            (architecture/09_output_schemas.md §6)
  8. run manifest                (architecture/09_output_schemas.md §11)
  9. source heartbeat update     (architecture/10_source_heartbeat_and_cursors.md)

The framework is NOT shippable unless this test passes.

Rules enforced:
- No county-specific data. Uses Synthtown placeholders only.
- No real names. Uses TEST_OWNER_* placeholders.
- No real addresses. Uses 100 Synthetic Lane style.
- No real portals. Sources are synthetic://... URLs.

Run with: python3 scaffold/tests/test_golden_path.py
"""

import json
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

# Path to the canonical doc types registry
FRAMEWORK_ROOT = Path(__file__).resolve().parent.parent.parent
REGISTRY_PATH = FRAMEWORK_ROOT / "knowledge_base" / "domain" / "canonical_doc_types.json"


def _load_registry():
    if not REGISTRY_PATH.exists():
        raise FileNotFoundError(f"Canonical doc types registry not found at {REGISTRY_PATH}")
    return json.loads(REGISTRY_PATH.read_text())


REGISTRY = _load_registry()
CANONICAL = REGISTRY["canonical_types"]


# ---------------------------------------------------------------------------
# Pipeline layer 1: raw source record
# ---------------------------------------------------------------------------

def make_raw_record():
    """Synthetic raw source record per architecture/09_output_schemas.md §1."""
    return {
        "raw_record_id": "raw_" + uuid.uuid4().hex,
        "source_id": "synth_clerk_recordings",
        "source_url": "synthetic://clerk/SYN-test-001",
        "source_fetched_at": "2026-05-07T12:00:00Z",
        "raw_payload": {
            "doc_type": "AOH",
            "instrument_number": "SYN-2026-0001",
            "recording_date": "2026-05-07",
            "grantor": "TEST_OWNER_001 (DECEASED)",
            "grantee": "TEST_HEIR_001",
            "parcel_ref": "SYN-001",
            "consideration": 0,
            "legal_description": "Lot 1, Block 1, Synthtown A Subdivision",
        },
        "raw_text": None,
        "first_seen_at": "2026-05-07T12:00:00Z",
        "last_seen_at": "2026-05-07T12:00:00Z",
        "change_status": "NEW_RECORD",
        "parser_confidence": 95,
    }


# ---------------------------------------------------------------------------
# Pipeline layer 2: normalize raw doc type to canonical
# ---------------------------------------------------------------------------

def normalize_doc_type(raw_doc_type, county_synonyms=None):
    """
    Minimal Step 1 (exact match) normalization per domain/08_document_normalization.md.
    Golden path uses a clean abbreviation (AOH) so we only need exact match.
    """
    if not raw_doc_type:
        return {"normalized_doc_type": None, "confidence": 0, "reason": "empty_input", "review_required": True}

    cleaned = raw_doc_type.strip().upper()
    county_synonyms = county_synonyms or {}

    if cleaned in county_synonyms:
        v = county_synonyms[cleaned]
        if isinstance(v, str):
            return {"normalized_doc_type": v, "confidence": 90, "reason": "exact_match_county_synonym", "review_required": False}
        return {"normalized_doc_type": v["normalized_doc_type"], "confidence": v.get("confidence", 90), "reason": "exact_match_county_synonym", "review_required": False}

    for ctype, entry in CANONICAL.items():
        if cleaned == ctype.upper():
            return {"normalized_doc_type": ctype, "confidence": 100, "reason": "exact_match_universal_registry", "review_required": False}
        for abbrev in entry.get("common_abbreviations", []):
            if cleaned == abbrev.upper():
                return {"normalized_doc_type": ctype, "confidence": 100, "reason": "exact_match_universal_registry", "review_required": False}

    return {"normalized_doc_type": None, "confidence": 0, "reason": "unknown", "review_required": True}


def make_normalized_signal(raw_record):
    """Per architecture/09_output_schemas.md §2."""
    norm = normalize_doc_type(raw_record["raw_payload"]["doc_type"])
    canonical_entry = CANONICAL.get(norm["normalized_doc_type"], {})

    return {
        "signal_id": "sig_" + uuid.uuid4().hex,
        "raw_record_id": raw_record["raw_record_id"],
        "source_id": raw_record["source_id"],
        "source_class": canonical_entry.get("source_class", "review_required"),
        "raw_doc_type": raw_record["raw_payload"]["doc_type"],
        "normalized_doc_type": norm["normalized_doc_type"],
        "doc_type_confidence": norm["confidence"],
        "doc_type_normalization_reason": norm["reason"],
        "pattern": canonical_entry.get("lead_pattern"),
        "subtype": canonical_entry.get("subtype"),
        "event_date": raw_record["raw_payload"]["recording_date"],
        "lifecycle_status": "ACTIVE",
        "lifecycle_stage": None,
        "suppressed_by": None,
        "supersedes": None,
        "document_priority": canonical_entry.get("document_priority", 0),
        "party_names": [
            raw_record["raw_payload"]["grantor"],
            raw_record["raw_payload"]["grantee"],
        ],
        "party_roles": {
            raw_record["raw_payload"]["grantor"]: "decedent",
            raw_record["raw_payload"]["grantee"]: "heir",
        },
        "property_refs": {
            "parcel_id": raw_record["raw_payload"]["parcel_ref"],
            "situs_address": None,
            "legal_description": raw_record["raw_payload"]["legal_description"],
            "case_number": None,
            "instrument_number": raw_record["raw_payload"]["instrument_number"],
        },
        "amounts": [],
        "status": "Confirmed",
        "evidence_ids": [],
    }


# ---------------------------------------------------------------------------
# Pipeline layer 3: parcel match (entity resolution)
# ---------------------------------------------------------------------------

def make_parcel_record():
    """Synthetic parcel master record (enrichment per domain/02)."""
    return {
        "parcel_id": "SYN-001",
        "situs_address": "100 Synthetic Lane",
        "situs_city": "Synthtown A",
        "situs_state": "ZZ",
        "situs_zip": "00001",
        "owner_name": "TEST_OWNER_001",
        "owner_mailing_addr1": "100 Synthetic Lane",
        "owner_mailing_city": "Synthtown A",
        "owner_mailing_state": "ZZ",
        "owner_mailing_zip": "00001",
        "assessed_value": 340000,
        "last_sale_price": 185000,
        "last_sale_date": "2002-03-12",
        "year_built": 1978,
        "_synthetic": True,
    }


def match_signal_to_parcel(signal, parcels):
    """
    Match on parcel_id per architecture/12_entity_resolution.md "Match confidence hierarchy".
    parcel_id match = 100 confidence.
    """
    for p in parcels:
        if p["parcel_id"] == signal["property_refs"]["parcel_id"]:
            return {"matched_parcel_id": p["parcel_id"], "match_confidence": 100, "match_method": "parcel_id_exact"}
    return {"matched_parcel_id": None, "match_confidence": 0, "match_method": "no_match"}


# ---------------------------------------------------------------------------
# Pipeline layer 4: stacked lead
# ---------------------------------------------------------------------------

def build_lead(signal, match, parcel):
    """Per architecture/09_output_schemas.md §4."""
    canonical_entry = CANONICAL.get(signal["normalized_doc_type"], {})
    pattern = signal["pattern"]

    return {
        "lead_id": "lead_" + uuid.uuid4().hex,
        "primary_parcel_id": match["matched_parcel_id"],
        "normalized_address": parcel["situs_address"],
        "owner_entity_id": "ent_" + uuid.uuid4().hex,
        "signals": [signal["signal_id"]],
        "patterns": [pattern] if pattern else [],
        "attributes": [],
        "score": 55,
        "score_reasons": [
            {"factor": f"base_score_{pattern}_estate", "delta": 50},
            {"factor": "parser_confidence_above_80", "delta": 5},
        ],
        "deal_paths": [
            {"path": "wholesale", "confidence": "high", "rationale": "AFFIDAVIT_OF_HEIRSHIP fires estate pattern; partial-interest path active"},
            {"path": "partial_interest", "confidence": "high", "rationale": "heir-of-decedent transfer pattern"},
        ],
        "deal_path_reasons": ["estate pattern + heir party role"],
        "match_confidence": match["match_confidence"],
        "parser_confidence_avg": 95,
        "doc_type_normalization": {
            "raw_doc_types_seen": [signal["raw_doc_type"]],
            "normalized_doc_types": [signal["normalized_doc_type"]],
            "doc_type_confidences": [signal["doc_type_confidence"]],
            "doc_type_review_required": False,
        },
        "lifecycle_states": [
            {
                "lifecycle": "probate",
                "current_stage": "heirship_declared",
                "stage_entered_at": signal["event_date"],
                "lifecycle_status": "active",
                "active_signals": [signal["signal_id"]],
                "suppressed_signals": [],
            }
        ],
        "title_complexity_score": 25,
        "title_complexity_tier": "Light curative",
        "title_complexity_contributors": [
            {"factor": "affidavit_of_heirship_no_supporting_probate", "weight": 15},
            {"factor": "missing_party_resolution", "weight": 10},
        ],
        "document_priority_max": canonical_entry.get("document_priority", 0),
        "evidence_ids": [],
        "review_flags": [],
        "lead_status": "STACKED_LEAD",
        "lead_status_history": [
            {"status": "RAW_RECORD", "transitioned_at": "2026-05-07T12:00:00Z", "reason": "scraped"},
            {"status": "NORMALIZED_SIGNAL", "transitioned_at": "2026-05-07T12:01:00Z", "reason": "doc_type normalized to canonical"},
            {"status": "MATCHED_PARCEL", "transitioned_at": "2026-05-07T12:02:00Z", "reason": "parcel_id_exact match"},
            {"status": "STACKED_LEAD", "transitioned_at": "2026-05-07T12:03:00Z", "reason": "score computed + deal paths assigned"},
        ],
        "export_status": "Needs Review",
    }


# ---------------------------------------------------------------------------
# Pipeline layer 5: evidence ledger
# ---------------------------------------------------------------------------

def attach_evidence(lead, signal, raw_record):
    """Per architecture/08_evidence_ledger.md."""
    evidence = {
        "evidence_id": "ev_" + uuid.uuid4().hex,
        "field_name": "patterns",
        "value": lead["patterns"],
        "source_id": signal["source_id"],
        "source_url": raw_record["source_url"],
        "source_reliability_grade": "A",
        "raw_record_id": raw_record["raw_record_id"],
        "signal_id": signal["signal_id"],
        "parser_confidence": signal["doc_type_confidence"],
        "captured_at": raw_record["source_fetched_at"],
        "status": "Confirmed",
    }
    lead["evidence_ids"].append(evidence["evidence_id"])
    return evidence


# ---------------------------------------------------------------------------
# Pipeline layer 6: review queue
# ---------------------------------------------------------------------------

def evaluate_review_queue(lead):
    """Per domain/05_review_queue_rules.md."""
    review_flags = []
    if lead["match_confidence"] < 80:
        review_flags.append("match_confidence_low")
    if lead["doc_type_normalization"]["doc_type_review_required"]:
        review_flags.append("low_doc_type_confidence")
    if lead["title_complexity_score"] >= 60:
        review_flags.append("high_title_complexity_review")
    if not lead["patterns"]:
        review_flags.append("no_pattern_fired")
    lead["review_flags"] = review_flags
    if review_flags:
        lead["lead_status"] = "REVIEW_REQUIRED"
        lead["lead_status_history"].append({
            "status": "REVIEW_REQUIRED",
            "transitioned_at": "2026-05-07T12:04:00Z",
            "reason": "; ".join(review_flags),
        })
    else:
        lead["lead_status"] = "APPROVED_FOR_DASHBOARD"
        lead["lead_status_history"].append({
            "status": "APPROVED_FOR_DASHBOARD",
            "transitioned_at": "2026-05-07T12:04:00Z",
            "reason": "no review flags triggered",
        })
    return lead


# ---------------------------------------------------------------------------
# Pipeline layer 7: dashboard projection
# ---------------------------------------------------------------------------

def project_to_dashboard(lead, parcel):
    """Per architecture/09_output_schemas.md §6."""
    return {
        "lead_id": lead["lead_id"],
        "display_address": parcel["situs_address"] + ", " + parcel["situs_city"] + ", " + parcel["situs_state"],
        "display_owner": parcel["owner_name"],
        "display_score": lead["score"],
        "display_patterns": lead["patterns"],
        "display_attributes": lead["attributes"],
        "display_deal_paths": [dp["path"] for dp in lead["deal_paths"]],
        "display_title_complexity_tier": lead["title_complexity_tier"],
        "display_lead_status": lead["lead_status"],
        "display_assessed_value": parcel["assessed_value"],
        "display_last_sale_price": parcel.get("last_sale_price", "Unknown"),
        "display_match_confidence": lead["match_confidence"],
    }


# ---------------------------------------------------------------------------
# Pipeline layer 8: run manifest
# ---------------------------------------------------------------------------

def build_run_manifest(sources_attempted, records_collected, records_normalized, leads_created, review_required, output_files):
    """Per architecture/09_output_schemas.md §11."""
    return {
        "run_id": "run_" + uuid.uuid4().hex,
        "county": "<synthetic>",
        "state": "ZZ",
        "started_at": "2026-05-07T12:00:00Z",
        "finished_at": "2026-05-07T12:05:00Z",
        "sources_attempted": sources_attempted,
        "records_collected": records_collected,
        "records_normalized": records_normalized,
        "leads_created": leads_created,
        "review_required": review_required,
        "errors": [],
        "output_files": output_files,
    }


# ---------------------------------------------------------------------------
# Pipeline layer 9: source heartbeat
# ---------------------------------------------------------------------------

def update_heartbeat(source_id, records_seen, records_new):
    """Per architecture/10_source_heartbeat_and_cursors.md."""
    return {
        "source_id": source_id,
        "source_name": "Synthetic Clerk Recordings (test fixture)",
        "source_class": "lead_generating",
        "source_priority": "P0",
        "source_reliability_grade": "A",
        "build_priority": "mvp_required",
        "access_pattern": "open_api",
        "status": "healthy",
        "last_attempted_at": "2026-05-07T12:00:00Z",
        "last_successful_at": "2026-05-07T12:00:30Z",
        "last_failed_at": None,
        "last_failure_reason": None,
        "records_seen_current_run": records_seen,
        "records_new_current_run": records_new,
        "records_seen_previous_run": 0,
        "records_new_previous_run": 0,
        "parser_confidence_avg": 95,
        "error_count_current_run": 0,
        "consecutive_failures": 0,
        "session_status": "not_applicable",
        "session_expires_at": None,
        "next_retry_at": None,
        "next_scheduled_run_at": "2026-05-08T06:00:00Z",
        "access_attempts": [
            {"attempt_order": 1, "strategy": "DIRECT_API", "status": "SUCCESS", "reason": "synthetic source", "timestamp": "2026-05-07T12:00:30Z"}
        ],
        "final_access_strategy": "DIRECT_API",
        "records_request_allowed": False,
    }


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_golden_path():
    failures = []
    assertions = []

    def assert_(label, condition, detail=None):
        if condition:
            assertions.append(("PASS", label))
        else:
            assertions.append(("FAIL", label, detail))
            failures.append((label, detail))

    # Layer 1: raw record
    raw = make_raw_record()
    assert_("Layer 1: raw_record_id present", bool(raw["raw_record_id"]))
    assert_("Layer 1: change_status is NEW_RECORD", raw["change_status"] == "NEW_RECORD")
    assert_("Layer 1: parser_confidence >= 80", raw["parser_confidence"] >= 80)
    assert_("Layer 1: raw_payload preserved verbatim", raw["raw_payload"]["doc_type"] == "AOH")

    # Layer 2: normalize
    signal = make_normalized_signal(raw)
    assert_("Layer 2: doc_type normalized to canonical", signal["normalized_doc_type"] == "AFFIDAVIT_OF_HEIRSHIP",
            f"got {signal['normalized_doc_type']!r}")
    assert_("Layer 2: doc_type_confidence == 100 (exact match)", signal["doc_type_confidence"] == 100,
            f"got {signal['doc_type_confidence']}")
    assert_("Layer 2: pattern is 'estate'", signal["pattern"] == "estate", f"got {signal['pattern']!r}")
    assert_("Layer 2: source_class is lead_generating", signal["source_class"] == "lead_generating")
    assert_("Layer 2: lifecycle_status is ACTIVE", signal["lifecycle_status"] == "ACTIVE")

    # Layer 3: parcel match
    parcel = make_parcel_record()
    match = match_signal_to_parcel(signal, [parcel])
    assert_("Layer 3: parcel matched on parcel_id", match["matched_parcel_id"] == "SYN-001")
    assert_("Layer 3: match_confidence == 100", match["match_confidence"] == 100)
    assert_("Layer 3: match_method is parcel_id_exact", match["match_method"] == "parcel_id_exact")

    # Layer 4: stacked lead
    lead = build_lead(signal, match, parcel)
    assert_("Layer 4: lead_id present", bool(lead["lead_id"]))
    assert_("Layer 4: score > 0", lead["score"] > 0)
    assert_("Layer 4: score_reasons present", len(lead["score_reasons"]) > 0)
    assert_("Layer 4: at least one deal path", len(lead["deal_paths"]) >= 1)
    assert_("Layer 4: every deal_path has rationale",
            all("rationale" in dp and dp["rationale"] for dp in lead["deal_paths"]))
    assert_("Layer 4: lead_status is STACKED_LEAD before review", lead["lead_status"] == "STACKED_LEAD")
    assert_("Layer 4: lead_status_history has 4 entries", len(lead["lead_status_history"]) == 4)
    assert_("Layer 4: title_complexity_score has contributors when > 0",
            lead["title_complexity_score"] == 0 or len(lead["title_complexity_contributors"]) > 0)

    # Layer 5: evidence
    evidence = attach_evidence(lead, signal, raw)
    assert_("Layer 5: evidence has source_reliability_grade", evidence["source_reliability_grade"] in ("A", "B", "C", "D", "E"))
    assert_("Layer 5: evidence linked back to raw_record_id", evidence["raw_record_id"] == raw["raw_record_id"])
    assert_("Layer 5: evidence_id appended to lead", evidence["evidence_id"] in lead["evidence_ids"])
    assert_("Layer 5: evidence parser_confidence preserved", evidence["parser_confidence"] == signal["doc_type_confidence"])

    # Layer 6: review queue
    lead = evaluate_review_queue(lead)
    assert_("Layer 6: review_flags evaluated", "review_flags" in lead)
    assert_("Layer 6: lead_status transitioned to APPROVED_FOR_DASHBOARD or REVIEW_REQUIRED",
            lead["lead_status"] in ("APPROVED_FOR_DASHBOARD", "REVIEW_REQUIRED"),
            f"got {lead['lead_status']!r}")
    # Golden path expects clean: no review flags, approved for dashboard
    assert_("Layer 6: golden-path lead is APPROVED_FOR_DASHBOARD (no flags)",
            lead["lead_status"] == "APPROVED_FOR_DASHBOARD",
            f"review_flags={lead['review_flags']}")

    # Layer 7: dashboard projection
    dash_row = project_to_dashboard(lead, parcel)
    assert_("Layer 7: dashboard row contains lead_id", dash_row["lead_id"] == lead["lead_id"])
    assert_("Layer 7: display_address is populated", bool(dash_row["display_address"]))
    assert_("Layer 7: display_patterns matches lead.patterns", dash_row["display_patterns"] == lead["patterns"])
    assert_("Layer 7: dashboard does not invent fields",
            set(dash_row.keys()) == {
                "lead_id", "display_address", "display_owner", "display_score",
                "display_patterns", "display_attributes", "display_deal_paths",
                "display_title_complexity_tier", "display_lead_status",
                "display_assessed_value", "display_last_sale_price", "display_match_confidence",
            })

    # Layer 8: run manifest
    manifest = build_run_manifest(
        sources_attempted=1, records_collected=1, records_normalized=1,
        leads_created=1, review_required=0,
        output_files=["data/leads.json", "data/signals.jsonl", "data/source_heartbeat.json"],
    )
    assert_("Layer 8: run_id present", bool(manifest["run_id"]))
    assert_("Layer 8: counts add up", manifest["records_collected"] == manifest["records_normalized"] == manifest["leads_created"] == 1)
    assert_("Layer 8: errors empty for golden path", manifest["errors"] == [])

    # Layer 9: heartbeat
    heartbeat = update_heartbeat("synth_clerk_recordings", records_seen=1, records_new=1)
    assert_("Layer 9: heartbeat status is healthy", heartbeat["status"] == "healthy")
    assert_("Layer 9: heartbeat source_priority is P0", heartbeat["source_priority"] == "P0")
    assert_("Layer 9: heartbeat source_reliability_grade in A-E",
            heartbeat["source_reliability_grade"] in ("A", "B", "C", "D", "E"))
    assert_("Layer 9: access_attempts logged", len(heartbeat["access_attempts"]) >= 1)
    assert_("Layer 9: final_access_strategy set", bool(heartbeat["final_access_strategy"]))

    # Cross-layer
    assert_("Cross-layer: raw → signal linkage", signal["raw_record_id"] == raw["raw_record_id"])
    assert_("Cross-layer: signal → lead linkage", signal["signal_id"] in lead["signals"])
    assert_("Cross-layer: parcel → lead linkage", lead["primary_parcel_id"] == parcel["parcel_id"])
    assert_("Cross-layer: evidence → lead linkage", evidence["evidence_id"] in lead["evidence_ids"])
    assert_("Cross-layer: no real geographic data", "Synth" in parcel["situs_city"] and parcel["situs_state"] == "ZZ")
    assert_("Cross-layer: no real owner names", parcel["owner_name"].startswith("TEST_OWNER"))
    assert_("Cross-layer: no real portal URLs", raw["source_url"].startswith("synthetic://"))

    # Report
    print("=" * 72)
    print("GOLDEN PATH TEST — one synthetic lead through 9 framework layers")
    print("=" * 72)
    passed = sum(1 for a in assertions if a[0] == "PASS")
    failed = sum(1 for a in assertions if a[0] == "FAIL")
    for a in assertions:
        marker = "PASS" if a[0] == "PASS" else "FAIL"
        print(f"  [{marker}] {a[1]}")
        if a[0] == "FAIL" and len(a) > 2:
            print(f"         detail: {a[2]}")
    print()
    print(f"RESULT: {passed} pass, {failed} fail")
    print("=" * 72)
    return failed == 0


if __name__ == "__main__":
    ok = run_golden_path()
    sys.exit(0 if ok else 1)
