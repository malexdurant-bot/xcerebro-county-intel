# County Lead Intelligence Engine — Framework v4

**Copyright © 2026 Xcerebro LLC. All rights reserved.**
Licensed under the proprietary Xcerebro LLC VIP license. See `LICENSE.md`. This Framework is not open source; access is limited to active Xcerebro LLC VIP members and approved licensees.

Reusable framework for building autonomous county-lead-intelligence dashboards in any county.

This is not a county-specific build. It is a portable shell. The only file that should change per county is the target county config inside `config/counties/`.

## What this framework does

**The product is fresh county-level distress intelligence with daily refresh.** The county is the moat. Daily refresh is non-negotiable. Fresh distress signals are the core asset. Enrichment data supports county intelligence; it never replaces it.

Every county built on this framework inherits:

**Distress ingestion (the moat):**
- Daily ingestion of fresh county distress filings: clerk recordings, court dockets, sheriff sales, code enforcement, tax delinquency
- Source priority tiers (P0 daily-distress / P1 weekly-distress / P2 enrichment) — Phase 0 build halts if no P0 source is unblocked
- Lifecycle reasoning over fresh filings — chronology, status engine, suppression of resolved signals (releases, satisfactions, discharges, dismissals)
- Source heartbeat and cursor tracking so daily refreshes don't duplicate or miss records
- Telegram alerts for new high-stack leads, source failures, session expiry, regressions

**Normalization and scoring:**
- Universal document normalization layer translating raw recorder/court abbreviations and OCR-corrupted text into canonical document types before scoring
- Source-classified, scored, deal-path-classified leads (wholesale / flip / sub-to / seller-finance / partial-interest / messy-title / rental-acquisition / dispo-only / do-not-pursue)
- Title complexity as a dimension separate from motivation, gating which deal paths are operationally viable
- Strict evidence ledger attached to every field and every claim

**Enrichment (supporting role only):**
- Entity resolution for individuals, LLCs, trusts, estates, parcels, addresses, cases, and instruments
- Parcel master / appraisal district enrichment for assessed value, equity proxy, owner mailing
- GIS / USPS vacancy / utility shutoff feeds where available

**Infrastructure:**
- GitHub private repo + GitHub Pages dashboard hosting (revocable client access)
- Optional Supabase database storage for production scale
- Live-browser verification with auto-rollback (broken dashboards never reach clients)
- Synthetic test harness before real county data enters the system

## Who this is for

Real estate operators who build lead-generation systems for investor clients. The framework's clients are wholesalers, flippers, creative-finance investors, partial-interest specialists, and messy-title investors. **They will physically call the leads this system produces.** Every architectural decision serves that.

## Universal rule

Do not hardcode a county. Do not hardcode a state. Do not carry assumptions from a previous county build. Each county is discovered from its own config and `RECON.md`.

## What's in this framework

```
framework_v4/
├── MASTER_PROMPT.md              # paste this into Claude Code to start a county build
├── MIGRATION.md                  # operator handoff — read this if you're using the framework
├── README.md                     # this file
│
├── knowledge_base/
│   ├── domain/                   # the WHAT — investor-side knowledge
│   │   ├── 00_client_business_model.md      # who the leads are for
│   │   ├── 01_lead_types.md                 # 14-pattern taxonomy
│   │   ├── 02_signals_and_sources.md        # lead / enrichment / negative-signal classification
│   │   ├── 03_scoring_and_stacking.md       # 0-100 scoring with reasons
│   │   ├── 04_deal_path_classifier.md       # routes to 9 deal paths
│   │   ├── 05_review_queue_rules.md         # quality gate
│   │   ├── 06_hallucination_controls.md     # anti-fabrication rules
│   │   ├── 07_fallback_metrics.md           # 12 quality thresholds
│   │   ├── 08_document_normalization.md     # raw recorder/court abbrev → canonical type
│   │   ├── 09_document_lifecycle.md         # chronology, status engine, suppression
│   │   ├── 10_title_complexity.md           # title complexity as separate dimension
│   │   └── canonical_doc_types.json         # machine-readable canonical type registry
│   │
│   ├── architecture/             # the CONTRACTS — data shape and integrity
│   │   ├── 08_evidence_ledger.md            # every claim needs evidence
│   │   ├── 09_output_schemas.md             # 10 strict record shapes
│   │   ├── 10_source_heartbeat_and_cursors.md  # source health and freshness
│   │   ├── 11_database_and_storage.md       # STATIC / SUPABASE / HYBRID
│   │   └── 12_entity_resolution.md          # when records refer to the same entity
│   │
│   └── engineering/              # the HOW — build-side knowledge
│       ├── 00_tooling_decision_tree.md      # which tool for which job
│       ├── 01_python_environment.md         # Python 3.12, pinned deps
│       ├── 02_scraping_libraries.md         # requests, Playwright, etc.
│       ├── 03_document_readers.md           # PDF, DOCX, XLSX, CSV, HTML
│       ├── 04_blocked_source_strategies.md  # reCAPTCHA, WAF, paywalls, login walls
│       ├── 05_verification_and_rollback.md  # live-browser gate + auto-rollback
│       └── 06_deployment.md                 # GitHub Pages, scheduled tasks
│
├── config/counties/              # per-county config — only thing that varies
│   ├── _schema.md                            # human-readable schema doc
│   ├── _schema.json                          # JSON Schema (validates configs)
│   └── _template.json                        # empty config to copy for new counties
│
└── scaffold/data/                # synthetic test harness
    ├── README.md
    ├── synthetic_parcels.jsonl               # 12 parcels covering all scenarios
    ├── synthetic_signals.jsonl               # 24 signals across all 11 patterns
    └── synthetic_expectations.json           # what the build should produce
```

## How to use it

1. Read `MIGRATION.md` end-to-end.
2. Create a private GitHub repo named `<county_id>-intel`.
3. Copy this directory into the new repo.
4. Copy `config/counties/_template.json` to `config/counties/<county_id>.json` and populate. **The template is intentionally not valid as a live county config until placeholders are filled.** It is a starting point. A copied real county config must validate against `_schema.json` before Phase 0 can pass.
5. Open Claude Code in the repo and paste `MASTER_PROMPT.md` as the first message.
6. Claude Code runs Phase 0 → Phase 8 autonomously.
7. Run the deployment checklist in `MIGRATION.md`.
8. The county is autonomous and refreshing daily.

## County build workflow

The framework's build sequence, phase by phase:

1. **Run Phase 0: County Source Recon and Onboarding Gate** — Walk the exhaustive source-category checklist in `knowledge_base/domain/02_signals_and_sources.md` "Phase 0 source-category checklist". For each category: discover the URL by following official navigation, verify it's reachable, classify `official_status` and `lead_value`, set `source_priority` and `build_priority`, produce a portal fingerprint per `knowledge_base/engineering/00_tooling_decision_tree.md` Question 0, and capture `verification_note` and `open_questions`.
2. **Save verified source map to `config/counties/<county_slug>.json`** — The recon's output IS the populated county config. Copy from `_template.json`, populate every required field per source.
3. **Validate county config** — Run `python -m jsonschema config/counties/_schema.json config/counties/<county_slug>.json`. Must exit 0. P0 gate: at least one P0 source must be unblocked or have a committed unblock plan. `UNVERIFIED` and `NOT_FOUND` sources require `operator_override: true`.
4. **Run portal fingerprinting** — confirm `data/recon/<source_id>.fingerprint.json` exists for every source; adapter modules are selected from the fingerprint.
5. **Build one thin vertical slice** — Phase 1 synthetic harness → Phase 2 first adapter (usually parcel master enrichment) → Phase 3 first lead source. Prove one source end-to-end before scaling.
6. **Run tests** — `python scaffold/tests/run_all.py` must exit 0 (golden path + county-agnostic regression). Adapter fixture tests must pass per `engineering/05_verification_and_rollback.md`.
7. **Build remaining sources** — Phase 4 property matcher + review queue. Add additional adapters in `build_priority` order.
8. **Deploy dashboard** — Phase 5 dashboard customization → Phase 6 live verification gate (Playwright against GitHub Pages) → Phase 7 refresh harness + alerts → Phase 8 `BUILD_SUMMARY.md`.

## How to run the gate tests

The framework ships with two gate tests that must pass before any build is considered shippable. Run them both with one command:

```
python scaffold/tests/run_all.py
```

Both tests can also be run individually if you want focused output:

```
python scaffold/tests/test_golden_path.py
python scaffold/tests/test_county_agnostic_regression.py
```

The runner exits 0 only when every test exits 0.

## Versioning

This is v4.0.0.

- Patch (4.0.1) — clarifications, doc fixes
- Minor (4.1.0) — new patterns, sources, deal paths, architecture additions
- Major (5.0.0) — breaking changes requiring migration of existing county builds

Each county's `BUILD_SUMMARY.md` records the framework version it was built against.

## What this framework refuses to do

(From `domain/06_hallucination_controls.md` and the master prompt)

- Skip the live verification gate
- Ship leads without prime-directive labels (Confirmed / Estimated / Possible / Unknown)
- Declare a build done without `BUILD_SUMMARY.md` passing all checks
- Back-fill empty buckets with derived noise
- Generate leads from parcel-master metadata alone
- Mix synthetic data with real data in production `leads.json`
- Auto-merge entities when evidence is weak

## License

**Copyright © 2026 Xcerebro LLC. All rights reserved.**

This Framework is proprietary software, not open source. Use is governed by the terms in `LICENSE.md`. Access is granted only to active Xcerebro LLC VIP members and approved licensees.

Permitted: building county lead-generation systems for the licensee's own operations or for the licensee's paying client projects; modifying the Framework for internal or client-specific use.

Prohibited: reselling, redistributing, publishing, sublicensing, uploading to a public repository, sharing outside the VIP group, repackaging as the licensee's own product, or using the Framework to create a competing framework, course, or automation product.

See `LICENSE.md` for the complete terms, including revocation conditions and the no-warranty clause.
