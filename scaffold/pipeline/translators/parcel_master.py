"""
parcel_master translator (built-in, v5.1.2-beta-r2+).

Converts NORMALIZED parcel-master records (produced by a county-side
scraper) into framework parcel records ready for the matcher.

Parcel-master sources are ENRICHMENT, not lead-generating. This translator
returns ([], parcels, {}) — no signals, only parcels.

This translator consumes the framework-canonical RAW RECORD shape declared
in MASTER_PROMPT §4.32 (v5.1.2-beta-r2+):

    {
        "raw_record_id": "<unique id, often derived from parcel_id>",
        "source_id": "<source id, e.g. 'parcel_master'>",
        "source_fetched_at": "<ISO timestamp>",
        "raw_payload": {
            "parcel_id": "<county-canonical parcel id, with prefix>",
            "address": "<situs address, uppercase canonical>",
            "owner_name": "<owner string as recorded>",
            "owner_mailing_address": "<mailing addr line 1>",
            "owner_mailing_city": "<mailing city>",
            "owner_mailing_state": "<2-letter state>",
            "owner_mailing_zip": "<5-digit ZIP>",
            "city": "<situs city>",
            "zip": "<situs zip>",
            "assessed_value": <int>,
            "land_value": <int>,
            "improvement_value": <int>,
            "year_built": <int>,
            "exempt_homestead": <bool>,
            "exempt_over_65": <bool>,
            "exempt_disabled": <bool>,
            "exempt_veteran": <bool>,
            "property_use": "<class code>",
            "acres": <float>,
            "legal_description": "<text>"
        }
    }

NOTE on exemption fields: this translator consumes pre-parsed boolean
exempt_* flags directly from raw_payload. The scraper is responsible for
parsing the source's exemption string (e.g. "HS, OV65, DV") into the
canonical boolean fields BEFORE writing the JSONL. This keeps source-
specific exemption code lists out of the universal translator.

If a county's scraper instead emits a raw `exemptions` string and an
`exemption_codes` map in translator_config, the translator falls back to
parsing inline (legacy compatibility mode).

Renamed in v5.1.2-beta-r2: dropped the vendor-protocol prefix from the
translator name. The translator is no longer protocol-specific.

Expected `source_config` structure (minimal):

    {
        "translator": "parcel_master",
        "parcel_id_prefix": "CAD-"
    }

Legacy-compatibility config (when scraper emits raw exemptions string):

    {
        "translator": "parcel_master",
        "translator_config": {
            "exemption_codes": {
                "homestead": ["HS"],
                "over_65": ["OV65", "O65"],
                "disabled": ["DV", "DIS"],
                "veteran": ["VET", "DAV"]
            }
        }
    }

Returns: ([], parcels, {})
"""

from __future__ import annotations
from typing import Any

from scaffold.pipeline.translators import register


def _parse_legacy_exemptions(
    raw_exemptions: str | None,
    exemption_codes: dict[str, list[str]],
) -> dict[str, bool]:
    """
    Parse a source's exemption STRING into framework-canonical bool flags.

    Legacy-compatibility path. New scrapers should emit exempt_* booleans
    directly in raw_payload and skip this code path.
    """
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


@register("parcel_master")
def translate_parcel_master(
    raw_records: list[dict],
    county_config: dict,
    source_config: dict,
) -> tuple[list[dict], list[dict], dict[str, dict]]:
    """
    Translate normalized parcel-master raw records into framework parcel records.

    Returns:
        (signals=[], parcels, per_signal_meta_by_url={})
    """
    tc = source_config.get("translator_config", {}) or {}
    legacy_exemption_codes = tc.get("exemption_codes", {})

    parcels: list[dict] = []

    for raw in raw_records:
        # Canonical shape: raw_payload contains normalized fields.
        payload = raw.get("raw_payload", {}) or {}

        parcel_id = payload.get("parcel_id")
        if parcel_id is None or str(parcel_id).strip() == "":
            continue

        # Parse exemptions. Prefer pre-parsed booleans; fall back to legacy
        # string parsing if booleans absent and legacy config provided.
        if "exempt_homestead" in payload or "exempt_over_65" in payload:
            exemption_flags = {
                "exempt_homestead": bool(payload.get("exempt_homestead", False)),
                "exempt_over_65": bool(payload.get("exempt_over_65", False)),
                "exempt_disabled": bool(payload.get("exempt_disabled", False)),
                "exempt_veteran": bool(payload.get("exempt_veteran", False)),
            }
        else:
            raw_exemptions = payload.get("exemptions")
            exemption_flags = _parse_legacy_exemptions(
                raw_exemptions, legacy_exemption_codes
            )

        parcel = {
            "parcel_id": str(parcel_id).strip(),
            "address": (payload.get("address") or "").strip(),
            "owner_name": (payload.get("owner_name") or "").strip(),
            "owner_mailing_address": (payload.get("owner_mailing_address") or "").strip(),
            "owner_mailing_city": (payload.get("owner_mailing_city") or "").strip(),
            "owner_mailing_state": (payload.get("owner_mailing_state") or "").strip(),
            "owner_mailing_zip": (payload.get("owner_mailing_zip") or "").strip(),
            "city": (payload.get("city") or "").strip(),
            "zip": (payload.get("zip") or "").strip(),
            "assessed_value": _try_int(payload.get("assessed_value")),
            "land_value": _try_int(payload.get("land_value")),
            "improvement_value": _try_int(payload.get("improvement_value")),
            "year_built": _try_int(payload.get("year_built")),
            "property_use": payload.get("property_use") or "",
            "acres": payload.get("acres"),
            "legal_description": (payload.get("legal_description") or "").strip(),
            "parcel_master_status": "matched_pending_join",
            **exemption_flags,
        }
        parcels.append(parcel)

    return [], parcels, {}
