"""
Shelby County Probate Court — CourtConnect case scraper.

Portal: https://prdata.shelbycountytn.gov/prweb/ck_public_qry_main.cp_main_idx
Covers: October 2013 – present

Architecture:
  - CourtConnect (ACS/Neumo) — direct GET requests, no Playwright needed
  - Results URL: prweb/ck_public_qry_cpty.cp_personcase_srch_details
    params: last_name (required), begin_date, end_date, case_type, partial_ind=checked
  - A–Z name sweep to work around the required last_name field
  - One row per party per case; merge to one record per case_number

Result table columns (confirmed):
  0: ID           — party ID (internal)
  1: Name         — party name (Last, First)
  2: Case ID      — "Case: PR036236 IN THE MATTER OF: ..." or "IN RE: ..."
  3: Party Type   — PETITIONER | RE: MATTER | ATTORNEY
  4: Party End Date (usually empty)
  5: Filing Date  — DD-MON-YYYY
  6: Case Status  — OPEN-OPEN | CLOSED-CLOSED etc.

Target case types (estate/death-related):
  01 — Probate of Will
  02 — Administration (intestate)
  05 — Administration, CTA
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    import requests
    from bs4 import BeautifulSoup
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

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

PORTAL_URL = "https://prdata.shelbycountytn.gov/prweb/ck_public_qry_main.cp_main_idx"
BASE_URL = "https://prdata.shelbycountytn.gov/prweb/"
RESULTS_PATH = "ck_public_qry_cpty.cp_personcase_srch_details"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

CASE_TYPE_LABELS: dict[str, str] = {
    "01": "PROBATE OF WILL",
    "02": "ADMINISTRATION",
    "03": "GUARDIANSHIP (MINOR)",
    "05": "ADMINISTRATION, CTA",
    "06": "CONSERVATORSHIP",
}

DEFAULT_CASE_TYPES = ["01", "02", "05"]

_CASE_ID_RE = re.compile(
    r"Case:\s*(\S+)\s+(?:IN\s+THE\s+MATTER\s+OF:|IN\s+RE:)\s*(.*)",
    re.IGNORECASE,
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
    return f"shelby_pr_{safe}"


def _format_date_cc(dt: datetime) -> str:
    return dt.strftime("%d-%b-%Y").upper()


def _parse_case_cell(text: str) -> tuple[str, str]:
    """Return (case_number, decedent_from_title) from the Case ID cell text."""
    m = _CASE_ID_RE.search(text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    # Fallback: grab first token after "Case:"
    parts = text.split()
    if len(parts) >= 2 and parts[0].upper() == "CASE:":
        return parts[1], ""
    return "", ""


def _normalize_status(raw: str) -> str:
    """'OPEN-OPEN' → 'OPEN', 'CLOSED-CLOSED' → 'CLOSED'."""
    return raw.split("-")[0].strip() if raw else raw


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


def _merge_with_prior(
    current: list, prior_by_id: dict, *, mark_missing_disappeared: bool = True
) -> list:
    """
    mark_missing_disappeared: when False, prior records not seen in `current`
    keep their existing change_status instead of flipping to DISAPPEARED.
    Callers should pass False when this run hit scrape errors — an empty or
    partial result set from a failed run is not trustworthy evidence that a
    previously-active record has actually disappeared from the source.
    """
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
        if mark_missing_disappeared:
            prev["change_status"] = "DISAPPEARED"
        out.append(prev)
    return out


# ---------------------------------------------------------------------------
# HTTP fetch
# ---------------------------------------------------------------------------


def _fetch_results(
    session: "requests.Session",
    last_name: str,
    begin_date: str,
    end_date: str,
    case_type: str,
    page: int = 1,
) -> str:
    from urllib.parse import urlencode
    params = urlencode({
        "backto": "P",
        "soundex_ind": "",
        "partial_ind": "checked",
        "last_name": last_name,
        "first_name": "",
        "middle_name": "",
        "begin_date": begin_date,
        "end_date": end_date,
        "case_type": case_type,
        "id_code": "",
        "PageNo": str(page),
    })
    url = BASE_URL + RESULTS_PATH + "?" + params
    r = session.get(url, timeout=20)
    r.raise_for_status()
    return r.text


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def _parse_page(html: str, case_type_label: str, now: str) -> list[dict]:
    """
    Parse one results page into a list of partial records (one row per party).
    Caller merges rows for the same case_number.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Find the results table (has th headers ID/Name/Case ID/Party Type/...)
    results_table = None
    for tbl in soup.find_all("table"):
        ths = [th.get_text(strip=True) for th in tbl.find_all("th")]
        if "Party Type" in ths and "Filing Date" in ths:
            results_table = tbl
            break

    if results_table is None:
        return []

    records = []
    for row in results_table.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
        if len(cells) < 6:
            continue

        party_type = cells[3].strip().upper() if len(cells) > 3 else ""
        # Skip pagination rows, header rows, attorney rows
        if not party_type or party_type == "ATTORNEY":
            continue
        if cells[0].startswith("Page:"):
            continue

        case_cell = cells[2].strip() if len(cells) > 2 else ""
        case_number, decedent_from_title = _parse_case_cell(case_cell)
        if not case_number:
            continue

        party_name = cells[1].strip() if len(cells) > 1 else ""
        filing_date = cells[5].strip() if len(cells) > 5 else ""
        case_status = _normalize_status(cells[6].strip() if len(cells) > 6 else "")

        link = row.find("a")
        href = ""
        if link and link.get("href"):
            raw_href = link["href"]
            if raw_href.startswith("http"):
                href = raw_href
            else:
                href = BASE_URL + raw_href.lstrip("/")

        records.append({
            "_case_number": case_number,
            "_party_type": party_type,
            "_party_name": party_name,
            "_decedent_from_title": decedent_from_title,
            "_filing_date": filing_date,
            "_case_status": case_status,
            "_href": href,
            "_case_type_label": case_type_label,
            "_now": now,
        })

    return records


def _has_next_page(html: str) -> bool:
    """Check if the results page shows a 'Next' pagination link."""
    soup = BeautifulSoup(html, "html.parser")
    return bool(soup.find("a", string=re.compile(r"Next", re.I)))


# ---------------------------------------------------------------------------
# Merge party rows → one record per case
# ---------------------------------------------------------------------------


def _build_records(party_rows: list[dict]) -> list[dict]:
    """
    Collapse multiple party rows for the same case into one record.
    RE: MATTER row → decedent_name
    PETITIONER row → petitioner_name
    """
    by_case: dict[str, dict] = {}

    for row in party_rows:
        cn = row["_case_number"]
        if cn not in by_case:
            by_case[cn] = {
                "case_number": cn,
                "case_type": row["_case_type_label"],
                "decedent_name": None,
                "petitioner_name": None,
                "filing_date": row["_filing_date"],
                "case_status": row["_case_status"],
                "href": row["_href"],
                "now": row["_now"],
            }
        rec = by_case[cn]

        pt = row["_party_type"]
        name = row["_party_name"]

        if "RE: MATTER" in pt or "IN RE" in pt:
            if not rec["decedent_name"]:
                rec["decedent_name"] = name
        elif "PETITIONER" in pt:
            if not rec["petitioner_name"]:
                rec["petitioner_name"] = name

        # Prefer RE: MATTER decedent; fall back to title extraction
        if not rec["decedent_name"] and row["_decedent_from_title"]:
            rec["decedent_name"] = row["_decedent_from_title"]

        if not rec["filing_date"] and row["_filing_date"]:
            rec["filing_date"] = row["_filing_date"]

    now = next(iter(by_case.values()), {}).get("now", _now_iso()) if by_case else _now_iso()
    records = []
    for cn, rec in by_case.items():
        confidence = 85 if (rec["decedent_name"] and rec["filing_date"]) else 65
        raw_payload = {
            "case_number": rec["case_number"],
            "case_type": rec["case_type"],
            "decedent_name": rec["decedent_name"],
            "petitioner_name": rec["petitioner_name"],
            "filing_date": rec["filing_date"],
            "case_status": rec["case_status"],
        }
        records.append({
            "raw_record_id": _raw_record_id(cn),
            "source_id": SOURCE_ID,
            "source_url": rec["href"] or PORTAL_URL,
            "source_fetched_at": rec["now"],
            "raw_payload": raw_payload,
            "raw_text": None,
            "first_seen_at": rec["now"],
            "last_seen_at": rec["now"],
            "change_status": "NEW_RECORD",
            "parser_confidence": confidence,
        })
    return records


# ---------------------------------------------------------------------------
# run_scraper — public API
# ---------------------------------------------------------------------------


def run_scraper(
    output_path: Path,
    *,
    days_back: int = 10,
    case_types: Optional[list[str]] = None,
    existing_path: Optional[Path] = None,
    verbose: bool = True,
    max_records: Optional[int] = None,
) -> dict:
    """
    Scrape Shelby County Probate Court (CourtConnect) for estate cases.

    Parameters
    ----------
    output_path:
        JSONL path to write.
    days_back:
        Number of calendar days back from today for the date range.
    case_types:
        List of case type codes. Defaults to ["01", "02", "05"].
        Valid: "01" (Probate of Will), "02" (Administration),
               "05" (Admin CTA), "06" (Conservatorship).
    existing_path:
        Path to prior JSONL for merge (defaults to output_path).
    verbose:
        Print progress messages.
    max_records:
        Cap on total records returned (for testing).
    """
    if not _REQUESTS_AVAILABLE:
        raise RuntimeError("requests and beautifulsoup4 required: pip install requests beautifulsoup4")

    resolved_types = case_types if case_types is not None else DEFAULT_CASE_TYPES
    prior_path = existing_path if existing_path is not None else output_path

    today = datetime.now(timezone.utc)
    begin_dt = today - timedelta(days=days_back)
    begin_date = _format_date_cc(begin_dt)
    end_date = _format_date_cc(today)

    if verbose:
        print(
            f"[Probate] Scraping {SOURCE_ID}: {begin_date} -> {end_date}, "
            f"case_types={resolved_types}",
            flush=True,
        )

    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    })

    now = _now_iso()
    all_party_rows: list[dict] = []
    errors: list[str] = []

    for ct in resolved_types:
        ct_label = CASE_TYPE_LABELS.get(ct, ct)
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            page_num = 1
            while True:
                try:
                    html = _fetch_results(session, letter, begin_date, end_date, ct, page_num)
                    rows = _parse_page(html, ct_label, now)
                    all_party_rows.extend(rows)
                    if verbose and rows:
                        print(
                            f"  [Probate] {ct_label} {letter!r} p{page_num}: {len(rows)} party rows",
                            flush=True,
                        )
                    if not _has_next_page(html):
                        break
                    page_num += 1
                except Exception as exc:
                    msg = f"fetch_error(ct={ct!r}, letter={letter!r}, page={page_num}): {exc}"
                    errors.append(msg)
                    if verbose:
                        print(f"  [Probate] ERROR: {msg}", flush=True)
                    break

    current = _build_records(all_party_rows)

    if max_records is not None:
        current = current[:max_records]

    if verbose:
        print(f"[Probate] {len(current)} cases pulled", flush=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    prior = _load_prior(prior_path)
    merged = _merge_with_prior(current, prior, mark_missing_disappeared=not errors)

    tmp = output_path.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for rec in merged:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(output_path)

    stats: dict = {
        "source_id": SOURCE_ID,
        "portal_url": PORTAL_URL,
        "days_back": days_back,
        "date_from": begin_date,
        "date_to": end_date,
        "case_types": resolved_types,
        "records_pulled": len(current),
        "prior_count": len(prior),
        "total_after_merge": len(merged),
        "new_record_count": sum(1 for r in merged if r["change_status"] == "NEW_RECORD"),
        "same_record_count": sum(1 for r in merged if r["change_status"] == "SAME"),
        "updated_record_count": sum(1 for r in merged if r["change_status"] == "UPDATED"),
        "disappeared_record_count": sum(1 for r in merged if r["change_status"] == "DISAPPEARED"),
        "output_path": str(output_path),
        "errors": errors,
    }
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Shelby County Probate Court scraper (CourtConnect / requests). "
            "Fetches estate cases by date range. No Playwright required."
        )
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=10,
        metavar="N",
        help="Number of calendar days back from today (default 10).",
    )
    parser.add_argument(
        "--case-type",
        action="append",
        dest="case_types",
        default=[],
        metavar="CODE",
        help=(
            "Case type code (repeatable). Valid: 01 02 05 06. "
            "Default: 01 02 05 (Probate of Will, Administration, Admin CTA)."
        ),
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        metavar="N",
        help="Cap on total records returned (for testing).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSONL path. Default: data/raw/probate_court_shelby.jsonl",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Fetch one page (last_name=A, case_type=01) and print raw rows, then exit.",
    )
    args = parser.parse_args()

    if args.probe:
        if not _REQUESTS_AVAILABLE:
            print("ERROR: pip install requests beautifulsoup4", file=sys.stderr)
            return 1
        today = datetime.now(timezone.utc)
        begin = _format_date_cc(today - timedelta(days=30))
        end = _format_date_cc(today)
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        html = _fetch_results(session, "A", begin, end, "01")
        rows = _parse_page(html, "PROBATE OF WILL", _now_iso())
        print(f"party rows: {len(rows)}")
        for r in rows[:10]:
            print(f"  {r['_case_number']} | {r['_party_type']} | {r['_party_name']} | {r['_filing_date']}")
        records = _build_records(rows)
        print(f"cases: {len(records)}")
        for r in records[:5]:
            p = r["raw_payload"]
            print(f"  {p['case_number']} | {p['decedent_name']} | {p['filing_date']} | {p['case_status']}")
        return 0

    if not _REQUESTS_AVAILABLE:
        print("ERROR: pip install requests beautifulsoup4", file=sys.stderr)
        return 1

    out = (
        Path(args.out)
        if args.out
        else REPO_ROOT / "data" / "raw" / "probate_court_shelby.jsonl"
    )
    ct = args.case_types if args.case_types else None
    stats = run_scraper(
        out,
        days_back=args.days_back,
        case_types=ct,
        max_records=args.max_records,
    )
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
