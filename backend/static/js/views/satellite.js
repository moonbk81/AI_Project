// 위성 — NTN(SpaceX) 정책 전이 또는 위성 모뎀(Tiantong) 제어 상태.

import { api, baseName } from "../api.js";
import {
  axis, baseLayout, card, el, fmt, frameTable, groupBy, lineTrace, section,
  seriesColors, table, tile, tileRow,
} from "../viz.js";

function ntnCard(series, panel) {
  const status = series.ntn_status;
  panel.prepend(tileRow([
    tile("대상 위성 PLMN", status.plmn),
    tile("적용 데이터 정책", status.data_policy),
    tile("상태바 아이콘", status.icon_status, "", status.icon_status === "OFF" ? "" : "표시 중",
         status.icon_status === "OFF" ? "" : "good"),
  ]));

  if (!series.transitions.length) {
    panel.note("표시할 상태 전이 이벤트가 없습니다.");
    return;
  }

  const colors = seriesColors();
  const traces = [...groupBy(series.transitions, (row) => row.event_type)].map(([name, rows], index) => ({
    type: "scatter", mode: "markers", name,
    x: rows.map((r) => r.time_dt), y: rows.map((r) => r.event_type),
    marker: {
      size: 13, symbol: "diamond", color: colors[index % colors.length],
      line: { width: 2, color: getComputedStyle(document.body).getPropertyValue("--surface-1").trim() },
    },
    text: rows.map((r) => `${r.ntn_plmn ?? "-"} · mode ${r.ntn_mode ?? "-"}`),
    hovertemplate: "<b>%{y}</b><br>%{text}<br>%{x}<extra></extra>",
  }));

  panel.draw(traces, baseLayout({
    showlegend: false,
    margin: { l: 168, r: 24, t: 8, b: 44 },
    yaxis: axis({ type: "category", categoryorder: "array", categoryarray: series.event_order }),
  }), frameTable(series.table));
}

function satAtCard(series, panel) {
  const kpi = series.kpi;
  panel.prepend(tileRow([
    tile("위성 ARFCN", kpi.arfcn),
    tile("등록 상태", kpi.reg_state),
    tile("음성 통화", `${kpi.calls_total} / ${kpi.calls_failed}`, "", "전체 / 실패",
         kpi.calls_failed ? "critical" : "good"),
    tile("SMS", `${kpi.sms_rx} / ${kpi.sms_tx_success} / ${kpi.sms_tx_fail}`, "", "Rx / Tx 성공 / Tx 실패",
         kpi.sms_tx_fail ? "critical" : "good"),
  ]));

  if (!series.registration.length) {
    panel.note("위성망 등록 이력이 없습니다.");
    return;
  }

  const colors = seriesColors();
  panel.draw([lineTrace("등록 상태", series.registration.map((r) => r.time),
                        series.registration.map((r) => r.status_str), colors[1], {
    line: { width: 2, shape: "hv", color: colors[1] },
    hovertemplate: "<b>%{y}</b><br>%{x}<extra></extra>",
  })], baseLayout({
    margin: { l: 152, r: 24, t: 8, b: 60 },
    xaxis: axis({ tickangle: -35 }),
    yaxis: axis({ type: "category", categoryorder: "array", categoryarray: series.reg_state_order }),
  }), frameTable(series.registration));
}

function callFlowCard(series, panel) {
  if (!series.call_flow.length) {
    panel.note("통화 제어 시퀀스가 없습니다.");
    return;
  }
  const actors = ["Android FW", "RIL Daemon", "Modem (CP)"];
  panel.content(table(["시각", "구간", "메시지", "판정"],
    series.call_flow.map((step) => [
      step.time,
      `${actors[step.src] ?? step.src} → ${actors[step.dst] ?? step.dst}`,
      step.desc,
      step.is_error ? "오류" : step.is_highlight ? "주요" : "",
    ])));
}

export async function renderSatellite(mount, sourceFile) {
  const band = section("위성 통신");
  mount.append(band.wrap);

  let overview;
  try {
    overview = await api.satelliteOverview(baseName(sourceFile));
  } catch (error) {
    console.error("satellite", error);
    band.grid.append(el("p", "card-note", "위성 분석 결과를 불러오지 못했습니다."));
    return;
  }

  if (!overview.sat_type) {
    band.grid.append(el("p", "card-note", "이 세션에는 NTN 위성 통신 로그가 없습니다."));
    return;
  }

  band.wrap.insertBefore(tileRow([tile("위성 종류", overview.sat_type)]), band.grid);

  const specs = overview.sat_type === "SpaceX"
    ? [{ chart: "ntn", title: "NTN 상태 전이", sub: "로밍 정책과 상태바 아이콘", render: ntnCard }]
    : [
        { chart: "sat-at", title: "위성 모뎀 제어 상태", sub: "등록 이력", render: satAtCard },
        { chart: "sat-at", title: "통화 제어 시퀀스", sub: "AP ↔ RIL ↔ Modem", render: callFlowCard },
      ];

  for (const spec of specs) {
    const panel = card(spec.title, spec.sub);
    band.grid.append(panel.section);

    api.chart(spec.chart, sourceFile)
      .then((series) => (series.status === "ok" ? spec.render(series, panel) : panel.empty(series.status)))
      .catch((error) => {
        console.error(spec.chart, error);
        panel.empty("load_failed");
      });
  }
}
