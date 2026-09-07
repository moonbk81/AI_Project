"""Packet capture chart series.

Built from the `<base>_pcap.json` artifact the analysis writes next to the
report. Nothing here imports a web framework or plotly — see
`core/charts/__init__.py` for the split.

The parser already reduced the capture to counts and a handful of examples;
this layer only reshapes it into frames a chart can draw, and lifts the
caveats (a guessed timezone, a capture that misses the log window, a walk that
stopped at the packet limit) into one list the card must show. Those caveats
decide whether "이상 없음" means anything at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from parsers.pcap_parser import capture_caveats

from .common import parse_log_times, with_parsed_times

# Anomaly rows of the breakdown chart, in the order a reader should scan them:
# name resolution first, then connection setup, then link quality.
# (contract key, label, event key holding the examples)
ANOMALY_KINDS = [
    ("dns_unanswered_count", "DNS 무응답", "dns_unanswered"),
    ("dns_error_count", "DNS 에러응답", "dns_errors"),
    ("tcp_connect_failure_count", "TCP 연결 실패", "tcp_connect_failures"),
    ("tls_handshake_failure_count", "TLS 실패", "tls_handshake_failures"),
    ("tcp_reset_count", "TCP RST", "tcp_resets"),
    ("tcp_retransmission_count", "TCP 재전송", "tcp_retransmissions"),
    ("tcp_zero_window_count", "TCP zero window", "tcp_zero_window"),
    ("icmp_unreachable_count", "ICMP unreachable", "icmp_unreachable"),
]

_GAP_COLUMNS = ["file", "start_time", "end_time", "duration_sec"]
_EVENT_COLUMNS = ["file", "kind", "time", "detail"]


@dataclass(frozen=True)
class PcapOverview:
    """What the packets say, per capture.

    `status` is `"ok"`, `"no_data"` (no capture was uploaded with this log) or
    the parser's own failure status (`"TSHARK_MISSING"`, `"PARSE_FAILED"`,
    `"EMPTY"`, `"FILE_NOT_FOUND"`) — in which case `message` says why and the
    frames are empty. A failed capture is not a clean capture, so the caller
    must not draw it as one.

    `warnings` are the reasons a reading could be wrong rather than reassuring.
    """

    status: str
    message: str = ""
    tshark_version: str = ""
    capture_count: int = 0
    analyzed_count: int = 0
    warnings: List[str] = field(default_factory=list)
    # One row per analyzed capture: file, packet/byte counts, window, verdict.
    captures: pd.DataFrame = field(default_factory=pd.DataFrame)
    # file, time, time_dt, packets, bytes, kbps — one series per capture.
    timeline: pd.DataFrame = field(default_factory=pd.DataFrame)
    bucket_sec: int = 0
    # label, count — the breakdown chart.
    anomalies: pd.DataFrame = field(default_factory=pd.DataFrame)
    # file, start_time, end_time, duration_sec, start_dt, end_dt — the chart
    # shades these onto the timeline, so the ends are parsed here too.
    gaps: pd.DataFrame = field(default_factory=pd.DataFrame)
    # file, kind, time, detail — the examples behind the counts.
    events: pd.DataFrame = field(default_factory=pd.DataFrame)


def _frame(rows: List[Dict[str, Any]], columns: List[str]) -> pd.DataFrame:
    """A frame that keeps its columns even when there are no rows.

    An empty frame with no columns makes the caller branch on two shapes.
    """
    return pd.DataFrame(rows, columns=columns) if not rows else pd.DataFrame(rows)[columns]


def _capture_row(capture: Dict[str, Any]) -> Dict[str, Any]:
    meta = capture.get("capture") or {}
    kpi = capture.get("kpi") or {}
    return {
        "file": meta.get("file") or "",
        "packet_count": meta.get("packet_count") or 0,
        "byte_count": meta.get("byte_count") or 0,
        "duration_sec": meta.get("duration_sec") or 0,
        "start_time": meta.get("start_time") or "",
        "end_time": meta.get("end_time") or "",
        "flow_count": capture.get("flow_count") or 0,
        "longest_silence_sec": kpi.get("longest_silence_sec") or 0,
        "verdict": kpi.get("verdict") or "",
    }


def _timeline_rows(capture: Dict[str, Any]) -> List[Dict[str, Any]]:
    timeline = capture.get("throughput_timeline") or {}
    bucket_sec = timeline.get("bucket_sec") or 0
    name = (capture.get("capture") or {}).get("file") or ""
    rows = []
    for bucket in timeline.get("buckets") or []:
        # 버킷 폭이 캡처마다 다를 수 있어(길이에 따라 접힌다) 바이트를 그대로
        # 겹쳐 그리면 폭이 넓은 쪽이 항상 커 보인다. 초당으로 환산해 둔다.
        kbps = round(bucket.get("bytes", 0) * 8 / 1000 / bucket_sec, 1) if bucket_sec else 0
        rows.append({
            "file": name,
            "time": bucket.get("time") or "",
            "packets": bucket.get("packets") or 0,
            "bytes": bucket.get("bytes") or 0,
            "kbps": kbps,
        })
    return rows


def _event_rows(capture: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The parser's examples, flattened into one table.

    Each anomaly kind names its evidence differently (a query name, a server
    name, an address and port). The card shows one column, so the difference
    is rendered here instead of in six branches of the view.
    """
    name = (capture.get("capture") or {}).get("file") or ""
    dns = capture.get("dns") or {}
    tcp = capture.get("tcp") or {}
    sources = {
        "dns_unanswered": (dns.get("unanswered"), lambda item: item.get("query") or ""),
        "dns_errors": (
            dns.get("errors"),
            lambda item: f"{item.get('query') or ''} → {item.get('rcode') or ''}".strip(" →"),
        ),
        "tcp_connect_failures": (
            tcp.get("connect_failures"),
            lambda item: f"{item.get('dst') or ''}:{item.get('dst_port') or ''}",
        ),
        "tls_handshake_failures": (
            (capture.get("tls") or {}).get("handshake_failures"),
            lambda item: item.get("server_name") or "",
        ),
        "tcp_resets": (tcp.get("resets"), lambda item: _endpoint(item)),
        "tcp_retransmissions": (tcp.get("retransmissions"), lambda item: _endpoint(item)),
        "tcp_zero_window": (tcp.get("zero_window"), lambda item: _endpoint(item)),
        "icmp_unreachable": (
            (capture.get("icmp") or {}).get("unreachable"),
            lambda item: f"{_endpoint(item)} type={item.get('icmp_type') or ''}",
        ),
    }
    labels = {event_key: label for _, label, event_key in ANOMALY_KINDS}

    rows = []
    for event_key, (items, detail) in sources.items():
        for item in items or []:
            rows.append({
                "file": name,
                "kind": labels.get(event_key, event_key),
                "time": item.get("time") or "",
                "detail": detail(item),
            })
    return sorted(rows, key=lambda row: (row["time"], row["kind"]))


def _endpoint(item: Dict[str, Any]) -> str:
    port = item.get("dst_port")
    return f"{item.get('dst') or ''}:{port}" if port else str(item.get("dst") or "")


def _counts(capture: Dict[str, Any]) -> Dict[str, int]:
    kpi = capture.get("kpi") or {}
    counts = {key: int(kpi.get(key) or 0) for key, _, _ in ANOMALY_KINDS}
    # zero window 만 KPI 에 없다 (tcp 항목에만 있다). 세는 자리를 한 곳으로
    # 모아 두지 않으면 카드마다 다른 숫자가 나온다.
    counts["tcp_zero_window_count"] = int((capture.get("tcp") or {}).get("zero_window_count") or 0)
    return counts


def _anomaly_frame(captures: List[Dict[str, Any]]) -> pd.DataFrame:
    """Counts summed over the captures, in the order a reader scans them.

    Kinds that never happened stay in the frame at zero — "재전송 0건" is an
    answer, and dropping the row makes the reader wonder if it was measured.
    """
    counted = [_counts(capture) for capture in captures]
    return pd.DataFrame([
        {"label": label, "count": sum(counts[key] for counts in counted)}
        for key, label, _ in ANOMALY_KINDS
    ])


def build_pcap_overview(analysis: Dict[str, Any], *, year: Optional[int] = None) -> PcapOverview:
    if not isinstance(analysis, dict) or not analysis:
        return PcapOverview(status="no_data")

    captures = [item for item in (analysis.get("captures") or []) if isinstance(item, dict)]
    analyzed = [item for item in captures if item.get("status") == "OK"]
    version = analysis.get("tshark_version") or ""

    if not analyzed:
        first = captures[0] if captures else {}
        return PcapOverview(
            status=analysis.get("status") or first.get("status") or "no_data",
            message=analysis.get("message") or first.get("message") or "",
            tshark_version=version,
            capture_count=len(captures),
        )

    timeline_rows = [row for capture in analyzed for row in _timeline_rows(capture)]
    timeline = _frame(timeline_rows, ["file", "time", "packets", "bytes", "kbps"])
    if not timeline.empty:
        timeline = with_parsed_times(timeline, "time", year=year)

    gap_rows = [
        {"file": (capture.get("capture") or {}).get("file") or "", **gap}
        for capture in analyzed
        for gap in (capture.get("silence_gaps") or [])
    ]
    gaps = _frame(
        sorted(gap_rows, key=lambda gap: gap.get("duration_sec") or 0, reverse=True),
        _GAP_COLUMNS,
    )
    if not gaps.empty:
        # 조용한 구간은 처리량 차트 위에 띠로 얹힌다. 날짜 축에 얹으려면 시각이
        # 필요한데, 표에는 로그와 같은 문자열이 남아야 해서 둘 다 싣는다.
        gaps["start_dt"] = parse_log_times(gaps["start_time"], year=year)
        gaps["end_dt"] = parse_log_times(gaps["end_time"], year=year)

    return PcapOverview(
        status="ok",
        tshark_version=version,
        capture_count=len(captures),
        analyzed_count=len(analyzed),
        warnings=[caveat for capture in analyzed for caveat in capture_caveats(capture)],
        captures=pd.DataFrame([_capture_row(capture) for capture in analyzed]),
        timeline=timeline,
        bucket_sec=int(
            (analyzed[0].get("throughput_timeline") or {}).get("bucket_sec") or 0
        ),
        anomalies=_anomaly_frame(analyzed),
        gaps=gaps,
        events=_frame([row for capture in analyzed for row in _event_rows(capture)], _EVENT_COLUMNS),
    )
