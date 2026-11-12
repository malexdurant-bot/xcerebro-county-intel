"""
Clayton County, GA — Probate Court estates via georgiaprobaterecords.com.

Source (config/counties/clayton_ga.json): probate_estates
Linked from https://www.claytoncountyga.gov/government/courts/probate-court/online-estate-records/
("Clayton County Probate Court estate records can be viewed online at no cost").

Portal: ASP.NET WebForms + Telerik controls, no login, no CAPTCHA.
  Search : POST /Estates/SearchEstates.aspx  (county RadDropDown "Clayton" = 1109,
           Filed Date range). Default grid page size is 10 rows.
  Detail : GET  /Estates/EstateDetails.aspx?RECID=<base64 id>
           stable element ids: cpMain_lblCaseName / lblCaseNo / lblStatus /
           lblStreetAddress / lblCityStateZip, repParty_* (relationship, name,
           address, party type), repFilings_* (filing type + filed date).

"Filed Date" matches ANY filing in the date range, so old estates with new
activity come back too. A NEW estate is one whose earliest filing falls inside
OPEN_LOOKBACK_DAYS; only those become lead events. The search runs one day at
a time so each window stays under the 10-row page; a full page is logged as
possible truncation rather than silently accepted.

The decedent's own street address is on the detail page and is normally the
property. Lead subject = the estate; DECEDENT: label feeds the framework's §17
probate DOCUMENT_BODY extractor.
"""

from __future__ import annotations

import base64
import hashlib
import json
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
RAW_DIR = ROOT / "data" / "raw" / "clayton_probate"
RAW_DIR.mkdir(parents=True, exist_ok=True)
STATE_PATH = RAW_DIR / "state.json"

BASE = "https://www.georgiaprobaterecords.com"
SEARCH_URL = BASE + "/Estates/SearchEstates.aspx"
DETAIL_URL = BASE + "/Estates/EstateDetails.aspx?RECID={recid}"
COUNTY_TEXT, COUNTY_VALUE, COUNTY_INDEX = "Clayton", "1109", 19

DELAY_S = 2.0
PAGE_SIZE = 10
OPEN_LOOKBACK_DAYS = 120
FIRST_RUN_DAYS = 30
RESCAN_OVERLAP_DAYS = 3

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
})
_last = 0.0


def _throttle() -> None:
    global _last
    wait = DELAY_S - (time.monotonic() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.monotonic()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _base_form() -> dict:
    _throttle()
    r = _SESSION.get(SEARCH_URL, timeout=45)
    r.raise_for_status()
    s = BeautifulSoup(r.text, "html.parser")
    form = {i["name"]: i.get("value", "") for i in s.find_all("input")
            if i.get("name") and i.get("type") in (None, "hidden", "text")}
    form.pop("ctl00$imgBanner", None)
    form["ctl00$cpMain$ddlCounty"] = COUNTY_TEXT
    form["ctl00_cpMain_ddlCounty_ClientState"] = json.dumps({
        "enabled": True, "logEntries": [], "selectedIndex": COUNTY_INDEX,
        "selectedText": COUNTY_TEXT, "selectedValue": COUNTY_VALUE})
    form["ctl00$cpMain$btnSearch"] = "Search"
    return form


def _set_date(form: dict, ctl: str, d: date) -> None:
    iso = d.isoformat()
    form[f"ctl00$cpMain${ctl}"] = iso
    form[f"ctl00$cpMain${ctl}$dateInput"] = f"{d.month}/{d.day}/{d.year}"
    form[f"ctl00_cpMain_{ctl}_dateInput_ClientState"] = json.dumps({
        "enabled": True, "emptyMessage": "", "validationText": f"{iso}-00-00-00",
        "valueAsString": f"{iso}-00-00-00", "minDateStr": "1000-01-01-00-00-00",
        "maxDateStr": "2099-12-31-00-00-00", "lastSetTextBoxValue": f"{d.month}/{d.day}/{d.year}"})


NEXT_PAGE_BTN = "ctl00$cpMain$rgEstates$ctl00$ctl03$ctl01$ctl10"   # Telerik pager "next"
MAX_PAGES = 15


def _page_form(s: BeautifulSoup) -> dict:
    """Re-post form built from a results page (fresh viewstate), minus buttons."""
    f = {}
    for i in s.find_all("input"):
        n = i.get("name")
        if not n or i.get("type") in ("submit", "button", "image", "checkbox", "radio"):
            continue
        f[n] = i.get("value", "")
    return f


def search_filed(form: dict, start: date, end: date) -> list[dict]:
    """All rows for the filed-date window, following the grid pager."""
    f = dict(form)
    _set_date(f, "txtFiledStartDate", start)
    _set_date(f, "txtFiledEndDate", end)
    _throttle()
    r = _SESSION.post(SEARCH_URL, data=f, timeout=60)
    r.raise_for_status()
    s = BeautifulSoup(r.text, "html.parser")
    rows, seen = _rows(s), set()
    seen.update(x["recid"] for x in rows)
    page = 1
    while len(rows) >= PAGE_SIZE * page and page < MAX_PAGES and s.find("input", attrs={"name": NEXT_PAGE_BTN}):
        nf = _page_form(s)
        nf[NEXT_PAGE_BTN] = " "
        _throttle()
        r = _SESSION.post(SEARCH_URL, data=nf, timeout=60)
        r.raise_for_status()
        s = BeautifulSoup(r.text, "html.parser")
        new = [x for x in _rows(s) if x["recid"] not in seen]
        if not new:
            break
        seen.update(x["recid"] for x in new)
        rows += new
        page += 1
    return rows


def _rows(s: BeautifulSoup) -> list[dict]:
    rows = []
    for tr in s.find_all("tr"):
        cls = tr.get("class") or []
        if "rgRow" not in cls and "rgAltRow" not in cls:
            continue
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        a = tr.find("a", href=re.compile(r"RECID="))
        if not a or len(tds) < 6:
            continue
        rows.append({"case_number": tds[0], "decedent": tds[1], "city": tds[2],
                     "died": tds[4] or None,
                     "recid": re.search(r"RECID=([^&]+)", a["href"]).group(1)})
    return rows


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

def _txt(s, id_):
    e = s.find(id=id_)
    return re.sub(r"\s+", " ", e.get_text(" ", strip=True)).strip() if e else None


def fetch_detail(recid: str) -> dict:
    _throttle()
    r = _SESSION.get(DETAIL_URL.format(recid=recid), timeout=45)
    r.raise_for_status()
    s = BeautifulSoup(r.text, "html.parser")
    parties, i = [], 0
    while s.find(id=f"cpMain_repParty_lblParty_{i}"):
        parties.append({
            "relationship": _txt(s, f"cpMain_repParty_lblRelationship_{i}"),
            "name": _txt(s, f"cpMain_repParty_lblParty_{i}"),
            "address": _txt(s, f"cpMain_repParty_lblAddress_{i}"),
            "city_state_zip": _txt(s, f"cpMain_repParty_lblCityStateZip_{i}"),
            "party_type": _txt(s, f"cpMain_repParty_lblPartyType_{i}"),
        })
        i += 1
    filings, i = [], 0
    while s.find(id=f"cpMain_repFilings_lblFilingTypeDesc_{i}"):
        filings.append({
            "type": _txt(s, f"cpMain_repFilings_lblFilingTypeDesc_{i}"),
            "filed": _txt(s, f"cpMain_repFilings_lblFiledDate_{i}"),
            "petitioner": _txt(s, f"cpMain_repFilings_lblPetitioner_{i}"),
        })
        i += 1
    return {
        "recid": recid,
        "decedent": _txt(s, "cpMain_lblCaseName"),
        "case_number": _txt(s, "cpMain_lblCaseNo"),
        "status": _txt(s, "cpMain_lblStatus"),
        "street": _txt(s, "cpMain_lblStreetAddress"),
        "city_state_zip": _txt(s, "cpMain_lblCityStateZip"),
        "parties": parties,
        "filings": filings,
    }


# ---------------------------------------------------------------------------
# -> raw_event_record
# ---------------------------------------------------------------------------

def _iso(d: str | None) -> str | None:
    try:
        return datetime.strptime(d, "%m/%d/%Y").date().isoformat() if d else None
    except ValueError:
        return None


def opened_on(detail: dict) -> str | None:
    ds = sorted(x for x in (_iso(f["filed"]) for f in detail["filings"]) if x)
    return ds[0] if ds else None


def classify(detail: dict) -> tuple[str, str]:
    first = min(detail["filings"], key=lambda f: _iso(f["filed"]) or "9999") if detail["filings"] else {}
    t = (first.get("type") or "").lower()
    if "will" in t:
        return "letters_testamentary", first.get("type") or ""
    if "no administration" in t or "year's support" in t or "years support" in t:
        return "determination_of_heirship", first.get("type") or ""
    return "letters_of_administration", first.get("type") or ""


def detail_to_event(d: dict, captured_at: str) -> dict:
    canonical, raw_type = classify(d)
    opened = opened_on(d)
    petitioners = [p for p in d["parties"] if (p.get("party_type") or "").upper() == "PETITIONER"]
    body = [f"DECEDENT: {d['decedent']}", f"CASE: {d['case_number']}", f"STATUS: {d['status']}",
            f"DECEDENT ADDRESS: {d['street']}, {d['city_state_zip']}", f"OPENED: {opened}",
            f"OPENING FILING: {raw_type}"]
    body += [f"{(p.get('party_type') or p.get('relationship') or 'PARTY').upper()}: {p['name']} "
             f"({p.get('relationship') or ''}) — {p.get('address')}, {p.get('city_state_zip')}"
             for p in d["parties"]]
    unknown_addr = (d["street"] or "").upper() in ("", "UNKNOWN", "NONE")
    return {
        "raw_event_id": "raw_cpr_" + hashlib.md5(d["case_number"].encode()).hexdigest()[:16],
        "source_id": "probate_estates",
        "source_role": "PRIMARY_EVENT_SOURCE",
        "raw_doc_type": raw_type.upper() or "ESTATE",
        "canonical_doc_type": canonical,
        "instrument_number": d["case_number"],
        "recorded_date": opened,
        "event_date": opened,
        "source_url": DETAIL_URL.format(recid=d["recid"]),
        "parties": [{"name": p["name"], "name_type": "PE", "raw_role": "PETITIONER"} for p in petitioners if p.get("name")],
        "document_body_text": "\n".join(body),
        "property_refs": {
            "parcel_id": None,
            "situs_address": None if unknown_addr else d["street"],
            "situs_city_state_zip": None if unknown_addr else d["city_state_zip"],
            "legal_description": None,
            "case_number": d["case_number"],
        },
        "amounts": [],
        "contacts": [{"name": p["name"], "relationship": p.get("relationship"),
                      "role": p.get("party_type"), "address": p.get("address"),
                      "city_state_zip": p.get("city_state_zip")} for p in d["parties"]],
        "parser_name": "georgia_probate_records",
        "parser_version": "1.0.0",
        "parser_confidence": 85 if not unknown_addr else 60,
        "captured_at": captured_at,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    return json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}


def scrape(incremental: bool = True, days: int | None = None) -> list[dict]:
    captured_at = datetime.now(timezone.utc).isoformat()
    st = _load_state() if incremental else {}
    emitted: set[str] = set(st.get("emitted", []))
    checked: dict[str, str] = st.get("checked_old", {})  # recid -> opened date (old estates)
    today = date.today()
    if days is not None:
        start = today - timedelta(days=days)
    elif st.get("last_scanned"):
        start = date.fromisoformat(st["last_scanned"]) - timedelta(days=RESCAN_OVERLAP_DAYS)
    else:
        start = today - timedelta(days=FIRST_RUN_DAYS)
    open_cutoff = (today - timedelta(days=OPEN_LOOKBACK_DAYS)).isoformat()

    try:
        form = _base_form()
    except requests.RequestException as exc:
        print(f"[clayton_probate] search page unavailable: {exc}")
        return []

    hits: dict[str, dict] = {}
    truncated_days = []
    d = start
    while d <= today:
        try:
            rows = search_filed(form, d, d)
        except requests.RequestException as exc:
            print(f"[clayton_probate] search {d} failed: {exc}")
            rows = []
        if len(rows) >= PAGE_SIZE * MAX_PAGES:
            truncated_days.append(d.isoformat())
        for r in rows:
            hits[r["recid"]] = r
        d += timedelta(days=1)
    print(f"[clayton_probate] {len(hits)} estates with filings {start}..{today}"
          + (f"; FULL PAGE (possible truncation) on {truncated_days}" if truncated_days else ""))

    events, old = [], 0
    for recid, row in hits.items():
        if row["case_number"] in emitted or recid in checked:
            continue
        try:
            det = fetch_detail(recid)
        except requests.RequestException as exc:
            print(f"[clayton_probate] detail {row['case_number']} failed: {exc}")
            continue
        opened = opened_on(det)
        if not opened or opened < open_cutoff:
            checked[recid] = opened or ""
            old += 1
            continue
        events.append(detail_to_event(det, captured_at))
        emitted.add(det["case_number"])
    print(f"[clayton_probate] {len(events)} newly opened estates; {old} older estates with new activity skipped")

    st.update({"last_scanned": today.isoformat(), "emitted": sorted(emitted), "checked_old": checked,
               "last_truncated_days": truncated_days})
    STATE_PATH.write_text(json.dumps(st, indent=2), encoding="utf-8")
    if events:
        out = RAW_DIR / f"clayton_probate_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
        with out.open("w", encoding="utf-8") as fh:
            for e in events:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        print(f"[clayton_probate] wrote {len(events)} events -> {out}")
    return events


if __name__ == "__main__":
    n = None
    if "--days" in sys.argv:
        n = int(sys.argv[sys.argv.index("--days") + 1])
    res = scrape(incremental="--full" not in sys.argv, days=n)
    print(f"Total new probate events: {len(res)}")
