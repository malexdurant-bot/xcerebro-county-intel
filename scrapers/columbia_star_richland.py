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

# Address: full and abbreviated street types; also a "Property Address:" prefix
_PROP_ADDR_PREFIX_RE = re.compile(
    r"(?:property\s+address|known\s+as|located\s+at)[:\s]+(\d.+?)(?:\n|;|,\s*SC|\Z)",
    re.IGNORECASE,
)
_ADDR_RE = re.compile(
    r"(\d{1,5}\s+[A-Za-z0-9\s,\.]+?"
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
        address = addr_m.group(1).strip() if addr_m else None
        # Exclude the courthouse sale address from property address
        if address and "2500 Decker" in address:
            addr_m2 = _ADDR_RE.search(block)
            if addr_m2 and "2500 Decker" not in addr_m2.group(1):
                address = addr_m2.group(1).strip()
            else:
                address = None

        parties_m = _IN_CASE_OF_RE.search(block)
        plaintiff_raw = parties_m.group(1).strip() if parties_m else None
        plaintiff = _PL_ROLE_SUFFIX_RE.sub("", plaintiff_raw).strip() if plaintiff_raw else None
        defendant_raw = parties_m.group(2).strip() if parties_m else None
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

_LP_SECTION_RE = re.compile(
    r"LIS\s+PENDENS(.*?)(?=LIS\s+PENDENS|\Z|SUMMONS AND COMPLAINT|ORDER APPOINTING|FAMILY COURT)",
    re.IGNORECASE | re.DOTALL,
)


def _parse_lis_pendens(text: str, article_num: int, article_url: str, captured_at: str) -> list[dict]:
    records = []
    for i, m in enumerate(_LP_SECTION_RE.finditer(text)):
        block = m.group(1).strip()
        if len(block) < 60:
            continue

        case_m = re.search(r"C/A\s+No\.?:?\s*([\d]{4}-CP-\d+-\d+|\d{4}CP\d+)", block, re.IGNORECASE)
        case_number = case_m.group(1).strip() if case_m else f"LP-{article_num}-{i+1}"

        tms_m = _TMS_RE.search(block)
        tms = tms_m.group(1).strip() if tms_m else None

        addr_m = _ADDR_RE.search(block)
        address = addr_m.group(1).strip() if addr_m else None

        # Plaintiff / defendant: same "in the case of...vs." pattern
        p_m = _IN_CASE_OF_RE.search(block)
        plaintiff_raw = p_m.group(1).strip() if p_m else None
        plaintiff = _PL_ROLE_SUFFIX_RE.sub("", plaintiff_raw).strip() if plaintiff_raw else None
        defendant_raw = p_m.group(2).strip() if p_m else None
        defendant = defendant_raw.split(";")[0].strip() if defendant_raw else None

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
        decedent = m.group(1).strip().rstrip(",.")
        case_number = m.group(2).strip()
        rep_name = m.group(3).strip().rstrip(",.")
        rep_address = m.group(4).strip()

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
