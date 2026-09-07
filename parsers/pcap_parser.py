"""패킷 캡처(pcap/pcapng) 분석.

로그는 "안드로이드 프레임워크가 무엇을 봤는가" 를 말해 주고, pcap 은 "선로에
실제로 무엇이 흘렀는가" 를 말해 준다. 인터넷 멈춤 분석에서 둘의 차이가 곧
답이다 -- 프레임워크가 data stall 을 선언했는데 패킷은 정상적으로 오갔다면
문제는 망이 아니라 위쪽에 있고, 반대로 SYN 만 나가고 아무것도 돌아오지 않았다면
망이다.

해석은 전부 ``tshark`` 에 맡긴다. 재전송 판정, DNS 응답 시간, 스트림 번호처럼
직접 구현하면 틀리기 쉬운 것들을 이미 정확하게 해 주기 때문이다. 이 모듈이 하는
일은 tshark 가 뱉은 필드를 집계해서, 사람과 LLM 이 읽을 크기로 줄이는 것이다.

패킷 원문(payload)은 결과에 절대 담지 않는다. 담는 것은 헤더에서 나온 사실과
DNS 질의 이름까지다. 리포트는 통째로 LLM 에 올라가고 Vector DB 에 적재된다.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

from parsers.pcap_timebase import check_overlap, resolve_timebase, to_log_time

logger = logging.getLogger(__name__)

TSHARK = "tshark"

PCAP_SUFFIXES = (".pcap", ".pcapng", ".cap")

# 한 번 훑는 데 걸어 두는 상한. 통째로 메모리에 올리지는 않지만, 수 GB 짜리
# 캡처가 분석 전체를 몇 시간씩 붙잡는 일은 막아야 한다.
DEFAULT_TIMEOUT_SEC = 900
DEFAULT_MAX_PACKETS = 5_000_000

# 집계 자료구조의 상한. pcap 은 흐름이 수십만 개까지 늘어날 수 있어서, 상한 없이
# 모으면 분석 서버 메모리를 캡처 하나가 다 먹는다. 넘친 뒤로는 새 키를 만들지
# 않고 몇 개를 못 셌는지만 남긴다.
MAX_FLOWS = 200_000
MAX_PENDING = 200_000

# 트래픽이 이만큼 끊기면 "조용한 구간" 으로 본다. 인터넷 멈춤 구간 후보다.
DEFAULT_MIN_GAP_SEC = 5.0

# 처리량 타임라인이 아무리 긴 캡처에서도 이 개수를 넘지 않도록 버킷을 넓힌다.
MAX_TIMELINE_BUCKETS = 720
MIN_BUCKET_SEC = 10

# 결과에 담을 개수. 리포트가 LLM 컨텍스트에 들어가므로 무한정 늘릴 수 없다.
TOP_FLOWS = 50
TOP_EVENTS = 50
TOP_GAPS = 30

# 암호화된 DNS. 안이 안 보이므로 이름 대신 "쓰이고 있다" 는 사실만 남긴다.
DOT_PORT = 853

# 첫 패스에서 뽑는 필드. 순서가 곧 열 순서다.
WALK_FIELDS = [
    "frame.time_epoch",
    "frame.len",
    "ip.src",
    "ip.dst",
    "ipv6.src",
    "ipv6.dst",
    "tcp.srcport",
    "tcp.dstport",
    "tcp.flags",
    "tcp.len",
    "tcp.stream",
    "udp.srcport",
    "udp.dstport",
    "dns.id",
    "dns.flags.response",
    "dns.qry.name",
    "dns.flags.rcode",
    "dns.time",
    "tls.handshake.type",
    "tls.handshake.extensions_server_name",
    "_ws.col.protocol",
]

# 이상 징후는 tshark 의 판정(expert analysis)을 그대로 쓴다. 표시 필터로 걸러
# 두 번째 패스에서 가져온다. ``-e tcp.analysis.retransmission`` 처럼 값이 없는
# 필드는 ``-T fields`` 로 꺼내면 tshark 버전에 따라 빈 칸이 나올 수 있어서,
# 값을 읽는 대신 필터로 거른다 -- 이쪽은 어느 버전에서나 같게 동작한다.
ANOMALY_FIELDS = [
    "frame.time_epoch",
    "ip.src",
    "ip.dst",
    "ipv6.src",
    "ipv6.dst",
    "tcp.srcport",
    "tcp.dstport",
    "tcp.stream",
    "udp.srcport",
    "udp.dstport",
    "dns.qry.name",
    "dns.flags.rcode",
    "icmp.type",
    "icmp.code",
    "_ws.col.protocol",
]

ANOMALY_FILTERS = {
    "tcp_retransmissions": "tcp.analysis.retransmission",
    "tcp_zero_window": "tcp.analysis.zero_window",
    "tcp_resets": "tcp.flags.reset == 1",
    "icmp_unreachable": "icmp.type == 3 || icmpv6.type == 1",
    "dns_errors": "dns.flags.response == 1 && dns.flags.rcode != 0",
}

# DNS 응답 코드. 숫자만 남기면 무슨 일이 있었는지 아무도 못 읽는다.
DNS_RCODE_NAMES = {
    "0": "NOERROR",
    "1": "FORMERR",
    "2": "SERVFAIL",
    "3": "NXDOMAIN",
    "4": "NOTIMP",
    "5": "REFUSED",
}

TCP_SYN = 0x02
TCP_ACK = 0x10


def is_pcap_name(filename) -> bool:
    """이름만 보고 패킷 캡처인지. 압축된 캡처(``.pcap.gz``)도 tshark 가 읽는다."""
    name = str(filename or "").lower()
    if name.endswith(".gz"):
        name = name[:-3]
    return name.endswith(PCAP_SUFFIXES)


def tshark_available() -> bool:
    return shutil.which(TSHARK) is not None


def tshark_version() -> str:
    try:
        out = subprocess.run(
            [TSHARK, "--version"], capture_output=True, text=True, timeout=15
        ).stdout
        return out.splitlines()[0].strip() if out else ""
    except Exception:
        return ""


class TsharkMissing(RuntimeError):
    pass


def _run_tshark(
    path: str,
    fields: List[str],
    display_filter: Optional[str] = None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    max_rows: Optional[int] = None,
) -> Iterable[List[str]]:
    """tshark 를 돌려 한 줄에 한 패킷씩 필드 값을 내어준다.

    파일을 통째로 읽지 않고 파이프에서 흘려 받는다. 캡처가 아무리 커도 이쪽
    메모리는 집계 자료구조만큼만 쓴다.
    """
    if not tshark_available():
        raise TsharkMissing(
            "tshark 가 설치돼 있지 않아 pcap 을 분석할 수 없습니다. "
            "`sudo apt install -y tshark` 로 설치하세요."
        )

    command = [
        TSHARK,
        "-r", path,
        "-n",                       # 이름 해석 안 함. 빠르고, 밖으로 질의가 나가지 않는다.
        "-T", "fields",
        "-E", "separator=/t",
        "-E", "quote=n",
        "-E", "occurrence=f",
        "-E", "header=n",
    ]
    for field in fields:
        command += ["-e", field]
    if display_filter:
        command += ["-Y", display_filter]

    deadline = time.monotonic() + timeout_sec
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        bufsize=1,
    )

    rows = 0
    try:
        for line in process.stdout:
            yield line.rstrip("\n").split("\t")
            rows += 1
            if max_rows is not None and rows >= max_rows:
                break
            # 줄마다 시계를 보면 비싸다. 가끔만 본다.
            if rows % 20_000 == 0 and time.monotonic() > deadline:
                logger.warning("tshark 제한 시간(%ds) 초과: %s", timeout_sec, path)
                break
    finally:
        if process.poll() is None:
            process.kill()
        stderr = process.stderr.read() if process.stderr else ""
        process.stdout.close()
        process.stderr.close()
        process.wait()
        if process.returncode not in (0, None) and rows == 0 and stderr.strip():
            raise RuntimeError(f"tshark 실패: {stderr.strip().splitlines()[0]}")


def _cell(row: List[str], index: int) -> str:
    return row[index].strip() if index < len(row) else ""


def _as_int(value: str, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(value, 16) if value.startswith("0x") else int(value)
    except (ValueError, AttributeError):
        return default


def _as_float(value: str, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _endpoints(row: List[str], idx: Dict[str, int]) -> Tuple[str, str]:
    src = _cell(row, idx["ip.src"]) or _cell(row, idx["ipv6.src"])
    dst = _cell(row, idx["ip.dst"]) or _cell(row, idx["ipv6.dst"])
    return src, dst


def _ports(row: List[str], idx: Dict[str, int]) -> Tuple[str, str, str]:
    tcp_src = _cell(row, idx["tcp.srcport"])
    if tcp_src or _cell(row, idx["tcp.dstport"]):
        return "TCP", tcp_src, _cell(row, idx["tcp.dstport"])
    udp_src = _cell(row, idx["udp.srcport"])
    if udp_src or _cell(row, idx["udp.dstport"]):
        return "UDP", udp_src, _cell(row, idx["udp.dstport"])
    return "", "", ""


class PcapParser:
    """pcap 하나를 읽어 리포트에 넣을 요약을 만든다.

    ``analyze()`` 는 다른 파서들과 달리 라인 목록이 아니라 파일 경로를 받는다.
    pcap 은 텍스트가 아니라서 ``BaseParser`` 의 계약(줄 단위)을 따를 수 없다.
    """

    def __init__(
        self,
        tz_offset_hours: Optional[float] = None,
        min_gap_sec: float = DEFAULT_MIN_GAP_SEC,
        max_packets: int = DEFAULT_MAX_PACKETS,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ):
        self.tz_offset_hours = tz_offset_hours
        self.min_gap_sec = min_gap_sec
        self.max_packets = max_packets
        self.timeout_sec = timeout_sec

    # ------------------------------------------------------------------ 진입점

    def analyze(
        self,
        pcap_path: str,
        report_data: dict = None,
        log_window: Optional[Tuple[str, str]] = None,
    ) -> dict:
        if not pcap_path or not os.path.exists(pcap_path):
            return self._error("FILE_NOT_FOUND", f"pcap 파일이 없습니다: {pcap_path}", pcap_path)

        timebase = resolve_timebase(report_data, self.tz_offset_hours)
        offset = timebase["tz_offset_hours"]

        try:
            walk = self._walk(pcap_path, offset)
        except TsharkMissing as missing:
            return self._error("TSHARK_MISSING", str(missing), pcap_path)
        except Exception as failure:  # 깨진 캡처, 지원하지 않는 링크 계층 등
            logger.error("pcap 분석 실패 (%s): %s", pcap_path, failure)
            return self._error("PARSE_FAILED", str(failure), pcap_path)

        if not walk["packet_count"]:
            return self._error("EMPTY", "캡처에 읽을 수 있는 패킷이 없습니다.", pcap_path)

        anomalies = self._collect_anomalies(pcap_path, offset)

        # 서버가 돌려준 에러 응답(SERVFAIL/NXDOMAIN)은 tshark 의 필터로 따로 뽑았지만,
        # 읽는 쪽에서는 DNS 이야기라 DNS 항목 안에 있어야 한다.
        dns = dict(walk["dns"])
        dns["error_count"] = anomalies["dns_error_count"]
        dns["errors"] = anomalies["dns_errors"]

        capture = {
            "file": os.path.basename(pcap_path),
            "packet_count": walk["packet_count"],
            "byte_count": walk["byte_count"],
            "start_time": walk["start_time"],
            "end_time": walk["end_time"],
            "duration_sec": round(walk["duration_sec"], 3),
            "truncated": walk["truncated"],
        }
        if walk["truncated"]:
            capture["truncated_note"] = (
                f"패킷 {self.max_packets:,}개에서 읽기를 멈췄습니다. 뒤쪽 구간은 분석에 "
                "포함되지 않았습니다."
            )

        timebase = dict(timebase)
        timebase["alignment"] = check_overlap(walk["start_time"], walk["end_time"], log_window)

        result = {
            "status": "OK",
            "capture": capture,
            "timebase": timebase,
            "protocols": walk["protocols"],
            "top_flows": walk["top_flows"],
            "flow_count": walk["flow_count"],
            "dns": dns,
            "tcp": {
                "connect_failures": walk["connect_failures"],
                "connect_attempts": walk["connect_attempts"],
                **anomalies["tcp"],
            },
            "tls": walk["tls"],
            "icmp": anomalies["icmp"],
            # 목록은 가장 긴 것들만 남긴다. 건수는 따로 싣는다 -- 목록 길이를
            # 건수로 읽으면 상한(30)에 걸린 캡처가 전부 "30건" 으로 보인다.
            "silence_gaps": walk["silence_gaps"],
            "silence_gap_count": walk["silence_gap_total_count"],
            "throughput_timeline": walk["throughput_timeline"],
        }
        result["kpi"] = self._build_kpi(result)
        return result

    # -------------------------------------------------------------- 1차 전체 훑기

    def _walk(self, pcap_path: str, offset: float) -> dict:
        idx = {name: position for position, name in enumerate(WALK_FIELDS)}

        packet_count = 0
        byte_count = 0
        first_epoch = None
        last_epoch = None
        truncated = False

        protocols: Dict[str, int] = defaultdict(int)
        flows: Dict[tuple, dict] = {}
        flows_dropped = 0

        # 조용한 구간 찾기용. 직전 패킷 시각만 들고 있으면 된다.
        gaps: List[dict] = []

        # 처리량 타임라인. 버킷 폭은 캡처 길이를 모르는 채로 시작하므로, 버킷이
        # 너무 많아지면 두 칸씩 접어서 폭을 넓힌다.
        bucket_sec = MIN_BUCKET_SEC
        buckets: Dict[int, dict] = {}

        # DNS 질의 -> 응답 짝짓기. 응답이 오지 않은 질의가 인터넷 멈춤의 핵심 증거다.
        dns_pending: Dict[tuple, float] = {}
        dns_answered = 0
        dns_query_count = 0
        dns_latencies: List[Tuple[float, str, float]] = []  # (latency, qname, epoch)
        dot_packets = 0

        # TCP 연결 시도 -> 응답. SYN 만 나가고 SYN/ACK 이 없으면 연결 자체가 안 된 것.
        syn_pending: Dict[str, dict] = {}
        syn_answered = set()
        connect_attempts = 0

        # TLS ClientHello -> ServerHello.
        tls_hello: Dict[str, dict] = {}
        tls_answered = set()
        tls_sni_seen: Dict[str, int] = defaultdict(int)

        for row in _run_tshark(
            pcap_path, WALK_FIELDS, timeout_sec=self.timeout_sec, max_rows=self.max_packets
        ):
            epoch = _as_float(_cell(row, idx["frame.time_epoch"]))
            if epoch is None:
                continue

            packet_count += 1
            byte_count += _as_int(_cell(row, idx["frame.len"]), 0) or 0

            if first_epoch is None:
                first_epoch = epoch
            elif epoch - last_epoch >= self.min_gap_sec:
                gaps.append({
                    "start_time": to_log_time(last_epoch, offset),
                    "end_time": to_log_time(epoch, offset),
                    "duration_sec": round(epoch - last_epoch, 3),
                })
            last_epoch = epoch

            protocol = _cell(row, idx["_ws.col.protocol"]) or "UNKNOWN"
            protocols[protocol] += 1

            bucket_sec, buckets = self._add_to_bucket(
                buckets, bucket_sec, first_epoch, epoch,
                _as_int(_cell(row, idx["frame.len"]), 0) or 0,
            )

            src, dst = _endpoints(row, idx)
            transport, sport, dport = _ports(row, idx)

            if src and dst:
                key = (transport or "IP", src, sport, dst, dport)
                flow = flows.get(key)
                if flow is None:
                    if len(flows) >= MAX_FLOWS:
                        flows_dropped += 1
                    else:
                        flow = flows[key] = {
                            "transport": transport or "IP",
                            "src": src, "src_port": sport,
                            "dst": dst, "dst_port": dport,
                            "packets": 0, "bytes": 0,
                            "first_epoch": epoch, "last_epoch": epoch,
                        }
                if flow is not None:
                    flow["packets"] += 1
                    flow["bytes"] += _as_int(_cell(row, idx["frame.len"]), 0) or 0
                    flow["last_epoch"] = epoch

            if sport == str(DOT_PORT) or dport == str(DOT_PORT):
                dot_packets += 1

            # --- DNS
            qname = _cell(row, idx["dns.qry.name"])
            if qname:
                dns_id = _cell(row, idx["dns.id"])
                is_response = _cell(row, idx["dns.flags.response"]) == "1"
                pair_key = (dns_id, qname.lower())
                if is_response:
                    dns_pending.pop(pair_key, None)
                    dns_answered += 1
                    latency = _as_float(_cell(row, idx["dns.time"]))
                    if latency is not None:
                        dns_latencies.append((latency, qname, epoch))
                else:
                    dns_query_count += 1
                    if len(dns_pending) < MAX_PENDING:
                        dns_pending.setdefault(pair_key, epoch)

            # --- TCP 3-way handshake
            stream = _cell(row, idx["tcp.stream"])
            flags = _as_int(_cell(row, idx["tcp.flags"]))
            if stream and flags is not None:
                is_syn = bool(flags & TCP_SYN)
                is_ack = bool(flags & TCP_ACK)
                if is_syn and not is_ack:
                    connect_attempts += 1
                    if len(syn_pending) < MAX_PENDING:
                        syn_pending.setdefault(stream, {
                            "epoch": epoch, "src": src, "dst": dst, "dst_port": dport,
                        })
                elif is_syn and is_ack:
                    syn_answered.add(stream)

            # --- TLS handshake
            handshake_type = _cell(row, idx["tls.handshake.type"])
            if stream and handshake_type == "1":
                sni = _cell(row, idx["tls.handshake.extensions_server_name"])
                if sni:
                    tls_sni_seen[sni] += 1
                if len(tls_hello) < MAX_PENDING:
                    tls_hello.setdefault(stream, {
                        "epoch": epoch, "server_name": sni, "dst": dst, "dst_port": dport,
                    })
            elif stream and handshake_type == "2":
                tls_answered.add(stream)

        if first_epoch is None:
            return {"packet_count": 0}

        truncated = packet_count >= self.max_packets

        return {
            "packet_count": packet_count,
            "byte_count": byte_count,
            "start_time": to_log_time(first_epoch, offset),
            "end_time": to_log_time(last_epoch, offset),
            "duration_sec": last_epoch - first_epoch,
            "truncated": truncated,
            "protocols": dict(sorted(protocols.items(), key=lambda kv: kv[1], reverse=True)[:20]),
            "flow_count": len(flows) + flows_dropped,
            "top_flows": self._top_flows(flows, offset, flows_dropped),
            "dns": self._summarize_dns(
                dns_query_count, dns_answered, dns_pending, dns_latencies, dot_packets, offset
            ),
            "connect_attempts": connect_attempts,
            "connect_failures": self._unanswered(
                syn_pending, syn_answered, offset,
                lambda pending: {
                    "time": to_log_time(pending["epoch"], offset),
                    "dst": pending["dst"],
                    "dst_port": pending["dst_port"],
                    "reason": "SYN 을 보냈지만 SYN/ACK 이 오지 않음 (연결 수립 실패)",
                },
            ),
            "tls": {
                "client_hello_count": len(tls_hello),
                "top_server_names": sorted(
                    tls_sni_seen.items(), key=lambda kv: kv[1], reverse=True
                )[:20],
                "handshake_failures": self._unanswered(
                    tls_hello, tls_answered, offset,
                    lambda pending: {
                        "time": to_log_time(pending["epoch"], offset),
                        "server_name": pending["server_name"] or "(SNI 없음)",
                        "dst": pending["dst"],
                        "dst_port": pending["dst_port"],
                        "reason": "ClientHello 를 보냈지만 ServerHello 가 오지 않음",
                    },
                ),
            },
            "silence_gaps": sorted(
                gaps, key=lambda gap: gap["duration_sec"], reverse=True
            )[:TOP_GAPS],
            "silence_gap_total_count": len(gaps),
            "throughput_timeline": self._finish_timeline(buckets, bucket_sec, first_epoch, offset),
        }

    # ------------------------------------------------------------------ 집계 도우미

    @staticmethod
    def _add_to_bucket(buckets, bucket_sec, first_epoch, epoch, size):
        """처리량 버킷에 한 패킷을 넣는다. 버킷이 너무 많아지면 폭을 두 배로 접는다."""
        index = int((epoch - first_epoch) // bucket_sec)
        bucket = buckets.get(index)
        if bucket is None:
            bucket = buckets[index] = {"packets": 0, "bytes": 0}
        bucket["packets"] += 1
        bucket["bytes"] += size

        if len(buckets) <= MAX_TIMELINE_BUCKETS:
            return bucket_sec, buckets

        folded: Dict[int, dict] = {}
        for old_index, values in buckets.items():
            target = folded.setdefault(old_index // 2, {"packets": 0, "bytes": 0})
            target["packets"] += values["packets"]
            target["bytes"] += values["bytes"]
        return bucket_sec * 2, folded

    def _finish_timeline(self, buckets, bucket_sec, first_epoch, offset) -> dict:
        # 버킷 인덱스는 캡처 시작 기준이라 그대로는 로그와 맞춰 볼 수 없다. 로그와
        # 같은 시각 문자열로 되돌려 담는다 -- 차트도 상관 분석도 이 축을 쓴다.
        return {"bucket_sec": bucket_sec, "buckets": [
            {
                "time": to_log_time(first_epoch + index * bucket_sec, offset),
                "packets": values["packets"],
                "bytes": values["bytes"],
            }
            for index, values in sorted(buckets.items())
        ]}

    @staticmethod
    def _top_flows(flows: Dict[tuple, dict], offset: float, dropped: int) -> List[dict]:
        ranked = sorted(flows.values(), key=lambda flow: flow["bytes"], reverse=True)[:TOP_FLOWS]
        out = []
        for flow in ranked:
            item = {key: value for key, value in flow.items() if not key.endswith("_epoch")}
            item["start_time"] = to_log_time(flow["first_epoch"], offset)
            item["end_time"] = to_log_time(flow["last_epoch"], offset)
            out.append(item)
        if dropped:
            logger.warning("흐름 상한(%d)을 넘겨 %d개를 세지 못했습니다", MAX_FLOWS, dropped)
        return out

    @staticmethod
    def _unanswered(pending: Dict[str, dict], answered: set, offset: float, render) -> List[dict]:
        """응답이 오지 않은 것만 골라 렌더링한다. 시간순으로 앞에서부터 자른다."""
        missing = [
            render(value) for key, value in pending.items() if key not in answered
        ]
        return sorted(missing, key=lambda item: item.get("time") or "")[:TOP_EVENTS]

    def _summarize_dns(
        self, query_count, answered, pending, latencies, dot_packets, offset
    ) -> dict:
        slow = sorted(latencies, key=lambda item: item[0], reverse=True)[:TOP_EVENTS]
        unanswered = sorted(
            (
                {"time": to_log_time(epoch, offset), "query": qname}
                for (_, qname), epoch in pending.items()
            ),
            key=lambda item: item["time"],
        )
        summary = {
            "query_count": query_count,
            "response_count": answered,
            "unanswered_count": len(pending),
            "unanswered": unanswered[:TOP_EVENTS],
            "slowest_queries": [
                {
                    "time": to_log_time(epoch, offset),
                    "query": qname,
                    "latency_ms": round(latency * 1000, 1),
                }
                for latency, qname, epoch in slow
            ],
            "max_latency_ms": round(slow[0][0] * 1000, 1) if slow else 0,
        }
        if dot_packets:
            summary["encrypted_dns"] = {
                "packets": dot_packets,
                "note": (
                    f"TCP/{DOT_PORT} (DNS over TLS) 트래픽이 있습니다. Private DNS 가 켜져 있어 "
                    "질의 내용은 캡처에서 볼 수 없고, 실패해도 이름이 남지 않습니다."
                ),
            }
        return summary

    # ------------------------------------------------------------ 2차 이상 징후 패스

    def _collect_anomalies(self, pcap_path: str, offset: float) -> dict:
        idx = {name: position for position, name in enumerate(ANOMALY_FIELDS)}
        collected: Dict[str, dict] = {}

        for name, display_filter in ANOMALY_FILTERS.items():
            events: List[dict] = []
            total = 0
            try:
                for row in _run_tshark(
                    pcap_path, ANOMALY_FIELDS, display_filter=display_filter,
                    timeout_sec=self.timeout_sec, max_rows=self.max_packets,
                ):
                    total += 1
                    if len(events) >= TOP_EVENTS:
                        continue
                    epoch = _as_float(_cell(row, idx["frame.time_epoch"]))
                    if epoch is None:
                        continue
                    src, dst = _endpoints(row, idx)
                    events.append(self._anomaly_event(name, row, idx, epoch, src, dst, offset))
            except Exception as failure:
                logger.error("tshark 이상 징후 조회 실패 (%s): %s", name, failure)
                collected[name] = {"count": 0, "events": [], "error": str(failure)}
                continue
            collected[name] = {"count": total, "events": events}

        return {
            "tcp": {
                "retransmission_count": collected["tcp_retransmissions"]["count"],
                "retransmissions": collected["tcp_retransmissions"]["events"],
                "zero_window_count": collected["tcp_zero_window"]["count"],
                "zero_window": collected["tcp_zero_window"]["events"],
                "reset_count": collected["tcp_resets"]["count"],
                "resets": collected["tcp_resets"]["events"],
            },
            "icmp": {
                "unreachable_count": collected["icmp_unreachable"]["count"],
                "unreachable": collected["icmp_unreachable"]["events"],
            },
            "dns_error_count": collected["dns_errors"]["count"],
            "dns_errors": collected["dns_errors"]["events"],
        }

    @staticmethod
    def _anomaly_event(name, row, idx, epoch, src, dst, offset) -> dict:
        event = {
            "time": to_log_time(epoch, offset),
            "src": src,
            "dst": dst,
            "protocol": _cell(row, idx["_ws.col.protocol"]),
        }
        port = _cell(row, idx["tcp.dstport"]) or _cell(row, idx["udp.dstport"])
        if port:
            event["dst_port"] = port
        if name == "icmp_unreachable":
            event["icmp_type"] = _cell(row, idx["icmp.type"])
            event["icmp_code"] = _cell(row, idx["icmp.code"])
        if name == "dns_errors":
            rcode = _cell(row, idx["dns.flags.rcode"])
            event["query"] = _cell(row, idx["dns.qry.name"])
            event["rcode"] = DNS_RCODE_NAMES.get(rcode, rcode)
        return event

    # ------------------------------------------------------------------------ KPI

    @staticmethod
    def _build_kpi(result: dict) -> dict:
        dns = result["dns"]
        tcp = result["tcp"]
        capture = result["capture"]

        attempts = tcp.get("connect_attempts") or 0
        failures = len(tcp.get("connect_failures") or [])
        queries = dns.get("query_count") or 0

        kpi = {
            "packet_count": capture["packet_count"],
            "duration_sec": capture["duration_sec"],
            "dns_query_count": queries,
            "dns_unanswered_count": dns.get("unanswered_count", 0),
            "dns_error_count": dns.get("error_count", 0),
            "dns_max_latency_ms": dns.get("max_latency_ms", 0),
            "tcp_connect_attempts": attempts,
            "tcp_connect_failure_count": failures,
            "tcp_retransmission_count": tcp.get("retransmission_count", 0),
            "tcp_reset_count": tcp.get("reset_count", 0),
            "tls_handshake_failure_count": len(result["tls"].get("handshake_failures") or []),
            "icmp_unreachable_count": result["icmp"].get("unreachable_count", 0),
            "longest_silence_sec": (
                result["silence_gaps"][0]["duration_sec"] if result["silence_gaps"] else 0
            ),
            "silence_gap_count": result.get(
                "silence_gap_count", len(result["silence_gaps"])
            ),
        }

        if attempts:
            kpi["tcp_connect_failure_rate_pct"] = round(100.0 * failures / attempts, 1)
        if queries:
            kpi["dns_unanswered_rate_pct"] = round(
                100.0 * kpi["dns_unanswered_count"] / queries, 1
            )

        kpi["verdict"] = PcapParser._verdict(kpi)
        return kpi

    @staticmethod
    def _verdict(kpi: dict) -> str:
        """패킷만 보고 말할 수 있는 한 줄. 로그와 맞춰 보기 전의 1차 소견이다."""
        if kpi["packet_count"] == 0:
            return "캡처에 패킷이 없습니다."
        if kpi.get("dns_unanswered_rate_pct", 0) >= 30:
            return "DNS 질의의 상당수가 응답을 받지 못했습니다. 이름 해석 단계에서 막혔습니다."
        if kpi.get("tcp_connect_failure_rate_pct", 0) >= 30:
            return "TCP 연결 시도의 상당수가 SYN/ACK 을 받지 못했습니다. 상위 구간 또는 망에서 막혔습니다."
        if kpi["tcp_reset_count"] and kpi["tcp_reset_count"] >= max(5, kpi["tcp_connect_attempts"] * 0.3):
            return "RST 가 반복됩니다. 상대 또는 중간 장비가 연결을 끊고 있습니다."
        if kpi["longest_silence_sec"] >= 30:
            return f"{kpi['longest_silence_sec']:.0f}초 동안 트래픽이 전혀 없는 구간이 있습니다."
        if kpi["tcp_retransmission_count"] >= max(10, kpi["packet_count"] * 0.05):
            return "재전송 비율이 높습니다. 무선 구간 품질을 함께 봐야 합니다."
        return "패킷 수준에서 두드러진 이상은 보이지 않습니다."

    @staticmethod
    def _error(status: str, message: str, pcap_path: str) -> dict:
        return {
            "status": status,
            "message": message,
            "capture": {"file": os.path.basename(pcap_path or "")},
        }


def capture_caveats(capture: dict) -> List[str]:
    """이 캡처의 결과를 그대로 믿으면 안 되는 이유들.

    파서가 이미 자기 자리에 적어 둔 문구(시간대 출처, 잘린 지점, 암호화된 DNS)를
    한 곳에 모은다. 화면과 LLM 도구가 각자 모으면 한쪽이 빠뜨렸을 때 그쪽에서만
    "패킷상 이상 없음" 으로 읽히는데, 그게 이 분석에서 가장 비싼 오독이다.
    """
    meta = capture.get("capture") or {}
    name = meta.get("file") or "캡처"
    timebase = capture.get("timebase") or {}
    alignment = timebase.get("alignment") or {}
    caveats = []

    if timebase.get("confidence") == "low":
        caveats.append(f"{name}: {timebase.get('note') or '시간대를 확정하지 못했습니다.'}")
    if alignment.get("checked") and not alignment.get("overlaps"):
        shift = alignment.get("suggested_extra_offset_hours")
        detail = f" (약 {shift:+g}시간 어긋나 보입니다)" if shift else ""
        caveats.append(f"{name}: {alignment.get('warning')}{detail}")
    if meta.get("truncated"):
        caveats.append(f"{name}: {meta.get('truncated_note')}")

    encrypted = (capture.get("dns") or {}).get("encrypted_dns")
    if encrypted:
        caveats.append(f"{name}: {encrypted.get('note')}")
    return caveats


def analyze_pcaps(
    pcap_paths: Iterable[str],
    report_data: dict = None,
    log_window: Optional[Tuple[str, str]] = None,
    tz_offset_hours: Optional[float] = None,
) -> dict:
    """여러 pcap 을 분석해 리포트에 넣을 한 덩어리로 묶는다.

    한 결함에 캡처가 여러 개 붙는 경우가 있어(단말/서버 양쪽, 재현 시도별) 파일
    하나로 가정하지 않는다. 하나가 실패해도 나머지는 그대로 살린다.
    """
    paths = [path for path in (pcap_paths or []) if path]
    if not paths:
        return {}

    parser = PcapParser(tz_offset_hours=tz_offset_hours)
    captures = [parser.analyze(path, report_data, log_window) for path in paths]
    analyzed = [item for item in captures if item.get("status") == "OK"]

    summary = {
        "capture_count": len(captures),
        "analyzed_count": len(analyzed),
        "captures": captures,
        "tshark_version": tshark_version(),
    }
    if not analyzed:
        summary["status"] = captures[0].get("status", "NO_DATA")
        summary["message"] = captures[0].get("message", "")
    else:
        summary["status"] = "OK"
    return summary
