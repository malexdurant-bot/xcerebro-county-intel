# Source verification

## magistrate_court_civil_evictions

    official_status: OFFICIAL_COURT
    verification_method: official_page_link
    verified_from_url: https://clerk.claytoncountyga.gov/case-search/
    verification_confidence: HIGH
    verification_note: Clerk site case-search page iframes weba.claytoncountyga.gov/casinqsvr. Sample GET wci010r.pgm?rtype=E&ctt=M&cyr=2026&ctp=CM&csq=00100 returned case 2026CM00100: landlord plaintiff vs tenant defendant, case type 'Patho Dispossessory', filing 01/02/2026, defendant street address, docket + disposition. Server-rendered HTML, no login, no CAPTCHA.

## state_court_civil_judgments

    official_status: OFFICIAL_COURT
    verification_method: official_page_link
    verified_from_url: https://clerk.claytoncountyga.gov/case-search/
    verification_confidence: HIGH
    verification_note: Sample 2026CV00100 (State Court, ctt=L): filed 01/08/2026, disposition 'Default Judgment' 09/25/2026, defendant name + Jonesboro street address. Same CGI as Magistrate.

## superior_court_civil

    official_status: OFFICIAL_COURT
    verification_method: official_page_link
    verified_from_url: https://clerk.claytoncountyga.gov/case-search/
    verification_confidence: MEDIUM
    verification_note: Name-search form for Superior Court is public. Case-number probes 2026/2025 CV 00001/00100/01000 with ctt=U all returned 'Case# not found' — Superior numbering differs from State/Magistrate, so sequential enumeration is not yet proven.

## probate_estates

    official_status: OFFICIAL_VENDOR_PORTAL
    verification_method: official_vendor_link
    verified_from_url: https://www.claytoncountyga.gov/government/courts/probate-court/online-estate-records/
    verification_confidence: MEDIUM
    verification_note: County probate page instructs: go to georgiaprobaterecords.com > Estates > select Clayton > search by name or by Deceased/Filed date range; 'estate records can be viewed online at no cost'; available documents show a PDF icon. Portal reachable (HTTP 200, Cloudflare front, no challenge to plain GET). A live filed-date search was not executed during recon (ASP.NET/Telerik postback).

## tax_sale_listings

    official_status: OFFICIAL_COUNTY
    verification_method: official_page_link
    verified_from_url: https://www.claytoncountytc.com/tax-sales/
    verification_confidence: HIGH
    verification_note: claytoncountytc.com/tax-sales links 'View Tax Sale Listings' -> publicaccess.claytoncountyga.gov/content/home_commissioner.htm, which links the current 'November 2026 Tax Sales' PDF. Inspected Sep-2026 and Nov-2026 PDFs: text-extractable (pdftotext -layout), rows carry sale date, parcel id (e.g. '05079A A026'), owner, situs address, years delinquent, FMV, cry-out bid.

## excess_funds_listing

    official_status: OFFICIAL_COUNTY
    verification_method: official_page_link
    verified_from_url: https://www.claytoncountytc.com/tax-sales/
    verification_confidence: HIGH
    verification_note: Linked from Tax Commissioner tax-sales page ('Excess Funds Listing'). June 2026 listing inspected: text-extractable, name + parcel + amount + sale date.

## gsccca_clerk_indexes

    official_status: OFFICIAL_STATE
    verification_method: official_page_link
    verified_from_url: https://clerk.claytoncountyga.gov/real-estate-division/
    verification_confidence: BLOCKED
    verification_note: Clayton Clerk Real Estate Division links to gsccca.org/search. Submitting the Real Estate name-search form returns an auto-submitting redirect to apps.gsccca.org/login.asp; instrument-type and daily searches are labelled PREMIUM subscription. No account created (forbidden during recon).

## ga_public_notice_foreclosures

    official_status: OFFICIAL_VENDOR_PORTAL
    verification_method: official_page_link
    verified_from_url: https://clerk.claytoncountyga.gov/
    verification_confidence: BLOCKED
    verification_note: Clayton County Clerk of Superior Court page links 'Legal Notices of the Clayton News-Daily' -> news-daily.com/.../legal/, which 301s to georgiapublicnotice.com. County=Clayton search (no CAPTCHA) returned ~100 pages for 9/13–11/12/2026 incl. 'Notice of Sale Under Power ... County of Clayton' and 'Notice of Foreclosure of Right of Redemption'; results show only a ~300-char teaser. Details.aspx requires a reCAPTCHA challenge and the Terms notice prohibits scraping, spidering or use in any database.

## assessor_parcel_master

    official_status: OFFICIAL_COUNTY
    verification_method: official_domain
    verified_from_url: https://publicaccess.claytoncountyga.gov/content/home_commissioner.htm
    verification_confidence: HIGH
    verification_note: Root redirects to Search/Disclaimer.aspx (click-through disclaimer, session cookie). Tyler branding in page. County assessor page also links Schneider Beacon AppID=1234 (Cloudflare challenge to plain HTTP) and an ArcGIS Experience parcel map.

## federal_bankruptcy_ndga

    official_status: OFFICIAL_COURT
    verification_method: court_portal
    verified_from_url: https://www.ganb.uscourts.gov/
    verification_confidence: BLOCKED
    verification_note: ganb.uscourts.gov reachable; case data is via PACER, which requires a funded account. Per §01.27 q11: flag for operator decision, do not auto-resolve.

## ga_sos_business_entities

    official_status: OFFICIAL_STATE
    verification_method: official_domain
    verified_from_url: https://ecorp.sos.ga.gov/
    verification_confidence: MEDIUM
    verification_note: Plain HTTP GET returns Cloudflare 'Just a moment...' (403). Recorded per §01.27 q15 as future enrichment only; no adapter.

## gsccca_ucc_index

    official_status: OFFICIAL_STATE
    verification_method: official_domain
    verified_from_url: https://clerk.claytoncountyga.gov/real-estate-division/uniform-commercial-code-ucc/
    verification_confidence: MEDIUM
    verification_note: Same GSCCCA login wall as the real estate index. Future enrichment only per §01.27 q15.

