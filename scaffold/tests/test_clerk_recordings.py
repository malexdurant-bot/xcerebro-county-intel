"""
Adapter test for scrapers/clerk_recordings.py (PublicSearch RP).

Per engineering/05_verification_and_rollback.md "Scraper fixture
requirement", every adapter ships with >= 8 fixtures covering the
realistic branches of the source. The injected `fetch_fn` returns the
appropriate rendered HTML based on the request URL (docTypes + offset),
so the adapter is exercised end-to-end without Playwright or the network.

The keystone fixture is the REAL captured result DOM
(`fixtures/clerk_recordings/result_list_real.html`, 50 rows) so the
header-driven parser is proven against ground truth; crafted edge-case
pages are built in-memory by `_page(...)`.

Runs as part of `scaffold/tests/run_all.py`.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scrapers import clerk_recordings as cr  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "clerk_recordings"
NO_SLEEP = lambda _s: None  # noqa: E731


def _assert(label, cond, detail=""):
    if cond:
        print(f"  [PASS] {label}")
        return True
    print(f"  [FAIL] {label}  --  {detail}")
    return False


# ---------------------------------------------------------------------
# In-memory page builder (crafted edge cases)
# ---------------------------------------------------------------------

# Default PublicSearch column order: token -> col index.
_DEFAULT_COLS = [
    ("Select all documents", None),
    ("Actions", None),
    ("Document status icons", None),
    ("Grantor", "grantor"),
    ("Grantee", "grantee"),
    ("Doc Type", "doc_type_label"),
    ("Recorded Date", "recorded_date"),
    ("Doc Number", "document_number"),
    ("Book/Volume/Page", "book_volume_page"),
    ("Legal Description", "legal_description"),
    ("Lot", "lot"),
    ("Block", "block"),
    ("NCB", "ncb"),
    ("County Block", "county_block"),
    ("Property Address", "property_address"),
]


def _page(rows, columns=_DEFAULT_COLS, include_checkbox=True):
    """Build a minimal PublicSearch-shaped result page.

    rows: list of dict keyed by the field name (grantor, doc_type_label,
    recorded_date, document_number, ...) plus optional "_doc_id".
    """
    thead = "<thead><tr>"
    for i, (label, _field) in enumerate(columns):
        thead += (f'<th class="col{i} is-sortable" scope="col" '
                  f'role="columnheader" aria-label="{label}, activate to sort">'
                  f'{label}</th>')
    thead += "</tr></thead>"

    body = "<tbody>"
    for row in rows:
        body += '<tr class="" role="row" aria-selected="false">'
        for i, (_label, field) in enumerate(columns):
            if i == 0:
                cb = ""
                if include_checkbox and row.get("_doc_id"):
                    cb = (f'<input id="table-checkbox-{row["_doc_id"]}" '
                          f'type="checkbox" class="checkbox__input">')
                body += f'<td class="col-{i}">{cb}</td>'
            elif field is None:
                body += f'<td class="col-{i}"></td>'
            else:
                val = row.get(field, "")
                body += f'<td class="col-{i}"><span>{val}</span></td>'
        body += "</tr>"
    body += "</tbody>"
    return f"<html><body><table>{thead}{body}</table></body></html>"


# ---------------------------------------------------------------------
# 1. Parse the real captured DOM (keystone)
# ---------------------------------------------------------------------

def test_parse_real_dom() -> int:
    html = (FIXTURES / "result_list_real.html").read_text(encoding="utf-8")
    recs = cr.parse_result_page(html, "NOTICE")
    ok = True
    ok &= _assert("real DOM: 50 rows parsed", len(recs) == 50, f"got {len(recs)}")
    ok &= _assert("real DOM: every row has internal_doc_id",
                  all(r["internal_doc_id"] for r in recs))
    ok &= _assert("real DOM: doc_type_code supplied from query param",
                  all(r["doc_type_code"] == "NOTICE" for r in recs))
    ok &= _assert("real DOM: recorded_date normalized to YYYY-MM-DD",
                  recs[0]["recorded_date"] == "2026-01-20",
                  recs[0]["recorded_date"])
    ok &= _assert("real DOM: address N/A normalized to null",
                  recs[0]["property_address"] is None)
    ok &= _assert("real DOM: a real address is preserved",
                  recs[1]["property_address"] ==
                  "20675 HUEBNER ROAD #709, SAN ANTONIO, TEXAS, 78258")
    ok &= _assert("real DOM: book/volume/page --/--/-- -> null",
                  recs[0]["book_volume_page"] is None)
    ok &= _assert("real DOM: parcel_grid keeps N/A sub-values verbatim",
                  recs[0]["parcel_grid_identifiers"] ==
                  "Lot 709, Block N/A, NCB N/A, County Block N/A")
    return 0 if ok else 1


# ---------------------------------------------------------------------
# 2. Header-driven mapping survives a reordered column layout (§5.3)
# ---------------------------------------------------------------------

def test_header_driven_reordered_columns() -> int:
    # Swap Grantor and Doc Number positions vs the default layout.
    cols = list(_DEFAULT_COLS)
    cols[3], cols[7] = cols[7], cols[3]  # Grantor <-> Doc Number column slots
    rows = [{"_doc_id": "999", "grantor": "SMITH JANE",
             "document_number": "20260099999", "doc_type_label": "LIS PENDENS",
             "recorded_date": "3/4/2026"}]
    recs = cr.parse_result_page(_page(rows, columns=cols), "LIS PEN")
    ok = True
    ok &= _assert("reordered: grantor still found by header", len(recs) == 1
                  and recs[0]["grantor"] == "SMITH JANE", str(recs))
    ok &= _assert("reordered: doc_number still found by header",
                  recs[0]["document_number"] == "20260099999")
    return 0 if ok else 1


# ---------------------------------------------------------------------
# 3. Empty page -> no rows
# ---------------------------------------------------------------------

def test_empty_page() -> int:
    recs = cr.parse_result_page(_page([]), "FTL")
    return 0 if _assert("empty page yields 0 rows", len(recs) == 0) else 1


# ---------------------------------------------------------------------
# 4. Missing doc number lowers confidence
# ---------------------------------------------------------------------

def test_missing_doc_number_confidence() -> int:
    rows = [{"_doc_id": "111", "doc_type_label": "LIS PENDENS",
             "recorded_date": "1/2/2026", "document_number": ""}]
    recs = cr.parse_result_page(_page(rows), "LIS PEN")
    wrapped = cr.wrap_record(recs[0], "2026-05-28T00:00:00Z")
    return 0 if _assert("missing doc# -> parser_confidence 70",
                        wrapped["parser_confidence"] == 70) else 1


# ---------------------------------------------------------------------
# 5. Malformed date -> None
# ---------------------------------------------------------------------

def test_malformed_date() -> int:
    rows = [{"_doc_id": "222", "doc_type_label": "LIS PENDENS",
             "recorded_date": "not-a-date", "document_number": "20260000222"}]
    recs = cr.parse_result_page(_page(rows), "LIS PEN")
    return 0 if _assert("malformed date -> recorded_date None",
                        recs[0]["recorded_date"] is None) else 1


# ---------------------------------------------------------------------
# 6. Row with no checkbox id is skipped (no stable id)
# ---------------------------------------------------------------------

def test_row_without_checkbox_skipped() -> int:
    rows = [{"doc_type_label": "LIS PENDENS", "recorded_date": "1/2/2026",
             "document_number": "20260000333"}]  # no _doc_id
    recs = cr.parse_result_page(_page(rows, include_checkbox=False), "LIS PEN")
    return 0 if _assert("row without checkbox id is skipped", len(recs) == 0) else 1


# ---------------------------------------------------------------------
# 7. Hard-halt detection (login wall / captcha)
# ---------------------------------------------------------------------

def test_hard_halt_detection() -> int:
    ok = True
    ok &= _assert("login wall detected",
                  cr.detect_hard_halt("<div>Please log in to continue</div>")
                  == "login_wall")
    ok &= _assert("captcha detected",
                  cr.detect_hard_halt("<div>reCAPTCHA challenge</div>")
                  == "captcha_or_bot_challenge")
    ok &= _assert("clean page -> no halt",
                  cr.detect_hard_halt(_page([{"_doc_id": "1"}])) is None)
    return 0 if ok else 1


# ---------------------------------------------------------------------
# 8. URL construction (§4.1)
# ---------------------------------------------------------------------

def test_url_construction() -> int:
    url = cr.build_results_url("LIS PEN", "20260501", "20260528", 50)
    ok = True
    ok &= _assert("URL encodes space in code", "docTypes=LIS%20PEN" in url, url)
    ok &= _assert("URL uses YYYYMMDD comma range",
                  "recordedDateRange=20260501,20260528" in url, url)
    ok &= _assert("URL limit=50 offset=50", "limit=50" in url and "offset=50" in url)
    ok &= _assert("URL department RP", "department=RP" in url)
    return 0 if ok else 1


# ---------------------------------------------------------------------
# 9. Mode + arg rejection (§2.2 / §2.4)
# ---------------------------------------------------------------------

def test_mode_rejection() -> int:
    ok = True
    for mode in ("historical_lookup", "bogus_mode"):
        try:
            cr.run(mode=mode, fetch_fn=lambda u: _page([]), sleep_fn=NO_SLEEP)
            ok &= _assert(f"{mode} rejected", False, "no error raised")
        except ValueError:
            ok &= _assert(f"{mode} rejected with ValueError", True)
    try:
        cr.run(mode="first_run_backfill", backfill_days=3,
               fetch_fn=lambda u: _page([]), sleep_fn=NO_SLEEP)
        ok &= _assert("bad backfill-days rejected", False, "no error raised")
    except ValueError:
        ok &= _assert("backfill-days=3 rejected (allowed 1/7/14/30)", True)
    return 0 if ok else 1


# ---------------------------------------------------------------------
# 10. Date-window computation (§2.3 / §2.4)
# ---------------------------------------------------------------------

def test_compute_window() -> int:
    today = date(2026, 5, 28)
    ok = True
    s, e, fb = cr.compute_window("daily_refresh", None, today, None)
    ok &= _assert("daily_refresh no-state -> 30d fallback + flag",
                  fb is True and (today - s).days == 30 and e == today)
    s, e, fb = cr.compute_window(
        "daily_refresh", {"last_successful_recorded_date": "2026-05-20"},
        today, None)
    ok &= _assert("daily_refresh with state -> last minus 3d overlap",
                  fb is False and s == date(2026, 5, 17) and e == today)
    s, e, fb = cr.compute_window("first_run_backfill", None, today, 7)
    ok &= _assert("backfill 7d window", (today - s).days == 7 and fb is False)
    return 0 if ok else 1


# ---------------------------------------------------------------------
# 11. run() end-to-end: append-only output, run metadata, cursor advance
# ---------------------------------------------------------------------

class _Fetch:
    """Maps a results URL -> rendered HTML. One row at offset 0 for every
    code; empty at higher offsets. Optional per-code overrides for failure
    and halt simulation."""

    def __init__(self, fail_codes=None, halt_codes=None):
        self.fail_codes = set(fail_codes or [])
        self.halt_codes = set(halt_codes or [])
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        q = parse_qs(urlparse(url).query)
        code = q.get("docTypes", [""])[0]
        offset = int(q.get("offset", ["0"])[0])
        if code in self.halt_codes:
            return "<div>Please log in to continue</div>"
        if code in self.fail_codes:
            raise cr.FetchError(f"simulated failure for {code}")
        if offset > 0:
            return _page([])
        doc_id = f"{abs(hash(code)) % 10_000_000}"
        return _page([{"_doc_id": doc_id, "doc_type_label": code,
                       "recorded_date": "5/20/2026",
                       "document_number": f"2026{doc_id}"}])


def _count_lines(p: Path) -> int:
    return sum(1 for _ in p.open(encoding="utf-8")) if p.exists() else 0


def test_run_end_to_end() -> int:
    today = date(2026, 5, 28)
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        out = tdp / "clerk_recordings.jsonl"
        runs = tdp / "runs"
        state = tdp / "state.json"
        htmld = tdp / "html"

        fetch = _Fetch()
        meta = cr.run(mode="daily_refresh", output_path=out, runs_dir=runs,
                      state_path=state, html_dir=htmld, today=today,
                      fetch_fn=fetch, sleep_fn=NO_SLEEP)
        ok = True
        ok &= _assert("run1: status success", meta["status"] == "success",
                      meta["status"])
        ok &= _assert("run1: 20 records appended (1 per code)",
                      meta["total_records_appended"] == 20,
                      str(meta["total_records_appended"]))
        ok &= _assert("run1: jsonl has 20 lines", _count_lines(out) == 20)
        ok &= _assert("run1: first_run_fallback true (no state)",
                      meta["first_run_fallback"] is True)
        ok &= _assert("run1: cursor advanced to end_date",
                      state.exists() and json.loads(state.read_text())
                      ["last_successful_recorded_date"] == "2026-05-28")
        ok &= _assert("run1: run-metadata file written",
                      (runs / f"{meta['run_id']}.json").exists())
        ok &= _assert("run1: html audit written",
                      any(htmld.rglob("*.html")))

        # Append-only: a second run adds another 20 lines.
        meta2 = cr.run(mode="daily_refresh", output_path=out, runs_dir=runs,
                       state_path=state, html_dir=htmld, today=today,
                       fetch_fn=_Fetch(), sleep_fn=NO_SLEEP)
        ok &= _assert("run2: append-only -> 40 lines total",
                      _count_lines(out) == 40, str(_count_lines(out)))
        ok &= _assert("run2: not first_run_fallback (state exists)",
                      meta2["first_run_fallback"] is False)
        return 0 if ok else 1


# ---------------------------------------------------------------------
# 12. Conservative cursor rule: a CORE failure freezes the cursor (§8)
# ---------------------------------------------------------------------

def test_core_failure_freezes_cursor() -> int:
    today = date(2026, 5, 28)
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        state = tdp / "state.json"
        # FTL is a CORE code -> any CORE failure must freeze the cursor.
        fetch = _Fetch(fail_codes={"FTL"})
        meta = cr.run(mode="daily_refresh", output_path=tdp / "o.jsonl",
                      runs_dir=tdp / "r", state_path=state,
                      html_dir=tdp / "h", today=today,
                      fetch_fn=fetch, sleep_fn=NO_SLEEP)
        ok = True
        ok &= _assert("core failure -> status partial", meta["status"] == "partial")
        ok &= _assert("core failure -> cursor NOT advanced",
                      meta["new_last_successful_recorded_date"] is None
                      and not state.exists())
        return 0 if ok else 1


# ---------------------------------------------------------------------
# 13. Hard halt mid-run -> halted, cursor frozen (§8)
# ---------------------------------------------------------------------

def test_hard_halt_freezes_cursor() -> int:
    today = date(2026, 5, 28)
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        state = tdp / "state.json"
        fetch = _Fetch(halt_codes={"LIS PEN"})  # CORE code triggers login wall
        meta = cr.run(mode="daily_refresh", output_path=tdp / "o.jsonl",
                      runs_dir=tdp / "r", state_path=state,
                      html_dir=tdp / "h", today=today,
                      fetch_fn=fetch, sleep_fn=NO_SLEEP)
        ok = True
        ok &= _assert("hard halt -> status halted", meta["status"] == "halted")
        ok &= _assert("hard halt -> halt_reason recorded",
                      meta.get("halt_reason") == "login_wall")
        ok &= _assert("hard halt -> cursor frozen", not state.exists())
        return 0 if ok else 1


def main() -> int:
    print("[adapter test] scrapers/clerk_recordings.py")
    rcs = [
        test_parse_real_dom(),
        test_header_driven_reordered_columns(),
        test_empty_page(),
        test_missing_doc_number_confidence(),
        test_malformed_date(),
        test_row_without_checkbox_skipped(),
        test_hard_halt_detection(),
        test_url_construction(),
        test_mode_rejection(),
        test_compute_window(),
        test_run_end_to_end(),
        test_core_failure_freezes_cursor(),
        test_hard_halt_freezes_cursor(),
    ]
    failures = sum(1 for rc in rcs if rc != 0)
    print(f"\nfailures: {failures} of {len(rcs)}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
