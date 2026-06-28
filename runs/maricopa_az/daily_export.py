"""Daily CSV export for Maricopa incremental pipeline.

Writes three daily files and latest/ copies:
  data/exports/maricopa_az/daily/new_leads_YYYY-MM-DD.csv
  data/exports/maricopa_az/daily/skiptrace_ready_YYYY-MM-DD.csv
  data/exports/maricopa_az/daily/review_required_YYYY-MM-DD.csv
  data/exports/maricopa_az/latest/new_leads.csv
  data/exports/maricopa_az/latest/skiptrace_ready.csv
  data/exports/maricopa_az/latest/review_required.csv

Columns include temporal fields from lead_history:
  first_seen_date, last_seen_date, is_new_today, days_since_first_seen, score_delta
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPORTS_BASE = REPO_ROOT / "data" / "exports" / "maricopa_az"

_COLUMNS = [
    "lead_id",
    "score",
    "score_tier",
    "review_status",
    "source_patterns",
    "motivation_tags",
    "apn",
    "property_address",
    "mailing_address",
    "owner_name",
    "source_count",
    "enrichment_status",
    "first_seen_date",
    "last_seen_date",
    "is_new_today",
    "days_since_first_seen",
    "score_delta",
    "generated_at",
]


def _fmt_address(*parts: Optional[str]) -> str:
    return ", ".join(p for p in parts if p and str(p).strip())


def _flat_row(
    lead: dict,
    history_row: Optional[dict],
    run_date: str,
    generated_at: str,
) -> dict:
    pd_ = lead.get("parcel_display") or {}

    first_seen = (history_row or {}).get("first_seen_date") or run_date
    last_seen = (history_row or {}).get("last_seen_date") or run_date
    score_delta = (history_row or {}).get("score_delta") or 0

    try:
        days_since = (date.fromisoformat(run_date) - date.fromisoformat(first_seen)).days
    except (ValueError, TypeError):
        days_since = 0

    source_ids = lead.get("source_ids") or []
    return {
        "lead_id": lead.get("lead_id") or "",
        "score": lead.get("score") or 0,
        "score_tier": lead.get("tier") or "",
        "review_status": lead.get("lead_status") or "",
        "source_patterns": "|".join(lead.get("patterns") or []),
        "motivation_tags": "|".join(lead.get("attributes") or []),
        "apn": lead.get("primary_parcel_id") or "",
        "property_address": _fmt_address(
            pd_.get("situs_address"),
            pd_.get("situs_city"),
            pd_.get("situs_state"),
        ),
        "mailing_address": _fmt_address(
            pd_.get("owner_mailing_address"),
            pd_.get("owner_mailing_city"),
            pd_.get("owner_mailing_state"),
            pd_.get("owner_mailing_zip"),
        ),
        "owner_name": lead.get("owner_name") or "",
        "source_count": lead.get("stack_depth") or len(source_ids),
        "enrichment_status": lead.get("enrichment_status") or "",
        "first_seen_date": first_seen,
        "last_seen_date": last_seen,
        "is_new_today": "1" if first_seen == run_date else "0",
        "days_since_first_seen": days_since,
        "score_delta": score_delta,
        "generated_at": generated_at,
    }


def _write_csv(path: Path, rows: list[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _is_skiptrace_ready(lead: dict) -> bool:
    return (
        lead.get("enrichment_status") == "ENRICHED"
        and lead.get("lead_status") != "REVIEW_REQUIRED"
        and bool(lead.get("primary_parcel_id"))
    )


def _is_review_required(lead: dict) -> bool:
    return (
        lead.get("lead_status") == "REVIEW_REQUIRED"
        or lead.get("enrichment_status") == "REVIEW_REQUIRED"
    )


def export_daily_csvs(
    new_leads: list[dict],
    history_by_id: dict[str, dict],
    run_date: str,
    out_dir: Path = EXPORTS_BASE,
) -> dict[str, int]:
    """Write three daily CSVs and their latest/ copies.

    Args:
        new_leads: Scored leads from today's incremental run.
        history_by_id: Dict of lead_id -> history row (from LeadHistory.get_history_by_id()).
        run_date: YYYY-MM-DD string for today.
        out_dir: Base exports directory (default: data/exports/maricopa_az/).

    Returns:
        Dict of {filename: row_count} for all three categories.
    """
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    daily_dir = out_dir / "daily"
    latest_dir = out_dir / "latest"

    rows = [
        _flat_row(lead, history_by_id.get(lead.get("lead_id") or ""), run_date, generated_at)
        for lead in new_leads
    ]
    lead_rows = list(zip(new_leads, rows))

    all_rows = [row for _, row in lead_rows]
    skiptrace_rows = [row for lead, row in lead_rows if _is_skiptrace_ready(lead)]
    review_rows = [row for lead, row in lead_rows if _is_review_required(lead)]

    counts: dict[str, int] = {}
    for label, data in [
        ("new_leads", all_rows),
        ("skiptrace_ready", skiptrace_rows),
        ("review_required", review_rows),
    ]:
        dated_path = daily_dir / f"{label}_{run_date}.csv"
        latest_path = latest_dir / f"{label}.csv"
        n = _write_csv(dated_path, data)
        _write_csv(latest_path, data)
        counts[f"{label}.csv"] = n

    return counts
