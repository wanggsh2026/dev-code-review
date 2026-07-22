#!/usr/bin/env python3
import argparse
import html
import json
import os
import tempfile
import zipfile
from pathlib import Path


DIMENSIONS = [
    ("exception", "异常"),
    ("security", "安全"),
    ("performance", "性能"),
    ("standard", "规范"),
]

SEVERITIES = ["critical", "high", "medium", "low"]

DEFAULT_TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "ai-agent-code-review-template.docx"

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>
"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>
"""

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:rPr><w:sz w:val="21"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/>
    <w:rPr><w:b/><w:sz w:val="32"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:rPr><w:b/><w:sz w:val="26"/></w:rPr>
  </w:style>
</w:styles>
"""


def load_json(path):
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return {}
    with p.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def esc(value):
    return html.escape("" if value is None else str(value), quote=False)


def checked(condition):
    return "☑" if condition else "☐"


def paragraph(text, style="Normal"):
    style_xml = f'<w:pPr><w:pStyle w:val="{esc(style)}"/></w:pPr>' if style else ""
    chunks = str(text).splitlines() or [""]
    runs = []
    for idx, chunk in enumerate(chunks):
        if idx:
            runs.append("<w:r><w:br/></w:r>")
        runs.append(f"<w:r><w:t>{esc(chunk)}</w:t></w:r>")
    return f"<w:p>{style_xml}{''.join(runs)}</w:p>"


def checklist(text, ok=False):
    return paragraph(f"{checked(ok)} {text}")


def cell(value):
    lines = str("" if value is None else value).splitlines() or [""]
    return "<w:tc>" + "".join(f"<w:p><w:r><w:t>{esc(line)}</w:t></w:r></w:p>" for line in lines) + "</w:tc>"


def table(rows):
    xml = ["<w:tbl>"]
    xml.append(
        '<w:tblPr><w:tblW w:w="0" w:type="auto"/>'
        "<w:tblBorders>"
        '<w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
        '<w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
        '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
        '<w:right w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
        '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
        '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
        "</w:tblBorders></w:tblPr>"
    )
    for row in rows:
        xml.append("<w:tr>")
        for value in row:
            xml.append(cell(value))
        xml.append("</w:tr>")
    xml.append("</w:tbl>")
    return "".join(xml)


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
    if not path:
        return ""
    return path.rstrip("/").split("/")[-1].removesuffix(".git")


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


def trigger_text(context):
    source = str(context.get("pipeline_source") or "").lower()
    is_mr = bool(context.get("gitlab_mr_iid")) or source == "merge_request_event"
    is_manual = source in {"local", "web", "api", "pipeline", "manual"} and not is_mr
    is_platform = source not in {"", "local", "web", "api", "pipeline", "manual", "merge_request_event"} and not is_mr
    return f"{checked(is_mr)} GitLab MR  {checked(is_manual)} 手工触发  {checked(is_platform)} 平台/API触发"


def generated_date(report):
    generated_at = str(report.get("generated_at") or "")
    return generated_at[:10] if len(generated_at) >= 10 else ""


def severity_count(decision, severity):
    return decision.get("severity_counts", {}).get(severity, 0)


def severity_blocking_text(decision, severity):
    count = severity_count(decision, severity)
    blocks = decision.get("status") == "BLOCKED" and severity in {"critical", "high"} and count > 0
    return f"{checked(blocks)} 是 {checked(not blocks)} 否"


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


def csv_compliance_rows(report):
    rows = [["审查项", "审查结果", "风险等级", "依据/流水号", "处理建议"]]
    items = [item for item in report.get("findings", []) if is_csv_finding(item)]
    if not items:
        rows.append(["CSV安全合规接口/工具审查", "未执行或未命中", "-", "待接入CSV部门接口后记录", "如本次变更命中CSV安全合规范围，应调用CSV部门接口并记录审查结论。"])
        return rows
    for item in items:
        rows.append(
            [
                item.get("category") or "CSV安全合规审查",
                "不通过" if item.get("severity") in {"critical", "high"} else "需确认",
                item.get("severity", ""),
                first_non_empty(item.get("ticket"), item.get("trace_id"), item.get("report_id"), item.get("external_url"), item.get("path")),
                finding_suggestion(item),
            ]
        )
    return rows


def section_rows(items, problem_label):
    rows = [["文件路径", "行号", problem_label, "风险等级", "修复建议"]]
    if not items:
        rows.append(["-", "-", "未发现", "-", "-"])
        return rows
    for item in items:
        rows.append(
            [
                item.get("path", ""),
                item.get("line", ""),
                item.get("content", ""),
                item.get("severity", ""),
                finding_suggestion(item),
            ]
        )
    return rows


def summary_rows(report):
    rows = [["序号", "分类", "文件路径", "行号", "问题描述", "风险等级", "处理结论"]]
    findings = report.get("findings") or []
    labels = dict(DIMENSIONS)
    if not findings:
        rows.append(["1", "-", "-", "-", "未发现阻断级问题", "-", "☐ 待修复 ☐ 已修复 ☐ 接受风险"])
        return rows
    for idx, item in enumerate(findings, 1):
        dimension = "CSV安全合规" if is_csv_finding(item) else labels.get(item.get("dimension"), item.get("dimension", ""))
        rows.append(
            [
                idx,
                dimension,
                item.get("path", ""),
                item.get("line", ""),
                item.get("content", ""),
                item.get("severity", ""),
                "☑ 待修复 ☐ 已修复 ☐ 接受风险",
            ]
        )
    return rows


def build_body(report):
    context = report.get("context", {})
    decision = report.get("decision", {})
    groups = report.get("findings_by_dimension", {})
    changed_files = report.get("changed_files") or []
    review_model = first_non_empty(
        report.get("review_model"),
        report.get("model"),
        os.environ.get("OCR_LLM_MODEL"),
        "________",
    )
    status = decision.get("status", "UNKNOWN")
    pass_status = status == "PASS"
    blocked_status = status == "BLOCKED"
    needs_manual = status not in {"PASS", "BLOCKED"} or bool(decision.get("warnings"))

    body = [
        paragraph("代码审计报告", "Title"),
        paragraph("1. 基础信息", "Heading1"),
        table(
            [
                ["项目/服务", project_name(context)],
                ["代码仓库", first_non_empty(context.get("project_url"), context.get("gitlab_project_url"), context.get("project_path"))],
                ["源分支 / 目标分支", f"{context.get('source_branch', '')} / {context.get('target_branch', '')}"],
                ["Review范围", review_range(context)],
                ["触发方式", trigger_text(context)],
                ["提交人 / 研发负责人", context.get("trigger_user", "")],
                ["Review日期", generated_date(report)],
                ["Review工具 / 模型", f"open-code-review / ocr / LLM模型：{review_model}"],
            ]
        ),
        paragraph("2. Review结论", "Heading1"),
        checklist("PASS：未发现阻断级问题，可继续部署/合并", pass_status),
        checklist("BLOCKED：存在阻断级问题，需修复后重新Review", blocked_status),
        checklist("NEEDS_MANUAL_REVIEW：存在不确定风险，需人工复核", needs_manual and not pass_status),
        table(
            [
                ["风险等级", "数量", "是否阻断", "说明"],
                ["Critical", severity_count(decision, "critical"), severity_blocking_text(decision, "critical"), "安全漏洞、数据破坏、权限绕过、线上不可恢复风险"],
                ["High", severity_count(decision, "high"), severity_blocking_text(decision, "high"), "高概率故障、核心流程异常、明显安全/性能风险"],
                ["Medium", severity_count(decision, "medium"), severity_blocking_text(decision, "medium"), "需修复或确认，但可按策略决定是否阻断"],
                ["Low", severity_count(decision, "low"), severity_blocking_text(decision, "low"), "规范、可维护性、轻微边界问题"],
            ]
        ),
    ]

    if decision.get("blocking_reasons"):
        body.append(paragraph("阻断原因", "Heading1"))
        body.append(table([["序号", "原因"]] + [[idx, reason] for idx, reason in enumerate(decision["blocking_reasons"], 1)]))
    if decision.get("warnings"):
        body.append(paragraph("提示", "Heading1"))
        body.append(table([["序号", "提示"]] + [[idx, warning] for idx, warning in enumerate(decision["warnings"], 1)]))
    if changed_files:
        body.append(paragraph("Review变更文件", "Heading1"))
        body.append(table([["序号", "文件路径"]] + [[idx, path] for idx, path in enumerate(changed_files, 1)]))

    body.extend(
        [
            paragraph("3. 异常处理审查", "Heading1"),
            checklist("是否存在吞异常、只打印日志但不中断流程的问题"),
            checklist("是否存在空指针、数组越界、类型转换失败等边界异常"),
            checklist("外部接口/文件/数据库调用失败是否有明确降级或错误返回"),
            checklist("事务异常是否会正确回滚，异步异常是否可观测"),
            table(section_rows(groups.get("exception", []), "问题描述")),
            paragraph("4. 安全审查", "Heading1"),
            checklist("是否存在越权访问、鉴权缺失、权限判断绕过"),
            checklist("是否存在 SQL 注入、命令注入、路径穿越、SSRF、XSS 风险"),
            checklist("敏感信息是否被写入日志、响应体、异常堆栈或前端页面"),
            checklist("Token、密钥、账号密码是否硬编码或被提交到仓库"),
            table(section_rows(groups.get("security", []), "安全风险")),
            paragraph("5. 性能审查", "Heading1"),
            checklist("是否存在 N+1 查询、循环内数据库/远程接口调用"),
            checklist("大文件/大集合处理是否存在内存溢出风险"),
            checklist("是否存在无分页查询、无上限导出、无超时控制"),
            checklist("缓存、索引、批处理、异步任务是否存在不合理使用"),
            table(section_rows(groups.get("performance", []), "性能问题")),
            paragraph("6. 代码规范审查", "Heading1"),
            checklist("命名、分层、职责边界是否清晰"),
            checklist("是否存在重复代码、过长方法、过深嵌套、魔法值"),
            checklist("日志级别、错误码、返回结构是否符合项目规范"),
            checklist("测试覆盖是否与变更风险匹配，是否删除关键测试且无替代覆盖"),
            table(section_rows(groups.get("standard", []), "规范问题")),
            paragraph("7. CSV安全合规专项审查", "Heading1"),
            checklist("本次变更是否命中 CSV 安全合规审查范围"),
            checklist("是否已调用 CSV 安全合规审查接口/工具"),
            checklist("CSV 审查结果是否通过"),
            checklist("CSV 审查发现的 Critical/High 风险是否已处理或阻断"),
            checklist("CSV 审查报告编号、流水号或外部报告链接是否已记录"),
            table(csv_compliance_rows(report)),
            paragraph("8. 问题明细汇总", "Heading1"),
            table(summary_rows(report)),
            paragraph("9. 准入确认", "Heading1"),
            checklist("所有 Critical/High 问题已修复或已有负责人确认", pass_status),
            checklist("CSV安全合规审查结果已完成确认", pass_status),
            checklist("影响范围、回归范围、测试结论已补充到提测确认单", pass_status),
            checklist("如本次为 BLOCKED，已在 GitLab/通知渠道同步阻断原因", blocked_status),
            table(
                [
                    ["角色", "姓名", "确认意见", "确认时间"],
                    ["研发负责人", "", "", ""],
                    ["Review负责人", "", "", ""],
                    ["测试负责人", "", "", ""],
                ]
            ),
        ]
    )
    return "".join(body)


def build_document(report):
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        + build_body(report)
        + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>'
        + "</w:body></w:document>"
    )


def write_scratch_docx(report, output_path):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "_rels").mkdir()
        (root / "word" / "_rels").mkdir(parents=True)
        (root / "[Content_Types].xml").write_text(CONTENT_TYPES, encoding="utf-8")
        (root / "_rels" / ".rels").write_text(ROOT_RELS, encoding="utf-8")
        (root / "word" / "_rels" / "document.xml.rels").write_text(DOC_RELS, encoding="utf-8")
        (root / "word" / "styles.xml").write_text(STYLES, encoding="utf-8")
        (root / "word" / "document.xml").write_text(build_document(report), encoding="utf-8")

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for path in root.rglob("*"):
                if path.is_file():
                    zout.write(path, path.relative_to(root).as_posix())


def write_from_template(report, template_path, output_path):
    document_xml = build_document(report).encode("utf-8")
    with zipfile.ZipFile(template_path) as zin, zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zout:
        written = set()
        for item in zin.infolist():
            data = document_xml if item.filename == "word/document.xml" else zin.read(item.filename)
            zout.writestr(item, data)
            written.add(item.filename)
        if "word/document.xml" not in written:
            zout.writestr("word/document.xml", document_xml)


def write_docx(report, output, template=""):
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    template_path = Path(template) if template else DEFAULT_TEMPLATE
    if template_path.exists():
        write_from_template(report, template_path, output_path)
    else:
        write_scratch_docx(report, output_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--template", default="")
    args = parser.parse_args()
    write_docx(load_json(args.report), args.output, args.template)


if __name__ == "__main__":
    main()
