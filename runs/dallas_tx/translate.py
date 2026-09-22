"""
Dallas County, TX — raw scraper output -> raw_event_record translators.

Converts each of the 4 verified Dallas scrapers' wrapped-record JSONL output
(raw_record_id/source_id/source_url/source_fetched_at/parser_confidence/
raw_payload/...) into the v5.4.0 staged-pipeline's
`raw_event_record.schema.json` shape consumed by
scaffold.pipeline.debtor_party_engine.resolve_debtor_party (see
scaffold/pipeline/contracts/raw_event_record.schema.json).

canonical_doc_type values here are the LOWERCASE snake_case keys used by
scaffold.pipeline.debtor_party_engine.UNIVERSAL_DEBTOR_PARTY_RULES — a
DIFFERENT namespace than scaffold/pipeline/normalize.py's UPPERCASE
CANONICAL registry (that registry belongs to the older build_leads.py
monolith path; the staged pipeline used here does not call
normalize_doc_type at all).

Known structural data limitations (not bugs — the source portals simply
don't expose these fields at the index/list level):
  - foreclosure_notices (PublicSearch FC department) exposes no
    grantor/grantee/owner name anywhere in the index/list view or the detail
    view's own "Parties" panel ("No parties found" on every notice checked
    live) -> parties=[] always. As of 2026-08-28, scrapers/
    publicsearch_foreclosures_dallas.py addresses this via OCR: it clicks into
    each row's detail view and OCRs the page-1 document image (these are
    typed legal documents, not handwriting — Tesseract reads them cleanly),
    populating raw_payload.document_body_text, which we pass through below so
    notice_of_substitute_trustee_sale's DOCUMENT_BODY debtor rule can extract
    MORTGAGOR/GRANTOR/BORROWER/etc. This resolves most records, but is NOT
    universal: it requires Tesseract to be installed (falls back to None,
    same as before, if missing), only page 1 is OCR'd (a debtor label stated
    only on a later page won't be found), and the extractor's label regex is
    a strict "LABEL:" match (e.g. "Grantor(s):" with a parenthetical doesn't
    match "Grantor"). Records that still can't resolve continue to route to
    REVIEW_REQUIRED/document_body_debtor_not_extractable — still emitted,
    never dropped, per framework philosophy.
  - taxsales_lgbs_dallas rows (both tax_deed and sheriff_sale) expose no
    owner/defendant name either (account/cause-number based, not
    owner-name based) -> parties=[] -> also routes to REVIEW_REQUIRED
    (no_debtor_rule / missing structured party) rather than a clean
    resolution. Still emitted with real address, cause number, sale
    date, and dollar amounts.
  - clerk_recordings and tax_collector DO carry real party/owner names
    and resolve cleanly.

clerk_recordings doc-type filtering: the RP department recording index is
mostly ordinary, non-distress paper (warranty deeds, deeds of trust,
releases of lien). Per this framework's product rule (parcel/recording
bulk data is enrichment-only unless it's a genuine distress EVENT), only
rows whose doc_type matches a known distress-relevant canonical type below
are emitted as raw events; everything else is silently filtered here
(not fed to the pipeline at all) rather than flooding it with
non-distress recording noise.
"""

from __future__ import annotations

import re
from datetime import datetime

SOURCE_ROLE = "PRIMARY_EVENT_SOURCE"

SIGNAL_TYPE_LABELS: dict[str, str] = {
    "notice_of_substitute_trustee_sale": "Foreclosure Notice",
    "tax_foreclosure_notice": "Tax Delinquency Lawsuit",
    "sheriff_sale": "Tax Foreclosure Sale",
    "tax_deed": "Struck-Off Tax Resale",
    "abstract_of_judgment": "Judgment Lien",
    "affidavit_of_heirship": "Affidavit of Heirship",
    "federal_tax_lien": "Federal Tax Lien",
    "state_tax_lien": "State Tax Lien",
    "mechanics_lien": "Mechanic's Lien",
    "construction_lien": "Construction Lien",
    "judgment_lien": "Judgment Lien",
    "municipal_lien": "Municipal Lien",
    "quitclaim_deed": "Quitclaim Deed",
    "executors_deed": "Executor's Deed",
    "administrators_deed": "Administrator's Deed",
    "notice_of_default": "Notice of Default",
    "appointment_of_substitute_trustee": "Appointment of Substitute Trustee",
    "lis_pendens": "Lis Pendens",
    "letters_testamentary": "Letters Testamentary",
    "letters_of_administration": "Letters of Administration",
    "muniment_of_title": "Muniment of Title",
    "determination_of_heirship": "Determination of Heirship",
    "partition_action": "Partition Action",
    "writ_of_possession": "Writ of Possession",
    "final_decree_of_divorce": "Final Decree of Divorce",
    "marital_property_division": "Marital Property Division",
    "code_violation_notice": "Code Violation Notice",
    "demolition_order": "Demolition Order",
    "condemnation_notice": "Condemnation Notice",
    # Dallas client expansion (2026-09-15).
    "trustees_deed_upon_sale": "Trustee's Deed Upon Sale",
    "sheriff_sale": "Forced Sale Deed (Sheriff/Constable/Marshal)",
    "tax_foreclosure_notice": "Tax Foreclosure Notice",
    "tax_deed": "Tax Sale / Tax Deed",
    "tax_sale_certificate": "Tax Sale Certificate",
    "transfer_of_tax_lien": "Transfer of Tax Lien (Property Tax Lender)",
    "administrative_lien": "Administrative Lien",
    "hospital_lien": "Hospital Lien",
    "child_support_lien": "Child Support Lien",
    "forfeiture_of_contract": "Forfeiture of Contract for Deed",
    "guardians_deed": "Guardian's Deed",
    "disclaimer_of_interest": "Disclaimer of Interest",
    "probate": "Probate Filing",
}

# clerk_recordings DOC TYPE (as the PublicSearch RP department publishes it,
# uppercase) -> UNIVERSAL_DEBTOR_PARTY_RULES canonical key. Doc types not in
# this table are ordinary non-distress recordings and are filtered out.
_CLERK_DOC_TYPE_MAP: dict[str, str] = {
    "ABSTRACT OF JUDGMENT": "abstract_of_judgment",
    "AFFIDAVIT OF HEIRSHIP": "affidavit_of_heirship",
    "FEDERAL TAX LIEN": "federal_tax_lien",
    "STATE TAX LIEN": "state_tax_lien",
    "MECHANIC'S LIEN": "mechanics_lien",
    "MECHANICS LIEN": "mechanics_lien",
    "CONSTRUCTION LIEN": "construction_lien",
    "JUDGMENT LIEN": "judgment_lien",
    "MUNICIPAL LIEN": "municipal_lien",
    "QUITCLAIM DEED": "quitclaim_deed",
    "EXECUTOR'S DEED": "executors_deed",
    "EXECUTORS DEED": "executors_deed",
    "ADMINISTRATOR'S DEED": "administrators_deed",
    "ADMINISTRATORS DEED": "administrators_deed",
    "NOTICE OF DEFAULT": "notice_of_default",
    "APPOINTMENT OF SUBSTITUTE TRUSTEE": "appointment_of_substitute_trustee",
    "LIS PENDENS": "lis_pendens",
    "LETTERS TESTAMENTARY": "letters_testamentary",
    "LETTERS OF ADMINISTRATION": "letters_of_administration",
    "MUNIMENT OF TITLE": "muniment_of_title",
    "DETERMINATION OF HEIRSHIP": "determination_of_heirship",
    "PARTITION ACTION": "partition_action",
    "WRIT OF POSSESSION": "writ_of_possession",
    "FINAL DECREE OF DIVORCE": "final_decree_of_divorce",
    "DIVORCE DECREE": "final_decree_of_divorce",
    "MARITAL PROPERTY DIVISION": "marital_property_division",
    "CODE VIOLATION": "code_violation_notice",
    "NOTICE OF VIOLATION": "code_violation_notice",
    "DEMOLITION ORDER": "demolition_order",
    "CONDEMNATION": "condemnation_notice",

    # ------------------------------------------------------------------
    # Dallas client expansion (2026-09-15) — Penny Patel's Tier 1
    # (distress) doc-type list. Raw strings are the county's own exact
    # index spellings (including misspellings) per the client's explicit
    # instruction that these must be searched verbatim or they won't
    # match. Where a client-listed type is structurally identical to an
    # already-verified canonical (e.g. every forced-sale deed variant —
    # sheriff/constable/marshal — behaves the same way for debtor
    # resolution), it's mapped straight onto that existing canonical
    # rather than inventing a near-duplicate. See debtor_party_engine.py
    # and knowledge_base/domain/canonical_doc_types.json for the few
    # genuinely new canonical types this introduces (transfer_of_tax_lien,
    # child_support_lien, forfeiture_of_contract — the last two
    # deliberately have no debtor-party rule yet and route to
    # REVIEW_REQUIRED rather than guess a role direction with no live
    # sample data, same posture the engine already takes elsewhere).
    # ------------------------------------------------------------------
    "APPOINTMENT OF SUBSTITUTE TRUST": "appointment_of_substitute_trustee",
    "APPOINTMENT OF TRUSTEE/SUBSTITUTE TRUSTEE": "appointment_of_substitute_trustee",
    "TRUSTEE'S/SUBSTITUTE TRUSTEE'S DEED": "trustees_deed_upon_sale",
    "TRUSTEE DEED": "trustees_deed_upon_sale",
    "DECLARATION OF INVALIDITY OF FORECLOSURE SALE": "notice_of_substitute_trustee_sale",
    "SHERIFF'S DEED": "sheriff_sale",
    "SHERIFFS DEED": "sheriff_sale",
    "CONSTABLES DEED": "sheriff_sale",
    "MARSHALS DEED": "sheriff_sale",
    "CONSTABLES BILL OF SALE": "sheriff_sale",
    "TAX LIEN": "state_tax_lien",
    # transfer_of_tax_lien deliberately has no debtor_party_engine rule —
    # live-checked and found inconsistent (see debtor_party_engine.py's
    # BROAD_KEY_REGISTRY_ALIASES comment) — still emitted, routes to
    # REVIEW_REQUIRED for manual owner confirmation.
    "TRANSFER OF TAX LIEN": "transfer_of_tax_lien",
    "TAX WARRANT": "tax_foreclosure_notice",
    "TAX SALE": "tax_deed",
    "TAX DEED": "tax_deed",
    "SEIZURE & SALE": "tax_foreclosure_notice",
    "CERTIFICATE OF SALE OF SEIZED PROPERTY": "tax_sale_certificate",
    "ABSTRACT OF ASSESSMENT": "state_tax_lien",
    "MECHANICS LIEN AFFIDAVIT": "mechanics_lien",
    "MECHANIC'S LIEN CONTRACT/AFFIDAVIT": "mechanics_lien",
    "LIEN AFFIDAVIT": "mechanics_lien",
    "LIEN CLAIM": "mechanics_lien",
    "LIEN NOTICE": "mechanics_lien",
    "ASSESSMENT LIEN BY HOMEOWNERS ASN": "municipal_lien",
    "ASSESSMENT LIEN": "municipal_lien",
    "ADMINISTRATIVE LIEN": "administrative_lien",
    "PAVING LIEN": "municipal_lien",
    "LIS PENDENS (NOTICE OF)": "lis_pendens",
    "BANKRUPTCY": "bankruptcy_petition",
    "BANKRUPTCY PROCEEDINGS": "bankruptcy_petition",
    "CONDEMNATION PROCEEDINGS": "condemnation_notice",
    "FORFEITURE OF CONTRACT": "forfeiture_of_contract",
    "HOSPITAL LIEN": "hospital_lien",
    "CHILD SUPPORT LIEN": "child_support_lien",
    # A revocation of a lien RELEASE reinstates the lien — distress again,
    # not a resolution — unlike every other release/partial-release/
    # subordination/termination variant in the portal's FEDERAL TAX LIENS /
    # STATE TAX LIENS / CHILD SUPPORT LIENS / HOSPITAL LIENS groups, which
    # are negative signals (a resolved distress) and are deliberately NOT
    # mapped here, matching this framework's existing release-type posture
    # (see RELEASE_OF_LIEN / RELEASE_OF_FEDERAL_TAX_LIEN in the registry).
    "REVOCATION OF RELEASE OF FEDERAL TAX LIEN": "federal_tax_lien",

    # Tier 2 (life-event) additions.
    "AFFIDAVIT OF HEIRSHIP AND CONVEYANCE": "affidavit_of_heirship",
    "JUDGEMENT DECLARAING HEIRSHIP": "determination_of_heirship",  # verbatim county misspelling
    "PROBATE PROCEEDINGS": "probate",
    "CERTIFIED COPY OF PROBATE": "probate",
    "CERTIFIED COPY OF WILL": "probate",
    "WILL": "probate",
    "GUARDIANS DEED": "guardians_deed",
    "GUARDIANSHIP": "probate",
    "DISCLAIMER": "disclaimer_of_interest",
    # verbatim county misspelling ("ESTAE" for "Estate", "WW" typo) — this
    # is the Texas HHSC Medicaid Estate Recovery Program (MERP).
    "MEDICAID ESTAE RECOVERY PROGRAM NOTICE OF WW OF CLAIM AGAINST ESTATE": "probate",
    "CERTIFIED COPY OF DIVORCE": "final_decree_of_divorce",
    "DIVORCE PROCEEDINGS": "divorce_filing",
    "COMMUNITY PROPERTY SETTLEMENT": "marital_property_division",
    "PARTITION DEED": "partition_action",
    "PARTITION AGREEMENT": "partition_action",
}

# Client-requested Tier 3 (title/payoff/signing — "run on every deal") and
# Tier 4 (competitor/own filings — "who else is working your farm area")
# doc types (2026-09-15). Deliberately NOT added to _CLERK_DOC_TYPE_MAP
# above — per explicit operator instruction, these are the majority of all
# county recordings (ordinary deeds, mortgages, releases) and would drown
# the daily distress signal if treated as leads the same way Tier 1/2 are.
# Instead they power the separate on-demand lookup tool,
# runs/dallas_tx/title_chain_lookup.py, which only searches for these doc
# types against an owner/property already surfaced by a Tier 1/2 lead, and
# links any hits back to that lead. Raw index strings, exact verbatim.
TIER3_DOC_TYPES: tuple[str, ...] = (
    "WARRANTY DEED", "GENERAL WARRANTY DEED", "SPECIAL WARRANTY DEED",
    "QUIT CLAIM DEED", "GIFT DEED", "DEED", "CONTRACT FOR DEED",
    "CORRECTION OF WARRANTY DEED", "CORRECTION AFFIDAVIT",
    "DEED OF TRUST", "AMENDMENT TO DEED OF TRUST",
    "ASSIGNMENT OF DEED OF TRUST", "MORTGAGE", "TRANSFER OF LIEN",
    "EXTENSION OF LIEN", "SUBORDINATION",
    "RELEASE OF LIEN", "RELEASE OF TAX LIEN", "RELEASE OF JUDGMENT",
    "PARTIAL RELEASE OF LIEN", "RELEASE OF LIS PENDENS", "SATISFACTION",
    "POWER OF ATTORNEY", "REVOCATION OF POWER OF ATTORNEY",
    "HOMESTEAD AFFIDAVIT", "HOMESTEAD DECLARATION", "HOMESTEAD DESIGNATION",
    "RESTRICTIVE COVENANTS", "RESTRICTIONS", "EASEMENT", "RIGHT OF WAY",
    "LEASE", "MEMORANDUM OF LEASE", "STATEMENT OF OWNERSHIP & LOCATION",
)
TIER4_DOC_TYPES: tuple[str, ...] = (
    "MEMORANDUM", "MEMORANDUM OF AGREEMENT", "CONTRACT OF SALE",
    "ASSIGNMENT OF CONTRACT", "OPTION",
)

_MDY_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_YMD_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _to_iso_date(raw) -> str | None:
    """Accept M/D/YYYY or already-ISO YYYY-MM-DD; return ISO or None."""
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    m = _MDY_RE.match(raw)
    if m:
        month, day, year = (int(x) for x in m.groups())
        try:
            return f"{year:04d}-{month:02d}-{day:02d}"
        except ValueError:
            return None
    if _YMD_RE.match(raw):
        return raw
    return None


def _party(name: str | None, name_type: str) -> dict | None:
    name = (name or "").strip()
    if not name:
        return None
    return {"name": name, "name_type": name_type, "raw_role": None}


# Added 2026-08-29: Kofile's RP index reuses the same Grantor/Grantee column
# pair for every recorded document type, including non-deed instruments
# where "grantor/grantee" isn't the real-world role — and empirically, which
# column holds the actual debtor FLIPS by doc-type family. Verified against
# the live raw data (not guessed):
#   - Judgment family (85 abstract_of_judgment/lis_pendens sampled): Grantor
#     is consistently a bank/creditor/government entity (CAPITAL ONE BANK
#     USA, FROST BANK, JPMORGAN CHASE BANK, DALLAS COUNTY, ...) and Grantee
#     is consistently the individual/business being sued — i.e. Grantor=
#     Plaintiff, Grantee=Defendant. The shared engine's rule already expects
#     debtor=DF/filer=PL for these types; only the tagging below needs to
#     match that.
#   - Tax lien family (16 state_tax_lien/federal_tax_lien sampled): Grantee
#     is ALWAYS the taxing authority (5/5 "TEXAS STATE[ OF]", 11/11 "U S A
#     INTERNAL REVENUE SERVICE") and Grantor is always the taxpayer — the
#     OPPOSITE of what the shared rule's filer_name_types=['GR'] assumes.
#     Previously this caused the authority to be emitted as "owner_name" and
#     the real taxpayer (sitting right there as Grantor) to never be
#     examined. Tagging grantor "TP" (the rule's exact expected_debtor_
#     name_type, no fallback needed) and grantee "GR" (satisfies
#     filer_name_types) fixes this without touching the shared rule.
#   - mechanics_lien/construction_lien: NOT touched — only 3 live samples
#     and the grantor/grantee pattern was mixed/inconclusive (unlike the
#     tax-lien and judgment families above), so flipping the direction here
#     would be a guess, not a verified fix. Left on the pre-existing GR/GE
#     default, which the shared rule already expects for these two.
#   - hospital_lien (Dallas client expansion, 2026-09-15; 2/2 live samples):
#     Grantor was the individual patient, Grantee the hospital ("PORCH
#     ANTHONY" / "RUIZ LUIS" -> "METHODIST DALLAS MEDICAL CENTER") — same
#     reversed shape as the tax-lien family, not the GR/GE default. Small
#     sample (2), but both consistent and each debtor/filer pair
#     unambiguous (a person's name vs. a named hospital), unlike the
#     genuinely mixed mechanics_lien/transfer_of_tax_lien cases above.
#   - administrative_lien / child_support_lien (Dallas client expansion,
#     2026-09-15): NOT touched — no live samples were checked for these
#     (both are low-volume filing types on this portal). Left on the GR/GE
#     default, same honest "unverified, not a guess we're confident in"
#     posture as mechanics_lien/construction_lien above — if early
#     production leads for either type show an institutional/agency name
#     as owner_name instead of a real person, that is the tell to flip
#     these into _TAX_LIEN_FAMILY_DOC_TYPES like hospital_lien was.
_JUDGMENT_FAMILY_DOC_TYPES = {"abstract_of_judgment", "lis_pendens", "judgment_lien"}
_TAX_LIEN_FAMILY_DOC_TYPES = {
    "state_tax_lien", "federal_tax_lien", "municipal_lien", "hospital_lien",
}

# affidavit_of_heirship (2026-08-29): the shared engine's rule requires
# DOCUMENT_BODY extraction (a "DECEDENT: ..." labelled line in real document
# text) and clerk_recordings has no OCR. But Kofile's Grantee column is
# reliably the decedent — confirmed live: of the ~34 sampled, every record
# whose Grantee carried an explicit "... DECD" suffix (e.g. "GOVAN MOSES
# DECD", "COOPER MARY ELLA DECD") had it in the Grantee slot, never Grantor.
# Rather than change the shared engine's DOCUMENT_BODY contract for this one
# doc type, we synthesize a minimal "DECEDENT: <name>" body string from the
# structured Grantee field so the existing extractor's label matching (which
# already recognizes "DECEDENT") resolves it — no shared-engine change, no
# new OCR dependency.
_DECEDENT_SUFFIX_RE = re.compile(r"\s+(DECD|AKA|DECEASED)\b.*$", re.IGNORECASE)


def _clean_decedent_name(name: str | None) -> str | None:
    name = (name or "").strip()
    if not name:
        return None
    return _DECEDENT_SUFFIX_RE.sub("", name).strip() or None


def translate_clerk_recordings(wrapped_records: list[dict]) -> list[dict]:
    events: list[dict] = []
    for rec in wrapped_records:
        payload = rec.get("raw_payload", {}) or {}
        raw_doc_type = (payload.get("doc_type") or "").strip().upper()
        canonical = _CLERK_DOC_TYPE_MAP.get(raw_doc_type)
        if canonical is None:
            continue  # non-distress recording (deed of trust, warranty deed, release of lien, ...)

        if canonical in _JUDGMENT_FAMILY_DOC_TYPES:
            grantor_type, grantee_type = "PL", "DF"
        elif canonical in _TAX_LIEN_FAMILY_DOC_TYPES:
            grantor_type, grantee_type = "TP", "GR"
        else:
            grantor_type, grantee_type = "GR", "GE"

        parties = []
        for p in (
            _party(payload.get("grantor_name"), grantor_type),
            _party(payload.get("grantee_name"), grantee_type),
        ):
            if p:
                parties.append(p)

        # document_body_text: for affidavit_of_heirship, always use the
        # synthesized DECEDENT-only text built from the index's grantee_name
        # field, never the scraper's real OCR text. grantee_name is clean,
        # structured data -- 100% reliable -- while real OCR text on these
        # documents is noisy and caused the shared engine's ESTATE OF/HEIRS
        # OF matcher to pull garbage fragments ("PAGE 1", "Son",
        # "Granddaughter") or fail to match at all (found 2026-08-30 while
        # diagnosing low estate-contact match rates). For every other
        # canonical doc type, real OCR text (when the scraper captured it --
        # see publicsearch_recorder_dallas.py's DISTRESS_DOC_TYPES) is used
        # as before.
        if canonical == "affidavit_of_heirship":
            decedent = _clean_decedent_name(payload.get("grantee_name"))
            document_body_text = f"DECEDENT: {decedent}" if decedent else None
        else:
            document_body_text = payload.get("document_body_text")

        # situs_address_ocr_hint (2026-08-30): a best-effort address pulled
        # from the recorded document's own text, anchored on the already-
        # resolved debtor name (see publicsearch_recorder_dallas.py's
        # _extract_address_near_name) -- independent of whether that party
        # currently owns property in Dallas County, unlike the DCAD-lookup
        # enrichment path. Re-validated here (not just trusted from the
        # scraper) the same way every other OCR-sourced field in this
        # county's pipeline is: must start with a plausible house number.
        ocr_address_hint = payload.get("situs_address_ocr_hint")
        situs_address = (
            ocr_address_hint
            if ocr_address_hint and re.match(r"^\d", ocr_address_hint.strip())
            else None
        )

        events.append({
            "raw_event_id": rec["raw_record_id"],
            "source_id": "clerk_recordings",
            "source_role": SOURCE_ROLE,
            "raw_doc_type": payload.get("doc_type"),
            "canonical_doc_type": canonical,
            "instrument_number": payload.get("doc_number"),
            "recorded_date": _to_iso_date(payload.get("recorded_date_raw")),
            "event_date": None,
            "source_url": rec.get("source_url") or "about:blank",
            "parties": parties,
            "document_body_text": document_body_text,
            "property_refs": {
                "parcel_id": None,
                "situs_address": situs_address,  # RP index itself exposes city only; see hint above
                "legal_description": payload.get("legal_description") or None,
                "case_number": None,
            },
            "amounts": [],
            "evidence_ids": [],
            "parser_name": "publicsearch_recorder_dallas",
            "parser_version": "1",
            "parser_confidence": rec.get("parser_confidence"),
            "captured_at": rec.get("source_fetched_at"),
        })
    return events


def translate_foreclosure_notices(wrapped_records: list[dict]) -> list[dict]:
    events: list[dict] = []
    for rec in wrapped_records:
        payload = rec.get("raw_payload", {}) or {}
        events.append({
            "raw_event_id": rec["raw_record_id"],
            "source_id": "foreclosure_notices",
            "source_role": SOURCE_ROLE,
            "raw_doc_type": payload.get("doc_type"),
            "canonical_doc_type": "notice_of_substitute_trustee_sale",
            "instrument_number": payload.get("doc_number"),
            "recorded_date": _to_iso_date(payload.get("recorded_date_raw")),
            "event_date": _to_iso_date(payload.get("sale_date_raw")),
            "source_url": rec.get("source_url") or "about:blank",
            "parties": [],  # FC department exposes no grantor/grantee/owner name at the index level
            "document_body_text": payload.get("document_body_text") or None,
            "property_refs": {
                "parcel_id": None,
                # Despite the scraper's "property_city" naming, live data shows
                # this field holds a full street address (street, city, state,
                # zip) on most records, not just a city name — but "CITY, TEXAS"
                # (2 comma-separated parts) is still a bare city/state, so
                # require at least 3 parts (street, city, state[, zip]) before
                # treating it as a real address.
                "situs_address": (
                    payload.get("city")
                    if payload.get("city") and len(str(payload.get("city")).split(",")) >= 3
                    else None
                ),
                "legal_description": None,
                "case_number": None,
            },
            "amounts": [],
            "evidence_ids": [],
            "parser_name": "publicsearch_foreclosures_dallas",
            "parser_version": "1",
            "parser_confidence": rec.get("parser_confidence"),
            "captured_at": rec.get("source_fetched_at"),
        })
    return events


TAX_COLLECTOR_MIN_DUE_YEAR = 2024  # see translate_tax_collector docstring


def stream_translate_tax_collector(path, verbose: bool = True) -> "tuple[list[dict], dict[str, float]]":
    """Stream tax_collector.jsonl line-by-line (it's ~1.4M lines / ~1.4GB —
    do NOT json.loads the whole file into a list first).

    2026-09-15 Dallas client request, revised: tax leads stay suit-required
    ONLY (the pre-existing _translate_tax_collector_row path, unchanged —
    an earlier version of this session's work added a second, no-suit-
    required "aged delinquency" lead type; the operator asked to drop that
    after seeing it more than double daily lead volume with lower-
    confidence signals). What the operator DID want kept: the years-
    delinquent number itself, as a filterable/visible attribute on the
    suit-based leads that already exist — so this function also aggregates
    each account's OLDEST unpaid due_date across every delinquent tax year
    (not just the suit-triggering one) and returns it as a separate
    {account: years_delinquent} map. The caller (run_pipeline.py) attaches
    this onto each suit-based lead's parcel_display; it never gates which
    rows become leads.

    Returns (events, years_delinquent_by_account). Prints progress every
    200k lines scanned so a long run doesn't look stalled."""
    import json as _json

    events: list[dict] = []
    accounts_agg: dict[str, dict] = {}
    scanned = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            scanned += 1
            if verbose and scanned % 200_000 == 0:
                print(f"  [translate] tax_collector: scanned {scanned}, kept {len(events)}", flush=True)
            line = line.strip()
            if not line:
                continue
            rec = _json.loads(line)
            translated = _translate_tax_collector_row(rec)
            if translated is not None:
                events.append(translated)
            _accumulate_delinquency_aggregate(rec, accounts_agg)
    years_delinquent_by_account = _years_delinquent_by_account(accounts_agg)
    if verbose:
        print(f"  [translate] tax_collector: done — scanned {scanned}, "
              f"{len(events)} suit-based events kept, years-delinquent computed "
              f"for {len(years_delinquent_by_account)} accounts", flush=True)
    return events, years_delinquent_by_account


def _accumulate_delinquency_aggregate(rec: dict, agg: dict) -> None:
    """Per-account rollup across a delinquent account's multiple tax-year
    rows (tax_collector.jsonl already only contains TOT_AMT_DUE > 0 rows —
    see tax_collector_dallas.py). Tracks the OLDEST unpaid due_date (the
    real delinquency age) so a multi-year-delinquent account gets one
    years-delinquent number, not one per year row."""
    payload = rec.get("raw_payload", {}) or {}
    account = payload.get("account")
    due_date = payload.get("due_date")
    if not account or not due_date:
        return
    entry = agg.get(account)
    if entry is None:
        entry = agg[account] = {"earliest_due_date": due_date}
    if due_date < entry["earliest_due_date"]:
        entry["earliest_due_date"] = due_date


def _years_delinquent_by_account(agg: dict) -> dict[str, float]:
    """{account: years_delinquent} for every account with a valid oldest
    unpaid due_date -- no threshold, every value is returned so the
    dashboard can filter/sort at whatever cutoff the operator picks, per
    the operator's explicit "give the option to see years of delinquency
    and filter for years delinquent" instruction. Only meaningful for
    accounts that also produced a suit-based lead (this map is looked up
    by parcel_id on those leads); accounts with no lawsuit are computed
    here too but simply never get looked up, since they're not leads."""
    from datetime import date as _date

    as_of = _date.today()
    out: dict[str, float] = {}
    for account, entry in agg.items():
        try:
            earliest_dt = _date.fromisoformat(entry["earliest_due_date"])
        except (ValueError, TypeError):
            continue
        out[account] = round((as_of - earliest_dt).days / 365.25, 1)
    return out


def _translate_tax_collector_row(rec: dict) -> dict | None:
    """Only rows with a real filed lawsuit (suit_pending + causeno) are
    genuine recorded/filed EVENTS; merely-delinquent-without-suit rows are
    balance-sheet facts, not discrete events, and are filtered out here
    (they remain available in the raw JSONL for enrichment/scoring use
    later if the framework grows a delinquency-only lead pattern).

    Of the 1,410,641 delinquent rows in the weekly TRW file, 249,153 carry
    a filed suit -- but that set spans decades (oldest observed due_date
    1975), most of which are long-dormant/uncollectible rather than
    current actionable distress. Additionally restricted here to
    due_date >= TAX_COLLECTOR_MIN_DUE_YEAR (~40-60k rows/year recently) so
    the lead volume stays both product-sensible (a 250k-lead single-source
    dump is not a curated lead list) and tractable for the per-record
    staged pipeline (debtor resolution + aggregation + scoring). Raise
    this constant (or drop the filter) once the pipeline's performance on
    large volumes has been separately validated. Returns None to skip."""
    payload = rec.get("raw_payload", {}) or {}
    if not payload.get("suit_pending") or not payload.get("causeno"):
        return None
    due_date = payload.get("due_date") or ""
    if not due_date[:4].isdigit() or int(due_date[:4]) < TAX_COLLECTOR_MIN_DUE_YEAR:
        return None

    parties = []
    p = _party(payload.get("owner_name"), "TP")
    if p:
        parties.append(p)

    amounts = []
    for label in ("tot_amt_due", "levy_balance", "tot_amt_due_90"):
        val = payload.get(label)
        if val is not None:
            amounts.append({"label": label, "value": val})

    return {
        "raw_event_id": rec["raw_record_id"],
        "source_id": "tax_collector",
        "source_role": SOURCE_ROLE,
        "raw_doc_type": "TAX_LAWSUIT_PENDING",
        "canonical_doc_type": "tax_foreclosure_notice",
        "instrument_number": payload.get("account"),
        "recorded_date": None,  # no clerk recording event; this is a tax-roll status row
        "event_date": _to_iso_date(payload.get("due_date")),
        "source_url": rec.get("source_url") or "about:blank",
        "parties": parties,
        "document_body_text": None,
        "property_refs": {
            # TRW's PARCEL_NO is NOT a reliable property identifier -- spot
            # checks show it frequently doesn't even match the row's own
            # address (e.g. parcel_no "2602" on a record whose address is
            # "3225 E LEDBETTER DR" and whose parcel_name is an unrelated
            # street). The most degenerate case, parcel_no "0", caused every
            # such row to collide into one artificial merged lead; using
            # parcel_no at all (zero or not) causes smaller-scale collisions
            # across unrelated properties that merely share a short numeric
            # value. Use the tax account number instead, unconditionally --
            # it's the field verified unique per account+year+jurisdiction
            # (see tax_collector_dallas.py's _raw_record_id) and correctly
            # groups a single property's multi-year delinquency history
            # into one lead, same as translate_taxsales_lgbs does with
            # account_nbr for its own sources.
            "parcel_id": payload.get("account") or None,
            "situs_address": payload.get("address") or None,
            "legal_description": None,
            "case_number": payload.get("causeno"),
        },
        "amounts": amounts,
        "evidence_ids": [],
        "parser_name": "tax_collector_dallas",
        "parser_version": "1",
        "parser_confidence": rec.get("parser_confidence"),
        "captured_at": rec.get("source_fetched_at"),
    }


def translate_taxsales_lgbs(
    wrapped_records: list[dict],
    dcad_lookup: dict | None = None,
) -> list[dict]:
    """Handles BOTH tax_foreclosure_resales (source_id already set to that
    by the scraper) and sheriff_sales rows — dispatch is by the wrapped
    record's own source_id, not by re-deriving sale_type here.

    dcad_lookup: optional {account_nbr: {owner_name, situs_address, ...} |
    None} from scrapers/parcel_master_dcad_dallas.py. The LGBS feed itself
    exposes no owner/defendant name (raw_doc_type has no party field at
    all), which otherwise leaves every one of these leads permanently
    debtor-unresolved and — per §13.14 — parcel-unresolved too even though
    property_refs.parcel_id (the account number) is known. When a DCAD
    lookup is supplied and matches, its owner_name becomes this event's
    party, letting debtor resolution succeed and the real address surface
    on the dashboard via the same primary_parcel_id path tax_collector uses.
    """
    dcad_lookup = dcad_lookup or {}
    events: list[dict] = []
    for rec in wrapped_records:
        payload = rec.get("raw_payload", {}) or {}
        source_id = rec.get("source_id")
        canonical = "tax_deed" if source_id == "tax_foreclosure_resales" else "sheriff_sale"

        amounts = []
        for label in ("appraised_value", "minimum_bid"):
            val = payload.get(label)
            if val is not None:
                try:
                    amounts.append({"label": label, "value": float(val)})
                except (TypeError, ValueError):
                    pass

        account_nbr = payload.get("account_nbr")
        dcad_match = dcad_lookup.get(account_nbr) if account_nbr else None
        parties = []
        if dcad_match and dcad_match.get("owner_name"):
            # tax_deed's §17 rule expects TP (taxpayer); sheriff_sale's
            # expects DF (defendant) with NO fallback at all -- tagging both
            # canonicals "TP" (fixed 2026-08-30) meant every one of 476
            # sheriff_sales leads silently ignored a DCAD-matched owner name
            # and routed to REVIEW_REQUIRED "expected_debtor_name_type DF
            # missing" regardless of match rate, confirmed live: 0/476
            # resolved before this fix, 100% hitting that exact reason.
            debtor_type = "TP" if canonical == "tax_deed" else "DF"
            p = _party(dcad_match["owner_name"], debtor_type)
            if p:
                parties.append(p)

        events.append({
            "raw_event_id": rec["raw_record_id"],
            "source_id": source_id,
            "source_role": SOURCE_ROLE,
            "raw_doc_type": payload.get("sale_type"),
            "canonical_doc_type": canonical,
            "instrument_number": None,
            "recorded_date": None,
            "event_date": _to_iso_date(payload.get("sale_date")),
            "source_url": rec.get("source_url") or "about:blank",
            "parties": parties,  # LGBS itself exposes no owner/defendant name; populated from DCAD when matched
            "document_body_text": None,
            "property_refs": {
                "parcel_id": payload.get("account_nbr") or None,
                "situs_address": payload.get("address") or None,
                "legal_description": None,
                "case_number": payload.get("cause_nbr") or None,
            },
            "amounts": amounts,
            "evidence_ids": [],
            "parser_name": "taxsales_lgbs_dallas",
            "parser_version": "1",
            "parser_confidence": rec.get("parser_confidence"),
            "captured_at": rec.get("source_fetched_at"),
        })
    return events
