"""Tests for lead_history.py and daily_export.py."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

_THIS_DIR = Path(__file__).parent
_REPO_ROOT = _THIS_DIR.parents[1]
for _p in (str(_REPO_ROOT), str(_THIS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lead_history import LeadHistory  # noqa: E402
from daily_export import export_daily_csvs  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_lead(lead_id: str, score: int = 55, tier: str = "Workable") -> dict:
    return {
        "lead_id": lead_id,
        "score": score,
        "tier": tier,
        "lead_status": "APPROVED_FOR_DASHBOARD",
        "enrichment_status": "ENRICHED",
        "patterns": ["tax"],
        "primary_parcel_id": lead_id.replace("lead_parcel_", ""),
        "attributes": ["absentee"],
        "stack_depth": 1,
        "source_ids": ["treasurer_tax_lien"],
        "parcel_display": {
            "situs_address": "123 Main St",
            "situs_city": "Phoenix",
            "situs_state": "AZ",
        },
        "owner_name": "SMITH JOHN",
    }


@pytest.fixture
def history(tmp_path):
    h = LeadHistory(tmp_path / "test_history.sqlite")
    yield h
    h.close()


# ---------------------------------------------------------------------------
# lead_history tests
# ---------------------------------------------------------------------------


def test_first_seen_stable(history):
    """first_seen_date must not change on subsequent upserts."""
    lead = _make_lead("lead_parcel_100-01-001")
    history.upsert_leads([lead], "2026-06-28")
    history.upsert_leads([lead], "2026-06-29")
    row = history.get_lead_row("lead_parcel_100-01-001")
    assert row["first_seen_date"] == "2026-06-28"


def test_last_seen_updates(history):
    """last_seen_date must advance to the most recent run date."""
    lead = _make_lead("lead_parcel_100-01-002")
    history.upsert_leads([lead], "2026-06-28")
    history.upsert_leads([lead], "2026-06-30")
    row = history.get_lead_row("lead_parcel_100-01-002")
    assert row["last_seen_date"] == "2026-06-30"


def test_score_delta(history):
    """score_delta = current_score - last_score after update."""
    lead = _make_lead("lead_parcel_100-01-003", score=55)
    history.upsert_leads([lead], "2026-06-28")
    lead["score"] = 65
    history.upsert_leads([lead], "2026-06-29")
    row = history.get_lead_row("lead_parcel_100-01-003")
    assert row["score_delta"] == 10
    assert row["current_score"] == 65
    assert row["last_score"] == 55


def test_dedup_by_lead_id(history):
    """Duplicate lead_ids in the same upsert batch don't create duplicate rows."""
    leads = [_make_lead("lead_parcel_100-01-004")] * 3
    history.upsert_leads(leads, "2026-06-28")
    rows = history.get_lead_rows()
    matches = [r for r in rows if r["lead_id"] == "lead_parcel_100-01-004"]
    assert len(matches) == 1


def test_known_lead_ids(history):
    """known_lead_ids returns all inserted lead_ids."""
    leads = [_make_lead(f"lead_parcel_100-{i:02d}-001") for i in range(5)]
    history.upsert_leads(leads, "2026-06-28")
    known = history.known_lead_ids()
    assert len(known) == 5
    assert "lead_parcel_100-00-001" in known


def test_rebuild_wipes_and_reinserts(history):
    """rebuild_from_leads replaces all existing history rows."""
    history.upsert_leads([_make_lead("lead_parcel_OLD")], "2026-06-01")
    history.rebuild_from_leads([_make_lead("lead_parcel_NEW")], "2026-06-28")
    assert history.get_lead_row("lead_parcel_OLD") is None
    assert history.get_lead_row("lead_parcel_NEW") is not None


def test_no_cap_rebuild(history):
    """rebuild_from_leads processes all leads without any cap."""
    leads = [_make_lead(f"lead_parcel_{i:06d}") for i in range(1000)]
    result = history.rebuild_from_leads(leads, "2026-06-28")
    assert result["new"] == 1000


def test_run_state_last_run_date(history):
    """get/set last_successful_run_date round-trips correctly."""
    assert history.get_last_run_date() is None
    history.set_last_run_date("2026-06-28")
    assert history.get_last_run_date() == "2026-06-28"
    history.set_last_run_date("2026-06-29")
    assert history.get_last_run_date() == "2026-06-29"


def test_get_lead_rows_since_date(history):
    """get_lead_rows(since_date) filters correctly."""
    history.upsert_leads([_make_lead("lead_parcel_A")], "2026-06-01")
    history.upsert_leads([_make_lead("lead_parcel_B")], "2026-06-28")
    rows = history.get_lead_rows(since_date="2026-06-10")
    ids = {r["lead_id"] for r in rows}
    assert "lead_parcel_B" in ids
    assert "lead_parcel_A" not in ids


def test_get_history_by_id(history):
    """get_history_by_id returns dict keyed by lead_id."""
    leads = [_make_lead(f"lead_parcel_{i:03d}") for i in range(3)]
    history.upsert_leads(leads, "2026-06-28")
    by_id = history.get_history_by_id()
    assert "lead_parcel_000" in by_id
    assert by_id["lead_parcel_001"]["first_seen_date"] == "2026-06-28"


# ---------------------------------------------------------------------------
# daily_export tests
# ---------------------------------------------------------------------------


def test_daily_csv_completeness(tmp_path):
    """export_daily_csvs writes dated and latest CSVs for all three categories."""
    leads = [
        {**_make_lead(f"lead_parcel_{i:03d}"), "primary_parcel_id": f"100-{i:02d}-001"}
        for i in range(10)
    ]
    history_by_id = {
        lead["lead_id"]: {
            "lead_id": lead["lead_id"],
            "first_seen_date": "2026-06-28",
            "last_seen_date": "2026-06-28",
            "score_delta": 0,
        }
        for lead in leads
    }

    counts = export_daily_csvs(
        new_leads=leads,
        history_by_id=history_by_id,
        run_date="2026-06-28",
        out_dir=tmp_path,
    )

    assert counts["new_leads.csv"] == 10
    assert (tmp_path / "daily" / "new_leads_2026-06-28.csv").exists()
    assert (tmp_path / "latest" / "new_leads.csv").exists()
    assert (tmp_path / "daily" / "skiptrace_ready_2026-06-28.csv").exists()
    assert (tmp_path / "latest" / "skiptrace_ready.csv").exists()
    assert (tmp_path / "daily" / "review_required_2026-06-28.csv").exists()
    assert (tmp_path / "latest" / "review_required.csv").exists()


def test_daily_csv_temporal_fields(tmp_path):
    """Daily CSVs include first_seen_date, is_new_today, score_delta columns."""
    lead = _make_lead("lead_parcel_TEST")
    history_by_id = {
        "lead_parcel_TEST": {
            "lead_id": "lead_parcel_TEST",
            "first_seen_date": "2026-06-25",
            "last_seen_date": "2026-06-28",
            "score_delta": 5,
        }
    }
    export_daily_csvs(
        new_leads=[lead],
        history_by_id=history_by_id,
        run_date="2026-06-28",
        out_dir=tmp_path,
    )
    csv_path = tmp_path / "latest" / "new_leads.csv"
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    assert len(rows) == 1
    row = rows[0]
    assert row["first_seen_date"] == "2026-06-25"
    assert row["is_new_today"] == "0"
    assert row["score_delta"] == "5"
    assert row["days_since_first_seen"] == "3"


def test_daily_csv_review_split(tmp_path):
    """REVIEW_REQUIRED leads appear in review_required.csv but not skiptrace_ready.csv."""
    review_lead = {
        **_make_lead("lead_parcel_REVIEW"),
        "lead_status": "REVIEW_REQUIRED",
        "enrichment_status": "REVIEW_REQUIRED",
    }
    skiptrace_lead = _make_lead("lead_parcel_SKIP")

    history_by_id = {
        lead["lead_id"]: {
            "lead_id": lead["lead_id"],
            "first_seen_date": "2026-06-28",
            "last_seen_date": "2026-06-28",
            "score_delta": 0,
        }
        for lead in [review_lead, skiptrace_lead]
    }

    counts = export_daily_csvs(
        new_leads=[review_lead, skiptrace_lead],
        history_by_id=history_by_id,
        run_date="2026-06-28",
        out_dir=tmp_path,
    )

    assert counts["new_leads.csv"] == 2
    assert counts["review_required.csv"] == 1
    assert counts["skiptrace_ready.csv"] == 1
