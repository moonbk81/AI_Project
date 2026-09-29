// 실시간 단말 — adb 로 이 PC 에 꽂힌 RTOS 워치를 몇 초마다 본다.
// 워치에는 ps/free/uptime/logcat 만 보낸다 (backend/live_device.py). 모니터는 서버에서 돌아서
// 탭을 떠나도 로그를 계속 받고, 워치가 죽으면 그 직전까지의 로그가 파일로 남는다.

import { api } from "../api.js";
import {
  axis, baseLayout, card, el, field, frameTable, lineTrace, panel, section, seriesColors, tile, tileRow, token,
} from "../viz.js";

const POLL_MS = 2000;
const TREND_TASKS = 3;
const pct = (value) => (value === null || value === undefined ? "-" : `${value}%`);
const clock = (value) => (value ? String(value).split(" ").pop() : "-");
const LEVEL_MARK = { critical: "✗", warning: "!", info: "·" };

// 색은 계열(태스크)을 따라간다. 순위가 바뀌어도 이미 받은 색은 그대로 두고, 빠진 태스크의 자리만 새로 준다.
function colorSlots() {
  const assigned = new Map();
  return (pids) => {
    for (const pid of [...assigned.keys()]) if (!pids.includes(pid)) assigned.delete(pid);
    const used = new Set(assigned.values());
    for (const pid of pids) {
      if (assigned.has(pid)) continue;
      const free = [1, 2, 3].find((slot) => !used.has(slot));
      assigned.set(pid, free);
      used.add(free);
    }
    return assigned;
  };
}

function trendPids(series) {
  const sums = new Map();
  for (const s of series) {
    for (const [pid, cpu] of Object.entries(s.top || {})) sums.set(pid, (sums.get(pid) || 0) + cpu);
  }
  return [...sums.entries()].sort((a, b) => b[1] - a[1]).slice(0, TREND_TASKS).map(([pid]) => pid);
}

function drawTrend(trend, tilesHost, status, slotsFor) {
  const latest = status.latest;
  const heap = latest?.heaps?.[0];
  const stackWarn = latest?.stack_warn || [];
  tilesHost.replaceChildren(tileRow([
    tile("전체 CPU (100 − Idle)", pct(latest?.total), "",
         latest ? `태스크 ${latest.task_count}개 · 모니터 ps ${pct(latest.monitor_overhead)}` : "",
         latest?.total >= 90 ? "critical" : (latest?.total >= 70 ? "warning" : "good")),
    tile("힙 사용", pct(heap?.used_pct), "",
         heap ? `최대 ${pct(heap.max_used_pct)} · ${Math.round(heap.used / 1024).toLocaleString()} / ${Math.round(heap.total / 1024).toLocaleString()} KB` : "",
         heap?.used_pct >= 90 ? "critical" : "good"),
    tile("스택 위험 태스크", stackWarn.length, "개",
         stackWarn.length ? stackWarn.slice(0, 2).map((t) => `${t.name} ${t.stack_filled}%`).join(", ") : "80% 넘는 태스크 없음",
         stackWarn.length ? "warning" : "good"),
    tile("uptime", latest?.uptime?.uptime_min ?? "-", "분",
         latest?.uptime?.load ? `load ${latest.uptime.load.join(" / ")}` : ""),
  ]));

  const series = status.series || [];
  if (!series.length) {
    trend.note("아직 받은 표본이 없습니다.");
    return;
  }
  const x = series.map((s) => s.time);
  const colors = seriesColors();
  const pids = trendPids(series);
  const slots = slotsFor(pids);
  const names = {};
  for (const s of series) Object.assign(names, s.names || {});
  const traces = [lineTrace("전체", x, series.map((s) => s.total), colors[0], {
    hovertemplate: "전체 %{y}%<extra></extra>",
  })];
  for (const pid of pids) {
    const label = `${names[pid] || pid} (${pid})`;
    traces.push(lineTrace(label, x, series.map((s) => s.top?.[pid] ?? 0), colors[slots.get(pid)], {
      hovertemplate: `${label} %{y}%<extra></extra>`,
    }));
  }
  trend.draw(traces, baseLayout({
    margin: { l: 56, r: 24, t: 36, b: 44 },
    showlegend: true,
    hovermode: "x unified",
    xaxis: axis({ type: "date", tickformat: "%H:%M:%S" }),
    yaxis: axis({ range: [0, 105], ticksuffix: "%" }),
    shapes: [{
      type: "line", xref: "paper", x0: 0, x1: 1, yref: "y", y0: 90, y1: 90,
      line: { width: 1, dash: "dot", color: token("--baseline") },
    }],
  }), frameTable(series.slice().reverse().map((s) => ({
    시각: clock(s.time), 전체: pct(s.total), "힙": pct(s.heap_used_pct),
    "상위 태스크": Object.entries(s.top || {}).slice(0, 5).map(([pid, cpu]) => `${s.names[pid]}(${pid}) ${cpu}%`).join(", "),
  })), ["시각", "전체", "힙", "상위 태스크"]));
}

function tasksTable(latest) {
  return frameTable((latest?.tasks || []).map((t) => ({
    PID: t.pid, 이름: t.name, "CPU(%)": t.cpu, PRI: t.pri, 상태: t.state, 대기: t.event || "-",
    스택: `${t.stack_filled}%${t.stack_warn ? " !" : ""}`,
  })), ["PID", "이름", "CPU(%)", "PRI", "상태", "대기", "스택"]);
}

function stackTable(latest) {
  const rows = latest?.stack_warn || [];
  if (!rows.length) return el("p", "table-note", "스택을 80% 넘게 쓴 태스크가 없습니다.");
  return frameTable(rows.map((t) => ({
    PID: t.pid, 이름: t.name, "사용/크기(B)": `${t.stack_used} / ${t.stack}`, 사용률: `${t.stack_filled}%`, 상태: t.state,
  })), ["PID", "이름", "사용/크기(B)", "사용률", "상태"]);
}

function eventsTable(events) {
  if (!events?.length) return el("p", "table-note", "이벤트가 없습니다.");
  return frameTable(events.map((e) => ({
    시각: clock(e.time), "": LEVEL_MARK[e.level] || "", 내용: e.text,
  })), ["시각", "", "내용"]);
}

function analysisBlock(analysis) {
  const wrap = el("div", "stack");
  const oem = analysis?.oem_kpi || {};
  const calls = analysis?.call_kpi || {};
  wrap.append(tileRow([
    tile("OEM_HOOK_RAW", oem.request_count ?? 0, "건",
         `문제 ${oem.problem_count ?? 0} · 모뎀 응답 ${oem.modem_answered_count ?? 0}`,
         oem.problem_count ? "critical" : "good"),
    tile("콜", calls.call_count ?? 0, "건",
         `연결 ${calls.connected_count ?? 0} · 끊김 ${calls.broken_count ?? 0}`, calls.broken_count ? "critical" : "good"),
  ]));
  if ((analysis?.oem_recent || []).length) {
    wrap.append(frameTable(analysis.oem_recent.slice().reverse().map((r) => ({
      token: r.token, 시각: clock(r.req_time), funcId: r.func_id ? `${r.func_id} ${r.func_name}` : "-",
      IPC: r.ipc || "-", 결과: r.status,
    })), ["token", "시각", "funcId", "IPC", "결과"]));
  }
  if ((analysis?.call_recent || []).length) {
    wrap.append(frameTable(analysis.call_recent.slice().reverse().map((c) => ({
      방향: c.direction, 시작: clock(c.start_time), 결과: c.status, "끊긴 지점": c.broken || "-",
    })), ["방향", "시작", "결과", "끊긴 지점"]));
  }
  return wrap;
}

export async function renderLive(mount, _sourceFile, ctx) {
  const band = section("실시간 단말");
  mount.append(band.wrap);

  // ---- 조작 ----
  const control = panel("adb 연결", "이 PC 에 USB 로 꽂힌 워치. ps/free/uptime 과 logcat 만 읽는다.");
  control.section.classList.add("wide");
  band.grid.append(control.section);
  const deviceSelect = el("select");
  const intervalSelect = el("select");
  for (const sec of [3, 5, 10]) {
    const option = el("option", null, `${sec}초`);
    option.value = String(sec);
    if (sec === 5) option.selected = true;
    intervalSelect.append(option);
  }
  const refreshButton = el("button", null, "단말 다시 찾기");
  const startButton = el("button", "primary", "시작");
  const stopButton = el("button", null, "중지");
  const analyzeButton = el("button", null, "지금까지 로그 분석");
  for (const button of [refreshButton, startButton, stopButton, analyzeButton]) button.type = "button";
  const row = el("div", "live-controls");
  row.append(field("단말", deviceSelect), field("간격", intervalSelect), refreshButton, startButton, stopButton, analyzeButton);
  const statusLine = el("p", "table-note", "");
  control.body.append(row, statusLine);

  // ---- 카드 ----
  const trend = card("CPU 추이", "전체와 최근 구간 평균 상위 3 태스크. 점선은 90%");
  trend.section.classList.add("wide");
  const tilesHost = el("div");
  trend.prepend(tilesHost);
  const tasks = card("태스크 (최근 ps)", "CPU 순 상위 15개. 스택에 ! 는 80% 이상");
  const stack = card("스택 위험", "스택을 80% 넘게 쓴 태스크 — 넘치면 크래시로 이어진다");
  const events = card("이벤트", "연결·재부팅·CPU 90%·스택 경고");
  const analysis = card("로그 분석 (받은 로그 기준)", "OEM_HOOK_RAW 와 콜 흐름을 받은 줄로 계속 다시 분석");
  const logs = card("로그", "최근 줄. 검색어를 넣으면 받은 로그 전체에서 거른다");
  logs.section.classList.add("wide");
  const grep = el("input");
  grep.placeholder = "검색 (예: OEM_HOOK_RAW, RIL_CPP, CPU:)";
  grep.style.width = "min(480px, 100%)";
  const logPre = el("pre", "log-tail");
  logs.prepend(grep);
  for (const c of [trend, tasks, stack, events, analysis, logs]) band.grid.append(c.section);
  const slotsFor = colorSlots();

  const loadDevices = async () => {
    const devices = await api.liveDevices().catch(() => []);
    deviceSelect.replaceChildren();
    if (!devices.length) {
      deviceSelect.append(el("option", null, "연결된 단말 없음"));
      startButton.disabled = true;
      return;
    }
    for (const d of devices) {
      const option = el("option", null, `${d.serial} (${d.state}${d.device ? `, ${d.device}` : ""})`);
      option.value = d.serial;
      deviceSelect.append(option);
    }
    startButton.disabled = false;
  };

  let timer = null;
  let left = false;
  const poll = async () => {
    clearTimeout(timer);
    try {
      const status = await api.liveStatus(grep.value.trim());
      startButton.disabled = status.running || deviceSelect.value === "";
      stopButton.disabled = !status.running;
      analyzeButton.disabled = !status.line_count;
      statusLine.textContent = status.running
        ? `${status.connected ? "연결됨" : "연결 끊김"} · ${status.serial} · ${status.interval}초 간격 · `
          + `로그 ${status.log_streaming ? "수신 중" : "끊김"} ${status.line_count.toLocaleString()}줄 · 저장: ${status.capture_path}`
        : (status.capture_path ? `중지됨 · 마지막 저장: ${status.capture_path}` : "모니터가 꺼져 있습니다.");
      drawTrend(trend, tilesHost, status, slotsFor);
      tasks.content(tasksTable(status.latest));
      stack.content(stackTable(status.latest));
      events.content(eventsTable(status.events));
      analysis.content(analysisBlock(status.analysis));
      logPre.textContent = (status.log_tail || []).join("\n");
      logs.content(logPre);
      logPre.scrollTop = logPre.scrollHeight;
    } catch (error) {
      console.error("live status", error);
      statusLine.textContent = `상태를 불러오지 못했습니다: ${error.message}`;
    }
    if (!left) timer = setTimeout(poll, POLL_MS);
  };

  refreshButton.addEventListener("click", loadDevices);
  startButton.addEventListener("click", async () => {
    try {
      await api.liveStart(deviceSelect.value, Number(intervalSelect.value));
    } catch (error) {
      statusLine.textContent = `시작하지 못했습니다: ${error.message}`;
    }
    poll();
  });
  stopButton.addEventListener("click", async () => {
    await api.liveStop().catch((error) => { statusLine.textContent = `중지하지 못했습니다: ${error.message}`; });
    poll();
  });
  analyzeButton.addEventListener("click", async () => {
    try {
      const job = await api.liveAnalyze();
      statusLine.textContent = `분석을 걸었습니다 (job ${job.job_id.slice(0, 8)}). 진행은 "파일 · 분석" 탭에서 봅니다.`;
    } catch (error) {
      statusLine.textContent = `분석을 걸지 못했습니다: ${error.message}`;
    }
  });
  grep.addEventListener("change", poll);

  // 탭을 떠나면 화면 갱신만 멈춘다. 서버의 모니터는 계속 돈다.
  ctx?.onLeave?.(() => {
    left = true;
    clearTimeout(timer);
  });
  await loadDevices();
  poll();
}
