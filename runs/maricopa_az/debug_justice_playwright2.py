"""Diagnostic v2: justice court button visibility and alternate click strategies."""
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

        print("1. Navigating (networkidle to let reCAPTCHA load)...")
        try:
            page.goto(PORTAL_URL, wait_until="networkidle", timeout=60_000)
        except Exception:
            page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60_000)

        print(f"   Title: {page.title()!r}")
        print(f"   URL: {page.url!r}")

        # Inspect all submit-like elements
        submit_els = page.evaluate("""
            () => {
                const els = document.querySelectorAll('input[type=submit], input[type=button], button[type=submit], button');
                return Array.from(els).map(el => ({
                    tag: el.tagName,
                    type: el.type || '',
                    name: el.name || '',
                    id: el.id || '',
                    value: el.value || el.textContent.trim().substring(0, 40),
                    visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
                    disabled: el.disabled
                }));
            }
        """)
        print(f"   Submit-like elements:")
        for el in submit_els:
            print(f"     {el}")

        # Check the specific button
        btn_info = page.evaluate("""
            () => {
                const btn = document.querySelector('[name="ctl00$MainContent$btnSearch1"]');
                if (!btn) return {found: false};
                const style = window.getComputedStyle(btn);
                return {
                    found: true,
                    visible: !!(btn.offsetWidth || btn.offsetHeight),
                    display: style.display,
                    visibility: style.visibility,
                    opacity: style.opacity,
                    type: btn.type,
                    disabled: btn.disabled,
                    parentDisplay: window.getComputedStyle(btn.parentElement).display,
                    outerHTML: btn.outerHTML
                };
            }
        """)
        print(f"   btnSearch1 info: {btn_info}")

        # Fill form
        ln_el = page.query_selector('[name="ctl00$MainContent$LastName"]')
        if ln_el:
            ln_el.fill("Smith")
            print("   Filled LastName = Smith")

        # Try JS click (bypasses visibility)
        print("2. Trying JavaScript click on btnSearch1...")
        try:
            page.evaluate("""
                () => {
                    const btn = document.querySelector('[name="ctl00$MainContent$btnSearch1"]');
                    if (btn) btn.click();
                }
            """)
            print("   JS click fired")
        except Exception as e:
            print(f"   JS click error: {e}")

        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            page.wait_for_load_state("domcontentloaded", timeout=15_000)

        html = page.content()
        (OUT_DIR / "debug_justice_after2.html").write_text(html, encoding="utf-8")
        print(f"   Title after: {page.title()!r}")
        print(f"   URL after: {page.url!r}")
        print(f"   HTML size: {len(html)} bytes")

        # Check for results
        import re
        m = re.search(r'[A-Z]{1,3}\d{4}-\d+|Case Number', html)
        if m:
            print(f"   Found at {m.start()}: {m.group()!r}")
            print(repr(html[max(0, m.start()-100):m.start()+500]))

        # Try form submit instead
        if not m or len(html) < 5000:
            print("3. Trying page.evaluate form.submit()...")
            page.goto(PORTAL_URL, wait_until="networkidle", timeout=60_000)
            ln_el = page.query_selector('[name="ctl00$MainContent$LastName"]')
            if ln_el:
                ln_el.fill("Smith")
            result = page.evaluate("""
                () => {
                    const form = document.querySelector('form');
                    if (!form) return 'no form found';
                    form.submit();
                    return 'submitted';
                }
            """)
            print(f"   form.submit result: {result}")
            try:
                page.wait_for_load_state("networkidle", timeout=30_000)
            except Exception:
                page.wait_for_load_state("domcontentloaded", timeout=15_000)
            html2 = page.content()
            (OUT_DIR / "debug_justice_after3.html").write_text(html2, encoding="utf-8")
            print(f"   Title after form.submit: {page.title()!r}")
            print(f"   HTML size: {len(html2)} bytes")

        browser.close()

if __name__ == "__main__":
    main()
