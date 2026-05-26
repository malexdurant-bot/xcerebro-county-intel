#!/usr/bin/env python3
"""v5.5.0 §S1 / §S2 invariants — recon-protocol + access-ladder docs.

Pins that the canonical knowledge_base docs carry the v5.5.0 hardening
sections. Per §0.2, every canon claim must ship with at least one of:
executable code, schema enforcement, invariant test, scanner, or
verification script. These two sections (§1 hard recon, §2 access ladder)
ship as docs PLUS:
  - §1.5 role taxonomy → records.SOURCE_ROLES + the source-roles test
  - §2.1 Playwright as standard tooling → the access-ladder enum in
    BLOCKED_SOURCE's punch-list field (verify_live_contract +
    refresh_verification_gate consume the role distinction)

This test is the doc-presence invariant; the code-side proofs live in the
sibling test files.

Run: python3 scaffold/tests/v5_5_0/test_recon_and_access_docs_present.py
Exit 0 = pass, non-zero = fail.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _norm(text: str) -> str:
    """Lowercase + drop backticks so phrase checks ignore markdown emphasis."""
    return text.replace("`", "").lower()


def main() -> int:
    checks: list = []

    def check(label, ok, detail=""):
        checks.append(("PASS" if ok else "FAIL", label, detail))

    # =====================================================================
    # §S1 — recon protocol
    # =====================================================================
    recon_doc = REPO_ROOT / "knowledge_base" / "protocols" / "01_county_recon.md"
    check("§S1 recon protocol doc exists",
          recon_doc.is_file(),
          f"missing: {recon_doc}")
    if recon_doc.is_file():
        text = _norm(recon_doc.read_text(encoding="utf-8"))
        for phrase in (
            "01.27 v5.5.0 hard-recon protocol",
            "§1.1 — exhaustive primary-source catalog",
            "tax collector",
            "property appraiser",
            "§1.2 — enrichment source catalog",
            "§1.3 — hunt beyond the dossier",
            "§1.4 — historical instances",
            "§1.5 — classification with the v5.5.0 8-role taxonomy",
            "primary_default_source",
            "primary_owner_status_source",
            "rejected_source",
            "§1.6 — missed-source audit",
        ):
            check(f"§S1 recon doc carries: {phrase!r}",
                  phrase.lower() in text)

    # =====================================================================
    # §S2 — access ladder
    # =====================================================================
    access_doc = REPO_ROOT / "knowledge_base" / "engineering" / "04_blocked_source_strategies.md"
    check("§S2 access-ladder doc exists", access_doc.is_file())
    if access_doc.is_file():
        text = _norm(access_doc.read_text(encoding="utf-8"))
        for phrase in (
            "v5.5.0 access ladder",
            "§2.1 — playwright is standard authorized tooling",
            "§2.2 — access ladder",
            "stdlib http",
            "playwright headless",
            "playwright + stealth",
            "operator-seeded session",
            "§2.3 — ci considerations",
            "xvfb-run",
            "continue-on-error",
            "§2.4 — recon-before-scraper",
            "§2.5 — reuse proven operator code",
        ):
            check(f"§S2 access doc carries: {phrase!r}",
                  phrase.lower() in text)

    # =====================================================================
    # §S2 — code-side proof (SOURCE_ROLES carries the 8-role v5.5.0 taxonomy)
    # =====================================================================
    from scaffold.pipeline.contracts import records
    for role in (
        "PRIMARY_DEFAULT_SOURCE",
        "PRIMARY_OWNER_STATUS_SOURCE",
        "REJECTED_SOURCE",
    ):
        check(f"§S2 code-side: contracts.records.SOURCE_ROLES carries {role!r}",
              role in records.SOURCE_ROLES)

    # --- Report -----------------------------------------------------------
    failed = [c for c in checks if c[0] == "FAIL"]
    for s, l, d in checks:
        print(f"  [{s}] {l}")
        if s == "FAIL" and d:
            print(f"         detail: {d}")
    if failed:
        print(f"FAIL: recon + access-ladder docs — {len(failed)}/"
              f"{len(checks)} checks failed")
        return 1
    print(f"PASS: §S1 / §S2 recon + access docs (v5.5.0) — all "
          f"{len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
