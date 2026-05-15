"""
arcgis_foreclosure_notices translator (built-in, v5.1.2-beta+).

Converts raw records from an ArcGIS REST MapServer / FeatureServer
foreclosure-notices layer into framework signals + placeholder parcels.

This translator is COUNTY-AGNOSTIC. It contains no county name, no
state statute reference, no municipality list, no field-name literal.
All county-specific schema mapping comes from `source_config`.

Expected `source_config` structure:

    {
        "translator": "arcgis_foreclosure_notices",
        "translator_config": {
            "layer_doc_type_map": {
                "0": {
                    "canonical": "NOTICE_OF_SUBSTITUTE_TRUSTEE_SALE",
                    "subtype_label": "Notice of Substitute Trustee's Sale",
                    "pattern": "foreclosure"
                },
                "1": {
                    "canonical": "TAX_FORECLOSURE_NOTICE",
                    "subtype_label": "Tax Foreclosure Notice",
                    "pattern": "tax"
                }
            },
            "address_field": "ADDRESS",
            "doc_number_field": "DOC_NUMBER",
            "year_field": "YEAR",
            "month_field": "MONTH",
            "city_field": "CITY",
            "zip_field": "ZIP"
        },
        "parcel_id_prefix": "BX-ADDR-"
    }

Expected raw record shape (from scaffold/scrapers/_arcgis_featureserver.py):

    {
        "source_id": "...",
        "raw_record_id": "...",
        "raw_payload": {
            "<address_field>": "...",
            "<doc_number_field>": "...",
            "<year_field>": 2026,
            "<month_field>": 6,
            "<city_field>": "...",
            "<zip_field>": "...",
            "_layer_id": 0
        },
        "source_url": "...",
        "source_fetched_at": "...",
        "parser_confidence": 95
    }

Returns: (signals, parcels, per_signal_meta_by_url)
"""

from __future__ import annotations
import hashlib
from datetime import date
from typing import Any

from scaffold.pipeline.translators import register
from scaffold.pipeline.sale_date_rules import derive_expected_sale_date


def _make_parcel_id(prefix: str, address: str) -> str:
    """Build a stable placeholder parcel ID from a prefix + address hash."""
    h = hashlib.sha1(address.upper().strip().encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}{h}" if prefix.endswith("-") else f"{prefix}-{h}"


def _city_check(
    city: str,
    accepted_municipalities: list,
    cross_county_policy: dict,
) -> tuple[list[str], str]:
    """
    Apply cross-county-leak detection per geography.accepted_municipalities.

    Returns:
        (preset_review_flags, action) — action is 'keep' or 'drop'.
    """
    if not city:
        return [], "keep"
    if not accepted_municipalities:
        # No accepted_municipalities means policy disabled.
        return [], "keep"
    city_upper = city.upper().strip()
    accepted_names = {m["name"].upper() for m in accepted_municipalities}
    if city_upper in accepted_names:
        return [], "keep"
    action = (cross_county_policy or {}).get("unknown_city_action", "flag_for_review")
    if action == "drop":
        return [], "drop"
    elif action == "accept_with_warning":
        return ["potential_cross_county_leak"], "keep"
    else:  # flag_for_review (default)
        return ["potential_cross_county_leak"], "keep"


@register("arcgis_foreclosure_notices")
def translate_arcgis_foreclosure_notices(
    raw_records: list[dict],
    county_config: dict,
    source_config: dict,
) -> tuple[list[dict], list[dict], dict[str, dict]]:
    """
    Translate ArcGIS foreclosure-notices raw records into pipeline signals.

    Args:
        raw_records: List of raw records from an ArcGIS layer.
        county_config: Full county config (geography, sources, etc.).
        source_config: This source's config block.

    Returns:
        (signals, parcels, per_signal_meta_by_url)
    """
    tc = source_config.get("translator_config", {}) or {}
    layer_doc_type_map: dict = tc.get("layer_doc_type_map", {})
    address_field = tc.get("address_field", "ADDRESS")
    doc_number_field = tc.get("doc_number_field", "DOC_NUMBER")
    year_field = tc.get("year_field", "YEAR")
    month_field = tc.get("month_field", "MONTH")
    city_field = tc.get("city_field", "CITY")
    zip_field = tc.get("zip_field", "ZIP")

    parcel_id_prefix = source_config.get("parcel_id_prefix", "PARCEL-")

    geography = county_config.get("geography", {}) or {}
    accepted_municipalities = geography.get("accepted_municipalities", []) or []
    cross_county_policy = geography.get("cross_county_policy", {}) or {}
    sale_date_rule = geography.get("sale_date_rule", {}) or {}

    signals: list[dict] = []
    parcels: list[dict] = []
    per_signal_meta_by_url: dict[str, dict] = {}
    seen_parcel_ids: set[str] = set()

    source_id = source_config.get("_source_id", "foreclosure_notices")

    for raw in raw_records:
        payload = raw.get("raw_payload", {})
        address = (payload.get(address_field) or "").strip()
        doc_number = (payload.get(doc_number_field) or "").strip()
        year = payload.get(year_field)
        month = payload.get(month_field)
        city = (payload.get(city_field) or "").strip()
        zip_code = (payload.get(zip_field) or "").strip()
        layer_id = str(payload.get("_layer_id", "0"))

        if not address or not doc_number:
            continue

        # Resolve doc-type from layer mapping.
        layer_mapping = layer_doc_type_map.get(layer_id, layer_doc_type_map.get("0", {}))
        canonical = layer_mapping.get("canonical", "NOTICE_OF_SUBSTITUTE_TRUSTEE_SALE")
        subtype_label = layer_mapping.get("subtype_label", canonical)
        pattern = layer_mapping.get("pattern", "foreclosure")

        # Cross-county-leak detection.
        preset_flags, action = _city_check(
            city, accepted_municipalities, cross_county_policy
        )
        if action == "drop":
            continue

        # Derive expected sale date via the configured state rule.
        try:
            year_int = int(year) if year else None
            month_int = int(month) if month else None
        except (ValueError, TypeError):
            year_int = month_int = None

        expected_sale_date = None
        if year_int and month_int:
            try:
                expected_sale_date = derive_expected_sale_date(
                    year=year_int,
                    month=month_int,
                    sale_date_rule=sale_date_rule,
                )
            except Exception:
                # On rule failure, fall back to first-of-month.
                try:
                    expected_sale_date = date(year_int, month_int, 1).isoformat()
                except Exception:
                    expected_sale_date = None

        # Build the parcel placeholder.
        parcel_id = _make_parcel_id(parcel_id_prefix, address)
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

        # Build the signal.
        signal_id = "sig_" + hashlib.sha1(
            f"{source_id}|{doc_number}|{layer_id}".encode("utf-8")
        ).hexdigest()[:16]
        source_url = (
            raw.get("source_url")
            or f"about:blank/{source_id}/{doc_number}/{layer_id}"
        )
        signal = {
            "signal_id": signal_id,
            "raw_record_id": raw.get("raw_record_id"),
            "source_id": source_id,
            "source_url": source_url,
            "doc_type": canonical,
            "doc_type_subtype_label": subtype_label,
            "doc_number": doc_number,
            "primary_parcel_id": parcel_id,
            "filing_date": expected_sale_date or (
                date(year_int, month_int, 1).isoformat()
                if (year_int and month_int) else None
            ),
            "parser_confidence": raw.get("parser_confidence", 95),
        }
        signals.append(signal)

        per_signal_meta_by_url[source_url] = {
            "preset_review_flags": preset_flags,
            "expected_sale_date": expected_sale_date,
            "match_confidence": 0,  # placeholder until parcel-master matcher runs
            "match_method": "placeholder",
            "address": address,
            "city": city,
            "zip": zip_code,
            "primary_parcel_id": parcel_id,
        }

    return signals, parcels, per_signal_meta_by_url
