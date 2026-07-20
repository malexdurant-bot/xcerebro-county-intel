"""
Phase 0 county config builder for Richland County, South Carolina.
Generated 2026-07-19. Run via: python runs/richland_sc/build_config.py
"""

import sys
import os

# Allow importing from project root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from scaffold.ops.write_county_config import write_county_config

config = {
    "county_id": "richland_sc",
    "county_name": "Richland",
    "state": "SC",
    "state_rule_family": "",
    "subject_state_full": "South Carolina",
    "fips_code": "45079",
    "timezone": "America/New_York",
    "operator_market_priority": "primary",
    "geography": {
        "municipalities": [
            {"name": "Columbia", "code": "columbia", "fips_place": "4516000"},
            {"name": "Forest Acres", "code": "forest_acres", "fips_place": "4525315"},
            {"name": "Arcadia Lakes", "code": "arcadia_lakes", "fips_place": "4502515"},
            {"name": "Eastover", "code": "eastover", "fips_place": "4521610"},
            {"name": "Blythewood", "code": "blythewood", "fips_place": "4507000"}
        ],
        "accepted_municipalities": [
            {"name": "COLUMBIA", "kind": "incorporated"},
            {"name": "FOREST ACRES", "kind": "incorporated"},
            {"name": "ARCADIA LAKES", "kind": "incorporated"},
            {"name": "EASTOVER", "kind": "incorporated"},
            {"name": "BLYTHEWOOD", "kind": "incorporated"},
            {"name": "IRMO", "kind": "neighboring_overlap"},
            {"name": "Hopkins", "kind": "unincorporated_community"},
            {"name": "DENTSVILLE", "kind": "cdp"},
            {"name": "PONTIAC", "kind": "unincorporated_community"},
            {"name": "BALLENTINE", "kind": "unincorporated_community"},
            {"name": "NORTHEAST COLUMBIA", "kind": "unincorporated_community"},
            {"name": "ST. ANDREWS", "kind": "unincorporated_community"},
            {"name": "DUTCH FORK", "kind": "unincorporated_community"},
            {"name": "LUGOFF", "kind": "neighboring_overlap"}
        ],
        "cross_county_policy": {
            "unknown_city_action": "flag_for_review",
            "neighboring_county_municipalities": ["IRMO", "LUGOFF"]
        },
        "sale_date_rule": {
            "rule_name": "scheduled_by_court",
            "statute_reference": "SC Code Ann. § 15-39-640 — Master-in-Equity conducts judicial foreclosure sales; dates set by court order, typically first Monday of month at 2500 Decker Blvd, Columbia SC 29206."
        },
        "parcel_id_format": "TMS number: letter prefix + numeric segments, e.g. R07516-06-50",
        "parcel_id_normalization": "uppercase, hyphens preserved",
        "address_format_notes": "Standard US addressing. Columbia SC 29201-29229. Richland County covers ZIP codes 29016, 29036, 29044, 29053, 29054, 29063, 29072, 29073, 29204-29229."
    },
    "sources": {
        "sc_courts_circuit_civil": {
            "category": "lead",
            "subtype": "court_civil",
            "url": "https://publicindex.sccourts.org/Richland/PublicIndex/",
            "access_pattern": "static_html",
            "auth_required": False,
            "rate_limit_rpm": None,
            "scraper_module": "scrapers.sc_courts_public_index",
            "translator": "custom",
            "refresh_cadence": "daily",
            "ttl_days": 365,
            "enabled": True,
            "allowed_to_export": True,
            "source_priority": "P0",
            "build_priority": "mvp_required",
            "official_status": "OFFICIAL_STATE",
            "official_entity": "South Carolina Judicial Department — Fifth Judicial Circuit",
            "portal_type": "Statewide court case search portal covering Circuit Court, Family Court, and Magistrate Court dockets for Richland County",
            "portal_family": "custom_county",
            "records_available": [
                "civil_cases",
                "foreclosure_cases",
                "lis_pendens_dockets",
                "civil_judgments",
                "eviction_cases",
                "probate_case_index"
            ],
            "search_fields": ["party_name", "case_number", "filing_date", "case_type"],
            "access_method": "OPEN_PUBLIC_PORTAL",
            "public_access_status": "FULL_PUBLIC_ACCESS",
            "document_access_status": "DOCUMENTS_NOT_AVAILABLE",
            "source_role": "PRIMARY_LEAD_SOURCE",
            "lead_value": "LEAD_GENERATING",
            "verification_confidence": "HIGH",
            "verification_method": "official_domain",
            "verified_from_url": "https://www.richlandcountysc.gov/Courts-Safety",
            "verification_note": "sccourts.org is the official SC Judicial Branch domain. publicindex.sccourts.org/Richland/PublicIndex/ is the official Richland County case docket search. Free public access confirmed. As of 2026-01-01, home address information is no longer displayed in public index results (SC Judicial Department policy change).",
            "sample_record_path_confirmed": True,
            "sample_record_type": "search_form",
            "sample_search_possible": True,
            "sample_document_view_possible": False,
            "open_questions": [
                "Does the public index expose case-level party addresses for defendants in foreclosure cases now that the 2026-01-01 address removal policy is in effect? Operator should verify what address fields remain available.",
                "What specific case type codes identify foreclosure (Master-in-Equity referral) cases vs. standard civil cases in the search interface?"
            ],
            "known_limitations": [
                "As of 2026-01-01, home address information removed from public index for all new and existing cases per SC Judicial Department policy.",
                "Document images not available through public index — docket entries only.",
                "Automated scraping explicitly prohibited per site disclaimer — operator must confirm tolerable rate limits before building scraper."
            ],
            "source_reliability_grade": "A",
            "blocker": "",
            "next_access_strategy": "",
            "auto_resolve_status": "NOT_ATTEMPTED",
            "final_resolution_status": "",
            "auto_resolve_attempts": []
        },
        "probate_estate_inquiry": {
            "category": "lead",
            "subtype": "court_probate",
            "url": "https://www7.richlandcountysc.gov/EstateInquiry/main.aspx",
            "access_pattern": "static_html",
            "auth_required": False,
            "rate_limit_rpm": None,
            "scraper_module": "scrapers.richland_probate_estate_inquiry",
            "translator": "custom",
            "refresh_cadence": "daily",
            "ttl_days": 365,
            "enabled": True,
            "allowed_to_export": True,
            "source_priority": "P0",
            "build_priority": "mvp_required",
            "official_status": "OFFICIAL_COUNTY",
            "official_entity": "Richland County Probate Court",
            "portal_type": "County-hosted probate estate inquiry portal — searchable by decedent name, covers estates filed after 1983",
            "portal_family": "custom_county",
            "records_available": [
                "probate_estate_filings",
                "estate_case_numbers",
                "decedent_names",
                "estate_filing_dates"
            ],
            "search_fields": ["decedent_last_name", "decedent_first_name"],
            "access_method": "OPEN_PUBLIC_PORTAL",
            "public_access_status": "FULL_PUBLIC_ACCESS",
            "document_access_status": "DOCUMENTS_UNKNOWN",
            "source_role": "PRIMARY_LEAD_SOURCE",
            "lead_value": "LEAD_GENERATING",
            "verification_confidence": "HIGH",
            "verification_method": "official_domain",
            "verified_from_url": "https://www.richlandcountysc.gov/Courts-Safety/Probate-Court/Estates/Estate-Records",
            "verification_note": "www7.richlandcountysc.gov is the official Richland County subdomain. EstateInquiry/main.aspx is officially linked from the Richland County Probate Court estate records page. Free public search confirmed. Covers estates filed after 1983; older records require in-person access.",
            "sample_record_path_confirmed": True,
            "sample_record_type": "search_form",
            "sample_search_possible": True,
            "sample_document_view_possible": False,
            "open_questions": [
                "Does the estate inquiry return only a case index (name + case number) or does it also return property addresses and personal representative info? Operator should run a sample search to confirm data fields.",
                "Are there any rate limits or CAPTCHA protection on the estate inquiry portal?"
            ],
            "known_limitations": [
                "Estates filed before 1983 not available online — in-person access at 1701 Main Street required.",
                "Search by decedent name only — no bulk download or date-range search confirmed."
            ],
            "source_reliability_grade": "A",
            "blocker": "",
            "next_access_strategy": "",
            "auto_resolve_status": "NOT_ATTEMPTED",
            "final_resolution_status": "",
            "auto_resolve_attempts": []
        },
        "master_in_equity_foreclosure_sales": {
            "category": "lead",
            "subtype": "sheriff_sales",
            "url": "https://www.richlandcountysc.gov/Courts-Safety/Civil-Court/Master-in-Equity/Foreclosure-Sales",
            "access_pattern": "static_html",
            "auth_required": False,
            "rate_limit_rpm": None,
            "scraper_module": "scrapers.richland_master_in_equity",
            "translator": "custom",
            "refresh_cadence": "monthly",
            "ttl_days": 60,
            "enabled": True,
            "allowed_to_export": True,
            "source_priority": "P0",
            "build_priority": "mvp_required",
            "official_status": "OFFICIAL_COUNTY",
            "official_entity": "Richland County Master-in-Equity Court — Joseph M. Strickland, Master-in-Equity",
            "portal_type": "Monthly judicial foreclosure sale notice page. SC is a judicial foreclosure state — all foreclosures are court-ordered and conducted by the Master-in-Equity.",
            "portal_family": "custom_county",
            "records_available": [
                "foreclosure_sale_notices",
                "sale_dates",
                "case_numbers",
                "property_addresses",
                "tms_parcel_numbers",
                "plaintiff_defendant_names",
                "sale_terms"
            ],
            "search_fields": ["date_range"],
            "access_method": "OPEN_PUBLIC_PORTAL",
            "public_access_status": "FULL_PUBLIC_ACCESS",
            "document_access_status": "DOCUMENTS_PUBLIC",
            "source_role": "PRIMARY_LEAD_SOURCE",
            "lead_value": "LEAD_GENERATING",
            "verification_confidence": "HIGH",
            "verification_method": "official_domain",
            "verified_from_url": "https://www.richlandcountysc.gov/Courts-Safety/Civil-Court/Master-in-Equity",
            "verification_note": "richlandcountysc.gov is the official county domain. Foreclosure-Sales page confirmed. Sales also published in the Columbia Star newspaper as required legal notices. Confirmed sale format: case number, property address, TMS/PIN number, plaintiff, defendant, sale time/location. Sales conducted at 2500 Decker Blvd, Columbia SC 29206, Courtroom #1, typically first Monday of month at 12:00 Noon.",
            "sample_record_path_confirmed": True,
            "sample_record_type": "docket_list",
            "sample_search_possible": True,
            "sample_document_view_possible": True,
            "open_questions": [
                "Does the richlandcountysc.gov/Foreclosure-Sales page publish the full sale list as HTML or does it link to a PDF? Operator should confirm format before building scraper.",
                "Is the Columbia Star (thecolumbiastar.com) the primary or secondary publication? Is there an official county-hosted PDF of the monthly sale list?"
            ],
            "known_limitations": [
                "Monthly cadence only — not a daily source.",
                "Sale list published 30+ days in advance by legal notice requirement; lead-to-sale window is narrow once published.",
                "SC judicial foreclosure: owner has right of redemption up until sale — not all listed properties actually sell."
            ],
            "source_reliability_grade": "A",
            "blocker": "",
            "next_access_strategy": "",
            "auto_resolve_status": "NOT_ATTEMPTED",
            "final_resolution_status": "",
            "auto_resolve_attempts": []
        },
        "register_of_deeds_sms": {
            "category": "lead",
            "subtype": "clerk_recordings",
            "url": "https://www7.richlandcountysc.gov/SMS_External/Login.aspx",
            "access_pattern": "spa_with_api",
            "auth_required": True,
            "rate_limit_rpm": None,
            "scraper_module": "scrapers.richland_register_of_deeds",
            "translator": "custom",
            "refresh_cadence": "daily",
            "ttl_days": 730,
            "enabled": False,
            "allowed_to_export": False,
            "source_priority": "P1",
            "build_priority": "high_value",
            "official_status": "OFFICIAL_COUNTY",
            "official_entity": "Richland County Register of Deeds",
            "portal_type": "Richland County SMS (Subscription Management System) — online deed and document recording index. Custom-built county portal. Covers records from 2/19/1998 onward with document images; index-only for pre-1998 records.",
            "portal_family": "custom_county",
            "records_available": [
                "deeds",
                "mortgages",
                "lis_pendens",
                "liens",
                "releases",
                "ucc_filings",
                "plats",
                "easements",
                "satisfactions"
            ],
            "search_fields": ["grantor_name", "grantee_name", "recording_date", "instrument_number", "legal_description"],
            "access_method": "FREE_ACCOUNT_REQUIRED",
            "public_access_status": "FREE_ACCOUNT_REQUIRED",
            "document_access_status": "DOCUMENTS_LOGIN_REQUIRED",
            "source_role": "BLOCKED_SOURCE",
            "lead_value": "LEAD_GENERATING",
            "verification_confidence": "MEDIUM",
            "verification_method": "official_domain",
            "verified_from_url": "https://www.richlandcountysc.gov/Property-Business/Mapping-and-Records/Register-of-Deeds",
            "verification_note": "Portal confirmed at www7.richlandcountysc.gov/SMS_External/ — the Richland County SMS (Subscription Management System). Login page requires email + password; account creation available. User guide references a 'Public Viewer' but the login page shows no guest/public path — all online access requires account creation. Subscription tiers (daily/weekly/monthly/annual) suggest paid tiers exist beyond free account. Portal fingerprint: custom county system, not Tyler/GovOS/Fidlar.",
            "sample_record_path_confirmed": True,
            "sample_record_type": "search_form",
            "sample_search_possible": False,
            "sample_document_view_possible": False,
            "blocker": "Online access requires account creation at minimum; subscription may be required for full document image access. Free account path unconfirmed — operator must register to determine if free tier provides searchable index.",
            "blocker_type": "PERMISSION_BLOCKER",
            "next_access_strategy": "request_free_account",
            "open_questions": [
                "Does free account registration at www7.richlandcountysc.gov/SMS_External/Login.aspx provide free index search access, or is a paid subscription required?",
                "Does the free tier allow bulk date-range searches (e.g., all lis pendens recorded in last 7 days) or only per-name lookups?",
                "What is the subscription cost for daily/weekly access?"
            ],
            "known_limitations": [
                "Account creation required before any online search is possible.",
                "Subscription may be required for document image access beyond the free index.",
                "Records before 2/19/1998 have index only — no document images online."
            ],
            "source_reliability_grade": "A",
            "paused_reason": "Account registration required — operator must create account and confirm access tier before enabling.",
            "credentials_required_kind": "free_account",
            "credentials_declared": False,
            "auto_resolve_status": "REQUIRES_OPERATOR_APPROVAL",
            "final_resolution_status": "CREDENTIALS_REQUIRED",
            "auto_resolve_attempts": [
                {
                    "attempt_order": 1,
                    "timestamp": "2026-07-19T23:55:00Z",
                    "blocker_type": "PERMISSION_BLOCKER",
                    "strategy": "request_free_account",
                    "status": "REQUIRES_OPERATOR_APPROVAL",
                    "result": "Login page at /SMS_External/Login.aspx requires account creation. Create New Account link is present. Operator must register at https://www7.richlandcountysc.gov/SMS_External/Login.aspx and confirm whether free account provides index search access.",
                    "next_step": "Operator creates free account; reports back access tier and whether date-range bulk search is possible."
                }
            ]
        },
        "delinquent_tax_sale": {
            "category": "lead",
            "subtype": "tax_delinquency",
            "url": "https://www.richlandcountysc.gov/Property-Business/Taxes/Delinquent-Taxes/Tax-Sale",
            "access_pattern": "static_html",
            "auth_required": False,
            "rate_limit_rpm": None,
            "scraper_module": "scrapers.richland_delinquent_tax",
            "translator": "custom",
            "refresh_cadence": "monthly",
            "ttl_days": 365,
            "enabled": True,
            "allowed_to_export": True,
            "source_priority": "P1",
            "build_priority": "high_value",
            "official_status": "OFFICIAL_COUNTY",
            "official_entity": "Richland County Treasurer / Tax Collector",
            "portal_type": "Annual delinquent tax sale page with GIS parcel viewer and PDF legal notice publication. Includes forfeited land list.",
            "portal_family": "custom_county",
            "records_available": [
                "delinquent_tax_property_list",
                "tax_sale_notices",
                "forfeited_land_list",
                "tax_sale_bidder_registration"
            ],
            "search_fields": ["parcel_id", "gis_map_viewer"],
            "access_method": "OPEN_PUBLIC_PORTAL",
            "public_access_status": "FULL_PUBLIC_ACCESS",
            "document_access_status": "DOCUMENTS_PUBLIC",
            "source_role": "PRIMARY_LEAD_SOURCE",
            "lead_value": "LEAD_GENERATING",
            "verification_confidence": "HIGH",
            "verification_method": "official_domain",
            "verified_from_url": "https://www.richlandcountysc.gov/Property-Business/Taxes/Delinquent-Taxes",
            "verification_note": "richlandcountysc.gov is the official county domain. Tax Sale page confirmed. GIS Tax Sale Parcel Viewer confirmed at richlandmaps.com/apps/delinquent/ (official county GIS domain). Legal notice PDF published annually at richlandcountysc.gov/portals/0/Departments/Treasurer/TaxSale/LegalNotice.pdf. Forfeited Land Available page also confirmed. Tax sale is annual (typically November/December in SC). 1-year+1-day redemption period per SC Code Ann. § 12-51-90.",
            "sample_record_path_confirmed": True,
            "sample_record_type": "pdf_index",
            "sample_search_possible": True,
            "sample_document_view_possible": True,
            "open_questions": [
                "Is the delinquent tax list available as a downloadable CSV or only as a PDF legal notice and GIS viewer? Operator should check the tax sale page directly for download options.",
                "When is the 2026 delinquent tax sale scheduled? (Typically November/December in SC.)"
            ],
            "known_limitations": [
                "Annual cadence — not a continuous daily feed. Delinquent list grows throughout the year but official sale notice is annual.",
                "SC 1-year+1-day redemption period means delinquent properties remain redeemable for over a year after sale — not immediate distress.",
                "GIS viewer may not support bulk export."
            ],
            "source_reliability_grade": "A",
            "blocker": "",
            "next_access_strategy": "",
            "auto_resolve_status": "NOT_ATTEMPTED",
            "final_resolution_status": "",
            "auto_resolve_attempts": []
        },
        "assessor_parcel_master": {
            "category": "enrichment",
            "subtype": "parcel_master",
            "url": "https://property.spatialest.com/sc/richland",
            "access_pattern": "static_html",
            "auth_required": False,
            "rate_limit_rpm": None,
            "scraper_module": "scrapers.richland_assessor_spatialest",
            "translator": "parcel_master",
            "refresh_cadence": "monthly",
            "ttl_days": 90,
            "enabled": True,
            "allowed_to_export": False,
            "source_priority": "P2",
            "build_priority": "enrichment",
            "official_status": "OFFICIAL_COUNTY",
            "official_entity": "Richland County Assessor",
            "portal_type": "Assessor property record card portal (Spatialest vendor) — parcel values, owner info, property characteristics",
            "portal_family": "custom_county",
            "records_available": [
                "property_record_cards",
                "assessed_values",
                "owner_names",
                "property_characteristics",
                "tms_parcel_numbers"
            ],
            "search_fields": ["owner_name", "address", "tms_number"],
            "access_method": "OPEN_PUBLIC_PORTAL",
            "public_access_status": "FULL_PUBLIC_ACCESS",
            "document_access_status": "DOCUMENTS_PUBLIC",
            "source_role": "ENRICHMENT_SOURCE",
            "lead_value": "ENRICHMENT",
            "verification_confidence": "HIGH",
            "verification_method": "official_page_link",
            "verified_from_url": "https://www.richlandcountysc.gov/Government/Organization/Departments/Assessor",
            "verification_note": "property.spatialest.com/sc/richland is the official Richland County assessor property record card portal hosted on the Spatialest platform. Spatialest is a known government assessor vendor. Portal confirmed.",
            "sample_record_path_confirmed": True,
            "sample_record_type": "search_form",
            "sample_search_possible": True,
            "sample_document_view_possible": True,
            "open_questions": [],
            "known_limitations": [
                "Enrichment only — parcel data cannot generate leads. Used only to enrich and match lead records from primary event sources."
            ],
            "source_reliability_grade": "A",
            "blocker": "",
            "next_access_strategy": "",
            "auto_resolve_status": "NOT_ATTEMPTED",
            "final_resolution_status": "",
            "auto_resolve_attempts": []
        },
        "gis_richlandmaps": {
            "category": "enrichment",
            "subtype": "gis_parcels",
            "url": "https://richlandmaps.com/",
            "access_pattern": "static_html",
            "auth_required": False,
            "rate_limit_rpm": None,
            "scraper_module": "scrapers.richland_gis",
            "translator": "",
            "refresh_cadence": "monthly",
            "ttl_days": 90,
            "enabled": True,
            "allowed_to_export": False,
            "source_priority": "P2",
            "build_priority": "enrichment",
            "official_status": "OFFICIAL_COUNTY",
            "official_entity": "Richland County Geographic Information Systems (GIS) Division",
            "portal_type": "Official county GIS portal with multiple map apps including DataViewer, Tax Sale Parcel Viewer, and aerial imagery",
            "portal_family": "custom_county",
            "records_available": [
                "gis_parcel_maps",
                "aerial_imagery",
                "delinquent_tax_parcels",
                "property_boundaries"
            ],
            "search_fields": ["parcel_id", "address", "map_viewer"],
            "access_method": "OPEN_PUBLIC_PORTAL",
            "public_access_status": "FULL_PUBLIC_ACCESS",
            "document_access_status": "DOCUMENTS_PUBLIC",
            "source_role": "ENRICHMENT_SOURCE",
            "lead_value": "ENRICHMENT",
            "verification_confidence": "HIGH",
            "verification_method": "official_domain",
            "verified_from_url": "https://www.richlandcountysc.gov/Property-Business/Mapping-and-Records/Geographic-Information-Systems",
            "verification_note": "richlandmaps.com is the official Richland County GIS portal, confirmed linked from the official county website GIS page.",
            "sample_record_path_confirmed": True,
            "sample_record_type": "search_form",
            "sample_search_possible": True,
            "sample_document_view_possible": True,
            "open_questions": [],
            "known_limitations": [
                "Enrichment only — GIS data cannot generate leads."
            ],
            "source_reliability_grade": "A",
            "blocker": "",
            "next_access_strategy": "",
            "auto_resolve_status": "NOT_ATTEMPTED",
            "final_resolution_status": "",
            "auto_resolve_attempts": []
        }
    },
    "scoring_overrides": {
        "match_confidence_floor": 60,
        "review_queue_ratio_alert_threshold": 0.3,
        "high_equity_assessed_to_sale_ratio": 1.5,
        "long_term_owned_years": 7,
        "senior_owner_proxy_years": 60,
        "favorable_loan_era_start": "2012-01-01",
        "favorable_loan_era_end": "2021-12-31"
    },
    "storage": {
        "mode": "STATIC_JSON_MODE",
        "supabase_enabled": False,
        "retain_raw_records_days": 90,
        "retain_source_runs_days": 30
    },
    "dashboard": {
        "title": "Richland County SC — Lead Intelligence",
        "subtitle": "Columbia, South Carolina",
        "primary_color": "#1A3A5C",
        "accent_color": "#C8A951",
        "default_view": "all_leads",
        "precanned_views": [
            {"id": "foreclosure", "label": "Foreclosure / Lis Pendens", "filter": "lead_type:foreclosure"},
            {"id": "probate", "label": "Probate", "filter": "lead_type:probate"},
            {"id": "tax_distress", "label": "Tax Distress", "filter": "lead_type:tax_delinquency"},
            {"id": "high_score", "label": "High Score (≥70)", "filter": "score:>=70"}
        ],
        "view_modes": ["CLIENT_VIEW", "OPERATOR_VIEW"],
        "build_label": "PARTIAL_BUILD",
        "build_label_reason": "Register of Deeds (clerk_recordings) is blocked pending operator account creation. Three primary lead sources live: SC Courts (foreclosure/civil/eviction), Probate Court, Delinquent Tax Sale."
    },
    "deployment": {
        "github_org": "malexdurant-bot",
        "github_repo": "xcerebro-county-intel",
        "scheduler_runtime_class": "SCHEDULER_NOT_CONFIGURED",
        "production_verification_status": "NOT_RUN"
    },
    "source_of_record_matrix": {
        "county_slug": "richland_sc",
        "county_name": "Richland",
        "state": "SC",
        "framework_version": "v5.1.2-beta-r3",
        "generated_at": "2026-07-19T23:55:00Z",
        "county_build_status": "PARTIAL_BUILD_READY",
        "lead_types": [
            {
                "lead_type": "judicial_foreclosure",
                "state_applicability": "APPLICABLE",
                "expected_authorities": ["Circuit Court", "Master-in-Equity"],
                "candidate_sources": [
                    {
                        "source_id": "sc_courts_circuit_civil",
                        "official_url": "https://publicindex.sccourts.org/Richland/PublicIndex/",
                        "authority_type": "state_court",
                        "vendor_name": "SC Judicial Department",
                        "source_role": "PRIMARY_EVENT_SOURCE",
                        "access_status": "OPEN_PUBLIC",
                        "bulk_availability": "BATCH_QUERY",
                        "verification_layers": {
                            "authority": "PASS — official SC Judicial Branch domain (sccourts.org)",
                            "lead_type_relevance": "PASS — civil case docket covers foreclosure cases filed in Circuit Court referred to Master-in-Equity",
                            "access": "PASS — free public search, no login required",
                            "extractability": "CONDITIONAL — docket entries available; document images not available through public index; address info removed 2026-01-01",
                            "refresh_provenance": "PASS — daily filing updates reflect new case activity"
                        },
                        "sample_record_path_confirmed": True,
                        "sample_document_view_possible": False,
                        "minimum_lead_fields_available": ["case_number", "party_names", "filing_date", "case_type"],
                        "operator_verified": False,
                        "notes": "Primary source for lis pendens and foreclosure case filings. Address removal policy since 2026-01-01 means enrichment from assessor will be critical for owner contact data."
                    },
                    {
                        "source_id": "master_in_equity_foreclosure_sales",
                        "official_url": "https://www.richlandcountysc.gov/Courts-Safety/Civil-Court/Master-in-Equity/Foreclosure-Sales",
                        "authority_type": "county_court",
                        "vendor_name": "Richland County",
                        "source_role": "SUPPORTING_EVENT_SOURCE",
                        "access_status": "OPEN_PUBLIC",
                        "bulk_availability": "FULL_COUNTY_BULK",
                        "verification_layers": {
                            "authority": "PASS — official county domain (richlandcountysc.gov)",
                            "lead_type_relevance": "PASS — monthly court-ordered foreclosure sale list with case numbers, addresses, TMS numbers",
                            "access": "PASS — free public page; also published in Columbia Star newspaper",
                            "extractability": "PASS — confirmed fields: case number, property address, TMS/PIN, plaintiff, defendant, sale terms",
                            "refresh_provenance": "PASS — monthly publication per SC legal notice requirement"
                        },
                        "sample_record_path_confirmed": True,
                        "sample_document_view_possible": True,
                        "minimum_lead_fields_available": ["case_number", "property_address", "tms_number", "plaintiff", "defendant", "sale_date"],
                        "operator_verified": False,
                        "notes": "Late-stage lead (already in active foreclosure). Best for investors targeting pre-sale opportunities. Monthly cadence."
                    }
                ],
                "selected_source_id": "sc_courts_circuit_civil",
                "status": "LIVE_SOURCE_FOUND",
                "coverage_notes": "SC is a judicial foreclosure state. All foreclosures go through Circuit Court / Master-in-Equity. SC Courts public index provides case-level docket for foreclosure filings. Master-in-Equity sale page provides monthly confirmed sale list. Both sources are live and free."
            },
            {
                "lead_type": "probate",
                "state_applicability": "APPLICABLE",
                "expected_authorities": ["Richland County Probate Court"],
                "candidate_sources": [
                    {
                        "source_id": "probate_estate_inquiry",
                        "official_url": "https://www7.richlandcountysc.gov/EstateInquiry/main.aspx",
                        "authority_type": "county_court",
                        "vendor_name": "Richland County",
                        "source_role": "PRIMARY_EVENT_SOURCE",
                        "access_status": "OPEN_PUBLIC",
                        "bulk_availability": "BATCH_QUERY",
                        "verification_layers": {
                            "authority": "PASS — official county subdomain (www7.richlandcountysc.gov), linked from official Probate Court page",
                            "lead_type_relevance": "PASS — probate estate filings are the canonical probate lead signal",
                            "access": "PASS — free public search by decedent name, no login",
                            "extractability": "PARTIAL — search returns estate cases; field set unknown until operator runs sample search",
                            "refresh_provenance": "PASS — estate filings appear in system as filed"
                        },
                        "sample_record_path_confirmed": True,
                        "sample_document_view_possible": False,
                        "minimum_lead_fields_available": ["decedent_name", "case_number", "filing_date"],
                        "operator_verified": False,
                        "notes": "Free public portal. Search by name only — no bulk date-range export confirmed. Operator must run sample to confirm returned fields (especially whether property addresses are returned)."
                    }
                ],
                "selected_source_id": "probate_estate_inquiry",
                "status": "LIVE_SOURCE_FOUND_LIMITED_COVERAGE",
                "coverage_notes": "Probate court portal confirmed live and free. Search by decedent name only — no bulk date-range search confirmed. Will need name-based sweep strategy or supplemental source for bulk daily monitoring."
            },
            {
                "lead_type": "tax_delinquency",
                "state_applicability": "APPLICABLE",
                "expected_authorities": ["Richland County Treasurer"],
                "candidate_sources": [
                    {
                        "source_id": "delinquent_tax_sale",
                        "official_url": "https://www.richlandcountysc.gov/Property-Business/Taxes/Delinquent-Taxes/Tax-Sale",
                        "authority_type": "county_treasurer",
                        "vendor_name": "Richland County",
                        "source_role": "PRIMARY_EVENT_SOURCE",
                        "access_status": "OPEN_PUBLIC",
                        "bulk_availability": "FULL_COUNTY_BULK",
                        "verification_layers": {
                            "authority": "PASS — official county domain; GIS viewer on official richlandmaps.com domain",
                            "lead_type_relevance": "PASS — delinquent tax properties are confirmed distress signals",
                            "access": "PASS — GIS viewer free public, PDF legal notice free public",
                            "extractability": "CONDITIONAL — GIS viewer may not support bulk export; PDF may require parsing",
                            "refresh_provenance": "PARTIAL — annual tax sale cycle; list updated throughout year but sale is annual"
                        },
                        "sample_record_path_confirmed": True,
                        "sample_document_view_possible": True,
                        "minimum_lead_fields_available": ["parcel_id", "property_address", "delinquent_amount"],
                        "operator_verified": False,
                        "notes": "Annual cadence limits daily freshness. SC 1-year+1-day redemption period reduces urgency. Best for identifying highly motivated sellers approaching forfeiture."
                    }
                ],
                "selected_source_id": "delinquent_tax_sale",
                "status": "LIVE_SOURCE_FOUND_LIMITED_COVERAGE",
                "coverage_notes": "Tax delinquency portal confirmed. Annual cadence. Forfeited Land Available page also confirmed as secondary reference for properties past redemption."
            },
            {
                "lead_type": "clerk_recordings_lis_pendens",
                "state_applicability": "APPLICABLE",
                "expected_authorities": ["Richland County Register of Deeds"],
                "candidate_sources": [
                    {
                        "source_id": "register_of_deeds_sms",
                        "official_url": "https://www7.richlandcountysc.gov/SMS_External/Login.aspx",
                        "authority_type": "county_recorder",
                        "vendor_name": "Richland County (SMS — custom county system)",
                        "source_role": "BLOCKED_SOURCE",
                        "access_status": "FREE_ACCOUNT_REQUIRED",
                        "bulk_availability": "UNKNOWN",
                        "verification_layers": {
                            "authority": "PASS — official county subdomain, confirmed from official Register of Deeds page",
                            "lead_type_relevance": "PASS — lis pendens, deeds, mortgages, liens are confirmed record types in SMS system",
                            "access": "BLOCKED — login required; account creation required for any online access",
                            "extractability": "UNKNOWN — cannot confirm until account created and access tier determined",
                            "refresh_provenance": "PASS — recordings indexed on recording date per user guide"
                        },
                        "sample_record_path_confirmed": True,
                        "sample_document_view_possible": False,
                        "minimum_lead_fields_available": [],
                        "operator_verified": False,
                        "notes": "Blocked pending operator account registration. Once account created, will provide lis pendens, deeds, mortgage filings — highest-value clerk recording source. Operator should register at /SMS_External/Login.aspx and report back on free vs. paid access tiers."
                    }
                ],
                "selected_source_id": "register_of_deeds_sms",
                "status": "SOURCE_FOUND_NEEDS_LOGIN",
                "coverage_notes": "ROD confirmed but blocked by account requirement. SC Courts public index covers foreclosure lis pendens at the case level, partially mitigating this gap. ROD is the definitive source for recording dates, exact document types, and grantee/grantor data."
            },
            {
                "lead_type": "eviction",
                "state_applicability": "APPLICABLE",
                "expected_authorities": ["Richland County Magistrate Courts"],
                "candidate_sources": [
                    {
                        "source_id": "sc_courts_circuit_civil",
                        "official_url": "https://publicindex.sccourts.org/Richland/PublicIndex/",
                        "authority_type": "state_court",
                        "vendor_name": "SC Judicial Department",
                        "source_role": "PRIMARY_EVENT_SOURCE",
                        "access_status": "OPEN_PUBLIC",
                        "bulk_availability": "BATCH_QUERY",
                        "verification_layers": {
                            "authority": "PASS — official SC Judicial Branch domain",
                            "lead_type_relevance": "PASS — SC courts public index covers magistrate court (dispossessory/eviction cases) in addition to Circuit Court",
                            "access": "PASS — free public search",
                            "extractability": "CONDITIONAL — address removal policy since 2026-01-01",
                            "refresh_provenance": "PASS — daily case filing updates"
                        },
                        "sample_record_path_confirmed": True,
                        "sample_document_view_possible": False,
                        "minimum_lead_fields_available": ["case_number", "party_names", "filing_date"],
                        "operator_verified": False,
                        "notes": "Eviction cases fall under magistrate court jurisdiction in SC (claims under $7,500). SC courts public index covers magistrate court. Richland County has 8 magistrate court locations."
                    }
                ],
                "selected_source_id": "sc_courts_circuit_civil",
                "status": "LIVE_SOURCE_FOUND",
                "coverage_notes": "Eviction cases accessible via SC courts public index under magistrate court case type. Same portal as civil/foreclosure."
            }
        ]
    },
    "source_coverage_map": {
        "live_sources": [
            "sc_courts_circuit_civil",
            "probate_estate_inquiry",
            "master_in_equity_foreclosure_sales",
            "delinquent_tax_sale",
            "assessor_parcel_master",
            "gis_richlandmaps"
        ],
        "blocked_sources": [
            "register_of_deeds_sms"
        ],
        "limited_coverage_sources": [
            "probate_estate_inquiry",
            "delinquent_tax_sale"
        ],
        "not_found_lead_types": [
            "code_enforcement"
        ],
        "operator_review_required": [
            "register_of_deeds_sms"
        ]
    },
    "api_discovery": {
        "searched": [
            "richlandcountysc.gov API documentation",
            "richlandmaps.com ArcGIS REST services",
            "sccourts.org developer API",
            "SC FOIA/open data portal"
        ],
        "found": [],
        "search_notes": "No documented public APIs found for Richland County SC during Phase 0 recon. richlandmaps.com appears to use ArcGIS but no REST endpoint documentation confirmed. SC courts public index has no documented API. All sources use HTML/form-based access."
    },
    "enrichment_index_strategy": {
        "bulk_index_available": False,
        "bulk_index_source": None,
        "per_record_query_required": True,
        "per_record_query_cost_estimate": "LOW — Spatialest parcel portal is free and per-record query by TMS/address is confirmed public",
        "recommended_strategy": "Per-record enrichment via Spatialest (property.spatialest.com/sc/richland) and RichlandMaps GIS. Trigger enrichment on each lead using TMS parcel number from source record. No bulk download confirmed.",
        "deferred_to_version": None
    },
    "build_verdict": "READY_WITH_BLOCKERS",
    "build_verdict_reason": "Three verified PRIMARY_LEAD_SOURCE entries with HIGH confidence and full public access: (1) SC Courts Public Index covering foreclosure/civil/eviction cases, (2) Richland County Probate Court Estate Inquiry, (3) Delinquent Tax Sale. Master-in-Equity monthly foreclosure sale list also confirmed. Register of Deeds (clerk_recordings, highest-value source for lis pendens and deed recordings) is BLOCKED pending operator account creation at www7.richlandcountysc.gov/SMS_External/. Code enforcement: NOT_FOUND as public list. Verdict is READY_WITH_BLOCKERS rather than READY_TO_BUILD because RoD blocker should be resolved before Build Mode to avoid building scrapers around an incomplete source map.",
    "build_verdict_at": "2026-07-19T23:55:00Z",
    "auto_resolve_status": "PARTIALLY_RESOLVED",
    "final_resolution_status": "PARTIALLY_RESOLVED",
    "operator_override_audit": []
}

result = write_county_config(
    config_dict=config,
    target_path="config/counties/richland_sc.json",
    schema_path="config/counties/_schema.json",
    overwrite=False,
)
print(result.summary())
import sys
sys.exit(0 if result.is_ok() else 1)
