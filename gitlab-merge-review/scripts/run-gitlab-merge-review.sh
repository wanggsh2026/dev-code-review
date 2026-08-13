#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_ROOT="$(cd "$DEMO_DIR/.." && pwd)"

ROOT_DIR="${REVIEW_WORKSPACE:-${CI_PROJECT_DIR:-$(pwd)}}"
cd "$ROOT_DIR"

TIMING_TOTAL_START="$(date +%s)"
TIMING_STAGE_NAME=""
TIMING_STAGE_START=0

timing_start() {
  TIMING_STAGE_NAME="$1"
  TIMING_STAGE_START="$(date +%s)"
  echo "[dev-code-review][timing] start ${TIMING_STAGE_NAME}"
}

timing_end() {
  local status="${1:-0}"
  local ended_at elapsed
  ended_at="$(date +%s)"
  elapsed=$((ended_at - TIMING_STAGE_START))
  echo "[dev-code-review][timing] end ${TIMING_STAGE_NAME}: ${elapsed}s status=${status}"
}

timing_total_end() {
  local status="${1:-0}"
  local ended_at elapsed
  ended_at="$(date +%s)"
  elapsed=$((ended_at - TIMING_TOTAL_START))
  echo "[dev-code-review][timing] total: ${elapsed}s status=${status}"
}

config_log() {
  echo "[dev-code-review][config] $*"
}

flow_log() {
  echo "[dev-code-review][flow] $*"
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

file_state() {
  local path="$1"
  if [[ -f "$path" ]]; then
    printf 'exists'
  else
    printf 'missing'
  fi
}

short_ref() {
  local value="${1:-}"
  if [[ -z "$value" ]]; then
    printf '<empty>'
  elif (( ${#value} > 12 )); then
    printf '%s...' "${value:0:12}"
  else
    printf '%s' "$value"
  fi
}

lower_value() {
  printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]'
}

is_truthy() {
  case "$(lower_value "${1:-}")" in
    true|1|yes|y|on) return 0 ;;
    *) return 1 ;;
  esac
}

is_falsey() {
  case "$(lower_value "${1:-}")" in
    false|0|no|n|off) return 0 ;;
    *) return 1 ;;
  esac
}

resolve_workspace_path() {
  local path="$1"
  if [[ -z "$path" ]]; then
    printf ''
  elif [[ "$path" == /* ]]; then
    printf '%s' "$path"
  else
    printf '%s/%s' "$ROOT_DIR" "$path"
  fi
}

file_size_bytes() {
  local path="$1"
  if [[ -f "$path" ]]; then
    wc -c < "$path" | tr -d '[:space:]'
  else
    printf '0'
  fi
}

OUTPUT_DIR="${REVIEW_OUTPUT_DIR:-review-output}"
CONFIG_PATH="${REVIEW_CONFIG:-$DEMO_DIR/review-config.example.json}"
EVALUATOR_PATH="${REVIEW_EVALUATOR:-$SCRIPT_DIR/evaluate_review.py}"
SCOPE_FILTER="${REVIEW_SCOPE_FILTER:-$SCRIPT_DIR/filter_review_scope.py}"
GITLAB_CONTEXT_SCRIPT="${REVIEW_GITLAB_CONTEXT_SCRIPT:-$APP_ROOT/scripts/gitlab_context.py}"
DOCX_GENERATOR="${REVIEW_DOCX_GENERATOR:-$APP_ROOT/scripts/generate_review_docx.py}"
DOCX_TEMPLATE="${REVIEW_DOCX_TEMPLATE:-$APP_ROOT/templates/ai-agent-code-review-template.docx}"
COMMENT_POSTER="${REVIEW_COMMENT_POSTER:-$SCRIPT_DIR/post_gitlab_review_comments.py}"
WECHAT_NOTIFIER="${REVIEW_WECHAT_NOTIFIER:-$SCRIPT_DIR/post_wechat_notification.py}"
PLATFORM_CALLBACK="${REVIEW_PLATFORM_CALLBACK:-$SCRIPT_DIR/post_platform_audit_run.py}"
mkdir -p "$OUTPUT_DIR"

TARGET_BRANCH="${REVIEW_TARGET_BRANCH:-${CI_MERGE_REQUEST_TARGET_BRANCH_NAME:-dev}}"
TO_COMMIT="${REVIEW_TO_COMMIT:-${CI_COMMIT_SHA:-$(git rev-parse HEAD)}}"
SOURCE_BRANCH="${REVIEW_SOURCE_BRANCH:-${CI_MERGE_REQUEST_SOURCE_BRANCH_NAME:-$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)}}"
BASE_COMMIT="${REVIEW_BASE_COMMIT:-${GITLAB_MERGE_REQUEST_DIFF_BASE_SHA:-${CI_MERGE_REQUEST_DIFF_BASE_SHA:-}}}"

POST_COMMENTS="$(lower_value "${REVIEW_POST_COMMENTS:-false}")"
COMMENT_MAX_FINDINGS="${REVIEW_COMMENT_MAX_FINDINGS:-10}"
NOTIFY_WECHAT="$(lower_value "${REVIEW_NOTIFY_WECHAT:-false}")"
WECHAT_ON="$(lower_value "${WECHAT_NOTIFY_ON:-always}")"
WECHAT_STYLE="$(lower_value "${WECHAT_NOTIFY_STYLE:-fun}")"
WECHAT_MAX_FINDINGS="${WECHAT_NOTIFY_MAX_FINDINGS:-3}"
CALLBACK_URL="${REVIEW_CALLBACK_URL:-${AUDIT_PLATFORM_URL:-}}"
CALLBACK_ON="$(lower_value "${REVIEW_CALLBACK_ON:-always}")"
CALLBACK_TIMEOUT_SECONDS="${REVIEW_CALLBACK_TIMEOUT_SECONDS:-10}"
REVIEW_ENABLED_VALUE="$(lower_value "${REVIEW_ENABLED:-true}")"
SCOPE_MODE="$(lower_value "${REVIEW_SCOPE_MODE:-changed}")"
RULES_MODE="$(lower_value "${REVIEW_RULES_MODE:-append}")"
USE_PROJECT_RULES="$(lower_value "${REVIEW_USE_PROJECT_RULES:-auto}")"
PROJECT_RULES_FILE="${REVIEW_PROJECT_RULES_FILE:-.dev-code-review/review-rules.md}"
PROJECT_RULES_MAX_BYTES="${REVIEW_PROJECT_RULES_MAX_BYTES:-20000}"
PROJECT_RULES_PATH="$(resolve_workspace_path "$PROJECT_RULES_FILE")"

case "$SCOPE_MODE" in
  changed|full|none|disabled) ;;
  *)
    flow_log "invalid REVIEW_SCOPE_MODE=$SCOPE_MODE, fallback to changed"
    SCOPE_MODE="changed"
    ;;
esac

case "$RULES_MODE" in
  append|override) ;;
  *)
    flow_log "invalid REVIEW_RULES_MODE=$RULES_MODE, fallback to append"
    RULES_MODE="append"
    ;;
esac

case "$USE_PROJECT_RULES" in
  auto|true|false|1|0|yes|no|on|off|y|n) ;;
  *)
    flow_log "invalid REVIEW_USE_PROJECT_RULES=$USE_PROJECT_RULES, fallback to auto"
    USE_PROJECT_RULES="auto"
    ;;
esac

if ! [[ "$PROJECT_RULES_MAX_BYTES" =~ ^[0-9]+$ ]]; then
  flow_log "invalid REVIEW_PROJECT_RULES_MAX_BYTES=$PROJECT_RULES_MAX_BYTES, fallback to 20000"
  PROJECT_RULES_MAX_BYTES="20000"
fi

if is_falsey "$REVIEW_ENABLED_VALUE"; then
  flow_log "REVIEW_ENABLED=$REVIEW_ENABLED_VALUE, force REVIEW_SCOPE_MODE=disabled"
  SCOPE_MODE="disabled"
fi

config_log "effective environment values:"
config_log "  REVIEW_WORKSPACE=$ROOT_DIR"
config_log "  REVIEW_OUTPUT_DIR=$OUTPUT_DIR"
config_log "  REVIEW_CONFIG=$CONFIG_PATH ($(file_state "$CONFIG_PATH"))"
config_log "  REVIEW_EVALUATOR=$EVALUATOR_PATH ($(file_state "$EVALUATOR_PATH"))"
config_log "  REVIEW_SCOPE_FILTER=$SCOPE_FILTER ($(file_state "$SCOPE_FILTER"))"
config_log "  REVIEW_GITLAB_CONTEXT_SCRIPT=$GITLAB_CONTEXT_SCRIPT ($(file_state "$GITLAB_CONTEXT_SCRIPT"))"
config_log "  REVIEW_DOCX_GENERATOR=$DOCX_GENERATOR ($(file_state "$DOCX_GENERATOR"))"
config_log "  REVIEW_DOCX_TEMPLATE=$DOCX_TEMPLATE ($(file_state "$DOCX_TEMPLATE"))"
config_log "  REVIEW_COMMENT_POSTER=$COMMENT_POSTER ($(file_state "$COMMENT_POSTER"))"
config_log "  REVIEW_WECHAT_NOTIFIER=$WECHAT_NOTIFIER ($(file_state "$WECHAT_NOTIFIER"))"
config_log "  REVIEW_PLATFORM_CALLBACK=$PLATFORM_CALLBACK ($(file_state "$PLATFORM_CALLBACK"))"
config_log "  REVIEW_ENABLED=$REVIEW_ENABLED_VALUE (false disables OCR review and produces a pass report)"
config_log "  REVIEW_SCOPE_MODE=$SCOPE_MODE (changed=MR diff, full=full repository snapshot, none/disabled=skip OCR review)"
config_log "  REVIEW_USE_PROJECT_RULES=$USE_PROJECT_RULES (auto loads existing project rules, true tries to load project rules, false skips them)"
config_log "  REVIEW_PROJECT_RULES_FILE=$PROJECT_RULES_FILE (resolved=$PROJECT_RULES_PATH, file=$(file_state "$PROJECT_RULES_PATH"))"
config_log "  REVIEW_PROJECT_RULES_MAX_BYTES=$PROJECT_RULES_MAX_BYTES"
config_log "  REVIEW_RULES_MODE=$RULES_MODE (append=default rules plus project rules, override=project rules replace default audit rules)"
config_log "  REVIEW_POST_COMMENTS=$POST_COMMENTS"
config_log "  REVIEW_COMMENT_MAX_FINDINGS=$COMMENT_MAX_FINDINGS"
config_log "  GITLAB_TOKEN=$(env_secret_state GITLAB_TOKEN)"
config_log "  GITLAB_PRIVATE_TOKEN=$(env_secret_state GITLAB_PRIVATE_TOKEN)"
config_log "  GITLAB_API_TOKEN=$(env_secret_state GITLAB_API_TOKEN)"
config_log "  CI_JOB_TOKEN=$(env_secret_state CI_JOB_TOKEN)"
config_log "  REVIEW_NOTIFY_WECHAT=$NOTIFY_WECHAT"
config_log "  WECHAT_WEBHOOK_URL=$(env_secret_state WECHAT_WEBHOOK_URL)"
config_log "  WECHAT_NOTIFY_ON=$WECHAT_ON"
config_log "  WECHAT_NOTIFY_STYLE=$WECHAT_STYLE"
config_log "  WECHAT_NOTIFY_MAX_FINDINGS=$WECHAT_MAX_FINDINGS"
config_log "  REVIEW_CALLBACK_URL=$(env_value REVIEW_CALLBACK_URL)"
config_log "  AUDIT_PLATFORM_URL=$(env_value AUDIT_PLATFORM_URL)"
config_log "  REVIEW_CALLBACK_TOKEN=$(env_secret_state REVIEW_CALLBACK_TOKEN)"
config_log "  AUDIT_PLATFORM_TOKEN=$(env_secret_state AUDIT_PLATFORM_TOKEN)"
config_log "  REVIEW_CALLBACK_ON=$CALLBACK_ON"
config_log "  REVIEW_CALLBACK_TIMEOUT_SECONDS=$CALLBACK_TIMEOUT_SECONDS"
config_log "  OCR_LLM_URL=$(env_value OCR_LLM_URL)"
config_log "  OCR_LLM_MODEL=$(env_value OCR_LLM_MODEL)"
config_log "  OCR_LLM_TOKEN=$(env_secret_state OCR_LLM_TOKEN)"
config_log "  GIT_DEPTH=$(env_value GIT_DEPTH 100)"
config_log "derived review refs: source=$SOURCE_BRANCH target=$TARGET_BRANCH base=$(short_ref "$BASE_COMMIT") to=$(short_ref "$TO_COMMIT")"

if [[ -n "$TARGET_BRANCH" ]]; then
  flow_log "fetch target branch: target=$TARGET_BRANCH depth=$(env_value GIT_DEPTH 100)"
  timing_start "fetch target branch"
  git fetch origin "$TARGET_BRANCH" --depth="${GIT_DEPTH:-100}" >/dev/null 2>&1 || git fetch origin "$TARGET_BRANCH" >/dev/null 2>&1 || true
  timing_end 0
fi

timing_start "resolve base commit"
if [[ -z "$BASE_COMMIT" && "$TARGET_BRANCH" != "unknown" ]]; then
  if git rev-parse "origin/${TARGET_BRANCH}" >/dev/null 2>&1; then
    BASE_COMMIT="$(git merge-base "$TO_COMMIT" "origin/${TARGET_BRANCH}" || true)"
  elif git rev-parse "$TARGET_BRANCH" >/dev/null 2>&1; then
    BASE_COMMIT="$(git merge-base "$TO_COMMIT" "$TARGET_BRANCH" || true)"
  fi
fi

if [[ -z "$BASE_COMMIT" ]]; then
  if git rev-parse "${TO_COMMIT}~1" >/dev/null 2>&1; then
    BASE_COMMIT="$(git rev-parse "${TO_COMMIT}~1")"
  fi
fi
timing_end 0
flow_log "base commit resolved: base=$(short_ref "$BASE_COMMIT") to=$(short_ref "$TO_COMMIT")"

OCR_FROM_REF="$BASE_COMMIT"

timing_start "collect changed files and diff"
case "$SCOPE_MODE" in
  none|disabled)
    flow_log "collect changed files and diff: skipped because REVIEW_SCOPE_MODE=$SCOPE_MODE"
    : > "$OUTPUT_DIR/changed-files.raw.txt"
    : > "$OUTPUT_DIR/diff.raw.patch"
    ;;
  full)
    flow_log "collect changed files and diff: REVIEW_SCOPE_MODE=full, using full repository snapshot at to commit"
    EMPTY_TREE="$(git hash-object -t tree /dev/null)"
    OCR_FROM_REF="$EMPTY_TREE"
    git ls-tree -r --name-only "$TO_COMMIT" > "$OUTPUT_DIR/changed-files.raw.txt"
    git diff --no-ext-diff --unified=80 "$EMPTY_TREE" "$TO_COMMIT" > "$OUTPUT_DIR/diff.raw.patch" || true
    ;;
  changed)
    if [[ -n "$BASE_COMMIT" ]]; then
      flow_log "collect changed files and diff: REVIEW_SCOPE_MODE=changed, using git diff base..to"
      git diff --name-only "$BASE_COMMIT" "$TO_COMMIT" > "$OUTPUT_DIR/changed-files.raw.txt"
      git diff --no-ext-diff --unified=80 "$BASE_COMMIT" "$TO_COMMIT" > "$OUTPUT_DIR/diff.raw.patch" || true
    else
      flow_log "collect changed files and diff: base commit empty, falling back to git show"
      git show --name-only --format='' "$TO_COMMIT" > "$OUTPUT_DIR/changed-files.raw.txt" || true
      git show --format=medium --no-ext-diff --unified=80 "$TO_COMMIT" > "$OUTPUT_DIR/diff.raw.patch" || true
    fi
    ;;
esac
timing_end 0

timing_start "filter review scope"
if [[ "$SCOPE_MODE" == "none" || "$SCOPE_MODE" == "disabled" ]]; then
  flow_log "filter review scope: skipped because REVIEW_SCOPE_MODE=$SCOPE_MODE"
  : > "$OUTPUT_DIR/changed-files.txt"
  : > "$OUTPUT_DIR/diff.patch"
  cat > "$OUTPUT_DIR/review-scope.json" <<JSON
{
  "mode": "${SCOPE_MODE}",
  "reviewed": [],
  "skipped": []
}
JSON
elif [[ -f "$SCOPE_FILTER" ]]; then
  flow_log "filter review scope: run REVIEW_SCOPE_FILTER=$SCOPE_FILTER with config=$CONFIG_PATH"
  python3 "$SCOPE_FILTER" \
    --config "$CONFIG_PATH" \
    --changed-files "$OUTPUT_DIR/changed-files.raw.txt" \
    --diff "$OUTPUT_DIR/diff.raw.patch" \
    --output-changed-files "$OUTPUT_DIR/changed-files.txt" \
    --output-diff "$OUTPUT_DIR/diff.patch" \
    --summary "$OUTPUT_DIR/review-scope.json"
else
  flow_log "filter review scope: REVIEW_SCOPE_FILTER missing, use raw changed files and raw diff"
  cp "$OUTPUT_DIR/changed-files.raw.txt" "$OUTPUT_DIR/changed-files.txt"
  cp "$OUTPUT_DIR/diff.raw.patch" "$OUTPUT_DIR/diff.patch"
fi
timing_end 0

timing_start "write review context"
cat > "$OUTPUT_DIR/review-context.json" <<JSON
{
  "project_id": "${CI_PROJECT_ID:-${GITLAB_PROJECT_ID:-}}",
  "project_path": "${CI_PROJECT_PATH:-}",
  "project_url": "${CI_PROJECT_URL:-${GITLAB_PROJECT_URL:-}}",
  "pipeline_id": "${CI_PIPELINE_ID:-local}",
  "pipeline_url": "${CI_PIPELINE_URL:-}",
  "pipeline_source": "${CI_PIPELINE_SOURCE:-local}",
  "target_branch": "${TARGET_BRANCH}",
  "source_branch": "${SOURCE_BRANCH}",
  "base_commit": "${BASE_COMMIT}",
  "to_commit": "${TO_COMMIT}",
  "gitlab_project_id": "${GITLAB_PROJECT_ID:-${CI_PROJECT_ID:-}}",
  "gitlab_mr_iid": "${GITLAB_MR_IID:-${CI_MERGE_REQUEST_IID:-}}",
  "gitlab_mr_title": "${CI_MERGE_REQUEST_TITLE:-}",
  "gitlab_project_url": "${GITLAB_PROJECT_URL:-${CI_PROJECT_URL:-}}",
  "trigger_user": "${GITLAB_USER_LOGIN:-${GITLAB_USER_NAME:-}}"
}
JSON
timing_end 0

GITLAB_CONTEXT="$OUTPUT_DIR/gitlab-context.json"
timing_start "collect GitLab context"
if [[ -f "$GITLAB_CONTEXT_SCRIPT" ]]; then
  set +e
  python3 "$GITLAB_CONTEXT_SCRIPT" \
    --context "$OUTPUT_DIR/review-context.json" \
    --output "$GITLAB_CONTEXT" \
    --update-context "$OUTPUT_DIR/review-context.json"
  GITLAB_CONTEXT_STATUS=$?
  set -e
  if [[ "$GITLAB_CONTEXT_STATUS" -ne 0 ]]; then
    echo "GitLab context collection failed with exit code $GITLAB_CONTEXT_STATUS" >&2
  fi
else
  echo '{"status":"skipped","errors":["gitlab context script not found"]}' > "$GITLAB_CONTEXT"
fi
timing_end "${GITLAB_CONTEXT_STATUS:-0}"

PROJECT_RULES_OUTPUT="$OUTPUT_DIR/project-review-rules.md"
PROJECT_RULES_LOADED="false"

timing_start "load project review rules"
SHOULD_LOAD_PROJECT_RULES="false"
if [[ "$USE_PROJECT_RULES" == "auto" ]]; then
  if [[ -s "$PROJECT_RULES_PATH" ]]; then
    SHOULD_LOAD_PROJECT_RULES="true"
  fi
elif is_truthy "$USE_PROJECT_RULES"; then
  SHOULD_LOAD_PROJECT_RULES="true"
elif is_falsey "$USE_PROJECT_RULES"; then
  SHOULD_LOAD_PROJECT_RULES="false"
fi

if [[ "$SHOULD_LOAD_PROJECT_RULES" == "true" ]]; then
  if [[ ! -f "$PROJECT_RULES_PATH" ]]; then
    flow_log "project review rules: requested but file is missing: $PROJECT_RULES_PATH"
  elif [[ ! -s "$PROJECT_RULES_PATH" ]]; then
    flow_log "project review rules: requested but file is empty: $PROJECT_RULES_PATH"
  else
    PROJECT_RULES_BYTES="$(file_size_bytes "$PROJECT_RULES_PATH")"
    if (( PROJECT_RULES_BYTES > PROJECT_RULES_MAX_BYTES )); then
      flow_log "project review rules: skipped because file size ${PROJECT_RULES_BYTES} bytes exceeds REVIEW_PROJECT_RULES_MAX_BYTES=$PROJECT_RULES_MAX_BYTES"
    else
      cp "$PROJECT_RULES_PATH" "$PROJECT_RULES_OUTPUT"
      PROJECT_RULES_LOADED="true"
      flow_log "project review rules: loaded $PROJECT_RULES_FILE (${PROJECT_RULES_BYTES} bytes), REVIEW_RULES_MODE=$RULES_MODE"
    fi
  fi
else
  flow_log "project review rules: skipped because REVIEW_USE_PROJECT_RULES=$USE_PROJECT_RULES"
fi
timing_end 0

timing_start "write review background"
if [[ "$PROJECT_RULES_LOADED" == "true" && "$RULES_MODE" == "override" ]]; then
  flow_log "write review background: REVIEW_RULES_MODE=override, project rules replace default audit rules"
  cat > "$OUTPUT_DIR/review-background.md" <<EOF
Review this GitLab Merge Request before it is merged into target branch ${TARGET_BRANCH}.

Output requirements:
- All finding category, content, and suggestion fields must be written in Simplified Chinese.
- Keep code identifiers, method names, class names, field names, SQL fragments, and exception names unchanged.
- Output must remain parseable by open-code-review as JSON comments.
- Each finding should include severity, category, path, line, content, and suggestion when possible.

REVIEW_RULES_MODE=override is enabled.
Use the following project review rules as the primary audit standard. The default dev-code-review audit dimensions are not used as mandatory rules in this run.

Project review rules:
EOF
  cat "$PROJECT_RULES_OUTPUT" >> "$OUTPUT_DIR/review-background.md"
else
  flow_log "write review background: using default audit rules"
  cat > "$OUTPUT_DIR/review-background.md" <<EOF
Review this GitLab Merge Request before it is merged into target branch ${TARGET_BRANCH}.

Output requirements:
- All finding category, content, and suggestion fields must be written in Simplified Chinese.
- Keep code identifiers, method names, class names, field names, SQL fragments, and exception names unchanged.
- Output must remain parseable by open-code-review as JSON comments.
- Each finding should include severity, category, path, line, content, and suggestion when possible.

Default dev-code-review audit dimensions:
1. Exception handling: null dereference, boundary conditions, state transitions, error handling, compatibility, and logic defects.
2. Security: authentication, authorization, injection, sensitive information leakage, secret leakage in logs, dependency risk, path traversal, SSRF, XSS, command injection, and unsafe file handling.
3. Performance: slow SQL, N+1 queries, cache misuse, loop and batch processing issues, memory pressure, concurrency/resource risks, and timeout control.
4. Code standard: naming, layering, maintainability, duplicated code, missing or mismatched tests, API contracts, configuration conventions, logging conventions, and error response contracts.
5. CSV security compliance: this is an external security compliance review scope, not a generic CSV file format check. If this MR touches CSV security compliance scope, record whether the CSV department interface/tool was called and whether its Critical/High findings should block the merge.

Critical or High findings block the merge. Medium and Low findings are recorded for follow-up unless project policy says otherwise.
EOF
  if [[ "$PROJECT_RULES_LOADED" == "true" ]]; then
    flow_log "write review background: append project review rules to default audit rules"
    cat >> "$OUTPUT_DIR/review-background.md" <<EOF

Project-specific supplemental review rules:
EOF
    cat "$PROJECT_RULES_OUTPUT" >> "$OUTPUT_DIR/review-background.md"
  fi
fi

cat >> "$OUTPUT_DIR/review-background.md" <<EOF

Review scope:
- REVIEW_SCOPE_MODE=${SCOPE_MODE}.
- Only review files kept in changed-files.txt and diff.patch after scope filtering.
- changed: review the GitLab MR diff between base commit and target commit.
- full: review the full repository snapshot at target commit by comparing it with an empty tree.
- none/disabled: skip OCR review and produce an empty finding set.
- Ignore dependency directories, build artifacts, generated reports, documents, images, media files, and lock files.
- Business source files such as Java, frontend source, scripts, SQL, and runtime configuration files remain in scope.
EOF
timing_end 0

OCR_STATUS=0
OCR_STDERR="$OUTPUT_DIR/ocr-stderr.log"
OCR_RESULT="$OUTPUT_DIR/ocr-result.json"

timing_start "ocr review"
if [[ ! -s "$OUTPUT_DIR/changed-files.txt" ]]; then
  flow_log "ocr review: skipped because changed-files.txt is empty after scope filtering"
  OCR_STATUS=0
  echo "no reviewable files after scope filtering; skipped ocr review" > "$OCR_STDERR"
  echo '{"comments":[]}' > "$OCR_RESULT"
elif ! command -v ocr >/dev/null 2>&1; then
  flow_log "ocr review: skipped because ocr command is not available"
  OCR_STATUS=127
  echo "ocr command not found in review image or GitLab runner" > "$OCR_STDERR"
  echo '{"comments":[]}' > "$OCR_RESULT"
elif [[ -z "$OCR_FROM_REF" ]]; then
  flow_log "ocr review: skipped because OCR from ref is empty"
  OCR_STATUS=2
  echo "OCR from ref is empty; cannot run merge diff review" > "$OCR_STDERR"
  echo '{"comments":[]}' > "$OCR_RESULT"
else
  flow_log "ocr review: running ocr review from $(short_ref "$OCR_FROM_REF") to $(short_ref "$TO_COMMIT")"
  set +e
  ocr review \
    --from "$OCR_FROM_REF" \
    --to "$TO_COMMIT" \
    --format json \
    --audience agent \
    --background-file "$OUTPUT_DIR/review-background.md" \
    > "$OCR_RESULT" 2> "$OCR_STDERR"
  OCR_STATUS=$?
  set -e
fi

timing_end "$OCR_STATUS"

REPORT_PATH="$OUTPUT_DIR/review-report.json"
REPORT_MD="${REVIEW_MD:-$OUTPUT_DIR/代码审计报告.md}"
REPORT_DOCX="${REVIEW_DOCX:-$OUTPUT_DIR/代码审计报告.docx}"

timing_start "evaluate review report"
set +e
python3 "$EVALUATOR_PATH" \
  --config "$CONFIG_PATH" \
  --context "$OUTPUT_DIR/review-context.json" \
  --changed-files "$OUTPUT_DIR/changed-files.txt" \
  --diff "$OUTPUT_DIR/diff.patch" \
  --ocr-result "$OCR_RESULT" \
  --ocr-stderr "$OCR_STDERR" \
  --ocr-exit-code "$OCR_STATUS" \
  --report "$REPORT_PATH" \
  --markdown "$REPORT_MD"
EVAL_STATUS=$?
set -e
timing_end "$EVAL_STATUS"

timing_start "generate docx report"
if [[ -f "$DOCX_GENERATOR" && -f "$REPORT_PATH" ]]; then
  python3 "$DOCX_GENERATOR" \
    --report "$REPORT_PATH" \
    --output "$REPORT_DOCX" \
    --template "$DOCX_TEMPLATE"
else
  echo "docx report skipped: generator/report not found" >&2
fi
timing_end 0

timing_start "post GitLab review comments"
flow_log "post GitLab review comments decision: REVIEW_POST_COMMENTS=$POST_COMMENTS, poster=$(file_state "$COMMENT_POSTER"), report=$(file_state "$REPORT_PATH"), max_findings=$COMMENT_MAX_FINDINGS"
if [[ "$POST_COMMENTS" == "true" && -f "$COMMENT_POSTER" && -f "$REPORT_PATH" ]]; then
  flow_log "post GitLab review comments: running"
  set +e
  python3 "$COMMENT_POSTER" \
    --report "$REPORT_PATH" \
    --max-findings "$COMMENT_MAX_FINDINGS"
  COMMENT_STATUS=$?
  set -e
  if [[ "$COMMENT_STATUS" -ne 0 ]]; then
    echo "GitLab review comment posting failed with exit code $COMMENT_STATUS; continuing without changing review result" >&2
  fi
else
  [[ "$POST_COMMENTS" == "true" ]] || flow_log "post GitLab review comments: skipped because REVIEW_POST_COMMENTS is not true"
  [[ -f "$COMMENT_POSTER" ]] || flow_log "post GitLab review comments: skipped because REVIEW_COMMENT_POSTER is missing"
  [[ -f "$REPORT_PATH" ]] || flow_log "post GitLab review comments: skipped because review report is missing"
fi
timing_end "${COMMENT_STATUS:-0}"

timing_start "send WeCom notification"
flow_log "send WeCom notification decision: REVIEW_NOTIFY_WECHAT=$NOTIFY_WECHAT, notifier=$(file_state "$WECHAT_NOTIFIER"), report=$(file_state "$REPORT_PATH"), webhook=$(env_secret_state WECHAT_WEBHOOK_URL), notify_on=$WECHAT_ON, style=$WECHAT_STYLE"
if [[ "$NOTIFY_WECHAT" == "true" && -f "$WECHAT_NOTIFIER" && -f "$REPORT_PATH" ]]; then
  flow_log "send WeCom notification: running"
  set +e
  python3 "$WECHAT_NOTIFIER" \
    --report "$REPORT_PATH"
  WECHAT_STATUS=$?
  set -e
  if [[ "$WECHAT_STATUS" -ne 0 ]]; then
    echo "WeCom notification failed with exit code $WECHAT_STATUS; continuing without changing review result" >&2
  fi
else
  [[ "$NOTIFY_WECHAT" == "true" ]] || flow_log "send WeCom notification: skipped because REVIEW_NOTIFY_WECHAT is not true"
  [[ -f "$WECHAT_NOTIFIER" ]] || flow_log "send WeCom notification: skipped because REVIEW_WECHAT_NOTIFIER is missing"
  [[ -f "$REPORT_PATH" ]] || flow_log "send WeCom notification: skipped because review report is missing"
fi
timing_end "${WECHAT_STATUS:-0}"

timing_start "post audit result to platform"
CALLBACK_DURATION_SECONDS=$(( $(date +%s) - TIMING_TOTAL_START ))
flow_log "post audit result to platform decision: url=$(if [[ -n "$CALLBACK_URL" ]]; then printf 'configured'; else printf '<empty>'; fi), callback=$(file_state "$PLATFORM_CALLBACK"), report=$(file_state "$REPORT_PATH"), markdown=$(file_state "$REPORT_MD"), callback_on=$CALLBACK_ON, timeout=${CALLBACK_TIMEOUT_SECONDS}s"
if [[ -n "$CALLBACK_URL" && -f "$PLATFORM_CALLBACK" && -f "$REPORT_PATH" ]]; then
  flow_log "post audit result to platform: running"
  set +e
  REVIEW_EVAL_STATUS="$EVAL_STATUS" \
  REVIEW_DURATION_SECONDS="$CALLBACK_DURATION_SECONDS" \
  python3 "$PLATFORM_CALLBACK" \
    --report "$REPORT_PATH" \
    --output-dir "$OUTPUT_DIR" \
    --markdown "$REPORT_MD"
  CALLBACK_STATUS=$?
  set -e
  if [[ "$CALLBACK_STATUS" -ne 0 ]]; then
    echo "Audit platform callback failed with exit code $CALLBACK_STATUS; continuing without changing review result" >&2
  fi
else
  [[ -n "$CALLBACK_URL" ]] || flow_log "post audit result to platform: skipped because REVIEW_CALLBACK_URL/AUDIT_PLATFORM_URL is empty"
  [[ -f "$PLATFORM_CALLBACK" ]] || flow_log "post audit result to platform: skipped because REVIEW_PLATFORM_CALLBACK is missing"
  [[ -f "$REPORT_PATH" ]] || flow_log "post audit result to platform: skipped because review report is missing"
fi
timing_end "${CALLBACK_STATUS:-0}"

timing_total_end "$EVAL_STATUS"
exit "$EVAL_STATUS"
