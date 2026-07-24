#!/usr/bin/env python3
import argparse
import fnmatch
import json
import shlex
from pathlib import Path


DEFAULT_SCOPE = {
    "exclude_path_segments": [
        "node_modules",
        "vendor",
        "target",
        "build",
        "dist",
        "out",
        ".gradle",
        ".idea",
        ".vscode",
        "coverage",
        "test-output",
        "surefire-reports",
        "jacoco",
    ],
    "exclude_path_prefixes": [
        ".mvn/wrapper/",
        "doc/",
        "docs/",
    ],
    "exclude_file_patterns": [
        "README.md",
        "CHANGELOG.md",
        "*.md",
        "*.doc",
        "*.docx",
        "*.pdf",
        "*.class",
        "*.jar",
        "*.war",
        "*.ear",
        "*.zip",
        "*.tar",
        "*.gz",
        "*.rar",
        "*.7z",
        "*.min.js",
        "*.map",
        "*.log",
        "*.tmp",
        "*.cache",
        "*.bak",
        "*.swp",
        "*.png",
        "*.jpg",
        "*.jpeg",
        "*.gif",
        "*.svg",
        "*.ico",
        "*.mp4",
        "*.mov",
        "*.avi",
        "*.lcov",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
    ],
    "include_file_patterns": [
        "*.java",
        "*.kt",
        "*.groovy",
        "*.yml",
        "*.yaml",
        "*.properties",
        "*.xml",
        "*.conf",
        "*.sh",
        "*.bat",
        "*.ps1",
        "*.py",
        "*.js",
        "*.ts",
        "*.jsx",
        "*.tsx",
        "*.vue",
        "*.sql",
        "Dockerfile",
        ".gitlab-ci.yml",
        "docker-compose.yml",
        "docker-compose.yaml",
        "pom.xml",
        "build.gradle",
        "settings.gradle",
        "gradle.properties",
        "package.json",
        "tsconfig.json",
    ],
}


def load_json(path):
    if not path:
        return {}
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return {}
    with p.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def load_scope(path):
    config = load_json(path)
    scope = dict(DEFAULT_SCOPE)
    scope.update(config.get("review_scope") or {})
    return scope


def normalize_path(value):
    path = str(value or "").lstrip("\ufeff").strip().replace("\\", "/")
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    while path.startswith("./"):
        path = path[2:]
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    return path


def split_segments(path):
    return [part for part in normalize_path(path).split("/") if part]


def matches_any(path, patterns):
    name = Path(path).name
    return any(fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(name, pattern) for pattern in patterns)


def review_scope_reason(path, scope):
    normalized = normalize_path(path)
    if not normalized or normalized == "/dev/null":
        return "empty"

    lower_path = normalized.lower()
    segments = [segment.lower() for segment in split_segments(normalized)]

    for prefix in scope["exclude_path_prefixes"]:
        if lower_path.startswith(str(prefix).lower()):
            return f"excluded prefix: {prefix}"

    for segment in scope["exclude_path_segments"]:
        if str(segment).lower() in segments:
            return f"excluded path segment: {segment}"

    if matches_any(normalized, scope["exclude_file_patterns"]):
        return "excluded file pattern"

    if matches_any(normalized, scope["include_file_patterns"]):
        return ""

    return "not in reviewable file types"


def is_reviewable(path, scope):
    return not review_scope_reason(path, scope)


def read_changed_files(path):
    p = Path(path)
    if not p.exists():
        return []
    return [normalize_path(line) for line in p.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]


def write_lines(path, lines):
    Path(path).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def parse_diff_paths(header):
    try:
        parts = shlex.split(header)
    except ValueError:
        parts = header.split()
    if len(parts) < 4:
        return []
    return [normalize_path(parts[2]), normalize_path(parts[3])]


def filter_diff(diff_path, output_path, scope):
    p = Path(diff_path)
    if not p.exists():
        Path(output_path).write_text("", encoding="utf-8")
        return

    lines = p.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    prelude = []
    current = []
    keep_current = False
    kept_sections = []
    seen_diff_header = False

    def flush_current():
        if current and keep_current:
            kept_sections.extend(current)

    for line in lines:
        if line.startswith("diff --git "):
            flush_current()
            seen_diff_header = True
            current = [line]
            paths = parse_diff_paths(line)
            keep_current = any(is_reviewable(path, scope) for path in paths)
            continue

        if seen_diff_header:
            current.append(line)
        else:
            prelude.append(line)

    flush_current()

    if kept_sections:
        Path(output_path).write_text("".join(prelude + kept_sections), encoding="utf-8")
    else:
        Path(output_path).write_text("", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    parser.add_argument("--changed-files", required=True)
    parser.add_argument("--diff", required=True)
    parser.add_argument("--output-changed-files", required=True)
    parser.add_argument("--output-diff", required=True)
    parser.add_argument("--summary", required=True)
    args = parser.parse_args()

    scope = load_scope(args.config)
    changed_files = read_changed_files(args.changed_files)
    reviewed = []
    skipped = []

    for path in changed_files:
        reason = review_scope_reason(path, scope)
        if reason:
            skipped.append({"path": path, "reason": reason})
        else:
            reviewed.append(path)

    write_lines(args.output_changed_files, reviewed)
    filter_diff(args.diff, args.output_diff, scope)

    summary = {
        "reviewed_files": reviewed,
        "skipped_files": skipped,
        "reviewed_count": len(reviewed),
        "skipped_count": len(skipped),
    }
    Path(args.summary).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"review scope: {len(reviewed)} reviewed file(s), {len(skipped)} skipped file(s)")


if __name__ == "__main__":
    main()
