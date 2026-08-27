// Every call the frontend makes to this project's backend.

async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `${path} → ${response.status}`);
  }
  return response.json();
}

async function get(path, params) {
  const query = params ? "?" + new URLSearchParams(params).toString() : "";
  const response = await fetch(path + query);
  if (!response.ok) throw new Error(`${path} → ${response.status}`);
  return response.json();
}

export const api = {
  health: () => get("/health"),

  files: () => get("/files").then((body) => body.files || []),

  quickPrompts: () => get("/quick-prompts").then((body) => body.prompts || {}),

  kpi: (sourceFile) => get("/dashboard/kpi", { source_file: sourceFile }),

  // The backend fills in the device KPI from the same file, so the caller
  // only sends the question and the recent turns.
  async ask(question, sourceFile, chatHistory) {
    const response = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, current_file: sourceFile, chat_history: chatHistory }),
    });
    if (!response.ok) throw new Error(`질의 실패 (${response.status})`);
    return response.json();
  },

  // Returns the builder's contract; `status` says whether there is anything to draw.
  chart: (name, sourceFile) =>
    get(`/charts/${name}`, { source_file: sourceFile }).then((body) => body.series),

  satelliteOverview: (baseName) => get(`/satellite/${encodeURIComponent(baseName)}`),

  knowledge: () => get("/knowledge"),
  saveKnowledge: (body) => post("/knowledge", body),
  recommendCategory: (text, categories) => post("/knowledge/recommend-category", { text, categories }),

  sessionReport: (baseName, currentFile) =>
    post("/reports/session", { base_name: baseName, current_file: currentFile }),
  satelliteReport: (baseName, satType, currentFile) =>
    post("/reports/satellite", { base_name: baseName, sat_type: satType, current_file: currentFile }),

  jobs: () => get("/jobs").then((body) => body.jobs || []),
  job: (jobId) => get(`/jobs/${jobId}`),

  async analyze(files) {
    const form = new FormData();
    for (const file of files) form.append("files", file, file.name);
    const response = await fetch("/jobs/analyze", { method: "POST", body: form });
    if (!response.ok) throw new Error(`분석 작업 생성 실패 (${response.status})`);
    return response.json();
  },

  // ------------------------------------------------------------------- PLM

  plmLocalTest: () => get("/plm/local-test"),
  plmSetLocalTest: (enabled) => post("/plm/local-test", { enabled }),

  plmGroups: (divisionCode) => get("/plm/groups", { division_code: divisionCode }),
  plmGroupUsers: (groupKey) => get(`/plm/groups/${encodeURIComponent(groupKey)}/users`),

  plmQuickSearch: (body) => post("/plm/quick-search", body),
  plmDefectDetails: (divisionCode, defectCodes) =>
    post("/plm/defects", { division_code: divisionCode, defect_codes: defectCodes }),
  plmFiles: (divisionCode, defectCode) =>
    post("/plm/files", { division_code: divisionCode, defect_code: defectCode }),
  plmHumanComments: (divisionCode, defectCode) =>
    post("/plm/defect-history/comments", { division_code: divisionCode, defect_code: defectCode }),
  plmAnalyze: (divisionCode, defectCode) =>
    post("/plm/analyze", { division_code: divisionCode, defect_code: defectCode }),
  plmAnalysisQuery: (divisionCode, defectCode, comments) =>
    post("/plm/analysis-query", { division_code: divisionCode, defect_code: defectCode, comments }),
  /** `fileIds` picks the attachments to open; omit it to take every archive. */
  plmAnalyzeAttachments: (divisionCode, defectCode, fileIds) =>
    post("/plm/attachments/analyze", {
      division_code: divisionCode,
      defect_code: defectCode,
      file_ids: fileIds && fileIds.length ? fileIds : null,
    }),

  // The comment body is turned into PLM's markup server-side, so the caller
  // sends what the user typed.
  plmSubmitComment: (form) => post("/plm/comment", { form }),
  /** Register a chat answer; the AI header is added server-side. */
  plmSubmitAnswer: (form) => post("/plm/comment", { form }),
  plmRegisterDefect: (form) => post("/plm/defects/register", { form }),

  plmDefectUrl: (defectId) =>
    `http://splm.sec.samsung.net/wl/tqm/defect/defectreg/goDefectDetail.do?isPopUp=Y&menuGubun=&defectId=${encodeURIComponent(defectId || "")}`,

  /** Fetch an attachment and hand it to the browser's downloader. */
  async plmDownload(divisionCode, file) {
    const response = await fetch("/plm/files/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        division_code: divisionCode,
        doc_id: file.docId,
        title: file.title,
        file_id: file.fileId,
      }),
    });
    if (!response.ok) throw new Error(`다운로드 실패 (${response.status})`);

    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = response.headers.get("X-Filename") || file.title || "attachment";
    link.click();
    URL.revokeObjectURL(url);
  },

  async resetDb() {
    const response = await fetch("/db/reset", { method: "POST" });
    if (!response.ok) throw new Error(`DB 초기화 실패 (${response.status})`);
    return response.json();
  },
};

// Metadata rows are keyed by "<base>_payload.json"; artifacts drop the suffix.
export const baseName = (sourceFile) => String(sourceFile || "").replace(/_payload\.json$/, "");
