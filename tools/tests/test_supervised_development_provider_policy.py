"""Model/rate selection is protected configuration, never candidate authority."""

from dataclasses import replace

import pytest

from tools.supervised_development_controller import LifecycleError
from tools.supervised_development_pipeline import (
    ExistingSelfDevelopmentWorker,
    GovernorAdapter,
    GovernorTask,
    SupervisedDevelopmentPipeline,
)
from tools.supervised_development_runtime import _provider_configuration


def _task(model="gpt-5.3-codex"):
    return GovernorTask(
        objective="Correct application routing for a recorded error.",
        source_sha="a" * 40,
        allowed_paths=("bridge/app/routing.py", "bridge/tests/test_routing.py"),
        provider="openai",
        model=model,
        max_calls=1,
        max_cost_usd=1,
        deadline_seconds=120,
        owner_id="owner",
        request_ref="request",
    )


def _environment():
    return {
        "JARVIS_AUTHORITATIVE_WORKSPACE": "/source",
        "JARVIS_SUPERVISED_WORKTREE_ROOT": "/workspaces",
        "JARVIS_GPT53_INPUT_USD_PER_MILLION": "1",
        "JARVIS_GPT53_OUTPUT_USD_PER_MILLION": "2",
    }


@pytest.fixture
def transport_guard(monkeypatch):
    calls = []

    class Worker:
        def __init__(self, source, **kwargs):
            calls.append((source, kwargs))

        def run(self, task, *, call_ledger):
            calls.append((task, call_ledger))
            return {"model": task.model}

    monkeypatch.setattr("tools.supervised_development_worker.BoundedDevelopmentWorker", Worker)
    return calls


def test_default_model_keeps_explicit_legacy_pricing_binding(transport_guard):
    ledger = object()
    result = ExistingSelfDevelopmentWorker(object(), _environment()).run(
        _task(), call_ledger=ledger
    )
    assert result == {"model": "gpt-5.3-codex"}
    assert transport_guard[0][1]["input_price_per_million"] == 1
    assert transport_guard[1][1] is ledger


def test_configured_model_requires_matching_generic_rates(transport_guard):
    environment = {
        **_environment(),
        "JARVIS_SUPERVISED_MODEL": "approved-coding-model",
        "JARVIS_SUPERVISED_PRICING_MODEL": "approved-coding-model",
        "JARVIS_SUPERVISED_INPUT_USD_PER_MILLION": "3",
        "JARVIS_SUPERVISED_OUTPUT_USD_PER_MILLION": "4",
        "JARVIS_SUPERVISED_MAX_INPUT_TOKENS": "500000",
        "JARVIS_SUPERVISED_MAX_OUTPUT_TOKENS": "5000",
    }
    task = _task("approved-coding-model")
    assert ExistingSelfDevelopmentWorker(object(), environment).run(task, call_ledger=object()) == {
        "model": task.model
    }
    assert transport_guard[0][1]["input_price_per_million"] == 3
    assert transport_guard[0][1]["max_input_tokens"] == 500000


@pytest.mark.parametrize(
    ("updates", "model", "reason"),
    [
        (
            {"JARVIS_SUPERVISED_PRICING_MODEL": "wrong-model"},
            "gpt-5.3-codex",
            "pricing_model_mismatch",
        ),
        (
            {"JARVIS_SUPERVISED_INPUT_USD_PER_MILLION": "1"},
            "gpt-5.3-codex",
            "pricing_model_mismatch",
        ),
        (
            {"JARVIS_SUPERVISED_PRICING_MODEL": "gpt-5.3-codex"},
            "gpt-5.3-codex",
            "pricing_unverified",
        ),
        ({"JARVIS_SUPERVISED_MODEL": "other-model"}, "other-model", "pricing_unverified"),
        ({"JARVIS_SUPERVISED_MODEL": "other-model"}, "gpt-5.3-codex", "model_policy_changed"),
        (
            {"JARVIS_SUPERVISED_MAX_INPUT_TOKENS": "1000001"},
            "gpt-5.3-codex",
            "token_policy_invalid",
        ),
        ({"JARVIS_SUPERVISED_MAX_OUTPUT_TOKENS": "65537"}, "gpt-5.3-codex", "token_policy_invalid"),
    ],
)
def test_unverified_or_changed_configuration_denies_before_worker(
    transport_guard, updates, model, reason
):
    with pytest.raises(LifecycleError, match=reason):
        ExistingSelfDevelopmentWorker(object(), {**_environment(), **updates}).run(
            _task(model), call_ledger=object()
        )
    assert transport_guard == []


def test_model_selected_by_protected_configuration_not_candidate():
    candidate = {
        "status": "queued",
        "summary": "Correct response routing for a recorded error.",
        "candidate_id": 12,
        "model": "attacker-model",
    }
    arguments = {
        "owner_id": "owner",
        "request_ref": "request",
        "source_sha": "a" * 40,
        "allowed_paths": ("bridge/app/routing.py",),
    }
    task = GovernorAdapter.from_candidate(candidate, **arguments, model="approved-model")
    assert task.model == "approved-model"
    assert GovernorAdapter.from_candidate(candidate, **arguments).model == "gpt-5.3-codex"
    assert SupervisedDevelopmentPipeline._parameters_hash(task) != (
        SupervisedDevelopmentPipeline._parameters_hash(replace(task, model="other-model"))
    )
    for invalid in ("", "model with spaces", "../model", "model\n", "x" * 129):
        with pytest.raises(LifecycleError, match="governor_model_not_approved"):
            GovernorAdapter.from_candidate(candidate, **arguments, model=invalid)


def test_provider_configuration_excludes_unrelated_secrets(monkeypatch):
    monkeypatch.setenv("JARVIS_SUPERVISED_MODEL", "approved-model")
    monkeypatch.setenv("JARVIS_SUPERVISED_PRICING_MODEL", "approved-model")
    monkeypatch.setenv("GITHUB_TOKEN", "disposable-test-not-a-token")
    configuration = _provider_configuration()
    assert configuration["JARVIS_SUPERVISED_MODEL"] == "approved-model"
    assert configuration["JARVIS_SUPERVISED_PRICING_MODEL"] == "approved-model"
    assert "GITHUB_TOKEN" not in configuration
