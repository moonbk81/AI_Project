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
        r'(data stall|DataStall|onDataStallAlarm|Suspecting data stall|trigger data stall|Data stall detected)',
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

    def analyze(self, lines, data_call_events=None, report_data=None):
        data_call_events = data_call_events or []
        report_data = report_data or {}

        timeline = []
        recent_context = deque(maxlen=80)

        for line in lines:
            clean = self.clean_line(line)
            if not clean:
                continue

            ts = self._extract_time(clean)
            if ts:
                event = self._classify_line(clean, ts)
                if event:
                    event["context_before"] = list(recent_context)[-20:]
                    timeline.append(event)

            recent_context.append(clean)

        # 기존 DataCallProcessor 결과를 timeline에 합침
        timeline.extend(self._convert_data_call_events(data_call_events))

        # report_data의 RF/OOS/signal을 timeline에 일부 합침
        timeline.extend(self._convert_rf_events(report_data))

        timeline = sorted(
            timeline,
            key=lambda x: self._to_sort_key(x.get("time"))
        )

        stall_windows = self._build_stall_windows(timeline)
        root_summary = self._summarize_root_causes(stall_windows, timeline)
        kpi = self._build_kpi(timeline, stall_windows, root_summary)

        return {
            "schema_version": "internet_stall_v1",
            "kpi": kpi,
            "root_cause_summary": root_summary,
            "stall_windows": stall_windows,
            "timeline": timeline[-2000:]
        }

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
        layer = None
        event_type = None
        severity = "info"
        reason = ""

        if self.DATA_STALL_RE.search(line):
            layer = "DATA_STALL"
            event_type = "DATA_STALL_DETECTED"
            severity = "critical"
            reason = "Data stall 감지/복구 관련 로그"

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

        return {
            "time": ts,
            "layer": layer,
            "event_type": event_type,
            "severity": severity,
            "reason": reason,
            "raw": line,
            "net_id": self._extract_net_id(line),
            "package": self._extract_package(line)
        }

    def _convert_data_call_events(self, data_call_events):
        converted = []
        for e in data_call_events:
            if not isinstance(e, dict):
                continue

            event_type = e.get("event_type", "DATA_CALL")
            time_value = e.get("req_time") or e.get("res_time")
            if not time_value:
                continue

            severity = "info"
            layer = "DATA_CALL"
            mapped_type = event_type
            reason = e.get("cause", "")

            if event_type == "DATA_STALL_RECOVERY":
                layer = "DATA_STALL"
                mapped_type = "DATA_STALL_RECOVERY"
                severity = "critical"
            elif event_type == "DATA_SETUP" and e.get("status") != "SUCCESS":
                severity = "warning"
                mapped_type = "DATA_SETUP_FAIL"
            elif event_type == "DATA_DEACTIVATE":
                mapped_type = "DATA_DEACTIVATE"
            elif event_type == "UNSOL_UPDATE" and "DROP" in str(e.get("status", "")).upper():
                severity = "warning"
                mapped_type = "DATA_CALL_DROP"

            converted.append({
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
                "raw": e.get("raw_payload") or json.dumps(e, ensure_ascii=False)
            })
        return converted

    def _convert_rf_events(self, report_data):
        converted = []

        network_history = report_data.get("telephony", {}).get("network_history", [])
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
            "DATA_STALL_RECOVERY",
            "VALIDATION_FAIL",
            "DNS_ISSUE",
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

    def _infer_window_causes(self, related):
        layers = defaultdict(int)
        types = defaultdict(int)

        for e in related:
            layers[e.get("layer", "UNKNOWN")] += 1
            types[e.get("event_type", "UNKNOWN")] += 1

        candidates = []

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

    def _build_kpi(self, timeline, stall_windows, root_summary):
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
            "data_stall_count": count_by_type.get("DATA_STALL_DETECTED", 0) + count_by_type.get("DATA_STALL_RECOVERY", 0),
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
