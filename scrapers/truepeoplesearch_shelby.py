"""
TruePeopleSearch — Playwright-based people search for Shelby County probate enrichment.

Searches by person name + city/state and returns current addresses, phones,
and relatives from the person detail page.

Used in probate lead enrichment:
  1. Search decedent name → get listed addresses → cross-ref with parcel layer
  2. Search executor name → get phone numbers

TruePeopleSearch uses Cloudflare bot protection; plain requests return 403.
Playwright with stealth flags passes the JS challenge.

Usage (CLI probe):
    python scrapers/truepeoplesearch_shelby.py --probe "William Jorgensen"
    python scrapers/truepeoplesearch_shelby.py --probe "William Jorgensen" --save-html probe_tps.html
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Optional

try:
    from bs4 import BeautifulSoup
    _BS4 = True
except ImportError:
    _BS4 = False

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    _PW = True
except ImportError:
    _PW = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEARCH_URL = "https://www.truepeoplesearch.com/results"
DETAIL_BASE = "https://www.truepeoplesearch.com"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
window.chrome = {runtime: {}};
"""

_PHONE_RE = re.compile(r"\(\d{3}\)\s*\d{3}-\d{4}")
_STREET_RE = re.compile(
    r"\d{1,5}\s+[A-Z][A-Z ]{2,40}"
    r"(?:ST|AVE|RD|DR|LN|CT|BLVD|WAY|PL|TER|CIR|HWY|PKWY|PKY|CK|PIKE)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_search_name(name: str) -> str:
    """Convert 'LASTNAME, FIRSTNAME MI' → 'FIRSTNAME LASTNAME' for TPS search."""
    name = name.strip()
    if "," in name:
        parts = name.split(",", 1)
        last = parts[0].strip()
        first_tokens = parts[1].strip().split()
        first = first_tokens[0] if first_tokens else ""
        return f"{first} {last}".strip()
    return name


def _name_tokens(name: str) -> set[str]:
    """Return meaningful uppercase tokens (len > 1, alpha only)."""
    return {
        t for t in re.sub(r"[^A-Z ]", "", name.upper()).split()
        if len(t) > 1
    }


def _name_overlap(a: str, b: str) -> int:
    return len(_name_tokens(a) & _name_tokens(b))


def _extract_street(address: str) -> str:
    """Extract street-address fragment from a full address string for parcel lookup."""
    # "123 Main St, Memphis, TN 38104" → "123 MAIN ST"
    m = _STREET_RE.search(address)
    if m:
        return m.group(0).strip().upper()
    # Fallback: everything before first comma
    first = address.split(",")[0].strip().upper()
    return first if len(first) > 4 else ""


# ---------------------------------------------------------------------------
# Playwright browser helpers
# ---------------------------------------------------------------------------


def _launch_browser(p):
    return p.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
    )


def _new_context(browser):
    return browser.new_context(
        user_agent=_UA,
        viewport={"width": 1366, "height": 768},
        locale="en-US",
        timezone_id="America/Chicago",
    )


# ---------------------------------------------------------------------------
# HTML parsers
# ---------------------------------------------------------------------------


def _is_blocked(html: str) -> bool:
    """Return True if the page is a CAPTCHA, block, or rate-limit page."""
    lower = html[:3000].lower()
    return (
        "captcha" in lower
        or "access denied" in lower
        or "robot" in lower
        or "cf-browser-verification" in lower
        or "rate limited" in lower
        or "datadome" in lower
        or len(html) < 2000  # real results page is always much larger
    )


def _parse_search_results(html: str, target_name: str) -> list[dict]:
    """
    Parse TruePeopleSearch results page.
    Returns list of candidate dicts sorted by name match score (best first):
      {"name": str, "detail_url": str, "match_score": int}
    """
    if not _BS4:
        return []
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[dict] = []

    # TPS shows each person in a card; try several selector strategies
    cards = (
        soup.select("div[data-item-id]")
        or soup.select("div.card-body")
        or soup.select("div.card")
        or []
    )

    for card in cards:
        # Name: try common selector patterns
        name_el = (
            card.select_one("span.h4")
            or card.select_one("[class*='card-title'] span")
            or card.select_one("h4")
            or card.select_one("h3")
            or card.select_one("h2")
        )
        name = name_el.get_text(strip=True) if name_el else ""
        if not name or len(name) < 3:
            continue

        link_el = card.select_one("a[href*='/find/person/']")
        href = link_el.get("href", "") if link_el else ""
        if not href:
            continue
        detail_url = DETAIL_BASE + href if href.startswith("/") else href

        score = _name_overlap(name, target_name)
        candidates.append({"name": name, "detail_url": detail_url, "match_score": score})

    # Fallback: scan all links to /find/person/ and try to extract nearby text as name
    if not candidates:
        for link in soup.select("a[href*='/find/person/']"):
            href = link.get("href", "")
            if not href:
                continue
            detail_url = DETAIL_BASE + href if href.startswith("/") else href
            # Try to get name from the link text or a nearby heading
            name = link.get_text(strip=True)
            if not name or len(name) < 3:
                parent = link.find_parent()
                if parent:
                    name = parent.get_text(" ", strip=True)[:60]
            score = _name_overlap(name, target_name)
            candidates.append({"name": name, "detail_url": detail_url, "match_score": score})

    candidates.sort(key=lambda x: -x["match_score"])
    return candidates


def _parse_detail_page(html: str) -> dict:
    """
    Parse a TruePeopleSearch person detail page.
    Returns:
      {
        "addresses": [str, ...],   # full address strings (current first)
        "phones":    [str, ...],   # e.g. "(901) 555-1234"
        "relatives": [str, ...],   # relative names
      }
    """
    if not _BS4:
        return {"addresses": [], "phones": [], "relatives": []}

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    # --- Phones ---
    phones = list(dict.fromkeys(_PHONE_RE.findall(text)))[:5]

    # --- Addresses ---
    addresses: list[str] = []

    # Strategy 1: structured selectors (TPS uses data attributes or class names)
    for sel in [
        "[data-field='address']",
        ".address-item",
        ".address",
        ".location-item",
        "[class*='address']",
    ]:
        for el in soup.select(sel):
            addr = el.get_text(", ", strip=True).strip()
            if addr and len(addr) > 10 and addr not in addresses:
                addresses.append(addr)
        if addresses:
            break

    # Strategy 2: look for headings like "Current Address" or "Past Addresses"
    if not addresses:
        for heading in soup.find_all(["h3", "h4", "h2", "div", "span"]):
            txt = heading.get_text(strip=True).lower()
            if "address" in txt or "location" in txt:
                # Grab text from siblings / parent
                parent = heading.find_parent()
                if parent:
                    block_text = parent.get_text(" ", strip=True)
                    for m in _STREET_RE.finditer(block_text):
                        candidate = m.group(0).strip()
                        if candidate and candidate not in addresses:
                            addresses.append(candidate)
                if addresses:
                    break

    # Strategy 3: regex scan of full page text for street+TN patterns
    if not addresses:
        addr_re = re.compile(
            r"\d{1,5}\s+[A-Z][A-Za-z ]{2,30}"
            r"(?:St|Ave|Rd|Dr|Ln|Ct|Blvd|Way|Pl|Ter|Cir|Hwy|Pkwy)\b"
            r"[^,\n]*(?:,\s*[A-Za-z ]{2,20},\s*TN\s+\d{5})?",
            re.IGNORECASE,
        )
        for m in addr_re.finditer(text):
            candidate = m.group(0).strip()
            if candidate not in addresses:
                addresses.append(candidate)

    # --- Relatives ---
    relatives: list[str] = []
    rel_header = soup.find(
        string=re.compile(r"possible relatives|relatives|associates", re.I)
    )
    if rel_header:
        container = rel_header.find_parent()
        if container:
            # Grab text items from following elements
            for sib in container.find_next_siblings()[:2]:
                for el in sib.find_all(["a", "li", "span", "p"]):
                    name = el.get_text(strip=True)
                    if name and 3 < len(name) < 60 and name not in relatives:
                        relatives.append(name)
            if not relatives:
                # Try within the same container
                for el in container.find_all(["a", "li"]):
                    name = el.get_text(strip=True)
                    if name and 3 < len(name) < 60:
                        relatives.append(name)

    return {
        "addresses": addresses[:6],
        "phones": phones[:3],
        "relatives": relatives[:10],
    }


# ---------------------------------------------------------------------------
# Core search function
# ---------------------------------------------------------------------------


def _search_person(
    page,
    name: str,
    city_state: str,
    verbose: bool,
    save_html: Optional[Path] = None,
) -> dict | None:
    """
    Search TPS for one person.  Returns detail dict or None.
    `page` is an open Playwright page object (browser already launched).
    """
    search_name = _to_search_name(name)
    from urllib.parse import urlencode
    url = SEARCH_URL + "?" + urlencode({"name": search_name, "citystatezip": city_state})

    if verbose:
        print(f"    [TPS] Searching: {search_name!r} in {city_state!r}", flush=True)

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        # Give JS a moment to render
        page.wait_for_timeout(2500)
    except PlaywrightTimeout:
        if verbose:
            print(f"    [TPS] Timeout loading search page for {name!r}", flush=True)
        return None

    html = page.content()

    if save_html:
        save_html.write_text(html, encoding="utf-8")
        if verbose:
            print(f"    [TPS] Saved search HTML → {save_html}", flush=True)

    if _is_blocked(html):
        if verbose:
            print(f"    [TPS] Blocked (CAPTCHA) for {name!r}", flush=True)
        return None

    candidates = _parse_search_results(html, name)
    if verbose:
        print(f"    [TPS] {len(candidates)} candidates found", flush=True)

    if not candidates:
        return None

    # Use best match only if score >= 2 (at least last name + one other token)
    best = candidates[0]
    if best["match_score"] < 2:
        if verbose:
            print(f"    [TPS] Best match score {best['match_score']} too low for {name!r}", flush=True)
        return None

    if verbose:
        print(f"    [TPS] Best match: {best['name']!r} (score={best['match_score']})", flush=True)

    # Fetch detail page
    time.sleep(1.2)
    try:
        page.goto(best["detail_url"], wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(2000)
    except PlaywrightTimeout:
        if verbose:
            print(f"    [TPS] Timeout loading detail page for {best['name']!r}", flush=True)
        return None

    detail_html = page.content()
    if _is_blocked(detail_html):
        if verbose:
            print(f"    [TPS] Blocked on detail page for {best['name']!r}", flush=True)
        return None

    detail = _parse_detail_page(detail_html)
    detail["matched_name"] = best["name"]
    detail["match_score"] = best["match_score"]
    detail["detail_url"] = best["detail_url"]

    if verbose:
        print(
            f"    [TPS] {best['name']!r}: "
            f"addrs={len(detail['addresses'])}, phones={len(detail['phones'])}",
            flush=True,
        )

    return detail


# ---------------------------------------------------------------------------
# Public enrichment API
# ---------------------------------------------------------------------------

def enrich_probate_lead(
    *,
    decedent_name: str,
    executor_name: str,
    city_state: str = "Memphis TN",
    cache_path: Optional[Path] = None,
    verbose: bool = False,
) -> dict | None:
    """
    Enrich a single probate lead via TruePeopleSearch.

    Steps:
      1. Search decedent name → get addresses (for parcel cross-reference)
      2. Search executor name → get phone numbers

    Returns a contact_info dict:
      {
        "executor_name": str,
        "executor_phone": str | None,
        "tps_decedent_addresses": [str, ...],
        "tps_decedent_relatives": [str, ...],
        "tps_enriched": bool,
      }

    Returns None if TPS is blocked or both searches return nothing.
    """
    if not _PW or not _BS4:
        raise RuntimeError(
            "playwright and beautifulsoup4 required: pip install playwright beautifulsoup4"
        )

    # Cache check
    cache: dict = {}
    if cache_path and cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    cache_key = f"{decedent_name.upper()}||{executor_name.upper()}"
    if cache_key in cache:
        if verbose:
            print(f"    [TPS] Cache hit for {decedent_name!r}", flush=True)
        return cache[cache_key]

    result: dict = {
        "executor_name": executor_name,
        "executor_phone": None,
        "tps_decedent_addresses": [],
        "tps_decedent_relatives": [],
        "tps_enriched": False,
    }

    with sync_playwright() as p:
        browser = _launch_browser(p)
        ctx = _new_context(browser)
        page = ctx.new_page()
        page.add_init_script(_STEALTH_JS)

        try:
            # 1. Decedent search
            dec_info = _search_person(page, decedent_name, city_state, verbose)
            if dec_info:
                result["tps_enriched"] = True
                result["tps_decedent_addresses"] = dec_info.get("addresses", [])
                result["tps_decedent_relatives"] = dec_info.get("relatives", [])

            # 2. Executor search
            if executor_name:
                time.sleep(1.5)
                exec_info = _search_person(page, executor_name, city_state, verbose)
                if exec_info and exec_info.get("phones"):
                    result["executor_phone"] = exec_info["phones"][0]
                    if not result.get("tps_enriched"):
                        result["tps_enriched"] = True

        finally:
            browser.close()

    # Write cache
    if cache_path and result["tps_enriched"]:
        cache[cache_key] = result
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")

    return result if result["tps_enriched"] else None


# ---------------------------------------------------------------------------
# Batch enrichment — single browser session for all leads
# ---------------------------------------------------------------------------

def batch_enrich_leads(
    leads: list[dict],
    *,
    city_state: str = "Memphis TN",
    inter_lead_delay: float = 4.0,
    cache_path: Optional[Path] = None,
    verbose: bool = False,
) -> dict[str, dict]:
    """
    Enrich multiple probate leads in ONE shared browser session.

    Each entry in `leads` must be a dict with:
      - "decedent_name": str
      - "executor_name": str  (may be empty)

    Returns a dict keyed by decedent_name.upper() whose values match the
    shape returned by enrich_probate_lead.

    Using a single browser session is preferable to calling enrich_probate_lead
    in a loop because:
      - One browser launch overhead instead of N
      - Cookies persist across requests (looks more like a human session)
      - inter_lead_delay prevents rate-limiting on back-to-back lookups
    """
    if not _PW or not _BS4:
        raise RuntimeError(
            "playwright and beautifulsoup4 required: pip install playwright beautifulsoup4"
        )

    # Load cache
    cache: dict = {}
    if cache_path and cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    results: dict[str, dict] = {}
    to_fetch: list[dict] = []

    for lead in leads:
        dec = (lead.get("decedent_name") or "").strip()
        exc = (lead.get("executor_name") or "").strip()
        if not dec:
            continue
        cache_key = f"{dec.upper()}||{exc.upper()}"
        if cache_key in cache:
            if verbose:
                print(f"  [TPS batch] Cache hit: {dec!r}", flush=True)
            results[dec.upper()] = cache[cache_key]
        else:
            to_fetch.append({"decedent_name": dec, "executor_name": exc, "cache_key": cache_key})

    if not to_fetch:
        if verbose:
            print(f"  [TPS batch] All {len(leads)} leads served from cache", flush=True)
        return results

    if verbose:
        print(
            f"  [TPS batch] {len(to_fetch)} leads to fetch "
            f"({len(leads) - len(to_fetch)} cached), delay={inter_lead_delay}s",
            flush=True,
        )

    blocked = False  # stop early if TPS blocks mid-batch

    with sync_playwright() as p:
        browser = _launch_browser(p)
        ctx = _new_context(browser)
        page = ctx.new_page()
        page.add_init_script(_STEALTH_JS)

        # Warm up the session with the TPS home page
        try:
            page.goto("https://www.truepeoplesearch.com/", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)
        except Exception:
            pass

        try:
            for i, item in enumerate(to_fetch):
                if blocked:
                    break

                dec = item["decedent_name"]
                exc = item["executor_name"]
                cache_key = item["cache_key"]

                if verbose:
                    print(
                        f"  [TPS batch] [{i+1}/{len(to_fetch)}] {dec!r} / {exc!r}",
                        flush=True,
                    )

                result: dict = {
                    "executor_name": exc,
                    "executor_phone": None,
                    "tps_decedent_addresses": [],
                    "tps_decedent_relatives": [],
                    "tps_enriched": False,
                }

                # Decedent search
                dec_info = _search_person(page, dec, city_state, verbose)
                if dec_info is None and _is_blocked(page.content()):
                    if verbose:
                        print("  [TPS batch] Blocked — stopping batch early", flush=True)
                    blocked = True
                    break

                if dec_info:
                    result["tps_enriched"] = True
                    result["tps_decedent_addresses"] = dec_info.get("addresses", [])
                    result["tps_decedent_relatives"] = dec_info.get("relatives", [])

                # Executor search (with intra-lead delay)
                if exc:
                    time.sleep(2.0)
                    exec_info = _search_person(page, exc, city_state, verbose)
                    if exec_info and exec_info.get("phones"):
                        result["executor_phone"] = exec_info["phones"][0]
                        result["tps_enriched"] = True

                results[dec.upper()] = result

                # Persist cache after each successful lead so partial results survive
                if result["tps_enriched"] and cache_path:
                    cache[cache_key] = result
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(
                        json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8"
                    )

                # Inter-lead delay (skip after last lead)
                if i < len(to_fetch) - 1:
                    time.sleep(inter_lead_delay)

        finally:
            browser.close()

    return results


# ---------------------------------------------------------------------------
# CLI probe
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe TruePeopleSearch for a person (Shelby County probate enrichment)."
    )
    parser.add_argument("name", help="Person name to search (e.g. 'William Jorgensen')")
    parser.add_argument(
        "--city-state", default="Memphis TN",
        help="City + state for search (default: 'Memphis TN')"
    )
    parser.add_argument(
        "--save-html", default=None, metavar="FILE",
        help="Save search results HTML to this file for selector debugging"
    )
    parser.add_argument(
        "--executor", default="", metavar="NAME",
        help="Also search this executor/petitioner name"
    )
    args = parser.parse_args()

    if not _PW:
        print("ERROR: pip install playwright && playwright install chromium", flush=True)
        return 1
    if not _BS4:
        print("ERROR: pip install beautifulsoup4", flush=True)
        return 1

    save_html = Path(args.save_html) if args.save_html else None

    with sync_playwright() as p:
        browser = _launch_browser(p)
        ctx = _new_context(browser)
        page = ctx.new_page()
        page.add_init_script(_STEALTH_JS)

        result = _search_person(page, args.name, args.city_state, verbose=True, save_html=save_html)
        print("\n--- Decedent result ---")
        print(json.dumps(result, indent=2))

        if args.executor:
            time.sleep(1.5)
            exec_result = _search_person(page, args.executor, args.city_state, verbose=True)
            print("\n--- Executor result ---")
            print(json.dumps(exec_result, indent=2))

        browser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
