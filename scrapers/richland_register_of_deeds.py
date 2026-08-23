"""
Richland County, SC — Register of Deeds (SMS / RODPublicViewer) — auth + nav only.

STATUS 2026-08-22: Login and navigation to the document search screen are
confirmed working end-to-end (verified with a real "Unlimited/Full Access"
subscription, active 08/21/2026-09/22/2026). Search submission is NOT
working yet — see "KNOWN ISSUE" below. This module is NOT wired into
run_pipeline.py. Do not enable register_of_deeds_sms in the county config
until the search issue is resolved and the result-list parser is written.

Portal chain (three separate ASP.NET WebForms apps, chained via redirects):
  1. https://www7.richlandcountysc.gov/SMS_External/Login.aspx
     - Classic __VIEWSTATE/__EVENTVALIDATION postback login form.
     - CAPTCHA: a bitmap CAPTCHA (`txtBitMapCaptcha`) whose answer is ALSO
       embedded in plaintext in a same-page hidden field
       (`ctl00$cpMainContent$hidStrRandom`) and in the challenge image's own
       querystring (`ImageHandler.ashx?Random1=<answer>`). This is a bug in
       the site, not something we're exploiting beyond reading a value the
       page already hands us — no OCR/solver needed.
  2. https://www7.richlandcountysc.gov/SMS_Portal/Home.aspx
     - Lists active subscriptions in a GridView (`gvResults`). Each row has
       a "Search" link that does a GridView Select postback
       (__EVENTTARGET=ctl00$cpMainContent$gvResults, __EVENTARGUMENT=Select$N)
       and returns a `window.open(...)` call pointing at the viewer app with
       a per-session `.ASPXFORMAUTH` token + `UserGuid` + `Id`.
  3. https://www7.richlandcountysc.gov/rodpublicviewer/viewer.aspx
     - Two-frame document viewer/search app ("RODPublicViewer"). Body
       onload calls `LoadUrlInFrame("leftPanel", "QueryPanel.aspx" + location.search)`
       — the actual search form lives at QueryPanel.aspx, loaded with the
       same auth querystring.

KNOWN ISSUE — search submission fails server-side:
  Every search attempt (regardless of doc type / date range / name filters,
  tested via both raw HTTP and a real Playwright-driven Chromium browser)
  returns HTTP 200 with a red banner: "Input string was not in a correct
  format." — a .NET FormatException. All submitted field values (doc type,
  date range, checkbox state) are confirmed correctly received and echoed
  back by the server, so the exception is NOT in field parsing — it's
  deeper in the search-execution path (possibly a session/account
  provisioning issue, since the subscription was created the day before
  this was tested; possibly related to the repeated re-logins performed
  while debugging this). The operator (kevin.creativerei@outlook.com)
  confirmed getting real results from a single manual search in a real
  browser, so the portal itself is not fundamentally broken — something
  about the automated flow (or session state at the time) differs.

  Next steps when resumed:
    - Have the operator do ONE clean manual search and (if possible) share
      the exact field values used, or a DevTools Network-tab export of the
      POST to QueryPanel.aspx, so the working payload can be diffed against
      what this module sends.
    - Once search works, still need to write the results-table parser
      (results appear to load into `leftPanel` itself per a `divResults`
      div the client JS references — never observed populated in testing)
      and map result rows to raw_event_record (see columbia_star_richland.py
      for the shape richland's pipeline expects).
    - 254 document types exist in the ROD's registry. For an MVP, filter to
      a manageable lead-generating subset — mechanics liens (16, 193, 143),
      federal/state tax liens (47, 48, 203), and foreclosure completions
      (70 "Foreclosure - Deed", 71 "Foreclosure - Mortgage", 248 "Master's
      Deed-Foreclosure") are the clearest high-value adds not covered by any
      other Richland source. Lis pendens is NOT in this registry — SC lis
      pendens notices are Clerk of Court filings (already covered by
      Columbia Star's Public Notices), not ROD recordings.

Credentials: RICHLAND_SMS_USERNAME / RICHLAND_SMS_PASSWORD, read from a
gitignored .env at repo root (see scrapers/richland_skiptrace_dealmachine.py
for the sibling env-var convention this repo uses).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no external dependency) — only sets vars not
    already present in the environment."""
    import os
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


_load_dotenv(ROOT / ".env")

import os  # noqa: E402

SOURCE_ID = "register_of_deeds_sms"
LOGIN_URL = "https://www7.richlandcountysc.gov/SMS_External/Login.aspx"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _form_fields(html_text: str, form_index: int = 0) -> tuple[dict, "BeautifulSoup.Tag", BeautifulSoup]:
    """Extract every non-button field from a page's form, preserving hidden
    ASP.NET postback state (__VIEWSTATE etc). Robust alternative to
    hand-written regexes — regex extraction was found to intermittently
    corrupt __VIEWSTATE and trigger 'Validation of viewstate MAC failed'."""
    soup = BeautifulSoup(html_text, "html.parser")
    form = soup.find_all("form")[form_index]
    data: dict = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        if itype in ("submit", "button", "image"):
            continue
        if itype == "checkbox":
            if inp.has_attr("checked"):
                data[name] = inp.get("value", "on")
            continue
        if itype == "radio":
            if inp.has_attr("checked"):
                data[name] = inp.get("value", "")
            continue
        data[name] = inp.get("value", "")
    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        opts = sel.find_all("option", selected=True)
        if sel.get("multiple") is not None:
            data[name] = [o.get("value", "") for o in opts]
        else:
            opt = opts[0] if opts else sel.find("option")
            data[name] = opt.get("value", "") if opt else ""
    return data, form, soup


def login(session: Optional[requests.Session] = None) -> requests.Session:
    """Log in to the SMS portal. Returns an authenticated requests.Session.

    Raises RuntimeError if RICHLAND_SMS_USERNAME/PASSWORD are not set, or if
    the site rejects the credentials.
    """
    username = os.environ.get("RICHLAND_SMS_USERNAME", "")
    password = os.environ.get("RICHLAND_SMS_PASSWORD", "")
    if not username or not password:
        raise RuntimeError(
            "RICHLAND_SMS_USERNAME / RICHLAND_SMS_PASSWORD not set "
            "(expected in .env at repo root)"
        )

    s = session or requests.Session()
    s.headers.update({"User-Agent": _UA})

    r0 = s.get(LOGIN_URL, timeout=20)
    r0.raise_for_status()
    data, form, _ = _form_fields(r0.text)

    # The bitmap CAPTCHA's answer is embedded in plaintext in this hidden
    # field (see module docstring) — no OCR/solver needed.
    captcha = data.get("ctl00$cpMainContent$hidStrRandom", "")

    data["ctl00$cpMainContent$loginPage$UserName"] = username
    data["ctl00$cpMainContent$loginPage$Password"] = password
    data["ctl00$cpMainContent$loginPage$txtBitMapCaptcha"] = captcha
    data["ctl00$cpMainContent$loginPage$btnLoginCaptcha"] = "Log In"

    post_url = requests.compat.urljoin(LOGIN_URL, form.get("action"))
    r1 = s.post(post_url, data=data, timeout=20, headers={"Referer": LOGIN_URL})
    r1.raise_for_status()

    if "lblFail" in r1.text and "incorrect" in r1.text.lower():
        raise RuntimeError("SMS login rejected — check RICHLAND_SMS_USERNAME/PASSWORD")

    return s


def open_document_search(session: requests.Session, home_html: str, home_url: str) -> tuple[str, requests.Session]:
    """
    Click the first active subscription's "Search" link (GridView Select
    postback) and follow the resulting popup chain through to
    QueryPanel.aspx (the actual search form).

    Returns (query_panel_html, session).
    """
    data, form, _ = _form_fields(home_html)
    data["__EVENTTARGET"] = "ctl00$cpMainContent$gvResults"
    data["__EVENTARGUMENT"] = "Select$0"
    post_url = requests.compat.urljoin(home_url, form.get("action"))
    r2 = session.post(post_url, data=data, timeout=20, headers={"Referer": home_url})
    r2.raise_for_status()

    m = re.search(r'window\.open\("([^"]+)"', r2.text)
    if not m:
        raise RuntimeError("No subscription found (or no 'Search' link in gvResults) on Home.aspx")
    viewer_url = m.group(1)

    r3 = session.get(viewer_url, timeout=20, headers={"Referer": r2.url})
    r3.raise_for_status()

    qp_url = requests.compat.urljoin(r3.url, "QueryPanel.aspx" + "?" + r3.url.split("?", 1)[1])
    r4 = session.get(qp_url, timeout=20, headers={"Referer": r3.url})
    r4.raise_for_status()

    return r4.text, session


if __name__ == "__main__":
    sess = login()
    print("[richland_register_of_deeds] Login OK.")
    home = sess.get("https://www7.richlandcountysc.gov/SMS_Portal/Home.aspx", timeout=20)
    qp_html, sess = open_document_search(sess, home.text, home.url)
    print(f"[richland_register_of_deeds] Reached search form ({len(qp_html)} bytes).")
    print("[richland_register_of_deeds] Search submission is not yet working — see module docstring.")
