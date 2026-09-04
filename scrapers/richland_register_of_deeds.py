"""
Richland County, SC — Register of Deeds (SMS / RODPublicViewer).

STATUS 2026-09-03: LIVE. Search execution is working end-to-end (login,
navigation, doc-type-filtered + date-ranged search, pagination, and a
results-row parser are all confirmed against real data). Wired into
run_pipeline.py as an additional PRIMARY_EVENT_SOURCE for two new
canonical_doc_types not covered by any other Richland source:
mechanics_lien and federal_tax_lien/state_tax_lien.

Root causes of the 2026-08-22 "FormatException" blocker (both were bugs in
this module, not the site):

  1. The http://…/viewer.aspx → https://…/viewer.aspx redirect the site
     issues after the GridView "Search" postback DUPLICATES every
     querystring param onto the redirect target (a bug in the site's own
     redirect handler — confirmed live: `r.history` shows a single-copy
     request URL, but `r.url` after `requests` follows the redirect comes
     back with `.ASPXFORMAUTH`/`UserGuid`/`Id` each appearing twice). The
     old code propagated that doubled querystring into every downstream
     request, including the actual search POST — ASP.NET then received
     `Id=<val>,<val>` server-side and threw the FormatException trying to
     parse it. Fix: dedupe the querystring (keep first occurrence of each
     key) before building the QueryPanel.aspx URL — see `_dedupe_qs`.
  2. Once the querystring bug was fixed, a search POST with
     `__EVENTTARGET=btnNewSearch` (the actual button's `name` attribute) is
     silently a no-op — the button's onclick calls `SubmitSearch()`, which
     itself calls `__doPostBack("btnSearch", '')`. The postback target the
     server actually dispatches on is `btnSearch`, not the button's own
     name. Fix: search() posts `__EVENTTARGET=btnSearch`.
  3. The date-range fields (`datePickerBegin`/`datePickerEnd`) are ignored
     by the server unless `chkUseDateRange=on` is also sent — that
     checkbox is unchecked by default and gates whether the server reads
     the date fields at all (confirmed live: without it, every doc-type
     search returns the full all-time result set for that type, oldest
     record first, "of 1000" total regardless of the date range sent).

Portal chain (three separate ASP.NET WebForms apps, chained via redirects):
  1. https://www7.richlandcountysc.gov/SMS_External/Login.aspx
     - Classic __VIEWSTATE/__EVENTVALIDATION postback login form.
     - CAPTCHA: a bitmap CAPTCHA (`txtBitMapCaptcha`) whose answer is ALSO
       embedded in plaintext in a same-page hidden field
       (`ctl00$cpMainContent$hidStrRandom`) and in the challenge image's own
       querystring (`ImageHandler.ashx?Random1=<answer>`). This is a bug in
       the site, not something we're exploiting beyond reading a value the
       page already hands us — no OCR/solver needed.
  2. https://www7.richlandcountysc.gov/SMS_Portal/Home.aspx
     - Lists active subscriptions in a GridView (`gvResults`). Each row has
       a "Search" link that does a GridView Select postback
       (__EVENTTARGET=ctl00$cpMainContent$gvResults, __EVENTARGUMENT=Select$N)
       and returns a `window.open(...)` call pointing at the viewer app with
       a per-session `.ASPXFORMAUTH` token + `UserGuid` + `Id`.
  3. https://www7.richlandcountysc.gov/rodpublicviewer/viewer.aspx
     - Two-frame document viewer/search app ("RODPublicViewer"). Redirects
       http → https, DUPLICATING the querystring in the process (see bug 1
       above). Body onload calls
       `LoadUrlInFrame("leftPanel", "QueryPanel.aspx" + location.search)`
       — the actual search form lives at QueryPanel.aspx, loaded with the
       same (deduped) auth querystring.

Results: a doc-type + date-range search returns up to 50 rows per page in
a `DataList1` control (`id="DataList1_ctl<NN>_lbl<Field>"` per row), with
"Next"/"Previous" as plain submit buttons (`btnNext`/`btnPrevious`) that
just re-POST the same form. Row fields used here: lblDate, lblInstrument,
lblDocumentType, lblNames (multiple names joined by `<br>`, debtor first
then filer/claimant — standard ROD indexing order, confirmed against real
tax-lien and mechanics-lien rows), lblBookPage, lblAddress1, lblAddress2,
lblCityStateZip, lblTaxMapNumbers, lblLegal.

MVP doc-type scope — mechanics liens and federal/state tax liens only
(NOT foreclosure-completion deeds — "Foreclosure - Deed" (70),
"Foreclosure - Mortgage" (71), "Master's Deed-Foreclosure" (248) all
record a SALE THAT ALREADY HAPPENED, so unlike a lien they aren't a new
distress signal on an still-in-trouble owner; Richland's Master-in-Equity
Columbia Star feed already covers the pre-sale opportunity for the same
cases. Recording completions would be a suppression/REO signal, a
different feature — deliberately left out of this build rather than
guessed at):
  mechanics_lien   -> doc codes 16 (Mechanics Lien), 193 (Affidavit -
                      Mechanics Lien), 143 (Mechanics Lien Bond)
  federal_tax_lien -> doc codes 47 (Tax Lien Federal), 203 (ACH Payment
                      Fed Tax Lien)
  state_tax_lien   -> doc code 48 (Tax Lien State)
254 document types exist in the registry total; see DOC_TYPE_CANONICAL
below for the exact code -> canonical_doc_type map this module searches.

Credentials: RICHLAND_SMS_USERNAME / RICHLAND_SMS_PASSWORD, read from a
gitignored .env at repo root (see scrapers/richland_skiptrace_dealmachine.py
for the sibling env-var convention this repo uses).

Rate policy: one page fetch every 1.5 seconds; capped at
MAX_PAGES_PER_DOC_TYPE_GROUP pages (10, i.e. 500 rows) per run — daily
incremental runs use a narrow date range and should never approach this;
it exists to keep a --full backfill from hammering the site.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no external dependency) — only sets vars not
    already present in the environment."""
    import os
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


_load_dotenv(ROOT / ".env")

import os  # noqa: E402

SOURCE_ID = "register_of_deeds_sms"
LOGIN_URL = "https://www7.richlandcountysc.gov/SMS_External/Login.aspx"
HOME_URL = "https://www7.richlandcountysc.gov/SMS_Portal/Home.aspx"

RAW_DIR = ROOT / "data" / "raw" / "richland_register_of_deeds"
RAW_DIR.mkdir(parents=True, exist_ok=True)
CURSOR_PATH = RAW_DIR / "cursor.json"

# code -> (raw label as it appears in lblDocumentType, canonical_doc_type)
DOC_TYPE_CANONICAL: dict[str, tuple[str, str]] = {
    "16": ("Mechanics Lien", "mechanics_lien"),
    "193": ("Affidavit - Mechanics Lien", "mechanics_lien"),
    "143": ("Mechanics Lien Bond", "mechanics_lien"),
    "47": ("Tax Lien Federal", "federal_tax_lien"),
    "203": ("ACH Payment Fed Tax Lien", "federal_tax_lien"),
    "48": ("Tax Lien State", "state_tax_lien"),
}

MAX_PAGES_PER_DOC_TYPE_GROUP = 10
PAGE_FETCH_DELAY_SECONDS = 1.5
DEFAULT_BACKFILL_DAYS = 30  # lookback window on first run (no cursor yet)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Form / querystring helpers
# ---------------------------------------------------------------------------

def _form_fields(html_text: str, form_index: int = 0) -> tuple[dict, "BeautifulSoup.Tag", BeautifulSoup]:
    """Extract every non-button field from a page's form, preserving hidden
    ASP.NET postback state (__VIEWSTATE etc). Robust alternative to
    hand-written regexes — regex extraction was found to intermittently
    corrupt __VIEWSTATE and trigger 'Validation of viewstate MAC failed'."""
    soup = BeautifulSoup(html_text, "html.parser")
    form = soup.find_all("form")[form_index]
    data: dict = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        if itype in ("submit", "button", "image"):
            continue
        if itype == "checkbox":
            if inp.has_attr("checked"):
                data[name] = inp.get("value", "on")
            continue
        if itype == "radio":
            if inp.has_attr("checked"):
                data[name] = inp.get("value", "")
            continue
        data[name] = inp.get("value", "")
    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        opts = sel.find_all("option", selected=True)
        if sel.get("multiple") is not None:
            data[name] = [o.get("value", "") for o in opts]
        else:
            opt = opts[0] if opts else sel.find("option")
            data[name] = opt.get("value", "") if opt else ""
    return data, form, soup


def _dedupe_qs(url: str) -> str:
    """Keep only the first occurrence of each querystring key. Needed
    because the site's own http->https redirect for viewer.aspx duplicates
    every param (see module docstring bug #1) — without this, every
    downstream request (and the search POST itself) inherits the doubled
    querystring and the server throws a FormatException trying to parse
    e.g. `Id=<val>,<val>` as a single value."""
    sp = urlsplit(url)
    seen: dict[str, str] = {}
    for k, v in parse_qsl(sp.query, keep_blank_values=True):
        seen.setdefault(k, v)
    return urlunsplit((sp.scheme, sp.netloc, sp.path, urlencode(seen), ""))


# ---------------------------------------------------------------------------
# Login / navigation
# ---------------------------------------------------------------------------

def login(session: Optional[requests.Session] = None) -> requests.Session:
    """Log in to the SMS portal. Returns an authenticated requests.Session.

    Raises RuntimeError if RICHLAND_SMS_USERNAME/PASSWORD are not set, or if
    the site rejects the credentials.
    """
    username = os.environ.get("RICHLAND_SMS_USERNAME", "")
    password = os.environ.get("RICHLAND_SMS_PASSWORD", "")
    if not username or not password:
        raise RuntimeError(
            "RICHLAND_SMS_USERNAME / RICHLAND_SMS_PASSWORD not set "
            "(expected in .env at repo root)"
        )

    s = session or requests.Session()
    s.headers.update({"User-Agent": _UA})

    r0 = s.get(LOGIN_URL, timeout=20)
    r0.raise_for_status()
    data, form, _ = _form_fields(r0.text)

    # The bitmap CAPTCHA's answer is embedded in plaintext in this hidden
    # field (see module docstring) — no OCR/solver needed.
    captcha = data.get("ctl00$cpMainContent$hidStrRandom", "")

    data["ctl00$cpMainContent$loginPage$UserName"] = username
    data["ctl00$cpMainContent$loginPage$Password"] = password
    data["ctl00$cpMainContent$loginPage$txtBitMapCaptcha"] = captcha
    data["ctl00$cpMainContent$loginPage$btnLoginCaptcha"] = "Log In"

    post_url = requests.compat.urljoin(LOGIN_URL, form.get("action"))
    r1 = s.post(post_url, data=data, timeout=20, headers={"Referer": LOGIN_URL})
    r1.raise_for_status()

    if "lblFail" in r1.text and "incorrect" in r1.text.lower():
        raise RuntimeError("SMS login rejected — check RICHLAND_SMS_USERNAME/PASSWORD")

    return s


def open_document_search(session: requests.Session, home_html: str, home_url: str) -> tuple[str, str, requests.Session]:
    """
    Click the first active subscription's "Search" link (GridView Select
    postback) and follow the resulting popup chain through to
    QueryPanel.aspx (the actual search form).

    Returns (query_panel_html, query_panel_url, session).
    """
    data, form, _ = _form_fields(home_html)
    data["__EVENTTARGET"] = "ctl00$cpMainContent$gvResults"
    data["__EVENTARGUMENT"] = "Select$0"
    post_url = requests.compat.urljoin(home_url, form.get("action"))
    r2 = session.post(post_url, data=data, timeout=20, headers={"Referer": home_url})
    r2.raise_for_status()

    m = re.search(r'window\.open\("([^"]+)"', r2.text)
    if not m:
        raise RuntimeError("No subscription found (or no 'Search' link in gvResults) on Home.aspx")
    viewer_url = m.group(1)

    r3 = session.get(viewer_url, timeout=20, headers={"Referer": r2.url})
    r3.raise_for_status()

    # r3.url is doubled by the site's own http->https redirect — dedupe
    # before building the QueryPanel.aspx request (see module docstring).
    clean_qs = _dedupe_qs(r3.url).split("?", 1)[1]
    sp3 = urlsplit(r3.url)
    qp_url = urlunsplit((
        sp3.scheme, sp3.netloc,
        sp3.path.rsplit("/", 1)[0] + "/QueryPanel.aspx",
        clean_qs, "",
    ))
    r4 = session.get(qp_url, timeout=20, headers={"Referer": r3.url})
    r4.raise_for_status()

    return r4.text, qp_url, session


# ---------------------------------------------------------------------------
# Search + pagination
# ---------------------------------------------------------------------------

def search(
    session: requests.Session,
    qp_html: str,
    qp_url: str,
    doc_type_codes: list[str],
    date_begin: date,
    date_end: date,
) -> str:
    """Submit the QueryPanel search form. Returns the results-page HTML
    (first page, up to 50 rows)."""
    data, form, _ = _form_fields(qp_html)
    data["__EVENTTARGET"] = "btnSearch"  # NOT the button's own name "btnNewSearch" — see docstring bug #2
    data["__EVENTARGUMENT"] = ""
    data["chkUseDateRange"] = "on"  # required or the date fields below are silently ignored — see docstring bug #3
    data["datePickerBegin"] = date_begin.strftime("%m/%d/%Y")
    data["datePickerEnd"] = date_end.strftime("%m/%d/%Y")
    data["ListBoxDocumentTypes"] = list(doc_type_codes)

    post_url = requests.compat.urljoin(qp_url, form.get("action"))
    r = session.post(post_url, data=data, timeout=30, headers={"Referer": qp_url})
    r.raise_for_status()
    if "not in a correct format" in r.text:
        raise RuntimeError(
            "ROD search returned the .NET FormatException banner — "
            "the auth querystring or postback target may have changed server-side"
        )
    return r.text


_RESULTS_DISPLAY_RE = re.compile(
    r'lblResultsDisplay"[^>]*>Displaying\s+(\d+)\s+to\s+(\d+)\s+of\s+(\d+)'
)


def _results_progress(html: str) -> Optional[tuple[int, int, int]]:
    """Return (from, to, total) from the 'Displaying X to Y of Z' label, or
    None if not present (no results, or the search itself failed)."""
    m = _RESULTS_DISPLAY_RE.search(html)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def next_page(session: requests.Session, results_html: str, post_url: str) -> str:
    """POST the results page's own form with btnNext=Next to fetch the next
    50 rows. `post_url` is the action URL used for the original search."""
    data, form, _ = _form_fields(results_html)
    data["btnNext"] = "Next"
    action_url = requests.compat.urljoin(post_url, form.get("action"))
    r = session.post(action_url, data=data, timeout=30, headers={"Referer": post_url})
    r.raise_for_status()
    return r.text


def search_all_pages(
    session: requests.Session,
    qp_html: str,
    qp_url: str,
    doc_type_codes: list[str],
    date_begin: date,
    date_end: date,
) -> list[str]:
    """Run a search and page through all results (capped at
    MAX_PAGES_PER_DOC_TYPE_GROUP). Returns the list of each page's raw HTML."""
    pages: list[str] = []
    html = search(session, qp_html, qp_url, doc_type_codes, date_begin, date_end)
    pages.append(html)

    progress = _results_progress(html)
    if progress is None:
        return pages  # no results

    _, to, total = progress
    post_url = requests.compat.urljoin(qp_url, "QueryPanel.aspx")
    page_count = 1
    while to < total and page_count < MAX_PAGES_PER_DOC_TYPE_GROUP:
        time.sleep(PAGE_FETCH_DELAY_SECONDS)
        html = next_page(session, html, post_url)
        pages.append(html)
        progress = _results_progress(html)
        if progress is None:
            break
        _, to, total = progress
        page_count += 1

    return pages


# ---------------------------------------------------------------------------
# Results parsing
# ---------------------------------------------------------------------------

_ROW_ID_RE = re.compile(r'id="DataList1_(ctl\d+)_lblDate"')


def _row_field(html: str, row_id: str, field: str) -> Optional[str]:
    m = re.search(
        rf'id="DataList1_{re.escape(row_id)}_{field}"[^>]*>(.*?)</span>',
        html, re.DOTALL,
    )
    if not m:
        return None
    return m.group(1)


def _split_multiline(raw_html: Optional[str]) -> list[str]:
    """Several row fields (lblNames, lblTaxMapNumbers, lblAddress1) can hold
    more than one value joined by literal '<br>' tags (multi-parcel liens,
    multi-owner records). Split on <br> (case-insensitive, self-closing or
    not), collapse whitespace per piece, drop empties."""
    if not raw_html:
        return []
    parts = re.split(r"<br\s*/?>", raw_html, flags=re.IGNORECASE)
    cleaned = [re.sub(r"\s+", " ", p).strip() for p in parts]
    return [c for c in cleaned if c]


def _clean_text(raw: Optional[str]) -> Optional[str]:
    """Collapse a possibly-multiline field to one display string —
    '; '-joins multiple <br>-separated values instead of leaving raw tags
    in the output (confirmed live: lblTaxMapNumbers/lblAddress1 can each
    carry more than one value on a multi-parcel lien)."""
    parts = _split_multiline(raw)
    return "; ".join(parts) if parts else None


def _split_names(raw_html: Optional[str]) -> list[str]:
    """lblNames content is e.g. 'SMITH,  BARBARA<BR>HOMESIDE LENDING INC<BR>'
    — split on <br>, collapse whitespace, drop empties."""
    return _split_multiline(raw_html)


def parse_result_rows(html: str) -> list[dict]:
    """Parse one results-page HTML into a list of raw row dicts."""
    rows = []
    for m in _ROW_ID_RE.finditer(html):
        row_id = m.group(1)
        date_raw = _row_field(html, row_id, "lblDate")
        instrument = _clean_text(_row_field(html, row_id, "lblInstrument"))
        doc_type = _clean_text(_row_field(html, row_id, "lblDocumentType"))
        names = _split_names(_row_field(html, row_id, "lblNames"))
        book_page = _clean_text(_row_field(html, row_id, "lblBookPage"))
        addr1 = _clean_text(_row_field(html, row_id, "lblAddress1"))
        addr2 = _clean_text(_row_field(html, row_id, "lblAddress2"))
        city_state_zip = _clean_text(_row_field(html, row_id, "lblCityStateZip"))
        tax_map = _clean_text(_row_field(html, row_id, "lblTaxMapNumbers"))
        legal = _clean_text(_row_field(html, row_id, "lblLegal"))

        rows.append({
            "date_raw": _clean_text(date_raw),
            "instrument": instrument,
            "doc_type": doc_type,
            "names": names,
            "book_page": book_page,
            "address1": addr1,
            "address2": addr2,
            "city_state_zip": city_state_zip,
            "tax_map": tax_map,
            "legal": legal,
        })
    return rows


def _normalize_recorded_date(date_raw: Optional[str]) -> Optional[str]:
    """'2/19/1998 11:34:49 AM' -> '1998-02-19'."""
    if not date_raw:
        return None
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", date_raw)
    if not m:
        return None
    mo, d, y = m.groups()
    return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"


_DOLLAR_RE = re.compile(r"\$\s*[\d,]+(?:\.\d{2})?")


def _split_legal_and_amount(legal_text: Optional[str]) -> tuple[Optional[str], list[dict]]:
    """The 'Legal:' row field is reused by the site to show the LIEN AMOUNT
    for lien-type documents (confirmed live: every mechanics/tax lien row's
    lblLegal is a bare dollar figure, e.g. '$94379.43', sometimes followed
    by a real legal description like '3.814 ACRES IN COUNTY' on the same
    line) rather than a subdivision/lot description. Split the dollar
    amount out into `amounts` and keep only the remainder as
    legal_description."""
    if not legal_text:
        return None, []
    amounts = []
    m = _DOLLAR_RE.search(legal_text)
    remainder = legal_text
    if m:
        value = float(m.group(0).replace("$", "").replace(",", ""))
        amounts.append({"amount": value, "amount_type": "lien_amount"})
        remainder = (legal_text[:m.start()] + legal_text[m.end():]).strip()
    return (remainder or None), amounts


def _situs_address(row: dict) -> Optional[str]:
    parts = [row.get("address1"), row.get("address2"), row.get("city_state_zip")]
    parts = [p for p in parts if p]
    return ", ".join(parts) if parts else None


def _stable_id(instrument: Optional[str], row: dict) -> str:
    key = instrument or json.dumps(row, sort_keys=True)
    return "raw_" + hashlib.md5(f"{SOURCE_ID}:{key}".encode()).hexdigest()[:16]


def row_to_raw_event_record(row: dict, captured_at: str) -> Optional[dict]:
    """Map a parsed ROD row to the raw_event_record shape the staged
    pipeline expects (see scrapers/columbia_star_richland.py for the same
    shape from Richland's other primary source)."""
    doc_type_text = row.get("doc_type") or ""
    canonical: Optional[str] = None
    for _code, (label, canon) in DOC_TYPE_CANONICAL.items():
        if doc_type_text.strip().lower() == label.lower():
            canonical = canon
            break
    if canonical is None:
        return None  # doc type we didn't search for / can't classify — skip

    names = row.get("names") or []
    if not names:
        # Confirmed live: newly-recorded instruments sometimes appear in
        # the index with every name field blank for the first few days
        # (the clerk's office backfills party names after the raw
        # recording) — with no debtor identity there's nothing for the
        # pipeline to resolve or score, so skip. scrape()'s trailing
        # cursor overlap re-covers these dates on the next run, so a
        # record like this gets picked up once its names are filled in —
        # as long as that happens within the overlap window.
        return None
    debtor_name = names[0] if names else None
    filer_name = names[1] if len(names) > 1 else None

    # name_type per UNIVERSAL_DEBTOR_PARTY_RULES (scaffold/pipeline/debtor_party_engine.py):
    #   federal_tax_lien / state_tax_lien -> debtor "TP", filer "GR"
    #   mechanics_lien                    -> debtor "GR", filer "GE"
    if canonical == "mechanics_lien":
        debtor_type, filer_type = "GR", "GE"
    else:
        debtor_type, filer_type = "TP", "GR"

    parties = []
    if debtor_name:
        parties.append({"name": debtor_name, "name_type": debtor_type, "raw_role": "DEBTOR"})
    if filer_name:
        parties.append({"name": filer_name, "name_type": filer_type, "raw_role": "FILER"})

    recorded_date = _normalize_recorded_date(row.get("date_raw"))
    instrument = row.get("instrument")
    legal_description, amounts = _split_legal_and_amount(row.get("legal"))

    # Multi-parcel liens carry more than one TMS in lblTaxMapNumbers
    # (already "; "-joined by _clean_text) — property_refs.parcel_id is a
    # single value elsewhere in this pipeline, so take the first; the full
    # list survives in document_body_text below. This site renders TMS
    # space-separated ("28900 01 20") where the rest of the pipeline
    # (scrapers/richland_assessor_spatialest.py's _TMS_DIGIT_RE) expects
    # dash-separated digit-only TMS ("28900-01-20") before it will prepend
    # the "R" prefix and enrich — normalize here so this source's leads are
    # enrichable the same way Columbia Star's already are.
    tax_maps = [
        re.sub(r"^(\d{5})\s+(\d{2})\s+(\d{2})$", r"\1-\2-\3", tm)
        for tm in ((row.get("tax_map") or "").split("; ") if row.get("tax_map") else [])
    ]
    parcel_id = tax_maps[0] if tax_maps else None

    body_lines = [
        f"DOCUMENT TYPE: {doc_type_text}",
        f"INSTRUMENT: {instrument or ''}",
        f"BOOK/PAGE: {row.get('book_page') or ''}",
        f"NAMES: {'; '.join(names)}",
        f"TAX MAP(S): {'; '.join(tax_maps)}",
    ]
    if legal_description:
        body_lines.append(f"LEGAL: {legal_description}")

    return {
        "raw_event_id": _stable_id(instrument, row),
        "source_id": SOURCE_ID,
        "source_role": "PRIMARY_EVENT_SOURCE",
        "raw_doc_type": doc_type_text,
        "canonical_doc_type": canonical,
        "instrument_number": instrument,
        "recorded_date": recorded_date,
        "event_date": recorded_date,
        "source_url": "https://www7.richlandcountysc.gov/rodpublicviewer/viewer.aspx",
        "parties": parties,
        "document_body_text": "\n".join(body_lines)[:2000],
        "property_refs": {
            "parcel_id": parcel_id,
            "situs_address": _situs_address(row),
            "legal_description": legal_description,
            "case_number": None,
        },
        "amounts": amounts,
        "parser_name": "richland_register_of_deeds_v1",
        "parser_version": "1.0.0",
        "parser_confidence": 70 if row.get("tax_map") or _situs_address(row) else 55,
        "captured_at": captured_at,
    }


# ---------------------------------------------------------------------------
# Cursor helpers
# ---------------------------------------------------------------------------

def _load_cursor() -> dict:
    if CURSOR_PATH.exists():
        return json.loads(CURSOR_PATH.read_text(encoding="utf-8"))
    return {"last_scraped_date": None}


def _save_cursor(cursor: dict) -> None:
    CURSOR_PATH.write_text(json.dumps(cursor, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main scrape entry point
# ---------------------------------------------------------------------------

def scrape(incremental: bool = True) -> list[dict]:
    """
    Scrape new Register of Deeds mechanics-lien and tax-lien recordings for
    Richland County, over the date range since the last successful run (or
    the last DEFAULT_BACKFILL_DAYS days on first run).

    Returns:
        List of raw_event_record dicts.
    """
    captured_at = datetime.now(timezone.utc).isoformat()
    today = date.today()

    cursor = _load_cursor() if incremental else {"last_scraped_date": None}
    last = cursor.get("last_scraped_date")
    if last:
        # 2-day trailing overlap, not last+1: newly-recorded instruments can
        # sit in the index with blank party names for a few days before the
        # clerk's office fills them in (see row_to_raw_event_record) — a
        # hard non-overlapping cursor would permanently skip a record whose
        # names populate after its recorded_date has scrolled past the
        # cursor. Re-scanning is safe: raw_event_id is stable per
        # instrument number, so the pipeline dedupes the repeat.
        date_begin = datetime.strptime(last, "%Y-%m-%d").date() - timedelta(days=2)
    else:
        date_begin = today - timedelta(days=DEFAULT_BACKFILL_DAYS)

    if date_begin > today:
        print(f"[richland_register_of_deeds] Cursor already up to date ({last}). Nothing to do.")
        return []

    print(f"[richland_register_of_deeds] Logging in…")
    session = login()
    home = session.get(HOME_URL, timeout=20)
    qp_html, qp_url, session = open_document_search(session, home.text, home.url)

    all_records: list[dict] = []
    doc_codes = list(DOC_TYPE_CANONICAL.keys())

    print(
        f"[richland_register_of_deeds] Searching doc types "
        f"{sorted(set(c for _, c in DOC_TYPE_CANONICAL.values()))} "
        f"from {date_begin} to {today}…"
    )
    try:
        pages = search_all_pages(session, qp_html, qp_url, doc_codes, date_begin, today)
    except Exception as exc:
        print(f"[richland_register_of_deeds] Search failed: {exc}")
        return []

    for page_html in pages:
        for row in parse_result_rows(page_html):
            rec = row_to_raw_event_record(row, captured_at)
            if rec:
                all_records.append(rec)

    progress = _results_progress(pages[0]) if pages else None
    total_hint = progress[2] if progress else 0
    print(
        f"[richland_register_of_deeds] Parsed {len(all_records)} records "
        f"across {len(pages)} page(s) (site reported {total_hint} total matches)"
    )

    cursor["last_scraped_date"] = today.strftime("%Y-%m-%d")
    _save_cursor(cursor)

    out_path = RAW_DIR / f"register_of_deeds_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for rec in all_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[richland_register_of_deeds] Wrote {len(all_records)} records → {out_path}")

    return all_records


if __name__ == "__main__":
    records = scrape(incremental=True)
    print(f"Total records collected: {len(records)}")
