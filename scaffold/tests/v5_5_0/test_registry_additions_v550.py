#!/usr/bin/env python3
"""v5.5.0 §4.5 invariants — three new canonical_doc_types.

Three judgment / foreclosure-cycle doc types added in v5.5.0:

  - civil_judgment        — upstream court money judgment; debtor-only;
                            source_class = review_required until property
                            attachment is proven (§3.6 doc-type honesty).
  - abstract_of_judgment  — the abstract-of-judgment filing; creates a
                            property lien when filed in the recording
                            county where the debtor owns real property;
                            review_required until property attachment is
                            proven; with proof, downstream classifies it
                            as JUDGMENT_LIEN.
  - certificate_of_title  — FL post-foreclosure title instrument (the
                            sheriff_deed / trustees_deed_upon_sale family);
                            POST_SALE_TITLE_EVENT per §3.9; lead_generating.

The test pins:

  - all three registered in canonical_doc_types.json with the right
    source_class / lead_pattern / subtype;
  - all three bridged in doc_type_bridge.REGISTRY_TO_LEAD_TYPE;
  - civil_judgment + abstract_of_judgment carry source_class
    'review_required' (§3.6);
  - certificate_of_title carries source_class 'lead_generating' and
    lead_pattern 'foreclosure';
  - the Abstract-of-Judgment shared-registry-mapping carve-out is GONE
    (abstract_of_judgment now has a first-class registry entry).

Run: python3 scaffold/tests/v5_5_0/test_registry_additions_v550.py
Exit 0 = pass, non-zero = fail.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scaffold.pipeline import doc_type_bridge
from scaffold.pipeline.normalize import CANONICAL

NEW_TYPES = ("CIVIL_JUDGMENT", "ABSTRACT_OF_JUDGMENT", "CERTIFICATE_OF_TITLE")


def main() -> int:
    checks: list = []

    def check(label, ok, detail=""):
        checks.append(("PASS" if ok else "FAIL", label, detail))

    # --- Registry registration -------------------------------------------
    for k in NEW_TYPES:
        check(f"registry: {k} is registered in canonical_doc_types.json",
              k in CANONICAL, f"keys present: {list(CANONICAL.keys())[:5]} …")

    # --- §3.6 doc-type honesty — judgment-family source_class --------------
    check("§3.6: CIVIL_JUDGMENT source_class is 'review_required' "
          "(upstream court order — no property attachment until proven)",
          CANONICAL.get("CIVIL_JUDGMENT", {}).get("source_class")
          == "review_required",
          f"got {CANONICAL.get('CIVIL_JUDGMENT', {}).get('source_class')!r}")
    check("§3.6: ABSTRACT_OF_JUDGMENT source_class is 'review_required' "
          "(the abstract is debtor-only until tied to real property)",
          CANONICAL.get("ABSTRACT_OF_JUDGMENT", {}).get("source_class")
          == "review_required",
          f"got {CANONICAL.get('ABSTRACT_OF_JUDGMENT', {}).get('source_class')!r}")
    check("§3.6: JUDGMENT_LIEN remains 'lead_generating' (the property-"
          "attached lien instrument — distinct from the upstream docket "
          "items)",
          CANONICAL.get("JUDGMENT_LIEN", {}).get("source_class")
          == "lead_generating")

    # --- §4.5: certificate_of_title is the FL post-foreclosure instrument -
    cot = CANONICAL.get("CERTIFICATE_OF_TITLE", {})
    check("§4.5: CERTIFICATE_OF_TITLE source_class is 'lead_generating'",
          cot.get("source_class") == "lead_generating",
          f"got {cot.get('source_class')!r}")
    check("§4.5: CERTIFICATE_OF_TITLE lead_pattern is 'foreclosure' "
          "(post-sale conclusion of the foreclosure lifecycle)",
          cot.get("lead_pattern") == "foreclosure")
    check("§4.5: CERTIFICATE_OF_TITLE common_abbreviations include 'COT'",
          "COT" in (cot.get("common_abbreviations") or []))

    # --- Bridge mapping ---------------------------------------------------
    bridge = doc_type_bridge.REGISTRY_TO_LEAD_TYPE
    check("bridge: civil_judgment → 'Civil Judgment'",
          bridge.get("civil_judgment") == "Civil Judgment")
    check("bridge: abstract_of_judgment → 'Abstract of Judgment' "
          "(now a first-class §16 lead_type — no longer shared via "
          "judgment_lien per the v5.5.0 promotion)",
          bridge.get("abstract_of_judgment") == "Abstract of Judgment")
    check("bridge: certificate_of_title → 'Foreclosure' "
          "(post-sale title event in the foreclosure family)",
          bridge.get("certificate_of_title") == "Foreclosure")

    # --- The Session-8 shared-mapping carve-out for Abstract of Judgment
    # is GONE now that the registry covers it directly.
    check("bridge: LEAD_TYPES_SHARED_REGISTRY_MAPPING no longer carries "
          "'Abstract of Judgment' (it has its own registry entry now)",
          "Abstract of Judgment" not in
          doc_type_bridge.LEAD_TYPES_SHARED_REGISTRY_MAPPING)

    # --- Bridge totality unaffected ---------------------------------------
    report = doc_type_bridge.bridge_totality_report()
    check("totality: registry_missing_from_bridge is still empty",
          report["registry_missing_from_bridge"] == [])
    check("totality: registry_total is 77 (74 from v5.4.0 + 3 from v5.5.0 §4.5)",
          report["registry_total"] == 77)
    check("totality: 'Civil Judgment' AND 'Abstract of Judgment' both "
          "appear in lead_types_with_registry",
          "Civil Judgment" in report["lead_types_with_registry"]
          and "Abstract of Judgment" in report["lead_types_with_registry"])
    check("totality: 'Tax Delinquency' is still the only §16 lead_type "
          "without a registry mapping (unchanged — it is a tax-roll status, "
          "not a recorded document)",
          report["lead_types_without_registry"] == ["Tax Delinquency"])

    # --- End-to-end bridge composition ------------------------------------
    check("end-to-end: monolith UPPERCASE CERTIFICATE_OF_TITLE → "
          "lowercased certificate_of_title",
          doc_type_bridge.monolith_to_registry("CERTIFICATE_OF_TITLE")
          == "certificate_of_title")
    check("end-to-end: CERTIFICATE_OF_TITLE → §16 Foreclosure",
          doc_type_bridge.lead_type_for_monolith_output("CERTIFICATE_OF_TITLE")
          == "Foreclosure")
    check("end-to-end: ABSTRACT_OF_JUDGMENT → §16 Abstract of Judgment",
          doc_type_bridge.lead_type_for_monolith_output("ABSTRACT_OF_JUDGMENT")
          == "Abstract of Judgment")

    failed = [c for c in checks if c[0] == "FAIL"]
    for s, l, d in checks:
        print(f"  [{s}] {l}")
        if s == "FAIL" and d:
            print(f"         detail: {d}")
    if failed:
        print(f"FAIL: v5.5.0 registry additions — {len(failed)}/"
              f"{len(checks)} checks failed")
        return 1
    print(f"PASS: v5.5.0 §4.5 registry additions — all {len(checks)} "
          f"checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
