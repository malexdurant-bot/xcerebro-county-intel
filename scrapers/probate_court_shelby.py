"""
Shelby County Probate Court — case search scraper (requests-based).

Portal: https://probatedata.shelbycountytn.gov/ProbateCourt/
Search: https://probatedata.shelbycountytn.gov/ProbateCourt/faces/app/search.xhtml

Flow discovered 2026-07-21:
  1. GET search.xhtml  →  extract ViewState
  2. POST radio AJAX   →  set customRadio=1 (name search)
  3. POST pSub AJAX    →  server runs search + stores results in session;
                          returns EvaluationException (JSF render-phase NPE, harmless)
  4. GET root page     →  extract root ViewState + DataTable rowCount from JS
  5. POST DataTable pagination AJAX  →  5 rows per call; repeat for all pages

  Note: opt1Input (lastName) IS processed by JSF because after step 2 the
  server-side component tree marks it as enabled. Different names return
  different result sets via LIKE '%name%' match. Empty string returns all cases.

Date filtering: the DataTable is sorted by case number ASC (oldest first).
We request pages from the END (highest first-row offset) and work backwards,
stopping when Date Filed < cutoff.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

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
SEARCH_URL = "https://probatedata.shelbycountytn.gov/ProbateCourt/faces/app/search.xhtml"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

ROWS_PER_PAGE = 5

_DATE_FMTS = ("%m-%d-%Y", "%m/%d/%Y", "%Y-%m-%d")

# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _parse_date(s: str) -> Optional[date]:
    s = s.strip()
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _is_placeholder_date(s: str) -> bool:
    """01-01-1901 is the portal's null-date placeholder."""
    return s.strip() in ("01-01-1901", "01/01/1901")


# ---------------------------------------------------------------------------
# Record helpers
# ---------------------------------------------------------------------------

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


def _merge_with_prior(current: list, prior_by_id: dict) -> list:
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
# HTTP session helpers
# ---------------------------------------------------------------------------

def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
    })
    return s


_AJAX_HEADERS = {
    "Accept": "application/xml, text/xml, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Faces-Request": "partial/ajax",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://probatedata.shelbycountytn.gov",
}


def _extract_viewstate(html: str) -> Optional[str]:
    m = re.search(r'<input[^>]+name="javax\.faces\.ViewState"[^>]+value="([^"]+)"', html)
    return m.group(1) if m else None


def _extract_viewstate_from_ajax(xml: str) -> Optional[str]:
    m = re.search(r'<update id="[^"]*ViewState[^"]*"><!\[CDATA\[([^\]]+)', xml)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------------------
# Portal search flow
# ---------------------------------------------------------------------------

def _setup_search(session: requests.Session, last_name: str = "", verbose: bool = False):
    """
    Execute the full search flow and return (root_viewstate, row_count).

    last_name: search term; empty string returns ALL cases (LIKE '%%').
    """
    # Step 1: GET search.xhtml
    r = session.get(
        SEARCH_URL, timeout=20,
        headers={"Accept": "text/html,application/xhtml+xml"},
    )
    r.raise_for_status()
    vs = _extract_viewstate(r.text)
    if not vs:
        raise RuntimeError("No ViewState found on search.xhtml")

    # Step 2: Radio AJAX — set customRadio=1 (name search)
    r_radio = session.post(
        SEARCH_URL,
        headers={**_AJAX_HEADERS, "Referer": SEARCH_URL},
        timeout=15,
        data={
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": "searchForm:customRadio",
            "javax.faces.partial.execute": "searchForm:customRadio",
            "javax.faces.partial.render": "searchForm",
            "javax.faces.behavior.event": "change",
            "javax.faces.partial.event": "change",
            "searchForm": "searchForm",
            "searchForm:customRadio": "1",
            "javax.faces.ViewState": vs,
        },
    )
    r_radio.raise_for_status()
    vs2 = _extract_viewstate_from_ajax(r_radio.text) or vs

    # Step 3: pSub AJAX — executes search; EvaluationException is expected
    session.post(
        SEARCH_URL,
        headers={**_AJAX_HEADERS, "Referer": SEARCH_URL},
        timeout=25,
        data={
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": "searchForm:pSub",
            "javax.faces.partial.execute": "@all",
            "javax.faces.partial.render": "@all",
            "searchForm:pSub": "searchForm:pSub",
            "searchForm": "searchForm",
            "searchForm:customRadio": "1",
            "searchForm:opt1Input": last_name,
            "searchForm:opt1Input1": "",
            "javax.faces.ViewState": vs2,
        },
    )
    # Response is EvaluationException XML — ignore it; results are in session

    # Step 4: GET root page → fresh ViewState + rowCount
    r_root = session.get(
        PORTAL_URL, timeout=20,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Referer": SEARCH_URL,
        },
    )
    r_root.raise_for_status()
    root_html = r_root.text

    root_vs = _extract_viewstate(root_html)
    if not root_vs:
        raise RuntimeError("No ViewState found on root page after search")

    rc_match = re.search(r"rowCount:(\d+)", root_html)
    row_count = int(rc_match.group(1)) if rc_match else 0

    if verbose:
        print(
            f"  [Probate] search={last_name!r} rowCount={row_count}",
            flush=True,
        )

    return root_vs, row_count


def _fetch_page(
    session: requests.Session,
    vs: str,
    first_row: int,
    rows: int = ROWS_PER_PAGE,
) -> list[dict]:
    """
    Request one page of DataTable results. Returns parsed row dicts.

    Row dict: {case_number, name, case_type, date_filed, date_of_death}
    Raises ValueError if the response looks like a ViewState expiry (no CDATA).
    """
    r = session.post(
        SEARCH_URL,
        headers={**_AJAX_HEADERS, "Referer": PORTAL_URL},
        timeout=20,
        data={
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": "searchForm:gilistTable",
            "javax.faces.partial.execute": "searchForm:gilistTable",
            "javax.faces.partial.render": "searchForm:gilistTable",
            "searchForm:gilistTable_pagination": "true",
            "searchForm:gilistTable_first": str(first_row),
            "searchForm:gilistTable_rows": str(rows),
            "searchForm:gilistTable_skipChildren": "true",
            "searchForm:gilistTable_encodeFeature": "true",
            "searchForm": "searchForm",
            "javax.faces.ViewState": vs,
        },
    )
    r.raise_for_status()
    result = _parse_datatable_ajax(r.text)
    # A small response with no CDATA indicates ViewState/session expiry
    if not result and len(r.content) < 1000:
        raise ValueError(f"ViewState likely expired (response {len(r.content)}b, no rows)")
    return result


def _parse_datatable_ajax(xml: str) -> list[dict]:
    """Extract row dicts from DataTable AJAX partial-response XML."""
    cdata = re.search(
        r'<update id="searchForm:gilistTable"><!\[CDATA\[(.*?)\]\]>',
        xml,
        re.DOTALL,
    )
    if not cdata:
        return []
    html = cdata.group(1)
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.find_all("tr", {"data-ri": True}):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        cells = [td.get_text(strip=True) for td in tds]
        rows.append({
            "case_number": cells[0],
            "name":        cells[1],
            "case_type":   cells[2],
            "date_filed":  cells[3],
            "date_of_death": cells[4],
        })
    return rows


# ---------------------------------------------------------------------------
# Sweep terms: empty/None search_term → sweep these vowels to cover all names
# (Every English last name contains at least one vowel.)
# ---------------------------------------------------------------------------

_SWEEP_TERMS = ["A", "E", "I", "O", "U"]


# ---------------------------------------------------------------------------
# _scrape_term — internal: one search term, backwards pagination
# ---------------------------------------------------------------------------

def _scrape_term(
    search_term: str,
    cutoff: Optional[date],
    now: str,
    *,
    max_pages: int = 0,
    verbose: bool = True,
) -> tuple[list[dict], list[str], int, int]:
    """
    Run one search term, paginate backwards, stop at cutoff.
    Returns (records, errors, pages_fetched, row_count).
    """
    session = _make_session()
    records: list[dict] = []
    errors: list[str] = []

    try:
        root_vs, row_count = _setup_search(session, search_term, verbose=verbose)
    except Exception as exc:
        return [], [str(exc)], 0, 0

    if row_count == 0:
        return [], [], 0, 0

    pages_fetched = 0
    stop_early = False
    last_first = ((row_count - 1) // ROWS_PER_PAGE) * ROWS_PER_PAGE
    consecutive_errors = 0

    for first_row in range(last_first, -1, -ROWS_PER_PAGE):
        if stop_early:
            break
        if max_pages > 0 and pages_fetched >= max_pages:
            break

        try:
            page_rows = _fetch_page(session, root_vs, first_row)
            consecutive_errors = 0
        except ValueError as exc:
            # ViewState/session expiry — re-establish the session
            if verbose:
                print(f"  [Probate] session refresh at first_row={first_row}: {exc}", flush=True)
            try:
                root_vs, _ = _setup_search(session, search_term, verbose=False)
                page_rows = _fetch_page(session, root_vs, first_row)
                consecutive_errors = 0
            except Exception as exc2:
                msg = f"term={search_term!r} page first={first_row} (retry): {exc2}"
                errors.append(msg)
                if verbose:
                    print(f"  [Probate] {msg}", flush=True)
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    break
                time.sleep(2)
                continue
        except Exception as exc:
            msg = f"term={search_term!r} page first={first_row}: {exc}"
            errors.append(msg)
            if verbose:
                print(f"  [Probate] {msg}", flush=True)
            consecutive_errors += 1
            if consecutive_errors >= 3:
                break
            time.sleep(1)
            continue

        pages_fetched += 1

        for row in reversed(page_rows):
            case_num = (row.get("case_number") or "").strip()
            if not case_num:
                continue

            date_filed_str = (row.get("date_filed") or "").strip()
            filing_date = _parse_date(date_filed_str) if date_filed_str else None

            if cutoff and filing_date and filing_date < cutoff:
                stop_early = True
                break

            raw_payload: dict = {
                "case_number":   case_num,
                "name":          (row.get("name") or "").strip(),
                "case_type":     (row.get("case_type") or "").strip(),
                "date_filed":    date_filed_str,
                "date_of_death": (row.get("date_of_death") or "").strip(),
            }
            records.append({
                "raw_record_id":    _raw_record_id(case_num),
                "source_id":        SOURCE_ID,
                "source_url":       PORTAL_URL,
                "source_fetched_at": now,
                "raw_payload":      raw_payload,
                "raw_text":         None,
                "first_seen_at":    now,
                "last_seen_at":     now,
                "change_status":    "NEW_RECORD",
                "parser_confidence": 80,
            })

        time.sleep(0.15)

    return records, errors, pages_fetched, row_count


# ---------------------------------------------------------------------------
# run_scraper — public API
# ---------------------------------------------------------------------------

def run_scraper(
    output_path: Path,
    *,
    days_back: int = 0,
    existing_path: Optional[Path] = None,
    verbose: bool = True,
    search_term: Optional[str] = None,
    max_pages: int = 0,
) -> dict:
    """
    Scrape Shelby County Probate Court.

    NOTE: This portal is a historical archive (cases through ~Sep 2013).
    The default days_back=0 collects all available records.

    When search_term is None or "" (default), sweeps vowels A/E/I/O/U so that
    every English last name is matched by at least one search pass.  Supply an
    explicit search_term to restrict to one name-contains query.

    Parameters
    ----------
    output_path     : JSONL path to write.
    days_back       : Calendar days back from today to keep. 0 = keep all (default).
    existing_path   : Prior JSONL for merge (defaults to output_path).
    verbose         : Print progress.
    search_term     : Name search term. None/"" → vowel sweep (recommended).
    max_pages       : Safety cap per term. 0 = no cap.
    """
    prior_path = existing_path if existing_path is not None else output_path
    today = date.today()
    cutoff = (today - timedelta(days=days_back)) if days_back > 0 else None

    sweep_mode = not search_term  # True when None or ""
    terms = _SWEEP_TERMS if sweep_mode else [search_term]

    if verbose:
        mode = f"vowel sweep {_SWEEP_TERMS}" if sweep_mode else f"term={search_term!r}"
        print(
            f"[Probate] Scraping {SOURCE_ID}: {mode}, "
            f"days_back={days_back}, cutoff={cutoff}",
            flush=True,
        )

    now = _now_iso()
    all_records: dict[str, dict] = {}  # raw_record_id → record (dedup)
    all_errors: list[str] = []
    total_pages = 0
    row_counts: dict[str, int] = {}

    for term in terms:
        recs, errs, pages, rc = _scrape_term(
            term, cutoff, now, max_pages=max_pages, verbose=verbose
        )
        row_counts[term] = rc
        total_pages += pages
        all_errors.extend(errs)
        for rec in recs:
            rid = rec["raw_record_id"]
            if rid not in all_records:
                all_records[rid] = rec
        if verbose:
            print(
                f"  [Probate] term={term!r} rowCount={rc} pages={pages} "
                f"new_this_term={len(recs)} unique_total={len(all_records)}",
                flush=True,
            )

    records = list(all_records.values())

    output_path.parent.mkdir(parents=True, exist_ok=True)
    prior = _load_prior(prior_path)
    merged = _merge_with_prior(records, prior)

    tmp = output_path.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for rec in merged:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(output_path)

    stats: dict = {
        "source_id":              SOURCE_ID,
        "portal_url":             PORTAL_URL,
        "search_url":             SEARCH_URL,
        "days_back":              days_back,
        "cutoff_date":            str(cutoff) if cutoff else None,
        "sweep_mode":             sweep_mode,
        "search_terms":           terms,
        "row_counts_per_term":    row_counts,
        "total_pages_fetched":    total_pages,
        "records_pulled":         len(records),
        "prior_count":            len(prior),
        "total_after_merge":      len(merged),
        "new_record_count":       sum(1 for r in merged if r["change_status"] == "NEW_RECORD"),
        "same_record_count":      sum(1 for r in merged if r["change_status"] == "SAME"),
        "updated_record_count":   sum(1 for r in merged if r["change_status"] == "UPDATED"),
        "disappeared_record_count": sum(1 for r in merged if r["change_status"] == "DISAPPEARED"),
        "output_path":            str(output_path),
        "errors":                 all_errors,
    }
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Shelby County Probate Court scraper (requests-based). "
            "Does a name search then paginates the DataTable backwards "
            "(newest-first) until the date cutoff."
        )
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=0,
        help="Calendar days back from today to keep (default 0 = all; portal is historical through ~2013).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSONL path. Default: data/raw/probate_court_shelby.jsonl",
    )
    parser.add_argument(
        "--search-term",
        default=None,
        help='Name search term (default: None = vowel sweep A/E/I/O/U).',
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Max DataTable pages to fetch (default: 0 = no cap).",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Run one search page and print first 10 rows, then exit.",
    )
    args = parser.parse_args()

    if args.probe:
        probe_term = args.search_term or "E"
        session = _make_session()
        print(f"[probe] Searching: term={probe_term!r}", flush=True)
        root_vs, row_count = _setup_search(session, probe_term, verbose=True)
        print(f"[probe] rowCount={row_count}, fetching last 2 pages...")
        last_first = ((row_count - 1) // ROWS_PER_PAGE) * ROWS_PER_PAGE
        for fi in [last_first, max(0, last_first - ROWS_PER_PAGE)]:
            rows = _fetch_page(session, root_vs, fi)
            print(f"  page first={fi}:")
            for r in rows:
                print(f"    {r}")
        return 0

    out = (
        Path(args.out)
        if args.out
        else REPO_ROOT / "data" / "raw" / "probate_court_shelby.jsonl"
    )
    stats = run_scraper(
        out,
        days_back=args.days_back,
        search_term=args.search_term,
        max_pages=args.max_pages,
        verbose=True,
    )
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
