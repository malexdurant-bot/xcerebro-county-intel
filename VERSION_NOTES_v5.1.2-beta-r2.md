# VERSION NOTES — v5.1.2-beta-r2

**Release date:** 2026-05-15
**Type:** Beta revision (corrective)
**Predecessor:** v5.1.2-beta (2026-05-14)
**Successor:** v5.1.2-beta-final (planned, post Bexar migration completion)

---

## TL;DR

v5.1.2-beta shipped translator primitives that assumed scrapers were
pass-through wrappers around raw vendor protocol output. The first
in-place migration (Bexar) discovered that pre-v5.1.2-beta scrapers
actually normalize at scrape time. The framework had not declared which
of the two paths is canonical, so the translator design was incomplete.

**v5.1.2-beta-r2 locks the contract: scrapers normalize, translators
consume normalized output.** Translators rewritten. Vendor-protocol
prefix dropped from translator names. New §4.32 in MASTER_PROMPT.

---

## What changed from v5.1.2-beta

### 1. New section: MASTER_PROMPT.md §4.32 — Scraper-to-translator data contract

Locks "Path 1": scrapers normalize, translators consume normalized.

Every `data/raw/<source_id>.jsonl` record must conform to:

```json
{
  "raw_record_id": "...",
  "source_id": "...",
  "source_url": "...",
  "source_fetched_at": "...",
  "parser_confidence": <0..100>,
  "raw_payload": {
    "<framework-canonical lowercase field>": <normalized value>
  }
}
```

Top-level fields = framework metadata. `raw_payload` = source-specific
data, but with NORMALIZED (framework-canonical lowercase) field names.

Translators read `raw_payload`. They never read top-level fields except
the metadata explicitly contractually exposed (raw_record_id, source_url
for evidence chains, parser_confidence for downstream scoring).

### 2. Translator rename

| Old name (v5.1.2-beta) | New name (v5.1.2-beta-r2) |
|---|---|
| `arcgis_foreclosure_notices` | `foreclosure_notices` |
| `arcgis_parcel_master` | `parcel_master` |
| `csv_static_list` | (unchanged) |

Reason: translators no longer know what protocol the underlying scraper
used. They consume normalized output regardless of whether the scraper
pulled via REST API, public-records portal, court e-portal, or static
CSV. The vendor-protocol prefix was misleading.

Files renamed under `scaffold/pipeline/translators/`. Old `arcgis_*.py`
modules removed.

Schema enum (`config/counties/_schema.json` `sources.<id>.translator`)
updated. Old names removed from enum. County configs declaring
`translator: "arcgis_foreclosure_notices"` MUST update to
`translator: "foreclosure_notices"` (and similarly for parcel_master).

### 3. Translator implementations rewritten

**`foreclosure_notices.py`:**
- Reads `raw_payload.address`, `raw_payload.doc_number`,
  `raw_payload.recording_year`, `raw_payload.recording_month`,
  `raw_payload.city`, `raw_payload.zip`, `raw_payload.layer_id`
  (lowercase, no underscore prefix on layer_id).
- Translator config still uses `layer_doc_type_map` (which doc-type each
  layer dispatches to) — no change in semantics.
- Cross-county-leak detection per `geography.accepted_municipalities`
  and `geography.cross_county_policy` — unchanged.
- Sale-date derivation via `geography.sale_date_rule` registry —
  unchanged.

**`parcel_master.py`:**
- Reads `raw_payload.parcel_id`, `raw_payload.address`,
  `raw_payload.owner_name`, and all enrichment fields with their
  framework-canonical names directly (no `field_map` required for new
  scrapers that normalize during scrape).
- Prefers pre-parsed boolean fields (`exempt_homestead`,
  `exempt_over_65`, `exempt_disabled`, `exempt_veteran`) from
  raw_payload.
- Legacy-compatibility path: if booleans absent and raw `exemptions`
  string present, falls back to `translator_config.exemption_codes`
  map for string parsing.
- Returns `(signals=[], parcels, meta={})` — parcel_master is enrichment,
  not lead-generating.

**`csv_static_list.py`:**
- Already used per-source field config. Docstring updated to document
  contract α; behavior unchanged. Accepts both wrapped and (legacy) flat
  records for CSV-ingest compatibility.

### 4. test_translator_registry.py rewritten

39 tests (was 26). Covers the new contract:
- Builtin registration under new names. Asserts old `arcgis_*` names
  are NOT registered.
- `foreclosure_notices` consumes normalized lowercase payload.
- `foreclosure_notices` cross-county-leak policy (drop / flag).
- `foreclosure_notices` sale_date_rule dispatch.
- `parcel_master` boolean-exemption fast path.
- `parcel_master` legacy exemption-string fallback.
- `parcel_master` skips records with empty parcel_id.
- `csv_static_list` per-source doc_type_synonyms (unchanged).
- `csv_static_list` skip-on-unknown-doc-type (unchanged).

### 5. Bexar migration playbook updated

`docs/v5.1.2-beta_bexar_migration_playbook.md` Step 4 rewritten:
- Translator declarations use new names.
- `translator_config` field-name references updated to lowercase
  normalized (or removed entirely where translators read canonical
  names directly).
- New sub-step: one-time deterministic shape transform of
  `data/raw/parcel_master.jsonl` from flat to wrapped. Preserves
  baseline reproducibility (no data drift).

### 6. Version stamps bumped

- `FRAMEWORK_VERSION.json`: `"framework_version": "v5.1.2-beta-r2"`,
  `"locked_at": "2026-05-15"`.
- `scaffold/bootstrap_county.py`: `FRAMEWORK_VERSION = "v5.1.2-beta-r2"`.

### 7. Gate suite

All 4 gate tests PASS:

```
[PASS] Golden path (test_golden_path.py)                              — 46 pass, 0 fail
[PASS] County-agnostic regression (test_county_agnostic_regression.py) — zero violations
[PASS] Atomic county config writer (test_write_county_config.py)      — 18 pass, 0 fail
[PASS] Translator registry (test_translator_registry.py)              — 39 pass, 0 fail
RESULT: PASS
```

---

## What did NOT change

- §4.31 Universality Contract (the 10 locked rules) — unchanged.
- Schema additions (accepted_municipalities, cross_county_policy,
  sale_date_rule, state_rule_family, sources.<id>.translator_config,
  sources.<id>.field_map, sources.<id>.parcel_id_prefix,
  sources.<id>.doc_type_synonyms) — unchanged in structure. Only the
  enum values inside `sources.<id>.translator` changed (renamed).
- canonical_doc_types.json — 74 types, unchanged.
- sale_date_rules.py registry — unchanged.
- owner_name_patterns.py with defensive guard — unchanged.
- Upgraded test_county_agnostic_regression.py scanner — unchanged
  behavior, still scans phrase blocklists across universal directories.
- All v5.1.1-beta and v5.1.0-beta features preserved.

---

## Breaking changes

**Only one, and it's narrow:**

County configs that declare `sources.<id>.translator: "arcgis_foreclosure_notices"`
or `"arcgis_parcel_master"` will fail schema validation against the
updated enum. The fix is a one-line edit to use the new name.

In practice, no production county had v5.1.2-beta translators wired —
Bexar was mid-migration and is the only county that had the old names
in any working tree. v5.1.2-beta-r2 is the recommended version for all
new county builds and all in-progress migrations.

---

## Bexar migration resume path

Bexar paused at Step 5 of the v5.1.2-beta playbook when the data-contract
ambiguity surfaced. v5.1.2-beta-r2 resolves the ambiguity.

To resume Bexar migration:

1. **Re-pull canonical framework** into the Bexar repo (rerun Step 1 of
   the playbook). This brings in the renamed translator files
   (`foreclosure_notices.py`, `parcel_master.py`), the updated schema,
   the rewritten registry test, and the new playbook revision.

2. **Update `config/counties/bexar_tx.json` `sources.<id>.translator`
   values:**
   - `"arcgis_foreclosure_notices"` → `"foreclosure_notices"`
   - `"arcgis_parcel_master"` → `"parcel_master"`

3. **Remove obsolete `translator_config` fields** that referenced
   UPPERCASE ArcGIS attribute names. The new translators read
   normalized canonical fields directly. Keep:
   - `layer_doc_type_map` on the foreclosure source
   - `exemption_codes` on the parcel_master source (only if scraper
     emits raw `exemptions` string; remove if scraper emits booleans)
   - All `parcel_id_prefix` values

4. **One-time deterministic transform of `data/raw/parcel_master.jsonl`**
   from flat shape into wrapped shape per the playbook script. Document
   in `runs/bexar_tx/operator_notes.md`. The transform preserves all
   record content bit-identically; only the outer envelope changes.

5. **`data/raw/foreclosure_notices_map.jsonl` requires no transform** —
   Bexar's foreclosure scraper already produces wrapped + normalized
   shape compatible with the contract.

6. **Re-validate `bexar_tx.json` against the updated schema** via
   `write_county_config()` round-trip.

7. **Resume Step 5 of the migration playbook** with corrected canonical
   translators. The config-driven loop in `build_leads.py` will now
   read the wrapped JSONL, dispatch to the named translator, and
   produce 287 leads matching the baseline.

8. **Pause Gate 3 at end of Step 12 per the original playbook.**
   Verify `data/leads.json` output equivalence against
   `data/leads.baseline.json`.

---

## Lessons captured for v5.1.2-beta-final

1. Framework needs a `validate(path, schema_path) -> ValidateResult` API
   in `scaffold/ops/write_county_config.py` to support the playbook's
   read-only validation command. Currently only `write_county_config()`
   (write + validate combined) exists.

2. Framework should publish a `canonical_record_fields.json` registry
   enumerating every framework-canonical field name with its type,
   definition, and which translators read it. Until that registry
   exists, scrapers must inspect translator docstrings for the
   canonical names.

3. The county-agnostic regression test should also scan scraper
   adapters (`scrapers/<source>.py`) to verify they normalize to
   canonical names. Currently `scrapers/` is exempt from the regression
   scanner — it should remain exempt for county-side code in general,
   but a separate test could verify the wrapped-shape contract on a
   per-record basis at fixture-load time.

4. `bootstrap_county.py` should write a stub `scrapers/_README.md`
   explaining the scraper-to-translator contract so new operators
   understand they MUST normalize at scrape time.

---

**Tag:** `v5.1.2-beta-r2`
**Commit message:** `v5.1.2-beta-r2 — Scraper-to-translator data contract (Path 1: scrapers normalize)`
