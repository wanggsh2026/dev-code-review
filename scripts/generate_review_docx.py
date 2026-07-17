#!/usr/bin/env python3
import argparse
import html
import json
import tempfile
import zipfile
from pathlib import Path


DIMENSIONS = [
    ("exception", "异常"),
    ("security", "安全"),
    ("performance", "性能"),
    ("standard", "规范"),
]


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


def paragraph(text, style="Normal"):
    style_xml = f'<w:pPr><w:pStyle w:val="{esc(style)}"/></w:pPr>' if style else ""
    return f"<w:p>{style_xml}<w:r><w:t>{esc(text)}</w:t></w:r></w:p>"


def bullet(text):
    return paragraph("- " + str(text))


def table(rows):
    xml = ["<w:tbl>"]
    xml.append(
        "<w:tblPr><w:tblW w:w=\"0\" w:type=\"auto\"/>"
        "<w:tblBorders>"
        "<w:top w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"auto\"/>"
        "<w:left w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"auto\"/>"
        "<w:bottom w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"auto\"/>"
        "<w:right w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"auto\"/>"
        "<w:insideH w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"auto\"/>"
        "<w:insideV w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"auto\"/>"
        "</w:tblBorders></w:tblPr>"
    )
    for row in rows:
        xml.append("<w:tr>")
        for cell in row:
            xml.append(f"<w:tc><w:p><w:r><w:t>{esc(cell)}</w:t></w:r></w:p></w:tc>")
        xml.append("</w:tr>")
    xml.append("</w:tbl>")
    return "".join(xml)


def build_document(report):
    context = report.get("context", {})
    decision = report.get("decision", {})
    groups = report.get("findings_by_dimension", {})
    changed_files = report.get("changed_files", [])

    body = [
        paragraph("Code Review 报告", "Title"),
        bullet(f"结论: {decision.get('status', 'UNKNOWN')}"),
        bullet(f"生成时间: {report.get('generated_at', '')}"),
        bullet(f"目标分支: {context.get('target_branch', '')}"),
        bullet(f"来源分支: {context.get('source_branch', '')}"),
        bullet(f"Base Commit: {context.get('base_commit', '')}"),
        bullet(f"To Commit: {context.get('to_commit', '')}"),
        paragraph("总览", "Heading1"),
        table([["维度", "数量"]] + [[label, decision.get("dimension_counts", {}).get(key, 0)] for key, label in DIMENSIONS]),
        paragraph("阻断原因", "Heading1"),
    ]

    blocking = decision.get("blocking_reasons") or ["无"]
    body.extend(bullet(item) for item in blocking)

    warnings = decision.get("warnings") or []
    if warnings:
        body.append(paragraph("提示", "Heading1"))
        body.extend(bullet(item) for item in warnings)

    body.append(paragraph("变更文件", "Heading1"))
    body.extend(bullet(item) for item in (changed_files or ["无"]))

    for key, label in DIMENSIONS:
        rows = [["级别", "文件", "行号", "问题"]]
        for item in groups.get(key, []):
            rows.append([item.get("severity", ""), item.get("path", ""), item.get("line", ""), item.get("content", "")])
        if len(rows) == 1:
            rows.append(["-", "-", "-", "未发现"])
        body.append(paragraph(label, "Heading1"))
        body.append(table(rows))

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(body)
        + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>'
        + "</w:body></w:document>"
    )


def write_docx(report, output):
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "_rels").mkdir()
        (root / "word" / "_rels").mkdir(parents=True)
        (root / "[Content_Types].xml").write_text(CONTENT_TYPES, encoding="utf-8")
        (root / "_rels" / ".rels").write_text(ROOT_RELS, encoding="utf-8")
        (root / "word" / "_rels" / "document.xml.rels").write_text(DOC_RELS, encoding="utf-8")
        (root / "word" / "styles.xml").write_text(STYLES, encoding="utf-8")
        (root / "word" / "document.xml").write_text(build_document(report), encoding="utf-8")

        if output_path.exists():
            output_path.unlink()
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for path in root.rglob("*"):
                if path.is_file():
                    zout.write(path, path.relative_to(root).as_posix())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    write_docx(load_json(args.report), args.output)


if __name__ == "__main__":
    main()
