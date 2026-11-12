# Document type discovery

## magistrate_court_civil_evictions / state_court_civil_judgments / superior_court_civil

    document_type_taxonomy_field_name: 2-char case type in case number + 'case type' label on summary
    total_types_observed: 2 (CM = Magistrate civil incl. 'Patho Dispossessory'; CV = State Court civil)
    types_mapped_to_canonical_primary: Patho Dispossessory -> Eviction; Default Judgment disposition -> Civil Judgment
    types_mapped_to_canonical_enrichment: none
    types_unknown: Superior Court type codes; other Magistrate codes (garnishment etc.)
    recommended_primary_doc_types_for_build: Magistrate dispossessory filings; State/Magistrate default and consent judgments

## probate_estates

    document_type_taxonomy_field_name: portal module (Estates / Wills / Guardianships / Deaths)
    total_types_observed: 5 modules
    types_mapped_to_canonical_primary: Estates -> Probate; Wills -> Probate
    types_mapped_to_canonical_enrichment: Deaths (supporting)
    types_unknown: estate petition sub-types (to sample in Phase 1)
    recommended_primary_doc_types_for_build: Estates filed in last 30 days

## tax_sale_listings / excess_funds_listing

    document_type_taxonomy_field_name: n/a (single-purpose PDF lists)
    total_types_observed: 2
    types_mapped_to_canonical_primary: Tax sale listing -> Tax Sale / Tax Delinquency; Excess funds -> Surplus
    types_mapped_to_canonical_enrichment: none
    types_unknown: none
    recommended_primary_doc_types_for_build: both

## Blocked sources (not accessible — taxonomy not captured)

    gsccca_clerk_indexes: instrument types expected (Lis Pendens, Deed Under Power, FiFa, Lien) — requires login
    ga_public_notice_foreclosures: GPA categories observed: Foreclosures, Sheriff's/Marshal's Sales, Tax Sales, Probate Notices, Condemnations
