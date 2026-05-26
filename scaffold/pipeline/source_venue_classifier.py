"""
source_venue_classifier — v5.5.0 §1.5 OFFICIAL-VENUE TEST.

A third-party-hosted platform IS a `PRIMARY_EVENT_SOURCE` /
`PRIMARY_DEFAULT_SOURCE` when recon confirms it is the OFFICIAL VENUE
where the county (or its appointed officer — sheriff, trustee, tax
collector) statutorily CONDUCTS or PUBLISHES the distress event. Vendor
hosting does NOT disqualify a source. RealAuction / RealForeclose /
RealTaxDeed (the clerk-of-court auction platform family used by multiple
judicial-foreclosure jurisdictions) are the established precedent:
county-appointed auction platforms, already canon as primary event
sources where the county clerk officially conducts the auction through
them.

The disqualifier is NOT "third-party domain." The disqualifier is
"marketplace re-listing." A platform that merely RE-LISTS or AGGREGATES
events conducted / published elsewhere, with its own derived status tags
(its own "pre-foreclosure" / "foreclosure" estimates), is REJECTED_SOURCE.

The test, applied per-county in recon (never blanket):

  Q1. Does the county / sheriff / trustee / tax collector officially
      conduct the sale, or publish the statutory notice, ON this platform?
        YES → PRIMARY (official venue).
        NO  → continue.
  Q2. Does the platform re-list sales conducted elsewhere, or apply its
      own derived distress tags?
        YES → REJECTED (aggregator / marketplace re-listing).

This module encodes:

  - KNOWN_OFFICIAL_VENUE_PLATFORMS — the precedent set of county-appointed
    auction platform families. Per-county recon evidence is STILL required
    (a platform being on this list does not blanket-classify it as PRIMARY
    in every county); the list documents the platforms recon has historically
    found to be official venues.
  - CONDITIONAL_PLATFORMS — platforms where the official-venue answer is
    per-county and recon must record the evidence to admit them as PRIMARY.
    Auction.com is the canonical example: PRIMARY in counties where recon
    documents the sheriff/trustee officially conducts the sale through it;
    REJECTED elsewhere.
  - REJECTED_AGGREGATOR_PLATFORMS — platforms that conduct NO sales and
    publish NO statutory notices in any county; their distress tags are
    derived estimates. Zillow, Trulia, Realtor.com, RealtyTrac. REJECTED
    in all counties.

A source admitted as PRIMARY by this classifier STILL passes the normal
qualification gate (§3.3 for tax-default rows, the §17 debtor-party
engine for event rows, §3.5 for owner-status rows) and STILL carries
source proof. The OFFICIAL-VENUE TEST is necessary, not sufficient.

This module is universal framework code: no county / state / vendor
literal beyond the platform-name registries the test is ABOUT — same
exemption pattern as scaffold/ops/stale_label_scanner.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Platform registries — the executable canon. Recon's source classification
# resolves a candidate URL/hostname against these and reports the verdict +
# the rationale.
# ---------------------------------------------------------------------------

KNOWN_OFFICIAL_VENUE_PLATFORMS: dict[str, str] = {
    # The Real* family — county-clerk-of-court auction platform precedent.
    # Per-county recon evidence is STILL required: a platform being on this
    # list does not blanket-admit it as PRIMARY for every county. The list
    # is historical-precedent documentation.
    "realauction.com":  "Real* family — county-appointed auction platform precedent (clerk-of-court foreclosure sales in judicial-foreclosure jurisdictions, and other county-appointed auctions); per-county recon evidence still required.",
    "realforeclose.com": "Real* family — county-appointed foreclosure-auction platform precedent; per-county recon evidence still required.",
    "realtaxdeed.com":  "Real* family — county-appointed tax-deed-sale platform precedent; per-county recon evidence still required.",
    "realtdm.com":      "Real* family — county-appointed tax-deed-management platform; per-county recon evidence still required.",
    "realtdaonline.com": "Real* family — county-appointed tax-deed-auction platform; per-county recon evidence still required.",
    "govease.com":      "County-appointed tax-sale auction platform used by multiple counties across several states; per-county recon evidence still required.",
    "publicsurplus.com": "Government surplus / sheriff-sale platform used by some counties; per-county recon evidence still required.",
    "bid4assets.com":   "County-appointed tax-deed / sheriff-sale platform precedent; per-county recon evidence still required.",
    "civicsource.com":  "Tax-sale platform used by some parish / municipal tax collectors; per-county recon evidence still required.",
}


CONDITIONAL_PLATFORMS: dict[str, str] = {
    # Platforms where the official-venue answer is per-county AND the answer
    # is variable enough that recon MUST record the specific evidence (the
    # county/sheriff/trustee page that designates the platform as the
    # official venue) before the source is admitted as PRIMARY.
    "auction.com": (
        "PRIMARY_EVENT_SOURCE only in counties where recon documents that "
        "the sheriff / trustee officially conducts the sale through "
        "Auction.com; otherwise REJECTED_SOURCE. The required evidence is "
        "the official county/sheriff/trustee page that designates "
        "Auction.com (or its operator) as the auction venue."
    ),
    "xome.com": (
        "Affiliate of a national servicer; PRIMARY_EVENT_SOURCE only where "
        "recon documents that a county sheriff/trustee officially conducts "
        "the sale through it. Default REJECTED_SOURCE — derived listings."
    ),
    "hudhomestore.gov": (
        "Federal HUD listings — PRIMARY only when recon documents that "
        "HUD-Owned-Property rows are the desired lead surface; otherwise "
        "ENRICHMENT (post-disposition listings)."
    ),
}


REJECTED_AGGREGATOR_PLATFORMS: dict[str, str] = {
    # Platforms that conduct NO sales and publish NO statutory notices in
    # any county. Their distress tags ("pre-foreclosure", "foreclosure",
    # "in distress") are derived estimates / sourced from third-party
    # aggregators. REJECTED_SOURCE in all counties.
    "zillow.com":     "Listings portal — derived 'pre-foreclosure' / 'foreclosure' estimates; conducts no sales, publishes no statutory notices. REJECTED in all counties.",
    "trulia.com":     "Listings portal (Zillow-owned) — derived estimates; REJECTED in all counties.",
    "realtor.com":    "Listings portal — derived distress estimates; conducts no sales. REJECTED in all counties.",
    "realtytrac.com": "Aggregator — re-lists distress events sourced from other parties + applies derived tags. REJECTED in all counties.",
    "foreclosure.com": "Aggregator — paid distress-listings re-aggregation. REJECTED in all counties.",
    "homes.com":      "Listings portal — REJECTED in all counties.",
    "redfin.com":     "Listings portal — REJECTED in all counties.",
    "movoto.com":     "Listings portal — REJECTED in all counties.",
    "estately.com":   "Listings portal — REJECTED in all counties.",
}


# ---------------------------------------------------------------------------
# Verdict shape.
# ---------------------------------------------------------------------------

OFFICIAL_VENUE_VERDICTS: tuple[str, ...] = (
    "OFFICIAL_VENUE_PRIMARY",
    "CONDITIONAL_REQUIRES_EVIDENCE",
    "AGGREGATOR_REJECTED",
    "UNKNOWN_PLATFORM",
)


@dataclass(frozen=True, kw_only=True)
class OfficialVenueClassification:
    """The verdict from classify_source_venue() on one candidate platform."""

    platform: str                 # the matched platform host (lowercased)
    verdict: str                  # one of OFFICIAL_VENUE_VERDICTS
    rationale: str
    requires_county_evidence: bool
    evidence_provided: bool       # True only when the caller supplied evidence
    suggested_role: Optional[str] # the §0.1 source_role to assign if admitted
    notes: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------

_HOST_RE = re.compile(r"https?://([^/?#]+)", re.IGNORECASE)


def extract_host(url_or_host: Optional[str]) -> str:
    """Lowercase / strip leading www. / extract host from a URL. Returns ''
    on empty input. Used by classify_source_venue() and the recon tooling
    so callers do not have to pre-normalize."""
    if not url_or_host:
        return ""
    s = url_or_host.strip()
    m = _HOST_RE.match(s)
    host = m.group(1) if m else s
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    # Strip trailing port / path that the regex may not have caught.
    host = host.split("/", 1)[0].split(":", 1)[0]
    return host


def classify_source_venue(
    url_or_host: Optional[str],
    *,
    county_evidence: Optional[dict] = None,
) -> OfficialVenueClassification:
    """Apply the §1.5 OFFICIAL-VENUE TEST to one candidate platform.

    Args:
        url_or_host: a full URL ("https://www.foo.com/bar") OR a bare host
            ("foo.com" / "www.foo.com").
        county_evidence: when the platform is CONDITIONAL, recon supplies a
            dict with two required keys to admit the source as PRIMARY:
              - "official_designation_url": the official county / sheriff /
                trustee / tax-collector page that designates this platform
                as the venue.
              - "officer_or_office": which officer / office made the
                designation (e.g. "sheriff", "trustee", "tax_collector").
            Optional but recommended:
              - "captured_at": ISO 8601 timestamp recon captured the
                designation.
              - "notes": operator notes on the verification.
            With evidence the verdict is OFFICIAL_VENUE_PRIMARY; without it
            CONDITIONAL_REQUIRES_EVIDENCE.

    Returns:
        OfficialVenueClassification. Callers map suggested_role to the
        §0.1 SOURCE_ROLES tuple and continue down the recon pipeline.
    """
    host = extract_host(url_or_host)
    if not host:
        return OfficialVenueClassification(
            platform="",
            verdict="UNKNOWN_PLATFORM",
            rationale="empty / missing URL",
            requires_county_evidence=False,
            evidence_provided=False,
            suggested_role=None,
        )

    # The match is "host endswith key" so subdomains route to the platform:
    # auctions.realauction.com → realauction.com, etc. This is canonical
    # for the venue-classification task because vendor platforms typically
    # serve county-specific subdomains.
    matched_aggregator = _find_match(host, REJECTED_AGGREGATOR_PLATFORMS)
    if matched_aggregator:
        key, rationale = matched_aggregator
        return OfficialVenueClassification(
            platform=key,
            verdict="AGGREGATOR_REJECTED",
            rationale=rationale,
            requires_county_evidence=False,
            evidence_provided=False,
            suggested_role="REJECTED_SOURCE",
        )

    matched_conditional = _find_match(host, CONDITIONAL_PLATFORMS)
    if matched_conditional:
        key, rationale = matched_conditional
        evidence_ok, evidence_notes = _validate_evidence(county_evidence)
        if evidence_ok:
            return OfficialVenueClassification(
                platform=key,
                verdict="OFFICIAL_VENUE_PRIMARY",
                rationale=rationale,
                requires_county_evidence=True,
                evidence_provided=True,
                suggested_role="PRIMARY_EVENT_SOURCE",
                notes=("admitted per recon evidence: "
                       f"{(county_evidence or {}).get('official_designation_url')!r} "
                       f"({(county_evidence or {}).get('officer_or_office')!r})",),
            )
        return OfficialVenueClassification(
            platform=key,
            verdict="CONDITIONAL_REQUIRES_EVIDENCE",
            rationale=rationale,
            requires_county_evidence=True,
            evidence_provided=False,
            suggested_role=None,
            notes=tuple(evidence_notes),
        )

    matched_official = _find_match(host, KNOWN_OFFICIAL_VENUE_PLATFORMS)
    if matched_official:
        key, rationale = matched_official
        # Even known-official-venue platforms must carry per-county recon
        # evidence; the registry is historical precedent. Without the
        # evidence dict, the verdict is still PRIMARY (the platform's
        # precedent makes that the default presumption), but the result
        # records that recon evidence is REQUIRED to admit it for this
        # specific county.
        evidence_ok, _ = _validate_evidence(county_evidence)
        return OfficialVenueClassification(
            platform=key,
            verdict="OFFICIAL_VENUE_PRIMARY",
            rationale=rationale,
            requires_county_evidence=True,
            evidence_provided=evidence_ok,
            suggested_role="PRIMARY_EVENT_SOURCE",
            notes=() if evidence_ok else (
                "known official-venue platform — per-county recon must still "
                "record the official designation page (sheriff / clerk-of-"
                "court / trustee / tax collector) for this county.",
            ),
        )

    # Unknown platform — recon must classify it manually (not auto-blessed,
    # not auto-rejected). The pipeline must NEVER promote an unknown
    # platform to PRIMARY without explicit recon classification.
    return OfficialVenueClassification(
        platform=host,
        verdict="UNKNOWN_PLATFORM",
        rationale=("platform not in v5.5.0 registry — recon must classify "
                   "manually per the §1.5 OFFICIAL-VENUE TEST."),
        requires_county_evidence=True,
        evidence_provided=False,
        suggested_role=None,
    )


# ---------------------------------------------------------------------------
# Internal helpers.
# ---------------------------------------------------------------------------

def _find_match(
    host: str, registry: dict[str, str],
) -> Optional[tuple[str, str]]:
    """Match `host` (already lowercased + www-stripped) against a registry
    by exact-or-suffix membership. Returns (key, rationale) or None."""
    for key, rationale in registry.items():
        # exact match OR suffix match (so subdomain.key.com matches key.com)
        if host == key or host.endswith("." + key):
            return key, rationale
    return None


def _validate_evidence(
    evidence: Optional[dict],
) -> tuple[bool, list[str]]:
    """Validate the county_evidence dict shape for CONDITIONAL admission.

    Returns (ok, missing_field_notes). ok=True only when both required
    keys are populated.
    """
    if not isinstance(evidence, dict):
        return False, [
            "no county_evidence supplied — CONDITIONAL platform requires "
            "an official_designation_url + officer_or_office to admit as "
            "PRIMARY.",
        ]
    missing: list[str] = []
    for k in ("official_designation_url", "officer_or_office"):
        v = evidence.get(k)
        if not isinstance(v, str) or not v.strip():
            missing.append(f"missing required evidence field: {k!r}")
    return (not missing), missing
