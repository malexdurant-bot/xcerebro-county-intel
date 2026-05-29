"""
Bexar County parcel-master adapter (BCAD).

Pulls the BCAD-style parcel records from the public ArcGIS layer at:

    https://maps.bexar.org/arcgis/rest/services/Parcels/MapServer/0

This layer carries the full appraisal field set (Owner, mailing
address, LandVal/ImprVal/TotVal, YrBlt, Exempts, PropUse, LglAcres,
LglDesc). See `runs/bexar_tx/operator_notes.md#parcel_master` for the
recon details that motivate the bulk-ArcGIS-pull access pattern over
per-parcel Harris Govern lookups.

Strategy
--------
The full BCAD layer is ~700k records. Phase 4 only needs the parcels
that intersect the foreclosure-notices set (288 addresses, ~12 unique
ZIPs). The scraper:

  1. Reads `data/raw/foreclosure_notices_map.jsonl` to derive the
     target ZIP set + the unique (address, city, zip) tuples we need
     to resolve.
  2. For each ZIP in the target set, paginates the BCAD ArcGIS layer
     with `WHERE Zip = '<zip>'` capturing every parcel in that ZIP.
  3. Normalizes each feature into a framework parcel-master record
     (situs_address normalized to single-space, owner_name as-is,
     mailing fields collapsed from AddrLn1-3, value fields preserved,
     exemption + property-class flags carried through).
  4. Writes `data/raw/parcel_master.jsonl` (one line per BCAD parcel).

Per-parcel matching against foreclosure addresses is the matcher's job
(scaffold/pipeline/matcher.py) — this adapter only pulls the BCAD
ground truth for the matcher to join against.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scaffold.scrapers._arcgis_featureserver import (  # noqa: E402
    ArcGISFeatureServer,
)


SOURCE_ID = "parcel_master"
SERVICE_URL = (
    "https://maps.bexar.org/arcgis/rest/services/Parcels/MapServer"
)
LAYER_ID = 0
USER_AGENT = "xcerebro-bexar-parcel-master/0.1 (+private repo)"

_WHITESPACE = re.compile(r"\s+")


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def normalize_address(s: str) -> str:
    """Collapse multiple whitespace runs and strip trailing space.

    BCAD's `Situs` field uses double-spaces (e.g. `"3795  MOUNT OLIVE RD "`)
    while the foreclosure map uses single-space (`"3795 MOUNT OLIVE RD"`).
    The matcher needs a single canonical form."""
    if not s:
        return ""
    return _WHITESPACE.sub(" ", s).strip().upper()


def _parcel_record(feature: dict) -> dict:
    a = feature.get("attributes") or {}
    situs_raw = a.get("Situs") or ""
    addr1 = a.get("AddrLn1") or ""
    addr2 = a.get("AddrLn2") or ""
    addr3 = a.get("AddrLn3") or ""
    # BCAD writes the literal string "NULL" instead of null in some
    # AddrLn fields. Filter those out.
    mailing_lines = [
        x.strip() for x in (addr1, addr2, addr3)
        if x and x.strip() and x.strip().upper() != "NULL"
    ]

    yrblt_raw = a.get("YrBlt") or ""
    yrblt = None
    if yrblt_raw and str(yrblt_raw).upper() != "NULL":
        try:
            yrblt = int(str(yrblt_raw).strip())
        except (ValueError, TypeError):
            yrblt = None

    parcel_id = f"BCAD-{int(a['PropID']):08d}" if a.get("PropID") else None
    situs_norm = normalize_address(situs_raw)

    return {
        "parcel_id": parcel_id,
        "bcad_prop_id": int(a["PropID"]) if a.get("PropID") else None,
        "situs_address": situs_norm,
        "situs_address_raw": situs_raw,
        "situs_city": (a.get("AddrCity") or "").strip(),
        "situs_state": "TX",
        "situs_zip": (a.get("Zip") or "").strip(),
        "situs_zip4": (a.get("Zip4") or "").strip(),
        "owner_name": (a.get("Owner") or "").strip(),
        "owner_mailing_addr1": " ".join(mailing_lines) if mailing_lines else "",
        "owner_mailing_city": (a.get("AddrCity") or "").strip(),
        "owner_mailing_state": (a.get("AddrSt") or "").strip(),
        "owner_mailing_zip": (a.get("Zip") or "").strip(),
        "owner_mailing_country": (a.get("Country") or "").strip(),
        "year_built": yrblt,
        "land_value": float(a["LandVal"]) if a.get("LandVal") is not None else None,
        "improvement_value": float(a["ImprVal"]) if a.get("ImprVal") is not None else None,
        "assessed_value": float(a["TotVal"]) if a.get("TotVal") is not None else None,
        "property_class": (a.get("PropUse") or "").strip(),
        "state_class_code": (a.get("State_cd") or "").strip(),
        "neighborhood": (a.get("Nbhd") or "").strip(),
        "exemptions": (a.get("Exempts") or "").strip(),
        "exempt_homestead": _has_exempt(a.get("Exempts"), "HS"),
        "exempt_over_65": _has_exempt(a.get("Exempts"), "OV65"),
        "exempt_disabled": _has_exempt(a.get("Exempts"), "DV") or _has_exempt(a.get("Exempts"), "DP"),
        "legal_description": (a.get("LglDesc") or "").strip(),
        "lgl_acres": float(a["LglAcres"]) if a.get("LglAcres") is not None else None,
        "acres": float(a["Acres"]) if a.get("Acres") is not None else None,
        "is_udi": (a.get("IS_UDI") or "").strip(),
        "udi_parent": int(a["UDIPARNT"]) if a.get("UDIPARNT") is not None else None,
        "roll": (a.get("Roll") or "").strip(),
        # Fields we cannot resolve from this source — set to None so the
        # framework knows they're missing rather than empty.
        "last_sale_date": None,
        "last_sale_price": None,
        # Bookkeeping.
        "_source_id": SOURCE_ID,
        "_object_id": a.get("OBJECTID"),
        "_fetched_at": _now_iso(),
    }


def _wrap_record(rec: dict) -> dict:
    """Wrap a flat parcel record in the §4.32 raw envelope the canonical
    translator contract expects (raw_payload + provenance). Previously this
    was a one-time external transform (v5.1.2-beta-r3 Step 5''); doing it in
    the scraper makes every re-pull translator-ready and removes the footgun
    where a fresh pull silently broke the parcel_master translator. The
    per-source field_map in the county config bridges these flat field names
    (situs_address, etc.) to the translator's canonical names — that bridge is
    unchanged because the flat fields now live inside raw_payload."""
    pid = rec.get("parcel_id") or rec.get("_object_id")
    return {
        "raw_record_id": f"raw_{pid}",
        "source_id": rec.get("_source_id") or SOURCE_ID,
        "source_url": f"{SERVICE_URL}/0",
        "source_fetched_at": rec.get("_fetched_at"),
        "parser_confidence": 100,
        "raw_payload": rec,
    }


def _has_exempt(exempt_str: str | None, token: str) -> bool:
    if not exempt_str:
        return False
    return bool(re.search(rf"\b{re.escape(token)}\b", exempt_str, re.IGNORECASE))


def _addresses_from_foreclosures(path: Path) -> list:
    """Read foreclosure raw records and return [(address, zip), ...] tuples."""
    if not path.exists():
        return []
    out: list = []
    seen: set = set()
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = rec.get("raw_payload") or {}
            addr = (payload.get("address") or "").strip().upper()
            zip_code = (payload.get("zip") or "").strip()
            if not addr or not zip_code:
                continue
            key = (addr, zip_code)
            if key in seen:
                continue
            seen.add(key)
            out.append({"address": addr, "zip": zip_code})
    return out


def _house_number(address: str) -> str:
    """Extract the leading integer-or-fraction from an address."""
    m = re.match(r"\s*([0-9]+[A-Z]?(?:\s*-\s*[0-9]+[A-Z]?)?)", address.upper())
    return m.group(1).replace(" ", "") if m else ""


def _street_root(address: str) -> str:
    """Return the first significant street-name token (skip directionals)."""
    tokens = address.upper().split()
    if len(tokens) < 2:
        return ""
    rest = tokens[1:]
    # Skip a single directional prefix if present.
    if rest and rest[0] in {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}:
        rest = rest[1:]
    return rest[0] if rest else ""


_FIELD_LIST = (
    "OBJECTID,PropID,Situs,Owner,AddrLn1,AddrLn2,AddrLn3,"
    "AddrCity,AddrSt,Country,Zip,Zip4,LandVal,ImprVal,TotVal,"
    "Nbhd,YrBlt,State_cd,LglAcres,Acres,Exempts,IS_UDI,UDIPARNT,"
    "Roll,PropUse,LglDesc"
)


def fetch_for_zips(server: ArcGISFeatureServer, zips: Iterable[str],
                    *, max_features_per_zip: int | None = None) -> Iterable[dict]:
    """Yield BCAD parcel records for every parcel in the given ZIP set.

    Bulk ZIP pull. Kept for completeness / future enrichment runs.
    Phase 4 default path is fetch_for_addresses() which is much leaner.
    """
    for z in sorted(zips):
        if not z:
            continue
        where = f"Zip = '{z}'"
        for feat in server.iter_features(
            layer_id=LAYER_ID,
            where=where,
            out_fields=_FIELD_LIST,
            return_geometry=False,
            max_features=max_features_per_zip,
        ):
            yield _parcel_record(feat)


def fetch_for_addresses(server: ArcGISFeatureServer,
                         addresses: list,
                         *, batch_size: int = 20) -> Iterable[dict]:
    """Targeted fetch: for each (address, zip) pair build a single ArcGIS
    WHERE clause that limits to that ZIP and to addresses that look like
    the foreclosure address (matched by house number + first street token).

    Multi-parcel addresses are returned as multiple features, which is
    the matcher's responsibility to disambiguate.
    """
    # Group by ZIP so each ZIP-bound query batches addresses in that ZIP.
    by_zip: dict = {}
    for entry in addresses:
        by_zip.setdefault(entry["zip"], []).append(entry["address"])

    for zip_code in sorted(by_zip.keys()):
        addrs = by_zip[zip_code]
        # Build per-address LIKE clauses. Group into batches so we stay
        # well under the ArcGIS WHERE length limit.
        clauses: list = []
        for addr in addrs:
            num = _house_number(addr)
            root = _street_root(addr)
            if not num or not root:
                # Fall back to full-address LIKE.
                clauses.append(
                    f"Situs LIKE '%{addr.replace(chr(39), chr(39)+chr(39))}%'"
                )
                continue
            clauses.append(
                f"(Situs LIKE '{num} %' AND Situs LIKE '%{root}%')"
            )

        for i in range(0, len(clauses), batch_size):
            chunk = clauses[i:i + batch_size]
            where = f"Zip = '{zip_code}' AND (" + " OR ".join(chunk) + ")"
            for feat in server.iter_features(
                layer_id=LAYER_ID,
                where=where,
                out_fields=_FIELD_LIST,
                return_geometry=False,
            ):
                yield _parcel_record(feat)


def run(*, output_path: Path | None = None,
        mode: str = "targeted",
        target_zips: Iterable[str] | None = None,
        max_features_per_zip: int | None = None,
        fetch_fn=None) -> dict:
    output_path = output_path or REPO_ROOT / "data" / "raw" / "parcel_master.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    server = ArcGISFeatureServer(SERVICE_URL,
                                  user_agent=USER_AGENT,
                                  fetch_fn=fetch_fn)

    stats = {
        "source_id": SOURCE_ID,
        "service_url": SERVICE_URL,
        "mode": mode,
        "output_path": str(output_path),
    }

    if mode == "zip_bulk":
        if target_zips is None:
            target_zips = {
                e["zip"]
                for e in _addresses_from_foreclosures(
                    REPO_ROOT / "data" / "raw" / "foreclosure_notices_map.jsonl"
                )
            }
        if not target_zips:
            stats["error"] = "no target ZIPs; specify --zip or run foreclosure scraper first"
            return stats

        tmp = output_path.with_suffix(".jsonl.tmp")
        per_zip_counts: dict = {}
        count = 0
        with open(tmp, "w", encoding="utf-8") as fh:
            for rec in fetch_for_zips(server, target_zips,
                                       max_features_per_zip=max_features_per_zip):
                fh.write(json.dumps(_wrap_record(rec), ensure_ascii=False) + "\n")
                count += 1
                z = rec.get("situs_zip") or ""
                per_zip_counts[z] = per_zip_counts.get(z, 0) + 1
        tmp.replace(output_path)
        stats.update({
            "zips_targeted": len(target_zips),
            "zips_list": sorted(target_zips),
            "records_pulled": count,
            "records_per_zip": dict(sorted(per_zip_counts.items())),
        })
        return stats

    # default = targeted address-aware pull
    addrs = _addresses_from_foreclosures(
        REPO_ROOT / "data" / "raw" / "foreclosure_notices_map.jsonl"
    )
    if not addrs:
        stats["error"] = "no foreclosure addresses; run scrapers/foreclosure_notices_map.py first"
        return stats

    tmp = output_path.with_suffix(".jsonl.tmp")
    count = 0
    seen_parcel_ids: set = set()
    with open(tmp, "w", encoding="utf-8") as fh:
        for rec in fetch_for_addresses(server, addrs):
            pid = rec.get("parcel_id")
            if pid and pid in seen_parcel_ids:
                continue
            if pid:
                seen_parcel_ids.add(pid)
            fh.write(json.dumps(_wrap_record(rec), ensure_ascii=False) + "\n")
            count += 1
    tmp.replace(output_path)
    stats.update({
        "addresses_targeted": len(addrs),
        "records_pulled": count,
    })
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pull BCAD parcel-master records via the public ArcGIS "
                    "layer at maps.bexar.org/.../Parcels/MapServer/0."
    )
    parser.add_argument("--out", default=None,
                        help="Output JSONL path. Default: data/raw/parcel_master.jsonl")
    parser.add_argument("--mode", choices=["targeted", "zip_bulk"],
                        default="targeted",
                        help="targeted: only pull parcels matching foreclosure "
                             "addresses (default). zip_bulk: pull every parcel "
                             "in the foreclosure ZIP set (heavier, but useful "
                             "for downstream enrichment).")
    parser.add_argument("--zip", action="append", default=None,
                        help="Limit pull to specific ZIP(s). Repeat for "
                             "multiple. zip_bulk mode only.")
    parser.add_argument("--max-features-per-zip", type=int, default=None,
                        help="Cap on records pulled per ZIP (testing).")
    args = parser.parse_args()

    out = Path(args.out) if args.out else None
    target_zips = set(args.zip) if args.zip else None
    stats = run(output_path=out,
                mode=args.mode,
                target_zips=target_zips,
                max_features_per_zip=args.max_features_per_zip)
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
