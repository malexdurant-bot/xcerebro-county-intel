# 01. County Recon Protocol (v5.1.2-beta+)

The county recon protocol is the deterministic procedure Claude Code follows during
Phase 0 of a county build. It turns a freshly bootstrapped county run folder into a
complete recon dossier and one Build Eligibility Gate verdict.

This is the first document in the `knowledge_base/protocols/` family. Protocols are
reusable, county-agnostic execution procedures — distinct from `architecture/` docs
(which define contracts and schemas) and `domain/` docs (which define investor-side
knowledge).

**v5.3.0 amendment.** Every county recon MUST produce a complete **Source-of-Record
Matrix** as its required output artifact — see `knowledge_base/architecture/16_source_of_
record_matrix.md` and the v5.3.0 amendment block in §01.20–§01.26 of this protocol. The
amendment adds the lead type sweep and three mandatory Phase 0 sub-steps (PDF/sample
inspection, documented API discovery, bulk-data availability classification) that apply
WITHIN Phases 0.A–0.H, before any source is classified deferred or limited-coverage.

---

## 01.0 Status and scope

- **Version:** v5.1.2-beta+ (extends the framework as currently committed; does not
  require any later patch to function).
- **Date:** 2026-05-17.
- **Purpose:** a county-agnostic Phase 0 recon procedure.
- **Authoritative for:** Phase 0 work for any new county, after
  `scaffold/bootstrap_county.py` has created the flat run folder and the user has
  approved bootstrap.
- **Scope IN:** source discovery, official source verification, portal fingerprinting,
  access classification, source role classification, document type discovery, blocker
  classification, and Build Eligibility Gate handoff using the active
  `MASTER_PROMPT.md §4.10` verdict enum.
- **Scope OUT:** scraper code, translator code, dashboard code, source-specific
  knowledge, county-specific examples, scoring weights, scoring overrides, and the
  stepwise gate algorithm (not yet formalized in the active framework — see §01.16).
- **Style:** matches the writing conventions of `knowledge_base/architecture/`
  documents — 4-space indented prose blocks for structured content, no triple-backtick
  code fences, inline backticks for filenames and field names.
- **Companion files:** this protocol is the first in the `knowledge_base/protocols/`
  family. Subsequent protocols (auto-resolve, scrape, translate, and others) are queued
  for future framework patches and are not assumed to exist yet.

---

## 01.1 Purpose

This protocol turns a freshly bootstrapped county run folder into a complete recon
dossier with eight named artifacts and one Build Eligibility Gate verdict.

It is the deterministic procedure Claude Code follows during Phase 0 of a county build.
It is **county-agnostic**: nothing in this document hardcodes a specific county, state,
vendor, or portal. Every county-specific value enters as a runtime input read from the
LAUNCH file (§01.3) and is substituted into the placeholders this protocol uses.

The recon dossier answers one operator question: **can this county produce a real lead
board, and if not, what is blocking it?** It answers that question with evidence — a
chain of named artifacts an operator can review without re-running the recon.

---

## 01.2 When this protocol runs

**Triggers — all must hold:**

- `scaffold/bootstrap_county.py` has completed for a new county, creating
  `runs/<county_slug>/LAUNCH_<SLUG>.md` and `runs/<county_slug>/operator_notes.md`.
- The user has approved bootstrap (per the bootstrap approval gate documented in
  `START_HERE.md`).
- `runs/<county_slug>/recon/` does not yet contain a populated `recon_summary.md` —
  this protocol is what creates it.

**This protocol does NOT run:**

- before `scaffold/bootstrap_county.py` has completed;
- if `recon_summary.md` already exists with a final verdict (re-running a completed
  recon requires explicit operator instruction);
- as part of any task that is not a county build.

---

## 01.3 Inputs

Claude Code reads every input from `runs/<county_slug>/LAUNCH_<SLUG>.md`, the launch
file written by `scaffold/bootstrap_county.py` (its `generate_launch_file_content`
function produces that file). Required inputs:

- `county_slug` — a validated slug (lowercase letters, digits, underscores; no leading,
  trailing, or consecutive underscores — the `SLUG_PATTERN` enforced by
  `scaffold/bootstrap_county.py`). Conventional form is `<county>_<state>`.
- `county_name` — the operator-readable county name (conventionally `<County> County`
  or the equivalent local term).
- `state_code` — the 2-letter, lowercase state code.
- `state_name` — the full state name.
- `bootstrap_phase` — the framework phase recorded at bootstrap time.

The protocol does NOT derive these inputs from anywhere else. It reads them from the
LAUNCH file. If the LAUNCH file is missing or malformed, **halt and report** — do not
guess the county or state.

---

## 01.4 Expected run folder location

All recon artifacts written by this protocol go under:

    runs/<county_slug>/recon/

`scaffold/bootstrap_county.py` (function `create_run_folder`) creates a **flat** run
folder containing only `LAUNCH_<SLUG>.md` and `operator_notes.md`. The `recon/`
subdirectory does **not** exist when this protocol begins; §01.5 creates it.

---

## 01.5 Recon folder creation rule

Before writing any recon artifact, Phase 0.A creates the recon subdirectory:

    runs/<county_slug>/recon/

This is the **only** directory this protocol creates. No nested subdirectories under
`recon/` are required (no `raw_html/`, no `fixtures/`, no `screenshots/` — those are
Phase 1+ artifacts and out of scope here).

If `runs/<county_slug>/recon/` already exists with prior contents, the protocol does
**not** overwrite. It reads the existing artifacts to determine whether a prior recon
was partial:

- if a prior recon was partial (some artifacts present, `recon_summary.md` absent or
  without a final verdict), resume from the first missing artifact;
- otherwise (a complete prior recon), halt and ask the operator before re-running.

---

## 01.6 Phase 0.A — Source discovery procedure

Use web search to find candidate official county sources. Every query uses placeholders
that Claude Code substitutes from the §01.3 inputs.

Required search queries, in priority order:

    1.  "<county_name> <state_name> county clerk official records"
    2.  "<county_name> <state_name> county recorder of deeds"
    3.  "<county_name> <state_name> district clerk court records"
    4.  "<county_name> <state_name> sheriff sale schedule"
    5.  "<county_name> <state_name> tax assessor delinquent"
    6.  "<county_name> <state_name> tax sale records"
    7.  "<county_name> <state_name> probate court records"
    8.  "<county_name> <state_name> foreclosure notice"
    9.  "<county_name> <state_name> mechanics lien filings"
    10. "<state_name> appraisal district"  OR  "<state_name> property assessor"
        (state-dependent enrichment search)

Optional additional queries may be derived from the state-specific event types
described in `knowledge_base/domain/02_signals_and_sources.md`. The protocol itself
contains no state-specific query text — additional queries are composed at runtime from
domain knowledge, never hardcoded here.

For each candidate URL found:

- visit the page;
- confirm it appears to be an official county government source (URL pattern, a
  vendor-hosted-for-county subdomain, county branding);
- capture the exact URL, the page title, and a brief note on what records the source
  covers;
- distinguish official sources from third-party aggregators. Paid-data aggregators and
  reseller portals are NOT primary recon targets and are explicitly out of scope for
  the framework — they are reseller layers over official data, not the official record
  authority.

Save findings to `runs/<county_slug>/recon/source_discovery.md`. One entry per source,
with fields:

    name
    official_url
    page_title
    gov_or_aggregator
    records_covered
    discovered_via_query

---

## 01.7 Phase 0.B — Official source verification layers

For each source recorded in `source_discovery.md`, verify it is genuinely the official
county source for its record class. Verification layers:

- **Layer 1 — Government domain check.** The domain ends in `.gov` or `.us`, or is a
  recognizable county-named subdomain.
- **Layer 2 — Vendor portal check.** If the source is hosted by a known portal vendor
  (consult `knowledge_base/engineering/08_vendor_portal_library.md` for recognized
  vendor patterns), confirm the vendor-hosted page is the official county-contracted
  portal — footer attribution, county branding, and a link inbound from the county's
  own `.gov` website.
- **Layer 3 — Cross-reference check.** The county's own `.gov` homepage links to this
  source.
- **Layer 4 — Records authority check.** The source corresponds to a recognized county
  records authority (Clerk of Court, County Clerk, Recorder of Deeds, Sheriff, Tax
  Assessor-Collector, District Clerk, Probate Court, or the local equivalent).

Classification rule:

- passes Layer 1 OR (Layer 2 AND Layer 3), AND passes Layer 4 → `VERIFIED_OFFICIAL`;
- passes some layers but not the rule above → `UNVERIFIED`, with a note on which layers
  passed;
- fails Layer 4 (no recognized records authority) → `NOT_RECORDS_AUTHORITY`; exclude
  from further processing.

Save to `runs/<county_slug>/recon/source_verification.md`. One entry per source, with
fields:

    name
    official_url
    layers_passed
    verification_status
    notes

---

## 01.8 Phase 0.C — Portal fingerprinting procedure

For each `VERIFIED_OFFICIAL` source, fingerprint the portal:

- **Portal vendor** — consult `knowledge_base/engineering/08_vendor_portal_library.md`
  for known vendor recognition patterns.
- **Detection heuristics used** — the HTML markers, URL patterns, JS bundle paths,
  `robots.txt` entries, or footer vendor branding that identified the vendor.
- **Page architecture** — single-page app (JS-rendered) versus server-rendered HTML.
- **Search interface type** — form-based POST, REST API, GraphQL, vendor-proprietary,
  or other.
- **Result page URL pattern.**
- **Detail page URL pattern.**
- **Estimated technical scrape difficulty:**
  - `LOW` — server-rendered HTML, no authentication;
  - `MEDIUM` — single-page app, no authentication;
  - `HIGH` — single-page app plus dynamic authentication or per-request tokens;
  - `VERY_HIGH` — Cloudflare challenge, CAPTCHA gate, IP block, or anti-scrape headers.

Save to `runs/<county_slug>/recon/portal_fingerprints.md`. One entry per source, with
fields:

    name
    vendor
    detection_heuristics
    architecture
    search_interface
    result_url_pattern
    detail_url_pattern
    scrape_difficulty

---

## 01.9 Phase 0.D — Access classification taxonomy

Canonical access classifications — use exactly these enum values:

    OPEN_PUBLIC                 searchable without login or payment; search results and
                                detail metadata fully visible
    SEARCH_ONLY_PUBLIC          search and result metadata are free; document images /
                                PDFs are behind payment. Acceptable for the framework —
                                document images are not required to produce matched
                                leads from search metadata
    FREE_ACCOUNT_REQUIRED       requires signup, but the account is free
    PAID_SUBSCRIPTION_REQUIRED  paid access required to use search at all
    LOGIN_REQUIRED              credentials required; paid/free status unknown
    CAPTCHA_PROTECTED           a CAPTCHA gates search results
    DOCUMENT_IMAGES_LOCKED      search works but document images are locked behind
                                payment. When document images are needed for the build,
                                this is a blocker; when they are not needed, it is
                                equivalent to SEARCH_ONLY_PUBLIC
    BLOCKED                     Cloudflare challenge, IP block, or anti-scrape headers
                                prevent access
    UNKNOWN                     could not be determined without taking a forbidden
                                action (see §01.17)

For each `VERIFIED_OFFICIAL` source, classify access by visiting the source and
observing — without taking any forbidden action (§01.17).

Save to `runs/<county_slug>/recon/access_classification.md`. One entry per source, with
fields:

    name
    access_classification
    evidence
    notes

The `evidence` field records what was observed that led to the classification — the
specific page behavior, header, challenge, or paywall seen. An access classification
without observed evidence is not acceptable.

---

## 01.10 Phase 0.E — Source role classification

Canonical source role values, per `knowledge_base/architecture/13_lead_origination_
contract.md` §13.2 and §13.3:

    PRIMARY_LEAD_SOURCE     clerk records, court filings, foreclosure notices, sheriff
                            sales, tax liens, tax delinquency, lis pendens, probate,
                            estate records, mechanics liens, judgments, and other
                            recorded distress events (full canonical list in §13.2)
    SUPPORTING_LEAD_SOURCE  related event sources that confirm a primary signal but do
                            not originate a lead on their own (for example, a trustee
                            sale appointment supporting a foreclosure notice)
    ENRICHMENT_SOURCE       parcel, GIS, CAD, assessor, tax roll, ownership, valuation,
                            vacancy, equity proxy, and property-attribute data (full
                            canonical list in §13.3)
    REFERENCE_ONLY          sources that provide context but are not used in the build
                            (county informational pages, vendor documentation)
    REJECTED_SOURCE         sources excluded from the build (third-party aggregators,
                            paywalled redundancies, sources that failed verification)

For each `VERIFIED_OFFICIAL` source, classify the role per §13. For unverified or
excluded sources, record `REJECTED_SOURCE` with a reason.

Save to `runs/<county_slug>/recon/source_role_classification.md`. One entry per source,
with fields:

    name
    source_role
    rationale
    section_13_reference

---

## 01.11 Primary lead source vs enrichment source rule

The hard rules from `knowledge_base/architecture/13_lead_origination_contract.md` §13.4
and §13.5 are restated here so Claude Code cannot miss them during Phase 0:

**HARD RULE (restated from §13.4.1).** A lead row MUST originate from a primary
event-based recorded source. Enrichment alone cannot create a lead row.

**HARD RULE (restated from §13.4.2).** Parcel data, GIS data, CAD data, assessor data,
tax roll data, ownership data, valuation data, vacancy data, equity proxies, and
property attributes are ENRICHMENT. They CANNOT create a lead row. They CAN attach to a
lead row that already exists from a primary source.

**HARD RULE (restated from §13.5.1).** Every Matched lead row (the record type defined
in `knowledge_base/architecture/09_output_schemas.md`) MUST contain at least one signal
from a §13.2 primary lead source category. This is a checkable property; the framework
verifies it before emitting any active lead output.

**Consequence for recon.** If zero `PRIMARY_LEAD_SOURCE` entries pass verification
(§01.7) AND access classification (§01.9) AND end up accessible — `OPEN_PUBLIC` or
`SEARCH_ONLY_PUBLIC` — then the Build Eligibility Gate verdict cannot be
`READY_TO_BUILD` or `READY_WITH_BLOCKERS`. The framework will not produce an active
lead dashboard from enrichment alone. Recon's job is to discover whether that primary
path exists; it does not get to wish one into being.

---

## 01.12 Phase 0.F — Document type discovery

For each `VERIFIED_OFFICIAL` source classified as `PRIMARY_LEAD_SOURCE` or
`SUPPORTING_LEAD_SOURCE` with an access classification of `OPEN_PUBLIC` or
`SEARCH_ONLY_PUBLIC`, perform a lightweight document type discovery:

- identify the document type taxonomy the portal uses — document type codes, document
  categories, or filing types;
- capture the available document type values, for example the contents of any
  "document type" dropdown on the search interface;
- cross-reference each discovered document type against the §13.2 primary lead source
  categories, to identify which document types are PRIMARY_LEAD signals and which are
  NOISE — administrative, lifecycle/suppression, or unrelated;
- reference `knowledge_base/domain/canonical_doc_types.json` for the canonical doc type
  registry. When a discovered type matches a canonical type, note the canonical name.

This phase is **metadata-only**. It does NOT scrape records. It captures the source's
document type vocabulary and proposes how that vocabulary maps to the §13.2 primary
categories.

Save to `runs/<county_slug>/recon/document_type_discovery.md`. Per source, with fields:

    source_name
    document_type_taxonomy_field_name
    total_types_observed
    types_mapped_to_canonical_primary
    types_mapped_to_canonical_enrichment
    types_unknown
    recommended_primary_doc_types_for_build

---

## 01.13 Phase 0.G — Blocker classification and auto-resolve boundaries

For each source with an access classification other than `OPEN_PUBLIC` or
`SEARCH_ONLY_PUBLIC`, classify the blocker type and identify whether auto-resolve (per
`MASTER_PROMPT.md §4.14`, Phase 0.5) is allowed.

**Technical blocker** — auto-resolve may be attempted in Phase 0.5:

- user-agent gating;
- a `robots.txt` that permits scraping;
- content server-rendered behind JavaScript that a headless browser can render;
- a simple session-cookie requirement.

**Permission blocker** — auto-resolve NOT allowed; requires operator escalation:

- `FREE_ACCOUNT_REQUIRED` — account creation is forbidden during recon;
- `PAID_SUBSCRIPTION_REQUIRED` — payment is forbidden during recon;
- `LOGIN_REQUIRED` — credentials are required;
- `CAPTCHA_PROTECTED` — solving CAPTCHAs is forbidden without an operator-approved
  solver;
- `DOCUMENT_IMAGES_LOCKED` when document images are required for the build.

**Hard blocker** — no auto-resolve; requires an operator decision:

- `BLOCKED` — Cloudflare challenge, IP block, or anti-scrape headers.

**Unknown blocker** — requires operator clarification, not an auto-resolve attempt.

Recon does NOT execute auto-resolve. Recon only classifies blockers and records what
type of operator action would be needed to clear each one. The actual auto-resolve
procedure is owned by `MASTER_PROMPT.md §4.14` (Phase 0.5) and is a separate phase.
Strategy detail for blocked sources lives in
`knowledge_base/engineering/04_blocked_source_strategies.md`.

This blocker classification feeds the Build Eligibility Gate verdict (§01.15).

---

## 01.14 Phase 0.H — Recon artifacts to write

All artifacts are written under `runs/<county_slug>/recon/`:

    source_discovery.md            Phase 0.A output — candidate sources from web search
    source_verification.md         Phase 0.B output — verification layer pass/fail
    portal_fingerprints.md         Phase 0.C output — vendor and architecture
    access_classification.md       Phase 0.D output — access tier per source
    source_role_classification.md  Phase 0.E output — PRIMARY / SUPPORTING /
                                   ENRICHMENT / REFERENCE / REJECTED
    document_type_discovery.md     Phase 0.F output — document type taxonomy
    build_eligibility_handoff.md   Phase 0.G + §01.15 output — blocker classification
                                   plus the gate handoff
    recon_summary.md               final operator-facing summary with the Build
                                   Eligibility Gate verdict (§01.15 / §01.16)

That is eight artifacts. Each uses 4-space indented prose blocks for structured content,
matching the existing harness convention — no triple-backtick code fences.

---

## 01.15 Build Eligibility Gate handoff

Phase 0.G plus the verdict computation produce
`runs/<county_slug>/recon/build_eligibility_handoff.md`, containing:

- **Counts** — `VERIFIED_OFFICIAL` sources, sources by role, sources by access
  classification.
- **Accessible primary sources count** — `PRIMARY_LEAD_SOURCE` intersected with
  `OPEN_PUBLIC` / `SEARCH_ONLY_PUBLIC` / `DOCUMENT_IMAGES_LOCKED`-when-acceptable.
- **Accessible primary document types** — the PRIMARY_LEAD signals from the §01.12
  discovery, for the accessible primary sources.
- **Blockers by type** — technical / permission / hard / unknown.
- **Recommended provisional verdict** — one of the five §4.10 values listed in §01.16.
- **Justification trail** — every source examined, its role, its access classification,
  and how it contributed to the verdict. The trail must let an operator reconstruct the
  verdict without re-running the recon.
- **Recommended operator next actions.**

Then write `runs/<county_slug>/recon/recon_summary.md` as the operator-facing executive
summary, citing `build_eligibility_handoff.md` for the detail.

---

## 01.16 Current §4.10 verdict definitions

Use the `MASTER_PROMPT.md §4.10` verdict enum — five values, no others:

- **`READY_TO_BUILD`** — at least one verified primary lead source is fully accessible
  without operator escalation (`OPEN_PUBLIC` or `SEARCH_ONLY_PUBLIC`), with at least one
  accessible primary document type; enrichment may or may not be available; no critical
  blocker prevents Phase 1+ work.
- **`READY_WITH_BLOCKERS`** — at least one verified primary source is accessible, but
  other primaries are blocked; partial coverage is achievable; operator authorization is
  required to proceed past the partial scope.
- **`RECON_ONLY`** — sources have been discovered but none is currently buildable as a
  primary lead source (for example, all primaries are `CAPTCHA_PROTECTED`, `BLOCKED`, or
  `PAID_SUBSCRIPTION_REQUIRED`); enrichment may be available but cannot independently
  produce a lead board per §13.
- **`WAITING_ON_ACCESS`** — a primary source has been identified and verified, but is
  blocked at the access layer (`FREE_ACCOUNT_REQUIRED`, `LOGIN_REQUIRED`, or
  `DOCUMENT_IMAGES_LOCKED` when document images are required); the build awaits operator
  credentials, account creation, or paid-subscription approval before it can proceed.
- **`NOT_BUILDABLE_YET`** — no primary source was identified after a reasonable search;
  the county may have no online official primary records, or all candidates failed
  verification.

Important note:

    The stepwise Build Eligibility Gate algorithm is not yet formalized in the active
    framework. Until the future gate enforcement patch lands, Claude Code applies §4.10
    using documented operator judgment from recon outputs.

The justification trail in `build_eligibility_handoff.md` is the transparency record
for that judgment — it is what makes an operator-judgment verdict reviewable.

---

## 01.17 What is forbidden during recon

Strict prohibitions during this protocol, consistent with the §01.13 auto-resolve
boundary:

- creating accounts, free or paid;
- paying for any service;
- solving CAPTCHAs;
- using proxy services;
- bypassing `robots.txt`;
- bypassing access controls of any kind;
- scraping records — recon is metadata-only: vendor identification, access
  classification, and document type taxonomy capture. Actual record extraction belongs
  to a later phase;
- submitting public records requests;
- modifying any framework file;
- modifying any file outside `runs/<county_slug>/recon/`;
- committing or pushing;
- writing scraper, translator, or dashboard code;
- producing Matched lead output;
- producing any operator-facing or client-facing artifact other than the eight recon
  artifacts listed in §01.14.

When access cannot be determined without taking one of these forbidden actions, the
correct outcome is the `UNKNOWN` access classification (§01.9) and an operator
escalation — never the forbidden action.

---

## 01.18 Completion checklist

Recon is complete when ALL of the following are true:

- `runs/<county_slug>/recon/` exists;
- `source_discovery.md` exists with at least one entry;
- `source_verification.md` exists with a verification status for every source in
  `source_discovery.md`;
- `portal_fingerprints.md` exists for every `VERIFIED_OFFICIAL` source;
- `access_classification.md` exists for every `VERIFIED_OFFICIAL` source;
- `source_role_classification.md` exists for every `VERIFIED_OFFICIAL` source;
- `document_type_discovery.md` exists for every accessible `PRIMARY_LEAD_SOURCE` or
  `SUPPORTING_LEAD_SOURCE`;
- `build_eligibility_handoff.md` exists with a §4.10 verdict and a justification trail;
- `recon_summary.md` exists with the operator-facing summary;
- no file was written outside `runs/<county_slug>/recon/`;
- no commit, no push, no stash operation occurred.

Then:

- if the verdict is `READY_TO_BUILD` or `READY_WITH_BLOCKERS`, the protocol hands off to
  Phase 1+ (scraper / translator / evidence / dashboard work — out of scope for this
  protocol);
- if the verdict is `RECON_ONLY`, `WAITING_ON_ACCESS`, or `NOT_BUILDABLE_YET`, the
  protocol STOPS and awaits an operator decision.

---

## 01.19 End marker

Protocol complete. The recon artifacts are operator-reviewable. The Build Eligibility
Gate verdict in `recon_summary.md` determines the next phase or the stop.

---

## 01.20 v5.3.0 amendment — Source of Record Matrix recon requirements

v5.3.0 extends this protocol. The requirements in §01.20–§01.26 are mandatory Phase 0
recon requirements; they apply WITHIN Phases 0.A–0.H, before any source is classified
deferred, limited-coverage, or not-buildable. They do not replace §01.0–§01.19 — they
extend it.

The required output artifact of every county recon is the **Source-of-Record Matrix**,
defined by `knowledge_base/architecture/16_source_of_record_matrix.md` and schema-checked
against the `sourceOfRecordMatrix` definition in `config/counties/_schema.json`. The
matrix and its companion artifacts are written under `runs/<county_slug>/recon/`:
`source_of_record_matrix.json`, `source_of_record_matrix.md`, `source_coverage_map.md`,
`api_discovery_report.md`, `operator_verified_sources.yml`,
`fingerprints/<source_id>.fingerprint.json`, and `build_eligibility_report.md`. A recon
that does not produce a complete matrix cannot proceed to Build Mode.

## 01.21 Lead Type Sweep requirement

Every county recon MUST walk the full canonical lead type sweep — the 27 lead types
enumerated in `§16.B` (Foreclosure, Trustee Sale, Notice of Trustee Sale, Notice of
Substitute Trustee Sale, Sheriff Sale, Tax Lien Foreclosure, Tax Sale, Tax Sale
Certificate, Tax Delinquency, Lis Pendens, Civil Judgment, Abstract of Judgment,
Mechanic Lien, Construction Lien, Federal Tax Lien, State Tax Lien, Probate, Affidavit
of Heirship, Executor Deed, Administrator Deed, Code Lien, Demolition, Condemnation,
Eviction, Divorce, Bankruptcy, Surplus).

For each lead type, recon MUST answer: where is the official source of record? what is
its URL? what is its access pattern? is it buildable? A recon that does not produce a
complete sweep — an entry per lead type — is incomplete and cannot proceed to Build
Mode.

## 01.22 Required Step — PDF/Sample Document Inspection (Gap 1)

Before classifying any source as deferred or limited-coverage based on the listing/index
page alone, recon MUST fetch and inspect at least 3 sample source documents (PDF,
scanned image, downloaded XML, or whatever the underlying record format is).

The inspection must answer:

- What fields does the actual source document carry that the listing/index page does
  not expose?
- Does the document carry: property address (situs), debtor/owner name, parcel
  identifier, sale/event date, document reference number, legal description?
- Is the document text-extractable or scanned-image (OCR required)?
- Does the layout vary across documents (multiple templates)?

Classifying a source as deferred without sample-document inspection is a recon defect.
The recon report must explicitly answer: "Sample documents inspected: Y/N. If N, evidence
of why inspection was not possible (access blocked, document images locked, etc.)."

Rationale: A source's listing/index page may expose only minimal metadata, while the
underlying document carries the address, debtor, and event date directly. Deferring such
a source based on listing-page inspection alone is a false negative — buildable sources
get misclassified as not-buildable.

## 01.23 Required Step — Documented API Discovery (Gap 2)

For every candidate source, recon MUST explicitly search for documented APIs before
settling on HTML scraping. Required search locations:

- `<domain>/api`
- `<domain>/api/swagger`
- `<domain>/swagger`
- `<domain>/docs`
- `<domain>/api-docs`
- Postman public collections (search vendor name + "postman")
- GitHub (search "<county_name> api" / "<vendor_name> api")
- Vendor documentation (if the portal is vendor-built)

The recon report must explicitly answer: "Documented API found: Y/N. If N, list of
search paths checked."

If a documented API is found, prefer it over HTML scraping. Document the API in
`runs/<county_slug>/recon/api_discovery_report.md` and link it from the
Source-of-Record Matrix.

Rationale: Documented APIs are more stable, faster, and exempt from anti-bot/WAF
protections that block HTML scrapers. Stopping recon at HTML/WAF classification when a
documented API exists is a false-negative recon outcome.

## 01.24 Required Step — Bulk-Data Availability Classification (Gap 3)

For every candidate source, recon MUST classify its bulk-data availability as one of:

- `FULL_COUNTY_BULK`
- `BATCH_QUERY`
- `PER_RECORD_ONLY`
- `UNKNOWN`

Per-record-only sources are buildable, but their coverage is bounded by the
externally-resolved parcel set. Recon must surface this constraint and document the
coverage implication.

The recon report must explicitly answer for each source: "Bulk availability: <class>.
Coverage implication: <description>."

Rationale: A per-record-only source cannot enumerate the universe of distressed
properties — it can only be queried for known parcel identifiers. Discovering this
constraint during build instead of recon causes scope re-estimation mid-build and
undermines build_verdict accuracy.

## 01.25 Operator-Verified Sources

When the operator manually surfaces a direct source link that recon missed, the link
must be captured in `runs/<county_slug>/recon/operator_verified_sources.yml` with:

- `lead_type`
- `discovered_by` (operator)
- `official_url`
- `official_origin_evidence` (how the operator confirmed the source is official)
- `reason_added` (why recon missed it)
- `review_status` (`accepted` | `rejected` | `pending`)
- `notes`

This is a recon supplement, not an override. Subsequent recon runs should still attempt
to discover the source independently; the operator-verified entry is provenance, not
exemption.

## 01.26 Halt condition (v5.3.0)

Recon halts when no verified primary event source is found across the complete lead type
sweep. Clerk and recorder are the most common primary event sources but not the only
valid ones — court portals, district clerks, sheriffs, tax offices, tax collectors,
trustee-sale portals, foreclosure-listing portals, auction vendors, official vendor
portals, and posted-notices pages are all valid primary event sources. Recon does not
halt merely because clerk or recorder access is blocked, provided another verified
primary event source exists for at least one lead type.

## 01.27 v5.5.0 hard-recon protocol (§S1 amendment)

v5.5.0 hardens the recon protocol against the failure modes the three-county
build exposed (Duval, Greene, Smith). The §1.1–1.6 canon below is mandatory
before a county is build-ready; the v5.5.0 hardening makes it executable and
testable through the framework's invariant suite, not just doc.

### §1.1 — exhaustive primary-source catalog

Recon MUST catalog every reachable primary source carrying a distress
event. Catalog categories (none of these is optional — operators must
investigate or PUNCH-LIST each):

- clerk / recorder official records
- foreclosure auction calendars (RealForeclose / RealAuction family)
- tax-deed and tax-certificate sale portals
- sheriff / constable sale calendars
- county court (foreclosure / probate / judgment)
- legal-notice publications
- code enforcement / demolition / condemnation
- surplus / excess proceeds
- **county TAX COLLECTOR (delinquency)** — a SEPARATE office from the
  county PROPERTY APPRAISER / ASSESSOR. Both must be cataloged separately;
  conflating them is a defect. v5.5.0 §3.3 originates leads from the
  tax-collector channel through the `PRIMARY_DEFAULT_SOURCE` role.

### §1.2 — enrichment source catalog

Recon MUST catalog enrichment sources explicitly:
- parcel master / appraisal district / property appraiser
- GIS layers
- assessor / CAD records
- tax roll (status-condition — `tax_default` / `tax_delinquency` only
  qualifies as a lead through the §3.3 gate, not through this catalog).

§13.14 / §4.3 dead-board rule: a county with no resolved enrichment is a
verification failure, not a pass. Enrichment is mandatory to LOCATE; per
R3(iii) it is OPTIONAL to scoring. Both rules coexist.

### §1.3 — hunt beyond the dossier

The banked source dossier is a starting point. Recon must actively search
for additional sources — sub-jurisdictional courts, municipal-specific
recorders, court-of-record-by-lead-type variants. v5.5.0 missed-source
audit (§1.6) is the test.

### §1.4 — historical instances + prior-year context

Recon must probe prior-year / historical instances of each source:
- prior auctions (recurrence is a §3.9 stacking signal — historical
  scheduled events become HISTORICAL_CONTEXT_ONLY on a current lead);
- prior petition PDFs (delinquency-history evidence on a current
  tax-default lead);
- prior posted notices (recurrence + dispossession-rate signals).

v5.5.0 §3.9 / §6.6 forbid pulling past sale dates as upcoming leads —
historical data feeds recurrence signal and downstream / post-sale lead
types, NOT past-as-upcoming.

### §1.5 — classification with the v5.5.0 8-role taxonomy

Each source gets classified with the full v5.5.0 role list:

- `PRIMARY_EVENT_SOURCE` — recorded / filed / posted event
- `PRIMARY_DEFAULT_SOURCE` — tax-default / delinquency / tax-sale (§3.3)
- `PRIMARY_OWNER_STATUS_SOURCE` — estate-titled / life-tenant ownership
  condition (§3.5)
- `SUPPORTING_EVENT_SOURCE`, `ENRICHMENT_SOURCE`, `REFERENCE_SOURCE`
- `BLOCKED_SOURCE` — punch-listed with the exact blocker and the exact
  operator unlock needed (Cloudflare → cf_clearance cookie / login →
  credentials / paywall → subscription / CAPTCHA → solver / etc.).
- `REJECTED_SOURCE` — not official enough / not relevant / not useful.
  Distinct from BLOCKED — REJECTED has no actionable next step.

A BLOCKED source is NEVER a reason to call the county complete. The exact
unlock path is part of recon output.

### §1.6 — MISSED-SOURCE AUDIT

Before any county is called done, recon must perform a structured re-sweep:
- run the §1.1 + §1.2 catalog tables again, confirming each row has been
  investigated;
- prove the negative — every category lists either a build path or a
  punch-listed blocker;
- confirm no unprobed source remains.

A county with unprobed sources is not complete. The v5.5.0 county-build
contract test consumes the audit output.


## 01.27.1 §1.5 — OFFICIAL-VENUE TEST (v5.5.0 adjustment)

A third-party-hosted platform IS a `PRIMARY_EVENT_SOURCE` /
`PRIMARY_DEFAULT_SOURCE` when recon confirms it is the OFFICIAL VENUE
where the county (or its appointed officer — sheriff, trustee, tax
collector) statutorily CONDUCTS or PUBLISHES the distress event. Vendor
hosting does NOT disqualify a source. The RealAuction / RealForeclose /
RealTaxDeed family is the precedent — county-appointed auction platforms,
already canon as primary event sources.

The disqualifier is NOT "third-party domain." The disqualifier is
"marketplace re-listing." A platform that merely RE-LISTS or AGGREGATES
events conducted / published elsewhere, with its own derived status tags
(its own "pre-foreclosure" / "foreclosure" estimates), is REJECTED_SOURCE.

### The test, applied per-county in recon (never blanket)

Q1. Does the county / sheriff / trustee / tax collector officially
    conduct the sale, or publish the statutory notice, ON this platform?
       YES → PRIMARY (official venue).
       NO  → continue.
Q2. Does the platform re-list sales conducted elsewhere, or apply its
    own derived distress tags?
       YES → REJECTED (aggregator / marketplace re-listing).

### Conditional / unconditional decisions canonized in code

The framework ships an executable classifier
(`scaffold/pipeline/source_venue_classifier.py`) carrying three registries
that operationalize this test:

- **`KNOWN_OFFICIAL_VENUE_PLATFORMS`** — the Real* family (RealAuction,
  RealForeclose, RealTaxDeed, RealTDM, RealTDA), GovEase, Bid4Assets,
  CivicSource, PublicSurplus. Default verdict OFFICIAL_VENUE_PRIMARY.
  Per-county recon evidence is STILL required (the platform being on the
  list does NOT blanket-classify it as PRIMARY in every county); the
  registry is historical-precedent documentation.

- **`CONDITIONAL_PLATFORMS`** — platforms where the official-venue answer
  is variable enough that recon MUST record the specific evidence (the
  county/sheriff/trustee page that designates the platform as the
  official venue) before the source is admitted as PRIMARY. **Auction.com**
  is the canonical case: PRIMARY only in counties where recon documents
  the sheriff/trustee officially conducts the sale through Auction.com;
  REJECTED elsewhere. Required evidence fields:
  - `official_designation_url` — the official county/sheriff/trustee page
  - `officer_or_office` — which officer made the designation (`sheriff`,
    `trustee`, `tax_collector`, `clerk_of_court`)

- **`REJECTED_AGGREGATOR_PLATFORMS`** — Zillow, Trulia, Realtor.com,
  RealtyTrac, Foreclosure.com, Homes.com, Redfin, Movoto, Estately.
  Listings portals that conduct NO sales and publish NO statutory
  notices in any county; their distress tags are derived estimates.
  AGGREGATOR_REJECTED in all counties.

Unknown platforms (not in any registry) return UNKNOWN_PLATFORM and
recon must classify manually per the §1.5 test — they are NEVER
auto-promoted to PRIMARY.

### Still subject to qualification

A source admitted as PRIMARY by the OFFICIAL-VENUE TEST STILL passes the
normal qualification gate (§3.3 for tax-default rows, the §17
debtor-party engine for event rows, §3.5 for owner-status rows) and
STILL carries source proof. The OFFICIAL-VENUE TEST is necessary, not
sufficient.

### Recon orchestrator always wins

The classifier returns the DEFAULT verdict for a platform based on its
registry. The per-county recon orchestrator is the source of truth for
the FINAL verdict — a known-official-venue platform can still be
REJECTED for a specific county if recon discovers it's being used as a
marketplace re-listing in that jurisdiction, and the operator records the
downgrade rationale.

