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
}

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
_JUDGMENT_FAMILY_DOC_TYPES = {"abstract_of_judgment", "lis_pendens", "judgment_lien"}
_TAX_LIEN_FAMILY_DOC_TYPES = {"state_tax_lien", "federal_tax_lien", "municipal_lien"}

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

        document_body_text = None
        if canonical == "affidavit_of_heirship":
            decedent = _clean_decedent_name(payload.get("grantee_name"))
            if decedent:
                document_body_text = f"DECEDENT: {decedent}"

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
                "situs_address": None,  # RP index exposes city only, not street address
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


def stream_translate_tax_collector(path, verbose: bool = True) -> list[dict]:
    """Stream tax_collector.jsonl line-by-line (it's ~1.4M lines / ~1.4GB —
    do NOT json.loads the whole file into a list first) and translate only
    the rows that pass the suit_pending + recency filter, discarding the
    rest immediately. Prints progress every 200k lines scanned so a long
    run doesn't look stalled."""
    import json as _json

    events: list[dict] = []
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
    if verbose:
        print(f"  [translate] tax_collector: done — scanned {scanned}, kept {len(events)}", flush=True)
    return events


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
            p = _party(dcad_match["owner_name"], "TP")
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
