"""
Probe: fetch one probate caseInfo.asp detail page and save the raw HTML.

Reads the first active record from data/raw/superior_court_probate.jsonl,
fetches the case_detail_url via urllib GET (no Playwright — detail pages are
direct GETs with no CAPTCHA), and writes the HTML to:
    runs/maricopa_az/pipeline_output/probate_detail_probe.html

Does NOT print the case number, URL, or any record content.
Reports only status + byte count.

Usage:
    python runs/maricopa_az/probe_probate_detail.py
"""

from __future__ import annotations

import json
import ssl
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
JSONL_PATH = REPO_ROOT / "data" / "raw" / "superior_court_probate.jsonl"
OUT_DIR = Path(__file__).parent / "pipeline_output"
OUT_HTML = OUT_DIR / "probate_detail_probe.html"

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
}


def _first_active_detail_url() -> str | None:
    if not JSONL_PATH.exists():
        return None
    with open(JSONL_PATH, encoding="utf-8") as fh:
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
            payload = rec.get("raw_payload") or {}
            url = payload.get("case_detail_url") or rec.get("source_url") or ""
            if url:
                return url
    return None


def main() -> None:
    detail_url = _first_active_detail_url()
    if not detail_url:
        print("ERROR: No active records with case_detail_url found in JSONL.")
        sys.exit(1)

    print(f"Fetching detail page... (URL redacted from transcript)")
    req = urllib.request.Request(detail_url, headers=_HEADERS)
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_SSL_CTX))
    try:
        with opener.open(req, timeout=30) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            html = resp.read().decode(charset, errors="replace")
            status = resp.status
    except Exception as exc:
        print(f"ERROR fetching detail page: {exc}")
        print("Playwright may be required. See probe_probate_detail_pw.py.")
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(html, encoding="utf-8")

    print(f"HTTP status:  {status}")
    print(f"Response size: {len(html)} bytes")
    print(f"Saved to:     {OUT_HTML}")
    print()
    print("Next step: run analyze_probate_detail_structure.py to inspect field layout.")


if __name__ == "__main__":
    main()
