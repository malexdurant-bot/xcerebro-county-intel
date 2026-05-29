"""
publicsearch_clerk_recordings translator (built-in, v5.4.0+).

Converts wrapped clerk recorded-instrument raw records (deeds, liens, lis
pendens, judgments, probate recordings, etc.), produced by a county-side
recorder/clerk-portal scraper, into framework signals + placeholder
parcels. Implements LEVEL 2 of the clerk-recordings translator contract:
doc-type code -> canonical mapping + field_map application + the two
LEVEL 2 flags (lifecycle suppression, foreclosure dedup), with NO LEVEL 3
inference (no heirship / judgment / umbrella sub-type detection).

County- AND vendor-agnostic per MASTER_PROMPT §4.31 — every county- or
portal-specific value (the doc-type code map, field_map, parcel_id_prefix,
flag code lists) arrives through `source_config`/`translator_config` at
call time. The module carries no county, portal, vendor, or doc-type-code
literal. The registered name is a fixed identifier in the config-schema
translator vocabulary; the module itself is generic and any clerk/recorder
portal whose county-side scraper emits the wrapped raw shape below can
reuse it by supplying config.

Input — wrapped raw record (§4.32). The `raw_payload` field names are the
scraper's normalized names; `field_map` (in source_config) bridges them to
the canonical names this translator reads. The names the translator reads:

    internal_doc_id, document_number, doc_type_code, recorded_date
    (YYYY-MM-DD), grantor, grantee, property_address, legal_description,
    parcel_grid_identifiers

Expected `source_config`:

    {
      "translator": "publicsearch_clerk_recordings",
      "translator_config": {
        "doc_type_code_map": {
          "<CODE>": {"canonical": "<CANON>", "subtype_label": "<LABEL>"}, ...
        },
        "lifecycle_suppression_codes": [<codes>],          # optional
        "foreclosure_dedup_codes": [<codes>],              # optional
        "unknown_doc_type_handling": "skip_with_warning"   # or
                                     "include_with_canonical_unknown"
      },
      "field_map": {"doc_number": "<raw name>", "address": "<raw name>"},
      "parcel_id_prefix": "<PREFIX>"
    }

IMPORTANT integration note. The v5.4.0 orchestrator
(`build_leads._signal_to_raw_event`) re-derives the registry
canonical_doc_type from the signal's *subtype label* via
`normalize.normalize_doc_type` -> `doc_type_bridge.monolith_to_registry`,
and DROPS any signal whose label does not normalize to a registry type.
So `subtype_label` here MUST be a label the framework normalizer
recognizes; it is taken authoritatively from `doc_type_code_map`, keyed by
the queried doc-type code — NOT from any free-text label cell, which can
vary row to row. Codes whose canonical is not yet in the framework
vocabulary are intentionally left OUT of `doc_type_code_map`; their
records are skipped here with a counted reason rather than emitted and
silently dropped downstream.

Returns: (signals, parcels, per_signal_meta_by_url)
"""

from __future__ import annotations

import hashlib
import re
import sys

from scaffold.pipeline.translators import register

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _make_parcel_id(prefix: str, basis: str) -> str:
    h = hashlib.sha1(basis.upper().strip().encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}{h}" if prefix.endswith("-") else f"{prefix}-{h}"


@register("publicsearch_clerk_recordings")
def translate_clerk_recordings(
    raw_records: list[dict],
    county_config: dict,
    source_config: dict,
) -> tuple[list[dict], list[dict], dict[str, dict]]:
    tc = source_config.get("translator_config", {}) or {}
    code_map: dict = tc.get("doc_type_code_map", {}) or {}
    lifecycle_codes = set(tc.get("lifecycle_suppression_codes", []) or [])
    fc_dedup_codes = set(tc.get("foreclosure_dedup_codes", []) or [])
    unknown_handling = tc.get("unknown_doc_type_handling", "skip_with_warning")

    field_map = source_config.get("field_map", {}) or {}

    def _resolve(name: str) -> str:
        return field_map.get(name, name)

    parcel_id_prefix = source_config.get("parcel_id_prefix", "PARCEL-")
    source_id = source_config.get("_source_id", "clerk_recordings")

    signals: list[dict] = []
    parcels: list[dict] = []
    per_signal_meta_by_url: dict[str, dict] = {}
    seen_parcel_ids: set[str] = set()
    skipped: dict[str, int] = {}

    def _skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for raw in raw_records:
        payload = raw.get("raw_payload")
        if not isinstance(payload, dict):
            _skip("malformed_raw_payload")
            continue

        confidence = raw.get("parser_confidence")
        if not isinstance(confidence, int) or not (0 <= confidence <= 100):
            _skip("invalid_parser_confidence")
            continue

        internal_doc_id = str(payload.get("internal_doc_id") or "").strip()
        if not internal_doc_id:
            _skip("missing_internal_doc_id")
            continue

        recorded_date = payload.get(_resolve("recorded_date"))
        if not (isinstance(recorded_date, str) and _DATE_RE.match(recorded_date)):
            _skip("unparseable_recorded_date")
            continue

        code = str(payload.get(_resolve("doc_type_code")) or "").strip()
        mapping = code_map.get(code)
        if not mapping:
            _skip("unsupported_doc_type_pending_vocab")
            if unknown_handling != "include_with_canonical_unknown":
                continue
            canonical, subtype_label = "UNKNOWN_DOC_TYPE", (code or "UNKNOWN")
        else:
            canonical = mapping["canonical"]
            subtype_label = mapping.get("subtype_label", canonical)

        doc_number = str(payload.get(_resolve("doc_number")) or "").strip() or None
        address = payload.get(_resolve("address"))
        address = address.strip() if isinstance(address, str) and address.strip() else None
        grantor = payload.get(_resolve("grantor")) or None
        grantee = payload.get(_resolve("grantee")) or None
        legal_description = payload.get(_resolve("legal_description")) or None
        parcel_grid = payload.get(_resolve("parcel_grid_identifiers")) or None

        # Parcel placeholder. Many recordings have no address -> fall back to
        # the (always-present) internal_doc_id so a stable parcel_id exists.
        basis = address if address else f"docid:{internal_doc_id}"
        parcel_id = _make_parcel_id(parcel_id_prefix, basis)
        if parcel_id not in seen_parcel_ids:
            parcels.append({
                "parcel_id": parcel_id,
                "address": address,
                "city": None,
                "zip": None,
                "owner_name": grantor,
                "parcel_master_status": "placeholder_pending_enrichment",
            })
            seen_parcel_ids.add(parcel_id)

        signal_id = "sig_" + hashlib.sha1(
            f"{source_id}|{doc_number}|{internal_doc_id}".encode("utf-8")
        ).hexdigest()[:16]
        source_url = raw.get("source_url") or f"about:blank/{source_id}/{internal_doc_id}"

        signals.append({
            "signal_id": signal_id,
            "raw_record_id": raw.get("raw_record_id"),
            "source_id": source_id,
            "source_url": source_url,
            "doc_type": canonical,
            # Authoritative, code-driven label the framework normalizer
            # recognizes (NOT a row's free-text label cell).
            "doc_type_subtype_label": subtype_label,
            "doc_number": doc_number,
            "primary_parcel_id": parcel_id,
            "filing_date": recorded_date,
            "parser_confidence": confidence,
            # Carried for the orchestrator's party extraction (§17 debtor engine).
            "grantor": grantor,
            "grantee": grantee,
        })

        review_flags: list[str] = []
        if code in lifecycle_codes:
            review_flags.append("lifecycle_suppression")
        if code in fc_dedup_codes:
            review_flags.append("fc_dedup_required")

        per_signal_meta_by_url[source_url] = {
            "preset_review_flags": review_flags,
            "match_confidence": 0,
            "match_method": "placeholder",
            "address": address,
            "city": None,
            "zip": None,
            "primary_parcel_id": parcel_id,
            "grantor": grantor,
            "grantee": grantee,
            "legal_description": legal_description,
            "parcel_grid_identifiers": parcel_grid,
        }

    if skipped:
        print(
            f"[clerk_recordings translator] {len(signals)} signals; "
            f"skipped {dict(sorted(skipped.items()))}",
            file=sys.stderr,
        )

    return signals, parcels, per_signal_meta_by_url
