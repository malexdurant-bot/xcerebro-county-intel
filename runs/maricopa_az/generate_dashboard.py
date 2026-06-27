"""
Maricopa County, AZ — dashboard payload generator.

Reads runs/maricopa_az/pipeline_output/scored_leads.json and writes
dashboard/data/leads.json using the framework's build_dashboard_payload.

The existing dashboard/index.html + dashboard.js will load
dashboard/data/leads.json automatically when served via HTTP.

Usage:
    python runs/maricopa_az/generate_dashboard.py

Then open the dashboard:
    cd dashboard && python -m http.server 8765
    Open: http://localhost:8765/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scaffold.pipeline.run_pipeline_staged import build_dashboard_payload

SCORED_LEADS_PATH = REPO_ROOT / "runs" / "maricopa_az" / "pipeline_output" / "scored_leads.json"
OUT_DIR = REPO_ROOT / "dashboard" / "data"
OUT_PATH = OUT_DIR / "leads.json"


def main() -> None:
    if not SCORED_LEADS_PATH.exists():
        print(f"ERROR: {SCORED_LEADS_PATH} not found. Run run_pipeline.py first.")
        sys.exit(1)

    scored_leads: list[dict] = json.loads(SCORED_LEADS_PATH.read_text(encoding="utf-8"))
    print(f"Loaded {len(scored_leads)} scored leads from {SCORED_LEADS_PATH.name}")

    payload = build_dashboard_payload(
        scored_leads,
        semantic_verdict="DEPLOY_OK",
        county="Maricopa County",
        state="AZ",
        mode="live",
        build_label="FULL_BUILD",
    )

    # Add review_status_counts (not in build_dashboard_payload — computed here)
    review_counts: dict[str, int] = {}
    for s in scored_leads:
        status = s.get("lead_status") or "UNKNOWN"
        review_counts[status] = review_counts.get(status, 0) + 1
    payload["review_status_counts"] = dict(sorted(review_counts.items()))

    # Add enrichment_breakdown explicitly (already in payload from build_dashboard_payload)
    # Add source metadata (combined: NOTS + Treasurer + Eviction)
    payload["source_id"] = (
        "recorder_maricopa+treasurer_tax_lien"
        "+justice_court_evictions+superior_court_civil+superior_court_probate"
    )
    payload["source_label"] = (
        "Maricopa County — Recorder NOTS + Treasurer Tax Lien"
        " + Justice Court Evictions + Superior Court Civil + Superior Court Probate"
    )
    payload["matched_leads_count"] = len(scored_leads)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Dashboard payload written to: {OUT_PATH}")
    print()
    print("=== Summary ===")
    print(f"  lead_total:          {payload['lead_total']}")
    print(f"  semantic_verdict:    {payload['semantic_verdict']}")
    print(f"  generated_at:        {payload['generated_at']}")
    print(f"  enrichment_breakdown: {payload['enrichment_breakdown']}")
    print(f"  score_tier_distribution: {payload['score_tier_distribution']}")
    print(f"  pattern_counts:      {payload['pattern_counts']}")
    print(f"  review_status_counts: {payload['review_status_counts']}")
    print(f"  attribute_counts:    {payload['attribute_counts']}")
    print()
    print("=== To open the dashboard ===")
    print(f"  cd {REPO_ROOT / 'dashboard'}")
    print(f"  python -m http.server 8765")
    print(f"  Open: http://localhost:8765/")


if __name__ == "__main__":
    main()
