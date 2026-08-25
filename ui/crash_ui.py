"""Crash / ANR / Binder views.

Rendering only: the tables, groupings and thresholds come from
`core.charts.crash`.
"""

import plotly.express as px
import streamlit as st

from core.charts import build_binder_proxy_histograms, build_crash_overview

def _render_system_kills(system_kills):
    if system_kills.empty:
        return

    st.error(f"**시스템 강제 종료(am_kill) {len(system_kills)}건 감지**")
    st.dataframe(system_kills, width="stretch", hide_index=True)

def _render_system_wtfs(system_wtf):
    if not system_wtf.total:
        return

    st.warning(f"**시스템 이상 징후(am_wtf) {system_wtf.total}건 감지**")
    st.dataframe(system_wtf.by_process, width="stretch", hide_index=True)

    with st.expander(f"최근 am_wtf 상세 로그 (최신 {system_wtf.recent_count}건 / 총 {system_wtf.total}건)"):
        st.dataframe(system_wtf.recent, width="stretch", hide_index=True)

def _render_binder_events(binder):
    if binder.status == "none":
        return

    # 💡 BINDER_ONEWAY_SPAM만 따로 빼서 강력하게 경고 (UI 최상단 노출)
    if binder.spam:
        st.error(f"🚨 **Binder Oneway Spamming (버퍼 고갈 원인 프로세스 감지) {len(binder.spam)}건 감지!**")
        for spam in binder.spam:
            st.warning(spam.desc)
            with st.expander(f"[{spam.time}] 커널 로그 원문 확인"):
                st.code(spam.raw, language='log')
        st.markdown("---") # 시각적 분리선

    st.warning(f"Binder 지연/실패/스레드 부족 이벤트 {binder.event_count}건 감지")

    with st.expander("Binder 이벤트 상세"):
        if not binder.events.empty:
            if binder.truncated:
                st.caption(f"최근 {binder.display_cap}건만 표시합니다. 전체: {binder.event_count}건")
            st.dataframe(binder.events, width="stretch")
        else:
            st.info("표시할 Binder 이벤트가 없습니다.")

    if not binder.signals.empty or binder.checklist:
        with st.expander("Binder 관련 추가 요약", expanded=False):
            if not binder.signals.empty:
                st.dataframe(binder.signals, width="stretch", hide_index=True)
            if binder.checklist:
                st.markdown("**확인 항목**")
                for item in binder.checklist:
                    st.markdown(f"- {item}")

def _render_native_crashes(native_crashes):
    if not native_crashes:
        return

    st.error(f"Native C/C++ Crash {len(native_crashes)}건 감지")
    for crash in native_crashes:
        with st.expander(f"[{crash.time}] {crash.process} - Native Crash (signal: {crash.signal})"):
            st.markdown(f"**Abort message:** `{crash.abort_message}`")

            if not crash.callstack.empty:
                st.markdown("**Native callstack**")
                st.dataframe(crash.callstack, hide_index=True, width="stretch")

            if crash.cross_context_logs:
                st.markdown("**주변 로그**")
                st.code("\n".join(crash.cross_context_logs), language='log')

def _render_anr_summary_metrics(summary):
    if summary is None:
        return

    st.markdown("**ANR 요약**")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Main Stack", "있음" if summary.has_main_stack else "없음")
    c2.metric("Lock Contention", "감지" if summary.has_lock_contention else "없음")
    c3.metric("Binder Wait", "감지" if summary.has_active_binder else "없음")
    c4.metric("Pre-Logcat", "있음" if summary.has_pre_anr_logcat else "없음")

    c5, c6, c7 = st.columns(3)
    c5.metric("CPU 단서", "있음" if summary.has_cpu_hint else "없음")
    c6.metric("System Server 단서", "있음" if summary.has_system_server_hint else "없음")
    c7.metric("I/O 단서", "있음" if summary.has_io_hint else "없음")

def _render_anr_context_analysis(anr):
    if not anr.has_context_logs:
        return

    st.markdown("**보조 분석 정보**")
    tab_cpu, tab_system, tab_io = st.tabs(["CPU", "System Server", "I/O"])

    with tab_cpu:
        if anr.cpu_logs:
            st.caption("CPU 사용률/부하 관련 로그")
            st.code("\n".join(anr.cpu_logs), language='log')
        else:
            st.info("CPU 관련 단서 로그가 없습니다.")

    with tab_system:
        if anr.system_server_logs:
            st.caption("System server 관련 로그")
            st.code("\n".join(anr.system_server_logs), language='log')
        else:
            st.info("System server 관련 단서 로그가 없습니다.")

    with tab_io:
        if anr.io_logs:
            st.caption("I/O 지연 또는 block 의심 로그")
            st.code("\n".join(anr.io_logs), language='log')
        else:
            st.info("I/O 관련 단서 로그가 없습니다.")

def _render_anr_lock_chain(lock_chain):
    if lock_chain is None:
        return

    st.markdown("**Lock contention 감지**")
    st.warning(
        f"Main thread가 lock(`{lock_chain.lock_address}`) 대기 중입니다. "
        f"(점유 Thread TID: {lock_chain.blocker_thread})"
    )
    if lock_chain.blocker_stack:
        st.markdown(f"**점유 Thread(TID: {lock_chain.blocker_thread}) callstack**")
        st.code("\n".join(lock_chain.blocker_stack), language='java')

def _render_anr_events(anr_events):
    if not anr_events:
        return

    st.error(f"ANR 이벤트 {len(anr_events)}건 감지")

    for anr in anr_events:
        with st.expander(f"[{anr.time}] ANR - {anr.process} (PID: {anr.pid})"):
            st.markdown(f"**ANR 사유:** `{anr.reason}`")
            _render_anr_summary_metrics(anr.summary)

            if anr.pre_logcat:
                with st.expander("ANR 직전 Logcat", expanded=False):
                    st.caption("ANR 감지 직전 로그입니다.")
                    st.code("\n".join(anr.pre_logcat), language='log')

            _render_anr_context_analysis(anr)
            _render_anr_lock_chain(anr.lock_chain)

            if not anr.binder_transactions.empty:
                st.markdown("**대기 중인 Binder transaction**")
                st.dataframe(anr.binder_transactions, width="stretch")

            if anr.main_stack:
                st.markdown("**Main thread callstack**")
                with st.expander("Main thread 전체 stack", expanded=True):
                    st.code("\n".join(anr.main_stack), language='java')

def _render_java_crashes(java_crashes):
    if not java_crashes:
        return

    st.error(f"System Crash/FATAL Exception {len(java_crashes)}건 감지")

    for crash in java_crashes:
        with st.expander(f"[{crash.time}] {crash.process} - {crash.crash_type}"):
            if crash.exception_info:
                st.error(f"**Exception 정보:** {crash.exception_info}")

            if crash.top_method:
                st.warning(f"**주요 Method:** {crash.top_method}")

            if crash.pre_context:
                st.markdown("**Crash 직전 단서 로그**")
                st.code("\n".join(crash.pre_context), language='log')

            if crash.call_stack:
                st.markdown("**Call stack**")
                st.code("\n".join(crash.call_stack), language='log' if crash.is_kernel else 'java')

            if crash.suspects_transaction_too_large:
                st.error("TransactionTooLargeException 의심: Intent 데이터가 Binder buffer 한계를 초과했을 가능성이 있습니다.")

            if crash.cross_context_logs:
                st.markdown("**주변 로그**")
                st.code("\n".join(crash.cross_context_logs), language='log')
            elif crash.trigger is not None:
                st.markdown("**Crash trigger 원문**")
                st.code(crash.trigger, language='log')

def render_crash_analyzer(report_data):
    st.subheader("Crash / ANR / Binder 분석")

    overview = build_crash_overview(report_data)
    if overview.status == "clean":
        st.success("Crash, ANR, FATAL Exception, Binder/System Kill 이벤트가 감지되지 않았습니다.")
        return

    _render_system_kills(overview.system_kills)
    _render_system_wtfs(overview.system_wtf)
    _render_binder_events(overview.binder)
    _render_native_crashes(overview.native_crashes)
    _render_anr_events(overview.anr_events)
    _render_java_crashes(overview.java_crashes)

def render_binder_proxy_leaks(binder_warnings):
    histograms = build_binder_proxy_histograms(binder_warnings)
    if not histograms:
        return

    st.markdown("### Binder Proxy 현황")

    for hist in histograms:
        if hist.is_leak:
            st.error(f"**[발생 시간: {hist.time}] Binder Proxy 임계치 초과**\n\n최대 Proxy 객체 수가 {hist.max_count}개로, 특정 인터페이스의 등록/해제 불균형 가능성이 있습니다.")
        else:
            st.info(f"**[발생 시간: {hist.time}] Binder Proxy 객체 상태** (최대 {hist.max_count}개)")

        if hist.counts.empty:
            continue

        fig = px.bar(
            hist.counts,
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
