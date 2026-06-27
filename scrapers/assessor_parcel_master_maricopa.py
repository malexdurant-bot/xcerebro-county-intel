"""
Maricopa County Assessor — Parcels MapServer adapter (Phase 2, enrichment).

Pulls parcel master records from the Maricopa County Assessor's ArcGIS
MapServer at:

    https://gis.mcassessor.maricopa.gov/arcgis/rest/services/Parcels/MapServer

Layer 0 ("Parcels") carries the full assessor field set: ownership,
mailing address, situs address, deed / sale data, land size, legal
description, assessed values, and jurisdiction fields.

This is an enrichment-only adapter — it does NOT generate leads. It is
designed to be run AFTER the lead-generating scrapers (e.g.
treasurer_tax_lien_maricopa.py) so it can enrich those records with
assessor parcel data.

Modes:
  targeted (default) — Reads APNs from all JSONL files in data/raw/
                        and fetches only those parcels. Efficient.
  bulk               — Queries with WHERE 1=1 (full refresh; expensive).
                       Use --max-features to cap during testing.

SSL note:
    gis.mcassessor.maricopa.gov had SSL verification issues during Phase 0
    recon. A custom _ssl_fetch() bypasses verification as a workaround.
    See _ssl_fetch() below for details.

Output: data/raw/assessor_parcel_master_maricopa.jsonl
_source_type: "enrichment"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Repo bootstrap
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scaffold.scrapers._arcgis_featureserver import (  # noqa: E402
    ArcGISFeatureServer,
    DEFAULT_BACKOFF_BASE_SECONDS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USER_AGENT,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOURCE_ID = "assessor_parcel_master"
SERVICE_URL = (
    "https://gis.mcassessor.maricopa.gov/arcgis/rest/services/Parcels/MapServer"
)
LAYER_ID = 0
USER_AGENT = "xcerebro-maricopa-assessor/0.1 (+private repo)"

# All confirmed field names from the live portal.
_FIELD_LIST = (
    "APN,FLOOR,BOOK,MAP,ITEM,APN_DASH,OWNER_NAME,"
    "MAIL_ADDR1,MAIL_ADDR2,MAIL_CITY,MAIL_STATE,MAIL_ZIP,MAIL_CNTRY,"
    "MAIL_ADDRESS,INCAREOF,"
    "PHYSICAL_STREET_NUM,PHYSICAL_STREET_DIR,PHYSICAL_STREET_NAME,"
    "PHYSICAL_STREET_TYPE,PHYSICAL_SUITE,PHYSICAL_CITY,PHYSICAL_ZIP,"
    "PHYSICAL_ADDRESS,PHYSICAL_STREET_SUFFIX,PHYSICAL_STREET_POSTDIR,"
    "DEED_NUMBER,DEED_DATE,SALE_DATE,SALE_PRICE,"
    "MCRNUM,SUBNAME,LAND_SIZE,LOT_NUM,STR,BLOCK,TRACT,"
    "CONST_YEAR,LIVING_SPACE,"
    "TAX_YR_CUR,FCV_CUR,LPV_CUR,TAX_YR_PREV,FCV_PREV,LPV_PREV,"
    "PUC,MCR_BOOK,MCR_PAGE,CITY_ZONING,JURISDICTION,"
    "LC_CUR,LC_PREV,LONGITUDE,LATITUDE"
)


# ---------------------------------------------------------------------------
# SSL-bypassing fetch (workaround for gis.mcassessor.maricopa.gov)
# ---------------------------------------------------------------------------


def _ssl_fetch(
    url: str,
    params: dict,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base: float = DEFAULT_BACKOFF_BASE_SECONDS,
) -> dict:
    """Fetch function with SSL verification disabled.

    gis.mcassessor.maricopa.gov presented SSL certificate issues during
    Phase 0 recon. This workaround disables hostname and certificate
    verification so the scraper can reach the endpoint. A Referer header
    matching the public parcel map portal is also added, as the server
    may check it.

    Signature matches _default_fetch() in scaffold/scrapers/_arcgis_featureserver.py
    so it can be passed as the fetch_fn argument to ArcGISFeatureServer.
    """
    qs = urllib.parse.urlencode(params, doseq=True)
    full_url = f"{url}?{qs}"

    # Build an unverified SSL context
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                full_url,
                headers={
                    "User-Agent": user_agent,
                    "Accept": "application/json",
                    "Referer": "https://maps.mcassessor.maricopa.gov",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            last_err = e
            if 400 <= e.code < 500 and e.code not in (408, 429):
                raise
            time.sleep(backoff_base * (2 ** attempt))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            time.sleep(backoff_base * (2 ** attempt))
    raise RuntimeError(
        f"Assessor fetch failed after {max_retries} attempts: {full_url}"
    ) from last_err


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _raw_record_id(apn: str) -> str:
    """Stable SHA1-based ID for an assessor parcel record across re-runs."""
    key = f"maricopa_assessor|{(apn or '').strip().upper()}"
    return "raw_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:24]


def _str_or_none(value) -> str | None:
    """Return stripped string, or None for empty / null values."""
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


# ---------------------------------------------------------------------------
# Feature normalization
# ---------------------------------------------------------------------------


def _parcel_record(feature: dict) -> dict:
    """Normalize one ArcGIS assessor feature into a raw framework record."""
    a = feature.get("attributes", {}) or {}
    now = _now_iso()

    apn = _str_or_none(a.get("APN"))
    owner_name = _str_or_none(a.get("OWNER_NAME"))

    raw_payload: dict = {
        # Parcel identifiers
        "apn": apn,
        "floor": _str_or_none(a.get("FLOOR")),
        "book": _str_or_none(a.get("BOOK")),
        "map": _str_or_none(a.get("MAP")),
        "item": _str_or_none(a.get("ITEM")),
        "apn_dash": _str_or_none(a.get("APN_DASH")),
        # Ownership
        "owner_name": owner_name,
        "incareof": _str_or_none(a.get("INCAREOF")),
        # Mailing address
        "mail_addr1": _str_or_none(a.get("MAIL_ADDR1")),
        "mail_addr2": _str_or_none(a.get("MAIL_ADDR2")),
        "mail_city": _str_or_none(a.get("MAIL_CITY")),
        "mail_state": _str_or_none(a.get("MAIL_STATE")),
        "mail_zip": _str_or_none(a.get("MAIL_ZIP")),
        "mail_cntry": _str_or_none(a.get("MAIL_CNTRY")),
        "mail_address": _str_or_none(a.get("MAIL_ADDRESS")),
        # Physical / situs address
        "physical_street_num": _str_or_none(a.get("PHYSICAL_STREET_NUM")),
        "physical_street_dir": _str_or_none(a.get("PHYSICAL_STREET_DIR")),
        "physical_street_name": _str_or_none(a.get("PHYSICAL_STREET_NAME")),
        "physical_street_type": _str_or_none(a.get("PHYSICAL_STREET_TYPE")),
        "physical_suite": _str_or_none(a.get("PHYSICAL_SUITE")),
        "physical_city": _str_or_none(a.get("PHYSICAL_CITY")),
        "physical_zip": _str_or_none(a.get("PHYSICAL_ZIP")),
        "physical_address": _str_or_none(a.get("PHYSICAL_ADDRESS")),
        "physical_street_suffix": _str_or_none(a.get("PHYSICAL_STREET_SUFFIX")),
        "physical_street_postdir": _str_or_none(a.get("PHYSICAL_STREET_POSTDIR")),
        # Deed / sale
        "deed_number": _str_or_none(a.get("DEED_NUMBER")),
        "deed_date": _str_or_none(a.get("DEED_DATE")),
        "sale_date": _str_or_none(a.get("SALE_DATE")),
        "sale_price": a.get("SALE_PRICE"),
        # MCR / subdivision
        "mcrnum": _str_or_none(a.get("MCRNUM")),
        "subname": _str_or_none(a.get("SUBNAME")),
        "land_size": a.get("LAND_SIZE"),
        "lot_num": _str_or_none(a.get("LOT_NUM")),
        "str": _str_or_none(a.get("STR")),
        "block": _str_or_none(a.get("BLOCK")),
        "tract": _str_or_none(a.get("TRACT")),
        # Construction / improvements
        "const_year": a.get("CONST_YEAR"),
        "living_space": a.get("LIVING_SPACE"),
        # Assessed values — current year
        "tax_yr_cur": a.get("TAX_YR_CUR"),
        "fcv_cur": a.get("FCV_CUR"),
        "lpv_cur": a.get("LPV_CUR"),
        # Assessed values — prior year
        "tax_yr_prev": a.get("TAX_YR_PREV"),
        "fcv_prev": a.get("FCV_PREV"),
        "lpv_prev": a.get("LPV_PREV"),
        # Classification / jurisdiction
        "puc": _str_or_none(a.get("PUC")),
        "mcr_book": _str_or_none(a.get("MCR_BOOK")),
        "mcr_page": _str_or_none(a.get("MCR_PAGE")),
        "city_zoning": _str_or_none(a.get("CITY_ZONING")),
        "jurisdiction": _str_or_none(a.get("JURISDICTION")),
        "lc_cur": _str_or_none(a.get("LC_CUR")),
        "lc_prev": _str_or_none(a.get("LC_PREV")),
        # Coordinates
        "longitude": a.get("LONGITUDE"),
        "latitude": a.get("LATITUDE"),
        # Bookkeeping
        "_source_type": "enrichment",
        "_object_id": feature.get("_object_id"),
    }

    source_url = f"https://mcassessor.maricopa.gov/mcs.php?q={apn}" if apn else ""

    return {
        "raw_record_id": _raw_record_id(apn or ""),
        "source_id": SOURCE_ID,
        "source_url": source_url,
        "source_fetched_at": now,
        "raw_payload": raw_payload,
        "raw_text": None,
        "first_seen_at": now,
        "last_seen_at": now,
        "change_status": "NEW_RECORD",  # rewritten by merge step
        "parser_confidence": 95 if (apn and owner_name) else 70,
    }


# ---------------------------------------------------------------------------
# APN discovery from lead JSONL files
# ---------------------------------------------------------------------------


def _apns_from_leads(repo_root: Path) -> list[str]:
    """Scan all JSONL files in data/raw/ for raw_payload.apn fields.

    Returns a deduplicated list of non-empty APN strings.  Any scraper
    that writes APN values under raw_payload.apn will automatically feed
    into this enrichment adapter.
    """
    raw_dir = repo_root / "data" / "raw"
    if not raw_dir.exists():
        return []
    seen: set[str] = set()
    out: list[str] = []
    for jsonl_path in sorted(raw_dir.glob("*.jsonl")):
        try:
            with open(jsonl_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = rec.get("raw_payload") or {}
                    apn = (payload.get("apn") or "").strip()
                    if apn and apn not in seen:
                        seen.add(apn)
                        out.append(apn)
        except OSError:
            continue
    return out


# ---------------------------------------------------------------------------
# Batch APN fetch
# ---------------------------------------------------------------------------


def _batch_fetch_by_apn(
    server: ArcGISFeatureServer,
    apns: list[str],
    *,
    batch_size: int = 50,
    max_features: int | None = None,
) -> Iterable[dict]:
    """Fetch assessor parcel records for the given list of APNs.

    APNs are batched into groups of up to batch_size and sent as
    APN_DASH IN ('XXX-XX-XXX', ...) WHERE clauses to the ArcGIS server.
    The database stores dash-formatted APNs in APN_DASH (e.g. '301-48-724');
    the raw APN field has no dashes ('30148724'). Pass dash-formatted APNs.
    Yields normalized raw records.
    """
    emitted = 0
    for i in range(0, len(apns), batch_size):
        chunk = apns[i : i + batch_size]
        apn_list = ", ".join(
            f"'{apn.replace(chr(39), chr(39) + chr(39))}'" for apn in chunk
        )
        where = f"APN_DASH IN ({apn_list})"
        for feat in server.iter_features(
            layer_id=LAYER_ID,
            where=where,
            out_fields=_FIELD_LIST,
            return_geometry=False,
            max_features=(max_features - emitted) if max_features else None,
        ):
            yield _parcel_record(feat)
            emitted += 1
            if max_features and emitted >= max_features:
                return


# ---------------------------------------------------------------------------
# Prior / merge
# ---------------------------------------------------------------------------


def _load_prior(path: Path) -> dict:
    """Map raw_record_id → prior record, for change tracking."""
    if not path.exists():
        return {}
    out: dict = {}
    with open(path, "r", encoding="utf-8") as fh:
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


def merge_with_prior(current: list, prior_by_id: dict) -> list:
    """Annotate each record with change_status against the prior run."""
    out = []
    current_ids = set()
    for rec in current:
        rid = rec["raw_record_id"]
        current_ids.add(rid)
        prev = prior_by_id.get(rid)
        if prev is None:
            rec["change_status"] = "NEW_RECORD"
        else:
            rec["first_seen_at"] = prev.get("first_seen_at", rec["first_seen_at"])
            rec["last_seen_at"] = rec["source_fetched_at"]
            if prev.get("raw_payload") == rec["raw_payload"]:
                rec["change_status"] = "SAME"
            else:
                rec["change_status"] = "UPDATED"
        out.append(rec)
    for rid, prev in prior_by_id.items():
        if rid in current_ids:
            continue
        prev = dict(prev)
        prev["change_status"] = "DISAPPEARED"
        out.append(prev)
    return out


# ---------------------------------------------------------------------------
# End-to-end runner
# ---------------------------------------------------------------------------


def run(
    *,
    output_path: Path | None = None,
    apns: list[str] | None = None,
    mode: str = "targeted",
    max_features: int | None = None,
) -> dict:
    """Run the enrichment adapter end-to-end. Returns a stats dict.

    Parameters
    ----------
    output_path:
        JSONL path to write. Defaults to
        data/raw/assessor_parcel_master_maricopa.jsonl.
    apns:
        Explicit APN list (overrides lead-file discovery in targeted mode).
    mode:
        "targeted" — collect APNs from data/raw/ lead files (or apns
                     parameter) then batch-fetch only those parcels.
        "bulk"     — query with WHERE 1=1 for a full refresh. Expensive;
                     use max_features to cap during testing.
    max_features:
        Cap on total records fetched (useful for testing both modes).
    """
    output_path = output_path or (
        REPO_ROOT / "data" / "raw" / "assessor_parcel_master_maricopa.jsonl"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    server = ArcGISFeatureServer(
        SERVICE_URL,
        user_agent=USER_AGENT,
        fetch_fn=_ssl_fetch,
    )

    errors: list[str] = []
    current: list[dict] = []

    if mode == "bulk":
        try:
            for feat in server.iter_features(
                layer_id=LAYER_ID,
                where="1=1",
                out_fields=_FIELD_LIST,
                return_geometry=False,
                max_features=max_features,
            ):
                current.append(_parcel_record(feat))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"bulk_fetch_error: {exc}")

        apns_targeted: str | list[str] = "bulk"
    else:
        # targeted mode
        if apns is not None:
            target_apns = [a.strip() for a in apns if a and a.strip()]
        else:
            target_apns = _apns_from_leads(REPO_ROOT)

        apns_targeted = target_apns

        if target_apns:
            try:
                for rec in _batch_fetch_by_apn(
                    server,
                    target_apns,
                    max_features=max_features,
                ):
                    current.append(rec)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"targeted_fetch_error: {exc}")
        else:
            errors.append(
                "no_apns: no APNs found in lead files; "
                "run lead scrapers first or pass --apn"
            )

    prior = _load_prior(output_path)
    merged = merge_with_prior(current, prior)

    tmp = output_path.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for rec in merged:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(output_path)

    stats: dict = {
        "source_id": SOURCE_ID,
        "service_url": SERVICE_URL,
        "mode": mode,
        "apns_targeted": apns_targeted,
        "records_pulled": len(current),
        "prior_count": len(prior),
        "total_after_merge": len(merged),
        "new_record_count": sum(
            1 for r in merged if r["change_status"] == "NEW_RECORD"
        ),
        "same_record_count": sum(
            1 for r in merged if r["change_status"] == "SAME"
        ),
        "updated_record_count": sum(
            1 for r in merged if r["change_status"] == "UPDATED"
        ),
        "disappeared_record_count": sum(
            1 for r in merged if r["change_status"] == "DISAPPEARED"
        ),
        "output_path": str(output_path),
        "errors": errors,
    }
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Maricopa County Assessor parcel enrichment adapter. "
            "Fetches full parcel-master records from the ArcGIS MapServer "
            "and writes them to JSONL for downstream enrichment of lead records."
        )
    )
    parser.add_argument(
        "--out",
        default=None,
        help=(
            "Output JSONL path. "
            "Default: data/raw/assessor_parcel_master_maricopa.jsonl"
        ),
    )
    parser.add_argument(
        "--apn",
        action="append",
        dest="apns",
        default=[],
        metavar="APN",
        help=(
            "APN to fetch (repeatable). "
            "Overrides lead-file APN discovery in targeted mode. "
            "Format: XXX-XX-XXXX"
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["targeted", "bulk"],
        default="targeted",
        help=(
            "targeted (default): fetch only APNs found in lead JSONL files "
            "(or supplied via --apn). "
            "bulk: query all parcels with WHERE 1=1 (expensive — use "
            "--max-features to cap)."
        ),
    )
    parser.add_argument(
        "--max-features",
        type=int,
        default=None,
        help="Cap on total records fetched (useful for testing).",
    )
    args = parser.parse_args()

    out = Path(args.out) if args.out else None
    stats = run(
        output_path=out,
        apns=args.apns if args.apns else None,
        mode=args.mode,
        max_features=args.max_features,
    )
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
