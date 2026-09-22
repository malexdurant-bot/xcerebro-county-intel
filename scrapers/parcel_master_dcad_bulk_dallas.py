"""
Dallas Central Appraisal District (DCAD) -- bulk "Data Products" ownership
file. Replaces the per-account live-lookup path (parcel_master_dcad_dallas.py)
as the PRIMARY parcel_master source for Dallas.

Source discovery (2026-09-15, live-verified): the Dallas client asked for
absentee-owner and out-of-state-owner detection, which needs a mailing
address distinct from the situs address for every property. DCAD's per-
account detail pages (AcctDetail*.aspx) are disallowed by robots.txt
(confirmed live: `Disallow:/Acct` in https://www.dallascad.org/robots.txt) --
parcel_master_dcad_dallas.py already respects that and never fetches them,
which is exactly why it has never been able to expose a mailing address.

DCAD separately publishes a public bulk download under Data Products
(dallascad.org/DataProducts.aspx) -- a DIFFERENT, NOT-disallowed path.
Confirmed live 2026-09-15:
  - "20XX Data Files (Most Current Ownership)" is a plain ~170MB zip, no
    login, no auth (`curl -I` returned 200 with Content-Length).
  - Inside: ACCOUNT_INFO.CSV (863k data rows, confirmed via `wc -l`) with a
    header row: ACCOUNT_NUM, APPRAISAL_YR, DIVISION_CD, BIZ_NAME,
    OWNER_NAME1, OWNER_NAME2, EXCLUDE_OWNER, OWNER_ADDRESS_LINE1..4,
    OWNER_CITY, OWNER_STATE, OWNER_ZIPCODE, OWNER_COUNTRY, STREET_NUM,
    STREET_HALF_NUM, FULL_STREET_NAME, BLDG_ID, UNIT_ID, PROPERTY_CITY,
    PROPERTY_ZIPCODE, MAPSCO, NBHD_CD, LEGAL1..5, DEED_TXFR_DATE,
    GIS_PARCEL_ID, PHONE_NUM, LMA, IMA.
  - ACCOUNT_NUM matches the same account-number identifier space already
    used elsewhere in this county's pipeline (tax_collector_dallas.py's
    ACCOUNT field, the LGBS tax-sales API's account_nbr field, and
    parcel_master_dcad_dallas.py's own SearchAcct.aspx lookups) -- spot-
    checked live: several sample rows show ACCOUNT_NUM == GIS_PARCEL_ID,
    confirming they share the same numbering scheme for ordinary (non-
    mobile-home) accounts. GIS_PARCEL_ID is NOT used as parcel_id here
    because it diverges from ACCOUNT_NUM specifically on manufactured/
    mobile-home accounts (confirmed live: ACCOUNT_NUM
    "750000G129616TN00" vs GIS_PARCEL_ID "941600000A0010000" on the same
    row) -- ACCOUNT_NUM is the one that actually matches what the rest of
    the pipeline already keys on.
  - OWNER_ADDRESS_LINE1 is NOT reliably the street line -- confirmed live
    against real sample rows: LINE1 is frequently a name-continuation /
    "ET AL" / unit-note line (e.g. "SPC 225", "ET AL", a co-owner's name),
    with the actual "<number> <street name>" text one line lower. This
    module picks whichever of LINE1..4 is street-shaped (`^\\d+\\s`) rather
    than assuming a fixed line number.
  - OWNER_STATE is the full state name ("TEXAS"), not a 2-letter code --
    normalized to a USPS abbreviation here so it compares cleanly against
    Dallas's own constant situs state "TX" (see _STATE_NAME_TO_ABBR).

This is a full snapshot (like tax_collector's weekly TRW file), refreshed
periodically by DCAD (observed "Appraisal Data Updated" date on the DCAD
homepage) -- not a per-day event log. Every account is emitted (parcel_master
sources are enrichment, never lead-generating, per this framework's product
rule -- see scaffold/pipeline/translators/parcel_master.py's own docstring).

Requires: pip install requests (already a framework dependency elsewhere)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]

SOURCE_ID = "parcel_master"
DATA_PRODUCTS_PAGE_URL = "https://www.dallascad.org/DataProducts.aspx"
CACHE_DIR = REPO_ROOT / "data" / "cache" / "dallas_dcad_bulk"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Matches the "Most Current Ownership" zip link on DataProducts.aspx -- e.g.
# href="ViewPDFs.aspx?type=3&id=\\DCAD.ORG\WEB\WEBDATA\WEBFORMS\data
# products\DCAD2027_CURRENT.zip" -- confirmed live 2026-09-15. The year in
# the filename changes annually; this regex does not hardcode it.
_CURRENT_OWNERSHIP_LINK_RE = re.compile(
    r'href="(ViewPDFs\.aspx\?type=\d+&id=[^"]*DCAD\d{4}_CURRENT\.zip)"',
    re.IGNORECASE,
)

_STREET_SHAPED_RE = re.compile(r"^\d+[A-Za-z]?\s")

# USPS state-name -> 2-letter abbreviation. DCAD's OWNER_STATE is the full
# name ("TEXAS"), confirmed live -- normalized here so out-of-state
# comparisons against Dallas's constant situs state "TX" are meaningful.
_STATE_NAME_TO_ABBR = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
    "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE",
    "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ",
    "NEW MEXICO": "NM", "NEW YORK": "NY", "NORTH CAROLINA": "NC",
    "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR",
    "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
    "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
    "DISTRICT OF COLUMBIA": "DC",
}


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _normalize_state(raw: str) -> str:
    raw = (raw or "").strip().upper()
    if not raw:
        return ""
    if len(raw) == 2:
        return raw
    return _STATE_NAME_TO_ABBR.get(raw, raw)


def _pick_street_line(lines: list[str]) -> str:
    """Return whichever candidate mailing-address line looks like a real
    street ('<number> <rest>'), skipping name-continuation/unit-note lines.
    Falls back to the first non-empty line if none look street-shaped."""
    candidates = [l.strip() for l in lines if l and l.strip()]
    for c in candidates:
        if _STREET_SHAPED_RE.match(c):
            return c
    return candidates[0] if candidates else ""


def discover_download_url(session: requests.Session, verbose: bool = False) -> str:
    resp = session.get(DATA_PRODUCTS_PAGE_URL, timeout=30)
    resp.raise_for_status()
    m = _CURRENT_OWNERSHIP_LINK_RE.search(resp.text)
    if not m:
        raise RuntimeError(
            "DCAD bulk: could not find a '...DCAD<year>_CURRENT.zip' download "
            f"link on {DATA_PRODUCTS_PAGE_URL} -- page structure may have changed"
        )
    relative = m.group(1).replace("&amp;", "&")
    url = f"https://www.dallascad.org/{relative}"
    if verbose:
        print(f"  [DCAD bulk] discovered download URL: {url}", flush=True)
    return url


def download_zip(session: requests.Session, url: str, verbose: bool) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # The id= query param embeds the real filename (DCAD<year>_CURRENT.zip);
    # use that as the cache key so a re-run this same week reuses the cache.
    m = re.search(r"(DCAD\d{4}_CURRENT\.zip)", url)
    filename = m.group(1) if m else "dcad_bulk_current.zip"
    dest = CACHE_DIR / filename
    if dest.exists():
        if verbose:
            print(f"  [DCAD bulk] cached copy already present: {dest}", flush=True)
        return dest

    if verbose:
        print(f"  [DCAD bulk] downloading {url} (~170MB, may take a few minutes)", flush=True)
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
                    print(f"  [DCAD bulk] {written / 1e6:.0f}MB / {total / 1e6:.0f}MB", flush=True)
    tmp.replace(dest)
    return dest


def _raw_record_id(account_num: str) -> str:
    return f"dallas_dcad_{account_num.strip().replace(' ', '_')}"


def parse_account_info(zip_path: Path, verbose: bool = False) -> list[dict]:
    """Streams ACCOUNT_INFO.CSV out of the zip (352MB / 863k rows -- do NOT
    read it fully into memory) and returns wrapped raw records already
    shaped for scaffold/pipeline/translators/parcel_master.py's contract."""
    now = _now_iso()
    out: list[dict] = []
    scanned = 0
    with zipfile.ZipFile(zip_path) as zf:
        member = "ACCOUNT_INFO.CSV"
        if member not in zf.namelist():
            raise RuntimeError(f"DCAD bulk: {member} not found inside {zip_path}")
        with zf.open(member) as raw_fh:
            text_fh = io.TextIOWrapper(raw_fh, encoding="latin-1", newline="")
            reader = csv.DictReader(text_fh)
            for row in reader:
                scanned += 1
                account = (row.get("ACCOUNT_NUM") or "").strip()
                if not account:
                    continue

                owner_name = (row.get("BIZ_NAME") or "").strip()
                if not owner_name:
                    name1 = (row.get("OWNER_NAME1") or "").strip()
                    name2 = (row.get("OWNER_NAME2") or "").strip()
                    owner_name = " & ".join(n for n in (name1, name2) if n)

                mailing_street = _pick_street_line([
                    row.get("OWNER_ADDRESS_LINE1") or "",
                    row.get("OWNER_ADDRESS_LINE2") or "",
                    row.get("OWNER_ADDRESS_LINE3") or "",
                    row.get("OWNER_ADDRESS_LINE4") or "",
                ])

                street_num = (row.get("STREET_NUM") or "").strip()
                full_street = (row.get("FULL_STREET_NAME") or "").strip()
                situs_address = f"{street_num} {full_street}".strip() if (street_num or full_street) else ""

                raw_payload = {
                    "parcel_id": account,
                    "address": situs_address or None,
                    "owner_name": owner_name or None,
                    "owner_mailing_address": mailing_street or None,
                    "owner_mailing_city": (row.get("OWNER_CITY") or "").strip() or None,
                    "owner_mailing_state": _normalize_state(row.get("OWNER_STATE") or "") or None,
                    "owner_mailing_zip": (row.get("OWNER_ZIPCODE") or "").strip() or None,
                    "situs_state": "TX",
                    "city": (row.get("PROPERTY_CITY") or "").strip() or None,
                    "zip": (row.get("PROPERTY_ZIPCODE") or "").strip() or None,
                    "legal_description": " ".join(
                        (row.get(f"LEGAL{i}") or "").strip() for i in range(1, 6)
                    ).strip() or None,
                }

                out.append({
                    "raw_record_id": _raw_record_id(account),
                    "source_id": SOURCE_ID,
                    "source_url": DATA_PRODUCTS_PAGE_URL,
                    "source_fetched_at": now,
                    "parser_confidence": 100,
                    "raw_payload": raw_payload,
                })

                if verbose and scanned % 200_000 == 0:
                    print(f"  [DCAD bulk] ...{scanned} rows scanned, {len(out)} kept", flush=True)

    if verbose:
        print(f"  [DCAD bulk] {len(out)} parcel records parsed out of {scanned} scanned", flush=True)
    return out


def _write_jsonl(records: list[dict], output_path: Path, verbose: bool = False) -> dict:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for i, rec in enumerate(records):
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if verbose and i and i % 200_000 == 0:
                print(f"  [DCAD bulk] wrote {i}/{len(records)}", flush=True)
    tmp.replace(output_path)
    return {"output_path": str(output_path), "records_written": len(records)}


def build_lookup(records: list[dict]) -> dict:
    """{account_number: {situs_address, situs_city, owner_name,
    assessed_value, property_type, owner_mailing_*, is_absentee_owner,
    is_out_of_state_owner}} -- shaped to be a drop-in superset of the live
    per-account lookup's dict shape (parcel_master_dcad_dallas.py), so
    run_pipeline.py's existing dcad_lookup consumers (translate_taxsales_lgbs,
    the Step 2c address/owner enrichment) keep working unmodified, and gain
    the two new flags for free.

    Also indexes by normalized street (for an address-based fallback lookup)
    and by owner_name (single-match only, mirroring the old live lookup's
    "never guess on an ambiguous name" discipline).
    """
    from scaffold.pipeline.translators.parcel_master import _derive_absentee_flags, _normalize_street

    by_account: dict = {}
    by_street: dict = {}
    by_owner_name: dict = {}

    for rec in records:
        p = rec["raw_payload"]
        account = p["parcel_id"]
        flags = _derive_absentee_flags(p)
        entry = {
            "situs_address": p.get("address"),
            "situs_city": p.get("city"),
            "owner_name": p.get("owner_name"),
            "assessed_value": None,  # not in ACCOUNT_INFO.CSV; left for shape-compat with the old live lookup
            "property_type": None,
            "owner_mailing_address": p.get("owner_mailing_address"),
            "owner_mailing_city": p.get("owner_mailing_city"),
            "owner_mailing_state": p.get("owner_mailing_state"),
            "owner_mailing_zip": p.get("owner_mailing_zip"),
            "is_absentee_owner": flags["is_absentee_owner"],
            "is_out_of_state_owner": flags["is_out_of_state_owner"],
        }
        by_account[account] = entry
        if p.get("address"):
            by_street.setdefault(_normalize_street(p["address"]), []).append(account)
        if p.get("owner_name"):
            by_owner_name.setdefault(p["owner_name"], []).append(account)

    return {
        "by_account": by_account,
        "by_street": by_street,
        "by_owner_name": by_owner_name,
    }


def load_lookup_from_jsonl(path: Path) -> dict:
    """Rebuild the same lookup dict build_lookup() returns from an
    already-written parcel_master.jsonl (avoids re-parsing the 352MB CSV on
    every pipeline run when the raw JSONL is already on disk)."""
    records = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return build_lookup(records)


def lookup_by_account(lookup: dict, account: str) -> "dict | None":
    """Same return shape as parcel_master_dcad_dallas.DCADSession.lookup_account
    (plus the owner_mailing_*/is_absentee_owner/is_out_of_state_owner keys) --
    a drop-in replacement for callers that only need account-keyed lookups."""
    return lookup["by_account"].get(account)


def lookup_by_address(lookup: dict, raw_address: str) -> "dict | None":
    """Same single-confident-match discipline as the live DCADSession
    version: only returns a hit when the normalized street matches exactly
    one account. Returns the same dict shape as lookup_by_account, plus
    account_number (the live version's contract includes it since, unlike
    an account lookup, the caller doesn't already know it)."""
    from scaffold.pipeline.translators.parcel_master import _normalize_street

    accounts = lookup["by_street"].get(_normalize_street(raw_address))
    if not accounts or len(accounts) != 1:
        return None
    account = accounts[0]
    entry = lookup["by_account"].get(account)
    if entry is None:
        return None
    return {**entry, "account_number": account}


def lookup_by_owner_name(lookup: dict, name: str) -> "dict | None":
    """Same single-confident-match discipline as the live DCADSession
    version: only returns a hit when the owner name matches exactly one
    account (this bulk index's owner_name strings are exact DCAD strings,
    not free text, so an exact-string match here is the bulk equivalent of
    the live version's "count()==1" ambiguity check)."""
    name = (name or "").strip()
    if not name:
        return None
    accounts = lookup["by_owner_name"].get(name)
    if not accounts or len(accounts) != 1:
        return None
    account = accounts[0]
    entry = lookup["by_account"].get(account)
    if entry is None:
        return None
    return {**entry, "account_number": account}


def run_scraper(out_dir: Path, verbose: bool = True) -> dict:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    download_url = discover_download_url(session, verbose)
    zip_path = download_zip(session, download_url, verbose)
    records = parse_account_info(zip_path, verbose)

    out_path = out_dir / "parcel_master.jsonl"
    stats = _write_jsonl(records, out_path, verbose)

    return {
        "source_page": DATA_PRODUCTS_PAGE_URL,
        "download_url": download_url,
        "zip_cached_at": str(zip_path),
        "records_parsed": len(records),
        "parcel_master": stats,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Dallas DCAD bulk Data Products ownership file parser. Downloads "
            "the current 'Most Current Ownership' zip and emits one "
            "parcel_master raw record per account, including owner mailing "
            "address for absentee/out-of-state detection."
        )
    )
    parser.add_argument("--out-dir", default=None,
                         help="Output directory for parcel_master.jsonl. Default: data/raw/")
    args = parser.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else REPO_ROOT / "data" / "raw"
    stats = run_scraper(out_dir)
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
