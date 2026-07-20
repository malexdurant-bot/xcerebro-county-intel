"""
Deep-dive: publicapi.recorder.maricopa.gov /documents/search response shape.
Find: full JSON schema, pagination params, total count, detail URL pattern.
"""
import sys, json, ssl, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime, timedelta, timezone

OUT_DIR = Path(__file__).parent

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

BASE = "https://publicapi.recorder.maricopa.gov"
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://recorder.maricopa.gov/recording/document-search.html",
    "Origin": "https://recorder.maricopa.gov",
}


def get(path: str, params: dict | None = None) -> tuple[int, str]:
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")[:500]
    except Exception as e:
        return 0, str(e)


def main():
    today = datetime.now(timezone.utc)
    date_from = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    date_to = today.strftime("%Y-%m-%d")

    # Get full NS search response
    print("=== Full /documents/search response (NS, last 30 days) ===")
    status, body = get("/documents/search", {
        "documentCode": "NS",
        "beginDate": date_from,
        "endDate": date_to,
    })
    print(f"Status: {status}")
    try:
        data = json.loads(body)
    except Exception:
        print(f"Not JSON: {body[:500]!r}")
        return

    # Save full response
    (OUT_DIR / "debug_recorder_api_ns_response.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )
    print(f"Top-level keys: {list(data.keys())}")

    results = data.get("searchResults", [])
    print(f"searchResults count: {len(results)}")
    if results:
        print(f"First record keys: {list(results[0].keys())}")
        print(f"First 3 records:")
        for r in results[:3]:
            print(f"  {json.dumps(r)}")
        print(f"Last record: {json.dumps(results[-1])}")

    other_keys = {k: v for k, v in data.items() if k != "searchResults"}
    print(f"Other response fields: {json.dumps(other_keys)}")

    # Test pagination params
    print("\n=== Pagination probe ===")
    for params in [
        {"documentCode": "NS", "beginDate": date_from, "endDate": date_to, "page": "1", "pageSize": "5"},
        {"documentCode": "NS", "beginDate": date_from, "endDate": date_to, "page": "2", "pageSize": "5"},
        {"documentCode": "NS", "beginDate": date_from, "endDate": date_to, "offset": "0", "limit": "5"},
        {"documentCode": "NS", "beginDate": date_from, "endDate": date_to, "size": "5", "from": "0"},
    ]:
        s, b = get("/documents/search", params)
        try:
            d = json.loads(b)
            r = d.get("searchResults", [])
            print(f"  params={list(params.items())[-2:]}: status={s}, count={len(r)}, first_rec_num={r[0].get('recordingNumber') if r else None}")
        except Exception:
            print(f"  params={list(params.items())[-2:]}: status={s}, body={b[:100]!r}")

    # Test detail URL
    print("\n=== Detail URL probe ===")
    if results:
        first = results[0]
        rec_num = first.get("recordingNumber", "")
        rec_suffix = first.get("recordingSuffix", "")
        print(f"First record: recordingNumber={rec_num!r}, suffix={rec_suffix!r}")

        # Try various detail endpoint patterns
        detail_paths = [
            f"/documents/{rec_num}",
            f"/documents/detail/{rec_num}",
            f"/documents/{rec_num}/{rec_suffix}" if rec_suffix else None,
            f"/documents/query",
        ]
        for dp in detail_paths:
            if dp is None:
                continue
            s, b = get(dp)
            if s == 200:
                print(f"  GET {dp} -> 200: {b[:400]!r}")
            else:
                print(f"  GET {dp} -> {s}")

        # Try /documents/query with recording number
        s, b = get("/documents/query", {"recordingYear": str(rec_num)[:4], "recordingNumber": str(rec_num)[4:]})
        print(f"  GET /documents/query year+num -> {s}: {b[:400]!r}")

        s, b = get("/documents/query", {"recordingNumber": str(rec_num)})
        print(f"  GET /documents/query full_num -> {s}: {b[:400]!r}")

    # Test other doc types
    print("\n=== Other doc codes (30-day) ===")
    other_codes = [("ND", "NOTICE OF DEFAULT"), ("QD", "QUIT CLAIM DEED"), ("LP", "LIS PENDENS"), ("ML", "MATERIAL MANS MECH LN")]
    for code, name in other_codes:
        s, b = get("/documents/search", {"documentCode": code, "beginDate": date_from, "endDate": date_to})
        try:
            d = json.loads(b)
            cnt = len(d.get("searchResults", []))
            print(f"  {code} ({name}): status={s}, count={cnt}")
        except Exception:
            print(f"  {code}: status={s}, body={b[:100]!r}")


if __name__ == "__main__":
    main()
