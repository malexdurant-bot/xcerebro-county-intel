# County Lead Intelligence Engine — Framework v4

**Copyright © 2026 Xcerebro LLC. All rights reserved.**
Licensed under the proprietary Xcerebro LLC VIP license. See `LICENSE.md`. This Framework is not open source; access is limited to active Xcerebro LLC VIP members and approved licensees.

Reusable framework for building autonomous county-lead-intelligence dashboards in any county.

This is not a county-specific build. It is a portable shell. The only file that should change per county is the target county config inside `config/counties/`.

---

## Quick start (v5.0.0+) — one sentence install

**First time using this framework?** Read [`START_HERE.md`](START_HERE.md) for the full first-run walkthrough. The short version:

1. **Clone or open** your private county-build repo (this repo).
2. **Open Claude Code** in the repo directory:
   ```
   claude
   ```
3. **Type one sentence** when Claude Code's prompt appears:
   ```
   Build <County Name>, <State>.
   ```
   Substitute your actual county and state. See `START_HERE.md` for a worked example.
4. Claude Code parses the target, shows the interpreted slug, asks approval to run the bootstrap script, and proceeds through Phase 0 autonomously.
5. **Approve the bootstrap once** when Claude Code asks. The bootstrap script creates `runs/<slug>/` and the launch instructions — nothing else.
6. **Watch Phase 0 run.** Claude Code performs County Source Recon and the Onboarding Gate. When it stops, it prints a change manifest. Review the manifest before authorizing Phase 1.

That's it. No PowerShell commands beyond `claude`. No JSON to edit by hand. No manual launch file to create.

> **Why the one-time approval click?** Claude Code asks before running shell commands by default — that's protection against destructive operations. The autonomous first-run grant is bounded to `scaffold/bootstrap_county.py` only, which creates a folder and a markdown file. Approve once and the bootstrap runs in seconds.

### Autonomy boundaries (v5.1.2-beta)

The first run is autonomous in the sense that you type one sentence and approve one bootstrap. Everything after that — source recon, 5-layer verification gate, auto-resolve of blockers, config writing, and the change manifest — runs hands-off.

There are three boundaries operators should know about:

1. **Claude Code may ask for approval to run bounded scripts.** During Phase 0, Claude Code may request permission for `web_search`, `web_fetch` against official portal domains, and one Python script call to atomically write the populated county config (via `scaffold/ops/write_county_config.py` — see MASTER_PROMPT Section 4.28). Approve broadly; the scope is bounded to the current county repo.
2. **Claude Code stops on config-write failure.** If the writer returns `JSON_INVALID` or `SCHEMA_INVALID`, Claude Code attempts exactly one structured repair and then stops with `CONFIG_WRITE_FAILED` if the second attempt also fails. It does NOT silently proceed. Open the resulting `runs/<slug>/CONFIG_WRITE_FAILED.md` for diagnosis.
3. **The framework is universal; the county is configured.** v5.1.2-beta locks in a hard contract (MASTER_PROMPT Section 4.31) that universal pipeline code never contains county-specific data. Counties enter the pipeline through `config/counties/<slug>.json`, `scrapers/<source>.py`, and the translator registry — never through hardcoded source dispatch or in-code municipality lists. This means the same `scaffold/pipeline/` runs for any county.

Phase 0 ends at a Build Mode Approval Gate. Build Mode (scrapers, dashboards, deployment) only starts when the operator explicitly authorizes it.

---

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

## How to use it (detailed)

**For first-time use, see the Quick Start at the top of this README or read `START_HERE.md`.** The flow below is the manual / advanced operator path used when bootstrap autonomy is not desired (e.g. CI environments, custom slug conventions, or scripted builds).

1. Read `MIGRATION.md` end-to-end.
2. Create a private GitHub repo named `<county_id>-intel-v4` (the `-v4` suffix is the framework-version convention).
3. Copy this directory into the new repo.
4. **Either** run `python scaffold/bootstrap_county.py --county "<Name>" --state "<State>" --slug <slug> --phase phase0` (recommended), **or** manually copy `config/counties/_template.json` to `config/counties/<slug>.json` and populate it. The template is intentionally not valid as a live county config until placeholders are filled.
5. Open Claude Code in the repo and paste this:
   ```
   Read MASTER_PROMPT.md and runs/<slug>/LAUNCH_<SLUG_UPPER>.md. Run Phase 0 only.
   ```
6. Claude Code runs Phase 0 → review change manifest → operator authorizes Phase 1 → Claude Code runs Phase 1 → review → and so on.
7. Run the deployment checklist in `MIGRATION.md` after Phase 8 completes.
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

This is **v5.0.0**.

- Patch (5.0.1) — clarifications, doc fixes
- Minor (5.1.0) — new patterns, sources, deal paths, architecture additions
- Major (6.0.0) — breaking changes requiring migration of existing county builds

**v5.0.0 added** (released 2026-05-13):

- **Five-Layer Source Verification Gate** (MASTER_PROMPT Section 4.7) — every source goes through Official Origin → Source Category → Data Access → Lead Value / Source Role → Portal Proof verification before being trusted.
- **Source proof packet** — 18 new fields per source recording the verification outcome.
- **Build Eligibility Gate** (Section 4.10) — Phase 0 produces a `build_verdict` (`READY_TO_BUILD` / `READY_WITH_BLOCKERS` / `RECON_ONLY` / `WAITING_ON_ACCESS` / `NOT_BUILDABLE_YET`). Build Mode does not start without authorization.
- **Do Not Proceed Matrix** (Section 4.11) — 11 conditions that halt Phase 0 with a diagnostic verdict.
- **No False Dashboard rule** — a dashboard row is created by a lead event, never by a parcel record alone.
- **Source Hierarchy** (Section 4.9) — Tier 1 primary lead / Tier 2 supporting / Tier 3 enrichment. Only Tier 1 creates leads.
- **Recon Mode vs Build Mode** (Section 4.6) — first run is always Recon Mode.
- **VIP-friendly verdict message** (Section 4.12) — plain English Phase 0 output.
- **Operator-readable lead names rule** (Section 4.13) — no raw clerk codes in operator-facing surfaces.
- **Schema breaking changes** — 26 new source-level fields, 3 new top-level fields, 7 new enum types. v4.x configs require Phase 0 re-recon to populate new proof packet fields.

**v4.1.0 added** (preserved in v5.0.0): the one-sentence install flow, `scaffold/bootstrap_county.py`, `START_HERE.md`, `MASTER_PROMPT.md` Section 4.5 (autonomous first-run rule), and the `runs/<slug>/` directory convention.

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
