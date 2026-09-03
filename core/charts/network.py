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

from .common import (
    has_columns,
    parse_log_times,
    slice_log_type as _slice,
    with_parsed_times,
)

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


# --------------------------------------------------------- app block windows

# Bars this short are a blip the reader cannot act on; they still count in the
# tiles, they just do not earn a row on the timeline.
_BLOCK_WINDOW_MIN_SECONDS = 1.0

# 한 세션이 앱 서른 개를 얼리는 일은 흔하다(안드로이드가 상시로 하는 일이다).
# 서른 줄을 한 카드에 밀어 넣으면 막대가 실선이 되고 라벨이 겹쳐 아무것도
# 못 읽는다. 그래서 전부 넘기되, 기본 화면은 오래 차단된 앱 위주로 자른다.
# 나머지는 카드의 앱 선택기로 골라 본다.
_BLOCK_DEFAULT_TOP_APPS = 10
_BLOCK_WINDOW_LIMIT = 200

# A window with no unblock line is still blocked as far as the log knows, so its
# bar runs to the last event in view rather than stopping at an invented end.
# When it *is* the last event there is nothing to run to, so it gets a floor —
# a share of the plotted span, because a fixed 30s is an invisible sliver on a
# long session and the whole point is that this row is the alarming one.
_OPEN_WINDOW_MIN_SECONDS = 30.0
_OPEN_WINDOW_MIN_SPAN_SHARE = 0.05

_BLOCK_TABLE_COLUMNS = {
    "package": "앱",
    "uid": "UID",
    "blocked_at": "차단 시작",
    "end_at": "차단 해제",
    "duration_sec": "지속(초)",
    "cause_label": "원인",
    "freeze_reason": "Freeze 사유",
}


@dataclass(frozen=True)
class AppBlockWindow:
    """One app's network-blocked span, as the Gantt row draws it."""

    package: str
    uid: str
    cause: str
    cause_label: str
    start_dt: Any
    end_dt: Any
    duration_sec: Optional[float]
    is_recovered: bool
    sockets_destroyed: bool
    resumed: bool
    hover_text: str


@dataclass(frozen=True)
class AppBlockedApp:
    """One app in the picker: how much of the session it spent blocked."""

    package: str
    window_count: int
    longest_sec: Optional[float]
    total_sec: float
    frozen: bool
    # 차단된 동안 실제로 막힌 DNS 요청 수. 이 앱의 차단이 사용자에게
    # 증상으로 보였는지를 가르는 값이라 선택기의 정렬 기준이 된다.
    dns_issue_count: int = 0


@dataclass(frozen=True)
class AppBlockWindows:
    """Per-app UID network blocks: when, how long, and whether a freeze caused it.

    `status` is `"ok"`, `"no_data"` (the session logged no block) or
    `"unparsable_time"`. `windows` carries every drawable span; `apps` is the
    picker's list and `default_apps` is the subset the card opens on. The tiles
    count every window, including the sub-second ones the timeline drops.
    """

    status: str
    windows: List[AppBlockWindow] = field(default_factory=list)
    apps: List[AppBlockedApp] = field(default_factory=list)
    default_apps: List[str] = field(default_factory=list)
    # 기본 화면을 무슨 기준으로 골랐는지: "dns_issues" 또는 "block_duration".
    # 카드가 제목을 그에 맞게 붙여야 독자가 목록을 오해하지 않는다.
    default_basis: str = "block_duration"
    table: pd.DataFrame = field(default_factory=pd.DataFrame)
    window_count: int = 0
    # 얼린 게 확인된 구간 전부와, 그중 사유(Bg)까지 남은 구간.
    freeze_count: int = 0
    background_freeze_count: int = 0
    app_count: int = 0
    longest_sec: Optional[float] = None
    unrecovered_count: int = 0
    # Rows dropped from the timeline for being too short to see.
    hidden_count: int = 0


# 프리즈 사유가 로그에 남은 경우와, 얼린 사실만 확인되는 경우를 구분한다.
_FROZEN_CAUSES = ("APP_BACKGROUND_FREEZE", "APP_PROCESS_FREEZE")

_CAUSE_LABELS = {
    "APP_BACKGROUND_FREEZE": "백그라운드 프리즈",
    "APP_PROCESS_FREEZE": "프로세스 프리즈(사유 미상)",
    "UID_NETWORK_BLOCK": "UID 네트워크 차단",
}


def _block_hover(row: pd.Series, duration: Optional[float], recovered: bool) -> str:
    lines = [
        f"<b>{row.get('package', 'Unknown')}</b> (UID {row.get('uid', '?')})",
        _CAUSE_LABELS.get(row.get("cause"), row.get("cause") or "차단"),
        f"시작 {row.get('blocked_at', '?')}",
    ]
    if recovered:
        lines.append(f"해제 {row.get('end_at', '?')}")
        if duration is not None:
            lines.append(f"지속 {duration:.1f}초")
    else:
        lines.append("해제 로그 없음")
    if row.get("freeze_reason"):
        lines.append(f"Freeze 사유 {row['freeze_reason']}")
    if row.get("sockets_destroyed_at"):
        lines.append("연결돼 있던 TCP 소켓 강제 종료")
    if row.get("resumed_at"):
        lines.append(f"앱 복귀 {row['resumed_at']}")
    return "<br>".join(lines)


def _dns_issue_counts(df: pd.DataFrame) -> Dict[str, int]:
    """차단당한 DNS 요청의 패키지별 건수.

    `Network_DNS_Issue` 에는 정책 차단(`is_blocked`)과 단순 응답 실패(NODATA
    타임아웃)가 같이 들어 있다. 이 카드가 설명하려는 건 차단이므로 타임아웃은
    빼고 센다. 안 그러면 타임아웃만 난 앱이 "요청이 막힌 앱" 목록에 올라온다.
    """
    dns_df = _slice(df, "Network_DNS_Issue")
    if dns_df.empty or "package" not in dns_df.columns:
        return {}
    if "is_blocked" in dns_df.columns:
        blocked = dns_df["is_blocked"].astype(object).where(
            lambda values: values.notna(), False
        ).astype(bool)
        dns_df = dns_df[blocked]
    if dns_df.empty:
        return {}
    return {str(pkg): int(n) for pkg, n in dns_df["package"].value_counts().items()}


def _blocked_apps(drawable: pd.DataFrame, dns_counts: Dict[str, int]) -> List[AppBlockedApp]:
    """선택기에 채울 앱 목록. 막힌 DNS 요청이 많은 앱이 앞으로 온다.

    안드로이드는 시스템 앱을 상시로 얼리기 때문에 차단 '시간'으로 줄을 세우면
    25분씩 얼려 있던 scpm·dkey 같은 앱이 위를 다 차지한다. 그런데 그 앱들은
    차단되는 동안 통신을 시도하지도 않아서 사용자에게는 아무 증상이 없다.
    실제 신고로 이어지는 건 "막힌 동안 접속을 시도했다가 실패한" 앱이므로,
    같은 시간대의 DNS 실패 건수를 1순위로 놓고 차단 길이를 동점 처리에 쓴다.
    """
    apps = []
    for package, rows in drawable.groupby("package", sort=False):
        durations = rows["duration_sec"].dropna()
        apps.append(
            AppBlockedApp(
                package=str(package),
                window_count=len(rows),
                longest_sec=float(durations.max()) if not durations.empty else None,
                total_sec=float(durations.sum()),
                frozen=bool(rows["cause"].isin(_FROZEN_CAUSES).any())
                if "cause" in rows.columns
                else False,
                dns_issue_count=dns_counts.get(str(package), 0),
            )
        )
    # 길이를 모르는(해제 로그 없는) 구간은 끝을 알 수 없으니 동점에서 앞에 둔다.
    return sorted(
        apps,
        key=lambda app: (
            app.dns_issue_count,
            app.longest_sec is None,
            app.longest_sec or 0,
        ),
        reverse=True,
    )


def build_app_block_windows(
    df: pd.DataFrame,
    *,
    year: Optional[int] = None,
) -> AppBlockWindows:
    block_df = _slice(df, "App_Network_Block_Window")
    if block_df.empty:
        return AppBlockWindows(status="no_data")

    block_df = block_df.copy()
    # A window closed by am_unfreeze alone still has a real end; prefer the
    # ConnectivityService line when both are present.
    unblocked = block_df.get("unblocked_at", pd.Series(dtype=object))
    unfreeze = block_df.get("unfreeze_at", pd.Series(dtype=object))
    block_df["end_at"] = unblocked.where(unblocked.notna() & (unblocked != ""), unfreeze)

    block_df = with_parsed_times(block_df, "blocked_at", year=year)
    if block_df.empty:
        return AppBlockWindows(status="unparsable_time")

    block_df = block_df.rename(columns={"time_dt": "start_dt"})
    block_df["end_dt"] = parse_log_times(block_df["end_at"], year=year)
    block_df["duration_sec"] = pd.to_numeric(
        block_df.get("duration_sec", pd.Series(dtype=float)), errors="coerce"
    )
    block_df["cause_label"] = block_df.get("cause", pd.Series(dtype=object)).map(
        lambda value: _CAUSE_LABELS.get(value, value or "차단")
    )

    # An open window runs to the horizon: the last moment any window covers.
    # Drawing it as a short stub would read as a short block, which is the one
    # thing it is not. The flag is kept because end_dt stops being empty below.
    open_rows = block_df["end_dt"].isna()
    block_df["end_is_open"] = open_rows
    horizon = max(
        [value for value in (block_df["end_dt"].max(), block_df["start_dt"].max()) if pd.notna(value)],
        default=None,
    )
    if open_rows.any() and horizon is not None:
        span = (horizon - block_df["start_dt"].min()).total_seconds()
        stub = max(_OPEN_WINDOW_MIN_SECONDS, span * _OPEN_WINDOW_MIN_SPAN_SHARE)
        floor = block_df.loc[open_rows, "start_dt"] + pd.Timedelta(seconds=stub)
        block_df.loc[open_rows, "end_dt"] = floor.clip(lower=horizon)

    drawable = block_df[
        block_df["duration_sec"].isna()
        | (block_df["duration_sec"] >= _BLOCK_WINDOW_MIN_SECONDS)
    ]
    hidden_count = len(block_df) - len(drawable)
    # 긴 차단부터 남긴다 — 한도에 걸려 잘려나가는 건 짧은 쪽이어야 한다.
    drawable = drawable.sort_values("duration_sec", ascending=False).head(_BLOCK_WINDOW_LIMIT)

    windows = []
    for _, row in drawable.sort_values("start_dt").iterrows():
        # "해제됨"은 파서가 그렇게 봤고 실제로 끝 시각이 읽힌 경우만이다.
        recovered = bool(row.get("is_recovered")) and not row["end_is_open"]
        duration = None if pd.isna(row["duration_sec"]) else float(row["duration_sec"])
        windows.append(
            AppBlockWindow(
                package=str(row.get("package", "Unknown")),
                uid=str(row.get("uid", "")),
                cause=str(row.get("cause", "")),
                cause_label=str(row["cause_label"]),
                start_dt=row["start_dt"],
                end_dt=row["end_dt"],
                duration_sec=duration,
                is_recovered=recovered,
                sockets_destroyed=bool(row.get("sockets_destroyed_at")),
                resumed=bool(row.get("resumed_at")),
                hover_text=_block_hover(row, duration, recovered),
            )
        )

    apps = _blocked_apps(drawable, _dns_issue_counts(df))

    # 차단됐어도 그동안 통신을 시도하지 않았으면 사용자에게 보이는 증상이 없다.
    # 기본 화면은 실제로 요청이 막힌 앱만 담는다. 다만 그런 앱이 하나도 없는
    # 세션(=DNS 실패 자체가 없는 로그)에서는 그래프가 통째로 비어버리므로,
    # 그때만 예전처럼 오래 막힌 순으로 되돌린다.
    symptomatic = [app for app in apps if app.dns_issue_count > 0]
    if symptomatic:
        default_apps = [app.package for app in symptomatic[:_BLOCK_DEFAULT_TOP_APPS]]
        default_basis = "dns_issues"
    else:
        default_apps = [app.package for app in apps[:_BLOCK_DEFAULT_TOP_APPS]]
        default_basis = "block_duration"

    columns = [column for column in _BLOCK_TABLE_COLUMNS if column in block_df.columns]
    table = (
        block_df.sort_values("start_dt")[columns].rename(columns=_BLOCK_TABLE_COLUMNS)
    )

    durations = block_df["duration_sec"].dropna()
    freeze_mask = block_df.get("cause", pd.Series(dtype=object)).isin(_FROZEN_CAUSES)
    recovered_flags = (
        block_df.get("is_recovered", pd.Series(dtype=bool))
        .astype(object)
        .where(lambda values: values.notna(), False)
        .astype(bool)
    )

    return AppBlockWindows(
        status="ok",
        windows=windows,
        apps=apps,
        default_apps=default_apps,
        default_basis=default_basis,
        table=table,
        window_count=len(block_df),
        freeze_count=int(freeze_mask.sum()),
        background_freeze_count=int(
            (block_df.get("cause", pd.Series(dtype=object)) == "APP_BACKGROUND_FREEZE").sum()
        ),
        app_count=int(block_df["package"].nunique()) if "package" in block_df.columns else 0,
        longest_sec=float(durations.max()) if not durations.empty else None,
        unrecovered_count=int((~recovered_flags).sum()),
        hidden_count=hidden_count,
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


@dataclass(frozen=True)
class ActiveDefaultNetwork:
    """The netId the device routes through by default, and its transport.

    `status` is `"ok"` or `"no_data"` — the log prints the line once, so a
    session that never printed it has nothing to show. `transport` stays None
    when the netId never appeared in a statistics block to read it from.
    """

    status: str
    net_id: Optional[str] = None
    transport: Optional[str] = None


def build_active_default_network(
    data: Optional[Dict[str, Any]],
) -> ActiveDefaultNetwork:
    # A session with no `network_timeseries` key reaches here as the registry's
    # empty-list default, not as a dict.
    if not isinstance(data, dict):
        return ActiveDefaultNetwork(status="no_data")

    net_id = data.get("active_network_id")
    if net_id in (None, ""):
        return ActiveDefaultNetwork(status="no_data")
    return ActiveDefaultNetwork(
        status="ok",
        net_id=str(net_id),
        transport=data.get("active_network_type") or None,
    )


def _timeline_metrics(ts_df: pd.DataFrame) -> List[MetricOption]:
    candidates = [
        MetricOption("DNS 평균 응답 시간(ms)", "dns_avg", "ms"),
        MetricOption("DNS 최대 응답 시간(ms)", "dns_max", "ms"),
        MetricOption("DNS 오류율(%)", "dns_err_rate", "%"),
        MetricOption("DNS 지연 건수", "dns_delayed_cnt", "건"),
        MetricOption("DNS 차단 건수", "dns_blocked_cnt", "건"),
        # TCP loss is only collected on some builds; offer it when it has values.
        MetricOption("TCP 평균 손실률(%)", "tcp_avg_loss", "%"),
    ]
    return [
        metric
        for metric in candidates
        if metric.column in ts_df.columns and ts_df[metric.column].notna().any()
    ]


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
    metrics = _timeline_metrics(ts_df)
    if "dns_delayed_cnt" not in ts_df.columns:
        ts_df["dns_delayed_cnt"] = 0

    spike_df = ts_df[
        (ts_df["dns_avg"] >= DNS_SPIKE_THRESHOLD_MS)
        | (pd.to_numeric(ts_df["dns_delayed_cnt"], errors="coerce").fillna(0) > 0)
    ]
    spike_columns = [column for column in _SPIKE_COLUMNS if column in spike_df.columns]
    spikes = spike_df[spike_columns].sort_values("dns_avg", ascending=False)

    return NetworkTimelineStats(
        status="ok", frame=ts_df, metrics=metrics, spikes=spikes
    )


# ------------------------------------------------------------------ data usage

_DATA_USAGE_TOP_APPS = 10
_DATA_USAGE_TOP_APPS_BY_BUCKET = 7


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


@dataclass(frozen=True)
class DataUsageTopByTime:
    """Top data-usage apps per hourly bucket.

    `status` is `"ok"`, `"unavailable"`, `"no_data"` or `"unparsable_time"`.
    """

    status: str
    bucket_minutes: int = 60
    top_n: int = _DATA_USAGE_TOP_APPS_BY_BUCKET
    frame: pd.DataFrame = field(default_factory=pd.DataFrame)
    table: pd.DataFrame = field(default_factory=pd.DataFrame)


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


def build_data_usage_top_by_time(
    df: pd.DataFrame,
    *,
    year: Optional[int] = None,
    bucket_minutes: int = 60,
    top_n: int = _DATA_USAGE_TOP_APPS_BY_BUCKET,
) -> DataUsageTopByTime:
    if not has_columns(df) or "log_type" not in df.columns:
        return DataUsageTopByTime(status="unavailable", bucket_minutes=bucket_minutes, top_n=top_n)

    du_df = _usage_frame(df)
    if du_df.empty:
        return DataUsageTopByTime(status="no_data", bucket_minutes=bucket_minutes, top_n=top_n)
    if "time" not in du_df.columns:
        return DataUsageTopByTime(status="unparsable_time", bucket_minutes=bucket_minutes, top_n=top_n)

    timed = with_parsed_times(du_df, "time", year=year)
    if timed.empty:
        return DataUsageTopByTime(status="unparsable_time", bucket_minutes=bucket_minutes, top_n=top_n)

    timed["app_name"] = timed["app_name"].fillna("UNKNOWN").astype(str)
    timed["bucket_dt"] = timed["time_dt"].dt.floor(f"{bucket_minutes}min")
    timed["bucket"] = timed["bucket_dt"].dt.strftime("%m-%d %H:%M")

    totals = (
        timed.groupby(["bucket_dt", "bucket", "app_name"], as_index=False)["total_mb"]
        .sum()
        .sort_values(["bucket_dt", "total_mb", "app_name"], ascending=[True, False, True])
    )
    totals["rank"] = totals.groupby("bucket_dt")["total_mb"].rank(method="first", ascending=False).astype(int)
    top = totals[totals["rank"] <= top_n].copy()
    top = top.sort_values(["bucket_dt", "rank"]).reset_index(drop=True)

    table = top[["bucket", "rank", "app_name", "total_mb"]].copy()
    return DataUsageTopByTime(
        status="ok",
        bucket_minutes=bucket_minutes,
        top_n=top_n,
        frame=top[["bucket", "bucket_dt", "app_name", "total_mb", "rank"]],
        table=table,
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
_DATA_STALL_FLOW_COLUMNS = [
    "start_time",
    "recovery_start_time",
    "recovery_end_time",
    "end_time",
    "duration_sec",
    "status",
    "event_count",
    "trigger",
]
_RF_WARNING_COLUMNS = ["time", "event_type", "slot", "rat", "reason", "raw"]


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
    data_stall_flows: pd.DataFrame = field(default_factory=pd.DataFrame)
    rf_warnings: pd.DataFrame = field(default_factory=pd.DataFrame)
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

    rf_warnings = pd.DataFrame(data.get("rf_warnings", []) or [])
    if not rf_warnings.empty:
        rf_warnings = rf_warnings[[column for column in _RF_WARNING_COLUMNS if column in rf_warnings.columns]]
    elif timeline_status == "ok":
        rf_df = timeline[
            (timeline["layer"] == "RF")
            & (timeline["event_type"].isin(["OOS_OR_REG_STATE", "WEAK_SIGNAL"]))
        ]
        if not rf_df.empty:
            rf_warnings = rf_df[[column for column in _RF_WARNING_COLUMNS if column in rf_df.columns]]

    flows = pd.DataFrame(data.get("data_stall_flows", []) or [], columns=_DATA_STALL_FLOW_COLUMNS)
    if not flows.empty:
        flows = flows[[column for column in _DATA_STALL_FLOW_COLUMNS if column in flows.columns]]

    return InternetStallReport(
        status="ok",
        kpi=kpi,
        root_causes=root_causes,
        timeline_status=timeline_status,
        timeline=timeline if timeline_status == "ok" else pd.DataFrame(),
        timeline_hover_columns=hover_columns,
        timeline_table=timeline_table,
        data_stall_flows=flows,
        rf_warnings=rf_warnings,
        windows=_stall_windows(data.get("stall_windows", []) or [], year),
    )
