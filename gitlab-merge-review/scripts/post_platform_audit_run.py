#!/usr/bin/env python3
"""Post audit result data to the lightweight audit platform."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SEVERITY_KEYS = ("Critical", "High", "Medium", "Low")


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def lower_env(name: str, default: str = "") -> str:
    return env(name, default).lower()


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"warning: failed to read json {path}: {exc}", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        print(f"warning: failed to read text {path}: {exc}", file=sys.stderr)
        return ""


def first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def should_post(status: str) -> bool:
    mode = lower_env("REVIEW_CALLBACK_ON", "always")
    status_upper = status.upper()
    if mode in {"never", "false", "off", "0", "no"}:
        return False
    if mode == "always":
        return True
    if mode in {"pass", "passed", "success", "succeeded"}:
        return status_upper == "PASS"
    if mode in {"blocked", "failed", "fail", "failure"}:
        return status_upper != "PASS"
    print(f"warning: unknown REVIEW_CALLBACK_ON={mode}, fallback to always", file=sys.stderr)
    return True


def normalize_counts(decision: dict[str, Any]) -> dict[str, int]:
    raw = decision.get("severity_counts")
    raw = raw if isinstance(raw, dict) else {}
    return {key: safe_int(raw.get(key, raw.get(key.lower(), 0))) for key in SEVERITY_KEYS}


def normalize_findings(findings: Any) -> list[dict[str, Any]]:
    if not isinstance(findings, list):
        return []
    normalized: list[dict[str, Any]] = []
    for finding in findings[:200]:
        if not isinstance(finding, dict):
            continue
        severity = first_text(finding.get("severity"), finding.get("level"))
        dimension = first_text(finding.get("dimension"), finding.get("category"), finding.get("type"))
        file_path = first_text(finding.get("file_path"), finding.get("path"), finding.get("file"))
        description = first_text(
            finding.get("description"),
            finding.get("content"),
            finding.get("message"),
            finding.get("summary"),
            finding.get("risk"),
            finding.get("title"),
        )
        normalized.append(
            {
                "severity": severity,
                "level": severity,
                "dimension": dimension,
                "category": dimension,
                "file_path": file_path,
                "file": file_path,
                "line": finding.get("line") or finding.get("line_number") or finding.get("new_line"),
                "description": description,
                "summary": description,
                "suggestion": first_text(
                    finding.get("suggestion"),
                    finding.get("fix"),
                    finding.get("recommendation"),
                ),
            }
        )
    return normalized


def changed_file_paths(report: dict[str, Any]) -> list[str]:
    files = report.get("changed_files")
    if not isinstance(files, list):
        return []
    result: list[str] = []
    for item in files:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            path = first_text(item.get("path"), item.get("new_path"), item.get("file_path"))
            if path:
                result.append(path)
    return result


def make_payload(report: dict[str, Any], output_dir: Path, markdown_path: Path) -> dict[str, Any]:
    context = report.get("context") if isinstance(report.get("context"), dict) else {}
    decision = report.get("decision") if isinstance(report.get("decision"), dict) else {}
    gitlab_context = read_json(output_dir / "gitlab-context.json")
    gitlab_project = gitlab_context.get("project") if isinstance(gitlab_context.get("project"), dict) else {}
    selected_mr = (
        gitlab_context.get("selected_merge_request")
        if isinstance(gitlab_context.get("selected_merge_request"), dict)
        else {}
    )
    mr_author = selected_mr.get("author") if isinstance(selected_mr.get("author"), dict) else {}
    scope = read_json(output_dir / "review-scope.json")
    report_markdown = read_text(markdown_path)

    status = first_text(decision.get("status"))
    if not status:
        status = "PASS" if env("REVIEW_EVAL_STATUS", "1") == "0" else "BLOCKED"
    status = status.upper()

    project_path = first_text(
        context.get("project_path"),
        gitlab_context.get("project_path"),
        env("CI_PROJECT_PATH"),
        env("CI_PROJECT_NAME"),
    )
    mr_iid = first_text(
        context.get("merge_request_iid"),
        context.get("gitlab_mr_iid"),
        gitlab_context.get("merge_request_iid"),
        selected_mr.get("iid"),
        env("CI_MERGE_REQUEST_IID"),
    )
    source_branch = first_text(
        context.get("source_branch"),
        gitlab_context.get("source_branch"),
        selected_mr.get("source_branch"),
        env("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME"),
        env("REVIEW_SOURCE_BRANCH"),
    )
    target_branch = first_text(
        context.get("target_branch"),
        gitlab_context.get("target_branch"),
        selected_mr.get("target_branch"),
        env("CI_MERGE_REQUEST_TARGET_BRANCH_NAME"),
        env("REVIEW_TARGET_BRANCH"),
    )
    pipeline_url = first_text(context.get("pipeline_url"), env("CI_PIPELINE_URL"))
    mr_url = first_text(
        context.get("merge_request_url"),
        context.get("gitlab_mr_web_url"),
        selected_mr.get("web_url"),
        env("CI_MERGE_REQUEST_PROJECT_URL"),
    )
    if mr_url and mr_iid and "/merge_requests/" not in mr_url:
        mr_url = f"{mr_url.rstrip('/')}/-/merge_requests/{mr_iid}"

    repository_url = first_text(
        context.get("repository_url"),
        context.get("project_url"),
        context.get("gitlab_project_url"),
        gitlab_project.get("url"),
        env("CI_PROJECT_URL"),
    )
    operator = first_text(
        context.get("trigger_user"),
        context.get("user"),
        context.get("gitlab_mr_author"),
        gitlab_context.get("user"),
        mr_author.get("username"),
        mr_author.get("name"),
        env("GITLAB_USER_LOGIN"),
        env("GITLAB_USER_NAME"),
    )
    severity_counts = normalize_counts(decision)
    risk_counts = {key.lower(): value for key, value in severity_counts.items()}
    findings = normalize_findings(report.get("findings"))
    duration_seconds = safe_int(env("REVIEW_DURATION_SECONDS"), -1)

    run_id = first_text(
        env("CI_JOB_ID"),
        "-".join(part for part in (project_path.replace("/", "-"), mr_iid, env("CI_COMMIT_SHORT_SHA")) if part),
    )

    payload = {
        "id": run_id,
        "run_id": run_id,
        "created_at": first_text(report.get("generated_at"), datetime.now(timezone.utc).isoformat()),
        "status": status,
        "result": status,
        "project": project_path,
        "project_name": first_text(env("CI_PROJECT_NAME"), project_path.rsplit("/", 1)[-1] if project_path else ""),
        "project_path": project_path,
        "repository": repository_url,
        "repository_url": repository_url,
        "source_branch": source_branch,
        "target_branch": target_branch,
        "mr_iid": mr_iid,
        "mr_id": first_text(context.get("merge_request_id"), env("CI_MERGE_REQUEST_ID")),
        "mr_title": first_text(context.get("gitlab_mr_title"), selected_mr.get("title"), env("CI_MERGE_REQUEST_TITLE")),
        "mr_url": mr_url,
        "gitlab_mr_url": mr_url,
        "pipeline_id": first_text(env("CI_PIPELINE_ID")),
        "pipeline_url": pipeline_url,
        "gitlab_pipeline_url": pipeline_url,
        "job_id": first_text(env("CI_JOB_ID")),
        "job_url": first_text(env("CI_JOB_URL")),
        "operator": operator,
        "trigger_user": operator,
        "base_commit": first_text(context.get("base_commit"), env("REVIEW_BASE_COMMIT")),
        "to_commit": first_text(context.get("to_commit"), env("REVIEW_TO_COMMIT"), env("CI_COMMIT_SHA")),
        "commit_sha": first_text(env("CI_COMMIT_SHA"), context.get("to_commit")),
        "review_scope": first_text(context.get("review_scope"), f"{source_branch} -> {target_branch}".strip()),
        "counts": risk_counts,
        "risk_counts": risk_counts,
        "severity_counts": severity_counts,
        "blocking_reasons": decision.get("blocking_reasons", []),
        "warnings": decision.get("warnings", []),
        "issues": findings,
        "findings": findings,
        "reviewed_files": scope.get("reviewed_files") if isinstance(scope.get("reviewed_files"), list) else changed_file_paths(report),
        "skipped_files": scope.get("skipped_files") if isinstance(scope.get("skipped_files"), list) else [],
        "report_markdown": report_markdown,
    }
    if duration_seconds >= 0:
        payload["duration_seconds"] = duration_seconds
    return payload


def post_payload(url: str, token: str, payload: dict[str, Any], timeout: int) -> tuple[int, str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-Audit-Token"] = token
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read().decode("utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--output-dir", default="review-output")
    parser.add_argument("--markdown")
    args = parser.parse_args()

    url = env("REVIEW_CALLBACK_URL") or env("AUDIT_PLATFORM_URL")
    if not url:
        print("skip audit platform callback: missing REVIEW_CALLBACK_URL/AUDIT_PLATFORM_URL")
        return 0

    report = read_json(Path(args.report))
    if not report:
        print(f"warning: audit platform callback report is missing or invalid: {args.report}", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir)
    markdown_path = Path(args.markdown) if args.markdown else output_dir / "代码审计报告.md"
    payload = make_payload(report, output_dir, markdown_path)
    if not should_post(str(payload.get("status", ""))):
        print(f"skip audit platform callback: REVIEW_CALLBACK_ON={lower_env('REVIEW_CALLBACK_ON', 'always')}, status={payload.get('status')}")
        return 0

    if not payload.get("report_markdown"):
        print(f"warning: audit platform callback markdown report is missing or empty: {markdown_path}", file=sys.stderr)
        return 2

    timeout_text = env("REVIEW_CALLBACK_TIMEOUT_SECONDS", "10")
    timeout = int(timeout_text) if timeout_text.isdigit() and int(timeout_text) > 0 else 10
    token = env("REVIEW_CALLBACK_TOKEN") or env("AUDIT_PLATFORM_TOKEN")

    try:
        status, _ = post_payload(url, token, payload, timeout)
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace").strip()[:500]
        detail = f", response={response_body}" if response_body else ""
        print(f"warning: audit platform callback failed: HTTP {exc.code} {exc.reason}{detail}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"warning: audit platform callback failed: {exc}", file=sys.stderr)
        return 1

    print(
        "sent audit platform callback: "
        f"http_status={status}, run_id={payload.get('id')}, "
        f"review_status={payload.get('status')}, findings={len(payload.get('findings', []))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
