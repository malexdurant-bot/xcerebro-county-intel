"""
Diagnostic: intercept XHR/fetch network requests during recorder search.
Also tests the legacy portal at legacy.recorder.maricopa.gov/recdocdata/.
"""
import sys, re, json
from pathlib import Path
from datetime import datetime, timedelta, timezone

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from playwright.sync_api import sync_playwright

PORTAL_URL = "https://recorder.maricopa.gov/recording/document-search.html"
LEGACY_URL = "https://legacy.recorder.maricopa.gov/recdocdata/"
OUT_DIR = Path(__file__).parent


def test_main_portal_api(page):
    """Intercept network calls during search to find API endpoint."""
    api_calls = []

    def on_request(request):
        if request.resource_type in ("xhr", "fetch"):
            api_calls.append({
                "url": request.url,
                "method": request.method,
                "headers": dict(request.headers),
                "post_data": request.post_data,
            })

    def on_response(response):
        if response.request.resource_type in ("xhr", "fetch"):
            for call in api_calls:
                if call["url"] == response.url and "response_status" not in call:
                    call["response_status"] = response.status
                    call["response_headers"] = dict(response.headers)
                    try:
                        call["response_text"] = response.text()[:2000]
                    except Exception:
                        pass

    page.on("request", on_request)
    page.on("response", on_response)

    import time
    today = datetime.now(timezone.utc)
    date_from = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    date_to = today.strftime("%Y-%m-%d")

    print(f"\n=== MAIN PORTAL API INTERCEPT ===")
    try:
        page.goto(PORTAL_URL, wait_until="load", timeout=90_000)
    except Exception as e:
        print(f"Goto error: {e}")

    # Handle Cloudflare
    for _ in range(12):
        if "just a moment" not in page.title().lower():
            break
        time.sleep(5)
    print(f"Title after load: {page.title()!r}")

    try:
        page.wait_for_selector('button#searchResults', timeout=30_000)
    except Exception as e:
        print(f"Form not found: {e}")
        return api_calls

    # Select NS and dates
    page.select_option('select[name="documentCode"]', value='NS', force=True)
    time.sleep(1)
    page.fill('input[name="beginDate"]', date_from)
    page.fill('input[name="endDate"]', date_to)

    print(f"Submitting search... (recording XHR/fetch calls)")
    try:
        page.click('button#searchResults', timeout=10_000)
    except Exception as e:
        print(f"Click error: {e}")

    # Wait and collect API calls
    time.sleep(15)
    print(f"\nCaptured {len(api_calls)} XHR/fetch calls:")
    for i, call in enumerate(api_calls):
        print(f"\n  [{i}] {call['method']} {call['url']}")
        if call.get('post_data'):
            print(f"      POST data: {call['post_data'][:300]!r}")
        if call.get('response_status'):
            print(f"      Response: {call['response_status']}")
        if call.get('response_text'):
            print(f"      Body[:500]: {call['response_text'][:500]!r}")

    return api_calls


def test_legacy_portal(page):
    """Test legacy.recorder.maricopa.gov/recdocdata/ for NOTS."""
    import time
    print(f"\n=== LEGACY PORTAL TEST ===")
    try:
        page.goto(LEGACY_URL, wait_until="load", timeout=60_000)
    except Exception as e:
        print(f"Goto error: {e}")

    # Handle Cloudflare
    for attempt in range(12):
        title = page.title()
        print(f"  attempt {attempt}: title={title!r}")
        if "just a moment" not in title.lower():
            break
        time.sleep(5)

    html = page.content()
    print(f"Legacy portal HTML size: {len(html)} bytes")
    print(f"Legacy title: {page.title()!r}")
    print(f"Legacy URL: {page.url!r}")
    (OUT_DIR / "debug_recorder_legacy.html").write_text(html, encoding="utf-8")
    print("Wrote debug_recorder_legacy.html")

    # Show form elements
    inputs = page.evaluate("""
        () => {
            const els = document.querySelectorAll('input, select, textarea, button');
            return Array.from(els).slice(0, 30).map(el => ({
                tag: el.tagName.toLowerCase(),
                type: el.type || '',
                name: el.name || '',
                id: el.id || '',
                placeholder: el.placeholder || '',
            }));
        }
    """)
    print(f"\nLegacy form elements ({len(inputs)}):")
    for el in inputs:
        print(f"  <{el['tag']} type={el['type']!r} name={el['name']!r} id={el['id']!r} placeholder={el['placeholder'][:50]!r}>")


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=300)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        api_calls = test_main_portal_api(page)

        # Save API calls to file
        (OUT_DIR / "debug_recorder_api_calls.json").write_text(
            json.dumps(api_calls, indent=2, default=str), encoding="utf-8"
        )
        print("\nSaved API calls to debug_recorder_api_calls.json")

        # Test legacy portal in same session (reuse cookies)
        test_legacy_portal(page)

        browser.close()


if __name__ == "__main__":
    main()
