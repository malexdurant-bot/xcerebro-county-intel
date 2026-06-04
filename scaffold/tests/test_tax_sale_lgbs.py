"""
Offline test for the Bexar tax-sale scraper (scrapers/tax_sale_lgbs.py).

Drives run() with an injected fetch_fn (no network) over a realistic 2-page
DRF response — verifies pagination, idempotent dedup by uid, the §4.32
envelope shape, and the run-metadata distributions. Fixture record mirrors a
real /api/property_sales record captured during recon (see
runs/bexar_tx/recon/tax_sale_lgbs_recon.md).

Standalone (not in run_all.py's universal gate), matching the foreclosure /
clerk-scraper adapter tests. Run with PYTHONUTF8=1.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scrapers import tax_sale_lgbs as ts  # noqa: E402


def _assert(name: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if not cond else ""))
    return cond


def _rec(uid, status="Scheduled for Auction", sale_type="SALE"):
    return {
        "uid": uid, "sale_id": 1000000000 + uid, "venue_id": 1112,
        "county": "BEXAR COUNTY", "state": "TX", "cause_nbr": f"2006TA{uid}",
        "precinct": "", "sale_nbr": 1, "sale_date": "2026-06-02T10:00:00",
        "sale_date_only": "2026-06-02", "sale_type": sale_type, "status": status,
        "account_nbr": f"02424004002{uid}", "street_name": "TORREON",
        "prop_address_one": f"{uid} TORREON", "prop_address_two": "",
        "prop_city": "SAN ANTONIO", "prop_state": "TX", "prop_zipcode": "78207",
        "value": "10970.00", "minimum_bid": "10866.25",
        "geometry": {"type": "Point", "coordinates": [-98.524135, 29.421119]},
    }


def _pages():
    p2 = "https://taxsales.lgbs.com/api/property_sales/?page=2"
    return {
        ts._first_page_url(): {"count": 3, "next": p2, "previous": None,
                               "results": [_rec(1), _rec(2, sale_type="RESALE")]},
        # uid 2 repeats across the page boundary -> must dedup to one.
        p2: {"count": 3, "next": None, "previous": None,
             "results": [_rec(2, sale_type="RESALE"), _rec(3, status="Sold")]},
    }


def test_pagination_dedup_envelope() -> int:
    pages = _pages()
    fetch_fn = lambda url: json.dumps(pages[url])  # noqa: E731
    ok = True
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "tax_sale_lgbs.jsonl"
        meta = ts.run(output_path=out, runs_dir=Path(d) / "runs",
                      fetch_fn=fetch_fn, sleep_fn=lambda s: None)
        lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
        ok &= _assert("two pages fetched", meta["pages_fetched"] == 2, str(meta["pages_fetched"]))
        ok &= _assert("4 raw records seen across pages", meta["records_fetched"] == 4, str(meta["records_fetched"]))
        ok &= _assert("deduped to 3 unique uids", meta["unique_uids"] == 3 and meta["records_written"] == 3,
                      f"unique={meta['unique_uids']} written={meta['records_written']}")
        ok &= _assert("one line per unique uid", len(lines) == 3, str(len(lines)))
        env = lines[0]
        ok &= _assert("§4.32 envelope keys present",
                      all(k in env for k in ("raw_record_id", "source_id", "source_url",
                                             "source_fetched_at", "parser_confidence", "raw_payload")),
                      sorted(env.keys()))
        ok &= _assert("raw_record_id keyed on uid", env["raw_record_id"].startswith("tax_sale_lgbs_"),
                      env["raw_record_id"])
        ok &= _assert("raw_payload preserved verbatim", env["raw_payload"]["cause_nbr"].startswith("2006TA"),
                      str(env["raw_payload"].get("cause_nbr")))
        ok &= _assert("sale_type distribution computed",
                      meta["sale_type_distribution"] == {"SALE": 2, "RESALE": 1},
                      str(meta["sale_type_distribution"]))
        ok &= _assert("status success", meta["status"] == "success", meta["status"])
    return 0 if ok else 1


def test_no_results() -> int:
    empty = {ts._first_page_url(): {"count": 0, "next": None, "previous": None, "results": []}}
    ok = True
    with tempfile.TemporaryDirectory() as d:
        meta = ts.run(output_path=Path(d) / "o.jsonl", runs_dir=Path(d) / "runs",
                      fetch_fn=lambda u: json.dumps(empty[u]), sleep_fn=lambda s: None)
        ok &= _assert("empty result -> status no_results", meta["status"] == "no_results", meta["status"])
        ok &= _assert("zero records written", meta["records_written"] == 0, str(meta["records_written"]))
    return 0 if ok else 1


def main() -> int:
    print("[scraper test] tax_sale_lgbs")
    rcs = [test_pagination_dedup_envelope(), test_no_results()]
    failures = sum(1 for rc in rcs if rc != 0)
    print(f"\nfailures: {failures} of {len(rcs)}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
