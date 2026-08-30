"""
Dallas County Clerk — Official Public Records search (PublicSearch/Kofile portal).

Portal: https://dallas.tx.publicsearch.us/
Vendor: Kofile "PublicSearch" — same vendor family already recon'd for Bexar
County (config/counties/bexar_tx.json: clerk_recordings.url =
https://bexar.tx.publicsearch.us/).

Architecture (fingerprinted 2026-08-22 via live Playwright network capture,
corrected 2026-08-22 after live verification found the initial approach
unreliable):
  - The portal is a Preact SPA. The rendered results table is populated over
    a WebSocket (wss://<slug>.tx.publicsearch.us/ws, Kofile's internal
    "@kofile/FETCH_DOCUMENTS/v4" protocol) — there is no plain REST/JSON HTTP
    endpoint and a bare `curl` of /results returns only the unrendered app
    shell.
  - IMPORTANT — do NOT navigate directly to a hand-built /results?... URL.
    That was the original design and it is broken: live verification on
    2026-08-22 showed the SPA gets stuck on "Loading Results..." indefinitely
    and eventually the backend itself returns "Error While Running Search:
    The request timed out." This happens whether or not the homepage is
    visited first in the same browser context. The WebSocket search
    apparently needs to be *initiated* by a real in-page UI interaction, not
    by a page navigation that merely lands on a URL matching the SPA's own
    route shape.
  - The working, verified approach: load the homepage, click the "Recorded
    Date" button to reveal the date-range fields, fill the
    `[aria-label="Starting Recorded Date"]` / `[aria-label="Ending Recorded
    Date"]` inputs (MM/DD/YYYY), then click the "Search" button element
    (there are multiple elements matching text "Search" on the page,
    including an a11y live-region div — you must select the actual
    `<button>` with exact inner_text "Search", scroll it into view, and use
    `force=True` on the click; a plain `page.click("text=Search")` resolves
    to the wrong element and hangs for the full 30s Playwright default
    timeout). This reliably returns results in ~3-5 seconds.
  - Known indexing lag: on 2026-08-22, a search through 08/22/2026 was
    silently capped by the portal at 08/19/2026 — the county's recording
    index appears to run ~3 days behind real-world recording dates. The
    daily refresh window must be wide enough to catch this lag (see
    `days_back` default below) rather than assuming yesterday's filings are
    already indexed today.
  - Pagination: confirmed via `[aria-label="next page"]` / `[aria-label="page
    N"]` controls, not a query-string offset. Do not construct `&offset=`
    URLs by hand for the same reason raw /results URLs are unreliable —
    click the actual "next page" control.
  - This adapter drives the SPA with Playwright and scrapes the rendered
    `<table>` rather than reverse-engineering the WebSocket frame protocol.
    That matches this framework's existing precedent (register_shelby.py)
    and survives internal API/protocol churn better than a hand-rolled WS
    client.

robots.txt note: dallas.tx.publicsearch.us/robots.txt disallows crawling
everything except the bare root path. This adapter respects that signal by
behaving like a single interactive user session (one browser context, no
concurrency, small page counts, real UA) rather than a bulk crawler — the
same posture the framework's other Playwright-based adapters take on portals
with no public bulk-export API. It does not touch the disallowed paths for
indexing purposes, only for the operator's own daily distress-event pull.

Result columns exposed by this portal for the Real Property (RP) department:
  GRANTOR | GRANTEE | DOC TYPE | RECORDED DATE | DOC NUMBER |
  BOOK/VOLUME/PAGE | TOWN | LEGAL DESCRIPTION

No situs street address is exposed at the index-search level (this is a
grantor/grantee recording index, not a parcel/property index) — `address`
and `zip` are honestly left null in raw_payload; this is a known, expected
limitation of recorder-index sources, not a scraper defect.

Source scope — this adapter produces `clerk_recordings` ONLY (v2, corrected
2026-08-22). The original v1 design assumed `foreclosure_notices` was a
subset of this RP feed, classified by `doc_type`. That assumption was wrong:
live verification found foreclosures are NOT filed under the RP department
on this portal at all — Dallas County Clerk exposes a genuinely separate
top-level document category, "Foreclosures" (department code `FC`), with
its own distinct result schema (DOC TYPE | RECORDED DATE | SALE DATE | DOC
NUMBER | PROPERTY ADDRESS — the last of which is actually city-only, same
limitation as here). See `publicsearch_foreclosures_dallas.py` for that
adapter. Do not reintroduce doc-type-based foreclosure classification here;
an RP-department search will structurally never return a foreclosure
notice.

document_body_text / situs_address_ocr_hint (added 2026-08-30): this index
never exposed a debtor address (see the address note above) -- but clerk_
recordings' distress doc types (liens, judgments, heirship) are OCR-viable
in a way an address-only fix can't reach, because the underlying RECORDED
DOCUMENT itself often states a real service address directly, independent
of whether the party currently owns property in Dallas County (confirmed
live 2026-08-30: an Abstract of Judgment's own "1. Defendant/Judgment
Debtor: ... / Address: 2021 Lamar Boulevard, Arlington, TX 76006" block --
note that's Tarrant County, not Dallas, which is exactly why a Dallas-only
DCAD property lookup can never find this person, but the document already
told us where they are -- and the debtor's name is ALREADY known with 100%
confidence from the index's own grantor/grantee field, no OCR needed for
that part; see translate.py's role-tagging fix). This adapter now clicks
into each row whose doc_type is a known distress type (see
DISTRESS_DOC_TYPES -- everything else is skipped, this portal returns
thousands of rows/week and OCRing ordinary deeds would be enormously
wasteful for zero benefit), OCRs the page-1 image the same way
publicsearch_foreclosures_dallas.py does, and extracts a candidate address
via _extract_address_near_name: anchor on the already-known debtor name
(the "Address:" LABEL text itself is not reliably OCR'd on this document
family's table-like layout, confirmed live -- but the name and the address
VALUE that follows it both come through fine) and take the nearest
address-shaped text after it. This is a best-effort candidate, not the
authoritative parse the shared debtor_party_engine performs on names --
translate.py only accepts it when it independently looks like a real
street address (see its own validation), same caution as every other OCR-
sourced field in this county's pipeline.

Requires: pip install playwright pytesseract opencv-python-headless numpy
  && playwright install chromium, and the Tesseract OCR engine itself (e.g.
  `winget install UB-Mannheim.TesseractOCR` on Windows).
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

_PLAYWRIGHT_INSTALL_MSG = (
    "playwright not installed. Run: pip install playwright && playwright install chromium"
)

# OCR is optional-at-import: a missing pytesseract/Tesseract must degrade to
# document_body_text=None, not crash the scraper. Same posture as
# publicsearch_foreclosures_dallas.py, which this mirrors (not shares code
# with, to avoid destabilizing that already-working adapter this late).
try:
    import pytesseract
    from PIL import Image
    OCR_LIBS_AVAILABLE = True
except ImportError:
    OCR_LIBS_AVAILABLE = False

TESSERACT_CMD_CANDIDATES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


def _configure_tesseract() -> bool:
    if not OCR_LIBS_AVAILABLE:
        return False
    import shutil
    if shutil.which("tesseract"):
        return True
    for candidate in TESSERACT_CMD_CANDIDATES:
        if Path(candidate).exists():
            pytesseract.pytesseract.tesseract_cmd = candidate
            return True
    return False


OCR_AVAILABLE = _configure_tesseract()

# Same doc-type set translate.py's _CLERK_DOC_TYPE_MAP treats as distress-
# relevant (kept as raw index strings here, duplicated rather than imported,
# to keep this scraper's only dependency on translate.py's internals at
# zero -- translate.py already depends on this module's output shape, a
# cycle the other direction isn't worth introducing for one constant list).
# Deliberately excludes real deeds (quitclaim/executor's/administrator's) --
# those ARE in translate.py's map but are ordinary conveyances, not the
# "does this document itself state a service address" case this OCR pass
# is for.
DISTRESS_DOC_TYPES = {
    "AFFIDAVIT OF HEIRSHIP", "FEDERAL TAX LIEN", "STATE TAX LIEN",
    "MECHANIC'S LIEN", "MECHANICS LIEN", "CONSTRUCTION LIEN", "JUDGMENT LIEN",
    "MUNICIPAL LIEN", "ABSTRACT OF JUDGMENT", "LIS PENDENS",
    "LETTERS TESTAMENTARY", "LETTERS OF ADMINISTRATION", "MUNIMENT OF TITLE",
    "DETERMINATION OF HEIRSHIP", "PARTITION ACTION", "WRIT OF POSSESSION",
    "FINAL DECREE OF DIVORCE", "DIVORCE DECREE", "MARITAL PROPERTY DIVISION",
    "CODE VIOLATION", "NOTICE OF VIOLATION", "DEMOLITION ORDER", "CONDEMNATION",
}

PAGE1_IMAGE_URL_RE = re.compile(r"/files/documents/\d+/images/\d+_1\.png")
_DOCUMENT_IMAGE_WAIT_MS = 8_000

# Matches the DCAD address-search suffix list's spirit but inline here to
# keep this module's only cross-file dependency at zero (see
# DISTRESS_DOC_TYPES comment above for the same reasoning).
_STREET_TYPE_WORDS = (
    "STREET", "ST", "DRIVE", "DR", "AVENUE", "AVE", "BOULEVARD", "BLVD",
    "LANE", "LN", "ROAD", "RD", "COURT", "CT", "CIRCLE", "CIR", "PLACE",
    "PL", "WAY", "TRAIL", "TRL", "PARKWAY", "PKWY", "LOOP", "TERRACE",
    "TER", "HIGHWAY", "HWY",
)
_ADDRESS_SHAPE_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.\-'\s]{2,40}\b(?:" + "|".join(_STREET_TYPE_WORDS) + r")\b",
    re.IGNORECASE,
)

# Legal/financial boilerplate that happens to end in a real street-type word
# -- confirmed live 2026-08-30: "58 cost of court" matched _ADDRESS_SHAPE_RE
# (COURT is both a legitimate street suffix and an ordinary English word)
# from an Abstract of Judgment's damages recital, not an address at all. If
# EVERY word between the number and the suffix is one of these, it's not a
# real street name.
_ADDRESS_BOILERPLATE_STOPWORDS = {
    "COST", "COSTS", "FEE", "FEES", "AMOUNT", "JUDGMENT", "INTEREST",
    "DAMAGES", "AWARD", "PRINCIPAL", "PAGE", "OF", "THE", "AND", "PER",
}


def _looks_like_boilerplate_not_address(matched_text: str) -> bool:
    words = re.findall(r"[A-Za-z]+", matched_text)[:-1]  # drop the suffix word itself
    return bool(words) and all(w.upper() in _ADDRESS_BOILERPLATE_STOPWORDS for w in words)


# Doc-type family -> which index field (grantor/grantee) is the actual
# debtor, matching translate.py's _JUDGMENT_FAMILY_DOC_TYPES / _TAX_LIEN_
# FAMILY_DOC_TYPES role-tagging exactly (see that module for the live-data
# verification behind each direction) -- needed here so the address
# extractor knows which of the two names in a multi-party document (e.g. a
# judgment's plaintiff AND defendant, each with their own address block) to
# anchor on. Duplicated rather than imported for the same reason as
# DISTRESS_DOC_TYPES above.
_GRANTOR_IS_DEBTOR_DOC_TYPES = {
    "STATE TAX LIEN", "FEDERAL TAX LIEN", "MUNICIPAL LIEN",
    "MECHANIC'S LIEN", "MECHANICS LIEN", "CONSTRUCTION LIEN",
}

_NAME_TOKEN_STOPWORDS = {
    "LLC", "INC", "LTD", "CORP", "CO", "COMPANY", "LP", "PLLC", "PA",
    "TRUST", "THE", "AND", "OF",
}


def _debtor_name_for_row(row: dict) -> "str | None":
    field = "grantor" if (row.get("doc_type") or "").strip().upper() in _GRANTOR_IS_DEBTOR_DOC_TYPES else "grantee"
    return row.get(field)


def _extract_address_near_name(text: str, debtor_name: "str | None") -> "str | None":
    """Find a street-address-shaped line in OCR'd document text, anchored
    near the already-known debtor name (from the index's own grantor/
    grantee field -- clerk_recordings' names are already 100% resolved
    without needing OCR at all, see translate.py's role-tagging fix; OCR
    here is purely to find WHERE that already-identified party lives).

    Anchoring on the name rather than an "Address:" label because the
    label itself is not reliably OCR'd -- confirmed live 2026-08-30 on a
    real Abstract of Judgment: the rendered page clearly showed "Address:"
    next to each party, but Tesseract's default reading order on this
    table-like layout dropped both label words entirely, leaving only the
    address VALUES in the OCR text (in party order, still). The debtor
    name is far more OCR-robust than one two-word label, and multi-party
    documents (a judgment's plaintiff vs defendant, each with their own
    address) make picking the RIGHT address important, not just picking
    the first one.

    Returns the address-shaped text nearest after a recognizable token of
    debtor_name, or None if no name anchor is found or nothing shaped like
    a real address follows it within a reasonable window.
    """
    if not text or not debtor_name:
        return None

    tokens = sorted(
        (t for t in re.split(r"[^A-Za-z0-9]+", debtor_name.upper()) if len(t) >= 3 and t not in _NAME_TOKEN_STOPWORDS),
        key=len, reverse=True,
    )
    if not tokens:
        return None

    def _clean(raw: str) -> "str | None":
        candidate = re.sub(r"\s+", " ", raw).strip().strip(".,;: ")
        return candidate if _ADDRESS_SHAPE_RE.search(candidate) else None

    # Try the most distinctive (longest) token first -- least likely to
    # accidentally anchor on the wrong party or an unrelated word.
    for token in tokens:
        for m in re.finditer(re.escape(token), text, re.IGNORECASE):
            window = text[m.end():m.end() + 400]
            for addr_m in _ADDRESS_SHAPE_RE.finditer(window):
                if _looks_like_boilerplate_not_address(addr_m.group(0)):
                    continue  # e.g. "58 cost of court" -- try the next match in this window
                # Extend to the end of that line/clause for the full street+
                # city+state+zip, not just the "NNN Street Name" the shape
                # regex itself matches.
                line_end = re.search(r"[\n;]|$", window[addr_m.start():])
                raw_line = window[addr_m.start():addr_m.start() + (line_end.start() if line_end else 80)]
                cleaned = _clean(raw_line)
                if cleaned:
                    return cleaned
    return None


REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PORTAL_HOST = "dallas.tx.publicsearch.us"
PORTAL_URL = f"https://{PORTAL_HOST}/"
DEPARTMENT = "RP"
SOURCE_ID = "clerk_recordings"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Recorded-date search fields lag real-world filings by a few days on this
# portal (confirmed 2026-08-22: a search through "today" was silently capped
# ~3 days earlier). Default days_back is wide enough to still catch a filing
# that lands in the index late; merge_with_prior()'s dedup on raw_record_id
# makes re-pulling overlapping days harmless.
DEFAULT_DAYS_BACK = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _raw_record_id(doc_number: str) -> str:
    safe = (doc_number or "").strip().upper().replace(" ", "_")
    return f"dallas_rp_{safe}"


def _fmt_yyyymmdd(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


def _load_prior(path: Path) -> dict:
    if not path.exists():
        return {}
    out: dict = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = rec.get("raw_record_id")
            if rid:
                out[rid] = rec
    return out


def merge_with_prior(current: list[dict], prior: dict) -> list[dict]:
    now = _now_iso()
    merged: list[dict] = []
    seen_ids = set()
    for rec in current:
        rid = rec["raw_record_id"]
        seen_ids.add(rid)
        prev = prior.get(rid)
        if prev is None:
            rec["first_seen_at"] = now
            rec["last_seen_at"] = now
            rec["change_status"] = "NEW_RECORD"
        else:
            rec["first_seen_at"] = prev.get("first_seen_at", now)
            rec["last_seen_at"] = now
            rec["change_status"] = (
                "SAME" if prev.get("raw_payload") == rec["raw_payload"] else "UPDATED"
            )
        merged.append(rec)
    for rid, prev in prior.items():
        if rid not in seen_ids:
            prev["change_status"] = "DISAPPEARED"
            merged.append(prev)
    return merged


# ---------------------------------------------------------------------------
# Playwright: click-driven search + pagination (see module docstring — do
# NOT replace this with hand-built /results?... URL navigation, it hangs)
# ---------------------------------------------------------------------------

_POLL_INTERVAL_MS = 3_000
_MAX_POLLS = 12  # 36s ceiling waiting for one search to resolve


def _click_button_by_text(page, text: str, timeout_ms: int = 10_000) -> None:
    for bt in page.query_selector_all("button"):
        if bt.inner_text().strip() == text:
            bt.scroll_into_view_if_needed()
            bt.click(force=True, timeout=timeout_ms)
            return
    raise RuntimeError(f"Dallas RP: no <button> with exact text {text!r} found")


def _run_search(page, date_from: datetime, date_to: datetime, verbose: bool) -> str:
    """Drive the homepage UI to start a search. Returns final status string:
    'HasRows' | 'NoResults' | 'Error'. Raises on unexpected states."""
    # networkidle is unreliable on this SPA family - the FC adapter's
    # advanced-search page never reaches idle at all (confirmed live
    # 2026-08-28). Use domcontentloaded + an explicit selector wait instead.
    page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_selector("button:has-text('Recorded Date')", timeout=15_000)
    page.wait_for_timeout(1_000)
    _click_button_by_text(page, "Recorded Date")
    page.wait_for_timeout(1_000)
    page.fill('input[aria-label="Starting Recorded Date"]', date_from.strftime("%m/%d/%Y"))
    page.fill('input[aria-label="Ending Recorded Date"]', date_to.strftime("%m/%d/%Y"))
    _click_button_by_text(page, "Search")

    for i in range(_MAX_POLLS):
        page.wait_for_timeout(_POLL_INTERVAL_MS)
        body = page.inner_text("body")
        trs = page.query_selector_all("table tbody tr")
        if trs:
            if verbose:
                print(f"  [Dallas RP] search resolved after {(i + 1) * _POLL_INTERVAL_MS / 1000:.0f}s: {len(trs)} rows", flush=True)
            return "HasRows"
        if "No Results Found" in body:
            return "NoResults"
        if "Error While Running Search" in body:
            return "Error"
    return "Error"


def _fetch_and_ocr_row_document(page, context, row_index: int, verbose: bool) -> "tuple[str | None, str | None]":
    """Click into row_index's detail view, capture + OCR its page-1 document
    image, navigate back. Mirrors publicsearch_foreclosures_dallas.py's
    function of the same name (see that module for the network-capture/
    authenticated-fetch reasoning) -- returns (document_body_text,
    detail_url); either may be None.

    detail_url (added 2026-08-30): a real, permanent, publicly-loadable URL
    (confirmed live: a plain unauthenticated GET returns 200) -- every
    wrapped record's source_url was previously a fake "about:blank/..."
    placeholder, meaning the dashboard's "source" link did nothing for
    clerk_recordings leads. See the FC adapter's version of this function
    for the fuller explanation (same portal, same fix).
    """
    trs = page.query_selector_all("table tbody tr")
    if row_index >= len(trs):
        return None, None

    captured: dict = {}

    def _on_response(resp):
        if "url" not in captured and PAGE1_IMAGE_URL_RE.search(resp.url):
            captured["url"] = resp.url

    page.on("response", _on_response)
    try:
        trs[row_index].click()
        waited = 0
        while "url" not in captured and waited < _DOCUMENT_IMAGE_WAIT_MS:
            page.wait_for_timeout(500)
            waited += 500
    finally:
        page.remove_listener("response", _on_response)

    detail_url = page.url if "/doc/" in page.url else None

    text = None
    if "url" in captured:
        try:
            resp = context.request.get(captured["url"])
            if resp.ok:
                text = pytesseract.image_to_string(Image.open(io.BytesIO(resp.body()))).strip() or None
        except Exception as exc:
            if verbose:
                print(f"  [Dallas RP] OCR fetch/parse failed: {exc}", flush=True)
    elif verbose:
        print(f"  [Dallas RP] no page-1 image response observed within {_DOCUMENT_IMAGE_WAIT_MS}ms", flush=True)

    page.go_back()
    page.wait_for_timeout(1_500)
    return text, detail_url


def _scrape_current_table_page(page, context=None, do_ocr: bool = False, verbose: bool = False) -> list[dict]:
    """Scrape the visible results table. When do_ocr is True, also clicks
    into each row whose doc_type is in DISTRESS_DOC_TYPES to OCR its page-1
    document image and extract a candidate debtor address (see module
    docstring) -- every other row (the large majority: ordinary deeds,
    releases, etc.) is left alone, OCRing them would be pure waste.

    Row text is read by DOM position, re-querying `table tbody tr` fresh
    every iteration rather than a single snapshot -- see
    publicsearch_foreclosures_dallas.py's _scrape_current_table_page for why
    (a malformed-row skip or a click-triggered SPA re-render can both
    invalidate a stale snapshot's element handles / index alignment).
    """
    rows_out: list[dict] = []
    tr_index = 0
    while True:
        trs = page.query_selector_all("table tbody tr")
        if tr_index >= len(trs):
            break
        tr = trs[tr_index]
        texts = [c.inner_text().strip() for c in tr.query_selector_all("td")]
        if len(texts) < 11:
            tr_index += 1
            continue
        grantor, grantee, doc_type, recorded_date, doc_number = texts[3:8]
        book_volume_page, town, legal_description = texts[8:11]
        if not doc_number:
            tr_index += 1
            continue

        row = {
            "grantor": grantor or None,
            "grantee": grantee or None,
            "doc_type": doc_type or None,
            "recorded_date": recorded_date or None,
            "doc_number": doc_number,
            "book_volume_page": book_volume_page or None,
            "town": town or None,
            "legal_description": legal_description or None,
            "document_body_text": None,
            "situs_address_ocr_hint": None,
            "detail_url": None,
        }

        if do_ocr and (doc_type or "").strip().upper() in DISTRESS_DOC_TYPES:
            expected_count = len(trs)
            row["document_body_text"], row["detail_url"] = _fetch_and_ocr_row_document(page, context, tr_index, verbose)
            row["situs_address_ocr_hint"] = _extract_address_near_name(
                row["document_body_text"] or "", _debtor_name_for_row(row)
            )
            if verbose:
                got = "captured" if row["document_body_text"] else "none"
                addr = row["situs_address_ocr_hint"] or "-"
                print(f"    [Dallas RP] doc {doc_number} ({doc_type}): document body {got}, address hint: {addr}", flush=True)
            # Poll rather than a single immediate snapshot -- confirmed live
            # 2026-08-29 on the FC adapter that a fixed post-go_back wait
            # false-positives this check after a handful of rows, silently
            # aborting OCR for the rest of the page every run.
            trs_now = page.query_selector_all("table tbody tr")
            recheck_waited = 0
            while len(trs_now) != expected_count and recheck_waited < 6_000:
                page.wait_for_timeout(500)
                recheck_waited += 500
                trs_now = page.query_selector_all("table tbody tr")
            if len(trs_now) != expected_count:
                if verbose:
                    print(f"  [Dallas RP] results table row count still wrong after "
                          f"{recheck_waited}ms extra wait ({len(trs_now)} != {expected_count}) "
                          f"— stopping document body capture for remaining rows on this page", flush=True)
                do_ocr = False

        rows_out.append(row)
        tr_index += 1
    return rows_out


def _goto_next_page(page, verbose: bool = False) -> bool:
    """Click the 'next page' pagination control. Returns False if absent/disabled
    or if the click itself fails (e.g. a transient SPA re-render stall) - a
    pagination failure should stop pagination, not discard the pages already
    scraped (see run_scraper's caller, which used to lose them)."""
    btn = page.query_selector('[aria-label="next page"]')
    if btn is None:
        return False
    disabled = btn.get_attribute("disabled") or btn.get_attribute("aria-disabled")
    if disabled in ("true", ""):
        return False
    btn.scroll_into_view_if_needed()
    try:
        btn.click(force=True, timeout=10_000)
    except Exception as exc:
        if verbose:
            print(f"  [Dallas RP] next-page click failed, stopping pagination here: {exc}", flush=True)
        return False
    page.wait_for_timeout(2_500)
    return True


def _scrape_window(
    page,
    context,
    date_from: datetime,
    date_to: datetime,
    limit: int,
    max_pages: int,
    verbose: bool,
    fetch_document_body: bool = True,
) -> list[dict]:
    status = _run_search(page, date_from, date_to, verbose)
    if status == "NoResults":
        if verbose:
            print("  [Dallas RP] no results for this window", flush=True)
        return []
    if status == "Error":
        raise RuntimeError(
            "Dallas RP: search did not resolve (portal returned an error or "
            "never left 'Loading Results...')"
        )

    do_ocr = fetch_document_body and OCR_AVAILABLE
    if fetch_document_body and not OCR_AVAILABLE and verbose:
        print("  [Dallas RP] OCR requested but pytesseract/Tesseract not available "
              "— document_body_text will be None for all rows", flush=True)

    rows_out: list[dict] = []
    for page_num in range(max_pages):
        page_rows = _scrape_current_table_page(page, context=context, do_ocr=do_ocr, verbose=verbose)
        if verbose:
            print(f"  [Dallas RP] page {page_num}: {len(page_rows)} rows", flush=True)
        rows_out.extend(page_rows)
        if len(page_rows) < limit:
            break
        if not _goto_next_page(page, verbose):
            break
    return rows_out


def _to_wrapped_records(scraped_rows: list[dict]) -> list[dict]:
    """Wrap scraped RP rows per the framework's raw-record contract. Every
    row here is clerk_recordings — foreclosures are a structurally separate
    department (see publicsearch_foreclosures_dallas.py), never a subset of
    this feed."""
    now = _now_iso()
    out: list[dict] = []

    for row in scraped_rows:
        doc_number = row["doc_number"]
        recorded_date = row["recorded_date"]
        recording_year = recording_month = None
        if recorded_date:
            m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", recorded_date)
            if m:
                recording_month, _day, recording_year = (
                    int(m.group(1)), int(m.group(2)), int(m.group(3))
                )

        raw_payload = {
            "address": None,
            "doc_number": doc_number,
            "recording_year": recording_year,
            "recording_month": recording_month,
            "city": (row["town"] or "").upper() or None,
            "zip": None,
            "doc_type": row["doc_type"],
            "grantor_name": row["grantor"],
            "grantee_name": row["grantee"],
            "book_volume_page": row["book_volume_page"],
            "legal_description": row["legal_description"],
            "recorded_date_raw": recorded_date,
            "document_body_text": row.get("document_body_text"),
            "situs_address_ocr_hint": row.get("situs_address_ocr_hint"),
        }

        out.append({
            "raw_record_id": _raw_record_id(doc_number),
            "source_id": SOURCE_ID,
            # Real, permanent, publicly-loadable when OCR ran for this row
            # (distress doc types only — see _fetch_and_ocr_row_document's
            # detail_url note); falls back to the old placeholder for
            # ordinary (non-distress) rows OCR never clicks into.
            "source_url": row.get("detail_url") or f"about:blank/{SOURCE_ID}/{doc_number}",
            "source_fetched_at": now,
            "parser_confidence": 100,
            "raw_payload": raw_payload,
        })

    return out


def _dedup(records: list[dict]) -> list[dict]:
    seen: set = set()
    out: list[dict] = []
    for rec in records:
        rid = rec["raw_record_id"]
        if rid not in seen:
            seen.add(rid)
            out.append(rec)
    return out


def _write_jsonl(records: list[dict], output_path: Path, prior_path: Path) -> dict:
    records = _dedup(records)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prior = _load_prior(prior_path)
    merged = merge_with_prior(records, prior)

    tmp = output_path.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for rec in merged:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(output_path)

    return {
        "output_path": str(output_path),
        "records_pulled": len(records),
        "prior_count": len(prior),
        "total_after_merge": len(merged),
        "new_record_count": sum(1 for r in merged if r["change_status"] == "NEW_RECORD"),
        "same_record_count": sum(1 for r in merged if r["change_status"] == "SAME"),
        "updated_record_count": sum(1 for r in merged if r["change_status"] == "UPDATED"),
        "disappeared_record_count": sum(1 for r in merged if r["change_status"] == "DISAPPEARED"),
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_scraper(
    out_dir: Path,
    days_back: int = DEFAULT_DAYS_BACK,
    limit_per_page: int = 50,
    max_pages: int = 20,
    headless: bool = True,
    verbose: bool = True,
    fetch_document_body: bool = True,
) -> dict:
    """limit_per_page must match the portal's actual results-per-page (50) —
    it is used only to detect the last page (page_rows < limit_per_page), not
    to request a page size; the portal doesn't expose that as a parameter.

    fetch_document_body: when True (default) and Tesseract/pytesseract are
    available, OCRs the page-1 document image for every row whose doc_type
    is in DISTRESS_DOC_TYPES (see module docstring) to extract a candidate
    debtor address. This department returns far more rows/run than
    foreclosure_notices, most of them ordinary deeds this adapter already
    skips for OCR purposes -- only the distress subset gets the extra
    per-row click+capture+OCR cost.
    """
    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(_PLAYWRIGHT_INSTALL_MSG)

    date_to = datetime.now(timezone.utc)
    date_from = date_to - timedelta(days=days_back)

    errors: list[str] = []
    scraped_rows: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        try:
            scraped_rows = _scrape_window(
                page, context, date_from, date_to, limit_per_page, max_pages, verbose,
                fetch_document_body=fetch_document_body,
            )
        except Exception as exc:
            errors.append(f"scrape_error: {exc}")
            if verbose:
                print(f"  [Dallas RP] ERROR: {exc}", flush=True)
        browser.close()

    clerk_recs = _to_wrapped_records(scraped_rows)

    out_dir.mkdir(parents=True, exist_ok=True)
    clerk_path = out_dir / "clerk_recordings.jsonl"
    clerk_stats = _write_jsonl(clerk_recs, clerk_path, clerk_path)

    return {
        "portal_url": PORTAL_URL,
        "department": DEPARTMENT,
        "date_from": _fmt_yyyymmdd(date_from),
        "date_to": _fmt_yyyymmdd(date_to),
        "rows_scraped": len(scraped_rows),
        "clerk_recordings": clerk_stats,
        "errors": errors,
        "ocr_available": OCR_AVAILABLE,
        "document_body_fetch_requested": fetch_document_body,
        "playwright_available": PLAYWRIGHT_AVAILABLE,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Dallas County Clerk PublicSearch (Kofile) Real Property recorder "
            "scraper. Writes clerk_recordings.jsonl only — foreclosure notices "
            "are a separate department on this portal, see "
            "publicsearch_foreclosures_dallas.py. Requires: pip install "
            "playwright && playwright install chromium."
        )
    )
    parser.add_argument("--days-back", type=int, default=DEFAULT_DAYS_BACK, metavar="N",
                         help=f"Calendar days back from now (default {DEFAULT_DAYS_BACK}, "
                              "wide enough to cover this portal's ~3-day recording-index lag; "
                              "dedup against prior runs makes the overlap harmless).")
    parser.add_argument("--limit-per-page", type=int, default=50, metavar="N",
                         help="Must match the portal's actual results-per-page (50). Used only "
                              "to detect the last page, not to request a page size.")
    parser.add_argument("--max-pages", type=int, default=20, metavar="N",
                         help="Safety cap on paginated requests per run.")
    parser.add_argument("--out-dir", default=None,
                         help="Output directory for the two JSONL files. Default: data/raw/")
    parser.add_argument("--no-headless", action="store_true")
    parser.add_argument("--no-document-body", action="store_true",
                         help="Skip the per-row detail-view OCR pass on distress doc types "
                              "(faster, but situs_address_ocr_hint stays None).")
    args = parser.parse_args()

    if not PLAYWRIGHT_AVAILABLE:
        print(f"ERROR: {_PLAYWRIGHT_INSTALL_MSG}", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir) if args.out_dir else REPO_ROOT / "data" / "raw"
    stats = run_scraper(
        out_dir,
        fetch_document_body=not args.no_document_body,
        days_back=args.days_back,
        limit_per_page=args.limit_per_page,
        max_pages=args.max_pages,
        headless=not args.no_headless,
    )
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
