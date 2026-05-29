"""
Bexar County clerk_recordings adapter — PublicSearch (RP / Land Records).

Pulls county-recorded Official Public Records (deeds, liens, lis pendens,
probate recordings, judgments, etc.) from the Bexar County Clerk's
PublicSearch portal:

    https://bexar.tx.publicsearch.us

This implements the operator-approved scraper spec v2 at
`runs/bexar_tx/recon/publicsearch_clerk_recordings_scraper_spec.md`
(all 5 v1 open questions resolved 2026-05-17). Read that spec before
editing this file — every behavior here is anchored to a numbered
section of it.

Design notes
------------
- The portal is a React SPA: a raw HTTP GET of `/results` returns an
  empty application shell. The rendered result table is produced by the
  SPA's own data fetch, so the live transport is Playwright headless
  Chromium (spec §5.1). Playwright is imported *lazily* inside the live
  fetch function, so this module loads — and its tested parser core runs —
  without Playwright installed. Live runs require:
      pip install playwright && python -m playwright install chromium
- The tested core is `parse_result_page(html, doc_type_code)`: rendered
  HTML -> list of raw_payload dicts. It is exercised offline against the
  real captured DOM fixture (`raw_html/02_result_list.html`) plus crafted
  edge cases. Network I/O is injected via `fetch_fn` exactly like
  `scrapers/foreclosure_notices_map.py` injects its ArcGIS fetcher.
- This file is a *county-side* adapter (`scrapers/`, not
  `scaffold/pipeline/`), so per MASTER_PROMPT §4.31 it may carry the
  Bexar/PublicSearch portal protocol and the locked v1 config values.
  The universality rule binds only the universal `scaffold/` modules.

Output (spec §3):
- `data/raw/clerk_recordings.jsonl`           — append-only wrapped raw records (§3.1/§3.2)
- `data/raw/clerk_recordings_runs/<run_id>.json` — per-run metadata (§3.3)
- `data/raw/clerk_recordings_state.json`      — cursor anchor (§3.4)
- `data/raw/clerk_recordings_html/<date>/...` — rendered-HTML audit (§3.5)
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# --------------------------------------------------------------------------
# Locked v1 configuration (spec §2.1, §12). These live county-side per §4.31.
# --------------------------------------------------------------------------

SOURCE_ID = "publicsearch_clerk_recordings"
BASE_URL = "https://bexar.tx.publicsearch.us"
RESULTS_PATH = "/results"
DETAIL_PATH_PREFIX = "/doc/"
DEPARTMENT = "RP"
RESULTS_PAGE_SIZE = 50
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# pipeline_modes (spec §2.1)
OVERLAP_DAYS = 3
BACKFILL_DEFAULT_DAYS = 30
BACKFILL_ALLOWED_DAYS = (1, 7, 14, 30)

# politeness + circuit breakers (spec §7, §8, §12)
POLITE_MIN_DELAY_SECONDS = 2
POLITE_MAX_DELAY_SECONDS = 5
INTER_DOC_TYPE_DELAY_SECONDS = 15
MAX_PAGES_PER_DOC_TYPE = 200
PER_PAGE_TIMEOUT_SECONDS = 60
RETRY_BACKOFF_SECONDS = [5, 30, 120]  # 3 retries after the initial attempt
HARD_HALT_PARTIAL_THRESHOLD = 3  # EXPANDED failures before the cursor freezes

# raw-HTML audit (spec §3.5)
RAW_HTML_AUDIT_ENABLED = True
RAW_HTML_AUDIT_RETENTION_DAYS = 30

# Doc-type iteration (spec §10): CORE first, then EXPANDED; alphabetical by code.
CORE_CODES = [
    ("DECREE", "DECREE"),
    ("FTL", "FEDERAL TAX LIEN"),
    ("LETTERS", "LETTERS"),
    ("LIS PEN", "LIS PENDENS"),
    ("MECHLN", "MECHANICS LIEN"),
    ("PROBATE", "PROBATE"),
    ("STL", "STATE TAX LIEN"),
    ("WILL", "WILL & TESTAMENT"),
]
EXPANDED_CODES = [
    ("AFFIDAV", "AFFIDAVIT"),
    ("CSUP LN", "CHILD SUPPORT LN"),
    ("FC", "FORECLOSURE"),
    ("HOSP LN", "HOSPITAL LIEN"),
    ("JUDG", "JUDGMENT"),
    ("LIEN", "LIEN"),
    ("LNLD LN", "LANDLORD LIEN"),
    ("MEMO", "MEMORANDUM"),
    ("MOD", "MODIFICATION"),
    ("NOTICE", "NOTICE"),
    ("PA", "POWER OF ATTORNEY"),
    ("SJ", "State-Judgment"),
]

# Default output locations.
DEFAULT_OUT = REPO_ROOT / "data" / "raw" / "clerk_recordings.jsonl"
DEFAULT_RUNS_DIR = REPO_ROOT / "data" / "raw" / "clerk_recordings_runs"
DEFAULT_STATE = REPO_ROOT / "data" / "raw" / "clerk_recordings_state.json"
DEFAULT_HTML_DIR = REPO_ROOT / "data" / "raw" / "clerk_recordings_html"


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------

class FetchError(Exception):
    """A retryable per-page fetch failure (5xx, timeout, selector miss)."""


class HardHalt(Exception):
    """A whole-run halt condition (login wall, CAPTCHA, maintenance, etc.)."""


# --------------------------------------------------------------------------
# HTML parsing (spec §5.3) — the tested core
# --------------------------------------------------------------------------

# thead aria-label leading token -> raw_payload field / grid sub-field.
_HEADER_FIELD = {
    "grantor": "grantor",
    "grantee": "grantee",
    "doc type": "doc_type_label",
    "recorded date": "recorded_date",
    "doc number": "document_number",
    "book/volume/page": "book_volume_page",
    "legal description": "legal_description",
    "lot": "lot",
    "block": "block",
    "ncb": "ncb",
    "county block": "county_block",
    "property address": "property_address",
}

# Fields that get N/A / placeholder normalized to null.
_NULLABLE_NA = {"property_address", "grantor", "grantee", "legal_description"}


class _ResultTableParser(HTMLParser):
    """Stream-parse the PublicSearch result table.

    Builds, per page, a header->column-index map from <thead> (spec §5.3
    fallback rule: identify columns by header, never by bare index), then
    extracts each <tbody> <tr role="row"> into a cell dict keyed by column
    index, plus the row's internal_doc_id from the checkbox <input> id.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_thead = False
        self.in_tbody = False
        self.header_by_col: dict[int, str] = {}  # col index -> field name
        self.rows: list[dict] = []  # each: {"_doc_id": str|None, <col_int>: text}
        self._cur_row: dict | None = None
        self._cur_col: int | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "thead":
            self.in_thead = True
        elif tag == "tbody":
            self.in_tbody = True
        elif tag == "th" and self.in_thead:
            cls = a.get("class", "") or ""
            m = re.search(r"\bcol(\d+)\b", cls)
            aria = (a.get("aria-label", "") or "").strip().lower()
            token = aria.split(",", 1)[0].strip()
            if m and token in _HEADER_FIELD:
                self.header_by_col[int(m.group(1))] = _HEADER_FIELD[token]
        elif tag == "tr" and self.in_tbody and a.get("role") == "row":
            self._cur_row = {"_doc_id": None}
        elif self._cur_row is not None and tag == "input":
            cid = a.get("id", "") or ""
            m = re.match(r"table-checkbox-(\d+)$", cid)
            if m:
                self._cur_row["_doc_id"] = m.group(1)
        elif self._cur_row is not None and tag == "td":
            cls = a.get("class", "") or ""
            m = re.search(r"\bcol-(\d+)\b", cls)
            self._cur_col = int(m.group(1)) if m else None
            self._text_parts = []

    def handle_data(self, data):
        if self._cur_col is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "thead":
            self.in_thead = False
        elif tag == "tbody":
            self.in_tbody = False
        elif tag == "td" and self._cur_col is not None and self._cur_row is not None:
            text = re.sub(r"\s+", " ", "".join(self._text_parts)).strip()
            self._cur_row[self._cur_col] = text
            self._cur_col = None
            self._text_parts = []
        elif tag == "tr" and self._cur_row is not None:
            self.rows.append(self._cur_row)
            self._cur_row = None


def _norm_date(raw: str | None) -> str | None:
    """`1/20/2026` (M/D/YYYY) -> `2026-01-20`. Returns None if unparseable."""
    if not raw:
        return None
    raw = raw.strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", raw)
    if not m:
        return None
    mo, da, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return f"{yr:04d}-{mo:02d}-{da:02d}"
    except ValueError:
        return None


def _clean(value: str | None, field: str) -> str | None:
    """Normalize a cell value: empty / N/A -> None for nullable fields;
    `--/--/--` placeholder -> None for book_volume_page."""
    if value is None:
        return None
    v = value.strip()
    if v == "":
        return None
    if field == "book_volume_page" and set(v) <= set("-/"):
        return None
    if field in _NULLABLE_NA and v.upper() == "N/A":
        return None
    return v


def parse_result_page(html: str, doc_type_code: str) -> list[dict]:
    """Extract one `raw_payload` dict per result row from a rendered page.

    `doc_type_code` is supplied from the queried `docTypes` URL parameter —
    authoritative per spec §5.4 (the result list carries only the human
    label, not the code).
    """
    parser = _ResultTableParser()
    parser.feed(html)

    # Invert header map: field name -> column index.
    col_by_field = {field: col for col, field in parser.header_by_col.items()}

    out: list[dict] = []
    for row in parser.rows:
        doc_id = row.get("_doc_id")
        if not doc_id:
            # No checkbox id -> cannot build a stable record id. Skip (§5.3:
            # internal_doc_id is structural and always present on real rows).
            continue

        def cell(field: str) -> str | None:
            col = col_by_field.get(field)
            return row.get(col) if col is not None else None

        recorded_date = _norm_date(cell("recorded_date"))
        document_number = _clean(cell("document_number"), "document_number")

        # parcel_grid_identifiers: single raw string, sub-values verbatim
        # (spec §3.2 / §5.3 — keep N/A inside the string, lose no detail).
        def grid(field: str) -> str:
            v = cell(field)
            return v.strip() if v else "N/A"

        parcel_grid = (
            f"Lot {grid('lot')}, Block {grid('block')}, "
            f"NCB {grid('ncb')}, County Block {grid('county_block')}"
        )

        raw_payload = {
            "internal_doc_id": doc_id,
            "document_number": document_number,
            "doc_type_code": doc_type_code,
            "doc_type_label": _clean(cell("doc_type_label"), "doc_type_label"),
            "recorded_date": recorded_date,
            "grantor": _clean(cell("grantor"), "grantor"),
            "grantee": _clean(cell("grantee"), "grantee"),
            "property_address": _clean(cell("property_address"), "property_address"),
            "legal_description": _clean(cell("legal_description"), "legal_description"),
            "book_volume_page": _clean(cell("book_volume_page"), "book_volume_page"),
            "parcel_grid_identifiers": parcel_grid,
        }
        out.append(raw_payload)
    return out


# --------------------------------------------------------------------------
# Record wrapping (spec §3.2 / MASTER_PROMPT §4.32)
# --------------------------------------------------------------------------

def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def wrap_record(raw_payload: dict, now: str) -> dict:
    """Wrap a raw_payload into the §4.32 envelope. parser_confidence is 95
    for a clean row, 70 when a load-bearing field was missing/degraded."""
    doc_id = raw_payload["internal_doc_id"]
    confident = bool(raw_payload.get("document_number")) and bool(
        raw_payload.get("recorded_date")
    )
    return {
        "raw_record_id": f"publicsearch_bexar_{doc_id}",
        "source_id": SOURCE_ID,
        "source_url": f"{BASE_URL}{DETAIL_PATH_PREFIX}{doc_id}",
        "source_fetched_at": now,
        "parser_confidence": 95 if confident else 70,
        "raw_payload": raw_payload,
    }


# --------------------------------------------------------------------------
# URL construction (spec §4.1)
# --------------------------------------------------------------------------

def build_results_url(doc_type_code: str, start_yyyymmdd: str,
                      end_yyyymmdd: str, offset: int) -> str:
    return (
        f"{BASE_URL}{RESULTS_PATH}"
        f"?department={DEPARTMENT}"
        f"&searchType=advancedSearch"
        f"&docTypes={quote(doc_type_code)}"
        f"&recordedDateRange={start_yyyymmdd},{end_yyyymmdd}"
        f"&limit={RESULTS_PAGE_SIZE}"
        f"&offset={offset}"
    )


# --------------------------------------------------------------------------
# Hard-halt / empty-page detection (spec §5.2, §6, §8)
# --------------------------------------------------------------------------

_HARD_HALT_PATTERNS = [
    (re.compile(r"\b(sign in to continue|please log in|login required|"
                r"authentication required)\b", re.I), "login_wall"),
    # NOTE: the bare token "captcha" is deliberately NOT matched here. Every
    # normal PublicSearch results page embeds a benign reCAPTCHA site key in
    # its config JSON ("captcha-site-key":"6Lf..."), which a \bcaptcha\b
    # pattern false-matched — hard-halting every live run (caught on the first
    # live pull, 2026-05-28). Match only genuine challenge phrasings/providers:
    # an interstitial says "reCAPTCHA"/"hCaptcha"/"verify you are human" and
    # carries a provider marker; it does NOT serve a result table.
    (re.compile(r"\b(recaptcha|hcaptcha|are you a robot|verify you are human|"
                r"bot challenge|complete the captcha|captcha verification|"
                r"please complete the captcha)\b|cf-browser-verification|"
                r"cf-challenge|challenge-platform|just a moment\b|"
                r"\b(datadome|perimeterx|incapsula)\b", re.I),
     "captcha_or_bot_challenge"),
    (re.compile(r"\b(service unavailable|under maintenance|503)\b", re.I),
     "maintenance_or_503"),
]


def detect_hard_halt(html: str) -> str | None:
    # A real challenge/interstitial never serves the result table. If rendered
    # result rows are present, the page is legitimate regardless of any token
    # that happens to appear in embedded config (defense-in-depth alongside the
    # tightened patterns above).
    if 'role="row"' in html:
        return None
    for pat, reason in _HARD_HALT_PATTERNS:
        if pat.search(html):
            return reason
    return None


def page_looks_empty(html: str) -> bool:
    """True when the page is a legitimate zero-result / end-of-pagination
    page (no result rows, with a no-results affordance present)."""
    if 'role="row"' in html:
        return False
    return bool(re.search(r"no results|0 results|no documents found",
                          html, re.I)) or "<tbody></tbody>" in html or True


# --------------------------------------------------------------------------
# Live transport (lazy Playwright) — not exercised by the offline gate
# --------------------------------------------------------------------------

def _playwright_fetch(url: str, timeout_seconds: int = PER_PAGE_TIMEOUT_SECONDS) -> str:
    """Default live fetcher. Lazy-imports Playwright so the module loads
    without it. Raises HardHalt / FetchError per spec §8."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: WPS433
        from playwright.sync_api import TimeoutError as PWTimeout  # noqa: WPS433
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise HardHalt(
            "Playwright is not installed. Run: pip install playwright && "
            "python -m playwright install chromium"
        ) from exc

    try:  # pragma: no cover - requires a live browser
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_context(user_agent=USER_AGENT).new_page()
                page.goto(url, wait_until="networkidle",
                          timeout=timeout_seconds * 1000)
                try:
                    page.wait_for_selector('tbody tr[role="row"]',
                                           timeout=5000)
                except PWTimeout:
                    pass  # zero-result page; handled by the caller
                content = page.content()
            finally:
                browser.close()
    except PWTimeout as exc:  # pragma: no cover
        raise FetchError(f"playwright timeout on {url}") from exc

    halt = detect_hard_halt(content)
    if halt:  # pragma: no cover
        raise HardHalt(halt)
    return content


# --------------------------------------------------------------------------
# Date-window computation (spec §2.3 / §2.4)
# --------------------------------------------------------------------------

def _yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _iso(d: date) -> str:
    return d.isoformat()


def compute_window(mode: str, state: dict | None, today: date,
                   backfill_days: int | None) -> tuple[date, date, bool]:
    """Return (start_date, end_date, first_run_fallback)."""
    if mode == "first_run_backfill":
        days = backfill_days if backfill_days is not None else BACKFILL_DEFAULT_DAYS
        return today - timedelta(days=days), today, False

    # daily_refresh
    last = (state or {}).get("last_successful_recorded_date")
    if not last:
        # First-run fallback: 30-day window, still recorded as daily_refresh.
        return today - timedelta(days=BACKFILL_DEFAULT_DAYS), today, True
    last_date = datetime.strptime(last, "%Y-%m-%d").date()
    return last_date - timedelta(days=OVERLAP_DAYS), today, False


# --------------------------------------------------------------------------
# State + audit helpers
# --------------------------------------------------------------------------

def _load_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _slug(code: str) -> str:
    return re.sub(r"\s+", "_", code.strip())


def _rotate_html_audit(html_dir: Path, today: date,
                       retention_days: int = RAW_HTML_AUDIT_RETENTION_DAYS) -> None:
    """Delete date-named subdirectories older than the retention window."""
    if not html_dir.exists():
        return
    cutoff = today - timedelta(days=retention_days)
    for child in html_dir.iterdir():
        if not child.is_dir():
            continue
        try:
            d = datetime.strptime(child.name, "%Y%m%d").date()
        except ValueError:
            continue
        if d < cutoff:
            for f in child.glob("*"):
                try:
                    f.unlink()
                except OSError:
                    pass
            try:
                child.rmdir()
            except OSError:
                pass


def _write_html_audit(html_dir: Path, today: date, code: str,
                      offset: int, html: str) -> None:
    """Best-effort audit write — never fatal (spec §3.5)."""
    try:
        day_dir = html_dir / _yyyymmdd(today)
        day_dir.mkdir(parents=True, exist_ok=True)
        (day_dir / f"{_slug(code)}_offset_{offset}.html").write_text(
            html, encoding="utf-8"
        )
    except OSError:
        pass  # audit-write failure must not halt the run


# --------------------------------------------------------------------------
# Per-doc-type pagination with retry/backoff (spec §6, §8)
# --------------------------------------------------------------------------

def _fetch_with_retry(fetch_fn: Callable[[str], str], url: str,
                      sleep_fn: Callable[[float], None]) -> str:
    """Fetch one page, retrying with backoff. HardHalt propagates
    immediately; FetchError after all retries re-raises."""
    attempts = [0] + RETRY_BACKOFF_SECONDS  # [0, 5, 30, 120] -> 4 attempts
    last_exc: Exception | None = None
    for i, backoff in enumerate(attempts):
        if backoff:
            sleep_fn(backoff)
        try:
            html = fetch_fn(url)
        except HardHalt:
            raise
        except FetchError as exc:
            last_exc = exc
            continue
        halt = detect_hard_halt(html)
        if halt:
            raise HardHalt(halt)
        return html
    raise last_exc if last_exc else FetchError(f"exhausted retries on {url}")


def fetch_doc_type(fetch_fn: Callable[[str], str], code: str,
                   start: date, end: date, *,
                   sleep_fn: Callable[[float], None],
                   html_dir: Path | None,
                   today: date,
                   max_pages: int = MAX_PAGES_PER_DOC_TYPE) -> tuple[list[dict], dict]:
    """Paginate one doc type. Returns (wrapped_records, per_doc_type_stats)."""
    records: list[dict] = []
    pages_fetched = 0
    retries_consumed = 0
    status = "success"
    start_s, end_s = _yyyymmdd(start), _yyyymmdd(end)

    offset = 0
    while pages_fetched < max_pages:
        url = build_results_url(code, start_s, end_s, offset)
        try:
            html = _fetch_with_retry(fetch_fn, url, sleep_fn)
        except HardHalt:
            raise
        except FetchError:
            # All retries for this page failed -> skip the rest of this code.
            retries_consumed += len(RETRY_BACKOFF_SECONDS)
            status = "partial" if records else "failed"
            break

        if html_dir is not None and RAW_HTML_AUDIT_ENABLED:
            _write_html_audit(html_dir, today, code, offset, html)

        page_records = parse_result_page(html, code)
        if not page_records:
            # Legitimate end-of-pagination empty page (spec §6).
            break

        now = _now_iso()
        records.extend(wrap_record(rp, now) for rp in page_records)
        pages_fetched += 1
        offset += RESULTS_PAGE_SIZE

        # Polite inter-page delay (spec §7).
        sleep_fn(random.uniform(POLITE_MIN_DELAY_SECONDS, POLITE_MAX_DELAY_SECONDS))
    else:
        # Loop exited via the while condition -> circuit breaker tripped.
        status = "partial"

    stats = {
        "code": code,
        "records_fetched": len(records),
        "pages_fetched": pages_fetched,
        "retries": retries_consumed,
        "status": status,
    }
    return records, stats


# --------------------------------------------------------------------------
# Orchestration (spec §2, §3, §8, §10)
# --------------------------------------------------------------------------

def run(*, mode: str = "daily_refresh",
        backfill_days: int | None = None,
        output_path: Path | None = None,
        runs_dir: Path | None = None,
        state_path: Path | None = None,
        html_dir: Path | None = None,
        today: date | None = None,
        fetch_fn: Callable[[str], str] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        invocation_args: list[str] | None = None) -> dict:
    """Run the scraper end-to-end. Returns the run-metadata dict."""
    if mode == "historical_lookup":
        raise ValueError(
            "historical_lookup mode is not supported in v1. "
            "Use daily_refresh or first_run_backfill."
        )
    if mode not in ("daily_refresh", "first_run_backfill"):
        raise ValueError(f"unrecognized mode: {mode!r}")
    if mode == "first_run_backfill":
        days = backfill_days if backfill_days is not None else BACKFILL_DEFAULT_DAYS
        if days not in BACKFILL_ALLOWED_DAYS:
            raise ValueError(
                f"--backfill-days must be one of {sorted(BACKFILL_ALLOWED_DAYS)}; "
                f"got {days}"
            )
        backfill_days = days

    output_path = output_path or DEFAULT_OUT
    runs_dir = runs_dir or DEFAULT_RUNS_DIR
    state_path = state_path or DEFAULT_STATE
    html_dir = html_dir if html_dir is not None else DEFAULT_HTML_DIR
    today = today or datetime.now(timezone.utc).date()
    fetch_fn = fetch_fn or _playwright_fetch
    sleep_fn = sleep_fn or time.sleep

    output_path.parent.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)

    run_started = datetime.now(timezone.utc)
    run_id = f"{run_started.strftime('%Y%m%dT%H%M%S')}_{mode}"

    # Audit rotation at the start of a daily_refresh run (spec §3.5).
    if mode == "daily_refresh" and html_dir is not None:
        _rotate_html_audit(html_dir, today)

    state = _load_state(state_path)
    start_date, end_date, first_run_fallback = compute_window(
        mode, state, today, backfill_days
    )

    iter_codes = [("CORE", c, lbl) for c, lbl in CORE_CODES] + \
                 [("EXPANDED", c, lbl) for c, lbl in EXPANDED_CODES]

    per_doc_type: list[dict] = []
    all_records: list[dict] = []
    status = "success"
    halt_reason: str | None = None
    core_failed = 0
    expanded_failed = 0

    try:
        for idx, (tier, code, label) in enumerate(iter_codes):
            records, st = fetch_doc_type(
                fetch_fn, code, start_date, end_date,
                sleep_fn=sleep_fn, html_dir=html_dir, today=today,
            )
            st["tier"] = tier
            st["label"] = label
            per_doc_type.append(st)
            all_records.extend(records)
            if st["status"] == "failed":
                if tier == "CORE":
                    core_failed += 1
                else:
                    expanded_failed += 1
                status = "partial"
            elif st["status"] == "partial" and status == "success":
                status = "partial"

            # Inter-doc-type delay (spec §7), skip after the last code.
            if idx < len(iter_codes) - 1:
                sleep_fn(INTER_DOC_TYPE_DELAY_SECONDS)
    except HardHalt as exc:
        status = "halted"
        halt_reason = str(exc)

    # Append-only output (spec §3.1).
    if all_records:
        with open(output_path, "a", encoding="utf-8") as fh:
            for rec in all_records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # Conservative cursor advance (spec §8).
    advanced = False
    new_cursor = None
    if status != "halted":
        if core_failed == 0 and expanded_failed < HARD_HALT_PARTIAL_THRESHOLD:
            new_cursor = _iso(end_date)
            advanced = True

    if advanced:
        state_path.write_text(json.dumps({
            "last_successful_recorded_date": new_cursor,
            "last_successful_run_id": run_id,
            "last_successful_run_finished_at": _now_iso(),
        }, indent=2), encoding="utf-8")

    run_finished = datetime.now(timezone.utc)
    metadata = {
        "run_id": run_id,
        "mode": mode,
        "invocation_args": invocation_args or [],
        "first_run_fallback": first_run_fallback,
        "start_date": _iso(start_date),
        "end_date": _iso(end_date),
        "per_doc_type": per_doc_type,
        "total_records_appended": len(all_records),
        "run_started_at": run_started.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "run_finished_at": run_finished.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "run_duration_seconds": round((run_finished - run_started).total_seconds(), 3),
        "status": status,
        "new_last_successful_recorded_date": new_cursor,
    }
    if halt_reason:
        metadata["halt_reason"] = halt_reason

    (runs_dir / f"{run_id}.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pull Bexar County clerk recordings from PublicSearch (RP)."
    )
    parser.add_argument("--mode", default="daily_refresh",
                        help="daily_refresh (default) | first_run_backfill | "
                             "historical_lookup (rejected)")
    parser.add_argument("--backfill-days", type=int, default=None,
                        help="first_run_backfill window: one of 1, 7, 14, 30.")
    parser.add_argument("--out", default=None,
                        help="Output JSONL path. "
                             "Default: data/raw/clerk_recordings.jsonl")
    args = parser.parse_args(argv)

    try:
        meta = run(
            mode=args.mode,
            backfill_days=args.backfill_days,
            output_path=Path(args.out) if args.out else None,
            invocation_args=(argv if argv is not None else sys.argv[1:]),
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(meta, indent=2))
    return 0 if meta["status"] != "halted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
