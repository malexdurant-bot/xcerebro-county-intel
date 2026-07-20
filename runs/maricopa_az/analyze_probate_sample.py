"""
Probate JSONL field-availability analysis.

Reads data/raw/superior_court_probate.jsonl and reports aggregate stats only.
No record content (names, case numbers, URLs) is printed.

Usage:
    python runs/maricopa_az/analyze_probate_sample.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
JSONL_PATH = REPO_ROOT / "data" / "raw" / "superior_court_probate.jsonl"

# Privacy-placeholder patterns (same as probate_adapter.py)
_PROTECTED_PATTERNS = (
    re.compile(r"\bminor\b.*\bDOB\b", re.IGNORECASE),
    re.compile(r"information is protected", re.IGNORECASE),
)

# Case prefix → classification
_PREFIX_RE = re.compile(r"^([A-Z]+)", re.IGNORECASE)

_PROBATE_PREFIXES = frozenset(["PB"])
_NOISE_PREFIXES = frozenset(["TR", "CR", "DR", "DO", "FC", "CV", "LP"])


def _is_protected(name: str) -> bool:
    return any(p.search(name) for p in _PROTECTED_PATTERNS)


def _is_blank(v) -> bool:
    return v is None or str(v).strip() == ""


def main() -> None:
    if not JSONL_PATH.exists():
        print(f"ERROR: {JSONL_PATH} not found. Run fetch_probate_sample.cmd first.")
        sys.exit(1)

    records: list[dict] = []
    with open(JSONL_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    total = len(records)
    if total == 0:
        print("No records in JSONL.")
        return

    # change_status breakdown
    status_counts: dict[str, int] = {}
    for r in records:
        cs = r.get("change_status") or "UNKNOWN"
        status_counts[cs] = status_counts.get(cs, 0) + 1

    # Filter to active records only (exclude DISAPPEARED)
    active = [r for r in records if r.get("change_status") != "DISAPPEARED"]
    n_active = len(active)

    # Field availability on active records
    n_has_case_number = 0
    n_has_decedent_name = 0
    n_decedent_protected = 0
    n_decedent_real = 0
    n_has_petitioner = 0
    n_has_filing_date = 0
    n_has_case_type = 0
    n_has_case_status = 0
    n_has_property_address = 0
    n_has_apn = 0
    n_has_detail_url = 0

    # Case prefix counts
    prefix_counts: dict[str, int] = {}
    # Classification counts
    class_counts: dict[str, int] = {
        "usable_estate_lead": 0,
        "review_required": 0,
        "noise": 0,
        "needs_detail_parsing": 0,
    }

    for r in active:
        payload = r.get("raw_payload") or {}

        case_number = (payload.get("case_number") or "").strip()
        decedent_name = (payload.get("decedent_name") or "").strip()
        petitioner_name = (payload.get("petitioner_name") or "").strip()
        filing_date = (payload.get("filing_date") or "").strip()
        case_type = (payload.get("case_type") or "").strip()
        case_status = (payload.get("case_status") or "").strip()
        case_detail_url = (payload.get("case_detail_url") or "").strip()

        # Note: property_address and APN are not fields the scraper captures from listing
        # They would only appear in a future detail-page scrape
        prop_address = (payload.get("property_address") or "").strip()
        apn = (payload.get("apn") or payload.get("parcel_id") or "").strip()

        if case_number:
            n_has_case_number += 1
        if decedent_name:
            n_has_decedent_name += 1
            if _is_protected(decedent_name):
                n_decedent_protected += 1
            else:
                n_decedent_real += 1
        if petitioner_name and petitioner_name != decedent_name:
            n_has_petitioner += 1
        if filing_date:
            n_has_filing_date += 1
        if case_type:
            n_has_case_type += 1
        if case_status:
            n_has_case_status += 1
        if prop_address:
            n_has_property_address += 1
        if apn:
            n_has_apn += 1
        if case_detail_url:
            n_has_detail_url += 1

        # Case prefix
        m = _PREFIX_RE.match(case_number.upper()) if case_number else None
        prefix = m.group(1) if m else "UNKNOWN"
        prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1

        # Classification
        if prefix in _NOISE_PREFIXES:
            class_counts["noise"] += 1
        elif prefix in _PROBATE_PREFIXES:
            # PB case — is it usable?
            if decedent_name and not _is_protected(decedent_name):
                # Real party name present; still needs detail page to confirm
                # decedent role, property, and specific case type
                class_counts["needs_detail_parsing"] += 1
            else:
                # Protected/no party → review required, nothing to work with from listing
                class_counts["review_required"] += 1
        else:
            class_counts["needs_detail_parsing"] += 1

    # Note: "usable_estate_lead" requires detail-page data (filing_date, case_type,
    # property address) — none of which are available from the listing. So this
    # bucket remains 0 until detail-page parsing is implemented.
    # Records classified as "needs_detail_parsing" are the best candidates.

    print("=" * 60)
    print("Probate JSONL Field-Availability Report")
    print(f"Source: {JSONL_PATH.name}")
    print("=" * 60)
    print()
    print(f"Total records in JSONL:          {total}")
    print(f"  Change-status breakdown:       {dict(sorted(status_counts.items()))}")
    print(f"  Active records (non-DISAPPEARED): {n_active}")
    print()
    print("--- Field availability (active records) ---")
    print(f"  case_number present:           {n_has_case_number} / {n_active}")
    print(f"  decedent_name present:         {n_has_decedent_name} / {n_active}")
    print(f"    of which — real name:        {n_decedent_real}")
    print(f"    of which — protected/redacted:{n_decedent_protected}")
    print(f"  petitioner (distinct from decedent): {n_has_petitioner} / {n_active}")
    print(f"  filing_date:                   {n_has_filing_date} / {n_active}  [listing field]")
    print(f"  case_type:                     {n_has_case_type} / {n_active}  [listing field]")
    print(f"  case_status:                   {n_has_case_status} / {n_active}  [listing field]")
    print(f"  property_address:              {n_has_property_address} / {n_active}  [detail-page only]")
    print(f"  APN / parcel_id:               {n_has_apn} / {n_active}  [detail-page only]")
    print(f"  case_detail_url:               {n_has_detail_url} / {n_active}")
    print()
    print("--- Case number prefix breakdown (active) ---")
    for prefix, cnt in sorted(prefix_counts.items(), key=lambda x: -x[1]):
        label = "probate" if prefix in _PROBATE_PREFIXES else ("noise" if prefix in _NOISE_PREFIXES else "unknown")
        print(f"  {prefix:<10} {cnt:>4}  ({label})")
    print()
    print("--- Classification (active records) ---")
    print(f"  usable_estate_lead:            {class_counts['usable_estate_lead']}")
    print(f"    (requires detail-page data — always 0 from listing alone)")
    print(f"  needs_detail_parsing:          {class_counts['needs_detail_parsing']}")
    print(f"    (PB prefix + real party name — best candidates for detail fetch)")
    print(f"  review_required:               {class_counts['review_required']}")
    print(f"    (PB prefix but party protected/missing — nothing actionable from listing)")
    print(f"  noise:                         {class_counts['noise']}")
    print(f"    (non-PB prefix — wrong case type)")
    print()
    print("--- Blockers ---")
    blockers = []
    if n_has_filing_date == 0:
        blockers.append("filing_date: not available from listing — detail-page parse required")
    if n_has_case_type == 0:
        blockers.append("case_type: not available from listing — all classified as letters_testamentary (default)")
    if n_has_property_address == 0:
        blockers.append("property_address: not available from listing — no APN resolution possible")
    if n_has_apn == 0:
        blockers.append("APN: not available from listing — no parcel enrichment or multi-signal stacking")
    if n_decedent_protected > 0:
        blockers.append(
            f"party protected: {n_decedent_protected} record(s) returned court privacy placeholder "
            f"— no usable party name available from listing"
        )
    if not blockers:
        blockers.append("none identified")
    for b in blockers:
        print(f"  - {b}")
    print()
    print("=" * 60)
    print("Summary:")
    print(f"  Records pulled (active):       {n_active}")
    print(f"  With real decedent name:       {n_decedent_real}  (party text from listing, role unconfirmed)")
    print(f"  With petitioner (distinct):    {n_has_petitioner}")
    print(f"  With filing date:              {n_has_filing_date}")
    print(f"  With property / address / APN: {n_has_property_address + n_has_apn}  (listing only)")
    print(f"  Likely usable (needs detail):  {class_counts['needs_detail_parsing']}")
    print(f"  Blockers: filing_date=NO  case_type=NO  property_address=NO  APN=NO")
    print("=" * 60)


if __name__ == "__main__":
    main()
