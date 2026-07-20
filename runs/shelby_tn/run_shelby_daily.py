"""
Shelby County, TN — daily incremental pipeline runner.

Runs a full pipeline pass (scrape + enrich + score) and writes output to the
production pipeline_output/ directory.  Designed to be called by the Windows
Task Scheduler daily.

Usage:
    python runs/shelby_tn/run_shelby_daily.py

This is intentionally thin — all logic lives in run_pipeline.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_THIS_DIR = Path(__file__).parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from run_pipeline import run_pipeline  # noqa: E402

if __name__ == "__main__":
    result = run_pipeline(
        scrape=True,          # always pull fresh CSV
        verbose=False,        # suppress per-record noise in scheduled runs
        approve_needs_review=True,
    )
    # Exit non-zero on pipeline failure so Task Scheduler can detect it
    if result.get("lead_count", 0) == 0 and result.get("semantic_verdict") not in ("DEPLOY_OK", "NEEDS_OPERATOR_REVIEW"):
        sys.exit(1)
    sys.exit(0)
