"""
Clayton County, GA — Tax Commissioner tax sale listings + excess funds listing.

Sources (config/counties/clayton_ga.json):
  - tax_sale_listings     -> canonical_doc_type tax_foreclosure_notice
  - excess_funds_listing  -> canonical_doc_type sheriff_sale_surplus

Both are PDFs linked from the Tax Commissioner's Public Access landing page:

    https://publicaccess.claytoncountyga.gov/content/home_commissioner.htm

The PDF filenames are ad hoc ("NOVEMBER TAX SALE LISTING 10-27-26.pdf",
"september_2026_tax_sale.pdf", "DQ759GA.pdf"), so this scraper discovers them
from the landing page links every run instead of guessing URLs.

Tax sale PDF row (pdfplumber table):
    [sale_date, "<parcel> /<OWNER ...>", situs, years, fair_market_value, cry_out_bid]
    The parcel/owner cell wraps over several lines; the "/" separates them.
Excess funds PDF row:
    [former_owner, parcel, "$ amount", sale_date (MM/DD/YY)]

Incremental: a seen.json cursor of (source, parcel, sale_date) keys; a parcel
re-listed for a later sale date is a new event. Recon-verified 2026-11-12.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import pdfplumber
import requests
from bs4 import BeautifulSoup

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "clayton_tax_sale"
RAW_DIR.mkdir(parents=True, exist_ok=True)
SEEN_PATH = RAW_DIR / "seen.json"

LANDING_URL = "https://publicaccess.claytoncountyga.gov/content/home_commissioner.htm"
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
})

PARCEL_RE = re.compile(r"^\s*(\d{5}[A-Z]?\s+[A-Z0-9]{3,6}(?:\s+[A-Z0-9]{2,3})?)\b")
DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{2,4}$")


def normalize_parcel(p: str | None) -> str | None:
    if not p:
        return None
    return re.sub(r"\s+", " ", p.strip().upper()) or None


def _money(s: str | None) -> float | None:
    if not s:
        return None
    s = s.replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _iso(d: str | None) -> str | None:
    if not d:
        return None
    d = d.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(d, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _clean_lines(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\n", " ")).strip()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_pdfs() -> dict[str, list[str]]:
    """Return {'tax_sale': [urls], 'excess_funds': [urls]} from the landing page."""
    r = _SESSION.get(LANDING_URL, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out: dict[str, list[str]] = {"tax_sale": [], "excess_funds": []}
    for a in soup.find_all("a", href=True):
        href, text = a["href"], a.get_text(" ", strip=True).lower()
        if not href.lower().endswith(".pdf"):
            continue
        url = urljoin(LANDING_URL, href)
        if "excess funds listing" in text:
            out["excess_funds"].append(url)
        elif "tax sale" in text and "registration" not in text:
            out["tax_sale"].append(url)
    return out


def _fetch_pdf_tables(url: str) -> list[list[str | None]]:
    r = _SESSION.get(url, timeout=60)
    r.raise_for_status()
    rows: list[list[str | None]] = []
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                rows.extend(table)
    return rows


# ---------------------------------------------------------------------------
# Row parsers
# ---------------------------------------------------------------------------

def parse_tax_sale_row(row: list[str | None]) -> dict | None:
    """[sale_date, parcel/owner, situs, years, fmv, bid] -> fields, or None."""
    if not row or len(row) < 6 or not row[0] or not DATE_RE.match(row[0].strip()):
        return None
    cell = _clean_lines(row[1])
    parcel, owner = None, None
    if "/" in cell:
        left, _, right = cell.partition("/")
        m = PARCEL_RE.match(left)
        parcel = normalize_parcel(m.group(1) if m else left)
        owner = right.strip() or None
    else:
        m = PARCEL_RE.match(cell)
        parcel = normalize_parcel(m.group(1)) if m else None
    if not parcel:
        return None
    years = [y for y in re.findall(r"\d{4}", row[3] or "")]
    return {
        "sale_date": _iso(row[0]),
        "parcel_id": parcel,
        "owner_name": owner,
        "situs_address": _clean_lines(row[2]) or None,
        "delinquent_years": years,
        "fair_market_value": _money(row[4]),
        "cry_out_bid": _money(row[5]),
    }


def parse_excess_row(row: list[str | None]) -> dict | None:
    """[former_owner, parcel, '$ amount', 'MM/DD/YY'] -> fields, or None."""
    if not row or len(row) < 4:
        return None
    parcel = normalize_parcel(row[1])
    if not parcel or not PARCEL_RE.match(parcel):
        return None
    return {
        "former_owner": _clean_lines(row[0]) or None,
        "parcel_id": parcel,
        "excess_amount": _money(row[2]),
        "sale_date": _iso(row[3]),
    }


# ---------------------------------------------------------------------------
# raw_event_record builders
# ---------------------------------------------------------------------------

def _rid(prefix: str, *parts: str | None) -> str:
    return prefix + hashlib.md5("|".join(p or "" for p in parts).encode()).hexdigest()[:16]


def tax_sale_event(f: dict, url: str, captured_at: str) -> dict:
    owner = f["owner_name"]
    return {
        "raw_event_id": _rid("raw_cts_", f["parcel_id"], f["sale_date"]),
        "source_id": "tax_sale_listings",
        "source_role": "PRIMARY_EVENT_SOURCE",
        "raw_doc_type": "CLAYTON TAX SALE LISTING",
        "canonical_doc_type": "tax_foreclosure_notice",
        "instrument_number": f"{f['parcel_id']}|{f['sale_date']}",
        "recorded_date": None,
        "event_date": f["sale_date"],
        "source_url": url,
        "parties": [{"name": owner, "name_type": "TP", "raw_role": "OWNER OF RECORD"}] if owner else [],
        "document_body_text": (
            f"PARCEL: {f['parcel_id']}\nOWNER: {owner}\nPROPERTY: {f['situs_address']}\n"
            f"SALE DATE: {f['sale_date']}\nDELINQUENT YEARS: {', '.join(f['delinquent_years'])}\n"
            f"FAIR MARKET VALUE: {f['fair_market_value']}\nCRY-OUT BID: {f['cry_out_bid']}"
        ),
        "property_refs": {
            "parcel_id": f["parcel_id"],
            "situs_address": f["situs_address"],
            "legal_description": None,
            "case_number": None,
        },
        "amounts": [a for a in (
            {"label": "delinquent_amount", "value": f["cry_out_bid"]} if f["cry_out_bid"] is not None else None,
            {"label": "fair_market_value", "value": f["fair_market_value"]} if f["fair_market_value"] is not None else None,
        ) if a],
        "parser_name": "clayton_tax_sale_pdf",
        "parser_version": "1.0.0",
        "parser_confidence": 90 if (owner and f["situs_address"]) else 65,
        "captured_at": captured_at,
    }


def excess_event(f: dict, url: str, captured_at: str) -> dict:
    owner = f["former_owner"]
    return {
        "raw_event_id": _rid("raw_cef_", f["parcel_id"], f["sale_date"], owner),
        "source_id": "excess_funds_listing",
        "source_role": "PRIMARY_EVENT_SOURCE",
        "raw_doc_type": "CLAYTON EXCESS FUNDS LISTING",
        "canonical_doc_type": "sheriff_sale_surplus",
        "instrument_number": f"{f['parcel_id']}|{f['sale_date']}|{owner}",
        "recorded_date": None,
        "event_date": f["sale_date"],
        "source_url": url,
        "parties": [{"name": owner, "name_type": "TP", "raw_role": "FORMER OWNER / CLAIMANT"}] if owner else [],
        "document_body_text": (
            f"PARCEL: {f['parcel_id']}\nFORMER OWNER: {owner}\n"
            f"EXCESS FUNDS: {f['excess_amount']}\nTAX SALE DATE: {f['sale_date']}"
        ),
        "property_refs": {"parcel_id": f["parcel_id"], "situs_address": None,
                          "legal_description": None, "case_number": None},
        "amounts": [{"label": "surplus_amount", "value": f["excess_amount"]}] if f["excess_amount"] is not None else [],
        "parser_name": "clayton_excess_funds_pdf",
        "parser_version": "1.0.0",
        "parser_confidence": 85 if owner else 60,
        "captured_at": captured_at,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _load_seen() -> set[str]:
    return set(json.loads(SEEN_PATH.read_text(encoding="utf-8"))) if SEEN_PATH.exists() else set()


def scrape(incremental: bool = True) -> list[dict]:
    captured_at = datetime.now(timezone.utc).isoformat()
    try:
        pdfs = discover_pdfs()
    except Exception as exc:  # noqa: BLE001
        print(f"[clayton_tax_sale] landing page fetch failed: {exc}")
        return []
    print(f"[clayton_tax_sale] discovered {len(pdfs['tax_sale'])} tax sale PDF(s), "
          f"{len(pdfs['excess_funds'])} excess funds PDF(s)")

    events: list[dict] = []
    for url in pdfs["tax_sale"]:
        try:
            rows = _fetch_pdf_tables(url)
        except Exception as exc:  # noqa: BLE001
            print(f"[clayton_tax_sale] {url}: {exc}")
            continue
        parsed = [p for p in (parse_tax_sale_row(r) for r in rows) if p]
        print(f"[clayton_tax_sale] {url.rsplit('/', 1)[-1]}: {len(parsed)}/{len(rows)} rows parsed")
        events += [tax_sale_event(p, url, captured_at) for p in parsed]
    for url in pdfs["excess_funds"]:
        try:
            rows = _fetch_pdf_tables(url)
        except Exception as exc:  # noqa: BLE001
            print(f"[clayton_tax_sale] {url}: {exc}")
            continue
        parsed = [p for p in (parse_excess_row(r) for r in rows) if p]
        print(f"[clayton_tax_sale] {url.rsplit('/', 1)[-1]}: {len(parsed)}/{len(rows)} rows parsed")
        events += [excess_event(p, url, captured_at) for p in parsed]

    # de-dup within the run (same parcel on two lists / repeated header rows)
    by_id = {e["raw_event_id"]: e for e in events}
    events = list(by_id.values())

    seen = _load_seen() if incremental else set()
    new = [e for e in events if e["raw_event_id"] not in seen]
    SEEN_PATH.write_text(json.dumps(sorted(seen | set(by_id)), indent=2), encoding="utf-8")

    if new:
        out = RAW_DIR / f"clayton_tax_sale_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
        with out.open("w", encoding="utf-8") as fh:
            for e in new:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        print(f"[clayton_tax_sale] wrote {len(new)} new events -> {out}")
    else:
        print("[clayton_tax_sale] no new events")
    return new


if __name__ == "__main__":
    res = scrape(incremental="--full" not in sys.argv)
    print(f"Total new tax sale / excess funds events: {len(res)}")
