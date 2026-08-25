// 인터넷 품질 — stall 구간과 그 원인 후보.

import { api } from "../api.js";
import {
  axis, barTrace, baseLayout, card, el, fmt, frameTable, groupBy, section,
  seriesColors, sequentialRamp, stepColor, table, tile, tileRow,
} from "../viz.js";

const KPI_TILES = [
  ["stall_window_count", "Stall 구간"],
  ["high_risk_window_count", "고위험 구간"],
  ["primary_root_cause_candidate", "주요 후보"],
  ["total_timeline_events", "이벤트 수"],
  ["dns_issue_count", "DNS 이슈"],
  ["validation_fail_count", "검증 실패"],
  ["data_stall_count", "Data Stall"],
  ["rf_warning_count", "RF 경고"],
  ["data_call_fail_or_drop_count", "DataCall 실패"],
  ["tcp_tls_timeout_count", "TCP/TLS Timeout"],
  ["power_idle_hint_count", "전원/Idle"],
];

// severity is a state, so it gets the status palette and always a label.
const SEVERITY_COLORS = {
  critical: "--status-critical",
  serious: "--status-serious",
  warning: "--status-warning",
  info: "--series-1",
};

const severityColor = (severity) =>
  getComputedStyle(document.body).getPropertyValue(SEVERITY_COLORS[severity] || "--series-1").trim();

function rootCauseCard(report, panel) {
  if (!report.root_causes.length) {
    panel.note("도출된 원인 후보가 없습니다.");
    return;
  }

  const rows = [...report.root_causes].sort((a, b) => a.count - b.count);
  const ramp = sequentialRamp();
  const max = Math.max(...rows.map((r) => r.count), 1);

  panel.draw([barTrace("건수", rows.map((r) => r.count), rows.map((r) => r.category),
                       rows.map((r) => stepColor(r.count, max, ramp)), {
    orientation: "h",
    text: rows.map((r) => fmt.count(r.count)),
    textposition: "outside",
    textfont: { size: 11 },
    cliponaxis: false,
    customdata: rows.map((r) => [r.high, r.medium, r.low]),
    hovertemplate: "<b>%{x}건</b><br>high %{customdata[0]} · medium %{customdata[1]} · low %{customdata[2]}<extra>%{y}</extra>",
  })], baseLayout({
    bargap: 0.45,
    margin: { l: 176, r: 72, t: 8, b: 44 },
    yaxis: axis({ gridcolor: "rgba(0,0,0,0)" }),
  }), frameTable(report.root_causes));
}

function timelineCard(report, panel) {
  if (report.timeline_status !== "ok") {
    panel.empty(report.timeline_status);
    return;
  }

  const traces = [...groupBy(report.timeline, (row) => row.severity)].map(([severity, rows]) => ({
    type: "scatter", mode: "markers", name: severity,
    x: rows.map((r) => r.time_dt), y: rows.map((r) => r.layer),
    marker: {
      size: 11, color: severityColor(severity),
      line: { width: 2, color: getComputedStyle(document.body).getPropertyValue("--surface-1").trim() },
    },
    text: rows.map((r) => `${r.event_type}${r.reason ? " · " + r.reason : ""}`),
    hovertemplate: "<b>%{y}</b><br>%{text}<br>%{x}<extra>" + severity + "</extra>",
  }));

  panel.draw(traces, baseLayout({
    showlegend: true,
    margin: { l: 128, r: 24, t: 8, b: 44 },
  }), frameTable(report.timeline_table));
}

function windowsCard(report, panel) {
  if (!report.windows.length) {
    panel.note("식별된 Stall 구간이 없습니다.");
    return;
  }

  const ranked = [...report.windows].sort((a, b) => (b.severity_score || 0) - (a.severity_score || 0));
  const wrap = el("div", "stack");
  wrap.append(table(["#", "중심 시각", "트리거", "심각도", "주요 원인", "확신도"],
    ranked.map((w) => [w.idx, w.center_time, w.trigger, w.severity_score, w.primary_category, w.confidence])));

  const picker = el("select", "metric-picker");
  for (const window of ranked) {
    picker.append(new Option(`#${window.idx} · ${window.center_time} · ${window.trigger}`, String(window.idx)));
  }

  const detail = el("div", "stack");
  const showDetail = (idx) => {
    const window = report.windows[Number(idx)];
    detail.replaceChildren();
    if (!window) return;

    detail.append(el("h3", "sub-head", "원인 후보"));
    detail.append(table(["분류", "확신도"],
      (window.root_cause_candidates || []).map((c) => [c.category, c.confidence])));

    if (window.related_table.length) {
      detail.append(el("h3", "sub-head", "관련 이벤트"));
      detail.append(frameTable(window.related_table));
    }
  };

  picker.addEventListener("change", () => showDetail(picker.value));
  wrap.append(el("h3", "sub-head", "구간 상세"), picker, detail);
  showDetail(ranked[0].idx);

  panel.content(wrap);
}

function layerCard(report, panel) {
  if (report.timeline_status !== "ok") {
    panel.empty(report.timeline_status);
    return;
  }

  const counts = new Map();
  for (const event of report.timeline) counts.set(event.layer, (counts.get(event.layer) || 0) + 1);

  const rows = [...counts.entries()].sort((a, b) => a[1] - b[1]);
  const ramp = sequentialRamp();
  const max = Math.max(...rows.map((r) => r[1]), 1);

  panel.draw([barTrace("이벤트", rows.map((r) => r[1]), rows.map((r) => r[0]),
                       rows.map((r) => stepColor(r[1], max, ramp)), {
    orientation: "h",
    text: rows.map((r) => fmt.count(r[1])),
    textposition: "outside",
    textfont: { size: 11 },
    cliponaxis: false,
    hovertemplate: "<b>%{x}건</b><extra>%{y}</extra>",
  })], baseLayout({
    bargap: 0.45,
    margin: { l: 144, r: 72, t: 8, b: 44 },
    yaxis: axis({ gridcolor: "rgba(0,0,0,0)" }),
  }), table(["계층", "이벤트 수"], [...rows].reverse()));
}

const CARDS = [
  { title: "원인 후보 분포", sub: "분류별 근거 수", render: rootCauseCard },
  { title: "계층별 이벤트 타임라인", sub: "심각도별로 표시", render: timelineCard },
  { title: "계층별 이벤트 수", sub: "어느 계층에서 몰렸는지", render: layerCard },
  { title: "고위험 구간", sub: "심각도 순", render: windowsCard },
];

export async function renderInternet(mount, sourceFile) {
  const band = section("인터넷 연결 품질");
  mount.append(band.wrap);

  let report;
  try {
    report = await api.chart("internet-stall", sourceFile);
  } catch (error) {
    console.error("internet-stall", error);
    band.grid.append(el("p", "card-note", "인터넷 품질 분석을 불러오지 못했습니다."));
    return;
  }

  if (report.status !== "ok") {
    band.grid.append(el("p", "card-note", "이 세션에는 인터넷 품질 분석 결과가 없습니다."));
    return;
  }

  const kpi = report.kpi;
  band.wrap.insertBefore(
    tileRow(KPI_TILES.map(([key, label]) => tile(label, kpi[key] === null || kpi[key] === undefined ? "-" : String(kpi[key])))),
    band.grid,
  );

  const panels = CARDS.map((spec) => {
    const panel = card(spec.title, spec.sub);
    band.grid.append(panel.section);
    return { spec, panel };
  });

  for (const { spec, panel } of panels) {
    try {
      spec.render(report, panel);
    } catch (error) {
      console.error(spec.title, error);
      panel.empty("load_failed");
    }
  }
}
