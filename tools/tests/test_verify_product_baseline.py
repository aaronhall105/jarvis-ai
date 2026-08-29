from pathlib import Path

from tools import verify_product_baseline


def test_repository_satisfies_mandatory_product_assertions() -> None:
    manifest = verify_product_baseline.load_manifest()
    assert verify_product_baseline.verify_assertions(manifest) == []
    assert verify_product_baseline.verify_android_identity() == []
    assert verify_product_baseline.verify_release_workflows() == []


def test_manifest_identifies_single_authoritative_branch() -> None:
    manifest = verify_product_baseline.load_manifest()
    assert manifest["authoritative_branch"] == "jarvis/unified-production"
    assert Path(verify_product_baseline.MANIFEST).is_file()


def test_manifest_covers_every_release_product_area() -> None:
    manifest = verify_product_baseline.load_manifest()
    areas = manifest["product_areas"]
    mandatory = {
        "bridge_app_and_core_apis",
        "ai_engine",
        "conversation_engine",
        "realtime_voice",
        "realtime_turn_ledger_and_recovery",
        "memory",
        "followup_engine",
        "presence_and_home_assistant_grounding",
        "home_assistant_integration",
        "external_agent_platform",
        "planner",
        "connector_framework",
        "google_oauth_and_integrations",
        "gmail",
        "google_calendar",
        "google_contacts",
        "web_research_and_browser_abstractions",
        "security_auth_and_action_receipts",
        "proactive_intelligence",
        "vision_and_cameras",
        "android_phone_app",
        "wear_os_app",
        "developer_codex_gateway",
        "static_web_chat",
        "ota_updater",
        "deployment_and_release_tooling",
        "observability_and_health",
        "tests",
    }
    assert mandatory == set(areas)
    for area in areas.values():
        assert area["branches"]
        assert area["intended_source"]
        assert area["unique_capabilities"]
        assert area["missing_from_other_line"]
        assert area["deployed_before"]
        assert area["reconciliation"]
