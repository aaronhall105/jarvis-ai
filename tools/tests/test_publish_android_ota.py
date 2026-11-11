"""Publication contract tests: disposable assets and a stateful GitHub boundary.

No APK signing, network writes, release or feed operations occur in these tests.
"""

import copy
import json
import subprocess
from pathlib import Path

import pytest

from tools import publish_android_ota as ota
from tools.generate_ota_manifest import generate
from tools.generate_product_release_manifest import build_manifest

SHA = "1" * 40
TAG = "v19.0.0-alpha47"
CODE = 190490


class GitHubFixture:
    def __init__(self):
        self.settings = {"enabled": True}
        self.canonical = SHA
        self.tag_object = SHA
        self.tag_target = SHA
        self.immutable_on_publish = True
        self.releases = {}
        self.assets = {}
        self.payloads = {}
        self.effects = []
        self.serial = 100
        self.fail_upload = False
        self.fail_publish = False
        for i, name in enumerate(["alpha", "beta", "stable"], 1):
            self.releases[i] = {
                "id": i,
                "tag_name": f"jarvis-{name}-feed",
                "draft": False,
                "immutable": False,
            }
            version = {"alpha": "19.0.0-alpha46", "beta": "18.0.0-beta1", "stable": "18.0.0"}[name]
            manifest = {
                "versionName": version,
                "versionCode": 190480,
                "channel": name,
                "tag": "v" + version,
                "commitSha": "2" * 40,
                "sha256": "3" * 64,
            }
            self.assets[i] = [self.asset("update-manifest.json", json.dumps(manifest).encode())]

    def asset(self, name, raw):
        self.serial += 1
        self.payloads[self.serial] = raw
        return {
            "id": self.serial,
            "name": name,
            "state": "uploaded",
            "size": len(raw),
            "digest": "sha256:" + ota.digest(raw),
        }

    def api(self, path, body=None, *, method="GET", settings=False):
        route = path.removeprefix(ota.BASE)
        if method != "GET":
            self.effects.append((method, path, copy.deepcopy(body)))
            if method == "POST" and route == "/releases":
                release = {**body, "id": 20, "immutable": False}
                self.releases[20] = release
                self.assets[20] = []
                return copy.deepcopy(release)
            if method == "PATCH" and route == "/releases/20":
                if self.fail_publish:
                    raise ota.PublicationError("ambiguous_publish")
                self.releases[20].update(draft=False, immutable=self.immutable_on_publish)
                return copy.deepcopy(self.releases[20])
            raise AssertionError((path, method))
        if route == "/immutable-releases":
            assert settings
            return copy.deepcopy(self.settings)
        if route == "/git/ref/heads/jarvis/unified-production":
            return {"object": {"type": "commit", "sha": self.canonical}}
        if route.startswith("/git/ref/tags/"):
            return {
                "ref": "refs/tags/" + route.split("/tags/")[1],
                "object": {
                    "type": "tag" if self.tag_object != self.tag_target else "commit",
                    "sha": self.tag_object,
                },
            }
        if route.startswith("/git/tags/"):
            return {"tag": TAG, "object": {"type": "commit", "sha": self.tag_target}}
        if route.startswith("/releases?per_page="):
            return copy.deepcopy(list(self.releases.values()))
        if route.startswith("/releases/tags/"):
            matches = [
                r for r in self.releases.values() if r["tag_name"] == route.split("/tags/")[1]
            ]
            if not matches:
                raise ota.PublicationError("missing_release")
            return copy.deepcopy(matches[0])
        if route.startswith("/releases/"):
            release_id = int(route.split("/")[2])
            return copy.deepcopy(
                self.assets[release_id] if "/assets?" in route else self.releases[release_id]
            )
        raise AssertionError(path)

    def download(self, asset_id):
        return self.payloads[asset_id]

    def upload(self, tag, files, *, feed=False):
        self.effects.append(("upload", tag, feed))
        if self.fail_upload:
            raise ota.PublicationError("ambiguous_upload")
        release_id = next(i for i, r in self.releases.items() if r["tag_name"] == tag)
        assert feed or not self.assets[release_id]
        if feed:
            assert self.releases[20]["immutable"] is True
        self.assets[release_id] = [self.asset(p.name, p.read_bytes()) for p in files]


def event(**changes):
    return {
        "ref": "refs/tags/" + TAG,
        "created": True,
        "deleted": False,
        "forced": False,
        "before": "0" * 40,
        "after": SHA,
        **changes,
    }


def prepare(gh, **changes):
    return ota.preflight(
        gh,
        **{
            "tag": TAG,
            "sha": SHA,
            "code": CODE,
            "event": event(),
            "run_id": "123",
            "attempt": "1",
            **changes,
        },
    )


@pytest.fixture
def candidate(tmp_path):
    return make_candidate(tmp_path, TAG[1:])


def make_candidate(tmp_path, version):
    gh = GitHubFixture()
    tag = "v" + version
    state = prepare(gh, tag=tag, event=event(ref="refs/tags/" + tag))
    phone = tmp_path / f"jarvis-assistant-v{version}-release.apk"
    watch = tmp_path / f"jarvis-wear-v{version}-release.apk"
    phone.write_bytes(b"test phone bytes; not an APK")
    watch.write_bytes(b"test watch bytes; not an APK")
    manifest = generate(phone, version, CODE, SHA, "Test notes", "2026-11-11T00:00:00Z")
    product = build_manifest(
        phone=phone,
        watch=watch,
        version=version,
        version_code=CODE,
        source_sha=SHA,
        signer_sha256=ota.CERT,
    )
    (tmp_path / "update-manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "product-release-manifest.json").write_text(json.dumps(product))
    sums = [f"{ota.digest(p.read_bytes())}  {p.name}\n" for p in (phone, watch)]
    (tmp_path / "SHA256SUMS.txt").write_text("".join(sums))
    (tmp_path / (phone.name + ".sha256")).write_text(sums[0])
    for name in ["signing-report.txt", "wear-signing-report.txt"]:
        (tmp_path / name).write_text("Signer #1 certificate SHA-256 digest: " + ota.CERT)
    for name in ["apk-inspection.txt", "wear-apk-inspection.txt", "release-notes.txt"]:
        (tmp_path / name).write_text("Fixture verification record")
    history = []
    return gh, state, tmp_path, lambda s: history.append(copy.deepcopy(s)), history


def test_complete_immutable_publication_then_alpha_promotion(candidate):
    gh, state, directory, save, history = candidate
    untouched = copy.deepcopy({i: gh.assets[i] for i in (2, 3)})
    ota.publish(gh, state, directory, save)
    assert len(gh.assets[20]) == 10 and state["phase"] == "immutable_verified"
    ota.promote(gh, state, directory, save)
    assert state["phase"] == "complete" and state["promoted"] == ["alpha"]
    assert [s["phase"] for s in history][:4] == [
        "creating_draft",
        "uploading_assets",
        "publishing",
        "immutable_verified",
    ]
    assert {i: gh.assets[i] for i in (2, 3)} == untouched
    assert [e for e in gh.effects if e[0] == "upload"] == [
        ("upload", TAG, False),
        ("upload", "jarvis-alpha-feed", True),
    ]


@pytest.mark.parametrize("settings", [{}, {"enabled": False}, {"enabled": "true"}, None])
def test_missing_or_disabled_repository_immutability_denies(settings):
    gh = GitHubFixture()
    gh.settings = settings
    with pytest.raises(ota.PublicationError, match="immutability"):
        prepare(gh)
    assert not gh.effects


@pytest.mark.parametrize("draft", [True, False])
def test_existing_version_including_draft_is_never_repaired(draft):
    gh = GitHubFixture()
    gh.releases[20] = {"tag_name": TAG, "draft": draft}
    with pytest.raises(ota.PublicationError, match="already_exists"):
        prepare(gh)
    assert not gh.effects


@pytest.mark.parametrize(
    "changes",
    [
        {"created": False},
        {"forced": True},
        {"deleted": True},
        {"before": "a" * 40},
        {"after": "a" * 40},
        {"ref": "refs/tags/v19.0.0-alpha46"},
    ],
)
def test_reused_moved_or_wrong_push_event_rejected(changes):
    gh = GitHubFixture()
    with pytest.raises(ota.PublicationError):
        prepare(gh, event=event(**changes))
    assert not gh.effects


def test_workflow_rerun_and_canonical_drift_rejected():
    gh = GitHubFixture()
    with pytest.raises(ota.PublicationError, match="reconciliation"):
        prepare(gh, attempt="2")
    gh.canonical = "a" * 40
    with pytest.raises(ota.PublicationError, match="canonical_source_drift"):
        prepare(gh)


def test_annotated_tag_exact_object_is_frozen():
    gh = GitHubFixture()
    gh.tag_object = "b" * 40
    state = prepare(gh)
    assert state["tag_object"] == gh.tag_object
    gh.tag_object = "c" * 40
    with pytest.raises(ota.PublicationError, match="tag_moved"):
        ota.fence(gh, state)
    gh.tag_target = "d" * 40
    with pytest.raises(ota.PublicationError, match="source_mismatch"):
        prepare(gh)


@pytest.mark.parametrize("defect", ["immutable", "missing", "nonmonotonic", "digest"])
def test_invalid_channel_blocks_before_any_release_creation(defect):
    gh = GitHubFixture()
    if defect == "immutable":
        gh.releases[1]["immutable"] = True
    elif defect == "missing":
        del gh.releases[1]
    elif defect == "nonmonotonic":
        old = json.loads(gh.download(gh.assets[1][0]["id"]))
        old["versionCode"] = CODE
        gh.assets[1] = [gh.asset("update-manifest.json", json.dumps(old).encode())]
    else:
        gh.assets[1][0]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(ota.PublicationError):
        prepare(gh)
    assert not gh.effects


@pytest.mark.parametrize(
    "file",
    [
        "product-release-manifest.json",
        "update-manifest.json",
        "signing-report.txt",
        "SHA256SUMS.txt",
        "wear-apk-inspection.txt",
    ],
)
def test_missing_or_tampered_local_evidence_prevents_publication(candidate, file):
    gh, state, directory, save, _ = candidate
    (directory / file).unlink()
    with pytest.raises(ota.PublicationError):
        ota.publish(gh, state, directory, save)
    assert not gh.effects


@pytest.mark.parametrize(
    "field,value",
    [("coreSourceSha", "a" * 40), ("signerSha256", "0" * 64), ("versionCode", 190480)],
)
def test_contradictory_provenance_rejected(candidate, field, value):
    gh, state, directory, save, _ = candidate
    path = directory / "product-release-manifest.json"
    metadata = json.loads(path.read_text())
    metadata[field] = value
    path.write_text(json.dumps(metadata))
    with pytest.raises(ota.PublicationError):
        ota.publish(gh, state, directory, save)
    assert not gh.effects


@pytest.mark.parametrize("immutable", [False, None, "true"])
def test_publication_not_proven_immutable_never_promotes(candidate, immutable):
    gh, state, directory, save, _ = candidate
    gh.immutable_on_publish = immutable
    with pytest.raises(ota.PublicationError, match="immutability"):
        ota.publish(gh, state, directory, save)
    with pytest.raises(ota.PublicationError, match="not_verified"):
        ota.promote(gh, state, directory, save)
    assert not any(e[0] == "upload" and e[2] for e in gh.effects)


@pytest.mark.parametrize(
    "defect", ["missing", "extra", "digest", "download", "source", "immutable"]
)
def test_remote_integrity_rechecked_before_feed(candidate, defect):
    gh, state, directory, save, _ = candidate
    ota.publish(gh, state, directory, save)
    if defect == "missing":
        gh.assets[20].pop()
    elif defect == "extra":
        gh.assets[20].append(gh.asset("unexpected", b"unexpected"))
    elif defect == "digest":
        gh.assets[20][0].pop("digest")
    elif defect == "download":
        gh.payloads[gh.assets[20][0]["id"]] = b"altered"
    elif defect == "source":
        gh.releases[20]["target_commitish"] = "a" * 40
    else:
        gh.settings["enabled"] = False
    with pytest.raises(ota.PublicationError):
        ota.promote(gh, state, directory, save)
    assert not any(e[0] == "upload" and e[2] for e in gh.effects)


@pytest.mark.parametrize("failure", ["fail_upload", "fail_publish"])
def test_uncertain_publication_is_not_replayed(candidate, failure):
    gh, state, directory, save, history = candidate
    setattr(gh, failure, True)
    with pytest.raises(ota.PublicationError, match="ambiguous"):
        ota.publish(gh, state, directory, save)
    assert history[-1]["phase"] in {"uploading_assets", "publishing"}
    effects = copy.deepcopy(gh.effects)
    with pytest.raises(ota.PublicationError, match="reconciliation"):
        ota.publish(gh, state, directory, save)
    assert gh.effects == effects


def test_changed_channel_and_failed_promotion_require_reconciliation(candidate):
    gh, state, directory, save, _ = candidate
    ota.publish(gh, state, directory, save)
    original = copy.deepcopy(gh.assets[1])
    gh.assets[1] = [gh.asset("update-manifest.json", gh.download(original[0]["id"]))]
    with pytest.raises(ota.PublicationError, match="channel_changed"):
        ota.promote(gh, state, directory, save)
    gh.assets[1] = original
    gh.fail_upload = True
    with pytest.raises(ota.PublicationError, match="ambiguous"):
        ota.promote(gh, state, directory, save)
    assert state["phase"] == "promoting"
    effects = copy.deepcopy(gh.effects)
    with pytest.raises(ota.PublicationError):
        ota.promote(gh, state, directory, save)
    assert gh.effects == effects


def test_channel_isolation():
    assert ota.targets("19.0.0-alpha47") == ["alpha"]
    assert ota.targets("19.0.0-beta1") == ["beta", "alpha"]
    assert ota.targets("19.0.0") == ["stable", "beta", "alpha"]


@pytest.mark.parametrize("version", ["19.0.0-beta1", "19.0.0"])
def test_verified_multi_channel_promotion(tmp_path, version):
    gh, state, directory, save, _ = make_candidate(tmp_path, version)
    ota.publish(gh, state, directory, save)
    ota.promote(gh, state, directory, save)
    assert state["phase"] == "complete"
    assert state["promoted"] == ota.targets(version)


def test_partial_channel_failure_preserves_prior_success_and_stops(tmp_path, monkeypatch):
    gh, state, directory, save, _ = make_candidate(tmp_path, "19.0.0-beta1")
    ota.publish(gh, state, directory, save)
    old_alpha = copy.deepcopy(gh.assets[1])
    old_stable = copy.deepcopy(gh.assets[3])
    upload = gh.upload

    def fail_alpha(tag, files, *, feed=False):
        if tag == "jarvis-alpha-feed":
            raise ota.PublicationError("ambiguous_channel_failure")
        upload(tag, files, feed=feed)

    monkeypatch.setattr(gh, "upload", fail_alpha)
    with pytest.raises(ota.PublicationError, match="ambiguous_channel_failure"):
        ota.promote(gh, state, directory, save)
    assert state["promoted"] == ["beta"]
    assert state["promoting"] == "alpha" and state["phase"] == "promoting"
    assert gh.assets[1] == old_alpha and gh.assets[3] == old_stable
    assert json.loads(gh.download(gh.assets[2][0]["id"]))["versionCode"] == CODE
    with pytest.raises(ota.PublicationError, match="not_verified"):
        ota.promote(gh, state, directory, save)


@pytest.mark.parametrize(
    "version,expected",
    [("19.0.0-beta1", ["beta", "alpha"]), ("19.0.0", ["stable", "beta", "alpha"])],
)
def test_channel_preflight_requires_every_target(version, expected):
    gh = GitHubFixture()
    tag = "v" + version
    state = prepare(gh, tag=tag, event=event(ref="refs/tags/" + tag))
    assert list(state["feeds"]) == expected
    del gh.releases[2]
    with pytest.raises(ota.PublicationError, match="missing_release"):
        prepare(gh, tag=tag, event=event(ref="refs/tags/" + tag))
    assert not gh.effects


def test_prior_channel_manifest_is_preserved_for_reconciliation(candidate):
    gh, state, _, _, _ = candidate
    prior = state["feeds"]["alpha"]
    assert prior["previous_manifest"].encode() == gh.download(prior["asset_id"])
    assert ota.digest(prior["previous_manifest"].encode()) == prior["sha256"]


def test_wrong_signer_and_symlink_cannot_reach_publication(candidate):
    gh, state, directory, save, _ = candidate
    path = directory / "signing-report.txt"
    path.write_text("Signer #1 certificate SHA-256 digest: " + "0" * 64)
    with pytest.raises(ota.PublicationError, match="signer"):
        ota.publish(gh, state, directory, save)
    path.unlink()
    path.symlink_to(directory / "wear-signing-report.txt")
    with pytest.raises(ota.PublicationError, match="symlink"):
        ota.publish(gh, state, directory, save)
    assert not gh.effects


def test_duplicate_appearing_after_preflight_blocks_without_overwrite(candidate):
    gh, state, directory, save, _ = candidate
    gh.releases[21] = {"tag_name": TAG, "draft": True}
    with pytest.raises(ota.PublicationError, match="already_exists"):
        ota.publish(gh, state, directory, save)
    assert not gh.effects


def test_ambiguous_draft_creation_records_state_and_never_retries(candidate, monkeypatch):
    gh, state, directory, save, history = candidate
    api = gh.api

    def uncertain(path, body=None, **kwargs):
        result = api(path, body, **kwargs)
        if kwargs.get("method") == "POST":
            raise ota.PublicationError("connection_lost_after_creation")
        return result

    monkeypatch.setattr(gh, "api", uncertain)
    with pytest.raises(ota.PublicationError, match="connection_lost"):
        ota.publish(gh, state, directory, save)
    assert history[-1]["phase"] == "creating_draft"
    assert gh.releases[20]["draft"]
    effects = copy.deepcopy(gh.effects)
    with pytest.raises(ota.PublicationError, match="reconciliation"):
        ota.publish(gh, state, directory, save)
    assert effects == gh.effects


def test_corrupt_feed_readback_never_reports_success(candidate, monkeypatch):
    gh, state, directory, save, _ = candidate
    ota.publish(gh, state, directory, save)
    upload = gh.upload

    def corrupt(tag, files, *, feed=False):
        upload(tag, files, feed=feed)
        gh.payloads[gh.assets[1][0]["id"]] = b"corrupt"

    monkeypatch.setattr(gh, "upload", corrupt)
    with pytest.raises(ota.PublicationError, match="readback_mismatch"):
        ota.promote(gh, state, directory, save)
    assert state["phase"] == "promoting" and "promoted" not in state


@pytest.mark.parametrize("result", ["403", "timeout", "malformed"])
def test_settings_evidence_transport_failure_is_closed(monkeypatch, result):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if result == "timeout":
            raise subprocess.TimeoutExpired(args, 300)
        return subprocess.CompletedProcess(
            args, 1 if result == "403" else 0, b"not-json", b"private error details"
        )

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("JARVIS_RELEASE_SETTINGS_READ_TOKEN", "fixture-settings-token")
    with pytest.raises((ota.PublicationError, subprocess.TimeoutExpired, json.JSONDecodeError)):
        prepare(ota.GitHub())
    assert len(calls) == 1 and "GET" in calls[0] and "POST" not in calls[0]


def test_real_transport_separates_settings_token_and_never_clobbers_versions(monkeypatch, tmp_path):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, b'{"enabled":true}', b"")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("GH_TOKEN", "fixture-content-token")
    monkeypatch.setenv("JARVIS_RELEASE_SETTINGS_READ_TOKEN", "fixture-settings-token")
    gh = ota.GitHub()
    ota.immutable_enabled(gh)
    gh.upload(TAG, [tmp_path / "asset"])
    gh.upload("jarvis-alpha-feed", [tmp_path / "update-manifest.json"], feed=True)
    assert calls[0][1]["env"]["GH_TOKEN"] == "fixture-settings-token"
    assert calls[1][1]["env"]["GH_TOKEN"] == "fixture-content-token"
    assert "--clobber" not in calls[1][0] and "--clobber" in calls[2][0]
    assert all("JARVIS_RELEASE_SETTINGS_READ_TOKEN" not in c[1]["env"] for c in calls)
    with pytest.raises(ota.PublicationError):
        gh.upload(TAG, [], feed=True)
    monkeypatch.delenv("JARVIS_RELEASE_SETTINGS_READ_TOKEN")
    with pytest.raises(ota.PublicationError, match="credential_missing"):
        ota.immutable_enabled(gh)


def test_workflow_orders_security_gate_before_signing_and_promotion():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/android-ota-release.yml").read_text()
    assert workflow.index("publish_android_ota preflight") < workflow.index(
        "Restore stable signing keystore"
    )
    assert workflow.index("publish_android_ota publish") < workflow.index(
        "publish_android_ota promote"
    )
    assert "group: jarvis-android-ota-publication" in workflow
    assert "gh release edit" not in workflow and "--clobber" not in workflow
    assert "publish_android_ota.py" in workflow
