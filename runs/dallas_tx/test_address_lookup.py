#!/usr/bin/env python3
"""Address -> parcel matching against the DCAD bulk index (synthetic county).

Regression for 2026-10-10: lookup_by_address normalized the WHOLE string, so
an address with a city / state / ZIP attached (every foreclosure-portal
address) matched 0 of 401 on real data. It must now isolate the street --
without ever guessing: ambiguity, units, ZIP disagreement and a missing house
number all return None.

Run: python3 runs/dallas_tx/test_address_lookup.py
Exit 0 = pass, non-zero = fail.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scrapers.parcel_master_dcad_bulk_dallas as dcad

FAILS = []


def case(name, ok):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILS.append(name)


def parcel(acct, address, city, zip_, owner="TEST OWNER"):
    return {"raw_payload": {
        "parcel_id": acct, "address": address, "city": city, "zip": zip_,
        "owner_name": owner, "owner_mailing_address": "9 MAIL RD",
        "owner_mailing_city": "DALLAS", "owner_mailing_state": "TX",
        "owner_mailing_zip": "75001", "situs_state": "TX", "legal_description": None,
    }}


lookup = dcad.build_lookup([
    parcel("A1", "311 S SAMPLE ST", "MESQUITE", "75149"),
    parcel("A2", "2017 MEADOW VIEW", "RICHARDSON", "75081"),   # street with no suffix word
    parcel("A3", "4400 TESTBIRD WAY", "MESQUITE", "75150"),
    parcel("A4", "500 OAK ST", "DALLAS", "75201"),               # two condos share this street...
    parcel("A5", "500 OAK ST", "DALLAS", "75201"),               # ...so it is ambiguous
    parcel("A6", "500 OAK", "GARLAND", "75040"),                 # NOT the same key as '500 OAK ST'? (suffix-stripped: it is) -> ambiguous too
    parcel("A7", "700 ELM ST", "DALLAS", "75202"),               # unique
    parcel("A8", "70 TEST", "DALLAS", "75203"),
    parcel("B1", "80 RIVER OAKS", "DALLAS", "75204"),            # an AMBIGUOUS longer street...
    parcel("B2", "80 RIVER OAKS", "DALLAS", "75204"),
    parcel("B3", "80 RIVER RD", "DALLAS", "75204"),              # ...beside a UNIQUE shorter, different street
])
look = lambda a: (dcad.lookup_by_address(lookup, a) or {}).get("account_number")

print("addresses as they actually arrive")
case("'STREET CITY TEXAS ZIP' with no separator (foreclosure portal)",
     look("311 S. SAMPLE STREET MESQUITE TEXAS 75149") == "A1")
case("'STREET, CITY, TX ZIP'", look("311 S SAMPLE ST, MESQUITE, TX 75149") == "A1")
case("a street with no suffix word, city appended",
     look("2017 MEADOW VIEW RICHARDSON TEXAS 75081") == "A2")
case("no ZIP at all", look("4400 TESTBIRD WAY MESQUITE TEXAS") == "A3")
case("a bare street still works (the old behavior)", look("4400 TESTBIRD WAY") == "A3")
case("ZIP+4 is understood", look("4400 TESTBIRD WAY MESQUITE TX 75150-1234") == "A3")
case("matching is case/punctuation-insensitive", look("311 s. sample st., mesquite, tx") == "A1")

print("it never guesses")
case("ambiguous street (several accounts) -> None", look("500 OAK ST DALLAS TEXAS 75201") is None)
case("ambiguity does NOT fall back to a shorter, different street ('80 RIVER OAKS' x2 vs unique '80 RIVER')",
     look("80 RIVER OAKS DALLAS TEXAS 75204") is None)
case("...while the unique shorter street is still found on its own",
     look("80 RIVER RD DALLAS TEXAS 75204") == "B3")
case("a unit / suite / apt address is never mapped onto the building's parcel",
     look("700 ELM ST APT 4 DALLAS TEXAS 75202") is None
     and look("700 ELM ST, SUITE 20 DALLAS TEXAS 75202") is None
     and look("700 ELM ST #12 DALLAS TX") is None)
case("the same unique street WITHOUT a unit does match", look("700 ELM ST DALLAS TEXAS 75202") == "A7")
case("a ZIP that disagrees with the parcel's -> None (same street name, another city)",
     look("700 ELM ST DALLAS TEXAS 75999") is None)
case("no leading house number -> None", look("ELM ST DALLAS TEXAS 75202") is None)
case("empty / None -> None", look("") is None and look(None) is None)
case("a street that is not in the county -> None", look("9999 NOWHERE LN DALLAS TEXAS 75202") is None)

print("the decline reason (only 'not_found' may be second-guessed by the live lookup)")
reason = lambda a: dcad.lookup_by_address_ex(lookup, a)[1]
case("a match reports 'matched'", reason("311 S SAMPLE ST MESQUITE TEXAS 75149") == "matched")
case("a street not in the county reports 'not_found'", reason("9999 NOWHERE LN DALLAS TEXAS") == "not_found")
case("an ambiguous street reports 'ambiguous' (NOT 'not_found')", reason("500 OAK ST DALLAS TEXAS 75201") == "ambiguous")
case("a unit address reports 'unit' (NOT 'not_found')", reason("700 ELM ST APT 4 DALLAS TEXAS") == "unit")
case("a ZIP disagreement reports 'zip_mismatch' (NOT 'not_found')",
     reason("700 ELM ST DALLAS TEXAS 75999") == "zip_mismatch")
case("no house number reports 'no_house_number'", reason("ELM ST DALLAS TEXAS") == "no_house_number")
case("empty reports 'empty'", reason("") == "empty")
case("lookup_by_address is just the match half",
     dcad.lookup_by_address(lookup, "311 S SAMPLE ST MESQUITE TEXAS 75149")["account_number"] == "A1")

print("the match carries the owner's mailing address")
hit = dcad.lookup_by_address(lookup, "311 S. SAMPLE STREET MESQUITE TEXAS 75149")
case("mailing address + flags come back with the match",
     hit["owner_mailing_address"] == "9 MAIL RD" and "is_absentee_owner" in hit)

if FAILS:
    print(f"\n{len(FAILS)} FAILED")
    sys.exit(1)
print("\nall passed")
