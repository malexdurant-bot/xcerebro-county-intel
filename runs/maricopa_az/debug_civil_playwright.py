"""Diagnostic: inspect civil court post-submit HTML structure."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from playwright.sync_api import sync_playwright

PORTAL_URL = "https://www.superiorcourt.maricopa.gov/docket/civilcourtcases/casesearch.asp"
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
        print(f"Fields: lastName={page.query_selector('[name=\"lastName\"]') is not None}, "
              f"FirstName={page.query_selector('[name=\"FirstName\"]') is not None}")

        page.fill('[name="lastName"]', "Smith")
        page.click('input[type="submit"]', timeout=10_000)

        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            page.wait_for_load_state("domcontentloaded", timeout=15_000)

        html = page.content()
        (OUT_DIR / "debug_civil_after.html").write_text(html, encoding="utf-8")
        print(f"Title after: {page.title()!r}")
        print(f"URL after: {page.url!r}")
        print(f"HTML size: {len(html)} bytes")
        print(f"<table>: {page.query_selector('table') is not None}")
        print(f"#tblForms: {page.query_selector('#tblForms') is not None}")

        # Check for result rows
        rows = page.query_selector_all('#tblForms .row a[href*="caseInfo"]')
        print(f"caseInfo links: {len(rows)}")
        if rows:
            link = rows[0]
            print(f"First case: {link.inner_text().strip()!r}, href={link.get_attribute('href')!r}")

        # Show HTML around first case number
        import re
        m = re.search(r'CV\d{4}-\d+|FC\d{4}-\d+', html)
        if m:
            print(f"First CV/FC case at {m.start()}: {m.group()!r}")
            print(repr(html[max(0, m.start()-300):m.start()+400]))

        browser.close()

if __name__ == "__main__":
    main()
