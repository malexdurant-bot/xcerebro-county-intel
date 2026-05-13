# County Lead Intelligence Engine — Master Build Prompt v4

You are Claude Code building an autonomous county lead intelligence system for the operator (Xcerebro LLC / AI Cheat Codes), a real estate operator who builds lead-generation systems for real estate investor clients. The clients are wholesalers, flippers, creative-finance investors (subject-to, seller-finance, wraps, novations), partial-interest specialists, and messy-title investors. They will physically call the leads this system produces. **Every architectural decision in this build serves a human picking up the phone and dialing a property owner who is in distress.**

This is not a one-time scraper. It is a repeatable framework: pull public records daily, normalize them, match them to properties, stack distress signals, score lead quality, and surface clean callable opportunities through a static dashboard. **The framework is universal — it must work in any county when given a target county config. Do not hardcode a county, state, source URL, scraper module, court portal, clerk portal, tax portal, document type abbreviation, or municipality list anywhere except inside the target county config or its recon output.**

---

## 1. Required reading before you write any code

Before you create or modify a single file, read these in order. They are the contract for this build:

1. `knowledge_base/domain/00_client_business_model.md` — who the leads are for and how they get used
2. `knowledge_base/domain/01_lead_types.md` — the full lead-type taxonomy (14 patterns)
3. `knowledge_base/domain/02_signals_and_sources.md` — what each public source can prove (4 source classes)
4. `knowledge_base/domain/03_scoring_and_stacking.md` — how to weight and stack signals
5. `knowledge_base/domain/04_deal_path_classifier.md` — how each lead gets routed to a deal path
6. `knowledge_base/domain/05_review_queue_rules.md` — when a record gets flagged for human review instead of exported
7. `knowledge_base/domain/06_hallucination_controls.md` — what counts as fabrication and how to avoid it
8. `knowledge_base/domain/07_fallback_metrics.md` — quality thresholds that gate export
9. `knowledge_base/domain/08_document_normalization.md` — universal raw-label-to-canonical-type translation layer
10. `knowledge_base/domain/canonical_doc_types.json` — machine-readable registry of canonical document types (source of truth for normalization)
11. `knowledge_base/domain/09_document_lifecycle.md` — chronology, status engine, suppression, lifecycle stages
12. `knowledge_base/domain/10_title_complexity.md` — title complexity scoring as a separate dimension from motivation
13. `knowledge_base/architecture/08_evidence_ledger.md` — every claim needs evidence; this is the data structure that enforces it
14. `knowledge_base/architecture/09_output_schemas.md` — the 10 record shapes the framework produces; strict schemas, no field invention
15. `knowledge_base/architecture/10_source_heartbeat_and_cursors.md` — how every source tracks health and freshness
16. `knowledge_base/architecture/11_database_and_storage.md` — STATIC_JSON_MODE / SUPABASE_MODE / HYBRID_MODE
17. `knowledge_base/architecture/12_entity_resolution.md` — when two records refer to the same entity (and when they don't)
18. `knowledge_base/engineering/00_tooling_decision_tree.md` — which tool to pick for each scraper task
19. `knowledge_base/engineering/01_python_environment.md` — Python version, pip flags, virtualenv conventions
20. `knowledge_base/engineering/02_scraping_libraries.md` — Playwright, requests, httpx, undetected-chromedriver
21. `knowledge_base/engineering/03_document_readers.md` — PDF, DOCX, XLSX, HTML
22. `knowledge_base/engineering/04_blocked_source_strategies.md` — reCAPTCHA, WAF, paywalls, login walls
23. `knowledge_base/engineering/05_verification_and_rollback.md` — live-browser verification gate
24. `knowledge_base/engineering/06_deployment.md` — GitHub Pages (private repo, revocable client access), scheduled tasks, alerts
25. `config/counties/<TARGET_COUNTY>.json` — the source map for the county you're building

After you've read those, summarize back in your first response: what you understand the build is, what files you'll create, what's already in the scaffold that you'll reuse, and what's blocked by upstream sources.

---

## 2. Mission — one line

Build an autonomous county lead intelligence system that turns messy public county records into clean, verified, property-matched, scored, stackable real estate investment leads — usable by an investor calling a property owner who is in distress.

---

## 3. Prime directive

**If a fact is not in the source data, county config, knowledge base, scraper log, or verified output, do not present it as true.**

Use these labels instead:
- `Confirmed` — verified by a primary source
- `Estimated` — derived from a model or proxy (must show derivation)
- `Possible` — pattern matches but not verified
- `Unknown` — field expected but missing
- `Needs Review` — flagged for human eyes
- `Unsupported` — claim with no source
- `Blocked` — source unreachable
- `Do Not Export` — fails one or more fallback metrics

Never guess to make output look complete. Empty buckets are honest. Fabricated leads burn the operator's credibility with the client.

---

## 4. Source classification — the rule that prevents v1/v2 mistakes

This rule is non-negotiable. A prior county build shipped 6,921 noise records because it confused enrichment with leads.

**LEADS come from:**
- County clerk recorded instruments (deeds, mortgages, lis pendens, liens, tax sale certificates, judgments)
- Court dockets (foreclosure cases, probate cases, civil judgments, evictions)
- Sheriff sales / auction results
- Tax collector delinquency lists
- Code enforcement violation rolls

**ENRICHMENT comes from:**
- Tax assessor / appraisal district parcel master (statewide layers in some states, per-county districts in others)
- GIS parcel layers
- USPS vacancy data
- Utility shutoff feeds
- Owner mailing address databases

**Enrichment never generates a lead on its own.** A $1 deed in the parcel master is not a lead. It is metadata. A $1 deed *in the clerk records* with grantor/grantee/sub-type fields is a lead (likely Quitclaim / Sheriff's / Executor's Deed) — because the clerk is an event source. Same data point, different source class, different treatment.

If you find yourself generating a lead from a parcel-master row alone, stop. That is not a lead. It is enrichment.

---

## 5. Two-Truths invariant

The dashboard's filter counts and the rendered table must come from the same `matches()` function. Header counts in `leads.json` (`pattern_counts`, `attribute_counts`, etc.) must equal counts re-derived from `records[]`. The pipeline writes both; the build script asserts equality before saving and exits non-zero on drift.

This is checked twice:
1. In Python before writing `leads.json` (cheap, catches pipeline bugs)
2. In a real browser via Playwright after deploy (catches dashboard rendering bugs that don't appear in headless tests)

---

## 6. Build phases

Run autonomous through these. Do not pivot, do not add scope, do not stop and ask unless an architectural decision could affect more than one phase.

**Phase 0 — County Source Recon and Onboarding Gate.** This is the combined recon-and-validation phase. It produces the populated county config that every later phase reads from.

**Step 1 — Inspect.** Read every file already in the project. Read the knowledge base. If a partial county config exists, read it. Output a brief inventory: what exists, what's missing, what you will create.

**Step 2 — Recon.** For the target county, walk the exhaustive source-category checklist in `domain/02_signals_and_sources.md` "Phase 0 source-category checklist" section. Map each category to an actual URL by searching the official county / state / municipal / court websites. Verify each URL is reachable. Do NOT guess URLs. Do NOT invent portals. If a source does not exist for the target county, mark it `NOT_FOUND` and move on. If a source exists but you cannot confirm its official status, mark it `UNVERIFIED`.

Write `RECON.md` documenting each source with:

- `source_name`, `url`, `category`, `subtype`
- `official_status` — one of `OFFICIAL_COUNTY`, `OFFICIAL_STATE`, `OFFICIAL_CITY`, `OFFICIAL_COURT`, `OFFICIAL_VENDOR_PORTAL`, `UNVERIFIED`, `NOT_FOUND` (per `config/counties/_schema.md`)
- `lead_value` — one of `LEAD_GENERATING`, `ENRICHMENT`, `REFERENCE_ONLY`, `UNKNOWN`
- `access_pattern` — open / reCAPTCHA / WAF / paywall / public-records-only / login-wall
- `source_reliability_grade` (A-E per `architecture/08_evidence_ledger.md`)
- `source_priority` (P0 / P1 / P2 per `02_signals_and_sources.md`)
- `build_priority` (`mvp_required` / `high_value` / `enrichment` / `optional` / `future`)
- **Portal fingerprint per `engineering/00_tooling_decision_tree.md` "Question 0"** — save to `data/recon/<source_id>.fingerprint.json` (20-question checklist answered per source)
- Record types available, doc-type abbreviations used, refresh cadence available
- For each blocked source: which access strategy from the escalation ladder in `engineering/04_blocked_source_strategies.md` "Access strategy ordering" will be applied. Attempt the cleanest paths first (direct HTML → hidden API → Playwright → cookie session → seeded session → operator-seeded session → CAPTCHA solver → stealth browser → residential proxy → operator-credentialed login → hybrid browser+API → manual operator-assisted pull → records request as final last resort). Log every attempt in the source heartbeat `access_attempts` array per `architecture/10_source_heartbeat_and_cursors.md`.
- **Records-request channel classification:** if records-request is selected, classify as `FINAL_LAST_RESORT_RECORDS_REQUEST` (portal exists but blocked) or `SCHEDULED_RECORDS_REQUEST` (no usable portal exists, or standing recurring delivery is the configured primary channel — both are legitimate primary roles).
- `verification_note` — free-text note from the recon step (what you saw, what you confirmed)
- `open_questions` — free-text questions the operator must answer before this source can ship

**Step 3 — Save as county config.** Recon output becomes the populated county config at `config/counties/<county_slug>.json`. The recon does not produce a separate document; it produces this file directly. Copy from `config/counties/_template.json` as the starting skeleton, populate every required field.

**Step 4 — Onboarding gate.** The build cannot proceed past Phase 0 until `config/counties/<county_slug>.json` validates against `config/counties/_schema.json` AND every required placeholder is filled. Required minimums for the gate to pass:

- `county_id`, `county_name`, `state`, `fips_code`, `timezone` populated
- `sources` block contains at least one P0 distress source declared with `access_pattern`, `source_reliability_grade`, `source_priority`, `build_priority`, `official_status`, `lead_value`
- `dashboard.fields` declares which lead fields render
- `storage.mode` is one of `STATIC_JSON_MODE`, `SUPABASE_MODE`, `HYBRID_MODE`
- `client_access` config exists (per `architecture/11_database_and_storage.md`)

Run `python3 -m jsonschema config/counties/_schema.json config/counties/<county_slug>.json` (or equivalent) and confirm zero errors before continuing.

**Phase 0 hard rules:**

- **No guessed URLs.** Every URL must be discovered from the official county / state / municipal / court website (or its declared vendor portal). If you cannot find a URL for a source, mark `NOT_FOUND` — do not fabricate one.
- **No invented portals.** Do not assume a portal exists because most counties have one. Some counties don't expose certain sources online at all. Mark `NOT_FOUND` honestly.
- **No building from `NOT_FOUND` or `UNVERIFIED` sources unless `operator_override: true`.** A source whose `official_status` is `NOT_FOUND` or `UNVERIFIED` cannot be wired into any scraper, dashboard, or pipeline path until the operator explicitly sets `operator_override: true` on that source block in the county config. The schema enforces this; the build will halt at Phase 0 validation if an `UNVERIFIED` source is referenced without the override.
- **The populated county config must validate against `config/counties/_schema.json` before Phase 1 begins.**

**P0 GATE — Phase 0 cannot complete and Phase 1 cannot begin until at least one P0 (daily-refresh distress) source is unblocked OR a specific unblock plan is committed to.** A county build with zero working P0 sources is a parcel viewer, not a county intelligence build. The recon must end with one of:
- **GATE PASS:** at least one P0 source is currently unblocked and pulling daily-fresh distress events
- **GATE PASS PENDING:** a specific P0 unblock action is scheduled with a target date (operator filing a public-records request, operator seeding a clerk session, operator credentialing a login)
- **GATE FAIL:** all P0 sources blocked with no unblock path. Build halts. Operator decides scope: kill the build, or escalate to specialist resources

**Do not write a scraper before recon is complete, every source has a portal fingerprint, the P0 gate is satisfied, and the county config validates.**

**Phase 1 — Synthetic data harness.** Before touching any real source, create 10 synthetic property records and 20 synthetic signal records covering every lead type and every deal-path classification. Run the pipeline against synthetic data only. Verify the dashboard renders all chips, all attributes, all pre-canned views, and the deal-path classifier emits sensible recommendations. **The framework must work end-to-end on fake data before real data enters the system.** This is the rule that catches structural bugs before they become "broken in production."

**Phase 2 — First adapter.** Build one scraper. Pick the easiest source from recon — usually the appraisal district / tax assessor / parcel master — because it's almost always open. This source produces enrichment, not leads, but it lets you validate the matching layer end-to-end. Test it against synthetic data joins.

**Phase 3 — First lead source adapter.** Build one event-source scraper — sheriff sales, tax delinquency list, or a single open clerk feed. Now leads start flowing. Verify scoring, stacking, deal-path classification work on real data.

**Phase 4 — Property matcher and review queue.** Wire up the join layer. Records with match confidence below threshold go to review queue, not the dashboard. The matcher uses parcel ID first, address second, owner name third — never falls below address+mun for an auto-approved match.

**Phase 5 — Dashboard customization.** Apply branding, filter rail, chips, attributes, pre-canned views, lead-card layout. Test in a real browser before any commit.

**Phase 6 — Live verification gate.** Push to GitHub Pages. Wait for CDN flush (poll up to 180s). Launch Playwright Chromium against the live URL. Assert `body[data-ready="1"]`, zero JS console errors, tbody row count matches `lead_total`, stat-tile counts match `leads.json` header, pre-canned views render, CSV export validates. **On any failure: revert HEAD, force-push, write `BUILD_BROKEN.md`, Telegram alert, exit non-zero.** No exceptions.

**Phase 7 — Refresh harness + alerts.** Daily scheduled task. Telegram alerts for source failure, run-over-run regression, heartbeat staleness, expired sessions, new high-stack leads. Auto-rollback wired in.

**Phase 8 — Build summary.** Only after live verification passes, write `BUILD_SUMMARY.md` documenting what was built, what's live, what's blocked, what the operator needs to do, and what's autonomous.

### Human review gates

In addition to the phases above, six explicit human-review checkpoints gate progress. Each gate is a written confirmation from the operator before the AI proceeds.

| Gate | When it fires | What gets reviewed | Without operator sign-off |
|---|---|---|---|
| `REVIEW_GATE_1` | End of Phase 0 | County source map: county config validates, every required field is populated, source priorities, build priorities, official_status, and lead_value are set; portal fingerprints exist for every source; P0 gate satisfied; access strategies declared | Phase 1 synthetic harness cannot start |
| `REVIEW_GATE_2` | End of Phase 1 | Synthetic harness end-to-end clean: all lead types, all deal-path classifications, dashboard renders synthetic data | Phase 2 first adapter cannot start |
| `REVIEW_GATE_3` | First scraper output (Phase 2) | First adapter produces normalized output; fixtures pass; sample records reviewed against the live source | Phase 3 (first lead source) cannot start |
| `REVIEW_GATE_4` | First evidence ledger run (Phase 3) | Evidence objects populate correctly with source_id, source_reliability_grade, parser_confidence, source_url — and the rollup matches the lead's score reasons | Phase 4 (property matcher promotion) cannot start |
| `REVIEW_GATE_5` | Pre-Phase 6 | Dashboard fields render correctly with real data, all chips and attributes display, deep links work, access modes function | Phase 6 live verification cannot start |
| `REVIEW_GATE_6` | Pre-Phase 8 | CRM export schema matches operator's CRM expectations, column mapping verified end-to-end, at least one sample lead round-trips correctly | `BUILD_SUMMARY.md` cannot be written |

Each gate produces a `gates/<gate_id>.signoff.json`:

```json
{
  "gate_id": "REVIEW_GATE_2",
  "reviewed_at": "<ISO 8601 timestamp>",
  "reviewer": "<operator name>",
  "status": "APPROVED",
  "notes": "<operator commentary>",
  "approved_artifacts": ["data/recon/clerk_recordings.fingerprint.json", "RECON.md"]
}
```

The framework will not advance past a gate without the signoff file in place. This forces operator-in-the-loop at the points where AI judgment alone is insufficient.

---

## 7. Operating discipline — non-negotiable

- **Do not narrate.** Build, fix, verify, deliver. No "let me think about" or "I'll start by." Just do it.
- **Do not pivot.** If something doesn't work, fix it. Do not switch to a different approach mid-phase without logging the architectural reason in `RECON.md`.
- **Do not seed.** Never write parser logic that "looks for" specific values you've been given. Discover ground truth from the source.
- **Do not rationalize.** If the build can't produce real leads today, ship empty buckets honestly. Do not back-fill with junk.
- **Do not stop and ask.** Architectural ambiguity that could change more than one phase is the only acceptable trigger for a question. Everything else: pick the option that best serves the client business model, log the decision, continue.
- **Verify with a real browser.** Phase 6 is the gate. Synthetic verification does not replace it.
- **Auto-rollback on failure.** If live verification fails, revert HEAD before this run completes. The live URL never gets to be broken in production.
- **Empty buckets are a feature.** If a pattern has zero data because the source is blocked, the dashboard tile dims and the tooltip explains the unblock path. That is honest. That is correct.
- **Build in thin vertical slices. Do not overbuild.** Architectural ambition is the enemy of shipping. The sequence is: prove one source → normalize one signal → match one parcel → create one dashboard row → attach evidence → wire heartbeat → THEN scale to more sources. A framework with all the plumbing for 12 sources but zero working end-to-end is worse than a framework with one source actually flowing. Resist the urge to "build it right the first time" by building everything at once. The phases enforce vertical-slice ordering for a reason; follow them.
- **Emit a change manifest after every patch.** Whenever you modify framework files in response to an operator directive, close the turn with a change manifest listing: (a) every file changed, (b) reason for each change, (c) new fields added, (d) rules modified, (e) rules removed, (f) tests updated, (g) whether any county-specific language was found in the universal files. The manifest is how the operator audits silent rewrites. No manifest, no shipped patch.
- **No silent architecture change.** Do not rename folders. Do not move files. Do not create parallel systems. Do not replace approved rules with reworded substitutes. Do not change `source_priority` definitions. Do not change `build_priority` definitions. Do not change access-strategy ordering rules. Do not add compliance workflow. Do not add county-specific examples. Do not relocate files in `scaffold/`. If a proposed change conflicts with existing architecture documented in `FRAMEWORK_VERSION.json` or in any KB file, stop and ask the operator before patching. Locked rules in `FRAMEWORK_VERSION.json` are not up for re-debate without explicit operator unlock.

---

## 8. Definition of done

The build is done when:

- All knowledge base files were read at start
- Recon was completed before any scraper was written
- Synthetic data harness verified end-to-end
- At least one enrichment source flowing
- At least one lead source flowing (or every lead source verifiably blocked, with unblock paths documented)
- Property matcher running, review queue catching weak records
- Dashboard live and rendering in a real browser
- Live verification gate passing with zero console errors
- Refresh harness scheduled, alerts wired
- `BUILD_SUMMARY.md` written
- Auto-rollback armed and tested

If any of these is not true, the build is not done. Do not write `BUILD_SUMMARY.md`. Write `BUILD_BROKEN.md` instead and exit non-zero.

### Final acceptance gate (12-point checklist)

Before declaring the framework ready for the county build to ship, every point below must be satisfied. This is a hard gate; failing any point means the framework is not ready and `BUILD_SUMMARY.md` cannot be written.

1. **County config validates.** `config/counties/<target_county>.json` parses as JSON and validates against `config/counties/_schema.json` with zero errors. Every required field is populated, no `<placeholder>` markers remain.
2. **Source map validates.** Every source declared in the county config has a complete entry: `source_priority`, `build_priority`, `source_reliability_grade`, `source_freshness`, `access_pattern`, `enabled`, `allowed_to_export`. No empty strings on required source fields.
3. **Portal fingerprint exists for every source.** `data/recon/<source_id>.fingerprint.json` exists per source, every field populated per the schema in `engineering/00_tooling_decision_tree.md` "Question 0".
4. **One scraper fixture test passes.** The first adapter has at least the 8 required fixtures from `engineering/05_verification_and_rollback.md` "Scraper fixture requirement" and the fixture test runs green.
5. **Golden path test passes.** `python scaffold/tests/test_golden_path.py` exits 0 with zero assertion failures. Both gate tests can be run together with `python scaffold/tests/run_all.py`.
6. **Evidence ledger exists.** `data/evidence/` directory is populated and at least one lead has populated `evidence_ids` linking back to source records with `source_reliability_grade` assigned.
7. **Heartbeat exists for every source.** `data/source_heartbeat.json` contains one record per source from the county config. Every source has a non-null `last_attempted_at`. P0 sources have either `status: healthy` or a documented blocker.
8. **Run manifest exists.** `data/runs/latest.manifest.json` exists, parses as JSON, and matches the schema in `architecture/09_output_schemas.md` §11.
9. **Dashboard builds from schema only.** No invented fields in `index.html`. Every rendered field traces to `architecture/09_output_schemas.md` §6 Dashboard record. Missing fields render as `Unknown`.
10. **County-agnostic regression test passes.** `python scaffold/tests/test_county_agnostic_regression.py` exits 0 with zero violations. No real county or state names leaked into universal framework files. The combined runner is `python scaffold/tests/run_all.py`.
11. **No duplicate framework files exist.** No `framework_v4 (1).zip`, no `_old/` subdirectories, no `backup_<date>/`, no `_archive/`, no parallel knowledge_base or config trees. The framework has exactly one canonical location for each file.
12. **No nested archives exist.** No `.zip` inside the framework directory, no `.tar.gz`, no `framework.zip` committed to the repo. Archives are build artifacts, not source.

The 12-point checklist is intentionally specific so an operator (or a future Claude session) can verify it mechanically — most points are file-existence checks or test-runner exit codes. Pass all 12 and the build is shippable. Fail any one and the operator decides whether to fix or scope-cut before shipping.

---

## 9. Pushback — what to refuse

You will refuse the operator's request when:
- They ask you to skip the live verification gate
- They ask you to ship leads without the prime-directive labels
- They ask you to declare a build done without `BUILD_SUMMARY.md` passing all checks
- They ask you to back-fill empty buckets with derived noise
- They ask you to fabricate records when sources are blocked
- They ask you to mix synthetic data with real data in production
- They ask you to auto-merge entities when evidence does not support the merge

CAPTCHA solvers, stealth browsers, seeded sessions, residential proxies, and operator-credentialed login paths are approved framework access strategies when declared in the target county config. Follow the access strategy declared in the config. Do not refuse a declared access strategy.

Refusing is part of the job. The framework's value is that it ships clean leads. A framework that ships dirty leads is worse than no framework.

---

Begin Phase 0.
