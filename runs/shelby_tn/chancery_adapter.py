"""
Shelby County Chancery Court — JSONL -> raw_event_record converter.

Reads data/raw/chancery_court_shelby.jsonl (written by
scrapers/chancery_court_shelby.py) and converts Chancery case records
into raw_event_records.

Case type -> canonical_doc_type mapping:
  FO -> lis_pendens          (judicial foreclosure complaint; DF = property owner)
  PA -> partition_action     (partition of jointly-owned property; DF = co-owner)
  QT -> quiet_title_action   (quiet title complaint; DF = party with title cloud)
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

_SOURCE_ID = "chancery_court_shelby"

_CASE_TYPE_MAP: dict[str, str] = {
    "FO": "lis_pendens",
    "PA": "partition_action",
    "QT": "quiet_title_action",
    "EF": "lis_pendens",   # enjoin foreclosure — similar to lis_pendens
    "CO": "lis_pendens",   # delinquent tax collection
    "LN": "judgment_lien", # lien
}

_DEFAULT_CANONICAL = "lis_pendens"


def load_chancery_jsonl(path: Path, max_records: Optional[int] = None) -> list[dict]:
    """Load active (non-DISAPPEARED) chancery records from JSONL."""
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


def build_chancery_raw_events(
    raw_records: list[dict],
    verbose: bool = False,
) -> list[dict]:
    """Convert chancery records to raw_event_records."""
    raw_events: list[dict] = []

    for i, raw_rec in enumerate(raw_records):
        payload = raw_rec.get("raw_payload") or {}
        case_number = (payload.get("case_number") or "").strip() or None
        case_type = (payload.get("case_type") or "").strip().upper() or None
        plaintiff = (payload.get("plaintiff") or "").strip() or None
        defendant = (payload.get("defendant") or "").strip() or None
        filing_date = (payload.get("filing_date") or "").strip() or None
        source_url = raw_rec.get("source_url") or ""
        captured_at = raw_rec.get("source_fetched_at")
        confidence = raw_rec.get("parser_confidence", 80)

        canonical_doc_type = _CASE_TYPE_MAP.get(case_type or "", _DEFAULT_CANONICAL)

        if verbose:
            print(f"  [CHANCERY {i+1}] {case_type} {case_number} PL={plaintiff} DF={defendant}")

        parties: list[dict] = []
        if plaintiff:
            parties.append({"name": plaintiff, "name_type": "PL", "raw_role": "PLAINTIFF"})
        if defendant:
            parties.append({"name": defendant, "name_type": "DF", "raw_role": "DEFENDANT"})

        raw_event: dict = {
            "raw_event_id": raw_rec["raw_record_id"],
            "source_id": _SOURCE_ID,
            "source_role": "PRIMARY_EVENT_SOURCE",
            "raw_doc_type": case_type,
            "canonical_doc_type": canonical_doc_type,
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
