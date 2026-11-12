"""Phase 0 builder for clayton_ga: builds config dict -> write_county_config, and emits recon artifacts."""
import json, os, sys
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(REPO))
from scaffold.ops.write_county_config import write_county_config  # noqa: E402

NOW = "2026-11-12T15:30:00Z"
SLUG = "clayton_ga"
RECON = REPO / "runs" / SLUG / "recon"
(RECON / "fingerprints").mkdir(parents=True, exist_ok=True)

CCG = "https://www.claytoncountyga.gov"


def src(**kw):
    base = dict(
        auth_required=False, rate_limit_rpm=10, enabled=True, allowed_to_export=True,
        translator="custom", ttl_days=90, operator_override=False,
        last_verified_at=NOW, known_limitations=[], open_questions=[],
        blocker="", blocker_type="", next_access_strategy="",
        auto_resolve_status="NOT_ATTEMPTED", final_resolution_status="",
        auto_resolve_attempts=[], estimated_cost_category="FREE",
    )
    base.update(kw)
    return base


# ---------------------------------------------------------------- sources
S = {}
S["magistrate_court_civil_evictions"] = src(
    category="lead", subtype="court_eviction",
    url="https://weba.claytoncountyga.gov/casinqsvr/htdocs/index.shtml",
    access_pattern="static_html", scraper_module="scrapers/clayton_case_inquiry.py",
    refresh_cadence="daily", ttl_days=60,
    source_priority="P0", build_priority="mvp_required", source_reliability_grade="A",
    official_status="OFFICIAL_COURT", official_entity="Clayton County Clerk of Superior & Magistrate Courts (Magistrate Court civil)",
    portal_type="county_case_inquiry_cgi", portal_family="IBM i CGI (casinqcgi-bin/wci01xr.pgm) — county-built JIS front end",
    records_available=["dispossessory (Patho) filings", "magistrate civil suits", "garnishments", "docket activity", "party names and addresses", "disposition"],
    search_fields=["court (L/M/U)", "case year", "case type (2 chars)", "case sequence (5 digits)", "last/first name"],
    access_method="SEARCHABLE_PUBLIC_PORTAL", public_access_status="FULL_PUBLIC_ACCESS",
    document_access_status="DOCUMENTS_NOT_AVAILABLE",
    source_role="PRIMARY_LEAD_SOURCE", lead_value="LEAD_GENERATING", verification_confidence="HIGH",
    verification_method="official_page_link", verified_from_url="https://clerk.claytoncountyga.gov/case-search/",
    verification_note=("Clerk site case-search page iframes weba.claytoncountyga.gov/casinqsvr. Sample GET wci010r.pgm?rtype=E&ctt=M&cyr=2026&ctp=CM&csq=00100 "
                       "returned case 2026CM00100: landlord plaintiff vs tenant defendant, case type 'Patho Dispossessory', filing 01/02/2026, "
                       "defendant street address, docket + disposition. Server-rendered HTML, no login, no CAPTCHA."),
    sample_record_path_confirmed=True, sample_record_type="Magistrate civil case summary (dispossessory)",
    sample_search_possible=True, sample_document_view_possible=False,
    known_limitations=["No date-range or case-type browse; enumeration is by walking sequential case numbers per year/type",
                       "Docket shows 'Image Document' entries but images are not served publicly",
                       "Tenant-defendant is often not the property owner — eviction signals a landlord-side lead (tired landlord) and must be matched to parcel owner via enrichment"],
    open_questions=["Confirm the full list of Magistrate case-type codes (CM observed for dispossessory/civil) — is there a separate code for dispossessory vs general civil?",
                    "Operator: confirm acceptable request rate for sequential case walking (default 10 rpm)."],
)
S["state_court_civil_judgments"] = src(
    category="lead", subtype="court_civil",
    url="https://weba.claytoncountyga.gov/casinqsvr/htdocs/index.shtml",
    access_pattern="static_html", scraper_module="scrapers/clayton_case_inquiry.py",
    refresh_cadence="daily", ttl_days=180,
    source_priority="P1", build_priority="high_value", source_reliability_grade="A",
    official_status="OFFICIAL_COURT", official_entity="State Court of Clayton County (via county Case Inquiry)",
    portal_type="county_case_inquiry_cgi", portal_family="IBM i CGI — county-built JIS front end",
    records_available=["civil suits", "default judgments", "dispositions", "party names and addresses"],
    search_fields=["case year", "case type", "case sequence", "name"],
    access_method="SEARCHABLE_PUBLIC_PORTAL", public_access_status="FULL_PUBLIC_ACCESS",
    document_access_status="DOCUMENTS_NOT_AVAILABLE",
    source_role="PRIMARY_LEAD_SOURCE", lead_value="LEAD_GENERATING", verification_confidence="HIGH",
    verification_method="official_page_link", verified_from_url="https://clerk.claytoncountyga.gov/case-search/",
    verification_note=("Sample 2026CV00100 (State Court, ctt=L): filed 01/08/2026, disposition 'Default Judgment' 09/25/2026, defendant name + Jonesboro street address. "
                       "Same CGI as Magistrate."),
    sample_record_path_confirmed=True, sample_record_type="State Court civil case summary with default judgment",
    sample_search_possible=True, sample_document_view_possible=False,
    known_limitations=["Most judgments are consumer debt (auto finance, credit cards) — judgment debtor must be matched to a parcel owner before it becomes a lead",
                       "Judgment becomes a property lien only once the FiFa is recorded in the GSCCCA lien index (paid)"],
)
S["superior_court_civil"] = src(
    category="lead", subtype="court_family",
    url="https://weba.claytoncountyga.gov/casinqcgi-bin/wci011r.pgm?rtype=I&dvt=V",
    access_pattern="static_html", scraper_module="scrapers/clayton_case_inquiry.py",
    refresh_cadence="weekly", ttl_days=180,
    source_priority="P2", build_priority="optional", source_reliability_grade="B",
    official_status="OFFICIAL_COURT", official_entity="Clayton County Superior Court (via county Case Inquiry)",
    portal_type="county_case_inquiry_cgi", portal_family="IBM i CGI — county-built JIS front end",
    records_available=["divorce", "quiet title / equity", "civil", "party names"],
    search_fields=["name (court=U)", "case number (format unconfirmed)"],
    access_method="SEARCHABLE_PUBLIC_PORTAL", public_access_status="FULL_PUBLIC_ACCESS",
    document_access_status="DOCUMENTS_NOT_AVAILABLE",
    source_role="SUPPORTING_LEAD_SOURCE", lead_value="LEAD_GENERATING", verification_confidence="MEDIUM",
    verification_method="official_page_link", verified_from_url="https://clerk.claytoncountyga.gov/case-search/",
    verification_note=("Name-search form for Superior Court is public. Case-number probes 2026/2025 CV 00001/00100/01000 with ctt=U all returned "
                       "'Case# not found' — Superior numbering differs from State/Magistrate, so sequential enumeration is not yet proven."),
    sample_record_path_confirmed=False, sample_record_type="",
    sample_search_possible=True, sample_document_view_possible=False,
    known_limitations=["Bulk enumeration unproven — currently name search only (PER_RECORD_ONLY)"],
    open_questions=["What is the Superior Court civil case-number format (type code / sequence width / division suffix)? A single recent Superior case number from the operator would unblock enumeration."],
)
S["probate_estates"] = src(
    category="lead", subtype="court_probate",
    url="https://www.georgiaprobaterecords.com/",
    access_pattern="spa_with_api", scraper_module="scrapers/georgia_probate_records.py",
    refresh_cadence="daily", ttl_days=365,
    source_priority="P0", build_priority="mvp_required", source_reliability_grade="A",
    official_status="OFFICIAL_VENDOR_PORTAL", official_entity="Clayton County Probate Court (Georgia Probate Records statewide portal)",
    portal_type="aspnet_webforms_telerik", portal_family="georgiaprobaterecords.com (Council of Probate Court Judges of Georgia portal)",
    records_available=["estates", "wills", "guardianships", "deaths", "hearing calendars"],
    search_fields=["county", "last/first name", "deceased date range", "filed date range", "case number"],
    access_method="SEARCHABLE_PUBLIC_PORTAL", public_access_status="FULL_PUBLIC_ACCESS",
    document_access_status="DOCUMENTS_PUBLIC",
    source_role="PRIMARY_LEAD_SOURCE", lead_value="LEAD_GENERATING", verification_confidence="MEDIUM",
    verification_method="official_vendor_link", verified_from_url=f"{CCG}/government/courts/probate-court/online-estate-records/",
    verification_note=("County probate page instructs: go to georgiaprobaterecords.com > Estates > select Clayton > search by name or by Deceased/Filed date range; "
                       "'estate records can be viewed online at no cost'; available documents show a PDF icon. Portal reachable (HTTP 200, Cloudflare front, no challenge to plain GET). "
                       "A live filed-date search was not executed during recon (ASP.NET/Telerik postback)."),
    sample_record_path_confirmed=False, sample_record_type="",
    sample_search_possible=True, sample_document_view_possible=True,
    known_limitations=["Estate records give decedent + petitioner/fiduciary, usually not the property — must match decedent to parcel owner via enrichment",
                       "Cloudflare front — may challenge automated clients under load"],
    open_questions=["Phase 1: execute one Clayton filed-date search to confirm result fields (decedent address present?) and pagination."],
)
S["tax_sale_listings"] = src(
    category="lead", subtype="tax_delinquency",
    url="https://publicaccess.claytoncountyga.gov/content/home_commissioner.htm",
    access_pattern="static_html", scraper_module="scrapers/clayton_tax_sale_pdf.py",
    translator="csv_static_list", refresh_cadence="weekly", ttl_days=45,
    source_priority="P0", build_priority="mvp_required", source_reliability_grade="A",
    official_status="OFFICIAL_COUNTY", official_entity="Clayton County Tax Commissioner",
    portal_type="pdf_publication", portal_family="Tyler iasWorld Public Access content folder",
    records_available=["monthly tax sale listing: sale date, parcel #, owner name, situs address, delinquent years, fair market value, cry-out bid"],
    search_fields=[],
    access_method="PDF_PUBLICATION", public_access_status="FULL_PUBLIC_ACCESS",
    document_access_status="DOCUMENTS_PUBLIC",
    source_role="PRIMARY_LEAD_SOURCE", lead_value="LEAD_GENERATING", verification_confidence="HIGH",
    verification_method="official_page_link", verified_from_url="https://www.claytoncountytc.com/tax-sales/",
    verification_note=("claytoncountytc.com/tax-sales links 'View Tax Sale Listings' -> publicaccess.claytoncountyga.gov/content/home_commissioner.htm, "
                       "which links the current 'November 2026 Tax Sales' PDF. Inspected Sep-2026 and Nov-2026 PDFs: text-extractable (pdftotext -layout), "
                       "rows carry sale date, parcel id (e.g. '05079A A026'), owner, situs address, years delinquent, FMV, cry-out bid."),
    sample_record_path_confirmed=True, sample_record_type="Tax sale listing PDF row",
    sample_search_possible=False, sample_document_view_possible=True,
    known_limitations=["Only parcels already levied for sale — not the full delinquent roll",
                       "PDF filenames are ad hoc (e.g. 'NOVEMBER TAX SALE LISTING 10-27-26.pdf'); discover via the landing page, not by URL pattern",
                       "Multi-line owner/address cells require layout-aware parsing; some rows interleave two parcels"],
)
S["excess_funds_listing"] = src(
    category="lead", subtype="tax_delinquency",
    url="https://publicaccess.claytoncountyga.gov/content/PDF/DQ759GA.pdf",
    access_pattern="static_html", scraper_module="scrapers/clayton_tax_sale_pdf.py",
    translator="csv_static_list", refresh_cadence="monthly", ttl_days=120,
    source_priority="P1", build_priority="high_value", source_reliability_grade="A",
    official_status="OFFICIAL_COUNTY", official_entity="Clayton County Tax Commissioner",
    portal_type="pdf_publication", portal_family="Tyler iasWorld Public Access content folder",
    records_available=["excess funds (surplus) after tax sale: former owner name, parcel #, amount, sale date"],
    search_fields=[],
    access_method="PDF_PUBLICATION", public_access_status="FULL_PUBLIC_ACCESS",
    document_access_status="DOCUMENTS_PUBLIC",
    source_role="PRIMARY_LEAD_SOURCE", lead_value="LEAD_GENERATING", verification_confidence="HIGH",
    verification_method="official_page_link", verified_from_url="https://www.claytoncountytc.com/tax-sales/",
    verification_note="Linked from Tax Commissioner tax-sales page ('Excess Funds Listing'). June 2026 listing inspected: text-extractable, name + parcel + amount + sale date.",
    sample_record_path_confirmed=True, sample_record_type="Excess funds listing PDF row",
    sample_search_possible=False, sample_document_view_possible=True,
    known_limitations=["No situs address — parcel id join to assessor required", "Updated irregularly (June 2026 edition current at recon)"],
)
S["gsccca_clerk_indexes"] = src(
    category="lead", subtype="clerk_recordings",
    url="https://search.gsccca.org/RealEstate/",
    access_pattern="paywall", scraper_module="scrapers/gsccca_indexes.py",
    refresh_cadence="daily", ttl_days=365, auth_required=True,
    source_priority="P0", build_priority="high_value", source_reliability_grade="A",
    official_status="OFFICIAL_STATE", official_entity="Georgia Superior Court Clerks' Cooperative Authority (statewide index for Clayton Clerk of Superior Court, county ID 31)",
    portal_type="classic_asp_search", portal_family="GSCCCA search.gsccca.org",
    records_available=["real estate index (deeds, security deeds, deeds under power, lis pendens, affidavits of descent)", "lien index (FiFa / writs, mechanics & materialman liens, federal & state tax liens)", "PT-61 transfer index", "plat index"],
    search_fields=["county", "party name", "instrument type", "date range", "book/page"],
    access_method="PAID_SUBSCRIPTION_REQUIRED", public_access_status="LOGIN_REQUIRED",
    document_access_status="DOCUMENTS_PAID_SUBSCRIPTION_REQUIRED",
    source_role="BLOCKED_SOURCE", lead_value="LEAD_GENERATING", verification_confidence="BLOCKED",
    verification_method="official_page_link", verified_from_url="https://clerk.claytoncountyga.gov/real-estate-division/",
    verification_note=("Clayton Clerk Real Estate Division links to gsccca.org/search. Submitting the Real Estate name-search form returns an auto-submitting "
                       "redirect to apps.gsccca.org/login.asp; instrument-type and daily searches are labelled PREMIUM subscription. No account created (forbidden during recon)."),
    sample_record_path_confirmed=False, sample_record_type="",
    sample_search_possible=False, sample_document_view_possible=False,
    blocker="GSCCCA index search requires a logged-in subscription account; premium tier needed for instrument-type/daily search.",
    blocker_type="PAID_ACCESS_REQUIRED", next_access_strategy="use_paid_subscription_if_operator_provides",
    auto_resolve_status="REQUIRES_PAYMENT", final_resolution_status="PAYMENT_REQUIRED",
    estimated_cost_category="REQUIRES_OPERATOR_APPROVAL",
    auto_resolve_attempts=[{"attempt_order": 1, "strategy": "discover_public_search_endpoint", "timestamp": NOW, "status": "FAILED",
                             "blocker_type": "PAID_ACCESS_REQUIRED", "result": "names.asp POST redirects to login; no anonymous endpoint"},
                            {"attempt_order": 2, "strategy": "use_paid_subscription_if_operator_provides", "timestamp": NOW, "status": "BLOCKED_BY_COST",
                             "blocker_type": "PAID_ACCESS_REQUIRED", "result": "Permission blocker — paid account requires operator approval and credentials",
                             "next_step": "Operator decides on GSCCCA subscription tier"}],
    blocked_unblock_paths=["operator_login", "manual_pull"],
    open_questions=["Does the operator already hold a GSCCCA subscription (and at which tier)? Premium unlocks instrument-type + daily search, which is what a lis pendens / deed-under-power feed needs."],
)
S["ga_public_notice_foreclosures"] = src(
    category="lead", subtype="sheriff_sales",
    url="https://www.georgiapublicnotice.com/",
    access_pattern="spa_recaptcha", scraper_module="scrapers/ga_public_notice.py",
    translator="foreclosure_notices", refresh_cadence="weekly", ttl_days=45,
    source_priority="P0", build_priority="high_value", source_reliability_grade="A",
    official_status="OFFICIAL_VENDOR_PORTAL", official_entity="Georgia Press Association statewide public-notice site (carries Clayton News Daily legal notices)",
    portal_type="aspnet_webforms", portal_family="GeorgiaPublicNotice.com (GPA)",
    records_available=["Notice of Sale Under Power (non-judicial foreclosure)", "Notice of Foreclosure of Right of Redemption (tax deed)", "Sheriff's/Marshal's sales", "Tax sales", "Probate notices", "Condemnations"],
    search_fields=["county", "category", "publication", "date range", "keywords"],
    access_method="PUBLIC_BUT_CAPTCHA_PROTECTED", public_access_status="CAPTCHA_PROTECTED",
    document_access_status="DOCUMENTS_PUBLIC_WITH_CAPTCHA",
    source_role="BLOCKED_SOURCE", lead_value="LEAD_GENERATING", verification_confidence="BLOCKED",
    verification_method="official_page_link", verified_from_url="https://clerk.claytoncountyga.gov/",
    verification_note=("Clayton County Clerk of Superior Court page links 'Legal Notices of the Clayton News-Daily' -> news-daily.com/.../legal/, which 301s to georgiapublicnotice.com. "
                       "County=Clayton search (no CAPTCHA) returned ~100 pages for 9/13–11/12/2026 incl. 'Notice of Sale Under Power ... County of Clayton' and "
                       "'Notice of Foreclosure of Right of Redemption'; results show only a ~300-char teaser. Details.aspx requires a reCAPTCHA challenge and the Terms "
                       "notice prohibits scraping, spidering or use in any database."),
    sample_record_path_confirmed=True, sample_record_type="Search-result teaser (full text CAPTCHA-gated)",
    sample_search_possible=True, sample_document_view_possible=False,
    blocker="Full notice text behind reCAPTCHA; site Terms prohibit automated collection.",
    blocker_type="PERMISSION_BLOCKER", next_access_strategy="manual_operator_assisted_pull",
    auto_resolve_status="NOT_ALLOWED", final_resolution_status="UNRESOLVED_NOT_ALLOWED",
    estimated_cost_category="UNKNOWN",
    auto_resolve_attempts=[{"attempt_order": 1, "strategy": "find_official_vendor_link", "timestamp": NOW, "status": "FAILED",
                             "blocker_type": "PERMISSION_BLOCKER", "result": "Clayton News-Daily legal page redirects to the same GPA site; no separate county posting page found"},
                            {"attempt_order": 2, "strategy": "use_captcha_solver", "timestamp": NOW, "status": "SKIPPED_NOT_ALLOWED",
                             "blocker_type": "PERMISSION_BLOCKER", "result": "CAPTCHA solving forbidden in recon and Terms of Use prohibit scraping",
                             "next_step": "Operator chooses manual notice feed or licensed source"}],
    blocked_unblock_paths=["manual_pull", "operator_login"],
    open_questions=["Operator: would you use GPA 'Smart Search' saved-search email alerts (sign-in) and forward/drop the notices for ingestion, or license notices from GPA / Clayton News Daily directly?",
                    "Alternative: once GSCCCA access exists, foreclosures can be detected post-sale via recorded Deeds Under Power (later in the cycle)."],
)
S["assessor_parcel_master"] = src(
    category="enrichment", subtype="parcel_master",
    url="https://publicaccess.claytoncountyga.gov/search/commonsearch.aspx?mode=realprop",
    access_pattern="static_html", scraper_module="scrapers/clayton_iasworld_publicaccess.py",
    translator="parcel_master", refresh_cadence="weekly", ttl_days=9999,
    source_priority="P2", build_priority="enrichment", source_reliability_grade="A",
    official_status="OFFICIAL_COUNTY", official_entity="Clayton County Tax Assessor / Tax Commissioner",
    portal_type="tyler_iasworld_public_access", portal_family="Tyler iasWorld Public Access",
    records_available=["owner", "mailing address", "situs", "values", "sales", "tax bill"],
    search_fields=["parcel id", "owner", "address", "advanced/sales search"],
    access_method="SEARCHABLE_PUBLIC_PORTAL", public_access_status="FULL_PUBLIC_ACCESS",
    document_access_status="DOCUMENTS_NOT_AVAILABLE",
    source_role="ENRICHMENT_SOURCE", lead_value="ENRICHMENT", verification_confidence="HIGH",
    verification_method="official_domain", verified_from_url="https://publicaccess.claytoncountyga.gov/content/home_commissioner.htm",
    verification_note=("Root redirects to Search/Disclaimer.aspx (click-through disclaimer, session cookie). Tyler branding in page. "
                       "County assessor page also links Schneider Beacon AppID=1234 (Cloudflare challenge to plain HTTP) and an ArcGIS Experience parcel map."),
    sample_record_path_confirmed=False, sample_record_type="",
    sample_search_possible=True, sample_document_view_possible=False,
    known_limitations=["Disclaimer acceptance sets a session; scraper must accept it", "ENRICHMENT ONLY — never originates a lead"],
    fields={"parcel_id": "PARID", "owner_name": "Owner", "situs_address": "Location"},
)
S["federal_bankruptcy_ndga"] = src(
    category="lead", subtype="court_civil",
    url="https://www.ganb.uscourts.gov/",
    access_pattern="paywall", scraper_module="scrapers/pacer_bankruptcy.py",
    refresh_cadence="daily", ttl_days=180, auth_required=True, enabled=False,
    source_priority="P2", build_priority="future", source_reliability_grade="A",
    official_status="OFFICIAL_COURT", official_entity="U.S. Bankruptcy Court, Northern District of Georgia (Atlanta Division covers Clayton)",
    portal_type="pacer_cmecf", portal_family="PACER / CM/ECF",
    records_available=["bankruptcy petitions", "debtor names and addresses", "schedules"],
    search_fields=["name", "case number", "filed date"],
    access_method="PAID_SUBSCRIPTION_REQUIRED", public_access_status="LOGIN_REQUIRED",
    document_access_status="DOCUMENTS_PAID_SUBSCRIPTION_REQUIRED",
    source_role="BLOCKED_SOURCE", lead_value="LEAD_GENERATING", verification_confidence="BLOCKED",
    verification_method="court_portal", verified_from_url="https://www.ganb.uscourts.gov/",
    verification_note="ganb.uscourts.gov reachable; case data is via PACER, which requires a funded account. Per §01.27 q11: flag for operator decision, do not auto-resolve.",
    sample_record_path_confirmed=False, sample_record_type="",
    sample_search_possible=False, sample_document_view_possible=False,
    blocker="PACER account with per-page fees required.", blocker_type="PAID_ACCESS_REQUIRED",
    next_access_strategy="use_paid_subscription_if_operator_provides",
    auto_resolve_status="REQUIRES_PAYMENT", final_resolution_status="PAYMENT_REQUIRED",
    estimated_cost_category="REQUIRES_OPERATOR_APPROVAL",
)
S["ga_sos_business_entities"] = src(
    category="enrichment", subtype="parcel_master",
    url="https://ecorp.sos.ga.gov/",
    access_pattern="waf_imperva", scraper_module="scrapers/none_future.py",
    translator="", refresh_cadence="on_demand", ttl_days=9999, enabled=False, allowed_to_export=False,
    source_priority="P2", build_priority="future", source_reliability_grade="A",
    official_status="OFFICIAL_STATE", official_entity="Georgia Secretary of State Corporations Division",
    portal_type="state_entity_registry", portal_family="GA SOS eCorp",
    records_available=["LLC / corporation registered agents and officers"], search_fields=["entity name"],
    access_method="PUBLIC_BUT_WAF_PROTECTED", public_access_status="WAF_PROTECTED", document_access_status="DOCUMENTS_UNKNOWN",
    source_role="ENRICHMENT_SOURCE", lead_value="ENRICHMENT", verification_confidence="MEDIUM",
    verification_method="official_domain", verified_from_url="https://ecorp.sos.ga.gov/",
    verification_note="Plain HTTP GET returns Cloudflare 'Just a moment...' (403). Recorded per §01.27 q15 as future enrichment only; no adapter.",
    sample_record_path_confirmed=False, sample_record_type="", sample_search_possible=False, sample_document_view_possible=False,
)
S["gsccca_ucc_index"] = src(
    category="enrichment", subtype="parcel_master",
    url="https://search.gsccca.org/UCC_Search/",
    access_pattern="paywall", scraper_module="scrapers/none_future.py",
    translator="", refresh_cadence="on_demand", ttl_days=9999, enabled=False, allowed_to_export=False,
    source_priority="P2", build_priority="future", source_reliability_grade="A",
    official_status="OFFICIAL_STATE", official_entity="GSCCCA statewide UCC index",
    portal_type="classic_asp_search", portal_family="GSCCCA search.gsccca.org",
    records_available=["UCC financing statements"], search_fields=["debtor name", "file number", "secured party"],
    access_method="LOGIN_REQUIRED", public_access_status="LOGIN_REQUIRED", document_access_status="DOCUMENTS_UNKNOWN",
    source_role="ENRICHMENT_SOURCE", lead_value="ENRICHMENT", verification_confidence="MEDIUM",
    verification_method="official_domain", verified_from_url="https://clerk.claytoncountyga.gov/real-estate-division/uniform-commercial-code-ucc/",
    verification_note="Same GSCCCA login wall as the real estate index. Future enrichment only per §01.27 q15.",
    sample_record_path_confirmed=False, sample_record_type="", sample_search_possible=False, sample_document_view_possible=False,
)

# --------------------------------------------------------- lead type sweep
CS = {  # candidate source records for matrix
    "magistrate_court_civil_evictions": ("county_court", "Clayton County (county-built CGI)", "PRIMARY_EVENT_SOURCE", "OPEN_PUBLIC", "BATCH_QUERY", True, False,
                                          ["case_number", "filing_date", "plaintiff", "defendant", "defendant_address", "case_type", "disposition"]),
    "state_court_civil_judgments": ("county_court", "Clayton County (county-built CGI)", "PRIMARY_EVENT_SOURCE", "OPEN_PUBLIC", "BATCH_QUERY", True, False,
                                     ["case_number", "filing_date", "plaintiff", "defendant", "defendant_address", "disposition"]),
    "superior_court_civil": ("county_court", "Clayton County (county-built CGI)", "SUPPORTING_EVENT_SOURCE", "OPEN_PUBLIC", "PER_RECORD_ONLY", False, False,
                             ["case_number", "party_names"]),
    "probate_estates": ("state_vendor_portal", "georgiaprobaterecords.com", "PRIMARY_EVENT_SOURCE", "OPEN_PUBLIC", "BATCH_QUERY", False, True,
                        ["case_number", "decedent_name", "filed_date", "deceased_date"]),
    "tax_sale_listings": ("county_tax_commissioner", "Tyler iasWorld Public Access (PDF)", "PRIMARY_EVENT_SOURCE", "OPEN_PUBLIC", "FULL_COUNTY_BULK", True, True,
                          ["parcel_id", "owner_name", "situs_address", "sale_date", "delinquent_years", "amount"]),
    "excess_funds_listing": ("county_tax_commissioner", "Tyler iasWorld Public Access (PDF)", "PRIMARY_EVENT_SOURCE", "OPEN_PUBLIC", "FULL_COUNTY_BULK", True, True,
                             ["owner_name", "parcel_id", "amount", "sale_date"]),
    "gsccca_clerk_indexes": ("state_clerk_authority", "GSCCCA", "BLOCKED_SOURCE", "PAID_SUBSCRIPTION_REQUIRED", "BATCH_QUERY", False, False, []),
    "ga_public_notice_foreclosures": ("state_press_association", "GeorgiaPublicNotice.com", "BLOCKED_SOURCE", "CAPTCHA_PROTECTED", "BATCH_QUERY", True, False,
                                      ["publication", "publish_date", "notice_teaser"]),
    "federal_bankruptcy_ndga": ("federal_court", "PACER", "BLOCKED_SOURCE", "PAID_SUBSCRIPTION_REQUIRED", "BATCH_QUERY", False, False, []),
}


def cand(sid):
    a, v, role, acc, bulk, samp, docv, fields = CS[sid]
    s = S[sid]
    return {
        "source_id": sid, "official_url": s["url"], "authority_type": a, "vendor_name": v,
        "source_role": role, "access_status": acc, "bulk_availability": bulk,
        "verification_layers": {
            "authority": f"PASS — {s['official_status']} via {s['verified_from_url']}",
            "lead_type_relevance": "PASS" if role != "ENRICHMENT_SOURCE" else "ENRICHMENT",
            "access": "PASS" if acc == "OPEN_PUBLIC" else f"FAIL — {acc}",
            "extractability": "PASS" if samp else "UNPROVEN",
            "refresh_provenance": s["refresh_cadence"],
        },
        "sample_record_path_confirmed": samp, "sample_document_view_possible": docv,
        "minimum_lead_fields_available": fields, "operator_verified": False, "notes": s["verification_note"][:300],
    }


SWEEP = [
    # lead_type, applicability, authorities, sources, selected, status, notes
    ("Foreclosure", "APPLICABLE", ["Legal organ (Clayton News Daily) via GPA", "Clerk of Superior Court (deed under power)"], ["ga_public_notice_foreclosures", "gsccca_clerk_indexes"], "ga_public_notice_foreclosures", "SOURCE_FOUND_CAPTCHA",
     "Georgia is non-judicial (power of sale in security deed). Notices run 4 weeks before first-Tuesday sale. Full text CAPTCHA-gated; GSCCCA paid."),
    ("Trustee Sale", "NOT_APPLICABLE_IN_STATE", ["n/a"], [], "", "NOT_APPLICABLE_IN_STATE", "Georgia uses security deeds with power of sale, not deeds of trust/trustees."),
    ("Notice of Trustee Sale", "NOT_APPLICABLE_IN_STATE", ["n/a"], [], "", "NOT_APPLICABLE_IN_STATE", "See Foreclosure — GA equivalent is Notice of Sale Under Power."),
    ("Notice of Substitute Trustee Sale", "NOT_APPLICABLE_IN_STATE", ["n/a"], [], "", "NOT_APPLICABLE_IN_STATE", "Texas-specific instrument."),
    ("Sheriff Sale", "APPLICABLE", ["Clayton County Sheriff via legal organ"], ["ga_public_notice_foreclosures"], "ga_public_notice_foreclosures", "SOURCE_FOUND_CAPTCHA",
     "No sheriff sale page on claytoncountyga.gov/government/sheriff/ (only jail docket). Levy sales advertised under GPA 'Sheriff's/Marshal's Sales'."),
    ("Tax Lien Foreclosure", "APPLICABLE", ["Tax deed holder via legal organ"], ["ga_public_notice_foreclosures"], "ga_public_notice_foreclosures", "SOURCE_FOUND_CAPTCHA",
     "Observed 'Notice of Foreclosure of Right of Redemption ... County of Clayton' in GPA results."),
    ("Tax Sale", "APPLICABLE", ["Clayton County Tax Commissioner"], ["tax_sale_listings"], "tax_sale_listings", "LIVE_SOURCE_FOUND", "Monthly PDF listings, text-extractable."),
    ("Tax Sale Certificate", "NOT_APPLICABLE_IN_STATE", ["n/a"], [], "", "NOT_APPLICABLE_IN_STATE", "Georgia sells tax deeds with right of redemption, not certificates."),
    ("Tax Delinquency", "APPLICABLE", ["Clayton County Tax Commissioner"], ["tax_sale_listings"], "tax_sale_listings", "LIVE_SOURCE_FOUND_LIMITED_COVERAGE",
     "Only levied parcels appear on sale lists; full delinquent roll not published. iasWorld bill inquiry is per-parcel (enrichment)."),
    ("Lis Pendens", "APPLICABLE", ["Clerk of Superior Court via GSCCCA"], ["gsccca_clerk_indexes"], "gsccca_clerk_indexes", "SOURCE_FOUND_PAID", "GSCCCA subscription required."),
    ("Civil Judgment", "APPLICABLE", ["State Court", "Magistrate Court"], ["state_court_civil_judgments", "magistrate_court_civil_evictions"], "state_court_civil_judgments", "LIVE_SOURCE_FOUND", "Default judgments visible with defendant address."),
    ("Abstract of Judgment", "APPLICABLE", ["Clerk of Superior Court lien index (FiFa)"], ["gsccca_clerk_indexes"], "gsccca_clerk_indexes", "SOURCE_FOUND_PAID", "GA equivalent is a recorded writ of fieri facias (FiFa)."),
    ("Mechanic Lien", "APPLICABLE", ["GSCCCA lien index"], ["gsccca_clerk_indexes"], "gsccca_clerk_indexes", "SOURCE_FOUND_PAID", ""),
    ("Construction Lien", "APPLICABLE", ["GSCCCA lien index"], ["gsccca_clerk_indexes"], "gsccca_clerk_indexes", "SOURCE_FOUND_PAID", "Materialman's liens."),
    ("Federal Tax Lien", "APPLICABLE", ["GSCCCA lien index"], ["gsccca_clerk_indexes"], "gsccca_clerk_indexes", "SOURCE_FOUND_PAID", ""),
    ("State Tax Lien", "APPLICABLE", ["GSCCCA lien index", "GA DOR pending lien search (GSCCCA-hosted, login)"], ["gsccca_clerk_indexes"], "gsccca_clerk_indexes", "SOURCE_FOUND_PAID", ""),
    ("Probate", "APPLICABLE", ["Clayton County Probate Court"], ["probate_estates"], "probate_estates", "LIVE_SOURCE_FOUND", "Free online estate search with filed-date range."),
    ("Affidavit of Heirship", "APPLICABLE", ["Clerk of Superior Court (affidavit of descent)"], ["gsccca_clerk_indexes"], "gsccca_clerk_indexes", "SOURCE_FOUND_PAID", ""),
    ("Executor Deed", "APPLICABLE", ["Clerk of Superior Court"], ["gsccca_clerk_indexes"], "gsccca_clerk_indexes", "SOURCE_FOUND_PAID", ""),
    ("Administrator Deed", "APPLICABLE", ["Clerk of Superior Court"], ["gsccca_clerk_indexes"], "gsccca_clerk_indexes", "SOURCE_FOUND_PAID", ""),
    ("Code Lien", "APPLICABLE", ["Clayton County Code Enforcement"], [], "", "SOURCE_NOT_FOUND", "No online violation/lien lookup found; county pages describe complaint intake only."),
    ("Demolition", "APPLICABLE", ["Clayton County Code Enforcement"], [], "", "SOURCE_NOT_FOUND", "No online demolition / unsafe-structure list found."),
    ("Condemnation", "APPLICABLE", ["Legal organ via GPA"], ["ga_public_notice_foreclosures"], "ga_public_notice_foreclosures", "SOURCE_FOUND_CAPTCHA", "GPA has a Condemnations category."),
    ("Eviction", "APPLICABLE", ["Clayton County Magistrate Court"], ["magistrate_court_civil_evictions"], "magistrate_court_civil_evictions", "LIVE_SOURCE_FOUND", "Dispossessory cases enumerable by sequential case number."),
    ("Divorce", "APPLICABLE", ["Clayton County Superior Court"], ["superior_court_civil"], "superior_court_civil", "LIVE_SOURCE_FOUND_LIMITED_COVERAGE", "Name search only until case-number format confirmed."),
    ("Bankruptcy", "APPLICABLE", ["U.S. Bankruptcy Court N.D. Ga."], ["federal_bankruptcy_ndga"], "federal_bankruptcy_ndga", "SOURCE_FOUND_NEEDS_LOGIN", "PACER."),
    ("Surplus", "APPLICABLE", ["Clayton County Tax Commissioner"], ["excess_funds_listing"], "excess_funds_listing", "LIVE_SOURCE_FOUND", "Excess funds listing PDF."),
    ("Bankruptcy Notice", "APPLICABLE", ["U.S. Bankruptcy Court N.D. Ga."], ["federal_bankruptcy_ndga"], "federal_bankruptcy_ndga", "SOURCE_FOUND_NEEDS_LOGIN", "PACER."),
    ("Public Notice", "APPLICABLE", ["Clayton News Daily via GeorgiaPublicNotice.com"], ["ga_public_notice_foreclosures"], "ga_public_notice_foreclosures", "SOURCE_FOUND_CAPTCHA", ""),
]

lead_types = [{
    "lead_type": lt, "state_applicability": app, "expected_authorities": auth,
    "candidate_sources": [cand(s) for s in srcs], "selected_source_id": sel, "status": st, "notes": n,
} for (lt, app, auth, srcs, sel, st, n) in SWEEP]

LIVE = ["magistrate_court_civil_evictions", "state_court_civil_judgments", "probate_estates", "tax_sale_listings", "excess_funds_listing", "superior_court_civil", "assessor_parcel_master"]
BLOCKED = ["gsccca_clerk_indexes", "ga_public_notice_foreclosures", "federal_bankruptcy_ndga"]

VERDICT = "READY_WITH_BLOCKERS"
VERDICT_REASON = (
    "Five verified, publicly accessible PRIMARY_LEAD_SOURCE feeds: Magistrate Court evictions (daily, HIGH), State Court civil judgments (HIGH), "
    "Probate estates via georgiaprobaterecords.com (MEDIUM — live date search still to be sampled), Tax Commissioner monthly tax sale listings (HIGH) and "
    "excess-funds listing (HIGH). Blocked: GSCCCA clerk indexes (lis pendens, deeds, FiFa/tax/mechanics liens) need a paid subscription; "
    "non-judicial foreclosure / sheriff sale notices on GeorgiaPublicNotice.com are reCAPTCHA-gated with no-scraping terms; bankruptcy needs PACER. "
    "Foreclosure — the highest-value Georgia signal — is therefore not covered until the operator supplies GSCCCA access or a manual notice feed."
)

config = {
    "county_id": SLUG, "county_name": "Clayton", "state": "GA", "subject_state_full": "Georgia",
    "state_rule_family": "", "fips_code": "13063", "timezone": "America/New_York",
    "operator_market_priority": "exploratory",
    "geography": {
        "municipalities": [{"name": n, "code": n.lower().replace(" ", "_")} for n in
                           ["Jonesboro", "Forest Park", "Morrow", "Riverdale", "Lake City", "Lovejoy", "College Park"]],
        "parcel_id_format": "^[0-9]{5}[A-Z]?\\s?[A-Z0-9]{3,6}(\\s[A-Z0-9]{2,3})?$",
        "parcel_id_normalization": "collapse-whitespace",
        "address_format_notes": "Parcel ids seen as '05079A A026', '05015 017010', '05213 214013 A07'. College Park is only partly in Clayton (mostly Fulton).",
    },
    "sources": S,
    "scoring_overrides": {},
    "storage": {"mode": "STATIC_JSON_MODE", "supabase_enabled": False, "retain_raw_records_days": 90, "retain_source_runs_days": 30},
    "dashboard": {
        "title": "Clayton County GA — Lead Intelligence", "subtitle": "Jonesboro, Georgia",
        "primary_color": "#1A3A5C", "accent_color": "#C8A951", "default_view": "all_leads",
        "fields": ["lead_type", "owner_name", "situs_address", "parcel_id", "event_date", "score", "deal_path", "evidence"],
        "precanned_views": [
            {"id": "eviction", "label": "Evictions (tired landlord)", "filter": "lead_type:eviction"},
            {"id": "probate", "label": "Probate", "filter": "lead_type:probate"},
            {"id": "tax_distress", "label": "Tax Sale / Excess Funds", "filter": "lead_type:tax_sale"},
            {"id": "judgment", "label": "Civil Judgments", "filter": "lead_type:civil_judgment"},
            {"id": "high_score", "label": "High Score (≥70)", "filter": "score:>=70"},
        ],
        "view_modes": ["CLIENT_VIEW", "OPERATOR_VIEW"], "build_label": "PARTIAL_BUILD",
        "build_label_reason": "Foreclosure, lis pendens and recorded-lien coverage blocked (GSCCCA paid; GPA notices CAPTCHA/ToS).",
    },
    "deployment": {"github_org": "malexdurant-bot", "github_repo": "xcerebro-county-intel",
                   "scheduler_runtime_class": "SCHEDULER_NOT_CONFIGURED", "production_verification_status": "NOT_RUN"},
    "build_verdict": VERDICT, "build_verdict_reason": VERDICT_REASON, "build_verdict_at": NOW,
    "auto_resolve_status": "REQUIRES_OPERATOR_APPROVAL", "final_resolution_status": "OPERATOR_REQUIRED",
    "operator_override_audit": [],
    "source_of_record_matrix": {
        "county_slug": SLUG, "county_name": "Clayton", "state": "GA", "framework_version": "v5.1.2-beta-r3",
        "generated_at": NOW, "county_build_status": "PARTIAL_BUILD_READY", "lead_types": lead_types,
    },
    "source_coverage_map": {
        "live_sources": LIVE, "blocked_sources": BLOCKED,
        "limited_coverage_sources": ["tax_sale_listings", "superior_court_civil", "probate_estates"],
        "not_found_lead_types": ["Code Lien", "Demolition"],
        "operator_review_required": BLOCKED + ["superior_court_civil"],
    },
    "api_discovery": {
        "searched": ["weba.claytoncountyga.gov/{api,swagger,docs}", "publicaccess.claytoncountyga.gov (Tyler iasWorld — no public API doc)",
                     "georgiaprobaterecords.com (ASP.NET WebForms, no API doc)", "search.gsccca.org (no public API; login)",
                     "georgiapublicnotice.com (no API; ToS forbids automated use)", "ArcGIS Experience parcel map daff6d6be7a14000a1595a99bb67b1f8 (REST layer not enumerated)",
                     "GitHub / Postman: 'Clayton County GA api', 'gsccca api', 'iasworld api'"],
        "found": [{"name": "Clayton County GIS Tax Parcels", "api_type": "ArcGIS",
                   "url": "https://gis.claytoncountyga.gov/server/rest/services/TaxAssessor/Parcels/MapServer/0",
                   "source_role": "ENRICHMENT_SOURCE",
                   "notes": "Found in Build Mode 2026-11-12 via the official Parcel Viewer web map. ~92,100 parcels, maxRecordCount 2000, public query, owner/mailing/situs/value/sale/homestead/DELINQUENT fields."}],
        "search_notes": ("No documented APIs for lead sources. The county case inquiry is a stable GET-parameter CGI (effectively an undocumented HTTP API). "
                         "Bulk parcel enrichment comes from the county ArcGIS MapServer behind the official Parcel Viewer."),
    },
    "enrichment_index_strategy": {
        "bulk_index_available": True,
        "bulk_index_source": "https://gis.claytoncountyga.gov/server/rest/services/TaxAssessor/Parcels/MapServer/0",
        "per_record_query_required": False,
        "per_record_query_cost_estimate": "NONE — full layer cached weekly (~47 paged requests)",
        "recommended_strategy": "scrapers/clayton_parcel_index.py caches the full layer; enrichment by parcel id, and parcel resolution by situs address for court/probate events with owner-name agreement checks.",
    },
}

res = write_county_config(config, str(REPO / "config/counties" / f"{SLUG}.json"),
                          schema_path=str(REPO / "config/counties/_schema.json"),
                          overwrite="--overwrite" in sys.argv)
print(res.summary())
if res.status != "OK":
    sys.exit(1)

# ----------------------------------------------------------- recon artifacts
def block(d, keys):
    return "\n".join(f"    {k}: {d.get(k, '')}" for k in keys) + "\n"

ids = list(S)
W = {}
W["source_discovery.md"] = "# Source discovery — Clayton County, Georgia\n\nPhase 0.A. 15 required queries run (see api_discovery_report.md for query outcomes).\n\n" + "".join(
    f"## {i}\n\n" + block({"name": i, "official_url": S[i]["url"], "page_title": S[i]["official_entity"], "gov_or_aggregator": "government / official vendor",
                           "records_covered": "; ".join(S[i]["records_available"]), "discovered_via_query": S[i]["verified_from_url"]},
                          ["name", "official_url", "page_title", "gov_or_aggregator", "records_covered", "discovered_via_query"]) + "\n" for i in ids) + (
    "## Rejected / reference candidates\n\n"
    "    thecountyoffice.com, searchsystems.net, blackbookonline.info, deeds.com, taxsaleresources.com: third-party aggregators — REJECTED_SOURCE\n"
    "    Clayton County Land Bank Authority (claytoncountyga.gov/?p=32241): found via search; page did not load through the Sucuri front during recon — REFERENCE_ONLY, UNVERIFIED\n"
    "    Clayton County Code Enforcement (Corrections Dept.): intake only, no online records — NOT_FOUND\n"
    "    Sheriff's Office page: jail docket only, no sale listings — REFERENCE_ONLY\n")
W["source_verification.md"] = "# Source verification\n\n" + "".join(
    f"## {i}\n\n" + block(S[i], ["official_status", "verification_method", "verified_from_url", "verification_confidence", "verification_note"]) + "\n" for i in ids)
W["portal_fingerprints.md"] = "# Portal fingerprints\n\n" + "".join(
    f"## {i}\n\n" + block({"name": i, "vendor": S[i]["portal_family"], "detection_heuristics": S[i]["portal_type"],
                           "architecture": S[i]["access_pattern"], "search_interface": ", ".join(S[i]["search_fields"]) or "none (publication)",
                           "result_url_pattern": S[i]["url"], "detail_url_pattern": "see fingerprint json",
                           "scrape_difficulty": {"static_html": "LOW", "spa_with_api": "MEDIUM", "paywall": "HIGH", "spa_recaptcha": "VERY_HIGH", "waf_imperva": "VERY_HIGH"}[S[i]["access_pattern"]]},
                          ["name", "vendor", "detection_heuristics", "architecture", "search_interface", "result_url_pattern", "detail_url_pattern", "scrape_difficulty"]) + "\n" for i in ids)
ACC = {"FULL_PUBLIC_ACCESS": "OPEN_PUBLIC", "LOGIN_REQUIRED": "LOGIN_REQUIRED", "CAPTCHA_PROTECTED": "CAPTCHA_PROTECTED", "WAF_PROTECTED": "BLOCKED"}
W["access_classification.md"] = "# Access classification\n\n" + "".join(
    f"## {i}\n\n" + block({"name": i, "access_classification": ("PAID_SUBSCRIPTION_REQUIRED" if S[i]["access_method"] == "PAID_SUBSCRIPTION_REQUIRED" else ACC[S[i]["public_access_status"]]),
                           "evidence": S[i]["verification_note"], "notes": "; ".join(S[i]["known_limitations"])},
                          ["name", "access_classification", "evidence", "notes"]) + "\n" for i in ids)
W["source_role_classification.md"] = "# Source role classification\n\n" + "".join(
    f"## {i}\n\n" + block({"name": i, "source_role": S[i]["source_role"], "rationale": f"{S[i]['lead_value']}; {S[i]['subtype']}",
                           "section_13_reference": "§13.2 primary" if S[i]["lead_value"] == "LEAD_GENERATING" else "§13.3 enrichment"},
                          ["name", "source_role", "rationale", "section_13_reference"]) + "\n" for i in ids)
W["document_type_discovery.md"] = """# Document type discovery

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
"""
fp_keys = ["url", "portal_type", "portal_family", "access_pattern", "access_method", "public_access_status", "document_access_status",
           "search_fields", "records_available", "verification_note", "known_limitations"]
for i in ids:
    (RECON / "fingerprints" / f"{i}.fingerprint.json").write_text(json.dumps({k: S[i][k] for k in fp_keys} | {"source_id": i, "fingerprinted_at": NOW}, indent=2), encoding="utf-8")
(RECON / "source_of_record_matrix.json").write_text(json.dumps(config["source_of_record_matrix"], indent=2), encoding="utf-8")
W["source_of_record_matrix.md"] = "# Source-of-Record Matrix — Clayton County, GA\n\n| Lead type | Applicability | Selected source | Status |\n|---|---|---|---|\n" + "".join(
    f"| {lt} | {app} | {sel or '—'} | {st} |\n" for (lt, app, _a, _s, sel, st, _n) in SWEEP)
W["source_coverage_map.md"] = "# Source coverage map\n\n" + "\n".join(f"    {k}: {', '.join(v)}" for k, v in config["source_coverage_map"].items()) + "\n"
W["api_discovery_report.md"] = "# API discovery report\n\nDocumented API found: N.\n\nPaths checked:\n\n" + "".join(f"    {x}\n" for x in config["api_discovery"]["searched"]) + "\n" + config["api_discovery"]["search_notes"] + """

## Fifteen Phase 0.A queries — outcomes

    1  county clerk official records        -> clerk.claytoncountyga.gov + GSCCCA (found, paid)
    2  recorder of deeds                    -> GSCCCA real estate index (found, paid)
    3  district clerk court records         -> weba.claytoncountyga.gov case inquiry (found, open)
    4  sheriff sale schedule                -> no county page; GPA Sheriff's Sales category (CAPTCHA)
    5  tax assessor delinquent              -> Tax Commissioner tax sale PDFs (found, open)
    6  tax sale records                     -> same (found, open)
    7  probate court records                -> georgiaprobaterecords.com (found, open)
    8  foreclosure notice                   -> GeorgiaPublicNotice.com / Clayton News Daily (CAPTCHA)
    9  mechanics lien filings               -> GSCCCA lien index (paid)
    10 property assessor                    -> iasWorld Public Access + Beacon (enrichment)
    11 bankruptcy court                     -> N.D. Ga. / PACER (paid)
    12 public notices                       -> GeorgiaPublicNotice.com (CAPTCHA)
    13 land bank                            -> Clayton County Land Bank Authority (reference, unverified)
    14 treasurer billing delinquent         -> no public delinquent roll; per-parcel bill via iasWorld (enrichment)
    15 UCC / entity                         -> GSCCCA UCC (login), GA SOS eCorp (Cloudflare) — future enrichment
"""
(RECON / "operator_verified_sources.yml").write_text("# No operator-verified sources yet.\nsources: []\n", encoding="utf-8")

accessible = [i for i in ids if S[i]["source_role"] == "PRIMARY_LEAD_SOURCE" and S[i]["public_access_status"] == "FULL_PUBLIC_ACCESS"]
handoff = f"""# Build eligibility handoff — Clayton County, GA

## Counts

    verified official sources: {len(ids)}
    PRIMARY_LEAD_SOURCE (accessible): {len(accessible)} — {', '.join(accessible)}
    SUPPORTING_LEAD_SOURCE: superior_court_civil
    ENRICHMENT_SOURCE: assessor_parcel_master, ga_sos_business_entities, gsccca_ucc_index
    BLOCKED_SOURCE: {', '.join(BLOCKED)}

## Blockers by type

    technical: none
    permission: gsccca_clerk_indexes (paid subscription), ga_public_notice_foreclosures (reCAPTCHA + no-scraping terms), federal_bankruptcy_ndga (PACER)
    hard: none
    unknown: none

## Phase 0.5

    All blockers are PERMISSION blockers. Per §4.14 / §01.13 auto-resolve is not allowed for them; requirements recorded, no further attempts.

## Recommended provisional verdict

    {VERDICT}

## Justification trail

""" + "".join(f"    {i}: role={S[i]['source_role']} access={S[i]['public_access_status']} confidence={S[i]['verification_confidence']}\n" for i in ids) + f"""
    Reason: {VERDICT_REASON}

## P0 gate

    GATE PASS — magistrate_court_civil_evictions (daily) and probate_estates (daily) are unblocked P0 sources; tax_sale_listings (P0, refreshed per monthly list) also unblocked.

## Recommended operator next actions

    1. Decide on GSCCCA subscription (Premium tier) to unlock lis pendens, deeds under power and liens.
    2. Decide how to obtain foreclosure notices (GPA Smart Search alerts + manual drop, or a licensed feed).
    3. Supply one Superior Court case number so case-number format can be confirmed.
    4. Approve Build Mode with the five accessible primary sources (PARTIAL_BUILD).
"""
W["build_eligibility_handoff.md"] = handoff
W["build_eligibility_report.md"] = handoff.replace("# Build eligibility handoff", "# Build eligibility report")
W["recon_summary.md"] = f"""# Recon summary — Clayton County, Georgia

    Build verdict: {VERDICT}
    Build label if approved: PARTIAL_BUILD
    Generated: {NOW}

{VERDICT_REASON}

Sample documents inspected: Y — Sep-2026 and Nov-2026 tax sale PDFs, June-2026 excess funds PDF, Magistrate case 2026CM00100, State Court case 2026CV00100.
Documented API found: N (see api_discovery_report.md).
Bulk availability: tax sale + excess funds FULL_COUNTY_BULK; case inquiry BATCH_QUERY (sequential case numbers); probate BATCH_QUERY (filed-date range); Superior Court PER_RECORD_ONLY.

Client access: private GitHub Pages with revocable client access (architecture/11). Not stored in the config because _schema.json has no client_access key.

Detail: build_eligibility_handoff.md
"""
for name, body in W.items():
    (RECON / name).write_text(body, encoding="utf-8")
print("recon artifacts:", sorted(p.name for p in RECON.iterdir()))
