"""
Dallas County Clerk PublicSearch — Tier 3 / Tier 4 on-demand lookup, linked
to existing leads (2026-09-15 client expansion).

Scope decision (confirmed with the operator this session): the client's
Tier 3 (chain-of-title / payoff / signing-authority doc types — warranty
deeds, mortgages, releases, powers of attorney, easements, leases — the
types she described as things to "run on every deal") and Tier 4
(memoranda / contracts-of-sale — described as seeing "who else is working
your farm area") are NOT added to the daily distress lead feed the way
Tier 1/2 are (see translate.py's TIER3_DOC_TYPES / TIER4_DOC_TYPES module
docstring — they're the majority of all county recordings and would drown
the real distress signal). Instead, this is a separate, operator-triggered
tool that:

  1. Reads an existing dashboard payload (pipeline_output/data.json by
     default) produced by run_pipeline.py.
  2. For each lead's already-resolved owner name, searches PublicSearch
     for that owner's OTHER filings.
  3. Keeps only the hits whose doc_type is in the selected tier
     (TIER3_DOC_TYPES or TIER4_DOC_TYPES).
  4. Writes the hits to a side file (related_records.json) keyed by
     lead_id, AND attaches a lightweight `related_records` array directly
     onto each matching lead in a copy of the dashboard payload, so the
     dashboard can render a link back to the original lead with zero
     frontend schema changes beyond reading one more optional array.

Per the operator's explicit instruction, this ONLY searches owners already
surfaced by a Tier 1/2 lead — it is not a bulk Tier 3/4 scrape of the whole
county, and it is never run automatically as part of the daily pipeline.

Reuses the already-verified click-driven search/scrape/pagination helpers
from publicsearch_recorder_dallas.py (same portal, same Playwright
quirks — see that module's docstring for why a hand-built /results?...
URL doesn't work here either) rather than duplicating that logic. Only the
"how the search is triggered" step is new (a party-name text search
instead of a date-range search).

Requires: pip install playwright && playwright install chromium
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

_PLAYWRIGHT_INSTALL_MSG = (
    "playwright not installed. Run: pip install playwright && playwright install chromium"
)

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scrapers.publicsearch_recorder_dallas import (  # noqa: E402
    PORTAL_URL,
    USER_AGENT,
    _click_button_by_text,
    _goto_next_page,
    _scrape_current_table_page,
)
from translate import TIER3_DOC_TYPES, TIER4_DOC_TYPES  # noqa: E402

_MAX_POLLS = 12
_POLL_INTERVAL_MS = 3_000
DEFAULT_MAX_PAGES_PER_OWNER = 5


def _run_owner_name_search(page, owner_name: str, verbose: bool) -> str:
    """Party-name text search via the Quick Search box (placeholder
    "Search for grantor/grantee, subdivision, doc type, or doc#" -- live-
    confirmed 2026-09-15 that this box matches on grantor/grantee name,
    not just doc type/subdivision). Returns 'HasRows' | 'NoResults' |
    'Error', same status contract as publicsearch_recorder_dallas.py's
    _run_search (not reused directly -- that function only drives the
    date-range fields, this one drives the search-term box instead)."""
    page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_selector(
        'input[placeholder*="grantor/grantee"]', timeout=15_000
    )
    page.fill('input[placeholder*="grantor/grantee"]', owner_name)
    _click_button_by_text(page, "Search")

    for i in range(_MAX_POLLS):
        page.wait_for_timeout(_POLL_INTERVAL_MS)
        body = page.inner_text("body")
        trs = page.query_selector_all("table tbody tr")
        if trs:
            if verbose:
                print(f"    [title_chain] search resolved after "
                      f"{(i + 1) * _POLL_INTERVAL_MS / 1000:.0f}s: {len(trs)} rows", flush=True)
            return "HasRows"
        if "No Results Found" in body:
            return "NoResults"
        if "Error While Running Search" in body:
            return "Error"
    return "Error"


def lookup_owner(
    page, owner_name: str, tier_doc_types: set[str],
    max_pages: int = DEFAULT_MAX_PAGES_PER_OWNER, verbose: bool = False,
) -> list[dict]:
    """Search for owner_name and return only the rows whose doc_type is in
    tier_doc_types. do_ocr is always False here -- this tool links
    existing filings to a lead, it doesn't need the address-extraction OCR
    pass the daily distress scraper uses."""
    status = _run_owner_name_search(page, owner_name, verbose)
    if status != "HasRows":
        return []

    hits: list[dict] = []
    for page_num in range(max_pages):
        rows = _scrape_current_table_page(page, do_ocr=False, verbose=verbose)
        hits.extend(r for r in rows if (r.get("doc_type") or "").strip().upper() in tier_doc_types)
        if len(rows) < 50:
            break
        if not _goto_next_page(page, verbose):
            break
    return hits


def run_lookup(
    dashboard_path: Path,
    tier: int,
    out_path: Path,
    max_leads: "int | None" = None,
    headless: bool = True,
    verbose: bool = True,
    attach_to_dashboard: bool = True,
) -> dict:
    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(_PLAYWRIGHT_INSTALL_MSG)
    if tier not in (3, 4):
        raise ValueError("tier must be 3 or 4")

    tier_doc_types = set(TIER3_DOC_TYPES if tier == 3 else TIER4_DOC_TYPES)
    payload = json.loads(dashboard_path.read_text(encoding="utf-8"))
    records = payload.get("records", [])
    leads_to_check = records[:max_leads] if max_leads else records

    results: dict = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=USER_AGENT, viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        try:
            for i, rec in enumerate(leads_to_check):
                owner = rec.get("display_owner")
                lead_id = rec.get("lead_id")
                if not owner or owner == "Unknown" or not lead_id:
                    continue
                if verbose:
                    print(f"  [title_chain] [{i + 1}/{len(leads_to_check)}] "
                          f"{owner} (lead {lead_id})...", flush=True)
                hits = lookup_owner(page, owner, tier_doc_types, verbose=verbose)
                if hits:
                    results[lead_id] = {
                        "owner_name": owner,
                        "tier": tier,
                        "related_records": [
                            {
                                "doc_type": h.get("doc_type"),
                                "recorded_date": h.get("recorded_date"),
                                "doc_number": h.get("doc_number"),
                                "detail_url": h.get("detail_url"),
                            }
                            for h in hits
                        ],
                    }
                    if verbose:
                        print(f"    [title_chain] {len(hits)} Tier {tier} record(s) found", flush=True)
        finally:
            browser.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if attach_to_dashboard and results:
        for rec in records:
            hit = results.get(rec.get("lead_id"))
            if hit:
                rec["related_records"] = hit["related_records"]
        dashboard_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
        )

    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Dallas County Tier 3/4 on-demand lookup — searches PublicSearch for "
            "existing leads' owners' OTHER filings (title/payoff/signing-authority "
            "types for --tier 3, competitor/own-filing types for --tier 4), and "
            "links any hits back to the originating lead. Never run automatically "
            "as part of the daily pipeline."
        )
    )
    parser.add_argument("--tier", type=int, required=True, choices=[3, 4],
                         help="Which doc-type tier to search for (3 or 4).")
    parser.add_argument("--dashboard", default=None,
                         help="Path to the dashboard payload to read leads from and "
                              "(unless --no-attach) update in place. "
                              "Default: pipeline_output/data.json")
    parser.add_argument("--out-file", default=None,
                         help="Path to write the related_records.json side file. "
                              "Default: pipeline_output/related_records.json")
    parser.add_argument("--max-leads", type=int, default=None,
                         help="Cap on how many leads to check (for a quick test run).")
    parser.add_argument("--no-attach", action="store_true",
                         help="Write the side file only; don't update the dashboard payload.")
    parser.add_argument("--no-headless", action="store_true")
    args = parser.parse_args()

    if not PLAYWRIGHT_AVAILABLE:
        print(f"ERROR: {_PLAYWRIGHT_INSTALL_MSG}", file=sys.stderr)
        return 1

    workdir = REPO_ROOT / "runs" / "dallas_tx" / "pipeline_output"
    dashboard_path = Path(args.dashboard) if args.dashboard else workdir / "data.json"
    if not dashboard_path.exists():
        print(f"ERROR: dashboard payload not found at {dashboard_path} — run "
              "run_pipeline.py at least once first.", file=sys.stderr)
        return 1
    out_path = Path(args.out_file) if args.out_file else workdir / "related_records.json"

    results = run_lookup(
        dashboard_path, args.tier, out_path,
        max_leads=args.max_leads,
        headless=not args.no_headless,
        attach_to_dashboard=not args.no_attach,
    )
    print(json.dumps({
        "tier": args.tier,
        "leads_with_hits": len(results),
        "out_file": str(out_path),
        "attached_to_dashboard": not args.no_attach,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
