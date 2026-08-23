"""
Columbia Star Legal Notices scraper — Richland County, SC.

Scrapes the Columbia Star's public-notices category page and parses three
lead-generating notice types published weekly:

  masters-sales-N         → NOTICE_OF_SALE    (Master-in-Equity foreclosure sales)
  public-notices-N        → LIS_PENDENS       (lis pendens section of public notices)
  notice-to-creditors-N   → LETTERS_TESTAMENTARY (probate Notice to Creditors)

Adapter fingerprint: STATIC_HTML / _html.py pattern.
Rate policy: one article fetch every 2 seconds; never more than 30 req/run.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "columbia_star_richland"
RAW_DIR.mkdir(parents=True, exist_ok=True)
CURSOR_PATH = RAW_DIR / "cursor.json"

CATEGORY_URL = "https://www.thecolumbiastar.com/category/public-notices/"
BASE_URL = "https://www.thecolumbiastar.com"

ARTICLE_TYPES = {
    "masters_sales": {
        "slug_prefix": "masters-sales-",
        "source_id": "master_in_equity_foreclosure_sales",
        "canonical_doc_type": "notice_of_sale",
    },
    "public_notices": {
        "slug_prefix": "public-notices-",
        "source_id": "sc_courts_circuit_civil",
        "canonical_doc_type": "lis_pendens",
    },
    "notice_to_creditors": {
        "slug_prefix": "notice-to-creditors-of-estates-",
        "source_id": "probate_estate_inquiry",
        "canonical_doc_type": "letters_testamentary",
    },
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

_SESSION = requests.Session()
_SESSION.headers.update(HEADERS)


# ---------------------------------------------------------------------------
# Cursor helpers
# ---------------------------------------------------------------------------

def _load_cursor() -> dict:
    if CURSOR_PATH.exists():
        return json.loads(CURSOR_PATH.read_text(encoding="utf-8"))
    return {k: 0 for k in ARTICLE_TYPES}


def _save_cursor(cursor: dict) -> None:
    CURSOR_PATH.write_text(json.dumps(cursor, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Category page — discover latest article numbers
# ---------------------------------------------------------------------------

def _fetch_category_page() -> dict[str, int]:
    """Return {type_key: latest_article_number} found on the category page."""
    r = _SESSION.get(CATEGORY_URL, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    latest: dict[str, int] = {}
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        for key, info in ARTICLE_TYPES.items():
            prefix = "/articles/" + info["slug_prefix"]
            if prefix in href:
                # extract trailing number
                m = re.search(r"/articles/" + re.escape(info["slug_prefix"]) + r"(\d+)", href)
                if m:
                    n = int(m.group(1))
                    if n > latest.get(key, 0):
                        latest[key] = n

    return latest


# ---------------------------------------------------------------------------
# Article fetchers & parsers
# ---------------------------------------------------------------------------

def _fetch_article_text(article_url: str) -> str:
    time.sleep(2)
    r = _SESSION.get(article_url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    # Try article body selectors
    body = (
        soup.find("div", class_="article-body")
        or soup.find("div", class_="entry-content")
        or soup.find("article")
        or soup.find("main")
    )
    return body.get_text("\n") if body else soup.get_text("\n")


def _stable_id(source_id: str, article_num: int, discriminator: str) -> str:
    key = f"{source_id}:{article_num}:{discriminator}"
    return "raw_" + hashlib.md5(key.encode()).hexdigest()[:16]


# --- Master's Sales parser ---

# Case number patterns — SC circuit court
_CASE_RE = re.compile(
    r"(?:C/A\s+No[.:\s]+|C/A:\s*)"
    r"((?:\d{4}-CP-\d+-\d+|\d{4}CP\d+|\d{4}-CP\d{2}-\d+))",
    re.IGNORECASE,
)
# Standalone bare case number
_BARE_CASE_RE = re.compile(r"\b(\d{4}-CP-\d+-\d+)\b")

# TMS / parcel number
_TMS_RE = re.compile(
    r"(?:TMS(?:[/#\s.]+|No\.?\s*)|Parcel\s+(?:No\.?|Number)[:\s]*)"
    r"(R?[\d]+[-\d]+)",
    re.IGNORECASE,
)

# Parties: anchored to "in the case of ... vs." (Columbia Star article language).
# Uses [^;\n] for defendant so periods in "P.M. Services, LLC" are captured.
_IN_CASE_OF_RE = re.compile(
    r"in\s+the\s+case\s+of\s+"
    r"(.{3,300}?)"       # plaintiff — lazy, stops at first "vs."
    r"\s+vs\.?\s+"       # "vs." or "vs" separator
    r"([^;\n]{3,200})",  # defendant — up to first ; or newline
    re.IGNORECASE | re.DOTALL,
)
# Strip trailing role labels appended after plaintiff name (e.g. ", Plaintiff,")
_PL_ROLE_SUFFIX_RE = re.compile(r",\s*Plaintiff\b.*$", re.IGNORECASE)

# Lis Pendens party pattern — this is the phrasing Public Notices' Lis
# Pendens section actually uses ("<name>, Plaintiff, vs. <name>,
# Defendant(s)."), confirmed against real article text: none of these
# records use "in the case of X vs. Y" (that phrasing is Master's Sales
# only), so _IN_CASE_OF_RE never matched a single one of them. "Petitioner"
# covers probate-style captions (e.g. a petition to sell estate real
# property) that use the same "vs." structure with a different plaintiff
# label. The defendant group is capped generously (1500, not 400) because
# foreclosures against a deceased owner's unknown heirs routinely list
# 5-10 named individuals before reaching "Defendant(s)." — see
# _HEIRS_OF_DECEASED_RE below, which extracts the one name from that list
# that actually matters (the deceased original owner) rather than trusting
# whichever name happens to come first in a multi-hundred-word caption.
_LP_PLAINTIFF_VS_RE = re.compile(
    r"(.{3,300}?),?\s*(?:Plaintiff|Petitioner),?\s*vs\.?\s+"
    # [,(]? — code-enforcement notices write "vs. NAME (Defendant)" with no
    # comma at all; without allowing the open-paren here too, it gets
    # swept into the captured name ("Kingsugi Properties, LLC (").
    r"(.{3,1500}?)[,(]?\s*Defendants?(?:\(s\))?\b",
    re.IGNORECASE | re.DOTALL,
)
# Strip a leading case-caption prefix ("C/A No.: 2026-CP-40-02434 ") that
# gets swept into the plaintiff capture when the block starts right at the
# case heading with nothing shorter for the lazy group to anchor to.
_CASE_PREFIX_RE = re.compile(
    r"^.*?C/A\s+No\.?:?\s*[\w-]+\s*", re.IGNORECASE | re.DOTALL
)
# Very common SC foreclosure caption for a deceased original owner: "Any
# heirs-at-law or devisees of <NAME>, [Deceased | their heirs...]". The
# decedent, not whichever named heir happens to appear first in the list
# that follows, is the actual debtor identity / property connection here —
# same idea as a probate lead.
_HEIRS_OF_DECEASED_RE = re.compile(
    r"heirs[\s-]+at[\s-]+law\s+or\s+devisees\s+of\s+"
    r"([A-Z][A-Za-z.,'\s]+?),?\s+(?:deceased|their\s+heirs)",
    re.IGNORECASE,
)
# Fallback for the mortgage-foreclosure boilerplate lis pendens notices that
# don't use the Plaintiff/Defendant caption at all: "...mortgage ... given
# by <mortgagor> to <lender>...". The mortgagor is the property owner facing
# foreclosure — the debtor identity we want either way.
_GIVEN_BY_RE = re.compile(
    r"given\s+by\s+(.{3,150}?)\s+to\s+", re.IGNORECASE
)

# Address: full and abbreviated street types; also a "Property Address:" prefix
_PROP_ADDR_PREFIX_RE = re.compile(
    r"(?:property\s+address|known\s+as|located\s+at)[:\s]+(\d.+?)(?:\n|;|,\s*SC|\Z)",
    re.IGNORECASE,
)
_ADDR_RE = re.compile(
    # The middle group used to be unbounded (`+?`), which let it lazily
    # span across entire unrelated sentences to reach a street-suffix word
    # much later in the block (e.g. matching from a "2022" in "recorded
    # February 25, 2022..." all the way to the real address two sentences
    # later). Real street names are short — bound it so it can't do that.
    r"(\d{1,5}[A-Za-z0-9\s,\.]{0,40}?"
    r"(?:Dr(?:ive)?|St(?:reet)?|Ave(?:nue)?|Rd|Road|Ln|Lane|Blvd|Boulevard"
    r"|Way|Ct|Court|Cir(?:cle)?|Pl(?:ace)?|Pkwy|Parkway|Hwy|Highway|Ter(?:race)?|Loop)\.?"
    r"[,\s]+(?:Columbia|Blythewood|Eastover|Forest Acres|Irmo|Hopkins|Dentsville|Pontiac|Ballentine)"
    r"[,\s]+SC\s+\d{5})",
    re.IGNORECASE,
)

# Sale date: "will be sold on MM/DD/YYYY" or "sold on Month D, YYYY"
_SALE_DATE_RE = re.compile(
    r"(?:sold|sale|scheduled for)\s+(?:on\s+)?"
    r"(\d{1,2}/\d{1,2}/\d{4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})",
    re.IGNORECASE,
)


def _split_into_sale_blocks(text: str) -> list[str]:
    """Split the article text into individual sale notice blocks."""
    # Split on "MASTER'S SALE" heading or "C/A No" at start of line
    blocks = re.split(r"(?i)(?=MASTER'S SALE\b|(?:^|\n)C/A\s+No)", text)
    return [b.strip() for b in blocks if len(b.strip()) > 50]


def _parse_masters_sales(text: str, article_num: int, article_url: str, captured_at: str) -> list[dict]:
    records = []
    blocks = _split_into_sale_blocks(text)

    # Extract sale date from article (same date for all properties)
    sale_date_str: str | None = None
    m = _SALE_DATE_RE.search(text)
    if m:
        sale_date_str = _normalize_date_str(m.group(1))

    for block in blocks:
        # Skip blocks too short to be a real listing
        if len(block) < 80:
            continue

        case_m = _CASE_RE.search(block) or _BARE_CASE_RE.search(block)
        if not case_m:
            continue
        case_number = case_m.group(1).strip()

        tms_m = _TMS_RE.search(block)
        tms = tms_m.group(1).strip() if tms_m else None

        addr_m = _PROP_ADDR_PREFIX_RE.search(block) or _ADDR_RE.search(block)
        address = _ws(addr_m.group(1)) if addr_m else None
        # Exclude the courthouse sale address from property address
        if address and "2500 Decker" in address:
            addr_m2 = _ADDR_RE.search(block)
            if addr_m2 and "2500 Decker" not in addr_m2.group(1):
                address = _ws(addr_m2.group(1))
            else:
                address = None

        parties_m = _IN_CASE_OF_RE.search(block)
        plaintiff_raw = _ws(parties_m.group(1)) if parties_m else None
        plaintiff = _PL_ROLE_SUFFIX_RE.sub("", plaintiff_raw).strip() if plaintiff_raw else None
        defendant_raw = _ws(parties_m.group(2)) if parties_m else None
        defendant_raw = _strip_and_if_deceased(defendant_raw)
        # Take only the first defendant if multiple
        defendant = re.split(r";|,\s+(?:et al\.?|and\s+)", defendant_raw or "")[0].strip() if defendant_raw else None

        raw_event_id = _stable_id("master_in_equity_foreclosure_sales", article_num, case_number)

        record = {
            "raw_event_id": raw_event_id,
            "source_id": "master_in_equity_foreclosure_sales",
            "source_role": "PRIMARY_EVENT_SOURCE",
            "raw_doc_type": "MASTER'S SALE",
            "canonical_doc_type": "notice_of_sale",
            "instrument_number": case_number,
            "recorded_date": None,
            "event_date": sale_date_str,
            "source_url": article_url,
            "parties": _build_parties(plaintiff, defendant),
            "document_body_text": block[:2000],
            "property_refs": {
                "parcel_id": tms,
                "situs_address": address,
                "legal_description": None,
                "case_number": case_number,
            },
            "amounts": [],
            "parser_name": "columbia_star_masters_sales_v1",
            "parser_version": "1.0.0",
            "parser_confidence": _confidence(case_number, tms, address, defendant),
            "captured_at": captured_at,
        }
        records.append(record)

    return records


# --- Lis Pendens parser ---

# The "Public Notices" article type bundles many unrelated notice kinds
# together (foreclosure summonses, probate petitions, name changes,
# negligence suits, demolition orders...) with no "LIS PENDENS" section
# heading anywhere in the actual text — that was a wrong assumption in the
# original version of this parser (confirmed live: every real case caption
# is a "C/A No.: <case> <plaintiff>, Plaintiff, vs. <defendant>,
# Defendant(s)." block, and the word "Lis Pendens" typically only shows up
# later, mentioned in passing inside a "NOTICE OF FILING OF COMPLAINT"
# paragraph — splitting on that phrase cut every block off before the
# caption it needed). Split on "C/A No" instead, the same boundary Master's
# Sales already uses successfully.
_CASE_BLOCK_RE = re.compile(r"(?=(?:^|\n)\s*C/A\s+No\.?:?)", re.IGNORECASE)
_CASE_NUMBER_RE = re.compile(r"C/A\s+No\.?:?\s*([\dA-Z-]{6,20})", re.IGNORECASE)


def _split_into_case_blocks(text: str) -> list[str]:
    """Split article text into one block per case caption ("C/A No...")."""
    blocks = re.split(_CASE_BLOCK_RE, text)
    return [b.strip() for b in blocks if len(b.strip()) > 80]


def extract_lis_pendens_parties(block: str) -> tuple[str | None, str | None]:
    """
    Extract (plaintiff, defendant) from a Lis Pendens notice block. Real
    notices use "<name>, Plaintiff, vs. <name>, Defendant(s)." — NOT the
    "in the case of X vs. Y" phrasing (that's Master's Sales only;
    confirmed against real article text that _IN_CASE_OF_RE never matches a
    lis pendens block). Tries that first anyway in case a future notice uses
    it, then the real pattern, then falls back to the mortgage-foreclosure
    "given by <mortgagor> to <lender>" boilerplate for notices that skip the
    Plaintiff/Defendant caption entirely (mortgagor = the property owner
    facing foreclosure — the debtor identity we want either way).

    Exposed as a standalone function (not inlined in _parse_lis_pendens) so
    a one-time repair pass can re-run the identical logic against
    document_body_text already saved from historical scrapes, without
    needing to re-fetch the source articles.
    """
    p_m = _IN_CASE_OF_RE.search(block) or _LP_PLAINTIFF_VS_RE.search(block)
    plaintiff_raw = _ws(p_m.group(1)) if p_m else None
    if plaintiff_raw:
        plaintiff_raw = _CASE_PREFIX_RE.sub("", plaintiff_raw)
    plaintiff = _PL_ROLE_SUFFIX_RE.sub("", plaintiff_raw).strip() if plaintiff_raw else None
    defendant_raw = _ws(p_m.group(2)) if p_m else None

    defendant = None
    if defendant_raw:
        # "Any heirs-at-law or devisees of <NAME>, deceased..." captions
        # list several named heirs after the boilerplate opener; the
        # decedent named in the opener is the real debtor identity, not
        # whichever heir happens to be split out first.
        heirs_m = _HEIRS_OF_DECEASED_RE.search(defendant_raw)
        if heirs_m:
            defendant = _ws(heirs_m.group(1)).rstrip(",")
        else:
            truncated = _strip_and_if_deceased(defendant_raw)
            defendant = re.split(r";|\s+and\s+", truncated)[0].strip().rstrip(",")

    if not defendant:
        given_m = _GIVEN_BY_RE.search(block)
        if given_m:
            mortgagor = _ws(given_m.group(1)).rstrip(",")
            mortgagor = _strip_and_if_deceased(mortgagor)
            defendant = re.split(r";|\s+and\s+", mortgagor)[0].strip().rstrip(",")

    return plaintiff, defendant or None


def _parse_lis_pendens(text: str, article_num: int, article_url: str, captured_at: str) -> list[dict]:
    records = []
    for block in _split_into_case_blocks(text):
        case_m = _CASE_NUMBER_RE.search(block)
        if not case_m:
            # No case caption in this block at all — text before the first
            # "C/A No" in the article, not a real case notice.
            continue
        case_number = case_m.group(1).strip()

        tms_m = _TMS_RE.search(block)
        tms = tms_m.group(1).strip() if tms_m else None

        addr_m = _ADDR_RE.search(block)
        address = _ws(addr_m.group(1)) if addr_m else None

        plaintiff, defendant = extract_lis_pendens_parties(block)

        # Recording date from block
        rec_m = re.search(
            r"recorded\s+on\s+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})",
            block, re.IGNORECASE,
        )
        rec_date = _normalize_date_str(rec_m.group(1)) if rec_m else None

        raw_event_id = _stable_id("sc_courts_circuit_civil", article_num, case_number)

        record = {
            "raw_event_id": raw_event_id,
            "source_id": "sc_courts_circuit_civil",
            "source_role": "PRIMARY_EVENT_SOURCE",
            "raw_doc_type": "LIS PENDENS",
            "canonical_doc_type": "lis_pendens",
            "instrument_number": case_number,
            "recorded_date": rec_date,
            "event_date": rec_date,
            "source_url": article_url,
            "parties": _build_parties(plaintiff, defendant),
            "document_body_text": block[:2000],
            "property_refs": {
                "parcel_id": tms,
                "situs_address": address,
                "legal_description": None,
                "case_number": case_number,
            },
            "amounts": [],
            "parser_name": "columbia_star_lis_pendens_v1",
            "parser_version": "1.0.0",
            "parser_confidence": _confidence(case_number, tms, address, defendant),
            "captured_at": captured_at,
        }
        records.append(record)

    return records


# --- Notice to Creditors parser ---

_ESTATE_ENTRY_RE = re.compile(
    r"Estate:\s+([A-Z][A-Z\s,\.]+?)\s+"
    r"(\d{2}ES\d+)\s+"
    r"Personal\s+Representative:\s+([A-Z][A-Z\s,\.]+?)\s+"
    r"Address:\s+([^\n]+)",
    re.IGNORECASE,
)


def _parse_notice_to_creditors(text: str, article_num: int, article_url: str, captured_at: str) -> list[dict]:
    records = []
    for m in _ESTATE_ENTRY_RE.finditer(text):
        # Names frequently wrap across a line break in the source article
        # (e.g. "PAMELA\nELAINE SIMS") — the regex's \s allows the newline
        # through uncollapsed, which corrupts every downstream consumer that
        # treats the name as a single display string (owner_name ends up
        # truncated to "PAMELA"). _ws() collapses it to a single space.
        decedent = _ws(m.group(1)).rstrip(",.")
        case_number = m.group(2).strip()
        rep_name = _ws(m.group(3)).rstrip(",.")
        rep_address = _ws(m.group(4))

        raw_event_id = _stable_id("probate_estate_inquiry", article_num, case_number)

        record = {
            "raw_event_id": raw_event_id,
            "source_id": "probate_estate_inquiry",
            "source_role": "PRIMARY_EVENT_SOURCE",
            "raw_doc_type": "NOTICE TO CREDITORS",
            "canonical_doc_type": "letters_testamentary",
            "instrument_number": case_number,
            "recorded_date": None,
            "event_date": None,
            "source_url": article_url,
            "parties": [
                {"name": decedent, "name_type": "GR", "raw_role": "DECEDENT"},
                {"name": rep_name, "name_type": "OTHER", "raw_role": "PERSONAL_REPRESENTATIVE"},
            ],
            "document_body_text": f"DECEDENT: {decedent}\nCASE: {case_number}\nPERSONAL REPRESENTATIVE: {rep_name}\nADDRESS: {rep_address}",
            "property_refs": {
                "parcel_id": None,
                "situs_address": None,
                "legal_description": None,
                "case_number": case_number,
            },
            "amounts": [],
            "parser_name": "columbia_star_notice_to_creditors_v1",
            "parser_version": "1.0.0",
            "parser_confidence": 85,
            "captured_at": captured_at,
        }
        records.append(record)

    return records


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ws(raw: str | None) -> str | None:
    """Collapse internal whitespace (article text frequently wraps names and
    addresses across a line break) to a single space, so downstream display
    fields never show a name truncated at an embedded newline."""
    if raw is None:
        return None
    return re.sub(r"\s+", " ", raw).strip()


# Common SC foreclosure boilerplate that follows a defendant's name:
# "<NAME> AND IF <NAME> be deceased then any child and heir at law to the
# Estate of <NAME> distributees and devisees at law to the Estate of
# <NAME> and if any of the same be dead any and all persons entitled to
# claim under or through them..." — repeats the same name(s), not a
# distinct additional defendant, but a defendant split that doesn't stop
# here ends up with the entire clause as the "name" (confirmed live —
# e.g. "Terrell L Rhodes and Sharon G Rhodes AND IF Terrell L Rhodes and
# Sharon G Rhodes be deceased then any child and heir at law to the Estate
# of Terrell L Rhodes and Sharon G Rhodes distributees and devis...").
_AND_IF_DECEASED_RE = re.compile(r"\s+AND\s+IF\s+.+?\s+be\s+deceased", re.IGNORECASE)


def _strip_and_if_deceased(defendant_raw: str | None) -> str | None:
    """Truncate a captured defendant clause before the AND-IF-deceased
    boilerplate, if present. Apply this before splitting on ';'/'and' so the
    boilerplate's own internal "and"s don't get treated as additional
    defendants."""
    if not defendant_raw:
        return defendant_raw
    return _AND_IF_DECEASED_RE.split(defendant_raw, maxsplit=1)[0]


def _build_parties(plaintiff: str | None, defendant: str | None) -> list[dict]:
    parties = []
    if plaintiff:
        parties.append({"name": plaintiff, "name_type": "PL", "raw_role": "PLAINTIFF"})
    if defendant:
        parties.append({"name": defendant, "name_type": "DF", "raw_role": "DEFENDANT"})
    return parties


def _confidence(case_number, tms, address, defendant) -> int:
    score = 0
    if case_number:
        score += 40
    if tms:
        score += 25
    if address:
        score += 20
    if defendant:
        score += 15
    return score


_MONTH_MAP = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _normalize_date_str(raw: str) -> str | None:
    """Normalize 'August 3, 2026' or '08/03/2026' → 'YYYY-MM-DD'."""
    raw = raw.strip()
    # numeric M/D/YYYY
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
    if m:
        return f"{m.group(3)}-{m.group(1).zfill(2)}-{m.group(2).zfill(2)}"
    # Month D, YYYY
    m = re.match(r"([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", raw)
    if m:
        mon = _MONTH_MAP.get(m.group(1)[:3].lower())
        if mon:
            return f"{m.group(3)}-{mon}-{m.group(2).zfill(2)}"
    return None


# ---------------------------------------------------------------------------
# Main scrape entry point
# ---------------------------------------------------------------------------

def scrape(incremental: bool = True, max_articles_per_type: int = 5) -> list[dict]:
    """
    Scrape new Columbia Star legal notice articles for Richland County.

    Args:
        incremental: If True, only fetch articles newer than the cursor.
        max_articles_per_type: Safety cap on articles fetched per type per run.

    Returns:
        List of raw_event_record dicts.
    """
    cursor = _load_cursor() if incremental else {k: 0 for k in ARTICLE_TYPES}
    captured_at = datetime.now(timezone.utc).isoformat()

    print("[columbia_star_richland] Fetching category page…")
    try:
        latest = _fetch_category_page()
    except Exception as exc:
        print(f"[columbia_star_richland] Category page fetch failed: {exc}")
        return []

    print(f"[columbia_star_richland] Latest article numbers: {latest}")

    all_records: list[dict] = []

    for key, info in ARTICLE_TYPES.items():
        latest_num = latest.get(key, 0)
        cursor_num = cursor.get(key, 0)

        if latest_num <= cursor_num:
            print(f"[columbia_star_richland] {key}: no new articles (cursor={cursor_num})")
            continue

        # Articles to fetch: from cursor+1 up to latest, capped
        nums_to_fetch = list(range(
            max(cursor_num + 1, latest_num - max_articles_per_type + 1),
            latest_num + 1,
        ))

        new_cursor = cursor_num
        for num in nums_to_fetch:
            article_url = f"{BASE_URL}/articles/{info['slug_prefix']}{num}/"
            print(f"[columbia_star_richland] Fetching {article_url}")
            try:
                text = _fetch_article_text(article_url)
            except Exception as exc:
                print(f"[columbia_star_richland]   fetch failed: {exc}")
                continue

            if key == "masters_sales":
                recs = _parse_masters_sales(text, num, article_url, captured_at)
            elif key == "public_notices":
                recs = _parse_lis_pendens(text, num, article_url, captured_at)
            elif key == "notice_to_creditors":
                recs = _parse_notice_to_creditors(text, num, article_url, captured_at)
            else:
                recs = []

            print(f"[columbia_star_richland]   parsed {len(recs)} records from article {num}")
            all_records.extend(recs)
            new_cursor = max(new_cursor, num)

        cursor[key] = new_cursor

    _save_cursor(cursor)

    # Write raw JSONL
    out_path = RAW_DIR / f"columbia_star_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for rec in all_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"[columbia_star_richland] Wrote {len(all_records)} records → {out_path}")
    return all_records


if __name__ == "__main__":
    records = scrape()
    print(f"Total records collected: {len(records)}")
