# Access classification

## magistrate_court_civil_evictions

    name: magistrate_court_civil_evictions
    access_classification: OPEN_PUBLIC
    evidence: Clerk site case-search page iframes weba.claytoncountyga.gov/casinqsvr. Sample GET wci010r.pgm?rtype=E&ctt=M&cyr=2026&ctp=CM&csq=00100 returned case 2026CM00100: landlord plaintiff vs tenant defendant, case type 'Patho Dispossessory', filing 01/02/2026, defendant street address, docket + disposition. Server-rendered HTML, no login, no CAPTCHA.
    notes: No date-range or case-type browse; enumeration is by walking sequential case numbers per year/type; Docket shows 'Image Document' entries but images are not served publicly; Tenant-defendant is often not the property owner — eviction signals a landlord-side lead (tired landlord) and must be matched to parcel owner via enrichment

## state_court_civil_judgments

    name: state_court_civil_judgments
    access_classification: OPEN_PUBLIC
    evidence: Sample 2026CV00100 (State Court, ctt=L): filed 01/08/2026, disposition 'Default Judgment' 09/25/2026, defendant name + Jonesboro street address. Same CGI as Magistrate.
    notes: Most judgments are consumer debt (auto finance, credit cards) — judgment debtor must be matched to a parcel owner before it becomes a lead; Judgment becomes a property lien only once the FiFa is recorded in the GSCCCA lien index (paid)

## superior_court_civil

    name: superior_court_civil
    access_classification: OPEN_PUBLIC
    evidence: Name-search form for Superior Court is public. Case-number probes 2026/2025 CV 00001/00100/01000 with ctt=U all returned 'Case# not found' — Superior numbering differs from State/Magistrate, so sequential enumeration is not yet proven.
    notes: Bulk enumeration unproven — currently name search only (PER_RECORD_ONLY)

## probate_estates

    name: probate_estates
    access_classification: OPEN_PUBLIC
    evidence: County probate page instructs: go to georgiaprobaterecords.com > Estates > select Clayton > search by name or by Deceased/Filed date range; 'estate records can be viewed online at no cost'; available documents show a PDF icon. Portal reachable (HTTP 200, Cloudflare front, no challenge to plain GET). A live filed-date search was not executed during recon (ASP.NET/Telerik postback).
    notes: Estate records give decedent + petitioner/fiduciary, usually not the property — must match decedent to parcel owner via enrichment; Cloudflare front — may challenge automated clients under load

## tax_sale_listings

    name: tax_sale_listings
    access_classification: OPEN_PUBLIC
    evidence: claytoncountytc.com/tax-sales links 'View Tax Sale Listings' -> publicaccess.claytoncountyga.gov/content/home_commissioner.htm, which links the current 'November 2026 Tax Sales' PDF. Inspected Sep-2026 and Nov-2026 PDFs: text-extractable (pdftotext -layout), rows carry sale date, parcel id (e.g. '05079A A026'), owner, situs address, years delinquent, FMV, cry-out bid.
    notes: Only parcels already levied for sale — not the full delinquent roll; PDF filenames are ad hoc (e.g. 'NOVEMBER TAX SALE LISTING 10-27-26.pdf'); discover via the landing page, not by URL pattern; Multi-line owner/address cells require layout-aware parsing; some rows interleave two parcels

## excess_funds_listing

    name: excess_funds_listing
    access_classification: OPEN_PUBLIC
    evidence: Linked from Tax Commissioner tax-sales page ('Excess Funds Listing'). June 2026 listing inspected: text-extractable, name + parcel + amount + sale date.
    notes: No situs address — parcel id join to assessor required; Updated irregularly (June 2026 edition current at recon)

## gsccca_clerk_indexes

    name: gsccca_clerk_indexes
    access_classification: PAID_SUBSCRIPTION_REQUIRED
    evidence: Clayton Clerk Real Estate Division links to gsccca.org/search. Submitting the Real Estate name-search form returns an auto-submitting redirect to apps.gsccca.org/login.asp; instrument-type and daily searches are labelled PREMIUM subscription. No account created (forbidden during recon).
    notes: 

## ga_public_notice_foreclosures

    name: ga_public_notice_foreclosures
    access_classification: CAPTCHA_PROTECTED
    evidence: Clayton County Clerk of Superior Court page links 'Legal Notices of the Clayton News-Daily' -> news-daily.com/.../legal/, which 301s to georgiapublicnotice.com. County=Clayton search (no CAPTCHA) returned ~100 pages for 9/13–11/12/2026 incl. 'Notice of Sale Under Power ... County of Clayton' and 'Notice of Foreclosure of Right of Redemption'; results show only a ~300-char teaser. Details.aspx requires a reCAPTCHA challenge and the Terms notice prohibits scraping, spidering or use in any database.
    notes: 

## assessor_parcel_master

    name: assessor_parcel_master
    access_classification: OPEN_PUBLIC
    evidence: Root redirects to Search/Disclaimer.aspx (click-through disclaimer, session cookie). Tyler branding in page. County assessor page also links Schneider Beacon AppID=1234 (Cloudflare challenge to plain HTTP) and an ArcGIS Experience parcel map.
    notes: Disclaimer acceptance sets a session; scraper must accept it; ENRICHMENT ONLY — never originates a lead

## federal_bankruptcy_ndga

    name: federal_bankruptcy_ndga
    access_classification: PAID_SUBSCRIPTION_REQUIRED
    evidence: ganb.uscourts.gov reachable; case data is via PACER, which requires a funded account. Per §01.27 q11: flag for operator decision, do not auto-resolve.
    notes: 

## ga_sos_business_entities

    name: ga_sos_business_entities
    access_classification: BLOCKED
    evidence: Plain HTTP GET returns Cloudflare 'Just a moment...' (403). Recorded per §01.27 q15 as future enrichment only; no adapter.
    notes: 

## gsccca_ucc_index

    name: gsccca_ucc_index
    access_classification: LOGIN_REQUIRED
    evidence: Same GSCCCA login wall as the real estate index. Future enrichment only per §01.27 q15.
    notes: 

