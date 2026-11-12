# CONFIG_WRITE_FAILED — clayton_ga

> **RESOLVED 2026-11-12.** On explicit operator instruction ("re-run write") the
> attempt fields were renamed as described below and the writer returned
> `status: OK`, `schema_validation: VALIDATED`. `config/counties/clayton_ga.json`
> now exists. This file is kept as the audit trail of the two failed attempts.

Date: 2026-11-12
Rule: MASTER_PROMPT.md §4.28.4 (one structured repair, then stop)

The populated config was built as a Python dict and written with
`scaffold/ops/write_county_config.py`. jsonschema 4.26 is installed, so full
schema validation ran. Both attempts failed validation, so
`config/counties/clayton_ga.json` was **not** written.

## Attempt 1 — SCHEMA_INVALID

    Additional properties are not allowed ('client_access' was unexpected)

Cause: a top-level `client_access` block was added because MASTER_PROMPT §6
Step 4 lists "`client_access` config exists" as a gate minimum. `_schema.json`
does not allow that key, and no other county config has it.
Repair: removed the key and moved the note to the recon summary.

## Attempt 2 — SCHEMA_INVALID

    sources.gsccca_clerk_indexes.auto_resolve_attempts[1]: 'attempt_order' is a required property

Cause: the auto-resolve attempt records used `attempted_at`/`result` keys. The
schema requires `attempt_order` (int), `timestamp`, `strategy`, and `status`
(enum: PENDING, SUCCESS, FAILED, BLOCKED_BY_PERMISSION, BLOCKED_BY_COST,
REQUIRES_CREDENTIALS, REQUIRES_OPERATOR_APPROVAL, SKIPPED_NOT_ALLOWED).

## Fix for the next run (not applied — the repair budget is used up)

Rename the attempt fields on `gsccca_clerk_indexes` and
`ga_public_notice_foreclosures`:

    attempted_at -> timestamp
    result       -> status   (FAILED / SKIPPED_NOT_ALLOWED / BLOCKED_BY_COST)
    note         -> result
    + attempt_order: 1, 2

No other schema errors are known, but jsonschema stops at the first error per
path, so there may be more.

## State left on disk

- `config/counties/clayton_ga.yoa6y3n1.tmp.json` — attempt-2 temp file, kept for inspection per §4.28.5
- `runs/clayton_ga/recon/fingerprints/` — empty (artifact generation runs after the config write)
- Recon findings are summarized in the Phase 0 change manifest in the session transcript

## Framework issue to flag

MASTER_PROMPT §6 Step 4 requires a `client_access` config, but `_schema.json`
rejects a top-level `client_access` key. One of the two needs to change.
