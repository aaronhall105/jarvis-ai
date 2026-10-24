import copy
import uuid

import pytest

from tools.autonomy_broker import Denied, PRODUCTION_BRANCH, REPOSITORY, Request
from tools.autonomy_broker_evidence import WORKFLOW_FOR_CHECK, merged_release

HEAD = "a" * 40
MERGE = "b" * 40


@pytest.fixture
def evidence_case():
    branch = "jarvis/autonomous-" + uuid.uuid4().hex
    request = Request(
        {"branch": branch, "source_sha": MERGE, "parameters": {"pr_number": 123, "pr_head": HEAD}}
    )
    tree = {path: "c" * 40 for path in WORKFLOW_FOR_CHECK.values()}
    tree["bridge/app/main.py"] = "d" * 40
    policy = {
        "protected_prefixes": [".github"],
        "protected_blobs": {path: tree[path] for path in WORKFLOW_FOR_CHECK.values()},
        "github_actions_app_id": 12345,
        "reviewer_ids": [200],
    }
    checks, runs = [], []
    for number, (name, path) in enumerate(WORKFLOW_FOR_CHECK.items(), 1):
        checks.append(
            {
                "name": name,
                "head_sha": HEAD,
                "status": "completed",
                "conclusion": "success",
                "app": {"id": 12345, "slug": "github-actions"},
                "check_suite": {"id": number},
            }
        )
        runs.append(
            {
                "head_sha": HEAD,
                "check_suite_id": number,
                "path": path,
                "event": "pull_request",
                "status": "completed",
                "conclusion": "success",
                "repository": {"full_name": REPOSITORY},
                "head_repository": {"full_name": REPOSITORY},
            }
        )
    pr = {
        "number": 123,
        "merged": True,
        "state": "closed",
        "merge_commit_sha": MERGE,
        "head": {"sha": HEAD, "ref": branch, "repo": {"full_name": REPOSITORY}},
        "base": {"ref": PRODUCTION_BRANCH, "repo": {"full_name": REPOSITORY}},
        "user": {"id": 100},
    }
    rows = {
        "pulls/123": pr,
        "git/ref/heads/" + PRODUCTION_BRANCH: {"object": {"sha": MERGE, "type": "commit"}},
        f"commits/{HEAD}/check-runs?filter=latest&per_page=100": {
            "total_count": len(checks),
            "check_runs": checks,
        },
        f"actions/runs?head_sha={HEAD}&per_page=100": {
            "total_count": len(runs),
            "workflow_runs": runs,
        },
        "pulls/123/reviews?per_page=100": [
            {"id": 1, "user": {"id": 200}, "commit_id": HEAD, "state": "APPROVED"}
        ],
    }

    class FakeGitHub:
        def get(self, path, **_):
            return copy.deepcopy(rows[path])

        def tree(self, sha):
            assert sha in {HEAD, MERGE}
            return copy.deepcopy(tree)

    return FakeGitHub(), request, policy, rows, tree, checks, runs


def test_exact_checked_reviewed_release_evidence(evidence_case):
    github, request, policy, *_ = evidence_case
    result = merged_release(github, request, policy)
    assert result["source_sha"] == MERGE and result["pr_head"] == HEAD
    assert len(result["checks"]) == 4
    assert result["independent_review_passed"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("head_sha", "e" * 40),
        ("status", "in_progress"),
        ("conclusion", "failure"),
        ("app", {"id": 999, "slug": "github-actions"}),
        ("check_suite", {"id": 999}),
    ],
)
def test_check_names_alone_are_not_authority(evidence_case, field, value):
    github, request, policy, _, _, checks, _ = evidence_case
    checks[0][field] = value
    with pytest.raises(Denied):
        merged_release(github, request, policy)


@pytest.mark.parametrize(
    "field,value",
    [
        ("head_sha", "e" * 40),
        ("path", ".github/workflows/fake.yml"),
        ("event", "workflow_dispatch"),
        ("status", "queued"),
        ("conclusion", "skipped"),
        ("repository", {"full_name": "other/repo"}),
        ("head_repository", {"full_name": "other/repo"}),
    ],
)
def test_workflow_origin_must_match(evidence_case, field, value):
    github, request, policy, _, _, _, runs = evidence_case
    runs[0][field] = value
    with pytest.raises(Denied):
        merged_release(github, request, policy)


def test_incomplete_check_page_fails_closed(evidence_case):
    github, request, policy, rows, *_ = evidence_case
    rows[f"commits/{HEAD}/check-runs?filter=latest&per_page=100"]["total_count"] = 200
    with pytest.raises(Denied, match="incomplete_github_evidence"):
        merged_release(github, request, policy)


def test_duplicate_named_check_fails_closed(evidence_case):
    github, request, policy, rows, _, checks, _ = evidence_case
    checks.append(copy.deepcopy(checks[0]))
    rows[f"commits/{HEAD}/check-runs?filter=latest&per_page=100"]["total_count"] += 1
    with pytest.raises(Denied, match="ambiguous_or_missing_check"):
        merged_release(github, request, policy)


@pytest.mark.parametrize("change", ["changed_workflow", "new_workflow", "missing_workflow"])
def test_safety_workflow_changes_cannot_self_authorize(evidence_case, change):
    github, request, policy, _, tree, *_ = evidence_case
    if change == "changed_workflow":
        tree[".github/workflows/jarvis-ci.yml"] = "e" * 40
    elif change == "new_workflow":
        tree[".github/workflows/fake.yml"] = "e" * 40
    else:
        del tree[".github/workflows/jarvis-ci.yml"]
    with pytest.raises(Denied, match="protected_source_changed"):
        merged_release(github, request, policy)


@pytest.mark.parametrize(
    "field,value",
    [
        ("commit_id", "e" * 40),
        ("state", "DISMISSED"),
        ("state", "CHANGES_REQUESTED"),
        ("user", {"id": 100}),
        ("user", {"id": 300}),
    ],
)
def test_review_must_be_independent_current_and_pinned(evidence_case, field, value):
    github, request, policy, rows, *_ = evidence_case
    rows["pulls/123/reviews?per_page=100"][0][field] = value
    with pytest.raises(Denied):
        merged_release(github, request, policy)


def test_new_changes_request_overrides_old_approval(evidence_case):
    github, request, policy, rows, *_ = evidence_case
    rows["pulls/123/reviews?per_page=100"].append(
        {"id": 2, "user": {"id": 200}, "commit_id": HEAD, "state": "CHANGES_REQUESTED"}
    )
    with pytest.raises(Denied, match="review_changes_requested"):
        merged_release(github, request, policy)


def test_wrong_canonical_head_rejected(evidence_case):
    github, request, policy, rows, *_ = evidence_case
    rows["git/ref/heads/" + PRODUCTION_BRANCH]["object"]["sha"] = "e" * 40
    with pytest.raises(Denied, match="canonical_remote_changed"):
        merged_release(github, request, policy)


def test_final_source_cannot_differ_from_tested_head(evidence_case, monkeypatch):
    github, request, policy, _, tree, *_ = evidence_case

    def changed_tree(sha):
        result = dict(tree)
        if sha == MERGE:
            result["bridge/app/main.py"] = "e" * 40
        return result

    monkeypatch.setattr(github, "tree", changed_tree)
    with pytest.raises(Denied, match="merged_tree_differs_from_tested_head"):
        merged_release(github, request, policy)
