"""
Clayton County, GA — Court Case Inquiry (Magistrate + State Court civil).

Sources (config/counties/clayton_ga.json):
  - magistrate_court_civil_evictions  (court M, case type CM)
        dispossessory ("Patho" / "Dispossessory") filings -> eviction_filing
  - state_court_civil_judgments       (court L, case type CV)
        cases whose disposition is a judgment            -> judgment_lien

Portal: https://weba.claytoncountyga.gov/casinqsvr/htdocs/index.shtml (linked
from clerk.claytoncountyga.gov/case-search/). Server-rendered IBM i CGI, no
login, no CAPTCHA. Detail lookup is a plain GET:

    /casinqcgi-bin/wci010r.pgm?rtype=E&dvt=V&opt=&ctt=<court>&cyr=<year>
                              &ctp=<type>&csq=<5-digit seq>&jdg=

There is no date or case-type browse, so new filings are found by walking the
sequence number forward from a stored cursor until N consecutive misses.
Recon 2026-11-12: Magistrate CM ran ~2,700 cases/month in 2026, State CV ~600.

The CGI sometimes answers with a "weekly maintenance ... check back later"
page instead of a result (seen on a Thursday, i.e. not only during the
Sunday window). That page is treated as TRANSIENT — retried with backoff, and
the run stops cleanly (cursor kept) if it persists. It is never treated as
"case exists" or "case not found".

Eviction lead subject is the LANDLORD (plaintiff, name_type PL); the rental
address comes from the tenant-defendant's service address. Institutional
landlords (apartment communities, large single-family-rental operators,
units with Apt/Unit/#) are not callable motivated sellers, so they are
skipped by default and counted — pass include_institutional=True to keep
them.

State Court judgments arrive months after filing, so new filings without a
judgment go into a `pending` set that is re-checked (oldest first, bounded
per run) until a disposition lands or the case ages out.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "clayton_case_inquiry"
RAW_DIR.mkdir(parents=True, exist_ok=True)
STATE_PATH = RAW_DIR / "state.json"

BASE = "https://weba.claytoncountyga.gov"
DETAIL_URL = BASE + "/casinqcgi-bin/wci010r.pgm"
PUBLIC_URL = BASE + "/casinqsvr/htdocs/index.shtml"

# config: rate_limit_rpm = 10  -> 6 s between requests (override with env)
DELAY_S = float(os.environ.get("CLAYTON_CASE_DELAY_S", "6"))
MISS_LIMIT = 15             # consecutive not-found before we call the frontier
PENDING_RECHECK_DAYS = 7
PENDING_MAX_AGE_DAYS = 270
PENDING_RECHECK_PER_RUN = 40

COURTS = {
    # key: (ctt, ctp, source_id, initial backfill size)
    "magistrate": ("M", "CM", "magistrate_court_civil_evictions", 150),
    "state":      ("L", "CV", "state_court_civil_judgments", 80),
}

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
})

INSTITUTIONAL_RE = re.compile(
    r"\b(APARTMENTS?|APTS|LLLP|LP|L\.P\.|RESIDENTIAL|COMMUNITIES|VILLAS|TOWNHOMES|"
    r"PROPERTY MANAGEMENT|MANAGEMENT|HOUSING AUTHORITY|INVITATION HOMES|PROGRESS RESIDENTIAL|"
    r"AMERICAN HOMES 4 RENT|AMH|FIRSTKEY|TRICON|MAIN STREET RENEWAL|HOME PARTNERS|VINEBROOK|"
    r"PRETIUM|CERBERUS|SFR|RENTAL HOMES|BORROWER|RESIDENCES|ESTATES|LANDING|POINTE|COMMONS)\b",
    re.I,
)
# "Apt 12", "Unit B", "#4", or a trailing unit token like "1507 PINE DRIVE B18"
UNIT_RE = re.compile(r"\b(APT|UNIT|STE|SUITE|BLDG|#)\s*[A-Z0-9-]+|\s[A-Z]{0,2}-?\d{1,4}[A-Z]?$", re.I)


class Transient(Exception):
    """Maintenance / throttle page — retry later, do not move the cursor past it."""


# ---------------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------------

_last = 0.0


def _get(ctt: str, year: int, ctp: str, seq: int) -> str:
    global _last
    wait = DELAY_S - (time.monotonic() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.monotonic()
    r = _SESSION.get(DETAIL_URL, params={
        "rtype": "E", "dvt": "V", "opt": "", "ctt": ctt, "cyr": str(year),
        "ctp": ctp, "csq": f"{seq:05d}", "jdg": "",
    }, timeout=45)
    r.raise_for_status()
    return r.text


def fetch_case(ctt: str, year: int, ctp: str, seq: int) -> dict | None:
    """Return parsed case dict, None when not found. Raises Transient."""
    for attempt in range(3):
        html = _get(ctt, year, ctp, seq)
        if "Case# not found" in html:
            return None
        if "maintenance" in html.lower() and "Case Summary" not in html:
            time.sleep(30 * (attempt + 1))
            continue
        return parse_case(html)
    raise Transient(f"maintenance page persisted for {year}{ctp}{seq:05d}")


def _cell_text(td) -> str:
    return re.sub(r"\s+", " ", td.get_text(" ", strip=True)).replace("\xa0", " ").strip()


def _party_lines(td) -> tuple[str | None, list[str]]:
    det = td.find("details")
    if not det:
        t = _cell_text(td)
        return (t or None), []
    name = _cell_text(det.find("summary")) if det.find("summary") else None
    summary = det.find("summary")
    if summary:
        summary.extract()
    lines = [re.sub(r"\s+", " ", x).strip() for x in det.get_text("\n").split("\n")]
    lines = [x for x in lines if x]
    if lines and name and lines[0].upper() == name.upper():
        lines = lines[1:]
    return name, lines


def parse_case(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    info: dict[str, str] = {}
    basic = soup.find("caption", string=re.compile("Basic Case Information"))
    if basic:
        for tr in basic.find_parent("table").find_all("tr"):
            cells = tr.find_all(["th", "td"])
            for i in range(0, len(cells) - 1, 2):
                k, v = _cell_text(cells[i]), _cell_text(cells[i + 1])
                if k:
                    info[k] = v
    parties, case_type = [], None
    ptab = soup.find("caption", string=re.compile("List of Parties"))
    if ptab:
        for tr in ptab.find_parent("table").find("tbody").find_all("tr", recursive=False):
            tds = tr.find_all("td", recursive=False)
            if len(tds) < 2:
                continue
            role = _cell_text(tds[1])
            name, lines = _party_lines(tds[0])
            if not role:  # the case-type pseudo-row: <summary>Patho</summary> Dispossessory
                if name:
                    case_type = (name + " " + " ".join(lines)).strip()
                continue
            addr = None
            if lines:
                addr = {"street": " ".join(lines[:-1]) if len(lines) > 1 else lines[0],
                        "city_state_zip": lines[-1] if len(lines) > 1 else None}
            parties.append({"name": name, "role": role, "address": addr})
    docket = []
    dtab = soup.find("caption", string=re.compile("Docket Activity"))
    if dtab:
        for tr in dtab.find_parent("table").find("tbody").find_all("tr"):
            c = [_cell_text(td) for td in tr.find_all("td")]
            if len(c) == 3 and any(c):
                docket.append({"date": c[0] or None, "action": c[1] or None, "description": c[2] or None})
    status = info.get("Status", "")
    disp_date = None
    m = re.search(r"(\d{2}/\d{2}/\d{4})", status)
    if m:
        disp_date = m.group(1)
    return {
        "case_number": re.sub(r"\s+", " ", info.get("Case Number", "")).strip(),
        "filing_date": info.get("Filing Date"),
        "status": status,
        "disposition": info.get("Disposition"),
        "disposition_date": disp_date,
        "case_type": case_type,
        "parties": parties,
        "docket": docket,
    }


# ---------------------------------------------------------------------------
# Classification -> raw_event_record
# ---------------------------------------------------------------------------

def _iso(d: str | None) -> str | None:
    try:
        return datetime.strptime(d, "%m/%d/%Y").date().isoformat() if d else None
    except ValueError:
        return None


def _rid(*parts) -> str:
    return "raw_cci_" + hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def is_dispossessory(case: dict) -> bool:
    t = " ".join(filter(None, [case.get("case_type")] +
                        [d.get("description") for d in case["docket"] if d.get("action") == "Case Filing"]))
    return bool(re.search(r"dispossess|patho", t, re.I))


def is_judgment(case: dict) -> bool:
    d = case.get("disposition") or ""
    return bool(re.search(r"judgment", d, re.I)) and not re.search(r"dismiss|vacat|set aside", d, re.I)


def _situs_from_defendants(case: dict) -> tuple[str | None, str | None]:
    for p in case["parties"]:
        if p["role"].lower().startswith("defendant") and p.get("address"):
            return p["address"]["street"], p["address"].get("city_state_zip")
    return None, None


def landlord_is_institutional(case: dict) -> bool:
    pls = [p["name"] or "" for p in case["parties"] if p["role"].lower().startswith("plaintiff")]
    street, _ = _situs_from_defendants(case)
    return any(INSTITUTIONAL_RE.search(n) for n in pls) or bool(street and UNIT_RE.search(street))


def event_is_institutional(e: dict) -> bool:
    """Same rule as landlord_is_institutional, applied to an archived raw event."""
    pls = [p.get("name") or "" for p in e.get("parties", []) if p.get("name_type") == "PL"]
    street = (e.get("property_refs") or {}).get("situs_address")
    return any(INSTITUTIONAL_RE.search(n) for n in pls) or bool(street and UNIT_RE.search(street))


def _parties_for_event(case: dict) -> list[dict]:
    out = []
    for p in case["parties"]:
        role = p["role"].lower()
        if not p["name"] or p["name"].upper().startswith("AND ALL OTHERS"):
            continue
        nt = "PL" if role.startswith("plaintiff") else "DF" if role.startswith("defendant") else None
        if nt:
            out.append({"name": p["name"], "name_type": nt, "raw_role": p["role"].upper()})
    return out


def case_to_event(case: dict, source_id: str, canonical: str, captured_at: str, url: str) -> dict:
    street, csz = _situs_from_defendants(case)
    event_date = _iso(case["filing_date"]) if canonical == "eviction_filing" else (
        _iso(case["disposition_date"]) or _iso(case["filing_date"]))
    body = [f"CASE: {case['case_number']}", f"CASE TYPE: {case.get('case_type')}",
            f"FILED: {case['filing_date']}", f"STATUS: {case['status']}",
            f"DISPOSITION: {case.get('disposition')}"]
    body += [f"{p['role'].upper()}: {p['name']} — {(p.get('address') or {}).get('street')}, "
             f"{(p.get('address') or {}).get('city_state_zip')}" for p in case["parties"]]
    return {
        "raw_event_id": _rid(source_id, case["case_number"], canonical),
        "source_id": source_id,
        "source_role": "PRIMARY_EVENT_SOURCE",
        "raw_doc_type": (case.get("case_type") or "CIVIL").upper(),
        "canonical_doc_type": canonical,
        "instrument_number": case["case_number"],
        "recorded_date": _iso(case["filing_date"]),
        "event_date": event_date,
        "source_url": url,
        "parties": _parties_for_event(case),
        "document_body_text": "\n".join(body),
        "property_refs": {
            "parcel_id": None,
            "situs_address": street,
            "situs_city_state_zip": csz,
            "legal_description": None,
            "case_number": case["case_number"],
        },
        "amounts": [],
        "lifecycle_hint": {"status": case["status"], "disposition": case.get("disposition")},
        "parser_name": "clayton_case_inquiry",
        "parser_version": "1.0.0",
        "parser_confidence": 85 if street else 60,
        "captured_at": captured_at,
    }


# ---------------------------------------------------------------------------
# Cursor / frontier
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    return json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}


def _save_state(st: dict) -> None:
    STATE_PATH.write_text(json.dumps(st, indent=2, sort_keys=True), encoding="utf-8")


def find_frontier(ctt: str, ctp: str, year: int) -> int:
    """Highest existing sequence this year (gap-tolerant binary search)."""
    def exists(seq: int) -> bool:
        return any(fetch_case(ctt, year, ctp, seq + k) is not None for k in (0, 1, 2))
    lo, hi = 0, 99999
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if exists(mid):
            lo = mid
        else:
            hi = mid - 1
    return lo


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def scrape(incremental: bool = True, include_institutional: bool = False,
           max_new_per_court: int | None = None) -> list[dict]:
    captured_at = datetime.now(timezone.utc).isoformat()
    year = date.today().year
    st = _load_state() if incremental else {}
    events: list[dict] = []
    stats: dict[str, dict] = {}

    for key, (ctt, ctp, source_id, backfill) in COURTS.items():
        cs = st.get(key) or {}
        s = stats.setdefault(key, {"fetched": 0, "events": 0, "skipped_institutional": 0,
                                   "non_target": 0, "pending_added": 0, "pending_resolved": 0})
        try:
            if cs.get("year") != year:
                if cs.get("year") and cs["year"] < year:
                    cs = {"year": year, "next_seq": 1, "pending": cs.get("pending", {})}
                else:
                    frontier = find_frontier(ctt, ctp, year)
                    cs = {"year": year, "next_seq": max(1, frontier - backfill + 1), "pending": {}}
                    print(f"[clayton_cases] {key}: frontier {year}{ctp}{frontier:05d}, backfilling {backfill}")
            seq, misses, n = cs["next_seq"], 0, 0
            last_found = seq - 1
            while misses < MISS_LIMIT and (max_new_per_court is None or n < max_new_per_court):
                case = fetch_case(ctt, year, ctp, seq)
                s["fetched"] += 1
                if case is None:
                    misses += 1
                else:
                    misses, last_found, n = 0, seq, n + 1
                    url = (f"{DETAIL_URL}?rtype=E&dvt=V&opt=&ctt={ctt}&cyr={year}&ctp={ctp}"
                           f"&csq={seq:05d}&jdg=")
                    if key == "magistrate":
                        if not is_dispossessory(case):
                            s["non_target"] += 1
                        elif landlord_is_institutional(case) and not include_institutional:
                            s["skipped_institutional"] += 1
                        else:
                            events.append(case_to_event(case, source_id, "eviction_filing", captured_at, url))
                            s["events"] += 1
                    else:
                        if is_judgment(case):
                            events.append(case_to_event(case, source_id, "judgment_lien", captured_at, url))
                            s["events"] += 1
                        elif not case["status"].lower().startswith("disposed"):
                            cs.setdefault("pending", {})[str(seq)] = {
                                "filed": _iso(case["filing_date"]), "checked": date.today().isoformat()}
                            s["pending_added"] += 1
                        else:
                            s["non_target"] += 1
                seq += 1
            cs["next_seq"] = last_found + 1

            # State Court: re-check pending cases for a judgment
            if key == "state":
                pend = cs.get("pending", {})
                cutoff_check = (date.today() - timedelta(days=PENDING_RECHECK_DAYS)).isoformat()
                cutoff_age = (date.today() - timedelta(days=PENDING_MAX_AGE_DAYS)).isoformat()
                for sq in [k for k, v in pend.items() if (v.get("filed") or "") < cutoff_age]:
                    pend.pop(sq)
                due = sorted((k for k, v in pend.items() if v["checked"] <= cutoff_check),
                             key=lambda k: pend[k]["checked"])[:PENDING_RECHECK_PER_RUN]
                for sq in due:
                    case = fetch_case(ctt, year, ctp, int(sq))
                    s["fetched"] += 1
                    if case is None:
                        pend.pop(sq)
                        continue
                    if is_judgment(case):
                        url = (f"{DETAIL_URL}?rtype=E&dvt=V&opt=&ctt={ctt}&cyr={year}&ctp={ctp}"
                               f"&csq={int(sq):05d}&jdg=")
                        events.append(case_to_event(case, source_id, "judgment_lien", captured_at, url))
                        s["events"] += 1
                        s["pending_resolved"] += 1
                        pend.pop(sq)
                    elif case["status"].lower().startswith("disposed"):
                        pend.pop(sq)
                    else:
                        pend[sq]["checked"] = date.today().isoformat()
        except Transient as exc:
            print(f"[clayton_cases] {key}: stopping early — {exc}")
        except requests.RequestException as exc:
            print(f"[clayton_cases] {key}: network error — {exc}")
        st[key] = cs
        _save_state(st)
        print(f"[clayton_cases] {key}: {s}")

    if events:
        out = RAW_DIR / f"clayton_cases_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
        with out.open("w", encoding="utf-8") as fh:
            for e in events:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        print(f"[clayton_cases] wrote {len(events)} events -> {out}")
    return events


if __name__ == "__main__":
    res = scrape(incremental="--full" not in sys.argv,
                 include_institutional="--include-institutional" in sys.argv)
    print(f"Total new case events: {len(res)}")
