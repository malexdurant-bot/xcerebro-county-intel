"""
Richland County, SC — daily pipeline runner.

Sources scraped in this build (PARTIAL_BUILD — Register of Deeds blocked):
  - Columbia Star Master's Sales       → notice_of_sale  (foreclosure)
  - Columbia Star Public Notices       → lis_pendens     (circuit civil)
  - Columbia Star Notice to Creditors  → letters_testamentary (probate)
  - Richland delinquent tax parcel API → tax_foreclosure_notice (tax distress)

Enrichment-only (does not add raw events, fills in fields on existing ones):
  - Richland Probate Estate Inquiry    → confirms case number / dates on
                                          Columbia Star probate leads by
                                          decedent-name lookup (the portal
                                          has no bulk or date-range search,
                                          so it cannot run as a primary feed)

Not built — see runs/richland_sc/richland_pipeline.log and
config/counties/richland_sc.json `blocker` fields for why:
  - SC Courts Public Index (publicindex.sccourts.org) — site disclaimer
    expressly prohibits automated scraping; Columbia Star Public Notices
    substitutes for the lis_pendens signal instead.
  - Master-in-Equity foreclosure sales page (richlandcountysc.gov) — the
    whole domain is Akamai-WAF-blocked from this environment (no residential
    proxy configured); Columbia Star Master's Sales substitutes instead.

Usage:
  python runs/richland_sc/run_pipeline.py               # incremental (default)
  python runs/richland_sc/run_pipeline.py --full        # full rebuild
  python runs/richland_sc/run_pipeline.py --dry-run     # scrape only, skip pipeline
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

WORKDIR = ROOT / "pipeline_output" / "richland_sc"
WORKDIR.mkdir(parents=True, exist_ok=True)

SIGNAL_TYPE_LABELS: dict[str, str] = {
    "notice_of_sale": "Foreclosure Sale",
    "lis_pendens": "Lis Pendens",
    "letters_testamentary": "Notice to Creditors",
    "tax_foreclosure_notice": "Delinquent Tax",
}


def _build_debtor_party_rules() -> dict:
    """
    SC judicial foreclosure state override.

    The universal `notice_of_sale` rule fans out to `foreclosure_notice`'s
    DOCUMENT_BODY extraction, which looks for 'MORTGAGOR:', 'GRANTOR:', etc.
    Columbia Star Master's Sales notices use the SC court format:
      'in the case of PLAINTIFF vs. DEFENDANT'
    The defendant IS a structured DF party in our raw events, so we override
    to STRUCTURED extraction for this county.
    """
    from scaffold.pipeline.debtor_party_engine import UNIVERSAL_DEBTOR_PARTY_RULES
    return {
        **UNIVERSAL_DEBTOR_PARTY_RULES,
        "notice_of_sale": {
            "expected_debtor_name_type": "DF",
            "fallback_debtor_name_type": None,
            "filer_name_types": ["PL"],
            "debtor_source": "STRUCTURED",
            "known_filer_role": "lender/plaintiff (SC judicial foreclosure)",
        },
    }


# ---------------------------------------------------------------------------
# Raw-event loader (full-rebuild path)
# ---------------------------------------------------------------------------

def _load_jsonl_glob(dir_path: Path, pattern: str) -> list[dict]:
    events: list[dict] = []
    for path in sorted(dir_path.glob(pattern)):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    return events


def _load_all_raw_events(columbia_star_dir: Path, delinquent_tax_dir: Path) -> list[dict]:
    """Load all raw event records from every source's JSONL files."""
    events = _load_jsonl_glob(columbia_star_dir, "columbia_star_*.jsonl")
    events += _load_jsonl_glob(delinquent_tax_dir, "delinquent_tax_*.jsonl")
    return events


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Richland County SC daily pipeline"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Full rebuild: reset scraper cursor and load all historical raw files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the scraper only; skip pipeline stages",
    )
    parser.add_argument(
        "--no-approve-review",
        action="store_true",
        default=False,
        help="Halt on NEEDS_OPERATOR_REVIEW instead of auto-approving",
    )
    args = parser.parse_args()

    incremental = not args.full
    approve_review = not args.no_approve_review

    ts_start = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[richland_sc] Pipeline starting — {ts_start}")
    print(f"[richland_sc] Mode: {'full rebuild' if args.full else 'incremental'}")

    # ------------------------------------------------------------------
    # Step 1 — Scrape Columbia Star + Richland delinquent tax
    # ------------------------------------------------------------------
    from scrapers.columbia_star_richland import scrape as scrape_columbia_star, RAW_DIR as CS_RAW_DIR  # noqa: PLC0415
    from scrapers.richland_delinquent_tax import scrape as scrape_delinquent_tax, RAW_DIR as DT_RAW_DIR  # noqa: PLC0415

    cs_records = scrape_columbia_star(incremental=incremental)
    print(f"[richland_sc] Columbia Star: {len(cs_records)} new records")

    dt_records = scrape_delinquent_tax(incremental=incremental)
    print(f"[richland_sc] Delinquent tax: {len(dt_records)} new records")

    new_records = cs_records + dt_records
    print(f"[richland_sc] Scrapers returned {len(new_records)} new records total")

    if args.dry_run:
        print("[richland_sc] --dry-run: stopping before pipeline. Done.")
        return

    # ------------------------------------------------------------------
    # Step 2 — Select raw events for this run
    # ------------------------------------------------------------------
    if args.full:
        raw_events = _load_all_raw_events(CS_RAW_DIR, DT_RAW_DIR)
        print(
            f"[richland_sc] Full rebuild: loaded {len(raw_events)} raw events "
            f"from {CS_RAW_DIR} and {DT_RAW_DIR}"
        )
    else:
        raw_events = new_records
        print(f"[richland_sc] Incremental: processing {len(raw_events)} records from this scrape")

    if not raw_events:
        print("[richland_sc] No raw events — pipeline up to date. Exiting.")
        return

    # ------------------------------------------------------------------
    # Step 2b — Confirm estate leads against the official Estate Inquiry
    # portal (case number + estate-opened date), then DealMachine skip-trace
    # for estate leads still missing a parcel ID.
    # ------------------------------------------------------------------
    from scrapers.richland_probate_estate_inquiry import (  # noqa: PLC0415
        enrich_estate_raw_events as confirm_estate_raw_events,
    )
    from scrapers.richland_skiptrace_dealmachine import enrich_estate_raw_events  # noqa: PLC0415

    raw_events = confirm_estate_raw_events(raw_events)

    estate_unenriched = sum(
        1 for e in raw_events
        if e.get("canonical_doc_type") == "letters_testamentary"
        and not (e.get("property_refs") or {}).get("parcel_id")
    )
    raw_events = enrich_estate_raw_events(raw_events)
    estate_newly_enriched = sum(
        1 for e in raw_events
        if e.get("canonical_doc_type") == "letters_testamentary"
        and (e.get("property_refs") or {}).get("_enriched_via") == "dealmachine_skiptrace"
    )
    dm_key_status = "set" if os.environ.get("DEALMACHINE_API_KEY") else "not set"
    print(
        f"[richland_sc] DealMachine skip-trace: "
        f"{estate_newly_enriched}/{estate_unenriched} estate events enriched "
        f"(DEALMACHINE_API_KEY {dm_key_status})"
    )

    # ------------------------------------------------------------------
    # Step 3 — Staged pipeline
    # ------------------------------------------------------------------
    from scaffold.pipeline.run_pipeline_staged import (  # noqa: PLC0415
        build_dashboard_payload,
        run_staged_pipeline,
    )
    from scaffold.pipeline.scoring_seam import (  # noqa: PLC0415
        SemanticGateBlocked,
        SemanticGateNeedsReview,
    )
    from scrapers.richland_assessor_spatialest import make_enrichment_provider  # noqa: PLC0415

    enrichment_provider = make_enrichment_provider()
    print("[richland_sc] Spatialest enrichment provider ready")

    print(f"[richland_sc] Running staged pipeline on {len(raw_events)} raw events…")

    try:
        result = run_staged_pipeline(
            raw_events,
            signal_type_labels=SIGNAL_TYPE_LABELS,
            workdir=WORKDIR,
            as_of=date.today(),
            approve_needs_review=approve_review,
            debtor_party_rules=_build_debtor_party_rules(),
            enrichment_provider=enrichment_provider,
        )
    except SemanticGateBlocked as exc:
        print(f"[richland_sc] PIPELINE BLOCKED — §20 semantic gate: {exc}")
        print("[richland_sc] Check pipeline_output/richland_sc/matched_leads.json for details.")
        sys.exit(1)
    except SemanticGateNeedsReview as exc:
        print(f"[richland_sc] §20 gate NEEDS_OPERATOR_REVIEW: {exc}")
        print("[richland_sc] Re-run without --no-approve-review to auto-approve and continue.")
        sys.exit(2)

    scored_leads = result["scored_leads"]
    semantic_verdict = result["semantic_verdict"]

    print(f"[richland_sc] §20 verdict:    {semantic_verdict}")
    print(f"[richland_sc] Scored leads:   {len(scored_leads)}")

    # ------------------------------------------------------------------
    # Step 4 — Dashboard payload
    # ------------------------------------------------------------------
    payload = build_dashboard_payload(
        scored_leads,
        semantic_verdict=semantic_verdict,
        county="Richland",
        state="SC",
        mode="incremental" if incremental else "full_rebuild",
        build_label="PARTIAL_BUILD",
    )

    payload_json = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    dash_path = WORKDIR / "data.json"
    dash_path.write_text(payload_json, encoding="utf-8")

    # Also write to the static dashboard's data directory so opening
    # dashboard/index.html shows live Richland SC leads immediately.
    static_dash_path = ROOT / "dashboard" / "data" / "leads.json"
    static_dash_path.parent.mkdir(parents=True, exist_ok=True)
    static_dash_path.write_text(payload_json, encoding="utf-8")

    print(f"[richland_sc] Dashboard written  → {dash_path}")
    print(f"[richland_sc] Static dashboard   → {static_dash_path}")
    print(f"[richland_sc] Lead total:          {payload['lead_total']}")
    print(f"[richland_sc] Score tiers:         {payload['score_tier_distribution']}")
    print(f"[richland_sc] Patterns:            {payload['pattern_counts']}")

    # ------------------------------------------------------------------
    # Step 5 — Publish to GitHub Pages (malexdurant-bot fork)
    # ------------------------------------------------------------------
    import subprocess  # noqa: PLC0415
    try:
        subprocess.run(
            ["git", "add", str(static_dash_path)],
            cwd=str(ROOT), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m",
             f"data(richland_sc): dashboard update {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"],
            cwd=str(ROOT), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "push", "pages", "main"],
            cwd=str(ROOT), check=True, capture_output=True, timeout=60,
        )
        print("[richland_sc] GitHub Pages updated → https://malexdurant-bot.github.io/xcerebro-county-intel/dashboard/")
    except subprocess.CalledProcessError as exc:
        # Non-fatal — pipeline succeeded even if publish fails
        stderr = exc.stderr.decode(errors="replace") if exc.stderr else ""
        if "nothing to commit" in stderr or "nothing to commit" in (exc.stdout or b"").decode(errors="replace"):
            print("[richland_sc] GitHub Pages: no changes to publish")
        else:
            print(f"[richland_sc] GitHub Pages publish failed (non-fatal): {stderr[:200]}")

    print(
        f"[richland_sc] Pipeline complete — "
        f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    )


if __name__ == "__main__":
    main()
