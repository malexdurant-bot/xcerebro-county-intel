"""
Shelby County Register of Deeds — document recording scraper.

Portal: https://search.register.shelby.tn.us/search/index.php

Architecture:
  - Main page has <iframe name="content_frame" src="content.php?...&random=XXXXXX">
  - The iframe holds the search form (form name/id = "davidson_form")
  - Form POSTs to content.php?random=XXXXXX with target="content_frame"
  - Results appear inside the same content_frame iframe (DataTables-based table)

Target document types:
  - APPT  : Appointment of Substitute Trustee (ASOT) — pre-foreclosure signal in TN
  - IRS   : Federal Tax Lien — federal tax distress signal
  - LIEN  : Judgment lien — creditor distress signal
  - NCTS  : Notice of County Tax Sale — county tax delinquency, near auction
  - TNTX  : State Tax Lien — TN Dept of Revenue lien against taxpayer
  - STR   : Sub Trustees Deed — completed foreclosure (post-sale deed)

Form key fields (all in content_frame):
  - start_date, end_date    : text, MM/DD/YYYY format
  - searchType              : hidden, value "davidson" (leave as-is)
  - show_pick_list          : hidden, value "on" (leave as-is)
  - party_type              : hidden, value "" (leave as-is)
  - instDocType[ALL]        : checkbox — uncheck to deselect all types
  - instDocType[$k][APPT]   : checkbox for Appointment of Substitute TR
  - instDocType[$k][IRS]    : checkbox for Federal Tax Lien
  - instDocType[$k][LIEN]   : checkbox for judgment lien

Search strategy:
  - Set date range, uncheck instDocType[ALL], then check only target doc types
  - Leave party name fields empty (date-range-only search)

SSL: Standard TLS — no legacy SSL fix needed for Playwright context.

Requires: pip install playwright && playwright install chromium
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Playwright import guard
# ---------------------------------------------------------------------------

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

_PLAYWRIGHT_INSTALL_MSG = (
    "playwright not installed. Run: pip install playwright && playwright install chromium"
)

# ---------------------------------------------------------------------------
# Repo bootstrap
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOURCE_ID = "register_shelby"

PORTAL_URL = "https://search.register.shelby.tn.us/search/index.php"
CONTENT_BASE = "https://search.register.shelby.tn.us/search/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# SSL context for probe mode urllib GET
# TN gov server may have cert chain issues — use lenient context for urllib
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_HEADERS_GET = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Document type checkbox keys
# Format in the form: instDocType[$k][CODE] where $k is an index counter
DOC_TYPE_KEYS = {
    "APPT": "APPT",   # Appointment of Substitute Trustee
    "IRS": "IRS",     # Federal Tax Lien
    "LIEN": "LIEN",   # Judgment Lien
    "NCTS": "NCTS",   # Notice of County Tax Sale
    "TNTX": "TNTX",   # State Tax Lien (TN Dept of Revenue)
    "STR": "STR",     # Sub Trustees Deed (completed foreclosure)
}

DEFAULT_DOC_TYPES = ["APPT", "IRS", "LIEN", "NCTS", "TNTX", "STR"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _raw_record_id(inst_num: str) -> str:
    safe = (inst_num or "").strip().upper().replace(" ", "_")
    return f"shelby_rod_{safe}"


def _format_date_rod(dt: datetime) -> str:
    """Format datetime as MM/DD/YYYY for the Register form."""
    return dt.strftime("%m/%d/%Y")


# ---------------------------------------------------------------------------
# Prior / merge
# ---------------------------------------------------------------------------


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


def merge_with_prior(current: list, prior_by_id: dict) -> list:
    out: list = []
    current_ids: set = set()
    for rec in current:
        rid = rec["raw_record_id"]
        current_ids.add(rid)
        prev = prior_by_id.get(rid)
        if prev is None:
            rec["change_status"] = "NEW_RECORD"
        else:
            rec["first_seen_at"] = prev.get("first_seen_at", rec["first_seen_at"])
            rec["last_seen_at"] = rec["source_fetched_at"]
            rec["change_status"] = (
                "SAME"
                if prev.get("raw_payload") == rec["raw_payload"]
                else "UPDATED"
            )
        out.append(rec)
    for rid, prev in prior_by_id.items():
        if rid in current_ids:
            continue
        prev = dict(prev)
        prev["change_status"] = "DISAPPEARED"
        out.append(prev)
    return out


# ---------------------------------------------------------------------------
# Probe helpers — urllib GET (no Playwright needed for initial field discovery)
# ---------------------------------------------------------------------------


def _get_url(url: str) -> str:
    req = urllib.request.Request(url, headers=_HEADERS_GET)
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_SSL_CTX)
    )
    with opener.open(req, timeout=30) as resp:
        return resp.read().decode(
            resp.headers.get_content_charset() or "utf-8", errors="replace"
        )


# ---------------------------------------------------------------------------
# Playwright: extract the iframe src URL (has the random param)
# ---------------------------------------------------------------------------


def _get_iframe_url(page) -> Optional[str]:
    """Extract the content_frame iframe src URL from the main page."""
    try:
        iframe_el = page.query_selector('iframe[name="content_frame"]')
        if iframe_el:
            src = iframe_el.get_attribute("src")
            if src:
                if src.startswith("http"):
                    return src
                return CONTENT_BASE + src
    except Exception:
        pass

    # Fallback: find any iframe
    for frame in page.frames:
        if frame.name == "content_frame" or "content" in (frame.name or "").lower():
            if frame.url and frame.url != "about:blank":
                return frame.url

    return None


def _wait_settled(page, timeout: int = 30_000) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Playwright: set form fields in the content_frame iframe
# ---------------------------------------------------------------------------


def _fill_search_form(
    content_frame,
    start_date: str,
    end_date: str,
    doc_types: list[str],
    verbose: bool,
) -> bool:
    """
    Fill the Register of Deeds search form inside the content_frame.
    Returns True on success.
    """
    try:
        # Wait for the form to be present
        content_frame.wait_for_selector(
            'form[name="davidson_form"], form[id="davidson_form"]',
            timeout=15_000,
        )
    except Exception as exc:
        if verbose:
            print(f"  [Register] WARNING: search form not found: {exc}", flush=True)
        return False

    # Set date fields
    try:
        start_el = content_frame.query_selector('[name="start_date"]')
        if start_el:
            start_el.fill("")
            start_el.type(start_date, delay=20)
        end_el = content_frame.query_selector('[name="end_date"]')
        if end_el:
            end_el.fill("")
            end_el.type(end_date, delay=20)
    except Exception as exc:
        if verbose:
            print(f"  [Register] WARNING: date fill error: {exc}", flush=True)

    # Uncheck "ALL" checkbox to deselect all doc types
    try:
        all_cb = content_frame.query_selector('[name="instDocType[ALL]"]')
        if all_cb and all_cb.is_checked():
            all_cb.uncheck()
            if verbose:
                print("  [Register] Unchecked instDocType[ALL]", flush=True)
    except Exception as exc:
        if verbose:
            print(f"  [Register] WARNING: uncheck ALL failed: {exc}", flush=True)

    # Check target doc type checkboxes
    # The form uses name patterns like instDocType[$k][APPT] where $k is an index
    # We try multiple strategies to find and check them
    for dt in doc_types:
        checked = False

        # Strategy 1: name contains "[CODE]" (bracket-wrapped) to avoid substring
        # collisions like IRS matching IRSA/IRSP/IRSR/IRSC/IRSSA/IRSW.
        try:
            cbs = content_frame.query_selector_all(
                f'input[type="checkbox"][name*="[{dt}]"]'
            )
            for cb in cbs:
                if not cb.is_checked():
                    cb.check()
                checked = True
                if verbose:
                    name_attr = cb.get_attribute("name") or dt
                    print(
                        f"  [Register] Checked doc type checkbox: {name_attr}", flush=True
                    )
        except Exception:
            pass

        if not checked:
            # Strategy 2: find by value attribute
            try:
                cbs = content_frame.query_selector_all(
                    f'input[type="checkbox"][value="{dt}"]'
                )
                for cb in cbs:
                    if not cb.is_checked():
                        cb.check()
                    checked = True
                    if verbose:
                        name_attr = cb.get_attribute("name") or dt
                        print(
                            f"  [Register] Checked doc type (by value): {name_attr}",
                            flush=True,
                        )
            except Exception:
                pass

        if not checked and verbose:
            print(
                f"  [Register] WARNING: could not find checkbox for doc type {dt!r}",
                flush=True,
            )

    return True


# ---------------------------------------------------------------------------
# Playwright: extract results from content_frame after search
# ---------------------------------------------------------------------------


def _extract_results(content_frame, verbose: bool) -> list[dict]:
    """
    Extract result rows from the DataTables results table (id="results") in content_frame.

    Confirmed column order from live portal inspection:
      0: Record Info (instrument number, e.g. "26055899")
      1: Rec. Date   (YYYY/MM/DD)
      2: Grantor
      3: Grantee
      4: Instrument Type (doc type code, e.g. "APPT", "QC")
      5: Prop. Description (address)
      6: Cross-References
      7: Consideration
      8: Image?
    """
    import time as _time
    now = _now_iso()
    records: list[dict] = []

    # Wait for the results table to appear (up to 15 s)
    try:
        content_frame.wait_for_selector(
            "table#results, table.dataTable", timeout=15_000
        )
    except Exception:
        pass

    # Check for "No records found"
    try:
        page_text = content_frame.inner_text("body") or ""
        if any(
            phrase in page_text.lower()
            for phrase in ("no records", "no results", "0 records")
        ):
            if verbose:
                print("  [Register] No records found for this query", flush=True)
            return []
    except Exception:
        pass

    # Extract rows from the confirmed results table.
    #
    # The portal's #results table is a CLIENT-SIDE DataTables instance
    # (serverSide: false, iDisplayLength: 100) — the full result set (which
    # can run into the hundreds for a wide date range) is already loaded into
    # the page; DataTables just detaches all but the current page's <tr>
    # elements from the visible <tbody>. Querying the DOM directly therefore
    # silently truncates to 100 rows. dt.rows().nodes() returns every row's
    # node regardless of pagination, at zero extra network cost, so pull
    # through the DataTables API when the table is one; fall back to a plain
    # DOM query for any results table that isn't (defensive, keeps this
    # working if the portal changes).
    try:
        rows_data = content_frame.evaluate("""
            () => {
                // Target the known results table; fall back to first dataTable
                const tbl = document.getElementById('results') ||
                            document.querySelector('table.dataTable');
                if (!tbl) return [];

                let rows;
                if (window.jQuery && jQuery.fn.dataTable &&
                    jQuery.fn.dataTable.isDataTable(tbl)) {
                    rows = jQuery(tbl).DataTable().rows().nodes().toArray();
                } else {
                    rows = Array.from(tbl.querySelectorAll('tbody tr'));
                }

                return rows.map(row => {
                    const cells = Array.from(row.querySelectorAll('td'));
                    const cellTexts = cells.map(
                        c => c.innerText.trim().replace(/\\s+/g, ' ')
                    );
                    const link = row.querySelector('a');
                    const href = link
                        ? new URL(
                            link.getAttribute('href') || '',
                            document.baseURI
                          ).href
                        : '';
                    return {cells: cellTexts, href};
                }).filter(r => r.cells.length >= 5);
            }
        """)
    except Exception as exc:
        if verbose:
            print(f"  [Register] JS extraction error: {exc}", flush=True)
        rows_data = []

    if verbose:
        print(
            f"  [Register] Raw rows extracted from DataTable: {len(rows_data)}", flush=True
        )

    # Confirmed column indices (0-based)
    # 0=inst_num, 1=rec_date(YYYY/MM/DD), 2=grantor, 3=grantee,
    # 4=doc_type, 5=prop_desc, 6=cross_refs, 7=consideration, 8=image
    for row in rows_data:
        cells = row.get("cells") or []
        href = (row.get("href") or "").strip()

        if not cells:
            continue

        inst_num = cells[0].strip() if len(cells) > 0 else None
        rec_date_raw = cells[1].strip() if len(cells) > 1 else None
        grantor = cells[2].strip() if len(cells) > 2 else None
        grantee = cells[3].strip() if len(cells) > 3 else None
        doc_type = cells[4].strip() if len(cells) > 4 else None
        prop_desc = cells[5].strip() if len(cells) > 5 else None
        cross_refs = cells[6].strip() if len(cells) > 6 else None
        consideration = cells[7].strip() if len(cells) > 7 else None

        # Convert date YYYY/MM/DD -> MM/DD/YYYY for consistency
        recording_date = None
        if rec_date_raw:
            m = re.match(r"^(\d{4})/(\d{2})/(\d{2})$", rec_date_raw)
            if m:
                recording_date = f"{m.group(2)}/{m.group(3)}/{m.group(1)}"
            else:
                recording_date = rec_date_raw

        if not inst_num or not re.match(r"^\d{5,12}$", inst_num):
            continue

        # Always use instrument URL — row hrefs can be JS calls or image links
        href = (
            f"https://search.register.shelby.tn.us/search/"
            f"content.php?inst_num={inst_num}"
        )

        confidence = 90 if (inst_num and recording_date and doc_type) else 70

        raw_payload: dict = {
            "instrument_number": inst_num,
            "recording_date": recording_date,
            "document_type": doc_type,
            "grantor": grantor,
            "grantee": grantee,
            "prop_description": prop_desc,
            "cross_references": cross_refs,
            "consideration": consideration or None,
            "all_cells": cells,
        }

        records.append({
            "raw_record_id": _raw_record_id(inst_num),
            "source_id": SOURCE_ID,
            "source_url": href,
            "source_fetched_at": now,
            "raw_payload": raw_payload,
            "raw_text": None,
            "first_seen_at": now,
            "last_seen_at": now,
            "change_status": "NEW_RECORD",
            "parser_confidence": confidence,
        })

    return records


# ---------------------------------------------------------------------------
# Playwright: main search function
# ---------------------------------------------------------------------------


def _pw_search(
    page,
    start_date: str,
    end_date: str,
    doc_types: list[str],
    max_records: Optional[int],
    verbose: bool,
) -> list[dict]:
    """
    Load the Register of Deeds portal, fill the search form in the iframe,
    submit, and extract results.
    """
    if verbose:
        print(
            f"  [Register] Loading portal: {PORTAL_URL}", flush=True
        )

    page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60_000)
    _wait_settled(page)

    # Get the content_frame
    content_frame = None
    for attempt in range(5):
        for frame in page.frames:
            if frame.name == "content_frame":
                content_frame = frame
                break
            # Also try by URL pattern
            if "content.php" in (frame.url or ""):
                content_frame = frame
                break
        if content_frame and content_frame.url != "about:blank":
            break
        import time as _time
        _time.sleep(0.5)

    if content_frame is None:
        if verbose:
            print(
                "  [Register] WARNING: content_frame not found. "
                f"Frames: {[(f.name, f.url) for f in page.frames]}",
                flush=True,
            )
        return []

    if verbose:
        print(
            f"  [Register] Found content_frame: {content_frame.url}", flush=True
        )

    # Wait for the form to be ready
    import time as _time
    _time.sleep(1)

    # Fill and submit the search form
    success = _fill_search_form(
        content_frame, start_date, end_date, doc_types, verbose
    )
    if not success:
        return []

    # Submit the form
    if verbose:
        print("  [Register] Submitting search form...", flush=True)

    try:
        # The Register portal uses a JS link (submitSearch) — no native submit button
        search_link = content_frame.query_selector('a[onclick*="submitSearch"]')
        if search_link:
            search_link.click()
        else:
            # Fallback: direct JS form submit
            content_frame.evaluate(
                "document.getElementById('davidson_form').submit()"
            )
    except Exception as exc:
        if verbose:
            print(f"  [Register] WARNING: form submit error: {exc}", flush=True)
        try:
            content_frame.evaluate(
                "document.getElementById('davidson_form').submit()"
            )
        except Exception:
            pass

    # Wait for results to load inside content_frame
    _wait_settled(page, timeout=60_000)
    _time.sleep(2)

    # Re-find the content_frame after form submission (it may have reloaded)
    content_frame = None
    for frame in page.frames:
        if frame.name == "content_frame":
            content_frame = frame
            break
        if "content.php" in (frame.url or ""):
            content_frame = frame
            break

    if content_frame is None:
        if verbose:
            print("  [Register] WARNING: content_frame lost after submission", flush=True)
        # Use first non-main frame
        non_main = [f for f in page.frames if f != page.main_frame]
        if non_main:
            content_frame = non_main[0]
        else:
            return []

    if verbose:
        print(
            f"  [Register] Results frame URL: {content_frame.url}", flush=True
        )

    records = _extract_results(content_frame, verbose)

    if max_records is not None:
        records = records[:max_records]

    return records


# ---------------------------------------------------------------------------
# Playwright: look up a single instrument by number and return prop_description
# ---------------------------------------------------------------------------


def _pw_lookup_inst_num(content_frame, inst_num: str, verbose: bool) -> Optional[str]:
    """
    Fill the inst_num field in content_frame and submit; return prop_description
    from the first result row, or None if not found.

    Assumes content_frame already has the search form loaded.
    """
    import time as _time

    try:
        content_frame.wait_for_selector(
            'form[name="davidson_form"], form[id="davidson_form"]',
            timeout=10_000,
        )
    except Exception:
        return None

    # Clear all name/date fields so only inst_num is active
    for field_name in ("grantor_last_name", "grantee_last_name", "both_last_name",
                       "start_date", "end_date", "inst_num"):
        try:
            el = content_frame.query_selector(f'[name="{field_name}"]')
            if el:
                el.fill("")
        except Exception:
            pass

    # Fill inst_num
    try:
        inst_el = content_frame.query_selector('[name="inst_num"], #inst_num')
        if not inst_el:
            return None
        inst_el.fill(inst_num)
    except Exception as exc:
        if verbose:
            print(f"  [Register TD] Could not fill inst_num for {inst_num}: {exc}", flush=True)
        return None

    # Submit via the JS submit link.  Use no_wait_after=True so the click returns
    # immediately without blocking on the resulting frame navigation — navigation
    # can take 60-90 s for slow portal searches.  We wait explicitly below.
    try:
        link = content_frame.query_selector('a[onclick*="submitSearch"]')
        if link:
            link.click(no_wait_after=True)
        else:
            content_frame.evaluate("document.getElementById('davidson_form').submit()")
    except Exception:
        try:
            content_frame.evaluate("document.getElementById('davidson_form').submit()")
        except Exception:
            return None

    # Brief pause to let the POST request begin before we start watching for results
    _time.sleep(2)

    # After form submit, the content_frame's server processes the search and returns
    # results HTML.  Portal searches can take 20-90s server-side depending on the
    # age and specificity of the query.  Use a 120s timeout to cover slow searches
    # while still being bounded (instruments genuinely absent time out cleanly).
    _found_results = False
    try:
        content_frame.wait_for_selector(
            "table#results tbody tr",
            timeout=120_000,
        )
        _found_results = True
    except Exception:
        pass  # timeout or no rows — treat as not found

    if not _found_results:
        if verbose:
            print(f"  [Register TD] {inst_num}: not found (timeout or no rows)", flush=True)
        return None

    # Extract prop_description from result rows using locators (avoids evaluate() blocking)
    try:
        rows = content_frame.locator("table#results tbody tr").all()
    except Exception:
        rows = []

    rows_data = []
    for row_locator in rows:
        try:
            cells = row_locator.locator("td").all()
            rows_data.append([c.inner_text(timeout=3_000).strip() for c in cells])
        except Exception:
            pass

    if not rows_data:
        if verbose:
            print(f"  [Register TD] {inst_num}: no results", flush=True)
        return None

    # Column order (per _extract_results): 0=inst_num, 1=rec_date, 2=grantor,
    # 3=grantee, 4=doc_type, 5=prop_description, 6=cross_refs, 7=consideration
    for row in rows_data:
        if len(row) >= 6:
            # Normalize: collapse newlines and extra spaces in multi-line addresses
            prop_desc = " ".join(row[5].split()).strip()
            if prop_desc:
                if verbose:
                    print(f"  [Register TD] {inst_num}: {prop_desc!r}", flush=True)
                return prop_desc

    return None


def _pw_navigate_new_search(page, verbose: bool):
    """
    Reset to a fresh portal search form by reloading the main page.

    Reloading the full main page is the most reliable reset strategy: it creates
    a new content_frame with a fresh random session token.

    Returns the new content_frame reference, or None on failure.
    """
    import time as _time

    page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=45_000)
    _wait_settled(page)

    # Retry finding the iframe — it loads asynchronously after the main page
    for _ in range(8):
        for frame in page.frames:
            if frame.name == "content_frame" or "content.php" in (frame.url or ""):
                if "about:blank" not in (frame.url or ""):
                    return frame
        _time.sleep(0.5)

    return None


def batch_lookup_td_addresses(
    inst_nums: list[str],
    headless: bool = True,
    verbose: bool = False,
    cache_path: Optional[Path] = None,
) -> dict[str, str]:
    """
    Look up a list of instrument numbers in the Register of Deeds portal and
    return a dict of {instrument_number: prop_description}.

    Uses a disk cache (JSON) so subsequent runs don't re-fetch known instruments.
    Missing = instrument not found in portal; cached miss stored as empty string.
    """
    if not PLAYWRIGHT_AVAILABLE:
        if verbose:
            print("  [Register TD] Playwright not available — skipping TD address lookup", flush=True)
        return {}

    import time as _time

    # Load cache
    cache: dict[str, str] = {}
    if cache_path and cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    # Filter to uncached
    to_fetch = [n for n in inst_nums if n not in cache]

    results: dict[str, str] = {k: v for k, v in cache.items() if k in inst_nums}

    if not to_fetch:
        if verbose:
            print(f"  [Register TD] All {len(inst_nums)} instruments already cached", flush=True)
        return results

    if verbose:
        print(
            f"  [Register TD] Fetching {len(to_fetch)} instrument addresses "
            f"({len(inst_nums) - len(to_fetch)} cached)...",
            flush=True,
        )

    # Use a fresh browser session per instrument.  Re-using a single browser
    # and resetting the search form between lookups is fragile (frame identity
    # can be lost after POST navigation).  One browser per instrument costs an
    # extra ~30s of startup but is completely reliable and cache makes it a
    # one-time cost per instrument.
    for inst_num in to_fetch:
        prop_desc = None
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=headless)
                context = browser.new_context(
                    user_agent=USER_AGENT,
                    viewport={"width": 1280, "height": 900},
                    ignore_https_errors=True,
                )
                page = context.new_page()

                page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60_000)
                _wait_settled(page)
                _time.sleep(1)

                # Find content_frame (retry up to 5 times — loads asynchronously)
                content_frame = None
                for _ in range(5):
                    for frame in page.frames:
                        if frame.name == "content_frame" or "content.php" in (frame.url or ""):
                            if "about:blank" not in (frame.url or ""):
                                content_frame = frame
                                break
                    if content_frame:
                        break
                    _time.sleep(0.5)

                if content_frame:
                    prop_desc = _pw_lookup_inst_num(content_frame, inst_num, verbose)

                browser.close()
        except Exception as exc:
            if verbose:
                print(f"  [Register TD] {inst_num}: browser error: {exc}", flush=True)

        val = prop_desc or ""
        cache[inst_num] = val
        results[inst_num] = val

        # Persist cache after each instrument so partial results survive crashes
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(cache, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )

    fetched = sum(1 for n in to_fetch if results.get(n))
    if verbose:
        print(
            f"  [Register TD] Fetched {fetched}/{len(to_fetch)} addresses", flush=True
        )

    return results


# ---------------------------------------------------------------------------
# run_scraper — public API
# ---------------------------------------------------------------------------


def run_scraper(
    output_path: Path,
    *,
    days_back: int = 7,
    doc_types: Optional[list[str]] = None,
    existing_path: Optional[Path] = None,
    verbose: bool = True,
    headless: bool = True,
    max_records: Optional[int] = None,
) -> dict:
    """
    Scrape Shelby County Register of Deeds for ASOT / Federal Tax Lien recordings.

    Parameters
    ----------
    output_path:
        JSONL path to write.
    days_back:
        Number of calendar days back from today for the date range.
    doc_types:
        List of document type codes. Defaults to all six types.
        Valid codes: "APPT" (Appointment of Substitute Trustee),
        "IRS" (Federal Tax Lien), "LIEN" (Judgment Lien),
        "NCTS" (Notice of County Tax Sale), "TNTX" (State Tax Lien),
        "STR" (Sub Trustees Deed / completed foreclosure).
    existing_path:
        Path to prior JSONL for merge (defaults to output_path).
    verbose:
        Print progress messages.
    headless:
        Run Playwright in headless mode.
    max_records:
        Cap on total records returned.

    Returns
    -------
    Stats dict with counts and errors.
    """
    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(_PLAYWRIGHT_INSTALL_MSG)

    resolved_doc_types = doc_types if doc_types is not None else DEFAULT_DOC_TYPES
    prior_path = existing_path if existing_path is not None else output_path

    today = datetime.now(timezone.utc)
    begin_dt = today - timedelta(days=days_back)
    start_date = _format_date_rod(begin_dt)
    end_date = _format_date_rod(today)

    if verbose:
        print(
            f"[Register] Scraping {SOURCE_ID}: {start_date} -> {end_date}, "
            f"doc_types={resolved_doc_types}",
            flush=True,
        )

    current: list[dict] = []
    errors: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
            ignore_https_errors=True,
        )
        page = context.new_page()

        try:
            records = _pw_search(
                page, start_date, end_date, resolved_doc_types, max_records, verbose
            )
            current.extend(records)
        except Exception as exc:
            msg = f"search_error: {exc}"
            errors.append(msg)
            if verbose:
                print(f"  [Register] ERROR: {msg}", flush=True)

        browser.close()

    # Deduplicate
    seen: set = set()
    deduped: list[dict] = []
    for rec in current:
        rid = rec["raw_record_id"]
        if rid not in seen:
            seen.add(rid)
            deduped.append(rec)
    current = deduped

    output_path.parent.mkdir(parents=True, exist_ok=True)
    prior = _load_prior(prior_path)
    merged = merge_with_prior(current, prior)

    tmp = output_path.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for rec in merged:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(output_path)

    stats: dict = {
        "source_id": SOURCE_ID,
        "portal_url": PORTAL_URL,
        "records_pulled": len(current),
        "prior_count": len(prior),
        "total_after_merge": len(merged),
        "new_record_count": sum(1 for r in merged if r["change_status"] == "NEW_RECORD"),
        "same_record_count": sum(1 for r in merged if r["change_status"] == "SAME"),
        "updated_record_count": sum(1 for r in merged if r["change_status"] == "UPDATED"),
        "disappeared_record_count": sum(
            1 for r in merged if r["change_status"] == "DISAPPEARED"
        ),
        "doc_types_searched": resolved_doc_types,
        "date_from": start_date,
        "date_to": end_date,
        "output_path": str(output_path),
        "errors": errors,
        "playwright_available": PLAYWRIGHT_AVAILABLE,
    }
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Shelby County Register of Deeds document scraper (Playwright). "
            "Fetches ASOT / Federal Tax Lien / Judgment Lien recordings by date range. "
            "Requires: pip install playwright && playwright install chromium."
        )
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=7,
        metavar="N",
        help="Number of calendar days back from today (default 7).",
    )
    parser.add_argument(
        "--doc-types",
        nargs="+",
        default=None,
        metavar="CODE",
        help=(
            "Document type codes to search (space-separated). "
            "Valid: APPT IRS LIEN NCTS TNTX STR. "
            "Default: all six types."
        ),
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        metavar="N",
        help="Cap on total records returned.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSONL path. Default: data/raw/register_shelby.jsonl",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run Playwright in visible (non-headless) mode for debugging.",
    )
    parser.add_argument(
        "--probe-fields",
        action="store_true",
        help=(
            "Load the portal via Playwright and print all form fields "
            "found in the content_frame, then exit."
        ),
    )
    args = parser.parse_args()

    if args.probe_fields:
        print(f"[probe] Loading portal via Playwright: {PORTAL_URL}", flush=True)
        if not PLAYWRIGHT_AVAILABLE:
            print(f"ERROR: {_PLAYWRIGHT_INSTALL_MSG}", file=sys.stderr)
            return 1

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.no_headless)
            context = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 900},
                ignore_https_errors=True,
            )
            page = context.new_page()
            page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60_000)
            _wait_settled(page)

            import time as _time
            _time.sleep(2)

            # Collect info about all frames
            frames_info = []
            content_frame = None
            for frame in page.frames:
                frames_info.append({
                    "name": frame.name,
                    "url": frame.url,
                })
                if frame.name == "content_frame" or "content.php" in (frame.url or ""):
                    content_frame = frame

            fields_info: dict = {
                "portal_url": PORTAL_URL,
                "probe_mode": True,
                "all_frames": frames_info,
            }

            if content_frame:
                fields_info["content_frame_url"] = content_frame.url
                try:
                    fields = content_frame.evaluate("""
                        () => {
                            const inputs = document.querySelectorAll(
                                'input, select, textarea, button'
                            );
                            return Array.from(inputs).map(el => ({
                                tag: el.tagName.toLowerCase(),
                                name: el.name || '',
                                id: el.id || '',
                                type: el.type || '',
                                value: el.value || '',
                                checked: el.checked || false,
                            }));
                        }
                    """)
                    fields_info["fields"] = fields
                    fields_info["field_count"] = len(fields)
                except Exception as exc:
                    fields_info["field_error"] = str(exc)
            else:
                fields_info["error"] = (
                    "content_frame not found. Check portal structure."
                )

            browser.close()

        print(json.dumps(fields_info, indent=2))
        return 0

    if not PLAYWRIGHT_AVAILABLE:
        print(f"ERROR: {_PLAYWRIGHT_INSTALL_MSG}", file=sys.stderr)
        return 1

    out = (
        Path(args.out)
        if args.out
        else REPO_ROOT / "data" / "raw" / "register_shelby.jsonl"
    )
    stats = run_scraper(
        out,
        days_back=args.days_back,
        doc_types=args.doc_types,
        headless=not args.no_headless,
        max_records=args.max_records,
    )
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
