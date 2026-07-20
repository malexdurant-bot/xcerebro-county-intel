"""
Shelby County Register of Deeds — JSONL -> raw_event_record converter.

Reads data/raw/register_shelby.jsonl (written by scrapers/register_shelby.py)
and converts each record into a raw_event_record.

Document type -> canonical_doc_type mapping:
  APPT -> appointment_of_substitute_trustee  (pre-foreclosure signal; §17 rule
          inherits from "foreclosure_notice" via BROAD_KEY_REGISTRY_ALIASES)
  IRS  -> federal_tax_lien  (taxpayer = GR; IRS = GE)
  LIEN -> judgment_lien     (debtor = GR; creditor = GE)
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

_SOURCE_ID = "register_shelby"

_DOC_TYPE_MAP: dict[str, str] = {
    "APPT": "appointment_of_substitute_trustee",
    "IRS": "federal_tax_lien",
    "LIEN": "judgment_lien",
}


def _parties_for_doc_type(raw_doc_type: str, grantor: Optional[str], grantee: Optional[str]) -> list[dict]:
    """Return party list shaped for the debtor_party_engine."""
    dt = (raw_doc_type or "").upper()
    parties: list[dict] = []

    if dt == "APPT":
        # Appointment of Substitute Trustee:
        # GR = lender / servicer (filer); GE = incoming substitute trustee
        # Homeowner (debtor) is NOT on this document — pipeline routes to REVIEW_REQUIRED
        if grantor:
            parties.append({"name": grantor, "name_type": "GR", "raw_role": "APPOINTING_PARTY"})
        if grantee:
            parties.append({"name": grantee, "name_type": "GE", "raw_role": "SUBSTITUTE_TRUSTEE"})

    elif dt == "IRS":
        # Federal Tax Lien: GR = taxpayer (the debtor); GE = IRS
        if grantor:
            parties.append({"name": grantor, "name_type": "GR", "raw_role": "TAXPAYER"})
        if grantee:
            parties.append({"name": grantee, "name_type": "GE", "raw_role": "IRS"})

    elif dt == "LIEN":
        # Judgment Lien: GR = debtor against whom judgment was entered; GE = creditor
        if grantor:
            parties.append({"name": grantor, "name_type": "GR", "raw_role": "JUDGMENT_DEBTOR"})
        if grantee:
            parties.append({"name": grantee, "name_type": "GE", "raw_role": "JUDGMENT_CREDITOR"})

    else:
        # Generic fallback
        if grantor:
            parties.append({"name": grantor, "name_type": "GR", "raw_role": "GRANTOR"})
        if grantee:
            parties.append({"name": grantee, "name_type": "GE", "raw_role": "GRANTEE"})

    return parties


def load_register_jsonl(path: Path, max_records: Optional[int] = None) -> list[dict]:
    """Load active (non-DISAPPEARED) register records from JSONL."""
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


def build_register_raw_events(
    raw_records: list[dict],
    verbose: bool = False,
) -> list[dict]:
    """Convert register records to raw_event_records."""
    raw_events: list[dict] = []

    for i, raw_rec in enumerate(raw_records):
        payload = raw_rec.get("raw_payload") or {}
        inst_num = (payload.get("instrument_number") or "").strip() or None
        raw_doc_type = (payload.get("document_type") or "").strip().upper() or None
        grantor = (payload.get("grantor") or "").strip() or None
        grantee = (payload.get("grantee") or "").strip() or None
        recording_date = (payload.get("recording_date") or "").strip() or None
        prop_desc = (payload.get("prop_description") or "").strip() or None

        canonical_doc_type = _DOC_TYPE_MAP.get(raw_doc_type or "", "appointment_of_substitute_trustee")
        source_url = raw_rec.get("source_url") or ""
        captured_at = raw_rec.get("source_fetched_at")
        confidence = raw_rec.get("parser_confidence", 80)

        if verbose:
            print(f"  [REGISTER {i+1}] {raw_doc_type} inst={inst_num} grantor={grantor}")

        parties = _parties_for_doc_type(raw_doc_type or "", grantor, grantee)

        raw_event: dict = {
            "raw_event_id": raw_rec["raw_record_id"],
            "source_id": _SOURCE_ID,
            "source_role": "PRIMARY_EVENT_SOURCE",
            "raw_doc_type": raw_doc_type,
            "canonical_doc_type": canonical_doc_type,
            "instrument_number": inst_num,
            "recorded_date": recording_date,
            "source_url": source_url,
            "parties": parties,
            "property_refs": {
                "parcel_id": None,
                "situs_address": None,
                "legal_description": prop_desc,
                "case_number": None,
            },
            "document_body_text": None,
            "parser_confidence": confidence,
            "captured_at": captured_at,
        }
        raw_events.append(raw_event)

    return raw_events
