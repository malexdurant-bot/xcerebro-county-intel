# API discovery report

Documented API found: N.

Paths checked:

    weba.claytoncountyga.gov/{api,swagger,docs}
    publicaccess.claytoncountyga.gov (Tyler iasWorld — no public API doc)
    georgiaprobaterecords.com (ASP.NET WebForms, no API doc)
    search.gsccca.org (no public API; login)
    georgiapublicnotice.com (no API; ToS forbids automated use)
    ArcGIS Experience parcel map daff6d6be7a14000a1595a99bb67b1f8 (REST layer not enumerated)
    GitHub / Postman: 'Clayton County GA api', 'gsccca api', 'iasworld api'

No documented APIs for lead sources. The county case inquiry is a stable GET-parameter CGI (effectively an undocumented HTTP API). Bulk parcel enrichment comes from the county ArcGIS MapServer behind the official Parcel Viewer.

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
