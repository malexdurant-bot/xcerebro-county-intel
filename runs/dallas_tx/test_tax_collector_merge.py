#!/usr/bin/env python3
"""Equivalence + behavior tests for scrapers/tax_collector_dallas.py's
streaming weekly merge (2026-10-09 rewrite).

The old merge held three full copies of the ~1.4M-row tax roll in memory and
MemoryError'd the daily run from Sep 29. The rewrite streams. The contract is
that the OUTPUT is unchanged, so the original list-based algorithm is kept
below as an oracle and the two are compared on a synthetic roll covering
every status (NEW / SAME / UPDATED / DISAPPEARED, an already-DISAPPEARED row
carried forward, and duplicate row IDs). All data here is synthetic.

Also covers the skip-when-the-weekly-file-is-unchanged marker.

Run: python3 runs/dallas_tx/test_tax_collector_merge.py
Exit 0 = pass, non-zero = fail.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scrapers.tax_collector_dallas as tcd

NOW = "2026-10-09T00:00:00+00:00"
tcd._now_iso = lambda: NOW  # deterministic timestamps for both paths
REAL_ITER_TRW_ROWS = tcd._iter_trw_rows  # the skip-marker tests below stub the module attribute

FAILS = []


def case(name, ok):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILS.append(name)


def rec(rid, **payload_overrides):
    payload = {
        "account": rid, "tax_year": "2025", "jurisdiction_code": "TEST",
        "owner_name": "TEST_OWNER", "tot_amt_due": 100.5, "suit_pending": None,
        "causeno": None, "attorney_fee_set": False, "date_paid": None,
    }
    payload.update(payload_overrides)
    return {
        "raw_record_id": rid, "source_id": "tax_collector", "source_url": "u",
        "source_fetched_at": "T-OLD", "parser_confidence": 100, "raw_payload": payload,
    }


def oracle_merge(current, prior):
    """VERBATIM the original merge_with_prior (pre-2026-10-09)."""
    now = tcd._now_iso()
    merged, seen_ids = [], set()
    for r in current:
        rid = r["raw_record_id"]
        seen_ids.add(rid)
        prev = prior.get(rid)
        if prev is None:
            r["first_seen_at"] = now
            r["last_seen_at"] = now
            r["change_status"] = "NEW_RECORD"
        else:
            r["first_seen_at"] = prev.get("first_seen_at", now)
            r["last_seen_at"] = now
            r["change_status"] = (
                "SAME" if prev.get("raw_payload") == r["raw_payload"] else "UPDATED"
            )
        merged.append(r)
    for rid, prev in prior.items():
        if rid not in seen_ids:
            prev["change_status"] = "DISAPPEARED"
            merged.append(prev)
    return merged


def prior_records():
    same = rec("A_SAME");          same.update(first_seen_at="T1", last_seen_at="T2", change_status="SAME")
    chg = rec("B_CHANGED");        chg.update(first_seen_at="T1", last_seen_at="T2", change_status="SAME")
    gone = rec("C_GONE");          gone.update(first_seen_at="T0", last_seen_at="T2", change_status="SAME")
    old_gone = rec("D_ALREADY_GONE"); old_gone.update(first_seen_at="T0", last_seen_at="T1", change_status="DISAPPEARED")
    dup1 = rec("E_DUP");           dup1.update(first_seen_at="T1", last_seen_at="T2", change_status="SAME")
    nofs = rec("F_NO_FIRST_SEEN"); nofs.update(last_seen_at="T2", change_status="SAME")  # legacy row, no first_seen_at
    return [same, chg, gone, old_gone, dup1, nofs]


def current_records():
    return [
        rec("A_SAME"),                                   # unchanged
        rec("B_CHANGED", tot_amt_due=999.0),             # amount changed -> UPDATED
        rec("G_NEW"),                                    # new
        rec("E_DUP"), rec("E_DUP"),                      # duplicate ids in the current roll
        rec("F_NO_FIRST_SEEN"),                          # prior had no first_seen_at
    ]


def write_jsonl(path, records):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")


def read_jsonl(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


print("streaming merge == original list-based merge")
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    out = td / "tax_collector.jsonl"
    write_jsonl(out, prior_records())

    prior_by_id = {r["raw_record_id"]: r for r in prior_records()}  # original _load_prior: last wins
    expected = oracle_merge(current_records(), prior_by_id)

    stats = tcd._stream_merge_write(iter(current_records()), out, verbose=False)
    got = read_jsonl(out)

    case("same number of output rows", len(got) == len(expected))
    case("identical records in identical order", got == expected)
    by_status = {}
    for r in got:
        by_status[r["change_status"]] = by_status.get(r["change_status"], 0) + 1
    case("every status exercised (NEW, SAME, UPDATED, DISAPPEARED)",
         set(by_status) == {"NEW_RECORD", "SAME", "UPDATED", "DISAPPEARED"})
    case("stats counters match the written file",
         stats["new_record_count"] == by_status["NEW_RECORD"]
         and stats["same_record_count"] == by_status["SAME"]
         and stats["updated_record_count"] == by_status["UPDATED"]
         and stats["disappeared_record_count"] == by_status["DISAPPEARED"]
         and stats["total_after_merge"] == len(got)
         and stats["records_pulled"] == len(current_records()))
    case("first_seen_at survives for a continuing row",
         next(r for r in got if r["raw_record_id"] == "A_SAME")["first_seen_at"] == "T1")
    case("missing first_seen_at falls back to now (as before)",
         next(r for r in got if r["raw_record_id"] == "F_NO_FIRST_SEEN")["first_seen_at"] == NOW)
    case("a vanished row is carried as DISAPPEARED with its history intact",
         next(r for r in got if r["raw_record_id"] == "C_GONE")["first_seen_at"] == "T0")
    case("no .tmp file left behind", not (td / "tax_collector.jsonl.tmp").exists())

    # idempotent second run: nothing changed -> everything SAME, nothing new
    stats2 = tcd._stream_merge_write(iter(current_records()), out, verbose=False)
    case("re-merging the same roll is stable (no NEW, no UPDATED)",
         stats2["new_record_count"] == 0 and stats2["updated_record_count"] == 0)

    # first ever run: no prior file at all
    fresh = td / "fresh.jsonl"
    stats3 = tcd._stream_merge_write(iter(current_records()), fresh, verbose=False)
    case("no prior file -> every row NEW_RECORD",
         stats3["new_record_count"] == len(current_records()) and stats3["prior_count"] == 0)

print("payload fingerprint")
a = {"x": 1.5, "y": None, "z": True, "s": "t"}
b = {"s": "t", "z": True, "y": None, "x": 1.5}
case("key order does not change the digest", tcd._payload_digest(a) == tcd._payload_digest(b))
case("a changed value changes the digest",
     tcd._payload_digest(a) != tcd._payload_digest({**a, "x": 1.6}))
case("float survives a JSON round trip unchanged",
     tcd._payload_digest({"v": 1234.5}) == tcd._payload_digest(json.loads(json.dumps({"v": 1234.5}))))

print("skip when the weekly file is unchanged")
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    calls = {"download": 0}
    tcd.discover_download_url = lambda session: "https://example.test/trwfile.111.zip"
    def fake_download(session, url, verbose):
        calls["download"] += 1
        return Path("ignored.zip")
    tcd.download_zip = fake_download
    tcd._iter_trw_rows = lambda zip_path, verbose: iter([{}])  # one placeholder row
    tcd._iter_wrapped_records = lambda rows, url: iter(current_records())

    r1 = tcd.run_scraper(td, verbose=False)
    case("first run merges and downloads", calls["download"] == 1 and not r1.get("skipped_unchanged"))
    case("marker written after a successful merge", (td / tcd._LAST_PARSED_MARKER).exists())

    r2 = tcd.run_scraper(td, verbose=False)
    case("same weekly file -> skipped, no download", calls["download"] == 1 and r2.get("skipped_unchanged") is True)
    case("skip result still reports the last merge's stats", r2["tax_collector"] == r1["tax_collector"])

    r3 = tcd.run_scraper(td, verbose=False, force=True)
    case("force=True re-merges even if unchanged", calls["download"] == 2 and not r3.get("skipped_unchanged"))

    tcd.discover_download_url = lambda session: "https://example.test/trwfile.222.zip"
    r4 = tcd.run_scraper(td, verbose=False)
    case("a new weekly file id is merged", calls["download"] == 3 and not r4.get("skipped_unchanged"))

    (td / "tax_collector.jsonl").write_text("", encoding="utf-8")
    r5 = tcd.run_scraper(td, verbose=False)
    case("empty output file is never treated as 'already merged'",
         calls["download"] == 4 and not r5.get("skipped_unchanged"))

print("parser keeps only rows already past due (billed-but-not-yet-due are not delinquent)")
import zipfile
from datetime import date, timedelta


def trw_line(account, year, due_yyyymmdd, amt_cents):
    buf = [" "] * tcd._EXPECTED_LINE_LEN
    for name, value in (("ACCOUNT", account), ("YEAR", year), ("JURISDICTION", "TEST"),
                        ("DUE_DATE", due_yyyymmdd), ("TOT_AMT_DUE", f"{amt_cents:011d}")):
        start, length = next((s, l) for nm, s, l in tcd.TRW_LAYOUT if nm == name)
        buf[start - 1:start - 1 + length] = list(str(value).ljust(length)[:length])
    return "".join(buf) + "\n"


past = (date.today() - timedelta(days=400)).strftime("%Y%m%d")
future = (date.today() + timedelta(days=100)).strftime("%Y%m%d")
with tempfile.TemporaryDirectory() as td:
    zp = Path(td) / "trwfile.test.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("usr2/spool/act/flat404.TEST.1", "".join([
            trw_line("TEST_PAST_DUE", "2024", past, 12345),        # delinquent
            trw_line("TEST_FUTURE", "2026", future, 12345),        # billed, not yet due
            trw_line("TEST_ZERO", "2024", past, 0),                # paid
            trw_line("TEST_NO_DUE_DATE", "2024", "        ", 500),  # unparseable date -> kept (conservative)
        ]))
    kept = [r["ACCOUNT"].strip() for r in REAL_ITER_TRW_ROWS(zp, verbose=False)]
    case("past-due row with an amount due is kept", "TEST_PAST_DUE" in kept)
    case("future-due (just billed) row is skipped", "TEST_FUTURE" not in kept)
    case("zero-balance row is skipped", "TEST_ZERO" not in kept)
    case("row with no parseable due date is kept rather than silently dropped",
         "TEST_NO_DUE_DATE" in kept)

print("translator ignores paid-off (DISAPPEARED) and not-yet-due rows")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import translate as tr


def tax_row(status, due="2026-02-01", account="TEST_ACCT_1"):
    r = rec(account, suit_pending="Y", causeno="TEST-CAUSE-1", due_date=due)
    r["raw_record_id"] = f"dallas_tax_{account}_{due}"
    if status is not None:
        r["change_status"] = status
    return r


case("a live suit-pending row becomes a lead event",
     tr._translate_tax_collector_row(tax_row("SAME")) is not None)
case("an UPDATED row still becomes a lead event",
     tr._translate_tax_collector_row(tax_row("UPDATED")) is not None)
case("a row with no change_status (older raw files) still becomes a lead event",
     tr._translate_tax_collector_row(tax_row(None)) is not None)
case("a DISAPPEARED (paid-off) row is NOT a lead",
     tr._translate_tax_collector_row(tax_row("DISAPPEARED")) is None)
future_iso = (date.today() + timedelta(days=100)).isoformat()
case("a suit-flagged row that is billed but not yet due is NOT a lead",
     tr._translate_tax_collector_row(tax_row("SAME", due=future_iso)) is None)
agg3 = {}
tr._accumulate_delinquency_aggregate(tax_row("SAME", due=future_iso, account="TEST_ACCT_3"), agg3)
case("a not-yet-due row does not count toward years delinquent", "TEST_ACCT_3" not in agg3)

agg = {}
tr._accumulate_delinquency_aggregate(tax_row("DISAPPEARED", due="2018-02-01"), agg)
tr._accumulate_delinquency_aggregate(tax_row("SAME", due="2024-02-01"), agg)
case("a paid-off older year does not set the 'oldest unpaid' date",
     agg["TEST_ACCT_1"]["earliest_due_date"] == "2024-02-01")
agg2 = {}
tr._accumulate_delinquency_aggregate(tax_row("SAME", due="2018-02-01"), agg2)
tr._accumulate_delinquency_aggregate(tax_row("SAME", due="2024-02-01"), agg2)
case("an unpaid older year still does",
     agg2["TEST_ACCT_1"]["earliest_due_date"] == "2018-02-01")

if FAILS:
    print(f"\n{len(FAILS)} FAILED")
    sys.exit(1)
print("\nall passed")
