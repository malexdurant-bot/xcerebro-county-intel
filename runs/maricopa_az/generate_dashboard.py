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
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_THIS_DIR = Path(__file__).parent
for _p in (str(REPO_ROOT), str(_THIS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scaffold.pipeline.run_pipeline_staged import build_dashboard_payload

SCORED_LEADS_PATH = REPO_ROOT / "runs" / "maricopa_az" / "pipeline_output" / "scored_leads.json"
LEAD_HISTORY_PATH = REPO_ROOT / "data" / "state" / "maricopa_lead_history.sqlite"
OUT_DIR = REPO_ROOT / "dashboard" / "data"
OUT_PATH = OUT_DIR / "leads.json"


def _load_history_by_id() -> dict[str, dict]:
    """Load history rows keyed by lead_id, if the history DB exists."""
    if not LEAD_HISTORY_PATH.exists():
        return {}
    try:
        from lead_history import LeadHistory
        with LeadHistory(LEAD_HISTORY_PATH) as h:
            return h.get_history_by_id()
    except Exception as exc:
        print(f"  NOTE: could not load lead history ({exc}) — temporal fields omitted")
        return {}


def _enrich_temporal(record: dict, history_by_id: dict[str, dict], today: str) -> dict:
    """Add temporal dashboard fields from lead history."""
    lead_id = record.get("lead_id") or ""
    row = history_by_id.get(lead_id)
    if not row:
        return record

    first_seen = row.get("first_seen_date") or today
    last_seen = row.get("last_seen_date") or today
    score_delta = row.get("score_delta") or 0

    try:
        days_since = (date.fromisoformat(today) - date.fromisoformat(first_seen)).days
    except (ValueError, TypeError):
        days_since = 0

    return {
        **record,
        "first_seen_date": first_seen,
        "last_seen_date": last_seen,
        "is_new_today": first_seen == today,
        "is_new_last_24h": first_seen >= today,
        "days_since_first_seen": days_since,
        "updated_today": last_seen == today and first_seen != today,
        "score_delta": score_delta,
    }


def main() -> None:
    if not SCORED_LEADS_PATH.exists():
        print(f"ERROR: {SCORED_LEADS_PATH} not found. Run run_pipeline.py first.")
        sys.exit(1)

    scored_leads: list[dict] = json.loads(SCORED_LEADS_PATH.read_text(encoding="utf-8"))
    print(f"Loaded {len(scored_leads)} scored leads from {SCORED_LEADS_PATH.name}")

    today = date.today().isoformat()
    history_by_id = _load_history_by_id()
    if history_by_id:
        print(f"  Loaded history for {len(history_by_id):,} leads → adding temporal fields")

    payload = build_dashboard_payload(
        scored_leads,
        semantic_verdict="DEPLOY_OK",
        county="Maricopa County",
        state="AZ",
        mode="live",
        build_label="FULL_BUILD",
    )

    if history_by_id:
        payload["records"] = [
            _enrich_temporal(r, history_by_id, today)
            for r in (payload.get("records") or [])
        ]

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
