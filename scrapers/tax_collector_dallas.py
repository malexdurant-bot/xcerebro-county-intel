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
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

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


def parse_trw_file(zip_path: Path, verbose: bool) -> list[dict]:
    records: list[dict] = []
    scanned = 0
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
                records.append(fields)
                if verbose and len(records) % 200_000 == 0:
                    print(f"  [Dallas TRW] ...{len(records)} delinquent rows so far ({scanned} scanned)", flush=True)
    if verbose:
        print(f"  [Dallas TRW] {len(records)} delinquent (TOT_AMT_DUE > 0) rows out of {scanned} scanned", flush=True)
    return records


def _raw_record_id(fields: dict) -> str:
    account = fields["ACCOUNT"].strip().replace(" ", "_")
    year = fields["YEAR"].strip()
    jurisdiction = fields["JURISDICTION"].strip()
    return f"dallas_tax_{account}_{year}_{jurisdiction}"


def _to_wrapped_records(rows: list[dict], source_url: str, verbose: bool = False) -> list[dict]:
    now = _now_iso()
    out: list[dict] = []
    for i, f in enumerate(rows):
        if verbose and i and i % 200_000 == 0:
            print(f"  [Dallas TRW] wrapped {i}/{len(rows)}", flush=True)
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

        out.append({
            "raw_record_id": _raw_record_id(f),
            "source_id": SOURCE_ID,
            "source_url": source_url,
            "source_fetched_at": now,
            "parser_confidence": 100,
            "raw_payload": raw_payload,
        })
    return out


def _load_prior(path: Path) -> dict:
    if not path.exists():
        return {}
    out: dict = {}
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
                out[rid] = rec
    return out


def merge_with_prior(current: list[dict], prior: dict) -> list[dict]:
    now = _now_iso()
    merged: list[dict] = []
    seen_ids = set()
    for rec in current:
        rid = rec["raw_record_id"]
        seen_ids.add(rid)
        prev = prior.get(rid)
        if prev is None:
            rec["first_seen_at"] = now
            rec["last_seen_at"] = now
            rec["change_status"] = "NEW_RECORD"
        else:
            rec["first_seen_at"] = prev.get("first_seen_at", now)
            rec["last_seen_at"] = now
            rec["change_status"] = (
                "SAME" if prev.get("raw_payload") == rec["raw_payload"] else "UPDATED"
            )
        merged.append(rec)
    for rid, prev in prior.items():
        if rid not in seen_ids:
            prev["change_status"] = "DISAPPEARED"  # paid off / resolved since last week's roll
            merged.append(prev)
    return merged


def _write_jsonl(records: list[dict], output_path: Path, verbose: bool = False) -> dict:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if verbose:
        print(f"  [Dallas TRW] loading prior state from {output_path}", flush=True)
    prior = _load_prior(output_path)
    if verbose:
        print(f"  [Dallas TRW] merging {len(records)} current vs {len(prior)} prior records", flush=True)
    merged = merge_with_prior(records, prior)

    if verbose:
        print(f"  [Dallas TRW] writing {len(merged)} records to {output_path}", flush=True)
    tmp = output_path.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for i, rec in enumerate(merged):
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if verbose and i and i % 200_000 == 0:
                print(f"  [Dallas TRW] wrote {i}/{len(merged)}", flush=True)
    tmp.replace(output_path)

    return {
        "output_path": str(output_path),
        "records_pulled": len(records),
        "prior_count": len(prior),
        "total_after_merge": len(merged),
        "new_record_count": sum(1 for r in merged if r["change_status"] == "NEW_RECORD"),
        "same_record_count": sum(1 for r in merged if r["change_status"] == "SAME"),
        "updated_record_count": sum(1 for r in merged if r["change_status"] == "UPDATED"),
        "disappeared_record_count": sum(1 for r in merged if r["change_status"] == "DISAPPEARED"),
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_scraper(out_dir: Path, verbose: bool = True) -> dict:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    download_url = discover_download_url(session)
    zip_path = download_zip(session, download_url, verbose)
    rows = parse_trw_file(zip_path, verbose)
    records = _to_wrapped_records(rows, download_url, verbose)

    out_path = out_dir / "tax_collector.jsonl"
    stats = _write_jsonl(records, out_path, verbose)

    return {
        "source_page": TAX_ROLL_PAGE_URL,
        "download_url": download_url,
        "zip_cached_at": str(zip_path),
        "delinquent_rows_parsed": len(rows),
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
    args = parser.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else REPO_ROOT / "data" / "raw"
    stats = run_scraper(out_dir)
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
