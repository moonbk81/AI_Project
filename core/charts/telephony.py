"""Telephony chart series.

Built from the Chroma metadata frame and the parser report the dashboard
already holds. Nothing here imports a web framework or plotly — see
`core/charts/__init__.py` for the split.

Where a builder exposes a `to_frame()` or a `table`, its column names are the
ones the user ends up seeing (plotly hover boxes, facet titles, dataframe
headers), so they stay exactly as rendered rather than being normalised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional

import pandas as pd

from .common import has_columns as _has_columns, log_year as _log_year

# Y-axis ordering for the registration timeline: worst state at the bottom, so a
# line dropping downwards reads as service degrading.
SERVICE_STATE_ORDER = ["POWER_OFF", "EMERGENCY_ONLY", "OUT_OF_SERVICE", "IN_SERVICE"]

_UNKNOWN_STATE = "UNKNOWN"
_REG_PREFIX_STATES = {
    "0": "IN_SERVICE",
    "1": "OUT_OF_SERVICE",
    "2": "EMERGENCY_ONLY",
    "3": "POWER_OFF",
}

# Dataclass field -> DataFrame column handed to plotly. The display names are
# what plotly prints in hover boxes and facet titles, so they are part of the
# rendered output and stay capitalised.
_FRAME_COLUMNS = {
    "time": "time",
    "time_dt": "time_dt",
    "slot": "Slot",
    "conn_type": "Type",
    "state": "State",
    "raw_reg": "Raw_Reg",
    "event": "Event",
    "cause": "Cause",
    "operator": "Operator",
    "radio_tech": "Radio_Tech",
    "label": "Label",
}


def map_registration_state(reg_value: Any) -> str:
    """Map a raw `voice_reg` / `data_reg` field onto a service state.

    The logs put the 3GPP registration code first and often append vendor
    detail (`"1 (denied)"`). Some parser paths already store the canonical
    Android state name, so both forms are accepted.
    """
    reg_str = "" if reg_value is None else str(reg_value)
    if not reg_str or reg_str == "nan":
        return _UNKNOWN_STATE
    if reg_str[0] in _REG_PREFIX_STATES:
        return _REG_PREFIX_STATES[reg_str[0]]

    upper_reg = reg_str.upper()
    for state in SERVICE_STATE_ORDER:
        if state in upper_reg:
            return state
    return _UNKNOWN_STATE


@dataclass(frozen=True)
class ServiceStatePoint:
    """One registration-state transition for a single (slot, connection type)."""

    time: str
    time_dt: Optional[pd.Timestamp]
    slot: str
    conn_type: str  # "Voice" or "Data"
    state: str
    raw_reg: str
    event: str
    cause: str
    operator: str
    radio_tech: str
    label: str  # operator/RAT caption, only on IN_SERVICE points


@dataclass(frozen=True)
class ServiceStateSeries:
    """Voice/Data registration transitions, ready to plot.

    `status` tells the caller what to render:

    * `"ok"`          - draw `points`
    * `"unavailable"` - the session has no log metadata at all; say nothing
    * `"no_events"`   - metadata exists but carries no OOS event; service held
    * `"no_changes"`  - OOS events exist but none of them changed a state
    """

    status: str
    points: List[ServiceStatePoint] = field(default_factory=list)
    state_order: List[str] = field(default_factory=lambda: list(SERVICE_STATE_ORDER))
    slot_count: int = 0

    def to_frame(self) -> pd.DataFrame:
        """Long-form frame keyed by the column names plotly shows to the user."""
        rows = [
            {column: getattr(point, attr) for attr, column in _FRAME_COLUMNS.items()}
            for point in self.points
        ]
        frame = pd.DataFrame(rows, columns=list(_FRAME_COLUMNS.values()))
        # Keep the dtype stable even when every timestamp failed to parse,
        # otherwise plotly gets an object column and drops the axis.
        frame["time_dt"] = pd.to_datetime(frame["time_dt"], errors="coerce")
        return frame


def _transition_records(oos_df: pd.DataFrame) -> List[Dict[str, Any]]:
    """One record per (event, connection type): every OOS row carries both."""
    records: List[Dict[str, Any]] = []
    for _, row in oos_df.iterrows():
        shared = {
            "time": row.get("time"),
            "slot": f"Slot {str(row.get('slot', row.get('slotId', '0')))}",
            "event": row.get("event", row.get("event_type", "Unknown")),
            "cause": row.get("candidate_reason", row.get("root_cause_candidate", "None")),
            "operator": row.get("operator", "Unknown"),
            "radio_tech": row.get("rat", "Unknown"),
        }
        for conn_type, column in (("Voice", "voice_reg"), ("Data", "data_reg")):
            raw_reg = str(row.get(column, "Unknown"))
            records.append(
                {
                    **shared,
                    "conn_type": conn_type,
                    "state": map_registration_state(raw_reg),
                    "raw_reg": raw_reg,
                }
            )
    return records


def _keep_transitions(state_df: pd.DataFrame) -> pd.DataFrame:
    """Drop repeats: a point survives only when it changes its own state.

    Compared within a (slot, connection type) track, ordered by the raw log
    time, so Voice repeating IN_SERVICE does not hide a Data drop.
    """
    state_df = state_df.sort_values(by=["slot", "conn_type", "time"]).reset_index(drop=True)
    tracks = state_df.groupby(["slot", "conn_type"])
    keep = state_df["state"] != tracks["state"].shift(1)
    keep.loc[tracks.head(1).index] = True  # each track's first sample is a transition
    return state_df[keep].copy()


def build_service_state_series(
    df: pd.DataFrame,
    *,
    year: Optional[int] = None,
) -> ServiceStateSeries:
    """Voice/Data registration transitions for the network-registration chart.

    Log timestamps carry no year, so one is prefixed before parsing; pass
    `year` to keep the result deterministic.
    """
    if not _has_columns(df) or "log_type" not in df.columns:
        return ServiceStateSeries(status="unavailable")

    oos_df = df[df["log_type"] == "OOS_Event"]
    if oos_df.empty:
        return ServiceStateSeries(status="no_events")

    clean_df = _keep_transitions(pd.DataFrame(_transition_records(oos_df)))
    if clean_df.empty:
        return ServiceStateSeries(status="no_changes")

    year = _log_year(year)
    clean_df["time_dt"] = pd.to_datetime(
        str(year) + "-" + clean_df["time"].astype(str),
        format="%Y-%m-%d %H:%M:%S.%f",
        errors="coerce",
    )
    clean_df = clean_df.sort_values(by=["time_dt", "slot", "conn_type"]).reset_index(drop=True)

    points = [
        ServiceStatePoint(
            time=row["time"],
            time_dt=None if pd.isna(row["time_dt"]) else row["time_dt"],
            slot=row["slot"],
            conn_type=row["conn_type"],
            state=row["state"],
            raw_reg=row["raw_reg"],
            event=row["event"],
            cause=row["cause"],
            operator=row["operator"],
            radio_tech=row["radio_tech"],
            # Only a registered point can name the network it registered on.
            label=f"[{row['radio_tech']}] {row['operator']}" if row["state"] == "IN_SERVICE" else "",
        )
        for _, row in clean_df.iterrows()
    ]

    return ServiceStateSeries(
        status="ok",
        points=points,
        state_order=list(SERVICE_STATE_ORDER),
        slot_count=int(clean_df["slot"].nunique()),
    )


# --------------------------------------------------------------- call sessions

# Columns of the call table, in display order; missing ones are simply skipped.
_CALL_HISTORY_COLUMNS = [
    "time",
    "slot",
    "status",
    "fail_reason",
    "release_reason",
    "call_id",
    "id",
    "source_file",
]


@dataclass(frozen=True)
class CallHistorySummary:
    """Call sessions of the analyzed log, as a status breakdown plus a table.

    `status` is `"ok"`, `"unavailable"` (no log metadata) or `"no_calls"`.
    """

    status: str
    call_count: int = 0
    # Raw per-session status values in log order — the pie counts them itself,
    # so slice colors stay in the order the log produced. None means the rows
    # carry no `status` field at all, which is a different message.
    statuses: Optional[List[str]] = None
    table: pd.DataFrame = field(default_factory=pd.DataFrame)


def _call_session_frame(data: Any) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame):
        return data
    if isinstance(data, list):
        frame = pd.DataFrame(data)
        if frame.empty:
            return frame
        frame["log_type"] = "Call_Session"
        if "time" not in frame.columns and "start_time" in frame.columns:
            frame["time"] = frame["start_time"]
        return frame
    return pd.DataFrame()


def build_call_history_summary(data: Any) -> CallHistorySummary:
    df = _call_session_frame(data)
    if not _has_columns(df) or "log_type" not in df.columns:
        return CallHistorySummary(status="unavailable")

    call_df = df[df["log_type"] == "Call_Session"]
    if call_df.empty:
        return CallHistorySummary(status="no_calls")

    columns = [column for column in _CALL_HISTORY_COLUMNS if column in call_df.columns]
    table = call_df[columns].fillna("-")
    if "time" in table.columns:
        table = table.sort_values(by="time", ascending=False)

    return CallHistorySummary(
        status="ok",
        call_count=len(table),
        statuses=call_df["status"].tolist() if "status" in call_df.columns else None,
        table=table,
    )


# ---------------------------------------------------------------- signal level

_SIGNAL_FRAME_COLUMNS = {
    "time": "time",
    "level": "Level",
    "rat": "rat",
    "slot": "slot",
    "hover_detail": "hover_detail",
    "raw_info": "raw_info",
}


@dataclass(frozen=True)
class SignalLevelPoint:
    time: Any
    level: Optional[float]
    rat: str
    slot: Any
    # Per-RAT measurements folded into one hover string. plotly renders hover
    # text as HTML, hence the tags.
    hover_detail: str
    raw_info: str


@dataclass(frozen=True)
class SignalLevelSeries:
    """Signal level over time, one line per RAT and one facet per slot.

    `status` is `"ok"`, `"unavailable"` (no log metadata) or `"no_data"`.
    """

    status: str
    points: List[SignalLevelPoint] = field(default_factory=list)

    def to_frame(self) -> pd.DataFrame:
        rows = [
            {column: getattr(point, attr) for attr, column in _SIGNAL_FRAME_COLUMNS.items()}
            for point in self.points
        ]
        return pd.DataFrame(rows, columns=list(_SIGNAL_FRAME_COLUMNS.values()))


def _hover_detail(row: pd.Series, detail_columns: List[str]) -> str:
    lines = []
    for column in detail_columns:
        value = row[column]
        if pd.notna(value) and str(value) != "None":
            lines.append(f"<b>{column.replace('details_', '')}</b>: {value}")
    return "<br>".join(lines)


def build_signal_level_series(df: pd.DataFrame) -> SignalLevelSeries:
    if not _has_columns(df) or "log_type" not in df.columns:
        return SignalLevelSeries(status="unavailable")

    sig_df = df[df["log_type"] == "Signal_Level"]
    if sig_df.empty:
        return SignalLevelSeries(status="no_data")

    levels = pd.to_numeric(sig_df.get("level", 0), errors="coerce")
    if not isinstance(levels, pd.Series):  # `level` column absent: one value for all rows
        levels = pd.Series(levels, index=sig_df.index)

    detail_columns = [column for column in sig_df.columns if column.startswith("details_")]

    points = [
        SignalLevelPoint(
            time=row.get("time"),
            level=None if pd.isna(level) else float(level),
            rat=row.get("rat", "Unknown"),
            slot=row.get("slot", "0"),
            hover_detail=_hover_detail(row, detail_columns),
            raw_info=row.get("raw_info", ""),
        )
        for (_, row), level in zip(sig_df.iterrows(), levels)
    ]
    return SignalLevelSeries(status="ok", points=points)


# ------------------------------------------------------------------- data call

# Fields the data-call view needs; the parser omits them when a log never
# exercised that path, so they are filled in rather than guarded at every use.
_DATA_CALL_COLUMNS = [
    "status",
    "latency_ms",
    "event_type",
    "req_time",
    "apn",
    "network",
    "protocol",
    "cause",
    "cid",
]

_DATA_CALL_POINT_COLUMNS = [
    "req_time",
    "req_time_dt",
    "apn",
    "status",
    "event_type",
    "network",
    "protocol",
    "cause",
    "latency_ms",
    "cid",
]


@dataclass(frozen=True)
class DataCallKpi:
    attempt_count: int
    success_rate: float
    fail_count: int
    avg_setup_latency_ms: float


@dataclass(frozen=True)
class DataCallSummary:
    """SETUP_DATA_CALL outcomes: headline numbers, scatter points and the log.

    `status` is `"ok"` or `"no_data"`. `points` can still be empty on `"ok"`
    when every event was an unchanged UNSOL update — there are numbers to show
    but no transition to plot.
    """

    status: str
    kpi: Optional[DataCallKpi] = None
    points: List[Dict[str, Any]] = field(default_factory=list)
    table: pd.DataFrame = field(default_factory=pd.DataFrame)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.points, columns=_DATA_CALL_POINT_COLUMNS)


def _data_call_kpi(setup_df: pd.DataFrame) -> DataCallKpi:
    attempts = len(setup_df)
    successes = len(setup_df[setup_df["status"] == "SUCCESS"])
    success_rate = (successes / attempts) * 100 if attempts > 0 else 0.0

    latency = pd.to_numeric(setup_df["latency_ms"], errors="coerce")
    measured = latency[latency > 0]  # 0 means "not measured", not "instant"

    return DataCallKpi(
        attempt_count=attempts,
        success_rate=success_rate,
        fail_count=attempts - successes,
        avg_setup_latency_ms=0.0 if measured.empty else float(measured.mean()),
    )


def build_data_call_summary(
    rows: Optional[List[Dict[str, Any]]],
    *,
    year: Optional[int] = None,
) -> DataCallSummary:
    if not rows:
        return DataCallSummary(status="no_data")

    df = pd.DataFrame(rows)
    for column in _DATA_CALL_COLUMNS:
        if column not in df.columns:
            df[column] = 0 if column == "latency_ms" else "UNKNOWN"

    kpi = _data_call_kpi(df[df["event_type"] == "DATA_SETUP"])

    # An UNSOL update that changed nothing is noise on a transition chart.
    is_changed = df.get("is_changed")
    if is_changed is None:
        noise = pd.Series(False, index=df.index)
    else:
        noise = (df["event_type"] == "UNSOL_UPDATE") & (is_changed == False)  # noqa: E712

    chart_df = df[~noise].copy()
    chart_df["req_time_dt"] = pd.to_datetime(
        str(_log_year(year)) + "-" + chart_df["req_time"].astype(str), errors="coerce"
    )
    chart_df = chart_df.dropna(subset=["req_time_dt"]).sort_values("req_time_dt")

    points = chart_df[_DATA_CALL_POINT_COLUMNS].to_dict("records")
    return DataCallSummary(status="ok", kpi=kpi, points=points, table=df)


# --------------------------------------------------------------------- IMS SIP

_SIP_TABLE_COLUMNS = ["time", "direction", "msg_type", "method_code", "tid", "cseq", "raw_log"]

# Method codes that answer a request successfully; everything else that is not
# flagged as an error is an in-flight message.
_SIP_SUCCESS_CODES = ("200 OK", "202")


@dataclass(frozen=True)
class SipMessage:
    time: str
    time_label: str  # clock part only; the ladder is always one call
    direction: str
    is_outgoing: bool  # UE -> IMS network
    method_code: str
    cseq: str
    is_error: bool
    kind: str  # "error" | "success" | "normal" — the UI picks the color


@dataclass(frozen=True)
class SipFlowKpi:
    transaction_count: int
    error_count: int
    # None when no INVITE/200 OK pair could be timed.
    setup_latency_ms: Optional[int]


@dataclass(frozen=True)
class SipFlow:
    """SIP ladder diagram between the handset and the IMS network.

    `status` is `"ok"` or `"no_data"`.
    """

    status: str
    kpi: Optional[SipFlowKpi] = None
    messages: List[SipMessage] = field(default_factory=list)
    table: pd.DataFrame = field(default_factory=pd.DataFrame)


def _sip_setup_latency_ms(sip_df: pd.DataFrame) -> Optional[int]:
    """Longest INVITE -> 200 OK gap, i.e. how long call setup took."""
    if "method_code" not in sip_df.columns or "time" not in sip_df.columns:
        return None

    times = pd.to_datetime(sip_df["time"], format="%m-%d %H:%M:%S.%f", errors="coerce")
    methods = sip_df["method_code"].astype(str)

    invite = times[methods.str.contains("INVITE", na=False)].min()
    ok = times[methods.str.contains("200 OK", na=False)].max()
    if pd.isna(invite) or pd.isna(ok) or ok < invite:
        return None
    return int((ok - invite).total_seconds() * 1000)


def _sip_kind(method_code: str, is_error: bool) -> str:
    if is_error:
        return "error"
    if any(code in method_code for code in _SIP_SUCCESS_CODES):
        return "success"
    return "normal"


def build_sip_flow(messages: Optional[List[Dict[str, Any]]]) -> SipFlow:
    if not messages:
        return SipFlow(status="no_data")

    sip_df = pd.DataFrame(messages)
    if "is_error" in sip_df.columns:
        errors = sip_df["is_error"].fillna(False).astype(bool)
    else:
        errors = pd.Series(False, index=sip_df.index)

    kpi = SipFlowKpi(
        transaction_count=len(sip_df),
        error_count=int(errors.sum()),
        setup_latency_ms=_sip_setup_latency_ms(sip_df),
    )

    sip_df = sip_df.sort_values("time")
    errors = errors.loc[sip_df.index]

    ladder = []
    for (_, row), is_error in zip(sip_df.iterrows(), errors):
        time_value = str(row.get("time", ""))
        method_code = str(row.get("method_code", ""))
        ladder.append(
            SipMessage(
                time=time_value,
                time_label=time_value.split(" ")[-1],
                direction=str(row.get("direction", "")),
                is_outgoing="Tx" in str(row.get("direction", "")),
                method_code=method_code,
                cseq=str(row.get("cseq", "")),
                is_error=bool(is_error),
                kind=_sip_kind(method_code, bool(is_error)),
            )
        )

    columns = [column for column in _SIP_TABLE_COLUMNS if column in sip_df.columns]
    return SipFlow(status="ok", kpi=kpi, messages=ladder, table=sip_df[columns])


# ------------------------------------------------------------ RILJ transaction

# A completed request slower than this is worth showing even though it worked.
RILJ_SLOW_THRESHOLD_MS = 500

_RILJ_ABNORMAL_COLUMNS = ["Status", "Time", "Command", "Latency(ms)", "Error Code", "Details"]
_RILJ_UNSOL_COLUMNS = ["Time", "Command", "Details"]


@dataclass(frozen=True)
class RiljKpi:
    request_count: int
    timeout_count: int
    error_count: int
    unsol_count: int


@dataclass(frozen=True)
class RiljOverview:
    """RIL requests that timed out, failed or ran slow, plus modem events.

    `status` is `"ok"` or `"no_data"`.
    """

    status: str
    kpi: Optional[RiljKpi] = None
    abnormal: pd.DataFrame = field(default_factory=pd.DataFrame)
    unsol: pd.DataFrame = field(default_factory=pd.DataFrame)
    slow_threshold_ms: int = RILJ_SLOW_THRESHOLD_MS


def _rilj_abnormal_rows(completed: List[Dict], timeouts: List[Dict]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = [
        {
            "Status": "TIMEOUT",
            "Time": timeout.get("time"),
            "Command": timeout.get("command"),
            "Latency(ms)": None,  # a request without a response has no latency
            "Error Code": "NO_RESPONSE",
            "Details": timeout.get("details"),
        }
        for timeout in timeouts
    ]

    for request in completed:
        if request.get("is_error"):
            rows.append(
                {
                    "Status": "ERROR",
                    "Time": request.get("start_time"),
                    "Command": request.get("command"),
                    "Latency(ms)": request.get("latency_ms"),
                    "Error Code": request.get("error_msg"),
                    "Details": f"Req: {request.get('req_details')} | Resp: {request.get('resp_details')}",
                }
            )
        elif request.get("latency_ms", 0) > RILJ_SLOW_THRESHOLD_MS:
            rows.append(
                {
                    "Status": "SLOW",
                    "Time": request.get("start_time"),
                    "Command": request.get("command"),
                    "Latency(ms)": request.get("latency_ms"),
                    "Error Code": "SUCCESS",
                    "Details": request.get("req_details"),
                }
            )

    return rows


def build_rilj_overview(report_data: Optional[Dict[str, Any]]) -> RiljOverview:
    rilj = (report_data or {}).get("rilj_transactions", {}) or {}
    completed = rilj.get("completed", []) or []
    timeouts = rilj.get("timeouts", []) or []
    unsol = rilj.get("unsol", []) or []

    if not completed and not timeouts and not unsol:
        return RiljOverview(status="no_data")

    kpi = RiljKpi(
        request_count=len(completed) + len(timeouts),
        timeout_count=len(timeouts),
        error_count=len([c for c in completed if c.get("is_error")]),
        unsol_count=len(unsol),
    )

    abnormal = pd.DataFrame(
        _rilj_abnormal_rows(completed, timeouts), columns=_RILJ_ABNORMAL_COLUMNS
    )
    # A timeout has no latency at all. Plain floats would print that gap as
    # "NaN"; the nullable integer type renders it as an empty cell instead.
    abnormal["Latency(ms)"] = (
        pd.to_numeric(abnormal["Latency(ms)"], errors="coerce").round().astype("Int64")
    )
    if not abnormal.empty:
        abnormal = abnormal.sort_values(by="Time")

    unsol_table = pd.DataFrame(
        [
            {"Time": event.get("time"), "Command": event.get("command"), "Details": event.get("details")}
            for event in unsol
        ],
        columns=_RILJ_UNSOL_COLUMNS,
    )
    if not unsol_table.empty:
        unsol_table = unsol_table.sort_values(by="Time")

    return RiljOverview(status="ok", kpi=kpi, abnormal=abnormal, unsol=unsol_table)


# ----------------------------------------------------- RF / call joint timeline

# RSRP is logged as free text such as "rsrp=-95dBm"; only the negative number
# is meaningful.
_RSRP_PATTERN = re.compile(r"(-\d+)")
# Timestamps in the report are "MM-DD HH:MM:SS[.mmm]"; the ladder only needs
# second resolution, so the fractional part is cut off.
_SECOND_PRECISION = 14


@dataclass(frozen=True)
class RsrpPoint:
    time_dt: pd.Timestamp
    rsrp_dbm: int
    rat: str
    hover_text: str


@dataclass(frozen=True)
class CallSpan:
    start_dt: pd.Timestamp
    end_dt: pd.Timestamp
    is_drop: bool
    label: str


@dataclass(frozen=True)
class SipErrorMarker:
    time_dt: pd.Timestamp
    label: str


@dataclass(frozen=True)
class RfCallTimeline:
    """Call outcomes laid over the RF conditions they happened in.

    `status` is `"ok"` or `"no_signal_history"` — without RSRP samples there is
    no baseline to lay the calls over, so nothing is drawn.
    """

    status: str
    rsrp_points: List[RsrpPoint] = field(default_factory=list)
    call_spans: List[CallSpan] = field(default_factory=list)
    sip_errors: List[SipErrorMarker] = field(default_factory=list)


def _report_time(value: Any, year: int) -> pd.Timestamp:
    return pd.to_datetime(f"{year}-{str(value)[:_SECOND_PRECISION]}", format="%Y-%m-%d %H:%M:%S")


def _strongest_rat_reading(details: Dict[str, Any], fallback_rat: str) -> tuple:
    """Prefer the sample RAT, then fall back across LTE/NR."""
    ordered_rats = []
    if fallback_rat:
        ordered_rats.append(str(fallback_rat).upper())
    ordered_rats.extend(["LTE", "NR"])
    for rat in dict.fromkeys(ordered_rats):
        if rat in details and details[rat].get("RSRP") != "Unknown":
            return details[rat]["RSRP"], rat
    return "Unknown", fallback_rat


def _rsrp_points(signal_history: List[Dict[str, Any]], year: int) -> List[RsrpPoint]:
    points = []
    for sample in signal_history:
        try:
            time_dt = _report_time(sample.get("time", ""), year)
            details = sample.get("details", {})
            rsrp_str, rat = _strongest_rat_reading(details, sample.get("rat", "LTE"))
            if rsrp_str == "Unknown":
                continue

            match = _RSRP_PATTERN.search(rsrp_str)
            if not match:
                continue

            readings = details.get(rat, {})
            points.append(
                RsrpPoint(
                    time_dt=time_dt,
                    rsrp_dbm=int(match.group(1)),
                    rat=rat,
                    hover_text=(
                        f"Time: {sample.get('time')}<br>"
                        f"RAT: {rat} (Slot {sample.get('slot', '0')})<br>"
                        f"Signal Level: {sample.get('level')}<br>"
                        f"RSRP: {rsrp_str}<br>"
                        f"RSRQ: {readings.get('RSRQ', 'Unknown')}<br>"
                        f"SINR: {readings.get('SINR', 'Unknown')}"
                    ),
                )
            )
        except Exception:
            continue  # one malformed sample must not empty the chart
    return points


def _call_spans(sessions: List[Dict[str, Any]], year: int) -> List[CallSpan]:
    spans = []
    for session in sessions:
        try:
            start_dt = _report_time(session.get("start_time"), year)
            end_time = session.get("end_time")
            # An unfinished session still needs width to be visible.
            end_dt = _report_time(end_time, year) if end_time else start_dt + pd.Timedelta(seconds=5)

            status = str(session.get("status", "")).upper()
            is_drop = "DROP" in status or "FAIL" in status
            call_type = session.get("type", "CALL")

            spans.append(
                CallSpan(
                    start_dt=start_dt,
                    end_dt=end_dt,
                    is_drop=is_drop,
                    label=f"{call_type} 실패/Drop ({session.get('id')})" if is_drop else f"{call_type} 완료",
                )
            )
        except Exception:
            continue
    return spans


def _sip_error_markers(sip_data: List[Dict[str, Any]], year: int) -> List[SipErrorMarker]:
    markers = []
    for message in sip_data:
        if not message.get("is_error"):
            continue
        try:
            markers.append(
                SipErrorMarker(
                    time_dt=_report_time(message.get("time"), year),
                    label=f"{message.get('method_code', 'SIP Error')} ({message.get('direction', 'Tx')})",
                )
            )
        except Exception:
            continue
    return markers


def build_rf_call_timeline(
    report_data: Optional[Dict[str, Any]],
    *,
    year: Optional[int] = None,
) -> RfCallTimeline:
    report_data = report_data or {}
    signal_history = report_data.get("signal_level_history", []) or []
    if not signal_history:
        return RfCallTimeline(status="no_signal_history")

    year = _log_year(year)
    return RfCallTimeline(
        status="ok",
        rsrp_points=_rsrp_points(signal_history, year),
        call_spans=_call_spans(report_data.get("call_sessions", []) or [], year),
        sip_errors=_sip_error_markers(report_data.get("ims_sip_data", []) or [], year),
    )


# ------------------------------------------------------------------------ NITZ

# Representative coordinates per UTC offset, biased towards the regions this
# project's roaming logs come from. The map answers "roughly where was the
# device", not "which country".
UTC_GEO_MAP = {
    9.0: {"lat": 37.5665, "lon": 126.9780, "name": "Korea/Japan (UTC+9)"},
    8.0: {"lat": 39.9042, "lon": 116.4074, "name": "China/Singapore (UTC+8)"},
    7.0: {"lat": 13.7563, "lon": 100.5018, "name": "SE Asia (UTC+7)"},
    5.5: {"lat": 28.6139, "lon": 77.2090, "name": "India (UTC+5.5)"},
    4.0: {"lat": 25.2048, "lon": 55.2708, "name": "UAE/Dubai (UTC+4)"},
    3.0: {"lat": 55.7558, "lon": 37.6173, "name": "Russia/Middle East (UTC+3)"},
    2.0: {"lat": 48.8566, "lon": 2.3522, "name": "Central Europe (UTC+2)"},
    1.0: {"lat": 51.5074, "lon": -0.1278, "name": "UK/Western Europe (UTC+1)"},
    0.0: {"lat": 51.4826, "lon": 0.0077, "name": "GMT/UTC (UTC+0)"},
    -4.0: {"lat": -23.5505, "lon": -46.6333, "name": "Brazil/SA (UTC-4)"},
    -5.0: {"lat": 40.7128, "lon": -74.0060, "name": "US Eastern (UTC-5)"},
    -8.0: {"lat": 34.0522, "lon": -118.2437, "name": "US Pacific (UTC-8)"},
    -10.0: {"lat": 21.3069, "lon": -157.8583, "name": "Hawaii (UTC-10)"},
}

# A timezone that survives less than this is a glitch, not a border crossing.
_NITZ_SETTLED_SECONDS = 600
# Three changes inside an hour means the network is flip-flopping.
_NITZ_PINGPONG_SECONDS = 3600
_NITZ_LONG_STAY_DAYS = 30

_NITZ_CHANGE_COLUMNS = {
    "log_time": "변경 시간",
    "timezone": "타임존",
    "nitz_raw": "원본 NITZ 데이터",
}


@dataclass(frozen=True)
class NitzKpi:
    first_timezone: str
    last_timezone: str
    change_count: int
    stability: str  # "unstable" | "long_stay" | "stable"


@dataclass(frozen=True)
class NitzOffsetPoint:
    log_time_dt: pd.Timestamp
    offset: float


@dataclass(frozen=True)
class NitzGeoPoint:
    offset_label: str
    lat: float
    lon: float
    region: str
    count: int


@dataclass(frozen=True)
class NitzTimeline:
    """Network-supplied timezone over the session, and where it points to.

    `status` is `"ok"` or `"no_data"`.
    """

    status: str
    kpi: Optional[NitzKpi] = None
    offsets: List[NitzOffsetPoint] = field(default_factory=list)
    geo: List[NitzGeoPoint] = field(default_factory=list)
    changes: pd.DataFrame = field(default_factory=pd.DataFrame)

    def offsets_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [{"log_time_dt": point.log_time_dt, "offset_num": point.offset} for point in self.offsets],
            columns=["log_time_dt", "offset_num"],
        )

    def geo_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "offset": point.offset_label,
                    "lat": point.lat,
                    "lon": point.lon,
                    "region": point.region,
                    "count": point.count,
                }
                for point in self.geo
            ],
            columns=["offset", "lat", "lon", "region", "count"],
        )


def _nitz_stability(changes: pd.DataFrame, duration_days: int) -> str:
    if len(changes) >= 3:
        # Compare every second change: two hops in an hour is a ping-pong.
        span = changes["log_time_dt"].diff(periods=2).dt.total_seconds()
        if not changes[span.abs() < _NITZ_PINGPONG_SECONDS].empty:
            return "unstable"
    return "long_stay" if duration_days > _NITZ_LONG_STAY_DAYS else "stable"


def _nitz_geo_points(df: pd.DataFrame) -> List[NitzGeoPoint]:
    points = []
    for offset in df["offset_num"].unique():
        # Offsets with no entry of their own borrow the nearest known region.
        closest = min(UTC_GEO_MAP.keys(), key=lambda known: abs(known - offset))
        region = UTC_GEO_MAP[closest]
        points.append(
            NitzGeoPoint(
                offset_label=f"UTC{'+' if offset > 0 else ''}{offset}",
                lat=region["lat"],
                lon=region["lon"],
                region=region["name"],
                count=int(len(df[df["offset_num"] == offset])),
            )
        )
    return points


def build_nitz_timeline(rows: Optional[List[Dict[str, Any]]]) -> NitzTimeline:
    if not rows:
        return NitzTimeline(status="no_data")

    df = pd.DataFrame(rows)
    df["log_time_dt"] = pd.to_datetime(df["log_time"], errors="coerce")
    df = df.dropna(subset=["log_time_dt"]).sort_values("log_time_dt")
    if df.empty:
        return NitzTimeline(status="no_data")

    # "UTC+9시간" -> 9.0, "UTC+5.5시간" -> 5.5. India and friends sit on a
    # half-hour offset, so the fractional part has to survive the parse.
    df["offset_num"] = (
        df["timezone"].str.extract(r"UTC([+-]?\d+(?:\.\d+)?)").astype(float).fillna(0.0)
    )

    changed = df[df["timezone"] != df["timezone"].shift()].copy()
    if len(changed) > 1:
        # How long each timezone held; the last one is assumed to have settled.
        held = changed["log_time_dt"].diff().shift(-1).dt.total_seconds()
        changed["duration_sec"] = held.fillna(_NITZ_SETTLED_SECONDS + 1)
        settled = changed[changed["duration_sec"] > _NITZ_SETTLED_SECONDS].copy()
    else:
        settled = changed

    duration_days = max(1, (df["log_time_dt"].max() - df["log_time_dt"].min()).days)

    kpi = NitzKpi(
        first_timezone=df["timezone"].iloc[0],
        last_timezone=df["timezone"].iloc[-1],
        change_count=max(0, len(settled) - 1),  # the first timezone is not a change
        stability=_nitz_stability(settled, duration_days),
    )

    offsets = [
        NitzOffsetPoint(log_time_dt=row["log_time_dt"], offset=float(row["offset_num"]))
        for _, row in df.iterrows()
    ]

    columns = [column for column in _NITZ_CHANGE_COLUMNS if column in changed.columns]
    changes = changed[columns].rename(columns=_NITZ_CHANGE_COLUMNS)

    return NitzTimeline(
        status="ok", kpi=kpi, offsets=offsets, geo=_nitz_geo_points(df), changes=changes
    )
