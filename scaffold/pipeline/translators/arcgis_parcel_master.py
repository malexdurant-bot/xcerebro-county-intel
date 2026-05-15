"""
arcgis_parcel_master translator (built-in, v5.1.2-beta+).

Converts raw records from an ArcGIS REST parcel-master layer into
framework parcel records ready for the matcher.

Parcel-master sources are ENRICHMENT, not lead-generating. This
translator returns ([], parcels, {}) — no signals, only parcels.
The pipeline's matcher (scaffold/pipeline/matcher.py) then joins
these parcels onto signals from other sources by address.

This translator is COUNTY-AGNOSTIC. All field-name mapping comes
from `source_config["field_map"]`.

Expected `source_config` structure:

    {
        "translator": "arcgis_parcel_master",
        "field_map": {
            "parcel_id":      "<source's parcel ID field>",
            "situs_address":  "<source's situs address field>",
            "owner_name":     "<source's owner name field>",
            "owner_address":  "<source's mailing address field>",
            "owner_city":     "<source's mailing city field>",
            "owner_state":    "<source's mailing state field>",
            "owner_zip":      "<source's mailing zip field>",
            "city":           "<source's situs city field>",
            "zip":            "<source's situs zip field>",
            "assessed_value": "<source's total appraised value field>",
            "land_value":     "<source's land value field>",
            "improvement_value": "<source's improvement value field>",
            "year_built":     "<source's year built field>",
            "exemptions":     "<source's exemptions field>",
            "property_use":   "<source's property class field>",
            "acres":          "<source's acres field>",
            "legal_description": "<source's legal description field>"
        },
        "exemption_codes": {
            "homestead":  ["HS"],
            "over_65":    ["OV65", "O65"],
            "disabled":   ["DV", "DIS"],
            "veteran":    ["VET", "DAV"]
        }
    }

Returns: ([], parcels, {})
"""

from __future__ import annotations
from typing import Any

from scaffold.pipeline.translators import register


def _parse_exemptions(
    raw_exemptions: str | None,
    exemption_codes: dict[str, list[str]],
) -> dict[str, bool]:
    """Parse a source's exemption string into framework-canonical bool flags."""
    flags = {
        "exempt_homestead": False,
        "exempt_over_65": False,
        "exempt_disabled": False,
        "exempt_veteran": False,
    }
    if not raw_exemptions:
        return flags
    parts = {p.strip().upper() for p in str(raw_exemptions).replace(";", ",").split(",")}
    for canonical_key, source_codes in (exemption_codes or {}).items():
        for code in source_codes:
            if code.upper() in parts:
                flags[f"exempt_{canonical_key}"] = True
                break
    return flags


def _try_int(value: Any) -> int | None:
    """Best-effort int conversion."""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


@register("arcgis_parcel_master")
def translate_arcgis_parcel_master(
    raw_records: list[dict],
    county_config: dict,
    source_config: dict,
) -> tuple[list[dict], list[dict], dict[str, dict]]:
    """
    Translate ArcGIS parcel-master raw records into framework parcel records.

    Returns:
        (signals=[], parcels, per_signal_meta_by_url={})
    """
    field_map = source_config.get("field_map", {}) or {}
    exemption_codes = (source_config.get("translator_config") or {}).get(
        "exemption_codes", {}
    )

    parcels: list[dict] = []

    for raw in raw_records:
        payload = raw.get("raw_payload", {}) or {}

        parcel_id_raw = payload.get(field_map.get("parcel_id", "PropID"))
        if parcel_id_raw is None or str(parcel_id_raw).strip() == "":
            continue

        situs = (payload.get(field_map.get("situs_address", "Situs")) or "").strip()
        owner = (payload.get(field_map.get("owner_name", "Owner")) or "").strip()

        raw_exemptions = payload.get(field_map.get("exemptions", "Exempts"))
        exemption_flags = _parse_exemptions(raw_exemptions, exemption_codes)

        parcel = {
            "parcel_id": str(parcel_id_raw).strip(),
            "address": situs,
            "owner_name": owner,
            "owner_mailing_address": (
                payload.get(field_map.get("owner_address", "AddrLn1")) or ""
            ).strip(),
            "owner_mailing_city": (
                payload.get(field_map.get("owner_city", "AddrCity")) or ""
            ).strip(),
            "owner_mailing_state": (
                payload.get(field_map.get("owner_state", "AddrSt")) or ""
            ).strip(),
            "owner_mailing_zip": (
                payload.get(field_map.get("owner_zip", "Zip")) or ""
            ).strip(),
            "city": (payload.get(field_map.get("city", "AddrCity")) or "").strip(),
            "zip": (payload.get(field_map.get("zip", "Zip")) or "").strip(),
            "assessed_value": _try_int(
                payload.get(field_map.get("assessed_value", "TotVal"))
            ),
            "land_value": _try_int(
                payload.get(field_map.get("land_value", "LandVal"))
            ),
            "improvement_value": _try_int(
                payload.get(field_map.get("improvement_value", "ImprVal"))
            ),
            "year_built": _try_int(
                payload.get(field_map.get("year_built", "YrBlt"))
            ),
            "property_use": (
                payload.get(field_map.get("property_use", "PropUse")) or ""
            ),
            "acres": payload.get(field_map.get("acres", "Acres")),
            "legal_description": (
                payload.get(field_map.get("legal_description", "LglDesc")) or ""
            ).strip(),
            "parcel_master_status": "matched_pending_join",
            **exemption_flags,
        }
        parcels.append(parcel)

    return [], parcels, {}
