"""
Translator + integration test for the tax_sale_lgbs translator
(scaffold/pipeline/translators/tax_sale_lgbs.py).

Two layers:
  1. Translator unit behavior — registration, three-tuple contract,
     canonical/subtype mapping, field_map bridging, lifecycle suppression
     by status, address-less uid fallback, per-record validation.
  2. KEYSTONE integration — drive the translator output through the real
     v5.4.0 orchestrator (build_leads.run_pipeline) and assert the records
     SURVIVE the normalize->bridge seam as scored `tax`-pattern leads (the
     seam silently drops anything that doesn't normalize to a registry
     canonical, so this proves the wiring works end-to-end).

Standalone (not in run_all.py's universal gate). Run with PYTHONUTF8=1.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scaffold.pipeline import build_leads as bl  # noqa: E402
from scaffold.pipeline.translators import lookup, registered_names  # noqa: E402

SOURCE_CFG = {
    "_source_id": "tax_sale_lgbs",
    "lead_value": "LEAD_GENERATING",
    "translator_config": {
        "canonical": "TAX_FORECLOSURE_NOTICE",
        "subtype_label": "Tax Foreclosure Notice",
        "lifecycle_suppression_statuses": ["Sold", "Cancelled"],
    },
    "field_map": {
        "doc_number": "cause_nbr", "address": "prop_address_one",
        "city": "prop_city", "zip": "prop_zipcode", "filing_date": "sale_date_only",
    },
    "parcel_id_prefix": "BX-TS-",
}


def _assert(name: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if not cond else ""))
    return cond


def _raw(uid, status="Scheduled for Auction", sale_type="SALE", address="1017 TORREON",
         date="2026-06-02"):
    return {
        "raw_record_id": f"tax_sale_lgbs_{uid}", "source_id": "tax_sale_lgbs",
        "source_url": f"https://taxsales.lgbs.com/api/property_sales/?uid={uid}",
        "source_fetched_at": "2026-06-03T00:00:00Z", "parser_confidence": 98,
        "raw_payload": {
            "uid": uid, "cause_nbr": f"2006TA{uid}", "status": status, "sale_type": sale_type,
            "sale_date_only": date, "prop_address_one": address, "prop_city": "SAN ANTONIO",
            "prop_zipcode": "78207", "account_nbr": f"acct{uid}", "value": "10970.00",
            "minimum_bid": "10866.25", "geometry": {"type": "Point", "coordinates": [-98.5, 29.4]},
        },
    }


def test_registered() -> int:
    return 0 if _assert("tax_sale_lgbs registered", "tax_sale_lgbs" in registered_names(),
                        str(registered_names())) else 1


def test_three_tuple_mapping_and_fieldmap() -> int:
    fn = lookup("tax_sale_lgbs")
    out = fn([_raw(1, sale_type="RESALE")], {"geography": {}}, SOURCE_CFG)
    ok = _assert("returns 3-tuple", isinstance(out, tuple) and len(out) == 3)
    sig, parc, meta = out
    ok &= _assert("one signal, one parcel", len(sig) == 1 and len(parc) == 1)
    s = sig[0]
    ok &= _assert("canonical = TAX_FORECLOSURE_NOTICE", s["doc_type"] == "TAX_FORECLOSURE_NOTICE", s["doc_type"])
    ok &= _assert("subtype label is normalizer-recognized", s["doc_type_subtype_label"] == "Tax Foreclosure Notice",
                  s["doc_type_subtype_label"])
    ok &= _assert("field_map: doc_number<-cause_nbr", s["doc_number"] == "2006TA1", s["doc_number"])
    ok &= _assert("field_map: filing_date<-sale_date_only", s["filing_date"] == "2026-06-02", s["filing_date"])
    ok &= _assert("parcel_id uses prefix", s["primary_parcel_id"].startswith("BX-TS-"), s["primary_parcel_id"])
    m = list(meta.values())[0]
    ok &= _assert("sale_type carried in meta (not as doc-type)", m["sale_type"] == "RESALE", str(m.get("sale_type")))
    ok &= _assert("account_nbr carried for enrichment", m["account_nbr"] == "acct1", str(m.get("account_nbr")))
    return 0 if ok else 1


def test_lifecycle_suppression() -> int:
    fn = lookup("tax_sale_lgbs")
    raws = [_raw(1, status="Scheduled for Auction"), _raw(2, status="Sold"),
            _raw(3, status="Cancelled"), _raw(4, status="Struck off to Jurisdiction")]
    sig, _, _ = fn(raws, {"geography": {}}, SOURCE_CFG)
    # Sold + Cancelled suppressed; Scheduled + Struck off kept.
    return 0 if _assert("Sold/Cancelled suppressed, others kept", len(sig) == 2, str(len(sig))) else 1


def test_validation_and_future_no_date() -> int:
    fn = lookup("tax_sale_lgbs")
    no_addr = _raw(9); no_addr["raw_payload"]["prop_address_one"] = ""          # emits (uid parcel)
    bad_conf = _raw(10); bad_conf["parser_confidence"] = "x"                    # skip
    malformed = _raw(11); malformed["raw_payload"]["sale_date_only"] = "06/02/2026"  # skip (present, bad fmt)
    future = _raw(12, status="Available for Future Sale")
    future["raw_payload"]["sale_date_only"] = ""                               # emits, unscheduled
    sig, _, _ = fn([no_addr, bad_conf, malformed, future], {"geography": {}}, SOURCE_CFG)
    ok = _assert("address-less + future-no-date emit; bad-conf + malformed-date skip",
                 len(sig) == 2, str(len(sig)))
    fut = [s for s in sig if s["doc_number"] == "2006TA12"]
    ok &= _assert("future-sale emitted with filing_date=None",
                  bool(fut) and fut[0]["filing_date"] is None,
                  str(fut[0]["filing_date"]) if fut else "missing")
    return 0 if ok else 1


def test_keystone_end_to_end() -> int:
    fn = lookup("tax_sale_lgbs")
    raws = [_raw(i) for i in (1, 2, 3)] + [_raw(4, status="Sold")]  # one suppressed
    sig, parc, _ = fn(raws, {"geography": {}}, SOURCE_CFG)
    adapted = [bl._adapt_translator_signal(s, "tax_sale_lgbs") for s in sig]
    ok = True
    with tempfile.TemporaryDirectory() as d:
        res = bl.run_pipeline(
            mode="production", parcels=parc, raw_signals=adapted,
            county_id="bexar_tx", county_name="Bexar", state="TX",
            scoring_overrides={}, as_of=None, build_label="PARTIAL_BUILD",
            build_label_reason="", deployment={}, workdir=d, approve_needs_review=False)
        p = res["payload"]
        ok &= _assert("§20 DEPLOY_OK", res["semantic_verdict"] == "DEPLOY_OK", res["semantic_verdict"])
        ok &= _assert("3 active leads survive the seam (1 Sold suppressed upstream)",
                      p["lead_total"] == 3, str(p["lead_total"]))
        ok &= _assert("all score as the tax pattern", p["pattern_counts"] == {"tax": 3},
                      str(p["pattern_counts"]))
        ok &= _assert("nothing dropped at normalize->bridge seam",
                      p.get("dropped_signals_unmapped_doc_type") == 0,
                      str(p.get("dropped_signals_unmapped_doc_type")))
    return 0 if ok else 1


def main() -> int:
    print("[translator test] tax_sale_lgbs")
    rcs = [
        test_registered(),
        test_three_tuple_mapping_and_fieldmap(),
        test_lifecycle_suppression(),
        test_validation_and_future_no_date(),
        test_keystone_end_to_end(),
    ]
    failures = sum(1 for rc in rcs if rc != 0)
    print(f"\nfailures: {failures} of {len(rcs)}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
