import json
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
    assert manifest["current_release"] == {
        "version_name": "19.0.0-alpha26",
        "version_code": 190280,
        "core_application_version": "3.7.0",
        "realtime_protocol": 2,
        "phone_package": "com.aaron.jarvisvoice",
        "production_signer_sha256": (
            "009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac"
        ),
    }
    assert Path(verify_product_baseline.MANIFEST).is_file()


def test_deployment_has_no_retired_checkout_fallback() -> None:
    deployment = (verify_product_baseline.ROOT / "tools/deploy_unified_core.sh").read_text(
        encoding="utf-8"
    )
    assert "JARVIS_PREVIOUS_LIVE_ROOT" not in deployment
    assert "final persistent-data cutover" not in deployment
    assert ".pre-cutover-" not in deployment
    assert 'LIVE_ROOT="${JARVIS_LIVE_ROOT:-/home/aaron/.local/share/jarvis-runtime}"' in deployment


def test_ground_truth_apks_resolve_to_one_existing_source_lineage() -> None:
    manifest = verify_product_baseline.load_manifest()
    phone = manifest["ground_truth_apks"]["phone"]
    watch = manifest["ground_truth_apks"]["watch"]
    assert phone["sha256"] == "5e3961eb3484c814a301f5385f11f3db890ad6d66a9ef79e933eb3209af40e16"
    assert watch["sha256"] == "98e1f543d1afcbf267dc465a84959aaa77e2cf76913cceab596aa3e0e41efe91"
    assert phone["source_commit"] == watch["source_commit"]
    assert phone["actions_artifact_id"] == watch["actions_artifact_id"]


def test_consolidation_lineage_has_required_decision_fields() -> None:
    path = verify_product_baseline.ROOT / "docs/JARVIS_CONSOLIDATION_LINEAGE.json"
    lineage = json.loads(path.read_text(encoding="utf-8"))
    assert lineage["authoritative_branch"] == "jarvis/unified-production"
    assert lineage["capabilities"]
    required = {
        "capability",
        "current_implementations",
        "best_source",
        "source_commit",
        "target_location",
        "action",
    }
    for capability in lineage["capabilities"]:
        assert required == set(capability)


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
