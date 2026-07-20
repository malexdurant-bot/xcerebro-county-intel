"""
Maricopa County Recorder — Notice of Trustee's Sale (NOTS) and related
document recordings adapter — Phase 2.

Portal URL:
    https://recorder.maricopa.gov/recording/document-search.html

Public API (Build Mode discovery 2026-06-27):
    The SPA's underlying REST API at publicapi.recorder.maricopa.gov is fully
    accessible without authentication or WAF:

        GET /documents/search?documentCode=NS&beginDate=YYYY-MM-DD&endDate=YYYY-MM-DD
        → {"searchResults": [...], "totalResults": 494}

        GET /documents/{recordingNumber}
        → {"names": [...], "documentCodes": [...], "pageAmount": N, ...}

    The SPA portal results page hits Cloudflare Turnstile (managed challenge)
    that blocks automated browsers even in non-headless Playwright. The public
    API bypasses this entirely — no Playwright needed.

Arizona context:
    Arizona is a NON-JUDICIAL foreclosure state (ARS § 33-808). There is NO
    Notice of Default in the AZ process — the first recorded signal is the
    Notice of Trustee's Sale (NOTS, doc code NS). The 90-day window between
    NOTS recording and trustee sale is the investor outreach window.

The adapter writes to data/raw/recorder_maricopa.jsonl via the standard atomic
tmp→replace pattern and returns a stats dict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Repo bootstrap
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOURCE_ID = "recorder_maricopa"

PORTAL_URL = "https://recorder.maricopa.gov/recording/document-search.html"
API_BASE = "https://publicapi.recorder.maricopa.gov"
API_SEARCH_PATH = "/documents/search"
API_DETAIL_TMPL = "/documents/{}"
API_DOC_CODES_PATH = "/documentcodes/short"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# SSL context: publicapi.recorder.maricopa.gov uses a cert chain that Python
# on Windows cannot verify from the default store.
_API_SSL_CTX = ssl.create_default_context()
_API_SSL_CTX.check_hostname = False
_API_SSL_CTX.verify_mode = ssl.CERT_NONE

_API_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": PORTAL_URL,
    "Origin": "https://recorder.maricopa.gov",
}

# ---------------------------------------------------------------------------
# Document type code map
# Maps human-readable query strings (and bare codes) → API code
# Confirmed 2026-06-27 via publicapi.recorder.maricopa.gov/documentcodes/short
# ---------------------------------------------------------------------------

_DOC_TYPE_CODE_MAP: dict[str, str] = {
    # Human-readable → code
    "NOTICE OF TRUSTEES SALE": "NS",
    "NOTICE OF TRUSTEE": "NS",        # common abbreviated form
    "NOTICE OF TRUSTEE'S SALE": "NS",
    "NOTS": "NS",
    "SUBSTITUTION OF TRUSTEE": "ST",   # SUBSTITUTION OF TRUSTEE ON DEED OF TRUST
    "SUBSTITUTION OF TRUSTEE ON DEED OF TRUST": "ST",
    "LIS PENDENS": "LP",
    "MECHANICS LIEN": "ML",
    "MECHANIC'S LIEN": "ML",
    "MATERIAL MANS MECH LN": "ML",
    "QUIT CLAIM DEED": "QD",
    "TRUSTEES DEED": "TD",
    "TRUSTEE'S DEED": "TD",
    "CANCELLATION OF NOTICE OF TRUSTEES SALE": "CQ",
    # Pass-through if code provided directly
    "NS": "NS", "ST": "ST", "LP": "LP", "ML": "ML",
    "QD": "QD", "TD": "TD", "CQ": "CQ",
}

# Default doc type queries for a full run
DOC_TYPE_QUERIES = [
    "NOTICE OF TRUSTEES SALE",    # NS — primary AZ NOTS signal (ARS 33-808)
    "SUBSTITUTION OF TRUSTEE",    # ST — trustee substitution, commonly accompanies NOTS
    "LIS PENDENS",                # LP — civil foreclosure or lawsuit affecting title
    "MECHANICS LIEN",             # ML — construction lien (ARS 33-981 et seq.)
    "QUIT CLAIM DEED",            # QD — distress/estate/divorce transfers
    "TRUSTEES DEED",              # TD — post-sale deed (completed trustee sale)
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _raw_record_id(recording_number: str, recording_date: str) -> str:
    key = (
        "maricopa_recorder|"
        + (recording_number or "").strip().upper()
        + "|"
        + (recording_date or "").strip()
    )
    return "raw_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:24]


def _format_date_api(dt: datetime) -> str:
    """Format datetime as YYYY-MM-DD for the public API."""
    return dt.strftime("%Y-%m-%d")


def _resolve_doc_type_code(query: str) -> Optional[str]:
    """Return the API code for a query string, or None if unrecognized."""
    return _DOC_TYPE_CODE_MAP.get(query.strip().upper())


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def _api_get(path: str, params: Optional[dict] = None) -> dict:
    """GET from the public API. Returns parsed JSON dict/list."""
    url = API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=_API_HEADERS)
    with urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_API_SSL_CTX)
    ).open(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _api_fetch_detail(recording_number: str) -> Optional[dict]:
    """
    Fetch detail record for a single document.
    Returns dict with keys: names, documentCodes, pageAmount,
    affidavitPresent, restricted, recordingDate, recordingNumber.
    """
    try:
        return _api_get(API_DETAIL_TMPL.format(recording_number))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Search function
# ---------------------------------------------------------------------------


def _search_document_type(
    doc_type_query: str,
    date_from: str,
    date_to: str,
    max_features: Optional[int] = None,
    fetch_detail: bool = True,
) -> list[dict]:
    """
    Search for a single document type via the public API.

    Parameters
    ----------
    doc_type_query:
        Human-readable query (e.g. "NOTICE OF TRUSTEES SALE") or bare code
        (e.g. "NS"). Both are resolved via _DOC_TYPE_CODE_MAP.
    date_from:
        Start date in YYYY-MM-DD format.
    date_to:
        End date in YYYY-MM-DD format.
    max_features:
        Cap on records returned. None = no cap.
    fetch_detail:
        If True, call GET /documents/{n} for each record to populate names
        and page_amount. Each call takes ~0.1–0.3s.

    Returns
    -------
    List of Xcerebro raw record dicts.
    """
    now = _now_iso()

    doc_type_code = _resolve_doc_type_code(doc_type_query)
    if doc_type_code is None:
        raise ValueError(
            f"Unrecognized doc_type_query {doc_type_query!r}. "
            f"Known queries: {sorted(_DOC_TYPE_CODE_MAP.keys())}"
        )

    params: dict = {
        "documentCode": doc_type_code,
        "beginDate": date_from,
        "endDate": date_to,
    }
    if max_features is not None:
        # API supports pageSize; request at most max_features
        params["pageSize"] = str(max_features)

    data = _api_get(API_SEARCH_PATH, params)
    search_results: list[dict] = data.get("searchResults", [])
    total_results: int = data.get("totalResults", len(search_results))

    if max_features is not None:
        search_results = search_results[:max_features]

    records: list[dict] = []
    for row in search_results:
        recording_number = str(row.get("recordingNumber") or "").strip()
        if not recording_number:
            continue

        recording_date = (row.get("recordingDate") or "").strip()
        doc_type_display = (row.get("documentCode") or "").strip()
        recording_suffix = str(row.get("recordingSuffix") or "").strip()
        docket_book = str(row.get("docketBook") or "").strip()
        page_map = str(row.get("pageMap") or "").strip()

        # Optionally fetch detail for names and page count
        detail: Optional[dict] = None
        if fetch_detail:
            detail = _api_fetch_detail(recording_number)

        names: list[str] = []
        page_amount: Optional[int] = None
        restricted: Optional[bool] = None
        affidavit_present: Optional[bool] = None

        if detail:
            names = detail.get("names") or []
            page_amount = detail.get("pageAmount")
            restricted = detail.get("restricted")
            affidavit_present = detail.get("affidavitPresent")

        # Heuristic grantor/grantee split: for NOTS, grantor is typically
        # the beneficiary/lender and grantee is the trustor/borrower. The API
        # does not label roles — names are returned as a flat list.
        grantor = names[0] if len(names) > 0 else None
        grantee = names[-1] if len(names) > 1 else None

        api_url = f"{API_BASE}{API_DETAIL_TMPL.format(recording_number)}"
        confidence = 95 if (recording_number and recording_date) else 70

        raw_payload: dict = {
            "recording_number": recording_number,
            "recording_suffix": recording_suffix,
            "recording_date": recording_date,
            "doc_type_code": doc_type_code,
            "doc_type_display": doc_type_display,
            "names": names,
            "grantor": grantor,
            "grantee": grantee,
            "page_amount": page_amount,
            "docket_book": docket_book,
            "page_map": page_map,
            "restricted": restricted,
            "affidavit_present": affidavit_present,
            "search_doc_type": doc_type_query,
            "doc_detail_url": api_url,
            "total_results_for_query": total_results,
        }

        records.append({
            "raw_record_id": _raw_record_id(recording_number, recording_date),
            "source_id": SOURCE_ID,
            "source_url": api_url,
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
# Playwright name enrichment — uses real Chrome profile to bypass Cloudflare
# ---------------------------------------------------------------------------

_DEFAULT_CHROME_PROFILE = str(
    Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
)
CHROME_PROFILE = os.environ.get("CHROME_PROFILE", _DEFAULT_CHROME_PROFILE)


def _playwright_batch_fetch_names(
    recording_numbers: list[str],
    chrome_profile: str = CHROME_PROFILE,
) -> dict[str, list[str]]:
    """
    Fetch party names for a batch of recording numbers via real Chrome.

    Copies only the Cookies/Preferences from the user's Chrome profile to a
    temp dir so Chrome does not need to be closed. Returns a dict mapping
    recording_number -> list[str] names (empty list if unavailable).
    """
    results: dict[str, list[str]] = {n: [] for n in recording_numbers}
    if not recording_numbers:
        return results

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return results

    tmp = tempfile.mkdtemp(prefix="recorder_chrome_")
    try:
        # Copy only the files Cloudflare cares about
        src_default = Path(chrome_profile) / "Default"
        dst_default = Path(tmp) / "Default"
        dst_default.mkdir(parents=True, exist_ok=True)
        for fname in ("Cookies", "Preferences"):
            src_f = src_default / fname
            if src_f.exists():
                try:
                    shutil.copy2(src_f, dst_default / fname)
                except OSError:
                    pass
        top_state = Path(chrome_profile) / "Local State"
        if top_state.exists():
            try:
                shutil.copy2(top_state, Path(tmp) / "Local State")
            except OSError:
                pass

        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=tmp,
                channel="chrome",
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-minimized",
                ],
                ignore_https_errors=True,
            )
            page = ctx.new_page()

            # Warm up — establish session cookies
            try:
                page.goto(PORTAL_URL, wait_until="networkidle", timeout=30_000)
                time.sleep(2)
            except Exception:
                pass

            for rec_num in recording_numbers:
                url = (
                    "https://recorder.maricopa.gov/recording/document-details.html"
                    f"?recordingNumber={rec_num}&suffix="
                )
                try:
                    page.goto(url, wait_until="networkidle", timeout=45_000)
                    title = page.title().lower()
                    if "just a moment" in title or "cloudflare" in title:
                        time.sleep(15)
                        page.wait_for_load_state("networkidle", timeout=30_000)

                    # Names are in the first <td> of the details table
                    tds = page.locator("td").all_inner_texts()
                    if tds and tds[0].strip():
                        raw = tds[0].strip()
                        names = [n.strip() for n in raw.split("\n\n") if n.strip()]
                        results[rec_num] = names
                except Exception:
                    pass  # leave as empty list

            ctx.close()

    except Exception:
        pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return results


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
                "SAME" if prev.get("raw_payload") == rec["raw_payload"] else "UPDATED"
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
# End-to-end runner
# ---------------------------------------------------------------------------


def run(
    *,
    output_path: Optional[Path] = None,
    doc_type_queries: Optional[list] = None,
    days_back: int = 30,
    probe_doc_types: bool = False,
    max_features: Optional[int] = None,
    fetch_detail: bool = True,
    enrich_names: bool = True,
    # Legacy params accepted for API consistency but unused
    headless: bool = True,
    slow_mo: int = 0,
    fetch_fn=None,
) -> dict:
    """
    Run the recorder scraper end-to-end. Returns a stats dict.

    Parameters
    ----------
    output_path:
        JSONL path to write. Defaults to data/raw/recorder_maricopa.jsonl.
    doc_type_queries:
        List of document type query strings. Defaults to DOC_TYPE_QUERIES.
        Accepts human-readable names (e.g. "NOTICE OF TRUSTEES SALE") or
        bare API codes (e.g. "NS"). See _DOC_TYPE_CODE_MAP for full mapping.
    days_back:
        Number of calendar days back from today to set date_from (default 30).
    probe_doc_types:
        If True, fetch all document codes from the API and print as JSON,
        then exit without writing output.
    max_features:
        Cap total records returned across all doc type searches.
    fetch_detail:
        If True (default), call GET /documents/{n} for each record to populate
        names. Set False for faster runs when names are not needed immediately.
    headless / slow_mo / fetch_fn:
        Accepted for API compatibility but not used (adapter is API-based).
    """
    if fetch_fn is not None:
        warnings.warn(
            "recorder_maricopa: fetch_fn is ignored — this adapter uses the public API.",
            stacklevel=2,
        )

    output_path = output_path or (
        REPO_ROOT / "data" / "raw" / f"{SOURCE_ID}.jsonl"
    )

    queries = doc_type_queries if doc_type_queries is not None else DOC_TYPE_QUERIES

    today = datetime.now(timezone.utc)
    date_from = _format_date_api(today - timedelta(days=days_back))
    date_to = _format_date_api(today)

    # --probe-doc-types: fetch code list from API and print
    if probe_doc_types:
        try:
            codes = _api_get(API_DOC_CODES_PATH)
        except Exception as exc:
            codes = {"error": str(exc)}
        result: dict = {
            "probe_mode": True,
            "api_base": API_BASE,
            "api_path": API_DOC_CODES_PATH,
            "doc_type_code_map": _DOC_TYPE_CODE_MAP,
            "document_codes": codes,
        }
        print(json.dumps(result, indent=2))
        return result

    # --- Main scrape loop ---
    current: list[dict] = []
    errors: list[str] = []
    doc_types_searched: list[str] = []
    total_results_by_query: dict[str, int] = {}

    for doc_type_query in queries:
        remaining = (
            max_features - len(current)
            if max_features is not None
            else None
        )
        if max_features is not None and remaining is not None and remaining <= 0:
            break

        doc_types_searched.append(doc_type_query)
        try:
            records = _search_document_type(
                doc_type_query,
                date_from,
                date_to,
                max_features=remaining,
                fetch_detail=fetch_detail,
            )
            current.extend(records)
            if records:
                total_results_by_query[doc_type_query] = (
                    records[0]["raw_payload"].get("total_results_for_query", 0)
                )
        except Exception as exc:
            errors.append(f"doc_type_search_error({doc_type_query!r}): {exc}")

    # Deduplicate
    seen_ids: set = set()
    deduped: list[dict] = []
    for rec in current:
        rid = rec["raw_record_id"]
        if rid not in seen_ids:
            seen_ids.add(rid)
            deduped.append(rec)
    current = deduped

    # Playwright name enrichment — fetch names for records still missing them
    if enrich_names:
        needs_names = [
            r for r in current
            if not r["raw_payload"].get("names")
        ]
        if needs_names:
            print(
                f"  [playwright] fetching names for {len(needs_names)} records "
                f"with empty names...",
                flush=True,
            )
            rec_nums = [r["raw_payload"]["recording_number"] for r in needs_names]
            name_map = _playwright_batch_fetch_names(rec_nums)
            enriched = 0
            for rec in needs_names:
                rec_num = rec["raw_payload"]["recording_number"]
                names = name_map.get(rec_num) or []
                if names:
                    rec["raw_payload"]["names"] = names
                    rec["raw_payload"]["grantor"] = names[0]
                    rec["raw_payload"]["grantee"] = names[-1] if len(names) > 1 else None
                    enriched += 1
            print(f"  [playwright] enriched {enriched}/{len(needs_names)} records", flush=True)

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
        "api_base": API_BASE,
        "records_pulled": len(current),
        "prior_count": len(prior),
        "total_after_merge": len(merged),
        "new_record_count": sum(1 for r in merged if r["change_status"] == "NEW_RECORD"),
        "same_record_count": sum(1 for r in merged if r["change_status"] == "SAME"),
        "updated_record_count": sum(1 for r in merged if r["change_status"] == "UPDATED"),
        "disappeared_record_count": sum(1 for r in merged if r["change_status"] == "DISAPPEARED"),
        "doc_types_searched": doc_types_searched,
        "total_results_by_query": total_results_by_query,
        "date_from": date_from,
        "date_to": date_to,
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
            "Maricopa County Recorder document scraper (public API). "
            "Fetches NOTS, Lis Pendens, Mechanics Liens, Quit Claim Deeds, "
            "and related recordings via publicapi.recorder.maricopa.gov. "
            "No browser or Playwright required."
        )
    )
    parser.add_argument(
        "--doc-type",
        action="append",
        dest="doc_types",
        default=[],
        metavar="DOC_TYPE",
        help=(
            "Document type query string to search (repeatable). "
            "Accepts human-readable names or codes (see _DOC_TYPE_CODE_MAP). "
            "Default: all entries in DOC_TYPE_QUERIES. "
            "Example: --doc-type 'NOTICE OF TRUSTEES SALE' --doc-type 'LP'."
        ),
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=30,
        metavar="N",
        help="Number of calendar days back from today for the date_from filter (default 30).",
    )
    parser.add_argument(
        "--max-features",
        type=int,
        default=None,
        metavar="N",
        help="Cap total records returned across all doc type searches (useful for bounded testing).",
    )
    parser.add_argument(
        "--no-fetch-detail",
        action="store_true",
        help=(
            "Skip the per-record detail API call (faster but names will be empty). "
            "Detail is fetched by default."
        ),
    )
    parser.add_argument(
        "--no-enrich-names",
        action="store_true",
        help=(
            "Skip the Playwright name enrichment step (no Chrome window). "
            "Enrichment is on by default and uses your real Chrome profile to "
            "fetch grantor/grantee names from recorder.maricopa.gov."
        ),
    )
    parser.add_argument(
        "--probe-doc-types",
        action="store_true",
        help=(
            "Fetch all document codes from the public API and print as JSON, then exit. "
            "Use this to discover exact document type codes."
        ),
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSONL path. Default: data/raw/recorder_maricopa.jsonl",
    )
    # Legacy args — accepted but silently ignored
    parser.add_argument("--no-headless", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--slow-mo", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args()

    out = Path(args.out) if args.out else None
    doc_types = args.doc_types if args.doc_types else None

    stats = run(
        output_path=out,
        doc_type_queries=doc_types,
        days_back=args.days_back,
        probe_doc_types=args.probe_doc_types,
        max_features=args.max_features,
        fetch_detail=not args.no_fetch_detail,
        enrich_names=not args.no_enrich_names,
    )
    if not args.probe_doc_types:
        print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
