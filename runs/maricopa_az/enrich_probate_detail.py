"""
Bounded probate detail enrichment runner.

Reads runs from data/raw/superior_court_probate.jsonl, fetches detail pages
for up to --max-records records, and writes enriched data to
data/raw/superior_court_probate_detail.jsonl.

Reports only aggregate counts — no names, case numbers, or URLs in output.

Usage:
    python runs/maricopa_az/enrich_probate_detail.py --max-records 18
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scrapers.superior_court_probate_detail_maricopa import (
    run as run_detail_enrichment,
    BASE_JSONL,
    DETAIL_JSONL,
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-records", type=int, default=18,
                    help="Max base records to enrich (default: 18)")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="Seconds between requests (default: 1.0)")
    args = ap.parse_args()

    if not BASE_JSONL.exists():
        print(f"ERROR: {BASE_JSONL} not found. Run fetch_probate_sample.cmd first.")
        sys.exit(1)

    print(f"=== Probate Detail Enrichment (max_records={args.max_records}) ===")
    print(f"Input:  {BASE_JSONL.name}")
    print(f"Output: {DETAIL_JSONL.name}")
    print(f"Delay:  {args.delay}s between requests")
    print()

    stats = run_detail_enrichment(
        base_jsonl=BASE_JSONL,
        output_path=DETAIL_JSONL,
        max_records=args.max_records,
        delay_seconds=args.delay,
    )

    print()
    print("=== Field Availability Report (aggregate only) ===")
    n = stats["records_attempted"]
    def pct(k: str) -> str:
        v = stats.get(k, 0)
        return f"{v}/{n}  ({100 * v // n if n else 0}%)"

    print(f"  records_attempted:      {n}")
    print(f"  detail_pages_loaded:    {stats['detail_pages_loaded']}")
    print(f"  fetch_errors:           {stats['fetch_errors']}")
    print()
    print(f"  filing_date_found:      {pct('filing_date_found')}")
    print(f"  case_type_found:        {pct('case_type_found')}")
    print(f"  decedent_confirmed:     {pct('decedent_confirmed')}")
    print(f"  petitioner_found:       {pct('petitioner_found')}")
    print(f"  pr_executor_found:      {pct('pr_found')}")
    print(f"  property_address_found: {pct('property_address_found')}")
    print(f"  apn_found:              {pct('apn_found')}")
    print()
    print(f"  estate_cases:           {stats['estate_cases']}")
    print(f"  noise_cases (guardian/conserv): {stats['noise_cases']}")
    print(f"  review_required:        {stats['review_required']}")
    print()
    if stats["errors"]:
        print(f"  errors ({len(stats['errors'])}):")
        for e in stats["errors"][:5]:
            print(f"    {e}")
    print(f"  output: {stats.get('output_path', DETAIL_JSONL)}")
    print()
    print("Next: report results to operator before running combined pipeline.")


if __name__ == "__main__":
    main()
