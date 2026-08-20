"""
Shelby County General Sessions Civil — JSONL -> raw_event_record converter.

Reads data/raw/general_sessions_shelby.jsonl (written by
scrapers/general_sessions_shelby.py) and converts FED/eviction case records
into raw_event_records.

canonical_doc_type: eviction_filing
  §17 rule: PL (plaintiff/landlord) is the debtor — the tired-landlord signal.
  The DF (defendant/tenant) is the filer side.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional

# all_cells[2] format: "1446 HARRISON ST Memphis TN 38108 Case: 2395408 PLAINTIFF V DEFENDANT"
_ADDR_CELL_RE = re.compile(r"^(.*?)\s+Case:\s*\d+", re.IGNORECASE)
# Caption after "Case: XXXXXX ": "MARCUS GIPSON V MONICA BRITTENUM"
_CAPTION_PL_RE = re.compile(r"Case:\s*\d+\s+(.+?)\s+V\s+", re.IGNORECASE)

_THIS_DIR = Path(__file__).parent
_REPO_ROOT = _THIS_DIR.parents[1]
for _p in (str(_REPO_ROOT), str(_THIS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_SOURCE_ID = "general_sessions_civil_shelby"
_CANONICAL_DOC_TYPE = "eviction_filing"


def load_eviction_jsonl(path: Path, max_records: Optional[int] = None) -> list[dict]:
    """Load active (non-DISAPPEARED) eviction records from JSONL."""
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


def build_eviction_raw_events(
    raw_records: list[dict],
    verbose: bool = False,
) -> list[dict]:
    """Convert eviction records to raw_event_records."""
    raw_events: list[dict] = []

    for i, raw_rec in enumerate(raw_records):
        payload = raw_rec.get("raw_payload") or {}
        case_number = (payload.get("case_number") or "").strip() or None
        case_type = (payload.get("case_type") or "").strip() or None
        plaintiff = (payload.get("plaintiff") or "").strip() or None
        defendant = (payload.get("defendant") or "").strip() or None
        filing_date = (payload.get("filing_date") or "").strip() or None
        source_url = raw_rec.get("source_url") or ""
        captured_at = raw_rec.get("source_fetched_at")
        confidence = raw_rec.get("parser_confidence", 80)

        # Extract property address + plaintiff-fallback from the combined cell.
        # all_cells[2]: "1446 HARRISON ST Memphis TN 38108 Case: 2395408 FATOUMATS DIALLO V ALL OCCUPANTS"
        situs_address: str | None = None
        all_cells = payload.get("all_cells") or []
        caption_plaintiff: str | None = None
        if len(all_cells) > 2:
            cell = str(all_cells[2]).strip()
            m_addr = _ADDR_CELL_RE.match(cell)
            if m_addr:
                situs_address = m_addr.group(1).strip() or None
            # Fallback: parse plaintiff from case caption when field is null
            if not plaintiff:
                m_pl = _CAPTION_PL_RE.search(cell)
                if m_pl:
                    caption_plaintiff = m_pl.group(1).strip() or None

        if verbose:
            print(f"  [EVICTION {i+1}] {case_number} PL={plaintiff} DF={defendant}")

        parties: list[dict] = []
        effective_plaintiff = plaintiff or caption_plaintiff
        if effective_plaintiff:
            parties.append({"name": effective_plaintiff, "name_type": "PL", "raw_role": "PLAINTIFF_LANDLORD"})
        if defendant:
            parties.append({"name": defendant, "name_type": "DF", "raw_role": "DEFENDANT_TENANT"})

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
                "situs_address": situs_address,
                "legal_description": situs_address,
                "case_number": case_number,
            },
            "document_body_text": None,
            "parser_confidence": confidence,
            "captured_at": captured_at,
        }
        raw_events.append(raw_event)

    return raw_events
