"""Read-only GitHub provenance checks for the protected release adapter.

Only a protected installer policy may supply workflow hashes, GitHub App IDs or
reviewer identities. Neither worker requests nor PR text are authority. No prompt,
review body, repository source or remote error is returned by these functions.
"""

from __future__ import annotations

from typing import Any

from tools.autonomy_broker import (
    PRODUCTION_BRANCH,
    REPOSITORY,
    REQUIRED_CHECKS,
    Denied,
    Request,
    source_sha,
)
from tools.autonomy_broker_host import GitHub

WORKFLOW_FOR_CHECK = {
    "core-tests": ".github/workflows/jarvis-ci.yml",
    "quality-security": ".github/workflows/jarvis-ci.yml",
    "android-unit-tests": ".github/workflows/jarvis-ci.yml",
    "Analyze Python": ".github/workflows/codeql.yml",
}


def bounded_rows(value: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        raise Denied("incomplete_github_evidence")
    rows, count = value.get(key), value.get("total_count")
    # Do not silently treat a truncated first page as complete evidence.
    if (
        not isinstance(rows, list)
        or type(count) is not int
        or count != len(rows)
        or count > 100
        or any(not isinstance(row, dict) for row in rows)
    ):
        raise Denied("incomplete_github_evidence")
    return rows


def protected_sources(tree: dict[str, str], policy: dict[str, Any]) -> None:
    prefixes = policy["protected_prefixes"]
    pins = policy["protected_blobs"]
    if not pins or not prefixes or any(path not in pins for path in WORKFLOW_FOR_CHECK.values()):
        raise Denied("unprovisioned_release_policy")
    observed = {
        name: digest
        for name, digest in tree.items()
        if any(name == prefix or name.startswith(prefix + "/") for prefix in prefixes)
    }
    if observed != pins:
        raise Denied("protected_source_changed")


def check_evidence(github: GitHub, head: str, policy: dict[str, Any]) -> list[dict[str, Any]]:
    source_sha(head)
    checks = bounded_rows(
        github.get(f"commits/{head}/check-runs?filter=latest&per_page=100"), "check_runs"
    )
    runs = bounded_rows(github.get(f"actions/runs?head_sha={head}&per_page=100"), "workflow_runs")
    result = []
    for name in sorted(REQUIRED_CHECKS):
        candidates = [row for row in checks if row.get("name") == name]
        if len(candidates) != 1:
            raise Denied("ambiguous_or_missing_check")
        check = candidates[0]
        if (
            check.get("head_sha") != head
            or check.get("status") != "completed"
            or check.get("conclusion") != "success"
            or check.get("app", {}).get("id") != policy["github_actions_app_id"]
            or check.get("app", {}).get("slug") != "github-actions"
        ):
            raise Denied("protected_checks_not_passed")
        suite_id = check.get("check_suite", {}).get("id")
        matching = [run for run in runs if run.get("check_suite_id") == suite_id]
        if len(matching) != 1:
            raise Denied("workflow_origin_unverified")
        run = matching[0]
        if (
            type(suite_id) is not int
            or suite_id <= 0
            or run.get("head_sha") != head
            or run.get("path") != WORKFLOW_FOR_CHECK[name]
            or run.get("event") != "pull_request"
            or run.get("status") != "completed"
            or run.get("conclusion") != "success"
            or run.get("repository", {}).get("full_name") != REPOSITORY
            or run.get("head_repository", {}).get("full_name") != REPOSITORY
        ):
            raise Denied("workflow_origin_unverified")
        result.append(
            {
                "name": name,
                "head_sha": head,
                "status": "completed",
                "conclusion": "success",
                "trusted_workflow": True,
            }
        )
    return result


def independent_review(
    github: GitHub, number: int, head: str, author_id: int, reviewer_ids: list[int]
) -> None:
    if not reviewer_ids or any(type(uid) is not int or uid <= 0 for uid in reviewer_ids):
        raise Denied("reviewer_identity_not_provisioned")
    rows = github.get(f"pulls/{number}/reviews?per_page=100")
    if not isinstance(rows, list) or len(rows) >= 100:
        raise Denied("incomplete_review_evidence")
    latest: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or type(row.get("id")) is not int:
            raise Denied("incomplete_review_evidence")
        actor = row.get("user", {}).get("id")
        if type(actor) is not int:
            raise Denied("incomplete_review_evidence")
        # Comments do not invalidate a previously submitted review. Dismissal,
        # changes requested and a newer approval do supersede prior authority.
        if row.get("state") in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            if actor not in latest or row["id"] > latest[actor]["id"]:
                latest[actor] = row
    if any(row["state"] == "CHANGES_REQUESTED" for row in latest.values()):
        raise Denied("review_changes_requested")
    if not any(
        actor in reviewer_ids
        and actor != author_id
        and row.get("commit_id") == head
        and row["state"] == "APPROVED"
        for actor, row in latest.items()
    ):
        raise Denied("independent_review_missing")


def merged_release(github: GitHub, request: Request, policy: dict[str, Any]) -> dict[str, Any]:
    """Exact remote evidence only; local workspace/artifact/data checks are separate."""
    body, params = request.body, request.body["parameters"]
    number, head, merged = params["pr_number"], params["pr_head"], body["source_sha"]
    pr = github.get(f"pulls/{number}")
    if (
        not isinstance(pr, dict)
        or pr.get("number") != number
        or pr.get("merged") is not True
        or pr.get("state") != "closed"
        or pr.get("merge_commit_sha") != merged
        or pr.get("head", {}).get("sha") != head
        or pr.get("head", {}).get("ref") != body["branch"]
        or pr.get("base", {}).get("ref") != PRODUCTION_BRANCH
        or pr.get("base", {}).get("repo", {}).get("full_name") != REPOSITORY
        or pr.get("head", {}).get("repo", {}).get("full_name") != REPOSITORY
    ):
        raise Denied("release_pr_identity_mismatch")
    ref = github.get("git/ref/heads/" + PRODUCTION_BRANCH)
    if ref.get("object", {}).get("sha") != merged or ref.get("object", {}).get("type") != "commit":
        raise Denied("canonical_remote_changed")
    head_tree, final_tree = github.tree(head), github.tree(merged)
    protected_sources(head_tree, policy)
    protected_sources(final_tree, policy)
    if head_tree != final_tree:
        raise Denied("merged_tree_differs_from_tested_head")
    checks = check_evidence(github, head, policy)
    independent_review(github, number, head, pr["user"]["id"], policy["reviewer_ids"])
    return {
        "checks": checks,
        "independent_review_passed": True,
        "policy_unchanged": True,
        "source_sha": merged,
        "pr_head": head,
        "pr_number": number,
    }
