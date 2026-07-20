"""
Probe actual PUC field values in the Maricopa assessor parcel layer.
Fetches 100 records with WHERE 1=1 and prints a frequency table of PUC values.
No parcel data stored — counts only.
"""
from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from collections import Counter

SERVICE_URL = (
    "https://gis.mcassessor.maricopa.gov"
    "/arcgis/rest/services/Parcels/MapServer/0/query"
)
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
_HEADERS = {
    "User-Agent": "xcerebro-maricopa-probe/0.1",
    "Accept": "application/json",
    "Referer": "https://maps.mcassessor.maricopa.gov",
}


def _get(params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{SERVICE_URL}?{qs}", headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=30, context=_CTX) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    print("Fetching 500 records to sample PUC values...")
    resp = _get({
        "where": "1=1",
        "outFields": "PUC,OBJECTID",
        "returnGeometry": "false",
        "resultRecordCount": 500,
        "resultOffset": 0,
        "f": "json",
    })
    features = resp.get("features", [])
    print(f"Records returned: {len(features)}")
    print()

    counter: Counter = Counter()
    for f in features:
        puc = f.get("attributes", {}).get("PUC") or "(null)"
        counter[puc] += 1

    print("PUC value frequency (top 30):")
    for val, cnt in counter.most_common(30):
        print(f"  {val!r:20s}  {cnt}")

    print()
    print("Distinct PUC values in sample:", len(counter))
    print()

    # Also test a few plausible residential filters
    candidates = [
        ("PUC LIKE 'R%'",         "starts with R"),
        ("PUC LIKE '%R%'",        "contains R"),
        ("PUC = 'R1'",            "equals R1"),
        ("PUC LIKE '0%'",         "starts with 0 (numeric code)"),
        ("PUC IN ('0100','0101','0102','0103','0200','0201','0202','0203')", "common res numeric codes"),
    ]
    print("Testing candidate residential WHERE clauses:")
    for clause, label in candidates:
        try:
            r = _get({
                "where": clause,
                "returnCountOnly": "true",
                "f": "json",
            })
            count = r.get("count", r.get("error", "error"))
        except Exception as exc:
            count = f"ERROR: {exc}"
        print(f"  {label:45s}  count={count}")


if __name__ == "__main__":
    main()
