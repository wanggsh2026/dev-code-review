#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_ROOT="$(cd "$DEMO_DIR/.." && pwd)"

ROOT_DIR="${REVIEW_WORKSPACE:-${CI_PROJECT_DIR:-$(pwd)}}"
cd "$ROOT_DIR"

OUTPUT_DIR="${REVIEW_OUTPUT_DIR:-review-output}"
CONFIG_PATH="${REVIEW_CONFIG:-$DEMO_DIR/review-config.example.json}"
EVALUATOR_PATH="${REVIEW_EVALUATOR:-$SCRIPT_DIR/evaluate_review.py}"
GITLAB_CONTEXT_SCRIPT="${REVIEW_GITLAB_CONTEXT_SCRIPT:-$APP_ROOT/scripts/gitlab_context.py}"
DOCX_GENERATOR="${REVIEW_DOCX_GENERATOR:-$APP_ROOT/scripts/generate_review_docx.py}"
DOCX_TEMPLATE="${REVIEW_DOCX_TEMPLATE:-$APP_ROOT/templates/ai-agent-code-review-template.docx}"
COMMENT_POSTER="${REVIEW_COMMENT_POSTER:-$SCRIPT_DIR/post_gitlab_review_comments.py}"
WECHAT_NOTIFIER="${REVIEW_WECHAT_NOTIFIER:-$SCRIPT_DIR/post_wechat_notification.py}"
mkdir -p "$OUTPUT_DIR"

TARGET_BRANCH="${REVIEW_TARGET_BRANCH:-${CI_MERGE_REQUEST_TARGET_BRANCH_NAME:-dev}}"
TO_COMMIT="${REVIEW_TO_COMMIT:-${CI_COMMIT_SHA:-$(git rev-parse HEAD)}}"
SOURCE_BRANCH="${REVIEW_SOURCE_BRANCH:-${CI_MERGE_REQUEST_SOURCE_BRANCH_NAME:-$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)}}"
BASE_COMMIT="${REVIEW_BASE_COMMIT:-${GITLAB_MERGE_REQUEST_DIFF_BASE_SHA:-${CI_MERGE_REQUEST_DIFF_BASE_SHA:-}}}"

if [[ -n "$TARGET_BRANCH" ]]; then
  git fetch origin "$TARGET_BRANCH" --depth="${GIT_DEPTH:-100}" >/dev/null 2>&1 || git fetch origin "$TARGET_BRANCH" >/dev/null 2>&1 || true
fi

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

if [[ -n "$BASE_COMMIT" ]]; then
  git diff --name-only "$BASE_COMMIT" "$TO_COMMIT" > "$OUTPUT_DIR/changed-files.txt"
  git diff --no-ext-diff --unified=80 "$BASE_COMMIT" "$TO_COMMIT" > "$OUTPUT_DIR/diff.patch" || true
else
  git show --name-only --format='' "$TO_COMMIT" > "$OUTPUT_DIR/changed-files.txt" || true
  git show --format=medium --no-ext-diff --unified=80 "$TO_COMMIT" > "$OUTPUT_DIR/diff.patch" || true
fi

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

GITLAB_CONTEXT="$OUTPUT_DIR/gitlab-context.json"
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

cat > "$OUTPUT_DIR/review-background.md" <<EOF
请对 GitLab Merge Request 合并到 ${TARGET_BRANCH} 分支的代码差异进行代码审计。

输出语言要求：
- 所有 finding 的 category、content、suggestion 必须使用中文。
- 如果模型内部先用英文分析，请在最终 JSON comments 中转换为中文。
- 保留必要的代码标识符、方法名、类名、字段名和异常类型原文。

重点关注并输出以下四类核心问题：
1. 异常：空指针、边界条件、状态流转、错误处理、兼容性、逻辑缺陷。
2. 安全：鉴权、越权、注入、敏感信息泄露、日志泄密、依赖风险。
3. 性能：慢 SQL、N+1 查询、缓存失效、循环/批量处理、内存和并发资源。
4. 规范：命名、可维护性、重复代码、测试缺失、接口契约、配置约定。

CSV 是外部安全合规审查域，不是逗号分隔文件格式检查。若本次变更命中 CSV 安全合规审查范围，请记录为 CSV 安全合规 finding；若已接入 CSV 部门接口/工具，应以其返回的审查结论、流水号或外部报告链接为准。

如果发现 critical 或 high 级别问题，CI 会失败并阻断 merge。输出必须能被解析为 OCR JSON comments，每条 finding 尽量包含 severity、category、path、line、content、suggestion。
EOF

OCR_STATUS=0
OCR_STDERR="$OUTPUT_DIR/ocr-stderr.log"
OCR_RESULT="$OUTPUT_DIR/ocr-result.json"

if ! command -v ocr >/dev/null 2>&1; then
  OCR_STATUS=127
  echo "ocr command not found in review image or GitLab runner" > "$OCR_STDERR"
  echo '{"comments":[]}' > "$OCR_RESULT"
elif [[ -z "$BASE_COMMIT" ]]; then
  OCR_STATUS=2
  echo "base commit is empty; cannot run dev merge diff review" > "$OCR_STDERR"
  echo '{"comments":[]}' > "$OCR_RESULT"
else
  set +e
  ocr review \
    --from "$BASE_COMMIT" \
    --to "$TO_COMMIT" \
    --format json \
    --audience agent \
    --background-file "$OUTPUT_DIR/review-background.md" \
    > "$OCR_RESULT" 2> "$OCR_STDERR"
  OCR_STATUS=$?
  set -e
fi

REPORT_PATH="$OUTPUT_DIR/review-report.json"
REPORT_MD="${REVIEW_MD:-$OUTPUT_DIR/代码审计报告.md}"
REPORT_DOCX="${REVIEW_DOCX:-$OUTPUT_DIR/代码审计报告.docx}"

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

if [[ -f "$DOCX_GENERATOR" && -f "$REPORT_PATH" ]]; then
  python3 "$DOCX_GENERATOR" \
    --report "$REPORT_PATH" \
    --output "$REPORT_DOCX" \
    --template "$DOCX_TEMPLATE"
else
  echo "docx report skipped: generator/report not found" >&2
fi

if [[ "${REVIEW_POST_COMMENTS:-false}" == "true" && -f "$COMMENT_POSTER" && -f "$REPORT_PATH" ]]; then
  set +e
  python3 "$COMMENT_POSTER" \
    --report "$REPORT_PATH" \
    --max-findings "${REVIEW_COMMENT_MAX_FINDINGS:-10}"
  COMMENT_STATUS=$?
  set -e
  if [[ "$COMMENT_STATUS" -ne 0 ]]; then
    echo "GitLab review comment posting failed with exit code $COMMENT_STATUS; continuing without changing review result" >&2
  fi
fi

if [[ "${REVIEW_NOTIFY_WECHAT:-false}" == "true" && -f "$WECHAT_NOTIFIER" && -f "$REPORT_PATH" ]]; then
  set +e
  python3 "$WECHAT_NOTIFIER" \
    --report "$REPORT_PATH" \
    --max-findings "${WECHAT_NOTIFY_MAX_FINDINGS:-3}"
  WECHAT_STATUS=$?
  set -e
  if [[ "$WECHAT_STATUS" -ne 0 ]]; then
    echo "WeCom notification failed with exit code $WECHAT_STATUS; continuing without changing review result" >&2
  fi
fi

exit "$EVAL_STATUS"
