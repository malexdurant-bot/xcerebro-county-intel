"""
Shelby County, TN — distress lead pipeline.

Phase 1 (MVP) sources:
  trustee_tax_sale  — Tax sale property list (CSV from S3, CONFIRMED accessible)

Phase 2 sources (REQUIRES_PLAYWRIGHT):
  register_shelby           — Register of Deeds (APPT/IRS/LIEN/NCTS/TNTX/STR)
  chancery_court_shelby     — Chancery Court (Lis Pendens, partition, quiet title)
  general_sessions_shelby   — General Sessions Civil (evictions / FED)
  probate_court_shelby      — Probate Court (estates, letters testamentary)

Enrichment:
  parcel_master_shelby  — ArcGIS CurrentParcels (owner name, mailing address)

Usage:
    python runs/shelby_tn/run_pipeline.py
    python runs/shelby_tn/run_pipeline.py --max-records 50
    python runs/shelby_tn/run_pipeline.py --scrape      # download fresh CSV first

Output: runs/shelby_tn/pipeline_output/
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# The scheduled daily task runs this under cmd.exe's default console code
# page (cp1252), not UTF-8. A stray non-cp1252 character in any print (e.g.
# the "->" arrow U+2192 in the client-dashboard status line) raises
# UnicodeEncodeError *after* all real work for the run has already
# succeeded, which reports the whole pipeline as failed. Force UTF-8 with
# replacement so an unencodable character degrades the print, not the run.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Repo bootstrap
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_THIS_DIR = Path(__file__).parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from build_config import (  # noqa: E402
    COUNTY_ID, COUNTY_NAME, STATE,
    TAX_SALE_JSONL, REGISTER_JSONL, CHANCERY_JSONL,
    GENERAL_SESSIONS_JSONL, PROBATE_JSONL,
    PARCEL_CACHE_PATH, OUTPUT_DIR,
)
from parcel_resolver import ParcelResolver  # noqa: E402
from tax_sale_adapter import load_tax_sale_jsonl, build_tax_sale_raw_events  # noqa: E402
from register_adapter import load_register_jsonl, build_register_raw_events  # noqa: E402
from eviction_adapter import load_eviction_jsonl, build_eviction_raw_events  # noqa: E402
from chancery_adapter import load_chancery_jsonl, build_chancery_raw_events  # noqa: E402
from probate_adapter import load_probate_jsonl, build_probate_raw_events  # noqa: E402

from scaffold.pipeline import debtor_party_engine  # noqa: E402
from scaffold.pipeline.debtor_party_engine import UNIVERSAL_DEBTOR_PARTY_RULES  # noqa: E402
from scaffold.pipeline.run_pipeline_staged import (  # noqa: E402
    run_staged_pipeline,
    build_dashboard_payload,
)

# ---------------------------------------------------------------------------
# §17 debtor-party rules override for TN tax sale
#
# tax_sale_certificate: taxpayer (TP) is the debtor.
# TP is supplied by the ArcGIS parcel lookup (same as Maricopa treasurer).
# Records without an ArcGIS hit emit empty parties -> §17 routes to
# REVIEW_REQUIRED "owner_not_on_document".
# ---------------------------------------------------------------------------

_DEBTOR_RULES: dict = {
    **UNIVERSAL_DEBTOR_PARTY_RULES,
    # Shelby Phase 2 — court case types not in universal rules
    "partition_action": {
        "expected_debtor_name_type": "DF",
        "fallback_debtor_name_type": "PL",
        "filer_name_types": ["PL"],
        "debtor_source": "STRUCTURED",
        "known_filer_role": "plaintiff co-owner seeking partition",
        "missing_debtor_review_reason": "owner_not_on_document",
    },
    "quiet_title_action": {
        "expected_debtor_name_type": "DF",
        "fallback_debtor_name_type": "PL",
        "filer_name_types": ["PL"],
        "debtor_source": "STRUCTURED",
        "known_filer_role": "plaintiff claiming clear title",
        "missing_debtor_review_reason": "owner_not_on_document",
    },
    # Probate — override DOCUMENT_BODY fan-out rule so the decedent name (GR)
    # is used directly as the debtor instead of requiring document body text.
    "letters_testamentary": {
        "expected_debtor_name_type": "GR",
        "fallback_debtor_name_type": None,
        "filer_name_types": ["OTHER"],
        "debtor_source": "STRUCTURED",
        "known_filer_role": "personal representative / executor",
        "missing_debtor_review_reason": "owner_not_on_document",
    },
    # Shelby Register of Deeds — overrides for universal rules that expect TP/DF
    # but the Register adapter emits GR (grantor) for the debtor party because
    # TN Register of Deeds records use Grantor/Grantee headers, not PL/DF.
    "judgment_lien": {
        "expected_debtor_name_type": "GR",   # JUDGMENT_DEBTOR is the Grantor
        "fallback_debtor_name_type": "GE",
        "filer_name_types": ["GE"],
        "debtor_source": "STRUCTURED",
        "known_filer_role": "judgment creditor",
        "missing_debtor_review_reason": "owner_not_on_document",
    },
    "federal_tax_lien": {
        "expected_debtor_name_type": "GR",   # TAXPAYER is the Grantor
        "fallback_debtor_name_type": "GE",
        "filer_name_types": ["GE"],
        "debtor_source": "STRUCTURED",
        "known_filer_role": "federal taxing authority (IRS)",
        "missing_debtor_review_reason": "owner_not_on_document",
    },
    "state_tax_lien": {
        "expected_debtor_name_type": "GR",   # TAXPAYER is the Grantor
        "fallback_debtor_name_type": "GE",
        "filer_name_types": ["GE"],
        "debtor_source": "STRUCTURED",
        "known_filer_role": "state taxing authority (TN DOR)",
        "missing_debtor_review_reason": "owner_not_on_document",
    },
    # APPT / STR: homeowner not on document — we inject parcel owner via
    # _enrich_events_by_address before the pipeline runs.
    "appointment_of_substitute_trustee": {
        "expected_debtor_name_type": "GR",   # injected parcel owner (first GR)
        "fallback_debtor_name_type": None,
        "filer_name_types": ["GE"],
        "debtor_source": "STRUCTURED",
        "known_filer_role": "lender / mortgagee appointing substitute trustee",
        "missing_debtor_review_reason": "owner_not_on_document",
    },
    "sub_trustees_deed": {
        "expected_debtor_name_type": "GR",   # injected parcel owner (first GR)
        "fallback_debtor_name_type": None,
        "filer_name_types": ["GE"],
        "debtor_source": "STRUCTURED",
        "known_filer_role": "substitute trustee (completed foreclosure)",
        "missing_debtor_review_reason": "owner_not_on_document",
    },
    # County tax sale notice — GE = delinquent taxpayer
    "county_tax_sale_notice": {
        "expected_debtor_name_type": "GE",
        "fallback_debtor_name_type": "GR",
        "filer_name_types": [],
        "debtor_source": "STRUCTURED",
        "known_filer_role": "county trustee / taxing authority",
        "missing_debtor_review_reason": "owner_not_on_document",
    },
}


# ---------------------------------------------------------------------------
# Parcel dict builder (maps ArcGIS parcel fields -> scoring seam shape)
# ---------------------------------------------------------------------------

def _enrich_events_by_address(
    register_events: list[dict],
    resolver: "ParcelResolver",
    verbose: bool,
) -> None:
    """
    For each raw event that has an address in property_refs.legal_description,
    look it up in the Shelby County parcel layer and:
      1. Set property_refs.parcel_id so the scoring seam can enrich the lead.
      2. For APPT / STR doc types (where the homeowner is NOT listed in the
         document parties), prepend the parcel OWNER as a GR party so the
         debtor_party_engine can resolve the owner_name.

    Works for Register of Deeds events and eviction events (both store the
    property address in legal_description after their respective adapters run).
    Address format: "530 MARIANNA ST MEMPHIS, TN 38111" or "1446 HARRISON ST Memphis TN 38108"
    """
    from scrapers.parcel_master_shelby import lookup_by_address

    _NEEDS_OWNER_INJECT = {"appointment_of_substitute_trustee", "sub_trustees_deed"}

    # Regex to extract "number + street name + suffix" without trailing city name.
    # Handles addresses like "530 MARIANNA ST MEMPHIS" -> "530 MARIANNA ST".
    _STREET_RE = re.compile(
        r"^(\d+(?:\s+\S+){1,6}?\s+(?:ST|AVE|RD|DR|LN|CT|BLVD|WAY|PL|TER|CIR|HWY|PKWY|"
        r"CV|COVE|PIKE|RUN|TRL|TRAIL|LOOP|XING|BND|PT|HTS|GLEN|VIEW|WALK|PASS|GROVE|ROW))\b",
        re.IGNORECASE,
    )
    # Matches: number, optional single-word directional, then first word of street name
    _NUM_WORD_RE = re.compile(
        r"^(\d+)\s+(?:N|S|E|W|NE|NW|SE|SW)\s+(\S+)|^(\d+)\s+(\S+)", re.IGNORECASE
    )

    def _normalize_street(s: str) -> str:
        """Expand-to-abbreviate: ArcGIS stores abbreviated directionals and suffixes."""
        for full, abbr in [
            ("EAST", "E"), ("WEST", "W"), ("NORTH", "N"), ("SOUTH", "S"),
            ("PLACE", "PL"), ("DRIVE", "DR"), ("COURT", "CT"), ("COVE", "CV"),
            ("BOULEVARD", "BLVD"), ("TERRACE", "TER"), ("AVENUE", "AVE"),
            ("STREET", "ST"), ("HIGHWAY", "HWY"), ("PARKWAY", "PKWY"),
        ]:
            s = re.sub(r"\b" + full + r"\b", abbr, s, flags=re.IGNORECASE)
        return s.strip()

    def _lookup_with_fallbacks(street: str) -> list[dict]:
        """Try ArcGIS lookup with normalization and number+first-word fallback."""
        candidates = lookup_by_address(street, max_results=8)
        if candidates:
            return candidates
        norm = _normalize_street(street)
        if norm.upper() != street.upper():
            candidates = lookup_by_address(norm, max_results=8)
            if candidates:
                return candidates
        # Last resort: just number + first non-directional word of street name
        m2 = _NUM_WORD_RE.match(street)
        if m2:
            num = m2.group(1) or m2.group(3)
            word = m2.group(2) or m2.group(4)
            frag = f"{num} {word}"
            if len(frag) > 5:
                candidates = lookup_by_address(frag, max_results=8)
        return candidates

    enriched = 0
    for event in register_events:
        refs = event.get("property_refs", {})
        legal_desc = (refs.get("legal_description") or "").strip()
        if not legal_desc or refs.get("parcel_id"):
            continue

        # Parse the street address (drop city/state/zip after first comma)
        raw_street = legal_desc.split(",")[0].strip().upper()
        # Further extract just number+name+suffix, dropping trailing city name
        m = _STREET_RE.match(raw_street)
        street = m.group(1).strip() if m else raw_street
        if len(street) < 6:
            continue

        try:
            candidates = _lookup_with_fallbacks(street)
        except Exception as exc:
            if verbose:
                print(f"  [Reg Addr] lookup error for {street!r}: {exc}", flush=True)
            continue

        if not candidates:
            continue

        # Pick best candidate: prefer exact street-number prefix match
        best = None
        num_prefix = re.match(r"^(\d+)\s", street)
        if num_prefix and len(candidates) > 1:
            num = num_prefix.group(1)
            for cand in candidates:
                par_addr = (cand.get("PAR_ADDR1") or "").strip().upper()
                if par_addr.startswith(num + " "):
                    best = cand
                    break
        if best is None and len(candidates) == 1:
            best = candidates[0]
        if best is None:
            continue

        parcel_id = (best.get("PARCELID") or "").strip()
        if not parcel_id:
            continue

        refs["parcel_id"] = parcel_id
        resolver._cache[parcel_id] = best
        enriched += 1

        # For APPT/STR: inject parcel owner as first GR party so debtor rule resolves
        doc_type = event.get("canonical_doc_type", "")
        if doc_type in _NEEDS_OWNER_INJECT:
            owner = (best.get("OWNER") or "").strip()
            if owner:
                event["parties"].insert(0, {
                    "name": owner,
                    "name_type": "GR",
                    "raw_role": "PROPERTY_OWNER_FROM_PARCEL",
                })
                if verbose:
                    print(
                        f"  [Reg Addr] {doc_type} {street!r} -> {parcel_id} owner={owner!r}",
                        flush=True,
                    )

    if verbose:
        print(f"  [Reg Addr] {enriched}/{len(register_events)} events matched to parcels", flush=True)


def _build_probate_name_map(probate_events: list[dict]) -> dict[str, dict]:
    """
    Return {decedent_name_upper: {"executor_name": str, "case_number": str}}
    from the raw probate events so the post-pipeline contact enrichment can
    retrieve the executor name for a given scored lead (which only carries
    the decedent as owner_name).
    """
    out: dict[str, dict] = {}
    for ev in probate_events:
        decedent = None
        executor = None
        for party in ev.get("parties", []):
            if party.get("raw_role") == "DECEDENT":
                decedent = party.get("name", "").strip()
            elif party.get("raw_role") == "PERSONAL_REPRESENTATIVE":
                executor = party.get("name", "").strip()
        if decedent:
            out[decedent.upper()] = {
                "executor_name": executor or "",
                "case_number": ev.get("property_refs", {}).get("case_number", ""),
            }
    return out


def _cross_ref_tps_addresses(
    addresses: list[str],
    decedent_name: str,
    verbose: bool,
) -> tuple[Optional[str], Optional[str]]:
    """
    For each address returned by TruePeopleSearch, look it up in the Shelby
    County parcel layer and check whether the owner name matches the decedent.

    Returns (confirmed_address_str, confirmed_parcel_id) or (None, None).
    """
    from scrapers.parcel_master_shelby import lookup_by_address

    def _tokens(s: str) -> set[str]:
        return {t for t in re.sub(r"[^A-Z ]", "", s.upper()).split() if len(t) > 1}

    decedent_tokens = _tokens(decedent_name)

    for raw_addr in addresses:
        # Extract street portion for ArcGIS query
        street_match = re.search(
            r"\d{1,5}\s+[A-Z][A-Za-z ]{2,30}"
            r"(?:St|Ave|Rd|Dr|Ln|Ct|Blvd|Way|Pl|Ter|Cir|Hwy|Pkwy|Pike)\b",
            raw_addr,
            re.IGNORECASE,
        )
        if not street_match:
            # Fallback: first segment before comma
            street = raw_addr.split(",")[0].strip().upper()
        else:
            street = street_match.group(0).strip().upper()

        if len(street) < 5:
            continue

        try:
            candidates = lookup_by_address(street, max_results=15)
        except Exception as exc:
            if verbose:
                print(f"    [CrossRef] Parcel lookup error for {street!r}: {exc}", flush=True)
            continue

        for cand in candidates:
            owner = (cand.get("OWNER") or "").strip().upper()
            owner_tokens = _tokens(owner)
            overlap = len(decedent_tokens & owner_tokens)
            if overlap >= 2:
                situs = (cand.get("PAR_ADDR1") or "").strip()
                city = (cand.get("MUNI") or "Memphis").strip()
                confirmed = f"{situs}, {city}, TN" if situs else None
                parcel_id = (cand.get("PARCELID") or "").strip() or None
                if verbose:
                    print(
                        f"    [CrossRef] Confirmed: {decedent_name!r} -> "
                        f"{confirmed} (overlap={overlap})",
                        flush=True,
                    )
                return confirmed, parcel_id

    return None, None


_PARCEL_MATCH_REVIEW_FLAGS: dict[str, str] = {
    "ambiguous_name": "parcel_match_ambiguous_common_name",
    "no_candidates": "parcel_match_not_found",
    "low_confidence_match": "parcel_match_low_confidence",
    "name_too_short": "parcel_match_name_too_short",
    "lookup_error": "parcel_match_lookup_error",
}


def _enrich_probate_contacts(
    scored_leads: list[dict],
    probate_name_map: dict[str, dict],
    cache_path: Path,
    verbose: bool,
    match_status: dict[str, str] | None = None,
) -> None:
    """
    Post-pipeline TruePeopleSearch enrichment for probate scored leads.

    For each probate lead:
      1. Search TPS for decedent name -> collect listed addresses
      2. Cross-reference each address with Shelby County parcel layer
         to confirm property ownership
      3. Search TPS for executor name -> collect phone numbers
      4. Attach contact_info dict to the scored lead in place
      5. Tag review_flags with *why* the parcel/skip-trace match failed
         (ambiguous common name, no candidates, TPS blocked, etc.) so these
         leads stay visible for manual client review instead of just
         showing a blank address.

    Modifies scored_leads in place; never raises (failures logged and skipped).
    """
    match_status = match_status or {}
    try:
        from scrapers.truepeoplesearch_shelby import batch_enrich_leads
    except ImportError as exc:
        print(f"  [CONTACT] TruePeopleSearch scraper unavailable: {exc}", flush=True)
        return

    probate_leads = [
        lead for lead in scored_leads
        if "probate_court_shelby" in lead.get("source_ids", [])
    ]

    if not probate_leads:
        return

    print(
        f"\n[CONTACT] TruePeopleSearch enrichment for {len(probate_leads)} probate leads "
        f"(cache: {cache_path.name})...",
        flush=True,
    )

    # Build the batch input list (always includes executor_name from CourtConnect)
    batch_input = []
    for lead in probate_leads:
        decedent_name = lead.get("owner_name", "").strip()
        if not decedent_name:
            continue
        name_info = probate_name_map.get(decedent_name.upper(), {})
        executor_name = name_info.get("executor_name", "")
        batch_input.append({"decedent_name": decedent_name, "executor_name": executor_name})

    # Run single-browser batch (4s inter-lead delay)
    try:
        tps_results = batch_enrich_leads(
            batch_input,
            city_state="Memphis TN",
            inter_lead_delay=4.0,
            cache_path=cache_path,
            verbose=verbose,
        )
    except Exception as exc:
        if verbose:
            print(f"  [CONTACT] TPS batch error: {exc}", flush=True)
        tps_results = {}

    # Apply results to scored leads
    enriched = 0
    for lead in probate_leads:
        decedent_name = lead.get("owner_name", "").strip()
        if not decedent_name:
            continue

        name_info = probate_name_map.get(decedent_name.upper(), {})
        executor_name = name_info.get("executor_name", "")
        tps = tps_results.get(decedent_name.upper())

        confirmed_address, confirmed_parcel_id = None, None
        if tps:
            confirmed_address, confirmed_parcel_id = _cross_ref_tps_addresses(
                tps.get("tps_decedent_addresses", []),
                decedent_name,
                verbose,
            )

        lead["contact_info"] = {
            "executor_name": executor_name or None,
            "executor_phone": tps.get("executor_phone") if tps else None,
            "confirmed_address": confirmed_address,
            "confirmed_parcel_id": confirmed_parcel_id,
            "tps_decedent_addresses": tps.get("tps_decedent_addresses", []) if tps else [],
            "tps_enriched": bool(tps and tps.get("tps_enriched")),
        }

        if confirmed_address and not lead.get("parcel_display"):
            lead["tps_confirmed_address"] = confirmed_address
        if confirmed_parcel_id and not lead.get("primary_parcel_id"):
            lead["tps_confirmed_parcel_id"] = confirmed_parcel_id

        flags = list(lead.get("review_flags") or [])
        parcel_flag = _PARCEL_MATCH_REVIEW_FLAGS.get(
            match_status.get(decedent_name.upper(), "")
        )
        if parcel_flag and parcel_flag not in flags:
            flags.append(parcel_flag)
        if not lead["contact_info"]["tps_enriched"] and not confirmed_address:
            if "skiptrace_unconfirmed" not in flags:
                flags.append("skiptrace_unconfirmed")
        lead["review_flags"] = flags

        enriched += 1

    print(
        f"  [CONTACT] Enriched {enriched}/{len(probate_leads)} probate leads "
        f"(TPS hits: {sum(1 for r in tps_results.values() if r.get('tps_enriched'))})",
        flush=True,
    )


# Name suffixes that must never be mistaken for a surname. Decedent names
# arrive in two different conventions from the court docket ("SPEARS II" /
# "GRIFFITH JR" with no comma, and "MILLER, SR." / "HURT, SR." where the
# comma sets off the suffix rather than separating last-from-first) — both
# need the suffix stripped before the true last name is taken.
_NAME_SUFFIXES = frozenset({"JR", "SR", "II", "III", "IV", "V"})


def _extract_last_name(decedent_name: str) -> str:
    """
    Best-effort last-name extraction across the court docket's inconsistent
    name formats: "LASTNAME, FIRST MIDDLE" (comma = true last-name split),
    "FIRST MIDDLE LASTNAME, SUFFIX" (comma sets off a suffix, not the name),
    and "FIRST MIDDLE LASTNAME [SUFFIX]" (no comma at all).
    """
    name = decedent_name.strip()
    if "," in name:
        before, after = name.split(",", 1)
        after_tok = after.strip().rstrip(".").upper()
        if after_tok in _NAME_SUFFIXES:
            parts = before.strip().split()
            return parts[-1] if parts else ""
        return before.strip()

    parts = name.split()
    while parts and parts[-1].rstrip(".").upper() in _NAME_SUFFIXES:
        parts.pop()
    return parts[-1] if parts else ""


def _enrich_probate_by_name(
    probate_events: list[dict],
    resolver: "ParcelResolver",
    verbose: bool,
) -> dict[str, str]:
    """
    For each probate raw event, query the parcel layer (the county tax roll)
    by decedent last name and disambiguate against the full candidate set —
    this IS the "search the tax roll for an address" path for probate leads
    that have no address in the source posting.

    Confidence rules:
      - Query uses 'LASTNAME ' (trailing space) so '%SMALL %' won't match SMALLWOOD
      - Pull up to 200 candidates (the tax roll's own cap) — do NOT bail out
        early just because a common surname returns a lot of rows; score
        every candidate instead of giving up before looking.
      - Score by # of meaningful name tokens (len > 1) shared with OWNER
      - Require best score >= 2 (last name + at least one other name token)
        AND a *unique* top scorer — a tie at the best score is genuine
        ambiguity (two same-named owners), not a resolvable match.

    Returns {decedent_name_upper: reason} for every event that did NOT get a
    parcel match, so callers can surface *why* a lead has no address instead
    of leaving it silently blank (reason in "ambiguous_name" / "no_candidates"
    / "low_confidence_match" / "name_too_short").
    """
    from scrapers.parcel_master_shelby import lookup_by_owner

    enriched = 0
    match_status: dict[str, str] = {}
    for event in probate_events:
        decedent_name = None
        for party in event.get("parties", []):
            if party.get("raw_role") == "DECEDENT":
                decedent_name = party.get("name", "").strip()
                break
        if not decedent_name:
            continue
        decedent_key = decedent_name.upper()

        last_name = _extract_last_name(decedent_name)

        if len(last_name) < 3:
            match_status[decedent_key] = "name_too_short"
            continue

        # Add trailing space so '%LASTNAME %' won't match LASTNAMEMORE (e.g. SMALL vs SMALLWOOD)
        try:
            candidates = lookup_by_owner(last_name + " ", max_results=200)
        except Exception as exc:
            if verbose:
                print(f"  [Probate Enrich] lookup error for {last_name!r}: {exc}", flush=True)
            match_status[decedent_key] = "lookup_error"
            continue

        if not candidates:
            match_status[decedent_key] = "no_candidates"
            continue

        # Score every candidate by meaningful token overlap (exclude single-char
        # initials) — never bail out on candidate count alone; a large tax-roll
        # hit set is fine as long as full-name overlap narrows it to one owner.
        name_tokens = {
            t for t in decedent_name.upper().replace(",", "").replace(".", "").split()
            if len(t) > 1
        }
        scored: list[tuple[int, dict]] = []
        for cand in candidates:
            owner_tokens = {
                t for t in (cand.get("OWNER") or "").upper().replace(",", "").replace(".", "").split()
                if len(t) > 1
            }
            score = len(name_tokens & owner_tokens)
            if score > 0:
                scored.append((score, cand))

        if not scored:
            match_status[decedent_key] = "low_confidence_match"
            continue

        best_score = max(s for s, _ in scored)
        top = [c for s, c in scored if s == best_score]

        if best_score < 2:
            match_status[decedent_key] = "low_confidence_match"
            continue
        if len(top) > 1:
            if verbose:
                print(
                    f"  [Probate Enrich] {decedent_name!r}: {len(top)} candidates tied "
                    f"at score={best_score} of {len(candidates)} '{last_name}' hits — ambiguous, skip",
                    flush=True,
                )
            match_status[decedent_key] = "ambiguous_name"
            continue

        best = top[0]
        parcel_id = (best.get("PARCELID") or "").strip()
        if parcel_id:
            event["property_refs"]["parcel_id"] = parcel_id
            resolver._cache[parcel_id] = best
            enriched += 1
            if verbose:
                print(
                    f"  [Probate Enrich] {decedent_name} -> {parcel_id} "
                    f"{best.get('PAR_ADDR1','')} (score={best_score} of {len(candidates)} '{last_name}' hits)",
                    flush=True,
                )
        else:
            match_status[decedent_key] = "low_confidence_match"

    if verbose:
        print(f"  [Probate Enrich] {enriched}/{len(probate_events)} events matched to parcels", flush=True)
    return match_status


# Words that indicate a business entity — skip name-based parcel lookup for these
# since entities are too ambiguous (multiple properties, common partial names).
_ENTITY_WORDS = frozenset({
    "LLC", "INC", "CORP", "CORPORATION", "ASSOCIATION", "TRUST",
    "LP", "LLP", "LTD", "CO", "GROUP", "HOLDINGS", "PROPERTIES",
    "REALTY", "INVESTMENTS", "MANAGEMENT", "SERVICES", "ENTERPRISES",
})

# Register doc types that don't include a property address but ARE linked to a
# specific debtor name — look up the debtor in the parcel layer to find what they own.
_REGISTER_NAME_LOOKUP_TYPES = frozenset({
    "federal_tax_lien",
    "state_tax_lien",
    "judgment_lien",
    "county_tax_sale_notice",
})


def _enrich_register_by_owner_name(
    register_events: list[dict],
    resolver: "ParcelResolver",
    verbose: bool,
) -> None:
    """
    For register tax/lien events that have no property address in the posting,
    look up the debtor (GR party) by last name in the Shelby County parcel layer.

    A tax lien attaches to all real property the debtor owns, so finding their
    parcel gives us the property the lien is secured against.

    Same confidence rules as _enrich_probate_by_name:
      - Skip entity names (LLC, INC, etc.)
      - Trailing space on last name to avoid prefix matches
      - Skip if > 10 candidates (common name)
      - Token overlap score >= 2 required
    """
    from scrapers.parcel_master_shelby import lookup_by_owner

    enriched = 0
    attempted = 0
    for event in register_events:
        if event.get("canonical_doc_type") not in _REGISTER_NAME_LOOKUP_TYPES:
            continue
        if (event.get("property_refs") or {}).get("parcel_id"):
            continue  # already resolved by address lookup

        # Get the GR (grantor/debtor) party name
        debtor_name = next(
            (p["name"].strip() for p in event.get("parties", []) if p.get("name_type") == "GR"),
            None,
        )
        if not debtor_name:
            continue

        # Skip entity names — too ambiguous for single-property matching
        name_upper_words = set(debtor_name.upper().split())
        if name_upper_words & _ENTITY_WORDS:
            continue

        attempted += 1

        # Register names are "LASTNAME FIRSTNAME [MIDDLE]" (no comma)
        # Probate names may be "LAST, FIRST" — handle both
        if "," in debtor_name:
            last_name = debtor_name.split(",")[0].strip()
        else:
            last_name = debtor_name.split()[0].strip()

        if len(last_name) < 3:
            continue

        try:
            candidates = lookup_by_owner(last_name + " ", max_results=20)
        except Exception as exc:
            if verbose:
                print(f"  [Reg Name] lookup error for {last_name!r}: {exc}", flush=True)
            continue

        if not candidates:
            continue

        # If too many results for last name alone, retry with last + first name.
        # ArcGIS OWNER format is "LASTNAME FIRSTNAME", so "DAVIS SAMUEL" is specific.
        if len(candidates) > 10:
            name_parts = debtor_name.replace(",", "").split()
            if len(name_parts) >= 2:
                full_query = f"{name_parts[0]} {name_parts[1]}"
                try:
                    candidates = lookup_by_owner(full_query, max_results=10)
                except Exception:
                    candidates = []
            if not candidates or len(candidates) > 10:
                continue

        # Score by meaningful token overlap
        name_tokens = {
            t for t in debtor_name.upper().replace(",", "").replace(".", "").split()
            if len(t) > 1
        }
        best, best_score = None, 0
        for cand in candidates:
            owner_tokens = {
                t for t in (cand.get("OWNER") or "").upper().replace(",", "").replace(".", "").split()
                if len(t) > 1
            }
            score = len(name_tokens & owner_tokens)
            if score > best_score:
                best_score, best = score, cand

        if best_score >= 1 and best:
            parcel_id = (best.get("PARCELID") or "").strip()
            if parcel_id:
                event["property_refs"]["parcel_id"] = parcel_id
                resolver._cache[parcel_id] = best
                enriched += 1
                if verbose:
                    print(
                        f"  [Reg Name] {debtor_name!r} -> {parcel_id} "
                        f"{best.get('PAR_ADDR1','')} (score={best_score})",
                        flush=True,
                    )

    print(f"  [Reg Name] {enriched}/{attempted} individual debtors matched to parcels", flush=True)


def _enrich_lien_by_hoa_subdivision(
    register_events: list[dict],
    resolver: "ParcelResolver",
    verbose: bool,
) -> None:
    """
    For judgment lien events where the creditor is an HOA/condo association,
    query the ArcGIS parcel layer by subdivision name (SUBDIV field) and match
    the debtor to a parcel by name token overlap.

    HOA liens only exist on property inside that HOA — far more precise than a
    county-wide owner name search.  Works even when the county-wide name search
    fails because the debtor's last name is too common or slightly mis-spelled.
    """
    from scrapers.parcel_master_shelby import _query as _arcgis_query

    _HOA_SUFFIX_RE = re.compile(
        r"\s+(HOMEOWNERS?\s+ASSOC(?:IATION)?S?|HOMEOWNERS?|ASSOC(?:IATION)?S?|"
        r"HOA|CONDOMINUIMS?|CONDOMINIUM?S?|SUBDIVISION|"
        r"INC(?:ORPORATED)?|LLC|LLP|LP|ASSN|OWNERS\s+ASSOC(?:IATION)?)\b.*$",
        re.IGNORECASE,
    )

    def _extract_subdiv_fragment(grantee: str) -> Optional[str]:
        upper = grantee.upper()
        if not any(kw in upper for kw in ("HOMEOWNER", "ASSOCIATION", "HOA", "CONDO", "SUBDIVISION")):
            return None
        fragment = _HOA_SUFFIX_RE.sub("", grantee.strip()).strip()
        if len(fragment) < 4:
            return None
        return " ".join(fragment.split()[:3]).upper()

    subdiv_cache: dict[str, list[dict]] = {}

    def _get_subdiv_parcels(fragment: str) -> list[dict]:
        if fragment in subdiv_cache:
            return subdiv_cache[fragment]
        safe = fragment.replace("'", "''")
        parcels: list[dict] = []
        offset = 0
        try:
            while True:
                batch = _arcgis_query(f"UPPER(SUBDIV) LIKE '%{safe}%'", count=100, offset=offset)
                if not batch:
                    break
                parcels.extend(batch)
                if len(batch) < 100:
                    break
                offset += len(batch)
        except Exception as exc:
            if verbose:
                print(f"  [HOA Subdiv] lookup error for {fragment!r}: {exc}", flush=True)
            subdiv_cache[fragment] = []
            return []
        subdiv_cache[fragment] = parcels
        return parcels

    enriched = 0
    for event in register_events:
        if event.get("canonical_doc_type") != "judgment_lien":
            continue
        if (event.get("property_refs") or {}).get("parcel_id"):
            continue

        debtor_name = next(
            (p["name"].strip() for p in event.get("parties", []) if p.get("name_type") == "GR"),
            None,
        )
        if not debtor_name:
            continue

        creditor_name = next(
            (p["name"].strip() for p in event.get("parties", []) if p.get("name_type") == "GE"),
            None,
        )
        if not creditor_name:
            continue

        subdiv_frag = _extract_subdiv_fragment(creditor_name)
        if not subdiv_frag:
            continue

        parcels = _get_subdiv_parcels(subdiv_frag)
        if not parcels:
            if verbose:
                print(f"  [HOA Subdiv] {subdiv_frag!r}: no parcels found in ArcGIS", flush=True)
            continue

        name_tokens = {
            t for t in debtor_name.upper().replace(",", "").replace(".", "").split()
            if len(t) > 1
        }
        best, best_score = None, 0
        for cand in parcels:
            owner_tokens = {
                t for t in (cand.get("OWNER") or "").upper().replace(",", "").replace(".", "").split()
                if len(t) > 1
            }
            score = len(name_tokens & owner_tokens)
            if score > best_score:
                best_score, best = score, cand

        if best_score >= 2 and best:
            parcel_id = (best.get("PARCELID") or "").strip()
            if parcel_id:
                event["property_refs"]["parcel_id"] = parcel_id
                resolver._cache[parcel_id] = best
                enriched += 1
                if verbose:
                    print(
                        f"  [HOA Subdiv] {debtor_name!r} in {subdiv_frag!r} -> "
                        f"{parcel_id} {best.get('PAR_ADDR1', '')} (score={best_score})",
                        flush=True,
                    )

    print(f"  [HOA Subdiv] {enriched} HOA lien debtors matched via subdivision", flush=True)


def _fetch_appt_td_addresses(
    register_events: list[dict],
    verbose: bool,
) -> int:
    """
    For APPT raw events that have no property address but have a cross_reference
    to a TD (Trust Deed) or MTG (Mortgage) instrument, fetch the original
    instrument from the Register portal to get the property address.

    Sets legal_description (and situs_address) on matching events so that
    _enrich_events_by_address can then do an ArcGIS parcel lookup.

    Returns the number of events that received an address.
    """
    try:
        from scrapers.register_shelby import batch_lookup_td_addresses
    except ImportError as exc:
        print(f"  [APPT TD] register_shelby import failed: {exc}", flush=True)
        return 0

    _XREF_RE = re.compile(r"^(\d{5,12})-(?:TD|MTG|MOD)$", re.IGNORECASE)

    # Collect events that need TD lookup
    needs_lookup: list[tuple[dict, str]] = []  # (event, inst_num)
    for event in register_events:
        if event.get("canonical_doc_type") != "appointment_of_substitute_trustee":
            continue
        refs = event.get("property_refs") or {}
        if refs.get("legal_description") or refs.get("parcel_id"):
            continue  # already enriched

        xref = (event.get("cross_references") or "").strip()
        m = _XREF_RE.match(xref)
        if not m:
            continue
        needs_lookup.append((event, m.group(1)))

    if not needs_lookup:
        return 0

    inst_nums = list({inst for _, inst in needs_lookup})
    print(
        f"  [APPT TD] Looking up {len(inst_nums)} trust deed / mortgage instruments "
        f"for {len(needs_lookup)} APPT events...",
        flush=True,
    )

    cache_path = REPO_ROOT / "data" / "cache" / "register_td_cache_shelby.json"
    td_addresses = batch_lookup_td_addresses(
        inst_nums,
        headless=True,
        verbose=verbose,
        cache_path=cache_path,
    )

    enriched = 0
    for event, inst_num in needs_lookup:
        addr = td_addresses.get(inst_num, "").strip()
        if not addr:
            continue
        refs = event.setdefault("property_refs", {})
        refs["legal_description"] = addr
        refs["situs_address"] = addr
        enriched += 1

    print(f"  [APPT TD] {enriched}/{len(needs_lookup)} APPT events received TD address", flush=True)
    return enriched


def _make_enrichment_provider(
    parcel_by_id: dict[str, dict],
    resolver_cache: Optional[dict] = None,
):
    """Return an EnrichmentProvider callable: parcel_id -> parcel dict | None.

    parcel_by_id stores normalized parcel dicts (from resolve_parcel_dict).
    resolver._cache stores raw ArcGIS attribute dicts — must normalize before
    returning so the scoring seam gets the expected field names.
    """
    from scrapers.parcel_master_shelby import normalize_parcel_attrs

    def enrichment_provider(parcel_id: Optional[str]) -> Optional[dict]:
        if not parcel_id:
            return None
        key = str(parcel_id).strip()
        if key in parcel_by_id:
            return parcel_by_id[key]
        raw = (resolver_cache or {}).get(key)
        if raw:
            return normalize_parcel_attrs(raw)
        return None
    return enrichment_provider


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    max_records: Optional[int] = None,
    scrape: bool = False,
    verbose: bool = True,
    approve_needs_review: bool = True,
) -> dict:
    t0 = time.time()
    run_ts = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"Shelby County, TN — Pipeline  ({run_ts})")
    print(f"{'='*60}")

    # --- Optional: re-scrape CSV -------------------------------------------
    if scrape:
        print("\n[SCRAPE] Downloading fresh TaxSaleExtract.csv...")
        from scrapers.trustee_tax_sale_shelby import run_scraper  # noqa: E402
        result = run_scraper(TAX_SALE_JSONL, existing_path=TAX_SALE_JSONL if TAX_SALE_JSONL.exists() else None)
        print(f"  {result}")
    elif not TAX_SALE_JSONL.exists():
        print(f"\n[WARNING] {TAX_SALE_JSONL} not found — skipping tax sale source. Run with --scrape to download.")

    # --- Load tax sale records -------------------------------------------
    if TAX_SALE_JSONL.exists():
        print(f"\n[LOAD] Tax sale records from {TAX_SALE_JSONL.name}...")
        tax_sale_records = load_tax_sale_jsonl(TAX_SALE_JSONL, max_records=max_records)
        print(f"  Loaded {len(tax_sale_records)} active records")
    else:
        tax_sale_records = []

    # --- Parcel resolver ---------------------------------------------------
    print(f"\n[ENRICH] Initialising ArcGIS parcel resolver...")
    resolver = ParcelResolver(cache_path=PARCEL_CACHE_PATH)

    # --- Build raw events from all sources ---------------------------------
    all_raw_events: list[dict] = []

    if tax_sale_records:
        print(f"\n[ADAPT] Tax sale: {len(tax_sale_records)} records...")
        tax_raw_events, parcel_by_id = build_tax_sale_raw_events(
            tax_sale_records,
            resolver=resolver,
            verbose=verbose,
        )
        all_raw_events.extend(tax_raw_events)
        print(f"  Built {len(tax_raw_events)} raw events")
    else:
        parcel_by_id = {}

    # Phase 2 — Register of Deeds
    register_records = load_register_jsonl(REGISTER_JSONL, max_records=max_records)
    if register_records:
        print(f"\n[ADAPT] Register of Deeds: {len(register_records)} records...")
        reg_events = build_register_raw_events(register_records, verbose=verbose)
        print(f"  Built {len(reg_events)} raw events")
        print(f"  Fetching TD/MTG addresses for APPT events via Register portal...")
        _fetch_appt_td_addresses(reg_events, verbose=verbose)
        print(f"  Enriching register events by address -> parcel lookup...")
        _enrich_events_by_address(reg_events, resolver, verbose=verbose)
        print(f"  Enriching tax/lien events by debtor name -> parcel lookup...")
        _enrich_register_by_owner_name(reg_events, resolver, verbose=verbose)
        print(f"  Enriching HOA lien events by subdivision -> parcel lookup...")
        _enrich_lien_by_hoa_subdivision(reg_events, resolver, verbose=verbose)
        all_raw_events.extend(reg_events)

    # Phase 2 — General Sessions Civil (evictions)
    eviction_records = load_eviction_jsonl(GENERAL_SESSIONS_JSONL, max_records=max_records)
    if eviction_records:
        print(f"\n[ADAPT] General Sessions Civil (evictions): {len(eviction_records)} records...")
        eviction_events = build_eviction_raw_events(eviction_records, verbose=verbose)
        print(f"  Built {len(eviction_events)} raw events")
        print(f"  Enriching eviction events by address -> parcel lookup...")
        _enrich_events_by_address(eviction_events, resolver, verbose=verbose)
        all_raw_events.extend(eviction_events)

    # Phase 2 — Chancery Court
    chancery_records = load_chancery_jsonl(CHANCERY_JSONL, max_records=max_records)
    if chancery_records:
        print(f"\n[ADAPT] Chancery Court: {len(chancery_records)} records...")
        chancery_events = build_chancery_raw_events(chancery_records, verbose=verbose)
        all_raw_events.extend(chancery_events)
        print(f"  Built {len(chancery_events)} raw events")

    # Phase 2 — Probate Court
    probate_events: list[dict] = []
    probate_name_map: dict = {}
    probate_match_status: dict[str, str] = {}
    probate_records = load_probate_jsonl(PROBATE_JSONL, max_records=max_records)
    if probate_records:
        print(f"\n[ADAPT] Probate Court: {len(probate_records)} records...")
        probate_events = build_probate_raw_events(probate_records, verbose=verbose)
        print(f"  Built {len(probate_events)} raw events")
        print(f"  Enriching probate events by decedent name -> parcel lookup...")
        probate_match_status = _enrich_probate_by_name(probate_events, resolver, verbose=verbose)
        probate_name_map = _build_probate_name_map(probate_events)
        all_raw_events.extend(probate_events)

    if not all_raw_events:
        print("  No raw events produced — check scraper output.")
        return {"lead_count": 0, "elapsed_seconds": round(time.time() - t0, 1)}

    # Save parcel cache
    resolver.save_cache()
    resolver.print_stats()

    # --- Build enrichment provider (callable: parcel_id -> parcel dict) ----
    enrichment_provider = _make_enrichment_provider(parcel_by_id, resolver._cache)

    # Collect posting addresses before the pipeline strips property_refs.
    # Unresolved lead_id = "lead_unresolved_{identity}" where identity is
    # instrument_number (for Register events) or raw_event_id (for other sources).
    # We key by both so the lookup works regardless of which identity was used.
    _addr_by_event_id: dict[str, str] = {}
    for _ev in all_raw_events:
        _refs = _ev.get("property_refs") or {}
        _addr = (_refs.get("situs_address") or _refs.get("legal_description") or "").strip()
        if _addr:
            _addr_by_event_id[_ev["raw_event_id"]] = _addr
            _instr = (_ev.get("instrument_number") or "").strip()
            if _instr:
                _addr_by_event_id[_instr] = _addr

    # --- Staged pipeline §17 -> §18 -> §19 -> §20 -> scoring ----------------
    print(f"\n[PIPELINE] Running staged pipeline on {len(all_raw_events)} raw events...")
    result = run_staged_pipeline(
        all_raw_events,
        workdir=OUTPUT_DIR,
        as_of=datetime.now(timezone.utc).date(),
        enrichment_provider=enrichment_provider,
        approve_needs_review=approve_needs_review,
        debtor_party_rules=_DEBTOR_RULES,
    )

    scored_leads = result["scored_leads"]
    print(f"\n[RESULT] {len(scored_leads)} scored leads written to {result['scored_leads_path']}")
    print(f"  Semantic verdict: {result['semantic_verdict']}")

    # --- Post-pipeline: TruePeopleSearch contact enrichment for probate ----
    if probate_events:
        tps_cache = REPO_ROOT / "data" / "cache" / "tps_shelby.json"
        _enrich_probate_contacts(
            scored_leads,
            probate_name_map,
            cache_path=tps_cache,
            verbose=verbose,
            match_status=probate_match_status,
        )

    # --- Re-write scored_leads.json to include contact_info from enrichment --
    if probate_events:
        scored_leads_path = result["scored_leads_path"]
        Path(scored_leads_path).write_text(
            json.dumps(scored_leads, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # --- Build dashboard payload ------------------------------------------
    dashboard = build_dashboard_payload(
        scored_leads,
        semantic_verdict=result["semantic_verdict"],
        county=COUNTY_NAME,
        state=STATE,
        mode="production",
        build_label="PARTIAL_BUILD",
    )

    # Patch contact_info + tps_confirmed_address onto dashboard records.
    # project_scored_lead (in the universal scaffold) doesn't know about
    # Shelby probate enrichment fields; we merge them here.
    _scored_by_id = {s["scored_lead_id"]: s for s in scored_leads}
    for rec in dashboard.get("records", []):
        _s = _scored_by_id.get(rec.get("scored_lead_id"))
        if not _s:
            continue
        if _s.get("contact_info"):
            rec["contact_info"] = _s["contact_info"]
        if _s.get("tps_confirmed_address"):
            rec["tps_confirmed_address"] = _s["tps_confirmed_address"]
        if _s.get("tps_confirmed_parcel_id"):
            rec["tps_confirmed_parcel_id"] = _s["tps_confirmed_parcel_id"]

    # Add primary_source_urls to each record (TPS detail URL for probate leads)
    for rec in dashboard.get("records", []):
        _s = _scored_by_id.get(rec.get("scored_lead_id"))
        if _s and _s.get("source_urls") and "primary_source_urls" not in rec:
            rec["primary_source_urls"] = _s["source_urls"]

    # Patch display_address from the original posting when parcel enrichment missed.
    # Unresolved leads use lead_id = "lead_unresolved_{raw_event_id}" so we can
    # recover the event's posting address without touching the scaffold.
    _UNRESOLVED_PREFIX = "lead_unresolved_"
    for rec in dashboard.get("records", []):
        if rec.get("display_address"):
            continue
        _s = _scored_by_id.get(rec.get("scored_lead_id"))
        if not _s:
            continue
        lead_id = _s.get("lead_id", "")
        if lead_id.startswith(_UNRESOLVED_PREFIX):
            raw_ev_id = lead_id[len(_UNRESOLVED_PREFIX):]
            raw_addr = _addr_by_event_id.get(raw_ev_id, "")
            if raw_addr:
                # Strip state/zip suffix to keep it clean for skip-tracing
                # "1446 HARRISON ST Memphis TN 38108" -> keep as-is (full address)
                rec["display_address"] = raw_addr
                rec["display_address_source"] = "posting"  # distinguish from ArcGIS

    dashboard_path = OUTPUT_DIR / "dashboard.json"
    dashboard_path.write_text(
        json.dumps(dashboard, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"  Dashboard payload: {dashboard_path}")

    # Mirror to the dashboard server's live data file so the browser reloads immediately
    import shutil as _shutil
    _live_dash = REPO_ROOT / "dashboard" / "data" / "leads.json"
    _live_dash.parent.mkdir(parents=True, exist_ok=True)
    _shutil.copy(dashboard_path, _live_dash)
    print(f"  Live dashboard updated: {_live_dash}")

    # ------------------------------------------------------------------
    # Publish to the isolated client-facing repo (shelbytn.justfriday.ai)
    #
    # dashboard/data/leads.json above is a SHARED path other counties'
    # pipelines also write to — whichever ran most recently wins there, so
    # it's not a reliable per-client URL. This repo is isolated (own repo,
    # own domain) so a Shelby client's browser has no path to another
    # county's data. Not fatal if the sibling checkout doesn't exist on a
    # given machine.
    # ------------------------------------------------------------------
    _client_repo_dir = REPO_ROOT.parent / "shelby-tn-leads"
    if _client_repo_dir.is_dir():
        try:
            import subprocess as _subprocess
            _client_data_path = _client_repo_dir / "data" / "leads.json"
            _client_data_path.parent.mkdir(parents=True, exist_ok=True)
            _client_data_path.write_text(
                dashboard_path.read_text(encoding="utf-8"), encoding="utf-8"
            )
            _subprocess.run(
                ["git", "add", "data/leads.json"],
                cwd=str(_client_repo_dir), check=True, capture_output=True,
            )
            _subprocess.run(
                ["git", "commit", "-m",
                 f"data: dashboard update {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"],
                cwd=str(_client_repo_dir), check=True, capture_output=True,
            )
            _subprocess.run(
                ["git", "push", "origin", "main"],
                cwd=str(_client_repo_dir), check=True, capture_output=True, timeout=60,
            )
            print("  Client dashboard updated → https://shelbytn.justfriday.ai/")
        except _subprocess.CalledProcessError as exc:
            _stderr = exc.stderr.decode(errors="replace") if exc.stderr else ""
            if "nothing to commit" in _stderr or "nothing to commit" in (exc.stdout or b"").decode(errors="replace"):
                print("  Client dashboard: no changes to publish")
            else:
                print(f"  Client dashboard publish failed (non-fatal): {_stderr[:200]}")
    else:
        print(
            f"  Client dashboard repo not found at {_client_repo_dir} — "
            "skipping (not fatal; only affects this machine)"
        )

    elapsed = round(time.time() - t0, 1)
    print(f"\n[DONE] Shelby County pipeline complete in {elapsed}s")
    print(f"  Leads: {len(scored_leads)}  |  Verdict: {result['semantic_verdict']}")

    return {
        "lead_count": len(scored_leads),
        "semantic_verdict": result["semantic_verdict"],
        "elapsed_seconds": elapsed,
        "scored_leads_path": str(result["scored_leads_path"]),
        "dashboard_path": str(dashboard_path),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Shelby County, TN — distress lead pipeline")
    parser.add_argument("--max-records", type=int, default=None,
                        help="Cap records per source (for bounded tests)")
    parser.add_argument("--scrape", action="store_true",
                        help="Re-download CSV from source before running pipeline")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress per-record verbose output")
    parser.add_argument("--no-approve-review", action="store_true",
                        help="Do not auto-approve REVIEW_REQUIRED leads")
    args = parser.parse_args()

    run_pipeline(
        max_records=args.max_records,
        scrape=args.scrape,
        verbose=not args.quiet,
        approve_needs_review=not args.no_approve_review,
    )
