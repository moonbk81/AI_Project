// 부팅 — 부팅 타이밍과 그 과정에서 터진 것들.

import { api } from "../api.js";
import {
  axis, barTrace, baseLayout, card, drawPlot, el, fmt, frameTable, lineTrace, section,
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

// ------------------------------------------------- crash / ANR detail views

const SUMMARY_LABELS = {
  has_main_stack: "Main Stack",
  has_lock_contention: "Lock Contention",
  has_active_binder: "Binder Wait",
  has_pre_anr_logcat: "Pre-Logcat",
  has_cpu_hint: "CPU 단서",
  has_system_server_hint: "System Server 단서",
  has_io_hint: "I/O 단서",
};

function fold(summary, { open = false } = {}) {
  const node = el("details", "fold");
  if (open) node.open = true;
  node.append(el("summary", null, summary));
  return node;
}

/** A log dump behind a fold; the line count belongs in the summary. */
function logFold(label, lines, { open = false } = {}) {
  if (!lines || !lines.length) return null;
  const node = fold(`${label} (${lines.length}줄)`, { open });
  node.append(el("pre", null, lines.join("\n")));
  return node;
}

function summaryChips(summary) {
  const row = el("div", "quick");
  for (const [key, label] of Object.entries(SUMMARY_LABELS)) {
    row.append(el("span", "chip" + (summary[key] ? " active" : ""), label));
  }
  return row;
}

function anrEvent(anr) {
  const node = fold(`[${anr.time}] ${anr.process} (PID ${anr.pid})`);
  node.append(el("p", "card-note", `사유: ${anr.reason}`));

  if (anr.summary) node.append(summaryChips(anr.summary));

  // Main thread first: it is the thing that was stuck.
  const main = logFold("Main thread callstack", anr.main_stack, { open: true });
  if (main) node.append(main);

  if (anr.lock_chain) {
    const lock = el("div", "alert");
    lock.append(el("p", null,
      `Main thread 가 lock(${anr.lock_chain.lock_address}) 대기 중 — 점유 Thread TID ${anr.lock_chain.blocker_thread}`));
    node.append(lock);

    const blocker = logFold(`점유 Thread(TID ${anr.lock_chain.blocker_thread}) callstack`, anr.lock_chain.blocker_stack);
    if (blocker) node.append(blocker);
  }

  if (anr.binder_transactions.length) {
    node.append(el("h4", "sub-head", "대기 중인 Binder transaction"));
    node.append(frameTable(anr.binder_transactions));
  }

  for (const [label, lines] of [
    ["ANR 직전 Logcat", anr.pre_logcat],
    ["CPU 관련 로그", anr.cpu_logs],
    ["System server 관련 로그", anr.system_server_logs],
    ["I/O 지연 의심 로그", anr.io_logs],
  ]) {
    const block = logFold(label, lines);
    if (block) node.append(block);
  }

  return node;
}

function javaCrash(crash) {
  const node = fold(`[${crash.time}] ${crash.process} — ${crash.crash_type}`);

  if (crash.exception_info) {
    const box = el("div", "alert");
    box.append(el("pre", null, crash.exception_info));
    node.append(box);
  }
  if (crash.top_method) node.append(el("p", "card-note", `주요 Method: ${crash.top_method}`));

  if (crash.suspects_transaction_too_large) {
    const warn = el("div", "alert");
    warn.append(el("p", null,
      "TransactionTooLargeException 의심: Intent 데이터가 Binder buffer 한계를 넘었을 가능성이 있습니다."));
    node.append(warn);
  }

  const stack = logFold("Call stack", crash.call_stack, { open: true });
  if (stack) node.append(stack);

  for (const [label, lines] of [
    ["Crash 직전 단서 로그", crash.pre_context],
    ["주변 로그", crash.cross_context_logs],
  ]) {
    const block = logFold(label, lines);
    if (block) node.append(block);
  }

  if (!crash.cross_context_logs.length && crash.trigger) {
    const trigger = fold("Crash trigger 원문");
    trigger.append(el("pre", null, crash.trigger));
    node.append(trigger);
  }

  return node;
}

function nativeCrash(crash) {
  const node = fold(`[${crash.time}] ${crash.process} — ${crash.signal}`);
  node.append(el("p", "card-note", `Abort message: ${crash.abort_message}`));

  if (crash.callstack.length) {
    node.append(el("h4", "sub-head", "Native callstack"));
    node.append(frameTable(crash.callstack));
  }
  const logs = logFold("주변 로그", crash.cross_context_logs);
  if (logs) node.append(logs);

  return node;
}

function eventListCard(events, render, emptyText) {
  return (series, panel) => {
    const items = events(series);
    if (!items.length) {
      panel.note(emptyText);
      return;
    }
    const wrap = el("div", "stack");
    for (const item of items) wrap.append(render(item));
    panel.content(wrap);
  };
}

function systemEventsCard(series, panel) {
  const blocks = [];
  const push = (title, node) => blocks.push(el("h3", "sub-head", title), node);

  if (series.system_kills.length) {
    push(`시스템 강제 종료(am_kill) ${series.system_kills.length}건`, frameTable(series.system_kills));
  }
  if (series.system_wtf.total) {
    push(`시스템 이상 징후(am_wtf) ${series.system_wtf.total}건`, frameTable(series.system_wtf.by_process));
    const recent = fold(`최근 ${series.system_wtf.recent_count}건 상세`);
    recent.append(frameTable(series.system_wtf.recent));
    blocks.push(recent);
  }

  const binder = series.binder;
  if (binder.status === "ok") {
    for (const spam of binder.spam) {
      const box = el("div", "alert");
      box.append(el("p", null, `[${spam.time}] Binder Oneway Spam — ${spam.desc}`));
      blocks.push(box);
      const raw = fold("커널 로그 원문");
      raw.append(el("pre", null, spam.raw));
      blocks.push(raw);
    }
    if (binder.events.length) {
      const truncated = binder.event_count > binder.display_cap;
      push(`Binder 지연 · 실패 ${binder.event_count}건` + (truncated ? ` (최근 ${binder.display_cap}건 표시)` : ""),
           frameTable(binder.events));
    }
    if (binder.signals.length || binder.checklist.length) {
      const extra = fold("Binder 관련 추가 요약");
      if (binder.signals.length) extra.append(frameTable(binder.signals));
      for (const item of binder.checklist) extra.append(el("p", "card-note", `· ${item}`));
      blocks.push(extra);
    }
  }

  if (!blocks.length) {
    panel.note("시스템 강제 종료나 Binder 이벤트가 없습니다.");
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
    queueMicrotask(() => drawPlot(plot, [barTrace("Proxy 객체", rows.map((r) => r.Count), rows.map((r) => r.Class),
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
      })));
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
  { chart: "nitz", title: "NITZ 타임존 변동", sub: "망이 알려준 시간대", render: nitzCard },
  { chart: "binder-proxy", title: "Binder Proxy 현황", sub: "인터페이스별 Proxy 객체 수", render: proxyCard },
  { chart: "crash", title: "시스템 이벤트", sub: "강제 종료 · 이상 징후 · Binder", render: systemEventsCard },
  {
    chart: "crash", title: "ANR", sub: "펼치면 callstack 과 직전 로그까지", wide: true,
    render: eventListCard((series) => series.anr_events, anrEvent, "ANR 이벤트가 없습니다."),
  },
  {
    chart: "crash", title: "Crash / FATAL EXCEPTION", sub: "펼치면 예외와 call stack 전체", wide: true,
    render: eventListCard((series) => series.java_crashes, javaCrash, "Crash / FATAL 이벤트가 없습니다."),
  },
  {
    chart: "crash", title: "Native Crash", sub: "signal 과 abort message", wide: true,
    render: eventListCard((series) => series.native_crashes, nativeCrash, "Native crash 가 없습니다."),
  },
];

export async function renderBoot(mount, sourceFile) {
  const band = section("부팅 시퀀스");
  mount.append(band.wrap);

  const panels = CARDS.map((spec) => {
    const panel = card(spec.title, spec.sub);
    // Log dumps need the whole row; charts sit side by side.
    if (spec.wide) panel.section.classList.add("wide");
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
