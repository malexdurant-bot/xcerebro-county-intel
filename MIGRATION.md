# Framework v4 — How to Use This for Any County

This is the operator-facing handoff document. If you're sitting down to build a new county on this framework, start here.

The framework is universal. It is not built for any specific county. It is designed to be copied into a new private repo, pointed at a target county config, and allowed to discover the county's source landscape from recon.

---

## What this framework is

A reusable system for building autonomous county-lead-intelligence dashboards. Every county built on this framework inherits:

- Daily refresh from county clerk / court / sheriff / tax / code sources
- Source-classified, scored, deal-path-classified leads
- Strict evidence ledger attached to every field and every claim
- Source heartbeat and cursor tracking so refreshes do not duplicate or miss records
- Six client deal-path classifications (wholesale / flip / sub-to / seller-finance / partial-interest / messy-title)
- Live-browser verification with auto-rollback (broken dashboards never reach clients)
- Telegram alerts for source failures, regressions, session expiry, new high-stack leads
- Watchdog every 6 hours catching post-deploy drift
- Entity resolution for individuals, LLCs, trusts, estates, parcels, addresses, cases, and instruments
- Synthetic test harness before real county data enters the system
- STATIC_JSON_MODE / SUPABASE_MODE / HYBRID_MODE storage options

The framework codifies what was learned across earlier county builds. Every county built on it inherits those lessons by default. **Earlier county builds remain as learning artifacts and are not retrofitted.**

---

## Universal rule

**Do not hardcode a county. Do not hardcode a state. Do not carry assumptions from a previous county build into a new one.**

Every new county is discovered from its own config and its own `RECON.md`. The framework's universal files (knowledge base, schema, scaffold) do not change per county. The only file that varies per county is `config/counties/<county_id>.json`.

---

## The workflow at a glance

```
1. Pick a county (any county)
2. Create a new private GitHub repo named <county_id>-intel
3. Copy this framework into the repo
4. Copy config/counties/_template.json to config/counties/<county_id>.json
5. Open Claude Code in the repo
6. Paste MASTER_PROMPT.md as the first message, with target county set
7. Let Claude Code run Phase 0 → Phase 8 autonomously
8. Run the deployment checklist (knowledge_base/engineering/06_deployment.md)
9. County is live and refreshing daily
```

---

## Step-by-step

### Step 1: Create the new repo

```bash
cd C:\Dev\xcerebro-builds\projects
mkdir <county_id>-intel
cd <county_id>-intel
git init
gh repo create xcerebroai/<county_id>-intel --private --source=. --remote=origin
```

The repo stays private. Client access is granted by GitHub-account add or revoked by removing access. GitHub Pages serves the dashboard from this private repo.

### Step 2: Copy the framework scaffold

```bash
xcopy /E /I C:\Dev\framework_v4\knowledge_base knowledge_base
xcopy /E /I C:\Dev\framework_v4\config config
xcopy /E /I C:\Dev\framework_v4\scaffold scaffold
copy C:\Dev\framework_v4\MASTER_PROMPT.md MASTER_PROMPT.md
```

### Step 3: Copy the template config and rename

```bash
copy config\counties\_template.json config\counties\<county_id>.json
```

Edit the new file to set:
- `county_id`
- `county_name`
- `state`
- `subject_state_full`
- `fips_code`
- `timezone`
- `geography.municipalities` (full list, with codes and FIPS place codes)
- `geography.parcel_id_format` (regex)
- `dashboard.title`
- `deployment.github_repo`
- `deployment.live_url`
- `deployment.scheduled_task_name`
- `deployment.watchdog_task_name`
- `storage.mode` (default STATIC_JSON_MODE; switch to SUPABASE_MODE or HYBRID_MODE if needed)

The `sources` block can be left mostly empty at this stage. Phase 0 recon fills in URLs, access patterns, field maps, and doc-type synonyms per source.

### Step 4: Open Claude Code in the repo

```bash
cd C:\Dev\xcerebro-builds\projects\<county_id>-intel
claude
```

### Step 5: Paste the master prompt

In Claude Code, paste the contents of `MASTER_PROMPT.md` as the first message, and append:

```
Target county: <county_id>
Begin Phase 0.
```

Claude Code will:
1. Read all required-reading files + the county config
2. Summarize back what it understands
3. Run Phase 0 (combined source recon + onboarding gate — produces validated county config)
4. Run Phase 1 (synthetic data harness against fake data)
5. Run Phase 2 (first adapter — usually parcel master enrichment)
6. Run Phases 3-7 (scrapers, matching, scoring, dashboard, verification)
7. Run Phase 7 (refresh harness + alerts)
8. Run Phase 8 (build summary)

Each phase reports completion. If a phase fails, Claude Code stops and writes `BUILD_BROKEN.md`. The operator investigates, fixes, restarts.

### Step 6: Enable GitHub Pages

After the first commit lands:

1. Go to `https://github.com/xcerebroai/<county_id>-intel/settings/pages`
2. Source: Deploy from a branch
3. Branch: `main`, folder `/` (root)
4. Save

Pages flushes in 1-3 minutes. The repo stays private; Pages serves through GitHub's auth.

### Step 7: First live verification

```bash
py -3.12 pipeline\verify.py
```

If it passes: `LIVE_VERIFIED.txt` is committed. The dashboard is live.

If it fails: `BUILD_BROKEN.md` is written and HEAD reverts. Read the broken file, fix the issue, re-run.

### Step 8: Register scheduled tasks

```powershell
schtasks /create /xml scripts\daily_refresh.xml /tn "<county_id>-intel-refresh" /ru <COMPUTERNAME>\<USER> /rp <password> /f
schtasks /create /xml scripts\watchdog.xml /tn "<county_id>-intel-watchdog" /ru <COMPUTERNAME>\<USER> /rp <password> /f
```

The framework writes the XML files; the operator provides the password (the autonomous build cannot acquire it).

### Step 9: Configure Telegram

Add to `.env`:

```
TELEGRAM_BOT_TOKEN=<from BotFather>
TELEGRAM_CHAT_ID=<from getUpdates after bot creation>
```

Smoke-test:
```bash
py -3.12 -c "from pipeline.alerts import telegram_send; telegram_send('test from <county_id>-intel')"
```

### Step 10: Smoke-test the daily refresh

```powershell
schtasks /run /tn "<county_id>-intel-refresh"
```

Wait 5-10 minutes. Confirm:
- The task ran (Task Scheduler history)
- A new commit landed
- The dashboard refreshed

If yes: the county is autonomous.

---

## What the operator does ongoing per county

**Weekly:**
- Re-seed any seeded-session sources (~30 sec each in Chrome → copy cookies → paste to `.env`)

**Monthly:**
- Sign and email any standing public-records requests

**As-needed (driven by Telegram alerts):**
- "Re-seed clerk session within 24h" → re-seed within 24h
- "Source layout changed for X" → look at scrape log, fix parser
- "Build auto-rolled back" → read `BUILD_BROKEN.md`, fix forward

That's the ongoing operator workload per county. Otherwise autonomous.

---

## Client access model

GitHub Pages hosts the dashboard from the private repo. The client gets:
- A bookmark to `https://xcerebroai.github.io/<county_id>-intel/`
- Read access to the repo (or no repo access — Pages can be served to clients without granting repo access via GitHub auth)

**Revocation paths** when a client stops paying or otherwise needs to lose access:
1. Disable Pages on the repo
2. Remove client's GitHub access
3. Replace `data/leads.json` with a placeholder
4. Disable the daily refresh scheduled task (dashboard goes stale)

The framework's hosting model assumes paid client access. Revocation paths are part of the product.

---

## What the framework refuses to do

(From `domain/06_hallucination_controls.md` and `MASTER_PROMPT.md`)

- Skip the live verification gate
- Ship leads without prime-directive labels (Confirmed / Estimated / Possible / Unknown)
- Declare a build done without `BUILD_SUMMARY.md` passing all checks
- Back-fill empty buckets with derived noise
- Generate leads from parcel-master metadata alone
- Mix synthetic data with real data in production `leads.json`
- Auto-merge entities when evidence is weak (always route to review)

---

## How to extend the framework

Three ways the framework can grow:

### A. Add a new pattern or subtype

Edit `domain/01_lead_types.md` (add pattern + subtypes + deal-path mapping), `domain/03_scoring_and_stacking.md` (base scores), `domain/04_deal_path_classifier.md` (rules), `scaffold/data/synthetic_signals.jsonl` (test coverage), `scaffold/data/synthetic_expectations.json` (expected counts). Run Phase 1 against synthetic data to confirm.

### B. Add a new source type

Edit `domain/02_signals_and_sources.md` (classification), `engineering/00_tooling_decision_tree.md` (access pattern), `config/counties/_schema.json` (subtype enum). Each county adds the source to its config.

### C. Add a new client persona / deal path

Edit `domain/00_client_business_model.md` (persona), `domain/04_deal_path_classifier.md` (rules), `domain/01_lead_types.md` (pattern matrix), `scaffold/data/synthetic_expectations.json` (path distribution).

---

## Versioning

This is **v5.0.0**.

**v5.0.0 changes from v4.1.0** (breaking schema change — v4.x configs need Phase 0 re-recon):

- Added five-layer Source Verification Gate to MASTER_PROMPT.md (Sections 4.6–4.13)
- Added 26 new source-level proof packet fields to `config/counties/_schema.json`
- Added 3 new top-level fields: `build_verdict`, `build_verdict_reason`, `build_verdict_at`
- Added 7 new enum types: `access_method` (17 values), `public_access_status` (12 values), `document_access_status` (7 values), `source_role` (6 values), `verification_confidence` (5 values), `verification_method` (8 values), `next_access_strategy` (15 values). Plus widened `official_status` to a 5-way `OFFICIAL_*` split.
- Added Build Eligibility Gate semantics — Phase 0 produces a build verdict
- Added Do Not Proceed Matrix — 11 conditions that halt Phase 0
- Added No False Dashboard rule — dashboard rows must come from lead events
- Added Source Hierarchy — Tier 1 primary lead / Tier 2 supporting / Tier 3 enrichment
- Added Recon Mode vs Build Mode distinction
- Added VIP-friendly verdict message format
- Added Operator-readable lead names rule
- Updated `scaffold/bootstrap_county.py` launch file template to reference v5.0.0 gates (bootstrap script logic unchanged)
- Bumped `FRAMEWORK_VERSION.json` to `v5.0.0`

**This is a breaking schema change.** A v4.x county config does not validate against the v5.0.0 schema until the proof packet fields are populated. Empty defaults in `_template.json` are accepted during recon; Phase 0 populates them through the verification gate.

**v4.1.0 features preserved:** the one-sentence install flow, `scaffold/bootstrap_county.py`, `START_HERE.md`, autonomous first-run rule (MASTER_PROMPT Section 4.5), and the `runs/<slug>/` directory convention.

- Patch (4.1.1) for clarifications, doc fixes
- Minor (4.2.0) for new patterns, sources, deal paths, architecture additions
- Major (5.0.0) for breaking changes that require migration of existing county builds

Each county's `BUILD_SUMMARY.md` records which framework version it was built against.

---

## Files in this framework

**Beginner entry point (v4.1.0+):**
- `START_HERE.md` — first-time-user walkthrough. Read this before anything else on your first run.

**Master entry point:**
- `MASTER_PROMPT.md` — the framework contract Claude Code reads on every build. Section 4.5 documents the autonomous first-run rule.

**Domain knowledge base** (the *what*):
- `knowledge_base/domain/00_client_business_model.md`
- `knowledge_base/domain/01_lead_types.md`
- `knowledge_base/domain/02_signals_and_sources.md`
- `knowledge_base/domain/03_scoring_and_stacking.md`
- `knowledge_base/domain/04_deal_path_classifier.md`
- `knowledge_base/domain/05_review_queue_rules.md`
- `knowledge_base/domain/06_hallucination_controls.md`
- `knowledge_base/domain/07_fallback_metrics.md`

**Architecture knowledge base** (the *contracts*):
- `knowledge_base/architecture/08_evidence_ledger.md`
- `knowledge_base/architecture/09_output_schemas.md`
- `knowledge_base/architecture/10_source_heartbeat_and_cursors.md`
- `knowledge_base/architecture/11_database_and_storage.md`
- `knowledge_base/architecture/12_entity_resolution.md`

**Engineering knowledge base** (the *how*):
- `knowledge_base/engineering/00_tooling_decision_tree.md`
- `knowledge_base/engineering/01_python_environment.md`
- `knowledge_base/engineering/02_scraping_libraries.md`
- `knowledge_base/engineering/03_document_readers.md`
- `knowledge_base/engineering/04_blocked_source_strategies.md`
- `knowledge_base/engineering/05_verification_and_rollback.md`
- `knowledge_base/engineering/06_deployment.md`

**Config:**
- `config/counties/_schema.md` — schema reference (human-readable)
- `config/counties/_schema.json` — JSON Schema (validates configs)
- `config/counties/_template.json` — empty config to copy for new counties

**Bootstrap and tests (scaffold):**
- `scaffold/bootstrap_county.py` — v4.1.0+ bounded bootstrap script. Creates `runs/<slug>/` and the launch file. Claude Code is authorized to run this automatically on first contact per `MASTER_PROMPT.md` Section 4.5.
- `scaffold/tests/run_all.py` — runs both gate tests
- `scaffold/tests/test_golden_path.py` — happy-path build gate
- `scaffold/tests/test_county_agnostic_regression.py` — enforces no hardcoded county names outside config/counties/ and LICENSE.md

**Synthetic test harness:**
- `scaffold/data/synthetic_parcels.jsonl` — 12 parcels covering every scenario
- `scaffold/data/synthetic_signals.jsonl` — 24 signals across all 11 patterns
- `scaffold/data/synthetic_expectations.json` — what the build should produce
- `scaffold/data/README.md` — how the harness works

**Per-county artifacts (v4.1.0+ convention):**

The framework distinguishes two locations for county-specific files:

- **Canonical config:** `config/counties/<county_slug>.json` — the single source-of-truth source map for the county. Produced by Phase 0 recon. Validated against `_schema.json`. Committed to the repo.

- **Run artifacts:** `runs/<county_slug>/` — county-specific launch files, run manifests, temporary logs, ad-hoc operator notes, and anything else that's county-specific but NOT part of the canonical config. The bootstrap script creates this folder. Phase manifests and operator notes accumulate here over time.

Examples of what goes where:

| File | Location |
|---|---|
| The verified source map | `config/counties/bexar_tx.json` |
| Phase 0 launch instructions | `runs/bexar_tx/LAUNCH_BEXAR_TX.md` |
| Phase 0 change manifest | `runs/bexar_tx/PHASE0_MANIFEST.md` |
| Notes from a recon session | `runs/bexar_tx/notes.md` |
| Build-specific logs (not committed) | `runs/bexar_tx/logs/` (add to `.gitignore`) |

**Why the separation:** the canonical config is what every later phase reads from. It must stay clean and validate-able. Run-folder content is operator scratchpad — useful, county-specific, but never part of the framework's data contract.

**This file:**
- `MIGRATION.md` — operator-facing instructions

