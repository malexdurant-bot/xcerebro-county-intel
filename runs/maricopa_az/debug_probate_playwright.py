"""
Diagnostic: inspect Playwright form fill and POST result for probate portal.
Saves page HTML at each stage so we can see exactly what the portal returns.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from playwright.sync_api import sync_playwright

PORTAL_URL = (
    "https://www.superiorcourt.maricopa.gov/docket/ProbateCourtCases/caseSearch.asp"
)
OUT_DIR = Path(__file__).parent

def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=500)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        print("1. Navigating to portal...")
        try:
            page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60_000)
        except Exception as e:
            print(f"   goto error: {e}")
            page.goto(PORTAL_URL, timeout=60_000)

        html_before = page.content()
        (OUT_DIR / "debug_probate_before.html").write_text(html_before, encoding="utf-8")
        print(f"   Page title: {page.title()!r}")
        print(f"   HTML size before submit: {len(html_before)} bytes")

        # Check which form fields are present
        for field in ("lastName", "FirstName", "caseNumber"):
            el = page.query_selector(f'[name="{field}"]')
            print(f"   Field [{field}]: {'FOUND' if el else 'NOT FOUND'}")

        submit_el = page.query_selector('input[type="submit"]')
        print(f"   Submit button: {'FOUND' if submit_el else 'NOT FOUND'}")
        if submit_el:
            print(f"     value={submit_el.get_attribute('value')!r}")

        recaptcha = "recaptcha" in html_before.lower() or "g-recaptcha" in html_before.lower()
        print(f"   reCAPTCHA on page: {recaptcha}")

        print("2. Filling form with last_name='Smith'...")
        ln_el = page.query_selector('[name="lastName"]')
        if ln_el:
            ln_el.fill("Smith")
        else:
            print("   ERROR: lastName field not found, aborting")
            browser.close()
            return

        print("3. Clicking submit...")
        page.click('input[type="submit"]', timeout=10_000)

        print("4. Waiting for page to settle...")
        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
            print("   networkidle reached")
        except Exception as e:
            print(f"   networkidle timeout: {e}, falling back to domcontentloaded")
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15_000)
            except Exception:
                pass

        html_after = page.content()
        (OUT_DIR / "debug_probate_after.html").write_text(html_after, encoding="utf-8")
        print(f"   Page title after: {page.title()!r}")
        print(f"   HTML size after submit: {len(html_after)} bytes")
        print(f"   Same as before: {len(html_after) == len(html_before)}")

        # Check for results table
        table_el = page.query_selector("table")
        print(f"   <table> found: {table_el is not None}")
        if table_el:
            rows = page.query_selector_all("table tr")
            print(f"   Table rows: {len(rows)}")

        # Check for error/no-results messages
        body_text = page.inner_text("body")[:500]
        print(f"   Body text snippet: {body_text!r}")

        recaptcha_after = "recaptcha" in html_after.lower() or "g-recaptcha" in html_after.lower()
        print(f"   reCAPTCHA still on page after submit: {recaptcha_after}")

        browser.close()

    print("Done. HTML files saved to runs/maricopa_az/debug_probate_*.html")

if __name__ == "__main__":
    main()
