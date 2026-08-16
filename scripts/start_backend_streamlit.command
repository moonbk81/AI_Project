#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA_ENV="${CONDA_ENV:-ai}"
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-8080}"
BACKEND_API_URL="${BACKEND_API_URL:-http://localhost:${BACKEND_PORT}}"

if ! command -v conda >/dev/null 2>&1; then
  echo "conda was not found. Open a shell where conda is available, then run this script again."
  exit 1
fi

CONDA_BASE="$(conda info --base)"
LAUNCH_DIR="${TMPDIR:-/tmp}/ai_project_launch_$$"
mkdir -p "${LAUNCH_DIR}"

BACKEND_RUNNER="${LAUNCH_DIR}/backend.sh"
FRONTEND_RUNNER="${LAUNCH_DIR}/frontend.sh"

write_optional_export() {
  local file="$1"
  local name="$2"
  local value="${!name:-}"

  if [[ -n "${value}" ]]; then
    printf 'export %s=%q\n' "${name}" "${value}" >> "${file}"
  fi
}

cat > "${BACKEND_RUNNER}" <<EOF
#!/usr/bin/env bash
set -euo pipefail

cd $(printf '%q' "${PROJECT_DIR}")
source $(printf '%q' "${CONDA_BASE}")/etc/profile.d/conda.sh
conda activate $(printf '%q' "${CONDA_ENV}")
EOF

write_optional_export "${BACKEND_RUNNER}" "RAG_LLM_PROVIDER"
write_optional_export "${BACKEND_RUNNER}" "RAG_LLM_BASE_URL"
write_optional_export "${BACKEND_RUNNER}" "RAG_LLM_MODEL"
write_optional_export "${BACKEND_RUNNER}" "RAG_LLM_API_KEY"
write_optional_export "${BACKEND_RUNNER}" "RAG_LLM_TIMEOUT"

cat >> "${BACKEND_RUNNER}" <<EOF

echo "[AI_Project] Starting FastAPI backend on ${BACKEND_HOST}:${BACKEND_PORT}"
python -m uvicorn backend.main:app --host $(printf '%q' "${BACKEND_HOST}") --port $(printf '%q' "${BACKEND_PORT}")
EOF

cat > "${FRONTEND_RUNNER}" <<EOF
#!/usr/bin/env bash
set -euo pipefail

cd $(printf '%q' "${PROJECT_DIR}")
source $(printf '%q' "${CONDA_BASE}")/etc/profile.d/conda.sh
conda activate $(printf '%q' "${CONDA_ENV}")

export USE_BACKEND_API=1
export BACKEND_API_URL=$(printf '%q' "${BACKEND_API_URL}")

echo "[AI_Project] Waiting for backend: ${BACKEND_API_URL}/health"
for attempt in {1..30}; do
  if curl -fsS "\${BACKEND_API_URL}/health" >/dev/null 2>&1; then
    break
  fi

  if [[ "\${attempt}" == "30" ]]; then
    echo "[AI_Project] Backend health check failed. Starting Streamlit anyway."
  else
    sleep 1
  fi
done

echo "[AI_Project] Starting Streamlit with backend API: \${BACKEND_API_URL}"
streamlit run web_app.py
EOF

chmod +x "${BACKEND_RUNNER}" "${FRONTEND_RUNNER}"

osascript - "${BACKEND_RUNNER}" "${FRONTEND_RUNNER}" <<'OSA'
on run argv
  tell application "Terminal"
    activate
    do script quoted form of item 1 of argv
    delay 1
    do script quoted form of item 2 of argv
  end tell
end run
OSA

echo "Started AI_Project backend and Streamlit in two Terminal windows."
