# Portal fingerprints

## magistrate_court_civil_evictions

    name: magistrate_court_civil_evictions
    vendor: IBM i CGI (casinqcgi-bin/wci01xr.pgm) — county-built JIS front end
    detection_heuristics: county_case_inquiry_cgi
    architecture: static_html
    search_interface: court (L/M/U), case year, case type (2 chars), case sequence (5 digits), last/first name
    result_url_pattern: https://weba.claytoncountyga.gov/casinqsvr/htdocs/index.shtml
    detail_url_pattern: see fingerprint json
    scrape_difficulty: LOW

## state_court_civil_judgments

    name: state_court_civil_judgments
    vendor: IBM i CGI — county-built JIS front end
    detection_heuristics: county_case_inquiry_cgi
    architecture: static_html
    search_interface: case year, case type, case sequence, name
    result_url_pattern: https://weba.claytoncountyga.gov/casinqsvr/htdocs/index.shtml
    detail_url_pattern: see fingerprint json
    scrape_difficulty: LOW

## superior_court_civil

    name: superior_court_civil
    vendor: IBM i CGI — county-built JIS front end
    detection_heuristics: county_case_inquiry_cgi
    architecture: static_html
    search_interface: name (court=U), case number (format unconfirmed)
    result_url_pattern: https://weba.claytoncountyga.gov/casinqcgi-bin/wci011r.pgm?rtype=I&dvt=V
    detail_url_pattern: see fingerprint json
    scrape_difficulty: LOW

## probate_estates

    name: probate_estates
    vendor: georgiaprobaterecords.com (Council of Probate Court Judges of Georgia portal)
    detection_heuristics: aspnet_webforms_telerik
    architecture: spa_with_api
    search_interface: county, last/first name, deceased date range, filed date range, case number
    result_url_pattern: https://www.georgiaprobaterecords.com/
    detail_url_pattern: see fingerprint json
    scrape_difficulty: MEDIUM

## tax_sale_listings

    name: tax_sale_listings
    vendor: Tyler iasWorld Public Access content folder
    detection_heuristics: pdf_publication
    architecture: static_html
    search_interface: none (publication)
    result_url_pattern: https://publicaccess.claytoncountyga.gov/content/home_commissioner.htm
    detail_url_pattern: see fingerprint json
    scrape_difficulty: LOW

## excess_funds_listing

    name: excess_funds_listing
    vendor: Tyler iasWorld Public Access content folder
    detection_heuristics: pdf_publication
    architecture: static_html
    search_interface: none (publication)
    result_url_pattern: https://publicaccess.claytoncountyga.gov/content/PDF/DQ759GA.pdf
    detail_url_pattern: see fingerprint json
    scrape_difficulty: LOW

## gsccca_clerk_indexes

    name: gsccca_clerk_indexes
    vendor: GSCCCA search.gsccca.org
    detection_heuristics: classic_asp_search
    architecture: paywall
    search_interface: county, party name, instrument type, date range, book/page
    result_url_pattern: https://search.gsccca.org/RealEstate/
    detail_url_pattern: see fingerprint json
    scrape_difficulty: HIGH

## ga_public_notice_foreclosures

    name: ga_public_notice_foreclosures
    vendor: GeorgiaPublicNotice.com (GPA)
    detection_heuristics: aspnet_webforms
    architecture: spa_recaptcha
    search_interface: county, category, publication, date range, keywords
    result_url_pattern: https://www.georgiapublicnotice.com/
    detail_url_pattern: see fingerprint json
    scrape_difficulty: VERY_HIGH

## assessor_parcel_master

    name: assessor_parcel_master
    vendor: Tyler iasWorld Public Access
    detection_heuristics: tyler_iasworld_public_access
    architecture: static_html
    search_interface: parcel id, owner, address, advanced/sales search
    result_url_pattern: https://publicaccess.claytoncountyga.gov/search/commonsearch.aspx?mode=realprop
    detail_url_pattern: see fingerprint json
    scrape_difficulty: LOW

## federal_bankruptcy_ndga

    name: federal_bankruptcy_ndga
    vendor: PACER / CM/ECF
    detection_heuristics: pacer_cmecf
    architecture: paywall
    search_interface: name, case number, filed date
    result_url_pattern: https://www.ganb.uscourts.gov/
    detail_url_pattern: see fingerprint json
    scrape_difficulty: HIGH

## ga_sos_business_entities

    name: ga_sos_business_entities
    vendor: GA SOS eCorp
    detection_heuristics: state_entity_registry
    architecture: waf_imperva
    search_interface: entity name
    result_url_pattern: https://ecorp.sos.ga.gov/
    detail_url_pattern: see fingerprint json
    scrape_difficulty: VERY_HIGH

## gsccca_ucc_index

    name: gsccca_ucc_index
    vendor: GSCCCA search.gsccca.org
    detection_heuristics: classic_asp_search
    architecture: paywall
    search_interface: debtor name, file number, secured party
    result_url_pattern: https://search.gsccca.org/UCC_Search/
    detail_url_pattern: see fingerprint json
    scrape_difficulty: HIGH

