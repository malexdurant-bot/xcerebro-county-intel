"""
Richland County, SC — Probate Estate Inquiry lookup / enrichment.

Portal: https://www7.richlandcountysc.gov/EstateInquiry/main.aspx
Confirmed live 2026-08-22: classic ASP.NET WebForms page (__VIEWSTATE /
__EVENTVALIDATION postback), searchable by decedent name only.

IMPORTANT — this is a name-lookup portal, not a bulk/date-range feed:
searching REQUIRES both a last name AND a first name of at least 2
characters each, and both are applied as real prefix filters (confirmed by
probing — a mismatched first name sharply narrows/empties the result set).
There is no way to list "estates opened since date X" and no last-name-only
search (unlike Shelby County's CourtConnect). A true bulk sweep would need a
combinatorial last-name-prefix x first-name-prefix crawl (tens of thousands
of requests) against a small county ASP.NET form, which is impractical and
not a reasonable request load.

So this module is NOT run as a primary event source. It is used as an
ENRICHMENT step: Columbia Star's "Notice to Creditors" scraper already
supplies a weekly, comprehensive list of decedent names and rough case
numbers (source_id `probate_estate_inquiry`, canonical_doc_type
`letters_testamentary`). For each of those raw events, this module looks up
the decedent by name here and — when it finds a confident match — fills in
the official case number and estate-opened date directly from the county's
own record, upgrading `event_date` from null to a real filing date.

Result table columns (confirmed live):
  Case Number | Decedent's Name (Last, First Middle Suffix) | Date of Death |
  Date Opened | Date Closed | Estate Clerk eMail | [View Parties]
Case number format: YYYY-ES40-NNNNN (year embedded).
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CACHE_DIR = ROOT / "data" / "raw" / "richland_probate_estate_inquiry"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_PATH = CACHE_DIR / "lookup_cache.json"

SOURCE_ID = "probate_estate_inquiry"
PORTAL_URL = "https://www7.richlandcountysc.gov/EstateInquiry/main.aspx"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_LOOKUP_DELAY = 1.0  # seconds between lookups — this is a small county form

_HIDDEN_FIELD_RE = {
    name: re.compile(
        r'name="' + re.escape(name) + r'" id="' + re.escape(name) + r'" value="([^"]*)"'
    )
    for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION")
}


def _extract_hidden(name: str, html_text: str) -> str:
    m = _HIDDEN_FIELD_RE[name].search(html_text)
    return m.group(1) if m else ""


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": _UA})
    return s


def _normalize_date(raw: str) -> Optional[str]:
    """'08/08/2018' -> '2018-08-08'."""
    raw = (raw or "").strip()
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
    if not m:
        return None
    mo, d, y = m.groups()
    return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"


def lookup_estate(
    last_name: str,
    first_name: str,
    *,
    session: Optional[requests.Session] = None,
) -> list[dict]:
    """
    Search the Estate Inquiry portal by decedent name.

    Both last_name and first_name must be >= 2 characters (portal
    requirement); shorter values are truncated-then-rejected by the site
    and will return an empty list here without a network call.

    Returns a list of dicts:
        {case_number, decedent_last, decedent_first, decedent_middle,
         suffix, date_of_death, date_opened, date_closed}
    ordered as the portal returns them (most portals: relevance/insert order).
    """
    last_name = (last_name or "").strip()
    first_name = (first_name or "").strip()
    if len(last_name) < 2 or len(first_name) < 2:
        return []

    s = session or _new_session()

    r0 = s.get(PORTAL_URL, timeout=20)
    r0.raise_for_status()

    data = {
        "__LASTFOCUS": "",
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": _extract_hidden("__VIEWSTATE", r0.text),
        "__VIEWSTATEGENERATOR": _extract_hidden("__VIEWSTATEGENERATOR", r0.text),
        "__VIEWSTATEENCRYPTED": "",
        "__EVENTVALIDATION": _extract_hidden("__EVENTVALIDATION", r0.text),
        "txtLastName": last_name,
        "txtFirstName": first_name,
        "btnSubmit": "Search",
    }
    r1 = s.post(PORTAL_URL, data=data, timeout=20)
    r1.raise_for_status()

    return _parse_results(r1.text)


def _parse_results(html_text: str) -> list[dict]:
    soup = BeautifulSoup(html_text, "html.parser")
    table = soup.find("table", id="gvEstates")
    if table is None:
        return []

    results: list[dict] = []
    rows = table.find_all("tr")
    for row in rows[1:]:  # skip header row
        cells = row.find_all("td")
        if len(cells) < 6:
            continue

        case_number = cells[1].get_text(strip=True)
        if not re.match(r"^\d{4}-ES\d{2}-\d+$", case_number):
            continue

        last_span = row.find("span", id=re.compile(r"lblDecedentLastName$"))
        first_span = row.find("span", id=re.compile(r"lblDecedentFirstName$"))
        middle_span = row.find("span", id=re.compile(r"lblDecedentMiddleName$"))
        suffix_span = row.find("span", id=re.compile(r"lblNameSuffix$"))

        results.append({
            "case_number": case_number,
            "decedent_last": last_span.get_text(strip=True) if last_span else "",
            "decedent_first": first_span.get_text(strip=True) if first_span else "",
            "decedent_middle": middle_span.get_text(strip=True) if middle_span else "",
            "suffix": suffix_span.get_text(strip=True) if suffix_span else "",
            "date_of_death": _normalize_date(cells[3].get_text(strip=True)),
            "date_opened": _normalize_date(cells[4].get_text(strip=True)),
            "date_closed": _normalize_date(cells[5].get_text(strip=True)),
        })

    return results


# ---------------------------------------------------------------------------
# Enrichment step — confirm Columbia Star probate leads against the portal
# ---------------------------------------------------------------------------

def _split_name(raw: str) -> Optional[tuple[str, str]]:
    """Best-effort split of a free-text decedent name into (first, last)."""
    raw = re.sub(r"\s+", " ", (raw or "").replace("\n", " ")).strip().rstrip(",.")
    if not raw:
        return None
    if "," in raw:
        last, _, rest = raw.partition(",")
        first = rest.strip().split()[0] if rest.strip() else ""
        last = last.strip()
    else:
        parts = raw.split()
        if len(parts) < 2:
            return None
        last = parts[-1]
        first = parts[0]
    if len(last) < 2 or len(first) < 2:
        return None
    return first, last


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def _cache_key(last: str, first: str) -> str:
    return f"{last.strip().upper()}|{first.strip().upper()}"


def enrich_estate_raw_events(
    raw_events: list[dict],
    *,
    max_new_lookups: int | None = None,
) -> list[dict]:
    """
    Pre-pipeline step: for `letters_testamentary` raw events (source_id
    probate_estate_inquiry, no event_date yet — Columbia Star's parser never
    sets one), look the decedent up on the official Estate Inquiry portal and
    fill in the confirmed case number / estate-opened date when a confident
    single match is found.

    Args:
        max_new_lookups: cap on NETWORK lookups performed this call (cache
            hits don't count against it). None (default) means unlimited —
            fine for normal daily incremental runs, which only ever have a
            handful of new estate events. Pass a small number for a one-off
            historical catch-up sweep so it finishes in a bounded time;
            events beyond the cap simply stay unconfirmed and get picked up
            on a later run (the cache means no lookup is ever repeated).

    Mutates events in-place. Returns the same list so callers can chain:
        raw_events = enrich_estate_raw_events(raw_events)
    """
    targets = [
        e for e in raw_events
        if e.get("canonical_doc_type") == "letters_testamentary"
        and not e.get("event_date")
    ]
    if not targets:
        return raw_events

    cache = _load_cache()
    cache_hits = sum(
        1 for e in targets
        if (n := next((p["name"] for p in e.get("parties", []) if p.get("name_type") == "GR"), None))
        and (s := _split_name(n))
        and _cache_key(s[1], s[0]) in cache
    )
    print(
        f"[richland_probate_estate_inquiry] Confirming {len(targets)} estate events against the "
        f"portal ({cache_hits} already cached from a prior run)…"
    )
    session = _new_session()
    confirmed = 0
    cache_dirty = False
    new_lookups = 0

    for event in targets:
        decedent = next(
            (p["name"] for p in event.get("parties", []) if p.get("name_type") == "GR"),
            None,
        )
        if not decedent:
            continue
        split = _split_name(decedent)
        if not split:
            continue
        first, last = split
        key = _cache_key(last, first)

        if key not in cache and max_new_lookups is not None and new_lookups >= max_new_lookups:
            continue

        if key in cache:
            m = cache[key]
        else:
            new_lookups += 1
            time.sleep(_LOOKUP_DELAY)
            try:
                matches = lookup_estate(last, first, session=session)
            except requests.RequestException as exc:
                print(f"[richland_probate_estate_inquiry]   lookup failed for {decedent!r}: {exc}")
                continue

            # Only act on a confident, unambiguous match.
            exact = [
                mm for mm in matches
                if mm["decedent_last"].strip().upper() == last.upper()
                and mm["decedent_first"].strip().upper().startswith(first.upper())
            ]
            if len(exact) > 1:
                # Same name can recur across decades of estate filings.
                # Columbia Star's Notice to Creditors is a weekly feed of
                # newly-opened estates, so disambiguate by keeping only
                # matches opened in the last ~18 months; only proceed if
                # that narrows to exactly one.
                recent = [
                    mm for mm in exact
                    if mm["date_opened"]
                    and (
                        datetime.now(timezone.utc).date()
                        - datetime.strptime(mm["date_opened"], "%Y-%m-%d").date()
                    ).days <= 548
                ]
                exact = recent if len(recent) == 1 else []

            m = exact[0] if len(exact) == 1 else None
            cache[key] = m
            cache_dirty = True
            # Save incrementally (not just at the end) so a long historical
            # sweep that gets interrupted doesn't lose all its progress —
            # every completed lookup is a real network round-trip we don't
            # want to repeat on the next run.
            _save_cache(cache)

        if m is None:
            continue

        if event.get("property_refs") is None:
            event["property_refs"] = {}
        event["property_refs"]["case_number"] = m["case_number"]
        event["property_refs"]["_enriched_via"] = "probate_estate_inquiry_confirmed"
        if m["date_opened"]:
            event["event_date"] = m["date_opened"]
        if m["date_of_death"]:
            body = event.get("document_body_text") or ""
            event["document_body_text"] = (
                body + f"\nCONFIRMED DATE OF DEATH: {m['date_of_death']}"
                f"\nCONFIRMED CASE NUMBER: {m['case_number']}"
            ).strip()
        confirmed += 1

    if cache_dirty:
        _save_cache(cache)

    print(f"[richland_probate_estate_inquiry] Confirmed {confirmed}/{len(targets)} estate events")
    return raw_events


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Richland County SC Estate Inquiry name lookup (probe/manual use)."
    )
    parser.add_argument("last_name")
    parser.add_argument("first_name")
    args = parser.parse_args()

    now = datetime.now(timezone.utc).isoformat()
    for r in lookup_estate(args.last_name, args.first_name):
        print(r)
