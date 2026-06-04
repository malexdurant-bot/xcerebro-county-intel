"""
Bexar County tax-foreclosure SALE scraper — LGBS (taxsales.lgbs.com).

County-side scraper (Bexar). Pulls the public Linebarger Goggan Blair &
Sampson tax-sale JSON API (Django-REST, no auth, no CAPTCHA) and writes
§4.32-wrapped raw records to data/raw/tax_sale_lgbs.jsonl for the
`tax_sale_lgbs` translator.

Idempotent by `uid`: the API returns the current active sale set, so each run
REWRITES the file (atomic tmp+replace) rather than appending — there is no
overlap cursor and therefore no duplicate-evidence problem. Freshness comes
from re-pulling (sale lists rotate ahead of the monthly first-Tuesday sale).

Recon + field shape: runs/bexar_tx/recon/tax_sale_lgbs_recon.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]

SOURCE_ID = "tax_sale_lgbs"
API_URL = "https://taxsales.lgbs.com/api/property_sales/"
COUNTY = "BEXAR COUNTY"
STATE = "TX"
SALE_TYPES = "SALE,RESALE,STRUCK OFF,FUTURE SALE"
# Bexar County bounding box (lon,lat) from /api/venues suggest bounds.
BBOX = "-98.806,29.114,-98.117,29.760"
ORDERING = "precinct,sale_nbr,uid"
PAGE_LIMIT = 100
USER_AGENT = "xcerebro-county-intel/tax_sale_lgbs (research; operator-contactable)"
REQUEST_DELAY_SECONDS = 1.0
RETRIES = [5, 30, 120]

DEFAULT_OUT = REPO_ROOT / "data" / "raw" / "tax_sale_lgbs.jsonl"
DEFAULT_RUNS_DIR = REPO_ROOT / "data" / "raw" / "tax_sale_lgbs_runs"


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _now_iso() -> str:
    return _iso(datetime.now(timezone.utc))


def _first_page_url() -> str:
    qs = urllib.parse.urlencode({
        "county": COUNTY, "state": STATE, "sale_type": SALE_TYPES,
        "in_bbox": BBOX, "ordering": ORDERING, "limit": PAGE_LIMIT,
    })
    return f"{API_URL}?{qs}"


def _http_get_json(url: str, *, fetch_fn: Callable[[str], str] | None = None,
                   sleep_fn: Callable[[float], None] = time.sleep) -> dict:
    """GET a JSON page with retry/backoff. `fetch_fn` injectable for tests."""
    if fetch_fn is not None:
        return json.loads(fetch_fn(url))
    last_err = None
    for attempt, delay in enumerate([0] + RETRIES):
        if delay:
            sleep_fn(delay)
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 — retry any transport/parse error
            last_err = exc
    raise RuntimeError(f"GET failed after {len(RETRIES)} retries: {url} :: {last_err}")


def fetch_all(*, fetch_fn: Callable[[str], str] | None = None,
              max_features: int | None = None,
              sleep_fn: Callable[[float], None] = time.sleep) -> tuple[list[dict], int]:
    """Follow DRF `next` pagination; return (records, pages_fetched)."""
    url = _first_page_url()
    out: list[dict] = []
    pages = 0
    while url:
        page = _http_get_json(url, fetch_fn=fetch_fn, sleep_fn=sleep_fn)
        pages += 1
        out.extend(page.get("results") or [])
        if max_features and len(out) >= max_features:
            return out[:max_features], pages
        url = page.get("next")
        if url:
            sleep_fn(REQUEST_DELAY_SECONDS)
    return out, pages


def _wrap(rec: dict, fetched_at: str) -> dict:
    """§4.32 wrapped envelope. raw_payload is the API record verbatim;
    field-name bridging to canonical translator names is done in the
    translator's field_map (county/vendor specifics stay in config)."""
    uid = rec.get("uid")
    return {
        "raw_record_id": f"{SOURCE_ID}_{uid}",
        "source_id": SOURCE_ID,
        "source_url": f"{API_URL}?uid={uid}",
        "source_fetched_at": fetched_at,
        "parser_confidence": 98,
        "raw_payload": rec,
    }


def run(*, output_path: Path | None = None, runs_dir: Path | None = None,
        max_features: int | None = None,
        fetch_fn: Callable[[str], str] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep) -> dict:
    """Run the scraper end-to-end. Returns the run-metadata dict."""
    output_path = output_path or DEFAULT_OUT
    runs_dir = runs_dir or DEFAULT_RUNS_DIR
    output_path.parent.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)

    started = datetime.now(timezone.utc)
    run_id = started.strftime("%Y%m%dT%H%M%S") + "_full_pull"
    fetched_at = _iso(started)

    records, pages = fetch_all(
        fetch_fn=fetch_fn, max_features=max_features, sleep_fn=sleep_fn)

    # Idempotent by uid (last occurrence wins) — no append, no overlap.
    by_uid: dict = {}
    for r in records:
        uid = r.get("uid")
        if uid is not None:
            by_uid[uid] = r
    wrapped = [_wrap(r, fetched_at) for r in by_uid.values()]

    tmp = output_path.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for w in wrapped:
            fh.write(json.dumps(w, ensure_ascii=False) + "\n")
    tmp.replace(output_path)

    finished = datetime.now(timezone.utc)
    status = "success" if wrapped else "no_results"
    meta = {
        "run_id": run_id,
        "source_id": SOURCE_ID,
        "api_url": API_URL,
        "county": COUNTY,
        "pages_fetched": pages,
        "records_fetched": len(records),
        "unique_uids": len(by_uid),
        "records_written": len(wrapped),
        "sale_type_distribution": dict(Counter(r.get("sale_type") for r in by_uid.values())),
        "status_distribution": dict(Counter(r.get("status") for r in by_uid.values())),
        "run_started_at": fetched_at,
        "run_finished_at": _iso(finished),
        "run_duration_seconds": round((finished - started).total_seconds(), 3),
        "status": status,
        "output_path": str(output_path),
    }
    (runs_dir / f"{run_id}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pull Bexar County tax-foreclosure sales from LGBS (taxsales.lgbs.com).")
    parser.add_argument("--out", default=None,
                        help="Output JSONL path. Default: data/raw/tax_sale_lgbs.jsonl")
    parser.add_argument("--max-features", type=int, default=None,
                        help="Cap on records pulled (testing).")
    args = parser.parse_args(argv)
    meta = run(output_path=Path(args.out) if args.out else None,
               max_features=args.max_features)
    print(json.dumps(meta, indent=2))
    return 0 if meta["status"] in ("success", "no_results") else 1


if __name__ == "__main__":
    raise SystemExit(main())
