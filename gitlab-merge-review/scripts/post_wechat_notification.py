#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path


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


def first_non_empty(*values):
    for value in values:
        if value:
            return str(value)
    return ""


def project_name(context):
    path = first_non_empty(context.get("project_path"), context.get("project_url"), context.get("gitlab_project_url"))
    if not path:
        return "-"
    return path.rstrip("/").split("/")[-1].removesuffix(".git")


def mr_url(context):
    project_url = first_non_empty(context.get("gitlab_project_url"), context.get("project_url"))
    mr_iid = context.get("gitlab_mr_iid")
    if project_url and mr_iid:
        return f"{project_url.rstrip('/')}/-/merge_requests/{mr_iid}"
    return first_non_empty(context.get("pipeline_url"), project_url)


def severity_label(value):
    text = str(value or "").lower()
    return {
        "critical": "Critical",
        "high": "High",
        "medium": "Medium",
        "low": "Low",
    }.get(text, text or "-")


def dimension_label(item):
    return DIMENSION_LABELS.get(str(item.get("dimension") or ""), item.get("category") or "-")


def short_text(value, limit=90):
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


def should_notify(status, mode):
    mode = str(mode or "always").lower()
    status = str(status or "").upper()
    if mode == "always":
        return True
    if mode == "blocked":
        return status != "PASS"
    if mode == "pass":
        return status == "PASS"
    return True


def build_fun_message(report, max_findings):
    context = report.get("context") or {}
    decision = report.get("decision") or {}
    counts = decision.get("severity_counts") or {}
    findings = [
        item
        for item in sorted_findings(report.get("findings") or [])
        if str(item.get("severity") or "").lower() in BLOCKING_SEVERITIES
    ][:max_findings]

    status = str(decision.get("status") or "-").upper()
    user = first_non_empty(context.get("trigger_user"), "匿名挑战者")
    source = first_non_empty(context.get("source_branch"), "-")
    target = first_non_empty(context.get("target_branch"), "-")
    link = mr_url(context)

    if status == "PASS":
        title = f"恭喜这位牛马：{user}"
        subtitle = "成功完成代码审计挑战，并且活着走出了 review 区。"
        result = "审计通过，可以合并"
    else:
        title = f"恭喜这位牛马：{user}"
        subtitle = "成功触发代码审计挑战，不过本轮 Boss 没打过。"
        result = "代码合并失败，审计未通过"

    lines = [
        f"**{title}**",
        "",
        subtitle,
        "",
        f"> 项目：{project_name(context)}",
        f"> MR：{source} -> {target}",
        f"> 结果：<font color=\"{'info' if status == 'PASS' else 'warning'}\">{result}</font>",
        (
            "> 风险："
            f"Critical {counts.get('critical', 0)} / "
            f"High {counts.get('high', 0)} / "
            f"Medium {counts.get('medium', 0)} / "
            f"Low {counts.get('low', 0)}"
        ),
    ]

    if findings:
        lines.extend(["", "**重点问题，先抓这几个：**"])
        for idx, item in enumerate(findings, 1):
            path = item.get("path") or "-"
            line = item.get("line") or "-"
            lines.append(
                f"{idx}. `{path}:{line}` {severity_label(item.get('severity'))} / "
                f"{dimension_label(item)}：{short_text(item.get('content'))}"
            )

    if link:
        lines.extend(["", f"[打开 GitLab 审计现场]({link})"])
    return "\n".join(lines)


def build_formal_message(report, max_findings):
    context = report.get("context") or {}
    decision = report.get("decision") or {}
    counts = decision.get("severity_counts") or {}
    status = str(decision.get("status") or "-").upper()
    source = first_non_empty(context.get("source_branch"), "-")
    target = first_non_empty(context.get("target_branch"), "-")
    link = mr_url(context)
    result = "审计通过，可以合并" if status == "PASS" else "代码合并失败，审计未通过"

    lines = [
        "**代码审计通知**",
        "",
        f"> 项目：{project_name(context)}",
        f"> 操作人：{first_non_empty(context.get('trigger_user'), '-')}",
        f"> MR：{source} -> {target}",
        f"> 结果：{result}",
        (
            "> 风险："
            f"Critical {counts.get('critical', 0)} / "
            f"High {counts.get('high', 0)} / "
            f"Medium {counts.get('medium', 0)} / "
            f"Low {counts.get('low', 0)}"
        ),
    ]
    if link:
        lines.extend(["", f"[查看 GitLab 审计结果]({link})"])
    return "\n".join(lines)


def post_wechat(webhook_url, content):
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "content": content,
        },
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8")
    result = json.loads(raw) if raw else {}
    if result.get("errcode") not in (None, 0):
        raise RuntimeError(f"WeCom webhook returned {result}")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--max-findings", type=int, default=int(os.environ.get("WECHAT_NOTIFY_MAX_FINDINGS", "3")))
    args = parser.parse_args()

    webhook_url = os.environ.get("WECHAT_WEBHOOK_URL", "")
    if not webhook_url:
        print("skip WeCom notification: missing WECHAT_WEBHOOK_URL")
        return 0

    report = load_json(args.report)
    status = (report.get("decision") or {}).get("status")
    if not should_notify(status, os.environ.get("WECHAT_NOTIFY_ON", "always")):
        print(f"skip WeCom notification: status {status} does not match WECHAT_NOTIFY_ON")
        return 0

    style = os.environ.get("WECHAT_NOTIFY_STYLE", "fun").lower()
    if style == "formal":
        content = build_formal_message(report, args.max_findings)
    else:
        content = build_fun_message(report, args.max_findings)

    try:
        post_wechat(webhook_url, content)
    except Exception as exc:
        print(f"warning: WeCom notification failed: {exc}", file=sys.stderr)
        return 0

    print("sent WeCom audit notification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
