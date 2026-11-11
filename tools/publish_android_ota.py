#!/usr/bin/env python3
"""Fail-closed versioned publication; mutable feeds are separate existing pointers.

Only the tag-triggered workflow invokes this tool. No credential provisioning,
repository-setting changes, release replacement or automatic recovery is exposed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from tools.generate_ota_manifest import channel

REPO = "aaronhall105/jarvis-ai"
BASE = f"repos/{REPO}"
API_VERSION = "2026-03-10"
CERT = "009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac"


class PublicationError(RuntimeError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise PublicationError(reason)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class GitHub:
    def run(self, args: list[str], *, data: bytes | None = None, settings: bool = False) -> bytes:
        env = dict(os.environ)
        token = env.pop("JARVIS_RELEASE_SETTINGS_READ_TOKEN", "")
        if settings:
            require(bool(token), "administration_read_credential_missing")
            env["GH_TOKEN"] = token
        require(bool(env.get("GH_TOKEN")), "publication_credential_missing")
        result = subprocess.run(
            ["gh", *args],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=300,
            check=False,
        )
        require(result.returncode == 0, "github_operation_failed_no_automatic_retry")
        return result.stdout

    def api(
        self, path: str, body: dict | None = None, *, method: str = "GET", settings: bool = False
    ) -> Any:
        args = ["api", "--method", method, "-H", f"X-GitHub-Api-Version: {API_VERSION}", path]
        if body is not None:
            args += ["--input", "-"]
        return json.loads(
            self.run(
                args,
                data=json.dumps(body).encode() if body is not None else None,
                settings=settings,
            )
        )

    def download(self, asset_id: int) -> bytes:
        return self.run(
            ["api", "-H", "Accept: application/octet-stream", f"{BASE}/releases/assets/{asset_id}"]
        )

    def upload(self, tag: str, files: list[Path], *, feed: bool = False) -> None:
        require(
            not feed or tag in {"jarvis-alpha-feed", "jarvis-beta-feed", "jarvis-stable-feed"},
            "not_a_channel_pointer",
        )
        args = ["release", "upload", tag, "--repo", REPO, *map(str, files)]
        if feed:
            args.append("--clobber")
        self.run(args)


def immutable_enabled(gh: GitHub) -> None:
    value = gh.api(f"{BASE}/immutable-releases", settings=True)
    require(
        isinstance(value, dict) and value.get("enabled") is True,
        "repository_immutability_disabled_or_unverified",
    )


def tag_identity(gh: GitHub, tag: str, sha: str) -> str:
    canonical = gh.api(f"{BASE}/git/ref/heads/jarvis/unified-production")
    require(
        canonical["object"]["type"] == "commit" and canonical["object"]["sha"] == sha,
        "canonical_source_drift",
    )
    ref = gh.api(f"{BASE}/git/ref/tags/{tag}")
    require(ref.get("ref") == f"refs/tags/{tag}", "tag_name_mismatch")
    obj = ref["object"]
    if obj["type"] == "tag":
        annotated = gh.api(f"{BASE}/git/tags/{obj['sha']}")
        require(annotated.get("tag") == tag, "annotated_tag_name_mismatch")
        target = annotated["object"]
    else:
        target = obj
    require(target["type"] == "commit" and target["sha"] == sha, "release_tag_source_mismatch")
    return str(obj["sha"])


def reject_existing_release(gh: GitHub, tag: str) -> None:
    # Listing includes drafts for the authenticated publisher; a 404 on a by-tag
    # lookup must never disguise a draft or an authorization failure as absence.
    for page in range(1, 101):
        releases = gh.api(f"{BASE}/releases?per_page=100&page={page}")
        require(isinstance(releases, list), "invalid_release_inventory")
        require(
            not any(item.get("tag_name") == tag for item in releases),
            "versioned_release_already_exists",
        )
        if len(releases) < 100:
            return
    raise PublicationError("release_inventory_incomplete")


def targets(version: str) -> list[str]:
    return {"alpha": ["alpha"], "beta": ["beta", "alpha"], "stable": ["stable", "beta", "alpha"]}[
        channel(version)
    ]


def feed_state(gh: GitHub, name: str, code: int) -> dict:
    release = gh.api(f"{BASE}/releases/tags/jarvis-{name}-feed")
    require(
        release.get("tag_name") == f"jarvis-{name}-feed"
        and release.get("draft") is False
        and release.get("immutable") is False,
        "channel_missing_or_not_mutable",
    )
    assets = gh.api(f"{BASE}/releases/{release['id']}/assets?per_page=100")
    require(
        isinstance(assets, list)
        and len(assets) == 1
        and assets[0].get("name") == "update-manifest.json",
        "channel_asset_inventory_invalid",
    )
    asset = assets[0]
    require(asset.get("state") == "uploaded", "channel_asset_not_uploaded")
    raw = gh.download(asset["id"])
    old = json.loads(raw)
    require(
        type(old.get("versionCode")) is int and 0 < old["versionCode"] < code,
        "channel_version_not_monotonic",
    )
    require(
        channel(old["versionName"]) in targets_channel_sources(name), "channel_provenance_invalid"
    )
    require(
        old.get("channel") == channel(old["versionName"])
        and old.get("tag") == "v" + old["versionName"],
        "channel_provenance_invalid",
    )
    require(
        bool(re.fullmatch(r"[0-9a-f]{40}", str(old.get("commitSha", ""))))
        and bool(re.fullmatch(r"[0-9a-f]{64}", str(old.get("sha256", "")))),
        "channel_provenance_invalid",
    )
    # Existing historical feeds can lack GitHub's newer digest metadata. Their
    # bytes/asset ID are pinned here; newly uploaded assets must supply a digest.
    if asset.get("digest") is not None:
        require(asset["digest"] == "sha256:" + digest(raw), "channel_digest_mismatch")
    require(asset.get("size") == len(raw), "channel_size_mismatch")
    return {
        "release_id": release["id"],
        "asset_id": asset["id"],
        "sha256": digest(raw),
        "version_code": old["versionCode"],
        "previous_manifest": raw.decode("utf-8"),
    }


def targets_channel_sources(name: str) -> set[str]:
    return {"alpha": {"alpha", "beta", "stable"}, "beta": {"beta", "stable"}, "stable": {"stable"}}[
        name
    ]


def preflight(
    gh: GitHub, *, tag: str, sha: str, code: int, event: dict, run_id: str, attempt: str
) -> dict:
    require(
        tag.startswith("v") and bool(re.fullmatch(r"[0-9a-f]{40}", sha)) and code > 0,
        "invalid_release_identity",
    )
    version = tag[1:]
    channel(version)
    require(attempt == "1" and run_id.isdigit(), "workflow_retry_requires_reconciliation")
    require(
        event.get("ref") == f"refs/tags/{tag}"
        and event.get("created") is True
        and event.get("deleted") is False
        and event.get("forced") is False
        and event.get("before") == "0" * 40,
        "tag_is_not_a_fresh_creation",
    )
    immutable_enabled(gh)
    identity = tag_identity(gh, tag, sha)
    require(event.get("after") in {sha, identity}, "push_event_source_mismatch")
    reject_existing_release(gh, tag)
    feeds = {name: feed_state(gh, name, code) for name in targets(version)}
    return {
        "tag": tag,
        "sha": sha,
        "code": code,
        "tag_object": identity,
        "run_id": run_id,
        "feeds": feeds,
        "phase": "prepared",
    }


def fence(gh: GitHub, state: dict) -> None:
    immutable_enabled(gh)
    require(
        tag_identity(gh, state["tag"], state["sha"]) == state["tag_object"], "release_tag_moved"
    )


def verify_signer_report(raw: bytes) -> None:
    """Validate apksigner's legacy and scheme-qualified signer report formats.

    The workflow has already executed apksigner verify successfully on each APK.
    This independent evidence check is strict about signer identity, not the
    Android Build Tools version's human-readable signer label.
    """
    try:
        report = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PublicationError("signer_report_mismatch") from exc
    lines = [line.strip() for line in report.splitlines()]
    require(
        "Verifies" in lines and "DOES NOT VERIFY" not in report,
        "signer_report_mismatch",
    )
    counts = [line for line in lines if line.startswith("Number of signers:")]
    require(counts == ["Number of signers: 1"], "signer_report_mismatch")
    fingerprints = [line for line in lines if "certificate SHA-256" in line]
    require(bool(fingerprints), "signer_report_mismatch")
    pattern = re.compile(
        r"(?:Signer #(?P<legacy_id>[1-9][0-9]*)|V[0-9]+(?:\.[0-9]+)? Signer:)"
        r" certificate SHA-256 digest: (?P<sha256>[0-9a-fA-F]{64})"
    )
    for line in fingerprints:
        match = pattern.fullmatch(line)
        if (
            match is None
            or match.group("legacy_id") not in (None, "1")
            or match.group("sha256").lower() != CERT
        ):
            raise PublicationError("signer_report_mismatch")


def local_assets(directory: Path, state: dict) -> dict[str, bytes]:
    version = state["tag"][1:]
    phone = f"jarvis-assistant-v{version}-release.apk"
    watch = f"jarvis-wear-v{version}-release.apk"
    names = [
        phone,
        phone + ".sha256",
        watch,
        "SHA256SUMS.txt",
        "update-manifest.json",
        "product-release-manifest.json",
        "signing-report.txt",
        "wear-signing-report.txt",
        "apk-inspection.txt",
        "wear-apk-inspection.txt",
    ]
    require(
        all((directory / name).is_file() and not (directory / name).is_symlink() for name in names),
        "release_asset_missing_or_symlink",
    )
    files = {name: (directory / name).read_bytes() for name in names}
    require(all(files.values()), "empty_release_asset")
    manifest = json.loads(files["update-manifest.json"])
    require(
        all(
            manifest.get(k) == v
            for k, v in {
                "versionName": version,
                "versionCode": state["code"],
                "commitSha": state["sha"],
                "tag": state["tag"],
                "channel": channel(version),
                "sha256": digest(files[phone]),
                "apkSize": len(files[phone]),
                "apkUrl": f"https://github.com/{REPO}/releases/download/{state['tag']}/{phone}",
                "schemaVersion": 1,
                "minRealtimeProtocol": 2,
                "minSdk": 31,
            }.items()
        ),
        "ota_manifest_mismatch",
    )
    product = json.loads(files["product-release-manifest.json"])
    require(
        product.get("versionName") == version
        and product.get("versionCode") == state["code"]
        and product.get("schemaVersion") == 1
        and product.get("signerSha256") == CERT,
        "product_identity_mismatch",
    )
    require(
        all(
            product.get(k) == state["sha"]
            for k in ["jarvisSourceSha", "coreSourceSha", "phoneSourceSha", "watchSourceSha"]
        ),
        "product_source_mismatch",
    )
    for kind, name in [("phone", phone), ("watch", watch)]:
        require(
            product.get(kind)
            == {"file": name, "sha256": digest(files[name]), "size": len(files[name])},
            "product_asset_mismatch",
        )
    expected_sums = f"{digest(files[phone])}  {phone}\n{digest(files[watch])}  {watch}\n"
    require(
        files["SHA256SUMS.txt"].decode() == expected_sums
        and files[phone + ".sha256"].decode() == expected_sums.splitlines(keepends=True)[0],
        "checksum_file_mismatch",
    )
    for name in ["signing-report.txt", "wear-signing-report.txt"]:
        verify_signer_report(files[name])
    return files


def verify_remote(gh: GitHub, state: dict, files: dict[str, bytes], *, published: bool) -> None:
    fence(gh, state)
    release = gh.api(f"{BASE}/releases/{state['release_id']}")
    require(
        release.get("id") == state["release_id"]
        and release.get("tag_name") == state["tag"]
        and release.get("target_commitish") == state["sha"]
        and release.get("draft") is (not published),
        "published_identity_mismatch",
    )
    require(release.get("immutable") is published, "release_immutability_unverified")
    assets = gh.api(f"{BASE}/releases/{state['release_id']}/assets?per_page=100")
    require(
        isinstance(assets, list)
        and len(assets) == len(files)
        and {a.get("name") for a in assets} == set(files),
        "release_asset_inventory_mismatch",
    )
    ids = set()
    for asset in assets:
        name = asset["name"]
        require(
            type(asset.get("id")) is int
            and asset["id"] not in ids
            and asset.get("state") == "uploaded",
            "release_asset_identity_invalid",
        )
        ids.add(asset["id"])
        require(
            asset.get("size") == len(files[name])
            and asset.get("digest") == "sha256:" + digest(files[name]),
            "release_asset_digest_mismatch",
        )
        require(gh.download(asset["id"]) == files[name], "release_asset_download_mismatch")


def publish(gh: GitHub, state: dict, directory: Path, save) -> None:
    require(state.get("phase") == "prepared", "publication_requires_reconciliation")
    fence(gh, state)
    reject_existing_release(gh, state["tag"])
    files = local_assets(directory, state)
    require(
        {n: feed_state(gh, n, state["code"]) for n in state["feeds"]} == state["feeds"],
        "channel_changed_after_preflight",
    )
    state["phase"] = "creating_draft"
    save(state)  # Record uncertain effect before transmitting; never auto-retry.
    release = gh.api(
        f"{BASE}/releases",
        {
            "tag_name": state["tag"],
            "target_commitish": state["sha"],
            "name": "Jarvis " + state["tag"][1:],
            "body": (directory / "release-notes.txt").read_text(),
            "draft": True,
            "prerelease": channel(state["tag"][1:]) != "stable",
            "make_latest": "false",
        },
        method="POST",
    )
    require(
        type(release.get("id")) is int
        and release.get("draft") is True
        and release.get("tag_name") == state["tag"]
        and release.get("target_commitish") == state["sha"],
        "draft_identity_mismatch",
    )
    state.update(release_id=release["id"], phase="uploading_assets")
    save(state)
    gh.upload(state["tag"], [directory / name for name in files])
    verify_remote(gh, state, files, published=False)
    state["phase"] = "publishing"
    save(state)
    fence(gh, state)
    gh.api(f"{BASE}/releases/{state['release_id']}", {"draft": False}, method="PATCH")
    verify_remote(gh, state, files, published=True)
    state["phase"] = "immutable_verified"
    save(state)


def promote(gh: GitHub, state: dict, directory: Path, save) -> None:
    require(state.get("phase") == "immutable_verified", "immutable_release_not_verified")
    files = local_assets(directory, state)
    verify_remote(gh, state, files, published=True)
    require(list(state["feeds"]) == targets(state["tag"][1:]), "unexpected_channel_targets")
    require(
        {n: feed_state(gh, n, state["code"]) for n in state["feeds"]} == state["feeds"],
        "channel_changed_after_preflight",
    )
    for name in state["feeds"]:
        fence(gh, state)
        require(
            feed_state(gh, name, state["code"]) == state["feeds"][name],
            "channel_changed_before_promotion",
        )
        state.update(phase="promoting", promoting=name)
        save(state)
        gh.upload(f"jarvis-{name}-feed", [directory / "update-manifest.json"], feed=True)
        release = gh.api(f"{BASE}/releases/tags/jarvis-{name}-feed")
        require(
            release.get("id") == state["feeds"][name]["release_id"]
            and release.get("immutable") is False
            and release.get("draft") is False,
            "channel_identity_changed",
        )
        assets = gh.api(f"{BASE}/releases/{release['id']}/assets?per_page=100")
        require(
            len(assets) == 1
            and assets[0].get("name") == "update-manifest.json"
            and assets[0].get("state") == "uploaded",
            "channel_readback_missing",
        )
        expected = files["update-manifest.json"]
        require(
            assets[0].get("digest") == "sha256:" + digest(expected)
            and gh.download(assets[0]["id"]) == expected,
            "channel_readback_mismatch",
        )
        state.setdefault("promoted", []).append(name)
        save(state)
    state["phase"] = "complete"
    save(state)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["preflight", "publish", "promote"])
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--assets", type=Path, default=Path.cwd())
    args = parser.parse_args()
    gh = GitHub()
    require(
        os.environ.get("GITHUB_REPOSITORY") == REPO
        and os.environ.get("GITHUB_EVENT_NAME") == "push"
        and os.environ.get("GITHUB_RUN_ATTEMPT") == "1",
        "unsupported_workflow_context",
    )

    def save(state: dict) -> None:
        temporary = args.state.with_suffix(".next")
        with temporary.open("x") as handle:
            json.dump(state, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, args.state)

    if args.phase == "preflight":
        require(not args.state.exists(), "preflight_already_recorded")
        baseline = json.loads(Path("docs/JARVIS_PRODUCT_BASELINE.json").read_text())[
            "current_release"
        ]
        tag = os.environ["GITHUB_REF_NAME"]
        require(tag == "v" + baseline["version_name"], "baseline_tag_mismatch")
        state = preflight(
            gh,
            tag=tag,
            sha=os.environ["GITHUB_SHA"],
            code=baseline["version_code"],
            event=json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()),
            run_id=os.environ["GITHUB_RUN_ID"],
            attempt=os.environ["GITHUB_RUN_ATTEMPT"],
        )
        save(state)
    else:
        state = json.loads(args.state.read_text())
        require(
            state["run_id"] == os.environ["GITHUB_RUN_ID"]
            and state["tag"] == os.environ["GITHUB_REF_NAME"]
            and state["sha"] == os.environ["GITHUB_SHA"],
            "publication_state_binding_mismatch",
        )
        {"publish": publish, "promote": promote}[args.phase](gh, state, args.assets, save)


if __name__ == "__main__":
    main()
