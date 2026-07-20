"""
Shelby County Chancery Court — civil docket scraper.

Portal: https://chancerydata.shelbycountytn.gov/chweb/ck_public_qry_main.cp_main_idx

Architecture: ACS CourtConnect (Oracle PL/SQL Web Toolkit). Identical navigation
flow to General Sessions Civil. Returns HTTP 403 on raw HTTP — Playwright required.

Navigation flow (CONFIRMED):
  1. Load main URL → frameset with "Header - blue" and "Big" frames
  2. In "Big" frame: click "Search by person name, business name or case type"
  3. Disclaimer page → "Action" frame → click "Accept" → POSTs to cp_personcase_setup_idx
  4. "Big" frame = cp_personcase_srch_setup (search form)
  5. Fill & submit → results at cp_personcase_details_idx

Target case types (Chancery Court jurisdiction in TN):
  - "FO" : Foreclosure (judicial — rare in TN non-judicial state)
  - "PA" : Complaint for Partition (co-owner disputes, estate-related)
  - "QT" : Pet/Quiet Title/Remove Cloud (title disputes)
  - "EF" : Pet. to Enjoin Foreclosure
  - "CO" : Collection of Delinquent Taxes
  - "LN" : Lien
  - "ALL": all case types

Requires: pip install playwright && playwright install chromium
"""

from __future__ import annotations

import argparse
import json
import sys
import time
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

SOURCE_ID = "chancery_court_shelby"

PORTAL_URL = (
    "https://chancerydata.shelbycountytn.gov/chweb/ck_public_qry_main.cp_main_idx"
)
BASE_URL = "https://chancerydata.shelbycountytn.gov/chweb/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Default case types — foreclosure, partition, quiet title
DEFAULT_CASE_TYPES = ["FO", "PA", "QT"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _raw_record_id(case_number: str) -> str:
    safe = (case_number or "").strip().upper().replace(" ", "_")
    return f"shelby_ch_{safe}"


def _format_date_cc(dt: datetime) -> str:
    """Format datetime as DD-MON-YYYY for CourtConnect (e.g. 01-JUL-2026)."""
    return dt.strftime("%d-%b-%Y").upper()


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
# Playwright helpers (shared CourtConnect navigation pattern)
# ---------------------------------------------------------------------------


def _wait_settled(page_or_frame, timeout: int = 30_000) -> None:
    try:
        page_or_frame.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        try:
            page_or_frame.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:
            pass


def _navigate_to_search_form(page, verbose: bool) -> Optional[object]:
    """
    Navigate through CourtConnect frameset to reach the party search form.
    Returns the frame containing the search form, or None on failure.
    """
    if verbose:
        print(f"  [Chancery] Loading main portal: {PORTAL_URL}", flush=True)

    page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60_000)
    _wait_settled(page)

    # Step 1: Find any frame that has the party search link
    big_frame = None
    for attempt in range(3):
        for frame in page.frames:
            try:
                el = frame.query_selector('a[href*="search_option=party"]')
                if el:
                    big_frame = frame
                    break
            except Exception:
                pass
        if big_frame:
            break
        time.sleep(0.5)

    if big_frame is None:
        if verbose:
            print("  [Chancery] WARNING: could not find Big/main frame", flush=True)
        return None

    if verbose:
        print(f"  [Chancery] Found main frame: {big_frame.url}", flush=True)

    # Click the "Search by person name, business name or case type" link
    try:
        link = big_frame.wait_for_selector(
            'a[href*="search_option=party"]', timeout=10_000
        )
        if link:
            link.click()
        else:
            big_frame.click('text=Search by person name')
    except Exception as exc:
        if verbose:
            print(f"  [Chancery] WARNING: party search link click failed: {exc}", flush=True)
        return None

    _wait_settled(page)

    # Check if the search form already appeared (disclaimer was skipped this session)
    def _find_search_frame():
        for frame in page.frames:
            try:
                if frame.query_selector('[name="last_name"]'):
                    return frame
            except Exception:
                pass
        return None

    search_frame = _find_search_frame()
    if search_frame is not None:
        if verbose:
            print(f"  [Chancery] Search form already available: {search_frame.url}", flush=True)
        return search_frame

    # Step 2: Find disclaimer/action frame with Accept button
    action_frame = None
    for attempt in range(5):
        for frame in page.frames:
            try:
                content = frame.content()
                if "Accept" in content and "Decline" in content:
                    action_frame = frame
                    break
            except Exception:
                pass
        if action_frame:
            break
        time.sleep(0.5)

    if action_frame is None:
        if verbose:
            print("  [Chancery] WARNING: could not find disclaimer frame", flush=True)
        return None

    if verbose:
        print(f"  [Chancery] Found disclaimer frame: {action_frame.url}", flush=True)

    # Click Accept
    try:
        accept_el = action_frame.query_selector('input[value="Accept"]')
        if accept_el:
            accept_el.click()
        else:
            accept_el = action_frame.query_selector('a:has-text("Accept")')
            if accept_el:
                accept_el.click()
            else:
                action_frame.click('text=Accept')
    except Exception as exc:
        if verbose:
            print(f"  [Chancery] WARNING: Accept click failed: {exc}", flush=True)
        return None

    _wait_settled(page)

    # Step 3: Find search form frame (has last_name field)
    search_frame = None
    for attempt in range(5):
        search_frame = _find_search_frame()
        if search_frame:
            break
        time.sleep(0.5)

    if search_frame is None:
        if verbose:
            print(
                "  [Chancery] WARNING: could not find search form frame",
                flush=True,
            )

    return search_frame


def _extract_results_from_page(
    page, case_type_label: str, verbose: bool
) -> list[dict]:
    """Extract case rows from a CourtConnect cp_personcase_srch_details page."""
    now = _now_iso()
    records: list[dict] = []

    try:
        rows_data = page.evaluate("""
            () => {
                const results = [];
                // Case links go to cp_dktrpt_fr (docket report) — same as GS Civil
                const links = document.querySelectorAll('a[href*="cp_dktrpt_fr"]');
                for (const link of links) {
                    const href = new URL(
                        link.getAttribute('href') || '', document.baseURI
                    ).href;
                    const caseNum = link.innerText.trim();
                    const row = link.closest('tr');
                    if (!row) continue;
                    const cellTexts = Array.from(row.querySelectorAll('td')).map(
                        c => c.innerText.trim().replace(/\\s+/g, ' ')
                    );
                    results.push({ case_number: caseNum, href: href, cells: cellTexts });
                }
                return results;
            }
        """)
    except Exception as exc:
        if verbose:
            print(f"  [Chancery] JS extraction error: {exc}", flush=True)
        rows_data = []

    if verbose:
        print(f"  [Chancery]   rows extracted: {len(rows_data)}", flush=True)

    for row in rows_data:
        case_number = (row.get("case_number") or "").strip()
        if not case_number:
            continue

        cells = row.get("cells") or []
        href = (row.get("href") or "").strip()

        # Chancery CourtConnect has 7 columns (differs from GS Civil's 5):
        # cells[0] = party record ID e.g. "@151821"
        # cells[1] = party name e.g. "GRANT, LEDERRIUS TRAMALE"
        # cells[2] = "Case: CH-26-0944 PLAINTIFF V DEFENDANT" (contains docket link)
        # cells[3] = party type e.g. "Defendant" or "Plaintiff"
        # cells[4] = (empty — possibly address)
        # cells[5] = filing date e.g. "09-JUL-2026"
        # cells[6] = case status e.g. "INITIAL-Initial case filing"
        party_name = cells[1].strip() if len(cells) > 1 else ""
        party_type = cells[3].strip() if len(cells) > 3 else None
        filing_date = cells[5].strip() if len(cells) > 5 else None

        # Parse case caption from cells[2]: "Case: CH-26-0944 SMITH V JONES"
        case_cell = cells[2].strip() if len(cells) > 2 else ""
        caption = case_cell
        # Strip "Case: <case_num> " prefix if present
        import re as _re
        m = _re.match(r'^Case:\s*\S+\s+(.*)', case_cell, _re.IGNORECASE)
        if m:
            caption = m.group(1).strip()

        plaintiff = None
        defendant = None
        if " V " in caption:
            parts = caption.split(" V ", 1)
            plaintiff = parts[0].strip()
            defendant = parts[1].strip()
        elif (party_type or "").upper() == "PLAINTIFF":
            plaintiff = party_name
        elif (party_type or "").upper() == "DEFENDANT":
            defendant = party_name
        else:
            plaintiff = party_name

        raw_payload: dict = {
            "case_number": case_number,
            "case_type": case_type_label,
            "case_caption": caption,
            "party_name": party_name,
            "plaintiff": plaintiff,
            "defendant": defendant,
            "party_type": party_type,
            "filing_date": filing_date,
            "all_cells": cells,
        }

        records.append({
            "raw_record_id": _raw_record_id(case_number),
            "source_id": SOURCE_ID,
            "source_url": href or PORTAL_URL,
            "source_fetched_at": now,
            "raw_payload": raw_payload,
            "raw_text": None,
            "first_seen_at": now,
            "last_seen_at": now,
            "change_status": "NEW_RECORD",
            "parser_confidence": 80 if plaintiff else 60,
        })

    return records


def _pw_search(
    page,
    begin_date: str,
    end_date: str,
    case_type: str,
    last_name: str,
    verbose: bool,
) -> list[dict]:
    """Navigate directly to the CourtConnect results URL for one last_name letter."""
    from urllib.parse import quote

    ct_encoded = quote(case_type, safe="")
    url = (
        f"{BASE_URL}ck_public_qry_cpty.cp_personcase_srch_details"
        f"?backto=P&soundex_ind=&partial_ind=checked"
        f"&last_name={last_name}&first_name=&middle_name="
        f"&begin_date={begin_date}&end_date={end_date}"
        f"&case_type={ct_encoded}&id_code=&PageNo=1"
    )
    if verbose:
        print(
            f"  [Chancery] last_name={last_name!r} case_type={case_type!r}",
            flush=True,
        )

    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    time.sleep(2)

    records = _extract_results_from_page(page, case_type, verbose)
    if verbose:
        print(
            f"  [Chancery]   -> {len(records)} records",
            flush=True,
        )
    return records


# ---------------------------------------------------------------------------
# run_scraper — public API
# ---------------------------------------------------------------------------


def run_scraper(
    output_path: Path,
    *,
    days_back: int = 7,
    case_types: Optional[list[str]] = None,
    existing_path: Optional[Path] = None,
    verbose: bool = True,
    headless: bool = True,
) -> dict:
    """
    Scrape Chancery Court docket for foreclosure/partition/quiet title cases.

    Parameters
    ----------
    output_path:
        JSONL path to write.
    days_back:
        Number of calendar days back from today for the date range.
    case_types:
        CourtConnect case type codes. Defaults to ["FO", "PA", "QT"].
        Valid codes: "FO" (Foreclosure), "PA" (Partition), "QT" (Quiet Title),
        "EF" (Enjoin Foreclosure), "CO" (Delinquent Tax Collection),
        "LN" (Lien), "ALL".
    existing_path:
        Path to prior JSONL for merge (defaults to output_path).
    verbose:
        Print progress messages.
    headless:
        Run Playwright in headless mode.

    Returns
    -------
    Stats dict with counts and errors.
    """
    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(_PLAYWRIGHT_INSTALL_MSG)

    resolved_case_types = case_types if case_types is not None else DEFAULT_CASE_TYPES
    prior_path = existing_path if existing_path is not None else output_path

    today = datetime.now(timezone.utc)
    begin_dt = today - timedelta(days=days_back)
    begin_date = _format_date_cc(begin_dt)
    end_date = _format_date_cc(today)

    if verbose:
        print(
            f"[Chancery] Scraping {SOURCE_ID}: {begin_date} -> {end_date}, "
            f"case_types={resolved_case_types}",
            flush=True,
        )

    current: list[dict] = []
    errors: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        # Establish session by navigating through the disclaimer once
        _navigate_to_search_form(page, verbose)

        for ct in resolved_case_types:
            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                try:
                    records = _pw_search(page, begin_date, end_date, ct, letter, verbose)
                    current.extend(records)
                except Exception as exc:
                    msg = f"search_error(case_type={ct!r}, letter={letter!r}): {exc}"
                    errors.append(msg)
                    if verbose:
                        print(f"  [Chancery] ERROR: {msg}", flush=True)

        browser.close()

    # Merge rows for the same case: A-Z sweep returns one row per party, so a
    # single case may appear multiple times with different party_type values.
    # Build one record per case_number with plaintiff from PLAINTIFF rows
    # and defendant from DEFENDANT rows. Attorney rows are skipped.
    by_case: dict[str, dict] = {}
    for rec in current:
        rid = rec["raw_record_id"]
        pt = ((rec.get("raw_payload") or {}).get("party_type") or "").upper()
        if "ATTORNEY" in pt:
            continue
        if rid not in by_case:
            by_case[rid] = rec
        else:
            existing = by_case[rid]
            ep = existing["raw_payload"]
            rp = rec["raw_payload"]
            if "PLAINTIFF" in pt and not ep.get("plaintiff"):
                ep["plaintiff"] = rp.get("party_name")
                ep["party_name"] = rp.get("party_name")
                ep["party_type"] = rp.get("party_type")
            if pt == "DEFENDANT" and not ep.get("defendant"):
                ep["defendant"] = rp.get("party_name")
            if not ep.get("filing_date") and rp.get("filing_date"):
                ep["filing_date"] = rp["filing_date"]
    current = list(by_case.values())

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
        "case_types_searched": resolved_case_types,
        "date_from": begin_date,
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
            "Shelby County Chancery Court docket scraper (Playwright). "
            "Fetches foreclosure/partition/quiet title cases by date range. "
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
        "--case-type",
        action="append",
        dest="case_types",
        default=[],
        metavar="CASE_TYPE",
        help=(
            "CourtConnect case type code (repeatable). "
            "Examples: FO, PA, QT, EF, CO, LN, ALL. "
            "Default: FO PA QT."
        ),
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSONL path. Default: data/raw/chancery_court_shelby.jsonl",
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
            "Load the search form via Playwright and print all discovered form "
            "field names, then exit."
        ),
    )
    args = parser.parse_args()

    if args.probe_fields:
        if not PLAYWRIGHT_AVAILABLE:
            print(f"ERROR: {_PLAYWRIGHT_INSTALL_MSG}", file=sys.stderr)
            return 1
        print(f"[probe] Loading portal via Playwright: {PORTAL_URL}", flush=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.no_headless)
            context = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()
            search_frame = _navigate_to_search_form(page, verbose=True)
            fields_info: dict = {"portal_url": PORTAL_URL, "probe_mode": True}
            if search_frame:
                try:
                    fields = search_frame.evaluate("""
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
                            }));
                        }
                    """)
                    fields_info["fields"] = fields
                    fields_info["field_count"] = len(fields)
                    fields_info["search_frame_url"] = search_frame.url
                except Exception as exc:
                    fields_info["error"] = str(exc)
            else:
                fields_info["error"] = "Could not navigate to search form"
            browser.close()
        print(json.dumps(fields_info, indent=2))
        return 0

    if not PLAYWRIGHT_AVAILABLE:
        print(f"ERROR: {_PLAYWRIGHT_INSTALL_MSG}", file=sys.stderr)
        return 1

    out = (
        Path(args.out)
        if args.out
        else REPO_ROOT / "data" / "raw" / "chancery_court_shelby.jsonl"
    )
    ct = args.case_types if args.case_types else None
    stats = run_scraper(
        out,
        days_back=args.days_back,
        case_types=ct,
        headless=not args.no_headless,
    )
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
