// Every call the frontend makes to this project's backend.

async function get(path, params) {
  const query = params ? "?" + new URLSearchParams(params).toString() : "";
  const response = await fetch(path + query);
  if (!response.ok) throw new Error(`${path} → ${response.status}`);
  return response.json();
}

export const api = {
  files: () => get("/files").then((body) => body.files || []),

  kpi: (sourceFile) => get("/dashboard/kpi", { source_file: sourceFile }),

  // Returns the builder's contract; `status` says whether there is anything to draw.
  chart: (name, sourceFile) =>
    get(`/charts/${name}`, { source_file: sourceFile }).then((body) => body.series),

  satelliteOverview: (baseName) => get(`/satellite/${encodeURIComponent(baseName)}`),

  jobs: () => get("/jobs").then((body) => body.jobs || []),
  job: (jobId) => get(`/jobs/${jobId}`),

  async analyze(files) {
    const form = new FormData();
    for (const file of files) form.append("files", file, file.name);
    const response = await fetch("/jobs/analyze", { method: "POST", body: form });
    if (!response.ok) throw new Error(`분석 작업 생성 실패 (${response.status})`);
    return response.json();
  },

  async resetDb() {
    const response = await fetch("/db/reset", { method: "POST" });
    if (!response.ok) throw new Error(`DB 초기화 실패 (${response.status})`);
    return response.json();
  },
};

// Metadata rows are keyed by "<base>_payload.json"; artifacts drop the suffix.
export const baseName = (sourceFile) => String(sourceFile || "").replace(/_payload\.json$/, "");
