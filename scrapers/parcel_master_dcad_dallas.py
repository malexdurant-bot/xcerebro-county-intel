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
_RESULT_ROW_RE = re.compile(
    r"<a href='AcctDetail\w*\.aspx\?ID=[^']*'[^>]*>\s*(?P<address>.*?)\s*</a>\s*"
    r"</td>\s*<td[^>]*>(?P<city>.*?)</td>\s*<td[^>]*>\s*<span[^>]*>(?P<owner>.*?)</span>\s*"
    r"</td>\s*<td[^>]*>\s*<span[^>]*>\$(?P<value>[\d,]*)</span>\s*"
    r"</td>\s*<td[^>]*>\s*<span[^>]*>(?P<proptype>.*?)</span>",
    re.DOTALL,
)


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

    def _fetch_tokens(self) -> dict:
        resp = self.session.get(SEARCH_URL, timeout=15)
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
        value = m.group("value")
        prop_type = _clean_html_text(m.group("proptype"))
        return {
            "situs_address": address or None,
            "situs_city": city or None,
            "owner_name": owner or None,
            "assessed_value": float(value.replace(",", "")) if value else None,
            "property_type": prop_type or None,
        }


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
