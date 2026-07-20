"""
Shelby County Probate Court — case search scraper.

Portal: https://probatedata.shelbycountytn.gov/ProbateCourt/
(Redirects from original pls URL to JSF app)

Architecture: JavaServer Faces (JSF) app — NOT CourtConnect.
Direct page navigation to:
  https://probatedata.shelbycountytn.gov/ProbateCourt/faces/app/search.xhtml

Form fields (CONFIRMED by probe):
  - searchForm                 : hidden, value "searchForm"
  - searchForm:customRadio     : radio — "1" = name search, "2" = case number search
  - searchForm:opt1Input       : text — last name (for radio=1)
  - searchForm:opt1Input1      : text — first name (for radio=1)
  - searchForm:opt2Input       : text — case number (for radio=2)
  - searchForm:opt2Input2      : text — additional case field (for radio=2)
  - searchForm:value1_input    : checkbox — "Admin: No" (leave unchecked)
  - javax.faces.ViewState      : hidden — JSF ViewState (must be read from page each time)
  - Submit: button element in the form

Search strategy:
  - This portal ONLY supports name-based or case-number-based search.
  - No date-range bulk search available.
  - For bulk use: search by last_name="%", but server may reject wildcard.
  - Fallback: return 0 records with SEARCH_ONLY_NO_BULK note in stats.

Requires: pip install playwright && playwright install chromium
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
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

SOURCE_ID = "probate_court_shelby"

PORTAL_URL = "https://probatedata.shelbycountytn.gov/ProbateCourt/"
SEARCH_URL = (
    "https://probatedata.shelbycountytn.gov/ProbateCourt/faces/app/search.xhtml"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

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
    return f"shelby_pb_{safe}"


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
# Playwright helpers
# ---------------------------------------------------------------------------


def _wait_settled(page, timeout: int = 30_000) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:
            pass


def _load_search_page(page, verbose: bool) -> bool:
    """Load the JSF search page. Returns True on success."""
    if verbose:
        print(f"  [Probate] Loading search page: {SEARCH_URL}", flush=True)

    try:
        page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60_000)
        _wait_settled(page)
    except Exception as exc:
        if verbose:
            print(f"  [Probate] WARNING: page load error: {exc}", flush=True)
        # Try portal root instead
        try:
            page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30_000)
            _wait_settled(page)
            # Navigate to search page
            try:
                page.click('a[href*="search"]', timeout=5_000)
                _wait_settled(page)
            except Exception:
                pass
        except Exception as exc2:
            if verbose:
                print(f"  [Probate] ERROR: fallback load failed: {exc2}", flush=True)
            return False

    # Verify we're on the search page — look for the form
    try:
        page.wait_for_selector(
            'input[id*="opt1Input"], input[name*="opt1Input"], '
            'input[id*="customRadio"], form[id*="searchForm"]',
            timeout=15_000,
        )
        return True
    except Exception:
        if verbose:
            page_url = page.url
            print(
                f"  [Probate] WARNING: search form not found at {page_url}", flush=True
            )
        return False


def _fill_and_submit_name_search(
    page, last_name: str, first_name: str, verbose: bool
) -> bool:
    """
    Fill and submit a name-based search on the JSF probate search form.
    Returns True if submission appeared successful.
    """
    # Select radio button for name search (value="1")
    try:
        # JSF radio buttons typically have id="searchForm:customRadio:0" for value=1
        # Try multiple selector strategies
        radio_selected = False
        for selector in (
            'input[id*="customRadio"][value="1"]',
            'input[name*="customRadio"][value="1"]',
            'input[type="radio"][value="1"]',
        ):
            try:
                el = page.query_selector(selector)
                if el:
                    el.click()
                    radio_selected = True
                    break
            except Exception:
                pass

        if not radio_selected and verbose:
            print(
                "  [Probate] WARNING: could not select name search radio button",
                flush=True,
            )
    except Exception as exc:
        if verbose:
            print(f"  [Probate] WARNING: radio selection error: {exc}", flush=True)

    time.sleep(0.3)

    # Fill last name field
    last_name_filled = False
    for selector in (
        'input[id*="opt1Input"]:not([id*="opt1Input1"])',
        'input[name*="opt1Input"]:not([name*="opt1Input1"])',
        '#searchForm\\:opt1Input',
        'input[id="searchForm:opt1Input"]',
    ):
        try:
            el = page.query_selector(selector)
            if el:
                el.fill(last_name)
                last_name_filled = True
                break
        except Exception:
            pass

    if not last_name_filled:
        # Try getting all text inputs and fill the first visible one
        try:
            inputs = page.query_selector_all('input[type="text"], input[type=""]')
            for inp in inputs:
                if inp.is_visible():
                    inp.fill(last_name)
                    last_name_filled = True
                    break
        except Exception:
            pass

    if not last_name_filled and verbose:
        print(
            "  [Probate] WARNING: could not fill last name field",
            flush=True,
        )

    # Fill first name field (opt1Input1)
    if first_name:
        for selector in (
            'input[id*="opt1Input1"]',
            'input[name*="opt1Input1"]',
            'input[id="searchForm:opt1Input1"]',
        ):
            try:
                el = page.query_selector(selector)
                if el:
                    el.fill(first_name)
                    break
            except Exception:
                pass

    if verbose:
        print(
            f"  [Probate] Submitting name search: last_name={last_name!r}, "
            f"first_name={first_name!r}",
            flush=True,
        )

    # Click submit button
    submitted = False
    for selector in (
        'input[type="submit"]',
        'button[type="submit"]',
        'input[id*="search"]',
        'button[id*="search"]',
        'button[id*="j_idt"]',
        'input[value*="Search"]',
        'button:has-text("Search")',
    ):
        try:
            el = page.query_selector(selector)
            if el and el.is_visible():
                el.click()
                submitted = True
                break
        except Exception:
            pass

    if not submitted:
        # Fallback: press Enter on the last name field
        try:
            el = page.query_selector('input[id*="opt1Input"]')
            if el:
                el.press("Enter")
                submitted = True
        except Exception:
            pass

    if not submitted and verbose:
        print("  [Probate] WARNING: could not submit form", flush=True)

    _wait_settled(page)
    time.sleep(1)

    return submitted


def _extract_probate_results(page, verbose: bool) -> list[dict]:
    """
    Extract case rows from the JSF Probate Court results page.
    """
    now = _now_iso()
    records: list[dict] = []

    # Check for "no results" message
    try:
        page_text = page.inner_text("body") or ""
        if any(
            phrase in page_text.lower()
            for phrase in (
                "no records",
                "no results",
                "0 records",
                "no cases found",
                "search returned no",
            )
        ):
            if verbose:
                print("  [Probate] No records found", flush=True)
            return []

        # Check for error messages
        if any(
            phrase in page_text.lower()
            for phrase in ("invalid search", "error", "exception")
        ):
            if verbose:
                print(
                    f"  [Probate] Server returned error/invalid search: "
                    f"{page_text[:200]}",
                    flush=True,
                )
    except Exception:
        pass

    # Extract rows using JavaScript
    try:
        rows_data = page.evaluate("""
            () => {
                const results = [];
                // JSF results are typically in a datatable or panel
                // Look for links that go to case details
                const links = document.querySelectorAll(
                    'a[href*="case"], a[href*="detail"], a[onclick*="case"]'
                );
                for (const link of links) {
                    const row = link.closest('tr') || link.closest('.ui-datatable-row');
                    if (!row) continue;

                    const cells = row.querySelectorAll('td');
                    const cellTexts = Array.from(cells).map(
                        c => c.innerText.trim().replace(/\\s+/g, ' ')
                    );
                    const href = new URL(
                        link.getAttribute('href') || '#',
                        document.baseURI
                    ).href;

                    results.push({
                        case_number: link.innerText.trim(),
                        href: href,
                        cells: cellTexts,
                    });
                }

                // Also try extracting from any data table rows directly
                if (results.length === 0) {
                    const tables = document.querySelectorAll(
                        '.ui-datatable table, table[id*="result"], table[id*="case"]'
                    );
                    for (const table of tables) {
                        const rows = table.querySelectorAll('tbody tr');
                        for (const row of rows) {
                            const cells = row.querySelectorAll('td');
                            if (cells.length < 2) continue;
                            const link = row.querySelector('a');
                            const cellTexts = Array.from(cells).map(
                                c => c.innerText.trim().replace(/\\s+/g, ' ')
                            );
                            results.push({
                                case_number: link ? link.innerText.trim() : cellTexts[0],
                                href: link
                                    ? new URL(
                                        link.getAttribute('href') || '#',
                                        document.baseURI
                                    ).href
                                    : '',
                                cells: cellTexts,
                            });
                        }
                    }
                }

                return results;
            }
        """)
    except Exception as exc:
        if verbose:
            print(f"  [Probate] JS extraction error: {exc}", flush=True)
        rows_data = []

    if verbose:
        print(f"  [Probate] Raw rows extracted: {len(rows_data)}", flush=True)

    for row in rows_data:
        case_number = (row.get("case_number") or "").strip()
        if not case_number or case_number.lower() in ("search", "reset", ""):
            continue

        cells = row.get("cells") or []
        href = (row.get("href") or "").strip()
        if href == "#" or not href:
            href = SEARCH_URL

        # Column mapping: JSF Probate table typically shows:
        # [0] Case Number | [1] Case Name/Decedent | [2] Filing Date | [3] Status
        # (exact layout TBD from live probe — we extract best-effort)
        case_name = cells[1] if len(cells) > 1 else None
        filing_date = cells[2] if len(cells) > 2 else None
        case_status = cells[3] if len(cells) > 3 else None
        personal_rep = cells[4] if len(cells) > 4 else None

        raw_payload: dict = {
            "case_number": case_number,
            "case_name": case_name,
            "filing_date": filing_date,
            "personal_rep": personal_rep,
            "case_status": case_status,
            "all_cells": cells,
        }

        records.append({
            "raw_record_id": _raw_record_id(case_number),
            "source_id": SOURCE_ID,
            "source_url": href,
            "source_fetched_at": now,
            "raw_payload": raw_payload,
            "raw_text": None,
            "first_seen_at": now,
            "last_seen_at": now,
            "change_status": "NEW_RECORD",
            "parser_confidence": 75,
        })

    return records


# ---------------------------------------------------------------------------
# run_scraper — public API
# ---------------------------------------------------------------------------


def run_scraper(
    output_path: Path,
    *,
    last_name: str = "%",
    first_name: str = "",
    existing_path: Optional[Path] = None,
    verbose: bool = True,
    headless: bool = True,
) -> dict:
    """
    Scrape Shelby County Probate Court by name search.

    LIMITATION: This portal only supports name-based or case-number-based search.
    Bulk date-range search is not available. The wildcard "%" for last_name may
    be rejected by the server. If rejected, returns 0 records with
    search_limitation note in stats.

    Parameters
    ----------
    output_path:
        JSONL path to write.
    last_name:
        Last name to search. Use "%" for wildcard (may be rejected by server).
    first_name:
        First name to search (leave empty for broad search).
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

    prior_path = existing_path if existing_path is not None else output_path

    if verbose:
        print(
            f"[Probate] Scraping {SOURCE_ID}: last_name={last_name!r}, "
            f"first_name={first_name!r}",
            flush=True,
        )

    current: list[dict] = []
    errors: list[str] = []
    search_limitation: Optional[str] = None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        try:
            form_loaded = _load_search_page(page, verbose)
            if not form_loaded:
                errors.append("Could not load JSF search form")
                search_limitation = (
                    "SEARCH_ONLY_NO_BULK: JSF form could not be loaded via Playwright. "
                    "This portal requires a valid session to access the search page."
                )
            else:
                submitted = _fill_and_submit_name_search(
                    page, last_name, first_name, verbose
                )
                if not submitted:
                    errors.append("Form submission failed")
                    search_limitation = (
                        "SEARCH_ONLY_NO_BULK: Could not submit the JSF search form. "
                        "Wildcard or empty search may have been rejected."
                    )
                else:
                    records = _extract_probate_results(page, verbose)
                    current.extend(records)

                    if len(records) == 0 and not errors:
                        # Check if wildcard was rejected
                        try:
                            page_text = page.inner_text("body") or ""
                            if any(
                                p in page_text.lower()
                                for p in ("invalid", "required", "enter a name")
                            ):
                                search_limitation = (
                                    "SEARCH_ONLY_NO_BULK: Server rejected wildcard search "
                                    f"for last_name={last_name!r}. "
                                    "This source requires a specific last name. "
                                    "Use a seeded name list for bulk extraction."
                                )
                        except Exception:
                            pass

        except Exception as exc:
            msg = f"scrape_error: {exc}"
            errors.append(msg)
            if verbose:
                print(f"  [Probate] ERROR: {msg}", flush=True)

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
        "search_url": SEARCH_URL,
        "last_name_searched": last_name,
        "first_name_searched": first_name,
        "records_pulled": len(current),
        "prior_count": len(prior),
        "total_after_merge": len(merged),
        "new_record_count": sum(1 for r in merged if r["change_status"] == "NEW_RECORD"),
        "same_record_count": sum(1 for r in merged if r["change_status"] == "SAME"),
        "updated_record_count": sum(1 for r in merged if r["change_status"] == "UPDATED"),
        "disappeared_record_count": sum(
            1 for r in merged if r["change_status"] == "DISAPPEARED"
        ),
        "output_path": str(output_path),
        "errors": errors,
        "playwright_available": PLAYWRIGHT_AVAILABLE,
    }
    if search_limitation:
        stats["search_limitation"] = search_limitation

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Shelby County Probate Court case scraper (Playwright). "
            "Searches by party name (date-range bulk search not available). "
            "Requires: pip install playwright && playwright install chromium."
        )
    )
    parser.add_argument(
        "--last-name",
        default="%",
        help="Last name to search. Use %% for wildcard (may be rejected by server).",
    )
    parser.add_argument(
        "--first-name",
        default="",
        help="First name to search (default: empty).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSONL path. Default: data/raw/probate_court_shelby.jsonl",
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

        print(f"[probe] Loading JSF search page: {SEARCH_URL}", flush=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.no_headless)
            context = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()
            page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60_000)
            _wait_settled(page)
            time.sleep(2)

            fields_info: dict = {
                "portal_url": PORTAL_URL,
                "search_url": SEARCH_URL,
                "probe_mode": True,
                "final_url": page.url,
            }
            try:
                fields = page.evaluate("""
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
                            visible: el.offsetParent !== null,
                        }));
                    }
                """)
                fields_info["fields"] = fields
                fields_info["field_count"] = len(fields)

                # Also get hidden ViewState
                vs = page.evaluate("""
                    () => {
                        const el = document.querySelector(
                            'input[name="javax.faces.ViewState"]'
                        );
                        return el ? el.value.substring(0, 50) + '...' : null;
                    }
                """)
                fields_info["javax_faces_viewstate_present"] = vs is not None
                fields_info["javax_faces_viewstate_prefix"] = vs

            except Exception as exc:
                fields_info["error"] = str(exc)

            browser.close()

        print(json.dumps(fields_info, indent=2))
        return 0

    if not PLAYWRIGHT_AVAILABLE:
        print(f"ERROR: {_PLAYWRIGHT_INSTALL_MSG}", file=sys.stderr)
        return 1

    out = (
        Path(args.out)
        if args.out
        else REPO_ROOT / "data" / "raw" / "probate_court_shelby.jsonl"
    )
    stats = run_scraper(
        out,
        last_name=args.last_name,
        first_name=args.first_name,
        headless=not args.no_headless,
    )
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
