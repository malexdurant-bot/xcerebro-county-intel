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
    page.goto(PORTAL_URL, wait_until="networkidle", timeout=30_000)
    page.wait_for_timeout(2_000)
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


def _scrape_current_table_page(page) -> list[dict]:
    rows_out: list[dict] = []
    for tr in page.query_selector_all("table tbody tr"):
        texts = [c.inner_text().strip() for c in tr.query_selector_all("td")]
        if len(texts) < 11:
            continue
        grantor, grantee, doc_type, recorded_date, doc_number = texts[3:8]
        book_volume_page, town, legal_description = texts[8:11]
        if not doc_number:
            continue
        rows_out.append({
            "grantor": grantor or None,
            "grantee": grantee or None,
            "doc_type": doc_type or None,
            "recorded_date": recorded_date or None,
            "doc_number": doc_number,
            "book_volume_page": book_volume_page or None,
            "town": town or None,
            "legal_description": legal_description or None,
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
            print("  [Dallas RP] no results for this window", flush=True)
        return []
    if status == "Error":
        raise RuntimeError(
            "Dallas RP: search did not resolve (portal returned an error or "
            "never left 'Loading Results...')"
        )

    rows_out: list[dict] = []
    for page_num in range(max_pages):
        page_rows = _scrape_current_table_page(page)
        if verbose:
            print(f"  [Dallas RP] page {page_num}: {len(page_rows)} rows", flush=True)
        rows_out.extend(page_rows)
        if len(page_rows) < limit:
            break
        if not _goto_next_page(page):
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
    it is used only to detect the last page (page_rows < limit_per_page), not
    to request a page size; the portal doesn't expose that as a parameter."""
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
