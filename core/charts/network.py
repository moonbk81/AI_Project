"""Network chart series: DNS, data usage and internet-stall analysis.

Built from the Chroma metadata frame and the internet-stall report. Nothing
here imports a web framework or plotly — see `core/charts/__init__.py` for the
split.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from .common import has_columns, slice_log_type as _slice, with_parsed_times

# A DNS answer that is neither of these is a failure or a block.
DNS_SUCCESS_CODES = ("0", "SUCCESS")


# ------------------------------------------------------------------ DNS errors


@dataclass(frozen=True)
class DnsErrorBreakdown:
    """Failed DNS queries per package and error code.

    `status` is `"ok"`, `"unavailable"` (no DNS rows, or rows without the
    package/return-code fields) or `"no_errors"`.
    """

    status: str
    counts: pd.DataFrame = field(default_factory=pd.DataFrame)
    # app_name x return_code matrix shown next to the chart.
    pivot: pd.DataFrame = field(default_factory=pd.DataFrame)


def build_dns_error_breakdown(df: pd.DataFrame) -> DnsErrorBreakdown:
    dns_df = _slice(df, "DNS_Query")
    if dns_df.empty or "return_code" not in dns_df.columns or "app_name" not in dns_df.columns:
        return DnsErrorBreakdown(status="unavailable")

    error_df = dns_df[~dns_df["return_code"].isin(DNS_SUCCESS_CODES)]
    if error_df.empty:
        return DnsErrorBreakdown(status="no_errors")

    return DnsErrorBreakdown(
        status="ok",
        counts=error_df.groupby(["app_name", "return_code"]).size().reset_index(name="count"),
        pivot=error_df.pivot_table(
            index="app_name", columns="return_code", aggfunc="size", fill_value=0
        ),
    )


# ---------------------------------------------------------- DNS server health


@dataclass(frozen=True)
class DnsHealthWarning:
    """A DNS server the parser judged unreachable for a whole network."""

    net_id: Any
    server_ip: Any
    score: Any
    timeout_count: Any
    description: str


def build_dns_health_warnings(df: pd.DataFrame) -> List[DnsHealthWarning]:
    warning_df = _slice(df, "DNS_Health_Warning")
    return [
        DnsHealthWarning(
            net_id=row.get("net_id", "Unknown"),
            server_ip=row.get("server_ip", "Unknown"),
            score=row.get("score", 0),
            timeout_count=row.get("timeout_count", 0),
            description=row.get("description", ""),
        )
        for _, row in warning_df.iterrows()
    ]


# ------------------------------------------------------------------ DNS issues

_DNS_ISSUE_COLUMNS = {
    "time": "Time",
    "net_id": "NetID",
    "package": "Package",
    "result": "Result/Error Code",
    "suspected_reason": "Suspected Reason",
}


@dataclass(frozen=True)
class DnsIssueSummary:
    """Blocked/failed DNS lookups, by suspected reason and by package.

    `status` is `"ok"` or `"no_data"`.
    """

    status: str
    # Reasons in log order — the pie counts them itself, so slice colors keep
    # the order the log produced.
    reasons: List[str] = field(default_factory=list)
    package_counts: pd.DataFrame = field(default_factory=pd.DataFrame)
    table: pd.DataFrame = field(default_factory=pd.DataFrame)


def build_dns_issue_summary(df: pd.DataFrame) -> DnsIssueSummary:
    dns_df = _slice(df, "Network_DNS_Issue")
    if dns_df.empty:
        return DnsIssueSummary(status="no_data")

    package_counts = dns_df["package"].value_counts().reset_index()
    package_counts.columns = ["package", "count"]

    columns = [column for column in _DNS_ISSUE_COLUMNS if column in dns_df.columns]
    table = dns_df[columns].rename(columns=_DNS_ISSUE_COLUMNS)

    return DnsIssueSummary(
        status="ok",
        reasons=dns_df["suspected_reason"].tolist(),
        package_counts=package_counts,
        table=table,
    )


# ------------------------------------------------------- network timeline stat

# A window whose mean DNS latency reaches this is a spike on its own, even when
# the parser flagged no individual query as delayed.
DNS_SPIKE_THRESHOLD_MS = 1000

_TIMELINE_NUMERIC_COLUMNS = [
    "dns_avg",
    "dns_err_rate",
    "tcp_avg_loss",
    "dns_max",
    "dns_delayed_cnt",
    "dns_blocked_cnt",
]

_SPIKE_COLUMNS = [
    "time",
    "netId",
    "transport",
    "dns_avg",
    "dns_max",
    "dns_err_rate",
    "dns_delayed_cnt",
    "dns_blocked_cnt",
]


@dataclass(frozen=True)
class MetricOption:
    """One selectable line for the timeline chart."""

    label: str
    column: str
    unit: str


@dataclass(frozen=True)
class NetworkTimelineStats:
    """Per-window DNS/TCP statistics over the session.

    `status` is `"ok"`, `"no_data"` or `"unparsable_time"` (rows exist but no
    timestamp survived parsing, so there is no axis to draw them on).
    """

    status: str
    frame: pd.DataFrame = field(default_factory=pd.DataFrame)
    metrics: List[MetricOption] = field(default_factory=list)
    spikes: pd.DataFrame = field(default_factory=pd.DataFrame)
    spike_threshold_ms: int = DNS_SPIKE_THRESHOLD_MS


def _timeline_metrics(ts_df: pd.DataFrame) -> List[MetricOption]:
    metrics = [
        MetricOption("DNS 평균 응답 시간(ms)", "dns_avg", "ms"),
        MetricOption("DNS 오류율(%)", "dns_err_rate", "%"),
    ]
    # TCP loss is only collected on some builds; offer it when it has values.
    if "tcp_avg_loss" in ts_df.columns and ts_df["tcp_avg_loss"].notna().any():
        metrics.append(MetricOption("TCP 평균 손실률(%)", "tcp_avg_loss", "%"))
    return metrics


def build_network_timeline_stats(
    df: pd.DataFrame,
    *,
    year: Optional[int] = None,
) -> NetworkTimelineStats:
    ts_df = _slice(df, "Network_Timeline_Stat")
    if ts_df.empty:
        return NetworkTimelineStats(status="no_data")

    ts_df = ts_df.copy()
    for column in _TIMELINE_NUMERIC_COLUMNS:
        if column in ts_df.columns:
            ts_df[column] = pd.to_numeric(ts_df[column], errors="coerce")

    ts_df = with_parsed_times(ts_df, "time", year=year)
    if ts_df.empty:
        return NetworkTimelineStats(status="unparsable_time")

    ts_df["netId"] = ts_df["netId"].astype(str)  # a network id is a label, not a number
    if "dns_delayed_cnt" not in ts_df.columns:
        ts_df["dns_delayed_cnt"] = 0

    spike_df = ts_df[
        (ts_df["dns_avg"] >= DNS_SPIKE_THRESHOLD_MS)
        | (pd.to_numeric(ts_df["dns_delayed_cnt"], errors="coerce").fillna(0) > 0)
    ]
    spike_columns = [column for column in _SPIKE_COLUMNS if column in spike_df.columns]
    spikes = spike_df[spike_columns].sort_values("dns_avg", ascending=False)

    return NetworkTimelineStats(
        status="ok", frame=ts_df, metrics=_timeline_metrics(ts_df), spikes=spikes
    )


# ------------------------------------------------------------------ data usage

_DATA_USAGE_TOP_APPS = 10


@dataclass(frozen=True)
class DataUsageProfile:
    """Cellular data volume by app and by RAT, plus its distribution in time.

    `status` is `"ok"`, `"unavailable"` (no log metadata) or `"no_data"`.
    `timeline_status` is `"ok"`, `"absent"` (the rows carry no timestamp, so the
    section is skipped entirely) or `"empty"` (timestamps exist but none parse).
    """

    status: str
    app_totals: pd.DataFrame = field(default_factory=pd.DataFrame)
    rat_totals: pd.DataFrame = field(default_factory=pd.DataFrame)
    timeline_status: str = "absent"
    timeline: pd.DataFrame = field(default_factory=pd.DataFrame)


def _usage_frame(df: pd.DataFrame) -> pd.DataFrame:
    du_df = _slice(df, "Data_Usage").copy()
    if not du_df.empty:
        du_df["total_mb"] = pd.to_numeric(du_df["total_mb"], errors="coerce")
    return du_df


def build_data_usage_profile(
    df: pd.DataFrame,
    *,
    year: Optional[int] = None,
) -> DataUsageProfile:
    if not has_columns(df) or "log_type" not in df.columns:
        return DataUsageProfile(status="unavailable")

    du_df = _usage_frame(df)
    if du_df.empty:
        return DataUsageProfile(status="no_data")

    app_totals = (
        du_df.groupby("app_name")["total_mb"]
        .sum()
        .reset_index()
        .sort_values(by="total_mb", ascending=False)
        .head(_DATA_USAGE_TOP_APPS)
    )
    rat_totals = du_df.groupby("rat")["total_mb"].sum().reset_index()

    if "time" not in du_df.columns:
        return DataUsageProfile(
            status="ok", app_totals=app_totals, rat_totals=rat_totals, timeline_status="absent"
        )

    timeline = with_parsed_times(du_df, "time", year=year)
    return DataUsageProfile(
        status="ok",
        app_totals=app_totals,
        rat_totals=rat_totals,
        timeline_status="ok" if not timeline.empty else "empty",
        timeline=timeline,
    )


# -------------------------------------------------------------- internet stall


@dataclass(frozen=True)
class LayerTab:
    title: str
    layers: Tuple[str, ...]


# Which parser layers belong under which tab. Validation groups the three
# layers that all describe "the network came up but did not work".
INTERNET_STALL_LAYER_TABS = [
    LayerTab("DNS", ("DNS",)),
    LayerTab("DataCall/Stall", ("DATA_CALL", "DATA_STALL")),
    LayerTab("Validation", ("VALIDATION", "NETWORK", "ROUTING")),
    LayerTab("RF", ("RF",)),
    LayerTab("TCP/TLS", ("TCP_TLS",)),
    LayerTab("전원", ("POWER",)),
]

_TIMELINE_HOVER_COLUMNS = ["time", "event_type", "reason", "net_id", "apn", "cid"]
_TIMELINE_TABLE_COLUMNS = ["time", "layer", "event_type", "severity", "reason", "net_id", "apn", "cid", "raw"]
_LAYER_TABLE_COLUMNS = ["time", "event_type", "severity", "reason", "net_id", "apn", "cid", "raw"]
_RELATED_TABLE_COLUMNS = ["time", "layer", "event_type", "severity", "reason", "apn", "cid", "raw"]
_ROOT_CAUSE_COLUMNS = ["category", "count", "high", "medium", "low", "example_time", "example_trigger"]
_WINDOW_COLUMNS = [
    "idx",
    "center_time",
    "trigger",
    "severity_score",
    "primary_category",
    "confidence",
    "layer_counts",
]


@dataclass(frozen=True)
class InternetStallKpi:
    stall_window_count: int = 0
    high_risk_window_count: int = 0
    primary_root_cause_candidate: str = "UNKNOWN"
    total_timeline_events: int = 0
    dns_issue_count: int = 0
    validation_fail_count: int = 0
    data_stall_count: int = 0
    rf_warning_count: int = 0
    data_call_fail_or_drop_count: int = 0
    tcp_tls_timeout_count: int = 0
    power_idle_hint_count: int = 0


@dataclass(frozen=True)
class LayerView:
    """Events of one layer group. `status` is `"ok"` or `"empty"`."""

    status: str
    counts: pd.DataFrame = field(default_factory=pd.DataFrame)
    table: pd.DataFrame = field(default_factory=pd.DataFrame)


@dataclass(frozen=True)
class StallWindow:
    """One suspected stall, with the events recorded around it."""

    idx: int
    center_time: Any
    trigger: Any
    severity_score: Any
    primary_category: str
    confidence: str
    layer_counts_json: str
    root_cause_candidates: List[Dict[str, Any]] = field(default_factory=list)
    # Raw events keep `context_before`, which the log view prints verbatim.
    related_events: List[Dict[str, Any]] = field(default_factory=list)
    related_table: pd.DataFrame = field(default_factory=pd.DataFrame)


@dataclass(frozen=True)
class InternetStallReport:
    """The internet-stall analysis, section by section.

    `status` is `"ok"` or `"no_data"`. `timeline_status` is `"ok"`, `"empty"`
    (no events at all) or `"unparsable_time"`.
    """

    status: str
    kpi: InternetStallKpi = field(default_factory=InternetStallKpi)
    root_causes: pd.DataFrame = field(default_factory=pd.DataFrame)
    timeline_status: str = "empty"
    timeline: pd.DataFrame = field(default_factory=pd.DataFrame)
    timeline_hover_columns: List[str] = field(default_factory=list)
    timeline_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    windows: List[StallWindow] = field(default_factory=list)

    def windows_frame(self) -> pd.DataFrame:
        """Windows worst-first; `idx` still points into `windows`."""
        frame = pd.DataFrame(
            [
                {
                    "idx": window.idx,
                    "center_time": window.center_time,
                    "trigger": window.trigger,
                    "severity_score": window.severity_score,
                    "primary_category": window.primary_category,
                    "confidence": window.confidence,
                    "layer_counts": window.layer_counts_json,
                }
                for window in self.windows
            ],
            columns=_WINDOW_COLUMNS,
        )
        return frame if frame.empty else frame.sort_values("severity_score", ascending=False)

    def layer_view(self, layers: Sequence[str]) -> LayerView:
        if self.timeline.empty:
            return LayerView(status="empty")

        layer_df = self.timeline[self.timeline["layer"].isin(list(layers))]
        if layer_df.empty:
            return LayerView(status="empty")

        counts = layer_df["event_type"].value_counts().reset_index()
        counts.columns = ["event_type", "count"]
        columns = [column for column in _LAYER_TABLE_COLUMNS if column in layer_df.columns]
        return LayerView(status="ok", counts=counts, table=layer_df[columns])


def _root_cause_rows(root_summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for category, info in root_summary.items():
        confidence = info.get("confidence", {}) or {}
        examples = info.get("examples", []) or []
        rows.append(
            {
                "category": category,
                "count": info.get("count", 0),
                "high": confidence.get("high", 0),
                "medium": confidence.get("medium", 0),
                "low": confidence.get("low", 0),
                # The first example is the earliest one the parser kept.
                "example_time": examples[0].get("time") if examples else "-",
                "example_trigger": examples[0].get("trigger") if examples else "-",
            }
        )
    return rows


def _stall_windows(windows: List[Dict[str, Any]], year: Optional[int]) -> List[StallWindow]:
    built = []
    for idx, window in enumerate(windows):
        candidates = window.get("root_cause_candidates", []) or []
        primary = candidates[0] if candidates else {}
        related = window.get("related_events", []) or []

        related_df = pd.DataFrame(related)
        if not related_df.empty:
            related_df = with_parsed_times(related_df, "time", year=year)
            columns = [column for column in _RELATED_TABLE_COLUMNS if column in related_df.columns]
            related_df = related_df[columns]

        built.append(
            StallWindow(
                idx=idx,
                center_time=window.get("center_time"),
                trigger=window.get("trigger"),
                severity_score=window.get("severity_score"),
                primary_category=primary.get("category", "UNKNOWN"),
                confidence=primary.get("confidence", "unknown"),
                layer_counts_json=json.dumps(window.get("layer_counts", {}), ensure_ascii=False),
                root_cause_candidates=candidates,
                related_events=related,
                related_table=related_df,
            )
        )
    return built


def build_internet_stall_report(
    data: Optional[Dict[str, Any]],
    *,
    year: Optional[int] = None,
) -> InternetStallReport:
    if not data:
        return InternetStallReport(status="no_data")

    kpi_values = data.get("kpi", {}) or {}
    kpi = InternetStallKpi(
        **{
            key: kpi_values[key]
            for key in InternetStallKpi.__dataclass_fields__
            if key in kpi_values
        }
    )

    root_causes = pd.DataFrame(
        _root_cause_rows(data.get("root_cause_summary", {}) or {}), columns=_ROOT_CAUSE_COLUMNS
    )

    timeline = pd.DataFrame(data.get("timeline", []) or [])
    if timeline.empty:
        timeline_status = "empty"
    else:
        timeline = with_parsed_times(timeline, "time", year=year)
        timeline_status = "ok" if not timeline.empty else "unparsable_time"

    hover_columns: List[str] = []
    timeline_table = pd.DataFrame()
    if timeline_status == "ok":
        # Both are optional in the parser output but required by the chart.
        if "severity" not in timeline.columns:
            timeline["severity"] = "info"
        if "layer" not in timeline.columns:
            timeline["layer"] = "UNKNOWN"
        hover_columns = [column for column in _TIMELINE_HOVER_COLUMNS if column in timeline.columns]
        timeline_table = timeline[
            [column for column in _TIMELINE_TABLE_COLUMNS if column in timeline.columns]
        ]

    return InternetStallReport(
        status="ok",
        kpi=kpi,
        root_causes=root_causes,
        timeline_status=timeline_status,
        timeline=timeline if timeline_status == "ok" else pd.DataFrame(),
        timeline_hover_columns=hover_columns,
        timeline_table=timeline_table,
        windows=_stall_windows(data.get("stall_windows", []) or [], year),
    )
