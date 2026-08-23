import subprocess

from tools import adb_readiness
from tools.adb_readiness import parse_devices, parse_mdns, parse_mdns_services


def test_mdns_parser_deduplicates_rotating_connect_endpoints() -> None:
    output = """\
phone _adb-tls-connect._tcp 192.168.1.149:40353
phone _adb-tls-connect._tcp 192.168.1.149:40353
watch _adb-tls-pairing._tcp 192.168.1.31:38888
watch _adb-tls-connect._tcp 192.168.1.31:37267
"""
    assert parse_mdns(output) == ["192.168.1.149:40353", "192.168.1.31:37267"]
    assert parse_mdns_services(output) == [
        ("phone", "192.168.1.149:40353"),
        ("watch", "192.168.1.31:37267"),
    ]


def test_device_parser_only_returns_explicit_jarvis_models() -> None:
    output = """\
List of devices attached
phone device product:t2s model:SM_G996B device:t2s
watch device product:fresh model:SM_L315F device:fresh
emulator-5554 device product:sdk model:sdk_gphone64 device:emu
offline offline model:SM_G996B
"""
    devices = parse_devices(output)
    assert [(item.serial, item.role) for item in devices] == [
        ("phone", "phone"),
        ("watch", "watch"),
    ]


def test_stale_endpoint_does_not_block_connected_device(monkeypatch) -> None:
    calls = []

    def fake_adb(*arguments: str, timeout: int = 8):
        calls.append(arguments)
        if arguments == ("mdns", "services"):
            return subprocess.CompletedProcess(arguments, 0, "watch _adb-tls-connect._tcp 1.2.3.4:9\n", "")
        return subprocess.CompletedProcess(
            arguments, 0,
            "List of devices attached\nphone device product:t2s model:SM_G996B device:t2s\n",
            "",
        )

    monkeypatch.setattr(adb_readiness, "run_adb", fake_adb)
    state = adb_readiness.discover()
    assert state["phone_ready"] is True
    assert state["watch_ready"] is False
    assert ("connect", "1.2.3.4:9") in calls
