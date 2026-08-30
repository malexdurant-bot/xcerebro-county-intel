"""
Dallas County Clerk — Foreclosure Notices search (PublicSearch/Kofile portal).

Portal: https://dallas.tx.publicsearch.us/
Vendor: Kofile "PublicSearch" — same vendor/portal as clerk_recordings
(publicsearch_recorder_dallas.py), but foreclosures are a structurally
separate top-level document category on this portal, not a doc-type filter
within Real Property recordings. Confirmed live 2026-08-22 via the county's
own instructions (dallascounty.org/government/county-clerk/recording/
foreclosures.php): "select the dropdown menu next to the search bar and
choose 'Foreclosure' from the list of search options."

Architecture (fingerprinted live 2026-08-22):
  - Reached via the Advanced Search screen (https://.../search/advanced),
    not the Quick Search homepage used by the recordings adapter. Advanced
    Search exposes a `#docTypes` combobox whose top-level category options
    include "Property Records", "Assumed Names", "Marriage", "Marks and
    Brands", "Commissioners Court Minutes", and "Foreclosures".
  - Selecting "Foreclosures" and searching routes internally (SPA client-side
    navigation, not a raw page load) to `/results?department=FC&...` and
    returns a DIFFERENT column schema than Real Property:
      DOC TYPE | RECORDED DATE | SALE DATE | DOC NUMBER | PROPERTY ADDRESS
    Sample confirmed live: "NOTICE OF FORECLOSURE", recorded 2/26/2026, sale
    date 04/07/2026, doc# 202600000007, "DALLAS".
  - IMPORTANT — "PROPERTY ADDRESS" is a misleading header: on the index/list
    view it is actually CITY ONLY (values observed: DALLAS, OTHER,
    CARROLLTON, DESOTO, MESQUITE, ...), never a street address. A full
    street address would require opening the individual document image.
    Do not treat this column as a real address in raw_payload.
  - Selecting the "Foreclosures" option in `#docTypes` is quirky: Playwright
    reports the `[role="option"]` elements as `is_visible() == False` even
    though the dropdown is open and functional (likely a virtualized/
    animated list). A normal `.click()` times out. The reliable approach is
    a native DOM click via `element.evaluate("el => el.click()")`.
  - Do NOT navigate directly to a hand-built `/results?department=FC&...`
    URL — same failure mode documented in publicsearch_recorder_dallas.py
    (the SPA hangs on "Loading Results..." then the backend times out) when
    the search isn't initiated through real in-page interaction.
  - Date filtering: after selecting "Foreclosures", the Advanced Search form
    exposes both `#recordedDateRange-start` / `#recordedDateRange-end`
    (when the notice was filed with the Clerk) and `#instrumentDateRange-start`
    / `#instrumentDateRange-end` (the scheduled sale date). This adapter
    filters on recordedDateRange for daily-refresh "what's newly posted"
    semantics, matching clerk_recordings' pattern — sale_date is preserved
    per-row as enrichment on the lead, not used as the incremental cursor.
  - Pagination: same `[aria-label="next page"]` control as the recordings
    adapter.

robots.txt note: dallas.tx.publicsearch.us/robots.txt disallows crawling
everything except the bare root path. This adapter behaves like a single
interactive user session (one browser context, no concurrency, small page
counts, real UA) rather than a bulk crawler, consistent with the framework's
other Playwright-based adapters on portals with no public bulk-export API.

Document body OCR (added 2026-08-28): the FC index/detail views expose no
grantor/mortgagor name anywhere in the DOM or an API — the document detail
page's own "Parties" panel reports "No parties found" for these notices.
The debtor identity only exists as text baked into the recorded document's
page-1 image (confirmed live: these are typed legal documents, not
handwriting, so OCR is viable). For each row, after the results table is
scraped, this adapter additionally clicks into that row's detail view,
captures the page-1 document image (the URL is only obtainable by observing
the network response after navigating there — it's a signed URL keyed by an
internal document id with no relation to the public doc_number, and doc_number
itself is not globally unique: the same number can independently exist under
a different department, e.g. RP), fetches the raw image bytes through the
authenticated browser context (a plain unauthenticated request 401s), OCRs it
with Tesseract, and stores the result as raw_payload.document_body_text for
translate.py to pass through to the debtor-resolution engine. Falls back to
None (same as before) if Tesseract/pytesseract aren't installed or a given
capture fails — never blocks or fails the run. Only page 1 is captured (both
manually-verified samples had the debtor label there); some multi-page
documents may state it later and will route to REVIEW_REQUIRED like before.

debtor_name_ocr_hint (added 2026-08-28, same rollout): every free-preview
document image on this portal carries a diagonal "Unofficial Copy" watermark
stamp, and where its ink physically overlaps the debtor name's ink, that's
unrecoverable — a shared black pixel carries no trace of which stroke it
came from. This field is a best-effort SECOND OCR pass (crop to the
debtor-label row + morphological erosion/watershed to separate touching
watermark strokes from text — see _ocr_watermark_cleaned_hint) that often
recovers characters the primary pass loses entirely, but still isn't
reliably correct at the character level. It is NOT used for extraction (the
shared debtor_party_engine never sees it) — run_pipeline.py writes it to a
separate side file for a human reviewer to consult on REVIEW_REQUIRED leads,
never into a schema-validated lead record. Requires cv2 (opencv-python-
headless) + numpy in addition to the OCR deps below; degrades to None
independently of document_body_text if unavailable.

Requires: pip install playwright pytesseract opencv-python-headless numpy
  && playwright install chromium, and the Tesseract OCR engine itself (e.g.
  `winget install UB-Mannheim.TesseractOCR` on Windows) — see
  TESSERACT_CMD_CANDIDATES below.
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
# document_body_text=None (same as before this feature existed), not crash
# the scraper — clerk_recordings/tax_collector/sheriff_sales/etc. don't need it.
try:
    import pytesseract
    from PIL import Image
    OCR_LIBS_AVAILABLE = True
except ImportError:
    OCR_LIBS_AVAILABLE = False

# Optional second pass: watermark-cleanup on the debtor-label row (see
# _ocr_watermark_cleaned_hint). cv2/numpy are heavier deps than pytesseract/
# Pillow, so this degrades independently — a missing cv2 just means no hint
# text, not a broken scrape.
try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

# winget's UB-Mannheim.TesseractOCR package (the one this adapter was built
# against) installs here and does not add itself to PATH.
TESSERACT_CMD_CANDIDATES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


def _configure_tesseract() -> bool:
    """Returns True if a usable tesseract binary is findable. Prefers PATH;
    falls back to the well-known Windows install locations."""
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

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PORTAL_HOST = "dallas.tx.publicsearch.us"
ADVANCED_SEARCH_URL = f"https://{PORTAL_HOST}/search/advanced"
DEPARTMENT = "FC"
SOURCE_ID = "foreclosure_notices"

# Matches the page-1 document image response, e.g.
# /files/documents/330336158/images/312020617_1.png?exp=...&sig=...
# The numeric ids are internal (unrelated to the public doc_number) and only
# observable by watching network responses after navigating to the detail view.
PAGE1_IMAGE_URL_RE = re.compile(r"/files/documents/\d+/images/\d+_1\.png")
_DOCUMENT_IMAGE_WAIT_MS = 8_000

# Debtor-label words this template uses (mirrors scaffold/pipeline/
# debtor_party_engine.py's foreclosure_notice/trustee_sale label set, plus
# TRUSTOR) — used only to locate the row to crop for the watermark-cleanup
# hint pass below, not for the actual extraction (that stays in the shared
# engine, on the plain document_body_text).
_DEBTOR_LABEL_WORDS = ("TRUSTOR", "MORTGAGOR", "GRANTOR", "DEBTOR", "BORROWER")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Same ~3-day recording-index lag observed on the RP department; assumed to
# apply here too until disproven by a longer production run.
DEFAULT_DAYS_BACK = 5

_POLL_INTERVAL_MS = 3_000
_MAX_POLLS = 12  # 36s ceiling waiting for one search to resolve


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
    return f"dallas_fc_{safe}"


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


def _click_button_by_text(page, text: str, timeout_ms: int = 10_000) -> None:
    for bt in page.query_selector_all("button"):
        if bt.inner_text().strip() == text:
            bt.scroll_into_view_if_needed()
            bt.click(force=True, timeout=timeout_ms)
            return
    raise RuntimeError(f"Dallas FC: no <button> with exact text {text!r} found")


def _select_foreclosures_doc_type(page) -> None:
    doc_types_input = page.query_selector("#docTypes")
    if doc_types_input is None:
        raise RuntimeError("Dallas FC: #docTypes combobox not found on Advanced Search page")
    doc_types_input.click()
    page.wait_for_timeout(800)
    for opt in page.query_selector_all('[role="option"]'):
        if opt.inner_text().strip() == "Foreclosures":
            # Normal .click() reports these options as not visible even
            # though the dropdown is open — native DOM click bypasses that.
            opt.evaluate("el => el.click()")
            page.wait_for_timeout(500)
            return
    raise RuntimeError('Dallas FC: "Foreclosures" option not found in #docTypes dropdown')


def _run_search(page, date_from: datetime, date_to: datetime, verbose: bool) -> str:
    """Drive the Advanced Search UI to start a Foreclosures search. Returns
    final status string: 'HasRows' | 'NoResults' | 'Error'."""
    # networkidle never fires on this page (the SPA holds a persistent
    # connection open indefinitely, confirmed live 2026-08-28 - a plain
    # networkidle goto reliably burns the full 30s timeout). Wait for the
    # DOM plus the #docTypes combobox we actually need instead.
    page.goto(ADVANCED_SEARCH_URL, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_selector("#docTypes", timeout=15_000)
    page.wait_for_timeout(1_000)
    _select_foreclosures_doc_type(page)

    start_inp = page.query_selector("#recordedDateRange-start")
    end_inp = page.query_selector("#recordedDateRange-end")
    if start_inp is None or end_inp is None:
        raise RuntimeError("Dallas FC: recordedDateRange inputs not found after selecting Foreclosures")
    start_inp.fill(date_from.strftime("%m/%d/%Y"))
    end_inp.fill(date_to.strftime("%m/%d/%Y"))

    _click_button_by_text(page, "Search")

    for i in range(_MAX_POLLS):
        page.wait_for_timeout(_POLL_INTERVAL_MS)
        body = page.inner_text("body")
        trs = page.query_selector_all("table tbody tr")
        if trs:
            if verbose:
                print(f"  [Dallas FC] search resolved after {(i + 1) * _POLL_INTERVAL_MS / 1000:.0f}s: {len(trs)} rows", flush=True)
            return "HasRows"
        if "No Results Found" in body:
            return "NoResults"
        if "Error While Running Search" in body:
            return "Error"
    return "Error"


def _scrape_current_table_page(page, context=None, do_ocr: bool = False, verbose: bool = False) -> list[dict]:
    """Scrape the visible results table. When do_ocr is True, also clicks into
    each row's detail view to OCR its page-1 document image (see
    _fetch_and_ocr_row_document) before moving to the next row.

    Row text is read by DOM position (tr index within <table tbody>), NOT by
    position within rows_out — a handful of rows can get skipped below (e.g.
    a malformed row with too few cells), and if OCR indexed into rows_out
    instead it would click the wrong physical row for everything after a
    skip. Re-querying `table tbody tr` fresh every iteration (rather than
    iterating a single snapshot) is also required: clicking into a detail
    view and back can cause the SPA to replace the table's DOM nodes, which
    would make a stale snapshot's element handles invalid on the next
    iteration.
    """
    rows_out: list[dict] = []
    tr_index = 0
    while True:
        trs = page.query_selector_all("table tbody tr")
        if tr_index >= len(trs):
            break
        tr = trs[tr_index]
        texts = [c.inner_text().strip() for c in tr.query_selector_all("td")]
        if len(texts) < 8:
            tr_index += 1
            continue
        doc_type, recorded_date, sale_date, doc_number, property_city = texts[3:8]
        if not doc_number:
            tr_index += 1
            continue

        row = {
            "doc_type": doc_type or None,
            "recorded_date": recorded_date or None,
            "sale_date": sale_date or None,
            "doc_number": doc_number,
            "property_city": property_city or None,
            "document_body_text": None,
            "debtor_name_ocr_hint": None,
            "detail_url": None,
        }

        if do_ocr:
            expected_count = len(trs)
            row["document_body_text"], row["debtor_name_ocr_hint"], row["detail_url"] = _fetch_and_ocr_row_document(
                page, context, tr_index, verbose
            )
            if verbose:
                got = "captured" if row["document_body_text"] else "none"
                print(f"    [Dallas FC] doc {doc_number}: document body {got}", flush=True)
            # Poll rather than a single immediate snapshot: _fetch_and_ocr_row_
            # document's fixed 1.5s post-go_back wait isn't always enough for
            # the SPA to finish re-rendering the full row count, especially
            # later in a long session with many navigations behind it -- a
            # single-snapshot check here was confirmed live 2026-08-29 to
            # false-positive after as few as 6 rows, silently aborting OCR
            # for the rest of the page every run. Give it up to 6s total
            # before concluding the table is genuinely broken (not just slow).
            trs_now = page.query_selector_all("table tbody tr")
            recheck_waited = 0
            while len(trs_now) != expected_count and recheck_waited < 6_000:
                page.wait_for_timeout(500)
                recheck_waited += 500
                trs_now = page.query_selector_all("table tbody tr")
            if len(trs_now) != expected_count:
                if verbose:
                    print(f"  [Dallas FC] results table row count still wrong after "
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
            print(f"  [Dallas FC] next-page click failed, stopping pagination here: {exc}", flush=True)
        return False
    page.wait_for_timeout(2_500)
    return True


def _ocr_watermark_cleaned_hint(image_bytes: bytes, verbose: bool = False) -> str | None:
    """Best-effort SECOND OCR pass, used only to produce a human-review hint
    — never fed into the actual owner_name extraction (see run_pipeline.py,
    which writes this to a separate side file, not into any schema-validated
    lead record).

    Context: Kofile stamps every free-preview document image with a diagonal
    "Unofficial Copy" watermark. Where the watermark's ink physically
    overlaps the debtor name's ink, that's fundamentally lost information —
    no algorithm recovers which stroke a shared black pixel belonged to. What
    IS recoverable: pixels where only one of (watermark, text) has ink. This
    does a rough watermark/text separation via morphological erosion +
    watershed reconstruction (thin diagonal watermark strokes get broken
    apart by erosion; compact letter-shaped blobs survive as seeds; each
    original touching region is then re-partitioned to its nearest seed) on
    just the row containing a recognized debtor label, then re-OCRs that
    cleaned crop. Still frequently garbled at the character level — that's
    exactly why it's a hint, not a resolution.
    """
    if not (OCR_LIBS_AVAILABLE and CV2_AVAILABLE):
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("L")
        w, h = img.size
        left = img.crop((0, 0, int(w * 0.52), h))  # left column only — see
        # module docstring: right column is a different field (Beneficiary/
        # Loan Servicer), bleeding it in was the original wrong-name bug.

        data = pytesseract.image_to_data(left, output_type=pytesseract.Output.DICT)
        label_y = None
        for word, top in zip(data["text"], data["top"]):
            if any(label in word.upper() for label in _DEBTOR_LABEL_WORDS):
                label_y = top
                break
        if label_y is None:
            return None

        strip = left.crop((0, max(0, label_y - 20), left.width, label_y + 120))
        strip_arr = np.array(strip)

        _, binary = cv2.threshold(strip_arr, 200, 255, cv2.THRESH_BINARY_INV)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        eroded = cv2.erode(binary, kernel, iterations=1)

        n, seed_labels, stats, _ = cv2.connectedComponentsWithStats(eroded, connectivity=8)
        text_seed_ids = set()
        for i in range(1, n):
            x, y, sw, sh, area = stats[i]
            if area < 3:
                continue
            density = area / (sw * sh)
            elongated = max(sw, sh) / max(1, min(sw, sh))
            # Thresholds fit empirically to this template's font size/weight
            # at the portal's ~300 DPI page-1 render — a compact, reasonably
            # dense, non-elongated blob is a letter/word; the watermark's
            # thin diagonal strokes and circular border fail at least one.
            if sh <= 60 and sw <= 120 and density >= 0.25 and elongated <= 6:
                text_seed_ids.add(i)
        if not text_seed_ids:
            return None

        markers = seed_labels.astype(np.int32) + 1
        unknown = cv2.subtract(binary, eroded)
        markers[unknown == 255] = 0
        color = cv2.cvtColor(strip_arr, cv2.COLOR_GRAY2BGR)
        cv2.watershed(color, markers)

        out = np.full_like(binary, 0)
        for seed_id in text_seed_ids:
            out[markers == (seed_id + 1)] = 255
            out[seed_labels == seed_id] = 255
        cleaned = 255 - out

        hint = pytesseract.image_to_string(cleaned).strip()
        return hint or None
    except Exception as exc:
        if verbose:
            print(f"  [Dallas FC] watermark-cleanup hint pass failed (non-fatal): {exc}", flush=True)
        return None


def _fetch_and_ocr_row_document(
    page, context, row_index: int, verbose: bool
) -> tuple[str | None, str | None, str | None]:
    """Click into row_index's detail view, capture + OCR its page-1 document
    image, then navigate back to the results table. Returns
    (document_body_text, debtor_name_ocr_hint, detail_url) — any may be None
    on failure; callers treat a None document_body_text identically to "this
    source has no document body", the pre-existing behavior.
    debtor_name_ocr_hint is purely a best-effort human-review aid (see
    _ocr_watermark_cleaned_hint) and is never used for extraction.

    detail_url (added 2026-08-30): the click navigates to a real, permanent,
    publicly-loadable URL (https://dallas.tx.publicsearch.us/doc/<internal
    id> -- confirmed live: a plain unauthenticated GET returns 200 with the
    actual document viewer). Every wrapped record's source_url was
    previously a fake "about:blank/..." placeholder because there's no way
    to construct this URL without actually clicking through the search UI
    (the internal id is unrelated to the public doc_number) -- meaning the
    dashboard's "source" link did nothing at all for these leads. Captured
    here since this is the only place that ever navigates there.
    """
    trs = page.query_selector_all("table tbody tr")
    if row_index >= len(trs):
        return None, None, None

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
    hint = None
    if "url" in captured:
        try:
            resp = context.request.get(captured["url"])
            if resp.ok:
                image_bytes = resp.body()
                text = pytesseract.image_to_string(Image.open(io.BytesIO(image_bytes))).strip() or None
                hint = _ocr_watermark_cleaned_hint(image_bytes, verbose=verbose)
        except Exception as exc:
            if verbose:
                print(f"  [Dallas FC] OCR fetch/parse failed: {exc}", flush=True)
    elif verbose:
        print(f"  [Dallas FC] no page-1 image response observed within {_DOCUMENT_IMAGE_WAIT_MS}ms", flush=True)

    page.go_back()
    page.wait_for_timeout(1_500)
    return text, hint, detail_url


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
            print("  [Dallas FC] no results for this window", flush=True)
        return []
    if status == "Error":
        raise RuntimeError(
            "Dallas FC: search did not resolve (portal returned an error or "
            "never left 'Loading Results...')"
        )

    do_ocr = fetch_document_body and OCR_AVAILABLE
    if fetch_document_body and not OCR_AVAILABLE and verbose:
        print("  [Dallas FC] OCR requested but pytesseract/Tesseract not available "
              "— document_body_text will be None for all rows", flush=True)

    rows_out: list[dict] = []
    for page_num in range(max_pages):
        page_rows = _scrape_current_table_page(page, context=context, do_ocr=do_ocr, verbose=verbose)
        if verbose:
            print(f"  [Dallas FC] page {page_num}: {len(page_rows)} rows", flush=True)

        rows_out.extend(page_rows)
        if len(page_rows) < limit:
            break
        if not _goto_next_page(page, verbose):
            break
    return rows_out


def _to_wrapped_records(scraped_rows: list[dict]) -> list[dict]:
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
            "city": (row["property_city"] or "").upper() or None,
            "zip": None,
            "doc_type": row["doc_type"],
            "sale_date_raw": row["sale_date"],
            "recorded_date_raw": recorded_date,
            "document_body_text": row.get("document_body_text"),
            # Best-effort watermark-cleanup re-OCR of the debtor-label row,
            # for human review only — see _ocr_watermark_cleaned_hint. Never
            # fed into owner_name extraction; run_pipeline.py surfaces it in
            # a separate side file, not in any schema-validated lead record.
            "debtor_name_ocr_hint": row.get("debtor_name_ocr_hint"),
        }

        out.append({
            "raw_record_id": _raw_record_id(doc_number),
            "source_id": SOURCE_ID,
            # Real, permanent, publicly-loadable when OCR ran (see
            # _fetch_and_ocr_row_document's detail_url note) — falls back to
            # the old placeholder only when OCR didn't run for this row
            # (--no-document-body, Tesseract missing, or capture failure).
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
    it is used only to detect the last page, not to request a page size.

    fetch_document_body: when True (default) and Tesseract/pytesseract are
    available, clicks into every row's detail view to OCR the page-1 document
    image for a debtor name (see module docstring). Roughly doubles the run's
    wall-clock time (one extra page load + image fetch + OCR pass per row).
    Set False to skip this and keep the old list-only-fields behavior.
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
                print(f"  [Dallas FC] ERROR: {exc}", flush=True)
        browser.close()

    records = _to_wrapped_records(scraped_rows)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "foreclosure_notices.jsonl"
    stats = _write_jsonl(records, out_path, out_path)

    return {
        "portal_url": ADVANCED_SEARCH_URL,
        "department": DEPARTMENT,
        "date_from": _fmt_yyyymmdd(date_from),
        "date_to": _fmt_yyyymmdd(date_to),
        "rows_scraped": len(scraped_rows),
        "foreclosure_notices": stats,
        "errors": errors,
        "playwright_available": PLAYWRIGHT_AVAILABLE,
        "ocr_available": OCR_AVAILABLE,
        "document_body_fetch_requested": fetch_document_body,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Dallas County Clerk PublicSearch (Kofile) Foreclosures scraper "
            "(department=FC, structurally separate from Real Property "
            "recordings). Requires: pip install playwright && "
            "playwright install chromium."
        )
    )
    parser.add_argument("--days-back", type=int, default=DEFAULT_DAYS_BACK, metavar="N",
                         help=f"Calendar days back from now for the recorded-date filter "
                              f"(default {DEFAULT_DAYS_BACK}).")
    parser.add_argument("--limit-per-page", type=int, default=50, metavar="N",
                         help="Must match the portal's actual results-per-page (50). Used only "
                              "to detect the last page, not to request a page size.")
    parser.add_argument("--max-pages", type=int, default=20, metavar="N",
                         help="Safety cap on paginated requests per run.")
    parser.add_argument("--out-dir", default=None,
                         help="Output directory for foreclosure_notices.jsonl. Default: data/raw/")
    parser.add_argument("--no-headless", action="store_true")
    parser.add_argument("--no-document-body", action="store_true",
                         help="Skip the per-row detail-view OCR pass (faster, but "
                              "document_body_text stays None and debtor names won't resolve).")
    args = parser.parse_args()

    if not PLAYWRIGHT_AVAILABLE:
        print(f"ERROR: {_PLAYWRIGHT_INSTALL_MSG}", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir) if args.out_dir else REPO_ROOT / "data" / "raw"
    stats = run_scraper(
        out_dir,
        days_back=args.days_back,
        limit_per_page=args.limit_per_page,
        max_pages=args.max_pages,
        headless=not args.no_headless,
        fetch_document_body=not args.no_document_body,
    )
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
