# VERSION NOTES — v5.1.2-beta-r3

**Release date:** 2026-05-15
**Type:** Beta revision (additive — universal field_map activation)
**Predecessor:** v5.1.2-beta-r2 (2026-05-15)
**Successor:** v5.1.2-beta-final (planned)

---

## TL;DR

v5.1.2-beta added `sources.<id>.field_map` to the schema. v5.1.2-beta-r2
shipped Path 1 (scrapers normalize, translators consume normalized).
But r2 translators didn't actually READ `field_map`.

**v5.1.2-beta-r3 activates `field_map` as a universal translator
feature.** Both canonical translators (`foreclosure_notices` and
`parcel_master`) now resolve every canonical field they read through
`field_map` before touching `raw_payload`. The same pattern will apply
to every future translator: `publicsearch_clerk_recordings`,
`tyler_odyssey_court`, `tax_collector`, etc.

This is a generic universality fix, not a county-specific
accommodation. Field naming varies across counties, portals, and
data domains (clerk recordings, court filings, tax data, parcel
enrichment, etc.). `field_map` is the bridge.

---

## What changed from v5.1.2-beta-r2

### 1. Universal `field_map` resolution in canonical translators

Both `foreclosure_notices.py` and `parcel_master.py` now read
`source_config.field_map` at the start of translation and route every
canonical-field read through a `_resolve(canonical_name)` helper:

```python
field_map = source_config.get("field_map", {}) or {}

def _resolve(canonical_name: str) -> str:
    return field_map.get(canonical_name, canonical_name)

# Usage in the translator body:
address = payload.get(_resolve("address"))
```

Keys are the canonical names the translator expects; values are the
actual field names the scraper wrote to `raw_payload`. Canonical
fields absent from `field_map` resolve to identity (translator reads
the canonical name directly). `field_map` itself is optional.

This is the canonical pattern. Future translators (clerk_recordings,
court, tax) implement the same `_resolve` helper at the top and use
it for every payload read. Adding `field_map` support to a new
translator is mechanical.

### 2. MASTER_PROMPT.md §4.32 — `field_map` documented

New subsection in §4.32 covers `field_map` syntax, partial mapping
semantics, and the one limitation: exemption boolean keys
(`exempt_homestead`, etc.) are NOT field-mapped — they're framework-
canonical semantic flags, not source nomenclature.

### 3. test_translator_registry.py extended

39 → 56 tests. New universal field_map test cases:
- Full field_map mapping bridges all canonical fields (both translators).
- Partial field_map (only some keys mapped, rest identity) works correctly.
- No field_map = pure identity (backward-compat for scrapers that
  emit canonical names directly).

### 4. Version stamps

- `FRAMEWORK_VERSION.json` → `v5.1.2-beta-r3`.
- `scaffold/bootstrap_county.py` → `FRAMEWORK_VERSION = "v5.1.2-beta-r3"`.

### 5. Bexar migration playbook updated

Step 4 example now shows `field_map` on the parcel_master source. The
Bexar-specific mappings (`address` → `situs_address`, etc.) are listed
as an example of how counties wire `field_map` for their own scraper
conventions, not as the universal default.

### 6. Gate suite

All 4 gates PASS:

```
[PASS] Golden path                                              — 46/46
[PASS] County-agnostic regression                               — zero violations
[PASS] Atomic config writer                                     — 18/18
[PASS] Translator registry                                      — 56/56
RESULT: PASS
```

---

## What did NOT change

- §4.31 Universality Contract — unchanged.
- §4.32 wrapped raw_payload contract structure — unchanged.
- Translator names — unchanged from r2.
- Schema (`field_map` was already in r2's `_schema.json`).
- All v5.1.1-beta and v5.1.0-beta features preserved.

---

## Breaking changes

**None.** r2 county configs work unchanged on r3. `field_map` is
optional; absent = identity mapping (which is exactly what r2 did).

---

## Forward path (clerk_recordings priority)

`field_map` is now the universal translator field-name bridge. The
post-Bexar-migration work targets `publicsearch_clerk_recordings`,
where field-name divergence across PublicSearch deployments
(different jurisdictions, different field labels) is the norm rather
than the exception. `field_map` per source handles it cleanly without
re-scraping or county-side shims.

Parcel/CAD/assessor enrichment is solved enough for Bexar to resume
migration and reproduce the 287-lead baseline. No further parcel-
specific framework work in r3. Future framework iterations focus on
clerk_recordings and other primary lead sources.

---

## Bexar migration resume

After overlaying r3 canonical into Bexar, add to
`config/counties/bexar_tx.json` parcel_master source:

```json
"field_map": {
  "address": "situs_address",
  "city": "situs_city",
  "zip": "situs_zip",
  "owner_mailing_address": "owner_mailing_addr1",
  "property_use": "property_class"
}
```

Foreclosure source needs no `field_map` (Bexar's foreclosure scraper
already emits canonical names). Run the wrap script from r2 to
transform `data/raw/parcel_master.jsonl` from flat to wrapped shape.
Resume migration from Step 5.

---

**Tag:** `v5.1.2-beta-r3`
**Commit message:** `v5.1.2-beta-r3 — Activate universal field_map bridge in canonical translators`
