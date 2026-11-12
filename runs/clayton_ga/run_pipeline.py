"""
Clayton County, GA — daily pipeline runner (PARTIAL_BUILD).

Primary event sources built (config/counties/clayton_ga.json):
  - magistrate_court_civil_evictions  scrapers/clayton_case_inquiry.py   -> eviction_filing
  - state_court_civil_judgments       scrapers/clayton_case_inquiry.py   -> judgment_lien
  - probate_estates                   scrapers/georgia_probate_records.py -> letters_* / determination_of_heirship
  - tax_sale_listings                 scrapers/clayton_tax_sale_pdf.py   -> tax_foreclosure_notice
  - excess_funds_listing              scrapers/clayton_tax_sale_pdf.py   -> sheriff_sale_surplus

Enrichment only (never creates a lead):
  - Clayton GIS Tax Parcels layer     scrapers/clayton_parcel_index.py
        bulk owner / mailing / value index; also resolves parcel_id for
        address-only events (evictions, judgments, probate).

NOT built — blocked pending operator action (see config `blocker` fields):
  - gsccca_clerk_indexes            paid GSCCCA subscription (lis pendens,
                                    deeds under power, FiFa / tax / mechanics liens)
  - ga_public_notice_foreclosures   reCAPTCHA + no-scraping terms (foreclosure
                                    sale notices, sheriff sales, tax-deed redemption)
  - federal_bankruptcy_ndga         PACER account
  - superior_court_civil            case-number format unconfirmed (name search only)

Publishing is NOT automatic: this runner writes pipeline_output/clayton_ga/ and
dashboard/clayton_ga/data/leads.json locally. It does not commit, push, or
call any client backend.

Usage:
  python runs/clayton_ga/run_pipeline.py              # incremental scrape + full republish
  python runs/clayton_ga/run_pipeline.py --full       # re-scrape ignoring cursors
  python runs/clayton_ga/run_pipeline.py --no-scrape  # rebuild from archived raw events only
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

WORKDIR = ROOT / "runs" / "clayton_ga" / "pipeline_output"
WORKDIR.mkdir(parents=True, exist_ok=True)
DASH_DATA = ROOT / "dashboard" / "clayton_ga" / "data" / "leads.json"

RAW_GLOBS = [
    ("data/raw/clayton_tax_sale", "clayton_tax_sale_*.jsonl"),
    ("data/raw/clayton_case_inquiry", "clayton_cases_*.jsonl"),
    ("data/raw/clayton_probate", "clayton_probate_*.jsonl"),
]

RECENCY_WINDOW_DAYS = 30

SIGNAL_TYPE_LABELS = {
    "eviction_filing": "Eviction (Dispossessory)",
    "judgment_lien": "Civil Judgment",
    "letters_of_administration": "Probate — Administration",
    "letters_testamentary": "Probate — Will",
    "determination_of_heirship": "Probate — No Admin / Year's Support",
    "tax_foreclosure_notice": "Tax Sale",
    "sheriff_sale_surplus": "Tax Sale Excess Funds",
}

BUILD_LABEL = "PARTIAL_BUILD"
BUILD_LABEL_REASON = (
    "Active: Magistrate evictions, State Court judgments, Probate estates, Tax sale list, Excess funds. "
    "Blocked pending operator action: foreclosure / sheriff sale notices (GeorgiaPublicNotice reCAPTCHA), "
    "lis pendens & recorded liens (GSCCCA subscription), bankruptcy (PACER). "
    "Enrichment: Clayton GIS parcel layer. This board does NOT include pre-foreclosures."
)


def load_raw_events() -> list[dict]:
    by_id: dict[str, dict] = {}
    for rel, pat in RAW_GLOBS:
        for p in sorted((ROOT / rel).glob(pat)):
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    e = json.loads(line)
                    by_id[e["raw_event_id"]] = e   # later files win (re-scrapes)
    return list(by_id.values())


def tag_recency(records: list[dict]) -> dict[str, int]:
    cutoff = (date.today() - timedelta(days=RECENCY_WINDOW_DAYS)).isoformat()
    counts = {"Active": 0, "Aged": 0}
    for r in records:
        d = r.get("primary_event_date")
        b = "Active" if not d or d >= cutoff else "Aged"
        r["display_recency_bucket"] = b
        counts[b] += 1
    return counts


_MAILING_KEYS = ("display_owner_mailing", "display_owner_mailing_address", "display_owner_mailing_city",
                 "display_owner_mailing_state", "display_owner_mailing_zip")


def guard_owner_attribution(records: list[dict]) -> int:
    """Withhold parcel mailing data when the parcel's owner of record is not the lead's owner.

    The enrichment provider is keyed on parcel only, so its mailing address is
    the CURRENT owner's. For excess-funds leads (former owner after a tax sale)
    and probate leads where title isn't in the decedent's name, that address
    belongs to someone else — showing it would send a caller to the wrong
    person. The row keeps its primary signal; the mailing block is cleared and
    the row is flagged for review with the current owner of record shown.
    """
    from scrapers.clayton_parcel_index import get_index, names_match
    idx, n = get_index(), 0
    for r in records:
        par = idx.get(r.get("primary_parcel_id"))
        if not par or names_match(par.get("OWNERNME"), r.get("display_owner")):
            continue
        for k in _MAILING_KEYS:
            if k in r:
                r[k] = None
        r["display_is_absentee_owner"] = None
        r["display_is_out_of_state_owner"] = None
        r["display_current_owner_of_record"] = (par.get("OWNERNME") or "").strip() or None
        flags = list(r.get("review_flags") or [])
        flags.append("parcel_owner_differs_from_lead_owner")
        r["review_flags"] = flags
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Clayton County GA pipeline")
    ap.add_argument("--full", action="store_true", help="ignore scraper cursors")
    ap.add_argument("--no-scrape", action="store_true", help="skip scraping; rebuild from raw archive")
    ap.add_argument("--skip-courts", action="store_true", help="skip the (slow, rate-limited) case inquiry walk")
    ap.add_argument("--no-approve-review", action="store_true",
                    help="halt on §20 NEEDS_OPERATOR_REVIEW instead of continuing")
    args = ap.parse_args()
    incremental = not args.full
    print(f"[clayton_ga] pipeline start {datetime.now(timezone.utc).isoformat(timespec='seconds')}")

    # Step 1 — scrape (each scraper is non-fatal to the others)
    if not args.no_scrape:
        from scrapers import clayton_tax_sale_pdf, georgia_probate_records, clayton_case_inquiry
        steps = [("tax sale / excess funds", lambda: clayton_tax_sale_pdf.scrape(incremental=incremental)),
                 ("probate", lambda: georgia_probate_records.scrape(incremental=incremental))]
        if not args.skip_courts:
            steps.append(("court cases", lambda: clayton_case_inquiry.scrape(incremental=incremental)))
        for name, fn in steps:
            try:
                print(f"[clayton_ga] {name}: {len(fn())} new events")
            except Exception as exc:  # noqa: BLE001
                print(f"[clayton_ga] {name} scrape FAILED (non-fatal): {exc}")

    # Step 2 — full archive (every run republishes everything; see Richland note)
    raw_events = load_raw_events()
    print(f"[clayton_ga] {len(raw_events)} raw events in archive")
    if not raw_events:
        print("[clayton_ga] nothing to publish")
        return 0

    # Step 3 — resolve parcels for address-only events (enrichment, never origination)
    from scrapers.clayton_parcel_index import attach_parcels, make_enrichment_provider
    from scrapers.clayton_case_inquiry import event_is_institutional
    for e in raw_events:
        if e.get("canonical_doc_type") == "eviction_filing" and event_is_institutional(e):
            e["_drop_reason"] = "institutional landlord / multi-unit address"
    stats = attach_parcels([e for e in raw_events if not e.get("_drop_reason")])
    for dt, s in stats.items():
        if s["needed"]:
            print(f"[clayton_ga] parcel match {dt}: {s['matched']}/{s['needed']}"
                  + (f" ({s['rejected_owner_mismatch']} renter/owner-mismatch rejected)"
                     if s["rejected_owner_mismatch"] else ""))
    dropped = [e for e in raw_events if e.get("_drop_reason")]
    if dropped:
        (WORKDIR / "dropped_events.json").write_text(json.dumps(
            [{"raw_event_id": e["raw_event_id"], "case": e.get("instrument_number"), "reason": e["_drop_reason"]}
             for e in dropped], indent=2), encoding="utf-8")
        raw_events = [e for e in raw_events if not e.get("_drop_reason")]
        print(f"[clayton_ga] dropped {len(dropped)} eviction events — institutional landlord or "
              f"plaintiff not owner of record (see pipeline_output/dropped_events.json)")

    # Step 4 — staged pipeline §17 → §18 → §19 → §20 → seam → scoring
    from scaffold.pipeline.run_pipeline_staged import build_dashboard_payload, run_staged_pipeline
    from scaffold.pipeline.scoring_seam import SemanticGateBlocked, SemanticGateNeedsReview
    try:
        result = run_staged_pipeline(
            raw_events,
            signal_type_labels=SIGNAL_TYPE_LABELS,
            workdir=WORKDIR,
            as_of=date.today(),
            approve_needs_review=not args.no_approve_review,
            enrichment_provider=make_enrichment_provider(),
        )
    except SemanticGateBlocked as exc:
        print(f"[clayton_ga] PIPELINE BLOCKED — §20 semantic gate: {exc}")
        return 1
    except SemanticGateNeedsReview as exc:
        print(f"[clayton_ga] §20 NEEDS_OPERATOR_REVIEW: {exc}")
        return 2

    scored = result["scored_leads"]
    print(f"[clayton_ga] §20 verdict: {result['semantic_verdict']}  scored leads: {len(scored)}")

    # Step 5 — dashboard payload (local only)
    payload = build_dashboard_payload(
        scored, semantic_verdict=result["semantic_verdict"], county="Clayton", state="GA",
        mode="incremental" if incremental else "full_rebuild", build_label=BUILD_LABEL)
    payload["build_label_reason"] = BUILD_LABEL_REASON
    guarded = guard_owner_attribution(payload["records"])
    print(f"[clayton_ga] owner-attribution guard: mailing withheld on {guarded} rows "
          f"(parcel owner of record differs from lead owner)")
    payload["recency_distribution"] = tag_recency(payload["records"])
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    (WORKDIR / "data.json").write_text(text, encoding="utf-8")
    DASH_DATA.parent.mkdir(parents=True, exist_ok=True)
    DASH_DATA.write_text(text, encoding="utf-8")
    print(f"[clayton_ga] lead_total={payload['lead_total']}  tiers={payload['score_tier_distribution']}")
    print(f"[clayton_ga] patterns={payload['pattern_counts']}  enrichment={payload.get('enrichment_breakdown')}")
    print(f"[clayton_ga] dashboard data -> {DASH_DATA}")
    print("[clayton_ga] not published (no commit/push) — publishing needs operator approval")
    return 0


if __name__ == "__main__":
    sys.exit(main())
