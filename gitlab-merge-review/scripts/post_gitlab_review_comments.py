#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path


SUMMARY_MARKER = "<!-- dev-code-review:summary -->"
FINDING_MARKER_PREFIX = "dev-code-review:finding:"
BLOCKING_SEVERITIES = {"critical", "high"}
DIMENSION_LABELS = {
    "exception": "异常",
    "security": "安全",
    "performance": "性能",
    "standard": "规范",
}


def load_json(path):
    with Path(path).open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def env_first(*names):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return ""


def first_non_empty(*values):
    for value in values:
        if value:
            return str(value)
    return ""


def infer_api_url(context):
    explicit = env_first("CI_API_V4_URL", "GITLAB_API_URL")
    if explicit:
        return explicit.rstrip("/")

    project_url = first_non_empty(context.get("project_url"), context.get("gitlab_project_url"))
    if not project_url:
        return ""
    parsed = urllib.parse.urlparse(project_url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}/api/v4"


def quote_project_id(project_id):
    return urllib.parse.quote(str(project_id), safe="")


class GitLabClient:
    def __init__(self, api_url, token, token_type):
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.token_type = token_type

    def headers(self):
        headers = {"Content-Type": "application/json"}
        if self.token_type == "job":
            headers["JOB-TOKEN"] = self.token
        else:
            headers["PRIVATE-TOKEN"] = self.token
        return headers

    def request(self, method, path, payload=None):
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.api_url + path,
            data=data,
            headers=self.headers(),
            method=method,
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
            if not body:
                return None
            return json.loads(body)


def finding_hash(item):
    raw = "|".join(
        [
            str(item.get("severity") or ""),
            str(item.get("path") or ""),
            str(item.get("line") or ""),
            str(item.get("content") or ""),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def marker_for(item):
    return f"<!-- {FINDING_MARKER_PREFIX}{finding_hash(item)} -->"


def severity_label(severity):
    text = str(severity or "").lower()
    return {
        "critical": "Critical",
        "high": "High",
        "medium": "Medium",
        "low": "Low",
    }.get(text, text or "-")


def dimension_label(item):
    return DIMENSION_LABELS.get(str(item.get("dimension") or ""), item.get("category") or "-")


def short_text(value, limit=260):
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def sorted_findings(findings):
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(
        findings,
        key=lambda item: (
            rank.get(str(item.get("severity") or "").lower(), 9),
            str(item.get("path") or ""),
            int(item.get("line") or 0) if str(item.get("line") or "").isdigit() else 0,
        ),
    )


def summary_body(report, max_findings):
    decision = report.get("decision") or {}
    counts = decision.get("severity_counts") or {}
    context = report.get("context") or {}
    findings = sorted_findings(report.get("findings") or [])
    blocking_findings = [
        item for item in findings if str(item.get("severity") or "").lower() in BLOCKING_SEVERITIES
    ]
    shown = blocking_findings[:max_findings]

    status = decision.get("status") or "-"
    source = context.get("source_branch") or "-"
    target = context.get("target_branch") or "-"
    pipeline_url = context.get("pipeline_url") or ""

    lines = [
        SUMMARY_MARKER,
        "## 代码审计摘要",
        "",
        f"- 审计结论：**{status}**",
        f"- 分支：`{source}` -> `{target}`",
        (
            "- 风险统计："
            f"Critical {counts.get('critical', 0)}，"
            f"High {counts.get('high', 0)}，"
            f"Medium {counts.get('medium', 0)}，"
            f"Low {counts.get('low', 0)}"
        ),
    ]
    if pipeline_url:
        lines.append(f"- 完整报告：请在 [本次 Pipeline Artifacts]({pipeline_url}) 下载 `代码审计报告.md` / `代码审计报告.docx`")
    else:
        lines.append("- 完整报告：请在本次 Job Artifacts 下载 `代码审计报告.md` / `代码审计报告.docx`")

    if decision.get("blocking_reasons"):
        lines.extend(["", "### 阻断原因"])
        for reason in decision["blocking_reasons"][:8]:
            lines.append(f"- {reason}")

    lines.extend(["", "### 重点问题"])
    if shown:
        for idx, item in enumerate(shown, 1):
            path = item.get("path") or "-"
            line = item.get("line") or "-"
            severity = severity_label(item.get("severity"))
            dimension = dimension_label(item)
            content = short_text(item.get("content"))
            suggestion = short_text(item.get("suggestion") or item.get("recommendation") or item.get("fix"))
            lines.append(f"{idx}. **{severity} / {dimension}** `{path}:{line}`")
            lines.append(f"   - 问题：{content or '-'}")
            if suggestion:
                lines.append(f"   - 建议：{suggestion}")
    else:
        lines.append("- 未发现 Critical/High 级别问题。")

    if len(blocking_findings) > len(shown):
        lines.append(f"- 另有 {len(blocking_findings) - len(shown)} 条 Critical/High 问题请查看完整报告。")

    return "\n".join(lines)


def finding_body(item):
    severity = severity_label(item.get("severity"))
    dimension = dimension_label(item)
    content = short_text(item.get("content"), 420)
    suggestion = short_text(item.get("suggestion") or item.get("recommendation") or item.get("fix"), 420)
    lines = [
        marker_for(item),
        f"**代码审计：{severity} / {dimension}**",
        "",
        f"问题：{content or '-'}",
    ]
    if suggestion:
        lines.extend(["", f"建议：{suggestion}"])
    return "\n".join(lines)


def get_notes(client, project_id, mr_iid):
    path = f"/projects/{quote_project_id(project_id)}/merge_requests/{mr_iid}/notes?per_page=100"
    return client.request("GET", path) or []


def upsert_summary_note(client, project_id, mr_iid, body):
    notes = get_notes(client, project_id, mr_iid)
    for note in notes:
        if SUMMARY_MARKER in str(note.get("body") or ""):
            path = f"/projects/{quote_project_id(project_id)}/merge_requests/{mr_iid}/notes/{note['id']}"
            client.request("PUT", path, {"body": body})
            print("updated GitLab MR audit summary note")
            return
    path = f"/projects/{quote_project_id(project_id)}/merge_requests/{mr_iid}/notes"
    client.request("POST", path, {"body": body})
    print("created GitLab MR audit summary note")


def get_discussion_markers(client, project_id, mr_iid):
    path = f"/projects/{quote_project_id(project_id)}/merge_requests/{mr_iid}/discussions?per_page=100"
    discussions = client.request("GET", path) or []
    markers = set()
    for discussion in discussions:
        for note in discussion.get("notes") or []:
            body = str(note.get("body") or "")
            if FINDING_MARKER_PREFIX in body:
                start = body.find(FINDING_MARKER_PREFIX)
                end = body.find("-->", start)
                if end != -1:
                    markers.add(body[start:end].strip())
    return markers


def latest_mr_version(client, project_id, mr_iid):
    path = f"/projects/{quote_project_id(project_id)}/merge_requests/{mr_iid}/versions?per_page=20"
    versions = client.request("GET", path) or []
    if not versions:
        return None
    return max(versions, key=lambda item: int(item.get("id") or 0))


def post_line_discussions(client, project_id, mr_iid, report, max_findings):
    version = latest_mr_version(client, project_id, mr_iid)
    if not version:
        print("skip GitLab line comments: MR version metadata not found")
        return

    base_sha = version.get("base_commit_sha")
    start_sha = version.get("start_commit_sha")
    head_sha = version.get("head_commit_sha")
    if not base_sha or not start_sha or not head_sha:
        print("skip GitLab line comments: MR position sha metadata is incomplete")
        return

    existing_markers = get_discussion_markers(client, project_id, mr_iid)
    findings = [
        item
        for item in sorted_findings(report.get("findings") or [])
        if str(item.get("severity") or "").lower() in BLOCKING_SEVERITIES
        and item.get("path")
        and str(item.get("line") or "").isdigit()
    ][:max_findings]

    created = 0
    for item in findings:
        marker = marker_for(item).removeprefix("<!-- ").removesuffix(" -->")
        if marker in existing_markers:
            continue
        payload = {
            "body": finding_body(item),
            "position": {
                "position_type": "text",
                "base_sha": base_sha,
                "start_sha": start_sha,
                "head_sha": head_sha,
                "old_path": item["path"],
                "new_path": item["path"],
                "new_line": int(item["line"]),
            },
        }
        path = f"/projects/{quote_project_id(project_id)}/merge_requests/{mr_iid}/discussions"
        try:
            client.request("POST", path, payload)
            created += 1
        except Exception as exc:
            print(
                f"warning: failed to create GitLab line comment for {item.get('path')}:{item.get('line')}: {exc}",
                file=sys.stderr,
            )
    print(f"created {created} GitLab line audit comments")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--max-findings", type=int, default=int(os.environ.get("REVIEW_COMMENT_MAX_FINDINGS", "10")))
    args = parser.parse_args()

    report = load_json(args.report)
    context = report.get("context") or {}
    project_id = first_non_empty(context.get("gitlab_project_id"), context.get("project_id"), env_first("CI_PROJECT_ID"))
    mr_iid = first_non_empty(context.get("gitlab_mr_iid"), env_first("CI_MERGE_REQUEST_IID"))
    api_url = infer_api_url(context)

    token = env_first("GITLAB_TOKEN", "GITLAB_PRIVATE_TOKEN", "GITLAB_API_TOKEN")
    token_type = "private"
    if not token:
        token = env_first("CI_JOB_TOKEN")
        token_type = "job"

    if not api_url or not project_id or not mr_iid or not token:
        print("skip GitLab review comments: missing api_url/project_id/mr_iid/token")
        return 0

    client = GitLabClient(api_url, token, token_type)
    body = summary_body(report, args.max_findings)
    try:
        upsert_summary_note(client, project_id, mr_iid, body)
        post_line_discussions(client, project_id, mr_iid, report, args.max_findings)
    except Exception as exc:
        print(f"warning: GitLab review comment posting failed: {exc}", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
