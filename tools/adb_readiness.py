#!/usr/bin/env python3
"""Maintain safe ADB discovery for Jarvis phone/watch development targets."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ADB = os.environ.get("ADB_BIN", "/home/aaron/Android/Sdk/platform-tools/adb")
INTERVAL_SECONDS = max(10, int(os.environ.get("JARVIS_ADB_SCAN_SECONDS", "20")))
STATE_PATH = Path(
    os.environ.get(
        "JARVIS_ADB_STATE_PATH",
        str(Path.home() / ".local/state/jarvis/adb-readiness.json"),
    )
)
ALLOWED_MODELS = {"SM_G996B": "phone", "SM_L315F": "watch"}


@dataclass(frozen=True)
class Device:
    serial: str
    model: str
    role: str


def parse_mdns(output: str) -> list[str]:
    endpoints: list[str] = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[-2] == "_adb-tls-connect._tcp":
            endpoint = fields[-1]
            if endpoint.count(":") == 1 and endpoint not in endpoints:
                endpoints.append(endpoint)
    return endpoints


def parse_mdns_services(output: str) -> list[tuple[str, str]]:
    services: list[tuple[str, str]] = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[-2] == "_adb-tls-connect._tcp":
            item = (fields[-3], fields[-1])
            if item not in services:
                services.append(item)
    return services


def parse_devices(output: str) -> list[Device]:
    devices: list[Device] = []
    for line in output.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 2 or fields[1] != "device":
            continue
        model_field = next((item for item in fields[2:] if item.startswith("model:")), "")
        model = model_field.removeprefix("model:").replace("-", "_")
        role = ALLOWED_MODELS.get(model)
        if role:
            devices.append(Device(fields[0], model, role))
    return devices


def run_adb(*arguments: str, timeout: int = 8) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [ADB, *arguments], check=False, capture_output=True, text=True, timeout=timeout
    )


def discover() -> dict[str, object]:
    mdns_output = run_adb("mdns", "services").stdout
    services = parse_mdns_services(mdns_output)
    connected = parse_devices(run_adb("devices", "-l").stdout)
    ready_roles = {device.role for device in connected}
    for service_name, endpoint in services:
        if any(service_name in device.serial for device in connected):
            continue
        try:
            run_adb("connect", endpoint, timeout=3)
        except subprocess.TimeoutExpired:
            continue
        refreshed = parse_devices(run_adb("devices", "-l").stdout)
        endpoint_device = next((item for item in refreshed if item.serial == endpoint), None)
        if endpoint_device is None:
            continue
        if endpoint_device.role in ready_roles:
            # The paired mDNS serial already represents this physical model.
            run_adb("disconnect", endpoint, timeout=3)
            continue
        connected = refreshed
        ready_roles.add(endpoint_device.role)
    return {
        "updated_at": int(time.time()),
        "wireless_endpoints_seen": len(services),
        "devices": [asdict(device) for device in connected],
        "phone_ready": any(device.role == "phone" for device in connected),
        "watch_ready": any(device.role == "watch" for device in connected),
    }


def write_state(state: dict[str, object]) -> None:
    STATE_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=STATE_PATH.parent, delete=False
    ) as output:
        json.dump(state, output, separators=(",", ":"))
        output.write("\n")
        temporary = Path(output.name)
    temporary.chmod(0o600)
    temporary.replace(STATE_PATH)


def main() -> None:
    previous_summary = ""
    while True:
        try:
            state = discover()
            write_state(state)
            summary = json.dumps(
                {
                    "phone_ready": state["phone_ready"],
                    "watch_ready": state["watch_ready"],
                    "wireless_endpoints_seen": state["wireless_endpoints_seen"],
                },
                sort_keys=True,
            )
            if summary != previous_summary:
                print(f"Jarvis ADB readiness {summary}", flush=True)
                previous_summary = summary
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            print(f"Jarvis ADB readiness scan failed: {type(error).__name__}", flush=True)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
