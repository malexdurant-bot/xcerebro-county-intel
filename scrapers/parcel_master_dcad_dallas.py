"""
Dallas Central Appraisal District (DCAD) -- account-number parcel enrichment.

Source discovery note (2026-08-26): the operator reported no leads had an
address. Root cause: this framework's dashboard address (display_address)
comes from a SEPARATE parcel-enrichment join, not from the distress-event
scrapers themselves (see scaffold/pipeline/dashboard.py's project_lead(lead,
parcel) -- `parcel` is a distinct object keyed by parcel_id, matching the
pattern richland_sc/shelby_tn already use via their own assessor-lookup
scrapers). Dallas had 5 working distress-event scrapers but no parcel_master
enrichment scraper at all -- this fills that gap.

Portal: https://www.dallascad.org/SearchAcct.aspx (ASP.NET WebForms).
Confirmed live 2026-08-26:
  - Plain `requests` (no Playwright/browser) works: GET once to capture
    __VIEWSTATE/__VIEWSTATEGENERATOR/__EVENTVALIDATION, then POST repeatedly
    with txtAcctNum -- the SAME viewstate tokens are reusable across many
    account lookups in one session (no per-request re-fetch needed),
    ~0.1-0.15s/request.
  - Accepts both Dallas County Tax Office account numbers (from
    tax_collector_dallas.py's TRW ACCOUNT field) and the LGBS tax-sale API's
    account_nbr field directly -- both resolved correctly against real
    properties, confirmed against known-good test cases:
      00000110496000000 -> "2015 CORINTH ST, DALLAS" / SOLIS JONATHAN (owner
        name matched exactly what tax_collector's own OWNER field already
        said, confirming this is the right account).
      28218500060120000 -> "113 NORTH ST, GRAND PRAIRIE" / JOHNSON ERIC &
        PAULINE WERLINE (address matched exactly what the LGBS sheriff_sales
        feed already reported for that account -- confirms cross-source
        consistency -- AND supplied a real owner name where LGBS itself
        exposes none, which can resolve LGBS's "unidentified party" debtor
        gap too).
  - robots.txt disallows indexing individual `/Acct*` detail pages, not the
    `/SearchAcct.aspx` search results page itself. This adapter never visits
    an individual detail page -- the search-results summary row already
    carries everything needed (address, city, owner/business name, total
    value, property type), so no restricted path is ever fetched.
  - Results-page format: an HTML table (id="SearchResults1_dgResults")
    whose one-match row is:
      <a href='AcctDetailCom.aspx?ID=...'>2015  CORINTH ST  </a></td>
      <td align="center">DALLAS ...</td>
      <td align="center"><span>SOLIS JONATHAN ...</span></td>
      <td align="center"><span>$515,840</span></td>
      <td ...><span>COMMERCIAL</span></td>
    Parsed directly from this raw HTML via regex (no HTML parser dependency
    needed) -- confirmed against the live response 2026-08-26. Note the
    detail-page href itself is never fetched (see robots.txt note above);
    only its link text (the address) is read from this results page. The
    detail page filename varies by property type -- AcctDetailCom.aspx for
    commercial, AcctDetailRes.aspx for residential, presumably others for
    other types -- so the regex matches AcctDetail\\w*\\.aspx generically
    rather than hardcoding one variant (an initial version hardcoded "Com"
    and silently failed to match every residential result).

Behaves like a single interactive user session (one requests.Session, no
concurrency, real UA, small per-request delay) rather than a bulk crawler,
consistent with this framework's other adapters on portals with no public
bulk-export API.

Requires: pip install requests
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]

SEARCH_URL = "https://www.dallascad.org/SearchAcct.aspx"
# Added 2026-08-29: clerk_recordings (liens/estate) and foreclosure_notices
# leads have no account number to look up at all -- they only ever carry a
# street address (foreclosure_notices, post-OCR-address-fix) or a resolved
# owner name (clerk_recordings, post-debtor-role fix; also foreclosure_
# notices when OCR resolved a name). SearchAddr.aspx / SearchOwner.aspx are
# DCAD's own address/owner search pages (confirmed live: "Search By: Owner
# Name | Account Number | Street Address | Business Name | Map" nav on every
# DCAD search page) -- both single-match-tested live against real leads from
# this run (11240 PELICAN DR -> HAJ EZZAT; owner "DUNCAN GREG" -> DUNCAN
# GREGORY M, 6421 FAIRFIELD DR) and both expose the DCAD account number in
# the result row's AcctDetail*.aspx?ID=... link, same as SEARCH_URL's own
# results -- so a hit from either of these plugs directly into the same
# account-number-keyed enrichment (dcad_lookup) the rest of the pipeline
# already trusts, no new schema/field needed anywhere downstream.
SEARCH_ADDR_URL = "https://www.dallascad.org/SearchAddr.aspx"
SEARCH_OWNER_URL = "https://www.dallascad.org/SearchOwner.aspx"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_DELAY_SECONDS = 0.2

_VIEWSTATE_RE = re.compile(r'id="__VIEWSTATE" value="([^"]*)"')
_VIEWSTATEGEN_RE = re.compile(r'id="__VIEWSTATEGENERATOR" value="([^"]*)"')
_EVENTVALIDATION_RE = re.compile(r'id="__EVENTVALIDATION" value="([^"]*)"')

# One result row inside the #SearchResults1_dgResults table (see module
# docstring for the exact HTML shape this was matched against live).
#
# The value cell is NOT always "$1,234" -- a property under active protest
# renders "Value in Dispute" there instead (confirmed live 2026-08-29: DCAD
# account 00000738961300000 / 11240 PELICAN DR). The original `\$(?P<value>
# [\d,]*)` required a literal "$" and so silently failed to match the ENTIRE
# row (not just the value) for any disputed-value property -- a real,
# pre-existing bug, not just new-code risk. `(?P<value>[^<]*)` accepts
# either shape; _parse_assessed_value below only converts it to a float when
# it actually looks numeric.
_RESULT_ROW_RE = re.compile(
    r"<a href='AcctDetail\w*\.aspx\?ID=[^']*'[^>]*>\s*(?P<address>.*?)\s*</a>\s*"
    r"</td>\s*<td[^>]*>(?P<city>.*?)</td>\s*<td[^>]*>\s*<span[^>]*>(?P<owner>.*?)</span>\s*"
    r"</td>\s*<td[^>]*>\s*<span[^>]*>(?P<value>[^<]*)</span>\s*"
    r"</td>\s*<td[^>]*>\s*<span[^>]*>(?P<proptype>.*?)</span>",
    re.DOTALL,
)

# Same result-row shape as _RESULT_ROW_RE but also captures the account
# number out of the AcctDetail*.aspx?ID=... href -- needed for address/owner
# search (unlike account search, the account number isn't already known
# going in).
_RESULT_ROW_WITH_ID_RE = re.compile(
    r"<a href='AcctDetail\w*\.aspx\?ID=(?P<acct>[^']*)'[^>]*>\s*(?P<address>.*?)\s*</a>\s*"
    r"</td>\s*<td[^>]*>(?P<city>.*?)</td>\s*<td[^>]*>\s*<span[^>]*>(?P<owner>.*?)</span>\s*"
    r"</td>\s*<td[^>]*>\s*<span[^>]*>(?P<value>[^<]*)</span>\s*"
    r"</td>\s*<td[^>]*>\s*<span[^>]*>(?P<proptype>.*?)</span>",
    re.DOTALL,
)


def _parse_assessed_value(raw: str) -> "float | None":
    """"$515,840" -> 515840.0; "Value in Dispute" (or anything else
    non-numeric) -> None -- a disputed value isn't a usable number, but the
    row (address/owner/account) around it is still real and worth keeping."""
    cleaned = (raw or "").strip().lstrip("$").replace(",", "")
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None

# "< PREV matches 1 - 1 of 1 properties. NEXT >" -- the total is group 1.
_MATCH_COUNT_RE = re.compile(r"matches\s*[\d,]*\s*-\s*[\d,]*\s*of\s*([\d,]+)\s*propert", re.IGNORECASE)

# DCAD's address-search hint (confirmed live on SearchAddr.aspx): "Do not
# enter the street type such as Street, Drive or Lane." -- so it must be
# stripped before searching. Ordered longest-first so e.g. "DRIVE" doesn't
# get shadowed by a shorter partial match.
_STREET_TYPE_SUFFIXES = sorted([
    "STREET", "DRIVE", "AVENUE", "BOULEVARD", "PARKWAY", "CIRCLE", "COURT",
    "TERRACE", "CRESCENT", "CROSSING", "HIGHWAY", "TRAIL", "SQUARE",
    "LANE", "ROAD", "PLACE", "LOOP", "PATH", "WAY", "ALLEY", "COVE", "PASS",
    "PIKE", "ROW", "RUN", "WALK",
    "ST", "DR", "AVE", "BLVD", "PKWY", "CIR", "CT", "TER", "HWY", "TRL",
    "SQ", "LN", "RD", "PL", "CV",
], key=len, reverse=True)
_STREET_DIRECTIONS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}


def parse_street_address(raw: str) -> "dict | None":
    """Parse a scraped/OCR'd address string into DCAD's SearchAddr.aspx
    fields: house number, optional leading direction, and a bare street
    name (type suffix stripped, per the site's own hint). Returns None if
    no leading house number is found (nothing to search on).

    Handles the shapes seen in this county's own raw data, e.g.
    "11240 PELICAN DRIVE, DALLAS, TEXAS, 75238" and
    "703 E. CHERRY STSREET, DUNCANVILLE, TEXAS, 75116" (yes, "STSREET" --
    a real OCR typo in live data; stripped as a best-effort prefix match
    against "STREET" rather than an exact-suffix match for exactly this
    reason).
    """
    if not raw or not isinstance(raw, str):
        return None
    # Only the portion before the first comma is the street address itself;
    # city/state/zip (if present) are handled separately via listCity.
    street_part = raw.split(",")[0].strip()
    m = re.match(r"^(\d+)\s+(.*)$", street_part)
    if not m:
        return None
    house_number, rest = m.group(1), m.group(2).strip()

    tokens = rest.split()
    direction = ""
    if tokens and tokens[0].strip(".").upper() in _STREET_DIRECTIONS:
        direction = tokens[0].strip(".").upper()
        tokens = tokens[1:]
    if tokens:
        last = tokens[-1].strip(".").upper()
        # Prefix match (not equality) to tolerate an OCR-mangled suffix like
        # "STSREET" for "STREET" -- still unambiguously a street-type word,
        # not part of the actual street name.
        if any(last.startswith(suf) or suf.startswith(last) for suf in _STREET_TYPE_SUFFIXES if len(last) >= 2):
            tokens = tokens[:-1]
    street_name = " ".join(tokens).strip()
    if not street_name:
        return None
    return {"house_number": house_number, "direction": direction, "street_name": street_name}


def _clean_html_text(raw: str) -> str:
    """Collapse internal whitespace and strip any stray tags/entities from
    a snippet of extracted table-cell HTML."""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = text.replace("&amp;", "&").replace("&nbsp;", " ")
    return " ".join(text.split())


def _now_iso() -> str:
    from datetime import datetime, timezone
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


class DCADSession:
    """Reuses one GET's viewstate tokens across many account lookups."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._tokens = None

    def _fetch_tokens(self, url: str = SEARCH_URL) -> dict:
        # ASP.NET __VIEWSTATE is tied to the specific page's control tree --
        # a token fetched from SearchAcct.aspx will NOT validate when posted
        # to SearchAddr.aspx/SearchOwner.aspx, so the caller must fetch from
        # (and post back to) the same url.
        resp = self.session.get(url, timeout=15)
        resp.raise_for_status()
        vs = _VIEWSTATE_RE.search(resp.text)
        vsg = _VIEWSTATEGEN_RE.search(resp.text)
        ev = _EVENTVALIDATION_RE.search(resp.text)
        return {
            "__VIEWSTATE": vs.group(1) if vs else "",
            "__VIEWSTATEGENERATOR": vsg.group(1) if vsg else "",
            "__EVENTVALIDATION": ev.group(1) if ev else "",
        }

    def lookup_account(
        self, account_number: str, verbose: bool = False, retries: int = 3
    ) -> dict | None:
        """Returns {situs_address, situs_city, owner_name, assessed_value,
        property_type} or None if no match / after exhausting retries on a
        network error. Never raises -- a single flaky request must not take
        down a multi-thousand-account batch (this crashed a full run
        2026-08-26 when an unhandled ReadTimeout on the token GET killed
        the whole process ~3500 accounts in).

        IMPORTANT: fetches a fresh __VIEWSTATE/__EVENTVALIDATION on every
        call rather than reusing one across many lookups. Confirmed live
        2026-08-26: reusing a session's tokens across sequential lookups
        degrades unpredictably -- a batch test against known-good accounts
        got as low as 1/10 matches with token reuse, but 9/9 (100%) when
        each of those same accounts was looked up with a freshly-fetched
        token instead. The failure mode is silent (a normal-looking "no
        results" response, not an HTTP error), so there is no reliable way
        to detect staleness after the fact and retry -- paying for one
        extra GET per lookup is the only fix that's actually reliable.
        """
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                self._tokens = self._fetch_tokens()
                data = dict(self._tokens)
                data["txtAcctNum"] = account_number
                data["Button1"] = "Submit"
                resp = self.session.post(SEARCH_URL, data=data, timeout=20)
                resp.raise_for_status()
                break
            except requests.RequestException as exc:
                last_exc = exc
                if verbose:
                    print(
                        f"  [DCAD] attempt {attempt + 1}/{retries} failed for "
                        f"{account_number}: {exc}",
                        flush=True,
                    )
                if attempt < retries - 1:
                    time.sleep(1.0 * (attempt + 1))
        else:
            if verbose:
                print(f"  [DCAD] giving up on {account_number} after {retries} attempts: {last_exc}", flush=True)
            return None

        if "matches 0" in resp.text or "No accounts found" in resp.text:
            return None

        m = _RESULT_ROW_RE.search(resp.text)
        if not m:
            return None

        address = _clean_html_text(m.group("address"))
        city = _clean_html_text(m.group("city"))
        owner = _clean_html_text(m.group("owner"))
        prop_type = _clean_html_text(m.group("proptype"))
        return {
            "situs_address": address or None,
            "situs_city": city or None,
            "owner_name": owner or None,
            "assessed_value": _parse_assessed_value(m.group("value")),
            "property_type": prop_type or None,
        }

    def _post_and_parse_single_match(
        self, url: str, extra_fields: dict, label: str, verbose: bool, retries: int
    ) -> dict | None:
        """Shared POST + single-match-only parsing for lookup_by_address and
        lookup_by_owner_name. Unlike lookup_account (an account number is
        inherently unique), address and especially owner-name searches can
        return many results -- e.g. "SMITH J" alone hit 505 live. Returning
        ANY result from an ambiguous multi-match set would risk attributing
        the wrong property/value to a lead, which is worse than leaving it
        unenriched, so this only ever returns a hit when there is EXACTLY
        one match. account (the DCAD account number, parsed out of the
        result row's own AcctDetail*.aspx?ID=... link) is included in the
        returned dict so callers can key dcad_lookup / parcel_id with it.
        """
        last_exc: Exception | None = None
        resp = None
        for attempt in range(retries):
            try:
                tokens = self._fetch_tokens(url)
                data = dict(tokens)
                data.update(extra_fields)
                resp = self.session.post(url, data=data, timeout=20)
                resp.raise_for_status()
                break
            except requests.RequestException as exc:
                last_exc = exc
                if verbose:
                    print(f"  [DCAD] {label} attempt {attempt + 1}/{retries} failed: {exc}", flush=True)
                if attempt < retries - 1:
                    time.sleep(1.0 * (attempt + 1))
        else:
            if verbose:
                print(f"  [DCAD] {label}: giving up after {retries} attempts: {last_exc}", flush=True)
            return None

        if "No Records Found" in resp.text:
            return None

        count_m = _MATCH_COUNT_RE.search(resp.text)
        if count_m and int(count_m.group(1).replace(",", "")) != 1:
            if verbose:
                print(f"  [DCAD] {label}: {count_m.group(1)} matches -- ambiguous, skipping", flush=True)
            return None

        m = _RESULT_ROW_WITH_ID_RE.search(resp.text)
        if not m:
            return None

        return {
            "account_number": m.group("acct").strip(),
            "situs_address": _clean_html_text(m.group("address")) or None,
            "situs_city": _clean_html_text(m.group("city")) or None,
            "owner_name": _clean_html_text(m.group("owner")) or None,
            "assessed_value": _parse_assessed_value(m.group("value")),
            "property_type": _clean_html_text(m.group("proptype")) or None,
        }

    def lookup_by_address(
        self, raw_address: str, verbose: bool = False, retries: int = 3
    ) -> dict | None:
        """raw_address: a scraped/OCR'd street address, e.g.
        "11240 PELICAN DRIVE, DALLAS, TEXAS, 75238" (city/state/zip after
        the first comma are ignored -- house number + street name alone was
        confirmed live to return a confident single match; city is
        deliberately left as DCAD's own "[ALL]" rather than attempting a
        city-name-to-code mapping). Returns the same shape as lookup_account
        plus "account_number", or None if unparseable/no match/ambiguous.
        """
        parsed = parse_street_address(raw_address)
        if parsed is None:
            return None
        fields = {
            "txtAddrNum": parsed["house_number"],
            "listStDir": parsed["direction"],
            "txtStName": parsed["street_name"],
            "txtBldgID": "",
            "txtUnitID": "",
            "listCity": "",
            "txtAddrNum1": "",
            "txtAddrNum2": "",
            "cmdSubmit": "Search",
            "AcctTypeCheckList1:chkAcctType:0": "on",
            "AcctTypeCheckList1:chkAcctType:1": "on",
            "AcctTypeCheckList1:chkAcctType:2": "on",
        }
        return self._post_and_parse_single_match(
            SEARCH_ADDR_URL, fields, f"address {raw_address!r}", verbose, retries
        )

    def lookup_by_owner_name(
        self, name: str, verbose: bool = False, retries: int = 3
    ) -> dict | None:
        """name: "LAST FIRST[ MIDDLE]" -- the exact format clerk_recordings'
        raw grantor/grantee fields and the shared engine's resolved
        owner_name already use (confirmed live: "DUNCAN GREG" -> single
        match "DUNCAN GREGORY M"), so callers pass the resolved owner_name
        straight through with no reformatting. Returns the same shape as
        lookup_by_address, or None if no match/ambiguous. DCAD requires the
        full last name plus at least 2 letters of the first name -- very
        short names may 422/validation-fail rather than return "No Records
        Found"; that surfaces as a request exception here and is treated as
        no-match by the retry loop's normal give-up path.
        """
        name = (name or "").strip()
        if not name or len(name.split()) < 2:
            return None
        fields = {
            "txtOwnerName": name,
            "cmdSubmit": "Search",
            "AcctTypeCheckList1:chkAcctType:0": "on",
            "AcctTypeCheckList1:chkAcctType:1": "on",
            "AcctTypeCheckList1:chkAcctType:2": "on",
        }
        return self._post_and_parse_single_match(
            SEARCH_OWNER_URL, fields, f"owner {name!r}", verbose, retries
        )


def enrich_accounts(
    account_numbers: list[str],
    verbose: bool = True,
    delay_seconds: float = REQUEST_DELAY_SECONDS,
    checkpoint_path: "Path | None" = None,
    checkpoint_every: int = 200,
) -> dict:
    """Batch-enrich a list of (deduplicated) account numbers. Returns
    {account_number: enrichment_dict_or_None}.

    Sequential and single-threaded ON PURPOSE. Each lookup needs its own
    fresh GET+POST (see lookup_account's docstring -- token reuse across
    lookups is unreliable), which makes this ~0.9s/account -- 75-90min for
    a few thousand accounts. A concurrent version (thread pool, 6 workers)
    was tried and dropped: confirmed live 2026-08-26 that the county's
    server returns HTTP 403 Forbidden under concurrent load. This is a
    courtesy lookup tool, not a bulk crawler -- slow-and-correct beats
    fast-and-blocked (or worse, fast-and-silently-wrong).

    checkpoint_path: if given, results are written to this JSON file every
    `checkpoint_every` accounts, and any existing checkpoint at that path
    is loaded on start so a killed/crashed run resumes instead of
    re-querying accounts it already has an answer for (a full run already
    crashed once on an unhandled network error ~3500/5324 accounts in --
    losing 60+ minutes of progress to one timeout is not acceptable twice).
    lookup_account itself already retries transient network errors, so
    this checkpoint is a second line of defense, not the primary fix.
    """
    results: dict = {}
    if checkpoint_path is not None and checkpoint_path.exists():
        try:
            results = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if verbose:
                print(f"  [DCAD] resuming from checkpoint: {len(results)} accounts already done", flush=True)
        except (json.JSONDecodeError, OSError):
            results = {}

    dcad = DCADSession()
    matched = sum(1 for v in results.values() if v)
    done = len(results)
    total = len(account_numbers)
    for i, acct in enumerate(account_numbers):
        if acct in results:
            continue
        try:
            result = dcad.lookup_account(acct, verbose=verbose)
        except Exception as exc:  # noqa: BLE001 -- must never take down the batch
            if verbose:
                print(f"  [DCAD] unexpected error for {acct}, treating as no-match: {exc}", flush=True)
            result = None
        results[acct] = result
        done += 1
        if result:
            matched += 1
        if verbose and done % 500 == 0:
            print(f"  [DCAD] {done}/{total} looked up, {matched} matched so far", flush=True)
        if checkpoint_path is not None and done % checkpoint_every == 0:
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_path.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
        if delay_seconds:
            time.sleep(delay_seconds)

    if checkpoint_path is not None:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    if verbose:
        print(f"  [DCAD] done: {matched}/{total} accounts matched", flush=True)
    return results


# ---------------------------------------------------------------------------
# CLI -- standalone cache-building entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "DCAD account-number parcel enrichment. Reads a JSON list of "
            "account numbers from --accounts-file (one array, or one "
            "account per line in a .txt file) and writes an enrichment "
            "cache JSON: {account_number: {situs_address, situs_city, "
            "owner_name, assessed_value, property_type} | null}."
        )
    )
    parser.add_argument("--accounts-file", required=True,
                         help="Path to a .json array or .txt (one per line) of account numbers.")
    parser.add_argument("--out-file", required=True,
                         help="Path to write the enrichment cache JSON.")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY_SECONDS)
    args = parser.parse_args()

    accounts_path = Path(args.accounts_file)
    if accounts_path.suffix == ".json":
        accounts = json.loads(accounts_path.read_text(encoding="utf-8"))
    else:
        accounts = [
            line.strip() for line in accounts_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    print(f"[DCAD] enriching {len(accounts)} unique accounts...", flush=True)
    results = enrich_accounts(accounts, verbose=True, delay_seconds=args.delay)

    out_path = Path(args.out_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[DCAD] wrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
