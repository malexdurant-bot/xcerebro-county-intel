#!/usr/bin/env python3
"""Unit tests for the absentee / out-of-state owner derivation
(scaffold/pipeline/translators/parcel_master.py::_derive_absentee_flags).

Covers the 2026-10-08 fix: a mailing line that is the property's own street
address plus an apartment/unit number must NOT count as absentee (county
situs addresses usually omit the unit), while the address->parcel MATCHING
normalizer (_normalize_street) must stay unit-sensitive so a tenant's unit
address can never match a whole complex's parcel.

Run: python3 scaffold/tests/v5_4_0/test_absentee_flags.py
Exit 0 = pass, non-zero = fail.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scaffold.pipeline.translators.parcel_master import (
    _derive_absentee_flags,
    _normalize_street,
)

FAILS: list[str] = []


def case(name: str, ok: bool) -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILS.append(name)


def flags(situs, mailing, mailing_state="TX", situs_state="TX"):
    return _derive_absentee_flags({
        "address": situs,
        "owner_mailing_address": mailing,
        "owner_mailing_state": mailing_state,
        "situs_state": situs_state,
    })


print("absentee: same property")
case("identical street -> not absentee",
     flags("10 TEST RD", "10 TEST RD")["is_absentee_owner"] is False)
case("street suffix spelling (DR vs DRIVE) -> not absentee",
     flags("20 TEST DR", "20 TEST DRIVE")["is_absentee_owner"] is False)
case("mailing has '# 1003' unit -> not absentee",
     flags("30 TEST DR", "30 TEST DR # 1003")["is_absentee_owner"] is False)
case("mailing has 'APT 44A' unit -> not absentee",
     flags("40 TEST DR", "40 TEST DR APT 44A")["is_absentee_owner"] is False)
case("mailing has 'UNIT D7' unit -> not absentee",
     flags("50 TEST WAY", "50 TEST WAY UNIT D7")["is_absentee_owner"] is False)
case("mailing has 'STE 130' suite -> not absentee",
     flags("60 TEST AVE", "60 TEST AVE STE 130")["is_absentee_owner"] is False)
case("unit on the situs side too -> not absentee",
     flags("500 OAK ST APT 2", "500 OAK ST #2")["is_absentee_owner"] is False)

print("absentee: genuinely elsewhere")
case("different house number -> absentee",
     flags("100 MAIN ST", "200 MAIN ST")["is_absentee_owner"] is True)
case("different street -> absentee",
     flags("100 MAIN ST", "100 ELM ST")["is_absentee_owner"] is True)
case("PO box -> absentee",
     flags("70 TEST ST", "PO BOX 100")["is_absentee_owner"] is True)
case("street name beginning LOT... is not mistaken for a unit",
     flags("99 LOTUS LN", "12 LOTUS LN")["is_absentee_owner"] is True)
case("no mailing address on file -> unknown (None), not False",
     flags("100 MAIN ST", "")["is_absentee_owner"] is None)

print("out of state")
case("mailing CO vs situs TX -> out of state",
     flags("1 A ST", "5 B ST", mailing_state="CO")["is_out_of_state_owner"] is True)
case("mailing TX vs situs TX -> in state",
     flags("1 A ST", "5 B ST", mailing_state="TX")["is_out_of_state_owner"] is False)
case("lowercase mailing state still compares",
     flags("1 A ST", "5 B ST", mailing_state="il")["is_out_of_state_owner"] is True)
case("missing mailing state -> unknown (None)",
     flags("1 A ST", "5 B ST", mailing_state="")["is_out_of_state_owner"] is None)
case("missing situs state -> unknown (None)",
     flags("1 A ST", "5 B ST", situs_state="")["is_out_of_state_owner"] is None)

print("matching normalizer stays unit-sensitive (single-confident-match guard)")
case("_normalize_street('123 MAIN ST APT 4') != _normalize_street('123 MAIN ST')",
     _normalize_street("123 MAIN ST APT 4") != _normalize_street("123 MAIN ST"))
case("_normalize_street still equates ST / STREET",
     _normalize_street("123 MAIN ST") == _normalize_street("123 MAIN STREET"))

if FAILS:
    print(f"\n{len(FAILS)} FAILED")
    sys.exit(1)
print("\nall passed")
