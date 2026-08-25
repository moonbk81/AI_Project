// 파일 · 분석 — 적재된 로그를 고르고, 새 로그를 올려 분석을 돌린다.

import { api } from "../api.js";
import { el, fmt, tile, tileRow } from "../viz.js";

const POLL_INTERVAL_MS = 2000;
const JOB_DONE = new Set(["done", "error"]);

/** A card with no plot: title, optional subtitle, then whatever is appended. */
function cardShell(title, subtitle) {
  const panel = el("section", "card");
  const head = el("div", "card-head");
  head.append(el("h2", null, title), el("span", "grow"));
  panel.append(head);
  if (subtitle) panel.append(el("p", "card-sub", subtitle));
  return panel;
}

function ingestedCard(files, activeFile, onPick) {
  const panel = cardShell("적재된 로그", "분석 대상으로 고르면 모든 화면이 그 파일을 봅니다.");

  if (!files.length) {
    panel.append(el("div", "empty", "적재된 로그가 없습니다. 아래에서 파일을 올려 분석하세요."));
    return panel;
  }

  const list = el("div", "stack");
  for (const file of files) {
    const row = el("div", "row");
    row.append(el("span", "row-name" + (file === activeFile ? " active" : ""), file));

    const pick = el("button", null, file === activeFile ? "보는 중" : "이 파일 보기");
    pick.type = "button";
    pick.disabled = file === activeFile;
    pick.addEventListener("click", () => onPick(file));

    row.append(el("span", "grow"), pick);
    list.append(row);
  }
  panel.append(list);
  return panel;
}

function uploadCard(onStarted) {
  const panel = cardShell("새 로그 분석", "여러 개를 한 번에 올릴 수 있습니다. 분석은 순서대로 처리됩니다.");

  const input = el("input");
  input.type = "file";
  input.multiple = true;

  const queue = el("div", "stack");
  const start = el("button", "primary", "분석 및 DB 적재 시작");
  start.type = "button";
  start.disabled = true;

  const status = el("p", "card-note");
  let picked = [];

  const drawQueue = () => {
    queue.replaceChildren();
    start.disabled = picked.length === 0;

    for (const [index, file] of picked.entries()) {
      const row = el("div", "row");
      row.append(el("span", "row-name", file.name),
                 el("span", "row-meta", `${(file.size / 1024 / 1024).toFixed(1)} MB`));

      const drop = el("button", null, "✕");
      drop.type = "button";
      drop.title = "대기열에서 제거";
      drop.addEventListener("click", () => {
        picked.splice(index, 1);
        drawQueue();
      });

      row.append(el("span", "grow"), drop);
      queue.append(row);
    }
  };

  input.addEventListener("change", () => {
    picked = picked.concat([...input.files]);
    input.value = "";
    drawQueue();
  });

  start.addEventListener("click", async () => {
    start.disabled = true;
    status.textContent = "업로드 중...";
    try {
      const { job_id: jobId } = await api.analyze(picked);
      picked = [];
      drawQueue();
      status.textContent = "";
      onStarted(jobId);
    } catch (error) {
      console.error(error);
      status.textContent = String(error.message || error);
      start.disabled = false;
    }
  });

  panel.append(input, queue, start, status);
  return panel;
}

function jobCard() {
  const panel = cardShell("분석 작업");
  const body = el("div", "stack");
  panel.append(body);

  const draw = (jobs) => {
    body.replaceChildren();
    if (!jobs.length) {
      body.append(el("div", "empty", "실행 중이거나 최근에 끝난 작업이 없습니다."));
      return;
    }

    for (const job of jobs) {
      const row = el("div", "job");
      row.append(el("span", "row-name", job.current_file || job.job_id.slice(0, 8)));
      row.append(el("span", "row-meta", job.message || job.status));

      const bar = el("div", "bar");
      const fill = el("div", "bar-fill");
      fill.style.width = `${Math.max(2, job.progress || 0)}%`;
      if (job.status === "error") fill.classList.add("error");
      if (job.status === "done") fill.classList.add("done");
      bar.append(fill);
      row.append(bar);

      if (job.error) row.append(el("p", "card-note", job.error));
      body.append(row);
    }
  };

  return { panel, draw };
}

export async function renderFiles(mount, sourceFile, ctx) {
  const wrap = el("section", "band");
  wrap.append(el("h2", "band-title", "파일 · 분석"));
  const grid = el("div", "grid");
  wrap.append(grid);
  mount.append(wrap);

  const files = await api.files().catch(() => []);
  wrap.insertBefore(tileRow([
    tile("적재된 로그", fmt.count(files.length), "개"),
    tile("보는 중", sourceFile || "-"),
  ]), grid);

  grid.append(ingestedCard(files, sourceFile, (file) => ctx.setSourceFile(file)));

  const jobs = jobCard();
  grid.append(uploadCard(() => refreshJobs()), jobs.panel);

  let timer = null;
  const refreshJobs = async () => {
    const running = await api.jobs().catch(() => []);
    jobs.draw(running.slice(0, 5));

    const active = running.some((job) => !JOB_DONE.has(job.status));
    clearTimeout(timer);
    if (active) {
      timer = setTimeout(refreshJobs, POLL_INTERVAL_MS);
    } else if (running.some((job) => job.status === "done")) {
      ctx.onFilesChanged();
    }
  };
  ctx.onLeave(() => clearTimeout(timer));
  refreshJobs();

  const danger = cardShell("위험 구역", "Vector DB 의 모든 적재 내용을 지웁니다. 되돌릴 수 없습니다.");

  const reset = el("button", "danger", "전체 DB 초기화");
  reset.type = "button";
  const resetNote = el("p", "card-note");
  reset.addEventListener("click", async () => {
    if (!window.confirm("적재된 로그를 모두 삭제합니다. 계속할까요?")) return;
    reset.disabled = true;
    resetNote.textContent = "초기화 중...";
    try {
      await api.resetDb();
      resetNote.textContent = "초기화했습니다.";
      ctx.onFilesChanged();
    } catch (error) {
      resetNote.textContent = String(error.message || error);
    } finally {
      reset.disabled = false;
    }
  });
  danger.append(reset, resetNote);
  grid.append(danger);
}
