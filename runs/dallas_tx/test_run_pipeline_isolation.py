#!/usr/bin/env python3
"""One failing source must not abort the Dallas daily run.

From Sep 29 to Oct 9 2026 a single failing scraper (tax roll MemoryError,
then a TLS error on the tax-sale feed) aborted Step 1, so nothing refreshed
and the dashboard went ~9 days stale. This drives the real run_pipeline.main()
(--dry-run, so it stops right after Step 1) with stub scrapers and checks that
a source failure is isolated, surfaced, and recorded -- and that Ctrl-C is not
swallowed.

Run: python3 runs/dallas_tx/test_run_pipeline_isolation.py
Exit 0 = pass, non-zero = fail.
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for p in (str(REPO_ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import run_pipeline as rp
import scrapers.publicsearch_recorder_dallas as m_rp
import scrapers.publicsearch_foreclosures_dallas as m_fc
import scrapers.tax_collector_dallas as m_tax
import scrapers.taxsales_lgbs_dallas as m_lgbs
import scrapers.parcel_master_dcad_bulk_dallas as m_dcad

FAILS = []


def case(name, ok):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILS.append(name)


def run_step1(behaviors):
    """behaviors: {source_key: callable(out_dir, verbose=...) -> stats}. Returns
    (call_order, failures_file_contents_or_None, exception_or_None)."""
    calls = []

    def stub(key):
        def _run(out_dir, verbose=True, **kw):
            calls.append(key)
            return behaviors.get(key, lambda: {"errors": []})()
        return _run

    saved = [(m_rp, "run_scraper"), (m_fc, "run_scraper"), (m_tax, "run_scraper"),
             (m_lgbs, "run_scraper"), (m_dcad, "run_scraper")]
    originals = [getattr(m, a) for m, a in saved]
    for (m, a), key in zip(saved, ("rp", "fc", "tax", "lgbs", "dcad")):
        setattr(m, a, stub(key))

    orig_argv, orig_work, orig_raw = sys.argv, rp.WORKDIR, rp.RAW_DIR
    exc = None
    with tempfile.TemporaryDirectory() as td:
        rp.WORKDIR = Path(td)
        rp.RAW_DIR = Path(td) / "raw"
        sys.argv = ["run_pipeline.py", "--dry-run"]
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rp.main()
        except BaseException as e:  # noqa: BLE001
            exc = e
        finally:
            sys.argv, rp.WORKDIR, rp.RAW_DIR = orig_argv, orig_work, orig_raw
            for (m, a), orig in zip(saved, originals):
                setattr(m, a, orig)
        f = Path(td) / "source_failures.json"
        data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    return calls, data, exc


def boom():
    raise RuntimeError("synthetic failure")


print("a clean run")
calls, data, exc = run_step1({})
case("every source is scraped, in order",
     calls == ["rp", "fc", "tax", "lgbs", "dcad"])
case("no exception", exc is None)
case("source_failures.json is written and empty", data is not None and data["failures"] == [])

print("one scraper raises")
calls, data, exc = run_step1({"tax": boom})
case("the run is NOT aborted", exc is None)
case("sources after the failing one still run", calls == ["rp", "fc", "tax", "lgbs", "dcad"])
case("the failure is recorded with its source and message",
     data and [f["source"] for f in data["failures"]] == ["tax_collector"]
     and "synthetic failure" in data["failures"][0]["error"]
     and data["failures"][0]["kind"] == "exception")

print("a MemoryError (the actual Sep 29 failure) is also contained")
def oom():
    raise MemoryError()
calls, data, exc = run_step1({"tax": oom})
case("MemoryError in one source does not abort the run", exc is None and len(calls) == 5)

print("a scraper that RETURNS errors instead of raising")
calls, data, exc = run_step1({"fc": lambda: {"errors": ["scrape_error: search did not resolve"]}})
case("returned errors are surfaced, not ignored",
     data and [f["source"] for f in data["failures"]] == ["foreclosure_notices"]
     and data["failures"][0]["kind"] == "reported_errors")

print("several sources fail")
calls, data, exc = run_step1({"rp": boom, "lgbs": boom})
case("all failures are listed", data and [f["source"] for f in data["failures"]] == ["clerk_recordings", "tax_sales_lgbs"])
case("the run still reaches the end", exc is None and len(calls) == 5)

print("Ctrl-C is not swallowed")
def interrupt():
    raise KeyboardInterrupt()
calls, data, exc = run_step1({"fc": interrupt})
case("KeyboardInterrupt propagates", isinstance(exc, KeyboardInterrupt))

if FAILS:
    print(f"\n{len(FAILS)} FAILED")
    sys.exit(1)
print("\nall passed")
