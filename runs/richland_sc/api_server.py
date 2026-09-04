"""
Richland County, SC — read-only leads API for external agents.

Serves the latest scored leads from runs/richland_sc/run_pipeline.py's
output over HTTP, gated by a static API key. Built so the client's own
agent can pull leads on a schedule and run its own skip-tracing, instead
of reading the dashboard by hand.

Deployment model: this process is meant to run on a small always-on host
(e.g. Render), separate from the Windows machine that actually scrapes
Richland County daily via Task Scheduler. Those two environments don't
share a filesystem, so the local pipeline PUSHES its output here after
each run via POST /richland/ingest, rather than this process reading a
local file that only exists on the scraping machine. See Step 5c in
run_pipeline.py.

Auth (two separate keys — different privilege, never share one for both):
  - RICHLAND_AGENT_API_KEY          — read-only. Give this to the client's
                                       agent as the `X-API-Key` header on
                                       GET requests.
  - RICHLAND_AGENT_API_INGEST_KEY   — write-only. Used only by our own
                                       local pipeline to push fresh data
                                       here after each run. Never hand
                                       this one to the client.

Local usage:
    pip install -r runs/richland_sc/requirements.txt
    python runs/richland_sc/api_server.py            # binds 127.0.0.1:8420
    python runs/richland_sc/api_server.py --host 0.0.0.0 --port 8420

Render deployment:
    Root directory: runs/richland_sc
    Build command:  pip install -r requirements.txt
    Start command:  python api_server.py --host 0.0.0.0 --port $PORT
    Env vars:       RICHLAND_AGENT_API_KEY, RICHLAND_AGENT_API_INGEST_KEY
                    (set both in Render's dashboard — do not commit them)
                    RICHLAND_DATA_DIR — REQUIRED for the deployed instance,
                    or every cold start silently wipes the dataset (see
                    DATA_DIR below). Attach a Render persistent disk
                    (Dashboard -> service -> Disks -> Add Disk, 1GB is
                    plenty), note its mount path (e.g. "/data"), and set
                    RICHLAND_DATA_DIR to that same path.

Endpoints:
    GET /health                    — no auth; liveness check
    GET /richland/meta             — auth; run summary (counts, generated_at)
    GET /richland/leads            — auth; full lead list (idempotent snapshot,
                                      does not affect the /new cursor)
        query params (all optional):
          min_score=<int>          — only leads with display_score >= N
          tier=<Hot|Strong|Workable|Low|Archive>
          pattern=<foreclosure|tax|lien|estate|lis_pendens>
          since=<ISO8601 datetime>  — only leads with a primary_event_date on
                                       or after this. NOTE: most Richland
                                       leads (estate/probate especially) have
                                       no primary_event_date, so this filter
                                       silently excludes them — use it only
                                       when you specifically want to filter
                                       leads that DO carry a filing date, not
                                       as a general "what's new" mechanism.
    GET /richland/leads/new        — auth; ONLY leads never delivered before
                                      through this endpoint. Marks every lead
                                      it returns as delivered (persisted to
                                      pipeline_output/richland_sc/
                                      _delivered_lead_ids.json), so a repeat
                                      call returns nothing until the next
                                      pipeline run adds genuinely new leads.
                                      This is the endpoint an automated
                                      poll-and-act workflow should use.
    POST /richland/ingest          — write-key auth (X-Ingest-Key); accepts
                                      the pipeline's full data.json payload
                                      as the request body and replaces the
                                      server's current dataset with it.
                                      Called by run_pipeline.py after each
                                      run, not by the client's agent.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
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
import hmac  # noqa: E402

from fastapi import Body, FastAPI, Header, HTTPException, Query  # noqa: E402

# RICHLAND_DATA_DIR: set this to a Render persistent disk's mount path
# (e.g. "/data") in production. Render's default container filesystem is
# ephemeral — every cold start (idle spin-down, redeploy, restart) wipes
# it, which silently resets both DATA_PATH and DELIVERED_PATH back to
# empty. Confirmed live 2026-09-04: a successful POST /richland/ingest the
# day before was gone after the very next cold start, reproducing the "No
# pipeline output yet" 503 even though the pipeline had already pushed
# real data. Left unset, this defaults to the same path as before
# (ephemeral, fine for local dev — NOT fine for the deployed instance
# until a disk is attached and this is pointed at its mount path).
DATA_DIR = Path(os.environ.get("RICHLAND_DATA_DIR") or (ROOT / "pipeline_output" / "richland_sc"))
DATA_PATH = DATA_DIR / "data.json"
DELIVERED_PATH = DATA_DIR / "_delivered_lead_ids.json"
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)

API_KEY = os.environ.get("RICHLAND_AGENT_API_KEY", "")
INGEST_KEY = os.environ.get("RICHLAND_AGENT_API_INGEST_KEY", "")

app = FastAPI(title="Richland SC Leads API", version="1.0")


def _check_key(x_api_key: Optional[str]) -> None:
    if not API_KEY:
        raise HTTPException(
            status_code=503,
            detail="RICHLAND_AGENT_API_KEY not configured on the server",
        )
    if not x_api_key or not hmac.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")


def _check_ingest_key(x_ingest_key: Optional[str]) -> None:
    if not INGEST_KEY:
        raise HTTPException(
            status_code=503,
            detail="RICHLAND_AGENT_API_INGEST_KEY not configured on the server",
        )
    if not x_ingest_key or not hmac.compare_digest(x_ingest_key, INGEST_KEY):
        raise HTTPException(status_code=401, detail="Missing or invalid X-Ingest-Key")


def _load_data() -> dict:
    if not DATA_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="No pipeline output yet — run runs/richland_sc/run_pipeline.py first",
        )
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def _load_delivered() -> set[str]:
    if not DELIVERED_PATH.exists():
        return set()
    return set(json.loads(DELIVERED_PATH.read_text(encoding="utf-8")))


def _save_delivered(ids: set[str]) -> None:
    DELIVERED_PATH.write_text(json.dumps(sorted(ids)), encoding="utf-8")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/richland/meta")
def meta(x_api_key: Optional[str] = Header(default=None)) -> dict:
    _check_key(x_api_key)
    data = _load_data()
    return {
        "generated_at": data.get("generated_at"),
        "build_label": data.get("build_label"),
        "mode": data.get("mode"),
        "county": data.get("county"),
        "state": data.get("state"),
        "lead_total": data.get("lead_total"),
        "score_tier_distribution": data.get("score_tier_distribution"),
        "pattern_counts": data.get("pattern_counts"),
        "data_file_modified_at": datetime.fromtimestamp(
            DATA_PATH.stat().st_mtime, tz=timezone.utc
        ).isoformat(),
    }


@app.get("/richland/leads")
def leads(
    x_api_key: Optional[str] = Header(default=None),
    min_score: Optional[int] = Query(default=None),
    tier: Optional[str] = Query(default=None),
    pattern: Optional[str] = Query(default=None),
    since: Optional[str] = Query(default=None),
) -> dict:
    _check_key(x_api_key)
    data = _load_data()
    records = data.get("records", [])

    if min_score is not None:
        records = [r for r in records if (r.get("display_score") or 0) >= min_score]
    if tier:
        records = [r for r in records if r.get("display_tier") == tier]
    if pattern:
        records = [r for r in records if pattern in (r.get("display_patterns") or [])]
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="since must be ISO 8601")
        records = [
            r for r in records
            if r.get("primary_event_date")
            and datetime.fromisoformat(r["primary_event_date"].replace("Z", "+00:00")) >= since_dt
        ]

    return {
        "generated_at": data.get("generated_at"),
        "county": data.get("county"),
        "state": data.get("state"),
        "count": len(records),
        "leads": records,
    }


@app.get("/richland/leads/new")
def leads_new(
    x_api_key: Optional[str] = Header(default=None),
    min_score: Optional[int] = Query(default=None),
    tier: Optional[str] = Query(default=None),
    pattern: Optional[str] = Query(default=None),
    dry_run: bool = Query(
        default=False,
        description="If true, return what WOULD be delivered without marking it delivered.",
    ),
) -> dict:
    """
    Delivery-cursor endpoint for polling workflows: returns only leads whose
    lead_id has never been returned by this endpoint before, then marks them
    delivered. A lead that's already gone out won't come back here even if
    the pipeline re-scores it — poll /richland/leads for the full picture.
    """
    _check_key(x_api_key)
    data = _load_data()
    records = data.get("records", [])
    delivered = _load_delivered()

    new_records = [r for r in records if r.get("lead_id") not in delivered]

    if min_score is not None:
        new_records = [r for r in new_records if (r.get("display_score") or 0) >= min_score]
    if tier:
        new_records = [r for r in new_records if r.get("display_tier") == tier]
    if pattern:
        new_records = [r for r in new_records if pattern in (r.get("display_patterns") or [])]

    if not dry_run and new_records:
        delivered.update(r["lead_id"] for r in new_records if r.get("lead_id"))
        _save_delivered(delivered)

    return {
        "generated_at": data.get("generated_at"),
        "county": data.get("county"),
        "state": data.get("state"),
        "count": len(new_records),
        "dry_run": dry_run,
        "leads": new_records,
    }


@app.post("/richland/ingest")
def ingest(
    payload: dict = Body(...),
    x_ingest_key: Optional[str] = Header(default=None),
) -> dict:
    """
    Replace the server's current dataset with a freshly-scraped payload.
    Called by run_pipeline.py after each run — expects the same shape as
    pipeline_output/richland_sc/data.json (generated_at, records, etc.).
    Does NOT touch the delivered-leads cursor, so /richland/leads/new keeps
    working correctly across ingests — genuinely new lead_ids in the new
    payload will still show up there; already-delivered ones won't repeat.
    """
    _check_ingest_key(x_ingest_key)
    if "records" not in payload:
        raise HTTPException(status_code=400, detail="payload missing 'records'")

    DATA_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return {
        "status": "ok",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "lead_total": len(payload.get("records", [])),
    }


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8420)))
    args = parser.parse_args()

    if not API_KEY:
        print(
            "[richland_sc api] WARNING: RICHLAND_AGENT_API_KEY not set — "
            "GET requests will 503 until it is."
        )
    if not INGEST_KEY:
        print(
            "[richland_sc api] WARNING: RICHLAND_AGENT_API_INGEST_KEY not set — "
            "POST /richland/ingest will 503 until it is."
        )

    uvicorn.run(app, host=args.host, port=args.port)
