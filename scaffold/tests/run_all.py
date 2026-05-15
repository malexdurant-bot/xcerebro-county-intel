"""
run_all.py — single entry point to run all framework gate tests.

Runs:
  1. scaffold/tests/test_golden_path.py
  2. scaffold/tests/test_county_agnostic_regression.py
  3. scaffold/tests/test_write_county_config.py   (v5.1.1-beta+)
  4. scaffold/tests/test_translator_registry.py    (v5.1.2-beta+)

All must pass for the framework to be shippable. Exits 0 only when every
test exits 0. Operator-friendly output preserved from each underlying script.

Usage:
  python scaffold/tests/run_all.py

Each underlying script can still be run directly for focused work:
  python scaffold/tests/test_golden_path.py
  python scaffold/tests/test_county_agnostic_regression.py
  python scaffold/tests/test_write_county_config.py
  python scaffold/tests/test_translator_registry.py
"""

import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PYTHON = sys.executable

TESTS = [
    ("Golden path", TESTS_DIR / "test_golden_path.py"),
    ("County-agnostic regression", TESTS_DIR / "test_county_agnostic_regression.py"),
    ("Atomic county config writer (v5.1.1-beta)", TESTS_DIR / "test_write_county_config.py"),
    ("Translator registry (v5.1.2-beta)", TESTS_DIR / "test_translator_registry.py"),
]


def main():
    results = []
    for label, script in TESTS:
        print()
        print("#" * 72)
        print(f"# RUNNING: {label} — {script.name}")
        print("#" * 72)
        proc = subprocess.run([PYTHON, str(script)])
        results.append((label, script.name, proc.returncode))

    # Summary
    print()
    print("=" * 72)
    print("FRAMEWORK GATE TEST SUMMARY")
    print("=" * 72)
    any_failed = False
    for label, name, code in results:
        marker = "PASS" if code == 0 else "FAIL"
        if code != 0:
            any_failed = True
        print(f"  [{marker}] {label} ({name}) — exit code {code}")
    print("=" * 72)

    if any_failed:
        print("RESULT: FAIL — one or more gate tests failed. Framework is not shippable.")
        sys.exit(1)
    else:
        print("RESULT: PASS — all gate tests green. Framework gate satisfied.")
        sys.exit(0)


if __name__ == "__main__":
    main()
