"""Diagnostic: submit NOTS search on recorder.maricopa.gov and dump results DOM."""
import sys, re
from pathlib import Path
from datetime import datetime, timedelta, timezone

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from playwright.sync_api import sync_playwright

PORTAL_URL = "https://recorder.maricopa.gov/recording/document-search.html"
OUT_DIR = Path(__file__).parent


def main():
    today = datetime.now(timezone.utc)
    date_from = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    date_to = today.strftime("%Y-%m-%d")
    print(f"Date range: {date_from} to {date_to}")

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

        print("Navigating...")
        import time
        try:
            page.goto(PORTAL_URL, wait_until="load", timeout=90_000)
        except Exception as e:
            print(f"load timeout ({e}) — waiting anyway")

        # Handle Cloudflare 'Just a moment...' challenge
        for attempt in range(12):
            title = page.title()
            print(f"Title (attempt {attempt}): {title!r}")
            if "just a moment" not in title.lower() and title:
                break
            time.sleep(5)
        else:
            print("WARNING: Cloudflare challenge may not have resolved")

        # Wait for the search form to be present
        try:
            page.wait_for_selector('button#searchResults', timeout=30_000)
            print("Search form present")
        except Exception as e:
            print(f"Search form not found: {e}")
            # Dump current state
            html_now = page.content()
            print(f"Current title: {page.title()!r}, URL: {page.url!r}")
            (OUT_DIR / "debug_recorder_cloudflare.html").write_text(html_now, encoding="utf-8")
            browser.close()
            return

        print(f"Title: {page.title()!r}")

        # Verify "Document Code" radio is selected (it should be by default)
        radio_val = page.evaluate("""
            () => {
                const checked = document.querySelector('input[name="documentTypeSelector"]:checked');
                return checked ? checked.value : null;
            }
        """)
        print(f"documentTypeSelector checked value: {radio_val!r}")
        if radio_val != "code":
            print("Clicking code radio...")
            page.click('input[name="documentTypeSelector"][value="code"]')

        # Use select_option on the hidden select (Select2 listens to change events)
        print("Selecting document code NS (NOTICE OF TRUSTEES SALE)...")
        try:
            page.select_option('select[name="documentCode"]', value='NS', force=True)
            print("select_option succeeded")
        except Exception as e:
            print(f"select_option failed: {e}")
            # Fallback: JS direct set + jQuery trigger
            result = page.evaluate("""
                () => {
                    const sel = document.querySelector('select[name="documentCode"]');
                    if (!sel) return 'select not found';
                    Array.from(sel.options).forEach(o => o.selected = false);
                    const opt = Array.from(sel.options).find(o => o.value === 'NS');
                    if (!opt) return 'NS option not found';
                    opt.selected = true;
                    sel.dispatchEvent(new Event('change', {bubbles: true}));
                    if (window.jQuery) jQuery(sel).trigger('change');
                    return 'ok: ' + opt.text;
                }
            """)
            print(f"JS fallback result: {result!r}")

        # Check what Select2 shows now
        time.sleep(1)
        s2_text = page.evaluate("""
            () => {
                const container = document.querySelector('.document-code-wrapper .select2-selection__rendered');
                return container ? container.innerText : null;
            }
        """)
        print(f"Select2 display text: {s2_text!r}")

        # Fill date fields
        print(f"Setting beginDate={date_from}, endDate={date_to}")
        page.fill('input[name="beginDate"]', date_from)
        page.fill('input[name="endDate"]', date_to)

        # Verify values set
        begin_val = page.input_value('input[name="beginDate"]')
        end_val = page.input_value('input[name="endDate"]')
        print(f"beginDate value: {begin_val!r}, endDate value: {end_val!r}")

        # Click search button
        print("Clicking SEARCH button (button#searchResults)...")
        try:
            with page.expect_navigation(wait_until="load", timeout=60_000):
                page.click('button#searchResults', timeout=10_000)
            print("Navigation after click complete")
        except Exception as e:
            print(f"Navigation click error: {e} — continuing")

        # Handle Cloudflare challenge on results page
        print("Waiting for Cloudflare challenge to clear on results page...")
        for attempt in range(18):
            title = page.title()
            url = page.url
            print(f"  attempt {attempt}: title={title!r}, url={url!r}")
            if "just a moment" not in title.lower() and "performing security" not in page.content().lower()[:500]:
                print("  => Results page loaded")
                break
            time.sleep(5)
        else:
            print("WARNING: Results page Cloudflare challenge did not clear")

        html = page.content()
        print(f"Post-search HTML size: {len(html)} bytes")
        print(f"URL after: {page.url!r}")
        (OUT_DIR / "debug_recorder_results.html").write_text(html, encoding="utf-8")
        print("Wrote debug_recorder_results.html")

        # Look for results indicators
        for pat in [r'NS\d{4}', r'document-results', r'results-table', r'tbody', r'<tr', r'No results', r'No records', r'totalResults']:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                snippet = html[max(0, m.start()-100):m.start()+500]
                print(f"\nPattern {pat!r} found at {m.start()}:")
                print(repr(snippet[:600]))

        # Dump all form elements post-search
        inputs_after = page.evaluate("""
            () => {
                const els = document.querySelectorAll('input, select, textarea, button[type="submit"]');
                return Array.from(els).slice(0, 20).map(el => ({
                    tag: el.tagName.toLowerCase(),
                    type: el.type || '',
                    name: el.name || '',
                    id: el.id || '',
                    value: el.value || ''
                }));
            }
        """)
        print("\nForm elements after search:")
        for el in inputs_after:
            print(f"  <{el['tag']} type={el['type']!r} name={el['name']!r} id={el['id']!r} value={str(el['value'])[:50]!r}>")

        browser.close()


if __name__ == "__main__":
    main()
