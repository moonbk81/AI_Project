import streamlit as st
import pandas as pd
import plotly.express as px
import json
import re

def _normalize_anr_list(anr_data_list):
    if isinstance(anr_data_list, dict) and anr_data_list:
        return [anr_data_list]
    if isinstance(anr_data_list, list):
        return anr_data_list
    return []

def _split_crash_context(original_crashes):
    crash_data = [
        c for c in original_crashes
        if isinstance(c, dict) and c.get("type") not in ("SYSTEM_KILL", "SYSTEM_WTF")
    ]
    return crash_data


def _split_system_kill_wtf_events(binder_warnings):
    system_kills = [b for b in binder_warnings if isinstance(b, dict) and b.get("type") == "SYSTEM_KILL"]
    system_wtfs = [b for b in binder_warnings if isinstance(b, dict) and b.get("type") == "SYSTEM_WTF"]
    return system_kills, system_wtfs

def _render_system_kills(system_kills):
    if not system_kills:
        return

    st.error(f"**🚨 시스템 강제 종료 (am_kill) {len(system_kills)}건 감지!** 프로세스가 시스템 서버에 의해 강제로 죽었습니다.")
    kill_rows = []
    for k in system_kills:
        kill_rows.append({
            "발생 시간 (Time)": k.get("time", "Unknown"),
            "종료된 프로세스 (Target)": k.get("process", "Unknown"),
            "강제 종료 사유 (Reason)": k.get("desc", k.get("top_method", "Unknown")),
            "트리거 원문 (Raw)": k.get("raw", k.get("trigger", ""))
        })
    df_kill = pd.DataFrame(kill_rows)
    st.dataframe(df_kill, use_container_width=True, hide_index=True)

def _render_system_wtfs(system_wtfs):
    if not system_wtfs:
        return

    st.warning(f"**⚠️ 시스템 이상 징후 (am_wtf) {len(system_wtfs)}건 감지!** (What a Terrible Failure)")

    wtf_summary = {}
    for w in system_wtfs:
        proc = w.get("process", "Unknown")
        ts = w.get("time", "Unknown")
        if proc not in wtf_summary:
            wtf_summary[proc] = {"count": 0, "first": ts, "last": ts}
        wtf_summary[proc]["count"] += 1
        if ts != "Unknown":
            wtf_summary[proc]["last"] = ts

    summary_rows = []
    for proc, data in wtf_summary.items():
        summary_rows.append({
            "대상 프로세스 (Target)": proc,
            "발생 횟수 (Count)": f"{data['count']}회",
            "최초 발생 (First Seen)": data["first"],
            "최근 발생 (Last Seen)": data["last"]
        })

    df_wtf_summary = pd.DataFrame(summary_rows)
    st.dataframe(df_wtf_summary, use_container_width=True, hide_index=True)

    with st.expander(f"🔍 최근 am_wtf 상세 로그 보기 (최신 20건 / 총 {len(system_wtfs)}건)"):
        wtf_rows = []
        for w in system_wtfs[-20:]:
            wtf_rows.append({
                "발생 시간 (Time)": w.get("time", "Unknown"),
                "대상 프로세스 (Target)": w.get("process", "Unknown"),
                "트리거 원문 (Raw)": w.get("raw", w.get("trigger", ""))
            })

        df_wtf_recent = pd.DataFrame(wtf_rows)
        st.dataframe(df_wtf_recent, use_container_width=True, hide_index=True)

def _render_binder_events(report_data, binder_warnings):
    if not binder_warnings:
        return

    binder_event_types = {
        "THREAD_EXHAUSTION", "TRANSACTION_DELAY", "BINDER_DELAY",
        "BINDER_TRANSACTION_FAILURE", "BINDER_BUFFER_ERROR", "REPEATED_BINDER_DELAY"
    }
    binder_event_rows = [
        b for b in binder_warnings
        if isinstance(b, dict) and b.get("type") in binder_event_types
    ]

    st.warning(
        f"Warning: {len(binder_event_rows)} Binder events (Delay/Failure/Exhaustion) detected. "
        "Examine carefully in correlation with ANR/Watchdog/Service restart instances."
    )
    with st.expander("Binder Event Details"):
        if binder_event_rows:
            binder_df = pd.DataFrame(binder_event_rows)[['time', 'type', 'desc']]
            max_display_rows = 300
            if len(binder_df) > max_display_rows:
                st.caption(f"Displaying most recent {max_display_rows} entries. Total: {len(binder_df)}")
                binder_df = binder_df.tail(max_display_rows)
            st.dataframe(binder_df, width="stretch")
        else:
            st.info("No Binder event details to display.")

    binder_context_summary = report_data.get("binder_context_summary", {})
    if binder_context_summary:
        with st.expander("Additional Binder Context Summary", expanded=False):
            signals = binder_context_summary.get("signals", {})
            checklist = binder_context_summary.get("checklist", [])
            if signals:
                signal_df = pd.DataFrame([
                    {"Context": k, "Matched lines": v} for k, v in signals.items()
                ])
                st.dataframe(signal_df, width="stretch", hide_index=True)
            if checklist:
                st.markdown("**Verification Checklist:**")
                for item in checklist:
                    st.markdown(f"- {item}")

def _render_native_crashes(native_crash_data):
    if not native_crash_data:
        return

    st.error(f"Critical: {len(native_crash_data)} Native C/C++ crash(es) detected.")
    for n_crash in native_crash_data:
        ts = n_crash.get('timestamp', 'Time Unknown')
        process = n_crash.get('process', 'Unknown')
        signal = n_crash.get('signal', 'Unknown')

        with st.expander(f"[{ts}] {process} - NATIVE CRASH (Signal: {signal})"):
            st.markdown(f"**Abort Message:** `{n_crash.get('abort_message', 'none')}`")

            callstack = n_crash.get('callstack', [])
            if callstack:
                st.markdown("**Native Callstack:**")
                stack_df = pd.DataFrame(callstack)
                st.dataframe(stack_df, hide_index=True, width="stretch")

            if 'cross_context_logs' in n_crash and n_crash['cross_context_logs']:
                st.markdown("**Surrounding Context Log:**")
                st.code("\n".join(n_crash['cross_context_logs']), language='log')

def _render_anr_summary_metrics(analysis_summary):
    if not analysis_summary:
        return

    st.markdown("**ANR Analysis Summary:**")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Main Stack", "Present" if analysis_summary.get('has_main_stack') else "None")
    c2.metric("Lock Contention", "Detected" if analysis_summary.get('has_lock_contention') else "None")
    c3.metric("Binder Wait", "Detected" if analysis_summary.get('has_active_binder') else "None")
    c4.metric("Pre-Logcat", "Present" if analysis_summary.get('has_pre_anr_logcat') else "None")

    c5, c6, c7 = st.columns(3)
    c5.metric("CPU Clue", "Present" if analysis_summary.get('has_cpu_hint') else "None")
    c6.metric("System Server Clue", "Present" if analysis_summary.get('has_system_server_hint') else "None")
    c7.metric("I/O Clue", "Present" if analysis_summary.get('has_io_hint') else "None")

def _render_anr_pre_logcat(anr_data):
    pre_anr_logs = anr_data.get('pre_anr_logcat', [])
    if not pre_anr_logs:
        return

    with st.expander("View Pre-ANR Logcat Context", expanded=False):
        st.caption("Logs immediately preceding the ANR detection.")
        st.code("\n".join(pre_anr_logs[-120:]), language='log')

def _render_anr_context_analysis(anr_data):
    context_analysis = anr_data.get('context_analysis', {})
    if not context_analysis:
        return

    cpu_logs = context_analysis.get('cpu_logs', [])
    system_server_logs = context_analysis.get('system_server_logs', [])
    io_logs = context_analysis.get('io_logs', [])

    if not (cpu_logs or system_server_logs or io_logs):
        return

    st.markdown("**Auxiliary Context Analysis:**")
    tab_cpu, tab_system, tab_io = st.tabs(["CPU", "System Server", "I/O"])

    with tab_cpu:
        if cpu_logs:
            st.caption("CPU usage/load related logs.")
            st.code("\n".join(cpu_logs[-80:]), language='log')
        else:
            st.info("No CPU related clue logs found.")

    with tab_system:
        if system_server_logs:
            st.caption("System server logs (ActivityManager, WindowManager, Watchdog, etc).")
            st.code("\n".join(system_server_logs[-80:]), language='log')
        else:
            st.info("No system server clue logs found.")

    with tab_io:
        if io_logs:
            st.caption("I/O delay/block suspected logs.")
            st.code("\n".join(io_logs[-80:]), language='log')
        else:
            st.info("No I/O clue logs found.")

def _render_anr_lock_chain(anr_data):
    lock_chain = anr_data.get('lock_chain', {})
    if not lock_chain or not lock_chain.get('blocker_thread'):
        return

    st.markdown("**Lock Contention / Deadlock Detected:**")
    st.warning(
        f"Main thread is waiting for lock (`{lock_chain['lock_address']}`). "
        f"(Occupying Thread TID: {lock_chain['blocker_thread']})"
    )
    if lock_chain.get('blocker_stack'):
        st.markdown(f"**Occupying Thread (TID: {lock_chain['blocker_thread']}) Callstack:**")
        st.code("\n".join(lock_chain['blocker_stack']), language='java')

def _render_anr_binder_transactions(anr_data):
    binder_txs = anr_data.get('active_binder_transactions', [])
    if not binder_txs:
        return

    st.markdown("**Pending Binder Transactions (Outgoing):**")
    binder_rows = []
    for tx in binder_txs:
        binder_rows.append({
            "from_pid": tx.get('from_pid', '-'),
            "from_tid": tx.get('from_tid', '-'),
            "to_pid": tx.get('to_pid', '-'),
            "to_tid": tx.get('to_tid', '-'),
            "code": tx.get('code', '-'),
            "raw": tx.get('raw', '')
        })
    st.dataframe(pd.DataFrame(binder_rows), width="stretch")

def _render_anr_main_stack(anr_data):
    main_stack = anr_data.get('main', {}).get('stack', [])
    if not main_stack:
        return

    st.markdown("**Main Thread Callstack:**")
    with st.expander("View Full Main Thread Stack", expanded=True):
        st.code("\n".join(main_stack), language='java')

def _render_anr_events(anr_data_list):
    if not anr_data_list:
        return

    st.error(f"Critical: {len(anr_data_list)} Application Not Responding (ANR) events detected.")

    for anr_data in anr_data_list:
        anr_time = anr_data.get('time', 'Unknown Time')
        anr_process = anr_data.get('process', 'Unknown Process')
        anr_reason = anr_data.get('reason', 'Unknown Reason')
        anr_pid = anr_data.get('process_info', {}).get('pid', 'Unknown')

        with st.expander(f"[{anr_time}] ANR - {anr_process} (PID: {anr_pid})"):
            st.markdown(f"**ANR Reason:** `{anr_reason}`")
            _render_anr_summary_metrics(anr_data.get('analysis_summary', {}))
            _render_anr_pre_logcat(anr_data)
            _render_anr_context_analysis(anr_data)
            _render_anr_lock_chain(anr_data)
            _render_anr_binder_transactions(anr_data)
            _render_anr_main_stack(anr_data)

def _render_java_crashes(crash_data):
    if not crash_data:
        return

    st.error(f"Critical: {len(crash_data)} System Crash/FATAL exception(s) detected.")

    for crash in crash_data:
        ts = crash.get('timestamp', 'Time Unknown')
        process = crash.get('process', 'Unknown Process')
        crash_type = crash.get('crash_type', 'FATAL EXCEPTION')

        with st.expander(f"[{ts}] {process} - {crash_type}"):
            raw_logs_str = str(crash.get('cross_context_logs', crash.get('raw_line', ''))).lower()
            if "transactiontoolargeexception" in raw_logs_str:
                st.error("Diagnostic Cause: TransactionTooLargeException. Buffer overflow triggered by intent data exceeding 1MB limit.")
            if 'cross_context_logs' in crash and crash['cross_context_logs']:
                st.markdown("**Surrounding Context Log:**")
                st.code("\n".join(crash['cross_context_logs']), language='log')
            elif 'raw_line' in crash:
                st.markdown("**Raw Crash Log:**")
                st.code(crash['raw_line'], language='log')

def render_crash_analyzer(report_data):
    st.subheader("System Crash & FATAL Error Analysis")

    original_crashes = report_data.get("crash_context", [])
    native_crash_data = report_data.get("native_crash_context", [])
    anr_data_list = _normalize_anr_list(report_data.get("anr_context", []))
    binder_warnings = report_data.get("binder_warnings", [])

    if not original_crashes and not anr_data_list and not native_crash_data and not binder_warnings:
        st.success("No system crashes, ANRs, FATAL exceptions, or Binder/System Kill events detected in the log.")
        return

    crash_data = _split_crash_context(original_crashes)
    system_kills, system_wtfs = _split_system_kill_wtf_events(binder_warnings)

    _render_system_kills(system_kills)
    _render_system_wtfs(system_wtfs)
    _render_binder_events(report_data, binder_warnings)
    _render_native_crashes(native_crash_data)
    _render_anr_events(anr_data_list)
    _render_java_crashes(crash_data)

def render_binder_proxy_leaks(binder_warnings):
    # (앞부분 json 파싱 및 타입 검사 방어 코드는 이전과 동일하게 유지)
    if isinstance(binder_warnings, str):
        try: binder_warnings = json.loads(binder_warnings)
        except: return
    if not isinstance(binder_warnings, list):
        binder_warnings = [binder_warnings]

    histograms = []
    for w in binder_warnings:
        if isinstance(w, str):
            try: w = json.loads(w)
            except: continue

        # 💡 변경점: 타입을 BINDER_PROXY_HISTOGRAM 으로 스캔
        if isinstance(w, dict) and w.get("type") in ("BINDER_PROXY_HISTOGRAM", "BINDER_PROXY_LEAK"):
            histograms.append(w)

    if not histograms:
        return

    st.markdown("### 📊 Binder Proxy Histogram Analysis")

    for idx, hist in enumerate(histograms):
        max_count = hist.get("max_count", 0)
        is_leak = max_count > 1000 # 💡 판단은 UI 단계에서 수행

        # 💡 임계치에 따른 동적 UI 렌더링
        if is_leak:
            st.error(f"**🚨 [발생 시간: {hist.get('time', 'Unknown')}] 시스템 리소스 누수(Leak) 임계치 초과!**\n\n최대 Proxy 객체 수가 {max_count}개로, 특정 인터페이스의 등록/해제 불균형(메모리 릭)이 의심되어 am_kill 위험이 높습니다.")
        else:
            st.info(f"**ℹ️ [발생 시간: {hist.get('time', 'Unknown')}] Binder Proxy 객체 상태** (최대 {max_count}개 기록됨 - 정상 범위)")

        # (이하 raw 데이터를 파싱해서 Plotly 바 차트로 그리는 로직은 이전과 완전히 동일하게 유지)
        raw_lines = hist.get("raw", "").split('\n')
        data = []
        for line in raw_lines:
            match = re.search(r'([a-zA-Z_][a-zA-Z0-9\.\$]+)\s*x\s*(\d+)', line)
            if match:
                full_class = match.group(1)
                short_class = full_class.split('.')[-1]
                count = int(match.group(2))
                data.append({"Class": short_class, "FullClass": full_class, "Count": count})

        if data:
            df = pd.DataFrame(data)
            df = df.sort_values(by="Count", ascending=True)

            fig = px.bar(
                df,
                x="Count",
                y="Class",
                orientation='h',
                text="Count",
                hover_data=["FullClass"],
                color="Count",
                color_continuous_scale="Reds",
                title="Top 10 Binder Proxy Descriptor Histogram"
            )

            fig.update_layout(
                xaxis_title="Proxy Object Count",
                yaxis_title="Target Interface",
                height=400,
                margin=dict(l=20, r=20, t=40, b=20)
            )
            fig.update_traces(textposition='outside')

            st.plotly_chart(fig, use_container_width=True)
