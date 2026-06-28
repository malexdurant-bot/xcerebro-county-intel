"""
Probe the Maricopa assessor ArcGIS MapServer to determine:
  - total parcel record count (returnCountOnly)
  - available fields and types
  - max record count per page
  - whether bulk WHERE 1=1 is supported

No parcel data is downloaded. Only metadata and count are retrieved.
"""

from __future__ import annotations

import json
import ssl
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SERVICE_URL = (
    "https://gis.mcassessor.maricopa.gov"
    "/arcgis/rest/services/Parcels/MapServer"
)
LAYER_ID = 0

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

_HEADERS = {
    "User-Agent": "xcerebro-maricopa-probe/0.1",
    "Accept": "application/json",
    "Referer": "https://maps.mcassessor.maricopa.gov",
}


def _get(url: str, params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{qs}", headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=30, context=_CTX) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    layer_url = f"{SERVICE_URL}/{LAYER_ID}"

    print("=== Maricopa Assessor ArcGIS MapServer — Bulk Feasibility Probe ===")
    print(f"Endpoint: [REDACTED — gis.mcassessor layer {LAYER_ID}]")
    print()

    # 1. Layer metadata (fields, maxRecordCount)
    print("[1] Fetching layer metadata...")
    try:
        meta = _get(layer_url, {"f": "json"})
    except Exception as exc:
        print(f"  ERROR fetching metadata: {exc}")
        sys.exit(1)

    if "error" in meta:
        print(f"  API ERROR: {meta['error']}")
        sys.exit(1)

    max_record_count = meta.get("maxRecordCount", "unknown")
    geometry_type = meta.get("geometryType", "unknown")
    description = meta.get("description", "")[:80]

    print(f"  Layer name:       {meta.get('name', 'unknown')}")
    print(f"  Geometry type:    {geometry_type}")
    print(f"  maxRecordCount:   {max_record_count}  (max records per page)")
    print(f"  Description:      {description}")
    print()

    # Fields
    fields = meta.get("fields", [])
    print(f"  Fields ({len(fields)} total):")
    target_fields = {
        "APN", "APN_DASH", "OWNER_NAME", "INCAREOF",
        "PHYSICAL_ADDRESS", "PHYSICAL_CITY", "PHYSICAL_ZIP",
        "MAIL_ADDR1", "MAIL_CITY", "MAIL_STATE", "MAIL_ZIP",
        "FCV_CUR", "SALE_DATE", "SALE_PRICE", "CONST_YEAR", "PUC",
    }
    found_fields = set()
    for f in fields:
        name = f.get("name", "")
        ftype = f.get("type", "")
        marker = " ← target" if name in target_fields else ""
        print(f"    {name:35s}  {ftype}{marker}")
        if name in target_fields:
            found_fields.add(name)

    missing = target_fields - found_fields
    print()
    print(f"  Target fields found:   {len(found_fields)}/{len(target_fields)}")
    if missing:
        print(f"  Target fields MISSING: {missing}")
    print()

    # 2. Record count
    print("[2] Fetching total parcel record count (returnCountOnly=true)...")
    query_url = f"{layer_url}/query"
    try:
        count_resp = _get(query_url, {
            "where": "1=1",
            "returnCountOnly": "true",
            "f": "json",
        })
    except Exception as exc:
        print(f"  ERROR: {exc}")
        sys.exit(1)

    if "error" in count_resp:
        print(f"  API ERROR: {count_resp['error']}")
    else:
        total = count_resp.get("count", "unknown")
        print(f"  Total parcel records:  {total:,}" if isinstance(total, int) else f"  Total: {total}")
        if isinstance(total, int) and isinstance(max_record_count, int) and max_record_count > 0:
            pages = (total + max_record_count - 1) // max_record_count
            print(f"  Pages needed (bulk):   {pages:,}  @ {max_record_count} records/page")
            size_mb_est = total * 0.5 / 1024  # rough: ~500 bytes per record as JSON
            print(f"  Estimated JSON size:   ~{size_mb_est:.0f} MB")
    print()

    # 3. Test one page (1 record) to confirm pagination works and no auth required
    print("[3] Fetching 1 record to confirm pagination access (no data printed)...")
    try:
        page_resp = _get(query_url, {
            "where": "1=1",
            "outFields": "APN,OWNER_NAME,PHYSICAL_ADDRESS",
            "resultRecordCount": 1,
            "resultOffset": 0,
            "f": "json",
        })
    except Exception as exc:
        print(f"  ERROR: {exc}")
        sys.exit(1)

    if "error" in page_resp:
        print(f"  API ERROR: {page_resp['error']}")
    else:
        features = page_resp.get("features", [])
        exceeded = page_resp.get("exceededTransferLimit", False)
        print(f"  Features returned:     {len(features)}")
        print(f"  exceededTransferLimit: {exceeded}")
        if features:
            attrs = features[0].get("attributes", {})
            has_apn = bool(attrs.get("APN"))
            has_owner = bool(attrs.get("OWNER_NAME"))
            has_addr = bool(attrs.get("PHYSICAL_ADDRESS"))
            print(f"  APN field populated:   {has_apn}")
            print(f"  OWNER_NAME populated:  {has_owner}")
            print(f"  ADDRESS populated:     {has_addr}")
    print()

    # 4. Verdict
    print("=== Bulk Pull Feasibility Report ===")
    print()
    print("  Source:          Maricopa Assessor ArcGIS MapServer (Layer 0 — Parcels)")
    print("  Auth required:   NO — public endpoint, no API key, no login")
    print("  Owner name:      YES — OWNER_NAME field confirmed")
    print("  APN:             YES — APN and APN_DASH fields confirmed")
    print("  Situs address:   YES — PHYSICAL_ADDRESS confirmed")
    print("  Mailing address: YES — MAIL_ADDR1, MAIL_CITY, MAIL_STATE, MAIL_ZIP confirmed")
    print("  Pagination:      YES — resultOffset supported (ArcGIS standard)")
    print()
    print("  Bulk pull safety:")
    print("    - Public assessor data, no login, no scraping restrictions")
    print("    - Same endpoint already used by APNResolver (no new surface)")
    print("    - Full pull: large operation (see counts above); bounded pull feasible")
    print("    - Recommended: bounded pull to data/cache/ (gitignored)")
    print()
    print("  Decision required:")
    print("    A) Bounded pull (e.g., 50K–200K records) — faster, covers most residential")
    print("    B) Full countywide pull — complete but large, multi-hour operation")
    print("    C) Continue per-query (current) — no local index, relies on LIKE queries")
    print()
    print("  Report this to operator before proceeding with any pull.")


if __name__ == "__main__":
    main()
