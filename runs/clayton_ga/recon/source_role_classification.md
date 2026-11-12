# Source role classification

## magistrate_court_civil_evictions

    name: magistrate_court_civil_evictions
    source_role: PRIMARY_LEAD_SOURCE
    rationale: LEAD_GENERATING; court_eviction
    section_13_reference: §13.2 primary

## state_court_civil_judgments

    name: state_court_civil_judgments
    source_role: PRIMARY_LEAD_SOURCE
    rationale: LEAD_GENERATING; court_civil
    section_13_reference: §13.2 primary

## superior_court_civil

    name: superior_court_civil
    source_role: SUPPORTING_LEAD_SOURCE
    rationale: LEAD_GENERATING; court_family
    section_13_reference: §13.2 primary

## probate_estates

    name: probate_estates
    source_role: PRIMARY_LEAD_SOURCE
    rationale: LEAD_GENERATING; court_probate
    section_13_reference: §13.2 primary

## tax_sale_listings

    name: tax_sale_listings
    source_role: PRIMARY_LEAD_SOURCE
    rationale: LEAD_GENERATING; tax_delinquency
    section_13_reference: §13.2 primary

## excess_funds_listing

    name: excess_funds_listing
    source_role: PRIMARY_LEAD_SOURCE
    rationale: LEAD_GENERATING; tax_delinquency
    section_13_reference: §13.2 primary

## gsccca_clerk_indexes

    name: gsccca_clerk_indexes
    source_role: BLOCKED_SOURCE
    rationale: LEAD_GENERATING; clerk_recordings
    section_13_reference: §13.2 primary

## ga_public_notice_foreclosures

    name: ga_public_notice_foreclosures
    source_role: BLOCKED_SOURCE
    rationale: LEAD_GENERATING; sheriff_sales
    section_13_reference: §13.2 primary

## assessor_parcel_master

    name: assessor_parcel_master
    source_role: ENRICHMENT_SOURCE
    rationale: ENRICHMENT; parcel_master
    section_13_reference: §13.3 enrichment

## federal_bankruptcy_ndga

    name: federal_bankruptcy_ndga
    source_role: BLOCKED_SOURCE
    rationale: LEAD_GENERATING; court_civil
    section_13_reference: §13.2 primary

## ga_sos_business_entities

    name: ga_sos_business_entities
    source_role: ENRICHMENT_SOURCE
    rationale: ENRICHMENT; parcel_master
    section_13_reference: §13.3 enrichment

## gsccca_ucc_index

    name: gsccca_ucc_index
    source_role: ENRICHMENT_SOURCE
    rationale: ENRICHMENT; parcel_master
    section_13_reference: §13.3 enrichment

