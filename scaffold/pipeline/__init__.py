"""
Framework-canonical lead pipeline package.

This package contains UNIVERSAL pipeline logic that runs for any county.
County-specific logic lives in:

  - config/counties/<slug>.json      (county configuration)
  - scrapers/<source>.py             (county source adapters)
  - runs/<slug>/                     (county run artifacts)
  - data/                            (county output data)

The universality contract (MASTER_PROMPT §4.31, added in v5.1.2-beta) is:

  1. No county name, city, statute, vendor, or portal hostname appears
     literally in any file under scaffold/pipeline/.
  2. No state-specific date arithmetic, doc-type list, or municipality
     list is hardcoded in scaffold/pipeline/. State and county rules
     enter the pipeline ONLY through config and the translator registry.
  3. Source-specific translators register against a generic protocol
     (scaffold/pipeline/translators/__init__.py). The orchestrator
     dispatches by string name from county config; it never branches
     on source IDs.
  4. Synthetic fixture logic stays in scaffold/data/ and is consumed
     only when --synthetic is passed. Production code paths never
     read synthetic fixtures.

Modules:
    normalize          Doc-type normalization (canonical + per-source synonyms).
    stack              Signal stacking, TTL filtering, negative-signal suppression.
    score              Base score + stack-depth bonus + recency + attribute bonus.
    classify           Deal-path classifier.
    evidence           Evidence ledger per architecture/08.
    review             Review-queue rule engine.
    dashboard          Payload projection + Two-Truths invariant.
    manifest           Run manifest + per-source heartbeat.
    matcher            Parcel-master matcher with confidence-tiered hierarchy.
    owner_name_patterns
                       Generic regex-based signal emitter for parcel-master
                       owner-name strings (estate, living_trust patterns).
    sale_date_rules    State-statute rule registry for expected_sale_date
                       derivation (first_tuesday_of_month, etc.).
    build_leads        CLI orchestrator. Reads county config, dispatches
                       translators, runs pipeline, writes data/leads.json.
    translators        Translator registry. Hybrid framework + county
                       adapter pattern (v5.1.2-beta+).

v5.1.2-beta CRITICAL CHANGES from earlier in-county Phase 1-4 work
(captured during the May 2026 universality audit):
  - Translator dispatch is config-driven, not hardcoded.
  - Geography rules (accepted_municipalities, sale_date_rule,
    cross_county_policy) come from config.
  - Doc-type synonyms come from per-source config blocks, not from
    in-code maps.
  - Parcel ID prefixes come from per-source config, not from in-code
    string constants.
  - Appraisal-district / county-specific literals scrubbed from all
    universal files.
"""
