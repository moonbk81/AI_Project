#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OS_TYPE="$(uname -s)"

if [[ "${OS_TYPE}" == "Darwin" ]]; then
  DEFAULT_CONDA_ENV="ai"
else
  DEFAULT_CONDA_ENV="ai_proj"
fi

CONDA_ENV="${CONDA_ENV:-${DEFAULT_CONDA_ENV}}"
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-8080}"
UI_URL="${UI_URL:-http://localhost:${BACKEND_PORT}/ui/}"

if ! command -v conda >/dev/null 2>&1; then
  echo "conda was not found. Open a shell where conda is available, then run this script again."
  exit 1
fi

CONDA_BASE="$(conda info --base)"

cd "${PROJECT_DIR}"
source "${CONDA_BASE}/etc/profile.d/conda.sh"
conda activate "${CONDA_ENV}"

echo "[AI_Project] Starting FastAPI backend on ${BACKEND_HOST}:${BACKEND_PORT}"
echo "[AI_Project] Browser UI: ${UI_URL}"

python -m uvicorn backend.main:app --host "${BACKEND_HOST}" --port "${BACKEND_PORT}"
