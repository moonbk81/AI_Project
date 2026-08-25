"""Boot sequence chart series.

Built from the parser report. Nothing here imports Streamlit or plotly — see
`core/charts/__init__.py` for the split.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import pandas as pd

# Which boot events count as "the phone can make calls" and "the phone has
# data". Matched case-insensitively against the event name.
VOICE_READY_PATTERN = "Voice|RIL|Telephony"
DATA_READY_PATTERN = "Data|Network|Setup"

# The bottleneck chart only shows the worst offenders.
SLOW_EVENT_LIMIT = 10

_SLOW_COLUMNS = ["Event", "Delta_ms"]


@dataclass(frozen=True)
class BootMilestones:
    """When each capability came up, in ms since boot. None = never reported."""

    boot_complete_ms: Optional[float] = None
    voice_ready_ms: Optional[float] = None
    data_ready_ms: Optional[float] = None


@dataclass(frozen=True)
class BootSequence:
    """Boot timing. `status` is `"ok"` or `"no_events"`.

    `slow_events` is empty when the parser recorded no per-event delta, which
    is a different thing from "nothing was slow" — `has_deltas` tells them
    apart.
    """

    status: str
    milestones: BootMilestones = field(default_factory=BootMilestones)
    has_deltas: bool = False
    slow_events: pd.DataFrame = field(default_factory=pd.DataFrame)
    timeline: pd.DataFrame = field(default_factory=pd.DataFrame)


def _peak_time(df: pd.DataFrame, pattern: str) -> Optional[float]:
    """The last moment an event of this kind was seen — when it was ready."""
    if "Event" not in df.columns or "Time_ms" not in df.columns:
        return None

    matching = df[df["Event"].str.contains(pattern, case=False, na=False)]
    if matching.empty:
        return None

    peak = pd.to_numeric(matching["Time_ms"], errors="coerce").max()
    return None if pd.isna(peak) else float(peak)


def build_boot_sequence(report_data: Optional[Dict[str, Any]]) -> BootSequence:
    boot_stats = (report_data or {}).get("boot_stats", []) or []
    # The parser has emitted both shapes over time.
    events = boot_stats.get("events", []) if isinstance(boot_stats, dict) else boot_stats
    if not events:
        return BootSequence(status="no_events")

    df = pd.DataFrame(events)

    boot_complete = None
    if "Time_ms" in df.columns:
        peak = pd.to_numeric(df["Time_ms"], errors="coerce").max()
        boot_complete = None if pd.isna(peak) else float(peak)

    milestones = BootMilestones(
        boot_complete_ms=boot_complete,
        voice_ready_ms=_peak_time(df, VOICE_READY_PATTERN),
        data_ready_ms=_peak_time(df, DATA_READY_PATTERN),
    )

    has_deltas = "Delta_ms" in df.columns
    slow_events = pd.DataFrame(columns=_SLOW_COLUMNS)
    if has_deltas:
        deltas = df.copy()
        deltas["Delta_ms"] = pd.to_numeric(deltas["Delta_ms"], errors="coerce")
        slow_events = (
            deltas[deltas["Delta_ms"] > 0]
            .sort_values("Delta_ms", ascending=False)
            .head(SLOW_EVENT_LIMIT)
        )

    timeline = df.sort_values("Time_ms") if "Time_ms" in df.columns else df

    return BootSequence(
        status="ok",
        milestones=milestones,
        has_deltas=has_deltas,
        slow_events=slow_events,
        timeline=timeline,
    )
