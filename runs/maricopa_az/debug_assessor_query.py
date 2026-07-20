"""
Debug: Maricopa Assessor ArcGIS MapServer query — APN format investigation.
Run: python runs/maricopa_az/debug_assessor_query.py
"""
import json
import ssl
import urllib.parse
import urllib.request

SERVICE_URL = "https://gis.mcassessor.maricopa.gov/arcgis/rest/services/Parcels/MapServer/0/query"
USER_AGENT = "xcerebro-maricopa-assessor/0.1 (+private repo)"

ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE


def query(where_clause, fields="APN,APN_DASH,BOOK,MAP,ITEM,OWNER_NAME,PHYSICAL_ADDRESS", n=5):
    params = urllib.parse.urlencode({
        "where": where_clause,
        "outFields": fields,
        "resultRecordCount": n,
        "f": "json",
    })
    req = urllib.request.Request(
        SERVICE_URL + "?" + params,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Referer": "https://maps.mcassessor.maricopa.gov",
        },
    )
    with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


print("=== First 5 records — showing APN and APN_DASH format ===")
result = query("1=1")
if "error" in result:
    print("ERROR:", result["error"])
else:
    for f in result.get("features", []):
        a = f["attributes"]
        print(f"  APN={a.get('APN')!r:20}  APN_DASH={a.get('APN_DASH')!r:20}  BOOK={a.get('BOOK')!r}  MAP={a.get('MAP')!r}  ITEM={a.get('ITEM')!r}")

print()
print("=== Query by BOOK/MAP/ITEM (Book=301, Map=55, Item=001) ===")
result2 = query("BOOK = '301' AND MAP = '55' AND ITEM = '001'")
if "error" in result2:
    print("ERROR:", result2["error"])
else:
    feats = result2.get("features", [])
    print(f"Features: {len(feats)}")
    for f in feats:
        a = f["attributes"]
        print(f"  APN={a.get('APN')!r}  OWNER={a.get('OWNER_NAME')!r}  ADDR={a.get('PHYSICAL_ADDRESS')!r}")

print()
print("=== Query by BOOK='301' (first 3 results) ===")
result3 = query("BOOK = '301'")
if "error" in result3:
    print("ERROR:", result3["error"])
else:
    feats = result3.get("features", [])
    print(f"Features: {len(feats)}")
    for f in feats:
        a = f["attributes"]
        print(f"  APN={a.get('APN')!r:20}  APN_DASH={a.get('APN_DASH')!r:20}")
