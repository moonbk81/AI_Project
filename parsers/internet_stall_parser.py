import os
import re
import json
from datetime import datetime, timedelta
from collections import defaultdict, deque

try:
    from parsers.base import BaseParser
except Exception:
    class BaseParser:
        def clean_line(self, line):
            return line.rstrip("\n")


class InternetStallParser(BaseParser):
    def _should_inspect_line(self, line):
        """정규식 검사 전에 명백히 무관한 라인을 빠르게 제외합니다."""
        return any(marker in line for marker in (
            "dns", "DNS", "Dns", "resolv", "PrivateDns", "DoT", "netd",
            "NetworkMonitor", "ConnectivityService", "DefaultNetwork", "NetworkAgent",
            "NetworkCapabilities", "LinkProperties", "CaptivePortal", "validation",
            "VALIDATED", "NO_INTERNET", "PARTIAL_CONNECTIVITY",
            "data stall", "DataStall", "onDataStallAlarm", "Suspecting data stall",
            "ETIMEDOUT", "ECONNRESET", "ECONNREFUSED", "SocketTimeout",
            "connect timed out", "connection timed out", "TLS handshake", "SSLException",
            "No route to host", "Network is unreachable",
            "DeviceIdleController", "doze", "idle", "AppStandby", "PowerManager",
            "wakelock", "screen off", "screen_on", "RadioPower", "RADIO_POWER",
        ))

    def _is_data_stall_manager_noise(self, line):
        """DataStallRecoveryManager 초기화/내부 메시지는 실제 stall/recovery 이벤트가 아니므로 제외합니다."""
        noise_markers = (
            "DataStallRecoveryManager created",
            "createDataStallRecoveryRandomOffsetsMillis",
            "Randomization disabled",
            "target=com.android.internal.telephony.data.DataStallRecoveryManager",
            "DataStallRecoveryManager async=false",
            "Manager created.",
            "Manager async=false",
            "RandomOffsetsMillis(): Randomization disabled.",
        )
        return any(marker in line for marker in noise_markers)

    def _compact_event_for_output(self, event, keep_context=False):
        """JSON 결과가 과도하게 커지지 않도록 이벤트 저장 크기를 줄입니다."""
        compact = {
            "time": event.get("time"),
            "layer": event.get("layer"),
            "event_type": event.get("event_type"),
            "severity": event.get("severity"),
            "reason": event.get("reason"),
            "raw": event.get("raw"),
            "net_id": event.get("net_id"),
            "package": event.get("package"),
            "cid": event.get("cid"),
            "apn": event.get("apn"),
            "network": event.get("network"),
            "protocol": event.get("protocol"),
            "latency_ms": event.get("latency_ms"),
            "slot": event.get("slot"),
            "rat": event.get("rat"),
            "phase": event.get("phase"),
        }
        compact = {k: v for k, v in compact.items() if v not in (None, "")}

        if keep_context:
            context_before = event.get("context_before") or []
            context_after = event.get("context_after") or []
            compact["context_before"] = context_before[-5:]
            compact["context_after"] = context_after[:5]

        return compact

    def _compact_stall_windows_for_output(self, stall_windows, max_windows=80, max_events_per_window=30):
        """핵심 window와 대표 이벤트만 저장해 UI/JSON 크기를 제한합니다."""
        if not stall_windows:
            return []

        sorted_windows = sorted(
            stall_windows,
            key=lambda w: w.get("severity_score", 0),
            reverse=True,
        )

        compact_windows = []
        for window in sorted_windows[:max_windows]:
            related_events = window.get("related_events") or []
            important_events = []
            representative_info_events = []
            seen_info_types = set()

            for event in related_events:
                severity = event.get("severity")
                event_type = event.get("event_type")
                layer = event.get("layer")

                if severity in ("warning", "error", "critical") or layer in ("DNS", "VALIDATION", "DATA_STALL", "TCP_TLS", "RF", "DATA_CALL"):
                    important_events.append(event)
                    continue

                if event_type not in seen_info_types:
                    seen_info_types.add(event_type)
                    representative_info_events.append(event)

            selected_events = (important_events + representative_info_events)[:max_events_per_window]

            compact_windows.append({
                "center_time": window.get("center_time"),
                "trigger": window.get("trigger"),
                "trigger_reason": window.get("trigger_reason"),
                "severity_score": window.get("severity_score"),
                "layer_counts": window.get("layer_counts"),
                "root_cause_candidates": window.get("root_cause_candidates"),
                "related_events": [
                    self._compact_event_for_output(
                        event,
                        keep_context=event.get("severity") in ("warning", "error", "critical")
                    )
                    for event in selected_events
                ],
                "related_event_total_count": len(related_events),
                "related_event_saved_count": len(selected_events),
            })

        return compact_windows
    """
    인터넷 멈춤/끊김 체감 현상을 계층별로 분석하는 독립 parser.

    목표:
    - 기존 DataCallProcessor 결과는 그대로 활용
    - raw log에서 DNS / NetworkMonitor / ConnectivityService / TCP / Power 힌트 추출
    - report_data의 RF/OOS/signal 정보와 시간 상관관계 계산
    - 결과는 <base_name>_internet_stall.json 형태로 저장 가능

    주요 출력:
    - kpi
    - timeline
    - stall_windows
    - root_cause_summary
    """

    TIME_RE = re.compile(r'^(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})')

    DNS_RE = re.compile(
        r'(dns|DnsResolver|resolv|PrivateDns|DoT|netd|ResolverController).*'
        r'(timeout|timed out|fail|failed|SERVFAIL|NXDOMAIN|unreachable|No address|rcode|latency|query)',
        re.IGNORECASE
    )

    NETWORK_MONITOR_RE = re.compile(
        r'(NetworkMonitor|ConnectivityService|DefaultNetwork|NetworkAgent|NetworkCapabilities|LinkProperties|CaptivePortal|validation|VALIDATED|NO_INTERNET|PARTIAL_CONNECTIVITY)',
        re.IGNORECASE
    )

    DATA_STALL_RE = re.compile(
        r'(data stall|DataStall|onDataStallAlarm|Suspecting data stall|trigger data stall|Data stall detected|hdd_data_stall_send_event)',
        re.IGNORECASE
    )

    TCP_RE = re.compile(
        r'(ETIMEDOUT|ECONNRESET|ECONNREFUSED|SocketTimeout|connect timed out|connection timed out|TLS handshake|SSLException|No route to host|Network is unreachable)',
        re.IGNORECASE
    )

    POWER_RE = re.compile(
        r'(DeviceIdleController|doze|idle|AppStandby|PowerManager|wakelock|screen off|screen_on|RadioPower|RADIO_POWER)',
        re.IGNORECASE
    )

    PRIVATE_DNS_RE = re.compile(
        r'(PrivateDns|private dns|DoT|dns-over-tls|TLS).*?(fail|failed|timeout|unreachable|broken)',
        re.IGNORECASE
    )

    DEFAULT_NETWORK_RE = re.compile(
        r'(default network|DefaultNetwork|setDefault|NetworkAgentInfo|netId|NetworkCapabilities|LinkProperties)',
        re.IGNORECASE
    )

    VALIDATION_FAIL_RE = re.compile(
        r'(validation failed|NO_INTERNET|PARTIAL_CONNECTIVITY|lost validation|not validated|Invalidated|CaptivePortal)',
        re.IGNORECASE
    )

    VALIDATION_PASS_RE = re.compile(
        r'(validation passed|VALIDATED|validated=true|isValidated)',
        re.IGNORECASE
    )

    def analyze(self, lines, data_call_events=None, dns_events=None, report_data=None):
        data_call_events = data_call_events or []
        dns_events = dns_events or []
        report_data = report_data or {}

        timeline = []
        recent_context = deque(maxlen=80)

        for line in lines:
            raw_line = str(line)
            if not self._should_inspect_line(raw_line):
                continue

            clean = self.clean_line(raw_line)
            if not clean:
                continue

            ts = self._extract_time(clean)
            if ts:
                event = self._classify_line(clean, ts)
                if event:
                    event["context_before"] = list(recent_context)[-8:]
                    timeline.append(event)

            recent_context.append(clean)

        # 기존 DataCallProcessor 결과를 timeline에 합침
        timeline.extend(self._convert_data_call_events(data_call_events))

        # DNS_Query 결과를 timeline에 합침
        timeline.extend(self._convert_dns_query_events(dns_events))

        # report_data의 RF/OOS/signal을 timeline에 일부 합침
        timeline.extend(self._convert_rf_events(report_data))

        timeline = sorted(
            timeline,
            key=lambda x: self._to_sort_key(x.get("time"))
        )

        data_stall_flows = self._build_data_stall_flows(timeline)
        stall_windows = self._build_stall_windows(timeline)
        root_summary = self._summarize_root_causes(stall_windows, timeline)
        kpi = self._build_kpi(timeline, stall_windows, root_summary, data_stall_flows)

        return {
            "schema_version": "internet_stall_v1",
            "kpi": kpi,
            "data_stall_flows": data_stall_flows[:80],
            "root_cause_summary": root_summary,
            "stall_windows": self._compact_stall_windows_for_output(stall_windows),
            "timeline": [self._compact_event_for_output(event) for event in timeline[-300:]],
            "timeline_total_count": len(timeline),
            "stall_window_total_count": len(stall_windows),
        }
    def _convert_dns_query_events(self, dns_events):
        converted = []

        for e in dns_events:
            if not isinstance(e, dict):
                continue

            time_value = e.get("time")
            if not time_value:
                continue

            latency_ms = e.get("latency_ms")
            severity = "info"
            event_type = "DNS_QUERY"
            reason = f"DNS query by {e.get('app_name', 'unknown')}"

            if isinstance(latency_ms, (int, float)):
                if latency_ms >= 5000:
                    severity = "critical"
                    event_type = "DNS_SLOW_RESPONSE"
                elif latency_ms >= 1000:
                    severity = "warning"
                    event_type = "DNS_SLOW_RESPONSE"

            converted.append({
                "time": time_value,
                "layer": "DNS",
                "event_type": event_type,
                "severity": severity,
                "reason": reason,
                "net_id": e.get("net_id"),
                "package": e.get("app_name"),
                "latency_ms": latency_ms,
                "raw": json.dumps(e, ensure_ascii=False)
            })

        return converted

    def save_ui_report(self, output_dir="./result", base_name="", analysis=None):
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f"{base_name}_internet_stall.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(analysis or {}, f, indent=4, ensure_ascii=False)
        return out_path

    def _extract_time(self, line):
        m = self.TIME_RE.search(line)
        return m.group(1) if m else None

    def _classify_line(self, line, ts):
        if self._is_data_stall_manager_noise(line):
            return None

        layer = None
        event_type = None
        severity = "info"
        reason = ""

        if self.DATA_STALL_RE.search(line):
            data_stall_type, phase = self._classify_data_stall_phase(line)
            if not data_stall_type:
                return None

            layer = "DATA_STALL"
            event_type = data_stall_type
            severity = "critical"
            reason = "Data stall 시작/복구/종료 흐름"

        elif self.VALIDATION_FAIL_RE.search(line):
            layer = "VALIDATION"
            event_type = "VALIDATION_FAIL"
            severity = "warning"
            reason = "Network validation 실패 또는 부분 연결"

        elif self.VALIDATION_PASS_RE.search(line) and self.NETWORK_MONITOR_RE.search(line):
            layer = "VALIDATION"
            event_type = "VALIDATION_PASS"
            severity = "info"
            reason = "Network validation 회복/성공"

        elif self.PRIVATE_DNS_RE.search(line):
            layer = "DNS"
            event_type = "PRIVATE_DNS_FAIL"
            severity = "warning"
            reason = "Private DNS / DoT 실패 의심"

        elif self.DNS_RE.search(line):
            layer = "DNS"
            event_type = "DNS_ISSUE"
            severity = "warning"
            reason = "DNS timeout/fail/latency 관련 로그"

        elif self.TCP_RE.search(line):
            layer = "TCP_TLS"
            event_type = "TCP_TLS_TIMEOUT"
            severity = "warning"
            reason = "TCP/TLS 연결 timeout/reset 계열"

        elif self.NETWORK_MONITOR_RE.search(line):
            layer = "NETWORK"
            event_type = "NETWORK_STATE"
            severity = "info"
            reason = "ConnectivityService/NetworkMonitor 상태 변화"

        elif self.DEFAULT_NETWORK_RE.search(line):
            layer = "ROUTING"
            event_type = "DEFAULT_NETWORK_CHANGE"
            severity = "info"
            reason = "default network / netId / LinkProperties 변화"

        elif self.POWER_RE.search(line):
            layer = "POWER"
            event_type = "POWER_IDLE_HINT"
            severity = "info"
            reason = "Doze/Idle/Radio power 관련 힌트"

        if not layer:
            return None

        event = {
            "time": ts,
            "layer": layer,
            "event_type": event_type,
            "severity": severity,
            "reason": reason,
            "raw": line,
            "net_id": self._extract_net_id(line),
            "package": self._extract_package(line)
        }
        if layer == "DATA_STALL":
            event["phase"] = phase
        return event

    def _classify_data_stall_phase(self, line):
        lower = line.lower()

        # Throughput/CCA samples are useful for low-level Wi-Fi debugging, but
        # they are not lifecycle boundaries for a user-facing stall summary.
        if "wifidatastall:" in lower and not any(
            marker in lower for marker in ("suspecting", "detected", "data stall event", "recovery")
        ):
            return None, None

        if any(marker in lower for marker in ("recovered", "resolved", "end data stall", "data stall end", "datastalled:false")):
            return "DATA_STALL_END", "end"

        if "recovery" in lower:
            if any(marker in lower for marker in ("start", "begin", "trigger", "attempt")):
                return "DATA_STALL_RECOVERY_START", "recovery_start"
            if any(marker in lower for marker in ("end", "finish", "done", "complete", "success", "recovered")):
                return "DATA_STALL_RECOVERY_END", "recovery_end"
            return "DATA_STALL_RECOVERY", "recovery"

        if any(marker in lower for marker in ("start", "begin", "suspecting", "detected", "onDataStallAlarm".lower(), "data stall event")):
            return "DATA_STALL_START", "start"

        return "DATA_STALL_DETECTED", "detected"

    def _convert_data_call_events(self, data_call_events):
        converted = []
        for e in data_call_events:
            if not isinstance(e, dict):
                continue

            event_type = e.get("event_type", "DATA_CALL")
            time_value = e.get("req_time") or e.get("res_time")
            if not time_value:
                continue

            raw_payload = e.get("raw_payload") or json.dumps(e, ensure_ascii=False)
            if self._is_data_stall_manager_noise(str(raw_payload)):
                continue
            severity = "info"
            layer = "DATA_CALL"
            mapped_type = event_type
            reason = e.get("cause", "")

            if event_type == "DATA_STALL_RECOVERY":
                layer = "DATA_STALL"
                mapped_type = "DATA_STALL_RECOVERY"
                severity = "critical"
                phase = "recovery"
            elif event_type == "DATA_SETUP" and e.get("status") != "SUCCESS":
                severity = "warning"
                mapped_type = "DATA_SETUP_FAIL"
            elif event_type == "DATA_DEACTIVATE":
                mapped_type = "DATA_DEACTIVATE"
            elif event_type == "UNSOL_UPDATE" and "DROP" in str(e.get("status", "")).upper():
                severity = "warning"
                mapped_type = "DATA_CALL_DROP"

            converted_event = {
                "time": time_value,
                "layer": layer,
                "event_type": mapped_type,
                "severity": severity,
                "reason": reason,
                "cid": e.get("cid"),
                "apn": e.get("apn"),
                "network": e.get("network"),
                "protocol": e.get("protocol"),
                "latency_ms": e.get("latency_ms"),
                "raw": raw_payload
            }
            if layer == "DATA_STALL":
                converted_event["phase"] = phase
            converted.append(converted_event)
        return converted

    def _convert_rf_events(self, report_data):
        converted = []

        network_history = report_data.get("oos_events", [])
        for e in network_history:
            if not isinstance(e, dict):
                continue
            t = e.get("time")
            if not t:
                continue

            v_reg = str(e.get("voice_reg", ""))
            d_reg = str(e.get("data_reg", ""))
            is_oos = any(x in (v_reg + d_reg).upper() for x in ["OUT_OF_SERVICE", "OOS", "POWER_OFF"])

            converted.append({
                "time": t,
                "layer": "RF",
                "event_type": "OOS_OR_REG_STATE" if is_oos else "REG_STATE_CHANGE",
                "severity": "warning" if is_oos else "info",
                "reason": f"voice={v_reg}, data={d_reg}",
                "slot": e.get("slotId"),
                "raw": json.dumps(e, ensure_ascii=False)
            })

        signal_history = report_data.get("signal_level_history", [])
        for e in signal_history:
            if not isinstance(e, dict):
                continue
            t = e.get("time")
            level = e.get("level")
            if not t:
                continue

            try:
                level_int = int(level)
            except Exception:
                continue

            if level_int <= 1:
                converted.append({
                    "time": t,
                    "layer": "RF",
                    "event_type": "WEAK_SIGNAL",
                    "severity": "warning",
                    "reason": f"signal level={level_int}",
                    "slot": e.get("slot"),
                    "rat": e.get("rat"),
                    "raw": json.dumps(e, ensure_ascii=False)
                })

        return converted

    def _build_stall_windows(self, timeline, window_sec=10):
        trigger_types = {
            "DATA_STALL_DETECTED",
            "DATA_STALL_START",
            "DATA_STALL_RECOVERY",
            "DATA_STALL_RECOVERY_START",
            "DATA_STALL_RECOVERY_END",
            "DATA_STALL_END",
            "VALIDATION_FAIL",
            "DNS_ISSUE",
            "DNS_SLOW_RESPONSE",
            "PRIVATE_DNS_FAIL",
            "TCP_TLS_TIMEOUT"
        }

        windows = []
        for idx, event in enumerate(timeline):
            if event.get("event_type") not in trigger_types:
                continue

            center_dt = self._parse_time(event.get("time"))
            if not center_dt:
                continue

            start_dt = center_dt - timedelta(seconds=window_sec)
            end_dt = center_dt + timedelta(seconds=window_sec)

            related = []
            for candidate in timeline:
                cdt = self._parse_time(candidate.get("time"))
                if cdt and start_dt <= cdt <= end_dt:
                    related.append(candidate)

            layer_counts = defaultdict(int)
            severity_score = 0
            for r in related:
                layer_counts[r.get("layer", "UNKNOWN")] += 1
                severity_score += {"info": 1, "warning": 3, "critical": 5}.get(r.get("severity"), 1)
                latency_ms = r.get("latency_ms")
                if isinstance(latency_ms, (int, float)):
                    if latency_ms >= 10000:
                        severity_score += 10
                    elif latency_ms >= 5000:
                        severity_score += 5
                    elif latency_ms >= 1000:
                        severity_score += 2

            root_candidates = self._infer_window_causes(related)

            windows.append({
                "center_time": event.get("time"),
                "trigger": event.get("event_type"),
                "trigger_reason": event.get("reason"),
                "severity_score": severity_score,
                "layer_counts": dict(layer_counts),
                "root_cause_candidates": root_candidates,
                "related_events": related[:120]
            })

        # 같은 시간대 중복이 많을 수 있으므로 trigger 기준 근접 window를 단순 dedup
        deduped = []
        seen_keys = set()
        for w in windows:
            key = (w["center_time"][:14], w["trigger"])
            if key not in seen_keys:
                seen_keys.add(key)
                deduped.append(w)

        return deduped[-200:]

    def _build_data_stall_flows(self, timeline):
        events = [
            e for e in timeline
            if e.get("layer") == "DATA_STALL"
            and e.get("event_type") in {
                "DATA_STALL_START",
                "DATA_STALL_DETECTED",
                "DATA_STALL_RECOVERY",
                "DATA_STALL_RECOVERY_START",
                "DATA_STALL_RECOVERY_END",
                "DATA_STALL_END",
            }
        ]
        if not events:
            return []

        flows = []
        current = None

        for event in events:
            event_type = event.get("event_type")
            event_time = event.get("time")
            parsed_time = self._parse_time(event_time)

            if event_type in ("DATA_STALL_START", "DATA_STALL_DETECTED") or current is None:
                if current is not None:
                    flows.append(current)
                current = {
                    "start_time": event_time,
                    "recovery_start_time": None,
                    "recovery_end_time": None,
                    "end_time": None,
                    "duration_sec": None,
                    "status": "진행/종료 미확인",
                    "event_count": 1,
                    "trigger": event_type,
                    "reason": event.get("reason"),
                    "raw": event.get("raw"),
                }
                continue

            current["event_count"] += 1

            if event_type in ("DATA_STALL_RECOVERY", "DATA_STALL_RECOVERY_START") and not current.get("recovery_start_time"):
                current["recovery_start_time"] = event_time
            elif event_type == "DATA_STALL_RECOVERY_END":
                current["recovery_end_time"] = event_time
            elif event_type == "DATA_STALL_END":
                current["end_time"] = event_time

            end_time = current.get("end_time") or current.get("recovery_end_time")
            start_dt = self._parse_time(current.get("start_time"))
            end_dt = self._parse_time(end_time)
            if start_dt and end_dt:
                current["duration_sec"] = round((end_dt - start_dt).total_seconds(), 3)
                current["status"] = "회복 완료"
            elif parsed_time and event_type in ("DATA_STALL_RECOVERY", "DATA_STALL_RECOVERY_START"):
                current["status"] = "복구 진행"

        if current is not None:
            flows.append(current)

        return flows[-200:]

    def _infer_window_causes(self, related):
        layers = defaultdict(int)
        types = defaultdict(int)

        for e in related:
            layers[e.get("layer", "UNKNOWN")] += 1
            types[e.get("event_type", "UNKNOWN")] += 1

        candidates = []

        max_dns_latency = max(
            [e.get("latency_ms", 0) for e in related if isinstance(e.get("latency_ms"), (int, float))],
            default=0
        )

        if max_dns_latency >= 5000:
            candidates.append({
                "category": "DNS_LATENCY_SPIKE",
                "confidence": "high",
                "reason": f"DNS latency spike detected (max={max_dns_latency}ms)"
            })

        if layers["RF"] > 0 and (layers["DATA_CALL"] > 0 or layers["DATA_STALL"] > 0 or layers["VALIDATION"] > 0):
            candidates.append({
                "category": "RF_OR_COVERAGE",
                "confidence": "high",
                "reason": "OOS/약전계가 인터넷 장애 이벤트 근처에 존재"
            })

        if layers["DATA_CALL"] > 0 and (types["DATA_SETUP_FAIL"] > 0 or types["DATA_DEACTIVATE"] > 0 or layers["DATA_STALL"] > 0):
            candidates.append({
                "category": "RIL_DATA_CALL",
                "confidence": "high",
                "reason": "SETUP/DEACTIVATE/Data stall 이벤트가 장애 window에 존재"
            })

        if layers["DNS"] > 0 and layers["DATA_CALL"] == 0 and layers["RF"] == 0:
            candidates.append({
                "category": "DNS_OR_PRIVATE_DNS",
                "confidence": "medium",
                "reason": "RF/DataCall 변화 없이 DNS/Private DNS 실패가 중심"
            })

        if layers["VALIDATION"] > 0 and layers["DNS"] > 0:
            candidates.append({
                "category": "NETWORK_VALIDATION",
                "confidence": "medium",
                "reason": "DNS 이슈와 Network validation 실패가 동반"
            })

        if layers["TCP_TLS"] > 0 and layers["DNS"] == 0:
            candidates.append({
                "category": "TCP_TLS_OR_SERVER_PATH",
                "confidence": "medium",
                "reason": "DNS보다 TCP/TLS timeout/reset 계열 힌트가 중심"
            })

        if layers["POWER"] > 0 and (layers["DATA_STALL"] > 0 or layers["DATA_CALL"] > 0):
            candidates.append({
                "category": "POWER_IDLE_POLICY",
                "confidence": "low",
                "reason": "Doze/Idle/Radio power 힌트가 네트워크 장애 근처에 존재"
            })

        if not candidates:
            candidates.append({
                "category": "UNKNOWN",
                "confidence": "low",
                "reason": "명확한 계층 상관관계 부족"
            })

        return candidates

    def _summarize_root_causes(self, stall_windows, timeline):
        summary = defaultdict(lambda: {"count": 0, "confidence": defaultdict(int), "examples": []})

        for w in stall_windows:
            for c in w.get("root_cause_candidates", []):
                category = c.get("category", "UNKNOWN")
                summary[category]["count"] += 1
                summary[category]["confidence"][c.get("confidence", "unknown")] += 1
                if len(summary[category]["examples"]) < 3:
                    summary[category]["examples"].append({
                        "time": w.get("center_time"),
                        "trigger": w.get("trigger"),
                        "reason": c.get("reason")
                    })

        return {
            k: {
                "count": v["count"],
                "confidence": dict(v["confidence"]),
                "examples": v["examples"]
            }
            for k, v in sorted(summary.items(), key=lambda item: item[1]["count"], reverse=True)
        }

    def _build_kpi(self, timeline, stall_windows, root_summary, data_stall_flows=None):
        count_by_type = defaultdict(int)
        count_by_layer = defaultdict(int)

        for e in timeline:
            count_by_type[e.get("event_type", "UNKNOWN")] += 1
            count_by_layer[e.get("layer", "UNKNOWN")] += 1

        high_risk_windows = [
            w for w in stall_windows
            if w.get("severity_score", 0) >= 10
        ]

        primary_candidate = "UNKNOWN"
        if root_summary:
            primary_candidate = next(iter(root_summary.keys()))

        return {
            "total_timeline_events": len(timeline),
            "stall_window_count": len(stall_windows),
            "high_risk_window_count": len(high_risk_windows),
            "primary_root_cause_candidate": primary_candidate,
            "dns_issue_count": count_by_type.get("DNS_ISSUE", 0) + count_by_type.get("PRIVATE_DNS_FAIL", 0),
            "validation_fail_count": count_by_type.get("VALIDATION_FAIL", 0),
            "data_stall_count": sum(
                count_by_type.get(name, 0)
                for name in (
                    "DATA_STALL_DETECTED",
                    "DATA_STALL_START",
                    "DATA_STALL_RECOVERY",
                    "DATA_STALL_RECOVERY_START",
                    "DATA_STALL_RECOVERY_END",
                    "DATA_STALL_END",
                )
            ),
            "data_stall_flow_count": len(data_stall_flows or []),
            "data_call_fail_or_drop_count": count_by_type.get("DATA_SETUP_FAIL", 0) + count_by_type.get("DATA_CALL_DROP", 0),
            "tcp_tls_timeout_count": count_by_type.get("TCP_TLS_TIMEOUT", 0),
            "rf_warning_count": count_by_layer.get("RF", 0),
            "power_idle_hint_count": count_by_layer.get("POWER", 0),
            "layer_counts": dict(count_by_layer),
            "event_type_counts": dict(count_by_type)
        }

    def _extract_net_id(self, line):
        m = re.search(r'\bnetId[=: ]+(\d+)', line, re.IGNORECASE)
        return m.group(1) if m else None

    def _extract_package(self, line):
        m = re.search(r'\b(?:pkg|package|uidName)[=: ]+([a-zA-Z0-9_.$:-]+)', line)
        return m.group(1) if m else None

    def _parse_time(self, value):
        if not value:
            return None

        value = str(value).strip()
        current_year = datetime.now().year

        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%m-%d %H:%M:%S.%f", "%m-%d %H:%M:%S"):
            try:
                if fmt.startswith("%m"):
                    return datetime.strptime(f"{current_year}-{value}", f"%Y-{fmt}")
                return datetime.strptime(value, fmt)
            except Exception:
                pass

        return None

    def _to_sort_key(self, value):
        dt = self._parse_time(value)
        return dt or datetime.min
