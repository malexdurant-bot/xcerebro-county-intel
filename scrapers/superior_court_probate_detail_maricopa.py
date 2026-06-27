"""
Maricopa County Superior Court Probate — detail-page parser and enrichment fetcher.

Fetches caseInfo.asp for each record in data/raw/superior_court_probate.jsonl
and writes enriched records to data/raw/superior_court_probate_detail.jsonl.

Portal page structure (confirmed 2026-06-27 from live caseInfo.asp probe):
  #tblForms   — Case Information (Case Number, Case Type, Plaintiff, Judge, Location)
  #tblForms2  — Party Information (container)
    #tblForms3 (repeated) — one div per party: Name / Relationship / Sex / Attorney
  #tblForms4  — Case Documents (Filing Date / Description / Docket Date / Filing Party)
  #tblForms5  — (additional section if present)

All sections use Bootstrap .row > .col-4.bold-font (label) + .col-8 (value) layout.
No <table>/<th>/<td> elements are used.

Detail pages are accessible via urllib GET (no reCAPTCHA on direct caseInfo.asp GETs).

=== What this fetcher extracts ===

From #tblForms:
  case_number      — canonical case ID
  case_type_raw    — portal label: "Probate", "Guardianship", "Conservatorship", etc.
  judge            — assigned judge
  location         — court location (Downtown, Northeast, etc.)
  plaintiff_raw    — primary party shown in case info header (may be redacted)

From #tblForms2/#tblForms3 (party rows):
  parties          — list of {name, relationship, sex, attorney}
  decedent_name    — party with Relationship == "Decedent" (estate cases only)
  petitioner_name  — party with Relationship == "Petitioner"
  personal_representative — party with Relationship in (Personal Representative, Executor, Administrator)

From #tblForms4 (documents):
  earliest_filing_date   — min date across all document Filing Date values (case open date)
  document_descriptions  — all document Description strings (used to infer case subtype)
  case_subtype_inferred  — letters_testamentary / letters_of_administration /
                           determination_of_heirship / muniment_of_title

=== Not available from this page ===
  property_address — not present; only on filed documents (image review required)
  APN / parcel_id  — not present on this page

=== Usage ===
    python scrapers/superior_court_probate_detail_maricopa.py \\
        --max-records 18
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SOURCE_ID = "superior_court_probate_detail"
BASE_JSONL = REPO_ROOT / "data" / "raw" / "superior_court_probate.jsonl"
DETAIL_JSONL = REPO_ROOT / "data" / "raw" / "superior_court_probate_detail.jsonl"

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.superiorcourt.maricopa.gov/docket/ProbateCourtCases/caseSearch.asp",
}

# Relationship values → role classification
_DECEDENT_RELS = frozenset(["decedent", "deceased"])
_PR_RELS = frozenset([
    "personal representative", "executor", "executrix",
    "administrator", "administratrix", "co-personal representative",
    "co-executor", "co-administrator",
])
_PETITIONER_RELS = frozenset(["petitioner", "co-petitioner"])
_NOISE_CASE_TYPES = frozenset([
    "guardianship", "conservatorship", "mental health",
    "guardianship/conservatorship", "civil commitment",
])

# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def _strip_html(s: str) -> str:
    """Remove HTML tags, decode common entities, collapse whitespace."""
    s = re.sub(r"<[^>]+>", " ", s)
    for ent, ch in [
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
        ("&gt;", ">"), ("&#39;", "'"), ("&quot;", '"'),
    ]:
        s = s.replace(ent, ch)
    return " ".join(s.split())


def _find_div_by_id(html: str, div_id: str) -> str:
    """Return the HTML of the outermost <div id="{div_id}"> block."""
    open_re = re.compile(
        rf'<div\b[^>]*\bid="{re.escape(div_id)}"[^>]*>', re.IGNORECASE
    )
    m = open_re.search(html)
    if not m:
        return ""
    start = m.start()
    depth = 0
    tag_re = re.compile(r"<(/?)div\b", re.IGNORECASE)
    for tm in tag_re.finditer(html, start):
        depth += -1 if tm.group(1) else 1
        if depth == 0:
            return html[start: tm.end() + 1]
    return html[start:]


def _bold_value_pairs(section_html: str) -> list[tuple[str, str]]:
    """Extract (label, value) pairs from .bold-font + next .col-8 sibling divs."""
    pat = re.compile(
        r'class="[^"]*bold-font[^"]*"[^>]*>(.*?)</div>\s*<div[^>]*>(.*?)</div>',
        re.DOTALL | re.IGNORECASE,
    )
    pairs: list[tuple[str, str]] = []
    for m in pat.finditer(section_html):
        label = _strip_html(m.group(1)).rstrip(":").strip()
        value = _strip_html(m.group(2)).strip()
        if label:
            pairs.append((label, value))
    return pairs


def _find_rows_by_class(container_html: str, class_fragment: str) -> list[str]:
    """Return full HTML of each <div> whose class contains class_fragment, depth-counting."""
    row_re = re.compile(
        rf'<div\b[^>]*class="[^"]*{re.escape(class_fragment)}[^"]*"[^>]*>',
        re.IGNORECASE,
    )
    tag_re = re.compile(r"<(/?)div\b", re.IGNORECASE)
    rows: list[str] = []
    for m in row_re.finditer(container_html):
        start = m.start()
        depth = 0
        for tm in tag_re.finditer(container_html, start):
            depth += -1 if tm.group(1) else 1
            if depth == 0:
                rows.append(container_html[start: tm.end() + 1])
                break
    return rows


def _find_party_rows(tblforms2_html: str) -> list[str]:
    """Return HTML of each pt-3.pb-3 party row inside #tblForms2."""
    return _find_rows_by_class(tblforms2_html, "pt-3")


def _parse_date(date_str: str) -> Optional[str]:
    """Convert M/D/YYYY to ISO YYYY-MM-DD. Returns None on failure."""
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _infer_subtype(descriptions: list[str]) -> str:
    """Infer canonical probate doc type from document description strings."""
    combined = " ".join(descriptions).lower()
    if "testamentary" in combined or "letters test" in combined:
        return "letters_testamentary"
    if "letters of admin" in combined or "administration" in combined:
        return "letters_of_administration"
    if "heirship" in combined or "determination of heir" in combined:
        return "determination_of_heirship"
    if "muniment" in combined:
        return "muniment_of_title"
    return "letters_testamentary"  # default when specific type undetermined


# ---------------------------------------------------------------------------
# Main parser (pure function — accepts HTML string)
# ---------------------------------------------------------------------------


def parse_probate_detail_html(html: str) -> dict:
    """Parse a probate caseInfo.asp page. Returns a detail dict.

    Keys:
      case_number, case_type_raw, judge, location, plaintiff_raw,
      parties (list of {name, relationship, sex, attorney}),
      decedent_name, petitioner_name, personal_representative,
      earliest_filing_date, document_descriptions,
      case_subtype_inferred, is_estate_case, is_noise_case,
      property_address (always None), apn (always None),
      parse_errors (list of str)
    """
    errors: list[str] = []

    # ---- Section 1: Case Information ----
    case_info_html = _find_div_by_id(html, "tblForms")
    lv = dict(_bold_value_pairs(case_info_html))

    case_number = lv.get("Case Number") or lv.get("case number") or None
    case_type_raw = lv.get("Case Type") or lv.get("case type") or None
    judge = lv.get("Judge") or None
    location = lv.get("Location") or None
    plaintiff_raw = lv.get("Plaintiff") or None

    if not case_info_html:
        errors.append("tblForms (Case Information) not found")

    # ---- Section 2: Party Information ----
    tblforms2_html = _find_div_by_id(html, "tblForms2")
    party_rows = _find_party_rows(tblforms2_html)

    parties: list[dict] = []
    decedent_name: Optional[str] = None
    petitioner_name: Optional[str] = None
    personal_representative: Optional[str] = None

    for row_html in party_rows:
        row_lv = dict(_bold_value_pairs(row_html))
        name = row_lv.get("Party Name") or ""
        relationship = row_lv.get("Relationship") or ""
        sex = row_lv.get("Sex") or ""
        attorney = row_lv.get("Attorney") or ""

        # Strip court privacy placeholders — do not emit as real parties
        name_clean = name.strip()
        is_protected = (
            "minor" in name_clean.lower() and "name not published" in name_clean.lower()
        ) or "information is protected" in name_clean.lower()

        if is_protected:
            name_clean = ""

        rel_lower = relationship.strip().lower()

        if name_clean:
            parties.append({
                "name": name_clean,
                "relationship": relationship.strip(),
                "sex": sex.strip(),
                "attorney": attorney.strip(),
            })

        if name_clean and rel_lower in _DECEDENT_RELS:
            decedent_name = name_clean
        elif name_clean and rel_lower in _PETITIONER_RELS and not petitioner_name:
            petitioner_name = name_clean
        elif name_clean and rel_lower in _PR_RELS and not personal_representative:
            personal_representative = name_clean

    if not tblforms2_html:
        errors.append("tblForms2 (Party Information) not found")

    # ---- Section 3: Case Documents ----
    tblforms4_html = _find_div_by_id(html, "tblForms4")
    document_descriptions: list[str] = []
    raw_filing_dates: list[str] = []

    if tblforms4_html:
        for row_html in _find_rows_by_class(tblforms4_html, "row g-0"):
            row_lv = dict(_bold_value_pairs(row_html))
            fd = row_lv.get("Filing Date") or ""
            desc = row_lv.get("Description") or ""
            if fd:
                raw_filing_dates.append(fd)
            if desc:
                document_descriptions.append(desc)
    else:
        errors.append("tblForms4 (Case Documents) not found")

    # Earliest filing date (ISO) — case open date
    iso_dates: list[str] = [d for d in (_parse_date(s) for s in raw_filing_dates) if d]
    earliest_filing_date = min(iso_dates) if iso_dates else None

    # ---- Derived fields ----
    case_subtype_inferred = _infer_subtype(document_descriptions)
    is_noise_case = (case_type_raw or "").strip().lower() in _NOISE_CASE_TYPES
    is_estate_case = bool(decedent_name) and not is_noise_case

    return {
        "case_number": case_number,
        "case_type_raw": case_type_raw,
        "judge": judge,
        "location": location,
        "plaintiff_raw": plaintiff_raw,
        "parties": parties,
        "decedent_name": decedent_name,
        "petitioner_name": petitioner_name,
        "personal_representative": personal_representative,
        "earliest_filing_date": earliest_filing_date,
        "document_descriptions": document_descriptions,
        "case_subtype_inferred": case_subtype_inferred,
        "is_estate_case": is_estate_case,
        "is_noise_case": is_noise_case,
        "property_address": None,
        "apn": None,
        "parse_errors": errors,
    }


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------


def fetch_detail_html(url: str) -> str:
    """Fetch a caseInfo.asp page via urllib GET. Raises on HTTP error."""
    req = urllib.request.Request(url, headers=_HEADERS)
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_SSL_CTX))
    with opener.open(req, timeout=30) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


# ---------------------------------------------------------------------------
# End-to-end runner
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_base_jsonl(path: Path, max_records: int) -> list[dict]:
    records: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("change_status") == "DISAPPEARED":
                continue
            records.append(rec)
            if len(records) >= max_records:
                break
    return records


def _load_prior_detail(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = rec.get("raw_record_id")
            if rid:
                out[rid] = rec
    return out


def run(
    base_jsonl: Path = BASE_JSONL,
    output_path: Path = DETAIL_JSONL,
    max_records: int = 50,
    delay_seconds: float = 1.0,
) -> dict:
    """Fetch detail pages for up to max_records base records. Returns stats."""
    if not base_jsonl.exists():
        raise FileNotFoundError(f"Base JSONL not found: {base_jsonl}")

    base_records = _load_base_jsonl(base_jsonl, max_records)
    prior_detail = _load_prior_detail(output_path)

    stats = {
        "records_attempted": 0,
        "detail_pages_loaded": 0,
        "fetch_errors": 0,
        "filing_date_found": 0,
        "case_type_found": 0,
        "decedent_confirmed": 0,
        "petitioner_found": 0,
        "pr_found": 0,
        "property_address_found": 0,
        "apn_found": 0,
        "estate_cases": 0,
        "noise_cases": 0,
        "review_required": 0,
        "errors": [],
    }

    detail_records: list[dict] = list(prior_detail.values())
    updated_ids: set = set()

    for base_rec in base_records:
        rid = base_rec.get("raw_record_id") or ""
        payload = base_rec.get("raw_payload") or {}
        url = payload.get("case_detail_url") or base_rec.get("source_url") or ""

        if not url:
            stats["errors"].append(f"{rid}: no case_detail_url")
            continue

        stats["records_attempted"] += 1

        # Skip if already fetched
        if rid in prior_detail:
            existing = prior_detail[rid]
            updated_ids.add(rid)
            _tally_stats(existing.get("detail") or {}, stats)
            print(f"  [{stats['records_attempted']}] cached — skip fetch")
            continue

        try:
            html = fetch_detail_html(url)
            stats["detail_pages_loaded"] += 1
        except Exception as exc:
            stats["fetch_errors"] += 1
            stats["errors"].append(f"{rid}: fetch error: {exc}")
            print(f"  [{stats['records_attempted']}] FETCH ERROR (URL redacted)")
            continue

        detail = parse_probate_detail_html(html)
        _tally_stats(detail, stats)

        out_rec = {
            "raw_record_id": rid,
            "source_id": SOURCE_ID,
            "detail_fetched_at": _now_iso(),
            "detail": detail,
        }
        detail_records.append(out_rec)
        updated_ids.add(rid)

        # Brief status (no PII in output)
        has_decedent = "yes" if detail["decedent_name"] else "no"
        has_pr = "yes" if detail["personal_representative"] else "no"
        has_date = "yes" if detail["earliest_filing_date"] else "no"
        noise = "NOISE" if detail["is_noise_case"] else ""
        print(
            f"  [{stats['records_attempted']}] "
            f"case_type={detail['case_type_raw']!r}  "
            f"decedent={has_decedent}  pr={has_pr}  "
            f"filing_date={has_date}  "
            f"subtype={detail['case_subtype_inferred']!r}  {noise}"
        )

        if delay_seconds > 0:
            time.sleep(delay_seconds)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for rec in detail_records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(output_path)

    stats["output_path"] = str(output_path)
    return stats


def _tally_stats(detail: dict, stats: dict) -> None:
    if detail.get("earliest_filing_date"):
        stats["filing_date_found"] += 1
    if detail.get("case_type_raw"):
        stats["case_type_found"] += 1
    if detail.get("decedent_name"):
        stats["decedent_confirmed"] += 1
    if detail.get("petitioner_name"):
        stats["petitioner_found"] += 1
    if detail.get("personal_representative"):
        stats["pr_found"] += 1
    if detail.get("property_address"):
        stats["property_address_found"] += 1
    if detail.get("apn"):
        stats["apn_found"] += 1
    if detail.get("is_estate_case"):
        stats["estate_cases"] += 1
    elif detail.get("is_noise_case"):
        stats["noise_cases"] += 1
    else:
        stats["review_required"] += 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-records", type=int, default=18, metavar="N",
                        help="Max base records to enrich (default: 18)")
    parser.add_argument("--delay", type=float, default=1.0, metavar="SECS",
                        help="Seconds between requests (default: 1.0)")
    parser.add_argument("--base-jsonl", default=None,
                        help="Override base JSONL path")
    parser.add_argument("--out", default=None,
                        help="Override output JSONL path")
    args = parser.parse_args()

    base = Path(args.base_jsonl) if args.base_jsonl else BASE_JSONL
    out = Path(args.out) if args.out else DETAIL_JSONL

    print(f"=== Probate detail enrichment: max_records={args.max_records} ===")
    print(f"Base JSONL:   {base.name}")
    print(f"Detail JSONL: {out}")
    print()

    stats = run(base_jsonl=base, output_path=out, max_records=args.max_records, delay_seconds=args.delay)

    print()
    print("=== Aggregate stats (no PII) ===")
    print(f"  records_attempted:      {stats['records_attempted']}")
    print(f"  detail_pages_loaded:    {stats['detail_pages_loaded']}")
    print(f"  fetch_errors:           {stats['fetch_errors']}")
    print(f"  filing_date_found:      {stats['filing_date_found']}")
    print(f"  case_type_found:        {stats['case_type_found']}")
    print(f"  decedent_confirmed:     {stats['decedent_confirmed']}")
    print(f"  petitioner_found:       {stats['petitioner_found']}")
    print(f"  pr_found:               {stats['pr_found']}")
    print(f"  property_address_found: {stats['property_address_found']}")
    print(f"  apn_found:              {stats['apn_found']}")
    print(f"  estate_cases:           {stats['estate_cases']}")
    print(f"  noise_cases:            {stats['noise_cases']}")
    print(f"  review_required:        {stats['review_required']}")
    if stats["errors"]:
        print(f"  errors ({len(stats['errors'])}):")
        for e in stats["errors"][:10]:
            print(f"    {e}")
    print(f"  output: {stats.get('output_path', out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
