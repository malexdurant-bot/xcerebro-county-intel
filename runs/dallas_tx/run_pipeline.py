"""
Dallas County, TX — daily pipeline runner.

Sources scraped in this build:
  - PublicSearch (Kofile) Real Property recordings  -> clerk_recordings
    (filtered to distress-relevant doc types only; see translate.py)
  - PublicSearch (Kofile) Foreclosures department    -> foreclosure_notices
  - County Tax Office weekly bulk Tax Roll (TRW)     -> tax_collector
    (filtered to rows with a filed lawsuit; see translate.py)
  - LGBS tax sales API (STRUCK OFF)                  -> tax_foreclosure_resales
  - LGBS tax sales API (SALE/RESALE/FUTURE SALE)     -> sheriff_sales

Not built:
  - court_civil — Tyler Odyssey Smart Search is lookup-only (no bulk
    date-range browse); a paid District Clerk subscription ($50/mo) was
    identified but not yet subscribed. See config/counties/dallas_tx.json
    court_civil block and runs/dallas_tx/operator_notes.md.
  - court_probate — dropped per operator decision (no viable bulk path
    exists at all, unlike court_civil). See config court_probate block
    (enabled=false).

Known structural data limitations — see translate.py module docstring:
  foreclosure_notices and both LGBS-derived sources expose no owner/
  defendant name at the index level, so those leads are emitted but route
  to REVIEW_REQUIRED for debtor resolution rather than a clean match
  (never dropped).

Usage:
  python runs/dallas_tx/run_pipeline.py                    # full run, all sources
  python runs/dallas_tx/run_pipeline.py --skip-tax-collector  # skip the slow (~10min) weekly TRW parse
  python runs/dallas_tx/run_pipeline.py --dry-run           # scrape only, skip pipeline stages
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
# Force line buffering even when stdout is redirected to a file (as it is
# when launched as a detached process) so progress prints show up live
# instead of sitting in a block buffer until exit -- long stages here
# (the 1.4M-line tax_collector file, the staged pipeline over tens of
# thousands of events) otherwise look silently stalled for many minutes.
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

WORKDIR = ROOT / "runs" / "dallas_tx" / "pipeline_output"
RAW_DIR = WORKDIR / "raw"
WORKDIR.mkdir(parents=True, exist_ok=True)
RAW_DIR.mkdir(parents=True, exist_ok=True)

from translate import (  # noqa: E402
    SIGNAL_TYPE_LABELS,
    stream_translate_tax_collector,
    translate_clerk_recordings,
    translate_foreclosure_notices,
    translate_taxsales_lgbs,
)


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Dallas County TX daily pipeline")
    parser.add_argument("--dry-run", action="store_true",
                         help="Run scrapers only; skip pipeline stages")
    parser.add_argument("--skip-tax-collector", action="store_true",
                         help="Skip the tax_collector weekly TRW scrape (slow, ~10min "
                              "even with a cached zip) and reuse whatever raw JSONL "
                              "already exists on disk")
    parser.add_argument("--no-approve-review", action="store_true", default=False,
                         help="Halt on NEEDS_OPERATOR_REVIEW instead of auto-approving")
    parser.add_argument("--skip-dcad-enrichment", action="store_true",
                         help="Skip the DCAD parcel/address enrichment lookup (one HTTP "
                              "request per unique account, ~0.2s each -- thousands of "
                              "accounts can take 15-25min). Leads will have no address.")
    parser.add_argument("--dcad-cache", default=None,
                         help="Optional path to a DCAD enrichment cache JSON (from "
                              "scrapers/parcel_master_dcad_dallas.py) to reuse instead of "
                              "re-querying every account live.")
    parser.add_argument("--skip-scrape", action="store_true",
                         help="Skip Step 1 entirely and reuse whatever raw JSONL for all 4 "
                              "sources already exists on disk. For re-running translate/"
                              "scoring/publish after a code fix without waiting through a "
                              "full re-scrape (e.g. clerk_recordings' RP department alone "
                              "can take 15+ pages).")
    args = parser.parse_args()

    approve_review = not args.no_approve_review

    ts_start = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[dallas_tx] Pipeline starting — {ts_start}")

    # ------------------------------------------------------------------
    # Step 1 — Scrape all 4 sources
    # ------------------------------------------------------------------
    if args.skip_scrape:
        print("[dallas_tx] --skip-scrape: reusing existing raw JSONL for all sources")
    else:
        from scrapers.publicsearch_recorder_dallas import run_scraper as scrape_clerk_recordings  # noqa: E402
        from scrapers.publicsearch_foreclosures_dallas import run_scraper as scrape_foreclosure_notices  # noqa: E402
        from scrapers.tax_collector_dallas import run_scraper as scrape_tax_collector  # noqa: E402
        from scrapers.taxsales_lgbs_dallas import run_scraper as scrape_taxsales_lgbs  # noqa: E402

        print("[dallas_tx] Scraping clerk_recordings + foreclosure_notices (PublicSearch)...")
        scrape_clerk_recordings(RAW_DIR, verbose=True)
        scrape_foreclosure_notices(RAW_DIR, verbose=True)

        if args.skip_tax_collector:
            print("[dallas_tx] --skip-tax-collector: reusing existing raw tax_collector.jsonl")
        else:
            print("[dallas_tx] Scraping tax_collector (weekly bulk TRW file — this can take "
                  "several minutes even with a cached zip)...")
            scrape_tax_collector(RAW_DIR, verbose=True)

        print("[dallas_tx] Scraping tax_foreclosure_resales + sheriff_sales (LGBS API)...")
        scrape_taxsales_lgbs(RAW_DIR, verbose=True)

    if args.dry_run:
        print("[dallas_tx] --dry-run: stopping before pipeline stages. Done.")
        return

    # ------------------------------------------------------------------
    # Step 2 — Load raw JSONL + translate to raw_event_record shape
    # ------------------------------------------------------------------
    print("[dallas_tx] Loading + translating clerk_recordings...", flush=True)
    clerk_raw = _load_jsonl(RAW_DIR / "clerk_recordings.jsonl")
    clerk_events = translate_clerk_recordings(clerk_raw)
    print(f"[dallas_tx]   clerk_recordings: {len(clerk_raw)} raw -> {len(clerk_events)} events", flush=True)

    print("[dallas_tx] Loading + translating foreclosure_notices...", flush=True)
    foreclosure_raw = _load_jsonl(RAW_DIR / "foreclosure_notices.jsonl")
    foreclosure_events = translate_foreclosure_notices(foreclosure_raw)
    print(f"[dallas_tx]   foreclosure_notices: {len(foreclosure_raw)} raw -> {len(foreclosure_events)} events", flush=True)

    # debtor_name_ocr_hint (2026-08-28): a best-effort, watermark-cleanup
    # SECOND OCR pass the scraper runs on the debtor-label row of each
    # foreclosure notice image (see publicsearch_foreclosures_dallas.py's
    # _ocr_watermark_cleaned_hint). It's frequently garbled at the character
    # level and is deliberately NOT fed into owner_name extraction — the
    # shared debtor_party_engine never sees it, and it's excluded from
    # scored_lead_record entirely (that schema is additionalProperties:false,
    # shared across counties). It's written here as its own side file, keyed
    # by instrument_number (== doc_number, stable across every pipeline
    # stage), so a human reviewing a REVIEW_REQUIRED foreclosure_notices lead
    # in foreclosure_notices_leads_base.json can cross-reference this file by
    # instrument_number for a possible-name lead — never treated as ground
    # truth.
    foreclosure_ocr_hints = [
        {
            "instrument_number": rec["raw_payload"].get("doc_number"),
            "recorded_date": rec["raw_payload"].get("recorded_date_raw"),
            "ocr_hint": rec["raw_payload"]["debtor_name_ocr_hint"],
        }
        for rec in foreclosure_raw
        if rec.get("raw_payload", {}).get("debtor_name_ocr_hint")
    ]
    if foreclosure_ocr_hints:
        hints_path = WORKDIR / "foreclosure_notices_review_hints.json"
        hints_path.write_text(
            json.dumps(foreclosure_ocr_hints, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"[dallas_tx]   {len(foreclosure_ocr_hints)} watermark-cleanup review hints "
              f"-> {hints_path}", flush=True)

    print("[dallas_tx] Streaming + translating tax_collector (large file, filtered inline)...", flush=True)
    tax_collector_path = RAW_DIR / "tax_collector.jsonl"
    tax_collector_events = (
        stream_translate_tax_collector(tax_collector_path, verbose=True)
        if tax_collector_path.exists() else []
    )
    print(f"[dallas_tx]   tax_collector: -> {len(tax_collector_events)} events (suit-pending + recent only)", flush=True)

    print("[dallas_tx] Loading tax_foreclosure_resales + sheriff_sales (LGBS)...", flush=True)
    resales_raw = _load_jsonl(RAW_DIR / "tax_foreclosure_resales.jsonl")
    sheriff_raw = _load_jsonl(RAW_DIR / "sheriff_sales.jsonl")

    # ------------------------------------------------------------------
    # Step 2b — DCAD parcel/address enrichment (2026-08-26 fix: operator
    # reported no lead had an address. Root cause: this framework's
    # dashboard address comes from a separate parcel-enrichment join, not
    # from the distress-event scrapers -- Dallas never had a parcel_master
    # scraper. See scrapers/parcel_master_dcad_dallas.py module docstring.
    # Also resolves most LGBS "unidentified party" REVIEW_REQUIRED leads,
    # since DCAD's account lookup returns a real owner name where the LGBS
    # feed itself exposes none, feeding translate_taxsales_lgbs's
    # dcad_lookup parameter.
    # ------------------------------------------------------------------
    dcad_lookup: dict = {}
    if args.dcad_cache:
        print(f"[dallas_tx] Loading DCAD enrichment cache from {args.dcad_cache}...", flush=True)
        dcad_lookup = json.loads(Path(args.dcad_cache).read_text(encoding="utf-8"))
    elif not args.skip_dcad_enrichment:
        accounts: set[str] = set()
        for ev in tax_collector_events:
            pid = (ev.get("property_refs") or {}).get("parcel_id")
            if pid:
                accounts.add(pid)
        for rec in resales_raw + sheriff_raw:
            acct = (rec.get("raw_payload") or {}).get("account_nbr")
            if acct:
                accounts.add(acct)
        print(f"[dallas_tx] DCAD enrichment: looking up {len(accounts)} unique accounts "
              f"(this can take 15-25min)...", flush=True)
        from scrapers.parcel_master_dcad_dallas import enrich_accounts  # noqa: E402
        dcad_cache_path = WORKDIR / "dcad_enrichment_cache.json"
        dcad_lookup = enrich_accounts(
            sorted(accounts), verbose=True, checkpoint_path=dcad_cache_path
        )
        print(f"[dallas_tx] DCAD enrichment cache written -> {dcad_cache_path}", flush=True)
    else:
        print("[dallas_tx] --skip-dcad-enrichment: leads will have no address", flush=True)

    print("[dallas_tx] Translating tax_foreclosure_resales + sheriff_sales...", flush=True)
    resales_events = translate_taxsales_lgbs(resales_raw, dcad_lookup=dcad_lookup)
    sheriff_events = translate_taxsales_lgbs(sheriff_raw, dcad_lookup=dcad_lookup)
    print(f"[dallas_tx]   tax_foreclosure_resales: {len(resales_raw)} raw -> {len(resales_events)} events", flush=True)
    print(f"[dallas_tx]   sheriff_sales: {len(sheriff_raw)} raw -> {len(sheriff_events)} events", flush=True)

    raw_events: list[dict] = (
        clerk_events + foreclosure_events + tax_collector_events
        + resales_events + sheriff_events
    )

    print(f"[dallas_tx] Translated to {len(raw_events)} raw_event_record entries total "
          f"(after distress-relevance filtering)", flush=True)

    if not raw_events:
        print("[dallas_tx] No raw events after filtering — pipeline up to date. Exiting.")
        return

    # ------------------------------------------------------------------
    # Step 3 — Staged pipeline. (2026-08-26: DCAD enrichment now runs above
    # in Step 2b and gets attached to each scored lead's `parcel_display`
    # just below — the R3(iii) enrichment-optional rule still applies, this
    # just no longer leaves every lead UNENRICHED by omission.)
    # ------------------------------------------------------------------
    from scaffold.pipeline.run_pipeline_staged import (  # noqa: E402
        build_dashboard_payload,
        run_staged_pipeline,
    )
    from scaffold.pipeline.scoring_seam import (  # noqa: E402
        SemanticGateBlocked,
        SemanticGateNeedsReview,
    )

    print(f"[dallas_tx] Running staged pipeline on {len(raw_events)} raw events...")

    try:
        result = run_staged_pipeline(
            raw_events,
            signal_type_labels=SIGNAL_TYPE_LABELS,
            workdir=WORKDIR,
            as_of=date.today(),
            approve_needs_review=approve_review,
        )
    except SemanticGateBlocked as exc:
        print(f"[dallas_tx] PIPELINE BLOCKED — §20 semantic gate: {exc}")
        print(f"[dallas_tx] Check {WORKDIR / 'matched_leads.json'} for details.")
        sys.exit(1)
    except SemanticGateNeedsReview as exc:
        print(f"[dallas_tx] §20 gate NEEDS_OPERATOR_REVIEW: {exc}")
        print("[dallas_tx] Re-run without --no-approve-review to auto-approve and continue.")
        sys.exit(2)

    scored_leads = result["scored_leads"]
    semantic_verdict = result["semantic_verdict"]

    print(f"[dallas_tx] §20 verdict:    {semantic_verdict}")
    print(f"[dallas_tx] Scored leads:   {len(scored_leads)}")

    # ------------------------------------------------------------------
    # Step 3b — Attach DCAD enrichment to each scored lead's parcel_display.
    # run_pipeline_staged.py's dashboard adapter reads
    # scored_lead["parcel_display"] directly (see its module docstring) --
    # no separate parcel-master join at dashboard-build time is needed
    # here, unlike scaffold/pipeline/dashboard.py's older project_lead(lead,
    # parcel) contract.
    # ------------------------------------------------------------------
    enriched_count = 0
    for lead in scored_leads:
        pid = lead.get("primary_parcel_id")
        match = dcad_lookup.get(pid) if pid else None
        if match:
            lead["parcel_display"] = {
                "situs_address": match.get("situs_address"),
                "situs_city": match.get("situs_city"),
                "situs_state": "TX",
                "assessed_value": match.get("assessed_value"),
            }
            # Schema contract: parcel_display is present iff enrichment_status
            # is ENRICHED (scored_lead_record.schema.json).
            lead["enrichment_status"] = "ENRICHED"
            enriched_count += 1
    print(f"[dallas_tx] DCAD-enriched {enriched_count}/{len(scored_leads)} leads with a real address", flush=True)

    # ------------------------------------------------------------------
    # Step 4 — Dashboard payload (LOCAL ONLY — see note below)
    # ------------------------------------------------------------------
    payload = build_dashboard_payload(
        scored_leads,
        semantic_verdict=semantic_verdict,
        county="Dallas",
        state="TX",
        mode="full_rebuild",
        build_label="PARTIAL_BUILD",  # court_civil/court_probate not built — see module docstring
    )
    payload_json = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    dash_path = WORKDIR / "data.json"
    dash_path.write_text(payload_json, encoding="utf-8")

    # NOTE: deliberately NOT writing to dashboard/data/leads.json. That file
    # is the single shared "live" dashboard other counties' run_pipeline.py
    # scripts overwrite on every run (confirmed: dashboard/dashboard.js only
    # ever reads ./data/leads.json — there is no per-county routing in the
    # static site). Overwriting it here would silently replace whatever
    # county's data is currently live (e.g. Shelby's) with Dallas's. Writing
    # only to the per-county archival path below, matching the pattern
    # already used for dashboard/shelby_tn/dashboard.json, until an operator
    # decides Dallas should become the live dashboard.
    county_dash_path = ROOT / "dashboard" / "dallas_tx" / "dashboard.json"
    county_dash_path.parent.mkdir(parents=True, exist_ok=True)
    county_dash_path.write_text(payload_json, encoding="utf-8")

    # Also refresh the internal standalone page's own data file
    # (dashboard/dallas_tx/index.html reads ./data/leads.json relative to
    # itself -- this was previously a manual copy step, now automatic).
    internal_page_data_path = ROOT / "dashboard" / "dallas_tx" / "data" / "leads.json"
    internal_page_data_path.parent.mkdir(parents=True, exist_ok=True)
    internal_page_data_path.write_text(payload_json, encoding="utf-8")

    print(f"[dallas_tx] Pipeline output    → {dash_path}")
    print(f"[dallas_tx] County dashboard   → {county_dash_path}")
    print(f"[dallas_tx] Internal page data → {internal_page_data_path}")
    print(f"[dallas_tx] (NOT written: dashboard/data/leads.json — shared live slot, "
          f"left untouched.)")
    print(f"[dallas_tx] Lead total:        {payload['lead_total']}")
    print(f"[dallas_tx] Score tiers:       {payload['score_tier_distribution']}")
    print(f"[dallas_tx] Patterns:          {payload['pattern_counts']}")

    # ------------------------------------------------------------------
    # Step 4b — Publish to the isolated client-facing repo
    # (dallastx.justfriday.ai), matching richland_sc / shelby_tn's exact
    # pattern. Own repo, own domain -- a Dallas client's browser has no
    # path to another county's data. Not fatal if the sibling checkout
    # doesn't exist on a given machine (e.g. a fresh clone elsewhere).
    # ------------------------------------------------------------------
    _client_repo_dir = ROOT.parent / "dallas-tx-leads"
    if _client_repo_dir.is_dir():
        try:
            import subprocess as _subprocess
            _client_data_path = _client_repo_dir / "data" / "leads.json"
            _client_data_path.parent.mkdir(parents=True, exist_ok=True)
            _client_data_path.write_text(payload_json, encoding="utf-8")
            _subprocess.run(
                ["git", "add", "data/leads.json"],
                cwd=str(_client_repo_dir), check=True, capture_output=True,
            )
            _subprocess.run(
                ["git", "commit", "-m",
                 f"data: dashboard update {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"],
                cwd=str(_client_repo_dir), check=True, capture_output=True,
            )
            _subprocess.run(
                ["git", "push", "origin", "main"],
                cwd=str(_client_repo_dir), check=True, capture_output=True, timeout=60,
            )
            print("[dallas_tx] Client dashboard updated → https://dallastx.justfriday.ai/")
        except _subprocess.CalledProcessError as exc:
            _stderr = exc.stderr.decode(errors="replace") if exc.stderr else ""
            if "nothing to commit" in _stderr or "nothing to commit" in (exc.stdout or b"").decode(errors="replace"):
                print("[dallas_tx] Client dashboard: no changes to publish")
            else:
                print(f"[dallas_tx] Client dashboard publish failed (non-fatal): {_stderr[:200]}")
    else:
        print(
            f"[dallas_tx] Client dashboard repo not found at {_client_repo_dir} — "
            "skipping (not fatal; only affects this machine)"
        )

    print(f"[dallas_tx] Pipeline complete — "
          f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}")


if __name__ == "__main__":
    main()
