// 대시보드 — 세션 하나의 상태를 한 화면에.

import { api, baseName } from "../api.js";
import { reportCard } from "../report.js";
import {
  axis, barTrace, baseLayout, card, el, endLabels, fmt, frameTable, groupBy,
  lineTrace, section, sectionAnalysisQuestion, seriesColors, sequentialRamp, stepColor, table, tile, tileRow,
} from "../viz.js";

// An empty slot already says "N/A" in the value — repeating it in the note is noise.
const noteText = (value) => (value && value !== "N/A" ? value : "");

function kpiBand(kpi) {
  const dropped = kpi.call_drop_count > 0;
  const outOfService = kpi.oos_count > 0;
  const wrap = el("div");
  const ctx = kpi.device_context || {};
  const firstRow = tileRow([
    tile("모델", ctx.model_name || "N/A", "", ctx.build_id || ""),
    tile("Radio", ctx.radio || "N/A"),
    tile("Build Network", ctx.network || "N/A"),
  ]);
  firstRow.classList.add("kpi-row-3col");
  wrap.append(firstRow);

  // One row per SIM slot: the SIM's own properties, then the network it is on.
  for (const sim of ctx.sim_slots || []) {
    const net = (ctx.network_slots || []).find((n) => n.slot === sim.slot) || {};
    wrap.append(tileRow([
      tile(`SIM ${sim.slot}`, sim.state || "N/A", "", noteText(sim.carrier)),
      tile(`SIM ${sim.slot} MCC/MNC`, sim.mcc_mnc || "N/A"),
      tile(`SIM ${sim.slot} Network PLMN`, net.plmn || "N/A"),
      tile(`SIM ${sim.slot} Network Alpha`, net.network_name || "N/A"),
    ]));
  }

  wrap.append(tileRow([
    tile("통화 성공률", fmt.fixed(kpi.call_success_rate), "%",
         dropped ? `실패/드롭 ${kpi.call_drop_count}건` : "실패 없음", dropped ? "critical" : "good"),
    tile("OOS 이벤트", fmt.count(kpi.oos_count), "",
         outOfService ? "권외 전환 감지" : "서비스 유지", outOfService ? "critical" : "good"),
    tile("평균 신호 레벨", fmt.fixed(kpi.avg_signal_level), "", "0(최악) ~ 4(최상)"),
    tile("최다 사용 앱", kpi.top_app_name, "", fmt.mb(kpi.top_app_mb)),
  ]));
  return wrap;
}

const CARDS = [
  {
    chart: "service-state",
    title: "망 등록 상태 전이",
    sub: "Voice / Data 등록 상태가 바뀐 시점만",
    prompt: "Voice/Data 등록 상태가 바뀐 시점, cause, operator 변화를 보고 OOS나 재등록 반복의 원인을 짚어줘.",
    render(series, panel) {
      const colors = seriesColors();
      const traces = [...groupBy(series.points, (p) => `${p.slot} · ${p.conn_type}`)].map(
        ([name, points], index) =>
          lineTrace(name, points.map((p) => p.time_dt), points.map((p) => p.state),
                    colors[index % colors.length], {
            line: { width: 2, shape: "hv", color: colors[index % colors.length] },
            hovertemplate: "<b>%{y}</b><br>%{x}<extra>" + name + "</extra>",
          }),
      );
      panel.draw(traces, baseLayout({
        showlegend: traces.length > 1,
        hovermode: "x unified",
        margin: { l: 136, r: 96, t: 8, b: 44 },
        yaxis: axis({ type: "category", categoryorder: "array", categoryarray: series.state_order }),
        annotations: endLabels(traces),
      }), frameTable(series.points, ["time", "slot", "conn_type", "state", "event", "cause", "operator"]));
    },
  },
  {
    chart: "rf-timeline",
    title: "통화 구간과 RF 환경",
    sub: "RSRP 추이 위에 통화 구간과 SIP 오류를 겹쳐 표시",
    prompt: "통화 drop 구간 전후의 RSRP 변화와 SIP 오류를 함께 보고 RF 문제인지 IMS/SIP 문제인지 구분해줘.",
    render(series, panel) {
      const colors = seriesColors();
      const traces = [...groupBy(series.rsrp_points, (p) => p.rat || "Unknown")].map(
        ([rat, points], index) =>
          lineTrace(`RSRP ${rat}`, points.map((p) => p.time_dt),
                    points.map((p) => p.rsrp_dbm), colors[index % colors.length], {
            text: points.map((p) => p.hover_text.replace(/<br>/g, " · ")),
            hoverinfo: "text",
          }),
      );

      if (series.sip_errors.length) {
        traces.push({
          type: "scatter", mode: "markers", name: "SIP 오류",
          x: series.sip_errors.map((e) => e.time_dt),
          y: series.sip_errors.map(() => -135),
          marker: { symbol: "x", size: 11, color: "var-critical", line: { width: 2 } },
          text: series.sip_errors.map((e) => e.label),
          hovertemplate: "<b>%{text}</b><br>%{x}<extra>SIP 오류</extra>",
        });
        traces[traces.length - 1].marker.color = getComputedStyle(document.body).getPropertyValue("--status-critical").trim();
      }

      // Call windows are context behind the line, not a series of their own.
      const shapes = series.call_spans.map((span) => ({
        type: "rect", xref: "x", yref: "paper", layer: "below",
        x0: span.start_dt, x1: span.end_dt, y0: 0, y1: 1,
        fillcolor: span.is_drop ? "rgba(208,59,59,0.13)" : "rgba(12,163,12,0.10)",
        line: { width: 0 },
      }));

      panel.draw(traces, baseLayout({
        showlegend: traces.length > 1,
        hovermode: "closest",
        shapes,
        yaxis: axis({ title: { text: "RSRP (dBm)", font: { size: 11 } }, range: [-145, -45], dtick: 10 }),
      }), table(["구간 시작", "구간 종료", "결과"],
                series.call_spans.map((s) => [s.start_dt, s.end_dt, s.label])));
    },
  },
  {
    chart: "signal-level",
    title: "RAT별 신호 세기",
    sub: "Signal level 추이",
    prompt: "RAT별 signal level 저하, 급변, slot 차이를 보고 품질 저하나 망 전환 징후가 있는지 분석해줘.",
    render(series, panel) {
      const colors = seriesColors();
      const traces = [...groupBy(series.points, (p) => p.rat || "Unknown")].map(
        ([name, points], index) =>
          lineTrace(name, points.map((p) => p.time), points.map((p) => p.level),
                    colors[index % colors.length], {
            line: { width: 2, shape: "hv", color: colors[index % colors.length] },
            hovertemplate: "level <b>%{y}</b><br>%{x}<extra>" + name + "</extra>",
          }),
      );
      panel.draw(traces, baseLayout({
        showlegend: traces.length > 1,
        hovermode: "x unified",
        margin: { l: 56, r: 88, t: 8, b: 72 },
        xaxis: axis({ tickangle: -35, nticks: 8 }),
        yaxis: axis({ title: { text: "level", font: { size: 11 } }, range: [-0.1, 4.1], dtick: 1 }),
        annotations: endLabels(traces),
      }), frameTable(series.points, ["time", "rat", "slot", "level"]));
    },
  },
  {
    chart: "call-history",
    title: "통화 세션",
    sub: "세션 결과별 건수",
    prompt: "실패/드롭 세션이 있으면 release cause, callFailCause, SIP/RF 주변 근거를 연결해서 통화 실패 원인을 정리해줘.",
    render(series, panel) {
      const counts = new Map();
      for (const status of series.statuses || []) counts.set(status, (counts.get(status) || 0) + 1);
      const rows = [...counts.entries()].sort((a, b) => a[1] - b[1]);
      const ramp = sequentialRamp();
      const max = Math.max(...rows.map((r) => r[1]), 1);

      panel.prepend(tileRow([tile("통화 세션", fmt.count(series.call_count), "건")]));
      panel.draw([barTrace("건수", rows.map((r) => r[1]), rows.map((r) => r[0]),
                           rows.map((r) => stepColor(r[1], max, ramp)), {
        orientation: "h",
        text: rows.map((r) => fmt.count(r[1])),
        textposition: "outside",
        textfont: { size: 11 },
        cliponaxis: false,
        hovertemplate: "<b>%{x}건</b><extra>%{y}</extra>",
      })], baseLayout({
        bargap: 0.45,
        margin: { l: 152, r: 72, t: 8, b: 44 },
        yaxis: axis({ gridcolor: "rgba(0,0,0,0)" }),
      }), frameTable(series.table));
    },
  },
  {
    chart: "data-call",
    title: "Data Call 설정",
    sub: "APN별 상태 전이",
    prompt: "Data call 실패 cause, APN, latency, Internet stall 관련 이벤트를 함께 보고 데이터 연결 실패 원인을 정리해줘.",
    render(series, panel) {
      const kpi = series.kpi;
      panel.prepend(tileRow([
        tile("연결 시도", fmt.count(kpi.attempt_count)),
        tile("성공률", fmt.fixed(kpi.success_rate), "%", `실패 ${kpi.fail_count}건`,
             kpi.fail_count ? "critical" : "good"),
        tile("평균 설정 지연", fmt.count(Math.round(kpi.avg_setup_latency_ms)), "ms"),
      ]));

      if (!series.points.length) {
        panel.note("표시할 상태 전이 이벤트가 없습니다.");
        return;
      }

      const colors = seriesColors();
      const traces = [...groupBy(series.points, (p) => p.status)].map(([name, points], index) => ({
        type: "scatter", mode: "markers", name,
        x: points.map((p) => p.req_time_dt), y: points.map((p) => p.apn),
        marker: { size: 11, color: colors[index % colors.length],
                  line: { width: 2, color: getComputedStyle(document.body).getPropertyValue("--surface-1").trim() } },
        text: points.map((p) => `${p.event_type} · ${p.cause}`),
        hovertemplate: "<b>%{y}</b><br>%{text}<br>%{x}<extra>" + name + "</extra>",
      }));

      panel.draw(traces, baseLayout({ showlegend: true, margin: { l: 120, r: 24, t: 8, b: 44 } }),
                 frameTable(series.table, ["req_time", "apn", "status", "event_type", "cause", "latency_ms"]));
    },
  },
  {
    chart: "rilj",
    title: "RILJ transaction",
    sub: "무응답 · 오류 · 지연된 요청",
    prompt: "Timeout, 오류 응답, slow request, UNSL 이벤트를 보고 modem/RIL 응답 지연이나 명령 실패가 있는지 분석해줘.",
    render(series, panel) {
      const kpi = series.kpi;
      panel.prepend(tileRow([
        tile("RIL 요청", fmt.count(kpi.request_count)),
        tile("Timeout", fmt.count(kpi.timeout_count), "", kpi.timeout_count ? "주의" : "정상",
             kpi.timeout_count ? "critical" : "good"),
        tile("오류 응답", fmt.count(kpi.error_count), "", kpi.error_count ? "오류" : "정상",
             kpi.error_count ? "critical" : "good"),
        tile("UNSL 이벤트", fmt.count(kpi.unsol_count)),
      ]));

      if (!series.abnormal.length) {
        panel.note(`Timeout, 오류 응답, ${series.slow_threshold_ms}ms 초과 지연이 없습니다.`);
        return;
      }
      panel.content(frameTable(series.abnormal));
    },
  },
  {
    chart: "sip-flow",
    title: "VoLTE / IMS SIP",
    sub: "단말과 IMS 망 사이 메시지",
    prompt: "SIP 4xx~6xx 오류, transaction 흐름, setup latency를 보고 VoLTE/IMS 실패 지점을 정리해줘.",
    render(series, panel) {
      const kpi = series.kpi;
      panel.prepend(tileRow([
        tile("SIP transaction", fmt.count(kpi.transaction_count)),
        tile("오류 응답", fmt.count(kpi.error_count), "", kpi.error_count ? "4xx~6xx 발생" : "정상",
             kpi.error_count ? "critical" : "good"),
        tile("통화 설정 지연", kpi.setup_latency_ms === null ? "N/A" : fmt.count(kpi.setup_latency_ms),
             kpi.setup_latency_ms === null ? "" : "ms"),
      ]));
      panel.content(table(["시각", "방향", "메시지", "CSeq", "판정"],
        series.messages.map((m) => [m.time_label, m.is_outgoing ? "UE → 망" : "망 → UE",
                                    m.method_code, m.cseq, m.kind === "error" ? "오류" : m.kind === "success" ? "성공" : ""])));
    },
  },
  {
    chart: "network-timeline",
    title: "네트워크 품질 추이",
    sub: "구간별 DNS/TCP 통계",
    prompt: "DNS/TCP 지연 spike와 netId별 품질 변화를 보고 인터넷 먹통이나 지연 구간의 계층별 원인을 정리해줘.",
    render(series, panel, sourceFile) {
      if (!series.metrics?.length) {
        panel.empty("no_data");
        return;
      }
      const colors = seriesColors();
      const picker = el("select", "metric-picker");
      for (const metric of series.metrics) picker.append(new Option(metric.label, metric.column));

      const drawMetric = (column) => {
        const metric = series.metrics.find((m) => m.column === column) || series.metrics[0];
        const traces = [...groupBy(series.frame, (row) => row.netId)].map(([name, rows], index) => {
          const points = rows
            .map((row) => ({ x: row.time_dt, y: Number(row[metric.column]) }))
            .filter((point) => point.x && Number.isFinite(point.y));
          return lineTrace(`netId ${name}`, points.map((point) => point.x), points.map((point) => point.y),
                           colors[index % colors.length], {
            hovertemplate: `<b>%{y}</b> ${metric.unit}<br>%{x}<extra>netId ${name}</extra>`,
          });
        }).filter((trace) => trace.x.length);
        panel.draw(traces, baseLayout({
          showlegend: traces.length > 1,
          hovermode: "x unified",
          margin: { l: 64, r: 96, t: 8, b: 44 },
          yaxis: axis({ title: { text: metric.unit, font: { size: 11 } } }),
          annotations: endLabels(traces),
        }), frameTable(series.spikes));
      };

      // The default route is one value for the whole session, so it comes from
      // its own endpoint instead of riding along on every timeline row. The
      // slot is placed now to keep it above the picker; the answer fills it in.
      const defaultNet = el("p", "card-note");
      panel.prepend(defaultNet);
      api.chart("active-network", sourceFile)
        .then((active) => {
          if (active.status !== "ok") {
            defaultNet.remove();
            return;
          }
          defaultNet.textContent = active.transport
            ? `기본설정 Network: ${active.transport}(${active.net_id})`
            : `기본설정 Network: netId ${active.net_id}`;
        })
        .catch(() => defaultNet.remove());

      picker.addEventListener("change", () => drawMetric(picker.value));
      panel.prepend(picker);
      drawMetric(series.metrics[0].column);

      if (series.spikes.length) {
        panel.body.append(el("p", "card-note",
          `DNS Spike 구간 ${series.spikes.length}개 (평균 ${series.spike_threshold_ms}ms 이상 또는 지연 플래그). "표"에서 확인하세요.`));
      }
    },
  },
  {
    chart: "internet-stall",
    title: "Data Stall 흐름",
    sub: "시작 · 복구 진행 · 종료 시점",
    prompt: "Data Stall 흐름의 시작/복구/종료 시점, 지속 시간, 주변 DataCall/RF 근거를 연결해서 장애 흐름을 정리해줘.",
    render(series, panel) {
      const kpi = series.kpi;
      const flows = series.data_stall_flows || [];
      const rfWarnings = series.rf_warnings || [];
      const completed = flows.filter((row) => row.status === "회복 완료").length;
      const unresolved = Math.max(flows.length - completed, 0);
      const eventLabel = (eventType) => {
        if (eventType === "WEAK_SIGNAL") return "약전계";
        if (eventType === "OOS_OR_REG_STATE") return "권외/등록상태";
        return eventType || "-";
      };

      panel.prepend(tileRow([
        tile("Data Stall 흐름", fmt.count(kpi.data_stall_flow_count ?? flows.length), "개",
             unresolved ? `미종료 ${unresolved}개` : "모두 회복",
             unresolved ? "critical" : "good"),
        tile("복구 완료", fmt.count(completed), "개"),
        tile("DataCall 실패", fmt.count(kpi.data_call_fail_or_drop_count), "건"),
        tile("RF 경고", fmt.count(kpi.rf_warning_count), "건"),
      ]));

      const summary = el("div", "stack");

      if (flows.length) {
        summary.append(frameTable(flows.slice(0, 8), [
          "start_time",
          "recovery_start_time",
          "recovery_end_time",
          "end_time",
          "duration_sec",
          "status",
          "event_count",
        ]));
      } else {
        summary.append(el("p", "card-note", "명확한 Data Stall start/recovery/end 흐름이 없습니다."));
      }

      if (rfWarnings.length) {
        summary.append(el("h3", "sub-head", "RF 경고 내용"));
        summary.append(table(["시각", "내용", "Slot", "RAT", "설명"],
          rfWarnings.slice(0, 6).map((row) => [
            row.time,
            eventLabel(row.event_type),
            row.slot || "-",
            row.rat || "-",
            row.reason || "-",
          ])));
      }

      panel.content(summary);
    },
  },
  {
    chart: "dns-errors",
    title: "패키지별 DNS 오류",
    sub: "성공(0/SUCCESS)을 제외한 응답 코드",
    prompt: "패키지별 DNS return code 분포를 보고 특정 앱/도메인/Private DNS 정책 문제 가능성을 정리해줘.",
    render(series, panel) {
      const codes = [...new Set(series.counts.map((row) => row.return_code))].slice(0, 4);
      const apps = [...new Set(series.counts.map((row) => row.app_name))];
      const colors = seriesColors();

      const traces = codes.map((code, index) =>
        barTrace(String(code), apps,
                 apps.map((app) => {
                   const hit = series.counts.find((row) => row.app_name === app && row.return_code === code);
                   return hit ? hit.count : 0;
                 }),
                 colors[index % colors.length],
                 { hovertemplate: "<b>%{y}건</b><br>%{x}<extra>" + String(code) + "</extra>" }),
      );

      panel.draw(traces, baseLayout({
        barmode: "stack", bargap: 0.45, showlegend: true,
        margin: { l: 56, r: 24, t: 8, b: 96 },
        xaxis: axis({ gridcolor: "rgba(0,0,0,0)", tickangle: -30 }),
        yaxis: axis({ title: { text: "건수", font: { size: 11 } } }),
      }), frameTable(series.counts));
    },
  },
  {
    chart: "dns-issues",
    title: "DNS 실패 · 차단",
    sub: "패키지별 발생 건수",
    prompt: "DNS 실패나 차단이 몰린 패키지를 중심으로 네트워크 문제인지 앱/정책 문제인지 구분해줘.",
    render(series, panel) {
      const rows = [...series.package_counts].sort((a, b) => a.count - b.count);
      const ramp = sequentialRamp();
      const max = Math.max(...rows.map((r) => r.count), 1);

      panel.draw([barTrace("건수", rows.map((r) => r.count), rows.map((r) => r.package),
                           rows.map((r) => stepColor(r.count, max, ramp)), {
        orientation: "h",
        text: rows.map((r) => fmt.count(r.count)),
        textposition: "outside",
        textfont: { size: 11 },
        cliponaxis: false,
        hovertemplate: "<b>%{x}건</b><extra>%{y}</extra>",
      })], baseLayout({
        bargap: 0.45,
        margin: { l: 176, r: 72, t: 8, b: 44 },
        yaxis: axis({ gridcolor: "rgba(0,0,0,0)" }),
      }), frameTable(series.table));
    },
  },
  {
    chart: "data-usage",
    title: "앱별 데이터 사용량",
    sub: "셀룰러 누적 사용량 상위 10개",
    prompt: "데이터 사용량이 큰 앱과 시간대/세션 근거를 보고 비정상 트래픽이나 배터리 영향 가능성을 정리해줘.",
    render(series, panel) {
      const rows = [...series.app_totals].sort((a, b) => a.total_mb - b.total_mb);
      const ramp = sequentialRamp();
      const max = Math.max(...rows.map((r) => r.total_mb), 1);

      panel.draw([barTrace("사용량", rows.map((r) => r.total_mb), rows.map((r) => r.app_name),
                           rows.map((r) => stepColor(r.total_mb, max, ramp)), {
        orientation: "h",
        text: rows.map((r) => fmt.mb(r.total_mb)),
        textposition: "outside",
        textfont: { size: 11 },
        cliponaxis: false,
        hovertemplate: "<b>%{x:.1f} MB</b><extra>%{y}</extra>",
      })], baseLayout({
        bargap: 0.45,
        margin: { l: 184, r: 96, t: 8, b: 44 },
        xaxis: axis({ title: { text: "MB", font: { size: 11 } } }),
        yaxis: axis({ gridcolor: "rgba(0,0,0,0)" }),
      }), table(["앱", "사용량(MB)"], [...rows].reverse().map((r) => [r.app_name, r.total_mb])));
    },
  },
  {
    chart: "data-usage-top-time",
    title: "시간대별 앱 사용량 Top 7",
    sub: "1시간 단위 앱별 셀룰러 사용량",
    prompt: "시간대별 상위 앱 사용량을 보고 특정 앱의 트래픽 집중, 백그라운드 사용, 장애 시간대와의 상관관계를 정리해줘.",
    render(series, panel) {
      const colors = seriesColors();
      const buckets = [...new Set(series.frame.map((r) => r.bucket))];
      const apps = [...new Set(series.frame.map((r) => r.app_name))];
      const byKey = new Map(series.frame.map((r) => [`${r.bucket}\n${r.app_name}`, r]));

      const traces = apps.map((app, index) => ({
        type: "bar",
        name: app,
        x: buckets,
        y: buckets.map((bucket) => byKey.get(`${bucket}\n${app}`)?.total_mb || 0),
        marker: {
          color: colors[index % colors.length],
          cornerradius: 3,
          line: { width: 1, color: getComputedStyle(document.body).getPropertyValue("--surface-1").trim() },
        },
        customdata: buckets.map((bucket) => byKey.get(`${bucket}\n${app}`)?.rank || ""),
        hovertemplate: "<b>%{y:.1f} MB</b><br>%{x}<br>rank %{customdata}<extra>" + app + "</extra>",
      }));

      panel.prepend(tileRow([
        tile("시간 버킷", fmt.count(buckets.length), "개"),
        tile("버킷당 상위", fmt.count(series.top_n), "앱"),
      ]));
      panel.draw(traces, baseLayout({
        barmode: "stack",
        showlegend: traces.length > 1,
        hovermode: "x unified",
        margin: { l: 64, r: 24, t: 8, b: 72 },
        xaxis: axis({ tickangle: -35, nticks: Math.min(buckets.length, 10) }),
        yaxis: axis({ title: { text: "MB", font: { size: 11 } } }),
      }), frameTable(series.table, ["bucket", "rank", "app_name", "total_mb"]));
    },
  },
  {
    chart: "power-thermal",
    title: "전력 · 발열",
    sub: "온도 센서 상위 10개",
    prompt: "온도 센서 상위값, thermal threshold, wakelock/앱 사용량 근거를 함께 보고 발열 원인 후보를 정리해줘.",
    render(series, panel) {
      const thermals = series.thermals;
      if (thermals.status !== "ok") {
        panel.empty(thermals.status);
        return;
      }

      const rows = [...thermals.frame].sort((a, b) => a.temperature - b.temperature);
      const ramp = sequentialRamp();
      const max = Math.max(...rows.map((r) => r.temperature), 1);

      panel.draw([barTrace("온도", rows.map((r) => r.temperature), rows.map((r) => r.sensor),
                           rows.map((r) => stepColor(r.temperature, max, ramp)), {
        orientation: "h",
        text: rows.map((r) => `${fmt.fixed(r.temperature)} °C`),
        textposition: "outside",
        textfont: { size: 11 },
        cliponaxis: false,
        hovertemplate: "<b>%{x:.1f} °C</b><extra>%{y}</extra>",
      })], baseLayout({
        bargap: 0.45,
        margin: { l: 152, r: 80, t: 8, b: 44 },
        xaxis: axis({ title: { text: "°C", font: { size: 11 } } }),
        yaxis: axis({ gridcolor: "rgba(0,0,0,0)" }),
        shapes: [{
          type: "line", xref: "x", yref: "paper", layer: "above",
          x0: series.thermal_warning_c, x1: series.thermal_warning_c, y0: 0, y1: 1,
          line: { width: 1, dash: "dot", color: getComputedStyle(document.body).getPropertyValue("--status-critical").trim() },
        }],
        annotations: [{
          x: series.thermal_warning_c, y: 1, xref: "x", yref: "paper", yanchor: "bottom",
          text: `주의 ${series.thermal_warning_c}°C`, showarrow: false,
          font: { size: 11, color: getComputedStyle(document.body).getPropertyValue("--status-critical").trim() },
        }],
      }), frameTable(rows, ["sensor", "temperature"]));
    },
  },
];

export async function renderDashboard(mount, sourceFile, ctx) {
  const band = section("세션 요약");
  mount.append(band.wrap);

  const report = card("종합 진단 리포트", "이 세션의 이벤트와 지표를 모아 LLM 이 정리합니다.");
  report.section.classList.add("wide");
  band.grid.append(report.section);
  reportCard(report, "현재 세션 리포트 생성",
             () => api.sessionReport(baseName(sourceFile), sourceFile),
             `session:${sourceFile}`);

  api.kpi(sourceFile)
    .then((kpi) => band.wrap.insertBefore(kpiBand(kpi), band.grid))
    .catch(() => band.wrap.insertBefore(el("p", "card-note", "KPI 를 불러오지 못했습니다."), band.grid));

  // Every shell first: a chart drawn into a one-column grid freezes that
  // width and then hangs over its neighbours once the grid reflows.
  const panels = CARDS.map((spec) => {
    const panel = card(spec.title, spec.sub);
    if (ctx?.startChat) {
      panel.action("LLM 분석 요청", () => ctx.startChat(sectionAnalysisQuestion("대시보드", spec, sourceFile)), "primary");
    }
    band.grid.append(panel.section);
    return { spec, panel };
  });

  for (const { spec, panel } of panels) {
    api.chart(spec.chart, sourceFile)
      .then((series) => (series.status === "ok" ? spec.render(series, panel, sourceFile) : panel.empty(series.status)))
      .catch((error) => {
        console.error(spec.chart, error);
        panel.empty("load_failed");
      });
  }
}
