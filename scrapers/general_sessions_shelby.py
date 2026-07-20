"""
Shelby County General Sessions Court — Civil Division eviction scraper.

Portal: https://gscivildata.shelbycountytn.gov/pls/gnweb/ck_public_qry_main.cp_main_idx

STATUS: REQUIRES_PLAYWRIGHT — ACS CourtConnect (Oracle PL/SQL, /pls/ path prefix)
returns HTTP 403 on automated HTTP fetch.  This stub raises NotImplementedError
until the Playwright implementation is built in Phase 2.

Target case types (General Sessions Civil jurisdiction in TN):
  - Forcible Entry Detainer (FED) — Tennessee's eviction action
  - Unlawful Detainer
  - Civil cases up to $25,000

In a FED case:
  - Plaintiff = landlord (property owner or property management company)
  - Defendant = tenant
  - Plaintiff name is the signal: repeated FED filings from same plaintiff
    → "tired_landlord" pattern → seller_finance / sub_to deal paths

Build notes:
  1. Use Playwright to load the CourtConnect main page.
  2. Search by case type "DETAINER" or "FED" with a filing date range.
  3. Extract: case_number, case_type, plaintiff, defendant, filing_date, status.
  4. Note: property address may NOT appear in case metadata — plaintiff name
     is the primary signal. Cross-reference assessor for plaintiff-owned parcels.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def run_scraper(
    output_path: Path,
    *,
    days_back: int = 7,
    existing_path: Optional[Path] = None,
    verbose: bool = True,
) -> dict:
    """Scrape General Sessions Civil eviction docket.

    Raises:
        NotImplementedError: Always — Playwright implementation pending.
    """
    raise NotImplementedError(
        "general_sessions_shelby scraper requires Playwright (Phase 2). "
        "The portal gscivildata.shelbycountytn.gov returns HTTP 403 on "
        "automated HTTP requests. Build the Playwright adapter before enabling "
        "this source in the pipeline."
    )
