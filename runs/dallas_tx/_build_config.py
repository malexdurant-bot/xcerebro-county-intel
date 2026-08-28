"""
Transient Phase 0 build script for dallas_tx. Constructs the populated
county config as a Python dict (per MASTER_PROMPT.md Section 4.28) and
writes it via scaffold/ops/write_county_config.py. Not a framework file;
lives under runs/dallas_tx/ and is deleted after a successful write.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scaffold.ops.write_county_config import write_county_config

NOW = "2026-08-22T13:30:00Z"

SOURCE_DEFAULTS = {
    "operator_override": False,
    "known_limitations": [],
    "paused_reason": "",
    "pause_until": "",
    "allowed_to_export": True,
    "enabled": True,
    "auth_required": False,
    "rate_limit_rpm": None,
    "fields": {},
    "doc_type_synonyms": {},
    "blocked_unblock_paths": [],
    "open_questions": [],
    "blocker": "",
    "next_access_strategy": "",
    "blocker_type": "",
    "auto_resolve_status": "NOT_ATTEMPTED",
    "final_resolution_status": "",
    "auto_resolve_attempts": [],
    "lifecycle_status": "ACTIVE",
    "suppression_reason": "",
    "source_freshness_status": "UNKNOWN",
    "last_successful_fetch_at": "",
    "last_attempted_fetch_at": "",
    "last_record_seen_at": "",
    "expire_if_not_seen_runs": None,
    "quarantine_status": "NOT_QUARANTINED",
    "quarantine_reason": "",
    "portal_fingerprint_id": "",
    "fingerprinted_at": "",
    "fingerprint_confidence": "",
    "fingerprint_summary": "",
    "recommended_adapter": "",
    "credentials_required_kind": "",
    "credentials_declared": False,
    "manual_upload_path": "",
    "manual_upload_received_at": "",
    "last_verified_at": NOW,
}


def src(**kw):
    d = dict(SOURCE_DEFAULTS)
    d.update(kw)
    return d


sources = {
    "clerk_recordings": src(
        url="https://dallas.tx.publicsearch.us/",
        official_status="OFFICIAL_VENDOR_PORTAL",
        lead_value="LEAD_GENERATING",
        source_reliability_grade="A",
        source_priority="P0",
        build_priority="mvp_required",
        source_freshness="DAILY",
        ttl_days=1095,
        notes=(
            "PublicSearch / Neumo vendor portal hosting the Dallas County "
            "Clerk's Official Public Records (deeds, mortgages, liens, lis "
            "pendens, releases, assumed names). Officially linked from the "
            "county's own Online Record Search hub page under 'UCC / "
            "Personal Property / Deeds'. Same vendor family as the "
            "already-verified bexar_tx clerk_recordings source."
        ),
        verification_note=(
            "Confirmed live and reachable. Search fields present: "
            "grantor/grantee name, subdivision, document type, document "
            "number, date range (Last 24 Hours through Last 1 Year), and a "
            "choice between index-only and full-text OCR search. No login "
            "required to search. A 'Cart' / account feature is present, "
            "suggesting document-image retrieval may be fee-based, but "
            "recording-event detection does not require document images."
        ),
        open_questions=[
            "Confirm whether document image PDFs are free or paid on this "
            "PublicSearch instance.",
            "Confirm scraping/robots policy and reasonable request rate "
            "with the operator before Build Mode.",
        ],
        verified_from_url="https://www.dallascounty.org/services/record-search/",
        verification_method="official_vendor_link",
        official_entity="Dallas County Clerk's Office",
        portal_type="land records / official public records search",
        records_available=[
            "deeds", "mortgages", "deeds_of_trust", "liens", "lis_pendens",
            "releases_of_lien", "assumed_name", "ucc_filings",
            "military_discharge",
        ],
        search_fields=[
            "grantor_grantee_name", "document_type", "document_number",
            "subdivision", "date_range",
        ],
        access_method="SEARCHABLE_PUBLIC_PORTAL",
        public_access_status="PUBLIC_SEARCH_ONLY",
        document_access_status="DOCUMENTS_UNKNOWN",
        source_role="PRIMARY_LEAD_SOURCE",
        verification_confidence="HIGH",
        sample_record_path_confirmed=True,
        sample_record_type="search_form",
        sample_search_possible=True,
        sample_document_view_possible=False,
        expected_refresh_cadence="DAILY",
        stale_after_hours=36,
        record_ttl_days=1095,
        stale_record_policy="KEEP_UNTIL_RELEASED",
        estimated_runtime_minutes=10,
        estimated_cost_category="FREE",
        portal_family="publicsearch_neumo",
        category="lead",
        subtype="clerk_recordings",
        access_pattern="spa_with_api",
        scraper_module="scrapers/clerk_seeded.py",
        refresh_cadence="daily",
    ),
    "foreclosure_notices": src(
        url="https://dallas.tx.publicsearch.us/",
        official_status="OFFICIAL_VENDOR_PORTAL",
        lead_value="LEAD_GENERATING",
        source_reliability_grade="A",
        source_priority="P0",
        build_priority="mvp_required",
        source_freshness="DAILY",
        ttl_days=90,
        notes=(
            "Texas is a non-judicial foreclosure state. Notices of "
            "Substitute Trustee's Sale must be posted at the George Allen "
            "Courts Building and filed with the County Clerk at least 21 "
            "days before the first-Tuesday sale date. For sales noticed "
            "after 2026-02-24, the County Clerk's foreclosures page "
            "directs the public to a dedicated 'Foreclosure' search type "
            "on the same PublicSearch portal used for clerk_recordings, "
            "filterable by sale date and city. Prior to that date, notices "
            "were only published as static PDFs grouped by month and city "
            "at dallascounty.org (still usable as a historical/legacy "
            "fallback path)."
        ),
        verification_note=(
            "Confirmed via the County Clerk's Recording Division "
            "'Find Foreclosure Notices' page, which explicitly names the "
            "PublicSearch portal as the current system and describes the "
            "sale-date/city filters. Free, no login observed for the "
            "foreclosure search type."
        ),
        open_questions=[
            "Confirm the exact PublicSearch query parameters/UI path for "
            "the 'Foreclosure' search type (vs. the general document-type "
            "search) for scraper targeting.",
            "Decide whether the legacy monthly PDF archive should be "
            "ingested for backfill/history.",
        ],
        verified_from_url="https://www.dallascounty.org/government/county-clerk/recording/foreclosures.php",
        verification_method="official_page_link",
        official_entity="Dallas County Clerk's Office (Recording Division)",
        portal_type="foreclosure notice search (PublicSearch) + legacy PDF archive",
        records_available=[
            "notice_of_substitute_trustee_sale", "sale_documents_pdf",
        ],
        search_fields=["sale_date", "city", "grantor_grantee_name"],
        access_method="SEARCHABLE_PUBLIC_PORTAL",
        public_access_status="PUBLIC_SEARCH_ONLY",
        document_access_status="DOCUMENTS_PUBLIC",
        source_role="PRIMARY_LEAD_SOURCE",
        verification_confidence="HIGH",
        sample_record_path_confirmed=True,
        sample_record_type="search_form",
        sample_search_possible=True,
        sample_document_view_possible=True,
        expected_refresh_cadence="DAILY",
        stale_after_hours=36,
        record_ttl_days=90,
        expire_if_not_seen_runs=3,
        stale_record_policy="EXPIRE_IF_NOT_SEEN",
        estimated_runtime_minutes=8,
        estimated_cost_category="FREE",
        portal_family="publicsearch_neumo",
        category="lead",
        subtype="sheriff_sales",
        access_pattern="spa_with_api",
        scraper_module="scrapers/foreclosure_notices.py",
        refresh_cadence="daily",
    ),
    "sheriff_sales": src(
        url="https://dallas.texas.sheriffsaleauctions.com",
        official_status="OFFICIAL_VENDOR_PORTAL",
        lead_value="LEAD_GENERATING",
        source_reliability_grade="C",
        source_priority="P0",
        build_priority="high_value",
        source_freshness="DAILY",
        ttl_days=90,
        notes=(
            "RealAuction-hosted online auction platform for Dallas County "
            "sheriff's sales / tax-foreclosure execution sales, officially "
            "linked from the Dallas County Tax Office's Sheriff's Sales "
            "page. RealAuction operates similar platforms for many Texas "
            "and Florida counties."
        ),
        verification_note=(
            "Layer 1 origin verified (officially linked from "
            "dallascounty.org/departments/tax/sheriff-sales.php). Layer 5 "
            "(portal proof) could not be completed: a direct fetch "
            "returned HTTP 403, consistent with bot/WAF protection common "
            "on RealAuction deployments, not with the source being fake or "
            "unofficial. Public list visibility (vs. registered-bidder-"
            "only visibility) could not be confirmed without a real "
            "browser session."
        ),
        open_questions=[
            "Confirm with a real browser session whether the sale list is "
            "publicly viewable without RealAuction bidder registration.",
            "If registration is required even to view listings, confirm "
            "registration is free and does not require identity-verified "
            "payment credentials.",
        ],
        verified_from_url="https://www.dallascounty.org/departments/tax/sheriff-sales.php",
        verification_method="official_page_link",
        official_entity="Dallas County Sheriff / Tax Office (RealAuction vendor)",
        portal_type="online sheriff sale / tax foreclosure execution auction platform",
        records_available=["sheriff_sale_listings", "tax_foreclosure_execution_sales"],
        search_fields=["sale_date", "case_number", "address"],
        access_method="PUBLIC_BUT_WAF_PROTECTED",
        public_access_status="UNKNOWN",
        document_access_status="DOCUMENTS_UNKNOWN",
        source_role="BLOCKED_SOURCE",
        verification_confidence="BLOCKED",
        sample_record_path_confirmed=False,
        sample_record_type="",
        sample_search_possible=False,
        sample_document_view_possible=False,
        blocker=(
            "Direct HTTP fetch returned 403 Forbidden, consistent with "
            "bot-protection in front of the RealAuction platform. Could "
            "not confirm sample search/list access programmatically."
        ),
        next_access_strategy="use_playwright",
        blocker_type="PUBLIC_ACCESS_UNCLEAR",
        auto_resolve_status="FAILED",
        final_resolution_status="UNRESOLVED_TECHNICAL",
        auto_resolve_attempts=[
            {
                "attempt_order": 1,
                "timestamp": NOW,
                "strategy": "find_official_vendor_link",
                "status": "SUCCESS",
                "result": "SUCCESS",
                "detail": (
                    "Confirmed official linkage from "
                    "dallascounty.org/departments/tax/sheriff-sales.php."
                ),
            },
            {
                "attempt_order": 2,
                "timestamp": NOW,
                "strategy": "use_playwright",
                "status": "SKIPPED_NOT_ALLOWED",
                "result": "NOT_ATTEMPTED_NO_TOOLING",
                "detail": (
                    "Phase 0 recon tooling in this run is fetch-only (no "
                    "headless browser). A real-browser session is the "
                    "recommended next step and is deferred to the Phase 2/3 "
                    "scraper build, where recommended_adapter already "
                    "specifies a Playwright-based approach."
                ),
            },
        ],
        expected_refresh_cadence="DAILY",
        stale_after_hours=48,
        record_ttl_days=90,
        expire_if_not_seen_runs=3,
        stale_record_policy="EXPIRE_IF_NOT_SEEN",
        estimated_runtime_minutes=10,
        estimated_cost_category="LOW",
        portal_family="realauction",
        fingerprint_summary=(
            "RealAuction-hosted auction platform; returned 403 to a plain "
            "HTTP fetch, likely requiring a real browser / session."
        ),
        recommended_adapter="playwright_realauction_scraper",
        category="lead",
        subtype="sheriff_sales",
        access_pattern="spa_recaptcha",
        scraper_module="scrapers/sheriff_sales_dallas.py",
        refresh_cadence="daily",
    ),
    "tax_foreclosure_resales": src(
        url="https://www.dallascounty.org/Assets/uploads/docs/public-works/StruckListWorking_2025_3-3-2026.pdf",
        official_status="OFFICIAL_COUNTY",
        lead_value="LEAD_GENERATING",
        source_reliability_grade="B",
        source_priority="P1",
        build_priority="high_value",
        source_freshness="MONTHLY",
        ttl_days=365,
        notes=(
            "Dallas County Public Works Property Division publishes a "
            "'struck-off' list of properties that went through tax "
            "foreclosure judgment and were struck off to a taxing entity, "
            "now available for resale via sealed bid. Published as a "
            "periodically-updated PDF, not a searchable database."
        ),
        verification_note=(
            "Officially linked from the Public Works Property Division "
            "page. PDF download confirmed reachable; the filename itself "
            "is dated, indicating it is refreshed periodically rather than "
            "daily."
        ),
        open_questions=[
            "Confirm actual refresh cadence of the struck-off PDF with the "
            "operator (filename suggests roughly monthly).",
            "Confirm whether a historical archive of prior struck-off "
            "lists is published anywhere.",
        ],
        verified_from_url="https://www.dallascounty.org/departments/pubworks/property-division.php",
        verification_method="official_page_link",
        official_entity="Dallas County Public Works Department, Property Division",
        portal_type="PDF publication list",
        records_available=["struck_off_properties", "tax_resale_eligible_properties"],
        search_fields=[],
        access_method="PDF_PUBLICATION",
        public_access_status="FULL_PUBLIC_ACCESS",
        document_access_status="DOCUMENTS_PUBLIC",
        source_role="PRIMARY_LEAD_SOURCE",
        verification_confidence="MEDIUM",
        sample_record_path_confirmed=True,
        sample_record_type="pdf_index",
        sample_search_possible=False,
        sample_document_view_possible=True,
        expected_refresh_cadence="MONTHLY",
        stale_after_hours=1440,
        record_ttl_days=365,
        stale_record_policy="MANUAL_REVIEW",
        estimated_runtime_minutes=2,
        estimated_cost_category="FREE",
        portal_family="static_pdf",
        recommended_adapter="pdf_table_parser",
        category="lead",
        subtype="tax_certificates",
        access_pattern="static_html",
        scraper_module="scrapers/tax_foreclosure_resales_dallas.py",
        refresh_cadence="monthly",
    ),
    "court_civil": src(
        url="https://courtsportal.dallascounty.org/DALLASPROD",
        official_status="OFFICIAL_COURT",
        lead_value="LEAD_GENERATING",
        source_reliability_grade="B",
        source_priority="P0",
        build_priority="high_value",
        source_freshness="DAILY",
        ttl_days=1095,
        notes=(
            "Tyler Technologies-powered Dallas County courts portal "
            "('Smart Search'), officially linked from both the County "
            "Clerk's Online Record Search hub and the county's Public "
            "Access to Court Records page. A second Tyler-family endpoint "
            "(obpublicaccess24.dallascounty.org/PublicAccess/) was also "
            "found and appears scoped to felony/misdemeanor criminal "
            "records rather than civil; this block targets civil/family "
            "district and county court cases (foreclosure-adjacent civil "
            "actions, judgments)."
        ),
        verification_note=(
            "Portal confirmed live, Tyler-branded ('Empowered By Tyler "
            "Technologies'). Page text states portal registration is "
            "required only for defense attorneys and county employees, "
            "implying public search does not require login, but the exact "
            "civil-case search fields and result fields were not directly "
            "observable from a static fetch of the login/landing screen."
        ),
        open_questions=[
            "Confirm with an interactive session which case types "
            "(civil, family, county-court-at-law) are exposed in Smart "
            "Search vs. the separate obpublicaccess24 endpoint.",
            "Confirm whether case documents/images are free or paid.",
        ],
        verified_from_url="https://www.dallascounty.org/services/record-search/",
        verification_method="official_page_link",
        official_entity="Dallas County District Clerk / County Clerk",
        portal_type="Tyler Odyssey-family court records portal",
        records_available=[
            "civil_district_court_cases", "county_court_at_law_cases",
            "family_court_cases",
        ],
        search_fields=["party_name", "case_number", "date_range", "case_type"],
        access_method="SEARCHABLE_PUBLIC_PORTAL",
        public_access_status="PUBLIC_SEARCH_ONLY",
        document_access_status="DOCUMENTS_UNKNOWN",
        source_role="PRIMARY_LEAD_SOURCE",
        verification_confidence="MEDIUM",
        sample_record_path_confirmed=True,
        sample_record_type="search_form",
        sample_search_possible=True,
        sample_document_view_possible=False,
        expected_refresh_cadence="DAILY",
        stale_after_hours=36,
        record_ttl_days=1095,
        stale_record_policy="KEEP_UNTIL_RELEASED",
        estimated_runtime_minutes=8,
        estimated_cost_category="FREE",
        portal_family="tyler_courts_portal",
        recommended_adapter="tyler_smart_search",
        category="lead",
        subtype="court_civil",
        access_pattern="spa_with_api",
        scraper_module="scrapers/court_civil_dallas.py",
        refresh_cadence="daily",
    ),
    "court_probate": src(
        url="https://courtsportal.dallascounty.org/DALLASPROD",
        official_status="OFFICIAL_COURT",
        lead_value="LEAD_GENERATING",
        source_reliability_grade="B",
        source_priority="P0",
        build_priority="high_value",
        source_freshness="DAILY",
        ttl_days=1825,
        notes=(
            "Dallas County has three statutory Probate Courts under the "
            "County Clerk's Probate Courts Division. Probate case records "
            "are indexed in the same Tyler-family courts portal used for "
            "civil records; declared as a separate source block because "
            "the lead pattern (estate openings, heirship, guardianship) is "
            "distinct from civil-distress patterns."
        ),
        verification_note=(
            "County Clerk Probate Courts Division page confirms probate "
            "records are available via the Dallas County Online Record "
            "Search. Texas Estates Code makes most probate filings public "
            "absent a sealing order."
        ),
        open_questions=[
            "Confirm whether the courts portal exposes a dedicated "
            "probate case-type filter or requires searching all case "
            "types and post-filtering.",
        ],
        verified_from_url="https://www.dallascounty.org/government/county-clerk/probate-courts/",
        verification_method="official_page_link",
        official_entity="Dallas County Clerk - Probate Courts Division",
        portal_type="Tyler Odyssey-family probate records search",
        records_available=[
            "probate_cases", "estate_openings", "heirship_proceedings",
            "guardianships",
        ],
        search_fields=["decedent_name", "case_number", "filing_date_range"],
        access_method="SEARCHABLE_PUBLIC_PORTAL",
        public_access_status="PUBLIC_SEARCH_ONLY",
        document_access_status="DOCUMENTS_UNKNOWN",
        source_role="PRIMARY_LEAD_SOURCE",
        verification_confidence="MEDIUM",
        sample_record_path_confirmed=True,
        sample_record_type="search_form",
        sample_search_possible=True,
        sample_document_view_possible=False,
        expected_refresh_cadence="DAILY",
        stale_after_hours=48,
        record_ttl_days=1825,
        stale_record_policy="KEEP_UNTIL_RELEASED",
        estimated_runtime_minutes=6,
        estimated_cost_category="FREE",
        portal_family="tyler_courts_portal",
        recommended_adapter="tyler_smart_search",
        category="lead",
        subtype="court_probate",
        access_pattern="spa_with_api",
        scraper_module="scrapers/court_probate_dallas.py",
        refresh_cadence="daily",
    ),
    "jp_eviction": src(
        url="",
        official_status="NOT_FOUND",
        operator_override=True,
        lead_value="UNKNOWN",
        source_reliability_grade="E",
        source_priority="P1",
        build_priority="future",
        source_freshness="UNKNOWN",
        ttl_days=90,
        notes=(
            "Dallas County has multiple Justice of the Peace precincts "
            "(each with its own eviction docket). No official countywide "
            "or per-precinct online case-search portal was found; every JP "
            "precinct page ('Evictions') directs the public to contact "
            "that precinct's office directly by phone/in person. A "
            "third-party 'Eviction Filing Dashboard' exists at "
            "dallaseac.org (Child Poverty Action Lab, described as done "
            "'in collaboration with Dallas County') but is hosted on a "
            "non-government domain and was not verified as an official, "
            "government-operated, or scrapable source."
        ),
        verification_note=(
            "Multiple JP precinct 'Evictions' pages checked via search; "
            "consistent message that no online search exists and the "
            "public must contact the precinct. Marked NOT_FOUND per "
            "Phase 0 hard rule (no invented portals)."
        ),
        open_questions=[
            "Ask the operator whether dallaseac.org's aggregate dashboard "
            "(non-government, possibly not per-property) is worth "
            "pursuing as a REFERENCE_ONLY signal, or whether a manual "
            "per-precinct pull is worth the ~30-precinct overhead.",
            "Confirm whether any individual JP precinct has since stood "
            "up an online docket search not surfaced by this search pass.",
        ],
        verified_from_url="",
        verification_method="not_verified",
        official_entity="",
        portal_type="",
        records_available=[],
        search_fields=[],
        access_method="NOT_SEARCHABLE",
        public_access_status="UNKNOWN",
        document_access_status="DOCUMENTS_UNKNOWN",
        source_role="NOT_FOUND",
        verification_confidence="NOT_FOUND",
        sample_record_path_confirmed=False,
        sample_record_type="",
        sample_search_possible=False,
        sample_document_view_possible=False,
        blocker=(
            "No online case-search portal exists per-precinct or "
            "countywide for JP eviction dockets; contact is phone/in-"
            "person only."
        ),
        next_access_strategy="manual_operator_assisted_pull",
        blocker_type="SOURCE_NOT_FOUND",
        auto_resolve_status="REQUIRES_MANUAL_ASSISTANCE",
        final_resolution_status="MANUAL_ASSISTANCE_REQUIRED",
        auto_resolve_attempts=[
            {
                "attempt_order": 1,
                "timestamp": NOW,
                "strategy": "discover_public_search_endpoint",
                "status": "FAILED",
                "result": "NOT_FOUND",
                "detail": (
                    "Checked several JP precinct 'Evictions' pages "
                    "(3-1, 4-1, 5-1, 5-2); all state no online search "
                    "exists and direct the public to contact the precinct."
                ),
            },
        ],
        expected_refresh_cadence="UNKNOWN",
        stale_after_hours=None,
        record_ttl_days=90,
        stale_record_policy="MANUAL_REVIEW",
        estimated_runtime_minutes=None,
        estimated_cost_category="UNKNOWN",
        category="lead",
        subtype="court_eviction",
        access_pattern="public_records_only",
        scraper_module="",
        refresh_cadence="on_demand",
    ),
    "tax_collector": src(
        url="https://www.dallasact.com/act_webdev/dallas/index.jsp",
        official_status="OFFICIAL_VENDOR_PORTAL",
        lead_value="LEAD_GENERATING",
        source_reliability_grade="A",
        source_priority="P0",
        build_priority="high_value",
        source_freshness="DAILY",
        ttl_days=365,
        notes=(
            "ACT Tax Solutions-hosted tax-account portal for the Dallas "
            "County Tax Office (Assessor-Collector) — same vendor family "
            "already verified for bexar_tx's tax_collector source. Free "
            "public account search by owner name, address, or account "
            "number; delinquency status is visible at the per-account "
            "level, not as a separate bulk delinquent-roll dump."
        ),
        verification_note=(
            "Confirmed reachable; page carries a 'DallasCounty.org for "
            "more services' banner confirming official linkage, and "
            "offers owner/business name, address, account, and fiduciary "
            "search without an apparent login wall for basic search. "
            "Exact delinquent-status display in results was not directly "
            "observed in this fetch."
        ),
        open_questions=[
            "Confirm delinquent-balance visibility and tax-years-owed "
            "detail actually renders in per-account results.",
            "Confirm whether a bulk delinquent-roll download/report "
            "exists as an alternative to per-parcel walks.",
        ],
        verified_from_url="https://www.dallascounty.org/departments/tax/pay-property-tax.php",
        verification_method="official_vendor_link",
        official_entity="Dallas County Tax Assessor-Collector",
        portal_type="tax account lookup with delinquent balance visibility",
        records_available=[
            "tax_account_balance", "delinquent_balance", "payment_history",
            "exemption_status",
        ],
        search_fields=[
            "owner_name", "business_name", "property_address",
            "account_number", "fiduciary_number",
        ],
        access_method="SEARCHABLE_PUBLIC_PORTAL",
        public_access_status="FULL_PUBLIC_ACCESS",
        document_access_status="DOCUMENTS_PUBLIC",
        source_role="PRIMARY_LEAD_SOURCE",
        verification_confidence="HIGH",
        sample_record_path_confirmed=True,
        sample_record_type="search_form",
        sample_search_possible=True,
        sample_document_view_possible=True,
        expected_refresh_cadence="DAILY",
        stale_after_hours=72,
        record_ttl_days=365,
        stale_record_policy="EXPIRE_AFTER_TTL",
        estimated_runtime_minutes=4,
        estimated_cost_category="FREE",
        portal_family="acttax",
        recommended_adapter="acttax_per_account_scraper",
        category="lead",
        subtype="tax_delinquency",
        access_pattern="static_html",
        scraper_module="scrapers/tax_collector_dallas.py",
        refresh_cadence="daily",
    ),
    "code_enforcement": src(
        url="",
        official_status="NOT_FOUND",
        operator_override=True,
        lead_value="UNKNOWN",
        source_reliability_grade="E",
        source_priority="P2",
        build_priority="future",
        source_freshness="UNKNOWN",
        ttl_days=365,
        notes=(
            "Dallas County itself does not run code enforcement; it is a "
            "municipal function (e.g., City of Dallas Code Compliance / "
            "311, City of Irving, City of Garland, each independently). "
            "Per engineering/knowledge_base guidance, county-wide code "
            "enforcement builds are out of scope unless the operator "
            "explicitly prioritizes a single municipality — Dallas County "
            "contains dozens of municipalities plus unincorporated area, "
            "so no single-portal county-level source exists."
        ),
        verification_note="Not searched further; marked NOT_FOUND at the county level by design, not by omission.",
        open_questions=[
            "If the operator wants code-enforcement coverage, which single "
            "municipality (most likely City of Dallas) should be "
            "prioritized as a P1/P2 add-on?",
        ],
        verified_from_url="",
        verification_method="not_verified",
        official_entity="",
        portal_type="",
        records_available=[],
        search_fields=[],
        access_method="UNKNOWN",
        public_access_status="UNKNOWN",
        document_access_status="DOCUMENTS_UNKNOWN",
        source_role="NOT_FOUND",
        verification_confidence="NOT_FOUND",
        sample_record_path_confirmed=False,
        sample_record_type="",
        sample_search_possible=False,
        sample_document_view_possible=False,
        blocker="No county-level code enforcement portal exists; function is municipal.",
        next_access_strategy="not_available",
        blocker_type="SOURCE_WRONG_CATEGORY",
        auto_resolve_status="NOT_ALLOWED",
        final_resolution_status="UNRESOLVED_NOT_FOUND",
        expected_refresh_cadence="UNKNOWN",
        stale_after_hours=None,
        record_ttl_days=365,
        stale_record_policy="MANUAL_REVIEW",
        estimated_runtime_minutes=None,
        estimated_cost_category="UNKNOWN",
        category="lead",
        subtype="code_enforcement",
        access_pattern="public_records_only",
        scraper_module="",
        refresh_cadence="on_demand",
    ),
    "parcel_master": src(
        url="https://www.dallascad.org/",
        official_status="OFFICIAL_COUNTY",
        lead_value="ENRICHMENT",
        source_reliability_grade="A",
        source_priority="P2",
        build_priority="enrichment",
        source_freshness="MONTHLY",
        ttl_days=9999,
        fields={
            "parcel_id": "", "owner_name": "", "owner_mailing_addr1": "",
            "owner_mailing_city": "", "owner_mailing_state": "",
            "owner_mailing_zip": "", "situs_address": "", "situs_city": "",
            "situs_zip": "", "year_built": "", "assessed_value": "",
            "land_value": "", "improvement_value": "", "last_sale_date": "",
            "last_sale_price": "", "deed_book": "", "deed_page": "",
            "property_class": "", "land_use_code": "", "acreage": "",
        },
        notes=(
            "Dallas Central Appraisal District (DCAD) parcel master. "
            "Appraises property on behalf of 61 local taxing entities in "
            "Dallas County. Property search by account number, address, "
            "owner name, DBA name, subdivision, or map grid."
        ),
        verification_note="Confirmed official (dallascad.org self-describes its statutory role). Search Appraisals feature confirmed; GIS map at maps.dcad.org confirmed.",
        open_questions=[
            "Confirm bulk-download / data-products licensing terms and "
            "cost (DCAD publishes 'Data Products' and 'GIS Data Products' "
            "sections whose pricing was not observed in this fetch).",
        ],
        verified_from_url="https://www.dallascad.org/",
        verification_method="official_domain",
        official_entity="Dallas Central Appraisal District (DCAD)",
        portal_type="appraisal district parcel master search",
        records_available=[
            "parcel_id", "owner_name_and_mailing_address", "situs_address",
            "assessed_value", "land_value", "improvement_value",
            "last_sale_date", "last_sale_price", "property_class",
        ],
        search_fields=["account_number", "address", "owner_name", "dba_name", "subdivision", "map_grid"],
        access_method="SEARCHABLE_PUBLIC_PORTAL",
        public_access_status="FULL_PUBLIC_ACCESS",
        document_access_status="DOCUMENTS_PUBLIC",
        source_role="ENRICHMENT_SOURCE",
        verification_confidence="HIGH",
        sample_record_path_confirmed=True,
        sample_record_type="search_form",
        sample_search_possible=True,
        sample_document_view_possible=True,
        expected_refresh_cadence="MONTHLY",
        stale_after_hours=720,
        record_ttl_days=9999,
        stale_record_policy="NEVER_EXPIRE",
        estimated_runtime_minutes=6,
        estimated_cost_category="FREE",
        portal_family="dcad_custom",
        recommended_adapter="dcad_search_scraper",
        category="enrichment",
        subtype="parcel_master",
        access_pattern="static_html",
        scraper_module="scrapers/parcel_master_dallas.py",
        refresh_cadence="monthly",
    ),
    "gis_parcels": src(
        url="https://maps.dcad.org/prd/dpm/",
        official_status="OFFICIAL_COUNTY",
        lead_value="ENRICHMENT",
        source_reliability_grade="A",
        source_priority="P2",
        build_priority="enrichment",
        source_freshness="WEEKLY",
        ttl_days=9999,
        notes=(
            "DCAD's 'Find Property on Map' GIS parcel viewer, linked "
            "directly from dallascad.org. Provides map-based parcel "
            "lookup by account number, address, owner name, subdivision, "
            "or map selection."
        ),
        verification_note="Confirmed linked from the official DCAD homepage.",
        open_questions=["Confirm whether a REST/FeatureServer endpoint exists behind the map viewer for programmatic bulk pull, as opposed to interactive-only use."],
        verified_from_url="https://www.dallascad.org/",
        verification_method="official_page_link",
        official_entity="Dallas Central Appraisal District (DCAD)",
        portal_type="GIS parcel map viewer",
        records_available=["parcels_polygon", "situs_address", "account_number"],
        search_fields=["account_number", "address", "owner_name", "map_bounds"],
        access_method="MAP_LAYER",
        public_access_status="FULL_PUBLIC_ACCESS",
        document_access_status="DOCUMENTS_PUBLIC",
        source_role="ENRICHMENT_SOURCE",
        verification_confidence="MEDIUM",
        sample_record_path_confirmed=True,
        sample_record_type="map_layer",
        sample_search_possible=True,
        sample_document_view_possible=False,
        expected_refresh_cadence="WEEKLY",
        stale_after_hours=240,
        record_ttl_days=9999,
        stale_record_policy="NEVER_EXPIRE",
        estimated_runtime_minutes=3,
        estimated_cost_category="FREE",
        portal_family="custom_gis_viewer",
        category="enrichment",
        subtype="gis_parcels",
        access_pattern="spa_with_api",
        scraper_module="scrapers/gis_parcels_dallas.py",
        refresh_cadence="weekly",
    ),
}

municipalities = [
    "Dallas", "Irving", "Garland", "Mesquite", "Grand Prairie", "Richardson",
    "DeSoto", "Duncanville", "Cedar Hill", "Balch Springs", "Carrollton",
    "Farmers Branch", "Sachse", "Rowlett", "Wilmer", "Hutchins", "Lancaster",
    "Seagoville", "Sunnyvale", "Addison", "University Park", "Highland Park",
    "Cockrell Hill", "Glenn Heights", "Ovilla", "Coppell", "Combine",
    "Grapevine", "Highland Village", "The Colony",
]

config = {
    "county_id": "dallas_tx",
    "county_name": "Dallas",
    "state": "TX",
    "state_rule_family": "TX_non_judicial_foreclosure",
    "subject_state_full": "Texas",
    "fips_code": "48113",
    "timezone": "America/Chicago",
    "operator_market_priority": "primary",
    "geography": {
        "municipalities": [
            {"name": m, "code": m.lower().replace(" ", "_").replace(".", ""), "fips_place": ""}
            for m in municipalities
        ],
        "accepted_municipalities": [
            {"name": m.upper(), "kind": "incorporated"} for m in municipalities
        ],
        "parcel_id_format": "^[0-9]{11,20}$",
        "parcel_id_normalization": "strip-dashes",
        "address_format_notes": (
            "DCAD account numbers are numeric strings of varying length "
            "(commonly 11-20 digits); normalization strips any dashes. "
            "Dallas County contains dozens of municipalities plus a small "
            "amount of unincorporated area; situs municipality should be "
            "cross-checked against DCAD parcel data where the recording "
            "source omits it."
        ),
        "sale_date_rule": {
            "rule_name": "first_tuesday_of_month",
            "holiday_shift": {
                "shift_dates": ["01-01", "07-04"],
                "shift_to": "next_wednesday",
            },
            "statute_reference": "Tex. Prop. Code Sec. 51.002",
        },
    },
    "sources": sources,
    "scoring_overrides": {
        "match_confidence_floor": 80,
        "review_queue_ratio_alert_threshold": 0.5,
        "high_equity_assessed_to_sale_ratio": 2.0,
        "long_term_owned_years": 15,
        "senior_owner_proxy_years": 25,
        "favorable_loan_era_start": "2020-01-01",
        "favorable_loan_era_end": "2022-06-30",
        "in_state_zip_prefixes": ["7"],
        "in_state_code": "TX",
    },
    "storage": {
        "mode": "STATIC_JSON_MODE",
        "supabase_enabled": False,
        "dashboard_payload": "data/leads.json",
        "retain_raw_records_days": 30,
        "retain_source_runs_days": 365,
    },
    "dashboard": {
        "title": "Dallas County Distress Intelligence",
        "subtitle": "Daily-refreshed real estate distress signals",
        "primary_color": "#0F172A",
        "accent_color": "#3B82F6",
        "default_view": "all_leads",
        "precanned_views": [],
        "view_modes": ["CLIENT_VIEW", "OPERATOR_VIEW"],
        "build_label": "",
        "build_label_reason": "",
    },
    "deployment": {
        "github_org": "xcerebro",
        "github_repo": "dallas",
        "live_url": "",
        "scheduled_task_name": "",
        "watchdog_task_name": "",
        "scheduler_runtime_class": "SCHEDULER_NOT_CONFIGURED",
        "scheduler_test_fired_at": "",
        "production_verification_status": "NOT_RUN",
        "production_verification_at": "",
        "last_known_good_commit": "",
        "last_known_good_dashboard_at": "",
    },
    "build_verdict": "READY_TO_BUILD",
    "build_verdict_reason": (
        "Five PRIMARY_LEAD_SOURCEs verified HIGH or MEDIUM confidence with "
        "public, free, no-login search access: clerk_recordings and "
        "foreclosure_notices on the county's PublicSearch/Neumo portal "
        "(dallas.tx.publicsearch.us); tax_collector on the ACT Tax vendor "
        "portal (dallasact.com); court_civil and court_probate on the "
        "county's Tyler-family courts portal (MEDIUM confidence pending "
        "confirmation of exact case-type search fields). tax_foreclosure_"
        "resales verified MEDIUM as a periodic PDF publication (P1). Two "
        "enrichment sources verified (DCAD parcel_master HIGH, DCAD "
        "gis_parcels MEDIUM). Two sources remain unresolved after Phase "
        "0.5: sheriff_sales is officially linked but returned HTTP 403 to "
        "a plain fetch (BLOCKED_SOURCE, technical, next step is a real-"
        "browser/Playwright pull in Build Mode) and jp_eviction has no "
        "online portal at all (NOT_FOUND, P1, manual-assistance path "
        "noted). Neither blocks the build: the P0 gate is satisfied "
        "independently by clerk_recordings and foreclosure_notices, both "
        "of which are unblocked and daily-refreshed today."
    ),
    "build_verdict_at": NOW,
    "auto_resolve_status": "PARTIALLY_RESOLVED",
    "final_resolution_status": "PARTIALLY_RESOLVED",
    "operator_override_audit": [
        {
            "source_id": "jp_eviction",
            "field": "operator_override",
            "set_to": True,
            "reason": (
                "Operator explicitly confirmed jp_eviction is genuinely "
                "NOT_FOUND, not a recon miss: Dallas County has no online "
                "case-search portal for any JP precinct's eviction docket "
                "(phone/in-person only). Override permits the config to "
                "structurally record a verified-absent source per schema "
                "requirements; does not change the source's NOT_FOUND "
                "status or make it lead-generating."
            ),
            "approved_by": "operator",
            "approved_at": NOW,
        },
        {
            "source_id": "code_enforcement",
            "field": "operator_override",
            "set_to": True,
            "reason": (
                "Operator explicitly confirmed code_enforcement is "
                "genuinely NOT_FOUND at the county level: code enforcement "
                "in Dallas County is a municipal function (City of Dallas, "
                "Irving, Garland, etc.), not administered county-wide, so "
                "no single county-level portal exists. Override permits "
                "the config to structurally record a verified-absent "
                "source per schema requirements; does not change the "
                "source's NOT_FOUND status."
            ),
            "approved_by": "operator",
            "approved_at": NOW,
        },
    ],
}

if __name__ == "__main__":
    result = write_county_config(
        config_dict=config,
        target_path=str(REPO_ROOT / "config" / "counties" / "dallas_tx.json"),
        schema_path=str(REPO_ROOT / "config" / "counties" / "_schema.json"),
        overwrite=False,
    )
    print(result.summary())
    raise SystemExit(0 if result.is_ok() else 1)
