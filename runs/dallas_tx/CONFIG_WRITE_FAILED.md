# CONFIG_WRITE_FAILED — dallas_tx (RESOLVED)

**Status: RESOLVED 2026-08-22.** Operator authorized the mechanical fix
described below (populate `attempt_order` / `timestamp` / `status` on the
3 affected `auto_resolve_attempts` entries). Attempt 4 (post-authorization
attempt 2) succeeded: `config/counties/dallas_tx.json` is now written and
schema-valid, `build_verdict: READY_TO_BUILD`. The three orphaned temp
files under `config/counties/` have been removed. This file is kept as
the historical record of the two failure classes encountered during
Phase 0 Step 4; no further action needed.

---

Per MASTER_PROMPT.md Section 4.28.4, `write_county_config` failed schema
validation twice. Locked rule: attempt exactly one structured repair; if the
second attempt also fails, stop, document both attempts, and surface the
failure to the operator. This file documents that stop.

**Update:** the operator authorized the `operator_override` fix described
below on 2026-08-22. That specific fix worked — `jp_eviction` and
`code_enforcement` are no longer blocking. But the operator-authorized
re-attempt (attempt 3 overall, attempt 1 post-authorization) surfaced a
**new, unrelated** schema error. Per the same locked rule, this run stops
again rather than silently attempting a fourth write.

## Attempt 1 (pre-authorization)

`status: SCHEMA_INVALID`

```
Schema validation failed at ['sources', 'tax_foreclosure_resales', 'subtype']:
'tax_foreclosure_resales' is not one of ['clerk_recordings', 'court_civil',
'court_probate', 'court_family', 'court_eviction', 'sheriff_sales',
'tax_delinquency', 'tax_certificates', 'code_enforcement', 'parcel_master',
'gis_parcels', 'usps_vacancy', 'utility_shutoff']
```

Root cause: `subtype` fields used descriptive county-specific names not in
the schema's fixed enum, and `access_pattern` / `refresh_cadence` values
were similarly out of enum on the same blocks.

## Attempt 2 — the one allowed structured repair (pre-authorization)

Re-built the config dict with corrected enum values:
- `foreclosure_notices` subtype → `sheriff_sales` (matches `bexar_tx.json` precedent)
- `tax_foreclosure_resales` subtype → `tax_certificates`; `access_pattern` → `static_html`
- `jp_eviction` subtype → `court_eviction`; `access_pattern` → `public_records_only`; `refresh_cadence` → `on_demand`
- `code_enforcement` `access_pattern` → `public_records_only`; `refresh_cadence` → `on_demand`

Result: `status: SCHEMA_INVALID`

```
Schema validation failed at ['sources', 'jp_eviction', 'operator_override']:
True was expected
```

Root cause: schema requires `operator_override: true` on any source block
whose `official_status` is `NOT_FOUND` or `UNVERIFIED`. Both `jp_eviction`
and `code_enforcement` were left at the default `operator_override: false`.

Stopped here per Section 4.28.4 (no third attempt without operator sign-off).

## Operator authorization (2026-08-22)

Operator explicitly approved: "Yes, set operator_override:true and write the
config" — confirming both sources are genuinely `NOT_FOUND` (JP eviction has
no online case-search portal anywhere in the county; code enforcement is a
municipal, not county-level, function). Applied:

- `jp_eviction.operator_override` → `True`
- `code_enforcement.operator_override` → `True`
- Logged both overrides in `operator_override_audit` with reason,
  `approved_by: "operator"`, and `approved_at` timestamp.

## Attempt 3 (post-authorization attempt 1) — 2026-08-22

`status: SCHEMA_INVALID`

```
Schema validation failed at ['sources', 'sheriff_sales', 'auto_resolve_attempts', 1]:
'attempt_order' is a required property
```

Root cause: this is a **different bug**, unrelated to the operator-override
fix. The schema requires every entry in a source's `auto_resolve_attempts`
array to carry `attempt_order` (integer, min 1), `timestamp`, `strategy`,
and `status`. The `sheriff_sales` block's two attempt entries (and the
single `jp_eviction` attempt entry) were built with only `strategy`,
`result`, and `detail` — `attempt_order`, `timestamp`, and `status` were
never populated. This was already present in Attempt 2's dict and simply
wasn't reached by the validator until the `operator_override` blocker was
cleared (jsonschema's `validate()` surfaces one best-match error at a time,
not a full error list).

## Why this stops here instead of a fourth attempt

Per Section 4.28.4's locked rule and the operator's explicit instruction for
this run ("if it fails again, write an updated CONFIG_WRITE_FAILED.md and
stop — do not attempt a further fix without asking again"), this run stops
here. **`config/counties/dallas_tx.json` still does not exist on disk.**

Three temp files remain in `config/counties/` for inspection:
- `dallas_tx.pm2394z3.tmp.json` (attempt 1)
- `dallas_tx.yko_mh92.tmp.json` (attempt 2)
- `dallas_tx.rid1eglo.tmp.json` (attempt 3 — closest; only the
  `auto_resolve_attempts` entries need `attempt_order` / `timestamp` /
  `status` filled in)

## What the fix actually is (for the operator's awareness, not yet applied)

Add to each object in the affected `auto_resolve_attempts` arrays
(`sheriff_sales`: 2 entries; `jp_eviction`: 1 entry) in
`runs/dallas_tx/_build_config.py`:

- `"attempt_order"`: sequential integer starting at 1 within that source's array
- `"timestamp"`: the recon timestamp (`NOW`, already defined in the script)
- `"status"`: one of the schema's enum values — `"SUCCESS"` for the
  `find_official_vendor_link` attempt, `"SKIPPED_NOT_ALLOWED"` or
  `"REQUIRES_OPERATOR_APPROVAL"` for the `use_playwright`
  (`NOT_ATTEMPTED_NO_TOOLING`) attempt and the `jp_eviction`
  `discover_public_search_endpoint` attempt (both were not actually
  executed as SUCCESS/FAILED — they were deferred, so `"PENDING"` or
  `"SKIPPED_NOT_ALLOWED"` is the more accurate enum value; operator
  judgment call on which fits better)

This is mechanical — no new sources, no re-verification needed, just filling
in three already-known fields on three existing array entries.

## Recommended next action

Operator says "fill in attempt_order/timestamp/status and write the config"
(or equivalent) to authorize the next write attempt. Given this is now the
second distinct bug class surfaced (not a second attempt at the *same* bug),
treat the next attempt as a fresh two-attempt budget under 4.28.4 rather than
a continuation of the exhausted one above — but that judgment call belongs to
the operator, not to this run.
