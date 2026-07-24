#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path


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


def build_fun_message(report):
    context = report.get("context") or {}
    decision = report.get("decision") or {}
    counts = decision.get("severity_counts") or {}

    status = str(decision.get("status") or "-").upper()
    user = first_non_empty(context.get("trigger_user"), "匿名挑战者")
    source = first_non_empty(context.get("source_branch"), "-")
    target = first_non_empty(context.get("target_branch"), "-")
    link = mr_url(context)

    if status == "PASS":
        title = "代码审计小剧场收工"
        subtitle = f"本次挑战者：{user}"
        result = "审计通过，可以合并"
        hint = "这轮比较丝滑，去 GitLab 确认后继续流程。"
    else:
        title = "代码审计小剧场开演了"
        subtitle = f"本次挑战者：{user}"
        result = "代码合并失败，审计未通过"
        hint = "别慌，问题已经贴到 GitLab 了，按评论处理就行。"

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
        "",
        hint,
    ]

    if link:
        lines.extend(["", f"[打开 GitLab 审计现场]({link})"])
    return "\n".join(lines)


def build_formal_message(report):
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
        content = build_formal_message(report)
    else:
        content = build_fun_message(report)

    try:
        post_wechat(webhook_url, content)
    except Exception as exc:
        print(f"warning: WeCom notification failed: {exc}", file=sys.stderr)
        return 0

    print("sent WeCom audit notification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
