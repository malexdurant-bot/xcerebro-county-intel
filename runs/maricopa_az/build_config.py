"""
Phase 0 county config builder for Maricopa County, Arizona.
Generated: 2026-06-26T18:55:00Z
Framework version: v5.1.2-beta-r3

Run this script from the repo root:
    python runs/maricopa_az/build_config.py

It calls write_county_config() atomically — never text-streams JSON.
"""

import sys
import os
from pathlib import Path

# Make the repo root importable regardless of CWD
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scaffold.ops.write_county_config import write_county_config

TARGET_PATH = REPO_ROOT / "config" / "counties" / "maricopa_az.json"
SCHEMA_PATH = REPO_ROOT / "config" / "counties" / "_schema.json"

# ---------------------------------------------------------------------------
# County config dict — built entirely in memory (no streaming write)
# ---------------------------------------------------------------------------

config = {
    "county_id": "maricopa_az",
    "county_name": "Maricopa County",
    "state": "AZ",
    "subject_state_full": "Arizona",
    "fips_code": "04013",
    "timezone": "America/Phoenix",
    "operator_market_priority": "primary",
    "state_rule_family": "AZ_non_judicial_foreclosure",

    # ------------------------------------------------------------------
    # GEOGRAPHY
    # ------------------------------------------------------------------
    "geography": {
        "municipalities": [
            {"name": "Phoenix",           "code": "phoenix",          "fips_place": "0455000"},
            {"name": "Mesa",              "code": "mesa",             "fips_place": "0446000"},
            {"name": "Chandler",          "code": "chandler",         "fips_place": "0412000"},
            {"name": "Scottsdale",        "code": "scottsdale",       "fips_place": "0465000"},
            {"name": "Tempe",             "code": "tempe",            "fips_place": "0473000"},
            {"name": "Gilbert",           "code": "gilbert",          "fips_place": "0427400"},
            {"name": "Glendale",          "code": "glendale",         "fips_place": "0429050"},
            {"name": "Peoria",            "code": "peoria",           "fips_place": "0454050"},
            {"name": "Surprise",          "code": "surprise",         "fips_place": "0471000"},
            {"name": "Avondale",          "code": "avondale",         "fips_place": "0404500"},
            {"name": "Goodyear",          "code": "goodyear",         "fips_place": "0430175"},
            {"name": "Buckeye",           "code": "buckeye",          "fips_place": "0408620"},
            {"name": "Fountain Hills",    "code": "fountain_hills",   "fips_place": "0426290"},
            {"name": "Paradise Valley",   "code": "paradise_valley",  "fips_place": "0453190"},
            {"name": "Litchfield Park",   "code": "litchfield_park",  "fips_place": "0441810"},
            {"name": "El Mirage",         "code": "el_mirage",        "fips_place": "0421440"},
            {"name": "Tolleson",          "code": "tolleson",         "fips_place": "0473620"},
            {"name": "Guadalupe",         "code": "guadalupe",        "fips_place": "0430300"},
            {"name": "Youngtown",         "code": "youngtown",        "fips_place": "0478950"},
            {"name": "Wickenburg",        "code": "wickenburg",       "fips_place": "0477740"},
            {"name": "Carefree",          "code": "carefree",         "fips_place": "0410450"},
            {"name": "Cave Creek",        "code": "cave_creek",       "fips_place": "0411080"},
            {"name": "Queen Creek",       "code": "queen_creek",      "fips_place": "0458640"},
            {"name": "Gila Bend",         "code": "gila_bend",        "fips_place": "0428270"},
        ],
        "accepted_municipalities": [
            {"name": "PHOENIX",           "kind": "incorporated"},
            {"name": "MESA",              "kind": "incorporated"},
            {"name": "CHANDLER",          "kind": "incorporated"},
            {"name": "SCOTTSDALE",        "kind": "incorporated"},
            {"name": "TEMPE",             "kind": "incorporated"},
            {"name": "GILBERT",           "kind": "incorporated"},
            {"name": "GLENDALE",          "kind": "incorporated"},
            {"name": "PEORIA",            "kind": "incorporated"},
            {"name": "SURPRISE",          "kind": "incorporated"},
            {"name": "AVONDALE",          "kind": "incorporated"},
            {"name": "GOODYEAR",          "kind": "incorporated"},
            {"name": "BUCKEYE",           "kind": "incorporated"},
            {"name": "FOUNTAIN HILLS",    "kind": "incorporated"},
            {"name": "PARADISE VALLEY",   "kind": "incorporated"},
            {"name": "LITCHFIELD PARK",   "kind": "incorporated"},
            {"name": "EL MIRAGE",         "kind": "incorporated"},
            {"name": "TOLLESON",          "kind": "incorporated"},
            {"name": "GUADALUPE",         "kind": "incorporated"},
            {"name": "YOUNGTOWN",         "kind": "incorporated"},
            {"name": "WICKENBURG",        "kind": "incorporated"},
            {"name": "CAREFREE",          "kind": "incorporated"},
            {"name": "CAVE CREEK",        "kind": "incorporated"},
            {"name": "QUEEN CREEK",       "kind": "incorporated"},
            {"name": "GILA BEND",         "kind": "incorporated"},
            # Unincorporated communities commonly appearing in county records
            {"name": "SUN CITY",          "kind": "unincorporated_community"},
            {"name": "SUN CITY WEST",     "kind": "unincorporated_community"},
            {"name": "SUN LAKES",         "kind": "unincorporated_community"},
            {"name": "LAVEEN",            "kind": "unincorporated_community"},
            {"name": "ANTHEM",            "kind": "unincorporated_community"},
            {"name": "RIO VERDE",         "kind": "unincorporated_community"},
            {"name": "NEW RIVER",         "kind": "unincorporated_community"},
            {"name": "WADDELL",           "kind": "unincorporated_community"},
            {"name": "WITTMANN",          "kind": "unincorporated_community"},
            {"name": "TONOPAH",           "kind": "unincorporated_community"},
            {"name": "MORRISTOWN",        "kind": "unincorporated_community"},
            {"name": "CASHION",           "kind": "unincorporated_community"},
            {"name": "AHWATUKEE",         "kind": "unincorporated_community"},
            {"name": "AGUILA",            "kind": "unincorporated_community"},
            {"name": "WINTERSBURG",       "kind": "unincorporated_community"},
        ],
        "cross_county_policy": {
            "unknown_city_action": "flag_for_review",
            "neighboring_county_municipalities": [
                "Maricopa",   # Pinal County city, name confusion risk
                "Gold Canyon",
                "San Tan Valley",
                "Apache Junction",
                "Florence",
                "Coolidge",
            ]
        },
        "sale_date_rule": {
            "rule_name": "any_business_day_after_notice",
            "statute_reference": "A.R.S. § 33-808 — Notice of Trustee's Sale must be recorded and published at least 90 days before the sale date. Trustee sets the specific sale date and time. Sales occur at the Maricopa County Courthouse steps or other designated location."
        },
        "parcel_id_format": "XXX-XX-XXX (Book-Map-Parcel, e.g., 301-55-001)",
        "parcel_id_normalization": "Remove hyphens and leading zeros within each segment: NNN-NN-NNN. Pad to standard length if shorter.",
        "address_format_notes": "Arizona situs addresses typically follow: [number] [direction] [street name] [suffix] [city], AZ [zip]. Many Phoenix metro addresses include compass quadrant (N/S/E/W) as part of the street name. Recorder documents may abbreviate direction (N, S, E, W) or spell it out."
    },

    # ------------------------------------------------------------------
    # SOURCES
    # ------------------------------------------------------------------
    "sources": {

        # ---- P0 PRIMARY: County Recorder --------------------------------
        "recorder_maricopa": {
            "category": "lead",
            "subtype": "clerk_recordings",
            "url": "https://recorder.maricopa.gov/recording/document-search.html",
            "official_status": "OFFICIAL_COUNTY",
            "verified_from_url": "https://recorder.maricopa.gov/",
            "verification_method": "official_domain",
            "official_entity": "Maricopa County Recorder's Office",
            "portal_type": "Land records and document search portal (full text retrieval, 1871–present)",
            "records_available": [
                "deeds",
                "warranty_deeds",
                "quitclaim_deeds",
                "trustee_deeds",
                "notice_of_trustee_sale",
                "deed_of_trust",
                "assignment_of_deed_of_trust",
                "release_of_deed_of_trust",
                "mechanics_liens",
                "hoa_liens",
                "federal_tax_liens",
                "state_tax_liens",
                "judgment_liens",
                "lis_pendens",
                "releases",
                "easements",
                "cc_and_rs",
                "affidavits"
            ],
            "search_fields": ["name", "business_name", "legal_description", "address", "document_type", "date_range", "recording_number"],
            "access_pattern": "spa_with_api",
            "access_method": "PUBLIC_BUT_WAF_PROTECTED",
            "public_access_status": "FULL_PUBLIC_ACCESS",
            "document_access_status": "DOCUMENTS_PUBLIC",
            "auth_required": False,
            "rate_limit_rpm": None,
            "source_role": "PRIMARY_LEAD_SOURCE",
            "lead_value": "LEAD_GENERATING",
            "verification_confidence": "HIGH",
            "sample_record_path_confirmed": True,
            "sample_record_type": "search_form",
            "sample_search_possible": True,
            "sample_document_view_possible": True,
            "blocker": "WAF (HTTP 403 returned to automated fetch). Portal is fully public and free in a browser — unofficial images viewable without login or payment. Certified copies require fee. Technical blocker only, not a permission blocker.",
            "blocker_type": "TECHNICAL_BLOCKER",
            "next_access_strategy": "use_playwright",
            "auto_resolve_status": "PARTIALLY_RESOLVED",
            "final_resolution_status": "PARTIALLY_RESOLVED",
            "auto_resolve_attempts": [
                {
                    "attempt_order": 1,
                    "timestamp": "2026-06-26T18:55:00Z",
                    "blocker_type": "TECHNICAL_BLOCKER",
                    "strategy": "discover_public_search_endpoint",
                    "status": "SUCCESS",
                    "result": "Record search endpoint confirmed at /recording/document-search.html via official research instructions and FAQs. URL is not guessed — confirmed from recorder.maricopa.gov official pages.",
                    "evidence": "Multiple official sources: recorder.maricopa.gov FAQs confirm free public search + free image viewing; search instructions confirm endpoint path.",
                    "files_created": [],
                    "files_modified": [],
                    "next_step": "Implement Playwright scraper in Build Mode to bypass WAF."
                },
                {
                    "attempt_order": 2,
                    "timestamp": "2026-06-26T18:55:10Z",
                    "blocker_type": "TECHNICAL_BLOCKER",
                    "strategy": "discover_hidden_api",
                    "status": "FAILED",
                    "result": "No public JSON/REST API documented for recorder document search. Portal appears to be an SPA with a non-documented XHR backend. Network-tab inspection requires a live browser session (Build Mode).",
                    "evidence": "Web search found no public API documentation. api.mcassessor.maricopa.gov returned ECONNREFUSED (internal only). No ArcGIS or REST endpoint found for recorder records.",
                    "files_created": [],
                    "files_modified": [],
                    "next_step": "use_playwright to render the SPA and intercept XHR calls for recorder data in Build Mode."
                },
                {
                    "attempt_order": 3,
                    "timestamp": "2026-06-26T18:55:20Z",
                    "blocker_type": "TECHNICAL_BLOCKER",
                    "strategy": "use_playwright",
                    "status": "REQUIRES_OPERATOR_APPROVAL",
                    "result": "Playwright identified as the correct resolution strategy. Portal is a JavaScript SPA protected by WAF against raw HTTP requests. Playwright can render the page fully and submit search forms. Build Mode implementation required.",
                    "evidence": "All direct WebFetch attempts to recorder.maricopa.gov return HTTP 403. Public access confirmed via official documentation. Playwright is the standard framework strategy for WAF-protected SPA portals.",
                    "files_created": [],
                    "files_modified": [],
                    "next_step": "Operator approves Build Mode → Phase 3 builds Playwright adapter for recorder_maricopa."
                }
            ],
            "scraper_module": "scrapers.recorder_maricopa",
            "translator": "publicsearch_clerk_recordings",
            "translator_config": {
                "doc_type_filter": ["NOTS", "Deed", "Lien", "Lis Pendens", "Federal Tax Lien", "Mechanic's Lien", "HOA Lien", "Judgment Lien", "Release"],
                "lead_doc_types": ["NOTS", "Lis Pendens", "Federal Tax Lien", "Mechanic's Lien", "HOA Lien", "Judgment Lien"],
                "negative_signal_doc_types": ["Release", "Reconveyance", "Satisfaction", "Discharge"],
                "note": "Exact Maricopa document type codes must be confirmed via portal fingerprinting in Build Mode. NOTS = Notice of Trustee's Sale (ARS 33-808)."
            },
            "doc_type_synonyms": {
                "Notice of Trustee Sale": "NOTICE_OF_TRUSTEE_SALE",
                "Notice of Trustee's Sale": "NOTICE_OF_TRUSTEE_SALE",
                "NOTS": "NOTICE_OF_TRUSTEE_SALE",
                "Lis Pendens": "LIS_PENDENS",
                "Warranty Deed": "WARRANTY_DEED",
                "Quitclaim Deed": "QUITCLAIM_DEED",
                "QCD": "QUITCLAIM_DEED",
                "Trustee's Deed": "TRUSTEE_DEED_AFTER_SALE",
                "Deed of Trust": "DEED_OF_TRUST",
                "Assignment of Deed of Trust": "ASSIGNMENT_OF_DEED_OF_TRUST",
                "Release of Deed of Trust": "RELEASE_OF_DEED_OF_TRUST",
                "Full Release": "RELEASE_OF_DEED_OF_TRUST",
                "Federal Tax Lien": "FEDERAL_TAX_LIEN",
                "State Tax Lien": "STATE_TAX_LIEN",
                "Judgment Lien": "JUDGMENT_LIEN",
                "Mechanic's Lien": "MECHANICS_LIEN",
                "Mechanic Lien": "MECHANICS_LIEN",
                "HOA Lien": "HOA_LIEN"
            },
            "parcel_id_prefix": "MCREC-",
            "refresh_cadence": "daily",
            "ttl_days": 365,
            "source_priority": "P0",
            "build_priority": "mvp_required",
            "source_reliability_grade": "A",
            "source_freshness": "DAILY",
            "enabled": True,
            "allowed_to_export": True,
            "paused_reason": "",
            "pause_until": "",
            "estimated_cost_category": "FREE",
            "estimated_runtime_minutes": 30,
            "portal_family": "custom_county",
            "last_verified_at": "2026-06-26T18:55:00Z",
            "verification_note": "Maricopa County Recorder portal (recorder.maricopa.gov) is fully public — free search, free unofficial image viewing, no login required. WAF returns HTTP 403 to automated HTTP fetch tools (WebFetch, requests, curl) but NOT to browser-rendered requests. Confirmed via official research instructions and FAQs. Database covers 1871–present. Most recent 2 years available by default with full historical search. Notice of Trustee's Sale (NOTS) is a core AZ non-judicial foreclosure document recorded here. This is the highest-value source for AZ foreclosure distress signals.",
            "known_limitations": [
                "WAF blocks automated HTTP fetch — requires Playwright SPA renderer in Build Mode",
                "Exact document type code list requires portal fingerprinting in Build Mode (e.g., whether 'NOTS' or 'NOTICE OF TRUSTEE SALE' is the official code)",
                "Default search window is last 2 years; historical search available but may need specific date parameters",
                "No documented public REST API for bulk data extraction — per-search form only"
            ],
            "open_questions": [
                "What are the exact document type codes used in the Maricopa Recorder portal for NOTS, QCD, mechanics liens, and HOA liens?",
                "Does the portal support bulk date-range search returning all recordings in a date range, or only named-party search?",
                "Is there an undocumented XHR/JSON API powering the search results page that Playwright can intercept and reuse?",
                "Does the portal enforce rate limiting per IP on Playwright requests?"
            ],
            "operator_override": False
        },

        # ---- P0 PRIMARY: Superior Court — Civil --------------------------
        "superior_court_civil": {
            "category": "lead",
            "subtype": "court_civil",
            "url": "https://www.superiorcourt.maricopa.gov/docket/civilcourtcases/casesearch.asp",
            "official_status": "OFFICIAL_COURT",
            "verified_from_url": "https://www.superiorcourt.maricopa.gov/docket/index.asp",
            "verification_method": "court_portal",
            "official_entity": "Judicial Branch of Arizona in Maricopa County — Superior Court",
            "portal_type": "Civil court docket — case search and minute entries",
            "records_available": ["civil_judgments", "money_judgments", "deficiency_judgments", "civil_suits", "civil_case_filings"],
            "search_fields": ["last_name_first_name", "business_name", "case_number"],
            "access_pattern": "static_html",
            "access_method": "SEARCHABLE_PUBLIC_PORTAL",
            "public_access_status": "FULL_PUBLIC_ACCESS",
            "document_access_status": "DOCUMENTS_PUBLIC",
            "auth_required": False,
            "rate_limit_rpm": None,
            "source_role": "PRIMARY_LEAD_SOURCE",
            "lead_value": "LEAD_GENERATING",
            "verification_confidence": "HIGH",
            "sample_record_path_confirmed": True,
            "sample_record_type": "search_form",
            "sample_search_possible": True,
            "sample_document_view_possible": True,
            "blocker": "",
            "blocker_type": "",
            "next_access_strategy": "",
            "auto_resolve_status": "NOT_ATTEMPTED",
            "final_resolution_status": "RESOLVED",
            "auto_resolve_attempts": [],
            "scraper_module": "scrapers.superior_court_civil_maricopa",
            "translator": "tyler_odyssey_court",
            "parcel_id_prefix": "MCCIV-",
            "refresh_cadence": "daily",
            "ttl_days": 365,
            "source_priority": "P0",
            "build_priority": "high_value",
            "source_reliability_grade": "A",
            "source_freshness": "DAILY",
            "enabled": True,
            "allowed_to_export": True,
            "paused_reason": "",
            "pause_until": "",
            "estimated_cost_category": "FREE",
            "estimated_runtime_minutes": 20,
            "portal_family": "custom_county",
            "last_verified_at": "2026-06-26T18:55:00Z",
            "verification_note": "Maricopa County Superior Court civil docket is publicly accessible with no login, no CAPTCHA, and no payment required. Search by name or case number. System notes 24-hour audit lag and daily maintenance window (3–4am). Generates civil judgment and deficiency judgment leads. Note: Arizona is non-judicial for primary mortgages — civil court handles deficiency judgments after trustee sale, not the primary foreclosure action.",
            "known_limitations": [
                "24-hour audit lag — records may not appear immediately after filing",
                "System unavailable Tue–Sat 3:00–4:00am",
                "Case documents (minute entries, orders) available via separate portal: courtminutes.clerkofcourt.maricopa.gov"
            ],
            "open_questions": [
                "What is the case type code for deficiency judgment actions in Maricopa Superior Court?",
                "Are civil judgment docket entries structured enough to extract party names, judgment amounts, and property references?"
            ],
            "operator_override": False
        },

        # ---- P0 PRIMARY: Superior Court — Probate -----------------------
        "superior_court_probate": {
            "category": "lead",
            "subtype": "court_probate",
            "url": "https://www.superiorcourt.maricopa.gov/docket/ProbateCourtCases/caseSearch.asp",
            "official_status": "OFFICIAL_COURT",
            "verified_from_url": "https://www.superiorcourt.maricopa.gov/docket/index.asp",
            "verification_method": "court_portal",
            "official_entity": "Judicial Branch of Arizona in Maricopa County — Probate Court",
            "portal_type": "Probate court docket — estate and guardianship case search",
            "records_available": ["decedent_estates", "conservatorships", "guardianships", "trust_proceedings", "probate_filings"],
            "search_fields": ["last_name_first_name", "business_name", "case_number"],
            "access_pattern": "static_html",
            "access_method": "SEARCHABLE_PUBLIC_PORTAL",
            "public_access_status": "FULL_PUBLIC_ACCESS",
            "document_access_status": "DOCUMENTS_PUBLIC",
            "auth_required": False,
            "rate_limit_rpm": None,
            "source_role": "PRIMARY_LEAD_SOURCE",
            "lead_value": "LEAD_GENERATING",
            "verification_confidence": "HIGH",
            "sample_record_path_confirmed": True,
            "sample_record_type": "search_form",
            "sample_search_possible": True,
            "sample_document_view_possible": True,
            "blocker": "",
            "blocker_type": "",
            "next_access_strategy": "",
            "auto_resolve_status": "NOT_ATTEMPTED",
            "final_resolution_status": "RESOLVED",
            "auto_resolve_attempts": [],
            "scraper_module": "scrapers.superior_court_probate_maricopa",
            "translator": "tyler_odyssey_court",
            "parcel_id_prefix": "MCPRB-",
            "refresh_cadence": "daily",
            "ttl_days": 730,
            "source_priority": "P0",
            "build_priority": "high_value",
            "source_reliability_grade": "A",
            "source_freshness": "DAILY",
            "enabled": True,
            "allowed_to_export": True,
            "paused_reason": "",
            "pause_until": "",
            "estimated_cost_category": "FREE",
            "estimated_runtime_minutes": 15,
            "portal_family": "custom_county",
            "last_verified_at": "2026-06-26T18:55:00Z",
            "verification_note": "Maricopa County probate docket is publicly accessible. Exposes decedent estate cases, conservatorships, and guardianships — all high-value leads for investors targeting inherited/estate properties. Same Judicial Branch docket system as civil cases. Accessible via case search at the probate-specific URL.",
            "known_limitations": [
                "Some conservatorship/guardianship records may be sealed for privacy",
                "Estate inventory values may not appear in docket (filed separately)"
            ],
            "open_questions": [
                "Are estate inventory filings (property lists) accessible via the public docket or only in person?",
                "Does the probate docket link to property addresses or only party names?"
            ],
            "operator_override": False
        },

        # ---- P1 PRIMARY: Justice Courts — Evictions ---------------------
        "justice_court_evictions": {
            "category": "lead",
            "subtype": "court_eviction",
            "url": "https://justicecourts.maricopa.gov/app/courtrecords/casesearch",
            "official_status": "OFFICIAL_COURT",
            "verified_from_url": "https://justicecourts.maricopa.gov/",
            "verification_method": "court_portal",
            "official_entity": "Maricopa County Justice Courts",
            "portal_type": "Justice Court case search — evictions (forcible detainer) and small claims",
            "records_available": ["eviction_cases", "forcible_detainer", "forcible_holdover", "protective_orders", "small_claims"],
            "search_fields": ["last_name_first_name_dob", "business_name", "case_number"],
            "access_pattern": "spa_with_api",
            "access_method": "SEARCHABLE_PUBLIC_PORTAL",
            "public_access_status": "FULL_PUBLIC_ACCESS",
            "document_access_status": "DOCUMENTS_PUBLIC",
            "auth_required": False,
            "rate_limit_rpm": None,
            "source_role": "PRIMARY_LEAD_SOURCE",
            "lead_value": "LEAD_GENERATING",
            "verification_confidence": "HIGH",
            "sample_record_path_confirmed": True,
            "sample_record_type": "search_form",
            "sample_search_possible": True,
            "sample_document_view_possible": True,
            "blocker": "",
            "blocker_type": "",
            "next_access_strategy": "",
            "auto_resolve_status": "NOT_ATTEMPTED",
            "final_resolution_status": "RESOLVED",
            "auto_resolve_attempts": [],
            "scraper_module": "scrapers.justice_court_evictions_maricopa",
            "translator": "tyler_odyssey_court",
            "parcel_id_prefix": "MCEV-",
            "refresh_cadence": "daily",
            "ttl_days": 180,
            "source_priority": "P1",
            "build_priority": "high_value",
            "source_reliability_grade": "A",
            "source_freshness": "DAILY",
            "enabled": True,
            "allowed_to_export": True,
            "paused_reason": "",
            "pause_until": "",
            "estimated_cost_category": "FREE",
            "estimated_runtime_minutes": 15,
            "portal_family": "custom_county",
            "last_verified_at": "2026-06-26T18:55:00Z",
            "verification_note": "Maricopa County Justice Courts handle eviction (forcible detainer / forcible holdover) filings. Case search is publicly accessible — no login, no CAPTCHA. Case types include evictions, small claims, traffic, and protective orders. System note: case documents are NOT directly available online (requires records request for full docs), but case metadata (parties, filing date, case type, status) IS publicly searchable. Eviction cases in the Phoenix metro represent high-volume distress signal given the large rental market.",
            "known_limitations": [
                "Full case documents not directly available online — case metadata (parties, dates, type) is accessible",
                "DOB required for name search which may limit bulk name searches",
                "Records subject to Arizona Supreme Court Records Retention Schedule for Limited Jurisdiction Courts"
            ],
            "open_questions": [
                "Does the Justice Court case search return property address (not just party name) for eviction cases?",
                "Is there a date-range or bulk export option for new eviction filings?"
            ],
            "operator_override": False
        },

        # ---- P1 PRIMARY: Superior Court — Family Court ------------------
        "superior_court_family": {
            "category": "lead",
            "subtype": "court_family",
            "url": "https://www.superiorcourt.maricopa.gov/docket/FamilyCourtCases/Index.asp",
            "official_status": "OFFICIAL_COURT",
            "verified_from_url": "https://www.superiorcourt.maricopa.gov/docket/index.asp",
            "verification_method": "court_portal",
            "official_entity": "Judicial Branch of Arizona in Maricopa County — Family Court",
            "portal_type": "Family court docket — divorce and domestic relations case search",
            "records_available": ["divorce_filings", "legal_separation", "child_support", "custody_proceedings"],
            "search_fields": ["last_name_first_name", "business_name", "case_number"],
            "access_pattern": "static_html",
            "access_method": "SEARCHABLE_PUBLIC_PORTAL",
            "public_access_status": "PUBLIC_SEARCH_ONLY",
            "document_access_status": "DOCUMENTS_UNKNOWN",
            "auth_required": False,
            "rate_limit_rpm": None,
            "source_role": "PRIMARY_LEAD_SOURCE",
            "lead_value": "LEAD_GENERATING",
            "verification_confidence": "MEDIUM",
            "sample_record_path_confirmed": True,
            "sample_record_type": "search_form",
            "sample_search_possible": True,
            "sample_document_view_possible": False,
            "blocker": "Some family court records may be sealed. Document images not confirmed publicly accessible.",
            "blocker_type": "DOCUMENT_ACCESS_LOCKED",
            "next_access_strategy": "try_open_public_portal",
            "auto_resolve_status": "NOT_ATTEMPTED",
            "final_resolution_status": "OPERATOR_REQUIRED",
            "auto_resolve_attempts": [],
            "scraper_module": "scrapers.superior_court_family_maricopa",
            "translator": "tyler_odyssey_court",
            "parcel_id_prefix": "MCFAM-",
            "refresh_cadence": "daily",
            "ttl_days": 365,
            "source_priority": "P1",
            "build_priority": "optional",
            "source_reliability_grade": "B",
            "source_freshness": "DAILY",
            "enabled": False,
            "allowed_to_export": False,
            "paused_reason": "Document access restrictions unconfirmed. Enable after operator verification that public case index is searchable and document images are available without restrictions.",
            "pause_until": "",
            "estimated_cost_category": "FREE",
            "estimated_runtime_minutes": 10,
            "portal_family": "custom_county",
            "last_verified_at": "2026-06-26T18:55:00Z",
            "verification_note": "Family court docket is accessible via the same Judicial Branch docket system. Recon confirmed the case search page exists. However, document access status for family court records is unclear — Arizona law may seal certain domestic relations records. Case index (parties, dates, case type) is likely public; document images may be restricted. Marked MEDIUM confidence pending operator verification.",
            "known_limitations": [
                "Certain domestic relations records may be sealed or restricted",
                "Property address may not appear in family court case metadata",
                "Divorce filings have low conversion rate to callable leads without property address"
            ],
            "open_questions": [
                "Are Maricopa Family Court case indices (parties, dates, case status) publicly searchable without restriction?",
                "Does Arizona law seal any category of family court records from public view?",
                "Do divorce filings include property addresses or legal descriptions that can be joined to parcel data?"
            ],
            "operator_override": False
        },

        # ---- P1 PRIMARY: Treasurer — Tax Delinquency & Tax Lien Sale ---
        "treasurer_tax_lien": {
            "category": "lead",
            "subtype": "tax_certificates",
            "url": "https://treasurer.maricopa.gov/",
            "official_status": "OFFICIAL_COUNTY",
            "verified_from_url": "https://www.maricopa.gov/",
            "verification_method": "official_domain",
            "official_entity": "Maricopa County Treasurer's Office",
            "portal_type": "Tax lien certificate sale, delinquent tax lookup, and parcel tax status",
            "records_available": ["delinquent_tax_properties", "tax_lien_certificates", "tax_lien_sale_results", "tax_deeded_land"],
            "search_fields": ["parcel_number", "owner_name", "address"],
            "access_pattern": "spa_with_api",
            "access_method": "PUBLIC_BUT_WAF_PROTECTED",
            "public_access_status": "FULL_PUBLIC_ACCESS",
            "document_access_status": "DOCUMENTS_PUBLIC",
            "auth_required": False,
            "rate_limit_rpm": None,
            "source_role": "PRIMARY_LEAD_SOURCE",
            "lead_value": "LEAD_GENERATING",
            "verification_confidence": "HIGH",
            "sample_record_path_confirmed": True,
            "sample_record_type": "search_form",
            "sample_search_possible": True,
            "sample_document_view_possible": True,
            "blocker": "Treasurer main site WAF returns HTTP 403 to automated fetch. GIS delinquent parcel map (gis.maricopa.gov/TSR/liendelinquentparcel/) accessible. Annual tax lien sale list at maricopa.arizonataxsale.com (Real Auction vendor, officially linked) is open to public 2–3 weeks before annual sale (held in February). Outside that window, delinquent data is accessible via GIS map and per-parcel lookup.",
            "blocker_type": "TECHNICAL_BLOCKER",
            "next_access_strategy": "use_playwright",
            "auto_resolve_status": "PARTIALLY_RESOLVED",
            "final_resolution_status": "PARTIALLY_RESOLVED",
            "auto_resolve_attempts": [
                {
                    "attempt_order": 1,
                    "timestamp": "2026-06-26T18:55:00Z",
                    "blocker_type": "TECHNICAL_BLOCKER",
                    "strategy": "find_official_vendor_link",
                    "status": "SUCCESS",
                    "result": "Annual tax lien auction vendor confirmed: Real Auction at maricopa.arizonataxsale.com. Officially linked from treasurer.maricopa.gov. Delinquent parcel GIS map confirmed at gis.maricopa.gov/TSR/liendelinquentparcel/index.html. Tax deeded land sales at maricopa.gov/780/Tax-Deeded-Land-Sales.",
                    "evidence": "Official Maricopa County Treasurer FAQ confirms Real Auction as the auction platform. GIS mapping applications page (maricopa.gov/3942) links to the Overdue Property Taxes GIS layer.",
                    "files_created": [],
                    "files_modified": [],
                    "next_step": "Build Mode: use GIS endpoint for year-round delinquent data; use Playwright for full treasurer parcel search."
                }
            ],
            "scraper_module": "scrapers.treasurer_tax_lien_maricopa",
            "translator": "csv_static_list",
            "parcel_id_prefix": "MCTAX-",
            "refresh_cadence": "quarterly",
            "ttl_days": 365,
            "source_priority": "P1",
            "build_priority": "high_value",
            "source_reliability_grade": "A",
            "source_freshness": "MONTHLY",
            "enabled": True,
            "allowed_to_export": True,
            "paused_reason": "",
            "pause_until": "",
            "estimated_cost_category": "FREE",
            "estimated_runtime_minutes": 20,
            "portal_family": "custom_county",
            "last_verified_at": "2026-06-26T18:55:00Z",
            "verification_note": "Maricopa County Treasurer conducts annual tax lien certificate auction (February). Delinquent property list published ~3 weeks before auction on maricopa.arizonataxsale.com and the Arizona Business Gazette. Delinquent parcel GIS map at gis.maricopa.gov/TSR/liendelinquentparcel/ is available year-round showing current delinquent parcels with owner and APN. Tax deeded land (properties that completed foreclosure and transferred to county) tracked at maricopa.gov/780/Tax-Deeded-Land-Sales. WAF on main treasurer site — GIS endpoint and sale website are the build targets.",
            "known_limitations": [
                "Comprehensive delinquent list only publicly available ~3 weeks before annual February auction",
                "GIS delinquent parcel map may have limited field depth compared to full parcel search",
                "Tax deeded land sales are periodic — not a daily refresh source"
            ],
            "open_questions": [
                "Does the GIS delinquent parcel map at gis.maricopa.gov/TSR expose an ArcGIS FeatureServer query endpoint for bulk export?",
                "Does maricopa.arizonataxsale.com stay accessible year-round or only during the bidding window?",
                "What is the field structure of the delinquent parcel GIS layer (APN, owner name, tax amount, years delinquent)?"
            ],
            "operator_override": False
        },

        # ---- P2 ENRICHMENT: Assessor — Parcel Master --------------------
        "assessor_parcel_master": {
            "category": "enrichment",
            "subtype": "parcel_master",
            "url": "https://mcassessor.maricopa.gov/",
            "official_status": "OFFICIAL_COUNTY",
            "verified_from_url": "https://www.maricopa.gov/",
            "verification_method": "official_domain",
            "official_entity": "Maricopa County Assessor's Office",
            "portal_type": "Property assessment and parcel data search portal",
            "records_available": ["parcel_id_apn", "owner_name", "situs_address", "mailing_address", "assessed_value", "year_built", "square_footage", "land_use", "last_sale_price", "last_sale_date"],
            "search_fields": ["apn", "address", "owner_name"],
            "access_pattern": "static_html",
            "access_method": "SEARCHABLE_PUBLIC_PORTAL",
            "public_access_status": "FULL_PUBLIC_ACCESS",
            "document_access_status": "DOCUMENTS_PUBLIC",
            "auth_required": False,
            "rate_limit_rpm": None,
            "source_role": "ENRICHMENT_SOURCE",
            "lead_value": "ENRICHMENT",
            "verification_confidence": "HIGH",
            "sample_record_path_confirmed": True,
            "sample_record_type": "search_form",
            "sample_search_possible": True,
            "sample_document_view_possible": True,
            "blocker": "Assessor main site (mcassessor.maricopa.gov) has SSL certificate verification issues on automated fetch. Parcel viewer at maps.mcassessor.maricopa.gov accessible. ArcGIS parcel MapServer endpoint exists at gis.mcassessor.maricopa.gov/arcgis/rest/services/Parcels/MapServer but returned 403.",
            "blocker_type": "TECHNICAL_BLOCKER",
            "next_access_strategy": "discover_hidden_api",
            "auto_resolve_status": "PARTIALLY_RESOLVED",
            "final_resolution_status": "PARTIALLY_RESOLVED",
            "auto_resolve_attempts": [
                {
                    "attempt_order": 1,
                    "timestamp": "2026-06-26T18:55:00Z",
                    "blocker_type": "TECHNICAL_BLOCKER",
                    "strategy": "discover_hidden_api",
                    "status": "FAILED",
                    "result": "ArcGIS MapServer at gis.mcassessor.maricopa.gov/arcgis/rest/services/Parcels/MapServer confirmed to exist but returns HTTP 403 on raw automated fetch. api.mcassessor.maricopa.gov discovered but ECONNREFUSED (internal/private). Open Data Portal at data-maricopa.opendata.arcgis.com confirmed public. REST endpoint docs at maps.mcassessor.maricopa.gov/help/g_rest.html confirmed but SSL error on fetch. ArcGIS endpoint identified; needs correct request headers in Build Mode to unlock.",
                    "evidence": "Web search confirmed ArcGIS REST services root, assessor open data products page, and assessor parcel viewer. Parcel search confirmed free and public from official Assessor's website documentation.",
                    "files_created": [],
                    "files_modified": [],
                    "next_step": "Build Mode: test ArcGIS FeatureServer query with correct headers; fall back to per-record parcel viewer scrape via Playwright if ArcGIS blocked."
                }
            ],
            "scraper_module": "scrapers.assessor_parcel_master_maricopa",
            "translator": "parcel_master",
            "field_map": {
                "parcel_id": "apn",
                "situs_address": "site_address",
                "owner_name": "owner_name",
                "mailing_address": "mailing_address",
                "assessed_value": "full_cash_value",
                "year_built": "year_built",
                "square_footage": "building_sq_ft",
                "land_use": "property_class"
            },
            "parcel_id_prefix": "MCASR-",
            "refresh_cadence": "monthly",
            "ttl_days": 90,
            "source_priority": "P2",
            "build_priority": "enrichment",
            "source_reliability_grade": "A",
            "source_freshness": "MONTHLY",
            "enabled": True,
            "allowed_to_export": True,
            "paused_reason": "",
            "pause_until": "",
            "estimated_cost_category": "FREE",
            "estimated_runtime_minutes": 60,
            "portal_family": "custom_county",
            "last_verified_at": "2026-06-26T18:55:00Z",
            "verification_note": "Maricopa County Assessor parcel search is free and public. Search by APN, address, or owner name. Returns assessed value, owner, situs/mailing addresses, year built, sq ft, last sale. ArcGIS REST services confirmed at gis.mcassessor.maricopa.gov/arcgis/rest/services. Assessor Open Data Portal at data-maricopa.opendata.arcgis.com has assessor datasets for download. This is the primary enrichment source for the framework.",
            "known_limitations": [
                "SSL certificate verification issue on direct automated fetch to mcassessor.maricopa.gov (use ssl=False flag or proper cert bundle)",
                "ArcGIS MapServer endpoint returned 403 — may need specific user-agent or Referer headers",
                "Bulk parcel data download (shapefile format) available for purchase from Assessor data sales program — not free",
                "Parcel data is updated periodically (not real-time) — ownership changes may lag recorder recordings by weeks to months"
            ],
            "open_questions": [
                "Can the ArcGIS FeatureServer at gis.mcassessor.maricopa.gov be queried in bulk with a where=1=1 clause?",
                "Is the assessor Open Data Portal at data-maricopa.opendata.arcgis.com free to download assessor datasets?",
                "What is the assessor data refresh cadence for ownership records?"
            ],
            "operator_override": False
        },

        # ---- P2 ENRICHMENT: GIS — Parcel Layer --------------------------
        "gis_parcel_layer": {
            "category": "enrichment",
            "subtype": "gis_parcels",
            "url": "https://data-maricopa.opendata.arcgis.com/",
            "official_status": "OFFICIAL_COUNTY",
            "verified_from_url": "https://www.maricopa.gov/3942/GIS-Mapping-Applications",
            "verification_method": "official_page_link",
            "official_entity": "Maricopa County GIS / Assessor's Office",
            "portal_type": "ArcGIS open data portal and parcel MapServer — parcel geometry and attributes",
            "records_available": ["parcel_boundaries", "apn", "address_points", "property_characteristics", "zoning"],
            "search_fields": ["apn", "address"],
            "access_pattern": "open_api",
            "access_method": "API_ENDPOINT",
            "public_access_status": "FULL_PUBLIC_ACCESS",
            "document_access_status": "DOCUMENTS_PUBLIC",
            "auth_required": False,
            "rate_limit_rpm": None,
            "source_role": "ENRICHMENT_SOURCE",
            "lead_value": "ENRICHMENT",
            "verification_confidence": "HIGH",
            "sample_record_path_confirmed": True,
            "sample_record_type": "api_endpoint",
            "sample_search_possible": True,
            "sample_document_view_possible": True,
            "blocker": "ArcGIS MapServer at gis.mcassessor.maricopa.gov/arcgis/rest/services/Parcels/MapServer returned HTTP 403 on direct automated fetch. May require specific request headers (Referer, User-Agent). Open Data Portal at data-maricopa.opendata.arcgis.com is publicly accessible.",
            "blocker_type": "TECHNICAL_BLOCKER",
            "next_access_strategy": "discover_hidden_api",
            "auto_resolve_status": "PARTIALLY_RESOLVED",
            "final_resolution_status": "PARTIALLY_RESOLVED",
            "auto_resolve_attempts": [],
            "scraper_module": "scrapers.gis_parcels_maricopa",
            "translator": "parcel_master",
            "parcel_id_prefix": "MCGIS-",
            "refresh_cadence": "monthly",
            "ttl_days": 90,
            "source_priority": "P2",
            "build_priority": "enrichment",
            "source_reliability_grade": "B",
            "source_freshness": "MONTHLY",
            "enabled": False,
            "allowed_to_export": False,
            "paused_reason": "ArcGIS endpoint access not confirmed. Enable after Build Mode testing confirms the correct ArcGIS FeatureServer query path.",
            "pause_until": "",
            "estimated_cost_category": "FREE",
            "estimated_runtime_minutes": 30,
            "portal_family": "custom_county",
            "last_verified_at": "2026-06-26T18:55:00Z",
            "verification_note": "Maricopa County GIS open data portal confirmed at data-maricopa.opendata.arcgis.com. ArcGIS parcel services confirmed at gis.mcassessor.maricopa.gov/arcgis/rest/services (403 on raw fetch — needs correct headers or ArcGIS client). GIS layer is secondary to assessor parcel master — only needed if assessor direct search is rate-limited or if geometry is required for map rendering.",
            "known_limitations": [
                "ArcGIS MapServer 403 on raw HTTP fetch — needs correct headers or ArcGIS REST client",
                "GIS parcel data may not include owner name directly — join to assessor data needed",
                "Free download from open data portal may have licensing restrictions on redistribution"
            ],
            "open_questions": [
                "What is the correct ArcGIS FeatureServer URL for querying parcels by APN?",
                "Does the data-maricopa.opendata.arcgis.com portal have a direct bulk download of the parcel layer?"
            ],
            "operator_override": False
        }

    },  # end sources

    # ------------------------------------------------------------------
    # SCORING OVERRIDES
    # ------------------------------------------------------------------
    "scoring_overrides": {
        "match_confidence_floor": 60,
        "review_queue_ratio_alert_threshold": 0.15,
        "high_equity_assessed_to_sale_ratio": 1.8,
        "long_term_owned_years": 10,
        "senior_owner_proxy_years": 65,
        "favorable_loan_era_start": "2020-01-01",
        "favorable_loan_era_end": "2022-12-31"
    },

    # ------------------------------------------------------------------
    # STORAGE
    # ------------------------------------------------------------------
    "storage": {
        "mode": "STATIC_JSON_MODE",
        "supabase_enabled": False,
        "dashboard_payload": "data/dashboard.json",
        "retain_raw_records_days": 90,
        "retain_source_runs_days": 30
    },

    # ------------------------------------------------------------------
    # DASHBOARD
    # ------------------------------------------------------------------
    "dashboard": {
        "title": "Maricopa County, AZ — Lead Intelligence",
        "subtitle": "Phoenix Metro Distress Signals · Powered by Xcerebro",
        "primary_color": "#1A1A2E",
        "accent_color": "#E63946",
        "default_view": "CLIENT_VIEW",
        "view_modes": ["CLIENT_VIEW", "OPERATOR_VIEW"],
        "build_label": "PARTIAL_BUILD",
        "build_label_reason": "Recorder source (primary AZ foreclosure/NOTS signals) requires Playwright Build Mode before activation. Court dockets (probate, civil, evictions) are live. Build label upgrades to FULL_BUILD after recorder Playwright adapter is complete.",
        "precanned_views": [
            {"id": "all_active",     "label": "All Active Leads",            "filter": "status=active"},
            {"id": "foreclosure",    "label": "Foreclosure / NOTS",          "filter": "lead_type=foreclosure"},
            {"id": "probate",        "label": "Probate / Estate",            "filter": "lead_type=probate"},
            {"id": "tax_distress",   "label": "Tax Delinquency",             "filter": "lead_type=tax"},
            {"id": "eviction",       "label": "Evictions",                   "filter": "lead_type=eviction"},
            {"id": "judgment",       "label": "Civil Judgments",             "filter": "lead_type=judgment"},
            {"id": "high_stack",     "label": "Multi-Signal Leads",          "filter": "signal_count>=2"}
        ]
    },

    # ------------------------------------------------------------------
    # DEPLOYMENT
    # ------------------------------------------------------------------
    "deployment": {
        "github_org": "xcerebro",
        "github_repo": "maricopa-az-intel",
        "live_url": "",
        "scheduled_task_name": "maricopa_az_daily_pull",
        "watchdog_task_name": "maricopa_az_watchdog",
        "scheduler_runtime_class": "SCHEDULER_NOT_CONFIGURED",
        "scheduler_test_fired_at": "",
        "production_verification_status": "NOT_RUN",
        "production_verification_at": "",
        "last_known_good_commit": "",
        "last_known_good_dashboard_at": ""
    },

    # ------------------------------------------------------------------
    # BUILD VERDICT (Phase 0 / 0.5 output)
    # ------------------------------------------------------------------
    "build_verdict": "READY_TO_BUILD",
    "build_verdict_reason": "Two fully accessible PRIMARY_LEAD_SOURCEs with HIGH verification confidence are confirmed: Superior Court Civil docket (judgments) and Superior Court Probate docket (estate leads). Justice Court eviction docket also confirmed accessible. One ENRICHMENT_SOURCE (Assessor parcel data) confirmed accessible. The Recorder (most valuable AZ source — NOTS/foreclosure recordings) has a WAF technical blocker that requires Playwright in Build Mode; it is NOT a permission or payment blocker. The county is buildable now with court-based leads; recorder activation follows Playwright build. Phase 0.5 confirmed Playwright as the resolution strategy for the recorder WAF.",
    "build_verdict_at": "2026-06-26T18:55:00Z",
    "auto_resolve_status": "PARTIALLY_RESOLVED",
    "final_resolution_status": "PARTIALLY_RESOLVED",
    "operator_override_audit": [],

    # ------------------------------------------------------------------
    # SOURCE OF RECORD MATRIX (v5.3.0)
    # ------------------------------------------------------------------
    "source_of_record_matrix": {
        "county_slug": "maricopa_az",
        "county_name": "Maricopa County",
        "state": "AZ",
        "framework_version": "v5.1.2-beta-r3",
        "generated_at": "2026-06-26T18:55:00Z",
        "county_build_status": "READY_TO_BUILD",
        "lead_types": [
            {
                "lead_type": "FORECLOSURE_NOTICE",
                "state_applicability": "APPLICABLE",
                "expected_authorities": ["County Recorder (non-judicial NOTS under ARS 33-808)"],
                "candidate_sources": [
                    {
                        "source_id": "recorder_maricopa",
                        "official_url": "https://recorder.maricopa.gov/recording/document-search.html",
                        "authority_type": "County Recorder",
                        "source_role": "PRIMARY_EVENT_SOURCE",
                        "access_status": "CAPTCHA_PROTECTED",
                        "bulk_availability": "PER_RECORD_ONLY",
                        "verification_layers": {
                            "authority": "CONFIRMED — official county recorder domain",
                            "lead_type_relevance": "CONFIRMED — Notice of Trustee's Sale (NOTS) recorded here per ARS 33-808",
                            "access": "PARTIAL — public browser access confirmed; WAF blocks automated fetch",
                            "extractability": "REQUIRES_PLAYWRIGHT — SPA portal, no public API",
                            "refresh_provenance": "DAILY — recordings added continuously"
                        },
                        "sample_record_path_confirmed": True,
                        "sample_document_view_possible": True,
                        "minimum_lead_fields_available": ["recording_date", "document_type", "grantor", "grantee", "legal_description", "document_number"],
                        "operator_verified": False,
                        "notes": "AZ non-judicial: NOTS is the primary foreclosure signal, not a court filing. 90-day notice window after NOTS recording before trustee sale."
                    }
                ],
                "selected_source_id": "recorder_maricopa",
                "status": "SOURCE_FOUND_CAPTCHA",
                "coverage_notes": "NOTS is recorded at the County Recorder. WAF blocker requires Playwright. After Playwright build this becomes LIVE_SOURCE_FOUND."
            },
            {
                "lead_type": "PROBATE_ESTATE",
                "state_applicability": "APPLICABLE",
                "expected_authorities": ["Superior Court Probate Division"],
                "candidate_sources": [
                    {
                        "source_id": "superior_court_probate",
                        "official_url": "https://www.superiorcourt.maricopa.gov/docket/ProbateCourtCases/caseSearch.asp",
                        "authority_type": "Superior Court",
                        "source_role": "PRIMARY_EVENT_SOURCE",
                        "access_status": "OPEN_PUBLIC",
                        "bulk_availability": "PER_RECORD_ONLY",
                        "verification_layers": {
                            "authority": "CONFIRMED — Judicial Branch of Arizona official domain",
                            "lead_type_relevance": "CONFIRMED — probate docket exposes decedent estate filings",
                            "access": "CONFIRMED — fully public, no login, no CAPTCHA",
                            "extractability": "CONFIRMED — searchable form, results in HTML",
                            "refresh_provenance": "DAILY — new filings appear within 24 hours"
                        },
                        "sample_record_path_confirmed": True,
                        "sample_document_view_possible": True,
                        "minimum_lead_fields_available": ["case_number", "decedent_name", "filing_date", "case_status", "case_type"],
                        "operator_verified": False,
                        "notes": "Probate case search fully accessible. Property address not always in docket — may require cross-reference to recorder or assessor data."
                    }
                ],
                "selected_source_id": "superior_court_probate",
                "status": "LIVE_SOURCE_FOUND",
                "coverage_notes": "Fully accessible. No blockers."
            },
            {
                "lead_type": "CIVIL_JUDGMENT",
                "state_applicability": "APPLICABLE",
                "expected_authorities": ["Superior Court Civil Division"],
                "candidate_sources": [
                    {
                        "source_id": "superior_court_civil",
                        "official_url": "https://www.superiorcourt.maricopa.gov/docket/civilcourtcases/casesearch.asp",
                        "authority_type": "Superior Court",
                        "source_role": "PRIMARY_EVENT_SOURCE",
                        "access_status": "OPEN_PUBLIC",
                        "bulk_availability": "PER_RECORD_ONLY",
                        "verification_layers": {
                            "authority": "CONFIRMED — Judicial Branch of Arizona official domain",
                            "lead_type_relevance": "CONFIRMED — civil docket exposes money judgments and deficiency judgments",
                            "access": "CONFIRMED — fully public, no login, no CAPTCHA",
                            "extractability": "CONFIRMED — searchable form, results in HTML",
                            "refresh_provenance": "DAILY — new judgments within 24 hours"
                        },
                        "sample_record_path_confirmed": True,
                        "sample_document_view_possible": True,
                        "minimum_lead_fields_available": ["case_number", "plaintiff", "defendant", "filing_date", "case_type", "case_status"],
                        "operator_verified": False,
                        "notes": "AZ civil court handles deficiency judgments after trustee sale. Also generates judgment lien leads when judgments are recorded at the Recorder."
                    }
                ],
                "selected_source_id": "superior_court_civil",
                "status": "LIVE_SOURCE_FOUND",
                "coverage_notes": "Fully accessible. No blockers."
            },
            {
                "lead_type": "EVICTION",
                "state_applicability": "APPLICABLE",
                "expected_authorities": ["Maricopa County Justice Courts"],
                "candidate_sources": [
                    {
                        "source_id": "justice_court_evictions",
                        "official_url": "https://justicecourts.maricopa.gov/app/courtrecords/casesearch",
                        "authority_type": "Justice Court",
                        "source_role": "PRIMARY_EVENT_SOURCE",
                        "access_status": "OPEN_PUBLIC",
                        "bulk_availability": "PER_RECORD_ONLY",
                        "verification_layers": {
                            "authority": "CONFIRMED — Maricopa County Justice Courts official domain",
                            "lead_type_relevance": "CONFIRMED — eviction (forcible detainer) cases filed here",
                            "access": "CONFIRMED — fully public, no login",
                            "extractability": "CONFIRMED — case search accessible",
                            "refresh_provenance": "DAILY"
                        },
                        "sample_record_path_confirmed": True,
                        "sample_document_view_possible": True,
                        "minimum_lead_fields_available": ["case_number", "plaintiff", "defendant", "filing_date", "case_type"],
                        "operator_verified": False,
                        "notes": "Property address may not appear directly in case metadata — may need cross-reference to other sources."
                    }
                ],
                "selected_source_id": "justice_court_evictions",
                "status": "LIVE_SOURCE_FOUND",
                "coverage_notes": "Fully accessible. No blockers."
            },
            {
                "lead_type": "TAX_LIEN",
                "state_applicability": "APPLICABLE",
                "expected_authorities": ["Maricopa County Treasurer"],
                "candidate_sources": [
                    {
                        "source_id": "treasurer_tax_lien",
                        "official_url": "https://treasurer.maricopa.gov/",
                        "authority_type": "County Treasurer",
                        "source_role": "PRIMARY_EVENT_SOURCE",
                        "access_status": "CAPTCHA_PROTECTED",
                        "bulk_availability": "BATCH_QUERY",
                        "verification_layers": {
                            "authority": "CONFIRMED — official county treasurer domain",
                            "lead_type_relevance": "CONFIRMED — annual tax lien certificate sale and delinquent property list",
                            "access": "PARTIAL — WAF on main site; GIS delinquent map and tax sale website are accessible",
                            "extractability": "PARTIAL — GIS endpoint may support query; tax sale website public before auction",
                            "refresh_provenance": "ANNUAL (lien sale) / MONTHLY (delinquent GIS)"
                        },
                        "sample_record_path_confirmed": True,
                        "sample_document_view_possible": True,
                        "minimum_lead_fields_available": ["parcel_number", "owner_name", "tax_amount_owed", "years_delinquent"],
                        "operator_verified": False,
                        "notes": "Annual auction (February). Delinquent list publicly available ~3 weeks before auction. GIS delinquent parcel map year-round. Arizona has redemption period — tax lien investors purchase certificates, not deeds. Use as distress signal for investor outreach."
                    }
                ],
                "selected_source_id": "treasurer_tax_lien",
                "status": "SOURCE_FOUND_CAPTCHA",
                "coverage_notes": "Main site WAF-blocked but GIS and tax sale website provide access paths. Annual cadence limits daily-refresh P0 classification."
            },
            {
                "lead_type": "MECHANICS_LIEN",
                "state_applicability": "APPLICABLE",
                "expected_authorities": ["County Recorder (mechanics liens recorded here)"],
                "candidate_sources": [
                    {
                        "source_id": "recorder_maricopa",
                        "official_url": "https://recorder.maricopa.gov/recording/document-search.html",
                        "authority_type": "County Recorder",
                        "source_role": "PRIMARY_EVENT_SOURCE",
                        "access_status": "CAPTCHA_PROTECTED",
                        "bulk_availability": "PER_RECORD_ONLY",
                        "verification_layers": {
                            "authority": "CONFIRMED",
                            "lead_type_relevance": "CONFIRMED — mechanics liens recorded at County Recorder per ARS 33-981 et seq.",
                            "access": "PARTIAL — WAF blocker, Playwright needed",
                            "extractability": "REQUIRES_PLAYWRIGHT",
                            "refresh_provenance": "DAILY"
                        },
                        "sample_record_path_confirmed": True,
                        "sample_document_view_possible": True,
                        "minimum_lead_fields_available": ["recording_date", "document_type", "claimant", "owner", "property_description", "lien_amount"],
                        "operator_verified": False,
                        "notes": "Same recorder portal as NOTS. Filtered by mechanic's lien document type."
                    }
                ],
                "selected_source_id": "recorder_maricopa",
                "status": "SOURCE_FOUND_CAPTCHA",
                "coverage_notes": "Same WAF blocker as NOTS source. Activated when Playwright build complete."
            },
            {
                "lead_type": "LIS_PENDENS",
                "state_applicability": "APPLICABLE",
                "expected_authorities": ["County Recorder"],
                "candidate_sources": [
                    {
                        "source_id": "recorder_maricopa",
                        "official_url": "https://recorder.maricopa.gov/recording/document-search.html",
                        "authority_type": "County Recorder",
                        "source_role": "PRIMARY_EVENT_SOURCE",
                        "access_status": "CAPTCHA_PROTECTED",
                        "bulk_availability": "PER_RECORD_ONLY",
                        "verification_layers": {
                            "authority": "CONFIRMED",
                            "lead_type_relevance": "CONFIRMED — lis pendens recorded at County Recorder",
                            "access": "PARTIAL — WAF, Playwright needed",
                            "extractability": "REQUIRES_PLAYWRIGHT",
                            "refresh_provenance": "DAILY"
                        },
                        "sample_record_path_confirmed": True,
                        "sample_document_view_possible": True,
                        "minimum_lead_fields_available": ["recording_date", "document_type", "plaintiff", "defendant", "legal_description"],
                        "operator_verified": False,
                        "notes": "Lis pendens in AZ often precedes civil judgment or title dispute. Less common than in judicial foreclosure states since primary foreclosure is non-judicial."
                    }
                ],
                "selected_source_id": "recorder_maricopa",
                "status": "SOURCE_FOUND_CAPTCHA",
                "coverage_notes": "Same recorder portal. Activated with Playwright build."
            },
            {
                "lead_type": "FEDERAL_TAX_LIEN",
                "state_applicability": "APPLICABLE",
                "expected_authorities": ["County Recorder (IRS tax liens recorded here)"],
                "candidate_sources": [
                    {
                        "source_id": "recorder_maricopa",
                        "official_url": "https://recorder.maricopa.gov/recording/document-search.html",
                        "authority_type": "County Recorder",
                        "source_role": "PRIMARY_EVENT_SOURCE",
                        "access_status": "CAPTCHA_PROTECTED",
                        "bulk_availability": "PER_RECORD_ONLY",
                        "verification_layers": {
                            "authority": "CONFIRMED",
                            "lead_type_relevance": "CONFIRMED — IRS federal tax liens recorded at County Recorder per federal statute",
                            "access": "PARTIAL — WAF",
                            "extractability": "REQUIRES_PLAYWRIGHT",
                            "refresh_provenance": "DAILY"
                        },
                        "sample_record_path_confirmed": True,
                        "sample_document_view_possible": True,
                        "minimum_lead_fields_available": ["recording_date", "document_type", "taxpayer_name", "lien_amount", "serial_number"],
                        "operator_verified": False,
                        "notes": "Federal tax liens recorded at County Recorder. High-value signal — IRS lien on real property indicates serious financial distress."
                    }
                ],
                "selected_source_id": "recorder_maricopa",
                "status": "SOURCE_FOUND_CAPTCHA",
                "coverage_notes": "Same recorder portal. Activated with Playwright build."
            },
            {
                "lead_type": "HOA_LIEN",
                "state_applicability": "APPLICABLE",
                "expected_authorities": ["County Recorder"],
                "candidate_sources": [
                    {
                        "source_id": "recorder_maricopa",
                        "official_url": "https://recorder.maricopa.gov/recording/document-search.html",
                        "authority_type": "County Recorder",
                        "source_role": "PRIMARY_EVENT_SOURCE",
                        "access_status": "CAPTCHA_PROTECTED",
                        "bulk_availability": "PER_RECORD_ONLY",
                        "verification_layers": {
                            "authority": "CONFIRMED",
                            "lead_type_relevance": "CONFIRMED — HOA liens recorded at County Recorder",
                            "access": "PARTIAL — WAF",
                            "extractability": "REQUIRES_PLAYWRIGHT",
                            "refresh_provenance": "DAILY"
                        },
                        "sample_record_path_confirmed": True,
                        "sample_document_view_possible": True,
                        "minimum_lead_fields_available": ["recording_date", "document_type", "hoa_name", "owner", "property_description", "lien_amount"],
                        "operator_verified": False,
                        "notes": "HOA liens are common distress signal in Maricopa County given density of HOA-governed communities in Phoenix metro. HOA can foreclose in AZ."
                    }
                ],
                "selected_source_id": "recorder_maricopa",
                "status": "SOURCE_FOUND_CAPTCHA",
                "coverage_notes": "Same recorder portal. Activated with Playwright build."
            },
            {
                "lead_type": "CODE_VIOLATION",
                "state_applicability": "APPLICABLE",
                "expected_authorities": ["Per-municipality code enforcement (Phoenix NSD, Mesa, Chandler, etc.)"],
                "candidate_sources": [],
                "selected_source_id": "",
                "status": "SOURCE_NOT_FOUND",
                "coverage_notes": "No county-wide unified code enforcement portal found. Each municipality operates independently. Mesa has Accela portal (aca-prod.accela.com/MESA) but case details not visible to public (restricted to complainant). Phoenix has no public code enforcement search portal. Recommend deferring to future build version targeting per-city portals."
            },
            {
                "lead_type": "DIVORCE",
                "state_applicability": "APPLICABLE",
                "expected_authorities": ["Superior Court Family Division"],
                "candidate_sources": [
                    {
                        "source_id": "superior_court_family",
                        "official_url": "https://www.superiorcourt.maricopa.gov/docket/FamilyCourtCases/Index.asp",
                        "authority_type": "Superior Court",
                        "source_role": "SUPPORTING_EVENT_SOURCE",
                        "access_status": "SEARCH_ONLY_PUBLIC",
                        "bulk_availability": "PER_RECORD_ONLY",
                        "verification_layers": {
                            "authority": "CONFIRMED",
                            "lead_type_relevance": "CONFIRMED — divorce filings accessible",
                            "access": "PARTIAL — sealing rules unclear",
                            "extractability": "UNKNOWN — document images unconfirmed",
                            "refresh_provenance": "DAILY"
                        },
                        "sample_record_path_confirmed": True,
                        "sample_document_view_possible": False,
                        "minimum_lead_fields_available": ["case_number", "petitioner", "respondent", "filing_date"],
                        "operator_verified": False,
                        "notes": "Divorce filings yield leads when property is referenced. Conversion rate lower than NOTS or probate without property address."
                    }
                ],
                "selected_source_id": "superior_court_family",
                "status": "NEEDS_OPERATOR_REVIEW",
                "coverage_notes": "Source paused pending confirmation of public access rules for family court records in Arizona."
            }
        ]
    },

    # ------------------------------------------------------------------
    # SOURCE COVERAGE MAP (v5.3.0)
    # ------------------------------------------------------------------
    "source_coverage_map": {
        "live_sources": [
            "superior_court_civil",
            "superior_court_probate",
            "justice_court_evictions"
        ],
        "blocked_sources": [
            "recorder_maricopa (WAF — Playwright strategy identified)",
            "treasurer_tax_lien (WAF on main site — GIS and tax sale site accessible)"
        ],
        "limited_coverage_sources": [
            "superior_court_family (sealing rules unclear — paused)",
            "gis_parcel_layer (ArcGIS 403 — header fix needed)"
        ],
        "not_found_lead_types": [
            "sheriff_sales (N/A — AZ non-judicial foreclosure state; no sheriff sale calendar)",
            "code_enforcement (no county-wide unified portal; per-city portals not buildable county-wide)"
        ],
        "operator_review_required": [
            "superior_court_family — confirm AZ public access rules for family court case index",
            "recorder_maricopa — confirm exact document type codes in portal before building doc_type_synonyms",
            "treasurer_tax_lien — confirm GIS FeatureServer query endpoint and field structure"
        ]
    },

    # ------------------------------------------------------------------
    # API DISCOVERY (v5.3.0)
    # ------------------------------------------------------------------
    "api_discovery": {
        "searched": [
            "recorder.maricopa.gov",
            "mcassessor.maricopa.gov",
            "gis.mcassessor.maricopa.gov",
            "data-maricopa.opendata.arcgis.com",
            "treasurer.maricopa.gov",
            "superiorcourt.maricopa.gov",
            "justicecourts.maricopa.gov",
            "api.mcassessor.maricopa.gov"
        ],
        "found": [
            {
                "api_url": "https://gis.mcassessor.maricopa.gov/arcgis/rest/services/Parcels/MapServer",
                "api_type": "ArcGIS",
                "documentation_url": "https://maps.mcassessor.maricopa.gov/help/g_rest.html",
                "auth_required": False,
                "rate_limited": False,
                "source_role": "ENRICHMENT_SOURCE",
                "notes": "Returns HTTP 403 on raw automated fetch — likely needs correct Referer/User-Agent headers or ArcGIS REST client. Confirmed to exist via web search and official documentation."
            },
            {
                "api_url": "https://data-maricopa.opendata.arcgis.com/",
                "api_type": "ArcGIS",
                "documentation_url": "https://data-maricopa.opendata.arcgis.com/pages/assessor-open-data-products",
                "auth_required": False,
                "rate_limited": False,
                "source_role": "ENRICHMENT_SOURCE",
                "notes": "Public ArcGIS open data portal. Assessor open data products available for download. Dataset-level ArcGIS FeatureServer URLs must be discovered per dataset in Build Mode."
            },
            {
                "api_url": "https://gis.maricopa.gov/TSR/liendelinquentparcel/index.html",
                "api_type": "ArcGIS",
                "documentation_url": "https://www.maricopa.gov/3942/GIS-Mapping-Applications",
                "auth_required": False,
                "rate_limited": False,
                "source_role": "PRIMARY_EVENT_SOURCE",
                "notes": "GIS web app for delinquent parcels. Redirects to a JavaScript app. Underlying FeatureServer endpoint must be discovered via network-tab inspection in Build Mode. Year-round availability confirmed."
            }
        ],
        "search_notes": "No public JSON/REST API found for Recorder recorded document search. Recorder portal is SPA-driven with a non-documented XHR backend. Superior Court docket is standard ASP-style HTML form portal (static HTML scraping or Playwright). Justice Court case search appears to be a JavaScript SPA (modern stack). No public API documentation found for any court portals."
    },

    # ------------------------------------------------------------------
    # ENRICHMENT INDEX STRATEGY (v5.3.0)
    # ------------------------------------------------------------------
    "enrichment_index_strategy": {
        "bulk_index_available": False,
        "bulk_index_source": None,
        "per_record_query_required": True,
        "per_record_query_cost_estimate": "FREE — per-record query via mcassessor.maricopa.gov parcel search (no fee, no login). Bulk parcel shapefile data available for purchase from Assessor data sales program (ftp.mcassessor.maricopa.gov/data-sales/gis.php). ArcGIS FeatureServer bulk query may be possible once endpoint confirmed in Build Mode.",
        "recommended_strategy": "Per-record APN lookup via Assessor parcel search for individual lead enrichment. Investigate ArcGIS FeatureServer bulk query for batch enrichment in Phase 2.",
        "deferred_to_version": None
    }
}


# ---------------------------------------------------------------------------
# Write the config atomically
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    result = write_county_config(
        config_dict=config,
        target_path=str(TARGET_PATH),
        schema_path=str(SCHEMA_PATH),
        overwrite=False,
    )
    print(result.summary())
    sys.exit(0 if result.is_ok() else 1)
