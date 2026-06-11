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

    st.error(f"**시스템 강제 종료(am_kill) {len(system_kills)}건 감지**")
    kill_rows = []
    for k in system_kills:
        kill_rows.append({
            "발생 시간": k.get("time", "Unknown"),
            "대상 프로세스": k.get("process", "Unknown"),
            "종료 사유": k.get("desc", k.get("top_method", "Unknown")),
            "원본 로그": k.get("raw", k.get("trigger", ""))
        })
    df_kill = pd.DataFrame(kill_rows)
    st.dataframe(df_kill, width="stretch", hide_index=True)

def _render_system_wtfs(system_wtfs):
    if not system_wtfs:
        return

    st.warning(f"**시스템 이상 징후(am_wtf) {len(system_wtfs)}건 감지**")

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
            "대상 프로세스": proc,
            "발생 횟수": f"{data['count']}회",
            "최초 발생": data["first"],
            "최근 발생": data["last"]
        })

    df_wtf_summary = pd.DataFrame(summary_rows)
    st.dataframe(df_wtf_summary, width="stretch", hide_index=True)

    with st.expander(f"최근 am_wtf 상세 로그 (최신 20건 / 총 {len(system_wtfs)}건)"):
        wtf_rows = []
        for w in system_wtfs[-20:]:
            wtf_rows.append({
                "발생 시간": w.get("time", "Unknown"),
                "대상 프로세스": w.get("process", "Unknown"),
                "원본 로그": w.get("raw", w.get("trigger", ""))
            })

        df_wtf_recent = pd.DataFrame(wtf_rows)
        st.dataframe(df_wtf_recent, width="stretch", hide_index=True)

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
        f"Binder 지연/실패/스레드 부족 이벤트 {len(binder_event_rows)}건 감지"
    )
    with st.expander("Binder 이벤트 상세"):
        if binder_event_rows:
            binder_df = pd.DataFrame(binder_event_rows)[['time', 'type', 'desc']]
            max_display_rows = 300
            if len(binder_df) > max_display_rows:
                st.caption(f"최근 {max_display_rows}건만 표시합니다. 전체: {len(binder_df)}건")
                binder_df = binder_df.tail(max_display_rows)
            st.dataframe(binder_df, width="stretch")
        else:
            st.info("표시할 Binder 이벤트가 없습니다.")

    binder_context_summary = report_data.get("binder_context_summary", {})
    if binder_context_summary:
        with st.expander("Binder 관련 추가 요약", expanded=False):
            signals = binder_context_summary.get("signals", {})
            checklist = binder_context_summary.get("checklist", [])
            if signals:
                signal_df = pd.DataFrame([
                    {"구분": k, "매칭 라인 수": v} for k, v in signals.items()
                ])
                st.dataframe(signal_df, width="stretch", hide_index=True)
            if checklist:
                st.markdown("**확인 항목**")
                for item in checklist:
                    st.markdown(f"- {item}")

def _render_native_crashes(native_crash_data):
    if not native_crash_data:
        return

    st.error(f"Native C/C++ Crash {len(native_crash_data)}건 감지")
    for n_crash in native_crash_data:
        ts = n_crash.get('timestamp', '시간 미상')
        process = n_crash.get('process', 'Unknown')
        signal = n_crash.get('signal', 'Unknown')

        with st.expander(f"[{ts}] {process} - Native Crash (signal: {signal})"):
            st.markdown(f"**Abort message:** `{n_crash.get('abort_message', 'none')}`")

            callstack = n_crash.get('callstack', [])
            if callstack:
                st.markdown("**Native callstack**")
                stack_df = pd.DataFrame(callstack)
                st.dataframe(stack_df, hide_index=True, width="stretch")

            if 'cross_context_logs' in n_crash and n_crash['cross_context_logs']:
                st.markdown("**주변 로그**")
                st.code("\n".join(n_crash['cross_context_logs']), language='log')

def _render_anr_summary_metrics(analysis_summary):
    if not analysis_summary:
        return

    st.markdown("**ANR 요약**")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Main Stack", "있음" if analysis_summary.get('has_main_stack') else "없음")
    c2.metric("Lock Contention", "감지" if analysis_summary.get('has_lock_contention') else "없음")
    c3.metric("Binder Wait", "감지" if analysis_summary.get('has_active_binder') else "없음")
    c4.metric("Pre-Logcat", "있음" if analysis_summary.get('has_pre_anr_logcat') else "없음")

    c5, c6, c7 = st.columns(3)
    c5.metric("CPU 단서", "있음" if analysis_summary.get('has_cpu_hint') else "없음")
    c6.metric("System Server 단서", "있음" if analysis_summary.get('has_system_server_hint') else "없음")
    c7.metric("I/O 단서", "있음" if analysis_summary.get('has_io_hint') else "없음")

def _render_anr_pre_logcat(anr_data):
    pre_anr_logs = anr_data.get('pre_anr_logcat', [])
    if not pre_anr_logs:
        return

    with st.expander("ANR 직전 Logcat", expanded=False):
        st.caption("ANR 감지 직전 로그입니다.")
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

    st.markdown("**보조 분석 정보**")
    tab_cpu, tab_system, tab_io = st.tabs(["CPU", "System Server", "I/O"])

    with tab_cpu:
        if cpu_logs:
            st.caption("CPU 사용률/부하 관련 로그")
            st.code("\n".join(cpu_logs[-80:]), language='log')
        else:
            st.info("CPU 관련 단서 로그가 없습니다.")

    with tab_system:
        if system_server_logs:
            st.caption("System server 관련 로그")
            st.code("\n".join(system_server_logs[-80:]), language='log')
        else:
            st.info("System server 관련 단서 로그가 없습니다.")

    with tab_io:
        if io_logs:
            st.caption("I/O 지연 또는 block 의심 로그")
            st.code("\n".join(io_logs[-80:]), language='log')
        else:
            st.info("I/O 관련 단서 로그가 없습니다.")

def _render_anr_lock_chain(anr_data):
    lock_chain = anr_data.get('lock_chain', {})
    if not lock_chain or not lock_chain.get('blocker_thread'):
        return

    st.markdown("**Lock contention 감지**")
    st.warning(
        f"Main thread가 lock(`{lock_chain['lock_address']}`) 대기 중입니다. "
        f"(점유 Thread TID: {lock_chain['blocker_thread']})"
    )
    if lock_chain.get('blocker_stack'):
        st.markdown(f"**점유 Thread(TID: {lock_chain['blocker_thread']}) callstack**")
        st.code("\n".join(lock_chain['blocker_stack']), language='java')

def _render_anr_binder_transactions(anr_data):
    binder_txs = anr_data.get('active_binder_transactions', [])
    if not binder_txs:
        return

    st.markdown("**대기 중인 Binder transaction**")
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

    st.markdown("**Main thread callstack**")
    with st.expander("Main thread 전체 stack", expanded=True):
        st.code("\n".join(main_stack), language='java')

def _render_anr_events(anr_data_list):
    if not anr_data_list:
        return

    st.error(f"ANR 이벤트 {len(anr_data_list)}건 감지")

    for anr_data in anr_data_list:
        anr_time = anr_data.get('time', '시간 미상')
        anr_process = anr_data.get('process', 'Unknown Process')
        anr_reason = anr_data.get('reason', 'Unknown Reason')
        anr_pid = anr_data.get('process_info', {}).get('pid', 'Unknown')

        with st.expander(f"[{anr_time}] ANR - {anr_process} (PID: {anr_pid})"):
            st.markdown(f"**ANR 사유:** `{anr_reason}`")
            _render_anr_summary_metrics(anr_data.get('analysis_summary', {}))
            _render_anr_pre_logcat(anr_data)
            _render_anr_context_analysis(anr_data)
            _render_anr_lock_chain(anr_data)
            _render_anr_binder_transactions(anr_data)
            _render_anr_main_stack(anr_data)

def _render_java_crashes(crash_data):
    if not crash_data:
        return

    st.error(f"System Crash/FATAL Exception {len(crash_data)}건 감지")

    for crash in crash_data:
        # 💡 시간 필드 매칭 강화 (timestamp, time 모두 지원)
        ts = crash.get('timestamp', crash.get('time', '시간 미상'))
        process = crash.get('process', 'Unknown Process')

        # 💡 커널 패닉일 경우 타이틀 변경
        is_kernel = crash.get('is_kernel', False)
        if is_kernel:
            crash_type = "KERNEL PANIC / MODEM CRASH"
        else:
            crash_type = crash.get('crash_type', crash.get('type', 'FATAL EXCEPTION'))

        with st.expander(f"[{ts}] {process} - {crash_type}"):
            # 1. Exception Info (패닉 사유)
            exception_info = crash.get('exception_info')
            if exception_info:
                st.error(f"**Exception 정보:** {exception_info}")

            # 2. Top Method
            top_method = crash.get('top_method')
            if top_method and top_method != "Unknown":
                st.warning(f"**주요 Method:** {top_method}")

            # 3. Pre-Crash Context (여기에 MNR 로그가 출력됩니다!)
            pre_context = crash.get('context', [])
            if pre_context:
                st.markdown("**Crash 직전 단서 로그**")
                st.code("\n".join(pre_context), language='log')

            # 4. Call Stack
            call_stack = crash.get('call_stack', [])
            if call_stack:
                st.markdown("**Call stack**")
                st.code("\n".join(call_stack), language='log' if is_kernel else 'java')

            # 기존 TransactionTooLarge 및 Cross Context 로직 유지
            raw_logs_str = str(crash.get('cross_context_logs', crash.get('trigger', ''))).lower()
            if "transactiontoolargeexception" in raw_logs_str:
                st.error("TransactionTooLargeException 의심: Intent 데이터가 Binder buffer 한계를 초과했을 가능성이 있습니다.")

            if 'cross_context_logs' in crash and crash['cross_context_logs']:
                st.markdown("**주변 로그**")
                st.code("\n".join(crash['cross_context_logs']), language='log')
            elif 'trigger' in crash:
                st.markdown("**Crash trigger 원문**")
                st.code(crash['trigger'], language='log')

def render_crash_analyzer(report_data):
    st.subheader("Crash / ANR / Binder 분석")

    original_crashes = report_data.get("crash_context", [])
    native_crash_data = report_data.get("native_crash_context", [])
    anr_data_list = _normalize_anr_list(report_data.get("anr_context", []))
    binder_warnings = report_data.get("binder_warnings", [])

    if not original_crashes and not anr_data_list and not native_crash_data and not binder_warnings:
        st.success("Crash, ANR, FATAL Exception, Binder/System Kill 이벤트가 감지되지 않았습니다.")
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

    st.markdown("### Binder Proxy 현황")

    for idx, hist in enumerate(histograms):
        max_count = hist.get("max_count", 0)
        is_leak = max_count > 1000 # 💡 판단은 UI 단계에서 수행

        # 💡 임계치에 따른 동적 UI 렌더링
        if is_leak:
            st.error(f"**[발생 시간: {hist.get('time', 'Unknown')}] Binder Proxy 임계치 초과**\n\n최대 Proxy 객체 수가 {max_count}개로, 특정 인터페이스의 등록/해제 불균형 가능성이 있습니다.")
        else:
            st.info(f"**[발생 시간: {hist.get('time', 'Unknown')}] Binder Proxy 객체 상태** (최대 {max_count}개)")

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
                title="Binder Proxy Descriptor Top 10"
            )

            fig.update_layout(
                xaxis_title="Proxy 객체 수",
                yaxis_title="대상 Interface",
                height=400,
                margin=dict(l=20, r=20, t=40, b=20)
            )
            fig.update_traces(textposition='outside')

            st.plotly_chart(fig, width="stretch")
