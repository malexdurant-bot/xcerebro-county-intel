"""
Shelby County Probate Court — JSONL -> raw_event_record converter.

Reads data/raw/probate_court_shelby.jsonl (written by
scrapers/probate_court_shelby.py) and converts probate estate records
into raw_event_records.

canonical_doc_type: letters_testamentary
  §17 rule: inherits from "probate" broad key (DOCUMENT_BODY debtor_source).
  The decedent's estate is the lead subject; the personal representative
  is the filer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

_THIS_DIR = Path(__file__).parent
_REPO_ROOT = _THIS_DIR.parents[1]
for _p in (str(_REPO_ROOT), str(_THIS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_SOURCE_ID = "probate_court_shelby"
_CANONICAL_DOC_TYPE = "letters_testamentary"


def load_probate_jsonl(path: Path, max_records: Optional[int] = None) -> list[dict]:
    """Load active (non-DISAPPEARED) probate records from JSONL."""
    records: list[dict] = []
    if not path.exists():
        return records
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("change_status") == "DISAPPEARED":
                continue
            records.append(rec)
            if max_records is not None and len(records) >= max_records:
                break
    return records


def build_probate_raw_events(
    raw_records: list[dict],
    verbose: bool = False,
) -> list[dict]:
    """Convert probate records to raw_event_records."""
    raw_events: list[dict] = []

    for i, raw_rec in enumerate(raw_records):
        payload = raw_rec.get("raw_payload") or {}
        case_number = (payload.get("case_number") or "").strip() or None
        case_type = (payload.get("case_type") or "").strip() or None
        # Support both new key names and legacy fallbacks from older scraper runs
        decedent = (
            payload.get("decedent_name") or payload.get("case_name") or payload.get("name") or ""
        ).strip() or None
        petitioner = (
            payload.get("petitioner_name") or payload.get("personal_rep") or ""
        ).strip() or None
        filing_date = (payload.get("filing_date") or payload.get("date_filed") or "").strip() or None
        source_url = raw_rec.get("source_url") or ""
        captured_at = raw_rec.get("source_fetched_at")
        confidence = raw_rec.get("parser_confidence", 70)

        if verbose:
            print(f"  [PROBATE {i+1}] {case_number} decedent={decedent}")

        parties: list[dict] = []
        if decedent:
            parties.append({"name": decedent, "name_type": "GR", "raw_role": "DECEDENT"})
        if petitioner:
            parties.append({"name": petitioner, "name_type": "OTHER", "raw_role": "PERSONAL_REPRESENTATIVE"})

        raw_event: dict = {
            "raw_event_id": raw_rec["raw_record_id"],
            "source_id": _SOURCE_ID,
            "source_role": "PRIMARY_EVENT_SOURCE",
            "raw_doc_type": case_type,
            "canonical_doc_type": _CANONICAL_DOC_TYPE,
            "instrument_number": None,
            "recorded_date": filing_date,
            "source_url": source_url,
            "parties": parties,
            "property_refs": {
                "parcel_id": None,
                "situs_address": None,
                "legal_description": None,
                "case_number": case_number,
            },
            "document_body_text": None,
            "parser_confidence": confidence,
            "captured_at": captured_at,
        }
        raw_events.append(raw_event)

    return raw_events
