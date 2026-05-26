"""
semantic_verify — v5.4.0 staged pipeline, stage 5 (the §20 semantic gate).

STATUS: IMPLEMENTED in v5.4.0 Session 5. This module is the §20 semantic
verification engine — the pre-dashboard deploy gate that runs AFTER mechanical
verification (§20.G) and confirms the pipeline's output is *meaningful*, not
merely well-formed.

Contract: knowledge_base/architecture/20_semantic_verification_contract.md.

§20.C defines twelve check classes. Each check returns one of the §20.D outcome
states — VALID, INVALID, AMBIGUOUS — or SKIPPED when the check's deploy-time
inputs are not available to this pre-dashboard run. The overall verdict (§20.F):

  - DEPLOY_OK             — every executed check is VALID.
  - DEPLOY_BLOCKED        — any executed check is INVALID.
  - NEEDS_OPERATOR_REVIEW — at least one AMBIGUOUS, none INVALID.

Six checks run on the staged pipeline's own artifacts (matched_leads.json plus
the leads-base records and evidence ledger): Check 1 (debtor attribution),
Check 2 (owner-type classification), Check 4 (enrichment-decoupling integrity
and the §13.5 No-False-Dashboard row-provenance rule), Check 5 (signal
aggregation), Check 6 (cross-source aggregation), Check 12 (universal
filer-as-owner scan). Six checks need deploy-time inputs not present before the
dashboard renders — Check 3 (parcel-master join data), Check 7 (OCR confidence),
Check 8 (CSV export), Check 9 (live source-URL resolution), Check 10 (browser),
Check 11 (build report) — and report SKIPPED here; a county verifier supplies
those inputs at deploy time (§20.H).

Check 5 — count vs distinct instrument numbers: the §19 aggregator's `count` is
the number of distinct events (distinct non-null instruments PLUS each
null-instrument record), so `count` legitimately exceeds `len(instrument_numbers)`
when records carry null instruments. Per §20.D this is AMBIGUOUS — null
instruments (legitimate) or a dedup failure (a bug) — and routes to operator
review, NOT INVALID. `count < len(instrument_numbers)` is impossible and is
INVALID.

This module is universal framework code: the twelve check classes, the
three-state model, and the deploy verdicts are universal; per-county sample
sizes and the deploy-time check implementations are county-scoped (§20.J). No
county / state / vendor literal appears here.
"""

from __future__ import annotations

import json
from typing import Optional

from jsonschema import Draft202012Validator

from scaffold.pipeline.contracts import schema_path
from scaffold.pipeline.debtor_party_engine import classify_owner_type, match_known_filer

CHECK_STATES = ("VALID", "INVALID", "AMBIGUOUS", "SKIPPED")
"""§20.D outcome states, plus SKIPPED for a check whose deploy-time inputs are
absent (a reporting state — not a §20.D outcome)."""

DEPLOY_VERDICTS = ("DEPLOY_OK", "DEPLOY_BLOCKED", "NEEDS_OPERATOR_REVIEW")
"""§20.F overall deploy verdicts."""

_VALIDATOR_CACHE: dict[str, Draft202012Validator] = {}


def _matched_lead_validator() -> Draft202012Validator:
    if "v" not in _VALIDATOR_CACHE:
        schema = json.loads(
            schema_path("matched_lead_record").read_text(encoding="utf-8")
        )
        _VALIDATOR_CACHE["v"] = Draft202012Validator(schema)
    return _VALIDATOR_CACHE["v"]


def _result(check: int, name: str, status: str, detail: str,
            samples: Optional[list] = None) -> dict:
    """One §20.C check result."""
    if status not in CHECK_STATES:
        raise ValueError(f"_result: invalid status {status!r}")
    return {
        "check": check,
        "name": name,
        "status": status,
        "detail": detail,
        "samples": list(samples or []),
    }


# ---------------------------------------------------------------------------
# §20.C checks that run on the staged pipeline's own artifacts.
# ---------------------------------------------------------------------------

def _check_1_debtor_attribution(matched_leads: list, ctx: dict) -> dict:
    """Check 1 — owner_name is the debtor, never a known filer (§17 / §20.C)."""
    offenders = []
    for lead in matched_leads:
        if lead.get("parcel_resolution_status") == "REVIEW_REQUIRED":
            # owner_name is the §17.E placeholder, not a real party name.
            continue
        owner = lead.get("owner_name") or ""
        hit = match_known_filer(owner)
        if hit:
            offenders.append({
                "lead_id": lead.get("lead_id"),
                "owner_name": owner,
                "filer_pattern": hit,
            })
    if offenders:
        return _result(
            1, "Debtor attribution sampling", "INVALID",
            f"{len(offenders)} matched lead(s) carry a known filer pattern as "
            f"owner_name — a filer-as-owner inversion (§17).", offenders)
    return _result(
        1, "Debtor attribution sampling", "VALID",
        f"full scan of {len(matched_leads)} matched leads: no resolved "
        f"owner_name matches a known filer pattern.")


def _check_2_owner_type(matched_leads: list, ctx: dict) -> dict:
    """Check 2 — owner_type matches the §17.F classifier for owner_name."""
    offenders = []
    checked = 0
    for lead in matched_leads:
        if lead.get("parcel_resolution_status") == "REVIEW_REQUIRED":
            continue
        owner = lead.get("owner_name") or ""
        declared = lead.get("owner_type")
        rederived = classify_owner_type(owner)
        checked += 1
        if rederived != declared:
            offenders.append({
                "lead_id": lead.get("lead_id"),
                "owner_name": owner,
                "declared_owner_type": declared,
                "reclassified_owner_type": rederived,
            })
    if offenders:
        return _result(
            2, "Owner type classification sampling", "INVALID",
            f"{len(offenders)} matched lead(s) have an owner_type that "
            f"disagrees with the §17.F classifier (substring false positive "
            f"or misclassification).", offenders)
    return _result(
        2, "Owner type classification sampling", "VALID",
        f"full scan of {checked} resolved matched leads: owner_type agrees "
        f"with the §17.F classifier.")


def _check_3_parcel_plausibility(matched_leads: list, ctx: dict) -> dict:
    """Check 3 — parcel-resolution plausibility (deploy-time)."""
    return _result(
        3, "Parcel-resolution plausibility", "SKIPPED",
        "requires parcel-master join data and situs-address join keys — a "
        "county deploy-time check (§20.H); not present on matched_leads.json.")


def _check_4_enrichment_decoupling(matched_leads: list, ctx: dict) -> dict:
    """Check 4 — §13.14 enrichment-status decoupling + §13.5 No False Dashboard.

    (a) No invalid (parcel_resolution_status, enrichment_status) pair —
        ENRICHED requires a RESOLVED parcel (§13.14.1).
    (b) No False Dashboard: every matched lead carries at least one signal
        from a PRIMARY_EVENT_SOURCE — an enrichment-only row (no primary lead
        event) is a fabricated dashboard row (§13.5). Runs when leads-base
        source roles are available.
    """
    bad_combo = []
    for lead in matched_leads:
        prs = lead.get("parcel_resolution_status")
        ens = lead.get("enrichment_status")
        if ens == "ENRICHED" and prs != "RESOLVED":
            bad_combo.append({
                "lead_id": lead.get("lead_id"),
                "parcel_resolution_status": prs,
                "enrichment_status": ens,
            })

    enrichment_only = []
    role_by_id = ctx.get("source_role_by_id") or {}
    provenance_checked = bool(role_by_id)
    if provenance_checked:
        for lead in matched_leads:
            source_ids = lead.get("source_ids") or []
            has_primary = any(
                role_by_id.get(sid) == "PRIMARY_EVENT_SOURCE"
                for sid in source_ids
            )
            if not has_primary:
                enrichment_only.append({
                    "lead_id": lead.get("lead_id"),
                    "source_ids": list(source_ids),
                })

    offenders = bad_combo + enrichment_only
    if offenders:
        parts = []
        if bad_combo:
            parts.append(f"{len(bad_combo)} ENRICHED row(s) without a RESOLVED "
                         f"parcel (§13.14.1)")
        if enrichment_only:
            parts.append(f"{len(enrichment_only)} enrichment-only row(s) with "
                         f"no PRIMARY_EVENT_SOURCE signal (No False Dashboard, "
                         f"§13.5)")
        return _result(4, "Enrichment status decoupling integrity", "INVALID",
                       "; ".join(parts) + ".", offenders)
    detail = ("enrichment-status pairs valid (§13.14.1)")
    if provenance_checked:
        detail += "; every matched lead carries a PRIMARY_EVENT_SOURCE signal"
    else:
        detail += "; row-provenance (No False Dashboard) not checked — no "
        detail += "leads-base source roles supplied"
    return _result(4, "Enrichment status decoupling integrity", "VALID",
                   detail + ".")


def _check_5_signal_aggregation(matched_leads: list, ctx: dict) -> dict:
    """Check 5 — signal count vs distinct instrument numbers (§18.E)."""
    invalid, ambiguous = [], []
    signal_count = 0
    for lead in matched_leads:
        for sig in lead.get("signals") or []:
            signal_count += 1
            count = sig.get("count")
            n_instruments = len(sig.get("instrument_numbers") or [])
            sample = {
                "lead_id": lead.get("lead_id"),
                "signal_type": sig.get("signal_type"),
                "count": count,
                "distinct_instrument_numbers": n_instruments,
            }
            if not isinstance(count, int) or count < n_instruments:
                invalid.append(sample)
            elif count > n_instruments:
                ambiguous.append(sample)
    if invalid:
        return _result(
            5, "Signal aggregation integrity", "INVALID",
            f"{len(invalid)} signal(s) have count below the distinct "
            f"instrument-number count — impossible; an aggregation bug.",
            invalid)
    if ambiguous:
        return _result(
            5, "Signal aggregation integrity", "AMBIGUOUS",
            f"{len(ambiguous)} signal(s) have count above the distinct "
            f"instrument-number count — null-instrument records (legitimate "
            f"§18.E) or a dedup failure (a bug); routed to operator review.",
            ambiguous)
    return _result(
        5, "Signal aggregation integrity", "VALID",
        f"full scan of {signal_count} signal(s): count equals the distinct "
        f"instrument-number count (clean §18.E stacking).")


def _check_6_cross_source(matched_leads: list, ctx: dict) -> dict:
    """Check 6 — cross-source aggregation and §18.F anti-collapse integrity."""
    offenders = []
    signal_count = 0
    for lead in matched_leads:
        seen_keys = set()
        for sig in lead.get("signals") or []:
            signal_count += 1
            key = sig.get("aggregation_key") or {}
            tup = (key.get("parcel_id"), key.get("canonical_doc_type"),
                   key.get("signal_type"))
            if tup in seen_keys:
                offenders.append({
                    "lead_id": lead.get("lead_id"),
                    "issue": "two signals share one aggregation key (under-merge)",
                    "aggregation_key": key,
                })
            seen_keys.add(tup)
            if (sig.get("canonical_doc_type") != key.get("canonical_doc_type")
                    or sig.get("signal_type") != key.get("signal_type")):
                offenders.append({
                    "lead_id": lead.get("lead_id"),
                    "issue": "signal fields disagree with its aggregation key",
                    "aggregation_key": key,
                    "signal_type": sig.get("signal_type"),
                    "canonical_doc_type": sig.get("canonical_doc_type"),
                })
    if offenders:
        return _result(
            6, "Cross-source aggregation integrity", "INVALID",
            f"{len(offenders)} signal-grouping inconsistency(ies) — an "
            f"under-merge or a key/signal mismatch (§18.D / §18.F).", offenders)
    return _result(
        6, "Cross-source aggregation integrity", "VALID",
        f"full scan of {signal_count} signal(s): every signal has a distinct, "
        f"self-consistent aggregation key (§18.F anti-collapse holds).")


def _check_7_ocr_routing(matched_leads: list, ctx: dict) -> dict:
    """Check 7 — OCR confidence routing (source-ingestion-time)."""
    return _result(
        7, "OCR confidence routing", "SKIPPED",
        "requires per-record OCR confidence scores — source-ingestion-time "
        "data not carried on matched_leads.json (§20.H).")


def _check_8_csv_schema(matched_leads: list, ctx: dict) -> dict:
    """Check 8 — CSV output schema validation (deploy-time)."""
    return _result(
        8, "CSV output schema validation", "SKIPPED",
        "requires the operator-facing CSV export — a deploy-time artifact "
        "(§20.H).")


def _check_9_source_links(matched_leads: list, ctx: dict) -> dict:
    """Check 9 — source proof link validation (deploy-time)."""
    return _result(
        9, "Source proof link validation", "SKIPPED",
        "requires live source-URL resolution (HTTP / offline-path checks) — a "
        "deploy-time check (§20.H).")


def _check_10_dashboard_rows(matched_leads: list, ctx: dict) -> dict:
    """Check 10 — dashboard row integrity (deploy-time, browser)."""
    return _result(
        10, "Dashboard row integrity", "SKIPPED",
        "requires browser automation against the rendered dashboard — a "
        "deploy-time check (§20.H).")


def _check_11_methodology(matched_leads: list, ctx: dict) -> dict:
    """Check 11 — methodology consistency (deploy-time)."""
    return _result(
        11, "Methodology consistency", "SKIPPED",
        "requires the build report — a deploy-time artifact (§20.H).")


def _check_12_universal_filer_scan(matched_leads: list, ctx: dict) -> dict:
    """Check 12 — universal filer-as-owner scan of matched_leads.json."""
    offenders = []
    for lead in matched_leads:
        owner = lead.get("owner_name") or ""
        hit = match_known_filer(owner)
        if hit:
            offenders.append({
                "lead_id": lead.get("lead_id"),
                "owner_name": owner,
                "filer_pattern": hit,
                "parcel_resolution_status": lead.get("parcel_resolution_status"),
            })
    if offenders:
        return _result(
            12, "Filer-as-owner spot check (universal patterns)", "INVALID",
            f"{len(offenders)} matched lead(s) emit a universal filer pattern "
            f"(government / IRS / hospital / mortgage / federal agency) as "
            f"owner_name — it may appear only as filer_entity.", offenders)
    return _result(
        12, "Filer-as-owner spot check (universal patterns)", "VALID",
        f"full scan of {len(matched_leads)} matched leads: no universal filer "
        f"pattern appears as owner_name.")


_CHECKS = (
    _check_1_debtor_attribution,
    _check_2_owner_type,
    _check_3_parcel_plausibility,
    _check_4_enrichment_decoupling,
    _check_5_signal_aggregation,
    _check_6_cross_source,
    _check_7_ocr_routing,
    _check_8_csv_schema,
    _check_9_source_links,
    _check_10_dashboard_rows,
    _check_11_methodology,
    _check_12_universal_filer_scan,
)


# ---------------------------------------------------------------------------
# v5.5.0 §4.1 / §4.2 / §4.3 / §4.6 — additional pre-publish semantic checks
# that run on the SCORED_LEADS layer (the data that actually reaches the
# dashboard). The §20 checks 1-12 above run on matched_leads.json (§19 output);
# the v5.5.0 checks need scored_leads + dashboard payload context so they live
# in their own check pass invoked after scoring (or skipped when scored_leads
# isn't supplied at call time).
# ---------------------------------------------------------------------------

def _check_13_tax_default_qualification(scored_leads: list, ctx: dict) -> dict:
    """v5.5.0 §4.1 — every scored_lead with lead_origin_type TAX_DEFAULT
    MUST carry qualification_status QUALIFIED and the §3.3 5-criteria
    evidence. A row whose lead_origin_type claims TAX_DEFAULT but the
    qualification gate did not bless is a §3.3 violation."""
    if scored_leads is None:
        return _result(
            13, "Tax-default qualification (v5.5.0 §4.1)", "SKIPPED",
            "no scored_leads supplied — check runs only when scoring stage "
            "results are available.")
    offenders = []
    seen = 0
    for sl in scored_leads:
        if sl.get("lead_origin_type") != "TAX_DEFAULT":
            continue
        seen += 1
        qs = sl.get("qualification_status")
        if qs != "QUALIFIED":
            offenders.append({
                "scored_lead_id": sl.get("scored_lead_id"),
                "qualification_status": qs,
                "reason": "TAX_DEFAULT lead without QUALIFIED status",
            })
            continue
        ev = sl.get("qualification_evidence") or {}
        if not all(
            ev.get(k) is True for k in
            ("a_official_source", "b_default_condition", "c_property_tie",
             "d_source_proof", "e_not_generic_roll")
        ):
            offenders.append({
                "scored_lead_id": sl.get("scored_lead_id"),
                "qualification_evidence": ev,
                "reason": "§3.3 five-criteria evidence incomplete",
            })
    if offenders:
        return _result(
            13, "Tax-default qualification (v5.5.0 §4.1)", "INVALID",
            f"{len(offenders)} TAX_DEFAULT scored_lead(s) presented without "
            f"the §3.3 five-criteria gate verdict / evidence — generic "
            f"tax-roll data is being inflated to leads.",
            offenders[:10])
    return _result(
        13, "Tax-default qualification (v5.5.0 §4.1)", "VALID",
        f"{seen} TAX_DEFAULT scored_lead(s) all carry QUALIFIED status with "
        f"complete §3.3 five-criteria evidence.")


def _check_14_eventless_lead_rejection(scored_leads: list, ctx: dict) -> dict:
    """v5.5.0 §4.2 — a scored_lead must have an event_source and evidence_ids
    AND a non-empty signals chain. Rows with no originating event / no
    source proof fail (an inflated board)."""
    if scored_leads is None:
        return _result(
            14, "Eventless-lead rejection (v5.5.0 §4.2)", "SKIPPED",
            "no scored_leads supplied.")
    offenders = []
    for sl in scored_leads:
        has_source_ids = bool(sl.get("source_ids") or [])
        has_evidence = bool(sl.get("evidence_ids") or [])
        has_event_source = bool(sl.get("event_source") or sl.get("source_ids"))
        if not (has_source_ids and has_evidence and has_event_source):
            offenders.append({
                "scored_lead_id": sl.get("scored_lead_id"),
                "lead_id": sl.get("lead_id"),
                "source_ids": sl.get("source_ids"),
                "evidence_ids": sl.get("evidence_ids"),
                "event_source": sl.get("event_source"),
                "reason": "missing event_source / source_ids / evidence_ids",
            })
    if offenders:
        return _result(
            14, "Eventless-lead rejection (v5.5.0 §4.2)", "INVALID",
            f"{len(offenders)} scored_lead(s) carry no originating event / "
            f"no source proof — an inflated-board violation.",
            offenders[:10])
    return _result(
        14, "Eventless-lead rejection (v5.5.0 §4.2)", "VALID",
        f"all {len(scored_leads)} scored_leads carry event_source + "
        f"source_ids + evidence_ids.")


def _check_15_dead_board_rule(scored_leads: list, ctx: dict) -> dict:
    """v5.5.0 §4.3 — DEAD-BOARD rule. Reject an all-Unknown board when
    enrichment was POSSIBLE but not joined. Pass when scored_leads show
    enrichment was attempted (any ENRICHED leads, or the operator explicitly
    declared no enrichment join was available — owner unresolved with proof
    is OK per §4.3).

    The rule is precise:
      - if ZERO scored_leads have enrichment_status == 'ENRICHED' AND
      - at least one scored_lead has a real parcel_id (i.e. a join key
        existed AND was therefore possible), AND
      - the dashboard would render with owner_name == placeholder /
        UNKNOWN for all rows
      → INVALID (DEAD board — a join key existed, enrichment was possible,
        not done).
      - if the operator-supplied ctx flag 'enrichment_join_unavailable'
        is True → AMBIGUOUS (skipped enforcement, operator override).
      - otherwise → VALID.
    """
    if scored_leads is None:
        return _result(
            15, "Dead-board rule (v5.5.0 §4.3)", "SKIPPED",
            "no scored_leads supplied.")
    if not scored_leads:
        return _result(
            15, "Dead-board rule (v5.5.0 §4.3)", "VALID",
            "empty scored_leads — no board to publish, no dead-board check.")

    n_enriched = sum(
        1 for sl in scored_leads
        if sl.get("enrichment_status") == "ENRICHED"
    )
    n_with_parcel = sum(
        1 for sl in scored_leads
        if (sl.get("primary_parcel_id") or "").strip()
    )
    n_with_known_owner = sum(
        1 for sl in scored_leads
        if (sl.get("owner_name") or "")
        and "unidentified party" not in str(sl.get("owner_name", "")).lower()
        and "UNKNOWN" not in str(sl.get("owner_name", "")).upper()
    )

    enrichment_join_unavailable = ctx.get("enrichment_join_unavailable", False)
    if enrichment_join_unavailable:
        return _result(
            15, "Dead-board rule (v5.5.0 §4.3)", "AMBIGUOUS",
            "operator override: enrichment_join_unavailable=True — the "
            "all-Unknown board is allowed because no enrichment join is "
            "available for this county/run (§4.3 carve-out).")

    if n_enriched == 0 and n_with_parcel > 0 and n_with_known_owner == 0:
        return _result(
            15, "Dead-board rule (v5.5.0 §4.3)", "INVALID",
            f"DEAD BOARD: {len(scored_leads)} scored_lead(s), "
            f"{n_with_parcel} carry a real parcel_id (so a join key exists), "
            f"yet 0 are ENRICHED and 0 carry a known owner_name. Enrichment "
            f"was possible and was not done.")

    return _result(
        15, "Dead-board rule (v5.5.0 §4.3)", "VALID",
        f"{n_enriched}/{len(scored_leads)} ENRICHED; "
        f"{n_with_known_owner}/{len(scored_leads)} carry a known owner.")


def _check_16_no_past_sale_as_upcoming(scored_leads: list, ctx: dict) -> dict:
    """v5.5.0 §4.6 — a scored_lead whose lead_origin_type implies a future
    sale (RECORDED_EVENT carrying an UPCOMING_SALE signal) MUST NOT have a
    past primary_event_date. We can only enforce this when scored_leads
    carry the §3.9 classification; absent that, this check is SKIPPED.

    Precise: a scored_lead whose primary_event_date is BEFORE today's date
    AND whose lead_origin_type is NOT POST_SALE_TITLE_EVENT / SURPLUS_EVENT
    / TAX_DEFAULT / OWNER_STATUS (i.e. a scheduled-event lead that should
    be future-dated) → INVALID. The check uses ctx['as_of'] (caller-
    supplied) for the cutoff.
    """
    if scored_leads is None:
        return _result(
            16, "No-past-sale-as-upcoming (v5.5.0 §4.6)", "SKIPPED",
            "no scored_leads supplied.")
    as_of = ctx.get("as_of")
    if as_of is None:
        return _result(
            16, "No-past-sale-as-upcoming (v5.5.0 §4.6)", "SKIPPED",
            "no as_of date supplied in ctx — cannot evaluate scheduled-event "
            "freshness.")
    from datetime import date as _date  # local import to keep header light
    if not isinstance(as_of, _date):
        return _result(
            16, "No-past-sale-as-upcoming (v5.5.0 §4.6)", "SKIPPED",
            f"ctx['as_of'] is not a date (got {type(as_of).__name__}).")
    OK_BACKWARD_ORIGINS = {
        "POST_SALE_TITLE_EVENT", "SURPLUS_EVENT",
        "TAX_DEFAULT", "OWNER_STATUS",
    }
    offenders = []
    for sl in scored_leads:
        origin = sl.get("lead_origin_type")
        if origin in OK_BACKWARD_ORIGINS or origin is None:
            continue  # not a scheduled-event lead — past dates are fine
        ed = sl.get("primary_event_date")
        if not ed:
            continue
        try:
            ed_date = _date.fromisoformat(str(ed)[:10])
        except (ValueError, TypeError):
            continue
        if ed_date < as_of:
            offenders.append({
                "scored_lead_id": sl.get("scored_lead_id"),
                "lead_origin_type": origin,
                "primary_event_date": str(ed),
                "as_of": as_of.isoformat(),
                "reason": "past sale date on scheduled-event lead",
            })
    if offenders:
        return _result(
            16, "No-past-sale-as-upcoming (v5.5.0 §4.6)", "INVALID",
            f"{len(offenders)} scheduled-event scored_lead(s) carry "
            f"primary_event_date in the past — §3.9 PAST_SALE leaked into "
            f"the upcoming-lead board.",
            offenders[:10])
    return _result(
        16, "No-past-sale-as-upcoming (v5.5.0 §4.6)", "VALID",
        f"all scheduled-event scored_leads carry primary_event_date >= as_of "
        f"({as_of.isoformat()}).")


_V5_5_0_CHECKS = (
    _check_13_tax_default_qualification,
    _check_14_eventless_lead_rejection,
    _check_15_dead_board_rule,
    _check_16_no_past_sale_as_upcoming,
)
"""v5.5.0 §4 check pass — runs on the scored_leads layer. Invoked by
run_v5_5_0_semantic_checks(); also auto-included in
run_semantic_verification when scored_leads is supplied."""


def _deploy_verdict(check_results: list) -> str:
    """Compute the §20.F deploy verdict over the executed checks."""
    executed = [r for r in check_results if r["status"] != "SKIPPED"]
    if any(r["status"] == "INVALID" for r in executed):
        return "DEPLOY_BLOCKED"
    if any(r["status"] == "AMBIGUOUS" for r in executed):
        return "NEEDS_OPERATOR_REVIEW"
    return "DEPLOY_OK"


def run_semantic_verification(
    matched_leads: list,
    *,
    leads_base_records: Optional[list] = None,
    evidence_ledger: Optional[dict] = None,
    scored_leads: Optional[list] = None,
    as_of=None,
    enrichment_join_unavailable: bool = False,
) -> dict:
    """Run the §20 semantic verification gate over matched_leads.json.

    §20.G: semantic verification runs AFTER mechanical verification. This
    function first mechanically validates every matched lead against
    matched_lead_record.schema.json; a mechanical failure blocks the semantic
    checks and yields DEPLOY_BLOCKED. It then runs the twelve §20.C checks
    AND, when scored_leads is supplied, the v5.5.0 §4 check pass (4 new
    checks — tax-default qualification, eventless-lead rejection, dead-board
    rule, no-past-sale-as-upcoming) — then computes the §20.F deploy verdict
    over the union.

    Args:
        matched_leads: The aggregator's matched-lead records (matched_leads.json).
        leads_base_records: The leads-base records behind those matched leads.
            Supplies source roles for the §13.5 No-False-Dashboard check (Check
            4). Optional — Check 4's row-provenance part is skipped without it.
        evidence_ledger: Optional evidence-ledger index (evidence_id -> entry).
            Reserved for evidence-trace reporting.
        scored_leads: Optional — when supplied, the v5.5.0 §4 check pass runs
            on the scored_leads layer (the data that actually reaches the
            dashboard). Without it, checks 13-16 SKIPPED.
        as_of: Optional — required for check 16 (no-past-sale-as-upcoming);
            checks scheduled-event freshness against this cutoff date.
        enrichment_join_unavailable: When True, check 15 (dead-board rule)
            yields AMBIGUOUS instead of INVALID — the operator override per
            §4.3 carve-out.

    Returns:
        A report dict — `verdict`, `checks` (§20.C 1-12 plus v5.5.0 13-16
        when scored_leads supplied), the run / skipped / invalid / ambiguous
        tallies, and `mechanical_ok`.
    """
    # §20.G — mechanical verification first.
    validator = _matched_lead_validator()
    mechanical_failures = []
    for lead in matched_leads:
        errors = sorted(validator.iter_errors(lead), key=lambda e: list(e.path))
        if errors:
            mechanical_failures.append({
                "lead_id": lead.get("lead_id"),
                "errors": [e.message for e in errors[:5]],
            })
    if mechanical_failures:
        return {
            "verdict": "DEPLOY_BLOCKED",
            "mechanical_ok": False,
            "mechanical_failures": mechanical_failures,
            "checks": [],
            "detail": (
                f"§20.G: mechanical verification failed for "
                f"{len(mechanical_failures)} matched lead(s) — semantic "
                f"verification did not run."
            ),
        }

    role_by_id: dict[str, str] = {}
    for record in leads_base_records or []:
        sid = record.get("source_id")
        role = record.get("source_role")
        if sid and role:
            role_by_id[sid] = role
    ctx = {
        "source_role_by_id": role_by_id,
        "evidence_ledger": evidence_ledger or {},
        "as_of": as_of,
        "enrichment_join_unavailable": enrichment_join_unavailable,
    }

    results = [check(matched_leads, ctx) for check in _CHECKS]
    # v5.5.0 §4 pass — runs over scored_leads when supplied; the four new
    # checks SKIP cleanly when scored_leads is None.
    results.extend(
        check(scored_leads, ctx) for check in _V5_5_0_CHECKS
    )
    verdict = _deploy_verdict(results)

    return {
        "verdict": verdict,
        "mechanical_ok": True,
        "checks": results,
        "checks_run": sum(1 for r in results if r["status"] != "SKIPPED"),
        "checks_skipped": sum(1 for r in results if r["status"] == "SKIPPED"),
        "invalid_checks": [r["check"] for r in results
                           if r["status"] == "INVALID"],
        "ambiguous_checks": [r["check"] for r in results
                             if r["status"] == "AMBIGUOUS"],
        "skipped_checks": [r["check"] for r in results
                           if r["status"] == "SKIPPED"],
        "detail": (
            f"verdict {verdict} over "
            f"{sum(1 for r in results if r['status'] != 'SKIPPED')} executed "
            f"check(s); {sum(1 for r in results if r['status'] == 'SKIPPED')} "
            f"check(s) require deploy-time inputs and were skipped (§20.H)."
        ),
    }
