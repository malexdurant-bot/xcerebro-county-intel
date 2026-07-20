"""Diagnostic: inspect justice court post-submit HTML structure."""
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
        ln = page.query_selector('[name="ctl00$MainContent$LastName"]')
        btn = page.query_selector('[name="ctl00$MainContent$btnSearch1"]')
        print(f"LastName field: {ln is not None}, Search button: {btn is not None}")

        if ln:
            ln.fill("Smith")
        if btn:
            btn.click(timeout=10_000)
        else:
            # Try input[type=submit]
            page.click('input[type="submit"]', timeout=10_000)

        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            page.wait_for_load_state("domcontentloaded", timeout=15_000)

        html = page.content()
        (OUT_DIR / "debug_justice_after.html").write_text(html, encoding="utf-8")
        print(f"Title after: {page.title()!r}")
        print(f"URL after: {page.url!r}")
        print(f"HTML size: {len(html)} bytes")
        print(f"<table>: {page.query_selector('table') is not None}")
        print(f"#tblForms: {page.query_selector('#tblForms') is not None}")

        # Show HTML snippet around first result
        import re
        m = re.search(r'CV\d{4}-\d+|SC\d{4}-\d+|FED\d{4}-\d+|eviction', html, re.IGNORECASE)
        if m:
            print(f"Pattern at {m.start()}: {m.group()!r}")
            print(repr(html[max(0, m.start()-200):m.start()+600]))
        else:
            # Show portion after body
            bidx = html.lower().find('<main')
            if bidx < 0:
                bidx = html.lower().find('<body')
            print(f"No case pattern found. Main/body snippet:")
            print(repr(html[bidx:bidx+2000]))

        browser.close()

if __name__ == "__main__":
    main()
