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

env_value() {
  local name="$1"
  local default_value="${2:-<empty>}"
  local value="${!name-}"
  if [[ -n "$value" ]]; then
    printf '%s' "$value"
  else
    printf '%s' "$default_value"
  fi
}

env_secret_state() {
  local name="$1"
  local value="${!name-}"
  if [[ -n "$value" ]]; then
    printf '<set, masked>'
  else
    printf '<empty>'
  fi
}

lower_value() {
  printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]'
}

is_review_disabled() {
  local enabled scope
  enabled="$(lower_value "${REVIEW_ENABLED:-true}")"
  scope="$(lower_value "${REVIEW_SCOPE_MODE:-changed}")"
  case "$enabled" in
    false|0|no|n|off) return 0 ;;
  esac
  case "$scope" in
    none|disabled) return 0 ;;
  esac
  return 1
}

log_entry_config() {
  log "container environment config:"
  log "  REVIEW_WORKSPACE=$REVIEW_WORKSPACE"
  log "  REVIEW_OUTPUT_DIR=$REVIEW_OUTPUT_DIR"
  log "  REVIEW_CONFIG=$REVIEW_CONFIG"
  log "  REVIEW_TARGET_BRANCH=$(env_value REVIEW_TARGET_BRANCH dev)"
  log "  OCR_LLM_URL=$(env_value OCR_LLM_URL)"
  log "  OCR_LLM_MODEL=$(env_value OCR_LLM_MODEL)"
  log "  OCR_LLM_TOKEN=$(env_secret_state OCR_LLM_TOKEN)"
  log "  OCR_USE_ANTHROPIC=$(env_value OCR_USE_ANTHROPIC)"
  log "  OCR_LLM_AUTH_HEADER=$(env_secret_state OCR_LLM_AUTH_HEADER)"
  log "  OCR_LLM_EXTRA_HEADERS=$(env_secret_state OCR_LLM_EXTRA_HEADERS)"
  log "  OCR_LLM_EXTRA_BODY=$(env_secret_state OCR_LLM_EXTRA_BODY)"
  log "  GITLAB_TOKEN=$(env_secret_state GITLAB_TOKEN)"
  log "  GITLAB_PRIVATE_TOKEN=$(env_secret_state GITLAB_PRIVATE_TOKEN)"
  log "  GITLAB_API_TOKEN=$(env_secret_state GITLAB_API_TOKEN)"
  log "  CI_JOB_TOKEN=$(env_secret_state CI_JOB_TOKEN)"
  log "  REVIEW_ENABLED=$(env_value REVIEW_ENABLED true)"
  log "  REVIEW_SCOPE_MODE=$(env_value REVIEW_SCOPE_MODE changed)"
  log "  REVIEW_USE_PROJECT_RULES=$(env_value REVIEW_USE_PROJECT_RULES auto)"
  log "  REVIEW_PROJECT_RULES_FILE=$(env_value REVIEW_PROJECT_RULES_FILE .dev-code-review/review-rules.md)"
  log "  REVIEW_PROJECT_RULES_MAX_BYTES=$(env_value REVIEW_PROJECT_RULES_MAX_BYTES 20000)"
  log "  REVIEW_RULES_MODE=$(env_value REVIEW_RULES_MODE append)"
  log "  REVIEW_POST_COMMENTS=$(env_value REVIEW_POST_COMMENTS false)"
  log "  REVIEW_COMMENT_MAX_FINDINGS=$(env_value REVIEW_COMMENT_MAX_FINDINGS 10)"
  log "  REVIEW_NOTIFY_WECHAT=$(env_value REVIEW_NOTIFY_WECHAT false)"
  log "  WECHAT_WEBHOOK_URL=$(env_secret_state WECHAT_WEBHOOK_URL)"
  log "  WECHAT_NOTIFY_ON=$(env_value WECHAT_NOTIFY_ON always)"
  log "  WECHAT_NOTIFY_STYLE=$(env_value WECHAT_NOTIFY_STYLE fun)"
  log "  WECHAT_NOTIFY_MAX_FINDINGS=$(env_value WECHAT_NOTIFY_MAX_FINDINGS 3)"
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
  if is_review_disabled; then
    log "review is disabled by REVIEW_ENABLED/REVIEW_SCOPE_MODE; OCR LLM variables are not required"
  else
    [[ -n "${OCR_LLM_URL:-}" ]] || missing+=("OCR_LLM_URL")
    [[ -n "${OCR_LLM_TOKEN:-}" ]] || missing+=("OCR_LLM_TOKEN")
    [[ -n "${OCR_LLM_MODEL:-}" ]] || missing+=("OCR_LLM_MODEL")
  fi

  if (( ${#missing[@]} > 0 )); then
    fail "missing required environment variable(s): ${missing[*]}"
  fi
}

configure_ocr() {
  if is_review_disabled; then
    log "skip ocr configuration because review is disabled"
    return 0
  fi

  if ! command -v ocr >/dev/null 2>&1; then
    log "ocr command not found; review script will report OCR execution failure"
    return 0
  fi

  log "configuring ocr LLM endpoint: url=$(env_value OCR_LLM_URL), model=$(env_value OCR_LLM_MODEL), token=$(env_secret_state OCR_LLM_TOKEN)"
  ocr config set llm.url "$OCR_LLM_URL" >/dev/null
  ocr config set llm.auth_token "$OCR_LLM_TOKEN" >/dev/null
  ocr config set llm.model "$OCR_LLM_MODEL" >/dev/null

  if [[ -n "${OCR_USE_ANTHROPIC:-}" ]]; then
    log "configuring ocr optional setting: OCR_USE_ANTHROPIC=$OCR_USE_ANTHROPIC"
    ocr config set llm.use_anthropic "$OCR_USE_ANTHROPIC" >/dev/null
  fi
  if [[ -n "${OCR_LLM_AUTH_HEADER:-}" ]]; then
    log "configuring ocr optional setting: OCR_LLM_AUTH_HEADER=<set, masked>"
    ocr config set llm.auth_header "$OCR_LLM_AUTH_HEADER" >/dev/null
  fi
  if [[ -n "${OCR_LLM_EXTRA_HEADERS:-}" ]]; then
    log "configuring ocr optional setting: OCR_LLM_EXTRA_HEADERS=<set, masked>"
    ocr config set llm.extra_headers "$OCR_LLM_EXTRA_HEADERS" >/dev/null
  fi
  if [[ -n "${OCR_LLM_EXTRA_BODY:-}" ]]; then
    log "configuring ocr optional setting: OCR_LLM_EXTRA_BODY=<set, masked>"
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

  log_entry_config
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
