"""Diagnostic v4: justice court - force=True click + expect_navigation."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from playwright.sync_api import sync_playwright

PORTAL_URL = "https://justicecourts.maricopa.gov/app/courtrecords/casesearch"
OUT_DIR = Path(__file__).parent

def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, slow_mo=0)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60_000)
        print(f"Title before: {page.title()!r}")

        page.fill('[name="ctl00$MainContent$LastName"]', "Smith")
        print("Filled LastName = Smith")

        # Try force=True click with expect_navigation context manager
        print("Clicking with force=True + expect_navigation...")
        try:
            with page.expect_navigation(wait_until="networkidle", timeout=30_000):
                page.click('[name="ctl00$MainContent$btnSearch1"]',
                           force=True, timeout=10_000)
            print("Navigation complete!")
        except Exception as e:
            print(f"force click + expect_navigation error: {e}")
            # Fallback: JS click
            print("Fallback: JS click with expect_navigation...")
            page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60_000)
            page.fill('[name="ctl00$MainContent$LastName"]', "Smith")
            try:
                with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
                    page.evaluate("""
                        () => {
                            const btn = document.querySelector('[name="ctl00$MainContent$btnSearch1"]');
                            if (btn) btn.click();
                        }
                    """)
                print("JS click + expect_navigation complete!")
            except Exception as e2:
                print(f"JS click + expect_navigation error: {e2}")

        html = page.content()
        (OUT_DIR / "debug_justice_after_v4.html").write_text(html, encoding="utf-8")
        print(f"Title after: {page.title()!r}")
        print(f"URL after: {page.url!r}")
        print(f"HTML size: {len(html)} bytes")
        print(f"#tblForms: {page.query_selector('#tblForms') is not None}")

        # Check for case data
        import re
        for pattern in [r'[A-Z]{1,3}\d{4}-\d+', r'Case Number', r'casedetail', r'case-detail']:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                print(f"Pattern {pattern!r} found at {m.start()}: {m.group()!r}")
                print(repr(html[max(0, m.start()-200):m.start()+600]))
                break
        else:
            # Dump first 3000 chars of body
            bidx = html.lower().find('<body')
            print("No case pattern. Body snippet:")
            print(repr(html[bidx:bidx+3000]))

        browser.close()

if __name__ == "__main__":
    main()
