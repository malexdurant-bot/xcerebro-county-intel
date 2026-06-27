"""
Maricopa County APN resolver — multi-strategy assessor name lookup.

Tries strategies in priority order and classifies each result as:
  CONFIRMED  — exactly 1 result from a high-confidence strategy
  POSSIBLE   — exactly 1 result from a lower-confidence strategy
  AMBIGUOUS  — 2–4 results (candidates preserved; never auto-selected)
  UNRESOLVED — 0 results from all strategies

Co-owner fallback: the resolver accepts a list of individual names and
tries each in turn, stopping at the first CONFIRMED or POSSIBLE hit.
An AMBIGUOUS result from one name is preserved but does not prevent
trying the remaining names.

Caching: all assessor queries are cached in memory and optionally
persisted to a JSON file on disk so reruns skip the network.
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIDENCE_CONFIRMED = "CONFIRMED"
CONFIDENCE_POSSIBLE = "POSSIBLE"
CONFIDENCE_AMBIGUOUS = "AMBIGUOUS"
CONFIDENCE_UNRESOLVED = "UNRESOLVED"

_ASSESSOR_URL = (
    "https://gis.mcassessor.maricopa.gov"
    "/arcgis/rest/services/Parcels/MapServer/0/query"
)
_ASSESSOR_FIELDS = (
    "APN,APN_DASH,OWNER_NAME,"
    "PHYSICAL_ADDRESS,PHYSICAL_CITY,PHYSICAL_ZIP,"
    "MAIL_ADDR1,MAIL_CITY,MAIL_STATE,MAIL_ZIP,"
    "FCV_CUR,SALE_PRICE,SALE_DATE,CONST_YEAR,PUC"
)
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# Maximum results to request per query.  Requesting N+1 distinguishes
# "exactly N results" from "N out of potentially many more".
_PAGE_SIZE = 6

# 1–4 results on a high-confidence strategy → CONFIRMED or POSSIBLE.
# 5+ → too many candidates, treat as AMBIGUOUS with truncated candidates.
_AMBIGUOUS_FLOOR = 2
_TOO_MANY = _PAGE_SIZE  # 6 = hit the cap → likely more exist

# Strategy names whose single-result confidence is CONFIRMED.
_CONFIRMED_STRATEGIES = {"first_last", "reversed", "first_second"}

# ---------------------------------------------------------------------------
# Default network fetch (real assessor API)
# ---------------------------------------------------------------------------


def _default_fetch(where: str, n: int = _PAGE_SIZE) -> list[dict]:
    """Query the Maricopa assessor ArcGIS MapServer; return features list."""
    params = urllib.parse.urlencode({
        "where": where,
        "outFields": _ASSESSOR_FIELDS,
        "resultRecordCount": n,
        "f": "json",
    })
    req = urllib.request.Request(
        f"{_ASSESSOR_URL}?{params}",
        headers={
            "User-Agent": "xcerebro-maricopa-apn-resolver/0.1",
            "Accept": "application/json",
            "Referer": "https://maps.mcassessor.maricopa.gov",
        },
    )
    with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if "error" in data:
        raise RuntimeError(f"Assessor API error: {data['error']}")
    return data.get("features", [])


# ---------------------------------------------------------------------------
# Resolution result
# ---------------------------------------------------------------------------


def _result(
    apn: Optional[str],
    confidence: str,
    strategy: Optional[str],
    matched_name: Optional[str],
    attrs: Optional[dict],
    candidates: list,
) -> dict:
    return {
        "apn": apn,
        "confidence": confidence,
        "strategy_used": strategy,
        "matched_name": matched_name,
        "attrs": attrs,
        "candidates": candidates,
    }


_UNRESOLVED = _result(None, CONFIDENCE_UNRESOLVED, None, None, None, [])


# ---------------------------------------------------------------------------
# Token sanitisation
# ---------------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def _safe_token(t: str) -> str:
    """Strip characters that would break a SQL LIKE clause."""
    return _NON_ALNUM.sub("", t.upper())


# ---------------------------------------------------------------------------
# Strategy builder
# ---------------------------------------------------------------------------


def _build_strategies(tokens: list[str]) -> list[tuple[str, str]]:
    """Return (strategy_name, WHERE_clause) pairs in priority order.

    Strategies:
      first_last   — token[0] + token[-1]  (primary)
      reversed     — token[-1] + token[0]  (last-name-first assessor format)
      first_second — token[0] + token[1]   (3-token names: skip middle/last)
      second_last  — token[1] + token[-1]  (3-token names: skip first)
      single_token — token[0] alone        (last resort; POSSIBLE only)
    """
    if not tokens:
        return []

    safe = [_safe_token(t) for t in tokens]
    safe = [t for t in safe if len(t) >= 2]  # drop single-char tokens

    if not safe:
        return []

    strategies: list[tuple[str, str]] = []

    if len(safe) >= 2:
        t0, tlast = safe[0], safe[-1]
        strategies.append(
            ("first_last", f"OWNER_NAME LIKE '%{t0}%{tlast}%'")
        )
        strategies.append(
            ("reversed", f"OWNER_NAME LIKE '%{tlast}%{t0}%'")
        )
        if len(safe) >= 3:
            t1 = safe[1]
            strategies.append(
                ("first_second", f"OWNER_NAME LIKE '%{t0}%{t1}%'")
            )
            strategies.append(
                ("second_last", f"OWNER_NAME LIKE '%{t1}%{tlast}%'")
            )

    # Single-token fallback only for long tokens (≥6 chars → more distinctive).
    if safe and len(safe[0]) >= 6:
        strategies.append(("single_token", f"OWNER_NAME LIKE '%{safe[0]}%'"))

    # Deduplicate while preserving order.
    seen: set = set()
    deduped: list[tuple[str, str]] = []
    for name, clause in strategies:
        if clause not in seen:
            seen.add(clause)
            deduped.append((name, clause))
    return deduped


# ---------------------------------------------------------------------------
# APNResolver
# ---------------------------------------------------------------------------


class APNResolver:
    """Multi-strategy APN resolver with in-memory + optional disk cache.

    Parameters
    ----------
    cache_path:
        Optional path to a JSON disk-cache file.  On construction the
        cache is loaded from disk (if the file exists).  Call
        save_cache() to persist after a run.
    fetch_fn:
        Callable (where, n) → list[feature_dict].  Defaults to the real
        assessor HTTP call.  Inject a mock for unit tests.
    """

    def __init__(
        self,
        cache_path: Optional[Path] = None,
        fetch_fn: Optional[Callable] = None,
    ) -> None:
        self._cache: dict[str, list] = {}
        self._cache_path = cache_path
        self._fetch = fetch_fn or _default_fetch
        if cache_path and Path(cache_path).exists():
            self._load_cache()

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _load_cache(self) -> None:
        try:
            raw = json.loads(Path(self._cache_path).read_text(encoding="utf-8"))
            for key, entry in raw.items():
                self._cache[key] = entry.get("features", [])
        except Exception:
            pass

    def save_cache(self) -> None:
        if not self._cache_path:
            return
        Path(self._cache_path).parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        out = {
            k: {"features": v, "cached_at": now}
            for k, v in self._cache.items()
        }
        Path(self._cache_path).write_text(
            json.dumps(out, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def cache_size(self) -> int:
        return len(self._cache)

    # ------------------------------------------------------------------
    # Single-query helper
    # ------------------------------------------------------------------

    def _query(self, where: str) -> list[dict]:
        if where not in self._cache:
            self._cache[where] = self._fetch(where, _PAGE_SIZE)
        return self._cache[where]

    # ------------------------------------------------------------------
    # Direct APN lookup (treasurer records already carry the APN)
    # ------------------------------------------------------------------

    def resolve_by_apn(
        self,
        apn_dash: str = "",
        apn: str = "",
    ) -> Optional[dict]:
        """Return the assessor attrs dict for a known APN, or None.

        Queries by APN_DASH first (dash format preferred); falls back to
        the raw numeric APN field. Returns attrs on an exact single match;
        returns None on zero or multiple results so callers never pick
        the wrong parcel.
        """
        where: Optional[str] = None
        if apn_dash:
            safe_dash = re.sub(r"[^0-9\-]", "", apn_dash.strip())
            if safe_dash:
                where = f"APN_DASH = '{safe_dash}'"
        if not where and apn:
            safe_apn = re.sub(r"[^0-9]", "", apn.strip())
            if safe_apn:
                where = f"APN = '{safe_apn}'"
        if not where:
            return None
        try:
            features = self._query(where)
        except Exception:
            return None
        if len(features) == 1:
            return features[0].get("attributes", {})
        return None

    # ------------------------------------------------------------------
    # Single-name resolution
    # ------------------------------------------------------------------

    def _resolve_name(self, name: str) -> dict:
        """Try all strategies for one individual name. Returns a result dict."""
        tokens = name.upper().split()
        strategies = _build_strategies(tokens)

        if not strategies:
            return _result(None, CONFIDENCE_UNRESOLVED, None, name, None, [])

        first_ambiguous: Optional[dict] = None

        for strategy_name, where in strategies:
            try:
                features = self._query(where)
            except Exception:
                continue

            n = len(features)
            if n == 0:
                continue

            if n == 1:
                attrs = features[0].get("attributes", {})
                conf = (
                    CONFIDENCE_CONFIRMED
                    if strategy_name in _CONFIRMED_STRATEGIES
                    else CONFIDENCE_POSSIBLE
                )
                return _result(
                    apn=attrs.get("APN"),
                    confidence=conf,
                    strategy=strategy_name,
                    matched_name=name,
                    attrs=attrs,
                    candidates=[],
                )

            if _AMBIGUOUS_FLOOR <= n < _TOO_MANY:
                candidates = [f.get("attributes", {}) for f in features]
                hit = _result(
                    apn=None,
                    confidence=CONFIDENCE_AMBIGUOUS,
                    strategy=strategy_name,
                    matched_name=name,
                    attrs=None,
                    candidates=candidates,
                )
                if first_ambiguous is None:
                    first_ambiguous = hit
                # Don't stop — a later strategy might be more specific.
                continue

            # n >= _TOO_MANY: this strategy is too broad; skip silently.

        if first_ambiguous is not None:
            return first_ambiguous

        return _result(None, CONFIDENCE_UNRESOLVED, None, name, None, [])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, individual_names: list[str]) -> dict:
        """Resolve a list of individual names to an APN.

        Tries each name in order (primary debtor first, co-owners after).
        Stops at the first CONFIRMED or POSSIBLE result.  If no definitive
        match is found, returns the first AMBIGUOUS result (candidates
        preserved for operator review), or UNRESOLVED if nothing matched.

        Parameters
        ----------
        individual_names:
            List of individual owner names from the recorder document,
            classified as INDIVIDUAL / ESTATE / TRUST by classify_owner_type.

        Returns
        -------
        Result dict with keys:
          apn, confidence, strategy_used, matched_name, attrs, candidates
        """
        if not individual_names:
            return _result(None, CONFIDENCE_UNRESOLVED, None, None, None, [])

        first_ambiguous: Optional[dict] = None

        for name in individual_names:
            name = name.strip()
            if not name:
                continue
            result = self._resolve_name(name)
            conf = result["confidence"]

            if conf in (CONFIDENCE_CONFIRMED, CONFIDENCE_POSSIBLE):
                return result

            if conf == CONFIDENCE_AMBIGUOUS and first_ambiguous is None:
                first_ambiguous = result
            # Continue trying co-owners even after AMBIGUOUS.

        if first_ambiguous is not None:
            return first_ambiguous

        return _result(None, CONFIDENCE_UNRESOLVED, None, None, None, [])
