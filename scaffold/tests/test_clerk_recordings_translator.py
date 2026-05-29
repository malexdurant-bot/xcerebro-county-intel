"""
Translator + integration test for the publicsearch_clerk_recordings
translator (scaffold/pipeline/translators/publicsearch_clerk_recordings.py).

Two layers:
  1. Translator unit behavior — three-tuple contract, code->canonical
     mapping, field_map, address-less parcel fallback, LEVEL 2 flags,
     per-record validation, unsupported-code skip+count.
  2. KEYSTONE integration — drive the translator output through the real
     v5.4.0 orchestrator (build_leads.run_pipeline) and assert the 5
     wired clerk patterns SURVIVE the normalize->bridge seam as scored
     leads (the seam silently drops anything that doesn't normalize to a
     registry canonical, so this is the assertion that the wiring works).

Standalone (not in run_all.py's universal gate), matching the
foreclosure / clerk-scraper adapter tests. Run with PYTHONUTF8=1.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scaffold.pipeline import build_leads as bl  # noqa: E402
from scaffold.pipeline.translators import lookup, registered_names  # noqa: E402

TRANSLATOR = "publicsearch_clerk_recordings"


def _assert(label, cond, detail=""):
    if cond:
        print(f"  [PASS] {label}")
        return True
    print(f"  [FAIL] {label}  --  {detail}")
    return False


def _county_config():
    return json.loads(
        (REPO_ROOT / "config" / "counties" / "bexar_tx.json").read_text(encoding="utf-8")
    )


def _src_config(county):
    src = dict(county["sources"]["clerk_recordings"])
    src["_source_id"] = "clerk_recordings"
    return src


def _raw(doc_id, code, addr, grantor="DOE JOHN", grantee="ACME BANK",
         dn=None, conf=95, recorded="2026-05-18"):
    return {
        "raw_record_id": f"publicsearch_bexar_{doc_id}",
        "source_id": "publicsearch_clerk_recordings",
        "source_url": f"https://bexar.tx.publicsearch.us/doc/{doc_id}",
        "source_fetched_at": "2026-05-20T00:00:00Z",
        "parser_confidence": conf,
        "raw_payload": {
            "internal_doc_id": doc_id,
            "document_number": dn or f"2026{doc_id}",
            "doc_type_code": code,
            "doc_type_label": code,  # portal free-text label (varies; ignored)
            "recorded_date": recorded,
            "grantor": grantor,
            "grantee": grantee,
            "property_address": addr,
            "legal_description": "LOT 1 BLK 2",
            "book_volume_page": None,
            "parcel_grid_identifiers": "Lot 1, Block 2, NCB N/A, County Block N/A",
        },
    }


_SUPPORTED = [
    ("1001", "LIS PEN", "100 MAIN ST, SAN ANTONIO, TEXAS, 78201"),
    ("1002", "MECHLN", "200 OAK AVE, SAN ANTONIO, TEXAS, 78202"),
    ("1003", "FTL", "300 ELM ST, SAN ANTONIO, TEXAS, 78203"),
    ("1004", "STL", "400 PINE ST, SAN ANTONIO, TEXAS, 78204"),
    ("1005", "HOSP LN", None),  # address-less -> internal_doc_id fallback
]


def test_registered() -> int:
    ok = _assert(f"{TRANSLATOR} registered", TRANSLATOR in registered_names())
    ok &= _assert("lookup returns callable", callable(lookup(TRANSLATOR)))
    return 0 if ok else 1


def test_three_tuple_and_mapping() -> int:
    county = _county_config()
    fn = lookup(TRANSLATOR)
    raws = [_raw(d, c, a) for d, c, a in _SUPPORTED] + \
           [_raw("1006", "NOTICE", "600 CEDAR, SAN ANTONIO, TEXAS, 78205")]
    out = fn(raws, county, _src_config(county))
    ok = True
    ok &= _assert("returns 3-tuple", isinstance(out, tuple) and len(out) == 3)
    signals, parcels, meta = out
    ok &= _assert("5 supported -> 5 signals (NOTICE skipped)", len(signals) == 5,
                  f"got {len(signals)}")
    ok &= _assert("5 parcels", len(parcels) == 5)
    ok &= _assert("meta keyed by source_url", len(meta) == 5)
    by_canon = {s["doc_type"] for s in signals}
    ok &= _assert("canonical doc types mapped",
                  by_canon == {"LIS_PENDENS", "MECHANICS_LIEN", "FEDERAL_TAX_LIEN",
                               "STATE_TAX_LIEN", "HOSPITAL_LIEN"}, str(by_canon))
    lis = next(s for s in signals if s["doc_type"] == "LIS_PENDENS")
    ok &= _assert("subtype_label is normalizer-recognized (code-driven)",
                  lis["doc_type_subtype_label"] == "LIS PENDENS")
    ok &= _assert("filing_date = recorded_date", lis["filing_date"] == "2026-05-18")
    ok &= _assert("field_map: doc_number read from document_number",
                  lis["doc_number"] == "20261001")
    ok &= _assert("grantor carried for §17 party extraction",
                  lis["grantor"] == "DOE JOHN")
    return 0 if ok else 1


def test_address_less_fallback() -> int:
    county = _county_config()
    fn = lookup(TRANSLATOR)
    signals, parcels, _ = fn([_raw("1005", "HOSP LN", None)], county, _src_config(county))
    ok = True
    ok &= _assert("address-less still emits a signal", len(signals) == 1)
    ok &= _assert("address-less parcel uses BX-PS- prefix",
                  parcels[0]["parcel_id"].startswith("BX-PS-"))
    ok &= _assert("address-less parcel address is null", parcels[0]["address"] is None)
    ok &= _assert("address-less parcel owner_name = grantor",
                  parcels[0]["owner_name"] == "DOE JOHN")
    return 0 if ok else 1


def test_validation_skips() -> int:
    county = _county_config()
    fn = lookup(TRANSLATOR)
    bad = [
        _raw("2001", "LIS PEN", "1 A ST", conf="high"),       # bad confidence
        _raw("", "LIS PEN", "2 B ST"),                          # missing doc id
        _raw("2003", "LIS PEN", "3 C ST", recorded="18-05-2026"),  # bad date
    ]
    signals, _, _ = fn(bad, county, _src_config(county))
    return 0 if _assert("invalid records all skipped", len(signals) == 0,
                        f"got {len(signals)}") else 1


def test_level2_flags() -> int:
    # County-agnostic flag logic: a code present in BOTH the map AND a flag
    # list must carry the flag. (Inert in Bexar's v1 5-pattern scope, so this
    # uses a synthetic source_config to prove the behavior.)
    county = _county_config()
    fn = lookup(TRANSLATOR)
    src = {
        "_source_id": "clerk_recordings",
        "parcel_id_prefix": "BX-PS-",
        "field_map": {"doc_number": "document_number", "address": "property_address"},
        "translator_config": {
            "doc_type_code_map": {
                "FC": {"canonical": "NOTICE_OF_SUBSTITUTE_TRUSTEE_SALE",
                       "subtype_label": "NOTICE OF SUBSTITUTE TRUSTEE SALE"},
                "RELEASE": {"canonical": "RELEASE_OF_LIEN", "subtype_label": "RELEASE"},
            },
            "foreclosure_dedup_codes": ["FC"],
            "lifecycle_suppression_codes": ["RELEASE"],
        },
    }
    signals, _, meta = fn(
        [_raw("3001", "FC", "9 FC ST"), _raw("3002", "RELEASE", "9 RL ST")],
        county, src,
    )
    fc_sig = next(s for s in signals if "3001" in s["raw_record_id"])
    rl_sig = next(s for s in signals if "3002" in s["raw_record_id"])
    ok = True
    ok &= _assert("FC code -> fc_dedup_required flag",
                  "fc_dedup_required" in meta[fc_sig["source_url"]]["preset_review_flags"])
    ok &= _assert("RELEASE code -> lifecycle_suppression flag",
                  "lifecycle_suppression" in meta[rl_sig["source_url"]]["preset_review_flags"])
    return 0 if ok else 1


def test_keystone_integration() -> int:
    """The 5 wired patterns must survive the orchestrator's normalize->bridge
    seam and reach scored leads (0 dropped). This is the assertion that
    proves the wiring actually produces leads."""
    county = _county_config()
    fn = lookup(TRANSLATOR)
    raws = [_raw(d, c, a) for d, c, a in _SUPPORTED] + \
           [_raw("1006", "NOTICE", "600 CEDAR, SAN ANTONIO, TEXAS, 78205")]
    signals, parcels, _ = fn(raws, county, _src_config(county))
    adapted = [bl._adapt_translator_signal(s, "clerk_recordings") for s in signals]
    with tempfile.TemporaryDirectory() as td:
        res = bl.run_pipeline(
            mode="production", parcels=parcels, raw_signals=adapted,
            county_id="bexar_tx", county_name="Bexar", state="TX",
            scoring_overrides=county.get("scoring_overrides", {}),
            build_label="SOURCE_LIMITED", workdir=Path(td),
            approve_needs_review=True,
        )
    ok = True
    ok &= _assert("§20 verdict DEPLOY_OK", res["semantic_verdict"] == "DEPLOY_OK",
                  res["semantic_verdict"])
    ok &= _assert("all 5 patterns survive the seam -> 5 scored leads",
                  len(res["scored_leads"]) == 5, str(len(res["scored_leads"])))
    ok &= _assert("0 signals dropped as unmapped doc type",
                  res["payload"].get("dropped_signals_unmapped_doc_type") == 0)
    ok &= _assert("pattern_counts cover lien + tax + foreclosure",
                  res["payload"]["pattern_counts"] == {"foreclosure": 1, "lien": 2, "tax": 2},
                  str(res["payload"]["pattern_counts"]))
    return 0 if ok else 1


def test_probate_cluster_end_to_end() -> int:
    """The probate/estate cluster (PROBATE / WILL / LETTERS), unlocked by the
    v5.4.0 framework-vocabulary extension (PROBATE_RECORDING / WILL_RECORDING /
    PROBATE_LETTERS -> §16 'Probate'), maps and reaches scored leads as the
    'estate' pattern. Umbrella probate recordings have no document body, so
    §17 routes them to REVIEW_REQUIRED — created, not dropped."""
    county = _county_config()
    fn = lookup(TRANSLATOR)
    raws = [
        _raw("2001", "PROBATE", "700 ESTATE LN, SAN ANTONIO, TEXAS, 78209"),
        _raw("2002", "WILL", "800 LEGACY DR, SAN ANTONIO, TEXAS, 78210"),
        _raw("2003", "LETTERS", "900 HEIR ST, SAN ANTONIO, TEXAS, 78211"),
    ]
    signals, parcels, _ = fn(raws, county, _src_config(county))
    ok = True
    ok &= _assert("probate cluster maps to umbrella canonicals",
                  {s["doc_type"] for s in signals} ==
                  {"PROBATE_RECORDING", "WILL_RECORDING", "PROBATE_LETTERS"},
                  str({s["doc_type"] for s in signals}))
    adapted = [bl._adapt_translator_signal(s, "clerk_recordings") for s in signals]
    with tempfile.TemporaryDirectory() as td:
        res = bl.run_pipeline(
            mode="production", parcels=parcels, raw_signals=adapted,
            county_id="bexar_tx", county_name="Bexar", state="TX",
            scoring_overrides=county.get("scoring_overrides", {}),
            build_label="SOURCE_LIMITED", workdir=Path(td),
            approve_needs_review=True,
        )
    ok &= _assert("probate cluster survives the seam -> 3 estate leads",
                  len(res["scored_leads"]) == 3
                  and res["payload"].get("dropped_signals_unmapped_doc_type") == 0,
                  str(len(res["scored_leads"])))
    ok &= _assert("probate cluster scores as the estate pattern",
                  res["payload"]["pattern_counts"] == {"estate": 3},
                  str(res["payload"]["pattern_counts"]))
    return 0 if ok else 1


def main() -> int:
    print("[translator test] publicsearch_clerk_recordings")
    rcs = [
        test_registered(),
        test_three_tuple_and_mapping(),
        test_address_less_fallback(),
        test_validation_skips(),
        test_level2_flags(),
        test_keystone_integration(),
        test_probate_cluster_end_to_end(),
    ]
    failures = sum(1 for rc in rcs if rc != 0)
    print(f"\nfailures: {failures} of {len(rcs)}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
