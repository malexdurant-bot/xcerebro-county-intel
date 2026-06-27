"""
Maricopa County Superior Court Probate docket adapter — Phase 2 (Playwright).

Searches the public portal at:

    https://www.superiorcourt.maricopa.gov/docket/ProbateCourtCases/caseSearch.asp

Playwright is required because the portal loads Google reCAPTCHA v3, which
silently rejects urllib POST submissions that lack a valid client-side token.
A real browser (Playwright) generates the token automatically.

Portal structure (confirmed 2026-06-26):
  - Form POST returns a results page at caseSearchResults.asp
  - Results use Bootstrap div rows (#tblForms .row a[href*="caseInfo"])
  - Two columns per result: Case Number + Party Name/Business Name
  - Full case details (filing date, type, status) are on the linked caseInfo.asp

Supports three modes:
  --last-name / --first-name   : party name search
  --case-number                : direct case number lookup (repeatable)
  --probe-fields               : urllib GET to print discovered form field names

Requires: pip install playwright && playwright install chromium

The adapter writes to data/raw/superior_court_probate.jsonl via the
standard atomic tmp→replace pattern and returns a stats dict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import ssl
import sys
import urllib.request
import warnings
from datetime import datetime, timezone
from html.parser import HTMLParser
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

# SSL context for probe mode urllib GET (portal has self-signed cert in chain)
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# ---------------------------------------------------------------------------
# Repo bootstrap
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOURCE_ID = "superior_court_probate"

PORTAL_URL = (
    "https://www.superiorcourt.maricopa.gov/docket/ProbateCourtCases/caseSearch.asp"
)
DETAIL_BASE_URL = (
    "https://www.superiorcourt.maricopa.gov/docket/ProbateCourtCases/caseInfo.asp"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Field names confirmed via --probe-fields against live portal 2026-06-26.
# Portal does NOT use ASP.NET ViewState — submit button has no name attr.
_FIELD_MAP: dict[str, str] = {
    "last_name": "lastName",
    "first_name": "FirstName",
    "case_number": "caseNumber",
}

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
    key = "|".join(["maricopa_probate", (case_number or "").strip().upper()])
    return "raw_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Probe mode helpers — urllib GET (no CAPTCHA required for GET)
# ---------------------------------------------------------------------------

_HEADERS_GET = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _get_url(url: str, *, fetch_fn=None) -> str:
    if fetch_fn is not None:
        return fetch_fn("GET", url, None)
    req = urllib.request.Request(url, headers=_HEADERS_GET)
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_SSL_CTX))
    with opener.open(req, timeout=30) as resp:
        return resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attr = dict(attrs)
        if tag in ("input", "select", "textarea", "button"):
            name = attr.get("name") or attr.get("id") or ""
            if name:
                self.fields[name] = attr.get("value", "")

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        self.handle_starttag(tag, attrs)


def _discover_field_names(html: str) -> list[str]:
    parser = _FormParser()
    parser.feed(html)
    return sorted(parser.fields.keys())


# ---------------------------------------------------------------------------
# Playwright results extractor
#
# Portal returns Bootstrap div-based results (not a <table>).
# Container: #tblForms, each result row: .row a[href*="caseInfo"]
# ---------------------------------------------------------------------------


def _pw_extract_results(
    page,
    search_type: str,
    max_features: Optional[int] = None,
) -> list[dict]:
    """Extract case rows from the rendered results page using browser DOM."""
    now = _now_iso()

    rows_data: list[dict] = page.evaluate("""
        () => {
            const links = document.querySelectorAll('#tblForms .row a[href*="caseInfo"]');
            return Array.from(links).map(link => {
                const row = link.closest('.row');
                const partyDiv = row ? row.querySelector('.col-6.col-lg-9') : null;
                const partyText = partyDiv ? partyDiv.innerText.trim() : '';
                return {
                    case_number: link.textContent.trim(),
                    href: new URL(link.getAttribute('href') || '', document.baseURI).href,
                    party_text: partyText.split('\\n')[0].trim()
                };
            });
        }
    """)

    if max_features is not None:
        rows_data = rows_data[:max_features]

    records: list[dict] = []
    for row in rows_data:
        case_number = (row.get("case_number") or "").strip()
        if not case_number:
            continue
        detail_url = (row.get("href") or "").strip()
        party_name = (row.get("party_text") or "").strip()
        confidence = 95 if (case_number and party_name) else 70

        raw_payload: dict = {
            "case_number": case_number,
            "decedent_name": party_name,
            "petitioner_name": party_name,
            "respondent_name": None,
            "filing_date": None,
            "case_type": None,
            "case_status": None,
            "case_detail_url": detail_url,
            "search_type": search_type,
        }

        records.append({
            "raw_record_id": _raw_record_id(case_number),
            "source_id": SOURCE_ID,
            "source_url": detail_url or DETAIL_BASE_URL,
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
# Playwright search functions
# ---------------------------------------------------------------------------


def _pw_goto_and_wait(page, url: str) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except Exception:
        page.goto(url, timeout=60_000)


def _pw_wait_settled(page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception:
        page.wait_for_load_state("domcontentloaded", timeout=15_000)


def _pw_search_by_name(
    page,
    last_name: str,
    first_name: str,
    *,
    max_features: Optional[int] = None,
) -> list[dict]:
    _pw_goto_and_wait(page, PORTAL_URL)
    page.wait_for_selector(f'[name="{_FIELD_MAP["last_name"]}"]', timeout=15_000)
    page.fill(f'[name="{_FIELD_MAP["last_name"]}"]', last_name.strip())
    page.fill(f'[name="{_FIELD_MAP["first_name"]}"]', first_name.strip())
    cn_el = page.query_selector(f'[name="{_FIELD_MAP["case_number"]}"]')
    if cn_el:
        cn_el.fill("")
    page.click('input[type="submit"]', timeout=10_000)
    _pw_wait_settled(page)
    return _pw_extract_results(page, "name", max_features=max_features)


def _pw_search_by_case_number(
    page,
    case_number: str,
    *,
    max_features: Optional[int] = None,
) -> list[dict]:
    _pw_goto_and_wait(page, PORTAL_URL)
    page.wait_for_selector(f'[name="{_FIELD_MAP["case_number"]}"]', timeout=15_000)
    for fname in (_FIELD_MAP["last_name"], _FIELD_MAP["first_name"]):
        el = page.query_selector(f'[name="{fname}"]')
        if el:
            el.fill("")
    page.fill(f'[name="{_FIELD_MAP["case_number"]}"]', case_number.strip())
    page.click('input[type="submit"]', timeout=10_000)
    _pw_wait_settled(page)
    return _pw_extract_results(page, "case_number", max_features=max_features)


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
            rec["change_status"] = "SAME" if prev.get("raw_payload") == rec["raw_payload"] else "UPDATED"
        out.append(rec)
    for rid, prev in prior_by_id.items():
        if rid in current_ids:
            continue
        prev = dict(prev)
        prev["change_status"] = "DISAPPEARED"
        out.append(prev)
    return out


# ---------------------------------------------------------------------------
# End-to-end runner
# ---------------------------------------------------------------------------


def run(
    *,
    output_path: Optional[Path] = None,
    last_name: str = "",
    first_name: str = "",
    case_numbers: Optional[list] = None,
    probe_fields: bool = False,
    headless: bool = True,
    slow_mo: int = 0,
    max_features: Optional[int] = None,
    fetch_fn=None,
) -> dict:
    """Run the scraper end-to-end. Returns a stats dict.

    fetch_fn is accepted for API consistency but ignored in search mode
    (Playwright only). It is passed through to _get_url in probe mode.
    """
    output_path = output_path or (REPO_ROOT / "data" / "raw" / f"{SOURCE_ID}.jsonl")

    if probe_fields:
        html = _get_url(PORTAL_URL, fetch_fn=fetch_fn)
        names = _discover_field_names(html)
        result = {
            "probe_mode": True,
            "portal_url": PORTAL_URL,
            "discovered_field_names": names,
            "field_count": len(names),
        }
        print(json.dumps(result, indent=2))
        return result

    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(_PLAYWRIGHT_INSTALL_MSG)

    if fetch_fn is not None:
        warnings.warn(
            "superior_court_probate: fetch_fn ignored in search mode — Playwright only.",
            stacklevel=2,
        )

    current: list[dict] = []
    errors: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, slow_mo=slow_mo)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        if last_name or first_name:
            try:
                records = _pw_search_by_name(
                    page, last_name, first_name, max_features=max_features
                )
                current.extend(records)
            except Exception as exc:
                errors.append(f"name_search_error: {exc}")

        for cn in (case_numbers or []):
            try:
                records = _pw_search_by_case_number(page, cn, max_features=max_features)
                current.extend(records)
            except Exception as exc:
                errors.append(f"case_number_search_error({cn}): {exc}")

        browser.close()

    seen_ids: set = set()
    deduped: list[dict] = []
    for rec in current:
        rid = rec["raw_record_id"]
        if rid not in seen_ids:
            seen_ids.add(rid)
            deduped.append(rec)
    current = deduped

    output_path.parent.mkdir(parents=True, exist_ok=True)
    prior = _load_prior(output_path)
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
        "disappeared_record_count": sum(1 for r in merged if r["change_status"] == "DISAPPEARED"),
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
            "Maricopa County Superior Court Probate docket scraper (Playwright). "
            "Search by party name or case number and write results to JSONL. "
            "Requires: pip install playwright && playwright install chromium."
        )
    )
    parser.add_argument("--last-name", default="",
                        help="Decedent/petitioner last name for party search.")
    parser.add_argument("--first-name", default="",
                        help="Decedent/petitioner first name for party search.")
    parser.add_argument("--case-number", action="append", dest="case_numbers", default=[],
                        metavar="CASE_NO",
                        help="Case number to look up directly (repeatable, e.g. PB2026-000123).")
    parser.add_argument("--max-features", type=int, default=None, metavar="N",
                        help="Cap on records returned per search (useful for testing).")
    parser.add_argument("--no-headless", action="store_true",
                        help="Run Playwright in visible (non-headless) mode for debugging.")
    parser.add_argument("--slow-mo", type=int, default=0, metavar="MS",
                        help="Playwright slow_mo milliseconds between actions (default 0).")
    parser.add_argument("--out", default=None,
                        help="Output JSONL path. Default: data/raw/superior_court_probate.jsonl")
    parser.add_argument("--probe-fields", action="store_true",
                        help="Fetch form via urllib GET, print field names as JSON, and exit.")
    args = parser.parse_args()

    if not PLAYWRIGHT_AVAILABLE and not args.probe_fields:
        print(f"ERROR: {_PLAYWRIGHT_INSTALL_MSG}", file=sys.stderr)
        return 1

    out = Path(args.out) if args.out else None
    stats = run(
        output_path=out,
        last_name=args.last_name,
        first_name=args.first_name,
        case_numbers=args.case_numbers,
        probe_fields=args.probe_fields,
        headless=not args.no_headless,
        slow_mo=args.slow_mo,
        max_features=args.max_features,
    )
    if not args.probe_fields:
        print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
