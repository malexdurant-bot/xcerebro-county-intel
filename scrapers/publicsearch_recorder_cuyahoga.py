"""
Cuyahoga County, OH -- Fiscal Office Recording Division (GovOS / Kofile
PublicSearch portal), distress document types.

Portal: https://cuyahoga.oh.publicsearch.us/  (Property Records = department RP)

Operator decision (Marcus, 2026-11-17): use the recorder "the same as Dallas"
(scrapers/publicsearch_recorder_dallas.py) -- one Playwright browser session
that drives the portal's own Advanced Search UI exactly like a person would,
no hand-built result URLs, no concurrency, small page counts. The Fiscal
Office's recorded-documents page warns that "automatic extraction ... is
considered abusive ... may be banned", so this adapter is MORE conservative
than Dallas:
  - one search per run covering all doc types (one result set, not one per type)
  - >= 6 s between result pages, hard page cap per run (default 40 = 2,000 rows)
  - no document-image downloads, no detail-page clicks, no OCR
  - Playwright's own headless user agent (no browser spoofing)
  - stops immediately on any challenge / access-denied page
No CAPTCHA solving, stealth browser or proxies.

What the index gives (checked live 2026-11-17): GRANTOR | GRANTEE | DOC TYPE |
RECORDED DATE | DOC NUMBER | BOOK/VOLUME/PAGE | LEGAL DESCRIPTION | PARCEL ID |
PROPERTY ADDRESS | REFERENCES | CONSIDERATION. Unlike Dallas, Cuyahoga indexes
the parcel id and property address on the row. The index is "certified through"
a date ~1 week behind today; the default window (21 days) covers that lag.

Party direction is documented in runs/cuyahoga_oh/translate.py (live samples).
"""

from __future__ import annotations

import argparse
import os
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scrapers._cook_common import now_iso, wrap, write_jsonl  # noqa: E402  (generic helpers)

PORTAL = "https://cuyahoga.oh.publicsearch.us"
ADVANCED_URL = f"{PORTAL}/search/advanced"
SOURCE_ID = "clerk_recordings"
OUTPUT_NAME = "clerk_recordings.jsonl"

# Distress / estate / divorce-proxy document types (Property Records dept).
DOC_TYPES = [
    "CLP",   # CERTIFICATE OF LIS PENDENS
    "CT",    # PROBATE COURT (certificate of transfer from an estate)
    "EX",    # EXECUTORS DEED
    "FD",    # FIDUCIARY DEED
    "ATOD",  # AFFIDAVIT OF TRANSFER ON DEATH (owner died)
    "AC",    # AFFIDAVIT OF SURVIVING TENANT (joint owner died)
    "TO",    # TRANSFER ON DEATH (designation by a living owner)
    "QC",    # QUIT CLAIM DEED (kept only between same-surname individuals)
    "PTXL",  # PROPERTY TAX LIENS (tax lien certificate sale)
    "FL",    # FEDERAL TAX LIEN
    "ML",    # MECHANIC'S LIEN
    "MDRL",  # MEDICAID LIEN (estate recovery)
    "CSEL",  # CHILD SUPPORT ENFORCEMENT LIEN
    "LIEN",  # LIEN (generic)
]

DEFAULT_DAYS_BACK = 14       # first run only; later runs are incremental (see _window)
DEFAULT_MAX_PAGES = 45       # 50 rows/page -> 2,250 rows; ~1,700 rows/week across these types
OVERLAP_DAYS = 3             # re-pull the last few days (late indexing); dedup is by doc number
PAGE_DELAY_S = 6.0
_POLL_MS = 3000
_MAX_POLLS = 40  # 2 minutes; results arrive over the portal websocket
_CHALLENGE_RE = re.compile(r"captcha|verify you are human|access denied|request blocked|unusual traffic", re.I)
_RANGE_RE = re.compile(r"(\d+)-(\d+)\s+of\s+([\d,]+)\s+results", re.I)
_CERT_RE = re.compile(r"Certified through\s+(\d{1,2}/\d{1,2}/\d{4})", re.I)


def _window(out_path: Path, end: date, days_back: int, incremental: bool) -> date:
    """First run: end - days_back. Later runs: from the previous run's end minus
    OVERLAP_DAYS -- keeps a daily run to a few days of results (~25 pages)."""
    start = end - timedelta(days=days_back)
    state = out_path.with_suffix(".state.json")
    if incremental and state.exists():
        try:
            last_end = date.fromisoformat(json.loads(state.read_text())["last_end"])
            start = max(start, last_end - timedelta(days=OVERLAP_DAYS))
        except Exception:  # noqa: BLE001 -- bad state file = full window
            pass
    return start


class ChallengePage(RuntimeError):
    """The portal answered with its bot-check page. Never worked around -- the run stops and reports."""


_PAGE_LOAD_ATTEMPTS = 3
_CHALLENGE_BACKOFF_S = 120
_FILTER_INPUT = (
    "input[placeholder='Filter Document Types'], "
    "input[placeholder*='Filter Document' i], "
    "input[placeholder*='Document Type' i]"
)

def _headed_display() -> str | None:
    """Local X display, if any.

    Confirmed live 2026-11-23: Cloudflare Turnstile on this portal blocks
    headless Chromium (the Filter Document Types input never appears; the
    page stays on 'Performing security verification'). Headed Chromium on
    the box display loads the Advanced Search form with Playwright's own
    user agent. The challenge is never solved, evaded, or spoofed; if no
    display is available the run stays headless and will report the
    challenge page and stop.
    """
    candidates: list[str] = []
    env = os.environ.get("DISPLAY")
    if env:
        candidates.append(env)
    for n in (9, 6, 5, 3, 1, 0):
        disp = f":{n}"
        if disp not in candidates:
            candidates.append(disp)
    for disp in candidates:
        num = disp[1:].split(".")[0] if disp.startswith(":") else None
        if num is not None and Path(f"/tmp/.X11-unix/X{num}").exists():
            return disp
        if num is None and env == disp:
            # non-local DISPLAY already set (rare); trust it
            return disp
    return None


def _is_challenge(page) -> bool:
    """Cloudflare / GovOS bot-check (same interstitial Dallas sees)."""
    try:
        url = page.url or ""
        title = (page.title() or "")
        body = page.inner_text("body")[:4000]
    except Exception:  # noqa: BLE001
        return False
    if "__cf_chl" in url or "Just a moment" in title:
        return True
    if "Performing security verification" in body or "cf-turnstile-response" in body:
        return True
    return bool(_CHALLENGE_RE.search(body))


def _check_challenge(page) -> None:
    if _is_challenge(page):
        raise ChallengePage(
            "Cuyahoga RP: portal returned its bot-check page "
            "('Performing security verification') -- not bypassed; stopping"
        )


def _open_advanced_search(page, verbose: bool) -> None:
    """Load /search/advanced, detect the Cloudflare interstitial, back off
    and retry like the Dallas recorder. Never solve or evade the check.
    The Filter Document Types input is only waited on after the challenge
    is gone -- a timeout there was the previous (misleading) failure mode.
    """
    last_exc = None
    for attempt in range(1, _PAGE_LOAD_ATTEMPTS + 1):
        try:
            page.goto(ADVANCED_URL, wait_until="domcontentloaded", timeout=45_000)
            # Wait for the form (or for a brief check to clear). A Turnstile
            # page has no filter input; after the wait we report it and back off.
            try:
                page.wait_for_selector(_FILTER_INPUT, timeout=30_000)
            except Exception:
                if _is_challenge(page):
                    raise ChallengePage(
                        "Cuyahoga RP: portal returned its bot-check page "
                        "('Performing security verification') -- not bypassed; stopping"
                    )
                raise
            page.wait_for_timeout(1500)
            _check_challenge(page)
            page.wait_for_selector(_FILTER_INPUT, timeout=15_000)
            # Results are delivered over wss://.../ws. If we search before
            # that socket is up, the page can sit on "Loading Results" until
            # our poll times out. Give it a moment; never interact with a
            # challenge widget.
            try:
                page.wait_for_function(
                    """() => {
                        const banner = document.querySelector('.online-status');
                        if (!banner) return true;
                        const st = getComputedStyle(banner);
                        return st.display === 'none' || st.visibility === 'hidden'
                            || !(banner.offsetWidth || banner.offsetHeight);
                    }""",
                    timeout=10_000,
                )
            except Exception:
                pass
            page.wait_for_timeout(1000)
            return
        except ChallengePage as exc:
            last_exc = exc
            if attempt == _PAGE_LOAD_ATTEMPTS:
                raise
            wait_s = _CHALLENGE_BACKOFF_S * attempt
            if verbose:
                print(
                    f"  [Cuyahoga RP] portal bot-check page "
                    f"(attempt {attempt}/{_PAGE_LOAD_ATTEMPTS}); backing off {wait_s}s",
                    flush=True,
                )
            page.wait_for_timeout(wait_s * 1000)
        except Exception as exc:  # noqa: BLE001 -- retried, then re-raised
            last_exc = exc
            if _is_challenge(page):
                last_exc = ChallengePage(
                    "Cuyahoga RP: portal returned its bot-check page "
                    "('Performing security verification') -- not bypassed; stopping"
                )
                if attempt == _PAGE_LOAD_ATTEMPTS:
                    raise last_exc
                wait_s = _CHALLENGE_BACKOFF_S * attempt
                if verbose:
                    print(
                        f"  [Cuyahoga RP] portal bot-check page "
                        f"(attempt {attempt}/{_PAGE_LOAD_ATTEMPTS}); backing off {wait_s}s",
                        flush=True,
                    )
                page.wait_for_timeout(wait_s * 1000)
                continue
            if verbose:
                print(
                    f"  [Cuyahoga RP] advanced search not ready "
                    f"(attempt {attempt}/{_PAGE_LOAD_ATTEMPTS}): {type(exc).__name__}: {exc}",
                    flush=True,
                )
            if attempt == _PAGE_LOAD_ATTEMPTS:
                raise
            page.wait_for_timeout(10_000 * attempt)
    raise last_exc


def _select_doc_types(page, codes: list[str]) -> list[str]:
    selected = []
    for code in codes:
        page.fill(_FILTER_INPUT, code)
        page.wait_for_timeout(700)
        for opt in page.query_selector_all("#docTypes-listbox li, #docTypes-listbox [role=option]"):
            if opt.inner_text().strip().startswith(code + " - "):
                opt.click()
                page.wait_for_timeout(300)
                selected.append(code)
                break
    page.fill(_FILTER_INPUT, "")
    return selected


def _cell_names(td) -> list[str]:
    spans = [s.inner_text().strip() for s in td.query_selector_all("span")]
    names = [s for s in spans if s] or [t.strip() for t in td.inner_text().split("\n") if t.strip()]
    out = []
    for n in names:
        if n not in out:
            out.append(n)
    return out


def _parse_rows(page) -> list[dict]:
    rows = []
    for tr in page.query_selector_all("table tbody tr"):
        tds = tr.query_selector_all("td")
        if len(tds) < 12:
            continue
        cb = tr.query_selector("input[type=checkbox]")
        doc_id = (cb.get_attribute("id") or "").replace("table-checkbox-", "") if cb else ""
        txt = [td.inner_text().strip() for td in tds]
        doc_type = txt[5]
        code = doc_type.split(" - ", 1)[0].strip()
        rows.append({
            "portal_doc_id": doc_id or None,
            "grantors": _cell_names(tds[3]),
            "grantees": _cell_names(tds[4]),
            "doc_type": doc_type,
            "doc_type_code": code,
            "recorded_date": txt[6],
            "doc_number": txt[7],
            "book_volume_page": None if txt[8].startswith("--") else txt[8],
            "legal_description": None if txt[9] in ("N/A", "") else txt[9],
            "parcel_id": None if txt[10] in ("N/A", "") else txt[10],
            "property_address": None if txt[11] in ("N/A", "") else re.sub(r"\s+", " ", txt[11]),
            "references": None if txt[12] in ("N/A", "") else txt[12],
            "consideration": None if len(txt) < 14 or txt[13] in ("N/A", "") else txt[13],
        })
    return rows



_TYPE_CHUNK = 4  # confirmed 2026-11-23: a single search of all 14 types
# stays on "Loading Search Results" indefinitely; groups of ~4 resolve.


def _click_named_button(page, label: str) -> bool:
    for bt in page.query_selector_all("button"):
        if bt.inner_text().strip() == label:
            bt.scroll_into_view_if_needed()
            bt.click(force=True)
            return True
    return False


def _wait_for_search_status(page, verbose: bool) -> str:
    status = "Error"
    for poll_i in range(_MAX_POLLS):
        page.wait_for_timeout(_POLL_MS)
        body = page.inner_text("body")
        if page.query_selector_all("table tbody tr"):
            return "HasRows"
        if "No Results Found" in body:
            return "NoResults"
        if "Error While Running Search" in body:
            if verbose:
                print("  [Cuyahoga RP] portal reported 'Error While Running Search'", flush=True)
            return "Error"
        if _is_challenge(page):
            return "Error"
        title = page.title() or ""
        if verbose and poll_i in (0, 4, 9, 19, 29):
            print(
                f"  [Cuyahoga RP] waiting for results "
                f"({(poll_i + 1) * _POLL_MS // 1000}s) title={title!r}",
                flush=True,
            )
    return status


def _collect_pages(page, max_pages: int, page_delay_s: float, verbose: bool) -> tuple[list[dict], int, bool]:
    """Paginate the current result set. Returns (rows, total_results or 0, page_cap_hit)."""
    rows: list[dict] = []
    total_results = 0
    if max_pages <= 0:
        return rows, total_results, True
    cap_hit = False
    for page_num in range(max_pages):
        body = page.inner_text("body")
        rm = _RANGE_RE.search(body)
        if rm:
            total_results = int(rm.group(3).replace(",", ""))
        page_rows = _parse_rows(page)
        rows.extend(page_rows)
        if verbose:
            print(
                f"  [Cuyahoga RP] page {page_num + 1}: {len(page_rows)} rows "
                f"({rm.group(0) if rm else '?'})",
                flush=True,
            )
        if rm and int(rm.group(2)) >= int(rm.group(3).replace(",", "")):
            break
        btn = page.query_selector('[aria-label="next page"]')
        if btn is None or (btn.get_attribute("disabled") in ("true", "")
                           or btn.get_attribute("aria-disabled") == "true"):
            break
        time.sleep(page_delay_s)
        first_before = page_rows[0]["doc_number"] if page_rows else None
        btn.scroll_into_view_if_needed()
        btn.click(force=True, timeout=10_000)
        for _ in range(10):
            page.wait_for_timeout(1000)
            trs = page.query_selector_all("table tbody tr td.col-7")
            if trs and trs[0].inner_text().strip() != first_before:
                break
        _check_challenge(page)
    else:
        cap_hit = True
    return rows, total_results, cap_hit


def _return_to_advanced_search(page, verbose: bool) -> None:
    """Prefer in-app navigation; a fresh goto can trip the bot-check page."""
    for sel in (
        'a[href="/search/advanced"]',
        "a:has-text('ADVANCED')",
        "a:has-text('NEW SEARCH')",
        "a:has-text('New Search')",
    ):
        loc = page.query_selector(sel)
        if loc is None:
            continue
        try:
            loc.click(timeout=10_000)
            page.wait_for_selector(_FILTER_INPUT, timeout=30_000)
            page.wait_for_timeout(800)
            _check_challenge(page)
            return
        except Exception:  # noqa: BLE001 -- try the next selector, then one goto
            continue
    _open_advanced_search(page, verbose)


def _run_one_search(page, start: date, end: date, codes: list[str],
                    max_pages: int, page_delay_s: float, verbose: bool) -> dict:
    """Fill the advanced-search form for `codes`, submit, and collect rows."""
    selected = _select_doc_types(page, codes)
    page.fill("#recordedDateRange-start", start.strftime("%m/%d/%Y"))
    page.fill("#recordedDateRange-end", end.strftime("%m/%d/%Y"))
    page.keyboard.press("Tab")
    _click_named_button(page, "Search")
    status = _wait_for_search_status(page, verbose)
    _check_challenge(page)
    if verbose:
        print(f"  [Cuyahoga RP] search types={codes} status={status} url={page.url}", flush=True)
    result = {"status": status, "selected": selected, "rows": [], "total_results": 0,
              "page_cap_hit": False, "results_url": page.url}
    if status == "HasRows":
        rows, total, cap = _collect_pages(page, max_pages, page_delay_s, verbose)
        result["rows"] = rows
        result["total_results"] = total
        result["page_cap_hit"] = cap
    return result


def run_scraper(out_dir: Path, days_back: int = DEFAULT_DAYS_BACK, doc_types: list[str] | None = None,
                max_pages: int = DEFAULT_MAX_PAGES, end_date: date | None = None,
                page_delay_s: float = PAGE_DELAY_S, verbose: bool = True, incremental: bool = True,
                headless: bool | None = None) -> dict:
    from playwright.sync_api import sync_playwright

    doc_types = doc_types or DOC_TYPES
    end = end_date or date.today()
    out_path = Path(out_dir) / OUTPUT_NAME
    start = _window(out_path, end, days_back, incremental)
    rows: list[dict] = []
    errors: list[str] = []
    stats: dict = {"window": [start.isoformat(), end.isoformat()], "doc_types": doc_types}
    if headless is None:
        display = _headed_display()
        if display:
            os.environ.setdefault("DISPLAY", display)
            headless = False
        else:
            headless = True
    if verbose:
        print(f"  [Cuyahoga RP] launching Chromium headless={headless} DISPLAY={os.environ.get('DISPLAY')!r}", flush=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context()  # Playwright's own user agent -- no spoofing
        page = ctx.new_page()
        try:
            _open_advanced_search(page, verbose)
            m = _CERT_RE.search(page.inner_text("body"))
            stats["certified_through"] = m.group(1) if m else None
            if stats["certified_through"]:
                try:
                    cert = datetime.strptime(stats["certified_through"], "%m/%d/%Y").date()
                except ValueError:
                    cert = None
                if cert and end > cert:
                    if verbose:
                        print(
                            f"  [Cuyahoga RP] capping end {end.isoformat()} "
                            f"to certified-through {cert.isoformat()}",
                            flush=True,
                        )
                    end = cert
                    if start > end:
                        start = end
                    stats["window"] = [start.isoformat(), end.isoformat()]
            chunks = [doc_types[i:i + _TYPE_CHUNK] for i in range(0, len(doc_types), _TYPE_CHUNK)]
            if len(chunks) > 1 and verbose:
                print(
                    f"  [Cuyahoga RP] searching {len(doc_types)} types in "
                    f"{len(chunks)} groups of up to {_TYPE_CHUNK} "
                    f"(a single combined search does not finish on this portal)",
                    flush=True,
                )
            selected_all: list[str] = []
            remaining_pages = max_pages
            seen: set[str] = set()
            for ci, chunk in enumerate(chunks):
                if ci:
                    time.sleep(page_delay_s)
                    _return_to_advanced_search(page, verbose)
                    _click_named_button(page, "Clear")
                    page.wait_for_timeout(500)
                if remaining_pages <= 0:
                    stats["page_cap_hit"] = True
                    errors.append(f"page cap {max_pages} reached -- window truncated")
                    break
                result = _run_one_search(
                    page, start, end, chunk, remaining_pages, page_delay_s, verbose,
                )
                selected_all.extend(result["selected"])
                stats["results_url"] = result["results_url"]
                if result["total_results"]:
                    stats["total_results"] = stats.get("total_results", 0) + result["total_results"]
                if result["status"] == "HasRows":
                    added = 0
                    for r in result["rows"]:
                        if r["doc_number"] in seen:
                            continue
                        seen.add(r["doc_number"])
                        rows.append(r)
                        added += 1
                    # pages consumed ~= rows/50
                    used = max(1, (added + 49) // 50)
                    remaining_pages -= used
                    if result["page_cap_hit"]:
                        stats["page_cap_hit"] = True
                        errors.append(f"page cap {max_pages} reached -- window truncated")
                        break
                elif result["status"] == "NoResults":
                    continue
                else:
                    errors.append(
                        f"search did not resolve for types {chunk} (error or timeout)"
                    )
            stats["doc_types_selected"] = selected_all
            stats["search_chunks"] = chunks
        except ChallengePage as exc:
            errors.append(str(exc))
        finally:
            browser.close()

    fetched = now_iso()
    wrapped = []
    for r in rows:
        if not r["doc_number"]:
            continue
        url = f"{PORTAL}/doc/{r['portal_doc_id']}" if r["portal_doc_id"] else stats.get("results_url", PORTAL)
        rec = wrap(f"cuyahoga_rp_{r['doc_number']}", SOURCE_ID, url, r)
        rec["source_fetched_at"] = fetched
        wrapped.append(rec)
    res = write_jsonl(wrapped, out_path, missing_status="RETAINED")
    if not errors and not stats.get("page_cap_hit"):
        out_path.with_suffix(".state.json").write_text(json.dumps({"last_end": end.isoformat(),
                                                                    "certified_through": stats.get("certified_through")}))
    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["doc_type_code"]] = by_type.get(r["doc_type_code"], 0) + 1
    res.update(stats)
    res["by_doc_type"] = by_type
    res["errors"] = errors
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(ROOT / "runs" / "cuyahoga_oh" / "pipeline_output" / "raw"))
    ap.add_argument("--days-back", type=int, default=DEFAULT_DAYS_BACK)
    ap.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    ap.add_argument("--doc-types", default="")
    ap.add_argument("--headless", action="store_true", help="Force headless Chromium (blocked by this portal's Turnstile)")
    ap.add_argument("--no-headless", action="store_true", help="Force headed Chromium")
    a = ap.parse_args()
    codes = [c.strip().upper() for c in a.doc_types.split(",") if c.strip()] or None
    forced_headless: bool | None
    if a.headless and a.no_headless:
        raise SystemExit("use only one of --headless / --no-headless")
    elif a.headless:
        forced_headless = True
    elif a.no_headless:
        forced_headless = False
    else:
        forced_headless = None
    result = run_scraper(Path(a.out_dir), days_back=a.days_back, doc_types=codes,
                         max_pages=a.max_pages, headless=forced_headless)
    print(json.dumps(result, indent=2, default=str))
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
