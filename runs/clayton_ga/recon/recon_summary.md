# Recon summary — Clayton County, Georgia

    Build verdict: READY_WITH_BLOCKERS
    Build label if approved: PARTIAL_BUILD
    Generated: 2026-11-12T15:30:00Z

Five verified, publicly accessible PRIMARY_LEAD_SOURCE feeds: Magistrate Court evictions (daily, HIGH), State Court civil judgments (HIGH), Probate estates via georgiaprobaterecords.com (MEDIUM — live date search still to be sampled), Tax Commissioner monthly tax sale listings (HIGH) and excess-funds listing (HIGH). Blocked: GSCCCA clerk indexes (lis pendens, deeds, FiFa/tax/mechanics liens) need a paid subscription; non-judicial foreclosure / sheriff sale notices on GeorgiaPublicNotice.com are reCAPTCHA-gated with no-scraping terms; bankruptcy needs PACER. Foreclosure — the highest-value Georgia signal — is therefore not covered until the operator supplies GSCCCA access or a manual notice feed.

Sample documents inspected: Y — Sep-2026 and Nov-2026 tax sale PDFs, June-2026 excess funds PDF, Magistrate case 2026CM00100, State Court case 2026CV00100.
Documented API found: N (see api_discovery_report.md).
Bulk availability: tax sale + excess funds FULL_COUNTY_BULK; case inquiry BATCH_QUERY (sequential case numbers); probate BATCH_QUERY (filed-date range); Superior Court PER_RECORD_ONLY.

Client access: private GitHub Pages with revocable client access (architecture/11). Not stored in the config because _schema.json has no client_access key.

Detail: build_eligibility_handoff.md
