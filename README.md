# Dev Code Review

`dev-code-review` is a GitLab/OpenCodeReview helper project for reviewing merge requests before they are merged into the `dev` branch.

It follows the same broad shape as the sibling `cicd` project:

- OpenCode agent assets under `.opencode/`
- GitLab merge request gate under `gitlab-merge-review/`
- deterministic Python scripts under `scripts/`
- Docker entrypoint under `docker/`
- Word report template under `templates/`
- sample offline inputs under `input/`

## Main Artifacts

The workflow writes diagnostic files to `review-output/`, but the GitLab CI template uploads only the final concise report files:

| File | Purpose |
| --- | --- |
| `代码审计报告.md` | Markdown report, suitable for GitLab preview |
| `代码审计报告.docx` | Word report, suitable for formal audit archiving |

When `REVIEW_POST_COMMENTS=true`, the job also posts a concise audit summary to the merge request and tries to add Critical/High findings as GitLab diff line comments.
When `REVIEW_NOTIFY_WECHAT=true`, the job sends a lightweight WeCom group robot notification after the audit report is generated.

Diagnostic files may still be generated locally in `review-output/`:

| File | Purpose |
| --- | --- |
| `review-context.json` | GitLab merge request context |
| `gitlab-context.json` | normalized GitLab MR context when available |
| `changed-files.txt` | files changed by `base_commit..to_commit` |
| `diff.patch` | patch reviewed by OCR/model |
| `ocr-result.json` | raw OCR-compatible review JSON |
| `ocr-stderr.log` | OCR stderr |
| `review-report.json` | source of truth for PASS/BLOCKED |

## Report Template

The workflow generates both `代码审计报告.md` and `代码审计报告.docx`. The Word report uses `templates/ai-agent-code-review-template.docx` by default. The generated reports follow the attached report template and include:

1. 基础信息
2. Review结论
3. 异常处理审查
4. 安全审查
5. 性能审查
6. 代码规范审查
7. CSV安全合规专项审查
8. 问题明细汇总
9. 准入确认

OCR comments are normalized into those dimensions by `gitlab-merge-review/scripts/evaluate_review.py`.
The OCR prompt requires Chinese finding descriptions and suggestions while preserving necessary code identifiers.

To use another Word template, pass `REVIEW_DOCX_TEMPLATE=/path/to/template.docx` when running the GitLab or local review script.

## GitLab Merge Gate

The trigger point is the GitLab merge request pipeline. The job only runs when the target branch is `dev`. If the report contains `critical` or `high` findings, the script exits non-zero and GitLab blocks the merge when the project requires successful pipelines before merge.

Add the example CI job to the business repository root `.gitlab-ci.yml`, or include the same job from your CI template:

```yaml
include:
  - local: 'gitlab-merge-review/merge-review-ci-template.yml'
```

Build and push the image used by GitLab Runner:

```bash
docker build -f docker/Dockerfile -t registry.example.com/platform/dev-code-review:latest .
docker push registry.example.com/platform/dev-code-review:latest
```

The Docker build pins npm package versions by default:

| Package | Version |
| --- | --- |
| `@alibaba-group/open-code-review` | `1.7.14` |
| `opencode-ai` | `1.18.4` |

To upgrade deliberately, change the Dockerfile defaults or pass build args:

```bash
docker build -f docker/Dockerfile \
  --build-arg OCR_PACKAGE_VERSION=1.7.14 \
  --build-arg OPENCODE_PACKAGE_VERSION=1.18.4 \
  -t registry.example.com/platform/dev-code-review:latest .
```

Run locally inside a checked-out business repo:

```bash
export REVIEW_TARGET_BRANCH=dev
export REVIEW_BASE_COMMIT=$(git merge-base HEAD origin/dev)
export REVIEW_TO_COMMIT=$(git rev-parse HEAD)
export OCR_LLM_URL=https://your-llm-gateway/v1
export OCR_LLM_TOKEN=your-token
export OCR_LLM_MODEL=your-model

bash /path/to/dev-code-review/gitlab-merge-review/scripts/run-gitlab-merge-review.sh
```

For GitLab CI, see `gitlab-merge-review/merge-review-ci-template.yml`.

Required GitLab CI/CD variables:

| Variable | Purpose |
| --- | --- |
| `DEV_CODE_REVIEW_IMAGE` | Docker image address, for example `registry.example.com/platform/dev-code-review:latest` |
| `OCR_LLM_URL` | LLM gateway URL used by `ocr` |
| `OCR_LLM_TOKEN` | LLM token, set as masked/protected when possible |
| `OCR_LLM_MODEL` | model name |
| `GITLAB_TOKEN` | optional; required when `REVIEW_POST_COMMENTS=true`; use a GitLab personal/project access token with permission to create MR notes |
| `WECHAT_WEBHOOK_URL` | optional; required when `REVIEW_NOTIFY_WECHAT=true`; WeCom group robot webhook URL |

Optional GitLab CI/CD variables:

| Variable | Purpose |
| --- | --- |
| `REVIEW_POST_COMMENTS` | set to `true` to post a concise audit summary and line comments back to the merge request |
| `REVIEW_COMMENT_MAX_FINDINGS` | maximum Critical/High findings to comment on changed lines; default `10` |
| `REVIEW_NOTIFY_WECHAT` | set to `true` to send a WeCom group robot notification |
| `WECHAT_NOTIFY_ON` | notification condition: `always`, `blocked`, or `pass`; default `always` |
| `WECHAT_NOTIFY_STYLE` | notification tone: `fun` or `formal`; default `fun` |

Comment posting is best-effort: if GitLab notes/discussions cannot be created, the job prints a warning but keeps the original audit result. Critical/High findings still make the job fail and block the merge when successful pipelines are required.
WeCom notification is also best-effort. It only sends a lightweight summary and links readers back to GitLab for line comments and full reports. The default `fun` style uses group-chat copy like:

```text
代码审计小剧场收工

本次挑战者：gaoshan

项目：dev-code-review-test
MR：feature/v1.0 -> dev
结果：审计通过，可以合并
风险：Critical 0 / High 0 / Medium 0 / Low 0

这轮比较丝滑，去 GitLab 确认后继续流程。
打开 GitLab 审计现场
```

For blocked audits, the result is sent as `代码合并失败，审计未通过`.

To make the review block merging, enable the GitLab project setting that requires successful pipelines before merge.

