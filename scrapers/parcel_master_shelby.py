"""
Shelby County, TN — ArcGIS parcel master (owner lookup + enrichment).

Queries the ReGIS CurrentParcels MapServer:
  https://scgis.shelbycountytn.gov/serverhigh/rest/services/Parcel/CurrentParcels/MapServer/0/query

Confirmed publicly accessible — no auth, no CAPTCHA.  Returns owner name,
mailing address, property address, class, and land use code.

NOTE: This layer does NOT include assessed value, year built, or sale history.
Those fields require a separate source (assessormelvinburgess.com — blocked as
of 2026-07-19, Playwright strategy deferred to Phase 2).

Parcel ID format note:
  Urban Memphis parcels:  "035087  00049"  (6 numeric + 2 spaces + 5 numeric)
  Suburban parcels:       "D0223E A00020"  (alphanumeric with 1 space)
  Both formats are stored exactly as-is in PARCELID — preserve spacing.
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.parse
import urllib.request
from typing import Optional

# ---------------------------------------------------------------------------
# ArcGIS endpoint — confirmed 2026-07-19
# ---------------------------------------------------------------------------

_BASE_URL = (
    "https://scgis.shelbycountytn.gov"
    "/serverhigh/rest/services/Parcel/CurrentParcels/MapServer/0/query"
)

_OUT_FIELDS = ",".join([
    "PARCELID", "PARID", "OWNER", "OWNER_EXT",
    "PAR_ADDR1", "PAR_ADRNO", "PAR_ADRSTR", "PAR_ADRSUF",
    "OWN_ADDR1", "OWN_ADDR2", "OWN_CITY", "OWN_STATE", "OWN_ZIP", "OWN_ZIP4",
    "CLASS", "LUC", "NBHD", "ZONING", "LANDUSE", "MUNI", "SUBDIV", "SUBLOT",
])

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://scgis.shelbycountytn.gov/",
    "Accept": "application/json",
}

# SSL context: government ArcGIS servers often use legacy TLS renegotiation.
# OP_LEGACY_SERVER_CONNECT is required on Python 3.12+ / OpenSSL 3.0+ builds.
def _make_ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # Allow legacy renegotiation (needed for many government servers on OpenSSL 3.0+)
    legacy_opt = getattr(ssl, "OP_LEGACY_SERVER_CONNECT", None)
    if legacy_opt is not None:
        ctx.options |= legacy_opt
    return ctx

_SSL_CTX = _make_ssl_ctx()

_PAGE_SIZE = 100
_RETRY_DELAY = 1.0  # seconds between retries
_MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# Low-level query
# ---------------------------------------------------------------------------

def _query(where: str, count: int = _PAGE_SIZE, offset: int = 0) -> list[dict]:
    """Execute one ArcGIS query; return list of attributes dicts."""
    params = urllib.parse.urlencode({
        "where": where,
        "outFields": _OUT_FIELDS,
        "resultRecordCount": count,
        "resultOffset": offset,
        "returnGeometry": "false",
        "f": "json",
    })
    url = f"{_BASE_URL}?{params}"
    for attempt in range(_MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, context=_SSL_CTX, timeout=30) as resp:
                data = json.loads(resp.read())
            features = data.get("features") or []
            return [f["attributes"] for f in features]
        except Exception as exc:
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_DELAY)
            else:
                raise RuntimeError(f"ArcGIS query failed after {_MAX_RETRIES} attempts: {exc}") from exc
    return []


# ---------------------------------------------------------------------------
# Public API — lookup by parcel ID
# ---------------------------------------------------------------------------

def lookup_by_parcelid(parcel_id: str) -> Optional[dict]:
    """Return attribute dict for a single PARCELID, or None if not found.

    The PARCELID must be passed exactly as it appears in the CSV (including
    internal spaces).  The SQL WHERE clause uses = with a quoted string.
    """
    # Escape single quotes in parcel ID (rare but defensive)
    safe_id = parcel_id.replace("'", "''")
    where = f"PARCELID = '{safe_id}'"
    results = _query(where, count=2)
    if not results:
        return None
    return results[0]


def lookup_by_address(address_fragment: str, max_results: int = 20) -> list[dict]:
    """Return parcels whose PAR_ADDR1 contains address_fragment (case-insensitive).

    Useful for cross-referencing a TruePeopleSearch address against county
    parcel records.  Pass just the street portion (e.g. '123 MAIN ST').
    """
    safe = address_fragment.replace("'", "''").upper()
    where = f"UPPER(PAR_ADDR1) LIKE '%{safe}%'"
    results: list[dict] = []
    offset = 0
    while len(results) < max_results:
        batch = _query(where, count=min(_PAGE_SIZE, max_results - len(results)), offset=offset)
        if not batch:
            break
        results.extend(batch)
        if len(batch) < _PAGE_SIZE:
            break
        offset += len(batch)
    return results


def lookup_by_owner(owner_fragment: str, max_results: int = 200) -> list[dict]:
    """Return attributes for all parcels whose OWNER contains owner_fragment.

    Uses LIKE '%fragment%' — case-insensitive on most ArcGIS servers.
    Paginates until all results fetched or max_results reached.
    """
    safe_frag = owner_fragment.replace("'", "''").upper()
    where = f"UPPER(OWNER) LIKE '%{safe_frag}%'"
    results: list[dict] = []
    offset = 0
    while len(results) < max_results:
        batch = _query(where, count=min(_PAGE_SIZE, max_results - len(results)), offset=offset)
        if not batch:
            break
        results.extend(batch)
        if len(batch) < _PAGE_SIZE:
            break
        offset += len(batch)
    return results


# ---------------------------------------------------------------------------
# Parcel dict normalizer (maps ArcGIS fields → framework parcel dict shape)
# ---------------------------------------------------------------------------

def normalize_parcel_attrs(attrs: dict) -> dict:
    """Convert ArcGIS attributes to the parcel dict the scoring seam expects.

    Field names must match scaffold/pipeline/scoring_seam.py _parcel_display_from:
      situs_address, situs_city, situs_state,
      owner_mailing_address, owner_mailing_city, owner_mailing_state, owner_mailing_zip,
      assessed_value, last_sale_price, last_sale_date, year_built, property_class.
    """
    def s(k: str) -> Optional[str]:
        v = attrs.get(k)
        if v is None:
            return None
        v = str(v).strip()
        return v if v else None

    owner = s("OWNER") or ""
    ext = s("OWNER_EXT") or ""
    owner_full = f"{owner} {ext}".strip() if ext else owner

    return {
        "parcel_id": s("PARCELID"),
        "owner_name": owner_full or None,
        # Situs — scoring seam reads situs_address and address; situs_city and city
        "situs_address": s("PAR_ADDR1"),
        "address": s("PAR_ADDR1"),
        "situs_city": s("MUNI"),
        "city": s("MUNI"),
        "situs_state": "TN",
        # Owner mailing — scoring seam reads owner_mailing_address (singular)
        "owner_mailing_address": s("OWN_ADDR1"),
        "owner_mailing_city": s("OWN_CITY"),
        "owner_mailing_state": s("OWN_STATE"),
        "owner_mailing_zip": s("OWN_ZIP"),
        # Property classification
        "property_class": s("CLASS"),
        "land_use_code": s("LUC"),
        "municipality": s("MUNI"),
        # Fields not available in ArcGIS CurrentParcels layer
        "assessed_value": None,
        "year_built": None,
        "last_sale_price": None,
        "last_sale_date": None,
    }
