"""
test_county_agnostic_regression.py — fail if real county/state examples leak into
universal framework files.

This test enforces the universal-shell rule: the framework outside of
user-supplied county configs must contain zero geographic references.

Exception logic (per operator confirmation):
- Word-boundary STRICT match for full geographic names and phrases:
    Ocean, Bexar, San Antonio, New Jersey, Texas, Arizona, Florida, Maricopa,
    OPRA, Public Information Act, NJ Courts, NJ DOIT
- Case-sensitive WHOLE-TOKEN match for two-letter state codes:
    NJ, TX, CA, AZ, FL, NY
  (does NOT match inside normal words like "text", "next", "case", "capture", "flag")

Exemption paths:
- config/counties/<county>.json is exempt (these are operator-supplied)
- config/counties/_template.json is NOT exempt (universal template)
- config/counties/_schema.md is NOT exempt (universal schema doc)
- config/counties/_schema.json is NOT exempt (universal schema)

Also exempts itself, the test runner, and any test fixtures that
intentionally reference these terms inside `_county_agnostic_exempt` markers.

Run with: python3 scaffold/tests/test_county_agnostic_regression.py
"""

import re
import sys
from pathlib import Path

FRAMEWORK_ROOT = Path(__file__).resolve().parent.parent.parent

# Phrases that match on word boundaries — case-insensitive
PHRASE_BLOCKLIST = [
    r"\bOcean\b",
    r"\bBexar\b",
    r"\bSan Antonio\b",
    r"\bNew Jersey\b",
    r"\bTexas\b",
    r"\bArizona\b",
    r"\bFlorida\b",
    r"\bMaricopa\b",
    r"\bOPRA\b",
    r"\bPublic Information Act\b",
    r"\bNJ Courts\b",
    r"\bNJ DOIT\b",
]

# State two-letter codes — case-sensitive whole-token match
# Match: " NJ ", "(NJ)", "NJ.", "NJ,", "NJ;", "NJ\n", "NJ\t" — but NOT inside other words
STATE_CODES = ["NJ", "TX", "CA", "AZ", "FL", "NY"]

# Files to skip entirely
def is_exempt_path(rel_path):
    """Per-operator-confirmation exemption rules."""
    rel_str = str(rel_path).replace("\\", "/")
    # County-specific configs ARE exempt — but NOT the universal templates/schemas
    if rel_str.startswith("config/counties/"):
        basename = Path(rel_str).name
        if basename in ("_template.json", "_schema.md", "_schema.json"):
            return False  # universal — must stay agnostic
        return True  # operator-supplied county config
    # Per-county run folders are exempt (launch files, manifests, operator notes)
    if rel_str.startswith("runs/"):
        return True
    # The test files themselves intentionally name these terms
    if rel_str.startswith("scaffold/tests/test_county_agnostic_regression"):
        return True
    # LICENSE.md legitimately names Texas + Bexar County, Texas in the
    # governing-law clause. This is a single legal document that defines the
    # jurisdiction where Xcerebro LLC is registered, not framework content
    # that should be county-agnostic. Exempt the whole file.
    if rel_str == "LICENSE.md":
        return True
    # v4.1.0 — START_HERE.md and scaffold/bootstrap_county.py legitimately use
    # 'Bexar County, Texas' and similar as user-facing examples to teach the
    # one-sentence install flow. The examples are explicitly framed as
    # placeholders to substitute. Concrete examples are necessary for an
    # onboarding doc — abstract `<COUNTY>` syntax loses the operator on the
    # first read. Exempt both files.
    if rel_str == "START_HERE.md":
        return True
    if rel_str == "scaffold/bootstrap_county.py":
        return True
    return False


def files_to_scan(root):
    """Walk framework looking at .md, .json, .py, .txt files."""
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in (".md", ".json", ".jsonl", ".py", ".txt"):
            continue
        rel = p.relative_to(root)
        if is_exempt_path(rel):
            continue
        yield p, rel


def scan_file(path, rel_path):
    """Return list of violations found in this file."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []

    violations = []

    # Phrase blocklist (word-boundary, case-insensitive)
    for pattern in PHRASE_BLOCKLIST:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            line_no = text[:m.start()].count("\n") + 1
            violations.append({
                "file": str(rel_path),
                "line": line_no,
                "match": m.group(),
                "rule": f"phrase_blocklist: {pattern}",
            })

    # State-code matches: case-sensitive whole-token match
    # A "token" boundary is: start/end of string, whitespace, or punctuation [.,;:()\[\]{}"'`/]
    # NOT: alphanumeric or underscore (those would make NJ part of "Injure" etc.)
    for code in STATE_CODES:
        # (?<![A-Za-z0-9_]) is a negative lookbehind for word chars
        # (?![A-Za-z0-9_]) is a negative lookahead for word chars
        # This ensures NJ matches " NJ ", "(NJ)", "NJ.", "NJ\n" — but NOT "NJSomething" or "InjureNJ"
        pattern = r"(?<![A-Za-z0-9_])" + re.escape(code) + r"(?![A-Za-z0-9_])"
        for m in re.finditer(pattern, text):
            line_no = text[:m.start()].count("\n") + 1
            line_content = text.split("\n")[line_no - 1] if line_no - 1 < len(text.split("\n")) else ""
            violations.append({
                "file": str(rel_path),
                "line": line_no,
                "match": m.group(),
                "line_content": line_content[:120],
                "rule": f"state_code_whole_token: {code}",
            })

    return violations


def run_regression():
    all_violations = []
    files_scanned = 0
    for path, rel in files_to_scan(FRAMEWORK_ROOT):
        files_scanned += 1
        all_violations.extend(scan_file(path, rel))

    print("=" * 72)
    print("COUNTY-AGNOSTIC REGRESSION TEST")
    print("=" * 72)
    print(f"Files scanned: {files_scanned}")
    print(f"Violations found: {len(all_violations)}")

    if all_violations:
        print()
        print("VIOLATIONS:")
        for v in all_violations:
            print(f"  {v['file']}:{v['line']}")
            print(f"    match: {v['match']!r}")
            print(f"    rule: {v['rule']}")
            if "line_content" in v:
                print(f"    context: {v['line_content']!r}")
            print()
        print("=" * 72)
        print(f"RESULT: FAIL — {len(all_violations)} county-specific term(s) leaked into universal files")
        print("=" * 72)
        return False
    else:
        print()
        print("=" * 72)
        print("RESULT: PASS — no county-specific terms found in universal framework files")
        print("=" * 72)
        return True


if __name__ == "__main__":
    ok = run_regression()
    sys.exit(0 if ok else 1)
