"""
Shelby County Register of Deeds — document recording scraper.

Portal: https://search.register.shelby.tn.us/search/index.php

STATUS: REQUIRES_PLAYWRIGHT — the portal is a JavaScript SPA that returns only
navigation markup on raw HTTP fetch.  This stub raises NotImplementedError until
the Playwright implementation is built in Phase 2.

Target document types (TN non-judicial foreclosure context):
  - Appointment of Successor Trustee (ASOT): pre-foreclosure signal; mapped to
    canonical_doc_type "notice_of_default"
  - Lis Pendens: judicial foreclosure / title dispute signal
  - Substitute Trustees Deed: post-sale document (confirmation, not lead)
  - Federal Tax Lien: IRS tax lien on property
  - Judgment Lien: civil judgment lien
  - Mechanics Lien / Mechanic's Lien: construction lien

Build notes:
  1. Use Playwright to render the SPA and interact with the search form.
  2. Fingerprint the form: find all input name attributes and document type
     dropdown values.
  3. Run a date-range search for document type = "Appointment of Successor
     Trustee" (or its portal code) for the past N days.
  4. Extract: instrument_number, recording_date, document_type, grantor,
     grantee, legal_description, parcel_id (if shown).
  5. Instrument URL pattern: search.register.shelby.tn.us/search/?instnum=XXXXXXXX
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def run_scraper(
    output_path: Path,
    *,
    days_back: int = 7,
    doc_types: Optional[list[str]] = None,
    existing_path: Optional[Path] = None,
    verbose: bool = True,
) -> dict:
    """Scrape Register of Deeds document recordings.

    Raises:
        NotImplementedError: Always — Playwright implementation pending.
    """
    raise NotImplementedError(
        "register_shelby scraper requires Playwright (Phase 2). "
        "The portal search.register.shelby.tn.us is a JavaScript SPA that "
        "cannot be scraped with raw HTTP requests. "
        "Build the Playwright adapter before enabling this source in the pipeline."
    )
