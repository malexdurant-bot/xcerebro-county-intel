"""
Shelby County Chancery Court — civil docket scraper.

Portal: https://chancerydata.shelbycountytn.gov/chweb/ck_public_qry_main.cp_main_idx

STATUS: REQUIRES_PLAYWRIGHT — ACS CourtConnect (Oracle PL/SQL Web Toolkit) portal
returns HTTP 403 on automated HTTP fetch.  This stub raises NotImplementedError
until the Playwright implementation is built in Phase 2.

Target case types (Chancery Court jurisdiction in TN):
  - Lis Pendens (Abstract of Lien Lis Pendens filed here per TCA § 20-3-101)
  - Judicial foreclosure actions (rare in TN — non-judicial is the norm)
  - Partition actions (co-owner disputes, often estate-related)
  - Quiet title actions (title cloud / messy title pattern)

Build notes:
  1. Use Playwright to load the CourtConnect main page.
  2. Try date-range search for new filings — check if form supports "by date filed"
     without requiring party name (many CourtConnect portals require name search).
  3. Extract: case_number, case_type, plaintiff, defendant, filing_date, case_status.
  4. Cross-reference Lis Pendens abstracts also filed with Register of Deeds
     (same signal, two sources).
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
    """Scrape Chancery Court civil docket.

    Raises:
        NotImplementedError: Always — Playwright implementation pending.
    """
    raise NotImplementedError(
        "chancery_court_shelby scraper requires Playwright (Phase 2). "
        "The portal chancerydata.shelbycountytn.gov returns HTTP 403 on "
        "automated HTTP requests. Build the Playwright adapter before enabling "
        "this source in the pipeline."
    )
