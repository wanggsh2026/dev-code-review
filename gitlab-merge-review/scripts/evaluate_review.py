#!/usr/bin/env python3
import argparse
import json
import os
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
    "security": ["security", "auth", "permission", "injection", "xss", "csrf", "secret", "sensitive", "csv", "formula", "公式注入", "安全"],
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
        "suggestion": str(
            comment.get("suggestion")
            or comment.get("recommendation")
            or comment.get("fix")
            or comment.get("advice")
            or ""
        ).strip(),
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
        blocking.append(f"目标分支必须是 {expected_target}，当前为 {target_branch or '空'}")

    if config.get("require_base_commit", True) and not context.get("base_commit"):
        blocking.append("base_commit 不能为空")

    if config.get("require_changed_files", False) and not changed_files:
        blocking.append("变更文件列表不能为空")
    elif not changed_files:
        warnings.append("未发现变更文件")

    if config.get("require_gitlab_context", False):
        if not context.get("gitlab_project_id") and not context.get("gitlab_project_url"):
            blocking.append("缺少必要的 GitLab 上下文")
    elif not context.get("gitlab_mr_iid"):
        warnings.append("未提供 GitLab MR IID，报告将使用提交级上下文")

    if config.get("require_ocr_success", True) and ocr_exit_code != 0:
        detail = ocr_stderr.strip().splitlines()
        suffix = f": {detail[0]}" if detail else ""
        blocking.append(f"OCR 执行失败，退出码 {ocr_exit_code}{suffix}")

    for severity in config.get("blocking_severities", ["critical", "high"]):
        count = counts.get(str(severity).lower(), 0)
        if count > 0:
            blocking.append(f"OCR 发现 {count} 个 {severity} 级别问题")

    max_medium = config.get("max_medium_findings")
    if isinstance(max_medium, int) and counts.get("medium", 0) > max_medium:
        blocking.append(f"OCR 发现 {counts.get('medium', 0)} 个 medium 级别问题，超过上限 {max_medium}")

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


def first_non_empty(*values):
    for value in values:
        if value:
            return str(value)
    return ""


def short_commit(value):
    text = str(value or "")
    return text[:8] if len(text) > 8 else text


def project_name(context):
    path = first_non_empty(context.get("project_path"), context.get("project_url"), context.get("gitlab_project_url"))
    return path.rstrip("/").split("/")[-1].removesuffix(".git") if path else ""


def review_range(context):
    base = short_commit(context.get("base_commit"))
    to = short_commit(context.get("to_commit"))
    mr = context.get("gitlab_mr_iid")
    parts = []
    if base or to:
        parts.append(f"{base}..{to}")
    if mr:
        parts.append(f"MR !{mr}")
    return " / ".join(parts)


def finding_suggestion(item):
    return first_non_empty(
        item.get("suggestion"),
        item.get("recommendation"),
        item.get("fix"),
        item.get("advice"),
        "请按问题描述修复，并补充必要的回归验证。",
    )


def is_csv_finding(item):
    haystack = " ".join(
        str(item.get(key) or "").lower()
        for key in ("category", "dimension", "path", "content", "suggestion", "recommendation")
    )
    return "csv" in haystack or "安全合规" in haystack


def severity_count(decision, severity):
    return decision.get("severity_counts", {}).get(severity, 0)


def append_table(lines, headers, rows):
    lines.append(markdown_table_row(headers))
    lines.append(markdown_table_row(["---"] * len(headers)))
    if rows:
        for row in rows:
            lines.append(markdown_table_row(row))
    else:
        lines.append(markdown_table_row(["-"] * len(headers)))


def append_findings_section(lines, title, items, problem_label):
    lines.extend(["", f"## {title}", ""])
    append_table(
        lines,
        ["文件路径", "行号", problem_label, "风险等级", "修复建议"],
        [
            [
                item.get("path", ""),
                item.get("line", ""),
                item.get("content", ""),
                item.get("severity", ""),
                finding_suggestion(item),
            ]
            for item in items
        ],
    )


def write_markdown(path, report):
    context = report["context"]
    decision = report["decision"]
    groups = report["findings_by_dimension"]
    changed_files = report["changed_files"]
    now = report["generated_at"]
    labels = dict(DIMENSIONS)
    review_model = first_non_empty(report.get("review_model"), os.environ.get("OCR_LLM_MODEL"), "________")

    lines = [
        "# 代码审计报告",
        "",
        "## 1. 基础信息",
        "",
        markdown_table_row(["项目", "内容"]),
        markdown_table_row(["---", "---"]),
        markdown_table_row(["项目/服务", project_name(context)]),
        markdown_table_row(["代码仓库", first_non_empty(context.get("project_url"), context.get("gitlab_project_url"), context.get("project_path"))]),
        markdown_table_row(["源分支", context.get("source_branch", "")]),
        markdown_table_row(["目标分支", context.get("target_branch", "")]),
        markdown_table_row(["Review 范围", review_range(context)]),
        markdown_table_row(["触发方式", "GitLab MR" if context.get("gitlab_mr_iid") else context.get("pipeline_source", "")]),
        markdown_table_row(["提交人/研发负责人", context.get("trigger_user", "")]),
        markdown_table_row(["Review 日期", now[:10]]),
        markdown_table_row(["Review 工具/模型", f"open-code-review / ocr / LLM 模型：{review_model}"]),
        "",
        "## 2. Review 结论",
        "",
        f"- 结论：{decision['status']}",
        "",
    ]
    append_table(
        lines,
        ["风险等级", "数量", "是否阻断", "说明"],
        [
            ["Critical", severity_count(decision, "critical"), "是" if severity_count(decision, "critical") else "否", "安全漏洞、数据破坏、权限绕过、线上不可恢复风险"],
            ["High", severity_count(decision, "high"), "是" if severity_count(decision, "high") else "否", "高概率故障、核心流程异常、明显安全/性能风险"],
            ["Medium", severity_count(decision, "medium"), "否", "需修复或确认，但可按策略决定是否阻断"],
            ["Low", severity_count(decision, "low"), "否", "规范、可维护性、轻微边界问题"],
        ],
    )

    lines.extend(["", "### 阻断原因", ""])
    if decision["blocking_reasons"]:
        lines.extend([f"- {item}" for item in decision["blocking_reasons"]])
    else:
        lines.append("- 无")

    if decision["warnings"]:
        lines.extend(["", "### 提示", ""])
        lines.extend([f"- {item}" for item in decision["warnings"]])

    lines.extend(["", "### Review 变更文件", ""])
    if changed_files:
        lines.extend([f"- {item}" for item in changed_files])
    else:
        lines.append("- 无")

    append_findings_section(lines, "3. 异常处理审查", groups.get("exception", []), "问题描述")
    append_findings_section(lines, "4. 安全审查", groups.get("security", []), "安全风险")
    append_findings_section(lines, "5. 性能审查", groups.get("performance", []), "性能问题")
    append_findings_section(lines, "6. 代码规范审查", groups.get("standard", []), "规范问题")

    csv_items = [item for item in report.get("findings", []) if is_csv_finding(item)]
    lines.extend(["", "## 7. CSV 安全合规专项审查", ""])
    append_table(
        lines,
        ["审查项", "审查结果", "风险等级", "依据/流水号", "处理建议"],
        [
            [
                item.get("category") or "CSV 安全合规审查",
                "不通过" if item.get("severity") in {"critical", "high"} else "需确认",
                item.get("severity", ""),
                first_non_empty(item.get("ticket"), item.get("trace_id"), item.get("report_id"), item.get("external_url"), item.get("path")),
                finding_suggestion(item),
            ]
            for item in csv_items
        ] or [["CSV 安全合规接口/工具审查", "未执行或未命中", "-", "待接入 CSV 部门接口后记录", "如本次变更命中 CSV 安全合规范围，应调用 CSV 部门接口并记录审查结论。"]],
    )

    lines.extend(["", "## 8. 问题明细汇总", ""])
    append_table(
        lines,
        ["序号", "分类", "文件路径", "行号", "问题描述", "风险等级", "处理结论"],
        [
            [
                idx,
                "CSV 安全合规" if is_csv_finding(item) else labels.get(item.get("dimension"), item.get("dimension", "")),
                item.get("path", ""),
                item.get("line", ""),
                item.get("content", ""),
                item.get("severity", ""),
                "待修复 / 已修复 / 接受风险",
            ]
            for idx, item in enumerate(report.get("findings") or [], 1)
        ] or [["1", "-", "-", "-", "未发现阻断级问题", "-", "-"]],
    )

    lines.extend([
        "",
        "## 9. 准入确认",
        "",
        "- [ ] 所有 Critical/High 问题已修复或已有负责人确认",
        "- [ ] CSV 安全合规审查结果已完成确认",
        "- [ ] 影响范围、回归范围、测试结论已补充到提测确认单",
        "- [ ] 如本次为 BLOCKED，已在 GitLab/通知渠道同步阻断原因",
    ])

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
        "review_tool": "open-code-review / ocr",
        "review_model": os.environ.get("OCR_LLM_MODEL", ""),
    }

    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(args.markdown, report)

    if decision["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
