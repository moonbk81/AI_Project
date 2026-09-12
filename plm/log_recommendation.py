"""Persistent, explainable recommendations for PLM log candidates.

Only explicit human choices train the recommender.  Agent recommendations are
recorded for auditability but are excluded from the statistics, avoiding a
self-reinforcing feedback loop.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, Iterable, List


_LOCK = threading.Lock()
_DEFAULT_DB = "./agent_state/log_selection_history.sqlite3"


def database_path() -> Path:
    return Path(os.getenv("PLM_LOG_SELECTION_DB", _DEFAULT_DB)).resolve()


def _family(candidate: Dict[str, Any]) -> str:
    path = str(candidate.get("path") or "/".join(candidate.get("route") or [])).lower()
    group = str(candidate.get("group") or "").lower()
    kind = str(candidate.get("kind") or "log").lower()
    if kind == "capture" or path.endswith((".pcap", ".pcapng", ".cap")):
        return "packet_capture"
    if "ap_silentlog" in group or "ap_silentlog" in path:
        return "ap_silentlog"
    if "g_manager" in path or "gear" in path:
        return "companion_device"
    name = path.rsplit("/", 1)[-1]
    if name.startswith("dumpstate") or name == "act_dumpstate.txt":
        return "dumpstate"
    if name.startswith("bugreport"):
        return "bugreport"
    return "other"


_PRIORS = {
    "dumpstate": 0.90,
    "bugreport": 0.88,
    "ap_silentlog": 0.72,
    "companion_device": 0.62,
    "packet_capture": 0.42,
    "other": 0.25,
}
_PRIOR_WEIGHT = 5.0
_RECOMMEND_THRESHOLD = 0.60


def _connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or database_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(target, timeout=15)
    db.execute("""
        CREATE TABLE IF NOT EXISTS log_selection_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            source TEXT NOT NULL,
            division_code TEXT NOT NULL,
            defect_code TEXT NOT NULL,
            user_id TEXT NOT NULL,
            family TEXT NOT NULL,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            file_id TEXT NOT NULL,
            route_json TEXT NOT NULL,
            selected INTEGER NOT NULL CHECK(selected IN (0, 1))
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_log_selection_learning ON log_selection_events(source, division_code, family)")
    return db


def _key(candidate: Dict[str, Any]) -> tuple:
    return str(candidate.get("file_id") or ""), tuple(candidate.get("route") or [])


def record_selection(
    candidates: Iterable[Dict[str, Any]], selected: Iterable[Dict[str, Any]], *,
    division_code: str, defect_code: str, user_id: str, source: str = "manual",
    path: Path | None = None,
) -> None:
    """Store one complete choice set, including candidates not selected."""
    chosen = {_key(item) for item in selected}
    rows = []
    now = datetime.now().astimezone().isoformat()
    for candidate in candidates:
        route = list(candidate.get("route") or [])
        rows.append((
            now, source, str(division_code), str(defect_code), str(user_id or ""),
            _family(candidate), str(candidate.get("kind") or "log"),
            str(candidate.get("path") or "/".join(route)),
            str(candidate.get("file_id") or ""), json.dumps(route, ensure_ascii=False),
            int(_key(candidate) in chosen),
        ))
    if not rows:
        return
    with _LOCK:
        with _connect(path) as db:
            db.executemany("""
                INSERT INTO log_selection_events
                (created_at, source, division_code, defect_code, user_id, family,
                 kind, path, file_id, route_json, selected)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)


def recommend_candidates(
    candidates: Iterable[Dict[str, Any]], division_code: str, *, path: Path | None = None,
) -> List[Dict[str, Any]]:
    """Decorate candidates with a smoothed human-selection probability."""
    items = [dict(item) for item in candidates]
    stats: Dict[str, tuple[int, int]] = {}
    try:
        with _LOCK:
            with _connect(path) as db:
                for family, seen, picked in db.execute("""
                    SELECT family, COUNT(*), COALESCE(SUM(selected), 0)
                    FROM log_selection_events
                    WHERE source='manual' AND division_code=?
                    GROUP BY family
                """, (str(division_code),)):
                    stats[family] = (int(seen), int(picked))
    except (OSError, sqlite3.Error):
        # Recommendation must never make log discovery unavailable.
        stats = {}

    for item in items:
        family = _family(item)
        seen, picked = stats.get(family, (0, 0))
        prior = _PRIORS[family]
        score = (picked + _PRIOR_WEIGHT * prior) / (seen + _PRIOR_WEIGHT)
        item["recommendation_score"] = round(score, 3)
        item["recommended"] = score >= _RECOMMEND_THRESHOLD
        item["recommendation_reason"] = (
            f"수동 선택 {picked}/{seen}" if seen else f"기본 규칙: {family}"
        )
    # Never return an empty recommendation when candidates exist.
    if items and not any(item["recommended"] for item in items):
        max(items, key=lambda item: item["recommendation_score"])["recommended"] = True
    return items

