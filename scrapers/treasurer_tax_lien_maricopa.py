"""
Maricopa County Treasurer — Lien and Delinquent Tax Parcels adapter (Phase 2).

Pulls lien and delinquent-tax parcel records from the Maricopa County
Treasurer's ArcGIS FeatureServer at:

    https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services/ParcelLienDelinquent/FeatureServer

Three layers:
    0 — BadInvestment   (tax_lien_bad_investment)
    1 — UnsoldLien      (tax_lien_unsold)
    2 — DelinquentTax   (tax_delinquent_parcel)

Confirmed fields (all layers):
    OBJECTID, APN, APNDashSplit, PropertyFullStreetAddress,
    DeedNumber, DeedWebLink, AssessorWebLink, TreasurerWebLink,
    Shape__Area, Shape__Length

The adapter writes to data/raw/treasurer_tax_lien.jsonl using the
standard atomic tmp→replace pattern and returns a stats dict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
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
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOURCE_ID = "treasurer_tax_lien"
SERVICE_URL = (
    "https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services"
    "/ParcelLienDelinquent/FeatureServer"
)
USER_AGENT = "xcerebro-maricopa-tax-lien/0.1 (+private repo)"

LAYERS = [
    {"layer_id": 0, "layer_name": "BadInvestment",  "category_hint": "tax_lien_bad_investment"},
    {"layer_id": 1, "layer_name": "UnsoldLien",     "category_hint": "tax_lien_unsold"},
    {"layer_id": 2, "layer_name": "DelinquentTax",  "category_hint": "tax_delinquent_parcel"},
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _raw_record_id(layer_id: int, apn: str) -> str:
    """Stable SHA1-based ID for a tax-lien record across re-runs."""
    key = f"maricopa_tax_lien|{layer_id}|{(apn or '').strip().upper()}"
    return "raw_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:24]


def _normalize_feature(feature: dict, layer_meta: dict, now: str) -> dict:
    attrs = feature.get("attributes", {}) or {}

    apn = (attrs.get("APN") or "").strip()
    apn_dash = (attrs.get("APNDashSplit") or "").strip()
    property_address = (attrs.get("PropertyFullStreetAddress") or "").strip()
    deed_number = (attrs.get("DeedNumber") or "").strip() or None
    deed_web_link = (attrs.get("DeedWebLink") or "").strip() or None
    assessor_web_link = (attrs.get("AssessorWebLink") or "").strip() or None
    treasurer_web_link = (attrs.get("TreasurerWebLink") or "").strip() or None

    # Source URL: use TreasurerWebLink if present, else fallback to public URL
    if treasurer_web_link:
        source_url = treasurer_web_link
    else:
        source_url = f"https://treasurer.maricopa.gov/Parcel/Index/{apn}"

    raw_payload = {
        "apn": apn,
        "apn_dash": apn_dash,
        "situs_address": property_address,
        "deed_number": deed_number,
        "deed_web_link": deed_web_link,
        "assessor_web_link": assessor_web_link,
        "treasurer_web_link": treasurer_web_link,
        "layer_id": layer_meta["layer_id"],
        "layer_name": layer_meta["layer_name"],
        "category_hint": layer_meta["category_hint"],
        "object_id": feature["_object_id"],
        "geometry": feature.get("geometry"),
    }

    return {
        "raw_record_id": _raw_record_id(layer_meta["layer_id"], apn),
        "source_id": SOURCE_ID,
        "source_url": source_url,
        "source_fetched_at": now,
        "raw_payload": raw_payload,
        "raw_text": None,
        "first_seen_at": now,
        "last_seen_at": now,
        "change_status": "NEW_RECORD",  # rewritten by merge step
        "parser_confidence": 95 if (apn and property_address) else 70,
    }


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
    # Records present in prior but absent now are stale; preserve them
    # for TTL/expiry tracking downstream.
    for rid, prev in prior_by_id.items():
        if rid in current_ids:
            continue
        prev = dict(prev)
        prev["change_status"] = "DISAPPEARED"
        out.append(prev)
    return out


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def fetch_all(
    server: ArcGISFeatureServer,
    *,
    where: str = "1=1",
    max_features: int | None = None,
) -> Iterable[dict]:
    """Yield normalized raw records across all three lien layers."""
    now = _now_iso()
    for layer in LAYERS:
        for feat in server.iter_features(
            layer_id=layer["layer_id"],
            where=where,
            out_fields="*",
            return_geometry=True,
            max_features=max_features,
        ):
            yield _normalize_feature(feat, layer, now)


# ---------------------------------------------------------------------------
# End-to-end runner
# ---------------------------------------------------------------------------


def run(
    *,
    output_path: Path | None = None,
    where: str = "1=1",
    max_features: int | None = None,
    fetch_fn=None,
) -> dict:
    """Run the scraper end-to-end. Returns a stats dict."""
    output_path = output_path or (
        REPO_ROOT / "data" / "raw" / "treasurer_tax_lien.jsonl"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    server = ArcGISFeatureServer(
        SERVICE_URL,
        user_agent=USER_AGENT,
        fetch_fn=fetch_fn,
    )

    current = list(fetch_all(server, where=where, max_features=max_features))
    prior = _load_prior(output_path)
    merged = merge_with_prior(current, prior)

    # Per-layer counts from current records only (not DISAPPEARED)
    per_layer_counts: dict[str, int] = {}
    for rec in current:
        layer_name = (rec.get("raw_payload") or {}).get("layer_name", "unknown")
        per_layer_counts[layer_name] = per_layer_counts.get(layer_name, 0) + 1

    tmp = output_path.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for rec in merged:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(output_path)

    stats: dict = {
        "source_id": SOURCE_ID,
        "service_url": SERVICE_URL,
        "current_count": len(current),
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
        "per_layer_counts": per_layer_counts,
        "output_path": str(output_path),
    }
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Pull Maricopa County Treasurer lien and delinquent-tax parcel "
            "records via the ArcGIS FeatureServer REST API."
        )
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSONL path. Default: data/raw/treasurer_tax_lien.jsonl",
    )
    parser.add_argument(
        "--where",
        default="1=1",
        help="ArcGIS WHERE clause applied to every layer (e.g. \"APN_DASH='301-48-724'\").",
    )
    parser.add_argument(
        "--max-features",
        type=int,
        default=None,
        help="Cap on records pulled per layer (useful for testing).",
    )
    args = parser.parse_args()

    out = Path(args.out) if args.out else None
    stats = run(
        output_path=out,
        where=args.where,
        max_features=args.max_features,
    )
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
