// 부팅 — 부팅 타이밍과 그 과정에서 터진 것들.

import { api } from "../api.js";
import {
  axis, barTrace, baseLayout, card, drawPlot, el, fmt, frameTable, lineTrace, section,
  sectionAnalysisQuestion, seriesColors, sequentialRamp, stepColor, table, tile, tileRow, token,
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

function anrTriage(anr) {
  const triage = anr.triage;
  if (!triage) return null;

  const wrap = el("div", "triage");
  const head = el("div", "triage-head");
  head.append(el("h4", null, "ANR 1차 진단"));
  if (triage.primary_signal) head.append(el("span", "triage-primary", triage.primary_signal));
  wrap.append(head);

  const facts = el("dl", "triage-facts");
  for (const fact of triage.facts || []) {
    facts.append(el("dt", null, fact.label));
    facts.append(el("dd", null, fact.value || "-"));
  }
  wrap.append(facts);

  const focus = el("div", "triage-focus");
  if (triage.main_thread?.top_frame && triage.main_thread.top_frame !== "Unknown") {
    const item = el("div", "triage-focus-item");
    item.append(el("strong", null, "Main thread"));
    item.append(el("code", null, triage.main_thread.check_target || triage.main_thread.top_frame));
    item.append(el("span", null, triage.main_thread.top_frame));
    focus.append(item);
  }
  if (triage.lock_owner?.top_frame && triage.lock_owner.top_frame !== "Unknown") {
    const item = el("div", "triage-focus-item");
    item.append(el("strong", null, `Lock owner TID ${triage.lock_owner.tid || "-"}`));
    item.append(el("code", null, triage.lock_owner.check_target || triage.lock_owner.top_frame));
    item.append(el("span", null, triage.lock_owner.top_frame));
    focus.append(item);
  }
  if (focus.children.length) wrap.append(focus);

  const signals = el("div", "triage-signals");
  for (const signal of triage.signals || []) {
    const item = el("div", "triage-signal");
    item.append(el("span", "triage-signal-label", signal.label));
    item.append(el("span", `triage-strength ${signal.strength === "강함" ? "strong" : signal.strength === "보조" ? "medium" : "weak"}`, signal.strength));
    item.append(el("p", null, signal.note));
    signals.append(item);
  }
  if (signals.children.length) wrap.append(signals);

  if (triage.next_check) {
    const next = el("div", "triage-next");
    next.append(el("strong", null, "우선 체크"));
    next.append(el("span", null, triage.next_check));
    wrap.append(next);
  }

  return wrap;
}

function anrEvent(anr) {
  const node = fold(`[${anr.time}] ${anr.process} (PID ${anr.pid})`);
  node.append(el("p", "card-note", `사유: ${anr.reason}`));

  if (anr.summary) node.append(summaryChips(anr.summary));
  const triage = anrTriage(anr);
  if (triage) node.append(triage);

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

function crashTriage(crash) {
  const triage = crash.triage;
  if (!triage) return null;

  const wrap = el("div", "triage");
  const head = el("div", "triage-head");
  head.append(el("h4", null, "Crash 1차 진단"));
  if (triage.primary_signal) head.append(el("span", "triage-primary", triage.primary_signal));
  wrap.append(head);

  const facts = el("dl", "triage-facts");
  for (const fact of triage.facts || []) {
    facts.append(el("dt", null, fact.label));
    facts.append(el("dd", null, fact.value || "-"));
  }
  wrap.append(facts);

  if (triage.top_frame && triage.top_frame !== "Unknown") {
    const focus = el("div", "triage-focus");
    const item = el("div", "triage-focus-item");
    item.append(el("strong", null, "Top frame"));
    item.append(el("code", null, triage.check_target || triage.top_frame));
    item.append(el("span", null, triage.top_frame));
    focus.append(item);
    wrap.append(focus);
  }

  const signals = el("div", "triage-signals");
  for (const signal of triage.signals || []) {
    const item = el("div", "triage-signal");
    item.append(el("span", "triage-signal-label", signal.label));
    item.append(el("span", `triage-strength ${signal.strength === "강함" ? "strong" : signal.strength === "보조" ? "medium" : "weak"}`, signal.strength));
    item.append(el("p", null, signal.note));
    signals.append(item);
  }
  if (signals.children.length) wrap.append(signals);

  if (triage.next_check) {
    const next = el("div", "triage-next");
    next.append(el("strong", null, "우선 체크"));
    next.append(el("span", null, triage.next_check));
    wrap.append(next);
  }

  return wrap;
}

function javaCrash(crash) {
  const node = fold(`[${crash.time}] ${crash.process} — ${crash.crash_type}`);

  if (crash.exception_info) {
    const box = el("div", "alert");
    box.append(el("pre", null, crash.exception_info));
    node.append(box);
  }
  if (crash.top_method) node.append(el("p", "card-note", `주요 Method: ${crash.top_method}`));
  const triage = crashTriage(crash);
  if (triage) node.append(triage);

  if (crash.suspects_transaction_too_large) {
    // 예외 자체에 찍힌 것과 주변 로그에서 스쳐간 것은 무게가 다르다.
    const inException = /TransactionTooLargeException/i.test(crash.exception_info || "");
    if (inException) {
      const warn = el("div", "alert");
      warn.append(el("p", null,
        "TransactionTooLargeException: Intent 데이터가 Binder buffer 한계를 넘었습니다."));
      node.append(warn);
    } else {
      node.append(el("p", "card-note",
        "주변 로그에 TransactionTooLargeException 이 있습니다 — 다른 IPC 호출의 실패일 수 있어 이 crash 의 원인으로 보기 어렵습니다."));
    }
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

function nativeCrashTriage(crash) {
  const triage = crash.triage;
  if (!triage) return null;

  const wrap = el("div", "triage");
  const head = el("div", "triage-head");
  head.append(el("h4", null, "Native crash 1차 진단"));
  if (triage.primary_signal) head.append(el("span", "triage-primary", triage.primary_signal));
  wrap.append(head);

  const facts = el("dl", "triage-facts");
  for (const fact of triage.facts || []) {
    facts.append(el("dt", null, fact.label));
    facts.append(el("dd", null, fact.value || "-"));
  }
  wrap.append(facts);

  if (triage.top_frame?.function && triage.top_frame.function !== "Unknown") {
    const focus = el("div", "triage-focus");
    const item = el("div", "triage-focus-item");
    item.append(el("strong", null, `Frame #${triage.top_frame.frame_level || "00"}`));
    item.append(el("code", null, triage.top_frame.library || "-"));
    item.append(el("span", null, triage.top_frame.function));
    focus.append(item);
    wrap.append(focus);
  }

  const signals = el("div", "triage-signals");
  for (const signal of triage.signals || []) {
    const item = el("div", "triage-signal");
    item.append(el("span", "triage-signal-label", signal.label));
    item.append(el("span", `triage-strength ${signal.strength === "강함" ? "strong" : signal.strength === "보조" ? "medium" : "weak"}`, signal.strength));
    item.append(el("p", null, signal.note));
    signals.append(item);
  }
  if (signals.children.length) wrap.append(signals);

  if (triage.next_check) {
    const next = el("div", "triage-next");
    next.append(el("strong", null, "우선 체크"));
    next.append(el("span", null, triage.next_check));
    wrap.append(next);
  }

  return wrap;
}

function nativeCrash(crash) {
  const node = fold(`[${crash.time}] ${crash.process} — ${crash.signal}`);
  node.append(el("p", "card-note", `Abort message: ${crash.abort_message}`));
  const triage = nativeCrashTriage(crash);
  if (triage) node.append(triage);

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

  // 죽음을 먼저 보여 준다. 아래 binder 실패들이 대부분 이것의 결과다.
  if (series.process_deaths.length) {
    push(`프로세스 종료 ${series.process_deaths.length}건 — 아래 Binder 실패의 원인`,
         frameTable(series.process_deaths));
  }
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
  }

  if (!blocks.length) {
    panel.note("표시할 프로세스 종료, 시스템 강제 종료, am_wtf, Binder oneway spam 이벤트가 없습니다.");
    return;
  }
  const wrap = el("div", "stack");
  wrap.append(...blocks);
  panel.content(wrap);
}

function proxyCard(series, panel) {
  const leakItems = series.items.filter((histogram) => histogram.is_leak);
  if (!leakItems.length) {
    panel.note("임계치를 초과한 Binder Proxy histogram이 없습니다.");
    return;
  }

  const wrap = el("div", "stack");
  const colors = seriesColors();

  leakItems.forEach((histogram, index) => {
    const killCount = Number(histogram.related_too_many_binders_kill_count || 0);
    const wtfCount = Number(histogram.related_wtf_count || 0);
    wrap.append(tileRow([
      tile("최대 의심 객체", histogram.top_descriptor || "Unknown", "", histogram.suspected_cause),
      tile("임계치 대비", `${histogram.threshold_ratio || 0}x`, "", `${fmt.count(histogram.max_count)} / ${fmt.count(histogram.threshold)}개`,
           (histogram.threshold_ratio || 0) >= 5 ? "critical" : "warn"),
      tile("연관 am_kill", fmt.count(killCount), "건",
           killCount ? "Too many Binders sent to SYSTEM" : "동반 없음",
           killCount ? "critical" : "good"),
      tile("연관 am_wtf", fmt.count(wtfCount), "건",
           (histogram.related_wtf_processes || []).slice(0, 2).join(", ") || "동반 없음",
           wtfCount ? "warn" : "good"),
    ]));

    wrap.append(el("h3", "sub-head",
      `[${histogram.time}] 최대 ${fmt.count(histogram.max_count)}개 — 임계치 초과`));

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

const NITZ_ZONE_NAMES = new Map([
  [9, "Asia/Seoul · Asia/Tokyo"],
  [8, "Asia/Shanghai · Asia/Singapore"],
  [7, "Asia/Bangkok"],
  [5.5, "Asia/Kolkata"],
  [4, "Asia/Dubai"],
  [3, "Europe/Moscow"],
  [2, "Europe/Paris"],
  [1, "Europe/London"],
  [0, "Etc/UTC"],
  [-4, "America/Sao_Paulo"],
  [-5, "America/New_York"],
  [-8, "America/Los_Angeles"],
  [-10, "Pacific/Honolulu"],
]);

function nitzOffsetValue(point) {
  const match = String(point.offset_label || "").match(/^UTC([+-]?\d+(?:\.\d+)?)$/);
  return match ? Number(match[1]) : Number.NaN;
}

function nitzZoneName(point) {
  return NITZ_ZONE_NAMES.get(nitzOffsetValue(point)) || point.region || point.offset_label;
}

function nitzRegionRows(geo) {
  return (geo || []).map((point) => [
    point.offset_label,
    nitzZoneName(point),
    point.region,
    `${fmt.count(point.count)}회`,
  ]);
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
  const wrap = el("div", "nitz-grid");
  const timeline = el("div");
  const mapPane = el("div");
  const timelinePlot = el("div", "plot");
  const mapPlot = el("div", "plot nitz-map");
  timeline.append(el("h3", "sub-head", "UTC 오프셋 변화"), timelinePlot);
  mapPane.append(el("h3", "sub-head", "타임존 기반 예상 지역"));

  if ((series.geo || []).length) {
    mapPane.append(mapPlot);
  } else {
    mapPane.append(el("div", "empty compact", "표시할 지도 좌표가 없습니다."));
  }
  mapPane.append(table(["오프셋", "대표 TZ", "예상 지역", "수신"], nitzRegionRows(series.geo), 20));
  wrap.append(timeline, mapPane);

  if ((series.changes || []).length) {
    const changes = fold("NITZ 변경 상세");
    changes.append(frameTable(series.changes));
    wrap.append(changes);
  }

  panel.content(wrap);

  queueMicrotask(() => drawPlot(timelinePlot, [lineTrace("UTC 오프셋", series.offsets.map((p) => p.log_time_dt),
    series.offsets.map((p) => p.offset), colors[2], {
      line: { width: 2, shape: "hv", color: colors[2] },
      hovertemplate: "UTC%{y:+g}<br>%{x}<extra></extra>",
    })], baseLayout({
      margin: { l: 64, r: 24, t: 8, b: 44 },
      yaxis: axis({ title: { text: "UTC 오프셋", font: { size: 11 } } }),
    })));

  if ((series.geo || []).length) {
    queueMicrotask(() => drawPlot(mapPlot, [{
      type: "scattergeo",
      mode: "markers+text",
      lat: series.geo.map((point) => point.lat),
      lon: series.geo.map((point) => point.lon),
      text: series.geo.map(nitzZoneName),
      textposition: "top center",
      customdata: series.geo.map((point) => [point.offset_label, point.region, point.count]),
      marker: {
        size: series.geo.map((point) => Math.min(30, 10 + Number(point.count || 0) * 5)),
        color: colors[0],
        opacity: 0.86,
        line: { width: 2, color: token("--surface-1") },
      },
      hovertemplate: "<b>%{text}</b><br>%{customdata[0]}<br>%{customdata[1]}<br>%{customdata[2]}회<extra></extra>",
    }], baseLayout({
      height: 300,
      margin: { l: 6, r: 6, t: 4, b: 4 },
      geo: {
        projection: { type: "natural earth" },
        showland: true,
        landcolor: token("--plane"),
        showocean: true,
        oceancolor: "rgba(42,120,214,0.08)",
        showcountries: true,
        countrycolor: token("--grid"),
        coastlinecolor: token("--baseline"),
        bgcolor: "rgba(0,0,0,0)",
      },
    })));
  }
}

const CARDS = [
  {
    chart: "boot",
    title: "부팅 지연 구간",
    sub: "가장 오래 걸린 이벤트",
    prompt: "Boot milestone과 Delta_ms가 큰 이벤트를 보고 부팅 병목 구간과 후속 영향 가능성을 분석해줘.",
    render: bootCard,
  },
  {
    chart: "nitz",
    title: "NITZ 타임존 변동",
    sub: "망이 알려준 시간대",
    prompt: "NITZ offset 변경, ping-pong 여부, 예상 지역 변화를 보고 망 시간 정보가 불안정했는지 분석해줘.",
    render: nitzCard,
  },
  {
    chart: "binder-proxy",
    title: "Binder Proxy 누수 의심",
    sub: "인터페이스별 proxy 객체 수와 임계치 초과 여부",
    prompt: "Binder proxy histogram의 max count, descriptor, leak threshold 초과 여부를 보고 특정 인터페이스의 proxy 객체 누수 가능성을 분석해줘.",
    render: proxyCard,
  },
  {
    chart: "crash",
    title: "프로세스 종료·시스템 이상 징후",
    sub: "프로세스 사망 · am_kill · am_wtf · Binder oneway spam",
    prompt: "죽은 프로세스와 그 시각의 tombstone, 그리고 함께 내려간 바인더 서비스를 이어서 무엇이 원인이고 무엇이 결과인지 정리해줘. am_kill/am_wtf/Binder oneway spam 도 함께 봐줘.",
    render: systemEventsCard,
  },
  {
    chart: "crash", title: "ANR", sub: "펼치면 callstack 과 직전 로그까지", wide: true,
    prompt: "ANR별 main thread callstack, lock contention, binder transaction, 직전 logcat/CPU/I/O 단서를 근거로 멈춤 원인을 분석해줘.",
    render: eventListCard((series) => series.anr_events, anrEvent, "ANR 이벤트가 없습니다."),
  },
  {
    chart: "crash", title: "Crash / FATAL EXCEPTION", sub: "펼치면 예외와 call stack 전체", wide: true,
    prompt: "Exception 정보, top method, trigger, call stack, crash 직전 단서 로그를 근거로 Java crash 원인을 분석해줘.",
    render: eventListCard((series) => series.java_crashes, javaCrash, "Crash / FATAL 이벤트가 없습니다."),
  },
  {
    chart: "crash", title: "Native Crash", sub: "signal 과 abort message", wide: true,
    prompt: "Signal, abort message, native callstack, 주변 로그를 근거로 native crash 원인 후보를 정리해줘.",
    render: eventListCard((series) => series.native_crashes, nativeCrash, "Native crash 가 없습니다."),
  },
];

export async function renderBoot(mount, sourceFile, ctx) {
  const band = section("시스템 진단");
  mount.append(band.wrap);

  const panels = CARDS.map((spec) => {
    const panel = card(spec.title, spec.sub);
    if (ctx?.startChat) {
      panel.action("LLM 분석 요청", () => ctx.startChat(sectionAnalysisQuestion("시스템 진단 탭", spec, sourceFile)), "primary");
    }
    // Log dumps need the whole row; charts sit side by side.
    if (spec.wide) panel.section.classList.add("wide", "event-card");
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
