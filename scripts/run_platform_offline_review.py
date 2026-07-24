#!/usr/bin/env python3
import argparse
import base64
import json
import shutil
import subprocess
import sys
from pathlib import Path


def normalize_review_result(raw):
    if isinstance(raw, list):
        return {"comments": raw}
    if not isinstance(raw, dict):
        raise ValueError("review result must be a JSON object or a comments array")
    comments = raw.get("comments", [])
    if comments is None:
        comments = []
    if not isinstance(comments, list):
        raise ValueError("review result field 'comments' must be an array")
    raw["comments"] = comments
    return raw


def read_review_result(args):
    sources = [
        bool(args.review_json),
        bool(args.review_json_file),
        bool(args.review_json_base64),
        args.review_json_stdin,
    ]
    if sum(1 for item in sources if item) != 1:
        raise ValueError("provide exactly one review source")

    if args.review_json:
        text = args.review_json
    elif args.review_json_file:
        text = Path(args.review_json_file).read_text(encoding="utf-8-sig")
    elif args.review_json_base64:
        text = base64.b64decode(args.review_json_base64).decode("utf-8")
    else:
        text = sys.stdin.read()
    text = text.lstrip("\ufeff")
    return normalize_review_result(json.loads(text))


def write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def copy_required(src, dst):
    src_path = Path(src)
    if not src_path.exists():
        raise FileNotFoundError(f"required input not found: {src_path}")
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src_path, dst_path)


def run_command(cmd, cwd):
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def main():
    parser = argparse.ArgumentParser(description="Write dev code review artifacts from platform model output.")
    parser.add_argument("--app-root", default="")
    parser.add_argument("--input-dir", default="input")
    parser.add_argument("--output-dir", default="output/review-output")
    parser.add_argument("--config", default="gitlab-merge-review/review-config.example.json")
    parser.add_argument("--docx-template", default="templates/ai-agent-code-review-template.docx")
    parser.add_argument("--review-json", default="")
    parser.add_argument("--review-json-file", default="")
    parser.add_argument("--review-json-base64", default="")
    parser.add_argument("--review-json-stdin", action="store_true")
    parser.add_argument("--fail-on-blocked", action="store_true")
    args = parser.parse_args()

    script_path = Path(__file__).resolve()
    app_root = Path(args.app_root).resolve() if args.app_root else script_path.parent.parent
    input_dir = (app_root / args.input_dir).resolve()
    output_dir = (app_root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    context = output_dir / "review-context.json"
    changed_files_raw = output_dir / "changed-files.raw.txt"
    diff_patch_raw = output_dir / "diff.raw.patch"
    changed_files = output_dir / "changed-files.txt"
    diff_patch = output_dir / "diff.patch"
    review_scope = output_dir / "review-scope.json"
    ocr_result = output_dir / "ocr-result.json"
    ocr_stderr = output_dir / "ocr-stderr.log"
    report = output_dir / "review-report.json"
    report_md = output_dir / "代码审计报告.md"
    report_docx = output_dir / "代码审计报告.docx"
    manifest = output_dir / "platform-offline-run.json"

    copy_required(input_dir / "review-context.json", context)
    copy_required(input_dir / "changed-files.txt", changed_files_raw)
    copy_required(input_dir / "diff.patch", diff_patch_raw)
    write_json(ocr_result, read_review_result(args))
    ocr_stderr.write_text("", encoding="utf-8")

    config_path = (app_root / args.config).resolve()
    scope_filter = app_root / "gitlab-merge-review" / "scripts" / "filter_review_scope.py"
    scope_proc = run_command(
        [
            sys.executable,
            str(scope_filter),
            "--config",
            str(config_path),
            "--changed-files",
            str(changed_files_raw),
            "--diff",
            str(diff_patch_raw),
            "--output-changed-files",
            str(changed_files),
            "--output-diff",
            str(diff_patch),
            "--summary",
            str(review_scope),
        ],
        app_root,
    )
    if scope_proc.returncode != 0:
        shutil.copyfile(changed_files_raw, changed_files)
        shutil.copyfile(diff_patch_raw, diff_patch)

    evaluator = app_root / "gitlab-merge-review" / "scripts" / "evaluate_review.py"
    evaluate_cmd = [
        sys.executable,
        str(evaluator),
        "--config",
        str(config_path),
        "--context",
        str(context),
        "--changed-files",
        str(changed_files),
        "--diff",
        str(diff_patch),
        "--ocr-result",
        str(ocr_result),
        "--ocr-stderr",
        str(ocr_stderr),
        "--ocr-exit-code",
        "0",
        "--report",
        str(report),
        "--markdown",
        str(report_md),
    ]
    eval_proc = run_command(evaluate_cmd, app_root)

    docx_proc = run_command(
        [
            sys.executable,
            str(app_root / "scripts" / "generate_review_docx.py"),
            "--report",
            str(report),
            "--output",
            str(report_docx),
            "--template",
            str((app_root / args.docx_template).resolve()),
        ],
        app_root,
    )

    status = "UNKNOWN"
    if report.exists():
        data = json.loads(report.read_text(encoding="utf-8-sig"))
        status = data.get("decision", {}).get("status", "UNKNOWN")

    write_json(
        manifest,
        {
            "status": "ok",
            "review_status": status,
            "evaluator_exit_code": eval_proc.returncode,
            "docx_exit_code": docx_proc.returncode,
            "scope_filter_exit_code": scope_proc.returncode,
            "artifacts": {
                "review_context": str(context),
                "changed_files": str(changed_files),
                "diff_patch": str(diff_patch),
                "review_scope": str(review_scope),
                "ocr_result": str(ocr_result),
                "review_report": str(report),
                "markdown": str(report_md),
                "docx": str(report_docx),
            },
            "scope_filter_stdout": scope_proc.stdout,
            "scope_filter_stderr": scope_proc.stderr,
            "evaluator_stdout": eval_proc.stdout,
            "evaluator_stderr": eval_proc.stderr,
            "docx_stdout": docx_proc.stdout,
            "docx_stderr": docx_proc.stderr,
        },
    )

    print(f"Review Result: {status}")
    print(f"Artifacts: {output_dir}")
    if docx_proc.returncode != 0:
        return docx_proc.returncode
    if args.fail_on_blocked and eval_proc.returncode != 0:
        return eval_proc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
