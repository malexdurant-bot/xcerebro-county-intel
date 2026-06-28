"""
Pull Maricopa residential parcel owner index from ArcGIS MapServer.

Queries WHERE PUC LIKE 'R%' (residential parcels only), pages through all
results, and writes compact JSONL to data/cache/parcel_owner_index.jsonl.
Geometry is NOT pulled — name and address fields only.

Safety cap: --max-records (default 600,000) prevents runaway pulls.
Use --resume to continue an interrupted pull from the last written record.

Usage:
    python runs/maricopa_az/pull_parcel_owner_index.py
    python runs/maricopa_az/pull_parcel_owner_index.py --max-records 200000
    python runs/maricopa_az/pull_parcel_owner_index.py --resume
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = REPO_ROOT / "data" / "cache" / "parcel_owner_index.jsonl"

SERVICE_URL = (
    "https://gis.mcassessor.maricopa.gov"
    "/arcgis/rest/services/Parcels/MapServer/0"
)
WHERE_CLAUSE = "PUC LIKE '0%'"   # 0xxx = residential/condo codes (1.6M of 1.76M total parcels)
OUT_FIELDS = (
    "APN,APN_DASH,OWNER_NAME,INCAREOF,"
    "PHYSICAL_ADDRESS,PHYSICAL_CITY,PHYSICAL_ZIP,"
    "MAIL_ADDR1,MAIL_CITY,MAIL_STATE,MAIL_ZIP,"
    "FCV_CUR,SALE_PRICE,SALE_DATE,CONST_YEAR,PUC"
)
PAGE_SIZE = 1000
DEFAULT_MAX_RECORDS = 600_000
DELAY_SECS = 0.5

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

_HEADERS = {
    "User-Agent": "xcerebro-maricopa-parcel-index/0.1",
    "Accept": "application/json",
    "Referer": "https://maps.mcassessor.maricopa.gov",
}


def _get(url: str, params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{qs}", headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=30, context=_CTX) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_count() -> int:
    resp = _get(f"{SERVICE_URL}/query", {
        "where": WHERE_CLAUSE,
        "returnCountOnly": "true",
        "f": "json",
    })
    if "error" in resp:
        raise RuntimeError(f"Count query failed: {resp['error']}")
    return resp.get("count", 0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--max-records", type=int, default=DEFAULT_MAX_RECORDS,
        help=f"Safety cap on total records pulled (default: {DEFAULT_MAX_RECORDS:,})",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from last completed offset (count existing JSONL lines)",
    )
    args = parser.parse_args()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    start_offset = 0
    if args.resume and OUTPUT_PATH.exists():
        with open(OUTPUT_PATH, encoding="utf-8") as fh:
            start_offset = sum(1 for ln in fh if ln.strip())
        print(f"Resume: skipping first {start_offset:,} records ({start_offset // PAGE_SIZE} pages already done)")

    print("=== Maricopa Residential Parcel Owner Index Pull ===")
    print(f"Filter:      {WHERE_CLAUSE}")
    print(f"Max records: {args.max_records:,}")
    print(f"Delay:       {DELAY_SECS}s between pages")
    print(f"Output:      {OUTPUT_PATH}")
    print()

    print("Fetching total residential parcel count...")
    try:
        total = _get_count()
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    cap = min(total, args.max_records)
    pages_expected = (cap - start_offset + PAGE_SIZE - 1) // PAGE_SIZE
    print(f"Residential parcels total: {total:,}")
    print(f"Will pull up to:           {cap:,}")
    print(f"Pages remaining:           {pages_expected:,}  (~{pages_expected * DELAY_SECS / 60:.0f} min at {DELAY_SECS}s/page)")
    print()

    mode = "a" if (args.resume and start_offset > 0) else "w"
    offset = start_offset
    written = start_offset
    page = start_offset // PAGE_SIZE

    with open(OUTPUT_PATH, mode, encoding="utf-8") as fh:
        while True:
            if written >= args.max_records:
                print(f"\nMax-records cap reached ({args.max_records:,}). Stopping.")
                break

            try:
                resp = _get(f"{SERVICE_URL}/query", {
                    "where": WHERE_CLAUSE,
                    "outFields": OUT_FIELDS,
                    "returnGeometry": "false",
                    "resultRecordCount": PAGE_SIZE,
                    "resultOffset": offset,
                    "f": "json",
                })
            except Exception as exc:
                print(f"\nERROR at offset {offset:,}: {exc}")
                print("Stopping. Re-run with --resume to continue from here.")
                break

            if "error" in resp:
                print(f"\nAPI ERROR: {resp['error']}")
                break

            features = resp.get("features", [])
            if not features:
                print(f"\nEmpty page at offset {offset:,} — pull complete.")
                break

            for feat in features:
                fh.write(json.dumps(feat["attributes"], ensure_ascii=False) + "\n")

            written += len(features)
            offset += len(features)
            page += 1

            if page % 10 == 0 or len(features) < PAGE_SIZE:
                pct = written / max(total, 1) * 100
                print(f"  page {page:4d}  offset {offset:7,}  written {written:7,}  ({pct:.1f}%)")

            if len(features) < PAGE_SIZE:
                print("Last page — pull complete.")
                break

            time.sleep(DELAY_SECS)

    print()
    print("=== Pull Summary ===")
    print(f"Records written: {written:,}")
    print(f"Output:          {OUTPUT_PATH}")
    print()
    print("Next: run enrich_probate_parcels_local.cmd")


if __name__ == "__main__":
    main()
