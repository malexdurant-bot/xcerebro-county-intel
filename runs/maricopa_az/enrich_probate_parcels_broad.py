"""
Probate decedent-to-parcel enrichment for the broad mixed-surname sample.

Reads confirmed estate cases from data/raw/superior_court_probate_detail_broad.jsonl,
runs local parcel matching against the full residential index, and reports
aggregate counts only.

Requires:
    data/cache/parcel_owner_index.jsonl           (full 1.6M residential index)
    data/raw/superior_court_probate_detail_broad.jsonl  (from broad_sample_pipeline)

Usage:
    python runs/maricopa_az/enrich_probate_parcels_broad.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
THIS_DIR = Path(__file__).parent
for _p in (str(THIS_DIR), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from apn_resolver import APNResolver
from local_parcel_index import LocalParcelIndex
from probate_parcel_matcher import (
    match_record,
    MATCH_CONFIRMED,
    MATCH_POSSIBLE,
    MATCH_AMBIGUOUS,
    MATCH_NONE,
)

DETAIL_JSONL = REPO_ROOT / "data" / "raw" / "superior_court_probate_detail_broad.jsonl"
NOTS_JSONL = REPO_ROOT / "data" / "raw" / "recorder_maricopa.jsonl"
TREASURER_JSONL = REPO_ROOT / "data" / "raw" / "treasurer_tax_lien.jsonl"
INDEX_PATH = REPO_ROOT / "data" / "cache" / "parcel_owner_index.jsonl"


def _load_detail_records(path: Path) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def _load_apn_set(jsonl_path: Path) -> set[str]:
    apns: set[str] = set()
    if not jsonl_path.exists():
        return apns
    with open(jsonl_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = rec.get("raw_payload") or {}
            apn = (
                payload.get("APN")
                or payload.get("apn")
                or payload.get("parcel_number")
                or ""
            )
            if apn:
                apns.add(str(apn).replace("-", "").strip())
    return apns


def main() -> None:
    if not DETAIL_JSONL.exists():
        print(f"ERROR: {DETAIL_JSONL} not found.")
        print("Run broad_sample_pipeline.cmd steps 1 and 2 first.")
        sys.exit(1)

    if not INDEX_PATH.exists():
        print(f"ERROR: {INDEX_PATH} not found.")
        print("Run pull_parcel_owner_index.cmd first to build the local parcel index.")
        sys.exit(1)

    print("=== Broad Sample — Probate Decedent-to-Parcel Enrichment (local index) ===")
    print(f"Detail input:  {DETAIL_JSONL.name}")
    print(f"Parcel index:  {INDEX_PATH.name}")
    print()

    print("Loading local parcel owner index...")
    idx = LocalParcelIndex().load(INDEX_PATH)
    print(f"  Records loaded: {idx.record_count:,}")
    print()

    resolver = APNResolver(fetch_fn=idx.fetch_fn)

    all_records = _load_detail_records(DETAIL_JSONL)
    estate_records = [r for r in all_records if r.get("detail", {}).get("is_estate_case")]
    noise_records = [r for r in all_records if r.get("detail", {}).get("is_noise_case")]

    print(f"Total detail records:   {len(all_records)}")
    print(f"Confirmed estate cases: {len(estate_records)}")
    print(f"Noise cases (excluded): {len(noise_records)}")
    print()

    if not estate_records:
        print("No estate cases to process. Exiting.")
        return

    nots_apns = _load_apn_set(NOTS_JSONL)
    treasurer_apns = _load_apn_set(TREASURER_JSONL)

    stats = {
        MATCH_CONFIRMED: 0,
        MATCH_POSSIBLE: 0,
        MATCH_AMBIGUOUS: 0,
        MATCH_NONE: 0,
        "apns_found": 0,
        "probate_nots_overlaps": 0,
        "probate_tax_overlaps": 0,
        "fetch_errors": 0,
        "no_hit": 0,
        "entity_rejected": 0,
        "single_token_rejected": 0,
        "not_estate": 0,
    }

    for i, outer in enumerate(estate_records):
        detail = outer.get("detail") or {}
        tier = outer.get("_surname_tier", "unknown")
        try:
            result = match_record(detail, resolver)
        except Exception as exc:
            stats["fetch_errors"] += 1
            print(f"  [{i+1}] ERROR: {exc}")
            continue

        conf = result["match_confidence"]
        stats[conf] = stats.get(conf, 0) + 1

        reason = result.get("reject_reason") or ""
        if reason == "no_assessor_hit":
            stats["no_hit"] += 1
        elif reason == "entity_owner_on_assessor":
            stats["entity_rejected"] += 1
        elif reason == "single_token_rejected":
            stats["single_token_rejected"] += 1
        elif reason == "not_an_estate_case":
            stats["not_estate"] += 1

        apn = result.get("apn")
        if apn:
            stats["apns_found"] += 1
            apn_norm = str(apn).replace("-", "").strip()
            if apn_norm in nots_apns:
                stats["probate_nots_overlaps"] += 1
            if apn_norm in treasurer_apns:
                stats["probate_tax_overlaps"] += 1

        strategy = result.get("strategy_used") or "—"
        cand = result.get("candidate_count", 0)
        tag = reason if reason else (f"candidates={cand}" if cand else f"strategy={strategy}")
        print(f"  [{i+1:3d}] [{tier}]  {conf}  {tag}")

    print()
    print("=== Aggregate Results (no PII) ===")
    print(f"  index_records_loaded:               {idx.record_count:,}")
    print(f"  probate_records_pulled:             {len(all_records)}")
    print(f"  detail_pages_loaded:                {len(all_records)}")
    print(f"  confirmed_estate_cases_tested:      {len(estate_records)}")
    print(f"  CONFIRMED_DECEDENT_OWNER_MATCH:     {stats[MATCH_CONFIRMED]}")
    print(f"  POSSIBLE_DECEDENT_OWNER_MATCH:      {stats[MATCH_POSSIBLE]}")
    print(f"  AMBIGUOUS_OWNER_MATCH:              {stats[MATCH_AMBIGUOUS]}")
    print(f"  NO_OWNER_MATCH:                     {stats[MATCH_NONE]}")
    print(f"    of which no_assessor_hit:         {stats['no_hit']}")
    print(f"    of which entity_rejected:         {stats['entity_rejected']}")
    print(f"    of which single_token_rejected:   {stats['single_token_rejected']}")
    print(f"  apns_found:                         {stats['apns_found']}")
    print(f"  probate_nots_overlaps:              {stats['probate_nots_overlaps']}")
    print(f"  probate_tax_overlaps:               {stats['probate_tax_overlaps']}")
    print(f"  fetch_errors:                       {stats['fetch_errors']}")

    blockers = []
    if stats[MATCH_CONFIRMED] == 0 and len(estate_records) > 0:
        blockers.append("0 CONFIRMED — all estate cases may be noise, entities, or very common names")
    if stats["fetch_errors"] > 0:
        blockers.append(f"{stats['fetch_errors']} errors — check index integrity")

    print()
    if blockers:
        print("  Blockers:")
        for b in blockers:
            print(f"    - {b}")
    else:
        print("  Blockers: none")
    print()
    if stats[MATCH_CONFIRMED] > 0:
        print(f"  {stats[MATCH_CONFIRMED]} CONFIRMED match(es) found.")
        print("  These APNs qualify to populate property_refs.parcel_id once")
        print("  operator approves pipeline integration.")
    else:
        print("  No CONFIRMED matches — do not populate property_refs.parcel_id.")


if __name__ == "__main__":
    main()
