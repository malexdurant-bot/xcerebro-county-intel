"""
Dallas County Clerk PublicSearch — Tier 3 / Tier 4 lookup, linked to
existing leads (2026-09-15 client expansion; made a standard automatic
pipeline step 2026-09-23 per operator instruction).

Scope decision (confirmed with the operator 2026-09-15): the client's
Tier 3 (chain-of-title / payoff / signing-authority doc types — warranty
deeds, mortgages, releases, powers of attorney, easements, leases — the
types she described as things to "run on every deal") and Tier 4
(memoranda / contracts-of-sale — described as seeing "who else is working
your farm area") are NOT added to the daily distress lead feed the way
Tier 1/2 are (see translate.py's TIER3_DOC_TYPES / TIER4_DOC_TYPES module
docstring — they're the majority of all county recordings and would drown
the real distress signal). Instead, this tool:

  1. Reads an existing dashboard payload (pipeline_output/data.json by
     default) produced by run_pipeline.py.
  2. For each lead's already-resolved owner name, searches PublicSearch
     for that owner's OTHER filings.
  3. Keeps only the hits whose doc_type is in the selected tier
     (TIER3_DOC_TYPES or TIER4_DOC_TYPES).
  4. Writes the hits to a side file (related_records.json) keyed by
     lead_id, AND attaches `related_records` / `related_records_count` /
     `has_related_records` directly onto each matching lead in a copy of
     the dashboard payload, so the dashboard can render a badge and filter
     on it with zero frontend schema changes beyond reading a few more
     optional fields.

Per the operator's original instruction, this ONLY searches owners already
surfaced by a Tier 1/2 lead — it is not a bulk Tier 3/4 scrape of the whole
county.

Automatic/incremental mode (2026-09-23): run_pipeline.py now calls
run_incremental_lookup() as a standard step every run. A per-lead owner
search takes ~15-25s (real browser search + pagination), so checking the
full ~1,500-lead Tier-1/2 backlog in one run would take hours — instead,
a persistent state file (title_chain_state.json) records which lead_ids
have already been checked per tier, so each lead is searched AT MOST ONCE
ever (not re-checked every day), and each run only spends its
--title-chain-budget (default 75 leads/tier/run) on leads it hasn't seen
yet -- new leads first, then working down the backlog across multiple
days. Trade-off: a lead's title chain is a snapshot as of when it was
checked -- a NEW Tier 3/4 filing recorded against that owner afterward
won't be picked up (no periodic re-check). --skip-title-chain opts out
per-run; the standalone `main()` CLI below still works for a manual,
uncapped, single-tier run against a specific dashboard file.

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
DEFAULT_BUDGET_PER_TIER = 75


def load_state(state_path: Path) -> dict:
    """{"tier3_checked": [lead_id, ...], "tier4_checked": [lead_id, ...],
    "related_records": {lead_id: [hit, ...]}} -- which leads have already
    been searched (per tier) and what was found, persisted across runs so
    an incremental run (a) never re-checks the same lead twice and (b) a
    lead checked on day 1 still shows its hits (or lack thereof) in every
    later day's freshly-rebuilt dashboard payload, not just the run that
    found them -- see hydrate_records()."""
    if not state_path.exists():
        return {"tier3_checked": [], "tier4_checked": [], "related_records": {}}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"tier3_checked": [], "tier4_checked": [], "related_records": {}}
    data.setdefault("tier3_checked", [])
    data.setdefault("tier4_checked", [])
    data.setdefault("related_records", {})
    return data


def save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def hydrate_records(records: list[dict], state: dict) -> None:
    """Mutates every record in place with related_records / related_records_
    count / has_related_records / related_records_checked, sourced entirely
    from `state` -- NOT just the records touched by this run's
    run_incremental_lookup() calls. Every pipeline run rebuilds the
    dashboard payload from scratch (project_scored_lead has no memory of
    prior runs), so without this, a lead checked and found clean on day 1
    would silently lose that "checked" status on day 2's payload. Call this
    once, after both tiers' run_incremental_lookup() calls for the run."""
    tier3_checked = set(state.get("tier3_checked", []))
    tier4_checked = set(state.get("tier4_checked", []))
    related_by_lead = state.get("related_records", {})
    for rec in records:
        lead_id = rec.get("lead_id")
        related = related_by_lead.get(lead_id, []) if lead_id else []
        rec["related_records"] = related
        rec["related_records_count"] = len(related)
        rec["has_related_records"] = len(related) > 0
        rec["related_records_checked"] = bool(
            lead_id and (lead_id in tier3_checked or lead_id in tier4_checked)
        )


def run_incremental_lookup(
    records: list[dict],
    tier: int,
    state: dict,
    budget: int = DEFAULT_BUDGET_PER_TIER,
    headless: bool = True,
    verbose: bool = True,
) -> dict:
    """For up to `budget` leads not yet checked for this tier (tracked via
    `state["tier{tier}_checked"]`), searches PublicSearch for the owner's
    Tier 3/4 filings and records any hits into `state["related_records"]`.
    Mutates `state` in place; does NOT touch `records` directly -- call
    hydrate_records() afterward to project state back onto the payload.
    Caller is responsible for persisting `state` (see save_state)."""
    if tier not in (3, 4):
        raise ValueError("tier must be 3 or 4")
    checked_key = f"tier{tier}_checked"
    already_checked = set(state.get(checked_key, []))
    related_by_lead = state.setdefault("related_records", {})

    candidates = [
        r for r in records
        if r.get("lead_id") and r["lead_id"] not in already_checked
        and r.get("display_owner") and r["display_owner"] != "Unknown"
    ]
    todo = candidates[:budget]

    stats = {"tier": tier, "attempted": 0, "leads_with_hits": 0,
              "remaining_backlog": max(0, len(candidates) - len(todo))}

    if not todo:
        if verbose:
            print(f"  [title_chain] tier {tier}: nothing new to check "
                  f"(backlog empty or budget exhausted last run)", flush=True)
        return stats

    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(_PLAYWRIGHT_INSTALL_MSG)
    tier_doc_types = set(TIER3_DOC_TYPES if tier == 3 else TIER4_DOC_TYPES)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=USER_AGENT, viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        try:
            for i, rec in enumerate(todo):
                owner = rec["display_owner"]
                lead_id = rec["lead_id"]
                if verbose:
                    print(f"  [title_chain] tier {tier} [{i + 1}/{len(todo)}] "
                          f"{owner} (lead {lead_id})...", flush=True)
                hits = lookup_owner(page, owner, tier_doc_types, verbose=verbose)
                stats["attempted"] += 1
                already_checked.add(lead_id)
                if hits:
                    stats["leads_with_hits"] += 1
                    related = [
                        {
                            "tier": tier,
                            "doc_type": h.get("doc_type"),
                            "recorded_date": h.get("recorded_date"),
                            "doc_number": h.get("doc_number"),
                            "detail_url": h.get("detail_url"),
                        }
                        for h in hits
                    ]
                    related_by_lead.setdefault(lead_id, []).extend(related)
                    if verbose:
                        print(f"    [title_chain] {len(hits)} Tier {tier} record(s) found", flush=True)
        finally:
            browser.close()

    state[checked_key] = sorted(already_checked)
    return stats


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
            "Dallas County Tier 3/4 manual lookup — searches PublicSearch for "
            "existing leads' owners' OTHER filings (title/payoff/signing-authority "
            "types for --tier 3, competitor/own-filing types for --tier 4), and "
            "links any hits back to the originating lead. run_pipeline.py now runs "
            "this automatically every day (incremental/budgeted, see "
            "run_incremental_lookup); this CLI is for an uncapped, single-tier, "
            "ad-hoc run against a specific dashboard file."
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
