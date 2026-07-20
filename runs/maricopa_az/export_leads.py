"""
Export filtered CSV leads from pipeline output.

Reads: runs/maricopa_az/pipeline_output/scored_leads.json
Writes (6 CSVs) to: data/exports/maricopa_az/

    hot_leads.csv                  score >= 80, ENRICHED, not REVIEW_REQUIRED
    strong_leads.csv               65 <= score < 80, ENRICHED, not REVIEW_REQUIRED
    probate_confirmed_property.csv probate source + confirmed APN + ENRICHED
    tax_foreclosure_overlap.csv    treasurer_tax_lien + recorder_maricopa both present
    review_required.csv            REVIEW_REQUIRED lead_status or enrichment_status
    skiptrace_ready.csv            ENRICHED, not REVIEW_REQUIRED, has APN, deduped by APN

Columns:
    lead_id, score, score_tier, review_status, source_patterns, motivation_tags,
    apn, property_address, mailing_address, owner_name, source_count,
    enrichment_status, generated_at

Usage:
    python -X utf8 runs/maricopa_az/export_leads.py
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
THIS_DIR = Path(__file__).parent

SCORED_LEADS_PATH = THIS_DIR / "pipeline_output" / "scored_leads.json"
EXPORTS_DIR = REPO_ROOT / "data" / "exports" / "maricopa_az"

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
    "generated_at",
]

# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------


def _fmt_address(*parts: str | None) -> str:
    return ", ".join(p for p in parts if p and str(p).strip())


def _flat_row(lead: dict, generated_at: str) -> dict:
    pd_ = lead.get("parcel_display") or {}
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
        "generated_at": generated_at,
    }


# ---------------------------------------------------------------------------
# Filter predicates
# ---------------------------------------------------------------------------


def _is_enriched_approved(lead: dict) -> bool:
    return (
        lead.get("enrichment_status") == "ENRICHED"
        and lead.get("lead_status") != "REVIEW_REQUIRED"
    )


def is_hot(lead: dict) -> bool:
    """score >= 80, ENRICHED, not REVIEW_REQUIRED."""
    return (lead.get("score") or 0) >= 80 and _is_enriched_approved(lead)


def is_strong(lead: dict) -> bool:
    """65 <= score < 80, ENRICHED, not REVIEW_REQUIRED."""
    score = lead.get("score") or 0
    return 65 <= score < 80 and _is_enriched_approved(lead)


def is_probate_confirmed_property(lead: dict) -> bool:
    """Probate source contributed + confirmed APN present + ENRICHED.

    Only CONFIRMED_DECEDENT_OWNER_MATCH ever sets primary_parcel_id for probate;
    POSSIBLE and AMBIGUOUS matches leave it None (enforced in probate_adapter.py).
    """
    return (
        "superior_court_probate" in (lead.get("source_ids") or [])
        and bool(lead.get("primary_parcel_id"))
        and lead.get("enrichment_status") == "ENRICHED"
    )


def is_tax_foreclosure_overlap(lead: dict) -> bool:
    """Both treasurer_tax_lien and recorder_maricopa signals present."""
    src = set(lead.get("source_ids") or [])
    return "treasurer_tax_lien" in src and "recorder_maricopa" in src


def is_review_required(lead: dict) -> bool:
    """REVIEW_REQUIRED in lead_status or enrichment_status."""
    return (
        lead.get("lead_status") == "REVIEW_REQUIRED"
        or lead.get("enrichment_status") == "REVIEW_REQUIRED"
    )


def is_skiptrace_ready(lead: dict) -> bool:
    """ENRICHED, not REVIEW_REQUIRED, has APN for deduplication."""
    return (
        lead.get("enrichment_status") == "ENRICHED"
        and lead.get("lead_status") != "REVIEW_REQUIRED"
        and bool(lead.get("primary_parcel_id"))
    )


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def _dedup_by_apn(rows: list[dict]) -> list[dict]:
    """Keep highest-scoring flat row per APN; sort descending by score."""
    best: dict[str, dict] = {}
    for row in rows:
        apn = row.get("apn") or ""
        if not apn:
            continue
        if apn not in best or (row.get("score") or 0) > (best[apn].get("score") or 0):
            best[apn] = row
    return sorted(best.values(), key=lambda r: r.get("score") or 0, reverse=True)


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------


def _write_csv(path: Path, rows: list[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


# ---------------------------------------------------------------------------
# Main export
# ---------------------------------------------------------------------------


def export_all(
    leads: list[dict],
    out_dir: Path,
    generated_at: str | None = None,
) -> dict[str, int]:
    """Run all six exports. Returns {filename: row_count}."""
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    rows = [_flat_row(lead, generated_at) for lead in leads]
    lead_rows = list(zip(leads, rows))

    hot = [row for lead, row in lead_rows if is_hot(lead)]
    strong = [row for lead, row in lead_rows if is_strong(lead)]
    probate_prop = [row for lead, row in lead_rows if is_probate_confirmed_property(lead)]
    tax_fc = [row for lead, row in lead_rows if is_tax_foreclosure_overlap(lead)]
    review = [row for lead, row in lead_rows if is_review_required(lead)]
    skiptrace_rows = [row for lead, row in lead_rows if is_skiptrace_ready(lead)]
    skiptrace = _dedup_by_apn(skiptrace_rows)

    counts = {
        "hot_leads.csv": _write_csv(out_dir / "hot_leads.csv", hot),
        "strong_leads.csv": _write_csv(out_dir / "strong_leads.csv", strong),
        "probate_confirmed_property.csv": _write_csv(
            out_dir / "probate_confirmed_property.csv", probate_prop
        ),
        "tax_foreclosure_overlap.csv": _write_csv(
            out_dir / "tax_foreclosure_overlap.csv", tax_fc
        ),
        "review_required.csv": _write_csv(out_dir / "review_required.csv", review),
        "skiptrace_ready.csv": _write_csv(out_dir / "skiptrace_ready.csv", skiptrace),
    }
    return counts


def main() -> None:
    if not SCORED_LEADS_PATH.exists():
        print(f"ERROR: {SCORED_LEADS_PATH} not found. Run run_pipeline.py first.")
        sys.exit(1)

    leads: list[dict] = json.loads(SCORED_LEADS_PATH.read_text(encoding="utf-8"))
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    counts = export_all(leads, EXPORTS_DIR, generated_at)

    print("=== Export Results ===")
    for fname, n in counts.items():
        print(f"  {fname}: {n} rows")
    print(f"  generated_at:   {generated_at}")
    print(f"  total_input:    {len(leads)} leads")
    print(f"  output_dir:     {EXPORTS_DIR}")


if __name__ == "__main__":
    main()
