#!/usr/bin/env python3
"""v5.5.0 §3.9 invariants — scheduled-event classifier.

Pins the §3.9 categories: UPCOMING_SALE / PAST_SALE / POST_SALE_TITLE_EVENT /
SURPLUS_EVENT / HISTORICAL_CONTEXT_ONLY. Confirms status-based distress
types do NOT pass through this classifier.

Run: python3 scaffold/tests/v5_5_0/test_scheduled_event_classifier.py
Exit 0 = pass, non-zero = fail.
"""
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scaffold.pipeline import scheduled_event_classifier as sec


AS_OF = date(2026, 5, 14)


def main() -> int:
    checks: list = []

    def check(label, ok, detail=""):
        checks.append(("PASS" if ok else "FAIL", label, detail))

    # =====================================================================
    # UPCOMING_SALE — future sale date, scheduled-event doc type
    # =====================================================================
    r = sec.classify_scheduled_event(
        {"canonical_doc_type": "notice_of_sale", "sale_date": "2026-07-01"},
        as_of=AS_OF,
    )
    check("§3.9 UPCOMING_SALE: notice_of_sale 7 weeks in future → UPCOMING_SALE",
          r.category == "UPCOMING_SALE" and r.is_lead_originating is True,
          f"got category={r.category!r}")

    r = sec.classify_scheduled_event(
        {"canonical_doc_type": "sheriff_sale", "sale_date": "2026-05-15"},
        as_of=AS_OF,
    )
    check("§3.9 UPCOMING_SALE: sheriff_sale tomorrow → UPCOMING_SALE",
          r.category == "UPCOMING_SALE")

    # Sale date == as_of → still UPCOMING (sale not yet executed).
    r = sec.classify_scheduled_event(
        {"canonical_doc_type": "notice_of_sale", "sale_date": "2026-05-14"},
        as_of=AS_OF,
    )
    check("§3.9 UPCOMING_SALE: sale_date == as_of is UPCOMING (the sale "
          "hasn't been executed yet)",
          r.category == "UPCOMING_SALE")

    # Beyond horizon → still UPCOMING but flagged in notes.
    r = sec.classify_scheduled_event(
        {"canonical_doc_type": "notice_of_sale", "sale_date": "2027-01-01"},
        as_of=AS_OF, forward_horizon_days=90,
    )
    check("§3.9 UPCOMING_SALE: beyond 90-day horizon still UPCOMING; "
          "classifier records horizon note, daily refresh's pull-window "
          "enforces the cutoff (per §6.6)",
          r.category == "UPCOMING_SALE" and bool(r.notes))

    # =====================================================================
    # PAST_SALE — past sale date, no current distress context
    # =====================================================================
    r = sec.classify_scheduled_event(
        {"canonical_doc_type": "notice_of_sale", "sale_date": "2025-12-15"},
        as_of=AS_OF,
    )
    check("§3.9 PAST_SALE: notice_of_sale dated 6 months ago → PAST_SALE, "
          "NOT lead-originating",
          r.category == "PAST_SALE" and r.is_lead_originating is False)

    # =====================================================================
    # HISTORICAL_CONTEXT_ONLY — past sale + current distress on property
    # =====================================================================
    r = sec.classify_scheduled_event(
        {"canonical_doc_type": "notice_of_sale", "sale_date": "2024-04-01"},
        as_of=AS_OF,
        has_current_distress_on_property=True,
    )
    check("§3.9 HISTORICAL_CONTEXT_ONLY: past sale + current distress on "
          "property → HISTORICAL_CONTEXT_ONLY (attaches as prior_distress "
          "recurrence signal, never its own lead)",
          r.category == "HISTORICAL_CONTEXT_ONLY"
          and r.is_lead_originating is False)

    # =====================================================================
    # POST_SALE_TITLE_EVENT — concluded foreclosure cycle
    # =====================================================================
    for cdt in ("certificate_of_title", "sheriff_deed",
                "trustees_deed_upon_sale", "tax_deed"):
        r = sec.classify_scheduled_event(
            {"canonical_doc_type": cdt, "sale_date": "2026-04-30"},
            as_of=AS_OF,
        )
        check(f"§3.9 POST_SALE_TITLE_EVENT: {cdt} → POST_SALE_TITLE_EVENT, "
              "lead-originating (the former owner just lost the property)",
              r.category == "POST_SALE_TITLE_EVENT"
              and r.is_lead_originating is True)

    # =====================================================================
    # SURPLUS_EVENT — sheriff_sale_surplus
    # =====================================================================
    r = sec.classify_scheduled_event(
        {"canonical_doc_type": "sheriff_sale_surplus",
         "sale_date": "2026-04-15"},
        as_of=AS_OF,
    )
    check("§3.9 SURPLUS_EVENT: sheriff_sale_surplus → SURPLUS_EVENT, "
          "lead-originating (former owner entitled to excess proceeds)",
          r.category == "SURPLUS_EVENT" and r.is_lead_originating is True)

    # =====================================================================
    # Status-based distress — NOT a scheduled event, classifier rejects
    # =====================================================================
    for cdt in ("tax_default", "tax_delinquency", "code_violation_notice",
                "municipal_lien", "federal_tax_lien", "judgment_lien"):
        r = sec.classify_scheduled_event(
            {"canonical_doc_type": cdt}, as_of=AS_OF,
        )
        check(f"§3.9 status-based {cdt!r} → HISTORICAL_CONTEXT_ONLY "
              "(not lead-originating through this classifier — status-based "
              "distress uses the §3.3 / status-condition gate, not §3.9)",
              r.category == "HISTORICAL_CONTEXT_ONLY"
              and r.is_lead_originating is False)

    # =====================================================================
    # Missing sale_date — scheduled event becomes unclassifiable
    # =====================================================================
    r = sec.classify_scheduled_event(
        {"canonical_doc_type": "notice_of_sale"}, as_of=AS_OF,
    )
    check("§3.9 missing sale_date: notice_of_sale with no sale_date → "
          "HISTORICAL_CONTEXT_ONLY + notes flag (cannot classify; adapter "
          "should populate or REVIEW_REQUIRED)",
          r.category == "HISTORICAL_CONTEXT_ONLY"
          and r.is_lead_originating is False
          and "sale_date" in (r.notes[0] if r.notes else ""))

    # =====================================================================
    # Canonical-type contract
    # =====================================================================
    for cat in ("UPCOMING_SALE", "PAST_SALE", "POST_SALE_TITLE_EVENT",
                "SURPLUS_EVENT", "HISTORICAL_CONTEXT_ONLY"):
        check(f"§3.9 canonical category {cat!r} declared",
              cat in sec.SCHEDULED_EVENT_CATEGORIES)

    # --- Report ----------------------------------------------------------
    failed = [c for c in checks if c[0] == "FAIL"]
    for s, l, d in checks:
        print(f"  [{s}] {l}")
        if s == "FAIL" and d:
            print(f"         detail: {d}")
    if failed:
        print(f"FAIL: scheduled-event classifier — {len(failed)}/"
              f"{len(checks)} checks failed")
        return 1
    print(f"PASS: §3.9 scheduled-event classifier (v5.5.0) — "
          f"all {len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
