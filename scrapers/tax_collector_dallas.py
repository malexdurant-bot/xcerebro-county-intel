"""
Dallas County Tax Office — Tax Roll (TRW) weekly bulk delinquent-tax file.

Source discovery note (2026-08-23): Phase 0 recon originally pointed
tax_collector at https://www.dallasact.com/act_webdev/dallas/index.jsp (the
ACT Tax Solutions per-account lookup portal). Live verification found that
portal is owner/address/account/fiduciary SEARCH ONLY — there is no bulk
delinquent-roll listing or export, so it cannot function as a standalone
daily-refresh distress-event scraper (you would need to already know which
account to look up). The Dallas County Tax Office's own site
(dallascounty.org/departments/tax/tax-roll.php) instead publishes a genuine
bulk file: the "Tax Roll (TRW)", an ASCII fixed-width file covering current
AND delinquent accounts for every taxing jurisdiction in the county,
regenerated every Friday and posted the following Monday. This adapter
targets that file instead. The ACT portal remains useful as a manual
per-property lookup tool but is not scraped here.

Download flow:
  1. GET the static tax-roll.php page and regex out the current week's
     `trwfile.<id>.zip` download link — the numeric id changes every
     publish, there is no stable/predictable URL.
  2. Stream-download the zip (~265MB observed 2026-08-23) to a local cache
     directory, keyed by the discovered filename so an unchanged weekly file
     is not re-downloaded.
  3. Extract the one fixed-width data member from inside the zip — it lives
     nested under `usr2/spool/act/flat404.DALLASCOUNTY.<date>.<id>` and is
     NOT the only file in the archive: `tcs404p.*` next to it is a
     human-readable jurisdiction/year summary REPORT, not row-level data —
     do not parse that one.
  4. Parse each 534-byte fixed-width line per the field layout documented in
     `3_Tax_Roll_TRW_File_Layout_v3.pdf` (also bundled inside the zip itself
     as `trwfile_layout.txt.<id>`) — see TRW_LAYOUT below. Verified against
     the county's own bundled sample file (trwfile.441510_SampleFile.zip)
     2026-08-23: field boundaries and decoded values (levy amounts, due
     dates, owner/address, aging buckets) all decode sensibly.
  5. This is a plain HTTP request/download — no Playwright, no browser
     automation. dallascounty.org serves this as a static file, not an SPA.

This is a full weekly SNAPSHOT of the entire tax roll, not an event log — the
file contains every account (paid and unpaid) for every jurisdiction/year.
Only rows with TOT_AMT_DUE > 0 (still owing as of end of month) are emitted
as raw records; a $0-due row is a paid/current account and is enrichment
noise, not a distress event, per this framework's product rule that a full
tax roll is never a lead by itself. Emitted rows additionally carry the
SUIT (pending lawsuit), BANKCODE (bankruptcy on file), ATTORNEY (33.07
attorney-fee date set — a real escalation threshold under the Texas Tax
Code), and TOT_AMT_DUE_30/60/90 aging fields as raw_payload signals for
downstream scoring — a delinquency with SUIT or BANKCODE set is a much
stronger distress signal than a small newly-overdue balance.

Because this is a full snapshot rather than an event stream, freshness is
tracked per (account, year, jurisdiction) via merge_with_prior — a row that
disappears from one week's file to the next has been paid off or resolved
(change_status DISAPPEARED), not literally deleted data.

Requires: pip install requests (already a framework dependency elsewhere)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]

SOURCE_ID = "tax_collector"
TAX_ROLL_PAGE_URL = "https://www.dallascounty.org/departments/tax/tax-roll.php"
CACHE_DIR = REPO_ROOT / "data" / "cache" / "dallas_tax_collector"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# (field_name, start_position_1indexed, length) — from
# 3_Tax_Roll_TRW_File_Layout_v3.pdf, verified against the county's own
# bundled sample file 2026-08-23.
TRW_LAYOUT: list[tuple[str, int, int]] = [
    ("ACCOUNT", 1, 34), ("YEAR", 35, 4), ("JURISDICTION", 39, 4),
    ("TAX_UNIT_ACCT", 43, 34), ("LEVY", 77, 11), ("HOMESTEAD", 88, 1),
    ("OVER65", 89, 1), ("VETERAN", 90, 1), ("DISABLED", 91, 1), ("AG", 92, 1),
    ("DATE_PAID", 93, 8), ("DUE_DATE", 101, 8), ("OMIT_FLAG", 109, 2),
    ("LEVY_BALANCE", 111, 11), ("SUIT", 122, 1), ("CAUSENO", 123, 40),
    ("BANKCODE", 163, 1), ("BANKRUPTNO", 164, 40), ("ATTORNEY", 204, 1),
    ("COURT_COST", 205, 7), ("ABSTRACT_FEE", 212, 7), ("DEFERRAL", 219, 1),
    ("BILLSUPP", 220, 1), ("SPLIT_PMTFLAG", 221, 1), ("CATEGORY_CODE", 222, 4),
    ("OWNER", 226, 40), ("ADDRESS2", 266, 40), ("ADDRESS3", 306, 40),
    ("ADDRESS4", 346, 40), ("CITY", 386, 40), ("STATE", 426, 2),
    ("ZIP", 428, 12), ("ROLL_CODE", 440, 1), ("PARCEL_NO", 441, 8),
    ("PARCEL_NAME", 449, 40), ("PAYMENT_AGREEMENT", 489, 1),
    ("TOT_AMT_DUE", 490, 11), ("TOT_AMT_DUE_30", 501, 11),
    ("TOT_AMT_DUE_60", 512, 11), ("TOT_AMT_DUE_90", 523, 11),
    ("AMOUNT_INDICATOR", 534, 1),
]
_EXPECTED_LINE_LEN = 534


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _cents_to_dollars(raw: str) -> float | None:
    raw = raw.strip()
    if not raw:
        return None
    neg = raw.startswith("-")
    digits = raw[1:] if neg else raw
    if not digits.isdigit():
        return None
    value = int(digits) / 100.0
    return -value if neg else value


def _parse_yyyymmdd(raw: str) -> str | None:
    raw = raw.strip()
    if len(raw) != 8 or not raw.isdigit():
        return None
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"


def discover_download_url(session: requests.Session) -> str:
    resp = session.get(TAX_ROLL_PAGE_URL, timeout=30)
    resp.raise_for_status()
    m = re.search(r'href="(https://www\.dallascounty\.org/Assets/uploads/docs/tax/trw/trwfile\.\d+\.zip)"', resp.text)
    if not m:
        raise RuntimeError(
            "Dallas TRW: could not find a trwfile.<id>.zip download link on "
            f"{TAX_ROLL_PAGE_URL} — page structure may have changed"
        )
    return m.group(1)


def download_zip(session: requests.Session, url: str, verbose: bool) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    filename = url.rsplit("/", 1)[-1]
    dest = CACHE_DIR / filename
    if dest.exists():
        if verbose:
            print(f"  [Dallas TRW] cached copy already present: {dest}", flush=True)
        return dest

    if verbose:
        print(f"  [Dallas TRW] downloading {url}", flush=True)
    tmp = dest.with_suffix(".zip.part")
    with session.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        written = 0
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                fh.write(chunk)
                written += len(chunk)
                if verbose and total:
                    print(f"  [Dallas TRW] {written / 1e6:.0f}MB / {total / 1e6:.0f}MB", flush=True)
    tmp.replace(dest)
    return dest


def _find_flat_member(zf: zipfile.ZipFile) -> str:
    for name in zf.namelist():
        base = name.rsplit("/", 1)[-1]
        if base.startswith("flat404."):
            return name
    raise RuntimeError("Dallas TRW: no flat404.* data member found inside the zip")


_TOT_AMT_DUE_START, _TOT_AMT_DUE_LEN = 490, 11


def _iter_trw_rows(zip_path: Path, verbose: bool) -> Iterator[dict]:
    """Yield one delinquent (TOT_AMT_DUE > 0) row's raw fields at a time.

    2026-10-09: was a list-returning parse_trw_file(). Holding all ~1.4M
    rows (40 string fields each) plus a second wrapped copy plus the prior
    state needed several GB at once and MemoryError'd the daily run on a
    7.7 GB machine whenever anything else was open (failing since Sep 29).
    Streaming keeps the whole scrape to roughly one row in flight."""
    kept = 0
    scanned = 0
    not_yet_due = 0
    today_iso = datetime.now(timezone.utc).date().isoformat()
    with zipfile.ZipFile(zip_path) as zf:
        member = _find_flat_member(zf)
        if verbose:
            print(f"  [Dallas TRW] parsing member {member}", flush=True)
        with zf.open(member) as fh:
            for raw_line in fh:
                scanned += 1
                if len(raw_line) < _EXPECTED_LINE_LEN:
                    continue
                # Cheap pre-filter on the raw bytes before decoding/slicing
                # the other 40 fields — most rows are $0 due (paid/current)
                # and can be skipped without building a full dict.
                tot_amt_raw = raw_line[_TOT_AMT_DUE_START - 1:_TOT_AMT_DUE_START - 1 + _TOT_AMT_DUE_LEN]
                if tot_amt_raw == b"00000000000":
                    continue
                line = raw_line.decode("latin-1").rstrip("\r\n")
                fields = {
                    name: line[start - 1:start - 1 + length]
                    for name, start, length in TRW_LAYOUT
                }
                if not _cents_to_dollars(fields["TOT_AMT_DUE"]):
                    continue  # $0 due = paid/current, not a distress event
                # An amount due is NOT the same as delinquent. Each October
                # the county issues the new year's bills -- due the following
                # Jan 31, so not yet delinquent -- and the weekly roll then
                # lists them with TOT_AMT_DUE > 0. The 2026-10-09 file went
                # from ~1.36M rows with an amount due to ~4.49M, of which
                # ~3.13M (3,134,230 of them tax year 2026) were due AFTER
                # today. Left in, they would have quadrupled the data AND
                # flipped the lead gating's "current due-date cycle" to the
                # not-yet-delinquent one (the suit flag repeats across an
                # account's rows, so ~31k of them look suit-pending). Only a
                # row already past its due date is a delinquency.
                due_iso = _parse_yyyymmdd(fields["DUE_DATE"])
                if due_iso and due_iso > today_iso:
                    not_yet_due += 1
                    continue
                kept += 1
                if verbose and kept % 200_000 == 0:
                    print(f"  [Dallas TRW] ...{kept} delinquent rows so far ({scanned} scanned)", flush=True)
                yield fields
    if verbose:
        print(f"  [Dallas TRW] {kept} delinquent (past due, TOT_AMT_DUE > 0) rows out of "
              f"{scanned} scanned ({not_yet_due} skipped: billed but not yet due)", flush=True)


def _raw_record_id(fields: dict) -> str:
    account = fields["ACCOUNT"].strip().replace(" ", "_")
    year = fields["YEAR"].strip()
    jurisdiction = fields["JURISDICTION"].strip()
    return f"dallas_tax_{account}_{year}_{jurisdiction}"


def _iter_wrapped_records(rows: Iterable[dict], source_url: str) -> Iterator[dict]:
    now = _now_iso()
    for f in rows:
        owner_lines = [f["OWNER"].strip(), f["ADDRESS2"].strip(), f["ADDRESS3"].strip(), f["ADDRESS4"].strip()]
        owner_lines = [l for l in owner_lines if l]

        raw_payload = {
            "address": owner_lines[1] if len(owner_lines) > 1 else None,
            "owner_name": owner_lines[0] if owner_lines else None,
            "city": f["CITY"].strip() or None,
            "state": f["STATE"].strip() or None,
            "zip": f["ZIP"].strip() or None,
            "account": f["ACCOUNT"].strip(),
            "tax_year": f["YEAR"].strip(),
            "jurisdiction_code": f["JURISDICTION"].strip(),
            "parcel_no": f["PARCEL_NO"].strip() or None,
            "parcel_name": f["PARCEL_NAME"].strip() or None,
            "due_date": _parse_yyyymmdd(f["DUE_DATE"]),
            "date_paid": _parse_yyyymmdd(f["DATE_PAID"]),
            "levy": _cents_to_dollars(f["LEVY"]),
            "levy_balance": _cents_to_dollars(f["LEVY_BALANCE"]),
            "tot_amt_due": _cents_to_dollars(f["TOT_AMT_DUE"]),
            "tot_amt_due_30": _cents_to_dollars(f["TOT_AMT_DUE_30"]),
            "tot_amt_due_60": _cents_to_dollars(f["TOT_AMT_DUE_60"]),
            "tot_amt_due_90": _cents_to_dollars(f["TOT_AMT_DUE_90"]),
            "suit_pending": f["SUIT"].strip() or None,
            "causeno": f["CAUSENO"].strip() or None,
            "bankruptcy_code": f["BANKCODE"].strip() or None,
            "bankruptcy_no": f["BANKRUPTNO"].strip() or None,
            "attorney_fee_set": f["ATTORNEY"].strip() == "Y",
            "payment_agreement": f["PAYMENT_AGREEMENT"].strip() == "Y",
            "deferral": f["DEFERRAL"].strip() == "D",
            "homestead_exemption": f["HOMESTEAD"].strip() == "Y",
        }

        yield {
            "raw_record_id": _raw_record_id(f),
            "source_id": SOURCE_ID,
            "source_url": source_url,
            "source_fetched_at": now,
            "parser_confidence": 100,
            "raw_payload": raw_payload,
        }


def _payload_digest(payload: dict) -> bytes:
    """Compact fingerprint of a raw_payload, so "did this row change since
    last run" can be answered from a small index instead of keeping every
    prior record in memory. Canonical JSON (sorted keys) so key order never
    matters; 12 bytes of blake2b makes an accidental collision (a changed
    row wrongly reported SAME) astronomically unlikely."""
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.blake2b(text.encode("utf-8"), digest_size=12).digest()


def _load_prior_index(path: Path, verbose: bool = False) -> dict:
    """{raw_record_id: (first_seen_at, payload_digest)} from the prior output.

    Holds only what the merge needs, NOT the records themselves. The old
    version kept every prior record (~1.4M full dicts) alongside the parsed
    rows and the wrapped records -- three full copies -- and MemoryError'd
    the daily run on a 7.7 GB machine from Sep 29 onward.

    2026-09-25: an earlier crash here was 'OSError: [Errno 22] Invalid
    argument' mid-read on this 1.4GB file (under a OneDrive-synced repo),
    which read fine moments later -- consistent with a transient OneDrive
    Files-On-Demand hiccup. So: retry with a short backoff, and if it still
    fails fall back to {} (same as a missing file: every row looks NEW)
    rather than crashing the whole daily pipeline."""
    import time

    if not path.exists():
        return {}
    last_exc: OSError | None = None
    for attempt in range(3):
        if attempt:
            if verbose:
                print(f"  [Dallas TRW] retrying prior-state read after {last_exc} "
                      f"(attempt {attempt + 1}/3)...", flush=True)
            time.sleep(5 * attempt)
        index: dict = {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    rid = rec.get("raw_record_id")
                    if rid:
                        first_seen = rec.get("first_seen_at")
                        index[rid] = (
                            sys.intern(first_seen) if isinstance(first_seen, str) else None,
                            _payload_digest(rec.get("raw_payload")),
                        )
            return index
        except OSError as exc:
            last_exc = exc
    if verbose:
        print(f"  [Dallas TRW] prior-state read failed 3x ({last_exc}) -- "
              f"continuing with no prior state (every row will look NEW_RECORD "
              f"this run rather than crashing the pipeline)", flush=True)
    return {}


def _stream_merge_write(records: Iterable[dict], output_path: Path, verbose: bool = False) -> dict:
    """Merge the current weekly roll against the prior output and write the
    result, streaming: each current record is classified and written as it
    arrives, and prior rows that vanished from the roll (paid off / resolved
    since last week) are then copied across from the old file with
    change_status DISAPPEARED. Output order and field semantics match the
    old list-based merge_with_prior(): current rows first (in roll order),
    then disappeared rows (in prior-file order)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if verbose:
        print(f"  [Dallas TRW] loading prior state from {output_path}", flush=True)
    prior = _load_prior_index(output_path, verbose=verbose)
    if verbose:
        print(f"  [Dallas TRW] {len(prior)} prior records indexed; streaming current "
              f"rows through the merge", flush=True)

    now = _now_iso()
    counts = {"NEW_RECORD": 0, "SAME": 0, "UPDATED": 0, "DISAPPEARED": 0}
    seen: set = set()
    pulled = 0
    tmp = output_path.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as out:
        for rec in records:
            pulled += 1
            rid = rec["raw_record_id"]
            seen.add(rid)
            prev = prior.get(rid)
            if prev is None:
                rec["first_seen_at"] = now
                rec["last_seen_at"] = now
                rec["change_status"] = "NEW_RECORD"
            else:
                first_seen, digest = prev
                rec["first_seen_at"] = first_seen or now
                rec["last_seen_at"] = now
                rec["change_status"] = (
                    "SAME" if digest == _payload_digest(rec["raw_payload"]) else "UPDATED"
                )
            counts[rec["change_status"]] += 1
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if verbose and pulled % 200_000 == 0:
                print(f"  [Dallas TRW] merged/wrote {pulled} records", flush=True)

        n_missing = sum(1 for rid in prior if rid not in seen)
        if n_missing:
            if verbose:
                print(f"  [Dallas TRW] carrying {n_missing} prior records no longer on "
                      f"the roll across as DISAPPEARED", flush=True)
            written_missing: set = set()
            try:
                with open(output_path, "r", encoding="utf-8") as old:
                    for line in old:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        rid = rec.get("raw_record_id")
                        if (not rid or rid in seen or rid not in prior
                                or rid in written_missing):
                            continue
                        written_missing.add(rid)
                        rec["change_status"] = "DISAPPEARED"
                        counts["DISAPPEARED"] += 1
                        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except OSError as exc:
                # Disappeared rows are history only (they were paid off); losing
                # some on a transient read error must not fail the whole run.
                if verbose:
                    print(f"  [Dallas TRW] could not finish copying DISAPPEARED rows "
                          f"({exc}); {counts['DISAPPEARED']}/{n_missing} carried", flush=True)
    tmp.replace(output_path)

    return {
        "output_path": str(output_path),
        "records_pulled": pulled,
        "prior_count": len(prior),
        "total_after_merge": pulled + counts["DISAPPEARED"],
        "new_record_count": counts["NEW_RECORD"],
        "same_record_count": counts["SAME"],
        "updated_record_count": counts["UPDATED"],
        "disappeared_record_count": counts["DISAPPEARED"],
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


_LAST_PARSED_MARKER = "tax_collector.last_parsed.json"


def _read_marker(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def run_scraper(out_dir: Path, verbose: bool = True, force: bool = False) -> dict:
    """The county republishes the roll weekly, but the pipeline runs daily.
    Re-parsing and re-merging the ~1.4M-row file on the six days it hasn't
    changed does nothing except burn ~10 minutes and risk the memory crash
    that failed the daily run from Sep 29 -- so when the discovered weekly
    file is the one already merged into tax_collector.jsonl (recorded in a
    marker written only AFTER a successful merge), skip it. force=True
    re-parses regardless."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    download_url = discover_download_url(session)
    out_path = out_dir / "tax_collector.jsonl"
    marker_path = out_dir / _LAST_PARSED_MARKER

    if not force:
        last = _read_marker(marker_path)
        if (last and last.get("download_url") == download_url
                and out_path.exists() and out_path.stat().st_size > 0):
            if verbose:
                print(f"  [Dallas TRW] weekly file unchanged since last merge "
                      f"({download_url.rsplit('/', 1)[-1]}, merged {last.get('completed_at')}) "
                      f"-- skipping re-parse/merge (use force to override)", flush=True)
            return {
                "source_page": TAX_ROLL_PAGE_URL,
                "download_url": download_url,
                "skipped_unchanged": True,
                "last_merged_at": last.get("completed_at"),
                "tax_collector": last.get("stats"),
            }

    zip_path = download_zip(session, download_url, verbose)
    rows = _iter_trw_rows(zip_path, verbose)
    records = _iter_wrapped_records(rows, download_url)
    stats = _stream_merge_write(records, out_path, verbose)

    marker_path.write_text(json.dumps({
        "download_url": download_url,
        "completed_at": _now_iso(),
        "stats": stats,
    }, indent=2) + "\n", encoding="utf-8")

    return {
        "source_page": TAX_ROLL_PAGE_URL,
        "download_url": download_url,
        "zip_cached_at": str(zip_path),
        "delinquent_rows_parsed": stats["records_pulled"],
        "tax_collector": stats,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Dallas County Tax Office weekly bulk Tax Roll (TRW) parser. "
            "Downloads the current week's ASCII fixed-width file and emits "
            "one raw record per delinquent (TOT_AMT_DUE > 0) account/year/"
            "jurisdiction row, with suit/bankruptcy/attorney escalation "
            "flags and 30/60/90-day aging preserved for scoring."
        )
    )
    parser.add_argument("--out-dir", default=None,
                         help="Output directory for tax_collector.jsonl. Default: data/raw/")
    parser.add_argument("--force", action="store_true",
                         help="Re-parse and re-merge even if this week's file was "
                              "already merged into tax_collector.jsonl.")
    args = parser.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else REPO_ROOT / "data" / "raw"
    stats = run_scraper(out_dir, force=args.force)
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
