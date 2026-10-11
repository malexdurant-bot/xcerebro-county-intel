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
_HOUSE_NUMBER_RE = re.compile(r"\d+")

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


def _owner_name(row: dict) -> str:
    """The owner of record: OWNER_NAME1 [& OWNER_NAME2].

    BIZ_NAME is NOT the owner. It's a property / operating-business
    descriptor: 21% of Dallas accounts (180,823 of 862,771) have one, it is
    overwhelmingly a land-use note ("VACANT" x16,875, "VACANT LAND", "LAND
    ONLY", "PARKING", "HOUSE"...), and 180,795 of those accounts ALSO carry
    a real owner name. This used to take BIZ_NAME first, which (found
    2026-10-09) put "VACANT" on ~94 leads as the owner and, worse, made
    every one of those accounts unmatchable by its true owner name in
    lookup_by_owner_name. BIZ_NAME is used only when an account has no owner
    name at all (28 accounts)."""
    name1 = (row.get("OWNER_NAME1") or "").strip()
    name2 = (row.get("OWNER_NAME2") or "").strip()
    owner = " & ".join(n for n in (name1, name2) if n)
    # DCAD writes a joint owner as "A B &" (name1) + "& C D" or "C D" (name2),
    # so joining can double the ampersand ("A B & & C D", 26 accounts).
    # Collapse repeats only; the single trailing "&" convention on ~156k
    # accounts is DCAD's own and is left as-is (changing it would alter
    # owner-name matching county-wide).
    owner = re.sub(r"(?:&\s*){2,}", "& ", owner + " ").strip()
    return owner or (row.get("BIZ_NAME") or "").strip()


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

                owner_name = _owner_name(row)

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
                    # condo / apartment accounts share one street address and
                    # differ only by these (e.g. BLDG 'G' UNIT '00136').
                    "bldg_id": (row.get("BLDG_ID") or "").strip() or None,
                    "unit_id": (row.get("UNIT_ID") or "").strip() or None,
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


# Legal-description cross-reference (2026-09-22): a client-facing operator
# noticed a real, populated legal description ("Subdivision - Name:
# LAKEWOOD POINTE Lot: 23 Block: 8") on a probate/heirship-family lead that
# has no address at all -- these doc types often carry no street address
# anywhere in the recorded document, but DO reliably state the subdivision
# + lot + block, which is exactly what DCAD's own bulk legal_description
# field encodes too. DCAD's live search portal itself has no
# subdivision/lot/block search mode (confirmed live: Search By is limited
# to Owner/Account/Address/Business) -- but the bulk file already has every
# account's own legal_description, so this is a LOCAL cross-reference, not
# a new network dependency. Verified live end-to-end on the case that
# motivated this: Kofile's "LAKEWOOD POINTE Lot 23 Block 8" matched exactly
# one DCAD account ("LAKEWOOD POINTE BLK 8 LOT 23...") whose owner name
# matched the surnames of the document's own parties (a decedent and a
# surviving relative), giving a real situs address.
#
# DCAD's own legal_description format ("<NAME> BLK <N> LT <N> ...") differs
# from Kofile's ("Subdivision - Name: <NAME> Lot: <N> Block: <N> ...") in
# both field order and trailing phase/section modifiers DCAD sometimes adds
# ("LAKEWOOD POINTE PH 5 REP" vs plain "LAKEWOOD POINTE") -- the subdivision
# name alone is too fuzzy to match exactly, so only its CORE (everything
# before the first phase/section/number token) is used as the join key,
# combined with the exact lot+block pair, which is the far more
# discriminating part of the key. Same "only when there's exactly one
# candidate" discipline as every other lookup_by_* in this module.
_SUBDIVISION_MODIFIER_RE = re.compile(
    r"^(PH|PHASE|SEC|SECTION|REP|REPLAT|INST|INSTALLMENT|NO|ADDN|ADDITION|"
    r"UNIT|AMD|AMENDED|REV|REVISED)\d*$"
)
_DCAD_LOT_BLOCK_RE = re.compile(
    r"^(?P<name>.*?)\s+BLK\.?\s*(?P<block>[A-Z0-9]+)\s+L(?:T|OT)\.?\s*(?P<lot>[A-Z0-9]+)\b",
    re.IGNORECASE,
)
_KOFILE_LOT_BLOCK_RE = re.compile(
    r"Name:\s*(?P<name>.+?)\s+Lot:\s*(?P<lot>[A-Za-z0-9]+)\s+Block:\s*(?P<block>[A-Za-z0-9]+)",
    re.IGNORECASE,
)


def _subdivision_core_key(name: str) -> str:
    tokens = re.findall(r"[A-Z0-9]+", (name or "").upper())
    core = []
    for t in tokens:
        if t.isdigit() or _SUBDIVISION_MODIFIER_RE.match(t):
            break
        core.append(t)
    return "".join(core)


def _lot_block_key(name: str, lot: str, block: str) -> "tuple[str, str, str] | None":
    core = _subdivision_core_key(name)
    if not core:
        return None
    return (core, lot.upper().lstrip("0") or "0", block.upper().lstrip("0") or "0")


def build_lookup(records: list[dict]) -> dict:
    """{account_number: {situs_address, situs_city, owner_name,
    assessed_value, property_type, owner_mailing_*, is_absentee_owner,
    is_out_of_state_owner}} -- shaped to be a drop-in superset of the live
    per-account lookup's dict shape (parcel_master_dcad_dallas.py), so
    run_pipeline.py's existing dcad_lookup consumers (translate_taxsales_lgbs,
    the Step 2c address/owner enrichment) keep working unmodified, and gain
    the two new flags for free.

    Also indexes by normalized street (for an address-based fallback lookup),
    by owner_name, and by (subdivision-core, lot, block) (both single-match
    only, mirroring the old live lookup's "never guess" discipline).
    """
    from scaffold.pipeline.translators.parcel_master import _derive_absentee_flags, _normalize_street

    by_account: dict = {}
    by_street: dict = {}
    by_owner_name: dict = {}
    by_lot_block: dict = {}
    by_house: dict = {}  # house number -> accounts (misspelled-street fallback)
    has_units = False

    for rec in records:
        p = rec["raw_payload"]
        account = p["parcel_id"]
        flags = _derive_absentee_flags(p)
        entry = {
            "situs_address": p.get("address"),
            "situs_city": p.get("city"),
            "situs_zip": p.get("zip"),
            "unit_id": p.get("unit_id"),
            "bldg_id": p.get("bldg_id"),
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
        if p.get("unit_id"):
            has_units = True
        if p.get("address"):
            by_street.setdefault(_normalize_street(p["address"]), []).append(account)
            house_match = _HOUSE_NUMBER_RE.match(p["address"])
            if house_match:
                by_house.setdefault(house_match.group(0), []).append(account)
        if p.get("owner_name"):
            by_owner_name.setdefault(p["owner_name"], []).append(account)
        ld_match = _DCAD_LOT_BLOCK_RE.match(p.get("legal_description") or "")
        if ld_match:
            key = _lot_block_key(ld_match.group("name"), ld_match.group("lot"), ld_match.group("block"))
            if key:
                by_lot_block.setdefault(key, []).append(account)

    return {
        "by_account": by_account,
        "by_street": by_street,
        "by_owner_name": by_owner_name,
        "by_lot_block": by_lot_block,
        "by_house": by_house,
        "has_units": has_units,
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


_ZIP_TAIL_RE = re.compile(r"[\s,]+(\d{5})(?:-\d{4})?\s*$")
_STATE_TAIL_RE = re.compile(r"[\s,]+(?:TEXAS|TX)\.?\s*$", re.IGNORECASE)

# A residential unit designator and the unit's own id: 'UNIT 11101', 'APT. 10100',
# '#16', 'NO 102', 'UNIT208'. (Suite / building / floor / lot / space / mail
# code are NOT here on purpose -- see _REFUSED_UNIT_RE.)
_LABELED_UNIT_RE = re.compile(
    r"(?:#|\b(?:APT|APARTMENT|UNIT|NO)(?=[\s.#\d])\.?)\s*#?\s*([A-Z0-9][A-Z0-9\-]*)",
    re.IGNORECASE,
)
# Designators that mean an office / commercial space / lot rather than a
# residential unit (a trustee's own office address is typically 'Suite 200').
# An address carrying one of these is never mapped onto a parcel.
_REFUSED_UNIT_RE = re.compile(
    r"\b(?:STE|SUITE|BLDG|BUILDING|FLOOR|FL|RM|ROOM|SPC|SPACE|TRLR|LOT|MAIL\s*CODE)\b|P\.?\s*O\.?\s*BOX",
    re.IGNORECASE,
)
# '7626-7628 X DR' (a range) or '4301, 4217, 4213 X MEWS' (a list): the notice
# covers several addresses, so no single parcel is "the" property.
_MULTI_ADDRESS_RE = re.compile(r"\s*\d+\s*(?:-\s*\d+\s+|,\s*&?\s*\d+)")
_BARE_UNIT_SEGMENT_RE = re.compile(r"\d{1,5}[A-Za-z]?|[A-Za-z]\d{1,5}")

_DIRECTION_WORDS = {
    "EAST": "E", "WEST": "W", "NORTH": "N", "SOUTH": "S",
    "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
}
_ORDINAL_WORDS = {
    "FIRST": "1ST", "SECOND": "2ND", "THIRD": "3RD", "FOURTH": "4TH", "FIFTH": "5TH",
    "SIXTH": "6TH", "SEVENTH": "7TH", "EIGHTH": "8TH", "NINTH": "9TH", "TENTH": "10TH",
}
# Street-type word -> the abbreviation DCAD uses. Used only to tell apart
# two streets that share a name ('DRAKE ST' vs 'DRAKE DR').
_SUFFIX_CANON = {
    "STREET": "ST", "DRIVE": "DR", "ROAD": "RD", "LANE": "LN", "CIRCLE": "CIR",
    "COURT": "CT", "AVENUE": "AVE", "BOULEVARD": "BLVD", "TRAIL": "TRL",
    "PLACE": "PL", "PARKWAY": "PKWY", "HIGHWAY": "HWY", "TERRACE": "TER",
}
_SUFFIX_ABBRS = set(_SUFFIX_CANON.values()) | {"WAY", "LOOP", "CV", "CROSSING", "SQ", "PASS", "RUN", "WALK"}

# Fuzzy street-name matching (misspelled streets on scanned notices).
# Deliberately strict: it only runs when NO street matched exactly, needs the
# house number plus a ZIP (or a city named in the address) to agree, a name of
# at least FUZZY_MIN_NAME_LEN letters, and a clear single winner.
FUZZY_MIN_NAME_LEN = 5
FUZZY_MIN_RATIO = 0.85
FUZZY_MIN_MARGIN = 0.04


def _expanded_variants(tokens: "list[str]") -> "list[list[str]]":
    """The token list, plus (when different) a copy with a leading direction
    word abbreviated ('EAST PIONEER' -> 'E PIONEER') and spelled-out ordinals
    made numeric ('SECOND' -> '2ND'). The original always comes first: 'WEST ST'
    is a real street name."""
    variants = [tokens]
    alt = list(tokens)
    changed = False
    if len(alt) >= 2:
        # tokens[0] is the house number; the direction word follows it
        head = alt[1].upper().strip(".,")
        if head in _DIRECTION_WORDS:
            alt[1] = _DIRECTION_WORDS[head]
            changed = True
    for i in range(1, len(alt)):
        w = alt[i].upper().strip(".,")
        if w in _ORDINAL_WORDS:
            alt[i] = _ORDINAL_WORDS[w]
            changed = True
    if changed:
        variants.append(alt)
    return variants


def _street_candidates(raw_address: str) -> "tuple[list[str], str | None]":
    """Split a free-form address into (candidate street strings, ZIP).

    Addresses reach the lookup as 'STREET CITY TEXAS ZIP' with no separator
    ('311 S. SAMPLE STREET MESQUITE TEXAS 75149'), as 'STREET, CITY, TX ZIP',
    or as a bare street. The county index is keyed by the STREET alone, and
    a city name can't be told apart from a street name by looking at it
    ('2017 MEADOW VIEW RICHARDSON'), so: drop a trailing ZIP and state,
    take what precedes the first comma, and offer every word-prefix of it,
    longest first. The caller takes the longest one that exists in the
    index, so a city only ever gets trimmed when the street is real.
    A leading direction word ('EAST') and spelled-out ordinals ('SECOND')
    are also offered in DCAD's abbreviated form ('E', '2ND')."""
    s = (raw_address or "").strip()
    zip5 = None
    m = _ZIP_TAIL_RE.search(s)
    if m:
        zip5 = m.group(1)
        s = s[:m.start()]
    s = _STATE_TAIL_RE.sub("", s)
    s = s.split(",", 1)[0].strip()
    tokens = s.split()
    out: list[str] = []
    variants = _expanded_variants(tokens)
    for n in range(len(tokens), 1, -1):
        for variant in variants:
            cand = " ".join(variant[:n])
            if cand not in out:
                out.append(cand)
    return out, zip5


def lookup_by_address(lookup: dict, raw_address: str) -> "dict | None":
    """See lookup_by_address_ex; this returns just the match (or None)."""
    return lookup_by_address_ex(lookup, raw_address)[0]


def _extract_unit(raw: str) -> "tuple[str | None, str, bool]":
    """(unit_id_or_None, address_with_the_unit_removed, refused). `refused`
    means the address carries a designator we never map onto a parcel
    (suite / building / floor / lot / P.O. box ...)."""
    if _REFUSED_UNIT_RE.search(raw):
        return None, raw, True
    m = _LABELED_UNIT_RE.search(raw)
    if m:
        return m.group(1), (raw[:m.start()] + " " + raw[m.end():]), False
    parts = raw.split(",")
    if len(parts) >= 3 and _BARE_UNIT_SEGMENT_RE.fullmatch(parts[1].strip() or "-"):
        seg = parts[1].strip()
        # a number that appears again in the address is the ZIP, not a unit
        # ('6410 TEST DRIVE, 75039, IRVING, TEXAS, 75039')
        if raw.count(seg) == 1:
            return seg, ",".join([parts[0]] + parts[2:]), False
    return None, raw, False


def _unit_key(value: "str | None") -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper()).lstrip("0")


def _city_key(value: "str | None") -> str:
    """'GARLAND (DALLAS CO)' -> 'GARLAND'."""
    return re.sub(r"\s*\(.*?\)\s*", " ", (value or "").upper()).strip()


def _city_named(city: "str | None", text_up: str) -> bool:
    """The parcel's city appears in the address text as a whole word, and not
    as part of 'DALLAS COUNTY'."""
    c = _city_key(city)
    if not c:
        return False
    return re.search(r"\b" + re.escape(c) + r"\b(?!\s+COUNTY)", text_up) is not None


def _last_street_word(text: "str | None") -> "str | None":
    words = re.findall(r"[A-Z0-9]+", (text or "").upper())
    if not words:
        return None
    w = words[-1]
    w = _SUFFIX_CANON.get(w, w)
    return w if w in _SUFFIX_ABBRS else None


def lookup_by_address_ex(lookup: dict, raw_address: str) -> "tuple[dict | None, str]":
    """(match_or_None, reason). reason is one of
      'matched'        -- exactly one account on that number + street
                          (a same-named street in another city, or a different
                          street type, is told apart by ZIP / street type / city)
      'matched_unit'   -- a condo / apartment: street AND the unit id agree
      'matched_city'   -- the notice's ZIP disagrees with the parcel's, but the
                          number + street are unique and the parcel's city is
                          named in the address
      'matched_fuzzy'  -- a misspelled street name, accepted only under the
                          strict rules in _fuzzy_match
    or why it declined: 'empty', 'no_house_number', 'unit', 'not_found',
    'ambiguous', 'zip_mismatch'. Callers use it to decide whether a second
    opinion (the slow live DCAD lookup) is appropriate: only 'not_found' is a
    gap the live lookup could fill; every other reason is a DELIBERATE
    refusal that a second lookup must not override.

    Same single-confident-match discipline as the live DCADSession
    version: only returns a hit when exactly one account fits. Returns the
    same dict shape as lookup_by_account, plus account_number.

    2026-10-10: this used to normalize the WHOLE string and look for an
    exact street-only key, so any address with a city / state / ZIP attached
    (every foreclosure-portal address) matched nothing -- 0 of 401 on real
    data. Now the street is isolated first (see _street_candidates). Never
    guesses:
      - still ambiguous after ZIP / street type / city -> None (it does NOT
        fall back to a shorter, different street);
      - an office-type designator (suite, building, floor, lot, P.O. box) ->
        None; a residential unit is matched only against DCAD's own unit id;
      - a ZIP that disagrees with the matched parcel's -> None, unless the
        parcel's city is named in the address (a ZIP typo);
      - no leading house number -> None."""
    from scaffold.pipeline.translators.parcel_master import _normalize_street

    raw = (raw_address or "").strip()
    unit, cleaned, refused = _extract_unit(raw)
    if _MULTI_ADDRESS_RE.match(raw):
        return None, "multi_address"  # '7626-7628 ...', '4301, 4217, 4213 ...': several properties
    candidates, zip5 = _street_candidates(cleaned)
    if not candidates:
        return None, "empty"
    if not candidates[0].split()[0][:1].isdigit():
        return None, "no_house_number"
    if refused:
        return None, "unit"
    if unit and not lookup.get("has_units"):
        return None, "unit"  # this index has no unit ids to check a unit against

    text_up = cleaned.upper()
    for candidate in candidates:
        accounts = lookup["by_street"].get(_normalize_street(candidate))
        if not accounts:
            continue
        return _resolve_accounts(lookup, accounts, unit, zip5, text_up, candidate, "matched")
    return _fuzzy_match(lookup, candidates, unit, zip5, text_up)


def _resolve_accounts(lookup, accounts, unit, zip5, text_up, candidate, ok_reason):
    """Narrow the accounts found on one number + street to a single one."""
    by_account = lookup["by_account"]
    entries = [(a, by_account.get(a)) for a in accounts]
    entries = [(a, e) for a, e in entries if e is not None]
    if not entries:
        return None, "not_found"
    reason = ok_reason

    if zip5:
        in_zip = [(a, e) for a, e in entries if (e.get("situs_zip") or "")[:5] == zip5]
        if in_zip:
            entries = in_zip
        elif all((e.get("situs_zip") or "") for _, e in entries):
            # Every parcel has a ZIP and none is the notice's: a ZIP typo is
            # forgiven only when the parcel's city is named in the address.
            # ...and the street type must not disagree as well ('STREET' vs
            # 'AVE'): two discrepancies is a different place, not a typo.
            want_suffix = _last_street_word(candidate)
            same_city = [
                (a, e) for a, e in entries
                if _city_named(e.get("situs_city"), text_up)
                and (not want_suffix or _last_street_word(e.get("situs_address")) in (None, want_suffix))
            ]
            if not same_city:
                return None, "zip_mismatch"
            entries = same_city
            reason = "matched_city"
    elif len(entries) > 1:
        same_city = [(a, e) for a, e in entries if _city_named(e.get("situs_city"), text_up)]
        if same_city:
            entries = same_city

    if len(entries) > 1 and not unit:
        suffix = _last_street_word(candidate)
        if suffix:
            same_suffix = [(a, e) for a, e in entries if _last_street_word(e.get("situs_address")) == suffix]
            if same_suffix:
                entries = same_suffix

    if unit:
        want = _unit_key(unit)
        entries = [(a, e) for a, e in entries if want and _unit_key(e.get("unit_id")) == want]
        if len(entries) == 1:
            account, entry = entries[0]
            return {**entry, "account_number": account}, ("matched_unit" if reason == "matched" else reason)
        return None, ("ambiguous" if len(entries) > 1 else "unit")

    if len(entries) != 1:
        return None, "ambiguous"
    account, entry = entries[0]
    return {**entry, "account_number": account}, reason


def _fuzzy_match(lookup, candidates, unit, zip5, text_up):
    """A street name misspelled on a scanned notice ('BUNDERA' for 'BANDERA').
    Only runs when no street matched exactly, and only accepts when ALL hold:
      - the house number matches parcels in the county;
      - those parcels are narrowed by the notice's ZIP (or, with no ZIP, by a
        city named in the address) -- with neither, nothing is attempted;
      - the street NAME (>= FUZZY_MIN_NAME_LEN letters) is at least
        FUZZY_MIN_RATIO similar, and clearly closer (FUZZY_MIN_MARGIN) than
        any other street in that pool;
      - the winning street has exactly one account (or the unit pins one)."""
    import difflib

    from scaffold.pipeline.translators.parcel_master import _normalize_street

    house = re.match(r"\d+", candidates[0])
    if not house:
        return None, "not_found"
    by_account = lookup["by_account"]
    pool = []
    for a in lookup.get("by_house", {}).get(house.group(0)) or []:
        e = by_account.get(a)
        if e is None:
            continue
        if zip5:
            if (e.get("situs_zip") or "")[:5] != zip5:
                continue
        elif not _city_named(e.get("situs_city"), text_up):
            continue
        pool.append((a, e))
    if not pool:
        return None, "not_found"

    def name_key(address: str) -> str:
        return _normalize_street(re.sub(r"^\s*\d+\S*\s*", "", address or ""))

    wanted = {name_key(c) for c in candidates}
    wanted = {w for w in wanted if len(w) >= FUZZY_MIN_NAME_LEN}
    if not wanted:
        return None, "not_found"

    by_name: dict = {}
    for a, e in pool:
        by_name.setdefault(name_key(e.get("situs_address")), []).append(a)
    scored = []
    for nm in by_name:
        if len(nm) < FUZZY_MIN_NAME_LEN:
            continue
        scored.append((max(difflib.SequenceMatcher(None, w, nm).ratio() for w in wanted), nm))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < FUZZY_MIN_RATIO:
        return None, "not_found"
    if len(scored) > 1 and scored[0][0] - scored[1][0] < FUZZY_MIN_MARGIN:
        return None, "ambiguous"

    hit, reason = _resolve_accounts(lookup, by_name[scored[0][1]], unit, None, text_up, "", "matched")
    if hit is None:
        return None, reason
    return hit, ("matched_fuzzy" if reason in ("matched", "matched_unit") else reason)


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


def lookup_by_legal_description(lookup: dict, kofile_legal_description: str) -> "dict | None":
    """Parse a Kofile-format legal description ("Subdivision - Name: X Lot:
    Y Block: Z ...") and cross-reference it against the bulk file's own
    (subdivision-core, lot, block) index (see build_lookup). Same single-
    confident-match discipline as every other lookup_by_* here -- returns
    None unless exactly one account shares that subdivision/lot/block."""
    m = _KOFILE_LOT_BLOCK_RE.search(kofile_legal_description or "")
    if not m:
        return None
    key = _lot_block_key(m.group("name"), m.group("lot"), m.group("block"))
    if not key:
        return None
    accounts = lookup.get("by_lot_block", {}).get(key)
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
