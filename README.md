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

The workflow writes review artifacts to `review-output/`:

| File | Purpose |
| --- | --- |
| `review-context.json` | GitLab merge request context |
| `gitlab-context.json` | normalized GitLab MR context when available |
| `changed-files.txt` | files changed by `base_commit..to_commit` |
| `diff.patch` | patch reviewed by OCR/model |
| `ocr-result.json` | raw OCR-compatible review JSON |
| `ocr-stderr.log` | OCR stderr |
| `review-report.json` | source of truth for PASS/BLOCKED |
| `code-review-report.md` | human-readable report |
| `代码审计报告.docx` | Word report |

## Report Template

The Word report is generated as `代码审计报告.docx` from `templates/ai-agent-code-review-template.docx` by default. The generated document follows the attached report template and includes:

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
| `GITLAB_TOKEN` | optional; only needed when the job must call GitLab MR APIs beyond the default CI context |

To make the review block merging, enable the GitLab project setting that requires successful pipelines before merge.

