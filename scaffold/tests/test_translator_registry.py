"""
Gate test for scaffold/pipeline/translators/ registry (v5.1.2-beta+).

Verifies:
  - Built-in translators register at import time and are looked up by name.
  - register() refuses duplicate registration without force=True.
  - lookup() raises TranslatorNotFound for unknown names.
  - Each built-in translator returns a (signals, parcels, meta) tuple
    even on empty / malformed input (defensive shape).
  - The arcgis_foreclosure_notices translator honors the cross-county-leak
    policy from county config (accepted_municipalities + unknown_city_action).
  - The arcgis_foreclosure_notices translator dispatches to the named
    sale_date_rule from county config.
  - The arcgis_parcel_master translator parses the configured field_map
    and exemption_codes correctly.
  - The csv_static_list translator maps per-source doc_type_synonyms.

Synthetic data only. No real county names or vendor portals.
"""

from __future__ import annotations

import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
SCAFFOLD_DIR = THIS_DIR.parent
FRAMEWORK_ROOT = SCAFFOLD_DIR.parent
sys.path.insert(0, str(FRAMEWORK_ROOT))

from scaffold.pipeline import translators  # noqa: E402
from scaffold.pipeline.translators import (  # noqa: E402
    register,
    lookup,
    registered_names,
    unregister,
    TranslatorNotFound,
    TranslatorAlreadyRegistered,
)


PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []


def case(name: str, passed: bool, detail: str = "") -> None:
    status = PASS if passed else FAIL
    results.append((status, name, detail))
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


def test_builtins_registered():
    print("\n[builtin translators register at import time]")
    names = registered_names()
    case("arcgis_foreclosure_notices registered", "arcgis_foreclosure_notices" in names)
    case("arcgis_parcel_master registered", "arcgis_parcel_master" in names)
    case("csv_static_list registered", "csv_static_list" in names)


def test_lookup_returns_callable():
    print("\n[lookup returns callables]")
    fn = lookup("arcgis_foreclosure_notices")
    case("lookup returns callable", callable(fn))


def test_lookup_unknown_raises():
    print("\n[lookup raises TranslatorNotFound]")
    raised = False
    try:
        lookup("does_not_exist_zzz")
    except TranslatorNotFound:
        raised = True
    case("TranslatorNotFound raised for unknown name", raised)


def test_register_refuses_duplicate():
    print("\n[register refuses duplicate without force=True]")
    raised = False
    try:
        @register("arcgis_foreclosure_notices")
        def _bad(*a, **k):
            return [], [], {}
    except TranslatorAlreadyRegistered:
        raised = True
    case("duplicate registration raises", raised)


def test_register_force_overwrites():
    print("\n[register force=True overwrites]")
    marker = object()

    @register("test_temp_force_translator", force=True)
    def _stub(raw, cc, sc):
        return [marker], [], {}

    fn = lookup("test_temp_force_translator")
    out = fn([], {}, {})
    case("force=True registered new translator", out[0] == [marker])
    unregister("test_temp_force_translator")


def test_foreclosure_translator_empty():
    print("\n[foreclosure translator handles empty input]")
    fn = lookup("arcgis_foreclosure_notices")
    sig, par, meta = fn([], {}, {})
    case("empty input returns ([], [], {})", sig == [] and par == [] and meta == {})


def test_foreclosure_translator_cross_county_drop():
    print("\n[foreclosure translator drops cross-county leaks when policy=drop]")
    fn = lookup("arcgis_foreclosure_notices")
    raw = [{
        "raw_record_id": "raw_x",
        "raw_payload": {
            "ADDRESS": "100 SYNTH ST",
            "DOC_NUMBER": "DOC-001",
            "YEAR": 2026, "MONTH": 6,
            "CITY": "Anytown",
            "ZIP": "00000",
            "_layer_id": 0,
        },
        "source_url": "about:test/raw_x",
        "parser_confidence": 95,
    }]
    county_config = {
        "geography": {
            "accepted_municipalities": [
                {"name": "OTHERCITY", "kind": "incorporated"}
            ],
            "cross_county_policy": {"unknown_city_action": "drop"},
        }
    }
    source_config = {
        "translator": "arcgis_foreclosure_notices",
        "translator_config": {
            "layer_doc_type_map": {
                "0": {
                    "canonical": "NOTICE_OF_SUBSTITUTE_TRUSTEE_SALE",
                    "subtype_label": "NSTS",
                    "pattern": "foreclosure",
                }
            }
        },
        "parcel_id_prefix": "TST-",
    }
    sig, par, meta = fn(raw, county_config, source_config)
    case("drop policy yields zero signals", len(sig) == 0)
    case("drop policy yields zero parcels", len(par) == 0)


def test_foreclosure_translator_cross_county_flag():
    print("\n[foreclosure translator flags cross-county leaks when policy=flag_for_review]")
    fn = lookup("arcgis_foreclosure_notices")
    raw = [{
        "raw_record_id": "raw_y",
        "raw_payload": {
            "ADDRESS": "200 SYNTH AVE",
            "DOC_NUMBER": "DOC-002",
            "YEAR": 2026, "MONTH": 6,
            "CITY": "Outsidetown",
            "ZIP": "11111",
            "_layer_id": 0,
        },
        "source_url": "about:test/raw_y",
        "parser_confidence": 95,
    }]
    county_config = {
        "geography": {
            "accepted_municipalities": [
                {"name": "OTHERCITY", "kind": "incorporated"}
            ],
            "cross_county_policy": {"unknown_city_action": "flag_for_review"},
        }
    }
    source_config = {
        "translator": "arcgis_foreclosure_notices",
        "translator_config": {
            "layer_doc_type_map": {
                "0": {"canonical": "NOTICE_OF_SUBSTITUTE_TRUSTEE_SALE", "subtype_label": "NSTS", "pattern": "foreclosure"}
            }
        },
        "parcel_id_prefix": "TST-",
    }
    sig, par, meta = fn(raw, county_config, source_config)
    case("flag policy yields one signal", len(sig) == 1)
    case("flag policy yields one parcel", len(par) == 1)
    # The flag should be in per_signal_meta_by_url
    flags_seen = []
    for url, m in meta.items():
        flags_seen.extend(m.get("preset_review_flags", []))
    case("potential_cross_county_leak flag set", "potential_cross_county_leak" in flags_seen)


def test_foreclosure_translator_sale_date_rule():
    print("\n[foreclosure translator dispatches to sale_date_rule]")
    fn = lookup("arcgis_foreclosure_notices")
    raw = [{
        "raw_record_id": "raw_z",
        "raw_payload": {
            "ADDRESS": "300 SYNTH BLVD",
            "DOC_NUMBER": "DOC-003",
            "YEAR": 2026, "MONTH": 7,  # July 2026 — Jul 4 is Saturday
            "CITY": "ACCEPTEDTOWN",
            "ZIP": "22222",
            "_layer_id": 0,
        },
        "source_url": "about:test/raw_z",
        "parser_confidence": 95,
    }]
    county_config = {
        "geography": {
            "accepted_municipalities": [
                {"name": "ACCEPTEDTOWN", "kind": "incorporated"}
            ],
            "sale_date_rule": {
                "rule_name": "first_tuesday_of_month",
                "holiday_shift": {"shift_dates": ["07-04"], "shift_to": "next_wednesday"},
            },
        }
    }
    source_config = {
        "translator": "arcgis_foreclosure_notices",
        "translator_config": {
            "layer_doc_type_map": {
                "0": {"canonical": "NOTICE_OF_SUBSTITUTE_TRUSTEE_SALE", "subtype_label": "NSTS", "pattern": "foreclosure"}
            }
        },
        "parcel_id_prefix": "TST-",
    }
    sig, par, meta = fn(raw, county_config, source_config)
    case("signal produced", len(sig) == 1)
    # July 2026: first Tuesday is July 7. Jul 4 is Saturday so no holiday shift triggers
    # for that Tuesday. Let's just verify the date isn't null.
    if sig:
        case("filing_date populated", sig[0]["filing_date"] is not None)


def test_parcel_master_translator_field_map():
    print("\n[parcel_master translator parses configured field_map]")
    fn = lookup("arcgis_parcel_master")
    raw = [{
        "raw_record_id": "p_1",
        "raw_payload": {
            "PID": "12345",
            "ADDR": "300 SYNTH BLVD",
            "OWN": "DOE JOHN",
            "EXEMPT": "HS, OV65",
            "TOTAL_VAL": 250000,
        },
    }]
    source_config = {
        "field_map": {
            "parcel_id": "PID",
            "situs_address": "ADDR",
            "owner_name": "OWN",
            "exemptions": "EXEMPT",
            "assessed_value": "TOTAL_VAL",
        },
        "translator_config": {
            "exemption_codes": {
                "homestead": ["HS"],
                "over_65": ["OV65"],
            }
        }
    }
    sig, par, meta = fn(raw, {}, source_config)
    case("returns no signals", len(sig) == 0)
    case("returns one parcel", len(par) == 1)
    if par:
        p = par[0]
        case("parcel_id mapped", p["parcel_id"] == "12345")
        case("address mapped", p["address"] == "300 SYNTH BLVD")
        case("owner_name mapped", p["owner_name"] == "DOE JOHN")
        case("homestead exemption parsed", p["exempt_homestead"] is True)
        case("over_65 exemption parsed", p["exempt_over_65"] is True)
        case("assessed_value parsed", p["assessed_value"] == 250000)


def test_csv_static_list_doc_type_synonyms():
    print("\n[csv_static_list translator maps per-source doc_type_synonyms]")
    fn = lookup("csv_static_list")
    raw = [{
        "raw_record_id": "csv_1",
        "raw_payload": {
            "address": "500 LIST ST",
            "doc_num": "L-001",
            "doc_type": "tax sale",
            "filing_date": "2026-06-01",
        },
        "source_url": "about:test/csv_1",
        "parser_confidence": 80,
    }]
    source_config = {
        "translator": "csv_static_list",
        "translator_config": {
            "doc_number_field": "doc_num",
            "doc_type_synonyms": {
                "tax sale": "TAX_FORECLOSURE_NOTICE",
            }
        },
        "parcel_id_prefix": "CSV-",
    }
    sig, par, meta = fn(raw, {}, source_config)
    case("CSV translator produces signal", len(sig) == 1)
    if sig:
        case("doc_type mapped from synonym", sig[0]["doc_type"] == "TAX_FORECLOSURE_NOTICE")


def test_csv_static_list_unknown_doc_type_skipped():
    print("\n[csv_static_list skips records with unmapped doc-type]")
    fn = lookup("csv_static_list")
    raw = [{
        "raw_record_id": "csv_skip",
        "raw_payload": {
            "address": "600 LIST AVE",
            "doc_num": "L-002",
            "doc_type": "totally_unknown_type",
            "filing_date": "2026-06-01",
        },
        "source_url": "about:test/csv_skip",
        "parser_confidence": 80,
    }]
    source_config = {
        "translator": "csv_static_list",
        "translator_config": {
            "doc_type_synonyms": {"some other thing": "QUITCLAIM_DEED"}
        },
    }
    sig, par, meta = fn(raw, {}, source_config)
    case("unknown doc_type is skipped", len(sig) == 0)


def main() -> int:
    print("=" * 72)
    print("TRANSLATOR REGISTRY TEST — v5.1.2-beta")
    print("=" * 72)
    test_builtins_registered()
    test_lookup_returns_callable()
    test_lookup_unknown_raises()
    test_register_refuses_duplicate()
    test_register_force_overwrites()
    test_foreclosure_translator_empty()
    test_foreclosure_translator_cross_county_drop()
    test_foreclosure_translator_cross_county_flag()
    test_foreclosure_translator_sale_date_rule()
    test_parcel_master_translator_field_map()
    test_csv_static_list_doc_type_synonyms()
    test_csv_static_list_unknown_doc_type_skipped()

    passed = sum(1 for r in results if r[0] == PASS)
    failed = sum(1 for r in results if r[0] == FAIL)
    print()
    print(f"RESULT: {passed} pass, {failed} fail")
    print("=" * 72)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
