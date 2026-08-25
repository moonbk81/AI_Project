// PLM — 결함을 찾아 첨부 로그를 분석하고, 결과를 코멘트로 남긴다.

import { api } from "../api.js";
import { setMarkdown } from "../markdown.js";
import { el, fmt, table } from "../viz.js";

const DIVISIONS = { Mobile: "25", Network: "26" };
const STATUSES = ["Open", "Resolve", "Close"];
const JOB_POLL_MS = 2000;
const JOB_DONE = new Set(["done", "error"]);

function panel(title, subtitle) {
  const section = el("section", "card");
  const head = el("div", "card-head");
  head.append(el("h2", null, title), el("span", "grow"));
  section.append(head);
  if (subtitle) section.append(el("p", "card-sub", subtitle));

  const body = el("div", "stack");
  section.append(body);
  return { section, body, head };
}

function field(label, control) {
  const wrap = el("label", "field");
  wrap.append(el("span", "field-label", label), control);
  return wrap;
}

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

function radioRow(name, options, onChange) {
  const wrap = el("div", "quick");
  let current = options[0];
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

  const state = { division: "25", defects: [], selected: null, analysis: null };

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
      drawResults();
    } catch (error) {
      modeNote.textContent = String(error.message || error);
    } finally {
      modeButton.disabled = false;
    }
  });

  api.plmLocalTest().then((body) => drawMode(body.enabled)).catch(() => drawMode(false));

  // ------------------------------------------------------------------ 검색
  const search = panel("결함 검색", "담당 그룹이나 Knox ID 로 찾습니다.");
  const status = radioRow("status", STATUSES);
  const method = radioRow("method", ["그룹", "사용자 ID"], () => drawTarget());

  const divisionPicker = select(Object.keys(DIVISIONS));
  divisionPicker.addEventListener("change", async () => {
    state.division = DIVISIONS[divisionPicker.value];
    await loadGroups();
    drawTarget();
  });

  const targetHost = el("div");
  const groupPicker = select([]);
  const userInput = input("예: bongki.moon");
  const searchButton = el("button", "primary", "검색");
  searchButton.type = "button";
  const searchNote = el("p", "card-note");

  const drawTarget = () => {
    targetHost.replaceChildren(
      method.value === "그룹" ? field("그룹", groupPicker) : field("Knox ID", userInput),
    );
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
    } catch (error) {
      groupPicker.append(new Option("그룹을 불러오지 못했습니다", ""));
    }
  };

  search.body.append(
    field("Division", divisionPicker),
    field("상태", status.wrap),
    field("검색 방식", method.wrap),
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
      const name = el("span", "row-name" + (defect === state.selected ? " active" : ""),
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

    const [details, comments] = await Promise.all([
      api.plmDefectDetails(state.division, [defect.defectCode]).catch(() => ({ defects: [] })),
      api.plmHumanComments(state.division, defect.defectCode).catch(() => ({ comments: [] })),
    ]);
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
  const attachments = panel("첨부 파일", "ZIP 안의 LOG 파일을 뽑아 바로 분석할 수 있습니다.");
  const attachmentHost = el("div", "stack");
  attachments.body.append(attachmentHost);

  let jobTimer = null;
  ctx.onLeave(() => clearTimeout(jobTimer));

  const followJob = (jobId, progressHost) => {
    const bar = el("div", "bar");
    const fill = el("div", "bar-fill");
    bar.append(fill);
    const message = el("p", "card-note", "시작하는 중...");
    progressHost.replaceChildren(message, bar);

    const poll = async () => {
      try {
        const job = await api.job(jobId);
        message.textContent = job.error || job.message || job.status;
        fill.style.width = `${Math.max(2, job.progress || 0)}%`;
        fill.classList.toggle("done", job.status === "done");
        fill.classList.toggle("error", job.status === "error");

        if (!JOB_DONE.has(job.status)) {
          jobTimer = setTimeout(poll, JOB_POLL_MS);
        } else if (job.status === "done" && job.current_file) {
          // Make the freshly analyzed log the active one, but leave this view
          // as it is — the search and the selected defect stay put.
          const active = await ctx.filesChanged({ select: job.current_file });
          message.textContent = active === job.current_file
            ? `${job.message} — '${job.current_file}' 을 활성 파일로 설정했습니다. 대시보드에서 볼 수 있습니다.`
            : `${job.message} — '${job.current_file}'`;
        }
      } catch (error) {
        message.textContent = String(error.message || error);
      }
    };
    poll();
  };

  const drawAttachments = async (defect) => {
    attachmentHost.replaceChildren(el("div", "empty", "불러오는 중..."));
    const listing = await api.plmFiles(state.division, defect.defectCode).catch(() => ({ files: [] }));
    const files = listing.files || [];

    attachmentHost.replaceChildren();
    if (!files.length) {
      attachmentHost.append(el("div", "empty", "첨부 파일이 없습니다."));
      return;
    }

    for (const file of files) {
      const row = el("div", "row");
      row.append(el("span", "row-name", file.title || "-"),
                 el("span", "grow"),
                 el("span", "row-meta", file.fileSize ? `${(file.fileSize / 1024).toFixed(1)} KB` : "-"));

      const download = el("button", null, "다운로드");
      download.type = "button";
      download.addEventListener("click", () => api.plmDownload(state.division, file));
      row.append(download);
      attachmentHost.append(row);
    }

    const progressHost = el("div");
    const analyze = el("button", "primary", "로그 추출해 분석");
    analyze.type = "button";
    analyze.addEventListener("click", async () => {
      analyze.disabled = true;
      try {
        const { job_id: jobId } = await api.plmAnalyzeAttachments(state.division, defect.defectCode);
        followJob(jobId, progressHost);
      } catch (error) {
        progressHost.replaceChildren(el("p", "card-note", String(error.message || error)));
      } finally {
        analyze.disabled = false;
      }
    });
    attachmentHost.append(analyze, progressHost);
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
        output.replaceChildren(table(
          ["항목", "내용"],
          Object.entries(body.context).map(([key, value]) => [key, String(value ?? "-").slice(0, 400)]),
        ));

        const toComment = el("button", null, "이 내용을 코멘트 초안으로");
        toComment.type = "button";
        toComment.addEventListener("click", () => {
          commentBody.value = [
            "🤖 AI 분석 결과",
            "",
            "**문제점:**",
            body.context.problem || "N/A",
            "",
            "**근본 원인:**",
            body.context.root_cause || "N/A",
            "",
            "**해결 방안:**",
            body.context.solution || "N/A",
          ].join("\n");
          commentBody.focus?.();
        });
        output.append(toComment);
      } catch (error) {
        output.replaceChildren(el("p", "card-note", String(error.message || error)));
      } finally {
        run.disabled = false;
      }
    });

    analysisHost.append(run, output);
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
      if (body.success) commentBody.value = "";
    } catch (error) {
      commentNote.textContent = String(error.message || error);
    } finally {
      commentButton.disabled = false;
    }
  });

  comment.body.append(field("내용", commentBody), field("작성자", commentUser), commentButton, commentNote);

  // ------------------------------------------------------------------ 배선
  const selectDefect = async (defect) => {
    state.selected = defect;
    drawResults();
    await Promise.all([drawDetail(defect), drawAttachments(defect)]);
    drawAnalysis(defect);
  };

  searchButton.addEventListener("click", async () => {
    searchButton.disabled = true;
    searchNote.textContent = "검색 중...";
    try {
      const ownerId = method.value === "그룹"
        ? (await api.plmGroupUsers(groupPicker.value)).users.join(",")
        : userInput.value.trim();

      if (!ownerId) {
        searchNote.textContent = "검색할 그룹이나 Knox ID 를 지정하세요.";
        return;
      }

      const body = await api.plmQuickSearch({
        division_code: state.division,
        main_owner_id: ownerId,
        status: status.value.toLowerCase(),
      });
      state.defects = body.defects || [];
      state.selected = null;
      searchNote.textContent = body.success
        ? `${fmt.count(body.defects?.length || 0)}건` + (body.truncated ? ` (전체 ${body.total_codes}건 중 일부)` : "")
        : body.message || "검색 실패";
      drawResults();
    } catch (error) {
      searchNote.textContent = String(error.message || error);
    } finally {
      searchButton.disabled = false;
    }
  });

  grid.append(search.section, results.section, detail.section, attachments.section,
              analysis.section, comment.section);

  detailHost.append(el("div", "empty", "결함을 선택하세요."));
  attachmentHost.append(el("div", "empty", "결함을 선택하세요."));
  analysisHost.append(el("div", "empty", "결함을 선택하세요."));

  await loadGroups();
  drawTarget();
  drawResults();
}
