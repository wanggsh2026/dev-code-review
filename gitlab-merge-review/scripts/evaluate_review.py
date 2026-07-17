#!/usr/bin/env python3
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


DIMENSIONS = [
    ("exception", "异常"),
    ("security", "安全"),
    ("performance", "性能"),
    ("standard", "规范"),
]

DEFAULT_ALIASES = {
    "exception": ["exception", "bug", "error", "correctness", "logic", "runtime", "null", "异常"],
    "security": ["security", "auth", "permission", "injection", "xss", "csrf", "secret", "sensitive", "安全"],
    "performance": ["performance", "perf", "latency", "memory", "cache", "sql", "n+1", "性能"],
    "standard": ["standard", "style", "maintainability", "readability", "test", "naming", "规范"],
}


def load_json(path, default):
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return default
    with p.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def read_text(path):
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def read_lines(path):
    return [line.strip() for line in read_text(path).splitlines() if line.strip()]


def collect_comments(raw):
    if isinstance(raw, list):
        comments = raw
    elif isinstance(raw, dict):
        comments = raw.get("comments", [])
    else:
        comments = []
    return [item for item in comments if isinstance(item, dict)]


def normalize_severity(value):
    text = str(value or "unknown").strip().lower()
    aliases = {
        "blocker": "critical",
        "fatal": "critical",
        "major": "high",
        "minor": "low",
        "info": "low",
        "warning": "medium",
    }
    return aliases.get(text, text or "unknown")


def dimension_for(comment, aliases):
    category = str(comment.get("category") or "").lower()
    content = str(comment.get("content") or comment.get("message") or comment.get("body") or "").lower()
    path = str(comment.get("path") or comment.get("file") or "").lower()
    haystack = " ".join([category, content, path])

    for key, _label in DIMENSIONS:
        for alias in aliases.get(key, DEFAULT_ALIASES[key]):
            if str(alias).lower() in haystack:
                return key
    return "standard"


def normalize_comment(comment, aliases):
    content = comment.get("content") or comment.get("message") or comment.get("body") or ""
    normalized = {
        "severity": normalize_severity(comment.get("severity")),
        "category": str(comment.get("category") or ""),
        "dimension": dimension_for(comment, aliases),
        "path": str(comment.get("path") or comment.get("file") or ""),
        "line": comment.get("line") or comment.get("start_line") or comment.get("new_line"),
        "content": str(content).strip(),
    }
    if not normalized["category"]:
        normalized["category"] = normalized["dimension"]
    return normalized


def severity_counts(comments):
    counts = {}
    for item in comments:
        severity = item["severity"]
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def dimension_counts(comments):
    counts = {key: 0 for key, _label in DIMENSIONS}
    for item in comments:
        key = item["dimension"]
        counts[key] = counts.get(key, 0) + 1
    return counts


def group_by_dimension(comments):
    groups = {key: [] for key, _label in DIMENSIONS}
    for item in comments:
        groups.setdefault(item["dimension"], []).append(item)
    return groups


def make_decision(config, context, changed_files, ocr_exit_code, ocr_stderr, comments):
    blocking = []
    warnings = []
    counts = severity_counts(comments)

    expected_target = str(config.get("target_branch", "dev"))
    target_branch = str(context.get("target_branch", ""))
    if config.get("require_target_branch_dev", True) and target_branch != expected_target:
        blocking.append(f"target branch must be {expected_target}, got {target_branch or 'empty'}")

    if config.get("require_base_commit", True) and not context.get("base_commit"):
        blocking.append("base_commit is required but empty")

    if config.get("require_changed_files", False) and not changed_files:
        blocking.append("changed files are required but empty")
    elif not changed_files:
        warnings.append("No changed files were found")

    if config.get("require_gitlab_context", False):
        if not context.get("gitlab_project_id") and not context.get("gitlab_project_url"):
            blocking.append("GitLab context is required but missing")
    elif not context.get("gitlab_mr_iid"):
        warnings.append("No GitLab MR IID was provided; report uses commit-level context")

    if config.get("require_ocr_success", True) and ocr_exit_code != 0:
        detail = ocr_stderr.strip().splitlines()
        suffix = f": {detail[0]}" if detail else ""
        blocking.append(f"OCR execution failed with exit code {ocr_exit_code}{suffix}")

    for severity in config.get("blocking_severities", ["critical", "high"]):
        count = counts.get(str(severity).lower(), 0)
        if count > 0:
            blocking.append(f"OCR found {count} {severity} finding(s)")

    max_medium = config.get("max_medium_findings")
    if isinstance(max_medium, int) and counts.get("medium", 0) > max_medium:
        blocking.append(f"OCR found {counts.get('medium', 0)} medium finding(s), limit is {max_medium}")

    return {
        "status": "PASS" if not blocking else "BLOCKED",
        "blocking_reasons": blocking,
        "warnings": warnings,
        "severity_counts": counts,
        "dimension_counts": dimension_counts(comments),
    }


def markdown_table_row(values):
    escaped = []
    for value in values:
        text = "" if value is None else str(value)
        text = text.replace("\n", "<br>").replace("|", "\\|")
        escaped.append(text)
    return "| " + " | ".join(escaped) + " |"


def write_markdown(path, report):
    context = report["context"]
    decision = report["decision"]
    groups = report["findings_by_dimension"]
    changed_files = report["changed_files"]
    now = report["generated_at"]

    lines = [
        "# Code Review 报告",
        "",
        f"- 结论: {decision['status']}",
        f"- 生成时间: {now}",
        f"- 目标分支: {context.get('target_branch', '')}",
        f"- 来源分支: {context.get('source_branch', '')}",
        f"- Base Commit: {context.get('base_commit', '')}",
        f"- To Commit: {context.get('to_commit', '')}",
        f"- GitLab Project: {context.get('project_path', '') or context.get('project_url', '')}",
        f"- Pipeline: {context.get('pipeline_url', '') or context.get('pipeline_id', '')}",
        f"- GitLab MR: {context.get('gitlab_mr_iid', '') or 'N/A'}",
        "",
        "## 总览",
        "",
        markdown_table_row(["维度", "数量"]),
        markdown_table_row(["---", "---"]),
    ]

    labels = dict(DIMENSIONS)
    for key, label in DIMENSIONS:
        lines.append(markdown_table_row([label, decision["dimension_counts"].get(key, 0)]))

    lines.extend([
        "",
        "## 变更文件",
        "",
    ])
    if changed_files:
        lines.extend([f"- {item}" for item in changed_files])
    else:
        lines.append("- 无")

    lines.extend([
        "",
        "## 阻断原因",
        "",
    ])
    if decision["blocking_reasons"]:
        lines.extend([f"- {item}" for item in decision["blocking_reasons"]])
    else:
        lines.append("- 无")

    if decision["warnings"]:
        lines.extend(["", "## 提示", ""])
        lines.extend([f"- {item}" for item in decision["warnings"]])

    for key, label in DIMENSIONS:
        lines.extend([
            "",
            f"## {label}",
            "",
            markdown_table_row(["级别", "文件", "行号", "问题"]),
            markdown_table_row(["---", "---", "---", "---"]),
        ])
        items = groups.get(key, [])
        if items:
            for item in items:
                lines.append(markdown_table_row([item["severity"], item["path"], item["line"] or "", item["content"]]))
        else:
            lines.append(markdown_table_row(["-", "-", "-", "未发现"]))

    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--changed-files", required=True)
    parser.add_argument("--diff", required=True)
    parser.add_argument("--ocr-result", required=True)
    parser.add_argument("--ocr-stderr", required=True)
    parser.add_argument("--ocr-exit-code", required=True, type=int)
    parser.add_argument("--report", required=True)
    parser.add_argument("--markdown", required=True)
    args = parser.parse_args()

    config = load_json(args.config, {})
    aliases = config.get("category_aliases") or DEFAULT_ALIASES
    context = load_json(args.context, {})
    raw_result = load_json(args.ocr_result, {})
    ocr_stderr = read_text(args.ocr_stderr)
    changed_files = read_lines(args.changed_files)
    comments = [normalize_comment(item, aliases) for item in collect_comments(raw_result)]

    decision = make_decision(config, context, changed_files, args.ocr_exit_code, ocr_stderr, comments)
    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
        "decision": decision,
        "context": context,
        "changed_files": changed_files,
        "findings": comments,
        "findings_by_dimension": group_by_dimension(comments),
        "ocr_exit_code": args.ocr_exit_code,
        "ocr_stderr": ocr_stderr,
        "diff_path": str(Path(args.diff)),
        "report_template": [label for _key, label in DIMENSIONS],
    }

    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(args.markdown, report)

    if decision["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
