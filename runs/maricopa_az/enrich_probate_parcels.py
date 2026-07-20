"""
Bounded probate decedent-to-parcel enrichment runner.

Reads confirmed estate cases from data/raw/superior_court_probate_detail.jsonl,
runs each decedent name through the Maricopa assessor owner-name index via
probate_parcel_matcher.match_record(), and reports aggregate counts only.

No names, APNs, case numbers, or addresses are printed.

Usage:
    python runs/maricopa_az/enrich_probate_parcels.py
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
from probate_parcel_matcher import (
    match_record,
    MATCH_CONFIRMED,
    MATCH_POSSIBLE,
    MATCH_AMBIGUOUS,
    MATCH_NONE,
)

DETAIL_JSONL = REPO_ROOT / "data" / "raw" / "superior_court_probate_detail.jsonl"
NOTS_JSONL = REPO_ROOT / "data" / "raw" / "recorder_maricopa.jsonl"
TREASURER_JSONL = REPO_ROOT / "data" / "raw" / "treasurer_tax_lien.jsonl"
CACHE_PATH = THIS_DIR / "pipeline_output" / "assessor_cache.json"


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
    """Return set of all APNs present in a raw JSONL (recorder or treasurer)."""
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
        print(f"ERROR: {DETAIL_JSONL} not found. Run enrich_probate_detail.cmd first.")
        sys.exit(1)

    print("=== Probate Decedent-to-Parcel Enrichment (bounded) ===")
    print(f"Input:  {DETAIL_JSONL.name}")
    print(f"Cache:  {CACHE_PATH.name} (loaded if exists, new queries added)")
    print()

    all_records = _load_detail_records(DETAIL_JSONL)
    estate_records = [r for r in all_records if r.get("detail", {}).get("is_estate_case")]

    print(f"Total detail records:   {len(all_records)}")
    print(f"Confirmed estate cases: {len(estate_records)}")
    print()

    if not estate_records:
        print("No estate cases to process. Exiting.")
        return

    # Load cross-signal APN sets for overlap reporting
    nots_apns = _load_apn_set(NOTS_JSONL)
    treasurer_apns = _load_apn_set(TREASURER_JSONL)

    resolver = APNResolver(cache_path=CACHE_PATH)
    print(f"Assessor cache loaded:  {resolver.cache_size()} entries")
    print()

    stats = {
        MATCH_CONFIRMED: 0,
        MATCH_POSSIBLE: 0,
        MATCH_AMBIGUOUS: 0,
        MATCH_NONE: 0,
        "apns_found": 0,
        "probate_nots_overlaps": 0,
        "probate_tax_overlaps": 0,
        "fetch_errors": 0,
    }

    for i, outer in enumerate(estate_records):
        detail = outer.get("detail") or {}
        try:
            result = match_record(detail, resolver)
        except Exception as exc:
            stats["fetch_errors"] += 1
            print(f"  [{i+1}] ERROR: {exc}")
            continue

        conf = result["match_confidence"]
        stats[conf] = stats.get(conf, 0) + 1

        apn = result.get("apn")
        if apn:
            stats["apns_found"] += 1
            apn_norm = str(apn).replace("-", "").strip()
            if apn_norm in nots_apns:
                stats["probate_nots_overlaps"] += 1
            if apn_norm in treasurer_apns:
                stats["probate_tax_overlaps"] += 1

        # Aggregate-only output — no names/APNs/addresses
        strategy = result.get("strategy_used") or "—"
        reason = result.get("reject_reason") or ""
        cand = result.get("candidate_count", 0)
        tag = f"reason={reason}" if reason else (f"candidates={cand}" if cand else f"strategy={strategy}")
        print(f"  [{i+1}] {conf}  {tag}")

    resolver.save_cache()
    print()
    print(f"=== Aggregate Results (no PII) ===")
    print(f"  confirmed_estate_cases_tested:  {len(estate_records)}")
    print(f"  CONFIRMED_DECEDENT_OWNER_MATCH: {stats[MATCH_CONFIRMED]}")
    print(f"  POSSIBLE_DECEDENT_OWNER_MATCH:  {stats[MATCH_POSSIBLE]}")
    print(f"  AMBIGUOUS_OWNER_MATCH:          {stats[MATCH_AMBIGUOUS]}")
    print(f"  NO_OWNER_MATCH:                 {stats[MATCH_NONE]}")
    print(f"  apns_found:                     {stats['apns_found']}")
    print(f"  probate_nots_overlaps:          {stats['probate_nots_overlaps']}")
    print(f"  probate_tax_overlaps:           {stats['probate_tax_overlaps']}")
    print(f"  fetch_errors:                   {stats['fetch_errors']}")

    blockers = []
    if stats[MATCH_CONFIRMED] == 0 and len(estate_records) > 0:
        blockers.append("0 CONFIRMED matches — assessor name format may differ from detail parser output")
    if stats["fetch_errors"] > 0:
        blockers.append(f"{stats['fetch_errors']} fetch errors — check assessor API connectivity")

    print()
    if blockers:
        print("  Blockers:")
        for b in blockers:
            print(f"    - {b}")
    else:
        print("  Blockers: none")

    print()
    print("Next: if CONFIRMED > 0, update probate_adapter.py to populate")
    print("      property_refs.parcel_id from CONFIRMED_DECEDENT_OWNER_MATCH.")


if __name__ == "__main__":
    main()
