// 위성 — NTN(SpaceX) 로밍 정책과 상태 전이.

import { api, baseName } from "../api.js";
import { reportCard } from "../report.js";
import {
  axis, baseLayout, card, el, frameTable, groupBy, section, seriesColors, tile, tileRow,
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

  const specs = [{ chart: "ntn", title: "NTN 상태 전이", sub: "로밍 정책과 상태바 아이콘", render: ntnCard }];

  const report = card(`${overview.sat_type} 위성망 리포트`, "위성 통신 구간을 LLM 이 정리합니다.");
  report.section.classList.add("wide");
  band.grid.append(report.section);
  reportCard(report, `${overview.sat_type} 리포트 생성`,
             () => api.satelliteReport(baseName(sourceFile), overview.sat_type, sourceFile));

  const panels = specs.map((spec) => {
    const panel = card(spec.title, spec.sub);
    band.grid.append(panel.section);
    return { spec, panel };
  });

  for (const { spec, panel } of panels) {
    api.chart(spec.chart, sourceFile)
      .then((series) => (series.status === "ok" ? spec.render(series, panel) : panel.empty(series.status)))
      .catch((error) => {
        console.error(spec.chart, error);
        panel.empty("load_failed");
      });
  }
}
