#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/ubuntu/GitHub/AIGrader"
VENV_DIR="/home/ubuntu/venvs/aigrader-venv"
ASSIGNMENTS_FILE="/home/ubuntu/GitHub/AIGrader/assignments.csv"
LOCK_FILE="/home/ubuntu/lock/aigrader.lock"

if [[ -e "${LOCK_FILE}" ]]; then
  echo "Lock file exists at ${LOCK_FILE}. Exiting."
  exit 0
fi

touch "${LOCK_FILE}"
trap 'rm -f "${LOCK_FILE}"' EXIT

# Load secrets (use an absolute path)
source /etc/aigrader.env

# Activate venv if desired (use an absolute path)
source "${VENV_DIR}/bin/activate"

# Run the CLI (use absolute paths for files)
aigrader \
  --assignment-file "${ASSIGNMENTS_FILE}" \
  --use-llm \
  --openai-key "$OPENAI_API_KEY" \
  --post-comment \
  --comment-html \
  --token "$CANVAS_TOKEN" \
  --base-url "$CANVAS_BASE_URL"

