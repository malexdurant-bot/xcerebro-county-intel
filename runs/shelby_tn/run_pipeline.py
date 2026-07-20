"""
Shelby County, TN — distress lead pipeline.

Phase 1 (MVP) sources:
  trustee_tax_sale  — Tax sale property list (CSV from S3, CONFIRMED accessible)

Phase 2 sources (REQUIRES_PLAYWRIGHT — not yet active):
  register_shelby           — Register of Deeds (ASOT, Lis Pendens, etc.)
  chancery_court_shelby     — Chancery Court (Lis Pendens, partition, quiet title)
  general_sessions_shelby   — General Sessions Civil (evictions / FED)
  probate_court_shelby      — Probate Court (estates, letters testamentary)

Enrichment:
  parcel_master_shelby  — ArcGIS CurrentParcels (owner name, mailing address)

Usage:
    python runs/shelby_tn/run_pipeline.py
    python runs/shelby_tn/run_pipeline.py --max-records 50
    python runs/shelby_tn/run_pipeline.py --scrape      # download fresh CSV first

Output: runs/shelby_tn/pipeline_output/
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

from build_config import (  # noqa: E402
    COUNTY_ID, COUNTY_NAME, STATE,
    TAX_SALE_JSONL, REGISTER_JSONL, CHANCERY_JSONL,
    GENERAL_SESSIONS_JSONL, PROBATE_JSONL,
    PARCEL_CACHE_PATH, OUTPUT_DIR,
)
from parcel_resolver import ParcelResolver  # noqa: E402
from tax_sale_adapter import load_tax_sale_jsonl, build_tax_sale_raw_events  # noqa: E402
from register_adapter import load_register_jsonl, build_register_raw_events  # noqa: E402
from eviction_adapter import load_eviction_jsonl, build_eviction_raw_events  # noqa: E402
from chancery_adapter import load_chancery_jsonl, build_chancery_raw_events  # noqa: E402
from probate_adapter import load_probate_jsonl, build_probate_raw_events  # noqa: E402

from scaffold.pipeline import debtor_party_engine  # noqa: E402
from scaffold.pipeline.debtor_party_engine import UNIVERSAL_DEBTOR_PARTY_RULES  # noqa: E402
from scaffold.pipeline.run_pipeline_staged import (  # noqa: E402
    run_staged_pipeline,
    build_dashboard_payload,
)

# ---------------------------------------------------------------------------
# §17 debtor-party rules override for TN tax sale
#
# tax_sale_certificate: taxpayer (TP) is the debtor.
# TP is supplied by the ArcGIS parcel lookup (same as Maricopa treasurer).
# Records without an ArcGIS hit emit empty parties → §17 routes to
# REVIEW_REQUIRED "owner_not_on_document".
# ---------------------------------------------------------------------------

_DEBTOR_RULES: dict = {
    **UNIVERSAL_DEBTOR_PARTY_RULES,
    # Shelby Phase 2 — court case types not in universal rules
    "partition_action": {
        "expected_debtor_name_type": "DF",
        "fallback_debtor_name_type": "PL",
        "filer_name_types": ["PL"],
        "debtor_source": "STRUCTURED",
        "known_filer_role": "plaintiff co-owner seeking partition",
        "missing_debtor_review_reason": "owner_not_on_document",
    },
    "quiet_title_action": {
        "expected_debtor_name_type": "DF",
        "fallback_debtor_name_type": "PL",
        "filer_name_types": ["PL"],
        "debtor_source": "STRUCTURED",
        "known_filer_role": "plaintiff claiming clear title",
        "missing_debtor_review_reason": "owner_not_on_document",
    },
}


# ---------------------------------------------------------------------------
# Parcel dict builder (maps ArcGIS parcel fields → scoring seam shape)
# ---------------------------------------------------------------------------

def _make_enrichment_provider(parcel_by_id: dict[str, dict]):
    """Return an EnrichmentProvider callable: parcel_id -> parcel dict | None.

    The scoring seam (scoring_seam.EnrichmentProvider) expects a plain callable
    that takes the primary_parcel_id string and returns the raw parcel dict.
    The seam applies its own _parcel_display_from projection internally.
    """
    def enrichment_provider(parcel_id: Optional[str]) -> Optional[dict]:
        if not parcel_id:
            return None
        return parcel_by_id.get(str(parcel_id).strip())
    return enrichment_provider


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    max_records: Optional[int] = None,
    scrape: bool = False,
    verbose: bool = True,
    approve_needs_review: bool = True,
) -> dict:
    t0 = time.time()
    run_ts = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"Shelby County, TN — Pipeline  ({run_ts})")
    print(f"{'='*60}")

    # --- Optional: re-scrape CSV -------------------------------------------
    if scrape:
        print("\n[SCRAPE] Downloading fresh TaxSaleExtract.csv...")
        from scrapers.trustee_tax_sale_shelby import run_scraper  # noqa: E402
        result = run_scraper(TAX_SALE_JSONL, existing_path=TAX_SALE_JSONL if TAX_SALE_JSONL.exists() else None)
        print(f"  {result}")
    elif not TAX_SALE_JSONL.exists():
        print(f"\n[WARNING] {TAX_SALE_JSONL} not found — skipping tax sale source. Run with --scrape to download.")

    # --- Load tax sale records -------------------------------------------
    if TAX_SALE_JSONL.exists():
        print(f"\n[LOAD] Tax sale records from {TAX_SALE_JSONL.name}...")
        tax_sale_records = load_tax_sale_jsonl(TAX_SALE_JSONL, max_records=max_records)
        print(f"  Loaded {len(tax_sale_records)} active records")
    else:
        tax_sale_records = []

    # --- Parcel resolver ---------------------------------------------------
    print(f"\n[ENRICH] Initialising ArcGIS parcel resolver...")
    resolver = ParcelResolver(cache_path=PARCEL_CACHE_PATH)

    # --- Build raw events from all sources ---------------------------------
    all_raw_events: list[dict] = []

    if tax_sale_records:
        print(f"\n[ADAPT] Tax sale: {len(tax_sale_records)} records...")
        tax_raw_events, parcel_by_id = build_tax_sale_raw_events(
            tax_sale_records,
            resolver=resolver,
            verbose=verbose,
        )
        all_raw_events.extend(tax_raw_events)
        print(f"  Built {len(tax_raw_events)} raw events")
    else:
        parcel_by_id = {}

    # Phase 2 — Register of Deeds
    register_records = load_register_jsonl(REGISTER_JSONL, max_records=max_records)
    if register_records:
        print(f"\n[ADAPT] Register of Deeds: {len(register_records)} records...")
        reg_events = build_register_raw_events(register_records, verbose=verbose)
        all_raw_events.extend(reg_events)
        print(f"  Built {len(reg_events)} raw events")

    # Phase 2 — General Sessions Civil (evictions)
    eviction_records = load_eviction_jsonl(GENERAL_SESSIONS_JSONL, max_records=max_records)
    if eviction_records:
        print(f"\n[ADAPT] General Sessions Civil (evictions): {len(eviction_records)} records...")
        eviction_events = build_eviction_raw_events(eviction_records, verbose=verbose)
        all_raw_events.extend(eviction_events)
        print(f"  Built {len(eviction_events)} raw events")

    # Phase 2 — Chancery Court
    chancery_records = load_chancery_jsonl(CHANCERY_JSONL, max_records=max_records)
    if chancery_records:
        print(f"\n[ADAPT] Chancery Court: {len(chancery_records)} records...")
        chancery_events = build_chancery_raw_events(chancery_records, verbose=verbose)
        all_raw_events.extend(chancery_events)
        print(f"  Built {len(chancery_events)} raw events")

    # Phase 2 — Probate Court
    probate_records = load_probate_jsonl(PROBATE_JSONL, max_records=max_records)
    if probate_records:
        print(f"\n[ADAPT] Probate Court: {len(probate_records)} records...")
        probate_events = build_probate_raw_events(probate_records, verbose=verbose)
        all_raw_events.extend(probate_events)
        print(f"  Built {len(probate_events)} raw events")

    if not all_raw_events:
        print("  No raw events produced — check scraper output.")
        return {"lead_count": 0, "elapsed_seconds": round(time.time() - t0, 1)}

    # Save parcel cache
    resolver.save_cache()
    resolver.print_stats()

    # --- Build enrichment provider (callable: parcel_id -> parcel dict) ----
    enrichment_provider = _make_enrichment_provider(parcel_by_id)

    # --- Staged pipeline §17 → §18 → §19 → §20 → scoring ----------------
    print(f"\n[PIPELINE] Running staged pipeline on {len(all_raw_events)} raw events...")
    result = run_staged_pipeline(
        all_raw_events,
        workdir=OUTPUT_DIR,
        as_of=datetime.now(timezone.utc).date(),
        enrichment_provider=enrichment_provider,
        approve_needs_review=approve_needs_review,
        debtor_party_rules=_DEBTOR_RULES,
    )

    scored_leads = result["scored_leads"]
    print(f"\n[RESULT] {len(scored_leads)} scored leads written to {result['scored_leads_path']}")
    print(f"  Semantic verdict: {result['semantic_verdict']}")

    # --- Build dashboard payload ------------------------------------------
    dashboard = build_dashboard_payload(
        scored_leads,
        semantic_verdict=result["semantic_verdict"],
        county=COUNTY_NAME,
        state=STATE,
        mode="production",
        build_label="PARTIAL_BUILD",
    )
    dashboard_path = OUTPUT_DIR / "dashboard.json"
    dashboard_path.write_text(
        json.dumps(dashboard, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"  Dashboard payload: {dashboard_path}")

    elapsed = round(time.time() - t0, 1)
    print(f"\n[DONE] Shelby County pipeline complete in {elapsed}s")
    print(f"  Leads: {len(scored_leads)}  |  Verdict: {result['semantic_verdict']}")

    return {
        "lead_count": len(scored_leads),
        "semantic_verdict": result["semantic_verdict"],
        "elapsed_seconds": elapsed,
        "scored_leads_path": str(result["scored_leads_path"]),
        "dashboard_path": str(dashboard_path),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Shelby County, TN — distress lead pipeline")
    parser.add_argument("--max-records", type=int, default=None,
                        help="Cap records per source (for bounded tests)")
    parser.add_argument("--scrape", action="store_true",
                        help="Re-download CSV from source before running pipeline")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress per-record verbose output")
    parser.add_argument("--no-approve-review", action="store_true",
                        help="Do not auto-approve REVIEW_REQUIRED leads")
    args = parser.parse_args()

    run_pipeline(
        max_records=args.max_records,
        scrape=args.scrape,
        verbose=not args.quiet,
        approve_needs_review=not args.no_approve_review,
    )
