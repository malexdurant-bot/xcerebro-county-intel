#!/usr/bin/env python3
"""DCAD bulk reader: the owner is OWNER_NAME1/2, never BIZ_NAME.

BIZ_NAME is a property/business descriptor ("VACANT", "PARKING"...). Taking it
first (the old behavior) put "VACANT" on leads as the owner and hid the real
owner name for 21% of the county. Synthetic rows only.

Run: python3 runs/dallas_tx/test_dcad_owner_name.py
Exit 0 = pass, non-zero = fail.
"""
import csv
import io
import sys
import tempfile
import zipfile
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


print("_owner_name")
case("a descriptor in BIZ_NAME does not replace the real owner",
     dcad._owner_name({"BIZ_NAME": "VACANT", "OWNER_NAME1": "TEST OWNER ONE"}) == "TEST OWNER ONE")
case("two owners are joined",
     dcad._owner_name({"OWNER_NAME1": "TEST OWNER ONE", "OWNER_NAME2": "TEST OWNER TWO"})
     == "TEST OWNER ONE & TEST OWNER TWO")
case("a second owner alone still works",
     dcad._owner_name({"OWNER_NAME2": "TEST OWNER TWO"}) == "TEST OWNER TWO")
case("BIZ_NAME is the last resort when there is no owner name at all",
     dcad._owner_name({"BIZ_NAME": "TEST BUSINESS LLC"}) == "TEST BUSINESS LLC")
case("nothing at all -> empty", dcad._owner_name({}) == "")
case("a doubled ampersand from the join is collapsed",
     dcad._owner_name({"OWNER_NAME1": "TEST ONE &", "OWNER_NAME2": "& TEST TWO"}) == "TEST ONE & TEST TWO")
case("a trailing doubled ampersand is collapsed to one",
     dcad._owner_name({"OWNER_NAME1": "TEST ONE &", "OWNER_NAME2": "&"}) == "TEST ONE &")
case("DCAD's normal single trailing '&' is left as-is",
     dcad._owner_name({"OWNER_NAME1": "TEST ONE &"}) == "TEST ONE &")
case("an ampersand inside a name is untouched",
     dcad._owner_name({"OWNER_NAME1": "TEST & SONS HOLDINGS"}) == "TEST & SONS HOLDINGS")
case("a joined pair with a normal separator is untouched",
     dcad._owner_name({"OWNER_NAME1": "TEST ONE", "OWNER_NAME2": "TEST TWO"}) == "TEST ONE & TEST TWO")
case("whitespace-only fields are treated as empty",
     dcad._owner_name({"BIZ_NAME": "  ", "OWNER_NAME1": " ", "OWNER_NAME2": ""}) == "")

print("end to end through parse_account_info")
HEADER = ["ACCOUNT_NUM", "BIZ_NAME", "OWNER_NAME1", "OWNER_NAME2", "OWNER_ADDRESS_LINE1",
          "OWNER_ADDRESS_LINE2", "OWNER_ADDRESS_LINE3", "OWNER_ADDRESS_LINE4", "OWNER_CITY",
          "OWNER_STATE", "OWNER_ZIPCODE", "STREET_NUM", "FULL_STREET_NAME", "PROPERTY_CITY",
          "PROPERTY_ZIPCODE", "LEGAL1", "LEGAL2", "LEGAL3", "LEGAL4", "LEGAL5"]


def row(acct, biz, o1, o2=""):
    r = dict.fromkeys(HEADER, "")
    r.update(ACCOUNT_NUM=acct, BIZ_NAME=biz, OWNER_NAME1=o1, OWNER_NAME2=o2,
             OWNER_ADDRESS_LINE1="100 TEST ST", OWNER_CITY="DALLAS", OWNER_STATE="TEXAS",
             STREET_NUM="1", FULL_STREET_NAME="TEST RD")
    return r


with tempfile.TemporaryDirectory() as td:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=HEADER)
    w.writeheader()
    w.writerow(row("ACCT_1", "VACANT", "TEST OWNER ONE"))
    w.writerow(row("ACCT_2", "", "TEST OWNER TWO", "TEST OWNER THREE"))
    zp = Path(td) / "t.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("ACCOUNT_INFO.CSV", buf.getvalue())
    recs = dcad.parse_account_info(zp)
    names = {r["raw_payload"]["parcel_id"]: r["raw_payload"]["owner_name"] for r in recs}
    case("an account with BIZ_NAME 'VACANT' carries its real owner",
         names["ACCT_1"] == "TEST OWNER ONE")
    case("a two-owner account is joined", names["ACCT_2"] == "TEST OWNER TWO & TEST OWNER THREE")
    lookup = dcad.build_lookup(recs)
    case("the real owner name is now findable (it was hidden behind 'VACANT')",
         dcad.lookup_by_owner_name(lookup, "TEST OWNER ONE") is not None)
    case("'VACANT' is no longer an owner-name key at all", "VACANT" not in lookup["by_owner_name"])

if FAILS:
    print(f"\n{len(FAILS)} FAILED")
    sys.exit(1)
print("\nall passed")
