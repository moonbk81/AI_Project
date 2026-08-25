// 부팅 — 부팅 타이밍과 그 과정에서 터진 것들.

import { api } from "../api.js";
import {
  axis, barTrace, baseLayout, card, el, fmt, frameTable, lineTrace, section,
  seriesColors, sequentialRamp, stepColor, table, tile, tileRow,
} from "../viz.js";

function bootCard(series, panel) {
  const milestones = series.milestones;
  panel.prepend(tileRow([
    tile("부팅 완료", fmt.ms(milestones.boot_complete_ms)),
    tile("Voice(RIL) 준비", fmt.ms(milestones.voice_ready_ms)),
    tile("Data(NW) 준비", fmt.ms(milestones.data_ready_ms)),
  ]));

  if (!series.has_deltas) {
    panel.note("이벤트별 지연(Delta_ms)이 기록되지 않아 병목 차트를 그릴 수 없습니다.");
    return;
  }
  if (!series.slow_events.length) {
    panel.note("지연된 부팅 구간이 없습니다.");
    return;
  }

  const rows = [...series.slow_events].sort((a, b) => a.Delta_ms - b.Delta_ms);
  const ramp = sequentialRamp();
  const max = Math.max(...rows.map((r) => r.Delta_ms), 1);

  panel.draw([barTrace("지연", rows.map((r) => r.Delta_ms), rows.map((r) => r.Event),
                       rows.map((r) => stepColor(r.Delta_ms, max, ramp)), {
    orientation: "h",
    text: rows.map((r) => fmt.ms(r.Delta_ms)),
    textposition: "outside",
    textfont: { size: 11 },
    cliponaxis: false,
    hovertemplate: "<b>%{x:,.0f} ms</b><extra>%{y}</extra>",
  })], baseLayout({
    bargap: 0.45,
    margin: { l: 224, r: 96, t: 8, b: 44 },
    xaxis: axis({ title: { text: "지연(ms)", font: { size: 11 } } }),
    yaxis: axis({ gridcolor: "rgba(0,0,0,0)" }),
  }), frameTable(series.timeline));
}

function crashCard(series, panel) {
  const blocks = [];

  const push = (title, node) => {
    blocks.push(el("h3", "sub-head", title));
    blocks.push(node);
  };

  if (series.system_kills.length) push(`시스템 강제 종료 ${series.system_kills.length}건`, frameTable(series.system_kills));
  if (series.system_wtf.total) push(`시스템 이상 징후 ${series.system_wtf.total}건`, frameTable(series.system_wtf.by_process));

  const binder = series.binder;
  if (binder.status === "ok") {
    if (binder.spam.length) {
      push(`Binder Oneway Spam ${binder.spam.length}건`,
           table(["시각", "설명"], binder.spam.map((s) => [s.time, s.desc])));
    }
    if (binder.events.length) {
      push(`Binder 지연 · 실패 ${binder.event_count}건` + (binder.truncated ? ` (최근 ${binder.display_cap}건 표시)` : ""),
           frameTable(binder.events));
    }
  }

  if (series.native_crashes.length) {
    push(`Native Crash ${series.native_crashes.length}건`,
         table(["시각", "프로세스", "signal", "abort"],
               series.native_crashes.map((c) => [c.time, c.process, c.signal, c.abort_message])));
  }

  if (series.anr_events.length) {
    push(`ANR ${series.anr_events.length}건`,
         table(["시각", "프로세스", "PID", "사유", "Lock 대기"],
               series.anr_events.map((a) => [a.time, a.process, a.pid, a.reason,
                                             a.lock_chain ? `TID ${a.lock_chain.blocker_thread}` : "-"])));
  }

  if (series.java_crashes.length) {
    push(`Crash / FATAL ${series.java_crashes.length}건`,
         table(["시각", "프로세스", "종류", "예외", "주요 Method"],
               series.java_crashes.map((c) => [c.time, c.process, c.crash_type, c.exception_info, c.top_method])));
  }

  if (!blocks.length) {
    panel.note("Crash, ANR, Binder 이벤트가 감지되지 않았습니다.");
    return;
  }

  const wrap = el("div", "stack");
  wrap.append(...blocks);
  panel.content(wrap);
}

function proxyCard(series, panel) {
  const wrap = el("div", "stack");
  const colors = seriesColors();

  series.items.forEach((histogram, index) => {
    wrap.append(el("h3", "sub-head",
      `[${histogram.time}] 최대 ${fmt.count(histogram.max_count)}개` + (histogram.is_leak ? " — 임계치 초과" : "")));

    if (!histogram.counts.length) {
      wrap.append(el("p", "card-note", "히스토그램 원문에서 인터페이스를 읽지 못했습니다."));
      return;
    }
    const plot = el("div", "plot");
    wrap.append(plot);

    const rows = histogram.counts;
    queueMicrotask(() => Plotly.react(plot, [barTrace("Proxy 객체", rows.map((r) => r.Count), rows.map((r) => r.Class),
      colors[index % colors.length], {
        orientation: "h",
        text: rows.map((r) => fmt.count(r.Count)),
        textposition: "outside",
        textfont: { size: 11 },
        cliponaxis: false,
        hovertemplate: "<b>%{x}</b><extra>%{y}</extra>",
      })], baseLayout({
        bargap: 0.45,
        margin: { l: 200, r: 80, t: 8, b: 44 },
        yaxis: axis({ gridcolor: "rgba(0,0,0,0)" }),
      }), { displayModeBar: false, responsive: true }));
  });

  panel.content(wrap);
}

function nitzCard(series, panel) {
  const kpi = series.kpi;
  const stability = { unstable: "불안정 (핑퐁)", long_stay: "장기 체류", stable: "안정" }[kpi.stability];
  panel.prepend(tileRow([
    tile("최초 타임존", kpi.first_timezone),
    tile("최종 타임존", kpi.last_timezone),
    tile("변경 횟수", fmt.count(kpi.change_count), "회", stability,
         kpi.stability === "unstable" ? "critical" : "good"),
  ]));

  const colors = seriesColors();
  panel.draw([lineTrace("UTC 오프셋", series.offsets.map((p) => p.log_time_dt),
                        series.offsets.map((p) => p.offset), colors[2], {
    line: { width: 2, shape: "hv", color: colors[2] },
    hovertemplate: "UTC%{y:+g}<br>%{x}<extra></extra>",
  })], baseLayout({
    margin: { l: 64, r: 24, t: 8, b: 44 },
    yaxis: axis({ title: { text: "UTC 오프셋", font: { size: 11 } } }),
  }), frameTable(series.changes));
}

const CARDS = [
  { chart: "boot", title: "부팅 지연 구간", sub: "가장 오래 걸린 이벤트", render: bootCard },
  { chart: "crash", title: "Crash · ANR · Binder", sub: "부팅 과정에서 감지된 이벤트", render: crashCard },
  { chart: "binder-proxy", title: "Binder Proxy 현황", sub: "인터페이스별 Proxy 객체 수", render: proxyCard },
  { chart: "nitz", title: "NITZ 타임존 변동", sub: "망이 알려준 시간대", render: nitzCard },
];

export async function renderBoot(mount, sourceFile) {
  const band = section("부팅 시퀀스");
  mount.append(band.wrap);

  for (const spec of CARDS) {
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
