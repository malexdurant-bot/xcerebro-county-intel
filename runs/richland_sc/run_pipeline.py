"""
Richland County, SC — daily pipeline runner.

Sources scraped in this build (PARTIAL_BUILD — Register of Deeds blocked):
  - Columbia Star Master's Sales       → notice_of_sale  (foreclosure)
  - Columbia Star Public Notices       → lis_pendens     (circuit civil)
  - Columbia Star Notice to Creditors  → letters_testamentary (probate)

Usage:
  python runs/richland_sc/run_pipeline.py               # incremental (default)
  python runs/richland_sc/run_pipeline.py --full        # full rebuild
  python runs/richland_sc/run_pipeline.py --dry-run     # scrape only, skip pipeline
"""

from __future__ import annotations

import argparse
import json
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

def _load_all_raw_events(raw_dir: Path) -> list[dict]:
    """Load all raw event records from every JSONL file in raw_dir."""
    events: list[dict] = []
    for path in sorted(raw_dir.glob("columbia_star_*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
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
    # Step 1 — Scrape Columbia Star
    # ------------------------------------------------------------------
    from scrapers.columbia_star_richland import scrape, RAW_DIR  # noqa: PLC0415

    new_records = scrape(incremental=incremental)
    print(f"[richland_sc] Scraper returned {len(new_records)} new records")

    if args.dry_run:
        print("[richland_sc] --dry-run: stopping before pipeline. Done.")
        return

    # ------------------------------------------------------------------
    # Step 2 — Select raw events for this run
    # ------------------------------------------------------------------
    if args.full:
        raw_events = _load_all_raw_events(RAW_DIR)
        print(
            f"[richland_sc] Full rebuild: loaded {len(raw_events)} raw events "
            f"from {RAW_DIR}"
        )
    else:
        raw_events = new_records
        print(f"[richland_sc] Incremental: processing {len(raw_events)} records from this scrape")

    if not raw_events:
        print("[richland_sc] No raw events — pipeline up to date. Exiting.")
        return

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
    print(
        f"[richland_sc] Pipeline complete — "
        f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    )


if __name__ == "__main__":
    main()
