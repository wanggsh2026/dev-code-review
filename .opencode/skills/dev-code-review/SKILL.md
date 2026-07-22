# Dev Code Review

Use this skill when GitLab or the business Agent platform needs code review before a merge request is merged into the `dev` branch.

## Goal

Answer one question:

```text
What issues would be introduced by merging this GitLab merge request into dev?
```

The final report must include four fixed sections:

- 异常
- 安全
- 性能
- 规范

## Required Inputs

Read review context from GitLab CI variables or prepared input files:

- `REVIEW_TARGET_BRANCH`: target branch; default `dev`
- `REVIEW_BASE_COMMIT`: base commit before the merge, defaulting to `CI_MERGE_REQUEST_DIFF_BASE_SHA`
- `REVIEW_TO_COMMIT`: commit to review; default `HEAD`
- `REVIEW_OUTPUT_DIR`: artifact output directory; default `review-output`
- `REVIEW_CONFIG`: review rule config; default `gitlab-merge-review/review-config.example.json`
- `OCR_LLM_URL`, `OCR_LLM_TOKEN`, `OCR_LLM_MODEL`: OpenCodeReview LLM configuration

Optional GitLab context:

- `GITLAB_TOKEN`
- `GITLAB_PROJECT_ID`
- `GITLAB_PROJECT_URL`
- `GITLAB_MR_IID`

## Workflow

### Business Agent Platform Offline Mode

When running inside the business Agent platform without direct OCR CLI access, use this write path:

1. Read:

   ```text
   input/review-context.json
   input/changed-files.txt
   input/diff.patch
   ```

2. Review `input/diff.patch` with the platform model and produce an OCR-compatible JSON object:

   ```json
   {
     "comments": [
       {
         "severity": "high",
         "category": "security",
         "path": "path/to/file",
         "line": null,
         "content": "finding"
       }
     ]
   }
   ```

3. Pass the JSON result to the deterministic writer:

   ```bash
   python3 scripts/run_platform_offline_review.py --review-json-stdin
   ```

4. Treat `output/review-output/review-report.json` as the source of truth. The conversational answer must match this file.

### GitLab Merge Request Mode

1. Resolve review context.
   - Identify `to_commit`.
   - Identify `base_commit` from `REVIEW_BASE_COMMIT`, `CI_MERGE_REQUEST_DIFF_BASE_SHA`, or `git merge-base`.
   - Confirm the target branch is `dev` unless config overrides it.
   - Write `review-output/review-context.json`.

2. Resolve review diff.
   - Review `base_commit..to_commit`.
   - Write:

     ```text
     review-output/changed-files.txt
     review-output/diff.patch
     ```

3. Collect GitLab context when available.
   - Read MR title, description, labels, author, source branch, target branch, and web URL.
   - Write `review-output/gitlab-context.json`.

4. Run OpenCodeReview.
   - Use JSON output for deterministic parsing.

   Example command:

   ```bash
   ocr review --from "$BASE_COMMIT" --to "$TO_COMMIT" --format json --audience agent --background-file review-output/review-background.md
   ```

   Write:

   ```text
   review-output/ocr-result.json
   review-output/ocr-stderr.log
   ```

5. Evaluate review result.
   - Normalize OCR comments.
   - Classify findings into 异常, 安全, 性能, 规范.
   - Count findings by severity and category.
   - Write `review-output/review-report.json`.

6. Generate report documents.
   - Always generate Markdown:

     ```text
     review-output/code-review-report.md
     ```

   - Generate Word when the generator script is available:

     ```text
     review-output/代码审计报告.docx
     ```

7. Return review result.
   - Exit `0` when the review result is `PASS`.
   - Exit non-zero when the review result is `BLOCKED` or required evidence cannot be produced.

## Output Contract

The workflow must produce these artifacts:

```text
review-output/review-context.json
review-output/gitlab-context.json
review-output/changed-files.txt
review-output/diff.patch
review-output/ocr-result.json
review-output/ocr-stderr.log
review-output/review-report.json
review-output/code-review-report.md
review-output/代码审计报告.docx
```

`review-report.json` is the source of truth for GitLab blocking behavior. GitLab CI should archive the whole `review-output/` directory regardless of pass or fail.

## Default Blocking Rules

- Required context is missing.
- Target branch is not `dev` when `require_target_branch_dev` is true.
- OCR cannot run successfully and `require_ocr_success` is true.
- OCR reports any configured blocking severity, typically `critical` or `high`; the CI job exits non-zero so GitLab blocks merge when "Pipelines must succeed" is enabled.
- OCR reports more `medium` findings than allowed by config.

Warnings do not block unless explicitly configured:

- GitLab MR context is missing.
- No changed files are found.
- Low severity findings exist.

## Safety Notes

- Do not modify business code from this skill.
- Do not place business repositories or generated artifacts inside `.opencode/`.
- Do not embed tokens in repository files or Docker images. GitLab CI variables should provide secrets at runtime.
- Treat the current merge diff as the primary anchor. GitLab MR information is supporting evidence.
