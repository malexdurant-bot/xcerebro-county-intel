#!/usr/bin/env python3
"""v5.5.0 §6 invariants — daily-refresh publish gate + bounded pull windows.

Pins:
  - §6.4 publish gate: PUBLISH on healthy board; DO_NOT_PUBLISH on empty,
    on owner-resolution miss, on address-resolution miss, on actionable
    floor miss.
  - §6.6 bounded pull windows: scheduled-event types → FORWARD window;
    recorded-event types → SHORT BACKWARD window; status types → no window.
  - §6.4 enrichment-join-unavailable override skips the owner/address
    floors.

Run: python3 scaffold/tests/v5_5_0/test_refresh_verification_gate.py
Exit 0 = pass, non-zero = fail.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scaffold.ops import refresh_verification_gate as gate


def _scored(**overrides):
    base = {
        "scored_lead_id": "sl",
        "lead_id": "lead",
        "owner_name": "TEST_OWNER",
        "owner_type": "INDIVIDUAL",
        "lead_status": "APPROVED_FOR_DASHBOARD",
        "parcel_display": {"situs_address": "100 Synthetic Lane"},
    }
    base.update(overrides)
    return base


def main() -> int:
    checks: list = []

    def check(label, ok, detail=""):
        checks.append(("PASS" if ok else "FAIL", label, detail))

    # =====================================================================
    # §6.4 publish gate
    # =====================================================================
    # Healthy board → PUBLISH.
    r = gate.verify_refresh_publishable([_scored(), _scored(lead_id="b")])
    check("§6.4 healthy board (2 leads, both known owner + address) → PUBLISH",
          r.verdict == "PUBLISH",
          f"got {r.verdict!r}: {r.reason!r}")

    # Empty → DO_NOT_PUBLISH.
    r = gate.verify_refresh_publishable([])
    check("§6.4 empty scored_leads → DO_NOT_PUBLISH",
          r.verdict == "DO_NOT_PUBLISH"
          and "lead_count" in (r.reason or ""))

    # All unknown owners → DO_NOT_PUBLISH (dead board).
    r = gate.verify_refresh_publishable([
        _scored(owner_name="hospital_lien against unidentified party"),
        _scored(owner_name="UNKNOWN", lead_id="b"),
    ])
    check("§6.4 all-Unknown owners → DO_NOT_PUBLISH "
          "(dead-board floor)",
          r.verdict == "DO_NOT_PUBLISH"
          and "owner" in (r.reason or "").lower())

    # All UNENRICHED / no address → DO_NOT_PUBLISH (enrichment failed).
    r = gate.verify_refresh_publishable([
        _scored(parcel_display=None),
        _scored(parcel_display=None, lead_id="b"),
    ])
    check("§6.4 all leads missing property_full_address → DO_NOT_PUBLISH "
          "(address-resolution floor)",
          r.verdict == "DO_NOT_PUBLISH"
          and "address" in (r.reason or "").lower())

    # Actionable floor — all REVIEW_REQUIRED → DO_NOT_PUBLISH.
    r = gate.verify_refresh_publishable([
        _scored(lead_status="REVIEW_REQUIRED"),
        _scored(lead_status="REVIEW_REQUIRED", lead_id="b"),
    ])
    check("§6.4 all leads REVIEW_REQUIRED → DO_NOT_PUBLISH "
          "(actionable floor)",
          r.verdict == "DO_NOT_PUBLISH"
          and "actionable" in (r.reason or "").lower())

    # enrichment_join_unavailable override → owner/address floors skipped.
    r = gate.verify_refresh_publishable(
        [_scored(parcel_display=None, owner_name="UNKNOWN")],
        enrichment_join_unavailable=True,
    )
    check("§6.4 enrichment_join_unavailable=True override → "
          "PUBLISH despite missing owner/address (§4.3 carve-out)",
          r.verdict == "PUBLISH"
          and any("override" in n for n in r.notes))

    # =====================================================================
    # §6.6 bounded pull windows
    # =====================================================================
    AS_OF = date(2026, 5, 14)

    w = gate.pull_window_for("notice_of_sale", as_of=AS_OF)
    check("§6.6 scheduled-event notice_of_sale → FORWARD window",
          w.direction == "FORWARD"
          and w.start == AS_OF
          and w.end == AS_OF + timedelta(days=90))

    w = gate.pull_window_for("sheriff_sale", as_of=AS_OF,
                              forward_horizon_days=30)
    check("§6.6 sheriff_sale with custom 30-day horizon → FORWARD 30",
          w.direction == "FORWARD" and w.horizon_days == 30
          and w.end == AS_OF + timedelta(days=30))

    w = gate.pull_window_for("lis_pendens", as_of=AS_OF)
    check("§6.6 recorded-event lis_pendens → BACKWARD window",
          w.direction == "BACKWARD"
          and w.start == AS_OF - timedelta(days=60)
          and w.end == AS_OF)

    w = gate.pull_window_for("affidavit_of_heirship", as_of=AS_OF,
                              backward_horizon_days=30)
    check("§6.6 affidavit_of_heirship with custom 30-day BACKWARD → "
          "start = as_of - 30",
          w.direction == "BACKWARD"
          and w.start == AS_OF - timedelta(days=30))

    w = gate.pull_window_for("tax_default", as_of=AS_OF)
    check("§6.6 status type tax_default → NONE window "
          "(pull the full current roll)",
          w.direction == "NONE" and w.start is None and w.end is None)

    w = gate.pull_window_for("tax_delinquency", as_of=AS_OF)
    check("§6.6 status type tax_delinquency → NONE window",
          w.direction == "NONE")

    # Unknown type → BACKWARD default.
    w = gate.pull_window_for("not_a_real_type", as_of=AS_OF)
    check("§6.6 unknown type → BACKWARD default (conservative)",
          w.direction == "BACKWARD")

    # --- Daily refresh workflow template exists --------------------------
    template_path = REPO_ROOT / "scaffold" / "ops" / "daily_refresh_template.yml"
    check("§6 daily refresh workflow template exists at "
          "scaffold/ops/daily_refresh_template.yml",
          template_path.is_file())
    if template_path.is_file():
        text = template_path.read_text()
        for token in ("daily-refresh", "§6.4", "§6.5", "§6.7",
                      "refresh_verification_gate", "data/raw/"):
            check(f"§6 template carries the {token!r} marker",
                  token in text)

    # --- Report -----------------------------------------------------------
    failed = [c for c in checks if c[0] == "FAIL"]
    for s, l, d in checks:
        print(f"  [{s}] {l}")
        if s == "FAIL" and d:
            print(f"         detail: {d}")
    if failed:
        print(f"FAIL: refresh-verification gate — {len(failed)}/"
              f"{len(checks)} checks failed")
        return 1
    print(f"PASS: §6 daily-refresh canon (v5.5.0) — all {len(checks)} "
          f"checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
