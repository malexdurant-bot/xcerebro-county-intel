#!/usr/bin/env python3
"""Property address read off a foreclosure notice's scanned text.

~47% of foreclosure notices have no address on the document's detail page.
Their scanned text often states the property -- but also states other
addresses (the trustee's office, the courthouse where the sale is held), so
ONLY an explicit property label counts. Synthetic text only.

Run: python3 runs/dallas_tx/test_foreclosure_notice_address.py
Exit 0 = pass, non-zero = fail.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for p in (str(REPO_ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import translate as tr

FAILS = []


def case(name, ok):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILS.append(name)


ex = tr._labeled_property_address

print("accepts an explicit property label")
case("table-style OCR with a date before the address",
     ex("Property address: August 20, 2024 100 TEST CT  DALLAS, TX 75001  Grantor(s)/Mortgagor(s): TEST OWNER")
     == "100 TEST CT DALLAS TX 75001")
case("'Property Address:' followed by street, city, state, ZIP",
     ex("Property Address: 200 TEST COURT DALLAS, TX 75002 Mortgage Servicer: TEST BANK")
     == "200 TEST COURT DALLAS TX 75002")
case("'commonly known as' with comma-separated parts",
     ex("the Property, commonly known as 300 TEST LN, GARLAND, TEXAS 75040, together with") ==
     "300 TEST LN, GARLAND TX 75040")
case("label and address split across lines (OCR line breaks)",
     ex("PROPERTY ADDRESS:\n\n400  TEST  WAY\nIRVING, TX 75038\nLender") == "400 TEST WAY IRVING TX 75038")

print("refuses the lookalikes")
case("the trustee's office (introduced by 'located at', not a property label)",
     ex("Substitute Trustee ... located at 5177 TEST FWY Suite 1230, Houston, TX 77056, to act under") is None)
case("the courthouse sale location",
     ex("sale will be held at the north side of the Test Courts Building, facing 600 TEST STREET in Dallas, Dallas County, Texas") is None)
case("'street address' is deliberately not a trusted label",
     ex("Street Address: 500 TEST BLVD DALLAS, TX 75201") is None)
case("a label with no Texas ZIP after it", ex("Property Address: 600 TEST ST") is None)
case("an address too far from the label to belong to it",
     ex("Property Address: " + ("x " * 120) + "700 TEST ST DALLAS, TX 75001") is None)
case("a non-Texas address after the label", ex("Property Address: 800 TEST ST TULSA, OK 74101") is None)
case("empty / None", ex("") is None and ex(None) is None)

print("how it feeds the lead")


def event(**payload):
    rec = {"raw_record_id": "rid", "source_url": "u", "parser_confidence": 100,
           "raw_payload": {"doc_type": "NOTICE OF FORECLOSURE", "doc_number": "1", **payload}}
    return tr.translate_foreclosure_notices([rec])[0]["property_refs"]["situs_address"]


ocr = "Property Address: 100 TEST CT DALLAS, TX 75001 Grantor"
case("the detail-page address wins over the scanned text",
     event(property_address_detail="9 DETAIL RD DALLAS TEXAS 75001", document_body_text=ocr)
     == "9 DETAIL RD DALLAS TEXAS 75001")
case("the index-row address (street, city, state) wins over the scanned text",
     event(city="9 ROW ST, DALLAS, TEXAS", document_body_text=ocr) == "9 ROW ST, DALLAS, TEXAS")
case("scanned text is the fallback when nothing else has a street address",
     event(property_address_detail="DALLAS TEXAS", city="DALLAS", document_body_text=ocr)
     == "100 TEST CT DALLAS TX 75001")
case("no address anywhere -> None",
     event(property_address_detail="DALLAS TEXAS", city="DALLAS", document_body_text="nothing useful") is None)

print("clerk documents: a trustee's or lender's office is not the property")


def clerk_address(hint):
    rec = {"raw_record_id": "rid", "source_url": "u", "parser_confidence": 100,
           "raw_payload": {"doc_type": "APPOINTMENT OF SUBSTITUTE TRUSTEE", "doc_number": "1",
                           "grantor_name": "TEST BANK", "grantee_name": "TEST OWNER",
                           "situs_address_ocr_hint": hint}}
    return tr.translate_clerk_recordings([rec])[0]["property_refs"]["situs_address"]


case("a plain street address hint is kept", clerk_address("100 TEST CT DALLAS TX 75001") == "100 TEST CT DALLAS TX 75001")
for office in ("100 TEST BLVD, Suite 200, TX", "100 TEST RD Ste 4", "100 TEST BLVD, Mail Code 1011-F, TX",
               "100 TEST AVE, Building 5", "100 TEST ST, Floor 3", "100 TEST ST, P.O. Box 12"):
    case(f"an office hint is dropped: {office}", clerk_address(office) is None)
case("a condo unit hint is still kept (a unit is a home, a suite is an office)",
     clerk_address("100 TEST CT APT 5 DALLAS TX 75001") == "100 TEST CT APT 5 DALLAS TX 75001")
case("a street named 'FLORIDA' is not mistaken for 'FL'", clerk_address("100 FLORIDA ST DALLAS TX 75001") == "100 FLORIDA ST DALLAS TX 75001")

if FAILS:
    print(f"\n{len(FAILS)} FAILED")
    sys.exit(1)
print("\nall passed")
