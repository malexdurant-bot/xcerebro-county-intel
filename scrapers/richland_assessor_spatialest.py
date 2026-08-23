"""
Richland County, SC — Spatialest assessor enrichment scraper.

Provides the enrichment_provider callable required by run_staged_pipeline().
Called with a TMS parcel ID; returns a parcel dict or None.

Auth: X-CSRF-TOKEN from meta tag (not X-XSRF-TOKEN from cookie).
Session must be established by GETting the root page before API calls.

Usage (as enrichment provider):
    from scrapers.richland_assessor_spatialest import make_enrichment_provider
    provider = make_enrichment_provider()
    parcel = provider("R03214-03-21")  # -> dict or None
"""
from __future__ import annotations

import logging
import re
import time
from functools import lru_cache
from typing import Optional

import requests

log = logging.getLogger(__name__)

ROOT_URL = "https://property.spatialest.com/sc/richland/"
RECORD_CARD_URL = ROOT_URL + "api/v1/recordcard/{tms}"
SEARCH_URL = ROOT_URL + "api/v2/search"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
_REQUEST_DELAY = 1.5  # seconds between calls — be polite

_CURRENCY_RE = re.compile(r"[\$,\s]")
_TMS_DIGIT_RE = re.compile(r"^\d{5}-\d{2}-\d{2}$")  # TMS without letter prefix


def _normalize_tms(tms: str) -> str:
    """Prepend 'R' if TMS is in digit-only format (e.g. 04111-01-09 → R04111-01-09)."""
    if _TMS_DIGIT_RE.match(tms):
        return "R" + tms
    return tms


_SITUS_RE = re.compile(
    r"^(.*)\s+(\w+)\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$"
)


def _build_session() -> tuple[requests.Session, str]:
    """GET root page → return (session, csrf_meta_token)."""
    sess = requests.Session()
    r = sess.get(ROOT_URL, headers={"User-Agent": _UA, "Accept": "text/html"}, timeout=20)
    r.raise_for_status()
    m = re.search(r'<meta name="csrf-token" content="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("Spatialest: csrf-token meta tag not found in root page")
    token = m.group(1)
    return sess, token


def _parse_currency(raw: str | None) -> Optional[int]:
    if not raw:
        return None
    cleaned = _CURRENCY_RE.sub("", raw)
    try:
        return int(float(cleaned))
    except (ValueError, TypeError):
        return None


def _parse_situs(full_address: str) -> tuple[str, str, str, str]:
    """
    Split "500 KENTON DR IRMO SC 29063" into (street, city, state, zip).
    Returns ("", "", "", "") on parse failure.
    """
    m = _SITUS_RE.match(full_address.strip())
    if m:
        return m.group(1), m.group(2).strip(), m.group(3), m.group(4)
    return full_address, "", "SC", ""


def _safe_float(val) -> Optional[float]:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> Optional[int]:
    f = _safe_float(val)
    return int(f) if f is not None else None


def _first_valid_sale(sales: list[dict]) -> tuple[Optional[str], Optional[int]]:
    """
    Walk sales history newest-first; return (date_iso, price_int) for the
    first sale that has a parseable numeric price.
    """
    for sale in sales:
        raw_price = sale.get("Sale_Price_Formatted", "")
        price = _parse_currency(raw_price)
        if price and price > 0:
            raw_date = sale.get("Last_Sale_Date", "")
            # Convert MM/DD/YYYY → YYYY-MM-DD
            if raw_date and "/" in raw_date:
                parts = raw_date.split("/")
                if len(parts) == 3:
                    raw_date = f"{parts[2]}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
            return raw_date, price
    return None, None


def _extract_section_rows(sections: list, section_idx: int) -> list[dict]:
    """
    sections is a 3-level nested list: sections[i][j][k] = row dict.
    Flatten section i into a single list of row dicts.
    """
    try:
        outer = sections[section_idx]
    except IndexError:
        return []
    rows = []
    for group in outer:
        if isinstance(group, list):
            rows.extend(group)
        elif isinstance(group, dict):
            rows.append(group)
    return rows


def _parse_record_card(tms: str, data: dict) -> dict:
    """Convert raw record card JSON to the enrichment_provider dict format."""
    header = data.get("parcel", {}).get("header", {})
    sections = data.get("parcel", {}).get("sections", [])

    # Situs address
    full_addr = header.get("address", "")
    situs_street, situs_city, situs_state, situs_zip = _parse_situs(full_addr)

    # Owner mailing
    owner_name = (header.get("Owner_Name") or "").strip()
    mail_street = " ".join(filter(None, [
        (header.get("Owner_Address") or "").strip(),
        (header.get("Owner_Address2") or "").strip(),
        (header.get("Owner_Address3") or "").strip(),
    ]))
    mail_city  = (header.get("Owner_City") or "").strip()
    mail_state = (header.get("Owner_State") or "").strip()
    mail_zip   = (header.get("Owner_Zip") or "").strip()

    # Assessed / market value
    assessed_raw = header.get("Taxable_Value", "")
    assessed_value = _parse_currency(assessed_raw)

    # Section 0: general info + valuation + tax
    s0_rows = _extract_section_rows(sections, 0)
    general = next((r for r in s0_rows if "Zoning_Primary" in r), {})
    valuation = next((r for r in s0_rows if "Market_Value" in r), {})
    market_value = _parse_currency(valuation.get("Market_Value"))
    building_value = _parse_currency(valuation.get("Building_Value"))
    land_value = _parse_currency(valuation.get("NonAg_Value") or valuation.get("Ag_Value"))

    # Section 1: land
    s1_rows = _extract_section_rows(sections, 1)
    land = s1_rows[0] if s1_rows else {}
    acreage = _safe_float(land.get("Acreage"))

    # Section 2: building/improvement
    s2_rows = _extract_section_rows(sections, 2)
    building = s2_rows[0] if s2_rows else {}
    year_built = _safe_int(building.get("YrBuilt"))
    sq_ft = _safe_int(building.get("HeatedSQFT"))
    property_type = building.get("PropertyType")
    beds = _safe_float(building.get("Beds"))
    baths = _safe_float(building.get("Baths"))
    stories = _safe_float(building.get("Stories"))

    # Section 3: sales history (newest first in the response)
    s3_rows = _extract_section_rows(sections, 3)
    last_sale_date, last_sale_price = _first_valid_sale(s3_rows)

    return {
        "parcel_id": tms,
        # Situs
        "address":          situs_street,
        "city":             situs_city,
        "situs_address":    situs_street,
        "situs_city":       situs_city,
        "situs_state":      situs_state or "SC",
        "situs_zip":        situs_zip,
        "full_situs_address": full_addr,
        # Owner
        "owner_name":              owner_name,
        "owner_mailing_address":   mail_street,
        "owner_mailing_city":      mail_city,
        "owner_mailing_state":     mail_state,
        "owner_mailing_zip":       mail_zip,
        # Values
        "assessed_value":   assessed_value or market_value,
        "market_value":     market_value,
        "building_value":   building_value,
        "land_value":       land_value,
        # Last sale
        "last_sale_date":   last_sale_date,
        "last_sale_price":  last_sale_price,
        # Physical
        "year_built":       year_built,
        "sq_ft":            sq_ft,
        "property_type":    property_type,
        "beds":             beds,
        "baths":            baths,
        "stories":          stories,
        "acreage":          acreage,
        # Parcel info
        "zoning":           general.get("Zoning_Primary"),
        "legal_residence":  general.get("LegalResidence"),
        "tax_district":     general.get("TaxDistrict") or header.get("nbhd"),
        # Source meta
        "_source":          "spatialest",
        "_source_url":      RECORD_CARD_URL.format(tms=tms),
    }


class SpatialestClient:
    """
    Thin HTTP client for the Spatialest record card API.
    Refreshes the session automatically on 419 errors.
    """

    def __init__(self) -> None:
        self._sess: requests.Session | None = None
        self._csrf_token: str = ""
        self._last_call: float = 0.0

    def _ensure_session(self) -> None:
        if self._sess is None or not self._csrf_token:
            log.debug("Spatialest: establishing session")
            self._sess, self._csrf_token = _build_session()

    def _api_headers(self) -> dict:
        return {
            "User-Agent":       _UA,
            "Accept":           "application/json, text/plain, */*",
            "Referer":          ROOT_URL,
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-TOKEN":     self._csrf_token,
        }

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < _REQUEST_DELAY:
            time.sleep(_REQUEST_DELAY - elapsed)
        self._last_call = time.monotonic()

    def fetch(self, tms: str) -> Optional[dict]:
        """Return parsed enrichment dict for TMS, or None on not-found / error."""
        tms = _normalize_tms(tms)
        self._ensure_session()
        self._throttle()
        url = RECORD_CARD_URL.format(tms=tms)
        try:
            r = self._sess.get(url, headers=self._api_headers(), timeout=20)
        except requests.RequestException as exc:
            log.warning("Spatialest: network error for %s: %s", tms, exc)
            return None

        if r.status_code == 419:
            # Session expired — refresh once and retry
            log.info("Spatialest: 419 on %s, refreshing session", tms)
            self._sess, self._csrf_token = _build_session()
            self._throttle()
            r = self._sess.get(url, headers=self._api_headers(), timeout=20)

        if r.status_code == 404:
            log.debug("Spatialest: TMS not found: %s", tms)
            return None
        if r.status_code != 200:
            log.warning("Spatialest: HTTP %s for %s", r.status_code, tms)
            return None

        try:
            data = r.json()
        except Exception as exc:
            log.warning("Spatialest: JSON parse error for %s: %s", tms, exc)
            return None

        if not data.get("parcel"):
            return None

        return _parse_record_card(tms, data)

    def search_by_owner_name(
        self, last: str, first: str, *, full_name: Optional[str] = None
    ) -> Optional[dict]:
        """
        Free owner-name search (no DealMachine/skip-trace needed): the
        general term endpoint matches "LAST FIRST" as a substring against
        the assessor's owner-name field, which is frequently a co-ownership
        string like "SHARPE NINA C & BECKHAM WILLIAM D" — so a common
        surname routinely returns several genuinely different owners/
        parcels, not near-duplicates of one property. Disambiguate using
        the result's own `Owner_Name` display field (never just "how many
        rows came back"):

          1. Keep only results whose Owner_Name contains both `last` and
             `first` as whole words (defensive — the search can be fuzzier
             than a strict substring check).
          2. If more than one remains and `full_name` (the complete cleaned
             decedent string, including middle name/initial) was given,
             narrow further to results whose Owner_Name contains every word
             of `full_name` — this is what separates the real
             "BECKHAM WILLIAM DENNIS" match from an unrelated co-owner
             recorded only as "BECKHAM WILLIAM D".
          3. Only return a match when exactly one candidate survives.

        Returns {"parcel_id": tms, "situs_address": "123 MAIN ST CITY SC
        29201"} on a confident match (the address comes straight from the
        search result — no second request needed to show something in the
        dashboard; the full record card is still fetched later by the
        normal enrichment_provider step). Returns None otherwise.
        """
        self._ensure_session()
        self._throttle()
        term = f"{last.strip()} {first.strip()}".strip()
        payload = {"filters": {"term": term}, "page": 1, "limit": 10}
        try:
            r = self._sess.post(SEARCH_URL, json=payload, headers=self._api_headers(), timeout=20)
        except requests.RequestException as exc:
            log.warning("Spatialest: owner-name search network error for %r: %s", term, exc)
            return None

        if r.status_code == 419:
            self._sess, self._csrf_token = _build_session()
            self._throttle()
            try:
                r = self._sess.post(SEARCH_URL, json=payload, headers=self._api_headers(), timeout=20)
            except requests.RequestException:
                return None

        if r.status_code != 200:
            return None

        try:
            data = r.json()
        except Exception:
            return None

        results = data.get("results") or []
        if not results:
            return None

        def _field(res: dict, field_id: str) -> str:
            for item in res.get("display") or []:
                if isinstance(item, dict) and item.get("id") == field_id:
                    return (item.get("value") or "").upper()
            return ""

        last_u, first_u = last.strip().upper(), first.strip().upper()
        candidates = [
            res for res in results
            if re.search(rf"\b{re.escape(last_u)}\b", _field(res, "Owner_Name"))
            and re.search(rf"\b{re.escape(first_u)}\b", _field(res, "Owner_Name"))
        ]

        if len(candidates) > 1 and full_name:
            name_words = re.findall(r"[A-Z']+", full_name.upper())
            narrowed = [
                res for res in candidates
                if all(re.search(rf"\b{re.escape(w)}\b", _field(res, "Owner_Name")) for w in name_words)
            ]
            if narrowed:
                candidates = narrowed

        if len(candidates) != 1:
            return None

        match = candidates[0]
        tms = match.get("ParcelIdentifier") or match.get("order_property_TMSNUM")
        if not tms:
            return None

        return {"parcel_id": tms, "situs_address": _field(match, "address") or None}


def make_enrichment_provider() -> "callable[[Optional[str]], Optional[dict]]":
    """
    Factory: returns an EnrichmentProvider callable bound to a shared session.

    Usage:
        provider = make_enrichment_provider()
        result["enrichment_provider"] = provider
    """
    client = SpatialestClient()

    def _provider(parcel_id: Optional[str]) -> Optional[dict]:
        if not parcel_id:
            return None
        return client.fetch(parcel_id)

    return _provider


def _split_person_name(raw: str) -> Optional[tuple[str, str]]:
    """Best-effort split of 'FIRST [MIDDLE] LAST' into (first, last)."""
    cleaned = re.sub(r"\s+", " ", (raw or "")).strip().rstrip(",.")
    parts = cleaned.split()
    if len(parts) < 2:
        return None
    return parts[0], parts[-1]


_ESTATE_SEARCH_CACHE_DIR = None  # set lazily so importing this module never touches disk paths eagerly


def _estate_search_cache_path() -> "Path":
    from pathlib import Path
    global _ESTATE_SEARCH_CACHE_DIR
    if _ESTATE_SEARCH_CACHE_DIR is None:
        _ESTATE_SEARCH_CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "raw" / "richland_assessor_spatialest"
        _ESTATE_SEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _ESTATE_SEARCH_CACHE_DIR / "owner_search_cache.json"


def _load_owner_search_cache() -> dict:
    import json
    path = _estate_search_cache_path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_owner_search_cache(cache: dict) -> None:
    import json
    _estate_search_cache_path().write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def _enrich_raw_events_by_owner_name(
    raw_events: list[dict],
    *,
    canonical_doc_type: str,
    party_name_type: str,
    label: str,
    max_new_lookups: int | None = None,
) -> list[dict]:
    """
    Pre-pipeline step: for raw events of `canonical_doc_type` with no parcel
    match yet, take the party whose name_type is `party_name_type` and
    search the county assessor by that name — free, no API key required —
    filling in parcel_id + situs_address on a confident single match. Run
    this BEFORE any paid skip-trace step (e.g. richland_skiptrace_
    dealmachine) so that step only has to cover whatever this free pass
    couldn't resolve.

    Args:
        max_new_lookups: cap on NETWORK lookups performed this call (cache
            hits don't count against it). None (default) is fine for normal
            daily incremental runs (a handful of new events); pass a small
            number for a bounded historical catch-up sweep.

    Mutates events in-place. Returns the same list so callers can chain.
    """
    targets = [
        e for e in raw_events
        if e.get("canonical_doc_type") == canonical_doc_type
        and not (e.get("property_refs") or {}).get("parcel_id")
    ]
    if not targets:
        return raw_events

    cache = _load_owner_search_cache()
    print(f"[richland_assessor_spatialest] Owner-name search for {len(targets)} {label} events…")
    client = SpatialestClient()
    found = 0
    new_lookups = 0

    for event in targets:
        subject = next(
            (p["name"] for p in event.get("parties", []) if p.get("name_type") == party_name_type),
            None,
        )
        if not subject:
            continue
        split = _split_person_name(subject)
        if not split:
            continue
        first, last = split
        full_name = re.sub(r"\s+", " ", subject).strip().rstrip(",.")
        key = full_name.upper()

        if key in cache:
            match = cache[key]
        else:
            if max_new_lookups is not None and new_lookups >= max_new_lookups:
                continue
            new_lookups += 1
            match = client.search_by_owner_name(last, first, full_name=full_name)
            cache[key] = match
            _save_owner_search_cache(cache)  # incremental — never lose progress on interrupt

        if not match:
            continue

        if event.get("property_refs") is None:
            event["property_refs"] = {}
        event["property_refs"]["parcel_id"] = match["parcel_id"]
        if match.get("situs_address"):
            event["property_refs"]["situs_address"] = match["situs_address"]
        event["property_refs"]["_enriched_via"] = "spatialest_owner_search"
        found += 1

    print(f"[richland_assessor_spatialest] Owner-name search matched {found}/{len(targets)} {label} events")
    return raw_events


def enrich_estate_raw_events(
    raw_events: list[dict],
    *,
    max_new_lookups: int | None = None,
) -> list[dict]:
    """Owner-name property lookup for `letters_testamentary` (probate)
    events, keyed on the decedent (GR party) — see
    _enrich_raw_events_by_owner_name for the shared implementation."""
    return _enrich_raw_events_by_owner_name(
        raw_events,
        canonical_doc_type="letters_testamentary",
        party_name_type="GR",
        label="estate",
        max_new_lookups=max_new_lookups,
    )


def enrich_lis_pendens_raw_events(
    raw_events: list[dict],
    *,
    max_new_lookups: int | None = None,
) -> list[dict]:
    """
    Owner-name property lookup for `lis_pendens` events, keyed on the
    defendant (DF party). A lis pendens notice's own text often doesn't
    state a property address at all (many are civil suits unrelated to real
    estate — see extract_lis_pendens_parties), but the defendant may still
    own a house that becomes relevant if they need to sell to resolve the
    legal matter, so this checks regardless of what the underlying lawsuit
    is about — see _enrich_raw_events_by_owner_name for the shared
    implementation.
    """
    return _enrich_raw_events_by_owner_name(
        raw_events,
        canonical_doc_type="lis_pendens",
        party_name_type="DF",
        label="lis pendens",
        max_new_lookups=max_new_lookups,
    )


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    import json
    tms = sys.argv[1] if len(sys.argv) > 1 else "R03214-03-21"
    provider = make_enrichment_provider()
    result = provider(tms)
    print(json.dumps(result, indent=2, ensure_ascii=False))
