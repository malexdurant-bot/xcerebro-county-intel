#!/usr/bin/env python3
"""v5.5.0 §1.5 invariants — OFFICIAL-VENUE TEST classifier.

Pins the per-platform classification rules:
  - KNOWN_OFFICIAL_VENUE_PLATFORMS (Real* family, govease, bid4assets,
    civicsource) → OFFICIAL_VENUE_PRIMARY (suggest PRIMARY_EVENT_SOURCE)
    + per-county recon evidence still required (note in result).
  - CONDITIONAL_PLATFORMS (auction.com) → OFFICIAL_VENUE_PRIMARY only with
    a valid county_evidence dict; CONDITIONAL_REQUIRES_EVIDENCE otherwise.
  - REJECTED_AGGREGATOR_PLATFORMS (zillow, trulia, realtor.com, realtytrac,
    foreclosure.com, homes.com, redfin.com, movoto.com, estately.com) →
    AGGREGATOR_REJECTED in all counties.
  - Unknown platform → UNKNOWN_PLATFORM (no auto-promote).
  - Subdomains route to the platform (auctions.realauction.com →
    realauction.com).
  - Vendor hosting does NOT disqualify — RealAuction etc. classify as
    OFFICIAL_VENUE_PRIMARY by precedent, ZILLOW classifies as
    AGGREGATOR_REJECTED.

Run: python3 scaffold/tests/v5_5_0/test_source_venue_classifier.py
Exit 0 = pass, non-zero = fail.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scaffold.pipeline import source_venue_classifier as svc


def main() -> int:
    checks: list = []

    def check(label, ok, detail=""):
        checks.append(("PASS" if ok else "FAIL", label, detail))

    # =====================================================================
    # Host extraction sanity
    # =====================================================================
    cases = [
        ("https://www.zillow.com/foo/bar", "zillow.com"),
        ("http://AUCTIONS.realauction.com/", "auctions.realauction.com"),
        ("www.auction.com", "auction.com"),
        ("auction.com", "auction.com"),
        ("https://realtor.com:8080/listings?x=1#hash", "realtor.com"),
        ("", ""),
        (None, ""),
    ]
    for inp, expected in cases:
        got = svc.extract_host(inp)
        check(f"extract_host({inp!r}) == {expected!r}", got == expected,
              f"got {got!r}")

    # =====================================================================
    # KNOWN OFFICIAL VENUES — Real* family precedent
    # =====================================================================
    for host in ("realauction.com", "realforeclose.com", "realtaxdeed.com",
                 "govease.com", "bid4assets.com", "civicsource.com"):
        r = svc.classify_source_venue(f"https://{host}/foo")
        check(f"§1.5 known official venue {host!r} → OFFICIAL_VENUE_PRIMARY",
              r.verdict == "OFFICIAL_VENUE_PRIMARY"
              and r.suggested_role == "PRIMARY_EVENT_SOURCE",
              f"got {r.verdict!r}")
        # Without evidence, the result still says PRIMARY but flags
        # evidence_provided=False — recon must still record the
        # county's official designation page.
        check(f"§1.5 known official venue {host!r}: requires_county_evidence=True "
              "even though the platform is precedent (per-county evidence "
              "still recorded)",
              r.requires_county_evidence is True
              and r.evidence_provided is False)

    # Subdomain routing — auctions.realauction.com → realauction.com.
    r = svc.classify_source_venue(
        "https://auctions.realauction.com/county-clerk-portal",
    )
    check("§1.5 subdomain routing: auctions.realauction.com → "
          "realauction.com OFFICIAL_VENUE_PRIMARY",
          r.platform == "realauction.com"
          and r.verdict == "OFFICIAL_VENUE_PRIMARY")

    # With county evidence supplied, evidence_provided flips to True.
    r = svc.classify_source_venue(
        "https://realauction.com/",
        county_evidence={
            "official_designation_url": "https://example-county.gov/clerk/auctions",
            "officer_or_office": "clerk_of_court",
            "captured_at": "2026-05-26T12:00:00Z",
        },
    )
    check("§1.5 official venue + valid county_evidence → "
          "OFFICIAL_VENUE_PRIMARY + evidence_provided=True",
          r.verdict == "OFFICIAL_VENUE_PRIMARY"
          and r.evidence_provided is True)

    # =====================================================================
    # REJECTED AGGREGATORS — Zillow et al. — REJECTED in all counties
    # =====================================================================
    for host in ("zillow.com", "trulia.com", "realtor.com", "realtytrac.com",
                 "foreclosure.com", "homes.com", "redfin.com",
                 "movoto.com", "estately.com"):
        r = svc.classify_source_venue(f"https://www.{host}/listings")
        check(f"§1.5 aggregator {host!r} → AGGREGATOR_REJECTED in all counties",
              r.verdict == "AGGREGATOR_REJECTED"
              and r.suggested_role == "REJECTED_SOURCE",
              f"got {r.verdict!r}")
        check(f"§1.5 aggregator {host!r} verdict is unaffected by "
              "county_evidence (Zillow et al. publish no statutory notices)",
              svc.classify_source_venue(
                  f"https://{host}",
                  county_evidence={
                      "official_designation_url": "https://example.gov/x",
                      "officer_or_office": "sheriff",
                  },
              ).verdict == "AGGREGATOR_REJECTED")

    # Subdomain match: www.realtor.com / mortgage.zillow.com all REJECTED.
    r = svc.classify_source_venue("https://mortgage.zillow.com/")
    check("§1.5 aggregator subdomain mortgage.zillow.com → "
          "AGGREGATOR_REJECTED",
          r.verdict == "AGGREGATOR_REJECTED"
          and r.platform == "zillow.com")

    # =====================================================================
    # CONDITIONAL PLATFORMS — Auction.com (the canonical example)
    # =====================================================================
    # No evidence → CONDITIONAL_REQUIRES_EVIDENCE.
    r = svc.classify_source_venue("https://www.auction.com/some/auction")
    check("§1.5 Auction.com WITHOUT county_evidence → "
          "CONDITIONAL_REQUIRES_EVIDENCE (NOT auto-rejected, NOT "
          "auto-admitted)",
          r.verdict == "CONDITIONAL_REQUIRES_EVIDENCE"
          and r.suggested_role is None)
    check("§1.5 Auction.com CONDITIONAL result names the required "
          "evidence fields (official_designation_url, officer_or_office)",
          any("official_designation_url" in n for n in r.notes))

    # Valid evidence → OFFICIAL_VENUE_PRIMARY.
    r = svc.classify_source_venue(
        "https://www.auction.com/",
        county_evidence={
            "official_designation_url": "https://example-sheriff.gov/sales/auction-com-designation",
            "officer_or_office": "sheriff",
            "captured_at": "2026-05-26T12:00:00Z",
        },
    )
    check("§1.5 Auction.com WITH valid county_evidence → "
          "OFFICIAL_VENUE_PRIMARY + suggested_role PRIMARY_EVENT_SOURCE",
          r.verdict == "OFFICIAL_VENUE_PRIMARY"
          and r.suggested_role == "PRIMARY_EVENT_SOURCE"
          and r.evidence_provided is True)
    check("§1.5 Auction.com WITH evidence: notes carry the designation URL",
          any("example-sheriff.gov" in n for n in r.notes))

    # Empty evidence dict → CONDITIONAL_REQUIRES_EVIDENCE.
    r = svc.classify_source_venue(
        "https://www.auction.com/", county_evidence={},
    )
    check("§1.5 Auction.com WITH empty evidence dict → "
          "CONDITIONAL_REQUIRES_EVIDENCE",
          r.verdict == "CONDITIONAL_REQUIRES_EVIDENCE")

    # Partial evidence (missing officer_or_office) → CONDITIONAL_REQUIRES_EVIDENCE.
    r = svc.classify_source_venue(
        "https://www.auction.com/",
        county_evidence={"official_designation_url": "https://x.gov/y"},
    )
    check("§1.5 Auction.com WITH partial evidence (missing "
          "officer_or_office) → CONDITIONAL_REQUIRES_EVIDENCE",
          r.verdict == "CONDITIONAL_REQUIRES_EVIDENCE"
          and any("officer_or_office" in n for n in r.notes))

    # =====================================================================
    # Unknown platform — recon must classify manually, no auto-promote
    # =====================================================================
    for host in ("acme-county-clerk.gov", "small-vendor-foreclosure.example",
                 "obscure-platform.net"):
        r = svc.classify_source_venue(f"https://{host}/")
        check(f"§1.5 unknown platform {host!r} → UNKNOWN_PLATFORM "
              "(NEVER auto-promote to PRIMARY)",
              r.verdict == "UNKNOWN_PLATFORM"
              and r.suggested_role is None)

    # Empty / None input → UNKNOWN_PLATFORM with platform=''.
    r = svc.classify_source_venue(None)
    check("§1.5 None input → UNKNOWN_PLATFORM (platform='')",
          r.verdict == "UNKNOWN_PLATFORM" and r.platform == "")

    # =====================================================================
    # OFFICIAL_VENUE_VERDICTS contract
    # =====================================================================
    check("§1.5 OFFICIAL_VENUE_VERDICTS carries exactly 4 values",
          len(svc.OFFICIAL_VENUE_VERDICTS) == 4)
    for v in ("OFFICIAL_VENUE_PRIMARY", "CONDITIONAL_REQUIRES_EVIDENCE",
              "AGGREGATOR_REJECTED", "UNKNOWN_PLATFORM"):
        check(f"§1.5 OFFICIAL_VENUE_VERDICTS includes {v!r}",
              v in svc.OFFICIAL_VENUE_VERDICTS)

    # =====================================================================
    # Registry invariants
    # =====================================================================
    check("§1.5 Auction.com is in CONDITIONAL_PLATFORMS, NOT in "
          "REJECTED_AGGREGATOR_PLATFORMS (canon — operator decision is "
          "per-county evidence, not blanket)",
          "auction.com" in svc.CONDITIONAL_PLATFORMS
          and "auction.com" not in svc.REJECTED_AGGREGATOR_PLATFORMS)
    check("§1.5 Zillow is in REJECTED_AGGREGATOR_PLATFORMS, NOT in "
          "CONDITIONAL_PLATFORMS (canon — Zillow is REJECTED in all "
          "counties, not conditional)",
          "zillow.com" in svc.REJECTED_AGGREGATOR_PLATFORMS
          and "zillow.com" not in svc.CONDITIONAL_PLATFORMS)
    check("§1.5 realauction.com is in KNOWN_OFFICIAL_VENUE_PLATFORMS",
          "realauction.com" in svc.KNOWN_OFFICIAL_VENUE_PLATFORMS)
    check("§1.5 the three registries are mutually disjoint",
          (set(svc.KNOWN_OFFICIAL_VENUE_PLATFORMS)
           & set(svc.CONDITIONAL_PLATFORMS)) == set()
          and (set(svc.KNOWN_OFFICIAL_VENUE_PLATFORMS)
               & set(svc.REJECTED_AGGREGATOR_PLATFORMS)) == set()
          and (set(svc.CONDITIONAL_PLATFORMS)
               & set(svc.REJECTED_AGGREGATOR_PLATFORMS)) == set())

    # =====================================================================
    # PRECEDENT-VS-NEW-EVIDENCE rule: a county can still classify a
    # known-official-venue platform as REJECTED if recon discovers it's
    # being used as an aggregator (per-county recon ALWAYS wins). The
    # classifier exposes the default + the evidence trail; the caller
    # (recon orchestrator) is the source of truth for the verdict.
    # We pin that the classifier returns OFFICIAL_VENUE_PRIMARY as the
    # DEFAULT for known venues — the recon orchestrator can downgrade.
    # =====================================================================
    r = svc.classify_source_venue("https://realauction.com/")
    check("§1.5 default verdict for known venue is OFFICIAL_VENUE_PRIMARY "
          "(recon orchestrator can still downgrade based on per-county "
          "evidence — the classifier is the DEFAULT, not the final verdict)",
          r.verdict == "OFFICIAL_VENUE_PRIMARY")

    # --- Report -----------------------------------------------------------
    failed = [c for c in checks if c[0] == "FAIL"]
    for s, l, d in checks:
        print(f"  [{s}] {l}")
        if s == "FAIL" and d:
            print(f"         detail: {d}")
    if failed:
        print(f"FAIL: source-venue classifier — {len(failed)}/{len(checks)} "
              f"checks failed")
        return 1
    print(f"PASS: §1.5 OFFICIAL-VENUE TEST classifier (v5.5.0) — "
          f"all {len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
