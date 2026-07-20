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


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    import json
    tms = sys.argv[1] if len(sys.argv) > 1 else "R03214-03-21"
    provider = make_enrichment_provider()
    result = provider(tms)
    print(json.dumps(result, indent=2, ensure_ascii=False))
