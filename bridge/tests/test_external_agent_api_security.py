from __future__ import annotations

import ast
from pathlib import Path


MAIN_PATH = Path(__file__).resolve().parents[1] / "app" / "main.py"

SENSITIVE_EXTERNAL_AGENT_ROUTES = {
    ("get", "/api/integrations/providers"),
    ("get", "/api/integrations/capabilities"),
    ("get", "/api/integrations/health"),
    ("get", "/api/integrations/actions"),
    ("post", "/api/agent/plans"),
    ("get", "/api/agent/plans"),
    ("get", "/api/agent/plans/{plan_id}"),
    ("post", "/api/agent/plans/{plan_id}/resume"),
    ("post", "/api/agent/plans/{plan_id}/replan"),
    ("post", "/api/agent/plans/{plan_id}/steps/{step_id}/approve"),
    ("post", "/api/agent/plans/{plan_id}/cancel"),
    ("post", "/api/external-monitors"),
    ("get", "/api/external-monitors"),
    ("post", "/api/external-monitors/{job_id}/cancel"),
}

MOBILE_ACCOUNT_ROUTES = {
    ("get", "/api/integrations/mobile/providers"),
    ("post", "/api/integrations/mobile/google/start"),
    ("get", "/api/integrations/mobile/google/sessions/{session_id}"),
    ("delete", "/api/integrations/mobile/google/accounts/{account_id}"),
    ("get", "/api/personal-assistant/jobs"),
    ("get", "/api/personal-assistant/jobs/completions"),
    ("get", "/api/personal-assistant/jobs/diagnostics"),
    ("get", "/api/personal-assistant/jobs/{job_id}"),
    ("post", "/api/personal-assistant/jobs/{job_id}/cancel"),
    ("post", "/api/personal-assistant/jobs/{job_id}/pause"),
    ("post", "/api/personal-assistant/jobs/{job_id}/resume"),
    ("post", "/api/personal-assistant/jobs/{job_id}/reschedule"),
}


def _route(decorator: ast.expr) -> tuple[str, str] | None:
    if not isinstance(decorator, ast.Call) or not decorator.args:
        return None
    function = decorator.func
    if (
        not isinstance(function, ast.Attribute)
        or not isinstance(function.value, ast.Name)
        or function.value.id != "app"
        or function.attr not in {"get", "post", "put", "patch", "delete"}
    ):
        return None
    path = decorator.args[0]
    if not isinstance(path, ast.Constant) or not isinstance(path.value, str):
        return None
    return function.attr, path.value


def test_every_sensitive_external_agent_route_requires_the_integration_token() -> None:
    tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"), filename=str(MAIN_PATH))
    protected: set[tuple[str, str]] = set()
    discovered: set[tuple[str, str]] = set()

    for node in tree.body:
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        routes = {
            route
            for decorator in node.decorator_list
            if (route := _route(decorator)) in SENSITIVE_EXTERNAL_AGENT_ROUTES
        }
        if not routes:
            continue
        discovered.update(routes)
        argument_names = {argument.arg for argument in node.args.args}
        calls_guard = any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "_require_integrations_token"
            for child in ast.walk(node)
        )
        if "x_jarvis_integrations_token" in argument_names and calls_guard:
            protected.update(routes)

    assert discovered == SENSITIVE_EXTERNAL_AGENT_ROUTES
    assert protected == SENSITIVE_EXTERNAL_AGENT_ROUTES


def test_integration_token_guard_is_fail_closed_and_constant_time() -> None:
    source = MAIN_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MAIN_PATH))
    guard = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_require_integrations_token"
    )
    segment = ast.get_source_segment(source, guard) or ""

    assert "if not expected" in segment
    assert "status_code=503" in segment
    assert "secrets.compare_digest" in segment
    assert "status_code=403" in segment


def test_mobile_account_routes_map_bearer_auth_to_server_owned_principal() -> None:
    source = MAIN_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MAIN_PATH))
    protected: set[tuple[str, str]] = set()
    for node in tree.body:
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        routes = {
            route
            for decorator in node.decorator_list
            if (route := _route(decorator)) in MOBILE_ACCOUNT_ROUTES
        }
        if routes and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "_require_mobile_integration_principal"
            for child in ast.walk(node)
        ):
            protected.update(routes)
    assert protected == MOBILE_ACCOUNT_ROUTES

    guard = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_require_mobile_integration_principal"
    )
    segment = ast.get_source_segment(source, guard) or ""
    assert "jarvis_integrations_owner_principal" in segment
    assert "secrets.compare_digest" in segment
    assert "status_code=503" in segment
    assert "status_code=403" in segment
    assert "_safe_token_fingerprint" in segment
    assert "authorization" not in segment.split("logger.warning", 1)[-1]
