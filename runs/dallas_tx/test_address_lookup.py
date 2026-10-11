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
case("a ZIP that disagrees with the parcel's -> None when the address names a DIFFERENT city",
     look("700 ELM ST GARLAND TEXAS 75999") is None)
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
     reason("700 ELM ST GARLAND TEXAS 75999") == "zip_mismatch")
case("no house number reports 'no_house_number'", reason("ELM ST DALLAS TEXAS") == "no_house_number")
case("empty reports 'empty'", reason("") == "empty")
case("lookup_by_address is just the match half",
     dcad.lookup_by_address(lookup, "311 S SAMPLE ST MESQUITE TEXAS 75149")["account_number"] == "A1")

print("the match carries the owner's mailing address")
hit = dcad.lookup_by_address(lookup, "311 S. SAMPLE STREET MESQUITE TEXAS 75149")
case("mailing address + flags come back with the match",
     hit["owner_mailing_address"] == "9 MAIL RD" and "is_absentee_owner" in hit)


# ---------------------------------------------------------------- round 2 (2026-10-10)
def parcel2(acct, address, city, zip_, unit=None, bldg=None):
    p = parcel(acct, address, city, zip_)
    p["raw_payload"]["unit_id"] = unit
    p["raw_payload"]["bldg_id"] = bldg
    return p


lk2 = dcad.build_lookup([
    # same name + number, different street type and city
    parcel2("D1", "1900 DRAKE ST", "GLENN HEIGHTS", "75154"),
    parcel2("D2", "1900 DRAKE DR", "DALLAS", "75081"),
    # same number + street type in two cities (told apart by ZIP / by city)
    parcel2("E1", "30 LAKE ST", "GARLAND", "75040"),
    parcel2("E2", "30 LAKE ST", "IRVING", "75061"),
    # a condo building: one street address, many units
    parcel2("C1", "9000 SAMPLE PARK ST", "DALLAS", "75243", unit="11101", bldg="L"),
    parcel2("C2", "9000 SAMPLE PARK ST", "DALLAS", "75243", unit="11102", bldg="L"),
    parcel2("C3", "9000 SAMPLE PARK ST", "DALLAS", "75243", unit="0205", bldg="M"),
    parcel2("C4", "9000 SAMPLE PARK ST", "DALLAS", "75243", unit="A", bldg="M"),
    parcel2("C5", "9000 SAMPLE PARK ST", "DALLAS", "75243", unit="A", bldg="N"),  # same unit id, other building
    # directions and ordinals
    parcel2("P1", "700 E PIONEER DR", "IRVING", "75061"),
    parcel2("P2", "400 S 2ND AVE", "DALLAS", "75210"),
    # misspelling targets
    parcel2("F1", "6300 BANDERA AVE", "DALLAS", "75225"),
    parcel2("F2", "6300 BAYLOR AVE", "DALLAS", "75225"),
    parcel2("F3", "810 BRIARWOOD DR", "RICHARDSON", "75080"),
    parcel2("F4", "810 BRIARGOOD DR", "RICHARDSON", "75080"),
    parcel2("F5", "55 OAK ST", "DALLAS", "75201"),
    parcel2("G1", "77 WILLOWBROOK ST", "LANCASTER", "75134"),
    parcel2("G2", "7000 DENT DR", "DALLAS", "75254"),
    parcel2("G3", "7002 DENT DR", "DALLAS", "75254"),
    parcel2("T1", "2 TYPO RD", "DALLAS", "75001"),
])
look2 = lambda a: (dcad.lookup_by_address(lk2, a) or {}).get("account_number")
why2 = lambda a: dcad.lookup_by_address_ex(lk2, a)[1]

print("same street name, different street type / city / ZIP")
case("'DRAKE STREET' picks DRAKE ST over DRAKE DR", look2("1900 DRAKE STREET GLENN HEIGHTS TEXAS 75154") == "D1")
case("'DRAKE DRIVE' picks DRAKE DR", look2("1900 DRAKE DRIVE DALLAS TEXAS 75081") == "D2")
case("the same street type in two cities is told apart by ZIP", look2("30 LAKE STREET IRVING TEXAS 75061") == "E2")
case("...and by the city named when there is no ZIP", look2("30 LAKE STREET, GARLAND, TEXAS") == "E1")
case("...but stays ambiguous with neither", why2("30 LAKE STREET") == "ambiguous")

print("condo / apartment units are matched against the county's own unit id")
case("'UNIT 11101' -> the one account with that unit id", look2("9000 SAMPLE PARK ST UNIT 11101, DALLAS, TEXAS, 75243") == "C1")
case("'#11102' / 'APT. 11102' / 'NO 11102' / 'UNIT11102' agree",
     {look2(f"9000 SAMPLE PARK ST {u}, DALLAS, TEXAS, 75243") for u in ("#11102", "APT. 11102", "NO 11102", "UNIT11102")} == {"C2"})
case("a unit written between commas", look2("9000 SAMPLE PARK ST, 11101, DALLAS, TEXAS, 75243") == "C1")
case("...but a ZIP repeated in the address is not a unit",
     look2("1900 DRAKE STREET, 75154, GLENN HEIGHTS, TEXAS, 75154") == "D1")
case("leading zeros do not matter ('205' finds '0205')", look2("9000 SAMPLE PARK ST #205 DALLAS TEXAS 75243") == "C3")
case("the reason is 'matched_unit'", why2("9000 SAMPLE PARK ST #11101 DALLAS TEXAS 75243") == "matched_unit")
case("a unit id shared by two buildings -> ambiguous, never a guess",
     look2("9000 SAMPLE PARK ST #A DALLAS TEXAS 75243") is None and why2("9000 SAMPLE PARK ST #A DALLAS TEXAS 75243") == "ambiguous")
case("a unit that does not exist -> declined as 'unit'", why2("9000 SAMPLE PARK ST #99999 DALLAS TEXAS 75243") == "unit")
case("no unit given on a building with many units -> ambiguous", why2("9000 SAMPLE PARK ST DALLAS TEXAS 75243") == "ambiguous")
case("an index WITHOUT unit ids still refuses any unit address",
     dcad.lookup_by_address_ex(lookup, "700 ELM ST APT 4 DALLAS TEXAS 75202")[1] == "unit")

print("office-type designators are never mapped onto a parcel")
for label in ("SUITE 200", "STE 4", "BLDG 5", "FLOOR 3", "MAIL CODE 1011-F", "P.O. BOX 12"):
    addr = f"1900 DRAKE STREET {label}, GLENN HEIGHTS, TEXAS 75154"
    case(f"'{label}' -> declined", look2(addr) is None and why2(addr) == "unit")

print("several addresses in one notice")
case("a range ('7000-7002 DENT DR') -> declined", why2("7000-7002 DENT DRIVE DALLAS TEXAS 75254") == "multi_address")
case("a list ('7000, 7002 DENT DR') -> declined", why2("7000, 7002 DENT DRIVE, DALLAS, TEXAS, 75254") == "multi_address")

print("direction and ordinal words")
case("'EAST PIONEER DRIVE' finds 'E PIONEER DR'", look2("700 EAST PIONEER DRIVE, IRVING, TEXAS, 75061") == "P1")
case("'SOUTH SECOND AVE' finds 'S 2ND AVE'", look2("400 SOUTH SECOND AVE DALLAS TEXAS 75210") == "P2")

print("a ZIP typo is forgiven only when the city agrees AND the street type does not disagree")
case("wrong ZIP, the parcel's city named -> matched_city",
     why2("2 TYPO ROAD, DALLAS, TEXAS, 15001") == "matched_city" and look2("2 TYPO ROAD, DALLAS, TEXAS, 15001") == "T1")
case("wrong ZIP, another city -> declined", why2("2 TYPO ROAD, GARLAND, TEXAS, 15001") == "zip_mismatch")
case("wrong ZIP and a different street type -> declined", why2("2 TYPO AVENUE, DALLAS, TEXAS, 15001") == "zip_mismatch")

print("misspelled street names (fuzzy) -- strict")
case("'BUNDERA AVE' -> BANDERA AVE, house number + ZIP agreeing", look2("6300 BUNDERA AVE DALLAS TEXAS 75225") == "F1")
case("the reason says it was fuzzy", why2("6300 BUNDERA AVE DALLAS TEXAS 75225") == "matched_fuzzy")
case("with no ZIP, the city named in the address stands in", look2("77 WILLOWBROK ST, LANCASTER, TEXAS") == "G1")
case("neither ZIP nor city -> no fuzzy attempt", look2("6300 BUNDERA AVE") is None)
case("a ZIP that is not the parcel's -> no fuzzy attempt", look2("6300 BUNDERA AVE DALLAS TEXAS 75999") is None)
case("two near-equal candidates -> declined, not guessed", look2("810 BRIARBOOD DR RICHARDSON TEXAS 75080") is None)
case("a name that is only somewhat similar is refused", look2("6300 BAXTER AVE DALLAS TEXAS 75225") is None)
case("short names are never fuzzy-matched ('OAK' vs 'OAKS')", look2("55 OAKS ST DALLAS TEXAS 75201") is None)
case("an exact street always wins over a fuzzy one",
     look2("6300 BANDERA AVE DALLAS TEXAS 75225") == "F1" and why2("6300 BANDERA AVE DALLAS TEXAS 75225") == "matched")

if FAILS:
    print(f"\n{len(FAILS)} FAILED")
    sys.exit(1)
print("\nall passed")
