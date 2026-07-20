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
import time
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
PARCEL_INDEX_PATH = REPO_ROOT / "data" / "cache" / "parcel_owner_index.jsonl"
PROBATE_MATCH_CACHE_PATH = REPO_ROOT / "data" / "cache" / "probate_parcel_match_cache.json"
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


def _load_probate_match_cache() -> Optional[dict]:
    """Return cached probate parcel matches, or None if stale/missing."""
    if not PROBATE_MATCH_CACHE_PATH.exists():
        return None
    cache_mtime = PROBATE_MATCH_CACHE_PATH.stat().st_mtime
    if PARCEL_INDEX_PATH.exists() and PARCEL_INDEX_PATH.stat().st_mtime > cache_mtime:
        print("  Probate match cache stale (parcel index updated) — will recompute")
        return None
    if PROBATE_DETAIL_JSONL_PATH.exists() and PROBATE_DETAIL_JSONL_PATH.stat().st_mtime > cache_mtime:
        print("  Probate match cache stale (detail file updated) — will recompute")
        return None
    try:
        with open(PROBATE_MATCH_CACHE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def _save_probate_match_cache(payload: dict) -> None:
    PROBATE_MATCH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PROBATE_MATCH_CACHE_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def _load_jsonl(path: Path, max_records: Optional[int]) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("change_status") == "DISAPPEARED":
                continue
            records.append(rec)
            if max_records is not None and len(records) >= max_records:
                break
    return records


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--max-records", type=int, default=None,
        help="Max records per source (default: None = no limit, loads all records)",
    )
    ap.add_argument(
        "--verbose", action="store_true", default=False,
        help="Print per-record debug lines (APNs/case numbers suppressed by default)",
    )
    ap.add_argument(
        "--use-local-index", action="store_true", default=False,
        help=(
            "Load the local parcel index (data/cache/parcel_owner_index.jsonl) "
            "as the resolver backend. Required for production runs: treasurer APN "
            "lookups become O(1) dict hits; NOTS name scans become in-memory only. "
            "Adds ~234s startup but eliminates live API calls for all 169K+ records."
        ),
    )
    args = ap.parse_args()

    _t_wall = time.perf_counter()
    _stage_times: dict[str, float] = {}

    def _log_stage(name: str, t_start: float) -> float:
        elapsed = time.perf_counter() - t_start
        _stage_times[name] = elapsed
        print(f"  [timing] {name}: {elapsed:.2f}s")
        return time.perf_counter()

    print(f"=== Maricopa Combined Pipeline (max_records={args.max_records}) ===")
    print(f"Output: {OUT_DIR}")
    print()

    # -------------------------------------------------------------------------
    # Stage 1 — Raw source loading
    # -------------------------------------------------------------------------
    if not NOTS_JSONL_PATH.exists():
        print(f"ERROR: {NOTS_JSONL_PATH} not found. Run the recorder scraper first.")
        sys.exit(1)

    t = time.perf_counter()
    nots_raw_records = _load_jsonl(NOTS_JSONL_PATH, args.max_records)
    print(f"Loaded {len(nots_raw_records):,} NOTS records")

    treasurer_raw_records: list[dict] = []
    if TREASURER_JSONL_PATH.exists():
        treasurer_raw_records = load_treasurer_jsonl(TREASURER_JSONL_PATH, args.max_records)
        print(f"Loaded {len(treasurer_raw_records):,} treasurer records")
    else:
        print(f"NOTE: {TREASURER_JSONL_PATH.name} not found — skipping treasurer")

    eviction_raw_records: list[dict] = []
    if EVICTION_JSONL_PATH.exists():
        eviction_raw_records = load_eviction_jsonl(EVICTION_JSONL_PATH, args.max_records)
        print(f"Loaded {len(eviction_raw_records):,} eviction records")
    else:
        print(f"NOTE: {EVICTION_JSONL_PATH.name} not found — skipping eviction")

    civil_raw_records: list[dict] = []
    if CIVIL_JSONL_PATH.exists():
        civil_raw_records = load_civil_jsonl(CIVIL_JSONL_PATH, args.max_records)
        print(f"Loaded {len(civil_raw_records):,} civil records")
    else:
        print(f"NOTE: {CIVIL_JSONL_PATH.name} not found — skipping civil")

    probate_raw_records: list[dict] = []
    if PROBATE_JSONL_PATH.exists():
        probate_raw_records = load_probate_jsonl(PROBATE_JSONL_PATH, args.max_records)
        print(f"Loaded {len(probate_raw_records):,} probate records")
    else:
        print(f"NOTE: {PROBATE_JSONL_PATH.name} not found — skipping probate")

    t = _log_stage("raw_source_loading", t)
    print()

    # -------------------------------------------------------------------------
    # Stage 2a — Local parcel index (optional, for production-scale runs)
    # -------------------------------------------------------------------------
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _local_idx = None

    if args.use_local_index:
        if not PARCEL_INDEX_PATH.exists():
            print(f"ERROR: --use-local-index set but {PARCEL_INDEX_PATH} not found.")
            sys.exit(1)
        from local_parcel_index import LocalParcelIndex
        mb = PARCEL_INDEX_PATH.stat().st_size // 1024 // 1024
        print(f"Loading local parcel index ({mb} MB) for bulk resolution…")
        t = time.perf_counter()
        _local_idx = LocalParcelIndex().load(PARCEL_INDEX_PATH)
        t = _log_stage("parcel_index_loading", t)
        print(f"  {_local_idx.record_count:,} residential records indexed")
        print()

    if _local_idx is not None:
        resolver = APNResolver(
            cache_path=OUT_DIR / "assessor_cache.json",
            fetch_fn=_local_idx.fetch_fn,
        )
    else:
        resolver = APNResolver(cache_path=OUT_DIR / "assessor_cache.json")

    # -------------------------------------------------------------------------
    # Stage 2b — NOTS APN resolution
    # -------------------------------------------------------------------------

    parcel_by_apn: dict[str, dict] = {}
    raw_events: list[dict] = []
    apn_stats: dict[str, int] = {
        CONFIDENCE_CONFIRMED: 0,
        CONFIDENCE_POSSIBLE: 0,
        CONFIDENCE_AMBIGUOUS: 0,
        CONFIDENCE_UNRESOLVED: 0,
    }

    print(f"=== NOTS APN resolution ({len(nots_raw_records)} records) ===")
    t = time.perf_counter()
    for raw_rec in nots_raw_records:
        payload = raw_rec.get("raw_payload", {})
        names: list[str] = payload.get("names") or []
        individual_names = [
            n for n in names
            if debtor_party_engine.classify_owner_type(n) in ("INDIVIDUAL", "ESTATE", "TRUST")
        ]
        apn_result = resolver.resolve(individual_names)
        conf = apn_result["confidence"]
        resolved_apn: Optional[str] = apn_result.get("apn")
        parcel_attrs: Optional[dict] = apn_result.get("attrs")
        apn_stats[conf] = apn_stats.get(conf, 0) + 1
        if resolved_apn and parcel_attrs:
            parcel_by_apn[resolved_apn] = _parcel_dict_from_attrs(parcel_attrs)
        phys_addr_str = (parcel_attrs.get("PHYSICAL_ADDRESS") if parcel_attrs else None)
        raw_event = _recorder_to_raw_event(raw_rec, resolved_apn, phys_addr_str)
        raw_events.append(raw_event)

    t = _log_stage("nots_apn_resolution", t)
    print(f"  APN stats: {apn_stats}")
    print(f"  Resolved (CONFIRMED+POSSIBLE): "
          f"{apn_stats[CONFIDENCE_CONFIRMED] + apn_stats[CONFIDENCE_POSSIBLE]}/{len(nots_raw_records)}")
    print()

    # -------------------------------------------------------------------------
    # Stage 2b — Treasurer APN resolution
    # -------------------------------------------------------------------------
    print(f"=== Treasurer APN resolution ({len(treasurer_raw_records)} records) ===")
    t = time.perf_counter()
    treasurer_raw_events: list[dict] = []
    treasurer_parcel_by_apn: dict[str, dict] = {}
    if treasurer_raw_records:
        treasurer_raw_events, treasurer_parcel_by_apn = build_treasurer_raw_events(
            treasurer_raw_records,
            resolver=resolver,
            parcel_dict_from_attrs=_parcel_dict_from_attrs,
            verbose=args.verbose,
        )
    t = _log_stage("treasurer_resolution", t)
    print()

    # -------------------------------------------------------------------------
    # Stage 2c — Eviction processing
    # -------------------------------------------------------------------------
    print(f"=== Eviction processing ({len(eviction_raw_records)} records) ===")
    t = time.perf_counter()
    eviction_raw_events: list[dict] = []
    if eviction_raw_records:
        eviction_raw_events = build_eviction_raw_events(eviction_raw_records, verbose=args.verbose)
    else:
        print("  No eviction records loaded.")
    t = _log_stage("eviction_processing", t)
    print()

    # -------------------------------------------------------------------------
    # Stage 2d — Civil processing
    # -------------------------------------------------------------------------
    print(f"=== Civil processing ({len(civil_raw_records)} records) ===")
    t = time.perf_counter()
    civil_raw_events: list[dict] = []
    if civil_raw_records:
        civil_raw_events = build_civil_raw_events(civil_raw_records, verbose=args.verbose)
    else:
        print("  No civil records loaded.")
    t = _log_stage("civil_processing", t)
    print()

    # -------------------------------------------------------------------------
    # Stage 2e — Probate: detail + parcel matching (cache avoids 657 MB reload)
    # -------------------------------------------------------------------------
    print(f"=== Probate processing ({len(probate_raw_records)} records) ===")
    probate_raw_events: list[dict] = []
    probate_parcel_by_apn: dict[str, dict] = {}
    probate_match_stats: dict[str, int] = {}
    parcel_match_by_id: dict[str, dict] = {}
    probate_detail: dict = {}

    if probate_raw_records:
        t = time.perf_counter()
        probate_detail = load_probate_detail_jsonl(PROBATE_DETAIL_JSONL_PATH)
        t = _log_stage("probate_detail_loading", t)
        if probate_detail:
            print(f"  Detail index: {len(probate_detail)} enriched records")
        else:
            print("  No detail file — all records route to REVIEW_REQUIRED")

        if probate_detail and PARCEL_INDEX_PATH.exists():
            t = time.perf_counter()
            cached = _load_probate_match_cache()
            if cached is not None:
                parcel_match_by_id = cached.get("parcel_match_by_id", {})
                probate_parcel_by_apn = cached.get("probate_parcel_by_apn", {})
                # Reconstruct match stats from cached results
                for res in parcel_match_by_id.values():
                    _c = res.get("match_confidence", "")
                    probate_match_stats[_c] = probate_match_stats.get(_c, 0) + 1
                print(f"  Parcel match cache hit: {len(parcel_match_by_id)} entries "
                      f"({len(probate_parcel_by_apn)} confirmed APNs) — index load skipped")
                t = _log_stage("probate_parcel_matching_cached", t)
            else:
                try:
                    from probate_parcel_matcher import (
                        match_record as _pm_match,
                        MATCH_CONFIRMED as _PM_CONFIRMED,
                    )
                    if _local_idx is not None:
                        _idx = _local_idx
                        print(f"  Reusing already-loaded parcel index ({_idx.record_count:,} records)")
                    else:
                        from local_parcel_index import LocalParcelIndex
                        print(f"  Loading parcel index ({PARCEL_INDEX_PATH.stat().st_size // 1024 // 1024} MB)…")
                        _idx = LocalParcelIndex().load(PARCEL_INDEX_PATH)
                        t = _log_stage("parcel_index_loading", t)
                        print(f"  Index loaded: {_idx.record_count:,} residential records")

                    t = time.perf_counter()
                    _probate_resolver = APNResolver(fetch_fn=_idx.fetch_fn)
                    for rid, det in probate_detail.items():
                        try:
                            res = _pm_match(det, _probate_resolver)
                            parcel_match_by_id[rid] = res
                            _c = res["match_confidence"]
                            probate_match_stats[_c] = probate_match_stats.get(_c, 0) + 1
                            if _c == _PM_CONFIRMED:
                                apn = res.get("apn")
                                if apn:
                                    attrs = _probate_resolver.resolve_by_apn(apn=apn)
                                    if attrs:
                                        probate_parcel_by_apn[apn] = _parcel_dict_from_attrs(attrs)
                        except Exception:
                            pass
                    t = _log_stage("probate_match_loop", t)
                    print(f"  Match stats: {probate_match_stats}")
                    _save_probate_match_cache({
                        "parcel_match_by_id": parcel_match_by_id,
                        "probate_parcel_by_apn": probate_parcel_by_apn,
                    })
                    print(f"  Match cache saved ({len(parcel_match_by_id)} entries) → "
                          f"{PROBATE_MATCH_CACHE_PATH.name}")
                except ImportError:
                    print("  local_parcel_index not available — skipping probate parcel matching")
        elif probate_detail:
            print(f"  No local parcel index found — skipping matching")

        t = time.perf_counter()
        probate_raw_events = build_probate_raw_events(
            probate_raw_records,
            detail_by_id=probate_detail,
            parcel_match_by_id=parcel_match_by_id or None,
            verbose=args.verbose,
        )
        t = _log_stage("probate_event_building", t)
    else:
        print("  No probate records loaded.")
    print()

    # -------------------------------------------------------------------------
    # Merge and combine
    # -------------------------------------------------------------------------
    resolver.save_cache()
    # NOTS wins on collision, then probate CONFIRMED, then treasurer
    combined_parcel_by_apn = {**treasurer_parcel_by_apn, **probate_parcel_by_apn, **parcel_by_apn}

    all_raw_events = (
        raw_events + treasurer_raw_events + eviction_raw_events
        + civil_raw_events + probate_raw_events
    )
    print(f"Total raw events: {len(all_raw_events)} "
          f"({len(raw_events)} NOTS + {len(treasurer_raw_events)} treasurer "
          f"+ {len(eviction_raw_events)} eviction + {len(civil_raw_events)} civil"
          f" + {len(probate_raw_events)} probate)")
    print()

    # -------------------------------------------------------------------------
    # Stage 3/4 — Staged pipeline
    # -------------------------------------------------------------------------
    def enrichment_provider(parcel_id: Optional[str]) -> Optional[dict]:
        if not parcel_id:
            return None
        return combined_parcel_by_apn.get(str(parcel_id).strip())

    print(f"=== Staged pipeline ({len(all_raw_events)} events) ===")
    print(f"  enrichment_provider: {len(combined_parcel_by_apn)} parcels cached")
    t = time.perf_counter()
    result = run_staged_pipeline(
        all_raw_events,
        workdir=OUT_DIR,
        debtor_party_rules=_COMBINED_DEBTOR_RULES,
        enrichment_provider=enrichment_provider,
        approve_needs_review=True,
    )
    t = _log_stage("staged_pipeline", t)
    print()

    # -------------------------------------------------------------------------
    # Results
    # -------------------------------------------------------------------------
    debtor_resolved = result.get("debtor_resolved") or []
    matched_leads = result.get("matched_leads") or []
    scored_leads = result.get("scored_leads") or []
    semantic_verdict = result.get("semantic_verdict", "?")

    multi_signal_count = sum(
        1 for sl in scored_leads
        if len(sl.get("signals") or []) >= 2
    )
    source_signal_counts: dict[str, int] = {}
    for sl in scored_leads:
        for sig in (sl.get("signals") or []):
            for src in (sig.get("source_ids") or []):
                source_signal_counts[src] = source_signal_counts.get(src, 0) + 1

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

    _probate_src = "superior_court_probate"
    probate_scored = sum(
        1 for sl in scored_leads
        if any(
            _probate_src in (sig.get("source_ids") or [])
            for sig in (sl.get("signals") or [])
        )
    )
    probate_enriched = sum(
        1 for sl in scored_leads
        if sl.get("primary_parcel_id") and any(
            _probate_src in (sig.get("source_ids") or [])
            for sig in (sl.get("signals") or [])
        )
    )
    probate_tax_overlaps = sum(
        1 for sl in scored_leads
        if any(
            _probate_src in (sig.get("source_ids") or [])
            for sig in (sl.get("signals") or [])
        ) and any(
            "treasurer_tax_lien" in (sig.get("source_ids") or [])
            for sig in (sl.get("signals") or [])
        )
    )
    probate_nots_overlaps = sum(
        1 for sl in scored_leads
        if any(
            _probate_src in (sig.get("source_ids") or [])
            for sig in (sl.get("signals") or [])
        ) and any(
            "recorder_maricopa" in (sig.get("source_ids") or [])
            for sig in (sl.get("signals") or [])
        )
    )
    review_required_total = sum(
        1 for sl in scored_leads
        if sl.get("enrichment_status") == "REVIEW_REQUIRED"
        or (sl.get("debtor_resolution_status") or "") == "REVIEW_REQUIRED"
    )

    print("=== Pipeline Results ===")
    print(f"  raw_events:           {len(all_raw_events)} "
          f"({len(raw_events)} NOTS + {len(treasurer_raw_events)} treasurer "
          f"+ {len(eviction_raw_events)} eviction + {len(civil_raw_events)} civil"
          f" + {len(probate_raw_events)} probate)")
    print(f"  debtor_resolved:      {len(debtor_resolved)}")
    print(f"  matched_leads:        {len(matched_leads)}")
    print(f"  semantic_verdict:     {semantic_verdict}")
    print(f"  scored_leads:         {len(scored_leads)}")
    print(f"  multi_signal_leads:   {multi_signal_count}")
    print(f"  court_only_leads:     {court_only_count}")
    print(f"  review_required:      {review_required_total}")
    print(f"  source_signal_counts: {source_signal_counts}")
    print()
    print("=== Probate Parcel Match Results ===")
    print(f"  parcel_matches_loaded:{len(parcel_match_by_id)}")
    print(f"  confirmed_matches:    {probate_match_stats.get('CONFIRMED_DECEDENT_OWNER_MATCH', 0)}")
    print(f"  possible_candidates:  {probate_match_stats.get('POSSIBLE_DECEDENT_OWNER_MATCH', 0)}")
    print(f"  ambiguous_candidates: {probate_match_stats.get('AMBIGUOUS_OWNER_MATCH', 0)}")
    print(f"  probate_scored:       {probate_scored}")
    print(f"  probate_enriched:     {probate_enriched}")
    print(f"  probate_tax_overlaps: {probate_tax_overlaps}")
    print(f"  probate_nots_overlaps:{probate_nots_overlaps}")
    print()

    # Debtor resolution status counts (no PII)
    status_summary: dict[str, int] = {}
    for dr in debtor_resolved:
        s = dr.get("debtor_resolution_status", "?")
        status_summary[s] = status_summary.get(s, 0) + 1
    print(f"  debtor_status_counts: {status_summary}")
    print()

    print(f"Outputs: {OUT_DIR}")
    for p in [result.get("scored_leads_path"), result.get("matched_leads_path"),
              *(result.get("leads_base_paths") or [])]:
        if p:
            print(f"  {p}")
    print()

    # -------------------------------------------------------------------------
    # Timing summary
    # -------------------------------------------------------------------------
    total_elapsed = time.perf_counter() - _t_wall
    slowest = max(_stage_times, key=_stage_times.__getitem__) if _stage_times else "n/a"
    print("=== Timing Summary ===")
    for stage, elapsed in sorted(_stage_times.items(), key=lambda kv: -kv[1]):
        pct = int(elapsed / total_elapsed * 20) if total_elapsed > 0 else 0
        bar = "█" * max(1, pct)
        print(f"  {stage:<40} {elapsed:6.2f}s  {bar}")
    print(f"  {'TOTAL':<40} {total_elapsed:6.2f}s")
    print()
    print("=== Run Summary ===")
    print(f"  max_records:      {args.max_records}")
    print(f"  total_runtime:    {total_elapsed:.2f}s")
    print(f"  lead_count:       {len(scored_leads)}")
    print(f"  semantic_verdict: {semantic_verdict}")
    print(f"  slowest_stage:    {slowest} ({_stage_times.get(slowest, 0):.2f}s)")
    blocker = slowest if _stage_times.get(slowest, 0) > 30 else "none"
    print(f"  blocker:          {blocker}")


if __name__ == "__main__":
    main()
