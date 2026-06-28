"""SQLite-backed lead history store for Maricopa County pipeline.

Tracks each unique lead_id across runs: first_seen_date, last_seen_date,
score history, score_delta, tier, review_status, and enrichment_status.

Schema:
    lead_history   — one row per lead_id, updated on each run
    run_log        — one row per completed run (for audit)
    run_state      — key/value store for last_successful_run_date

Usage:
    with LeadHistory() as h:
        counts = h.upsert_leads(scored_leads, run_date="2026-06-28")
        h.set_last_run_date("2026-06-28")
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "state" / "maricopa_lead_history.sqlite"


class LeadHistory:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS lead_history (
                lead_id             TEXT PRIMARY KEY,
                first_seen_date     TEXT NOT NULL,
                last_seen_date      TEXT NOT NULL,
                source_patterns     TEXT,
                score_tier          TEXT,
                review_status       TEXT,
                enrichment_status   TEXT,
                last_score          INTEGER,
                current_score       INTEGER,
                score_delta         INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS run_log (
                run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date        TEXT NOT NULL,
                run_type        TEXT NOT NULL,
                lead_count      INTEGER,
                new_count       INTEGER,
                updated_count   INTEGER,
                runtime_s       REAL,
                completed_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS run_state (
                key     TEXT PRIMARY KEY,
                value   TEXT NOT NULL
            );
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Run state
    # ------------------------------------------------------------------

    def get_last_run_date(self) -> Optional[str]:
        """Return YYYY-MM-DD of last successful run, or None."""
        row = self._conn.execute(
            "SELECT value FROM run_state WHERE key = 'last_successful_run_date'"
        ).fetchone()
        return row["value"] if row else None

    def set_last_run_date(self, run_date: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO run_state (key, value) "
            "VALUES ('last_successful_run_date', ?)",
            (run_date,),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Lead tracking
    # ------------------------------------------------------------------

    def known_lead_ids(self) -> set[str]:
        rows = self._conn.execute("SELECT lead_id FROM lead_history").fetchall()
        return {r["lead_id"] for r in rows}

    def upsert_leads(
        self, scored_leads: list[dict], run_date: str
    ) -> dict[str, int]:
        """Insert new leads and update existing ones. Returns {'new': N, 'updated': N}."""
        known = self.known_lead_ids()
        new_count = updated_count = 0

        for lead in scored_leads:
            lead_id = lead.get("lead_id")
            if not lead_id:
                continue

            current_score = lead.get("score") or 0
            tier = lead.get("tier") or ""
            review_status = lead.get("lead_status") or ""
            enrichment_status = lead.get("enrichment_status") or ""
            source_patterns = "|".join(lead.get("patterns") or [])

            if lead_id in known:
                row = self._conn.execute(
                    "SELECT current_score FROM lead_history WHERE lead_id = ?",
                    (lead_id,),
                ).fetchone()
                prev_score = row["current_score"] if row else current_score
                self._conn.execute(
                    """UPDATE lead_history SET
                        last_seen_date  = ?,
                        source_patterns = ?,
                        score_tier      = ?,
                        review_status   = ?,
                        enrichment_status = ?,
                        last_score      = ?,
                        current_score   = ?,
                        score_delta     = ?
                    WHERE lead_id = ?""",
                    (
                        run_date, source_patterns, tier, review_status,
                        enrichment_status, prev_score, current_score,
                        current_score - prev_score, lead_id,
                    ),
                )
                updated_count += 1
            else:
                self._conn.execute(
                    """INSERT INTO lead_history (
                        lead_id, first_seen_date, last_seen_date,
                        source_patterns, score_tier, review_status,
                        enrichment_status, last_score, current_score, score_delta
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                    (
                        lead_id, run_date, run_date,
                        source_patterns, tier, review_status,
                        enrichment_status, current_score, current_score,
                    ),
                )
                known.add(lead_id)
                new_count += 1

        self._conn.commit()
        return {"new": new_count, "updated": updated_count}

    def rebuild_from_leads(
        self, scored_leads: list[dict], run_date: str
    ) -> dict[str, int]:
        """Wipe the history table and rebuild from a full scored_leads list.

        All leads get first_seen_date = run_date. Used by full-rebuild cmd.
        """
        self._conn.execute("DELETE FROM lead_history")
        self._conn.commit()

        count = 0
        for lead in scored_leads:
            lead_id = lead.get("lead_id")
            if not lead_id:
                continue
            current_score = lead.get("score") or 0
            self._conn.execute(
                """INSERT INTO lead_history (
                    lead_id, first_seen_date, last_seen_date,
                    source_patterns, score_tier, review_status,
                    enrichment_status, last_score, current_score, score_delta
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    lead_id, run_date, run_date,
                    "|".join(lead.get("patterns") or []),
                    lead.get("tier") or "",
                    lead.get("lead_status") or "",
                    lead.get("enrichment_status") or "",
                    current_score, current_score,
                ),
            )
            count += 1

        self._conn.commit()
        return {"new": count, "updated": 0}

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_lead_row(self, lead_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM lead_history WHERE lead_id = ?", (lead_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_lead_rows(self, since_date: Optional[str] = None) -> list[dict]:
        """Return history rows, optionally filtered to first_seen_date >= since_date."""
        if since_date:
            rows = self._conn.execute(
                "SELECT * FROM lead_history WHERE first_seen_date >= ?", (since_date,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM lead_history").fetchall()
        return [dict(r) for r in rows]

    def get_history_by_id(self) -> dict[str, dict]:
        return {r["lead_id"]: r for r in self.get_lead_rows()}

    # ------------------------------------------------------------------
    # Run logging
    # ------------------------------------------------------------------

    def log_run(
        self,
        run_date: str,
        run_type: str,
        lead_count: int,
        new_count: int,
        updated_count: int,
        runtime_s: float,
    ) -> None:
        self._conn.execute(
            """INSERT INTO run_log
               (run_date, run_type, lead_count, new_count, updated_count,
                runtime_s, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                run_date, run_type, lead_count, new_count, updated_count,
                runtime_s, datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "LeadHistory":
        return self

    def __exit__(self, *_) -> None:
        self.close()
