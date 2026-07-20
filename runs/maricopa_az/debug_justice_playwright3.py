"""Diagnostic v3: justice court - click visible button#btn-search1, inspect results."""
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
        print(f"Title: {page.title()!r}")

        # Fill last name
        page.fill('[name="ctl00$MainContent$LastName"]', "Smith")
        print("Filled LastName = Smith")

        # Click the visible button
        visible_btn = page.query_selector('button#btn-search1')
        print(f"button#btn-search1 visible: {visible_btn is not None}")
        if visible_btn:
            visible_btn.click(timeout=10_000)
            print("Clicked button#btn-search1")
        else:
            # Fallback: JS click on hidden button
            print("Fallback: JS click on hidden button")
            page.evaluate("""
                () => { document.querySelector('[name="ctl00$MainContent$btnSearch1"]').click(); }
            """)

        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception as e:
            print(f"networkidle timeout: {e}")
            page.wait_for_load_state("domcontentloaded", timeout=15_000)

        html = page.content()
        (OUT_DIR / "debug_justice_after_v3.html").write_text(html, encoding="utf-8")
        print(f"Title after: {page.title()!r}")
        print(f"URL after: {page.url!r}")
        print(f"HTML size: {len(html)} bytes")
        print(f"<table>: {page.query_selector('table') is not None}")
        print(f"#tblForms: {page.query_selector('#tblForms') is not None}")

        # Check for result rows
        case_links = page.query_selector_all('#tblForms .row a[href*="caseInfo"]')
        print(f"caseInfo links in #tblForms: {len(case_links)}")

        # Try generic table
        tables = page.query_selector_all('table')
        print(f"Tables found: {len(tables)}")

        # Look for any case number patterns
        import re
        m = re.search(r'[A-Z]{1,3}\d{4}-\d+|eviction|case\s*number', html[:10000], re.IGNORECASE)
        if m:
            print(f"Pattern at {m.start()}: {m.group()!r}")

        # Show a key slice of HTML
        main_idx = html.lower().find('main-content')
        if main_idx < 0:
            main_idx = html.lower().find('<main')
        if main_idx < 0:
            main_idx = html.lower().find('casesearch')
        if main_idx >= 0:
            print(f"Main content snippet at {main_idx}:")
            print(repr(html[main_idx:main_idx+3000]))
        else:
            print("Body snippet:")
            print(repr(html[1000:4000]))

        browser.close()

if __name__ == "__main__":
    main()
