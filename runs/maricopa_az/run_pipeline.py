"""
Maricopa County, AZ — combined NOTS + Treasurer + Eviction + Civil + Probate → normalized leads.

Five PRIMARY_EVENT_SOURCE inputs:
  recorder_maricopa       — Notice of Trustee's Sale (NOTS) records
  treasurer_tax_lien      — Tax lien / delinquent parcel records
  justice_court_evictions — Eviction (FED) docket records
  superior_court_civil    — Civil judgment / lis pendens docket records
  superior_court_probate  — Probate estate / letters docket records

All sources flow through the same staged pipeline call. Parcels that appear
in multiple sources produce multi-signal leads via the §18.B APN aggregation
key. Court records (eviction/civil/probate) carry no APN from listing data
and route to REVIEW_REQUIRED "owner_not_on_document" per §17.K.

Usage (bounded test):
    python runs/maricopa_az/run_pipeline.py --max-records 100

The --max-records cap enforces the bounded-test rule; it applies to each
source independently. No full production pulls without explicit operator
approval.

Output: runs/maricopa_az/pipeline_output/
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Repo bootstrap
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_THIS_DIR = Path(__file__).parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from apn_resolver import (  # noqa: E402 — local module in runs/maricopa_az/
    APNResolver,
    CONFIDENCE_CONFIRMED,
    CONFIDENCE_POSSIBLE,
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_UNRESOLVED,
)
from treasurer_adapter import (  # noqa: E402 — local module in runs/maricopa_az/
    load_treasurer_jsonl,
    build_treasurer_raw_events,
)
from eviction_adapter import (  # noqa: E402 — local module in runs/maricopa_az/
    load_eviction_jsonl,
    build_eviction_raw_events,
)
from civil_adapter import (  # noqa: E402 — local module in runs/maricopa_az/
    load_civil_jsonl,
    build_civil_raw_events,
)
from probate_adapter import (  # noqa: E402 — local module in runs/maricopa_az/
    load_probate_jsonl,
    load_probate_detail_jsonl,
    build_probate_raw_events,
)
from scaffold.pipeline import debtor_party_engine
from scaffold.pipeline.debtor_party_engine import UNIVERSAL_DEBTOR_PARTY_RULES
from scaffold.pipeline.run_pipeline_staged import run_staged_pipeline

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

NOTS_JSONL_PATH = REPO_ROOT / "data" / "raw" / "recorder_maricopa.jsonl"
TREASURER_JSONL_PATH = REPO_ROOT / "data" / "raw" / "treasurer_tax_lien.jsonl"
EVICTION_JSONL_PATH = REPO_ROOT / "data" / "raw" / "justice_court_evictions.jsonl"
CIVIL_JSONL_PATH = REPO_ROOT / "data" / "raw" / "superior_court_civil.jsonl"
PROBATE_JSONL_PATH = REPO_ROOT / "data" / "raw" / "superior_court_probate.jsonl"
PROBATE_DETAIL_JSONL_PATH = REPO_ROOT / "data" / "raw" / "superior_court_probate_detail.jsonl"
OUT_DIR = Path(__file__).parent / "pipeline_output"

# ---------------------------------------------------------------------------
# Combined §17 debtor-party rules for this run.
#
# Starts from UNIVERSAL_DEBTOR_PARTY_RULES (already fan-outted at import to
# include broad-key aliases), then overrides notice_of_sale:
#
#   notice_of_sale override (AZ recorder NOTS):
#     The universal fan-out of foreclosure_notice uses DOCUMENT_BODY — the
#     recorder API provides no document text. This override switches to
#     STRUCTURED mode: GR-tagged individual names are the debtor; GE-tagged
#     corporate names are the servicer/lender filer.
#
#   tax_sale_certificate (treasurer_tax_lien records):
#     Already present in UNIVERSAL_DEBTOR_PARTY_RULES (Session 7 Rule 3).
#     Expects TP (taxpayer) party — supplied by the treasurer adapter's
#     assessor lookup. Records without an assessor hit emit no TP and route
#     to REVIEW_REQUIRED "owner_not_on_document".
# ---------------------------------------------------------------------------

_COMBINED_DEBTOR_RULES: dict = {
    **UNIVERSAL_DEBTOR_PARTY_RULES,
    "notice_of_sale": {
        "expected_debtor_name_type": "GR",
        "fallback_debtor_name_type": None,
        "filer_name_types": ["GE"],
        "debtor_source": "STRUCTURED",
        "known_filer_role": "servicer / substitute trustee / lender",
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parcel_dict_from_attrs(attrs: dict) -> dict:
    """Map ArcGIS assessor attributes to the parcel dict the enrichment
    pipeline and scoring seam expect."""
    def s(k):
        v = attrs.get(k)
        return str(v).strip() if v is not None and str(v).strip() else None

    phys_addr = s("PHYSICAL_ADDRESS")
    phys_city = s("PHYSICAL_CITY")
    return {
        "apn": s("APN"),
        "owner_name": s("OWNER_NAME"),
        # Situs address — used by _parcel_display_from and derive_attributes
        "address": phys_addr,
        "situs_address": phys_addr,
        "city": phys_city,
        "situs_city": phys_city,
        "situs_state": "AZ",
        # Mailing — used by derive_attributes (absentee / out_of_state)
        "owner_mailing_address": s("MAIL_ADDR1"),
        "owner_mailing_city": s("MAIL_CITY"),
        "owner_mailing_state": s("MAIL_STATE"),
        "owner_mailing_zip": s("MAIL_ZIP"),
        # Valuation / sale
        "assessed_value": attrs.get("FCV_CUR"),
        "last_sale_price": attrs.get("SALE_PRICE"),
        "last_sale_date": s("SALE_DATE"),
        "year_built": attrs.get("CONST_YEAR"),
        "property_class": s("PUC"),
    }


def _normalize_recording_date(raw_date: str) -> Optional[str]:
    """Convert 'm-dd-yyyy' or 'mm-dd-yyyy' from the recorder API to ISO date."""
    try:
        return datetime.strptime(raw_date.strip(), "%m-%d-%Y").strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return None


def _tag_parties(names: list[str]) -> list[dict]:
    """Tag each name as GR (individual debtor) or GE (corporate filer).

    Uses §17.F classify_owner_type so the same classification logic the
    engine applies when scoring the debtor is consistent here. INDIVIDUAL /
    ESTATE / TRUST → GR (the property-owner side of a deed of trust).
    ENTITY / UNKNOWN → GE (lender / servicer / trustee side).
    """
    parties = []
    for name in names:
        owner_type = debtor_party_engine.classify_owner_type(name)
        name_type = "GR" if owner_type in ("INDIVIDUAL", "ESTATE", "TRUST") else "GE"
        parties.append({"name": name, "name_type": name_type})
    return parties


def _recorder_to_raw_event(
    raw_record: dict,
    resolved_apn: Optional[str],
    situs_address: Optional[str],
) -> dict:
    """Convert one recorder JSONL record to raw_event_record schema."""
    payload = raw_record.get("raw_payload", {})
    names: list[str] = payload.get("names") or []
    recording_number = str(payload.get("recording_number") or "")
    raw_date = payload.get("recording_date") or ""

    return {
        "raw_event_id": raw_record.get("raw_record_id") or f"evt_{recording_number}",
        "source_id": "recorder_maricopa",
        "source_role": "PRIMARY_EVENT_SOURCE",
        "raw_doc_type": payload.get("doc_type_code"),
        "canonical_doc_type": "notice_of_sale",
        "instrument_number": recording_number or None,
        "recorded_date": _normalize_recording_date(raw_date),
        "source_url": raw_record.get("source_url") or payload.get("doc_detail_url") or "",
        "parties": _tag_parties(names),
        "property_refs": {
            "parcel_id": resolved_apn,
            "situs_address": situs_address,
            "legal_description": None,
            "case_number": None,
        },
        "document_body_text": None,
        "parser_confidence": raw_record.get("parser_confidence"),
        "captured_at": raw_record.get("source_fetched_at"),
    }


def _load_jsonl(path: Path, max_records: int) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
            if len(records) >= max_records:
                break
    return records


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--max-records", type=int, default=5,
        help="Max NOTS records to process (default: 5, per bounded-test rule)",
    )
    args = ap.parse_args()

    print(f"=== Maricopa Combined Pipeline (NOTS + Treasurer + Eviction + Civil + Probate, max_records={args.max_records}) ===")
    print(f"NOTS input:       {NOTS_JSONL_PATH}")
    print(f"Treasurer input:  {TREASURER_JSONL_PATH}")
    print(f"Eviction input:   {EVICTION_JSONL_PATH}")
    print(f"Civil input:      {CIVIL_JSONL_PATH}")
    print(f"Probate input:    {PROBATE_JSONL_PATH}")
    print(f"Output:           {OUT_DIR}")
    print()

    # Step 1 — Load bounded samples
    if not NOTS_JSONL_PATH.exists():
        print(f"ERROR: {NOTS_JSONL_PATH} not found. Run the recorder scraper first.")
        sys.exit(1)

    nots_raw_records = _load_jsonl(NOTS_JSONL_PATH, args.max_records)
    print(f"Loaded {len(nots_raw_records)} NOTS records from {NOTS_JSONL_PATH.name}")

    treasurer_raw_records: list[dict] = []
    if TREASURER_JSONL_PATH.exists():
        treasurer_raw_records = load_treasurer_jsonl(TREASURER_JSONL_PATH, args.max_records)
        print(f"Loaded {len(treasurer_raw_records)} treasurer records from {TREASURER_JSONL_PATH.name}")
    else:
        print(f"NOTE: {TREASURER_JSONL_PATH.name} not found — run fetch_treasurer.cmd first.")

    eviction_raw_records: list[dict] = []
    if EVICTION_JSONL_PATH.exists():
        eviction_raw_records = load_eviction_jsonl(EVICTION_JSONL_PATH, args.max_records)
        print(f"Loaded {len(eviction_raw_records)} eviction records from {EVICTION_JSONL_PATH.name}")
    else:
        print(f"NOTE: {EVICTION_JSONL_PATH.name} not found — run fetch_evictions.cmd first.")

    civil_raw_records: list[dict] = []
    if CIVIL_JSONL_PATH.exists():
        civil_raw_records = load_civil_jsonl(CIVIL_JSONL_PATH, args.max_records)
        print(f"Loaded {len(civil_raw_records)} civil records from {CIVIL_JSONL_PATH.name}")
    else:
        print(f"NOTE: {CIVIL_JSONL_PATH.name} not found — run fetch_civil.cmd first.")

    probate_raw_records: list[dict] = []
    if PROBATE_JSONL_PATH.exists():
        probate_raw_records = load_probate_jsonl(PROBATE_JSONL_PATH, args.max_records)
        print(f"Loaded {len(probate_raw_records)} probate records from {PROBATE_JSONL_PATH.name}")
    else:
        print(f"NOTE: {PROBATE_JSONL_PATH.name} not found — run fetch_probate.cmd first.")
    print()

    # Step 2 — APN resolution + raw_event conversion
    # Collect assessor parcel data keyed by APN for the enrichment_provider.
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    resolver = APNResolver(cache_path=OUT_DIR / "assessor_cache.json")

    parcel_by_apn: dict[str, dict] = {}
    raw_events: list[dict] = []

    apn_stats: dict[str, int] = {
        CONFIDENCE_CONFIRMED: 0,
        CONFIDENCE_POSSIBLE: 0,
        CONFIDENCE_AMBIGUOUS: 0,
        CONFIDENCE_UNRESOLVED: 0,
    }

    for i, raw_rec in enumerate(nots_raw_records):
        payload = raw_rec.get("raw_payload", {})
        names: list[str] = payload.get("names") or []
        rec_num = payload.get("recording_number")
        print(f"[{i+1}] Recording {rec_num} — names: {names}")

        individual_names = [
            n for n in names
            if debtor_party_engine.classify_owner_type(n) in ("INDIVIDUAL", "ESTATE", "TRUST")
        ]
        print(f"     Individual names: {individual_names}")

        apn_result = resolver.resolve(individual_names)
        conf = apn_result["confidence"]
        resolved_apn: Optional[str] = apn_result.get("apn")
        parcel_attrs: Optional[dict] = apn_result.get("attrs")

        apn_stats[conf] = apn_stats.get(conf, 0) + 1

        if conf in (CONFIDENCE_CONFIRMED, CONFIDENCE_POSSIBLE):
            matched = apn_result.get("matched_name")
            strategy = apn_result.get("strategy_used")
            phys_addr = (parcel_attrs or {}).get("PHYSICAL_ADDRESS")
            print(f"     APN={resolved_apn}  conf={conf}  strategy={strategy}  name={matched!r}  addr={phys_addr}")
        elif conf == CONFIDENCE_AMBIGUOUS:
            n_cands = len(apn_result.get("candidates") or [])
            print(f"     APN: AMBIGUOUS ({n_cands} candidates) — skipping enrichment")
        else:
            print(f"     APN: UNRESOLVED — no assessor match")

        # Cache parcel data for enrichment_provider
        if resolved_apn and parcel_attrs:
            parcel_by_apn[resolved_apn] = _parcel_dict_from_attrs(parcel_attrs)

        # Build raw_event_record
        phys_addr_str = (parcel_attrs.get("PHYSICAL_ADDRESS") if parcel_attrs else None)
        raw_event = _recorder_to_raw_event(raw_rec, resolved_apn, phys_addr_str)
        raw_events.append(raw_event)
        print()

    # Step 2b — Treasurer records: resolve owner via assessor APN lookup
    print(f"=== Treasurer APN → Assessor resolution ===")
    treasurer_raw_events: list[dict] = []
    treasurer_parcel_by_apn: dict[str, dict] = {}
    if treasurer_raw_records:
        treasurer_raw_events, treasurer_parcel_by_apn = build_treasurer_raw_events(
            treasurer_raw_records,
            resolver=resolver,
            parcel_dict_from_attrs=_parcel_dict_from_attrs,
        )
    print()

    # Step 2c — Eviction records: no APN lookup possible (no address in listing)
    print(f"=== Eviction records ===")
    eviction_raw_events: list[dict] = []
    if eviction_raw_records:
        eviction_raw_events = build_eviction_raw_events(eviction_raw_records)
    else:
        print(f"  No eviction records loaded.")
    print()

    # Step 2d — Civil records: case type inferred from case_number prefix; no APN lookup
    print(f"=== Civil records ===")
    civil_raw_events: list[dict] = []
    if civil_raw_records:
        civil_raw_events = build_civil_raw_events(civil_raw_records)
    else:
        print(f"  No civil records loaded.")
    print()

    # Step 2e — Probate records: merge detail if available; noise cases filtered
    print(f"=== Probate records ===")
    probate_raw_events: list[dict] = []
    if probate_raw_records:
        probate_detail = load_probate_detail_jsonl(PROBATE_DETAIL_JSONL_PATH)
        if probate_detail:
            print(f"  Detail index loaded: {len(probate_detail)} enriched records")
        else:
            print(f"  No detail file found — all records route to REVIEW_REQUIRED")
            print(f"  (run enrich_probate_detail.cmd to enable decedent resolution)")
        probate_raw_events = build_probate_raw_events(probate_raw_records, detail_by_id=probate_detail)
    else:
        print(f"  No probate records loaded.")
    print()

    # Merge parcel maps (treasurer APN hits supplement NOTS hits; NOTS wins on collision)
    # Court records (eviction/civil/probate) add nothing to parcel_by_apn (no APN from listing)
    combined_parcel_by_apn = {**treasurer_parcel_by_apn, **parcel_by_apn}

    resolver.save_cache()
    print(f"=== NOTS APN resolution summary ===")
    print(f"  {apn_stats}")
    print(f"  Resolved (CONFIRMED+POSSIBLE): {apn_stats[CONFIDENCE_CONFIRMED] + apn_stats[CONFIDENCE_POSSIBLE]}/{len(nots_raw_records)}")
    print()

    # Combine raw events from all five sources
    all_raw_events = (
        raw_events + treasurer_raw_events + eviction_raw_events
        + civil_raw_events + probate_raw_events
    )
    print(f"Total raw events for pipeline: {len(all_raw_events)} "
          f"({len(raw_events)} NOTS + {len(treasurer_raw_events)} treasurer "
          f"+ {len(eviction_raw_events)} eviction + {len(civil_raw_events)} civil"
          f" + {len(probate_raw_events)} probate)")
    print()

    # Step 3 — Build enrichment_provider closure over combined parcel_by_apn
    def enrichment_provider(parcel_id: Optional[str]) -> Optional[dict]:
        if not parcel_id:
            return None
        return combined_parcel_by_apn.get(str(parcel_id).strip())

    # Step 4 — Run staged pipeline (both sources in one call)
    print(f"Running staged pipeline on {len(all_raw_events)} events...")
    print(f"  debtor_party_rules: combined (UNIVERSAL + notice_of_sale override)")
    print(f"  enrichment_provider: {len(combined_parcel_by_apn)} parcels cached")
    print(f"  approve_needs_review: True")
    print(f"  sources: NOTS + Treasurer + Eviction + Civil + Probate")
    print()

    result = run_staged_pipeline(
        all_raw_events,
        workdir=OUT_DIR,
        debtor_party_rules=_COMBINED_DEBTOR_RULES,
        enrichment_provider=enrichment_provider,
        approve_needs_review=True,
    )

    # Step 5 — Report summary (no unredacted personal data in transcript)
    debtor_resolved = result.get("debtor_resolved") or []
    matched_leads = result.get("matched_leads") or []
    scored_leads = result.get("scored_leads") or []
    semantic_verdict = result.get("semantic_verdict", "?")

    # Count multi-signal leads (parcel in both NOTS and treasurer)
    multi_signal_count = sum(
        1 for sl in scored_leads
        if len(sl.get("signals") or []) >= 2
    )
    # Count per-source contributions in scored leads
    source_signal_counts: dict[str, int] = {}
    for sl in scored_leads:
        for sig in (sl.get("signals") or []):
            for src in (sig.get("source_ids") or []):
                source_signal_counts[src] = source_signal_counts.get(src, 0) + 1

    # Count court-only leads (signals exclusively from court sources, no NOTS/Treasurer)
    _court_sources = {"justice_court_evictions", "superior_court_civil", "superior_court_probate"}
    court_only_count = sum(
        1 for sl in scored_leads
        if all(
            all(src in _court_sources for src in (sig.get("source_ids") or []))
            for sig in (sl.get("signals") or [])
        ) and any(
            any(src in _court_sources for src in (sig.get("source_ids") or []))
            for sig in (sl.get("signals") or [])
        )
    )

    print("=== Pipeline Results ===")
    print(f"  Raw events fed:       {len(all_raw_events)} "
          f"({len(raw_events)} NOTS + {len(treasurer_raw_events)} treasurer "
          f"+ {len(eviction_raw_events)} eviction + {len(civil_raw_events)} civil"
          f" + {len(probate_raw_events)} probate)")
    print(f"  §17 debtor_resolved:  {len(debtor_resolved)}")
    print(f"  §19 matched_leads:    {len(matched_leads)}")
    print(f"  §20 semantic_verdict: {semantic_verdict}")
    print(f"  Scored leads:         {len(scored_leads)}")
    print(f"  Multi-signal leads:   {multi_signal_count}  (parcel in 2+ sources)")
    print(f"  Court-only leads:     {court_only_count}  (eviction/civil/probate, no NOTS/Treasurer)")
    print(f"  Source signal counts: {source_signal_counts}")
    print()

    # Per-record summary
    print("=== Per-record summary ===")
    for dr in debtor_resolved:
        status = dr.get("debtor_resolution_status")
        method = dr.get("debtor_extraction_method")
        owner_type = dr.get("owner_type")
        parcel_id = (dr.get("property_refs") or {}).get("parcel_id")
        instr = dr.get("instrument_number")
        review_reason = dr.get("review_reason")
        apn_tag = f"APN:{parcel_id}" if parcel_id else "APN:unresolved"
        if status == "DEBTOR_RESOLVED":
            print(f"  {instr}  status={status}  type={owner_type}  method={method}  {apn_tag}")
        else:
            print(f"  {instr}  status={status}  reason={review_reason!r}  {apn_tag}")

    print()
    print("=== Scored leads summary ===")
    for sl in scored_leads:
        lead_id = sl.get("lead_id")
        score = sl.get("score")
        enrich_status = sl.get("enrichment_status")
        primary_parcel = sl.get("primary_parcel_id")
        attrs = sl.get("attributes") or []
        print(f"  lead_id={lead_id}  score={score}  enrichment={enrich_status}  "
              f"parcel={primary_parcel}  attrs={attrs}")

    print()
    print(f"Outputs written to: {OUT_DIR}")
    print(f"  {result.get('scored_leads_path')}")
    print(f"  {result.get('matched_leads_path')}")
    leads_base_paths = result.get("leads_base_paths") or []
    for p in leads_base_paths:
        print(f"  {p}")


if __name__ == "__main__":
    main()
