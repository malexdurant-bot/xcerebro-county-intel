"""
Clayton County, GA — court case walker for the county JIS case inquiry.

One public, no-login case inquiry covers Magistrate, State and Superior
Court civil cases (linked from clerk.claytoncountyga.gov -> Case Search):

    https://weba.claytoncountyga.gov/casinqsvr/htdocs/index.shtml

It searches by name or case number only (no date / case-type search), so
this module walks case numbers. Findings, live 2026-10-28:

  - Magistrate civil: one yearly sequence for every civil type,
    "2026CM26000" (~95 cases/day). Dispossessories, account/note/contract
    suits, garnishments, abandoned vehicles all share it.
  - State Court civil: "2026CV#####" (~20/day), mostly contract/account
    debt suits and torts.
  - Superior Court: one yearly counter shared by several type codes
    ("2026DR00002" divorce, "2026CV00010" civil, "CS" child support ...),
    so each number is tried under each wanted code (~15 cases/day).
  - The judge suffix ("LK", "11") is optional on lookup.
  - The server's nginx answers ~1 request/s bursts with 503: this runs at
    one request every REQUEST_DELAY_S seconds with backoff retries.
  - The court CALENDAR app on the same host puts its case lists behind a
    Cloudflare Turnstile challenge. Not used; if the case inquiry ever
    starts answering with a challenge, fetch_case raises -- never bypass it.

Privacy: tenants' / defendants' typed "Answer" text on the docket is kept
out entirely (action + date only).

Callers give a `categorize(case) -> source_id | None` per court; every kept
case is written to `<source_id>.jsonl` (wrapped-record shape, merged with
the prior run like the other Clayton scrapers). The per-court high-water
mark lives in `jis_<court>_state.json`, so daily runs only fetch new
numbers plus a bounded re-check of still-pending cases.

Requires: pip install requests beautifulsoup4
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import requests
from bs4 import BeautifulSoup

BASE = "https://weba.claytoncountyga.gov"
CASE_URL = BASE + "/casinqcgi-bin/wci010r.pgm"
SEARCH_PAGE = BASE + "/casinqsvr/htdocs/index.shtml"

# court key -> (ctt court code, case type codes tried for each number)
COURTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "magistrate": ("M", ("CM",)),
    "state": ("L", ("CV",)),
    "superior": ("U", ("DR", "CV")),
}

REQUEST_DELAY_S = 2.5
RETRY_WAITS_S = (15, 45, 120)
CONNECT_TIMEOUT_S = 10
READ_TIMEOUT_S = 30
CASE_DEADLINE_S = 90
GAP_PROBE = 6
STOP_AFTER_OLD = 40
STOP_AFTER_MISSING = 150
MAX_SEQ = 99999
# 2026-11-22: a 503 storm (Nov 21-22 night) kept one court walk busy for
# hours and held up the counties queued behind Clayton. A walk now stops
# after this many case fetches fail in a row, or after this much wall time;
# the court's high-water mark is then NOT advanced, so the next run re-walks
# the unfinished numbers (kept cases dedupe on merge).
MAX_CONSECUTIVE_FETCH_FAILURES = 20
WALK_DEADLINE_S = 75 * 60

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_CITY_LINE_RE = re.compile(r",\s*[A-Z]{2}\s+\d{5}(-\d{4})?\s*$", re.I)
_PHONE_RE = re.compile(r"^\(?\d{3}\)?[\s-]*\d{3}-\d{4}$")
_WRIT_RE = re.compile(r"\bWRIT\b", re.I)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mdy(raw: str | None) -> date | None:
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", raw or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def case_url(ctt: str, year: int, ctp: str, seq: int) -> str:
    return (f"{CASE_URL}?rtype=E&dvt=V&opt=&ctt={ctt}&cyr={year}"
            f"&ctp={ctp}&csq={seq:05d}&jdg=")


class JisFetchError(RuntimeError):
    """Raised when a case fetch exhausts retries or hits the per-case deadline."""


def _new_session(prior: requests.Session | None = None) -> requests.Session:
    """Fresh requests session, copying headers from `prior` if given.

    The previous session is closed so a hung TCP socket is not reused.
    User-Agent / other headers are unchanged from what the caller already set.
    """
    session = requests.Session()
    if prior is not None:
        session.headers.update(prior.headers)
        try:
            prior.close()
        except Exception:  # noqa: BLE001 -- close is best-effort
            pass
    else:
        session.headers.update({"User-Agent": USER_AGENT})
    return session


def fetch_case(session: requests.Session, ctt: str, year: int, ctp: str, seq: int
               ) -> tuple[str | None, requests.Session]:
    """Case page HTML, or None when the number does not exist under ctp.

    Returns `(html_or_none, session)` so the caller can keep using a session
    that may have been replaced after a dropped connection.

    Each attempt uses timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S). The whole
    fetch is bounded by CASE_DEADLINE_S seconds of wall time. ReadTimeout,
    ConnectTimeout, or ConnectionError closes the session and retries with a
    fresh one after RETRY_WAITS_S (waits are capped so they do not overrun the
    deadline). After the last retry or a missed deadline, a loud error is
    printed and JisFetchError is raised. Verification stays on; headers and
    the user-agent are not changed; a Turnstile challenge still aborts.
    """
    url = case_url(ctt, year, ctp, seq)
    case_id = f"{year}{ctp}{seq:05d}"
    deadline = time.monotonic() + CASE_DEADLINE_S
    attempts = list(RETRY_WAITS_S) + [None]
    last_exc: BaseException | None = None
    resp = None

    for attempt, wait in enumerate(attempts, start=1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            last_exc = last_exc or TimeoutError(
                f"per-case deadline {CASE_DEADLINE_S}s exceeded")
            break
        time.sleep(min(REQUEST_DELAY_S, remaining))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            last_exc = last_exc or TimeoutError(
                f"per-case deadline {CASE_DEADLINE_S}s exceeded")
            break
        connect_t = min(float(CONNECT_TIMEOUT_S), remaining)
        read_t = min(float(READ_TIMEOUT_S), max(1.0, remaining - connect_t))
        try:
            resp = session.get(url, timeout=(connect_t, read_t))
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_exc = exc
            print(f"  [Clayton JIS] {case_id} attempt {attempt}/{len(attempts)}: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            session = _new_session(session)
            if wait is not None:
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(wait, remaining))
            continue

        if resp.status_code in (429, 502, 503, 504) and wait is not None:
            last_exc = RuntimeError(f"HTTP {resp.status_code}")
            print(f"  [Clayton JIS] {case_id} attempt {attempt}/{len(attempts)}: "
                  f"HTTP {resp.status_code}", flush=True)
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(wait, remaining))
            continue

        last_exc = None
        break
    else:
        last_exc = last_exc or TimeoutError(
            f"per-case deadline {CASE_DEADLINE_S}s exceeded")

    if resp is None or last_exc is not None:
        reason = last_exc or TimeoutError(
            f"per-case deadline {CASE_DEADLINE_S}s exceeded")
        msg = (f"[Clayton JIS] FAILED {case_id} after {len(attempts)} attempts "
               f"or {CASE_DEADLINE_S}s deadline: {type(reason).__name__}: {reason}")
        print(f"  {msg}", flush=True)
        raise JisFetchError(msg) from (reason if isinstance(reason, BaseException) else None)

    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        msg = f"[Clayton JIS] FAILED {case_id}: HTTP {resp.status_code}: {exc}"
        print(f"  {msg}", flush=True)
        raise JisFetchError(msg) from exc

    body = resp.text
    if "Case# not found" in body:
        return None, session
    if "turnstile" in body.lower() or "challenges.cloudflare.com" in body:
        raise RuntimeError("case inquiry now returns a Turnstile challenge -- stopping")
    if "Basic Case Information" not in body:
        raise RuntimeError(f"unexpected case page for {case_id}")
    return body, session


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _details_lines(td) -> tuple[str, list[str]]:
    det = td.find("details")
    if det is None:
        return _clean(td.get_text(" ")), []
    name = _clean(det.summary.get_text(" ")) if det.summary else ""
    if det.summary:
        det.summary.extract()
    lines = [x for x in (_clean(x) for x in det.get_text("\n").split("\n")) if x]
    if lines and lines[0].upper() == name.upper():
        lines = lines[1:]
    return name, lines


def _split_address(lines: list[str]) -> dict:
    city_state_zip, rest = None, []
    for ln in lines:
        if city_state_zip is None and _CITY_LINE_RE.search(ln):
            city_state_zip = ln
        elif _PHONE_RE.match(ln):
            continue
        elif city_state_zip is None:
            rest.append(ln)
    return {"street": rest[0] if rest else None,
            "unit": " ".join(rest[1:]) or None if len(rest) > 1 else None,
            "city_state_zip": city_state_zip}


def parse_case(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    info: dict[str, str] = {}
    for th in soup.select("section[aria-labelledby=case-summary] th"):
        td = th.find_next_sibling("td")
        key = _clean(th.get_text())
        if key and td is not None:
            info[key] = _clean(td.get_text(" "))
    status_raw = info.get("Status", "")

    parties: list[dict] = []
    case_types: list[str] = []
    for tr in soup.select("section[aria-labelledby=case-parties] tbody tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 2:
            continue
        role = _clean(tds[1].get_text())
        name, lines = _details_lines(tds[0])
        if not role:
            # case-type row: <summary>Patho</summary> Dispossessory
            if name:
                case_types.append(_clean(f"{name} / {' '.join(lines)}") if lines else name)
            continue
        attorney = None
        if len(tds) > 2:
            att_name, _ = _details_lines(tds[2])
            if att_name and "not applicable" not in att_name.lower():
                attorney = att_name
        parties.append({"name": name, "party_type": role, "attorney": attorney,
                        "has_address": bool(lines), **_split_address(lines)})

    docket: list[dict] = []
    for tr in soup.select("section[aria-labelledby=docket-info] tbody tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 3:
            continue
        d, action, desc = (_clean(t.get_text(" ")) for t in tds[:3])
        if d or action:
            docket.append({"date": _iso(_mdy(d)), "action": action or None,
                           "description": None if action.lower() == "answer" else (desc or None)})
        elif docket and (docket[-1]["action"] or "").lower() != "answer":
            prev = docket[-1]
            prev["description"] = _clean(f"{prev['description'] or ''} {desc}") or None

    filing_entry = next((e for e in reversed(docket)
                         if (e["action"] or "").lower() == "case filing"), None)
    if not case_types and filing_entry and filing_entry["description"]:
        case_types.append(filing_entry["description"])
    writs = [e for e in docket if _WRIT_RE.search(f"{e['action'] or ''} {e['description'] or ''}")]
    return {
        "case_number": info.get("Case Number"),
        "assigned_judge": info.get("Assigned Judge"),
        "filing_date": _iso(_mdy(info.get("Filing Date"))),
        "case_status": status_raw.split(",")[0].strip() or None,
        "status_date": _iso(_mdy(status_raw)),
        "disposition": info.get("Disposition") or None,
        "case_types": case_types,
        "parties": parties,
        "docket": docket,
        "answer_filed": any((e["action"] or "").lower() == "answer" for e in docket),
        "writ_of_possession_date": writs[0]["date"] if writs else None,
        "hearing_dates": sorted({e["date"] for e in docket
                                 if (e["action"] or "").lower() == "calendar" and e["date"]}),
    }


def case_type_text(case: dict) -> str:
    return " ".join(case.get("case_types") or []).lower()


# ---------------------------------------------------------------------------
# Walking
# ---------------------------------------------------------------------------


class _Walker:
    def __init__(self, session, court: str, year: int):
        self.session = session
        self.ctt, self.codes = COURTS[court]
        self.year = year
        self.cache: dict[int, tuple[str, str] | None] = {}  # seq -> (ctp, html) | None

    def get(self, seq: int) -> tuple[str, str] | None:
        if seq not in self.cache:
            hit = None
            for ctp in self.codes:
                html, self.session = fetch_case(self.session, self.ctt, self.year, ctp, seq)
                if html is not None:
                    hit = (ctp, html)
                    break
            self.cache[seq] = hit
        return self.cache[seq]

    def exists_near(self, seq: int) -> bool:
        return any(self.get(s) is not None for s in range(seq, min(seq + GAP_PROBE, MAX_SEQ + 1)))

    def high_water(self, floor: int) -> int:
        lo = max(floor, 1)
        if not self.exists_near(lo):
            return 0 if floor <= 1 else floor
        step = 256
        hi = lo + step
        while hi <= MAX_SEQ and self.exists_near(hi):
            lo, step = hi, step * 2
            hi = lo + step
        hi = min(hi, MAX_SEQ + 1)
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.exists_near(mid):
                lo = mid
            else:
                hi = mid
        top = lo
        for s in range(lo, min(lo + GAP_PROBE, MAX_SEQ + 1)):
            if self.cache.get(s) is not None:
                top = s
        return top


def _wrap(source_id: str, ctt: str, year: int, ctp: str, seq: int, case: dict) -> dict:
    case.update(case_year=year, case_type_code=ctp, case_sequence=seq, court_code=ctt)
    return {
        "raw_record_id": f"clayton_{ctt.lower()}_{year}{ctp}{seq:05d}",
        "source_id": source_id,
        "source_url": case_url(ctt, year, ctp, seq),
        "source_fetched_at": _now_iso(),
        "parser_confidence": 90,
        "raw_payload": case,
    }


def _load_jsonl(path: Path) -> dict:
    if not path.exists():
        return {}
    out = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rec = json.loads(line)
                out[rec["raw_record_id"]] = rec
    return out


def _merge_write(path: Path, current: list[dict]) -> dict:
    prior = _load_jsonl(path)
    now = _now_iso()
    merged, seen = [], set()
    for rec in current:
        rid = rec["raw_record_id"]
        seen.add(rid)
        prev = prior.get(rid)
        if prev is None:
            rec.update(first_seen_at=now, last_seen_at=now, change_status="NEW_RECORD")
        else:
            rec["first_seen_at"] = prev.get("first_seen_at", now)
            rec["last_seen_at"] = now
            rec["change_status"] = "SAME" if prev["raw_payload"] == rec["raw_payload"] else "UPDATED"
        merged.append(rec)
    for rid, prev in prior.items():
        if rid not in seen:
            prev["change_status"] = "SAME"  # rolling window -- never DISAPPEARED
            merged.append(prev)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for rec in merged:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(path)
    return {"records_pulled": len(current), "prior_count": len(prior),
            "total_after_merge": len(merged),
            "new_record_count": sum(1 for r in merged if r["change_status"] == "NEW_RECORD"),
            "updated_record_count": sum(1 for r in merged if r["change_status"] == "UPDATED")}


def run_court_walk(
    out_dir: Path,
    court: str,
    categorize: Callable[[dict], str | None],
    source_ids: tuple[str, ...],
    days_back: int,
    refresh_policy: dict[str, tuple[int, int]] | None = None,
    verbose: bool = True,
    end: date | None = None,
    label: str = "",
) -> dict:
    """Walk one court's new case numbers back to `days_back`, keep the
    cases `categorize` assigns a source_id, write one JSONL per source_id.

    refresh_policy: {source_id: (window_days, min_days_between_fetches)} --
    still-pending kept cases filed within window_days are re-fetched when
    their last fetch is at least min_days old (writs, judgments, decrees
    land weeks after filing)."""
    end = end or date.today()
    start = end - timedelta(days=days_back)
    ctt, _codes = COURTS[court]
    label = label or f"Clayton {court}"
    state_path = out_dir / f"jis_{court}_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    priors = {sid: _load_jsonl(out_dir / f"{sid}.jsonl") for sid in source_ids}

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    kept: dict[str, list[dict]] = {sid: [] for sid in source_ids}
    errors: list[str] = []
    scanned = refreshed = 0
    high_water: dict[str, int] = {}

    walk_deadline = time.monotonic() + WALK_DEADLINE_S
    consecutive_failures = 0
    stopped_early = None
    for year in range(end.year, start.year - 1, -1):
        if stopped_early:
            break
        w = _Walker(session, court, year)
        prev_top = int(state.get("high_water", {}).get(str(year), 0))
        try:
            top = w.high_water(floor=max(prev_top - GAP_PROBE, 1))
        except JisFetchError as exc:
            errors.append(f"{year} high-water: {exc}")
            if verbose:
                print(f"  [{label}] {year}: high-water FAILED: {exc}", flush=True)
            session = w.session
            continue
        top = max(top, prev_top)
        high_water[str(year)] = top
        if verbose:
            print(f"  [{label}] {year}: high-water {top} (prior run {prev_top or 'none'})", flush=True)
        old_run = missing_run = 0
        for seq in range(top, prev_top, -1):
            if time.monotonic() > walk_deadline:
                stopped_early = (f"wall-clock cap {WALK_DEADLINE_S // 60} min reached at "
                                 f"{year} seq {seq}")
                break
            try:
                hit = w.get(seq)
            except JisFetchError as exc:
                errors.append(f"fetch {year} seq {seq}: {exc}")
                session = w.session
                scanned += 1
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FETCH_FAILURES:
                    stopped_early = (f"{consecutive_failures} case fetches failed in a row "
                                     f"at {year} seq {seq}")
                    break
                continue
            consecutive_failures = 0
            scanned += 1
            if hit is None:
                missing_run += 1
                if missing_run >= STOP_AFTER_MISSING:
                    break
                continue
            missing_run = 0
            ctp, html = hit
            try:
                case = parse_case(html)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"parse {year}{ctp}{seq:05d}: {type(exc).__name__}: {exc}")
                continue
            filed = date.fromisoformat(case["filing_date"]) if case.get("filing_date") else None
            if filed and filed < start:
                old_run += 1
                if old_run >= STOP_AFTER_OLD:
                    break
                continue
            old_run = 0
            case["case_type_code"] = ctp  # categorizers route on it
            sid = categorize(case)
            if sid in kept:
                kept[sid].append(_wrap(sid, ctt, year, ctp, seq, case))
            if verbose and scanned % 100 == 0:
                print(f"  [{label}] scanned {scanned} numbers (at {year}{ctp}{seq:05d}, "
                      f"filed {case.get('filing_date')}); kept "
                      + ", ".join(f"{k}={len(v)}" for k, v in kept.items()), flush=True)
        session = w.session
        if stopped_early:
            # Don't advance past numbers this run never reached.
            if prev_top:
                high_water[str(year)] = prev_top
            else:
                high_water.pop(str(year), None)

    if stopped_early:
        msg = f"{label}: walk stopped early -- {stopped_early}; unfinished numbers retry next run"
        errors.append(msg)
        print(f"  [{label}] WARNING: {msg}", flush=True)

    refresh_failures = 0
    refresh_stopped = False
    for sid, (window_days, min_gap) in (refresh_policy or {}).items():
        if stopped_early:
            break  # the server is struggling; skip the optional re-checks
        fresh_ids = {r["raw_record_id"] for r in kept.get(sid, [])}
        for rid, prev in priors.get(sid, {}).items():
            p = prev.get("raw_payload") or {}
            if rid in fresh_ids or (p.get("case_status") or "").lower() != "pending":
                continue
            filed = date.fromisoformat(p["filing_date"]) if p.get("filing_date") else None
            last = (prev.get("source_fetched_at") or "")[:10]
            if not filed or filed < end - timedelta(days=window_days):
                continue
            if last and date.fromisoformat(last) > end - timedelta(days=min_gap):
                continue
            if time.monotonic() > walk_deadline or refresh_failures >= MAX_CONSECUTIVE_FETCH_FAILURES:
                why = ("wall-clock cap reached" if time.monotonic() > walk_deadline
                       else f"{refresh_failures} re-checks failed in a row")
                errors.append(f"{label}: pending re-checks stopped early ({why}); the rest retry next run")
                print(f"  [{label}] WARNING: pending re-checks stopped early ({why})", flush=True)
                refresh_stopped = True
                break
            try:
                html, session = fetch_case(session, p["court_code"], p["case_year"],
                                           p["case_type_code"], p["case_sequence"])
                refresh_failures = 0
                if html:
                    kept[sid].append(_wrap(sid, p["court_code"], p["case_year"],
                                           p["case_type_code"], p["case_sequence"],
                                           parse_case(html)))
                    refreshed += 1
            except Exception as exc:  # noqa: BLE001
                refresh_failures += 1
                errors.append(f"refresh {rid}: {type(exc).__name__}: {exc}")
        if refresh_stopped:
            break

    if scanned > 50 and not any(kept.values()) and not any(priors.values()):
        errors.append(f"{label}: scanned {scanned} numbers and kept nothing filed "
                      f"{start}..{end} -- page layout or numbering may have changed")

    per_source = {sid: _merge_write(out_dir / f"{sid}.jsonl", recs) for sid, recs in kept.items()}
    state_path.write_text(json.dumps({"high_water": {**state.get("high_water", {}), **high_water},
                                      "updated_at": _now_iso()}, indent=2) + "\n",
                          encoding="utf-8")
    return {
        "source_url": SEARCH_PAGE,
        "court": court,
        "filed_window": [start.isoformat(), end.isoformat()],
        "high_water": high_water,
        "case_numbers_scanned": scanned,
        "pending_refetched": refreshed,
        "per_source": per_source,
        "records_pulled": sum(v["records_pulled"] for v in per_source.values()),
        "new_record_count": sum(v["new_record_count"] for v in per_source.values()),
        "errors": errors,
    }
