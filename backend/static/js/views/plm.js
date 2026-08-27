// PLM — 결함을 찾아 첨부 로그를 분석하고, 결과를 코멘트로 남긴다.

import { api } from "../api.js";
import { el, field, fmt, panel, table } from "../viz.js";
import { createDefectCache, defectCacheKey } from "./plm_data.js";

const MOBILE_DIVISION = "25";
const STATUSES = ["Open", "Resolve", "Close"];
const SEARCH_METHODS = ["그룹", "사용자 ID", "PLM 번호"];
const JOB_POLL_MS = 2000;
const JOB_DONE = new Set(["done", "error"]);
// core/log_archive.py 의 ARCHIVE_SUFFIXES 와 같은 목록. 로그는 압축 첨부 안에만
// 들어 있으므로, 고를 수 있는 첨부도 이것들뿐이다.
const ARCHIVE_SUFFIXES = [".zip", ".7z"];

const sizeText = (bytes) => {
  const value = Number(bytes) || 0;
  return value >= 1024 * 1024
    ? `${(value / 1024 / 1024).toFixed(1)} MB`
    : `${(value / 1024).toFixed(1)} KB`;
};

const isArchiveName = (name) =>
  ARCHIVE_SUFFIXES.some((suffix) => String(name || "").toLowerCase().endsWith(suffix));

function input(placeholder, value = "") {
  const node = el("input", "text-input");
  node.type = "text";
  node.placeholder = placeholder || "";
  node.value = value;
  return node;
}

function textarea(placeholder, rows = 4) {
  const node = el("textarea", "text-input");
  node.placeholder = placeholder || "";
  node.rows = rows;
  return node;
}

function select(options, labels) {
  const node = el("select");
  for (const option of options) node.append(new Option(labels ? labels[option] : option, option));
  return node;
}

function radioRow(name, options, onChange, initial) {
  const wrap = el("div", "quick");
  let current = options.includes(initial) ? initial : options[0];
  const buttons = options.map((option) => {
    const button = el("button", option === current ? "chip active" : "chip", option);
    button.type = "button";
    button.addEventListener("click", () => {
      current = option;
      for (const [index, other] of buttons.entries()) {
        other.classList.toggle("active", options[index] === current);
      }
      onChange?.(current);
    });
    wrap.append(button);
    return button;
  });
  return { wrap, get value() { return current; } };
}

export async function renderPlm(mount, sourceFile, ctx) {
  const wrap = el("section", "band");
  wrap.append(el("h2", "band-title", "PLM 결함"));
  const grid = el("div", "grid plm-grid");
  wrap.append(grid);
  mount.append(wrap);

  const state = ctx.plmState;
  state.division = MOBILE_DIVISION;
  state.divisionName = "Mobile";
  if (!state.cache) state.cache = {};
  if (!state.attachmentJobs) state.attachmentJobs = {};
  if (!state.attachmentPicks) state.attachmentPicks = {};
  const cache = createDefectCache(api, state.cache);

  // ------------------------------------------------------------ 로컬 테스트
  // 사내망 밖에서는 PLM 에 닿지 않는다. 이 모드에서는 백엔드가 샘플로 답하고
  // 쓰기(코멘트·등록)는 전송되지 않는다.
  const modeRow = el("div", "row mode-row");
  const modeLabel = el("span", "row-name", "PLM 로컬 테스트 모드");
  const modeNote = el("span", "row-meta", "");
  const modeButton = el("button", null, "확인 중...");
  modeButton.type = "button";
  modeRow.append(modeLabel, el("span", "grow"), modeNote, modeButton);
  wrap.insertBefore(modeRow, grid);

  const drawMode = (enabled) => {
    modeButton.textContent = enabled ? "켜짐 — 끄기" : "꺼짐 — 켜기";
    modeButton.className = enabled ? "chip active" : "chip";
    modeNote.textContent = enabled
      ? "샘플 결함으로 동작합니다. PLM 에는 아무것도 전송되지 않습니다."
      : "실제 사내 PLM 에 연결합니다.";
  };

  modeButton.addEventListener("click", async () => {
    modeButton.disabled = true;
    try {
      const current = await api.plmLocalTest();
      const next = await api.plmSetLocalTest(!current.enabled);
      drawMode(next.enabled);
      state.defects = [];
      state.selected = null;
      state.analysis = null;
      state.attachmentJobs = {};
      state.searchNote = "";
      drawResults();
      clearSelectionViews();
    } catch (error) {
      modeNote.textContent = String(error.message || error);
    } finally {
      modeButton.disabled = false;
    }
  });

  api.plmLocalTest().then((body) => drawMode(body.enabled)).catch(() => drawMode(false));

  // ------------------------------------------------------------------ 검색
  const search = panel("결함 검색", "담당 그룹, Knox ID, PLM 번호로 찾습니다.");
  const status = radioRow("status", STATUSES, (value) => {
    state.status = value;
  }, state.status);
  const method = radioRow("method", SEARCH_METHODS, (value) => {
    state.method = value;
    drawTarget();
  }, state.method);

  const statusHost = el("div");
  const targetHost = el("div");
  const groupPicker = select([]);
  const userInput = input("예: bongki.moon");
  userInput.value = state.user || "";
  userInput.addEventListener("input", () => {
    state.user = userInput.value;
  });
  const defectInput = input("예: P260711-LOCAL01");
  defectInput.value = state.defectCode || "";
  defectInput.addEventListener("input", () => {
    state.defectCode = defectInput.value;
  });
  const searchButton = el("button", "primary", "검색");
  searchButton.type = "button";
  const searchNote = el("p", "card-note");
  searchNote.textContent = state.searchNote || "";

  const drawTarget = () => {
    statusHost.replaceChildren();
    if (method.value !== "PLM 번호") statusHost.append(field("상태", status.wrap));

    if (method.value === "그룹") {
      targetHost.replaceChildren(field("그룹", groupPicker));
    } else if (method.value === "사용자 ID") {
      targetHost.replaceChildren(field("Knox ID", userInput));
    } else {
      targetHost.replaceChildren(field("PLM 번호", defectInput));
    }
  };

  const loadGroups = async () => {
    groupPicker.replaceChildren();
    try {
      const { groups } = await api.plmGroups(state.division);
      const keys = Object.keys(groups);
      if (!keys.length) {
        groupPicker.append(new Option("설정된 그룹이 없습니다", ""));
        return;
      }
      for (const key of keys) groupPicker.append(new Option(groups[key], key));
      groupPicker.value = keys.includes(state.group) ? state.group : keys[0];
      state.group = groupPicker.value;
    } catch (error) {
      groupPicker.append(new Option("그룹을 불러오지 못했습니다", ""));
    }
  };
  groupPicker.addEventListener("change", () => {
    state.group = groupPicker.value;
  });

  search.body.append(
    field("검색 방식", method.wrap),
    statusHost,
    targetHost,
    searchButton,
    searchNote,
  );

  // ------------------------------------------------------------------ 결과
  const results = panel("검색 결과", "행을 선택하면 아래에 상세가 열립니다.");
  const resultsHost = el("div");
  results.body.append(resultsHost);

  const drawResults = () => {
    resultsHost.replaceChildren();
    if (!state.defects.length) {
      resultsHost.append(el("div", "empty", "검색 결과가 없습니다."));
      return;
    }

    const list = el("div", "stack");
    for (const defect of state.defects) {
      const row = el("div", "row");
      const isSelected = Boolean(state.selected)
                        && defect.defectCode === state.selected.defectCode;
      const name = el("span", "row-name" + (isSelected ? " active" : ""),
                      `${defect.defectCode} · ${defect.plmTitle || ""}`.slice(0, 110));
      const meta = el("span", "row-meta", `${defect.plmStatus || "-"} · ${defect.mainOwnerName || "-"}`);

      const open = el("button", null, "열기");
      open.type = "button";
      open.addEventListener("click", () => selectDefect(defect));

      row.append(name, el("span", "grow"), meta, open);
      list.append(row);
    }
    resultsHost.append(list);
  };

  // ------------------------------------------------------------------ 상세
  const detail = panel("결함 상세", "선택된 결함의 내용과 개발자 코멘트");
  const detailHost = el("div", "stack");
  detail.body.append(detailHost);

  const drawDetail = async (defect) => {
    detailHost.replaceChildren(el("div", "empty", "불러오는 중..."));

    const { details, comments } = await cache.detail(state.division, defect);
    const full = details.defects?.[0] || defect;

    detailHost.replaceChildren();
    const link = el("a", "plm-link", full.defectCode);
    link.href = api.plmDefectUrl(full.defectId);
    link.target = "_blank";
    link.rel = "noreferrer";

    const head = el("div", "row");
    head.append(link, el("span", "grow"),
                el("span", "row-meta", `${full.plmStatus || "-"} · ${full.plmPriority || "-"} · ${full.mainOwnerName || "-"}`));
    detailHost.append(head, el("h3", "sub-head", full.plmTitle || ""));

    if (full.content) {
      const fold = el("details", "fold");
      fold.open = true;
      fold.append(el("summary", null, "문제 내용"), el("pre", null, full.content));
      detailHost.append(fold);
    }

    // Comments are ticked to say "analyze this one too", so the fold starts open.
    const humanComments = comments.comments || [];
    const picked = new Set(humanComments.map((_, index) => index));

    if (humanComments.length) {
      const fold = el("details", "fold");
      fold.open = true;
      fold.append(el("summary", null, `개발자 코멘트 ${humanComments.length}건`),
                  el("p", "card-note", "분석에 함께 넘길 코멘트를 고르세요. (AI 가 남긴 코멘트는 이미 제외돼 있습니다)"));

      humanComments.forEach((comment, index) => {
        const block = el("div", "reference");
        const label = el("label", "check");
        const box = el("input");
        box.type = "checkbox";
        box.checked = true;
        box.addEventListener("change", () => (box.checked ? picked.add(index) : picked.delete(index)));

        label.append(box, el("span", null, `${comment.historyUser || "-"} · ${comment.historyDate || "-"}`));
        block.append(label, el("pre", null, comment.comment));
        fold.append(block);
      });
      detailHost.append(fold);
    }

    // ---- 채팅으로 분석
    const analyzeNote = el("p", "card-note");
    const toChat = el("button", "primary", "🚀 채팅으로 분석");
    toChat.type = "button";
    toChat.addEventListener("click", async () => {
      toChat.disabled = true;
      analyzeNote.textContent = "문제 내용을 정리하는 중입니다...";
      try {
        const body = await api.plmAnalysisQuery(
          state.division,
          full.defectCode,
          humanComments
            .filter((_, index) => picked.has(index))
            .map((comment) => ({
              user: comment.historyUser || "",
              date: comment.historyDate || "",
              text: comment.comment || "",
            })),
        );

        if (!body.success) {
          analyzeNote.textContent = body.message || "분석 질의를 만들지 못했습니다.";
          return;
        }
        // The refined text is what actually gets asked; show the original too.
        if (body.original_content && body.original_content !== body.refined_content) {
          const fold = el("details", "fold");
          fold.append(el("summary", null, "정제된 문제 내용 / 원본"),
                      el("pre", null, body.refined_content),
                      el("h4", null, "원본"),
                      el("pre", null, body.original_content));
          detailHost.append(fold);
        }
        ctx.startChat(body.query);
      } catch (error) {
        analyzeNote.textContent = String(error.message || error);
      } finally {
        toChat.disabled = false;
      }
    });

    detailHost.append(toChat, analyzeNote);
  };

  // ------------------------------------------------------------------ 첨부
  const attachments = panel(
    "첨부 파일",
    "고른 압축 파일(ZIP/7z)만 내려받아 안의 LOG 파일을 목록으로 보여 줍니다. 분석할 로그를 골라 주세요.",
  );
  const attachmentHost = el("div", "stack");
  attachments.body.append(attachmentHost);

  const jobTimers = new Set();
  ctx.onLeave(() => {
    for (const timer of jobTimers) clearTimeout(timer);
    jobTimers.clear();
  });

  const attachmentJobKey = (defect, variant = "") =>
    `${defectCacheKey(state.division, defect.defectCode)}${variant}`;

  /** 꺼내지 못해 빠진 로그. 나머지 분석은 그대로 진행된다. */
  const drawSkipped = (job, host) => {
    const skipped = job.skipped_logs || [];
    if (!skipped.length) {
      host.replaceChildren();
      return;
    }
    const fold = el("details", "fold");
    fold.append(el("summary", null, `건너뛴 로그 ${skipped.length}개`));
    const list = el("ul");
    for (const line of skipped) list.append(el("li", null, line));
    fold.append(list);
    host.replaceChildren(fold);
  };

  const drawJobProgress = (job, progressHost) => {
    const bar = el("div", "bar");
    const fill = el("div", "bar-fill");
    fill.style.width = `${Math.max(2, job.progress || 0)}%`;
    fill.classList.toggle("done", job.status === "done");
    fill.classList.toggle("error", job.status === "error");
    bar.append(fill);
    const message = el("p", "card-note", job.display_message || job.error || job.message || job.status || "시작하는 중...");
    const extra = el("div");
    progressHost.replaceChildren(message, bar, extra);
    drawSkipped(job, extra);
    return { fill, message, extra };
  };

  /** 분석이 끝나면 그 로그를 활성 파일로 삼는다. 화면은 그대로 둔다. */
  const activateAnalyzed = async (job, message) => {
    if (!job.current_file) return;
    const active = await ctx.filesChanged({ select: job.current_file });
    message.textContent = active === job.current_file
      ? `${job.message} — '${job.current_file}' 을 활성 파일로 설정했습니다. 대시보드에서 볼 수 있습니다.`
      : `${job.message} — '${job.current_file}'`;
  };

  const followJob = (jobId, { progressHost, defect, variant = "", setBusy, onDone }) => {
    const key = attachmentJobKey(defect, variant);
    const stored = state.attachmentJobs[key] || {};
    // 새 잡이면 지난 잡의 흔적(activated, 메시지, 후보 목록)을 물려받지 않는다.
    state.attachmentJobs[key] = {
      ...(stored.job_id === jobId ? stored : {}),
      job_id: jobId,
      division: state.division,
      defect_code: defect.defectCode,
    };

    const { fill, message, extra } = drawJobProgress(state.attachmentJobs[key], progressHost);
    setBusy?.(!JOB_DONE.has(state.attachmentJobs[key].status));

    const poll = async () => {
      try {
        const job = await api.job(jobId);
        state.attachmentJobs[key] = { ...state.attachmentJobs[key], ...job };
        message.textContent = state.attachmentJobs[key].display_message || job.error || job.message || job.status;
        fill.style.width = `${Math.max(2, job.progress || 0)}%`;
        fill.classList.toggle("done", job.status === "done");
        fill.classList.toggle("error", job.status === "error");
        drawSkipped(job, extra);

        if (!JOB_DONE.has(job.status)) {
          const timer = setTimeout(poll, JOB_POLL_MS);
          jobTimers.add(timer);
          return;
        }

        setBusy?.(false);
        // 한 번 끝난 잡은 화면을 다시 그려도 다시 처리하지 않는다.
        if (job.status === "done" && !state.attachmentJobs[key].activated) {
          state.attachmentJobs[key].activated = true;
          await onDone?.(job, message);
          state.attachmentJobs[key].display_message = message.textContent;
        }
      } catch (error) {
        message.textContent = String(error.message || error);
        state.attachmentJobs[key] = { ...state.attachmentJobs[key], status: "error", error: message.textContent };
        fill.classList.add("error");
        setBusy?.(false);
      }
    };
    poll();
  };

  const drawAttachments = async (defect) => {
    attachmentHost.replaceChildren(el("div", "empty", "불러오는 중..."));
    const listing = await cache.attachments(state.division, defect);
    const files = listing.files || [];

    attachmentHost.replaceChildren();
    if (!files.length) {
      attachmentHost.append(el("div", "empty", "첨부 파일이 없습니다."));
      return;
    }

    // 1단계: 열어 볼 첨부 고르기. 첨부가 여럿인 결함에서 전부 내려받아 여는 데
    // 걸리던 시간이 그대로 대기 시간이었다.
    const scanKey = attachmentJobKey(defect, "::scan");
    const picked = new Set(state.attachmentPicks[scanKey] || []);
    const boxes = [];
    const logHost = el("div", "stack");
    const progressHost = el("div");
    const scan = el("button", "primary", "로그 파일 찾기");
    scan.type = "button";
    let busy = false;
    let selectAll = null;

    const refresh = () => {
      scan.textContent = picked.size
        ? `선택한 첨부 ${picked.size}개에서 로그 파일 찾기`
        : "로그 파일 찾기 (첨부를 선택하세요)";
      scan.disabled = busy || picked.size === 0;
      if (selectAll) {
        selectAll.checked = boxes.length > 0 && picked.size === boxes.length;
        selectAll.indeterminate = picked.size > 0 && picked.size < boxes.length;
      }
      state.attachmentPicks[scanKey] = [...picked];
    };

    for (const file of files) {
      const row = el("div", "row");
      const fileId = String(file.fileId);
      const analyzable = isArchiveName(file.title) && file.docId && file.fileId;

      if (analyzable) {
        const box = el("input");
        box.type = "checkbox";
        box.title = "이 압축 파일 안을 훑는다";
        box.checked = picked.has(fileId);
        box.addEventListener("change", () => {
          if (box.checked) picked.add(fileId);
          else picked.delete(fileId);
          refresh();
        });
        boxes.push({ box, fileId });
        row.append(box);
      } else {
        // 자리를 맞춰 두면 압축이 아닌 첨부도 목록에서 밀리지 않는다.
        row.append(el("span", "row-pick"));
      }

      row.append(el("span", "row-name", file.title || "-"),
                 el("span", "grow"),
                 el("span", "row-meta", file.fileSize ? sizeText(file.fileSize) : "-"));

      const download = el("button", null, "다운로드");
      download.type = "button";
      download.addEventListener("click", () => api.plmDownload(state.division, file));
      row.append(download);
      attachmentHost.append(row);
    }

    if (!boxes.length) {
      attachmentHost.append(el("div", "empty", "안을 훑을 수 있는 압축 첨부(ZIP/7z)가 없습니다."));
      return;
    }

    if (boxes.length > 1) {
      const allRow = el("div", "row");
      selectAll = el("input");
      selectAll.type = "checkbox";
      selectAll.addEventListener("change", () => {
        for (const { box, fileId } of boxes) {
          box.checked = selectAll.checked;
          if (selectAll.checked) picked.add(fileId);
          else picked.delete(fileId);
        }
        refresh();
      });
      allRow.append(selectAll, el("span", "row-name", `전체 선택 (압축 첨부 ${boxes.length}개)`));
      attachmentHost.prepend(allRow);
    } else if (!picked.size) {
      // 압축 첨부가 하나뿐이면 고를 것이 없으므로 미리 체크해 둔다.
      boxes[0].box.checked = true;
      picked.add(boxes[0].fileId);
    }

    // 2단계: 훑어서 나온 로그 중 분석할 것 고르기. ap_silentlog 처럼 한 폴더에
    // 잘게 쪼개져 있는 로그는 통째로 한 항목이 된다.
    const drawCandidates = (candidates) => {
      logHost.replaceChildren();
      const found = candidates || [];
      if (!found.length) {
        logHost.append(el("div", "empty", "고를 만한 로그 파일이 없습니다."));
        return;
      }

      const chosen = new Map();
      const analyzeHost = el("div");
      const analyze = el("button", "primary", "선택한 로그 분석");
      analyze.type = "button";
      let analyzing = false;

      const refreshAnalyze = () => {
        const count = [...chosen.values()].reduce((sum, items) => sum + items.length, 0);
        analyze.textContent = count ? `선택한 로그 ${count}개 분석` : "분석할 로그를 선택하세요";
        analyze.disabled = analyzing || count === 0;
      };

      // id 는 첨부까지 포함해야 한다. 첨부 두 개에 같은 이름의 폴더가 들어 있는
      // 경우가 있다.
      const addRow = (id, label, meta, items) => {
        const row = el("div", "row");
        const box = el("input");
        box.type = "checkbox";
        box.addEventListener("change", () => {
          if (box.checked) chosen.set(id, items);
          else chosen.delete(id);
          refreshAnalyze();
        });
        row.append(box, el("span", "row-name", label), el("span", "grow"), el("span", "row-meta", meta));
        logHost.append(row);
        return box;
      };

      const groups = new Map();
      const singles = [];
      for (const candidate of found) {
        if (!candidate.group) { singles.push(candidate); continue; }
        const key = `${candidate.file_id}::${candidate.group}`;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(candidate);
      }

      logHost.append(el("p", "card-note", `찾은 로그 ${found.length}개. 분석할 것을 고르세요.`));

      const rows = [];
      for (const candidate of singles) {
        rows.push(addRow(`${candidate.file_id}::${candidate.path}`, candidate.path,
                         sizeText(candidate.size),
                         [{ file_id: candidate.file_id, route: candidate.route }]));
      }

      for (const [id, items] of groups) {
        const folder = items[0].group.split("/").pop();
        const total = items.reduce((sum, item) => sum + (Number(item.size) || 0), 0);
        rows.push(addRow(id, `${folder} 폴더 (로그 ${items.length}개)`, sizeText(total),
                         items.map((item) => ({ file_id: item.file_id, route: item.route }))));

        const fold = el("details", "fold");
        fold.append(el("summary", null, `${folder} 안의 파일`));
        const list = el("ul");
        for (const item of items) {
          list.append(el("li", null, `${item.path.split("/").pop()} — ${sizeText(item.size)}`));
        }
        fold.append(list);
        logHost.append(fold);
      }

      // 고를 것이 하나뿐이면 미리 체크해 둔다.
      if (rows.length === 1) {
        rows[0].checked = true;
        rows[0].dispatchEvent(new Event("change"));
      }

      analyze.addEventListener("click", async () => {
        const logs = [...chosen.values()].flat();
        analyzing = true;
        refreshAnalyze();
        try {
          const { job_id: jobId } = await api.plmAnalyzeAttachments(
            state.division, defect.defectCode, null, logs,
          );
          followJob(jobId, {
            progressHost: analyzeHost,
            defect,
            setBusy: (value) => { analyzing = value; refreshAnalyze(); },
            onDone: activateAnalyzed,
          });
        } catch (error) {
          analyzeHost.replaceChildren(el("p", "card-note", String(error.message || error)));
          analyzing = false;
          refreshAnalyze();
        }
      });

      logHost.append(analyze, analyzeHost);
      refreshAnalyze();

      // 분석 잡이 돌고 있었다면 진행 상황을 이어서 보여 준다.
      const running = state.attachmentJobs[attachmentJobKey(defect)];
      if (running?.job_id) {
        followJob(running.job_id, {
          progressHost: analyzeHost,
          defect,
          setBusy: (value) => { analyzing = value; refreshAnalyze(); },
          onDone: activateAnalyzed,
        });
      }
    };

    const setBusy = (value) => { busy = value; refresh(); };

    scan.addEventListener("click", async () => {
      logHost.replaceChildren();
      delete state.attachmentJobs[scanKey];
      setBusy(true);
      try {
        const { job_id: jobId } = await api.plmScanAttachmentLogs(
          state.division, defect.defectCode, [...picked],
        );
        followJob(jobId, {
          progressHost, defect, variant: "::scan", setBusy,
          onDone: (job) => drawCandidates(job.log_candidates),
        });
      } catch (error) {
        progressHost.replaceChildren(el("p", "card-note", String(error.message || error)));
        setBusy(false);
      }
    });

    attachmentHost.append(scan, progressHost, logHost);
    refresh();

    // 탭을 다녀와도 훑은 결과와 진행 중인 잡은 그대로 이어진다.
    const scanned = state.attachmentJobs[scanKey];
    if (scanned?.log_candidates) {
      drawJobProgress(scanned, progressHost);
      drawCandidates(scanned.log_candidates);
    } else if (scanned?.job_id) {
      followJob(scanned.job_id, {
        progressHost, defect, variant: "::scan", setBusy,
        onDone: (job) => drawCandidates(job.log_candidates),
      });
    }
  };

  // -------------------------------------------------------------- AI 분석
  const analysis = panel("AI 분석", "결함 내용을 정리해 코멘트 초안을 만듭니다.");
  const analysisHost = el("div", "stack");
  analysis.body.append(analysisHost);

  const drawAnalysis = (defect) => {
    analysisHost.replaceChildren();
    const run = el("button", "primary", "분석 컨텍스트 생성");
    run.type = "button";
    const output = el("div", "stack");

    const renderContext = (context) => {
      output.replaceChildren(table(
        ["항목", "내용"],
        Object.entries(context).map(([key, value]) => [key, String(value ?? "-").slice(0, 400)]),
      ));

      const toComment = el("button", null, "이 내용을 코멘트 초안으로");
      toComment.type = "button";
      toComment.addEventListener("click", () => {
        commentBody.value = [
          "🤖 AI 분석 결과",
          "",
          "**문제점:**",
          context.problem || "N/A",
          "",
          "**근본 원인:**",
          context.root_cause || "N/A",
          "",
          "**해결 방안:**",
          context.solution || "N/A",
        ].join("\n");
        commentBody.focus?.();
      });
      output.append(toComment);
    };

    run.addEventListener("click", async () => {
      run.disabled = true;
      output.replaceChildren(el("p", "card-note", "PLM 에서 결함 정보를 가져오는 중..."));
      try {
        const body = await api.plmAnalyze(state.division, defect.defectCode);
        if (!body.success) {
          output.replaceChildren(el("p", "card-note", body.message || "분석 컨텍스트를 만들지 못했습니다."));
          return;
        }
        state.analysis = body.context;
        renderContext(body.context);
      } catch (error) {
        output.replaceChildren(el("p", "card-note", String(error.message || error)));
      } finally {
        run.disabled = false;
      }
    });

    analysisHost.append(run, output);
    if (state.analysis) renderContext(state.analysis);
  };

  // -------------------------------------------------------------- 코멘트
  const comment = panel("코멘트 등록", "줄바꿈은 PLM 화면에서도 유지됩니다.");
  const commentBody = textarea("결함에 남길 내용", 8);
  const commentUser = input("Knox ID");
  const commentButton = el("button", "primary", "등록");
  commentButton.type = "button";
  const commentNote = el("p", "card-note");

  commentButton.addEventListener("click", async () => {
    if (!state.selected) return;
    commentButton.disabled = true;
    commentNote.textContent = "등록 중...";
    try {
      const body = await api.plmSubmitComment({
        division_code: state.division,
        defect_code: state.selected.defectCode,
        comment: commentBody.value,
        create_user: commentUser.value,
      });
      commentNote.textContent = body.success ? "등록했습니다." : body.message || "등록 실패";
      if (body.success) {
        commentBody.value = "";
        cache.invalidate(state.division, state.selected.defectCode);
      }
    } catch (error) {
      commentNote.textContent = String(error.message || error);
    } finally {
      commentButton.disabled = false;
    }
  });

  comment.body.append(field("내용", commentBody), field("작성자", commentUser), commentButton, commentNote);

  // ------------------------------------------------------------------ 배선

  /** Reaches into all three selection panels, so it belongs to the wiring
   *  rather than to any one of them. Clearing ctx.activeDefect is the part
   *  that matters most: the chat files its answers against it. */
  function clearSelectionViews() {
    detailHost.replaceChildren(el("div", "empty", "결함을 선택하세요."));
    attachmentHost.replaceChildren(el("div", "empty", "결함을 선택하세요."));
    analysisHost.replaceChildren(el("div", "empty", "결함을 선택하세요."));
    ctx.setActiveDefect(null);
  }

  const selectDefect = async (defect) => {
    if (state.selected?.defectCode !== defect.defectCode) state.analysis = null;
    state.selected = defect;
    // The chat registers its answers against whichever defect is open here.
    ctx.setActiveDefect({
      code: defect.defectCode,
      division: state.division,
      title: defect.plmTitle || "",
    });
    drawResults();
    await Promise.all([drawDetail(defect), drawAttachments(defect)]);
    drawAnalysis(defect);
  };

  const searchByDefectCode = async () => {
    const defectCodes = defectInput.value
      .split(/[,\s]+/)
      .map((code) => code.trim())
      .filter(Boolean);

    state.defectCode = defectInput.value;
    if (!defectCodes.length) {
      return { success: false, message: "PLM 번호를 입력하세요.", defects: [] };
    }
    return api.plmDefectDetails(state.division, defectCodes);
  };

  const searchByOwner = async () => {
    const ownerId = method.value === "그룹"
      ? (await api.plmGroupUsers(groupPicker.value)).users.join(",")
      : userInput.value.trim();

    if (!ownerId) {
      return { success: false, message: "검색할 그룹이나 Knox ID 를 지정하세요.", defects: [] };
    }

    return api.plmQuickSearch({
      division_code: state.division,
      main_owner_id: ownerId,
      status: status.value.toLowerCase(),
    });
  };

  searchButton.addEventListener("click", async () => {
    searchButton.disabled = true;
    searchNote.textContent = "검색 중...";
    try {
      const body = method.value === "PLM 번호" ? await searchByDefectCode() : await searchByOwner();
      state.defects = body.defects || [];
      state.selected = null;
      state.analysis = null;
      // Without this the previous defect stays on screen and, worse, stays in
      // ctx.activeDefect — the chat would file its answer against it.
      clearSelectionViews();
      state.searchNote = body.success
        ? `${fmt.count(body.defects?.length || 0)}건` + (body.truncated ? ` (전체 ${body.total_codes}건 중 일부)` : "")
        : body.message || "검색 실패";
      searchNote.textContent = state.searchNote;
      drawResults();
    } catch (error) {
      state.searchNote = String(error.message || error);
      searchNote.textContent = state.searchNote;
    } finally {
      searchButton.disabled = false;
    }
  });

  grid.append(search.section, results.section, detail.section, attachments.section,
              analysis.section, comment.section);

  await loadGroups();
  drawTarget();
  drawResults();
  if (state.selected) {
    await selectDefect(state.selected);
  } else {
    detailHost.append(el("div", "empty", "결함을 선택하세요."));
    attachmentHost.append(el("div", "empty", "결함을 선택하세요."));
    analysisHost.append(el("div", "empty", "결함을 선택하세요."));
  }
}
