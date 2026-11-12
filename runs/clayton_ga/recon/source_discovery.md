# Source discovery — Clayton County, Georgia

Phase 0.A. 15 required queries run (see api_discovery_report.md for query outcomes).

## magistrate_court_civil_evictions

    name: magistrate_court_civil_evictions
    official_url: https://weba.claytoncountyga.gov/casinqsvr/htdocs/index.shtml
    page_title: Clayton County Clerk of Superior & Magistrate Courts (Magistrate Court civil)
    gov_or_aggregator: government / official vendor
    records_covered: dispossessory (Patho) filings; magistrate civil suits; garnishments; docket activity; party names and addresses; disposition
    discovered_via_query: https://clerk.claytoncountyga.gov/case-search/

## state_court_civil_judgments

    name: state_court_civil_judgments
    official_url: https://weba.claytoncountyga.gov/casinqsvr/htdocs/index.shtml
    page_title: State Court of Clayton County (via county Case Inquiry)
    gov_or_aggregator: government / official vendor
    records_covered: civil suits; default judgments; dispositions; party names and addresses
    discovered_via_query: https://clerk.claytoncountyga.gov/case-search/

## superior_court_civil

    name: superior_court_civil
    official_url: https://weba.claytoncountyga.gov/casinqcgi-bin/wci011r.pgm?rtype=I&dvt=V
    page_title: Clayton County Superior Court (via county Case Inquiry)
    gov_or_aggregator: government / official vendor
    records_covered: divorce; quiet title / equity; civil; party names
    discovered_via_query: https://clerk.claytoncountyga.gov/case-search/

## probate_estates

    name: probate_estates
    official_url: https://www.georgiaprobaterecords.com/
    page_title: Clayton County Probate Court (Georgia Probate Records statewide portal)
    gov_or_aggregator: government / official vendor
    records_covered: estates; wills; guardianships; deaths; hearing calendars
    discovered_via_query: https://www.claytoncountyga.gov/government/courts/probate-court/online-estate-records/

## tax_sale_listings

    name: tax_sale_listings
    official_url: https://publicaccess.claytoncountyga.gov/content/home_commissioner.htm
    page_title: Clayton County Tax Commissioner
    gov_or_aggregator: government / official vendor
    records_covered: monthly tax sale listing: sale date, parcel #, owner name, situs address, delinquent years, fair market value, cry-out bid
    discovered_via_query: https://www.claytoncountytc.com/tax-sales/

## excess_funds_listing

    name: excess_funds_listing
    official_url: https://publicaccess.claytoncountyga.gov/content/PDF/DQ759GA.pdf
    page_title: Clayton County Tax Commissioner
    gov_or_aggregator: government / official vendor
    records_covered: excess funds (surplus) after tax sale: former owner name, parcel #, amount, sale date
    discovered_via_query: https://www.claytoncountytc.com/tax-sales/

## gsccca_clerk_indexes

    name: gsccca_clerk_indexes
    official_url: https://search.gsccca.org/RealEstate/
    page_title: Georgia Superior Court Clerks' Cooperative Authority (statewide index for Clayton Clerk of Superior Court, county ID 31)
    gov_or_aggregator: government / official vendor
    records_covered: real estate index (deeds, security deeds, deeds under power, lis pendens, affidavits of descent); lien index (FiFa / writs, mechanics & materialman liens, federal & state tax liens); PT-61 transfer index; plat index
    discovered_via_query: https://clerk.claytoncountyga.gov/real-estate-division/

## ga_public_notice_foreclosures

    name: ga_public_notice_foreclosures
    official_url: https://www.georgiapublicnotice.com/
    page_title: Georgia Press Association statewide public-notice site (carries Clayton News Daily legal notices)
    gov_or_aggregator: government / official vendor
    records_covered: Notice of Sale Under Power (non-judicial foreclosure); Notice of Foreclosure of Right of Redemption (tax deed); Sheriff's/Marshal's sales; Tax sales; Probate notices; Condemnations
    discovered_via_query: https://clerk.claytoncountyga.gov/

## assessor_parcel_master

    name: assessor_parcel_master
    official_url: https://publicaccess.claytoncountyga.gov/search/commonsearch.aspx?mode=realprop
    page_title: Clayton County Tax Assessor / Tax Commissioner
    gov_or_aggregator: government / official vendor
    records_covered: owner; mailing address; situs; values; sales; tax bill
    discovered_via_query: https://publicaccess.claytoncountyga.gov/content/home_commissioner.htm

## federal_bankruptcy_ndga

    name: federal_bankruptcy_ndga
    official_url: https://www.ganb.uscourts.gov/
    page_title: U.S. Bankruptcy Court, Northern District of Georgia (Atlanta Division covers Clayton)
    gov_or_aggregator: government / official vendor
    records_covered: bankruptcy petitions; debtor names and addresses; schedules
    discovered_via_query: https://www.ganb.uscourts.gov/

## ga_sos_business_entities

    name: ga_sos_business_entities
    official_url: https://ecorp.sos.ga.gov/
    page_title: Georgia Secretary of State Corporations Division
    gov_or_aggregator: government / official vendor
    records_covered: LLC / corporation registered agents and officers
    discovered_via_query: https://ecorp.sos.ga.gov/

## gsccca_ucc_index

    name: gsccca_ucc_index
    official_url: https://search.gsccca.org/UCC_Search/
    page_title: GSCCCA statewide UCC index
    gov_or_aggregator: government / official vendor
    records_covered: UCC financing statements
    discovered_via_query: https://clerk.claytoncountyga.gov/real-estate-division/uniform-commercial-code-ucc/

## Rejected / reference candidates

    thecountyoffice.com, searchsystems.net, blackbookonline.info, deeds.com, taxsaleresources.com: third-party aggregators — REJECTED_SOURCE
    Clayton County Land Bank Authority (claytoncountyga.gov/?p=32241): found via search; page did not load through the Sucuri front during recon — REFERENCE_ONLY, UNVERIFIED
    Clayton County Code Enforcement (Corrections Dept.): intake only, no online records — NOT_FOUND
    Sheriff's Office page: jail docket only, no sale listings — REFERENCE_ONLY
