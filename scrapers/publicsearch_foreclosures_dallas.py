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

Requires: pip install playwright && playwright install chromium
"""

from __future__ import annotations

import argparse
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

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PORTAL_HOST = "dallas.tx.publicsearch.us"
ADVANCED_SEARCH_URL = f"https://{PORTAL_HOST}/search/advanced"
DEPARTMENT = "FC"
SOURCE_ID = "foreclosure_notices"

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
    page.goto(ADVANCED_SEARCH_URL, wait_until="networkidle", timeout=30_000)
    page.wait_for_timeout(2_000)
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


def _scrape_current_table_page(page) -> list[dict]:
    rows_out: list[dict] = []
    for tr in page.query_selector_all("table tbody tr"):
        texts = [c.inner_text().strip() for c in tr.query_selector_all("td")]
        if len(texts) < 8:
            continue
        doc_type, recorded_date, sale_date, doc_number, property_city = texts[3:8]
        if not doc_number:
            continue
        rows_out.append({
            "doc_type": doc_type or None,
            "recorded_date": recorded_date or None,
            "sale_date": sale_date or None,
            "doc_number": doc_number,
            "property_city": property_city or None,
        })
    return rows_out


def _goto_next_page(page) -> bool:
    """Click the 'next page' pagination control. Returns False if absent/disabled."""
    btn = page.query_selector('[aria-label="next page"]')
    if btn is None:
        return False
    disabled = btn.get_attribute("disabled") or btn.get_attribute("aria-disabled")
    if disabled in ("true", ""):
        return False
    btn.scroll_into_view_if_needed()
    btn.click(force=True, timeout=10_000)
    page.wait_for_timeout(2_500)
    return True


def _scrape_window(
    page,
    date_from: datetime,
    date_to: datetime,
    limit: int,
    max_pages: int,
    verbose: bool,
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

    rows_out: list[dict] = []
    for page_num in range(max_pages):
        page_rows = _scrape_current_table_page(page)
        if verbose:
            print(f"  [Dallas FC] page {page_num}: {len(page_rows)} rows", flush=True)
        rows_out.extend(page_rows)
        if len(page_rows) < limit:
            break
        if not _goto_next_page(page):
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
        }

        out.append({
            "raw_record_id": _raw_record_id(doc_number),
            "source_id": SOURCE_ID,
            "source_url": f"about:blank/{SOURCE_ID}/{doc_number}",
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
) -> dict:
    """limit_per_page must match the portal's actual results-per-page (50) —
    it is used only to detect the last page, not to request a page size."""
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
                page, date_from, date_to, limit_per_page, max_pages, verbose
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
    )
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
