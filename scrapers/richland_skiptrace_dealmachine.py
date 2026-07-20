"""
Richland County, SC — DealMachine skip-trace integration for estate leads.

Flow:
  1. skip_trace_by_name(first, last) -> mailing address
  2. address_to_tms(address)         -> TMS via Spatialest address search
  3. Spatialest record card          -> full parcel (existing enrichment provider)

Configuration:
  Set env var DEALMACHINE_API_KEY before running, or pass api_key= to
  make_estate_enricher().

Usage (in run_pipeline.py pre-pipeline step):
    from scrapers.richland_skiptrace_dealmachine import enrich_estate_raw_events
    raw_events = enrich_estate_raw_events(raw_events)
    # estate events now have parcel_id filled in where found;
    # Spatialest enrichment_provider handles the rest
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)

# ── DealMachine config ────────────────────────────────────────────────────────
# Set DEALMACHINE_API_KEY in the environment before running.
# Operator: replace the empty string with the client's key, or set the env var.
DEALMACHINE_API_KEY: str = os.environ.get("DEALMACHINE_API_KEY", "")

_DM_BASE   = "https://api.dealmachine.com"
_DM_UA     = "Mozilla/5.0 xcerebro-county-intel/1.0"
_DM_DELAY  = 1.0  # seconds between DealMachine calls

# ── Spatialest config (for address → TMS lookup) ─────────────────────────────
_SP_ROOT   = "https://property.spatialest.com/sc/richland/"
_SP_UA     = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_SP_DELAY  = 1.5


# ── DealMachine skip-trace ────────────────────────────────────────────────────

def _dm_headers(api_key: str) -> dict:
    return {
        "User-Agent":    _DM_UA,
        "Accept":        "application/json",
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {api_key}",
    }


def skip_trace_by_name(
    first: str,
    last: str,
    *,
    api_key: str = "",
    state: str = "SC",
    city: str = "",
    sess: Optional[requests.Session] = None,
) -> Optional[dict]:
    """
    Call DealMachine skip-trace API with a person's name.
    Returns the first result dict, or None on no-match / error.

    TODO: Confirm exact endpoint and payload shape from DealMachine API docs
    once the client's API key is available.  The stub below follows the
    common DealMachine v2 people-search pattern; adjust if the actual
    endpoint differs.
    """
    key = api_key or DEALMACHINE_API_KEY
    if not key:
        log.warning("DealMachine: DEALMACHINE_API_KEY not set — skip trace disabled")
        return None

    s = sess or requests.Session()
    payload = {
        "first_name": first.strip().title(),
        "last_name":  last.strip().title(),
        "state":      state,
    }
    if city:
        payload["city"] = city.strip().title()

    # TODO: verify endpoint path with client's API key
    url = f"{_DM_BASE}/v2/people/search"
    try:
        r = s.post(url, json=payload, headers=_dm_headers(key), timeout=20)
    except requests.RequestException as exc:
        log.warning("DealMachine: network error: %s", exc)
        return None

    if r.status_code == 401:
        log.error("DealMachine: 401 Unauthorized — check DEALMACHINE_API_KEY")
        return None
    if r.status_code == 404:
        # endpoint path wrong — log full response for debugging
        log.warning("DealMachine: 404 on %s — check endpoint path. Body: %s", url, r.text[:200])
        return None
    if r.status_code != 200:
        log.warning("DealMachine: HTTP %s — %s", r.status_code, r.text[:200])
        return None

    try:
        data = r.json()
    except Exception:
        return None

    # Common DealMachine response shapes:
    #   {"results": [...]}  or  {"data": [...]}  or  [...]
    results = (
        data.get("results")
        or data.get("data")
        or (data if isinstance(data, list) else [])
    )
    if not results:
        return None

    # Return the first result (most confident match)
    return results[0] if results else None


def _parse_dm_address(result: dict) -> Optional[str]:
    """
    Extract a usable street address string from a DealMachine skip-trace result.
    Adjust field names once the real API response shape is known.
    """
    # Try common field names DealMachine uses
    for addr_key in ["address", "mailing_address", "property_address",
                     "street_address", "current_address"]:
        val = result.get(addr_key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            parts = " ".join(filter(None, [
                val.get("street") or val.get("address1") or val.get("line1", ""),
                val.get("city", ""),
                val.get("state", ""),
                val.get("zip") or val.get("postal_code", ""),
            ]))
            if parts.strip():
                return parts.strip()

    # Fallback: look for any key containing 'address'
    for k, v in result.items():
        if "address" in k.lower() and isinstance(v, str) and v.strip():
            return v.strip()
    return None


# ── Spatialest address → TMS ──────────────────────────────────────────────────

class _SpatialestAddressSearcher:
    """Searches Spatialest by address string to resolve a TMS."""

    def __init__(self) -> None:
        self._sess: Optional[requests.Session] = None
        self._csrf: str = ""
        self._last_call: float = 0.0

    def _ensure_session(self) -> None:
        if self._sess and self._csrf:
            return
        s = requests.Session()
        r = s.get(_SP_ROOT, headers={"User-Agent": _SP_UA}, timeout=20)
        r.raise_for_status()
        m = re.search(r'<meta name="csrf-token" content="([^"]+)"', r.text)
        if not m:
            raise RuntimeError("Spatialest: csrf-token not found")
        self._sess  = s
        self._csrf  = m.group(1)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < _SP_DELAY:
            time.sleep(_SP_DELAY - elapsed)
        self._last_call = time.monotonic()

    def find_tms(self, address: str) -> Optional[str]:
        """Search Spatialest by address; return TMS if exactly one result."""
        self._ensure_session()
        self._throttle()
        hdrs = {
            "User-Agent":       _SP_UA,
            "Accept":           "application/json",
            "Content-Type":     "application/json",
            "Referer":          _SP_ROOT,
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-TOKEN":     self._csrf,
        }
        try:
            r = self._sess.post(
                _SP_ROOT + "api/v2/search",
                json={"filters": {"term": address}, "page": 1, "limit": 5},
                headers=hdrs,
                timeout=20,
            )
        except requests.RequestException as exc:
            log.warning("Spatialest address search error: %s", exc)
            return None

        if r.status_code == 419:
            self._sess, self._csrf = None, ""
            return None
        if r.status_code != 200:
            return None

        data = r.json()
        # Direct match
        if data.get("direct_id"):
            return str(data["direct_id"])
        # Single result in results list
        results = data.get("results", [])
        if len(results) == 1:
            return results[0].get("ParcelIdentifier") or results[0].get("order_property_TMSNUM")
        return None


# ── Main enrichment step ──────────────────────────────────────────────────────

def enrich_estate_raw_events(
    raw_events: list[dict],
    *,
    api_key: str = "",
) -> list[dict]:
    """
    Pre-pipeline step: for estate (letters_testamentary) raw events that have
    no parcel_id, attempt skip-trace via DealMachine → TMS via Spatialest.

    Mutates events in-place (adds parcel_id to property_refs if found).
    Returns the same list so callers can chain: raw_events = enrich_estate_raw_events(raw_events)
    """
    key = api_key or DEALMACHINE_API_KEY
    if not key:
        log.info("DealMachine: API key not configured — skipping estate enrichment")
        return raw_events

    estate_events = [
        e for e in raw_events
        if e.get("canonical_doc_type") == "letters_testamentary"
        and not (e.get("property_refs") or {}).get("parcel_id")
    ]

    if not estate_events:
        return raw_events

    log.info("DealMachine: enriching %d estate events", len(estate_events))
    dm_sess   = requests.Session()
    sp_search = _SpatialestAddressSearcher()

    found = 0
    for event in estate_events:
        # Extract decedent name (GR party = grantor = deceased)
        grantor = next(
            (p["name"] for p in event.get("parties", []) if p.get("name_type") == "GR"),
            None,
        )
        if not grantor:
            continue

        # Parse name into first/last (format from Columbia Star: "FIRST MIDDLE LAST" or "LAST FIRST")
        name_parts = re.sub(r"\s+", " ", grantor.replace("\n", " ")).strip().split()
        if len(name_parts) < 2:
            continue
        last  = name_parts[-1]
        first = " ".join(name_parts[:-1])

        # Step 1: DealMachine skip trace
        time.sleep(_DM_DELAY)
        dm_result = skip_trace_by_name(first, last, api_key=key, sess=dm_sess)
        if not dm_result:
            continue

        address = _parse_dm_address(dm_result)
        if not address:
            continue

        log.debug("DealMachine: %s %s -> %s", first, last, address)

        # Step 2: Spatialest address → TMS
        tms = sp_search.find_tms(address)
        if not tms:
            continue

        # Step 3: Update the raw event
        if event.get("property_refs") is None:
            event["property_refs"] = {}
        event["property_refs"]["parcel_id"]     = tms
        event["property_refs"]["situs_address"] = address
        event["property_refs"]["_enriched_via"] = "dealmachine_skiptrace"
        found += 1
        log.info("DealMachine: enriched estate event -> TMS=%s addr=%s", tms, address)

    log.info("DealMachine: enriched %d/%d estate events", found, len(estate_events))
    return raw_events
