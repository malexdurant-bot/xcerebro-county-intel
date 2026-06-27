"""
Unit tests for superior_court_probate_detail_maricopa.parse_probate_detail_html.

Uses synthetic HTML fixtures that mirror the live page structure confirmed by
probe on 2026-06-27 (Bootstrap div layout, #tblForms / #tblForms2 / #tblForms4).

Run standalone:
    python -X utf8 runs/maricopa_az/test_probate_detail_parser.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scrapers.superior_court_probate_detail_maricopa import (
    parse_probate_detail_html,
    _parse_date,
    _infer_subtype,
    _strip_html,
)

# ---------------------------------------------------------------------------
# HTML fixtures — mirror live page structure without real data
# ---------------------------------------------------------------------------

def _make_page(
    case_number: str = "PB2024-001000",
    case_type: str = "Probate",
    judge: str = "Smith, Jane",
    location: str = "Downtown",
    plaintiff: str = "Test Testable",
    parties: list[tuple] = (),  # (name, relationship, sex, attorney)
    documents: list[tuple] = (),  # (filing_date, description, docket_date, filing_party)
) -> str:
    """Build synthetic caseInfo.asp HTML matching the live page structure."""

    def party_row(name: str, rel: str, sex: str, atty: str) -> str:
        return f"""
        <div class="row g-0  pt-3 pb-3" id="tblForms3">
            <div class="col-4 m-visibility bold-font">Party Name</div>
            <div class="col-8 col-lg-4">{name}</div>
            <div class="col-4 m-visibility bold-font">Relationship</div>
            <div class="col-8 col-lg-3">{rel}</div>
            <div class="col-4 m-visibility bold-font">Sex</div>
            <div class="col-8 col-lg-2">{sex}</div>
            <div class="col-4 m-visibility bold-font">Attorney</div>
            <div class="col-8 col-lg-3">{atty}</div>
        </div>"""

    def doc_row(fd: str, desc: str, dd: str, fp: str) -> str:
        return f"""
        <div class="row g-0">
            <div class="col-4 m-visibility bold-font">Filing Date</div>
            <div class="col-8 col-lg-2">{fd}</div>
            <div class="col-4 m-visibility bold-font">Description</div>
            <div class="col-8 col-lg-5">{desc}</div>
            <div class="col-4 m-visibility bold-font">Docket Date</div>
            <div class="col-8 col-lg-2">{dd}</div>
            <div class="col-4 m-visibility bold-font">Filing Party</div>
            <div class="col-8 col-lg-3">{fp}</div>
        </div>"""

    party_html = "\n".join(party_row(*p) for p in parties)
    doc_html = "\n".join(doc_row(*d) for d in documents)

    return f"""<!DOCTYPE html><html><head><title>Probate</title></head><body>
    <div id="tblForms" class="zebraRowTable grid-um no-zebra">
        <div class="">Case Information</div>
        <div class="row g-0 py-0">
            <div class="col-4 col-lg-3 bold-font">Case Number:</div>
            <div class="col-8 col-lg-3">{case_number}</div>
            <div class="col-4 col-lg-3 bold-font">Judge:</div>
            <div class="col-8 col-lg-3">{judge}</div>
        </div>
        <div class="row g-0 py-0">
            <div class="col-4 col-lg-3 bold-font">Plaintiff</div>
            <div class="col-8 col-lg-3">{plaintiff}</div>
            <div class="col-4 col-lg-3 bold-font">Location:</div>
            <div class="col-8 col-lg-3">{location}</div>
        </div>
        <div class="row g-0 py-0">
            <div class="col-4 col-lg-3 bold-font">Case Type:</div>
            <div class="col-8 col-lg-3">{case_type}</div>
        </div>
    </div>
    <div id="tblForms2" class="zebraRowTable grid-um">
        <div class="col-12">Party Information</div>
        <div class="row g-0 d-visibility rptr-header">
            <div class="col-4 col-lg-4">Party Name</div>
            <div class="col-8 col-lg-3">Relationship</div>
        </div>
        {party_html}
    </div>
    <div id="tblForms4" class="zebraRowTable grid-um">
        <div class="row g-0"><div class="col-12">Case Documents</div></div>
        <div class="row g-0 d-visibility rptr-header">
            <div class="col-6 col-lg-2">Filing Date</div>
            <div class="col-6 col-lg-5">Description</div>
        </div>
        {doc_html}
    </div>
    </body></html>"""


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

_passed = 0
_failed = 0


def _assert(cond: bool, msg: str) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {msg}")
    else:
        _failed += 1
        print(f"  FAIL  {msg}")


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


def test_estate_letters_testamentary() -> None:
    """Estate case: Decedent + Petitioner + Letters Testamentary doc."""
    html = _make_page(
        case_number="PB2024-001001",
        case_type="Probate",
        parties=[
            ("John Doe", "Decedent", "Male", "Pro Per"),
            ("Jane Doe", "Petitioner", "Female", "Pro Per"),
            ("Jane Doe", "Personal Representative", "Female", "Smith, Robert"),
        ],
        documents=[
            ("1/15/2024", "Petition for Letters Testamentary", "1/16/2024", "Petitioner(1)"),
            ("2/20/2024", "Letters Testamentary", "2/21/2024", "Court"),
        ],
    )
    r = parse_probate_detail_html(html)
    _assert(r["case_number"] == "PB2024-001001", "case_number parsed")
    _assert(r["case_type_raw"] == "Probate", "case_type_raw parsed")
    _assert(r["decedent_name"] == "John Doe", "decedent_name extracted")
    _assert(r["petitioner_name"] == "Jane Doe", "petitioner_name extracted")
    _assert(r["personal_representative"] == "Jane Doe", "personal_representative extracted")
    _assert(r["is_estate_case"] is True, "is_estate_case=True")
    _assert(r["is_noise_case"] is False, "is_noise_case=False")
    _assert(r["case_subtype_inferred"] == "letters_testamentary", "subtype=letters_testamentary")
    _assert(r["earliest_filing_date"] == "2024-01-15", "earliest_filing_date correct")
    _assert(len(r["document_descriptions"]) == 2, "2 document descriptions")
    _assert(r["property_address"] is None, "property_address=None (not on page)")
    _assert(r["apn"] is None, "apn=None (not on page)")


def test_estate_letters_of_administration() -> None:
    """Estate case without will: Letters of Administration."""
    html = _make_page(
        case_number="PB2023-005000",
        case_type="Probate",
        parties=[
            ("Maria Gomez", "Decedent", "Female", ""),
            ("Carlos Gomez", "Petitioner", "Male", ""),
        ],
        documents=[
            ("3/1/2023", "Petition for Letters of Administration", "3/2/2023", "Petitioner(1)"),
        ],
    )
    r = parse_probate_detail_html(html)
    _assert(r["decedent_name"] == "Maria Gomez", "decedent extracted")
    _assert(r["is_estate_case"] is True, "is_estate_case=True")
    _assert(r["case_subtype_inferred"] == "letters_of_administration", "subtype=letters_of_administration")


def test_determination_of_heirship() -> None:
    """Determination of Heirship case."""
    html = _make_page(
        case_number="PB2022-008000",
        case_type="Probate",
        parties=[
            ("Robert Johnson", "Decedent", "Male", ""),
            ("Alice Johnson", "Petitioner", "Female", ""),
        ],
        documents=[
            ("6/10/2022", "Petition for Determination of Heirship", "6/11/2022", "Petitioner(1)"),
        ],
    )
    r = parse_probate_detail_html(html)
    _assert(r["decedent_name"] == "Robert Johnson", "decedent extracted")
    _assert(r["case_subtype_inferred"] == "determination_of_heirship", "subtype=determination_of_heirship")


def test_guardianship_is_noise() -> None:
    """Guardianship case: no Decedent — is_noise_case=True."""
    html = _make_page(
        case_number="PB2024-002000",
        case_type="Guardianship",
        parties=[
            ("Party is a minor: name not published", "Minor", "Female", "Pro Per"),
            ("Guardian Person", "Petitioner", "Male", "Pro Per"),
        ],
        documents=[
            ("4/1/2024", "Petition for Appointment of Guardian", "4/2/2024", "Petitioner(1)"),
        ],
    )
    r = parse_probate_detail_html(html)
    _assert(r["decedent_name"] is None, "no decedent in guardianship case")
    _assert(r["is_noise_case"] is True, "is_noise_case=True for Guardianship")
    _assert(r["is_estate_case"] is False, "is_estate_case=False for Guardianship")
    # Protected minor placeholder should not appear as a party
    _assert(
        all(p["name"] != "Party is a minor: name not published" for p in r["parties"]),
        "protected minor placeholder stripped from parties",
    )


def test_conservatorship_is_noise() -> None:
    """Conservatorship case: also noise."""
    html = _make_page(
        case_number="PB2023-009000",
        case_type="Conservatorship",
        parties=[
            ("Ward Person", "Protected Person", "Male", ""),
        ],
        documents=[
            ("5/5/2023", "Petition for Appointment of Conservator", "5/6/2023", "Petitioner(1)"),
        ],
    )
    r = parse_probate_detail_html(html)
    _assert(r["is_noise_case"] is True, "is_noise_case=True for Conservatorship")
    _assert(r["is_estate_case"] is False, "is_estate_case=False")


def test_no_decedent_defaults_review_required() -> None:
    """Probate case type but no Decedent in parties → review_required."""
    html = _make_page(
        case_number="PB2021-003000",
        case_type="Probate",
        parties=[
            ("Some Petitioner", "Petitioner", "Male", ""),
        ],
        documents=[
            ("8/1/2021", "Petition for Probate", "8/2/2021", "Petitioner(1)"),
        ],
    )
    r = parse_probate_detail_html(html)
    _assert(r["decedent_name"] is None, "no decedent found")
    _assert(r["is_estate_case"] is False, "not an estate case without decedent")
    _assert(r["is_noise_case"] is False, "not noise (case_type is Probate)")


def test_multiple_document_dates_min_selected() -> None:
    """Earliest filing date should be the minimum across all documents."""
    html = _make_page(
        case_number="PB2020-000001",
        case_type="Probate",
        parties=[("Decedent X", "Decedent", "Male", "")],
        documents=[
            ("6/15/2021", "Order", "6/16/2021", "Court"),
            ("3/1/2020", "Petition for Letters Testamentary", "3/2/2020", "Petitioner"),
            ("12/1/2020", "Notice to Creditors", "12/2/2020", "PR"),
        ],
    )
    r = parse_probate_detail_html(html)
    _assert(r["earliest_filing_date"] == "2020-03-01", "min filing date selected")
    _assert(len(r["document_descriptions"]) == 3, "all 3 descriptions captured")


def test_executor_role_maps_to_pr() -> None:
    """Executor relationship is mapped to personal_representative."""
    html = _make_page(
        case_number="PB2024-007000",
        case_type="Probate",
        parties=[
            ("Deceased Person", "Decedent", "Female", ""),
            ("Named Executor", "Executor", "Male", ""),
        ],
        documents=[("1/1/2024", "Letters Testamentary", "1/2/2024", "Court")],
    )
    r = parse_probate_detail_html(html)
    _assert(r["personal_representative"] == "Named Executor", "Executor maps to personal_representative")


def test_administrator_role_maps_to_pr() -> None:
    """Administrator relationship is mapped to personal_representative."""
    html = _make_page(
        case_number="PB2024-008000",
        case_type="Probate",
        parties=[
            ("Late Person", "Decedent", "Male", ""),
            ("Admin Person", "Administrator", "Female", ""),
        ],
        documents=[("2/1/2024", "Letters of Administration", "2/2/2024", "Court")],
    )
    r = parse_probate_detail_html(html)
    _assert(r["personal_representative"] == "Admin Person", "Administrator maps to personal_representative")
    _assert(r["case_subtype_inferred"] == "letters_of_administration", "admin subtype inferred from docs")


def test_empty_page_no_crash() -> None:
    """Empty / minimal HTML should not crash the parser."""
    r = parse_probate_detail_html("<html><body></body></html>")
    _assert(r["case_number"] is None, "empty page: case_number=None")
    _assert(r["decedent_name"] is None, "empty page: decedent=None")
    _assert(r["is_estate_case"] is False, "empty page: is_estate_case=False")
    _assert(len(r["parse_errors"]) > 0, "empty page: parse_errors populated")


def test_parse_date_formats() -> None:
    """_parse_date handles M/D/YYYY and M/DD/YYYY."""
    _assert(_parse_date("1/5/2024") == "2024-01-05", "parse 1/5/2024")
    _assert(_parse_date("12/31/2023") == "2023-12-31", "parse 12/31/2023")
    _assert(_parse_date("3/29/2018") == "2018-03-29", "parse 3/29/2018")
    _assert(_parse_date("") is None, "parse empty string → None")
    _assert(_parse_date("not-a-date") is None, "parse garbage → None")


def test_infer_subtype_from_descriptions() -> None:
    """_infer_subtype correctly identifies known document types."""
    _assert(
        _infer_subtype(["Petition for Letters Testamentary"]) == "letters_testamentary",
        "testamentary from petition description",
    )
    _assert(
        _infer_subtype(["Letters Testamentary"]) == "letters_testamentary",
        "testamentary from letters description",
    )
    _assert(
        _infer_subtype(["Petition for Letters of Administration"]) == "letters_of_administration",
        "administration from description",
    )
    _assert(
        _infer_subtype(["Petition for Determination of Heirship"]) == "determination_of_heirship",
        "heirship from description",
    )
    _assert(
        _infer_subtype(["Muniment of Title"]) == "muniment_of_title",
        "muniment from description",
    )
    _assert(
        _infer_subtype(["ROF - Receipt Of Funds", "005 - ME: Hearing"]) == "letters_testamentary",
        "generic docs default to letters_testamentary",
    )


def test_strip_html_entity_decoding() -> None:
    """_strip_html removes tags and decodes entities."""
    _assert(_strip_html("<b>Hello &amp; World</b>") == "Hello & World", "entity decode &amp;")
    _assert(_strip_html("A&nbsp;B") == "A B", "entity decode &nbsp;")
    _assert(_strip_html("<div>  spaced  </div>") == "spaced", "collapse whitespace")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    print("=== test_probate_detail_parser ===")
    print()

    tests = [
        test_estate_letters_testamentary,
        test_estate_letters_of_administration,
        test_determination_of_heirship,
        test_guardianship_is_noise,
        test_conservatorship_is_noise,
        test_no_decedent_defaults_review_required,
        test_multiple_document_dates_min_selected,
        test_executor_role_maps_to_pr,
        test_administrator_role_maps_to_pr,
        test_empty_page_no_crash,
        test_parse_date_formats,
        test_infer_subtype_from_descriptions,
        test_strip_html_entity_decoding,
    ]

    for test_fn in tests:
        print(f"[{test_fn.__name__}]")
        test_fn()
        print()

    total = _passed + _failed
    print(f"Results: {_passed}/{total} passed  ({_failed} failed)")
    if _failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
