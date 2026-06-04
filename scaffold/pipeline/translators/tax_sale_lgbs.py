"""
tax_sale_lgbs translator (built-in, v5.5.0+).

Converts wrapped tax-foreclosure SALE records (produced by a county-side
tax-sale-portal scraper) into framework signals + placeholder parcels.

These are EVENT-BASED distress records: a property under a delinquent-tax
foreclosure judgment, scheduled for (or returned from) a public tax sale.
They are leads in their own right per the §4 product rule — not enrichment.

County- AND vendor-agnostic per MASTER_PROMPT §4.31. The registered name is a
fixed identifier in the config-schema translator vocabulary; the module itself
is generic — any tax-sale source whose county-side scraper emits the wrapped
raw shape below can reuse it by supplying `source_config`/`translator_config`.
The module carries no county, portal, vendor, or status literal beyond neutral
framework defaults; every county-/vendor-specific value (field_map,
parcel_id_prefix, suppressed statuses, the canonical doc-type) arrives through
config at call time.

Input — wrapped raw record (§4.32). `raw_payload` field names are the
scraper's verbatim source names; `field_map` (in source_config) bridges them
to the canonical names this translator reads:

    uid, doc_number, address, city, zip, filing_date (YYYY-MM-DD), status,
    sale_type, account_nbr, value, minimum_bid, geometry

Expected `source_config`:

    {
      "translator": "tax_sale_lgbs",
      "translator_config": {
        "canonical": "TAX_FORECLOSURE_NOTICE",
        "subtype_label": "Tax Foreclosure Notice",
        "lifecycle_suppression_statuses": ["Sold", "Cancelled"]
      },
      "field_map": {"doc_number": "cause_nbr", "address": "prop_address_one",
                    "city": "prop_city", "zip": "prop_zipcode",
                    "filing_date": "sale_date_only"},
      "parcel_id_prefix": "<PREFIX>"
    }

IMPORTANT integration note (same as publicsearch_clerk_recordings). The v5.4.0
orchestrator re-derives the registry canonical_doc_type from the signal's
*subtype label* via normalize.normalize_doc_type -> doc_type_bridge, and DROPS
any signal whose label does not normalize to a registry type. So
`subtype_label` MUST be a framework-recognized label. The default
"Tax Foreclosure Notice" (canonical TAX_FORECLOSURE_NOTICE, lead_pattern
`tax`) is the same label the `foreclosure_notices` translator emits for its
tax layer — proven to normalize. `sale_type` is carried as DISPLAY metadata
(per_signal_meta), never as the normalized doc-type, because RESALE /
STRUCK OFF / etc. are not registry types.

Returns: (signals, parcels, per_signal_meta_by_url)
"""

from __future__ import annotations

import hashlib
import re
import sys

from scaffold.pipeline.translators import register

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_DEFAULT_CANONICAL = "TAX_FORECLOSURE_NOTICE"
_DEFAULT_SUBTYPE = "Tax Foreclosure Notice"


def _make_parcel_id(prefix: str, basis: str) -> str:
    h = hashlib.sha1(basis.upper().strip().encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}{h}" if prefix.endswith("-") else f"{prefix}-{h}"


@register("tax_sale_lgbs")
def translate_tax_sale_lgbs(
    raw_records: list[dict],
    county_config: dict,
    source_config: dict,
) -> tuple[list[dict], list[dict], dict[str, dict]]:
    tc = source_config.get("translator_config", {}) or {}
    canonical = tc.get("canonical") or _DEFAULT_CANONICAL
    subtype_label = tc.get("subtype_label") or _DEFAULT_SUBTYPE
    suppressed_statuses = {
        str(s).strip().lower()
        for s in (tc.get("lifecycle_suppression_statuses") or [])
    }

    field_map = source_config.get("field_map", {}) or {}

    def _resolve(name: str) -> str:
        return field_map.get(name, name)

    def _get(payload: dict, canonical_name: str):
        return payload.get(_resolve(canonical_name))

    parcel_id_prefix = source_config.get("parcel_id_prefix", "PARCEL-")
    source_id = source_config.get("_source_id", "tax_sale_lgbs")

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

        uid = str(_get(payload, "uid") or "").strip()
        if not uid:
            _skip("missing_uid")
            continue

        # Lifecycle suppression — drop terminal/non-actionable sale states.
        status = str(_get(payload, "status") or "").strip()
        if status.lower() in suppressed_statuses:
            _skip("lifecycle_suppressed_status")
            continue

        # Sale date. "Available for Future Sale" records are not yet scheduled
        # and carry no date — these are still valid distress leads, so emit them
        # with filing_date=None (the pipeline tolerates a null event date, same
        # as foreclosure_notices). Only a PRESENT-but-malformed date is a skip.
        raw_date = _get(payload, "filing_date")
        if raw_date in (None, ""):
            filing_date = None
        elif isinstance(raw_date, str) and _DATE_RE.match(raw_date):
            filing_date = raw_date
        else:
            _skip("unparseable_sale_date")
            continue

        doc_number = str(_get(payload, "doc_number") or "").strip() or None
        address = _get(payload, "address")
        address = address.strip() if isinstance(address, str) and address.strip() else None
        city = _get(payload, "city")
        city = city.strip() if isinstance(city, str) and city.strip() else None
        zip_code = _get(payload, "zip")
        zip_code = zip_code.strip() if isinstance(zip_code, str) and zip_code.strip() else None

        sale_type = _get(payload, "sale_type")
        account_nbr = _get(payload, "account_nbr")
        value = _get(payload, "value")
        minimum_bid = _get(payload, "minimum_bid")
        geometry = _get(payload, "geometry")

        # Parcel placeholder. Tax-sale records carry an address; fall back to
        # the always-present uid so a stable parcel_id exists either way.
        basis = address if address else f"uid:{uid}"
        parcel_id = _make_parcel_id(parcel_id_prefix, basis)
        if parcel_id not in seen_parcel_ids:
            parcels.append({
                "parcel_id": parcel_id,
                "address": address,
                "city": city,
                "zip": zip_code,
                "owner_name": None,
                "parcel_master_status": "placeholder_pending_enrichment",
            })
            seen_parcel_ids.add(parcel_id)

        signal_id = "sig_" + hashlib.sha1(
            f"{source_id}|{uid}".encode("utf-8")
        ).hexdigest()[:16]
        source_url = raw.get("source_url") or f"about:blank/{source_id}/{uid}"

        signals.append({
            "signal_id": signal_id,
            "raw_record_id": raw.get("raw_record_id"),
            "source_id": source_id,
            "source_url": source_url,
            "doc_type": canonical,
            # Authoritative framework-recognized label (NOT sale_type).
            "doc_type_subtype_label": subtype_label,
            "doc_number": doc_number,
            "primary_parcel_id": parcel_id,
            "filing_date": filing_date,
            "parser_confidence": confidence,
        })

        per_signal_meta_by_url[source_url] = {
            "preset_review_flags": [],
            "match_confidence": 0,
            "match_method": "placeholder",
            "address": address,
            "city": city,
            "zip": zip_code,
            "primary_parcel_id": parcel_id,
            # Tax-sale display / enrichment context.
            "sale_type": sale_type,
            "sale_status": status or None,
            "account_nbr": account_nbr,
            "adjudged_value": value,
            "minimum_bid": minimum_bid,
            "geometry": geometry,
        }

    if skipped:
        print(
            f"[tax_sale_lgbs translator] {len(signals)} signals; "
            f"skipped {dict(sorted(skipped.items()))}",
            file=sys.stderr,
        )

    return signals, parcels, per_signal_meta_by_url
