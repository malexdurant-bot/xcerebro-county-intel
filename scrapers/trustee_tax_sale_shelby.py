"""
Shelby County Trustee — tax sale property list scraper.

Downloads the official CSV from the Shelby County Trustee's S3 bucket:
  https://scgpublic.s3.amazonaws.com/TaxSaleExtract.csv

CSV schema (confirmed 2026-07-19):
  ParcelID     — parcel ID string (e.g. "035087  00049") — exact spacing required
  Alt_Parcel   — alternative parcel number (no spaces, e.g. "03508700000490")
  Street Number — address house number
  Street Name  — street name (no type suffix, e.g. "S MAIN")
  Tax Sale     — sale code: "TS2301" or "TS2302" (sale cycle identifier)
  Register GIS — link to Register GIS map: http://gis.register.shelby.tn.us/?parcelid=...

Owner name is NOT in the CSV.  Owner resolution is performed by the
tax_sale_adapter using the parcel_master_shelby ArcGIS lookup.

Output format: JSONL file at data/raw/trustee_tax_sale_shelby.jsonl
Each line is a raw_record:
  {
    "raw_record_id": "shelby_ts_<alt_parcel>",
    "raw_payload": { ... CSV fields ... },
    "source_url": "https://scgpublic.s3.amazonaws.com/TaxSaleExtract.csv",
    "source_fetched_at": "2026-07-19T...",
    "parser_confidence": "CONFIRMED",
    "change_status": "NEW" | "SAME" | "DISAPPEARED"
  }
"""

from __future__ import annotations

import csv
import io
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_CSV_URL = "https://scgpublic.s3.amazonaws.com/TaxSaleExtract.csv"
_SOURCE_ID = "trustee_tax_sale"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/csv,text/plain,*/*",
}


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _download_csv(url: str = _CSV_URL) -> str:
    """Download the tax sale CSV and return its text content."""
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    # Try UTF-8, fall back to latin-1
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _parse_csv(text: str) -> list[dict]:
    """Parse CSV text into a list of row dicts with cleaned field names."""
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        # Strip whitespace from keys and values
        cleaned = {k.strip(): (v.strip() if v else "") for k, v in row.items()}
        rows.append(cleaned)
    return rows


def _make_record_id(row: dict) -> str:
    """Create a stable record ID from Alt_Parcel (no spaces, unique)."""
    alt = re.sub(r"\W+", "", row.get("Alt_Parcel", ""))
    parcel = re.sub(r"\W+", "", row.get("ParcelID", ""))
    key = alt or parcel or "unknown"
    return f"shelby_ts_{key}"


def _build_situs_address(row: dict) -> Optional[str]:
    """Combine Street Number and Street Name into a partial address."""
    num = row.get("Street Number", "").strip()
    street = row.get("Street Name", "").strip()
    if not street:
        return None
    if num and num != "0":
        return f"{num} {street}"
    return street


# ---------------------------------------------------------------------------
# Main scraper function
# ---------------------------------------------------------------------------

def run_scraper(
    output_path: Path,
    *,
    existing_path: Optional[Path] = None,
    verbose: bool = True,
) -> dict:
    """Download the tax sale CSV and write JSONL output.

    Args:
        output_path: Where to write the JSONL file.
        existing_path: Previous JSONL (for change_status tracking). If None,
                       all records are marked NEW.
        verbose: Print progress.

    Returns:
        Summary dict with counts: new, same, disappeared, total.
    """
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    if verbose:
        print(f"[trustee_tax_sale_shelby] Downloading CSV from {_CSV_URL}...")

    text = _download_csv()
    rows = _parse_csv(text)

    if verbose:
        print(f"  Parsed {len(rows)} rows from CSV")

    # Load existing records for change_status tracking
    existing_by_id: dict[str, dict] = {}
    if existing_path and existing_path.exists():
        with open(existing_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    existing_by_id[rec["raw_record_id"]] = rec
                except (json.JSONDecodeError, KeyError):
                    continue

    seen_ids: set[str] = set()
    output_records: list[dict] = []

    new_count = same_count = 0
    for row in rows:
        parcel_id = row.get("ParcelID", "").strip()
        if not parcel_id:
            continue

        record_id = _make_record_id(row)
        seen_ids.add(record_id)

        situs_address = _build_situs_address(row)

        payload = {
            "parcel_id": parcel_id,
            "alt_parcel": row.get("Alt_Parcel", "").strip() or None,
            "situs_address": situs_address,
            "tax_sale_code": row.get("Tax Sale", "").strip() or None,
            "gis_link": row.get("Register GIS", "").strip() or None,
            "source": "trustee_tax_sale_csv",
        }

        # Determine change status
        if record_id in existing_by_id:
            old_payload = existing_by_id[record_id].get("raw_payload", {})
            if old_payload.get("tax_sale_code") == payload["tax_sale_code"]:
                change_status = "SAME"
                same_count += 1
            else:
                change_status = "UPDATED"
                new_count += 1
        else:
            change_status = "NEW"
            new_count += 1

        record = {
            "raw_record_id": record_id,
            "raw_payload": payload,
            "source_url": _CSV_URL,
            "source_fetched_at": fetched_at,
            "parser_confidence": "CONFIRMED",
            "change_status": change_status,
        }
        output_records.append(record)

    # Mark disappeared records (were in existing, not in new download)
    disappeared_count = 0
    for rec_id, existing_rec in existing_by_id.items():
        if rec_id not in seen_ids:
            disappeared_rec = {**existing_rec, "change_status": "DISAPPEARED"}
            output_records.append(disappeared_rec)
            disappeared_count += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        for rec in output_records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    summary = {
        "total": len(rows),
        "new": new_count,
        "same": same_count,
        "disappeared": disappeared_count,
        "output_path": str(output_path),
        "fetched_at": fetched_at,
    }

    if verbose:
        print(f"  NEW: {new_count}  SAME: {same_count}  DISAPPEARED: {disappeared_count}")
        print(f"  Written to {output_path}")

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    _REPO_ROOT = Path(__file__).resolve().parents[1]
    out = _REPO_ROOT / "data" / "raw" / "trustee_tax_sale_shelby.jsonl"
    prev = out if out.exists() else None

    result = run_scraper(out, existing_path=prev, verbose=True)
    print(f"\nDone: {result}")
    sys.exit(0)
