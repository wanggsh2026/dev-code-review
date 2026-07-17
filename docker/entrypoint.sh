#!/usr/bin/env bash
set -euo pipefail

log() {
  printf '[dev-code-review] %s\n' "$*"
}

fail() {
  printf '[dev-code-review] ERROR: %s\n' "$*" >&2
  exit 2
}

require_command() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    fail "required command not found: $name"
  fi
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
app_root="$(cd "$script_dir/.." && pwd)"

workspace="${REVIEW_WORKSPACE:-${CI_PROJECT_DIR:-}}"
if [[ -z "$workspace" ]]; then
  if [[ -d /workspace ]]; then
    workspace=/workspace
  else
    workspace="$(pwd)"
  fi
fi

if [[ ! -d "$workspace" ]]; then
  fail "workspace does not exist: $workspace"
fi

export REVIEW_WORKSPACE="$workspace"
export REVIEW_OUTPUT_DIR="${REVIEW_OUTPUT_DIR:-review-output}"
export REVIEW_CONFIG="${REVIEW_CONFIG:-$app_root/gitlab-merge-review/review-config.example.json}"

check_required_env() {
  local missing=()

  [[ -n "${REVIEW_TARGET_BRANCH:-dev}" ]] || missing+=("REVIEW_TARGET_BRANCH")
  [[ -n "${OCR_LLM_URL:-}" ]] || missing+=("OCR_LLM_URL")
  [[ -n "${OCR_LLM_TOKEN:-}" ]] || missing+=("OCR_LLM_TOKEN")
  [[ -n "${OCR_LLM_MODEL:-}" ]] || missing+=("OCR_LLM_MODEL")

  if (( ${#missing[@]} > 0 )); then
    fail "missing required environment variable(s): ${missing[*]}"
  fi
}

configure_ocr() {
  if ! command -v ocr >/dev/null 2>&1; then
    log "ocr command not found; review script will report OCR execution failure"
    return 0
  fi

  log "configuring ocr LLM endpoint"
  ocr config set llm.url "$OCR_LLM_URL" >/dev/null
  ocr config set llm.auth_token "$OCR_LLM_TOKEN" >/dev/null
  ocr config set llm.model "$OCR_LLM_MODEL" >/dev/null

  if [[ -n "${OCR_USE_ANTHROPIC:-}" ]]; then
    ocr config set llm.use_anthropic "$OCR_USE_ANTHROPIC" >/dev/null
  fi
  if [[ -n "${OCR_LLM_AUTH_HEADER:-}" ]]; then
    ocr config set llm.auth_header "$OCR_LLM_AUTH_HEADER" >/dev/null
  fi
  if [[ -n "${OCR_LLM_EXTRA_HEADERS:-}" ]]; then
    ocr config set llm.extra_headers "$OCR_LLM_EXTRA_HEADERS" >/dev/null
  fi
  if [[ -n "${OCR_LLM_EXTRA_BODY:-}" ]]; then
    ocr config set llm.extra_body "$OCR_LLM_EXTRA_BODY" >/dev/null
  fi
}

run_shell_review() {
  local review_script="$app_root/gitlab-merge-review/scripts/run-gitlab-merge-review.sh"
  if [[ ! -f "$review_script" ]]; then
    fail "review script not found: $review_script"
  fi
  log "running shell review"
  (
    cd "$workspace"
    bash "$review_script"
  )
}

run_review() {
  run_shell_review
}

main() {
  require_command git
  require_command bash
  require_command python3

  check_required_env
  configure_ocr

  local rc=0
  set +e
  if (( $# > 0 )); then
    log "running custom command: $*"
    "$@"
    rc=$?
  else
    run_review
    rc=$?
  fi
  set -e

  if [[ -d "$workspace/$REVIEW_OUTPUT_DIR" ]]; then
    log "review artifacts:"
    find "$workspace/$REVIEW_OUTPUT_DIR" -maxdepth 2 -type f -print | sed 's/^/[dev-code-review]   /'
  fi

  if (( rc == 0 )); then
    log "review passed"
  else
    log "review blocked or failed with exit code $rc"
  fi
  exit "$rc"
}

main "$@"
