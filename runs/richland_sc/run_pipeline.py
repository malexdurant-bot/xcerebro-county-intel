"""
Richland County, SC — daily pipeline runner.

Sources scraped in this build (PARTIAL_BUILD — see "Not built" below):
  - Columbia Star Master's Sales       → notice_of_sale  (foreclosure)
  - Columbia Star Public Notices       → lis_pendens     (circuit civil)
  - Columbia Star Notice to Creditors  → letters_testamentary (probate)
  - Richland delinquent tax parcel API → tax_foreclosure_notice (tax distress)
  - Register of Deeds SMS (mechanics/tax liens only — see
    scrapers/richland_register_of_deeds.py for the doc-type scope and the
    2026-08-22 blocker's root cause + fix) →
      mechanics_lien / federal_tax_lien / state_tax_lien

Enrichment-only (does not add raw events, fills in fields on existing ones):
  - Richland Probate Estate Inquiry    → confirms case number / dates on
                                          Columbia Star probate leads by
                                          decedent-name lookup (the portal
                                          has no bulk or date-range search,
                                          so it cannot run as a primary feed)

Not built — see runs/richland_sc/richland_pipeline.log and
config/counties/richland_sc.json `blocker` fields for why:
  - SC Courts Public Index (publicindex.sccourts.org) — site disclaimer
    expressly prohibits automated scraping; Columbia Star Public Notices
    substitutes for the lis_pendens signal instead.
  - Master-in-Equity foreclosure sales page (richlandcountysc.gov) — the
    whole domain is Akamai-WAF-blocked from this environment (no residential
    proxy configured); Columbia Star Master's Sales substitutes instead.
  - Register of Deeds foreclosure-completion deeds (Foreclosure - Deed,
    Foreclosure - Mortgage, Master's Deed-Foreclosure) — deliberately out
    of scope, not blocked: these record a sale that already happened, so
    they aren't a fresh distress lead the way the liens above are. See
    scrapers/richland_register_of_deeds.py module docstring.

Usage:
  python runs/richland_sc/run_pipeline.py               # incremental (default)
  python runs/richland_sc/run_pipeline.py --full        # full rebuild
  python runs/richland_sc/run_pipeline.py --dry-run     # scrape only, skip pipeline
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no external dependency) — only sets vars not
    already present in the environment. Mirrors the loader in
    scrapers/richland_register_of_deeds.py.

    This file's own env reads below (LEADS_BACKEND_URL etc.) run at import
    time — before main() gets to the `from scrapers... import` lines that
    would otherwise populate .env via richland_register_of_deeds.py's own
    loader. Without calling this here first, run_richland_daily.cmd (which
    sets no environment variables of its own) has every optional
    integration read as unset even when .env has real values — confirmed
    live 2026-09-03: the deployed client-agent API had never received a
    single push despite months of "successful" daily runs, because
    RICHLAND_AGENT_API_URL/RICHLAND_AGENT_API_INGEST_KEY were always None
    by the time Step 5c read them."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


_load_dotenv(ROOT / ".env")

WORKDIR = ROOT / "pipeline_output" / "richland_sc"
WORKDIR.mkdir(parents=True, exist_ok=True)

# Client-facing dashboard (richlandsc.justfriday.ai) is a static, credential-
# free frontend living in its own dedicated repo (richland-sc-leads),
# isolated from this repo's other counties' code. It holds no data file —
# leads are fetched client-side only after the visitor authenticates,
# straight from the hosted backend this step publishes to below.
LEADS_BACKEND_URL = os.environ.get("LEADS_BACKEND_URL")
LEADS_BACKEND_WRITE_KEY = os.environ.get("LEADS_BACKEND_WRITE_KEY")

# Client-agent leads API (runs/richland_sc/api_server.py) — a separate,
# deliberately minimal read API a client's own automation polls for new
# leads (distinct from the dashboard backend above). Deployed on its own
# host (e.g. Render); this repo only pushes to it, never serves it locally.
RICHLAND_AGENT_API_URL = os.environ.get("RICHLAND_AGENT_API_URL")
RICHLAND_AGENT_API_INGEST_KEY = os.environ.get("RICHLAND_AGENT_API_INGEST_KEY")

SIGNAL_TYPE_LABELS: dict[str, str] = {
    "notice_of_sale": "Foreclosure Sale",
    "lis_pendens": "Lis Pendens",
    "letters_testamentary": "Notice to Creditors",
    "tax_foreclosure_notice": "Delinquent Tax",
    "mechanics_lien": "Mechanics Lien",
    "federal_tax_lien": "Federal Tax Lien",
    "state_tax_lien": "State Tax Lien",
}


def _build_debtor_party_rules() -> dict:
    """
    SC judicial foreclosure state override.

    The universal `notice_of_sale` rule fans out to `foreclosure_notice`'s
    DOCUMENT_BODY extraction, which looks for 'MORTGAGOR:', 'GRANTOR:', etc.
    Columbia Star Master's Sales notices use the SC court format:
      'in the case of PLAINTIFF vs. DEFENDANT'
    The defendant IS a structured DF party in our raw events, so we override
    to STRUCTURED extraction for this county.
    """
    from scaffold.pipeline.debtor_party_engine import UNIVERSAL_DEBTOR_PARTY_RULES
    return {
        **UNIVERSAL_DEBTOR_PARTY_RULES,
        "notice_of_sale": {
            "expected_debtor_name_type": "DF",
            "fallback_debtor_name_type": None,
            "filer_name_types": ["PL"],
            "debtor_source": "STRUCTURED",
            "known_filer_role": "lender/plaintiff (SC judicial foreclosure)",
        },
    }


# ---------------------------------------------------------------------------
# Raw-event loader (full-rebuild path)
# ---------------------------------------------------------------------------

def _load_jsonl_glob(dir_path: Path, pattern: str) -> list[dict]:
    events: list[dict] = []
    for path in sorted(dir_path.glob(pattern)):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    return events


def _load_all_raw_events(
    columbia_star_dir: Path, delinquent_tax_dir: Path, register_of_deeds_dir: Path,
) -> list[dict]:
    """Load all raw event records from every source's JSONL files."""
    events = _load_jsonl_glob(columbia_star_dir, "columbia_star_*.jsonl")
    events += _load_jsonl_glob(delinquent_tax_dir, "delinquent_tax_*.jsonl")
    events += _load_jsonl_glob(register_of_deeds_dir, "register_of_deeds_*.jsonl")
    return events


def _filter_by_recency(raw_events: list[dict], days: int) -> list[dict]:
    """Keep only events dated within the last `days` days (by event_date,
    falling back to recorded_date), PLUS every event that has neither field
    set at all. Some lead types (e.g. letters_testamentary — Columbia
    Star's Notice to Creditors parser never populates a date; see
    scrapers/columbia_star_richland.py) never carry a date on the raw
    record even though they're inherently recent (bounded by the scraper's
    own latest-N-articles fetch cap) — excluding them here would just be
    wrong, not a real recency filter."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    kept = []
    for e in raw_events:
        d = e.get("event_date") or e.get("recorded_date")
        if d is None or d >= cutoff:
            kept.append(e)
    return kept


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Richland County SC daily pipeline"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Full rebuild: reset scraper cursor and load all historical raw files",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="With --full, only keep raw events dated within the last N days "
             "(events with no date at all are always kept — see _filter_by_recency)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the scraper only; skip pipeline stages",
    )
    parser.add_argument(
        "--no-approve-review",
        action="store_true",
        default=False,
        help="Halt on NEEDS_OPERATOR_REVIEW instead of auto-approving",
    )
    args = parser.parse_args()

    incremental = not args.full
    approve_review = not args.no_approve_review

    ts_start = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[richland_sc] Pipeline starting — {ts_start}")
    print(f"[richland_sc] Mode: {'full rebuild' if args.full else 'incremental'}")

    # ------------------------------------------------------------------
    # Step 1 — Scrape Columbia Star + Richland delinquent tax
    # ------------------------------------------------------------------
    from scrapers.columbia_star_richland import scrape as scrape_columbia_star, RAW_DIR as CS_RAW_DIR  # noqa: PLC0415
    from scrapers.richland_delinquent_tax import scrape as scrape_delinquent_tax, RAW_DIR as DT_RAW_DIR  # noqa: PLC0415
    from scrapers.richland_register_of_deeds import scrape as scrape_register_of_deeds, RAW_DIR as ROD_RAW_DIR  # noqa: PLC0415

    cs_records = scrape_columbia_star(incremental=incremental)
    print(f"[richland_sc] Columbia Star: {len(cs_records)} new records")

    dt_records = scrape_delinquent_tax(incremental=incremental)
    print(f"[richland_sc] Delinquent tax: {len(dt_records)} new records")

    try:
        rod_records = scrape_register_of_deeds(incremental=incremental)
    except Exception as exc:
        # Non-fatal: the SMS portal is a fragile, unofficial-API third-party
        # ASP.NET WebForms app (see module docstring) — a session/site hiccup
        # here shouldn't take down Columbia Star + delinquent tax, which are
        # this county's primary, more reliable sources.
        print(f"[richland_sc] Register of Deeds scrape failed (non-fatal): {exc}")
        rod_records = []
    print(f"[richland_sc] Register of Deeds: {len(rod_records)} new records")

    new_records = cs_records + dt_records + rod_records
    print(f"[richland_sc] Scrapers returned {len(new_records)} new records total")

    if args.dry_run:
        print("[richland_sc] --dry-run: stopping before pipeline. Done.")
        return

    # ------------------------------------------------------------------
    # Step 2 — Select raw events for this run
    # ------------------------------------------------------------------
    if args.full:
        raw_events = _load_all_raw_events(CS_RAW_DIR, DT_RAW_DIR, ROD_RAW_DIR)
        print(
            f"[richland_sc] Full rebuild: loaded {len(raw_events)} raw events "
            f"from {CS_RAW_DIR}, {DT_RAW_DIR}, and {ROD_RAW_DIR}"
        )
        if args.days is not None:
            before = len(raw_events)
            raw_events = _filter_by_recency(raw_events, args.days)
            print(
                f"[richland_sc] --days {args.days}: kept {len(raw_events)}/{before} "
                f"raw events (dateless events always kept)"
            )
    else:
        raw_events = new_records
        print(f"[richland_sc] Incremental: processing {len(raw_events)} records from this scrape")

    if not raw_events:
        print("[richland_sc] No raw events — pipeline up to date. Exiting.")
        return

    # ------------------------------------------------------------------
    # Step 2b — Confirm estate leads against the official Estate Inquiry
    # portal (case number + estate-opened date), then resolve a parcel for
    # each: first a free owner-name search against the county assessor
    # (no API key), then DealMachine skip-trace for whatever's still
    # unmatched.
    # ------------------------------------------------------------------
    from scrapers.richland_probate_estate_inquiry import (  # noqa: PLC0415
        enrich_estate_raw_events as confirm_estate_raw_events,
    )
    from scrapers.richland_assessor_spatialest import (  # noqa: PLC0415
        enrich_estate_raw_events as assessor_owner_search,
        enrich_lis_pendens_raw_events as assessor_lp_owner_search,
        enrich_foreclosure_raw_events as assessor_fc_owner_search,
        enrich_lien_raw_events as assessor_lien_owner_search,
        repair_broken_parcel_ids,
    )
    from scrapers.richland_skiptrace_dealmachine import enrich_estate_raw_events  # noqa: PLC0415

    raw_events = confirm_estate_raw_events(raw_events)

    # Source articles occasionally misprint a TMS (confirmed live: a
    # one-digit typo that silently blocked a lead's address entirely, since
    # a bad parcel_id makes the enrichment fetch fail with no fallback by
    # design). Verify every already-extracted parcel_id actually resolves
    # before trying to fill in the ones that have no parcel_id at all.
    raw_events = repair_broken_parcel_ids(raw_events)

    estate_unenriched = sum(
        1 for e in raw_events
        if e.get("canonical_doc_type") == "letters_testamentary"
        and not (e.get("property_refs") or {}).get("parcel_id")
    )
    raw_events = assessor_owner_search(raw_events)
    estate_still_unenriched = sum(
        1 for e in raw_events
        if e.get("canonical_doc_type") == "letters_testamentary"
        and not (e.get("property_refs") or {}).get("parcel_id")
    )
    print(
        f"[richland_sc] Assessor owner-name search: "
        f"{estate_unenriched - estate_still_unenriched}/{estate_unenriched} estate events matched to a parcel"
    )

    # Lis pendens defendants often own a house even when the lawsuit itself
    # has nothing to do with real estate (a car-accident judgment, say) —
    # they may still need to sell it to resolve the legal matter, so check
    # regardless of what the notice's own text says.
    lp_unenriched = sum(
        1 for e in raw_events
        if e.get("canonical_doc_type") == "lis_pendens"
        and not (e.get("property_refs") or {}).get("parcel_id")
        and not (e.get("property_refs") or {}).get("situs_address")
    )
    raw_events = assessor_lp_owner_search(raw_events)
    lp_still_unenriched = sum(
        1 for e in raw_events
        if e.get("canonical_doc_type") == "lis_pendens"
        and not (e.get("property_refs") or {}).get("parcel_id")
        and not (e.get("property_refs") or {}).get("situs_address")
    )
    print(
        f"[richland_sc] Assessor owner-name search (lis pendens): "
        f"{lp_unenriched - lp_still_unenriched}/{lp_unenriched} defendants matched to a parcel"
    )

    # Most Master's Sales notices already state the property address
    # directly in the text; this only fires for the minority where all we
    # have is the defendant's name.
    fc_unenriched = sum(
        1 for e in raw_events
        if e.get("canonical_doc_type") == "notice_of_sale"
        and not (e.get("property_refs") or {}).get("parcel_id")
        and not (e.get("property_refs") or {}).get("situs_address")
    )
    raw_events = assessor_fc_owner_search(raw_events)
    fc_still_unenriched = sum(
        1 for e in raw_events
        if e.get("canonical_doc_type") == "notice_of_sale"
        and not (e.get("property_refs") or {}).get("parcel_id")
        and not (e.get("property_refs") or {}).get("situs_address")
    )
    print(
        f"[richland_sc] Assessor owner-name search (foreclosure): "
        f"{fc_unenriched - fc_still_unenriched}/{fc_unenriched} defendants matched to a parcel"
    )

    # Register of Deeds liens (mechanics_lien / federal_tax_lien /
    # state_tax_lien) — a tax lien is filed against the taxpayer, not a
    # specific parcel, so most of these start with no parcel_id at all.
    lien_types = {"mechanics_lien", "federal_tax_lien", "state_tax_lien"}
    lien_unenriched = sum(
        1 for e in raw_events
        if e.get("canonical_doc_type") in lien_types
        and not (e.get("property_refs") or {}).get("parcel_id")
        and not (e.get("property_refs") or {}).get("situs_address")
    )
    raw_events = assessor_lien_owner_search(raw_events)
    lien_still_unenriched = sum(
        1 for e in raw_events
        if e.get("canonical_doc_type") in lien_types
        and not (e.get("property_refs") or {}).get("parcel_id")
        and not (e.get("property_refs") or {}).get("situs_address")
    )
    print(
        f"[richland_sc] Assessor owner-name search (liens): "
        f"{lien_unenriched - lien_still_unenriched}/{lien_unenriched} debtors matched to a parcel"
    )

    raw_events = enrich_estate_raw_events(raw_events)
    estate_newly_enriched = sum(
        1 for e in raw_events
        if e.get("canonical_doc_type") == "letters_testamentary"
        and (e.get("property_refs") or {}).get("_enriched_via") == "dealmachine_skiptrace"
    )
    dm_key_status = "set" if os.environ.get("DEALMACHINE_API_KEY") else "not set"
    print(
        f"[richland_sc] DealMachine skip-trace: "
        f"{estate_newly_enriched}/{estate_still_unenriched} estate events enriched "
        f"(DEALMACHINE_API_KEY {dm_key_status})"
    )

    # ------------------------------------------------------------------
    # Step 3 — Staged pipeline
    # ------------------------------------------------------------------
    from scaffold.pipeline.run_pipeline_staged import (  # noqa: PLC0415
        build_dashboard_payload,
        run_staged_pipeline,
    )
    from scaffold.pipeline.scoring_seam import (  # noqa: PLC0415
        SemanticGateBlocked,
        SemanticGateNeedsReview,
    )
    from scrapers.richland_assessor_spatialest import make_enrichment_provider  # noqa: PLC0415

    enrichment_provider = make_enrichment_provider()
    print("[richland_sc] Spatialest enrichment provider ready")

    print(f"[richland_sc] Running staged pipeline on {len(raw_events)} raw events…")

    try:
        result = run_staged_pipeline(
            raw_events,
            signal_type_labels=SIGNAL_TYPE_LABELS,
            workdir=WORKDIR,
            as_of=date.today(),
            approve_needs_review=approve_review,
            debtor_party_rules=_build_debtor_party_rules(),
            enrichment_provider=enrichment_provider,
        )
    except SemanticGateBlocked as exc:
        print(f"[richland_sc] PIPELINE BLOCKED — §20 semantic gate: {exc}")
        print("[richland_sc] Check pipeline_output/richland_sc/matched_leads.json for details.")
        sys.exit(1)
    except SemanticGateNeedsReview as exc:
        print(f"[richland_sc] §20 gate NEEDS_OPERATOR_REVIEW: {exc}")
        print("[richland_sc] Re-run without --no-approve-review to auto-approve and continue.")
        sys.exit(2)

    scored_leads = result["scored_leads"]
    semantic_verdict = result["semantic_verdict"]

    print(f"[richland_sc] §20 verdict:    {semantic_verdict}")
    print(f"[richland_sc] Scored leads:   {len(scored_leads)}")

    # ------------------------------------------------------------------
    # Step 4 — Dashboard payload
    # ------------------------------------------------------------------
    payload = build_dashboard_payload(
        scored_leads,
        semantic_verdict=semantic_verdict,
        county="Richland",
        state="SC",
        mode="incremental" if incremental else "full_rebuild",
        build_label="PARTIAL_BUILD",
    )

    payload_json = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    dash_path = WORKDIR / "data.json"
    dash_path.write_text(payload_json, encoding="utf-8")

    # Also write to the static dashboard's data directory so opening
    # dashboard/index.html shows live Richland SC leads immediately.
    static_dash_path = ROOT / "dashboard" / "data" / "leads.json"
    static_dash_path.parent.mkdir(parents=True, exist_ok=True)
    static_dash_path.write_text(payload_json, encoding="utf-8")

    print(f"[richland_sc] Dashboard written  → {dash_path}")
    print(f"[richland_sc] Static dashboard   → {static_dash_path}")
    print(f"[richland_sc] Lead total:          {payload['lead_total']}")
    print(f"[richland_sc] Score tiers:         {payload['score_tier_distribution']}")
    print(f"[richland_sc] Patterns:            {payload['pattern_counts']}")

    # ------------------------------------------------------------------
    # Step 5 — Publish to GitHub Pages (malexdurant-bot fork)
    # ------------------------------------------------------------------
    import subprocess  # noqa: PLC0415
    try:
        subprocess.run(
            ["git", "add", str(static_dash_path)],
            cwd=str(ROOT), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m",
             f"data(richland_sc): dashboard update {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"],
            cwd=str(ROOT), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "push", "pages", "main"],
            cwd=str(ROOT), check=True, capture_output=True, timeout=60,
        )
        print("[richland_sc] GitHub Pages updated → https://malexdurant-bot.github.io/xcerebro-county-intel/dashboard/")
    except subprocess.CalledProcessError as exc:
        # Non-fatal — pipeline succeeded even if publish fails
        stderr = exc.stderr.decode(errors="replace") if exc.stderr else ""
        if "nothing to commit" in stderr or "nothing to commit" in (exc.stdout or b"").decode(errors="replace"):
            print("[richland_sc] GitHub Pages: no changes to publish")
        else:
            print(f"[richland_sc] GitHub Pages publish failed (non-fatal): {stderr[:200]}")

    # ------------------------------------------------------------------
    # Step 5b — Publish to the login-gated client dashboard backend
    # (richlandsc.justfriday.ai serves a static, credential-free frontend;
    # the actual lead data is fetched client-side only after the visitor
    # authenticates, from this hosted table — never committed to a repo).
    # ------------------------------------------------------------------
    if LEADS_BACKEND_URL and LEADS_BACKEND_WRITE_KEY:
        try:
            resp = requests.post(
                f"{LEADS_BACKEND_URL}/rest/v1/dashboard_payloads",
                headers={
                    "apikey": LEADS_BACKEND_WRITE_KEY,
                    "Authorization": f"Bearer {LEADS_BACKEND_WRITE_KEY}",
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates",
                },
                json={
                    "county": "richland_sc",
                    "payload": json.loads(payload_json),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                timeout=30,
            )
            resp.raise_for_status()
            print("[richland_sc] Client dashboard data updated → https://richlandsc.justfriday.ai/")
        except requests.RequestException as exc:
            print(f"[richland_sc] Client dashboard publish failed (non-fatal): {exc}")
    else:
        print(
            "[richland_sc] Client dashboard backend not configured "
            "(LEADS_BACKEND_URL / LEADS_BACKEND_WRITE_KEY not set in .env) — "
            "skipping client publish"
        )

    # ------------------------------------------------------------------
    # Step 5c — Push to the client-agent leads API (runs/richland_sc/
    # api_server.py, deployed separately e.g. on Render). That process
    # doesn't share this machine's filesystem, so it can't read data.json
    # itself — we push it the finished payload after every run instead.
    # ------------------------------------------------------------------
    if RICHLAND_AGENT_API_URL and RICHLAND_AGENT_API_INGEST_KEY:
        try:
            resp = requests.post(
                f"{RICHLAND_AGENT_API_URL}/richland/ingest",
                headers={
                    "X-Ingest-Key": RICHLAND_AGENT_API_INGEST_KEY,
                    "Content-Type": "application/json",
                },
                data=payload_json.encode("utf-8"),
                timeout=30,
            )
            resp.raise_for_status()
            print(f"[richland_sc] Agent API updated → {RICHLAND_AGENT_API_URL}/richland/leads/new")
        except requests.RequestException as exc:
            print(f"[richland_sc] Agent API push failed (non-fatal): {exc}")
    else:
        print(
            "[richland_sc] Agent API not configured "
            "(RICHLAND_AGENT_API_URL / RICHLAND_AGENT_API_INGEST_KEY not set in .env) — "
            "skipping agent API push"
        )

    # ------------------------------------------------------------------
    # Step 5d — Push to the richlandsc.justfriday.ai dashboard's own repo
    # (malexdurant-bot/richland-sc-leads, a separate GitHub Pages site on a
    # custom domain — isolated from this repo so the client never sees our
    # scraper source). That repo's data/leads.json was hand-published once
    # on 2026-08-24 and never updated again — the LEADS_BACKEND_URL/
    # LEADS_BACKEND_WRITE_KEY Supabase-REST design above was a different,
    # never-provisioned approach; confirmed live 2026-09-04 (via the site's
    # own network requests) that the deployed frontend actually just fetches
    # a static data/leads.json from its own domain, identical in shape to
    # payload_json. Uses the GitHub Contents API via `gh api` (already
    # authenticated on this machine) rather than a local git clone, since
    # this repo has no working tree for richland-sc-leads. Fetch-then-PUT
    # (not a plain git push) because the Contents API requires the
    # existing file's blob sha to update it.
    # ------------------------------------------------------------------
    RICHLAND_SC_LEADS_REPO = "malexdurant-bot/richland-sc-leads"
    RICHLAND_SC_LEADS_PATH = "data/leads.json"
    try:
        import base64  # noqa: PLC0415
        import tempfile  # noqa: PLC0415

        sha_result = subprocess.run(
            ["gh", "api", f"repos/{RICHLAND_SC_LEADS_REPO}/contents/{RICHLAND_SC_LEADS_PATH}",
             "--jq", ".sha"],
            capture_output=True, text=True, check=True, timeout=30,
        )
        current_sha = sha_result.stdout.strip()

        body = {
            "message": f"data(richland_sc): dashboard update {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            "content": base64.b64encode(payload_json.encode("utf-8")).decode("ascii"),
            "sha": current_sha,
            "branch": "main",
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(body, f)
            tmp_path = f.name
        try:
            subprocess.run(
                ["gh", "api", f"repos/{RICHLAND_SC_LEADS_REPO}/contents/{RICHLAND_SC_LEADS_PATH}",
                 "-X", "PUT", "--input", tmp_path],
                check=True, capture_output=True, timeout=60,
            )
        finally:
            os.unlink(tmp_path)
        print("[richland_sc] richlandsc.justfriday.ai updated → https://richlandsc.justfriday.ai/")
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        print(f"[richland_sc] richlandsc.justfriday.ai publish failed (non-fatal): {stderr[:300]}")
    except Exception as exc:  # noqa: BLE001 - best-effort publish, never block the pipeline
        print(f"[richland_sc] richlandsc.justfriday.ai publish failed (non-fatal): {exc}")

    print(
        f"[richland_sc] Pipeline complete — "
        f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    )


if __name__ == "__main__":
    main()
