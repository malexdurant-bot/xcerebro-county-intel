"""
Pull a bounded mixed-surname probate sample for parcel matching validation.

Searches the Maricopa Superior Court probate portal for multiple last names
chosen to span three ambiguity tiers in the local parcel index:

  Tier 1 — very common Anglo surnames:  expect AMBIGUOUS (many parcels per name)
  Tier 2 — medium-frequency surnames:   expect mix of CONFIRMED and POSSIBLE
  Tier 3 — less-common surnames:        expect CONFIRMED (unique parcel hit)

Caps total at MAX_TOTAL records across all surnames (PER_SURNAME_MAX each).
Writes to data/raw/superior_court_probate_broad.jsonl (separate from production).
No names, case numbers, URLs, or raw records are printed.

Requires: pip install playwright && playwright install chromium

Usage:
    python runs/maricopa_az/pull_probate_sample_mixed.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

from scrapers.superior_court_probate_maricopa import (
    _pw_search_by_name,   # noqa: PLC2701 — internal import within same repo
    USER_AGENT,
)

OUTPUT_PATH = REPO_ROOT / "data" / "raw" / "superior_court_probate_broad.jsonl"

MAX_TOTAL = 100
PER_SURNAME_MAX = 15

# Surnames chosen for varied parcel-index ambiguity profiles.
# (tier label, surname) — order controls search priority
TARGET_SURNAMES: list[tuple[str, str]] = [
    # Tier 1 — very common: multiple parcels per name expected → AMBIGUOUS
    ("tier1_common",    "Smith"),
    ("tier1_common",    "Johnson"),
    # Tier 2 — medium: mix of CONFIRMED, POSSIBLE, AMBIGUOUS
    ("tier2_medium",    "Williams"),
    ("tier2_medium",    "Brown"),
    ("tier2_medium",    "Davis"),
    ("tier2_medium",    "Miller"),
    ("tier2_medium",    "Anderson"),
    ("tier2_medium",    "Murphy"),
    # Tier 3 — less common: unique parcel hit expected → CONFIRMED
    ("tier3_uncommon",  "Petersen"),
    ("tier3_uncommon",  "Cunningham"),
    ("tier3_uncommon",  "Blackwell"),
    ("tier3_uncommon",  "Yamamoto"),
]


def main() -> None:
    if not PLAYWRIGHT_AVAILABLE:
        print("ERROR: playwright not installed.")
        print("Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    print("=== Mixed-Surname Probate Sample Pull ===")
    print(f"Max total:        {MAX_TOTAL}")
    print(f"Max per surname:  {PER_SURNAME_MAX}")
    print(f"Output:           {OUTPUT_PATH.name}")
    print(f"Surnames:         {len(TARGET_SURNAMES)}")
    print()

    accumulated: list[dict] = []
    seen_ids: set[str] = set()
    tier_counts: Counter = Counter()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        for tier, surname in TARGET_SURNAMES:
            remaining = MAX_TOTAL - len(accumulated)
            if remaining <= 0:
                print(f"  [{tier}] {surname:15s}  cap reached, skipping")
                continue

            per_max = min(PER_SURNAME_MAX, remaining)
            try:
                records = _pw_search_by_name(page, surname, "", max_features=per_max)
            except Exception as exc:
                print(f"  [{tier}] {surname:15s}  ERROR: {exc}")
                continue

            added = 0
            for rec in records:
                rid = rec.get("raw_record_id")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    rec["_surname_tier"] = tier
                    accumulated.append(rec)
                    tier_counts[tier] += 1
                    added += 1

            print(f"  [{tier}] {surname:15s}  portal={len(records)}  added={added}  total={len(accumulated)}")

        browser.close()

    # Write clean (no merge — this is a fresh broad sample, not a production run)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        for rec in accumulated:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print()
    print("=== Pull Summary ===")
    print(f"  Records written:      {len(accumulated)}")
    for tier in ["tier1_common", "tier2_medium", "tier3_uncommon"]:
        print(f"  {tier}:  {tier_counts[tier]}")
    print(f"  Output:               {OUTPUT_PATH.name}")
    print()
    print("Next: run broad_sample_pipeline.cmd step 2 (detail enrichment)")


if __name__ == "__main__":
    main()
