"""Crash / ANR / Binder views.

Built from the parser report. Nothing here imports Streamlit or plotly — see
`core/charts/__init__.py` for the split. The nested log dumps stay as plain
lists of strings; only the truncation each view applies is decided here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Dict, List, Optional

import pandas as pd

# System-level events travel in `binder_warnings` but describe process deaths,
# so the crash list must not show them twice.
SYSTEM_EVENT_TYPES = ("SYSTEM_KILL", "SYSTEM_WTF")

# Binder findings worth tabulating; anything else in `binder_warnings` is a
# system event or a proxy histogram handled elsewhere.
BINDER_EVENT_TYPES = {
    "THREAD_EXHAUSTION",
    "TRANSACTION_DELAY",
    "BINDER_DELAY",
    "BINDER_TRANSACTION_FAILURE",
    "BINDER_BUFFER_ERROR",
    "REPEATED_BINDER_DELAY",
    "BINDER_ONEWAY_SPAM",
}

# More proxy objects than this for one interface means registrations are not
# being balanced by releases.
BINDER_PROXY_LEAK_THRESHOLD = 1000

# How much of each log dump is worth showing.
BINDER_EVENT_DISPLAY_CAP = 300
WTF_RECENT_COUNT = 20
PRE_ANR_LOGCAT_LINES = 120
ANR_CONTEXT_LINES = 80

_KILL_COLUMNS = ["발생 시간", "대상 프로세스", "종료 사유", "원본 로그"]
_WTF_SUMMARY_COLUMNS = ["대상 프로세스", "발생 횟수", "최초 발생", "최근 발생"]
_WTF_RECENT_COLUMNS = ["발생 시간", "대상 프로세스", "원본 로그"]
_BINDER_EVENT_COLUMNS = ["time", "type", "desc"]
_BINDER_TX_COLUMNS = ["from_pid", "from_tid", "to_pid", "to_tid", "code", "raw"]

_UNKNOWN = "Unknown"
_UNKNOWN_TIME = "시간 미상"


def _dicts(values: Any) -> List[Dict[str, Any]]:
    return [value for value in (values or []) if isinstance(value, dict)]


def _tail(values: Any, count: int) -> List[str]:
    return list(values or [])[-count:]


# ----------------------------------------------------------- system kill / wtf


@dataclass(frozen=True)
class SystemWtfSummary:
    """am_wtf events, grouped per process plus the most recent raw lines."""

    total: int
    by_process: pd.DataFrame = field(default_factory=pd.DataFrame)
    recent: pd.DataFrame = field(default_factory=pd.DataFrame)
    recent_count: int = WTF_RECENT_COUNT


def build_system_kills(binder_warnings: Any) -> pd.DataFrame:
    kills = [w for w in _dicts(binder_warnings) if w.get("type") == "SYSTEM_KILL"]
    return pd.DataFrame(
        [
            {
                "발생 시간": kill.get("time", _UNKNOWN),
                "대상 프로세스": kill.get("process", _UNKNOWN),
                "종료 사유": kill.get("desc", kill.get("top_method", _UNKNOWN)),
                "원본 로그": kill.get("raw", kill.get("trigger", "")),
            }
            for kill in kills
        ],
        columns=_KILL_COLUMNS,
    )


def build_system_wtf_summary(binder_warnings: Any) -> SystemWtfSummary:
    wtfs = [w for w in _dicts(binder_warnings) if w.get("type") == "SYSTEM_WTF"]
    if not wtfs:
        return SystemWtfSummary(total=0)

    grouped: Dict[str, Dict[str, Any]] = {}
    for wtf in wtfs:
        process = wtf.get("process", _UNKNOWN)
        timestamp = wtf.get("time", _UNKNOWN)
        if process not in grouped:
            grouped[process] = {"count": 0, "first": timestamp, "last": timestamp}
        grouped[process]["count"] += 1
        if timestamp != _UNKNOWN:  # keep the last time we actually know
            grouped[process]["last"] = timestamp

    by_process = pd.DataFrame(
        [
            {
                "대상 프로세스": process,
                "발생 횟수": f"{data['count']}회",
                "최초 발생": data["first"],
                "최근 발생": data["last"],
            }
            for process, data in grouped.items()
        ],
        columns=_WTF_SUMMARY_COLUMNS,
    )

    recent = pd.DataFrame(
        [
            {
                "발생 시간": wtf.get("time", _UNKNOWN),
                "대상 프로세스": wtf.get("process", _UNKNOWN),
                "원본 로그": wtf.get("raw", wtf.get("trigger", "")),
            }
            for wtf in wtfs[-WTF_RECENT_COUNT:]
        ],
        columns=_WTF_RECENT_COLUMNS,
    )

    return SystemWtfSummary(total=len(wtfs), by_process=by_process, recent=recent)


# --------------------------------------------------------------- binder events


@dataclass(frozen=True)
class BinderSpamEvent:
    time: Any
    desc: str
    raw: str


@dataclass(frozen=True)
class BinderEvents:
    """Binder delays and failures. `status` is `"ok"` or `"none"`."""

    status: str
    spam: List[BinderSpamEvent] = field(default_factory=list)
    events: pd.DataFrame = field(default_factory=pd.DataFrame)
    event_count: int = 0
    display_cap: int = BINDER_EVENT_DISPLAY_CAP
    signals: pd.DataFrame = field(default_factory=pd.DataFrame)
    checklist: List[str] = field(default_factory=list)

    @property
    def truncated(self) -> bool:
        return self.event_count > self.display_cap


def build_binder_events(report_data: Optional[Dict[str, Any]]) -> BinderEvents:
    report_data = report_data or {}
    warnings = _dicts(report_data.get("binder_warnings", []))
    if not warnings:
        return BinderEvents(status="none")

    spam = [
        BinderSpamEvent(time=w.get("time", _UNKNOWN), desc=w.get("desc", ""), raw=w.get("raw", ""))
        for w in warnings
        if w.get("type") == "BINDER_ONEWAY_SPAM"
    ]

    rows = [w for w in warnings if w.get("type") in BINDER_EVENT_TYPES]
    events = pd.DataFrame(rows, columns=_BINDER_EVENT_COLUMNS) if rows else pd.DataFrame()
    if len(events) > BINDER_EVENT_DISPLAY_CAP:
        events = events.tail(BINDER_EVENT_DISPLAY_CAP)

    context = report_data.get("binder_context_summary", {}) or {}
    signal_values = context.get("signals", {}) or {}
    signals = pd.DataFrame(
        [{"구분": name, "매칭 라인 수": count} for name, count in signal_values.items()],
        columns=["구분", "매칭 라인 수"],
    )

    return BinderEvents(
        status="ok",
        spam=spam,
        events=events,
        event_count=len(rows),
        signals=signals,
        checklist=list(context.get("checklist", []) or []),
    )


# -------------------------------------------------------- binder proxy leakage

# Histogram lines look like "android.os.IBinder x 1234".
_PROXY_LINE = re.compile(r"([a-zA-Z_][a-zA-Z0-9\.\$]+)\s*x\s*(\d+)")


@dataclass(frozen=True)
class BinderProxyHistogram:
    time: Any
    max_count: int
    is_leak: bool
    counts: pd.DataFrame = field(default_factory=pd.DataFrame)


def _as_dicts(binder_warnings: Any) -> List[Dict[str, Any]]:
    """Accept the report field however it arrives: dict, list or JSON string."""
    if isinstance(binder_warnings, str):
        try:
            binder_warnings = json.loads(binder_warnings)
        except Exception:
            return []
    if not isinstance(binder_warnings, list):
        binder_warnings = [binder_warnings]

    parsed = []
    for warning in binder_warnings:
        if isinstance(warning, str):
            try:
                warning = json.loads(warning)
            except Exception:
                continue
        if isinstance(warning, dict):
            parsed.append(warning)
    return parsed


def build_binder_proxy_histograms(binder_warnings: Any) -> List[BinderProxyHistogram]:
    histograms = []
    for warning in _as_dicts(binder_warnings):
        if warning.get("type") not in ("BINDER_PROXY_HISTOGRAM", "BINDER_PROXY_LEAK"):
            continue

        rows = []
        for line in str(warning.get("raw", "")).split("\n"):
            match = _PROXY_LINE.search(line)
            if match:
                full_class = match.group(1)
                rows.append(
                    {
                        "Class": full_class.split(".")[-1],  # the bar labels stay readable
                        "FullClass": full_class,
                        "Count": int(match.group(2)),
                    }
                )

        counts = pd.DataFrame(rows, columns=["Class", "FullClass", "Count"])
        if not counts.empty:
            # Ascending, because a horizontal bar chart draws the first row lowest.
            counts = counts.sort_values(by="Count", ascending=True)

        max_count = warning.get("max_count", 0)
        histograms.append(
            BinderProxyHistogram(
                time=warning.get("time", _UNKNOWN),
                max_count=max_count,
                is_leak=max_count > BINDER_PROXY_LEAK_THRESHOLD,
                counts=counts,
            )
        )
    return histograms


# ------------------------------------------------------------------------ ANR


@dataclass(frozen=True)
class AnrSummary:
    has_main_stack: bool = False
    has_lock_contention: bool = False
    has_active_binder: bool = False
    has_pre_anr_logcat: bool = False
    has_cpu_hint: bool = False
    has_system_server_hint: bool = False
    has_io_hint: bool = False


@dataclass(frozen=True)
class AnrLockChain:
    lock_address: Any
    blocker_thread: Any
    blocker_stack: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class AnrEvent:
    time: Any
    process: Any
    reason: Any
    pid: Any
    summary: Optional[AnrSummary] = None
    pre_logcat: List[str] = field(default_factory=list)
    cpu_logs: List[str] = field(default_factory=list)
    system_server_logs: List[str] = field(default_factory=list)
    io_logs: List[str] = field(default_factory=list)
    lock_chain: Optional[AnrLockChain] = None
    binder_transactions: pd.DataFrame = field(default_factory=pd.DataFrame)
    main_stack: List[str] = field(default_factory=list)

    @property
    def has_context_logs(self) -> bool:
        return bool(self.cpu_logs or self.system_server_logs or self.io_logs)


def _anr_summary(values: Dict[str, Any]) -> Optional[AnrSummary]:
    if not values:
        return None
    return AnrSummary(
        **{key: bool(values.get(key)) for key in AnrSummary.__dataclass_fields__}
    )


def _anr_lock_chain(values: Dict[str, Any]) -> Optional[AnrLockChain]:
    # Without a blocking thread there is no chain to show, only a lock address.
    if not values or not values.get("blocker_thread"):
        return None
    return AnrLockChain(
        lock_address=values.get("lock_address"),
        blocker_thread=values.get("blocker_thread"),
        blocker_stack=list(values.get("blocker_stack") or []),
    )


def _anr_binder_transaction_row(transaction: Dict[str, Any]) -> Dict[str, Any]:
    row = {column: transaction.get(column, "-") for column in _BINDER_TX_COLUMNS}
    row["raw"] = transaction.get("raw", "")  # a missing raw line is blank, not a dash
    return row


def _anr_binder_transactions(transactions: List[Dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        [_anr_binder_transaction_row(transaction) for transaction in transactions or []],
        columns=_BINDER_TX_COLUMNS,
    )


def _anr_event(anr: Dict[str, Any]) -> AnrEvent:
    context = anr.get("context_analysis", {}) or {}
    return AnrEvent(
        time=anr.get("time", _UNKNOWN_TIME),
        process=anr.get("process", "Unknown Process"),
        reason=anr.get("reason", "Unknown Reason"),
        pid=(anr.get("process_info", {}) or {}).get("pid", _UNKNOWN),
        summary=_anr_summary(anr.get("analysis_summary", {}) or {}),
        pre_logcat=_tail(anr.get("pre_anr_logcat"), PRE_ANR_LOGCAT_LINES),
        cpu_logs=_tail(context.get("cpu_logs"), ANR_CONTEXT_LINES),
        system_server_logs=_tail(context.get("system_server_logs"), ANR_CONTEXT_LINES),
        io_logs=_tail(context.get("io_logs"), ANR_CONTEXT_LINES),
        lock_chain=_anr_lock_chain(anr.get("lock_chain", {}) or {}),
        binder_transactions=_anr_binder_transactions(anr.get("active_binder_transactions", [])),
        main_stack=list((anr.get("main", {}) or {}).get("stack") or []),
    )


def normalize_anr_list(anr_data: Any) -> List[Dict[str, Any]]:
    """A single ANR may arrive as a bare dict instead of a one-item list."""
    if isinstance(anr_data, dict) and anr_data:
        return [anr_data]
    if isinstance(anr_data, list):
        return anr_data
    return []


# --------------------------------------------------------------------- crashes


@dataclass(frozen=True)
class NativeCrash:
    time: Any
    process: Any
    signal: Any
    abort_message: Any
    callstack: pd.DataFrame = field(default_factory=pd.DataFrame)
    cross_context_logs: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class JavaCrash:
    time: Any
    process: Any
    crash_type: str
    is_kernel: bool
    exception_info: Any = None
    top_method: Any = None
    pre_context: List[str] = field(default_factory=list)
    call_stack: List[str] = field(default_factory=list)
    cross_context_logs: List[str] = field(default_factory=list)
    trigger: Optional[str] = None
    # An oversized Intent shows up as this exception somewhere in the raw logs.
    suspects_transaction_too_large: bool = False


def _native_crash(crash: Dict[str, Any]) -> NativeCrash:
    return NativeCrash(
        time=crash.get("timestamp", _UNKNOWN_TIME),
        process=crash.get("process", _UNKNOWN),
        signal=crash.get("signal", _UNKNOWN),
        abort_message=crash.get("abort_message", "none"),
        callstack=pd.DataFrame(crash.get("callstack") or []),
        cross_context_logs=list(crash.get("cross_context_logs") or []),
    )


def _java_crash(crash: Dict[str, Any]) -> JavaCrash:
    is_kernel = bool(crash.get("is_kernel", False))
    top_method = crash.get("top_method")
    raw_logs = str(crash.get("cross_context_logs", crash.get("trigger", ""))).lower()

    return JavaCrash(
        time=crash.get("timestamp", crash.get("time", _UNKNOWN_TIME)),
        process=crash.get("process", "Unknown Process"),
        crash_type=(
            "KERNEL PANIC / MODEM CRASH"
            if is_kernel
            else crash.get("crash_type", crash.get("type", "FATAL EXCEPTION"))
        ),
        is_kernel=is_kernel,
        exception_info=crash.get("exception_info"),
        top_method=top_method if top_method and top_method != _UNKNOWN else None,
        pre_context=list(crash.get("context") or []),
        call_stack=list(crash.get("call_stack") or []),
        cross_context_logs=list(crash.get("cross_context_logs") or []),
        trigger=crash.get("trigger"),
        suspects_transaction_too_large="transactiontoolargeexception" in raw_logs,
    )


@dataclass(frozen=True)
class CrashOverview:
    """Everything the crash page shows. `status` is `"ok"` or `"clean"`."""

    status: str
    system_kills: pd.DataFrame = field(default_factory=pd.DataFrame)
    system_wtf: SystemWtfSummary = field(default_factory=lambda: SystemWtfSummary(total=0))
    binder: BinderEvents = field(default_factory=lambda: BinderEvents(status="none"))
    native_crashes: List[NativeCrash] = field(default_factory=list)
    anr_events: List[AnrEvent] = field(default_factory=list)
    java_crashes: List[JavaCrash] = field(default_factory=list)


def build_crash_overview(report_data: Optional[Dict[str, Any]]) -> CrashOverview:
    report_data = report_data or {}

    crashes = report_data.get("crash_context", []) or []
    native_crashes = report_data.get("native_crash_context", []) or []
    anr_events = normalize_anr_list(report_data.get("anr_context", []))
    binder_warnings = report_data.get("binder_warnings", []) or []

    if not crashes and not anr_events and not native_crashes and not binder_warnings:
        return CrashOverview(status="clean")

    return CrashOverview(
        status="ok",
        system_kills=build_system_kills(binder_warnings),
        system_wtf=build_system_wtf_summary(binder_warnings),
        binder=build_binder_events(report_data),
        native_crashes=[_native_crash(crash) for crash in _dicts(native_crashes)],
        anr_events=[_anr_event(anr) for anr in _dicts(anr_events)],
        # System kill/wtf events ride along in `binder_warnings`; they have
        # their own sections, so they must not repeat as app crashes.
        java_crashes=[
            _java_crash(crash)
            for crash in _dicts(crashes)
            if crash.get("type") not in SYSTEM_EVENT_TYPES
        ],
    )
