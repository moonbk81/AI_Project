"""Satellite (NTN) chart series.

Built from the parser artifacts the satellite tab fetches. Nothing here imports
Streamlit or plotly — see `core/charts/__init__.py` for the split.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from .common import log_year

# ------------------------------------------------------------ NTN (SpaceX) FW

# Y-axis order of the transition chart: radio first, registration decisions
# after it, so the sequence reads top to bottom.
NTN_EVENT_ORDER = ["RADIO_POWER", "PLMN_MATCH", "HYSTERESIS_ICON_ON", "NTN_MODE_NOTIFY"]

# Fields the view needs; the parser omits them for logs that never used them.
_NTN_EXPECTED_COLUMNS = [
    "ntn_plmn",
    "data_policy",
    "power_state",
    "ntn_mode",
    "last_ntn_mode",
    "last_phone_mode",
    "is_hysteresis",
    "raw_info",
]

_NTN_TABLE_COLUMNS = [
    "time",
    "event_type",
    "power_state",
    "ntn_plmn",
    "last_ntn_mode",
    "ntn_mode",
    "is_hysteresis",
    "data_policy",
]

ICON_ON_REAL = "ON (Real)"
ICON_ON_HYSTERESIS = "ON (Hysteresis)"
ICON_OFF = "OFF"


@dataclass(frozen=True)
class NtnStatus:
    """What the phone was showing the user by the end of the log."""

    plmn: str
    data_policy: str
    icon_status: str


@dataclass(frozen=True)
class NtnOverview:
    """NTN roaming policy and the state transitions behind it.

    `status` is `"ok"`, `"no_data"` (nothing was extracted) or
    `"no_ntn_events"` (only radio power toggles, no NTN decision).
    """

    status: str
    ntn_status: Optional[NtnStatus] = None
    transitions: pd.DataFrame = field(default_factory=pd.DataFrame)
    table: pd.DataFrame = field(default_factory=pd.DataFrame)
    event_order: List[str] = field(default_factory=lambda: list(NTN_EVENT_ORDER))


def _keep_ntn_transitions(ntn_df: pd.DataFrame) -> pd.DataFrame:
    """Drop repeats: each event type is compared against its own history.

    A PLMN match that names the same satellite network, a mode notify that
    changes nothing, or a radio toggle to the state already held are all noise.
    """
    is_plmn = ntn_df["event_type"] == "PLMN_MATCH"
    ntn_df.loc[is_plmn, "keep"] = (
        ntn_df[is_plmn]["ntn_plmn"] != ntn_df[is_plmn]["ntn_plmn"].shift(1)
    )

    is_mode = ntn_df["event_type"] == "NTN_MODE_NOTIFY"
    # A notify counts only when it both differs from what the modem last
    # reported and from the previous notify.
    changed_within_event = ntn_df[is_mode]["last_ntn_mode"] != ntn_df[is_mode]["ntn_mode"]
    changed_since_previous = ntn_df[is_mode]["ntn_mode"] != ntn_df[is_mode]["ntn_mode"].shift(1)
    ntn_df.loc[is_mode, "keep"] = changed_within_event & changed_since_previous

    is_radio = ntn_df["event_type"] == "RADIO_POWER"
    ntn_df.loc[is_radio, "keep"] = (
        ntn_df[is_radio]["power_state"] != ntn_df[is_radio]["power_state"].shift(1)
    )

    ntn_df.loc[~(is_plmn | is_mode | is_radio), "keep"] = True
    return ntn_df[ntn_df["keep"] == True].copy()  # noqa: E712


def _latest_value(ntn_df: pd.DataFrame, event_type: str, column: str) -> str:
    rows = ntn_df[ntn_df["event_type"] == event_type]
    return rows.iloc[-1][column] if not rows.empty else "N/A"


def _icon_status(ntn_df: pd.DataFrame) -> str:
    """The last event that touched the status bar decides what it shows."""
    for _, row in ntn_df.iloc[::-1].iterrows():
        if row["event_type"] == "NTN_MODE_NOTIFY":
            return ICON_ON_REAL if str(row["ntn_mode"]).upper() == "ON" else ICON_OFF
        if row["event_type"] == "HYSTERESIS_ICON_ON":
            return ICON_ON_HYSTERESIS
    return ICON_OFF


def build_ntn_overview(
    data: Optional[List[Dict[str, Any]]],
    *,
    year: Optional[int] = None,
) -> NtnOverview:
    if not data:
        return NtnOverview(status="no_data")

    ntn_df = pd.DataFrame(data)
    if ntn_df[ntn_df["event_type"] != "RADIO_POWER"].empty:
        return NtnOverview(status="no_ntn_events")

    for column in _NTN_EXPECTED_COLUMNS:
        if column not in ntn_df.columns:
            ntn_df[column] = None

    ntn_df = ntn_df.sort_values("time").reset_index(drop=True)
    clean_df = _keep_ntn_transitions(ntn_df)

    # The policy line is a setting, not a transition, so it stays out of the chart.
    chart_df = clean_df[clean_df["event_type"] != "DATA_POLICY"].copy()
    if not chart_df.empty:
        chart_df["time_dt"] = pd.to_datetime(
            str(log_year(year)) + "-" + chart_df["time"].astype(str), errors="coerce"
        )
        chart_df = chart_df.sort_values("time_dt")

    columns = [column for column in _NTN_TABLE_COLUMNS if column in clean_df.columns]

    return NtnOverview(
        status="ok",
        ntn_status=NtnStatus(
            plmn=_latest_value(ntn_df, "PLMN_MATCH", "ntn_plmn"),
            data_policy=_latest_value(ntn_df, "DATA_POLICY", "data_policy"),
            icon_status=_icon_status(ntn_df),
        ),
        transitions=chart_df,
        table=clean_df[columns].fillna("-"),
    )


# ------------------------------------------------- satellite AT (Tiantong) modem

# Y-axis order for the registration chart, worst state first.
SAT_REG_STATE_ORDER = ["Deregistered (0)", "Searching", "Registered (1)"]

# Column index of each actor in the sequence diagram.
SAT_FLOW_ACTORS = ["Android FW", "RIL Daemon", "Modem (CP)"]
_FRAMEWORK_COLUMN = 0

# A step whose description mentions either of these ended a call badly.
_FLOW_ERROR_MARKERS = ("ERROR", "CEND")


@dataclass(frozen=True)
class SatAtKpi:
    arfcn: Any = "N/A"
    reg_state: Any = "Unknown"
    calls_total: int = 0
    calls_failed: int = 0
    sms_rx: int = 0
    sms_tx_success: int = 0
    sms_tx_fail: int = 0


@dataclass(frozen=True)
class SatCallFlowStep:
    """One message of the AP - RIL - modem sequence diagram."""

    time: Any
    src: int
    dst: int
    desc: str
    is_highlight: bool
    is_error: bool
    # Framework hops are drawn in a different hue from modem-side ones.
    involves_framework: bool


@dataclass(frozen=True)
class SatAtOverview:
    """Satellite modem control state. The KPI row renders even when empty."""

    kpi: SatAtKpi
    registration: pd.DataFrame = field(default_factory=pd.DataFrame)
    reg_state_order: List[str] = field(default_factory=lambda: list(SAT_REG_STATE_ORDER))
    call_flow: List[SatCallFlowStep] = field(default_factory=list)


def _flow_step(message: Dict[str, Any]) -> SatCallFlowStep:
    src = message["src"]
    dst = message["dst"]
    desc = message["desc"]
    return SatCallFlowStep(
        time=message["time"],
        src=src,
        dst=dst,
        desc=desc,
        is_highlight=bool(message.get("is_highlight", False)),
        is_error=any(marker in desc for marker in _FLOW_ERROR_MARKERS),
        involves_framework=_FRAMEWORK_COLUMN in (src, dst),
    )


def build_sat_at_overview(data: Optional[Dict[str, Any]]) -> SatAtOverview:
    data = data or {}
    metrics = data.get("metrics", {}) or {}

    kpi = SatAtKpi(
        arfcn=metrics.get("arfcn", "N/A"),
        reg_state=metrics.get("current_reg_state", "Unknown"),
        calls_total=metrics.get("calls_total", 0),
        calls_failed=metrics.get("calls_dropped_or_failed", 0),
        sms_rx=metrics.get("sms_rx", 0),
        sms_tx_success=metrics.get("sms_tx_success", 0),
        sms_tx_fail=metrics.get("sms_tx_fail", 0),
    )

    registration = pd.DataFrame(data.get("registration_history", []) or [])
    call_flow = [_flow_step(message) for message in data.get("call_flow", []) or []]

    return SatAtOverview(kpi=kpi, registration=registration, call_flow=call_flow)
