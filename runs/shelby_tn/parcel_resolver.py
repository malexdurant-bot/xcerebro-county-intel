"""
Shelby County, TN — ArcGIS parcel resolver.

Wraps scrapers/parcel_master_shelby.py with an in-memory + disk cache so that
repeated lookups during a single pipeline run don't hit the ArcGIS server.

The cache persists to JSON on disk between runs to minimize API calls.

Usage:
    resolver = ParcelResolver(cache_path=PARCEL_CACHE_PATH)
    attrs = resolver.resolve_by_parcelid("035087  00049")
    parcel = resolver.normalize(attrs)          # → framework parcel dict
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_THIS_DIR = Path(__file__).parent
_REPO_ROOT = _THIS_DIR.parents[1]
for _p in (str(_REPO_ROOT), str(_THIS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scrapers.parcel_master_shelby import (  # noqa: E402
    lookup_by_parcelid,
    lookup_by_owner,
    normalize_parcel_attrs,
)


class ParcelResolver:
    """Resolve Shelby County parcel IDs to owner + address via ArcGIS.

    Args:
        cache_path: Path to JSON cache file.  Created if it doesn't exist.
        load_cache: Whether to load existing cache from disk on init.
    """

    def __init__(
        self,
        cache_path: Optional[Path] = None,
        load_cache: bool = True,
    ) -> None:
        self._cache: dict[str, Optional[dict]] = {}
        self._cache_path = cache_path
        self._hits = 0
        self._misses = 0
        self._cache_hits = 0

        if load_cache and cache_path and cache_path.exists():
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                self._cache = data.get("parcels", {})
                print(f"[ParcelResolver] Loaded {len(self._cache)} cached parcels from {cache_path.name}")
            except Exception as exc:
                print(f"[ParcelResolver] Warning: could not load cache: {exc}")

    # -----------------------------------------------------------------------
    # Primary lookup
    # -----------------------------------------------------------------------

    def resolve_by_parcelid(self, parcel_id: str) -> Optional[dict]:
        """Return raw ArcGIS attribute dict for a parcel ID, or None.

        Uses the cache first; falls back to live ArcGIS query.
        """
        key = parcel_id.strip()
        if key in self._cache:
            self._cache_hits += 1
            return self._cache[key]

        attrs = lookup_by_parcelid(key)
        self._cache[key] = attrs
        if attrs:
            self._hits += 1
        else:
            self._misses += 1
        return attrs

    def resolve_parcel_dict(self, parcel_id: str) -> Optional[dict]:
        """Return normalized parcel dict (framework shape) for a parcel ID."""
        attrs = self.resolve_by_parcelid(parcel_id)
        if attrs is None:
            return None
        return normalize_parcel_attrs(attrs)

    # -----------------------------------------------------------------------
    # Cache persistence
    # -----------------------------------------------------------------------

    def save_cache(self) -> None:
        """Write the in-memory cache to disk."""
        if not self._cache_path:
            return
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "county": "shelby_tn",
            "parcel_count": len(self._cache),
            "parcels": self._cache,
        }
        self._cache_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def print_stats(self) -> None:
        hits = self._hits
        misses = self._misses
        cache_hits = self._cache_hits
        total = hits + misses + cache_hits
        print(
            f"[ParcelResolver] Lookups: {total} total — "
            f"{cache_hits} cache hits, {hits} API hits, {misses} API misses"
        )

    # -----------------------------------------------------------------------
    # Enrichment provider protocol (compatible with scoring_seam)
    # -----------------------------------------------------------------------

    def get_parcel(self, parcel_id: str) -> Optional[dict]:
        """EnrichmentProvider interface: return parcel dict or None."""
        return self.resolve_parcel_dict(parcel_id)
