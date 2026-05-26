# v5.5.0 Release Notes — Framework Hardening (CANDIDATE)

**Tag:** *not yet* — this is the `feat/v5.5.0-framework-hardening` candidate branch.
**Date:** 2026-05-26
**Codename:** Xcerebro County Intelligence Harness
**Previous version:** `v5.4.0`
**Release channel:** candidate (review-only)

---

## Scope

v5.4.0 shipped the executable §17–§20 staged pipeline. v5.5.0 hardens it
against every defect the three-county build (Duval, Greene, Smith) exposed
when v5.4.0 met real-world recon. The build did not break the pipeline —
it broke the assumptions about what a "lead" is, where leads come from,
and how the dashboard renders them. v5.5.0 folds those operator-supplied
lessons into framework canon, code-first.

Every new claim in this patch ships with at least one of: executable
code, schema enforcement, an invariant test, a scanner, or a verification
script (the §0.2 contract).

This is a **candidate branch**. No tag. No merge to main. The branch is
review-ready as of this commit.

---

## Highlights

### §3.3 — Tax-default qualification gate

`scaffold/pipeline/tax_default_gate.py` (NEW). The five-criteria gate
(a/b/c/d/e) for tax-default rows + the account-level dedupe. Canonical
lead types: `tax_default`, `tax_default_low_priority`, `tax_foreclosure`,
`tax_sale`, `tax_certificate`, `review_required`. Pinned by
`scaffold/tests/v5_5_0/test_tax_default_gate.py` (31 checks).

### §3.5 — Estate-titled owner origination

`scaffold/pipeline/owner_status_classifier.py` (NEW). Strict
inclusion/exclusion rules + `LIFE ESTATE` split + (owner, parcel)
dedupe. Canonical lead types: `estate_titled_owner`, `life_estate`,
`not_estate`. Pinned by `scaffold/tests/v5_5_0/test_owner_status_classifier.py`
(35 checks).

### §3.9 — Scheduled-event classification

`scaffold/pipeline/scheduled_event_classifier.py` (NEW). Five categories:
UPCOMING_SALE / PAST_SALE / POST_SALE_TITLE_EVENT / SURPLUS_EVENT /
HISTORICAL_CONTEXT_ONLY. Status-based distress (tax_default,
code_violation_notice, liens) is REJECTED by this classifier — it lives
on the §3.3 / status-condition gates instead. Pinned by
`scaffold/tests/v5_5_0/test_scheduled_event_classifier.py` (23 checks).

### §4.4 — leads_base_writer parcel_id bug fix

The defect: the writer zeroed the aggregation-key `parcel_id` whenever
§17 routed REVIEW_REQUIRED, even when the raw event carried a real
parcel — which blocked downstream enrichment that needs to join on that
parcel. Fix: the agg-key carries parcel_id whenever the raw event has
one; the verdict status lives on `parcel_resolution_status` and is not
folded into the key. Pinned by
`scaffold/tests/v5_5_0/test_leads_base_writer_review_required_keeps_parcel_id.py`
(8 checks).

### §4.5 — Three new canonical doc types

Added to `canonical_doc_types.json`:
- `civil_judgment` (review_required by default — debtor-only until property
  attachment is proven; §3.6 doc-type honesty).
- `abstract_of_judgment` (review_required by default — same gate).
- `certificate_of_title` (lead_generating — FL post-foreclosure title
  instrument, POST_SALE_TITLE_EVENT per §3.9).

`doc_type_bridge.REGISTRY_TO_LEAD_TYPE` updated; "Abstract of Judgment"
promoted from shared-mapping carve-out to first-class registry entry.
Pinned by `scaffold/tests/v5_5_0/test_registry_additions_v550.py` (20 checks).

### §4.1 / §4.2 / §4.3 / §4.6 — §20 new check classes (13–16)

`semantic_verify.py` extended. New checks run on the scored_leads layer
(invoked when scored_leads is supplied to `run_semantic_verification`):
- **Check 13** — TAX_DEFAULT scored_leads must carry QUALIFIED status
  with the §3.3 five-criteria evidence.
- **Check 14** — eventless scored_leads (no source_ids / evidence_ids /
  event_source) are an inflated-board violation.
- **Check 15** — DEAD-BOARD rule: all-Unknown owner board when parcel
  keys exist and enrichment was possible → INVALID. Operator
  `enrichment_join_unavailable=True` override → AMBIGUOUS.
- **Check 16** — no past-sale-as-upcoming: scheduled-event leads with
  past `primary_event_date` → INVALID. POST_SALE_TITLE_EVENT /
  SURPLUS_EVENT / TAX_DEFAULT / OWNER_STATUS origins exempt.

Pinned by `scaffold/tests/v5_5_0/test_semantic_verify_v550_checks.py`
(17 checks).

### §0.1 / §3.8 — Source-role taxonomy + lead-origination provenance

`contracts/records.py SOURCE_ROLES` extended from 5 to 8 roles:
adds `PRIMARY_DEFAULT_SOURCE`, `PRIMARY_OWNER_STATUS_SOURCE`,
`REJECTED_SOURCE`. The four event-stream schemas (raw_event_record,
debtor_resolved_record, leads_base_record) accept all 8 enum values.

`scored_lead_record` schema + `ScoredLeadRecord` dataclass extended with
optional v5.5.0 provenance fields: `lead_origin_type`, `event_source`,
`owner_source`, `enrichment_source`, `qualification_status`,
`qualification_evidence`. New constants `LEAD_ORIGIN_TYPES` and
`QUALIFICATION_STATUSES`. Backward compatible — v5.4.x scored_leads
without these fields still validate. Pinned by
`scaffold/tests/v5_5_0/test_source_roles_and_provenance.py` (45 checks).

### §5 — Dashboard renderer contract + stale-label scanner

`scaffold/pipeline/dashboard_contract.py` (NEW) — declared field-mapping
contract (REQUIRED_DASHBOARD_FIELDS), STANDARD_FILTERS,
DEFAULT_FILTER_STATE (neutral, never all-checked), BANNER_PROHIBITED_TOKENS.

`scaffold/ops/stale_label_scanner.py` (NEW) — scans dashboard files for
foreign-county tokens (Bexar / Duval / Greene / Smith / El Paso / EPCAD /
Maricopa / Pima / etc.). The current county's own tokens are exempt via
`--county <slug>`. Pinned by
`scaffold/tests/v5_5_0/test_dashboard_contract_and_stale_labels.py`
(40 checks).

### §6 — Daily refresh canon

`scaffold/ops/refresh_verification_gate.py` (NEW) — pre-publish gate.
PUBLISH only when: lead_count ≥ floor, actionable_fraction ≥ floor,
resolved_owner_fraction ≥ floor (unless enrichment_join_unavailable),
resolved_address_fraction ≥ floor. Bounded pull-window registry per
distress type (forward for scheduled events, backward for recorded
events, none for status conditions). Pinned by
`scaffold/tests/v5_5_0/test_refresh_verification_gate.py` (20 checks).

`scaffold/ops/daily_refresh_template.yml` (NEW) — canonical GitHub
Actions workflow scaffold. Clean data/raw/, re-fetch primary + enrichment,
run §6.4 gate, publish OR preserve last-good per gate verdict.

### §7.1 — Live-URL verification contract

`scaffold/ops/verify_live_contract.py` (NEW). Two halves:
- STATIC (no browser — stdlib-only): 8 checks (URL fetches, body is HTML,
  not-a-JSON-parse-error, data artifact reachable + parseable, v5.5.0
  dashboard contract fields present, no stale foreign-county labels, no
  banner-prohibited tokens).
- INTERACTIVE (Playwright contract declared here; implementation is the
  county verify_live.py): 7 checks (cards render, owner+address visible,
  3 filter click-tests change the visible count, reset restores, no JS
  console errors).

Pinned by `scaffold/tests/v5_5_0/test_verify_live_contract.py` (20 checks).

### §S1 / §S2 — Recon protocol + access ladder docs

`knowledge_base/protocols/01_county_recon.md` §01.27 (NEW) — v5.5.0 hard
recon protocol: §1.1 exhaustive primary-source catalog (incl. tax
collector vs property appraiser distinction); §1.2 enrichment catalog;
§1.3 hunt beyond the dossier; §1.4 historical instances; §1.5
classification with the v5.5.0 8-role taxonomy; §1.6 missed-source
audit.

`knowledge_base/engineering/04_blocked_source_strategies.md` v5.5.0
access-ladder section (NEW) — §2.1 Playwright is standard authorized
tooling; §2.2 access ladder (stdlib → Playwright headless → +stealth →
operator-seeded); §2.3 CI considerations; §2.4 recon-before-scraper;
§2.5 reuse proven operator code.

Pinned by `scaffold/tests/v5_5_0/test_recon_and_access_docs_present.py`
(29 checks).

---

## Test count

v5.5.0 ships **288 new invariant checks** across 11 new test files in
`scaffold/tests/v5_5_0/`. The harness (`scaffold/tests/run_all.py`)
auto-discovers them.

---

## Deferred

None — every section §1–§7.5 of the patch brief is canonized with code
or executable invariants. Operator-supplied county fixtures (real
RealForeclose / RealAuction calendars, real tax-collector portals) for
INTERACTIVE check 7 (no JS console errors during a filter sequence)
remain a county-side task — the framework defines the contract; the
county's Playwright runner produces the per-check verdict.
