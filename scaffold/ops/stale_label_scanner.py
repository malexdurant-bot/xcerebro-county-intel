"""
stale_label_scanner — v5.5.0 §5.8 / §5.11 stale-label invariant.

Renderer scanner that FAILS if universal templates or dashboard scaffolds
carry foreign county tokens. Operationally, this catches the "El Paso
labels leaked into the Smith County dashboard" class of bug — a county
adapter that copied a template from another county and forgot to update
the labels.

What this scanner enforces (canon per §5.8 / §5.11):

  - Universal framework dashboard files (scaffold/dashboard templates,
    dashboard.js / dashboard.html / dashboard.css when present at the
    repo root in the universal layer) MUST contain ZERO county tokens.
  - A county's own dashboard (dashboard/ in a county dir) MAY contain
    its own county name, but NEVER a foreign county's name.

  Foreign county tokens scanned for:

    EPCAD, El Paso, Bexar, Duval, Greene, Smith, Ocean, Maricopa, Pima,
    San Antonio, Jacksonville, Tyler, Catskill, Houston, Phoenix,
    Cleveland, Cuyahoga, New Hanover.

This module is universal framework code: it contains the token list (these
are the names being SCANNED FOR — they're the rule, not literal
county-specific examples). The county-agnostic regression scanner
explicitly exempts this file (it's the implementation of the rule, by the
same exemption pattern that exempts test_county_agnostic_regression.py).

Usage:
    python3 scaffold/ops/stale_label_scanner.py [--county <slug>] [<path>]

When --county is supplied, the scanner allows the supplied slug's tokens
in the scanned files (a county's own labels stay). When --county is not
supplied (universal-layer mode), no county tokens are allowed.

Exit codes:
    0 — no foreign-county tokens found
    1 — foreign-county tokens detected
    2 — usage / scan error
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


# v5.5.0 §5.11 — the foreign-county token registry. Each entry is a regex
# (with word boundaries where appropriate). The scanner walks each
# included file and reports any match it finds.
FOREIGN_COUNTY_TOKENS: dict[str, re.Pattern] = {
    "Bexar":         re.compile(r"\bBexar\b", re.IGNORECASE),
    "Duval":         re.compile(r"\bDuval\b", re.IGNORECASE),
    "Greene":        re.compile(r"\bGreene\b", re.IGNORECASE),
    "Smith":         re.compile(r"\bSmith\s+County\b", re.IGNORECASE),
    "Ocean":         re.compile(r"\bOcean\s+County\b", re.IGNORECASE),
    "Maricopa":      re.compile(r"\bMaricopa\b", re.IGNORECASE),
    "Pima":          re.compile(r"\bPima\b", re.IGNORECASE),
    "El Paso":       re.compile(r"\bEl\s+Paso\b", re.IGNORECASE),
    "EPCAD":         re.compile(r"\bEPCAD\b"),
    "San Antonio":   re.compile(r"\bSan\s+Antonio\b", re.IGNORECASE),
    "Jacksonville":  re.compile(r"\bJacksonville\b", re.IGNORECASE),
    "Tyler":         re.compile(r"\bTyler\s+County\b", re.IGNORECASE),
    "Catskill":      re.compile(r"\bCatskill\b", re.IGNORECASE),
    "Houston":       re.compile(r"\bHouston\s+County\b", re.IGNORECASE),
    "Phoenix":       re.compile(r"\bPhoenix\b", re.IGNORECASE),
    "Cleveland":     re.compile(r"\bCleveland\b", re.IGNORECASE),
    "Cuyahoga":      re.compile(r"\bCuyahoga\b", re.IGNORECASE),
    "New Hanover":   re.compile(r"\bNew\s+Hanover\b", re.IGNORECASE),
    "Bexar AD":      re.compile(r"\bBCAD\b"),
    "Harris AD":     re.compile(r"\bHCAD\b"),
    "Dallas AD":     re.compile(r"\bDCAD\b"),
}


SCANNABLE_EXTENSIONS: frozenset[str] = frozenset({
    ".js", ".html", ".css", ".json", ".md", ".txt", ".jsx", ".ts", ".tsx",
})

# Always-skip directory names — virtualenvs, vendored packages, etc.
EXCLUDED_DIR_NAMES: frozenset[str] = frozenset({
    ".venv", "venv", "site-packages", "node_modules", ".git",
    "__pycache__", ".pytest_cache", ".mypy_cache",
    "dist", "build", ".tox", ".eggs",
})


def scan_paths(
    roots: list[Path], *, current_county_slug: str | None = None,
) -> list[dict]:
    """Walk `roots` and report any foreign-county-token hits.

    Args:
      roots: file or directory paths to scan.
      current_county_slug: when supplied (e.g. "duval_fl"), the corresponding
        county-name pattern is REMOVED from the scan — a county may carry
        its own name. Without a slug, all tokens are flagged (universal
        layer).
    """
    patterns = dict(FOREIGN_COUNTY_TOKENS)
    if current_county_slug:
        # Map county slugs to their token keys to remove from the scan.
        slug_to_keys = {
            "bexar_tx":   {"Bexar", "Bexar AD", "San Antonio"},
            "duval_fl":   {"Duval", "Jacksonville"},
            "greene_ny":  {"Greene", "Catskill"},
            "smith_tx":   {"Smith", "Tyler"},
            "ocean_nj":   {"Ocean"},
            "maricopa_az": {"Maricopa", "Phoenix"},
            "pima_az":    {"Pima"},
            "el_paso_tx": {"El Paso", "EPCAD"},
        }
        for k in slug_to_keys.get(current_county_slug, set()):
            patterns.pop(k, None)

    violations: list[dict] = []
    for root in roots:
        if root.is_file():
            violations.extend(_scan_file(root, patterns))
            continue
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if any(part in EXCLUDED_DIR_NAMES for part in p.parts):
                continue
            if p.suffix.lower() not in SCANNABLE_EXTENSIONS:
                continue
            violations.extend(_scan_file(p, patterns))
    return violations


def _scan_file(path: Path, patterns: dict[str, re.Pattern]) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    out: list[dict] = []
    for token, regex in patterns.items():
        for m in regex.finditer(text):
            line_no = text[:m.start()].count("\n") + 1
            line_text = text.split("\n")[line_no - 1][:160]
            out.append({
                "file": str(path),
                "line": line_no,
                "token": token,
                "match": m.group(0),
                "line_text": line_text,
            })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="v5.5.0 §5.11 stale-label scanner — flag foreign county "
                    "tokens in dashboard / template files.",
    )
    parser.add_argument(
        "paths", nargs="*", default=["dashboard", "scaffold/dashboard"],
        help="Files or directories to scan. Defaults to dashboard/ and "
             "scaffold/dashboard/ (when present).",
    )
    parser.add_argument(
        "--county", default=None,
        help="The current county slug (e.g. duval_fl) — the scanner allows "
             "this county's own tokens. Omit for universal-layer scans.",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Only print summary line + exit code.",
    )
    args = parser.parse_args()

    roots = [Path(p) for p in args.paths if Path(p).exists()]
    if not roots:
        print("(stale_label_scanner: no scannable paths supplied — exit 0)")
        return 0
    violations = scan_paths(roots, current_county_slug=args.county)

    if not args.quiet:
        if violations:
            print("STALE-LABEL SCANNER (v5.5.0 §5.11)")
            print(f"  county scope:  {args.county or '<universal>'}")
            print(f"  scanned roots: {[str(r) for r in roots]}")
            print(f"  violations:    {len(violations)}")
            for v in violations[:50]:
                print(f"    [{v['token']}] {v['file']}:{v['line']}  "
                      f"-> {v['line_text']!r}")
            if len(violations) > 50:
                print(f"    ... and {len(violations) - 50} more")
        else:
            print(f"STALE-LABEL SCANNER (v5.5.0 §5.11): PASS — "
                  f"no foreign-county tokens in "
                  f"{[str(r) for r in roots]} "
                  f"(scope: {args.county or '<universal>'})")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
