import json
import os
import argparse

class RagPayloadBuilder:
    def __init__(self, input_file):
        self.input_file = input_file

    def _build_markdown_doc(self, data_dict, log_type):
        """임베딩 및 LLM이 읽을 아주 가벼운 본문(Document) 생성"""
        lines = [f"### [Type: {log_type}]"]

        # 제외할 키워드 (전체 원문을 다 넣으면 임베딩 품질이 떨어지므로 일단 제외)
        exclude_keys = ["logs", "context_snapshot", "context", "stack", "call_stack", "raw_logs", "cross_context_logs"]

        for key, value in data_dict.items():
            if key in exclude_keys:
                continue
            if isinstance(value, dict):
                for sub_k, sub_v in value.items():
                    lines.append(f"- {key}_{sub_k}: {sub_v}")
            else:
                lines.append(f"- {key}: {value}")

        # 원문 로그가 임베딩 모델의 눈에 띄도록, 에러가 몰려있는 마지막 5줄만 강제로 본문에 삽입합니다.
        raw_snippets = []
        for exc_key in exclude_keys:
            if exc_key in data_dict and data_dict[exc_key]:
                val = data_dict[exc_key]
                if isinstance(val, list) and len(val) > 0:
                    # 긴 스택이나 로그에서 핵심 단서가 있는 뒷부분 추출 (최대 5줄)
                    snippet = "\n".join(str(x) for x in val[-5:])
                    raw_snippets.append(f"[{exc_key} Key Snippet]:\n{snippet}")

        if raw_snippets:
            lines.append("\n" + "\n".join(raw_snippets))

        return "\n".join(lines)

    def _extract_metadata(self, data_dict, log_type):
        """엔지니어가 확인할 정보와 차트용 수치를 메타데이터에 포함 (에러 원천 차단)"""
        base_name = os.path.basename(self.input_file).replace("_report.json", "")
        metadata = {"log_type": log_type}

        def add_safe_meta(key, val):
            if "stack" in key: return
            if val in [None, [], {}]: return
            if not isinstance(val, (str, int, float, bool)):
                metadata[key] = str(val)
            else:
                metadata[key] = val

        for k, v in data_dict.items():
            if k in ["logs", "context_snapshot", "context", "stack", "call_stack", "raw_logs", "cross_context_logs"]:
                continue
            if isinstance(v, dict):
                for sub_k, sub_v in v.items():
                    add_safe_meta(f"{k}_{sub_k}", sub_v)
            else:
                add_safe_meta(k, v)

        MAX_LINES = 300
        def get_safe_list(log_list):
            if not isinstance(log_list, list): return log_list
            if len(log_list) > MAX_LINES:
                return log_list[:150] + ["\n... [초대용량 로그 중략됨] ...\n"] + log_list[-150:]
            return log_list.copy()

        # 🚨 100% 안전한 키 접근 (.get 방식)
        if data_dict.get("logs"):
            metadata["raw_logs"] = json.dumps(get_safe_list(data_dict.get("logs")), ensure_ascii=False)
        if data_dict.get("context_snapshot"):
            metadata["raw_context"] = json.dumps(get_safe_list(data_dict.get("context_snapshot")), ensure_ascii=False)
        if data_dict.get("context"):
            metadata["raw_context"] = json.dumps(get_safe_list(data_dict.get("context")), ensure_ascii=False)

        if data_dict.get("request_time"): metadata["time"] = data_dict.get("request_time")
        elif data_dict.get("start_time"): metadata["time"] = data_dict.get("start_time")
        elif data_dict.get("time"): metadata["time"] = data_dict.get("time")
        elif data_dict.get("stats_period"): metadata["time"] = data_dict.get("stats_period")
        # 🚨 Boot_Stat의 Time_ms도 표준 time으로 인식하게 함
        elif data_dict.get("Time_ms") is not None: metadata["time"] = data_dict.get("Time_ms")


        if data_dict.get("slot"): metadata["slot"] = data_dict.get("slot")
        elif data_dict.get("slotId"): metadata["slot"] = data_dict.get("slotId")

        # 🚨 Signal_Level 전용 메타데이터 통과시키기
        if data_dict.get("rat") is not None:
            metadata["rat"] = data_dict.get("rat")
        if data_dict.get("level") is not None:
            metadata["level"] = data_dict.get("level")
        if data_dict.get("raw_info"):
            metadata["raw_info"] = data_dict.get("raw_info")

        if data_dict.get("top_method") is not None:
            metadata["top_method"] = data_dict.get("top_method")
        if data_dict.get("exception_info") is not None:
            metadata["exception_info"] = data_dict.get("exception_info")

        return metadata

    def _safe_int(self, value, default=0):
        try:
            if value is None:
                return default
            return int(str(value).replace(",", "").strip())
        except Exception:
            return default

    def _extract_leaked_descriptor(self, text: str) -> str:
        text = text or ""
        if "IIntentReceiver" in text:
            return "android.content.IIntentReceiver"
        if "IContentProvider" in text:
            return "android.content.IContentProvider"
        if "IServiceConnection" in text:
            return "android.app.IServiceConnection"
        return "Unknown"

    def _extract_proxy_count(self, warning: dict) -> int:
        for key in ("max_count", "count", "proxy_count", "max_proxy_count"):
            if warning.get(key) is not None:
                return self._safe_int(warning.get(key), 0)

        text = " ".join([
            str(warning.get("desc", "")),
            str(warning.get("raw", "")),
            str(warning.get("raw_info", "")),
            str(warning.get("details", "")),
        ])

        import re
        nums = [self._safe_int(x, 0) for x in re.findall(r"\b\d{3,7}\b", text)]
        return max(nums) if nums else 0

    def _build_binder_leak_rca_docs(self, report_data):
        """
        RCA Layer:
        Binder Proxy Histogram + am_wtf + am_kill 을 하나의 원인 분석 문서로 합성한다.
        TC 전용이 아니라 실제 분석 품질 개선용 상위 RCA 문서.
        """
        rca_docs = []

        binder_warnings = report_data.get("binder_warnings", []) or []
        crashes = report_data.get("crash_context", []) or []

        leak_warnings = [
            bw for bw in binder_warnings
            if isinstance(bw, dict)
            and bw.get("type") in (
                "BINDER_PROXY_HISTOGRAM",
                "BINDER_PROXY_LEAK",
                "BINDER_PROXY_LEAK_SUMMARY"
            )
        ]

        if not leak_warnings:
            return rca_docs

        leak_warnings = sorted(
            leak_warnings,
            key=lambda x: self._extract_proxy_count(x),
            reverse=True
        )
        top_leak = leak_warnings[0]

        leak_text = " ".join([
            str(top_leak.get("desc", "")),
            str(top_leak.get("raw", "")),
            str(top_leak.get("raw_info", "")),
            str(top_leak.get("details", "")),
        ])

        max_count = self._extract_proxy_count(top_leak)
        leaked_descriptor = self._extract_leaked_descriptor(leak_text)

        system_kills = [
            c for c in crashes
            if isinstance(c, dict)
            and c.get("type") == "SYSTEM_KILL"
            and "Too many Binders sent to SYSTEM" in " ".join([
                str(c.get("exception_info", "")),
                str(c.get("top_method", "")),
                str(c.get("trigger", "")),
            ])
        ]

        if not system_kills:
            return rca_docs

        phone_kill = next(
            (c for c in system_kills if c.get("process") == "com.android.phone"),
            None
        )
        victim = phone_kill or system_kills[0]

        process = victim.get("process", "Unknown")
        time = victim.get("time") or top_leak.get("time") or "Unknown"
        trigger = victim.get("trigger", "")
        kill_reason = "Too many Binders sent to SYSTEM"

        wtf_events = [
            c for c in crashes
            if isinstance(c, dict)
            and (
                c.get("type") in ("SYSTEM_WTF", "SYSTEM_WTF_SUMMARY")
                or "am_wtf" in str(c.get("trigger", ""))
                or "am_wtf" in str(c.get("trigger_sample", ""))
            )
        ]

        wtf_count = 0
        for w in wtf_events:
            wtf_count += self._safe_int(w.get("count"), 0)

        if leaked_descriptor == "android.content.IIntentReceiver":
            root_cause = "IIntentReceiver Binder proxy leak"
            developer_action = "동적 BroadcastReceiver register 후 unregister 누락 여부를 점검해야 함"
        else:
            root_cause = "Binder proxy object leak"
            developer_action = "누수된 Binder interface의 acquire/release 또는 register/unregister 생명주기 점검 필요"

        metadata = {
            "source_file": os.path.basename(self.input_file),
            "log_type": "RCA_Event",
            "rca_type": "BINDER_PROXY_LEAK_RCA",
            "time": time,
            "process": process,
            "kill_event": "am_kill",
            "kill_reason": kill_reason,
            "leaked_descriptor": leaked_descriptor,
            "max_proxy_count": max_count,
            "am_wtf_count_observed": wtf_count,
            "root_cause": root_cause,
            "developer_action": developer_action,
            "trigger": trigger,
        }

        document = (
            f"[RCA: BINDER_PROXY_LEAK] {process} 프로세스가 am_kill로 강제 종료됨. "
            f"강제 종료 사유는 '{kill_reason}'. "
            f"동시간대 Binder Proxy Histogram에서 {leaked_descriptor} 객체가 최대 {max_count}개까지 누수됨. "
            f"am_wtf 이상 징후도 함께 관찰되며, 이는 Binder proxy leak으로 인한 시스템 리소스 고갈 정황임. "
            f"근본 원인은 일반 앱 크래시나 Native Crash가 아니라 {root_cause}. "
            f"개발 조치: {developer_action}."
        )

        rca_docs.append({
            "document": document,
            "metadata": metadata
        })

        return rca_docs

    def build_payload(self, output_filename=None):
        if not os.path.exists(self.input_file):
            print(f"❌ 파일을 찾을 수 없습니다: {self.input_file}")
            return

        if output_filename is None:
            input_basename = os.path.basename(self.input_file)
            name_without_ext = os.path.splitext(input_basename)[0]
            output_filename = f"{name_without_ext}_rag_payload.json"

        with open(self.input_file, 'r', encoding='utf-8') as f:
            report_data = json.load(f)

        rag_payload = []

        def add_to_payload(item, type_name):
            doc = self._build_markdown_doc(item, type_name)
            meta = self._extract_metadata(item, type_name)
            rag_payload.append({"document": doc, "metadata": meta})

        def add_clean_state(log_type, clean_message):
            clean_meta = {
                "log_type": log_type,
                "status": "CLEAN",
                "source_file": os.path.basename(self.input_file)
            }
            clean_doc = f"### [Type: {log_type}]\nNO_EVENT_DETECTED: {clean_message}"
            rag_payload.append({"document": clean_doc, "metadata": clean_meta})

        if "radio_power" in report_data:
            # 상태 변경 로직은 가벼우므로 역순 없이 처리
            for rp in report_data["radio_power"]:
                add_to_payload(rp, "Radio_Power_Event")

        # ==========================================
        # 🚨 [수정] Call Session 최신 로그 우선 처리
        # ==========================================
        if "call_sessions" in report_data and report_data["call_sessions"]:
            # 최근에 발생한 Call (에러 등) 10개만 추출
            recent_calls = report_data["call_sessions"][::-1][:10]
            for session in recent_calls:
                add_to_payload(session, "Call_Session")

        # ==========================================
        # 🚨 [수정] OOS Event 최신 로그 우선 처리
        # ==========================================
        if "oos_events" in report_data and report_data["oos_events"]:
            recent_oos = report_data["oos_events"][::-1][:5]
            for oos in recent_oos:
                add_to_payload(oos, "OOS_Event")

        # 3. ANR 방어
        if "anr_context" in report_data and report_data["anr_context"]:
            anr_data = report_data["anr_context"]
            if isinstance(anr_data, dict):
                anr_data = [anr_data]
            for anr_item in anr_data:
                add_to_payload(anr_item, "ANR_Context")

        # 4. Crash 방어 로직 적용
        if "crash_context" in report_data:
            crashes = report_data["crash_context"]
            if not crashes:
                add_clean_state("Crash_Event", "분석 구간 내 치명적인 Crash 이력이 발견되지 않았습니다.")
            else:
                # 💡 [신규 추가] am_wtf와 일반 크래시 분리
                wtfs = [c for c in crashes if c.get("type") == "SYSTEM_WTF"]
                others = [c for c in crashes if c.get("type") != "SYSTEM_WTF"]

                # 일반 크래시 및 am_kill은 그대로 개별 적재
                for crash in others:
                    add_to_payload(crash, "Crash_Event")

                # 💡 [신규 추가] am_wtf 대량 발생(Flood) 방어 로직: 프로세스별로 1개로 압축
                if wtfs:
                    wtf_summary = {}
                    for w in wtfs:
                        proc = w.get("process", "Unknown")
                        ts = w.get("time", "Unknown")
                        if proc not in wtf_summary:
                            wtf_summary[proc] = {
                                "count": 0,
                                "first": ts,
                                "last": ts,
                                "raw_sample": w.get("trigger", "")
                            }
                        wtf_summary[proc]["count"] += 1
                        if ts != "Unknown":
                            wtf_summary[proc]["last"] = ts

                    # 압축된 요약본만 RAG 페이로드에 단일 문서로 추가
                    for proc, data in wtf_summary.items():
                        summary_doc = {
                            "time": data["last"],  # 기준 시간은 가장 최근 시간
                            "type": "SYSTEM_WTF_SUMMARY",
                            "process": proc,
                            "exception_info": f"am_wtf 이상 징후 대량 발생: 총 {data['count']}회 반복됨 (최초: {data['first']} ~ 최후: {data['last']})",
                            "trigger_sample": data["raw_sample"]
                        }
                        # RAG DB에는 단 1건으로 들어감
                        add_to_payload(summary_doc, "Crash_Event")
        else:
            add_clean_state("Crash_Event", "분석 구간 내 치명적인 Crash 이력이 발견되지 않았습니다.")

        if "native_crash_context" in report_data and report_data["native_crash_context"]:
            for native_crash in report_data["native_crash_context"]:
                stack_str = "\n".join([f"#{c['frame_level']} {c['library']} ({c['function']})" for c in native_crash.get('callstack', [])])
                native_crash['raw_stack'] = stack_str
                add_to_payload(native_crash, "Native_Crash_Event")

        # 배터리 통계
        if "battery_stats" in report_data:
            add_to_payload(report_data["battery_stats"], "Battery_Drain_Report")

        if "boot_stats" in report_data:
            for boot_stat in report_data["boot_stats"]:
                add_to_payload(boot_stat, "Boot_Stat")

        if "signal_level_history" in report_data:
            for sig in report_data["signal_level_history"]:
                add_to_payload(sig, "Signal_Level")

        if "network_timeseries" in report_data:
            net_data = report_data["network_timeseries"]
            timeline = net_data.get("sorted_timeline", {})

            for ts, details in timeline.items():
                for stat in details.get("net_stats", []):
                    stat_item = {
                        "time": ts,
                        "log_type": "Network_Timeline_Stat",
                        "netId": stat.get("netId"),
                        "transport": stat.get("transport"),
                        "dns_avg": stat.get("dns_avg"),
                        "dns_err_rate": stat.get("dns_err_rate"),
                        "tcp_avg_loss": stat.get("tcp_avg_loss")
                    }
                    doc = f"Network Stat at {ts}: netId={stat.get('netId')}, DNS Avg={stat.get('dns_avg')}ms"
                    rag_payload.append({"document": doc, "metadata": stat_item})

            for dns_issue in net_data.get("dns_issues", []):
                dns_issue["log_type"] = "Network_DNS_Issue"
                doc = (
                    f"DNS Blocked Event: Package {dns_issue['package']} (UID: {dns_issue['uid']}) "
                    f"was blocked. Effective Policy: {dns_issue.get('effective_policy', 'Unknown')}. "
                    f"Time: {dns_issue['time']}"
                )
                rag_payload.append({"document": doc, "metadata": dns_issue})

                if net_data.get("sorted_timeline"):
                    summary = {"timeline_count": len(net_data["sorted_timeline"])}
                    add_to_payload(summary, "Network_Timeline_Summary")

        if "data_usage_stats" in report_data:
            for usage in report_data["data_usage_stats"]:
                if usage.get("total_mb", 0) < 0.1: continue

                meta = {
                    "source_file": os.path.basename(self.input_file),
                    "log_type": "Data_Usage",
                    "time": usage.get("time", "시간 미상"),
                    "app_name": usage.get("app_name", "Unknown"),
                    "rat": usage.get("rat", "Unknown"),
                    "total_mb": usage.get("total_mb", 0.0),
                    "rx_mb": usage.get("rx_mb", 0.0),
                    "tx_mb": usage.get("tx_mb", 0.0)
                }
                text_content = f"[{meta['time']}] 데이터 사용량 기록: {meta['app_name']} 앱이 {meta['rat']} 망에서 총 {meta['total_mb']} MB의 셀룰러 데이터를 사용했습니다. (다운로드: {meta['rx_mb']} MB, 업로드: {meta['tx_mb']} MB)"
                rag_payload.append({"document": text_content, "metadata": meta})

        # ==========================================
        # 🚨 [수정] DNS 쿼리 최신 로그 우선 처리
        # ==========================================
        if "dns_queries" in report_data:
            recent_dns = report_data["dns_queries"][::-1][:15]
            for dns in recent_dns:
                meta = {
                    "source_file": os.path.basename(self.input_file),
                    "log_type": "DNS_Query",
                    "time": dns.get("time", ""),
                    "uid": dns.get("uid", ""),
                    "app_name": dns.get("app_name", "Unknown"),
                    "return_code": dns.get("return_code", "UNKNOWN"),
                    "raw_info": dns.get("raw_info", "")
                }
                text_content = f"DNS 요청 기록: {meta['time']}에 {meta['app_name']} 앱(UID: {meta['uid']})이 DNS 요청을 수행했습니다. 결과 코드(return_code)는 {meta['return_code']} 입니다. (상세정보: {meta['raw_info']})"
                rag_payload.append({"document": text_content, "metadata": meta})

        # ==========================================
        # 🚨 [수정] VoLTE/IMS SIP 메시지 최신 로그 우선 처리
        # ==========================================
        if "ims_sip_data" in report_data:
            recent_sip = report_data["ims_sip_data"][::-1][:10]
            for sip in recent_sip:
                meta = self._extract_metadata(sip, "IMS_SIP_Message")
                if "raw_log" in sip:
                    meta["raw_logs"] = json.dumps([sip["raw_log"]], ensure_ascii=False)
                text_content = sip.get("document", self._build_markdown_doc(sip, "IMS_SIP_Message"))
                rag_payload.append({"document": text_content, "metadata": meta})

        battery_thermal = report_data.get("battery_thermal_stats", {})
        if "thermal_stats" in battery_thermal:
            for thermal in battery_thermal["thermal_stats"]:
                meta = {
                    "source_file": os.path.basename(self.input_file),
                    "log_type": "Thermal_Stat",
                    "sensor": thermal.get("sensor", ""),
                    "temperature": thermal.get("temperature", 0.0)
                }
                text_content = f"기기 온도 기록: {meta['sensor']} 센서의 온도가 {meta['temperature']}도로 측정되었습니다."
                rag_payload.append({"document": text_content, "metadata": meta})

        if "wakelock_stats" in battery_thermal:
            for wl in battery_thermal["wakelock_stats"]:
                meta = {
                    "source_file": os.path.basename(self.input_file),
                    "log_type": "Wakelock_Stat",
                    "app_name": wl.get("app_name", "Unknown"),
                    "duration": wl.get("duration", ""),
                    "times": wl.get("times", 0)
                }
                text_content = f"Wakelock(배터리 점유) 기록: {meta['app_name']} 앱이 단말기가 잠들지 못하도록 {meta['times']}회 깨웠으며, 총 {meta['duration']} 동안 배터리를 강제 소모시켰습니다."
                rag_payload.append({"document": text_content, "metadata": meta})

        if "cpu_usage_stats" in report_data:
            for cpu in report_data["cpu_usage_stats"]:
                proc = cpu.get("process", "Unknown").lstrip("/")
                pct = float(cpu.get("cpu_percent", 0.0))
                cpu_payload = {
                    "document": f"[CPU 점유율] 프로세스명: {proc}, 점유율: {pct}%",
                    "metadata": {
                        "log_type": "Cpu_Usage_Stat",
                        "process": proc,
                        "cpu_percent": pct
                    }
                }
                rag_payload.append(cpu_payload)

        if "nitz_history" in report_data:
            for nitz in report_data["nitz_history"]:
                meta = {
                    "log_type": "Nitz_Time_Event",
                    "time": nitz.get("log_time", ""),
                    "timezone": nitz.get("timezone", ""),
                    "raw_info": nitz.get("nitz_raw", "")
                }
                text_content = f"NITZ Time Update: Time: {meta['time']}, Timezone: {meta['timezone']} (RAW: {meta['raw_info']})"
                rag_payload.append({"document": text_content, "metadata": meta})

        # ==========================================
        # 🚨 [수정] Binder Warning 최신 로그 우선 처리
        # ==========================================
        if "binder_warnings" in report_data:
            binder_warnings = report_data["binder_warnings"] or []

            # 1. 바인더 프록시 누수/히스토그램은 무조건 적재
            leak_warnings = [
                bw for bw in binder_warnings
                if isinstance(bw, dict)
                and bw.get("type") in (
                    "BINDER_PROXY_HISTOGRAM",
                    "BINDER_PROXY_LEAK",
                    "BINDER_PROXY_LEAK_SUMMARY"
                )
            ]

            for bw in leak_warnings:
                max_count = self._extract_proxy_count(bw)
                desc = bw.get("desc") or bw.get("raw") or bw.get("raw_info") or ""
                leaked_descriptor = self._extract_leaked_descriptor(desc)

                meta = {
                    "source_file": os.path.basename(self.input_file),
                    "log_type": "Binder_Warning",
                    "time": bw.get("time", "Unknown"),
                    "type": "BINDER_PROXY_LEAK_SUMMARY",
                    "leaked_descriptor": leaked_descriptor,
                    "max_proxy_count": max_count,
                    "raw_info": desc,
                }

                text_content = (
                    f"심각한 바인더 프록시 객체 누수 감지. "
                    f"누수 객체: {leaked_descriptor}, 최대 누수 개수: {max_count}개. "
                    f"상세: {desc}"
                )

                rag_payload.append({
                    "document": text_content,
                    "metadata": meta
                })

            # 2. 일반 바인더 경고는 최신 10개만 적재
            normal_warnings = [
                bw for bw in binder_warnings
                if isinstance(bw, dict)
                and bw.get("type") not in (
                    "BINDER_PROXY_HISTOGRAM",
                    "BINDER_PROXY_LEAK",
                    "BINDER_PROXY_LEAK_SUMMARY"
                )
            ]

            for bw in normal_warnings[::-1][:10]:
                meta = {
                    "source_file": os.path.basename(self.input_file),
                    "log_type": "Binder_Warning",
                    "time": bw.get("time", ""),
                    "type": bw.get("type", ""),
                    "desc": bw.get("desc", ""),
                    "raw_info": bw.get("raw", bw.get("raw_info", "")),
                }

                text_content = (
                    f"[바인더 통신 이벤트] 시간: {meta['time']}, "
                    f"유형: {meta['type']}, 상세: {meta['desc']}"
                )

                rag_payload.append({
                    "document": text_content,
                    "metadata": meta
                })

            # 3. RCA Layer 추가
            rag_payload.extend(self._build_binder_leak_rca_docs(report_data))

        if "binder_context_summary" in report_data:
            ctx = report_data.get("binder_context_summary") or {}
            signals = ctx.get("signals", {})
            checklist = ctx.get("checklist", [])
            if signals or checklist:
                meta = {
                    "source_file": os.path.basename(self.input_file),
                    "log_type": "Binder_Context",
                    "signals": json.dumps(signals, ensure_ascii=False),
                    "signal_keys": ",".join(sorted(signals.keys())) if isinstance(signals, dict) else "",
                }
                text_content = (
                    f"[바인더 추가 확인 문맥] 감지된 주변 신호: {signals}. "
                    f"추가 확인 항목: {' / '.join(checklist)}"
                )
                rag_payload.append({"document": text_content, "metadata": meta})

        # ==========================================
        # 🚨 [수정] RILJ 최신 이슈 우선 추출 (핵심)
        # ==========================================
        if "rilj_transactions" in report_data:
            rilj_data = report_data["rilj_transactions"]

            # 1. 타임아웃 (응답 없음) - 가장 최근 5개만 추출
            recent_timeouts = rilj_data.get("timeouts", [])[::-1][:5]
            for t in recent_timeouts:
                meta = {"log_type": "RILJ_Transaction", "status": "TIMEOUT", "command": t["command"]}
                doc = f"[모뎀 응답 먹통(TIMEOUT)] 시간: {t['time']}, 명령어: {t['command']} 에 대해 모뎀이 응답하지 않았습니다."
                rag_payload.append({"document": doc, "metadata": meta})

            # 2. 에러 및 지연 응답 - 에러/지연 필터링 후 가장 최근 5개만 추출
            bad_responses = [c for c in rilj_data.get("completed", []) if c.get("is_error") or c.get("latency_ms", 0) > 500]
            recent_bad = bad_responses[::-1][:5]
            for c in recent_bad:
                status = "ERROR" if c.get("is_error") else "SLOW"
                meta = {
                    "log_type": "RILJ_Transaction",
                    "status": status,
                    "command": c["command"],
                    "latency_ms": c["latency_ms"]
                }
                doc = f"[모뎀 응답 이상({status})] 시간: {c['start_time']}, 명령어: {c['command']}, 지연시간: {c['latency_ms']}ms, 에러내용: {c['error_msg']}"
                rag_payload.append({"document": doc, "metadata": meta})

        # ==========================================
        # 🚨 [신규] System Property 최우선 적재
        # ==========================================
        if "system_properties" in report_data and report_data["system_properties"]:
            props = report_data["system_properties"]

            # 검색 및 메타데이터 필터링용 핵심 값 추출
            meta = {
                "source_file": os.path.basename(self.input_file),
                "log_type": "Device_Property_State",
                "airplane_mode": props.get("persist.radio.airplane_mode_on", "Unknown"),
                "radio_state": props.get("ril.radiostate", "Unknown")
            }

            # LLM이 읽을 본문(Document) 텍스트 조립
            doc_lines = ["### [Type: Device_Property_State]"]
            for k, v in props.items():
                doc_lines.append(f"- {k}: {v}")

            rag_payload.append({
                "document": "\n".join(doc_lines),
                "metadata": meta
            })

        base_dir = os.path.dirname(os.path.abspath(__file__))
        payload_dir = os.path.join(base_dir, "payloads")
        os.makedirs(payload_dir, exist_ok=True)
        final_output_path = os.path.join(payload_dir, os.path.basename(output_filename))

        with open(final_output_path, 'w', encoding='utf-8') as f:
            json.dump(rag_payload, f, indent=4, ensure_ascii=False)

