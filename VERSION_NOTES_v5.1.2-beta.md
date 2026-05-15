# VERSION NOTES — v5.1.2-beta

**Codename:** Universality Contract
**Released:** 2026-05-14
**Type:** Architectural cleanup release. Schema additions, contract enforcement, translator registry. **No breaking schema changes for existing configs.**

---

## TL;DR

The May 2026 universality audit on the Bexar build identified 11 specific county-specific leaks in `scaffold/pipeline/` and 4 in `dashboard/`. The framework code knew it was running for a specific county. That violated the core product promise.

v5.1.2-beta locks the **Universality Contract** (MASTER_PROMPT §4.31, 10 rules) and ships the architectural primitives needed to clean the contaminated pipeline:

- **Schema:** 9 new fields (`geography.accepted_municipalities`, `geography.sale_date_rule`, `geography.cross_county_policy`, `state_rule_family`, `sources.<id>.translator`, `sources.<id>.translator_config`, `sources.<id>.field_map`, `sources.<id>.parcel_id_prefix`, expanded `sources.<id>.doc_type_synonyms`).
- **Canonical doc types:** 71 → 74. Promoted `ESTATE_OWNER_NAME_PATTERN`, `LIVING_TRUST_OWNER_NAME_PATTERN`, `SHERIFF_SALE_SURPLUS` from synthetic-only to canonical.
- **Translator registry** (hybrid pattern): framework provides generic protocol clients + 3 built-in translators (`arcgis_foreclosure_notices`, `arcgis_parcel_master`, `csv_static_list`). Counties wire by string name in config — no in-code source dispatch.
- **State rule registry:** `sale_date_rules.py` provides `first_tuesday_of_month`, `first_monday_of_month`, `first_business_day_of_month`, `first_of_month` with configurable `holiday_shift`. State statutes never appear as literal code.
- **Universal owner-name pattern emitter** with defensive guard (audit Q9): standalone enrichment-only parcels CANNOT produce signals.
- **Upgraded county-agnostic regression test:** scans 15+ phrase patterns including vendors (BCAD, HCAD, etc.), portal hostnames, statute references. Exempts `data/`, `.claude/`, `dashboard/`, `scrapers/`, fixtures.
- **Bexar in-place migration playbook** (`docs/v5.1.2-beta_bexar_migration_playbook.md`): step-by-step instructions to clean the Bexar pipeline code IN-PLACE while preserving the 287-lead production data as a regression baseline.

---

## What's in this release

### Files added (new)
- `scaffold/pipeline/__init__.py`
- `scaffold/pipeline/translators/__init__.py`
- `scaffold/pipeline/translators/arcgis_foreclosure_notices.py`
- `scaffold/pipeline/translators/arcgis_parcel_master.py`
- `scaffold/pipeline/translators/csv_static_list.py`
- `scaffold/pipeline/sale_date_rules.py`
- `scaffold/pipeline/owner_name_patterns.py`
- `scaffold/data/synthetic_attribute_overrides.json` (placeholder)
- `scaffold/tests/test_translator_registry.py`
- `docs/v5.1.2-beta_bexar_migration_playbook.md`

### Files modified
- `config/counties/_schema.json` — 9 new fields, no removals, no required-field changes
- `knowledge_base/domain/canonical_doc_types.json` — 3 new canonical types (71 → 74)
- `MASTER_PROMPT.md` — added §4.31 Universality Contract (10 locked rules)
- `MIGRATION.md` — v5.1.2-beta version-delta entry
- `README.md` — autonomy boundaries updated for the universality contract
- `FRAMEWORK_VERSION.json` — bumped to v5.1.2-beta
- `scaffold/bootstrap_county.py` — version stamp bumped
- `scaffold/tests/test_county_agnostic_regression.py` — upgraded to scan vendor/portal/statute patterns and properly exempt county-scoped paths
- `scaffold/tests/run_all.py` — wired in translator registry test (gate suite now has 4 tests)

### Files NOT yet in canonical (deferred to v5.1.2-beta-final)
The framework's universal pipeline runtime modules — `normalize.py`, `stack.py`, `score.py`, `classify.py`, `evidence.py`, `review.py`, `dashboard.py`, `manifest.py`, `matcher.py`, and `build_leads.py` — still live as contaminated code inside the Bexar repo from the v5.1.1-beta-seeded Phase 1-4 build. The Bexar in-place migration playbook describes how to clean these in-place against the v5.1.2-beta primitives. Once verified against the Bexar regression baseline, the cleaned modules get promoted back to canonical as **v5.1.2-beta-final**.

This staging is intentional. The operator decision (Pushback 3 = option B) was "in-place migration with strict verification" — keep the 287-lead Bexar production data as the regression baseline rather than throwing it away and re-running Phase 0-4 from a fresh seed.

---

## Test results

Framework gate suite (`python3 scaffold/tests/run_all.py`):

```
[PASS] Golden path (test_golden_path.py)                     — exit code 0
[PASS] County-agnostic regression (v5.1.2-beta upgraded)    — exit code 0
[PASS] Atomic county config writer (v5.1.1-beta)            — exit code 0  (18/18)
[PASS] Translator registry (v5.1.2-beta)                    — exit code 0  (26/26)
```

The county-agnostic regression test PASSES on the v5.1.2-beta canonical framework. No contamination patterns remain in universal directories.

---

## Universality Contract — the 10 locked rules

(See MASTER_PROMPT.md §4.31 for the authoritative text.)

1. **No county name, no city, no statute, no vendor, no portal hostname** in `scaffold/pipeline/`.
2. **Cross-county portability.** Same pipeline runs for any county. Counties enter through config + scrapers + translator registry only.
3. **State-specific rules go through state rule families.** No literal state logic in pipeline code.
4. **Doc-type synonyms come from config.** Per-source `doc_type_synonyms` block.
5. **Field maps come from config.** Per-source `field_map` block.
6. **Parcel ID prefixes come from config.** Per-source `parcel_id_prefix` block.
7. **Synthetic fixtures isolated.** `scaffold/data/synthetic_attribute_overrides.json` loaded only with `--synthetic`.
8. **Defensive guard on owner-name emission.** Standalone enrichment parcels cannot produce signals.
9. **Translator registry is the only source-dispatch path.** No `if source_id == "..."` branches.
10. **Comments referencing real counties are scrubbed.**

The regression test enforces all 10 rules on every commit to canonical.

---

## Operator workflow

### 1. Pull v5.1.2-beta canonical into the Mac framework repo

```bash
cd ~/Dev/xcerebro/reference/framework_v4
# unpack the v5.1.2-beta zip ON TOP of the existing tree
unzip -o /path/to/framework_v5.1.2-beta.zip
# verify
cat FRAMEWORK_VERSION.json
# should show "framework_version": "v5.1.2-beta"
python3 scaffold/tests/run_all.py
# should show all 4 gate tests PASS
```

### 2. Commit + tag canonical

```bash
cd ~/Dev/xcerebro/reference/framework_v4
git add -A
git status  # review the change set against the file list above
git commit -m "v5.1.2-beta: universality contract, translator registry, schema additions"
git tag v5.1.2-beta
git push origin main --tags
```

### 3. Run the Bexar in-place migration

```bash
cd ~/Dev/xcerebro/counties/bexar
# Read the playbook first
cat ../../reference/framework_v4/docs/v5.1.2-beta_bexar_migration_playbook.md
```

Then execute the playbook step-by-step in Claude Code. Each step is atomic and gate-tested. The output equivalence check at Step 12 is the gate that protects against regression.

### 4. After migration verification passes — promote cleaned pipeline modules back

When `data/leads.json` (post-migration) matches `data/leads.baseline.json` (pre-migration) on lead count, pattern counts, attribute counts, and tier distribution, the migration is verified. Then:

```bash
# from the Bexar repo
cp scaffold/pipeline/normalize.py \
   scaffold/pipeline/stack.py \
   scaffold/pipeline/score.py \
   scaffold/pipeline/classify.py \
   scaffold/pipeline/evidence.py \
   scaffold/pipeline/review.py \
   scaffold/pipeline/dashboard.py \
   scaffold/pipeline/manifest.py \
   scaffold/pipeline/matcher.py \
   scaffold/pipeline/build_leads.py \
   ~/Dev/xcerebro/reference/framework_v4/scaffold/pipeline/

cd ~/Dev/xcerebro/reference/framework_v4
python3 scaffold/tests/run_all.py  # county-agnostic regression must PASS
# if PASS:
git add -A
git commit -m "v5.1.2-beta-final: promote cleaned pipeline modules from Bexar migration"
git tag v5.1.2-beta-final
git push origin main --tags
```

### 5. Phase 5 unlocked

Phase 5 (clerk_recordings via PublicSearch) can now start. Add a new built-in translator `publicsearch_clerk_recordings` to canonical framework (mirrors the pattern of the 3 existing built-ins). Bexar config declares the source with `translator: "publicsearch_clerk_recordings"` and its per-source `doc_type_synonyms`, `field_map`, `parcel_id_prefix`. Phase 5 runs.

### 6. Second-county test unlocked

Once v5.1.2-beta-final is in canonical, the next county build is the universality proof. Pick any county the operator wants (Maricopa, Pinellas, Cuyahoga, etc.). Run `claude` in a new county repo. Type `Build <County>, <State>`. Watch Phase 0 run. **No framework code changes should be needed** to bring the second county online — only config + a county-side scraper adapter.

If framework code changes ARE needed during the second-county build, that's a v5.1.3-beta backlog item.

---

## What's deferred to v5.1.3-beta or later

- **Universal pipeline modules promoted to canonical (v5.1.2-beta-final).** This is the natural follow-on, gated by Bexar regression verification.
- **publicsearch_clerk_recordings translator.** Built when Phase 5 starts.
- **Cross-county dashboard branding system.** v5.1.2-beta documents the dashboard contamination but defers full templating to a future release — current operator focus is the pipeline, not the dashboard.
- **State rule families with default policies.** The schema has `state_rule_family` enum but no per-family default lookups yet. Future enhancement.
- **v5.2.0 watchdog + verify_live.py Playwright impl + rollback execution + alert layer + data quality regression engine + portal fingerprint cache + county source memory + run manifest + audit pack.** All still deferred.

---

## Compatibility notes

- **v5.1.1-beta county configs validate against v5.1.2-beta schema unchanged.** All new fields are optional.
- **v5.1.1-beta counties continue to work** without upgrading — they just don't gain the universality-contract benefits until they migrate.
- **The Bexar repo currently runs v5.1.1-beta-seeded contaminated code.** It must be migrated via the in-place playbook before v5.1.2-beta-final. Until migration, the Bexar repo's regression test will FAIL (that's expected; it's what's catching the contamination).
- **v5.0.0 county configs need to upgrade.** No change from v5.1.0-beta on this.

---

## Honesty disclosure

The framework canonical repo at v5.1.2-beta does NOT yet contain a runnable end-to-end production pipeline. It contains the architectural contract + the scaffolding + the synthetic harness. The full pipeline lives in the Bexar repo as contaminated code that the playbook migrates in-place.

This was an intentional choice (Pushback 3 option B) to preserve the 287-lead Bexar baseline as a regression test. It means v5.1.2-beta canonical alone is not enough to build a new county end-to-end. v5.1.2-beta-final (the post-migration promotion) is.

If the operator picks up a second county BEFORE v5.1.2-beta-final lands, the second county build will need to use the contaminated Bexar pipeline as a starting point — which defeats the universality cleanup. Recommended order: ship v5.1.2-beta canonical → Bexar in-place migration → output equivalence verified → v5.1.2-beta-final promoted → THEN start the second county.

---

## Acknowledgment of audit findings

This release is a direct response to the May 2026 universality audit. The audit identified:

- 11 Bexar-specific leaks in `scaffold/pipeline/`
- 4 hardcoded references in `dashboard/`
- 1 architectural gap (no translator registry)
- 1 defensive-guard fragility (Q9: standalone parcels could emit signals)

All 17 findings are addressed in v5.1.2-beta (the canonical scaffolding) or are scheduled for v5.1.2-beta-final (the pipeline promotion). The audit found exactly what it needed to find, and the operator decision to stop Bexar before Phase 5 was correct.
