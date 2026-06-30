"""
Maricopa County — Daily incremental pipeline.

Processes only new/updated source records (change_status == NEW or UPDATED)
since the last successful run. Updates the lead history store and exports
daily CSVs. Designed for unattended daily execution via Task Scheduler.

Usage:
    python -X utf8 runs/maricopa_az/run_maricopa_daily_incremental.py

Full-rebuild mode (after running run_maricopa_full_rebuild.cmd):
    python -X utf8 runs/maricopa_az/run_maricopa_daily_incremental.py --rebuild

Task Scheduler entry point:
    runs/maricopa_az/run_maricopa_daily_incremental.cmd
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
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

from apn_resolver import APNResolver  # noqa: E402
from treasurer_adapter import build_treasurer_raw_events  # noqa: E402
from eviction_adapter import build_eviction_raw_events  # noqa: E402
from civil_adapter import build_civil_raw_events  # noqa: E402
from lead_history import LeadHistory  # noqa: E402
from daily_export import export_daily_csvs  # noqa: E402
from scaffold.pipeline import debtor_party_engine  # noqa: E402
from scaffold.pipeline.debtor_party_engine import UNIVERSAL_DEBTOR_PARTY_RULES  # noqa: E402
from scaffold.pipeline.run_pipeline_staged import run_staged_pipeline  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

NOTS_JSONL_PATH = REPO_ROOT / "data" / "raw" / "recorder_maricopa.jsonl"
TREASURER_JSONL_PATH = REPO_ROOT / "data" / "raw" / "treasurer_tax_lien.jsonl"
EVICTION_JSONL_PATH = REPO_ROOT / "data" / "raw" / "justice_court_evictions.jsonl"
CIVIL_JSONL_PATH = REPO_ROOT / "data" / "raw" / "superior_court_civil.jsonl"
OUT_DIR = _THIS_DIR / "pipeline_output"               # shared cache lives here
INCREMENTAL_OUT_DIR = OUT_DIR / "incremental"         # delta output — never overwrites full production scored_leads.json
LEAD_HISTORY_PATH = REPO_ROOT / "data" / "state" / "maricopa_lead_history.sqlite"
SCORED_LEADS_PATH = OUT_DIR / "scored_leads.json"     # full production — read by generate_dashboard.py

# ---------------------------------------------------------------------------
# Debtor-party rules — identical to run_pipeline.py
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
# Helpers — kept identical to run_pipeline.py
# ---------------------------------------------------------------------------


def _parcel_dict_from_attrs(attrs: dict) -> dict:
    def s(k):
        v = attrs.get(k)
        return str(v).strip() if v is not None and str(v).strip() else None

    phys_addr = s("PHYSICAL_ADDRESS")
    phys_city = s("PHYSICAL_CITY")
    return {
        "apn": s("APN"),
        "owner_name": s("OWNER_NAME"),
        "address": phys_addr,
        "situs_address": phys_addr,
        "city": phys_city,
        "situs_city": phys_city,
        "situs_state": "AZ",
        "owner_mailing_address": s("MAIL_ADDR1"),
        "owner_mailing_city": s("MAIL_CITY"),
        "owner_mailing_state": s("MAIL_STATE"),
        "owner_mailing_zip": s("MAIL_ZIP"),
        "assessed_value": attrs.get("FCV_CUR"),
        "last_sale_price": attrs.get("SALE_PRICE"),
        "last_sale_date": s("SALE_DATE"),
        "year_built": attrs.get("CONST_YEAR"),
        "property_class": s("PUC"),
    }


def _normalize_recording_date(raw_date: str) -> Optional[str]:
    try:
        return datetime.strptime(raw_date.strip(), "%m-%d-%Y").strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return None


def _tag_parties(names: list[str]) -> list[dict]:
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


# ---------------------------------------------------------------------------
# JSONL loader — new/updated records only
# ---------------------------------------------------------------------------


def _load_new_records(path: Path) -> list[dict]:
    """Load records with change_status NEW or UPDATED, skipping DISAPPEARED/SAME."""
    records: list[dict] = []
    if not path.exists():
        return records
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("change_status") in ("NEW", "UPDATED"):
                records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Rebuild mode — reads scored_leads.json from last full pipeline run
# ---------------------------------------------------------------------------


def _run_rebuild(history: LeadHistory, run_date: str) -> None:
    if not SCORED_LEADS_PATH.exists():
        print(f"ERROR: {SCORED_LEADS_PATH} not found.")
        print("Run run_maricopa_full_rebuild.cmd first to produce scored_leads.json.")
        sys.exit(1)

    scored_leads: list[dict] = json.loads(
        SCORED_LEADS_PATH.read_text(encoding="utf-8")
    )
    print(f"  Loaded {len(scored_leads):,} leads from scored_leads.json")

    counts = history.rebuild_from_leads(scored_leads, run_date)
    history.set_last_run_date(run_date)
    history.log_run(run_date, "full_rebuild", len(scored_leads), counts["new"], 0, 0.0)

    print(f"  History rebuilt: {counts['new']:,} leads inserted")
    print(f"  last_successful_run_date set to {run_date}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rebuild", action="store_true",
        help=(
            "Rebuild lead history from the existing scored_leads.json "
            "(produced by run_maricopa_full_rebuild.cmd). "
            "Does not re-fetch sources or re-run the pipeline."
        ),
    )
    args = ap.parse_args()

    t_wall = time.perf_counter()
    run_date = date.today().isoformat()

    history = LeadHistory(LEAD_HISTORY_PATH)

    if args.rebuild:
        print(f"=== Maricopa Lead History Rebuild ({run_date}) ===")
        print()
        _run_rebuild(history, run_date)
        history.close()
        return

    # ------------------------------------------------------------------
    # Daily incremental mode
    # ------------------------------------------------------------------

    print(f"=== Maricopa Daily Incremental ({run_date}) ===")
    print()

    last_run = history.get_last_run_date()
    known_ids = history.known_lead_ids()
    print(f"  History: {len(known_ids):,} known lead_ids")
    print(f"  Last successful run: {last_run or '(none)'}")
    print()

    # ------------------------------------------------------------------
    # Stage 1 — Load new source records (change_status == NEW or UPDATED)
    # ------------------------------------------------------------------
    t = time.perf_counter()
    nots_new = _load_new_records(NOTS_JSONL_PATH)
    treas_new = _load_new_records(TREASURER_JSONL_PATH)
    eviction_new = _load_new_records(EVICTION_JSONL_PATH)
    civil_new = _load_new_records(CIVIL_JSONL_PATH)
    load_s = time.perf_counter() - t

    print("New source records (change_status=NEW/UPDATED):")
    print(f"  NOTS:      {len(nots_new):>6,}")
    print(f"  Treasurer: {len(treas_new):>6,}")
    print(f"  Eviction:  {len(eviction_new):>6,}")
    print(f"  Civil:     {len(civil_new):>6,}")
    print(f"  [timing] source_loading: {load_s:.2f}s")

    total_new = len(nots_new) + len(treas_new) + len(eviction_new) + len(civil_new)
    if total_new == 0:
        print()
        print("No new source records — nothing to process.")
        history.log_run(run_date, "daily", 0, 0, 0, time.perf_counter() - t_wall)
        history.set_last_run_date(run_date)
        history.close()
        return
    print()

    # ------------------------------------------------------------------
    # Stage 2 — APN resolution (live API; small daily batch)
    # ------------------------------------------------------------------
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    INCREMENTAL_OUT_DIR.mkdir(parents=True, exist_ok=True)
    resolver = APNResolver(cache_path=OUT_DIR / "assessor_cache.json")

    parcel_by_apn: dict[str, dict] = {}
    raw_events: list[dict] = []
    apn_stats: dict[str, int] = {}

    print(f"=== NOTS APN resolution ({len(nots_new)} records) ===")
    t = time.perf_counter()
    for raw_rec in nots_new:
        payload = raw_rec.get("raw_payload", {})
        names: list[str] = payload.get("names") or []
        individual_names = [
            n for n in names
            if debtor_party_engine.classify_owner_type(n) in ("INDIVIDUAL", "ESTATE", "TRUST")
        ]
        apn_result = resolver.resolve(individual_names)
        conf = apn_result["confidence"]
        resolved_apn = apn_result.get("apn")
        parcel_attrs = apn_result.get("attrs")
        apn_stats[conf] = apn_stats.get(conf, 0) + 1
        if resolved_apn and parcel_attrs:
            parcel_by_apn[resolved_apn] = _parcel_dict_from_attrs(parcel_attrs)
        situs = parcel_attrs.get("PHYSICAL_ADDRESS") if parcel_attrs else None
        raw_events.append(_recorder_to_raw_event(raw_rec, resolved_apn, situs))
    nots_s = time.perf_counter() - t
    print(f"  APN stats: {apn_stats}")
    print(f"  [timing] nots_apn_resolution: {nots_s:.2f}s")
    print()

    print(f"=== Treasurer APN resolution ({len(treas_new)} records) ===")
    t = time.perf_counter()
    treasurer_events: list[dict] = []
    treasurer_parcel_by_apn: dict[str, dict] = {}
    if treas_new:
        treasurer_events, treasurer_parcel_by_apn = build_treasurer_raw_events(
            treas_new,
            resolver=resolver,
            parcel_dict_from_attrs=_parcel_dict_from_attrs,
        )
    treas_s = time.perf_counter() - t
    print(f"  [timing] treasurer_resolution: {treas_s:.2f}s")
    print()

    print(f"=== Eviction processing ({len(eviction_new)} records) ===")
    t = time.perf_counter()
    eviction_events = build_eviction_raw_events(eviction_new) if eviction_new else []
    ev_s = time.perf_counter() - t
    print(f"  [timing] eviction_processing: {ev_s:.2f}s")
    print()

    print(f"=== Civil processing ({len(civil_new)} records) ===")
    t = time.perf_counter()
    civil_events = build_civil_raw_events(civil_new) if civil_new else []
    civ_s = time.perf_counter() - t
    print(f"  [timing] civil_processing: {civ_s:.2f}s")
    print()

    # ------------------------------------------------------------------
    # Stage 3 — Staged pipeline on delta only
    # ------------------------------------------------------------------
    resolver.save_cache()
    combined_parcel_by_apn = {**treasurer_parcel_by_apn, **parcel_by_apn}
    all_events = raw_events + treasurer_events + eviction_events + civil_events

    print(
        f"Total delta events: {len(all_events)} "
        f"({len(raw_events)} NOTS + {len(treasurer_events)} treasurer "
        f"+ {len(eviction_events)} eviction + {len(civil_events)} civil)"
    )

    def enrichment_provider(parcel_id: Optional[str]) -> Optional[dict]:
        if not parcel_id:
            return None
        return combined_parcel_by_apn.get(str(parcel_id).strip())

    print(f"=== Staged pipeline ({len(all_events)} events) ===")
    t = time.perf_counter()
    result = run_staged_pipeline(
        all_events,
        workdir=INCREMENTAL_OUT_DIR,
        debtor_party_rules=_COMBINED_DEBTOR_RULES,
        enrichment_provider=enrichment_provider,
        approve_needs_review=True,
    )
    pipeline_s = time.perf_counter() - t
    print(f"  [timing] staged_pipeline: {pipeline_s:.2f}s")

    scored_leads: list[dict] = result.get("scored_leads") or []
    print(f"  scored_leads: {len(scored_leads)}")
    print()

    # ------------------------------------------------------------------
    # Stage 4 — Merge delta into master scored_leads.json
    #
    # The master file is the cumulative dataset read by generate_dashboard.py.
    # New lead_ids are appended; existing ones are updated in place.
    # The full production 159K dataset is never rebuilt just for daily runs.
    # ------------------------------------------------------------------
    master: list[dict] = []
    if SCORED_LEADS_PATH.exists():
        try:
            master = json.loads(SCORED_LEADS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            master = []

    master_by_id: dict[str, int] = {
        lead.get("lead_id"): i
        for i, lead in enumerate(master)
        if lead.get("lead_id")
    }

    added = updated_master = 0
    for lead in scored_leads:
        lid = lead.get("lead_id")
        if not lid:
            continue
        if lid in master_by_id:
            master[master_by_id[lid]] = lead
            updated_master += 1
        else:
            master_by_id[lid] = len(master)
            master.append(lead)
            added += 1

    SCORED_LEADS_PATH.write_text(
        json.dumps(master, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"=== Master scored_leads.json ===")
    print(f"  total: {len(master):,}  (+{added} new, {updated_master} updated)")
    print()

    # ------------------------------------------------------------------
    # Stage 5 — History update
    # ------------------------------------------------------------------
    counts = history.upsert_leads(scored_leads, run_date)
    print(f"  lead_history: +{counts['new']} new, {counts['updated']} updated")

    # ------------------------------------------------------------------
    # Stage 6 — Daily CSV export (new leads only: first_seen_date == today)
    # ------------------------------------------------------------------
    history_by_id = history.get_history_by_id()
    new_leads = [
        sl for sl in scored_leads
        if history_by_id.get(sl.get("lead_id") or "", {}).get("first_seen_date") == run_date
    ]

    export_counts = export_daily_csvs(
        new_leads=new_leads,
        history_by_id=history_by_id,
        run_date=run_date,
    )
    print()
    print("=== Daily Export ===")
    for fname, n in export_counts.items():
        print(f"  {fname}: {n} rows")

    # ------------------------------------------------------------------
    # Finalize
    # ------------------------------------------------------------------
    total_s = time.perf_counter() - t_wall
    history.log_run(
        run_date, "daily", len(master),
        counts["new"], counts["updated"], total_s
    )
    history.set_last_run_date(run_date)
    history.close()

    print()
    print("=== Run Summary ===")
    print(f"  run_date:        {run_date}")
    print(f"  last_run:        {last_run or '(none)'}")
    print(f"  delta_events:    {len(all_events)}")
    print(f"  delta_leads:     {len(scored_leads)}")
    print(f"  master_total:    {len(master):,}")
    print(f"  new_today:       {counts['new']}")
    print(f"  updated:         {counts['updated']}")
    print(f"  runtime:         {total_s:.1f}s")


if __name__ == "__main__":
    main()
