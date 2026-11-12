"""
Clayton County, GA — bulk parcel index (ENRICHMENT ONLY).

Source: Clayton County GIS, TaxAssessor/Parcels MapServer layer 0 ("Tax Parcels")
    https://gis.claytoncountyga.gov/server/rest/services/TaxAssessor/Parcels/MapServer/0
Found 2026-11-12 behind the county's official ArcGIS Parcel Viewer (linked from
the Tax Assessor "Property Search Information" page). ~92,100 parcels,
maxRecordCount 2000, public query endpoint, no key. Each record carries owner,
mailing address, situs, appraised/assessed value, last sale, year built,
homestead code and a DELINQUENT flag; PROPCARD links the iasWorld record card.

This never originates a lead (§13.4.2). It:
  1. supplies the staged pipeline's enrichment_provider (parcel_id -> parcel), and
  2. resolves a parcel_id for primary events that only carry an address
     (evictions, judgments, probate) — attach_parcels().

The full layer is cached at data/raw/clayton_parcels/parcels.json and
refreshed when older than REFRESH_DAYS.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "raw" / "clayton_parcels"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_PATH = CACHE_DIR / "parcels.json"
REFRESH_DAYS = 7

LAYER = "https://gis.claytoncountyga.gov/server/rest/services/TaxAssessor/Parcels/MapServer/0"
FIELDS = ("PARCELID,OWNERNME,SITEADDRES,SITECITY,SITEZIP5,PSTLADDRES,PSTLCITY,PSTLSTATE,PSTLZIP5,"
          "YEARBUILT,SQRFT,BED,BATH,APPRVAL,ASSESSVAL,SALEDATE,SALEPRICE,HOMESTEADEXEMPTCDE,"
          "DELINQUENT,LANDUSED,STRUCTYPE,APARTMENTS,PROPCARD,OBJECTID")
PAGE = 2000

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0"})


# ---------------------------------------------------------------------------
# Download / cache
# ---------------------------------------------------------------------------

def download() -> list[dict]:
    out, offset = [], 0
    while True:
        r = _SESSION.get(LAYER + "/query", params={
            "where": "1=1", "outFields": FIELDS, "returnGeometry": "false",
            "orderByFields": "OBJECTID", "resultOffset": offset, "resultRecordCount": PAGE,
            "f": "json"}, timeout=90)
        r.raise_for_status()
        j = r.json()
        if "error" in j:
            raise RuntimeError(j["error"])
        feats = [f["attributes"] for f in j.get("features", [])]
        out += feats
        if len(feats) < PAGE and not j.get("exceededTransferLimit"):
            break
        offset += len(feats)
        time.sleep(0.5)
    return out


def load(force: bool = False) -> list[dict]:
    fresh = CACHE_PATH.exists() and (
        datetime.now() - datetime.fromtimestamp(CACHE_PATH.stat().st_mtime) < timedelta(days=REFRESH_DAYS))
    if fresh and not force:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    rows = download()
    CACHE_PATH.write_text(json.dumps(rows), encoding="utf-8")
    print(f"[clayton_parcels] cached {len(rows)} parcels -> {CACHE_PATH}")
    return rows


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_SUFFIX = {"ROAD": "RD", "STREET": "ST", "DRIVE": "DR", "AVENUE": "AVE", "COURT": "CT", "CIRCLE": "CIR",
           "LANE": "LN", "PLACE": "PL", "TERRACE": "TER", "BOULEVARD": "BLVD", "PARKWAY": "PKWY",
           "TRAIL": "TRL", "WAY": "WAY", "HIGHWAY": "HWY", "COVE": "CV", "POINT": "PT", "RIDGE": "RDG",
           "CROSSING": "XING", "SQUARE": "SQ", "PASS": "PASS", "RUN": "RUN", "LOOP": "LOOP"}
_DIRS = {"NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W", "NORTHEAST": "NE", "NORTHWEST": "NW",
         "SOUTHEAST": "SE", "SOUTHWEST": "SW"}
_UNIT = re.compile(r"\b(APT|APARTMENT|UNIT|STE|SUITE|BLDG|LOT|#)\b.*$")


def norm_parcel(p: Optional[str]) -> Optional[str]:
    return re.sub(r"\s+", " ", p.strip().upper()) if p and p.strip() else None


def norm_addr(a: Optional[str]) -> Optional[str]:
    if not a:
        return None
    s = a.upper().replace(".", " ").replace(",", " ")
    s = _UNIT.sub("", s)
    toks = [_SUFFIX.get(t, _DIRS.get(t, t)) for t in s.split()]
    s = " ".join(toks).strip()
    return s if re.match(r"^\d+\s+\S", s) else None


def addr_key_loose(a: Optional[str]) -> Optional[str]:
    """house number + first street word — tolerant of suffix differences."""
    n = norm_addr(a)
    if not n:
        return None
    t = n.split()
    return f"{t[0]} {t[1]}" if len(t) > 1 else None


_NAME_NOISE = re.compile(r"\b(LLC|INC|LP|LLLP|ETAL|ET AL|JR|SR|II|III|IV|TRUST|TRUSTEE|ESTATE|OF|THE|AND|&|DBA)\b")


def name_tokens(n: Optional[str]) -> set[str]:
    if not n:
        return set()
    s = _NAME_NOISE.sub(" ", re.sub(r"[^A-Z0-9 &]", " ", n.upper()))
    return {t for t in s.split() if len(t) > 1}


def names_match(a: Optional[str], b: Optional[str]) -> bool:
    """Surname-level match: share at least one meaningful token of >=3 chars."""
    ta, tb = name_tokens(a), name_tokens(b)
    return any(len(t) >= 3 for t in ta & tb)


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

class ParcelIndex:
    def __init__(self, rows: list[dict]):
        self.by_parcel: dict[str, dict] = {}
        self.by_addr: dict[str, list[dict]] = {}
        self.by_addr_loose: dict[str, list[dict]] = {}
        for r in rows:
            pid = norm_parcel(r.get("PARCELID"))
            if not pid:
                continue
            self.by_parcel[pid] = r
            k = norm_addr(r.get("SITEADDRES"))
            if k:
                self.by_addr.setdefault(k, []).append(r)
            lk = addr_key_loose(r.get("SITEADDRES"))
            if lk:
                self.by_addr_loose.setdefault(lk, []).append(r)

    def get(self, parcel_id: Optional[str]) -> Optional[dict]:
        return self.by_parcel.get(norm_parcel(parcel_id) or "")

    def find_by_address(self, street: Optional[str], city_zip: Optional[str] = None) -> Optional[dict]:
        k = norm_addr(street)
        cands = self.by_addr.get(k or "", [])
        if not cands:
            cands = self.by_addr_loose.get(addr_key_loose(street) or "", [])
        if not cands:
            return None
        if len(cands) == 1:
            return cands[0]
        z = re.search(r"\b(\d{5})\b", city_zip or "")
        if z:
            zc = [c for c in cands if (c.get("SITEZIP5") or "").strip() == z.group(1)]
            if len(zc) == 1:
                return zc[0]
        return None  # ambiguous — don't guess

    @staticmethod
    def to_enrichment(r: dict) -> dict:
        def num(x):
            try:
                return float(str(x).replace(",", "")) if x not in (None, "") else None
            except ValueError:
                return None
        sd = (r.get("SALEDATE") or "").strip()
        sale_date = f"{sd[:4]}-{sd[4:6]}-{sd[6:8]}" if re.fullmatch(r"\d{8}", sd) else None
        return {
            "parcel_id": norm_parcel(r.get("PARCELID")),
            "owner_name": (r.get("OWNERNME") or "").strip() or None,
            "address": (r.get("SITEADDRES") or "").strip() or None,
            "situs_address": (r.get("SITEADDRES") or "").strip() or None,
            "situs_city": (r.get("SITECITY") or "").strip() or None,
            "situs_state": "GA",
            "situs_zip": (r.get("SITEZIP5") or "").strip() or None,
            "owner_mailing_address": (r.get("PSTLADDRES") or "").strip() or None,
            "owner_mailing_city": (r.get("PSTLCITY") or "").strip() or None,
            "owner_mailing_state": (r.get("PSTLSTATE") or "").strip() or None,
            "owner_mailing_zip": (r.get("PSTLZIP5") or "").strip() or None,
            "assessed_value": num(r.get("APPRVAL")),
            "last_sale_price": num(r.get("SALEPRICE")),
            "last_sale_date": sale_date,
            "year_built": int(r["YEARBUILT"]) if str(r.get("YEARBUILT") or "").isdigit() else None,
            "property_class": (r.get("LANDUSED") or "").strip() or None,
            "homestead": bool((r.get("HOMESTEADEXEMPTCDE") or "").strip()),
            "tax_delinquent_flag": (r.get("DELINQUENT") or "").strip().upper() == "YES",
            "record_card_url": r.get("PROPCARD"),
        }


_INDEX: Optional[ParcelIndex] = None


def get_index() -> ParcelIndex:
    global _INDEX
    if _INDEX is None:
        _INDEX = ParcelIndex(load())
    return _INDEX


def make_enrichment_provider():
    idx = get_index()

    def _provider(parcel_id: Optional[str]) -> Optional[dict]:
        r = idx.get(parcel_id)
        return ParcelIndex.to_enrichment(r) if r else None
    return _provider


# ---------------------------------------------------------------------------
# Parcel resolution for address-only primary events
# ---------------------------------------------------------------------------

def attach_parcels(raw_events: list[dict]) -> dict[str, dict]:
    """Fill property_refs.parcel_id from the address where it is missing.

    Rules per doc type (an unmatched event is kept, just unenriched):
      eviction_filing     — rental address -> parcel. When the parcel resolves
                            but its owner of record is NOT the plaintiff, the
                            event is marked `_drop_reason` (manager / roommate /
                            institutional filer — not a landlord lead).
      judgment_lien       — defendant address -> parcel ONLY when the owner of
                            record shares a surname with the defendant (a
                            judgment against a renter is not a property lead).
      probate family      — decedent address -> parcel; owner/decedent surname
                            agreement recorded as `owner_matches_party`.
    """
    idx = get_index()
    stats: dict[str, dict] = {}
    for e in raw_events:
        refs = e.setdefault("property_refs", {})
        dt = e.get("canonical_doc_type")
        s = stats.setdefault(dt, {"needed": 0, "matched": 0, "rejected_owner_mismatch": 0})
        if refs.get("parcel_id"):
            if idx.get(refs["parcel_id"]):
                refs["parcel_id"] = norm_parcel(refs["parcel_id"])
            continue
        if not refs.get("situs_address"):
            continue
        s["needed"] += 1
        hit = idx.find_by_address(refs["situs_address"], refs.get("situs_city_state_zip"))
        if not hit:
            continue
        owner = hit.get("OWNERNME")
        if dt == "eviction_filing":
            party = next((p["name"] for p in e.get("parties", []) if p.get("name_type") == "PL"), None)
        elif dt == "judgment_lien":
            party = next((p["name"] for p in e.get("parties", []) if p.get("name_type") == "DF"), None)
        else:
            m = re.search(r"DECEDENT:\s*(.+)", e.get("document_body_text") or "")
            party = m.group(1) if m else None
        match = names_match(owner, party)
        if dt == "judgment_lien" and not match:
            s["rejected_owner_mismatch"] += 1
            continue
        if dt == "eviction_filing" and not match:
            # The plaintiff is not the owner of record (property manager,
            # roommate/sub-tenant, or an institutional owner's filing entity):
            # not a callable landlord lead. Marked for the runner to drop.
            e["_drop_reason"] = f"eviction plaintiff is not owner of record ({(owner or '').strip()})"
            s["rejected_owner_mismatch"] += 1
            continue
        refs["parcel_id"] = norm_parcel(hit["PARCELID"])
        refs["_enriched_via"] = "clayton_gis_parcels_address_match"
        refs["owner_matches_party"] = match
        s["matched"] += 1
    return stats


if __name__ == "__main__":
    rows = load(force="--refresh" in sys.argv)
    idx = ParcelIndex(rows)
    print(f"parcels: {len(idx.by_parcel)}  address keys: {len(idx.by_addr)}")
