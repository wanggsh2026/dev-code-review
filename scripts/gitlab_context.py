#!/usr/bin/env python3
import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def load_json(path, default):
    if not path:
        return default
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return default
    with p.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def first_non_empty(*values):
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def infer_api_url(project_url):
    if not project_url:
        return ""
    parsed = urllib.parse.urlparse(project_url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}/api/v4"


def infer_project_path(project_url):
    if not project_url:
        return ""
    parsed = urllib.parse.urlparse(project_url)
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return path


def project_identifier(project_id, project_url):
    value = first_non_empty(project_id, infer_project_path(project_url))
    if not value:
        return ""
    return urllib.parse.quote(value, safe="")


def normalize_author(author):
    if not isinstance(author, dict):
        return {}
    return {
        "id": author.get("id"),
        "name": author.get("name", ""),
        "username": author.get("username", ""),
        "web_url": author.get("web_url", ""),
    }


def parse_time(value):
    if not value:
        return 0
    text = str(value).replace("Z", "+00:00")
    try:
        return time.mktime(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return 0


class GitLabClient:
    def __init__(self, api_url, token, token_header="PRIVATE-TOKEN", timeout=20):
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.token_header = token_header
        self.timeout = timeout

    def get(self, path, params=None, allow_missing=False):
        url = self.api_url + path
        if params:
            qs = urllib.parse.urlencode({k: v for k, v in params.items() if v})
            if qs:
                url += "?" + qs
        headers = {"Accept": "application/json"}
        if self.token:
            headers[self.token_header] = self.token
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            if allow_missing and exc.code in (404, 405):
                return None
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitLab API {exc.code} for {url}: {body[:500]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"GitLab API request failed for {url}: {exc}") from exc


def choose_merge_request(items, preferred_iid="", target_branch=""):
    if not items:
        return None
    if preferred_iid:
        for item in items:
            if str(item.get("iid", "")) == str(preferred_iid):
                return item
    candidates = list(items)
    if target_branch:
        branch_matches = [item for item in candidates if item.get("target_branch") == target_branch]
        if branch_matches:
            candidates = branch_matches
    state_rank = {"merged": 3, "opened": 2, "locked": 1, "closed": 0}
    candidates.sort(
        key=lambda item: (
            state_rank.get(str(item.get("state", "")).lower(), 0),
            parse_time(item.get("merged_at") or item.get("updated_at") or item.get("created_at")),
        ),
        reverse=True,
    )
    return candidates[0]


def normalize_mr(mr):
    if not isinstance(mr, dict):
        return {}
    return {
        "id": mr.get("id"),
        "iid": mr.get("iid"),
        "title": mr.get("title", ""),
        "description": mr.get("description", ""),
        "state": mr.get("state", ""),
        "author": normalize_author(mr.get("author")),
        "source_branch": mr.get("source_branch", ""),
        "target_branch": mr.get("target_branch", ""),
        "labels": mr.get("labels") or [],
        "web_url": mr.get("web_url", ""),
        "created_at": mr.get("created_at", ""),
        "updated_at": mr.get("updated_at", ""),
        "merged_at": mr.get("merged_at", ""),
    }


def collect_gitlab_context(args, context):
    project_url = first_non_empty(args.project_url, context.get("gitlab_project_url"), os.getenv("GITLAB_PROJECT_URL"), os.getenv("CI_PROJECT_URL"))
    api_url = first_non_empty(args.api_url, os.getenv("GITLAB_API_URL"), os.getenv("CI_API_V4_URL"), infer_api_url(project_url))
    project_raw = first_non_empty(args.project_id, context.get("gitlab_project_id"), os.getenv("GITLAB_PROJECT_ID"), os.getenv("CI_PROJECT_ID"))
    project = project_identifier(project_raw, project_url)
    token = first_non_empty(args.token, os.getenv("GITLAB_TOKEN"), os.getenv("CI_JOB_TOKEN"))
    token_header = first_non_empty(args.token_header, os.getenv("GITLAB_TOKEN_HEADER"))
    if not token_header:
        token_header = "JOB-TOKEN" if os.getenv("CI_JOB_TOKEN") and not os.getenv("GITLAB_TOKEN") else "PRIVATE-TOKEN"
    commit = first_non_empty(args.commit, context.get("to_commit"), os.getenv("REVIEW_TO_COMMIT"), os.getenv("CI_COMMIT_SHA"))
    preferred_iid = first_non_empty(args.mr_iid, context.get("gitlab_mr_iid"), os.getenv("GITLAB_MR_IID"), os.getenv("CI_MERGE_REQUEST_IID"))
    target_branch = first_non_empty(args.target_branch, context.get("target_branch"), os.getenv("REVIEW_TARGET_BRANCH"), os.getenv("CI_MERGE_REQUEST_TARGET_BRANCH_NAME"), "dev")

    base = {
        "status": "skipped",
        "errors": [],
        "project": {"id": project_raw, "url": project_url, "api_url": api_url},
        "commit": commit,
        "merge_requests": [],
        "selected_merge_request": {},
    }

    missing = []
    if not api_url:
        missing.append("GITLAB_API_URL or GITLAB_PROJECT_URL")
    if not project:
        missing.append("GITLAB_PROJECT_ID or GITLAB_PROJECT_URL")
    if not commit and not preferred_iid:
        missing.append("REVIEW_TO_COMMIT/GIT_COMMIT or GITLAB_MR_IID")
    if not token:
        missing.append("GITLAB_TOKEN or CI_JOB_TOKEN")
    if missing:
        base["errors"].append("missing required input(s): " + ", ".join(missing))
        return base

    client = GitLabClient(api_url, token, token_header=token_header, timeout=args.timeout)
    try:
        merge_requests = []
        if preferred_iid:
            detail = client.get(f"/projects/{project}/merge_requests/{preferred_iid}", allow_missing=True)
            if detail:
                merge_requests = [detail]
        if not merge_requests and commit:
            found = client.get(
                f"/projects/{project}/repository/commits/{urllib.parse.quote(commit, safe='')}/merge_requests",
                params={"state": "all"},
                allow_missing=True,
            )
            if isinstance(found, list):
                merge_requests = found

        selected = choose_merge_request(merge_requests, preferred_iid=preferred_iid, target_branch=target_branch)
        if selected and selected.get("iid"):
            detail = client.get(f"/projects/{project}/merge_requests/{selected['iid']}", allow_missing=True)
            if detail:
                selected = detail

        base["status"] = "ok"
        base["merge_requests"] = [normalize_mr(item) for item in merge_requests]
        base["selected_merge_request"] = normalize_mr(selected or {})
        if not base["selected_merge_request"]:
            base["status"] = "not_found"
            base["errors"].append("no merge request found for commit")
        return base
    except Exception as exc:
        base["status"] = "failed"
        base["errors"].append(str(exc))
        return base


def merge_context(context, gitlab_context):
    selected = gitlab_context.get("selected_merge_request") or {}
    project = gitlab_context.get("project") or {}
    merged = dict(context)
    merged["gitlab"] = gitlab_context
    merged["gitlab_project_id"] = first_non_empty(context.get("gitlab_project_id"), project.get("id"))
    merged["gitlab_project_url"] = first_non_empty(context.get("gitlab_project_url"), project.get("url"))
    merged["gitlab_mr_iid"] = first_non_empty(context.get("gitlab_mr_iid"), selected.get("iid"))
    merged["gitlab_mr_title"] = selected.get("title", "")
    merged["gitlab_mr_description"] = selected.get("description", "")
    merged["gitlab_mr_author"] = (selected.get("author") or {}).get("username") or (selected.get("author") or {}).get("name", "")
    merged["source_branch"] = first_non_empty(context.get("source_branch"), selected.get("source_branch"))
    merged["target_branch"] = first_non_empty(context.get("target_branch"), selected.get("target_branch"))
    merged["gitlab_mr_labels"] = selected.get("labels", [])
    merged["gitlab_mr_web_url"] = selected.get("web_url", "")
    return merged


def main():
    parser = argparse.ArgumentParser(description="Collect GitLab MR context for dev code review.")
    parser.add_argument("--context", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--update-context", default="")
    parser.add_argument("--api-url", default="")
    parser.add_argument("--project-id", default="")
    parser.add_argument("--project-url", default="")
    parser.add_argument("--token", default="")
    parser.add_argument("--token-header", default="")
    parser.add_argument("--commit", default="")
    parser.add_argument("--mr-iid", default="")
    parser.add_argument("--target-branch", default="")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    context = load_json(args.context, {})
    gitlab_context = collect_gitlab_context(args, context)
    write_json(args.output, gitlab_context)
    if args.update_context:
        write_json(args.update_context, merge_context(context, gitlab_context))

    if args.strict and gitlab_context.get("status") != "ok":
        print("; ".join(gitlab_context.get("errors") or ["GitLab context collection failed"]))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
