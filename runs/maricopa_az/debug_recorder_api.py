"""
Probe publicapi.recorder.maricopa.gov for document search endpoints.
No browser needed — this is a direct HTTP API.
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
PORTAL_REFERER = "https://recorder.maricopa.gov/recording/document-search.html"

HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": PORTAL_REFERER,
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


def post(path: str, data: dict | list) -> tuple[int, str]:
    url = BASE + path
    body = json.dumps(data).encode("utf-8")
    headers = {**HEADERS, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
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
    print(f"Date range: {date_from} to {date_to}")

    # Known endpoints
    print("\n--- Known endpoints ---")
    for path in ["/documentcodes/short", "/documents/index"]:
        status, body = get(path)
        print(f"GET {path} -> {status}, body[:200]: {body[:200]!r}")

    # Guess common search/query endpoints
    print("\n--- Probing search endpoints ---")
    guesses = [
        "/documents/search",
        "/documents",
        "/search",
        "/recording/search",
        "/recordings",
        "/recordings/search",
        "/documents/query",
    ]
    for path in guesses:
        status, body = get(path)
        print(f"GET {path} -> {status}, body[:200]: {body[:200]!r}")

    # Try POST to search with NS doc code
    print("\n--- POST search attempts ---")
    search_payloads = [
        ("/documents/search", {
            "documentCode": "NS",
            "beginDate": date_from,
            "endDate": date_to,
        }),
        ("/recordings/search", {
            "documentCode": ["NS"],
            "beginDate": date_from,
            "endDate": date_to,
        }),
        ("/search", {
            "documentTypeSelector": "code",
            "documentCode": "NS",
            "beginDate": date_from,
            "endDate": date_to,
        }),
    ]
    for path, payload in search_payloads:
        status, body = post(path, payload)
        print(f"POST {path} -> {status}, body[:300]: {body[:300]!r}")

    # Try the cart/create endpoint (was POSTed in network trace)
    print("\n--- Cart endpoint ---")
    status, body = post("/cart/create", {})
    print(f"POST /cart/create -> {status}, body[:200]: {body[:200]!r}")
    cart_id = body.strip().strip('"')
    print(f"Cart ID: {cart_id!r}")

    # Try GET with NS and dates using various param names
    print("\n--- GET with query params ---")
    param_sets = [
        {"documentCode": "NS", "beginDate": date_from, "endDate": date_to},
        {"docCode": "NS", "startDate": date_from, "endDate": date_to},
        {"code": "NS", "begin": date_from, "end": date_to},
        {"documentCode": "NS", "beginDate": date_from, "endDate": date_to, "pageSize": "10"},
        {"documentCode": "NS", "beginDate": date_from, "endDate": date_to, "limit": "10"},
    ]
    for params in param_sets:
        for path in ["/documents", "/documents/search", "/recordings"]:
            status, body = get(path, params)
            if status not in (400, 404, 405) or "NS" in body:
                print(f"GET {path} {params} -> {status}, body[:400]: {body[:400]!r}")
                if status == 200:
                    break


if __name__ == "__main__":
    main()
