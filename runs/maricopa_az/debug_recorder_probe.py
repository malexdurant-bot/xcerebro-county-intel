"""Diagnostic: dump recorder.maricopa.gov rendered DOM for selector discovery."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from playwright.sync_api import sync_playwright

PORTAL_URL = "https://recorder.maricopa.gov/recording/document-search.html"
OUT_DIR = Path(__file__).parent


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=500)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        print("Navigating to portal...")
        try:
            page.goto(PORTAL_URL, wait_until="networkidle", timeout=60_000)
        except Exception as e:
            print(f"networkidle timeout: {e} — falling back to domcontentloaded")
            page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60_000)

        print(f"Title: {page.title()!r}")
        print(f"URL: {page.url!r}")

        # Extra wait for SPA to render
        import time
        time.sleep(3)

        html = page.content()
        print(f"HTML size: {len(html)} bytes")
        (OUT_DIR / "debug_recorder_full.html").write_text(html, encoding="utf-8")
        print("Wrote debug_recorder_full.html")

        # Print all input, select, textarea elements with their attributes
        inputs = page.evaluate("""
            () => {
                const els = document.querySelectorAll('input, select, textarea, button');
                return Array.from(els).map(el => ({
                    tag: el.tagName.toLowerCase(),
                    type: el.type || '',
                    name: el.name || '',
                    id: el.id || '',
                    placeholder: el.placeholder || '',
                    class: el.className || '',
                    'aria-label': el.getAttribute('aria-label') || '',
                    value: el.value || '',
                    outerHTML: el.outerHTML.slice(0, 300)
                }));
            }
        """)
        print(f"\nFound {len(inputs)} form elements:")
        for el in inputs:
            print(f"  <{el['tag']} type={el['type']!r} name={el['name']!r} id={el['id']!r} "
                  f"placeholder={el['placeholder']!r} class={el['class'][:60]!r}>")

        # Look for anything that might be a doc type / search field
        import re
        patterns = [
            r'docType', r'documentType', r'doc_type', r'DocType',
            r'recordingDate', r'dateFrom', r'dateTo', r'startDate', r'endDate',
            r'SearchButton', r'searchBtn', r'btnSearch',
            r'grantor', r'grantee', r'search-form',
        ]
        print("\nSearching HTML for known field patterns:")
        for pat in patterns:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                snippet = html[max(0, m.start()-100):m.start()+400]
                print(f"\n  Pattern {pat!r} found at {m.start()}:")
                print(f"  {snippet!r}")

        browser.close()


if __name__ == "__main__":
    main()
