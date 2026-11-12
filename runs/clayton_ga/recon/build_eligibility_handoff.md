# Build eligibility handoff — Clayton County, GA

## Counts

    verified official sources: 12
    PRIMARY_LEAD_SOURCE (accessible): 5 — magistrate_court_civil_evictions, state_court_civil_judgments, probate_estates, tax_sale_listings, excess_funds_listing
    SUPPORTING_LEAD_SOURCE: superior_court_civil
    ENRICHMENT_SOURCE: assessor_parcel_master, ga_sos_business_entities, gsccca_ucc_index
    BLOCKED_SOURCE: gsccca_clerk_indexes, ga_public_notice_foreclosures, federal_bankruptcy_ndga

## Blockers by type

    technical: none
    permission: gsccca_clerk_indexes (paid subscription), ga_public_notice_foreclosures (reCAPTCHA + no-scraping terms), federal_bankruptcy_ndga (PACER)
    hard: none
    unknown: none

## Phase 0.5

    All blockers are PERMISSION blockers. Per §4.14 / §01.13 auto-resolve is not allowed for them; requirements recorded, no further attempts.

## Recommended provisional verdict

    READY_WITH_BLOCKERS

## Justification trail

    magistrate_court_civil_evictions: role=PRIMARY_LEAD_SOURCE access=FULL_PUBLIC_ACCESS confidence=HIGH
    state_court_civil_judgments: role=PRIMARY_LEAD_SOURCE access=FULL_PUBLIC_ACCESS confidence=HIGH
    superior_court_civil: role=SUPPORTING_LEAD_SOURCE access=FULL_PUBLIC_ACCESS confidence=MEDIUM
    probate_estates: role=PRIMARY_LEAD_SOURCE access=FULL_PUBLIC_ACCESS confidence=MEDIUM
    tax_sale_listings: role=PRIMARY_LEAD_SOURCE access=FULL_PUBLIC_ACCESS confidence=HIGH
    excess_funds_listing: role=PRIMARY_LEAD_SOURCE access=FULL_PUBLIC_ACCESS confidence=HIGH
    gsccca_clerk_indexes: role=BLOCKED_SOURCE access=LOGIN_REQUIRED confidence=BLOCKED
    ga_public_notice_foreclosures: role=BLOCKED_SOURCE access=CAPTCHA_PROTECTED confidence=BLOCKED
    assessor_parcel_master: role=ENRICHMENT_SOURCE access=FULL_PUBLIC_ACCESS confidence=HIGH
    federal_bankruptcy_ndga: role=BLOCKED_SOURCE access=LOGIN_REQUIRED confidence=BLOCKED
    ga_sos_business_entities: role=ENRICHMENT_SOURCE access=WAF_PROTECTED confidence=MEDIUM
    gsccca_ucc_index: role=ENRICHMENT_SOURCE access=LOGIN_REQUIRED confidence=MEDIUM

    Reason: Five verified, publicly accessible PRIMARY_LEAD_SOURCE feeds: Magistrate Court evictions (daily, HIGH), State Court civil judgments (HIGH), Probate estates via georgiaprobaterecords.com (MEDIUM — live date search still to be sampled), Tax Commissioner monthly tax sale listings (HIGH) and excess-funds listing (HIGH). Blocked: GSCCCA clerk indexes (lis pendens, deeds, FiFa/tax/mechanics liens) need a paid subscription; non-judicial foreclosure / sheriff sale notices on GeorgiaPublicNotice.com are reCAPTCHA-gated with no-scraping terms; bankruptcy needs PACER. Foreclosure — the highest-value Georgia signal — is therefore not covered until the operator supplies GSCCCA access or a manual notice feed.

## P0 gate

    GATE PASS — magistrate_court_civil_evictions (daily) and probate_estates (daily) are unblocked P0 sources; tax_sale_listings (P0, refreshed per monthly list) also unblocked.

## Recommended operator next actions

    1. Decide on GSCCCA subscription (Premium tier) to unlock lis pendens, deeds under power and liens.
    2. Decide how to obtain foreclosure notices (GPA Smart Search alerts + manual drop, or a licensed feed).
    3. Supply one Superior Court case number so case-number format can be confirmed.
    4. Approve Build Mode with the five accessible primary sources (PARTIAL_BUILD).
