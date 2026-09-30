"""checks upgrade selection without flashing hardware."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
import requests
import serial
from serial.tools import list_ports

from angrymiao import __main__ as cli
from angrymiao import catalog, dfu, recovery, state
from angrymiao.catalog import FirmwareError, FirmwareService, Release, is_newer
from angrymiao.device import USB_PID, USB_VID, Device, _crc8
from angrymiao.paths import firmware_root, state_root


@pytest.fixture(autouse=True)
def isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def _devices() -> list[Device]:
    return [
        Device(
            "dongle@0-1",
            "AM Relic 80 dongle",
            "AM_DONGLE_1",
            "AM_DONGL.N20.R2.01.05",
            "/dev/cu.dongle",
            "0-1",
        ),
        Device(
            "relic80@2-1",
            "AM Relic 80",
            "AM21",
            "AM_Relic.N40.R1.01.12",
            "/dev/cu.keyboard",
            "2-1",
        ),
    ]


class FakeService:
    def check(self, product_id: str) -> Release:
        versions = {
            "AM_DONGLE_1": "AM_DONGL.N20.R2.01.05",
            "AM21": "AM_Relic.N40.R1.01.12",
        }
        return Release(
            product_id, versions[product_id], "https://example.test/test.zip"
        )

    def prepare(self, _release: Release, *, progress: bool = False) -> Path:
        del progress
        return Path("/tmp/firmware.zip")


def test_crc_matches_observed_keyboard_reply() -> None:
    packet = bytes((1, 1, 4)) + b"AM21" + bytes(56) + bytes((0x5B,))
    assert len(packet) == 64
    assert _crc8(packet[:-1]) == packet[-1]


def test_version_comparison_rejects_other_hardware() -> None:
    assert is_newer("AM_Relic.N40.R1.01.13", "AM_Relic.N40.R1.01.12")
    assert not is_newer("AM_Relic.N40.R1.01.12", "AM_Relic.N40.R1.01.12")
    with pytest.raises(FirmwareError):
        is_newer("AM_DONGL.N20.R2.01.06", "AM_Relic.N40.R1.01.12")


def test_relative_xdg_paths_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", "relative/state")
    monkeypatch.setenv("XDG_DATA_HOME", "relative/data")
    assert state_root() == Path.home() / ".local/state/angrymiao"
    assert firmware_root() == Path.home() / ".local/share/angrymiao/firmware"


def test_saved_versions_change_only_for_the_requested_operation() -> None:
    device = _devices()[1]
    current = Release(
        device.product_id, device.version, "https://example.test/current.zip"
    )
    advertised = Release(
        device.product_id,
        "AM_Relic.N40.R1.01.14",
        "https://example.test/advertised.zip",
    )
    state.record_check(device, current)
    state.record_available(device, advertised)
    after_download = state.read_versions()[device.id]
    assert after_download["device_version"] == device.version
    assert after_download["available_version"] == advertised.version

    state.record_upgraded(device, "AM_Relic.N40.R1.01.13")
    after_upgrade = state.read_versions()[device.id]
    assert after_upgrade["device_version"] == "AM_Relic.N40.R1.01.13"
    assert after_upgrade["available_version"] == advertised.version


def test_unwritable_xdg_root_reports_firmware_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("file", encoding="utf-8")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocked))
    device = _devices()[0]
    release = FakeService().check(device.product_id)
    with pytest.raises(FirmwareError, match="Cannot save firmware versions"):
        state.record_check(device, release)


def test_upgrade_skips_current_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "discover", _devices)
    monkeypatch.setattr(cli, "recoverable", lambda: [])
    monkeypatch.setattr(cli, "FirmwareService", FakeService)

    def unexpected_upgrade(*_args: object) -> None:
        raise AssertionError("current device was flashed")

    monkeypatch.setattr(cli, "upgrade_device", unexpected_upgrade)
    assert cli.main(["upgrade"]) == 0


def test_check_reports_all_or_selected_device(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "discover", _devices)
    monkeypatch.setattr(cli, "recoverable", lambda: [])
    monkeypatch.setattr(cli, "FirmwareService", FakeService)
    assert cli.main(["check"]) == 0
    all_output = capsys.readouterr().out
    assert "dongle@0-1" in all_output
    assert "relic80@2-1" in all_output
    assert all_output.count("up to date") == 2
    records = state.read_versions()
    assert records["dongle@0-1"]["device_version"] == "AM_DONGL.N20.R2.01.05"
    assert records["dongle@0-1"]["available_version"] == "AM_DONGL.N20.R2.01.05"

    assert cli.main(["check", "dongle@0-1"]) == 0
    selected_output = capsys.readouterr().out
    assert "dongle@0-1" in selected_output
    assert "relic80@2-1" not in selected_output


def test_download_targets_one_device_and_saves_available_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    downloaded: list[str] = []
    monkeypatch.setattr(cli, "discover", _devices)
    monkeypatch.setattr(cli, "recoverable", lambda: [])

    class Service(FakeService):
        def prepare(self, release: Release, *, progress: bool = False) -> Path:
            assert progress
            downloaded.append(release.product_id)
            return firmware_root() / "package.zip"

    monkeypatch.setattr(cli, "FirmwareService", Service)
    assert cli.main(["download", "dongle@0-1"]) == 0
    assert downloaded == ["AM_DONGLE_1"]
    assert "saved" in capsys.readouterr().out
    records = state.read_versions()
    assert records["dongle@0-1"]["available_version"] == "AM_DONGL.N20.R2.01.05"
    assert "device_version" not in records["dongle@0-1"]
    downloaded.clear()
    assert cli.main(["download"]) == 0
    assert downloaded == ["AM_DONGLE_1", "AM21"]


def test_download_continues_after_one_device_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downloaded: list[str] = []
    monkeypatch.setattr(cli, "discover", _devices)
    monkeypatch.setattr(cli, "recoverable", lambda: [])

    class Service(FakeService):
        def prepare(self, release: Release, *, progress: bool = False) -> Path:
            assert progress
            downloaded.append(release.product_id)
            if release.product_id == "AM_DONGLE_1":
                raise FirmwareError("dongle package unavailable")
            return firmware_root() / "keyboard.zip"

    monkeypatch.setattr(cli, "FirmwareService", Service)
    assert cli.main(["download"]) == 1
    assert downloaded == ["AM_DONGLE_1", "AM21"]


def test_download_accepts_a_recovery_id(monkeypatch: pytest.MonkeyPatch) -> None:
    device = _devices()[1]
    pending = recovery.Recovery(
        device,
        FakeService().check(device.product_id),
        firmware_root() / "old.zip",
        "/dev/cu.dfu",
    )
    prepared: list[str] = []
    monkeypatch.setattr(cli, "discover", lambda: [])
    monkeypatch.setattr(cli, "recoverable", lambda: [pending])

    class Service(FakeService):
        def prepare(self, release: Release, *, progress: bool = False) -> Path:
            assert progress
            prepared.append(release.product_id)
            return firmware_root() / "latest.zip"

    monkeypatch.setattr(cli, "FirmwareService", Service)
    assert cli.main(["download", device.id]) == 0
    assert prepared == [device.product_id]


def test_timeout_option_reaches_download_and_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeouts: list[float] = []
    monkeypatch.setattr(cli, "discover", _devices)
    monkeypatch.setattr(cli, "recoverable", lambda: [])

    def service_factory(*, timeout: float) -> FakeService:
        timeouts.append(timeout)
        return FakeService()

    monkeypatch.setattr(cli, "FirmwareService", service_factory)
    monkeypatch.setattr(cli, "upgrade_device", lambda *_args: None)
    assert cli.main(["download", "dongle@0-1", "--timeout", "30"]) == 0
    assert cli.main(["upgrade", "--force", "--timeout", "45", "dongle@0-1"]) == 0
    assert timeouts == [30.0, 45.0]
    with pytest.raises(SystemExit):
        cli.main(["download", "--timeout", "0"])


def test_firmware_service_passes_timeout_to_both_network_requests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = _cached_package(tmp_path).read_bytes()
    url = f"https://{catalog.PACKAGE_HOST}/firmware/test.zip"
    seen: list[tuple[str, float]] = []

    class Response:
        headers = {"Content-Length": str(len(payload))}

        def __init__(self) -> None:
            self.url = url

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "key": "AM21",
                "version": "AM_Relic.N40.R1.01.12",
                "files": [{"url": url}],
            }

        def iter_content(self, _size: int) -> list[bytes]:
            return [payload]

    def post(*_args: object, **kwargs: object) -> Response:
        seen.append(("post", kwargs["timeout"]))  # type: ignore[arg-type]
        return Response()

    def get(*_args: object, **kwargs: object) -> Response:
        seen.append(("get", kwargs["timeout"]))  # type: ignore[arg-type]
        return Response()

    service = FirmwareService(timeout=12)
    monkeypatch.setattr(service.session, "post", post)
    monkeypatch.setattr(service.session, "get", get)
    release = service.check("AM21")
    service.prepare(release)
    assert seen == [("post", 12), ("get", 12)]


def test_force_targets_only_selected_device(monkeypatch: pytest.MonkeyPatch) -> None:
    flashed: list[str] = []
    monkeypatch.setattr(cli, "discover", _devices)
    monkeypatch.setattr(cli, "recoverable", lambda: [])
    monkeypatch.setattr(cli, "FirmwareService", FakeService)

    def record_upgrade(device: Device, _release: Release, _service: object) -> None:
        flashed.append(device.id)

    monkeypatch.setattr(cli, "upgrade_device", record_upgrade)
    assert cli.main(["upgrade", "--force", "dongle@0-1"]) == 0
    assert flashed == ["dongle@0-1"]
    assert state.read_versions() == {}


def test_multi_device_upgrade_prepares_all_before_flashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared: list[str] = []
    flashed: list[str] = []
    monkeypatch.setattr(cli, "discover", _devices)
    monkeypatch.setattr(cli, "recoverable", lambda: [])

    class Service(FakeService):
        def prepare(self, release: Release, *, progress: bool = False) -> Path:
            del progress
            prepared.append(release.product_id)
            if release.product_id == "AM21":
                raise FirmwareError("keyboard package unavailable")
            return Path("/tmp/firmware.zip")

    monkeypatch.setattr(cli, "FirmwareService", Service)
    monkeypatch.setattr(
        cli,
        "upgrade_device",
        lambda device, *_args: flashed.append(device.id),
    )
    assert cli.main(["upgrade", "--force"]) == 1
    assert prepared == ["AM_DONGLE_1", "AM21"]
    assert flashed == []


def test_download_retries_without_entering_dfu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    attempts = []

    def timeout(*_args: object, **_kwargs: object) -> None:
        attempts.append(True)
        raise requests.Timeout("package host stalled")

    service = FirmwareService()
    monkeypatch.setattr(service.session, "get", timeout)
    monkeypatch.setattr(dfu, "_nrfutil_base", lambda: ["nrfutil"])

    def unexpected_serial(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("device was switched to DFU")

    monkeypatch.setattr(serial, "Serial", unexpected_serial)
    release = Release(
        "AM21",
        "AM_Relic.N40.R1.01.12",
        "https://angrymiao-diy.oss-cn-shenzhen.aliyuncs.com/firmware/test.zip",
    )
    with pytest.raises(FirmwareError, match="normal mode"):
        dfu.upgrade_device(_devices()[1], release, service)
    assert len(attempts) == 3
    assert not list(state_root().glob("recovery-*.json"))


def test_download_shows_bar_and_each_retry_error_before_any_bytes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "discover", _devices)
    monkeypatch.setattr(cli, "recoverable", lambda: [])
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    class Service(FirmwareService):
        def check(self, product_id: str) -> Release:
            return FakeService().check(product_id)

    service = Service()

    def timeout(*_args: object, **_kwargs: object) -> None:
        raise requests.Timeout("package host stalled")

    monkeypatch.setattr(service.session, "get", timeout)
    monkeypatch.setattr(cli, "FirmwareService", lambda: service)
    assert cli.main(["download", "dongle@0-1"]) == 1
    output = capsys.readouterr()
    assert "initial download" in output.out
    assert "retry 1/2" in output.out
    assert "retry 2/2" in output.out
    assert output.err.count("[....................] 0% (connecting)") == 3
    assert "initial download failed: package host stalled" in output.err
    assert "retry 1/2 failed: package host stalled" in output.err
    assert "retry 2/2 failed: package host stalled" in output.err
    assert "Firmware download failed after 2 retries" in output.err


def _cached_package(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    package = tmp_path / "firmware.zip"
    with ZipFile(package, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"manifest": {"application": {}}}))
    return package


def test_verified_package_is_reused_during_network_outage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package_bytes = _cached_package(tmp_path).read_bytes()
    release = Release(
        "AM21",
        "AM_Relic.N40.R1.01.12",
        "https://angrymiao-diy.oss-cn-shenzhen.aliyuncs.com/firmware/test.zip",
    )

    class Response:
        url = release.url
        headers: dict[str, str] = {}

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, _size: int) -> list[bytes]:
            return [package_bytes]

    service = FirmwareService()
    monkeypatch.setattr(service.session, "get", lambda *_args, **_kwargs: Response())
    cached = service.prepare(release)
    assert cached.read_bytes() == package_bytes

    def unexpected_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("cached package triggered a network request")

    monkeypatch.setattr(service.session, "get", unexpected_network)
    assert service.prepare(release) == cached


def test_progress_and_three_recent_packages_per_product(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package_bytes = _cached_package(tmp_path).read_bytes()

    class Response:
        def __init__(self, url: str) -> None:
            self.url = url
            self.headers = {"Content-Length": str(len(package_bytes))}

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, _size: int) -> list[bytes]:
            midpoint = len(package_bytes) // 2
            return [package_bytes[:midpoint], package_bytes[midpoint:]]

    service = FirmwareService()
    monkeypatch.setattr(service.session, "get", lambda url, **_kwargs: Response(url))
    releases = [
        Release(
            "AM21",
            f"AM_Relic.N40.R1.01.{version:02d}",
            f"https://{catalog.PACKAGE_HOST}/firmware/{version}.zip",
        )
        for version in range(12, 16)
    ]
    paths = [service.prepare(release, progress=True) for release in releases]
    assert "100%" in capsys.readouterr().err
    assert not paths[0].exists()
    assert all(path.exists() for path in paths[1:])
    assert len(list(firmware_root().glob("*.zip"))) == 3

    recovery.save_recovery(_devices()[1], releases[1], paths[1])
    next_release = Release(
        "AM21",
        "AM_Relic.N40.R1.01.16",
        f"https://{catalog.PACKAGE_HOST}/firmware/16.zip",
    )
    newest = service.prepare(next_release)
    assert newest.exists()
    assert paths[1].exists()
    assert not paths[2].exists()
    assert len(list(firmware_root().glob("*.zip"))) == 3


def test_interrupted_dfu_can_be_found_and_resumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = _devices()[1]
    release = Release(device.product_id, device.version, "https://example.test/fw.zip")
    package = _cached_package(firmware_root())
    recovery.save_recovery(device, release, package)
    bootloader = SimpleNamespace(
        vid=recovery.DFU_VID,
        pid=recovery.DFU_PID,
        description="nRF52 DFU",
        location=device.location,
        device="/dev/cu.dfu",
    )
    monkeypatch.setattr(list_ports, "comports", lambda: [bootloader])
    matches = recovery.recoverable()
    assert len(matches) == 1
    assert matches[0].device.id == device.id
    assert matches[0].bootloader_port == "/dev/cu.dfu"

    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=1)
    )
    command = ["nrfutil", "dfu", "serial", "--port", "/dev/cu.dfu"]
    with pytest.raises(FirmwareError, match="status 1"):
        dfu._run_dfu(command, device, release)
    assert recovery.recoverable()
    assert state.read_versions() == {}

    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0)
    )
    monkeypatch.setattr(dfu, "_wait_for_version", lambda *_args: None)
    dfu._run_dfu(command, device, release)
    assert not recovery.recoverable()
    assert state.read_versions()[device.id]["device_version"] == release.version
    assert "available_version" not in state.read_versions()[device.id]


def test_old_nrfutil_is_rejected_before_dfu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="nrfutil version 5.2.0", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(FirmwareError, match="6.1.7 or newer"):
        dfu._nrfutil_command(["nrfutil"], Path("firmware.zip"), "__DFU_PORT__")
    assert commands == [["nrfutil", "version"]]


def test_changed_identity_aborts_before_mode_switch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package = _cached_package(tmp_path)
    device = _devices()[1]
    release = Release(device.product_id, device.version, "https://example.test/fw.zip")

    class Service:
        def prepare(self, _release: Release) -> Path:
            return package

    monkeypatch.setattr(dfu, "_nrfutil_base", lambda: ["nrfutil"])
    monkeypatch.setattr(
        dfu,
        "_nrfutil_command",
        lambda *_args: ["nrfutil", "--port", "__DFU_PORT__"],
    )
    monkeypatch.setattr(dfu, "probe", lambda *_args: _devices()[0])
    with pytest.raises(FirmwareError, match="changed while preparing"):
        dfu.upgrade_device(device, release, Service())  # type: ignore[arg-type]
    assert not list(state_root().glob("recovery-*.json"))


def test_dfu_wait_ignores_other_usb_location(monkeypatch: pytest.MonkeyPatch) -> None:
    other = SimpleNamespace(
        vid=dfu.DFU_VID,
        pid=dfu.DFU_PID,
        description="nRF52 DFU",
        location="9-9",
        device="/dev/cu.other-dfu",
    )
    expected = SimpleNamespace(
        vid=dfu.DFU_VID,
        pid=dfu.DFU_PID,
        description="nRF52 DFU",
        location="2-1",
        device="/dev/cu.our-dfu",
    )
    monkeypatch.setattr(list_ports, "comports", lambda: [other])
    with pytest.raises(FirmwareError, match="did not expose"):
        dfu._wait_for_bootloader(set(), "2-1", timeout=0.01)
    monkeypatch.setattr(list_ports, "comports", lambda: [other, expected])
    assert dfu._wait_for_bootloader(set(), "2-1", timeout=0.01) == expected.device


def test_moved_device_aborts_before_mode_switch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    device = _devices()[1]
    release = Release(device.product_id, device.version, "https://example.test/fw.zip")

    class Service:
        def prepare(self, _release: Release) -> Path:
            return _cached_package(tmp_path)

    monkeypatch.setattr(dfu, "_nrfutil_base", lambda: ["nrfutil"])
    monkeypatch.setattr(
        dfu,
        "_nrfutil_command",
        lambda *_args: ["nrfutil", "--port", "__DFU_PORT__"],
    )
    monkeypatch.setattr(dfu, "probe", lambda *_args: device)
    moved = SimpleNamespace(
        vid=USB_VID,
        pid=USB_PID,
        location="9-9",
        device=device.port,
    )
    monkeypatch.setattr(list_ports, "comports", lambda: [moved])
    monkeypatch.setattr(
        serial,
        "Serial",
        lambda *_args, **_kwargs: pytest.fail("DFU command sent to moved device"),
    )
    with pytest.raises(FirmwareError, match="moved or disconnected"):
        dfu.upgrade_device(device, release, Service())  # type: ignore[arg-type]


def test_resume_rechecks_dfu_port_location(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    device = _devices()[1]
    release = Release(device.product_id, device.version, "https://example.test/fw.zip")
    pending = recovery.Recovery(device, release, tmp_path / "fw.zip", "/dev/cu.dfu")
    monkeypatch.setattr(list_ports, "comports", lambda: [])
    with pytest.raises(FirmwareError, match="DFU port.*changed"):
        dfu.resume_upgrade(pending)


def test_dfu_recovery_requires_specific_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    device = _devices()[1]
    release = Release(device.product_id, device.version, "https://example.test/fw.zip")
    pending = recovery.Recovery(
        device, release, tmp_path / "firmware.zip", "/dev/cu.dfu"
    )
    resumed: list[str] = []
    monkeypatch.setattr(cli, "discover", lambda: [])
    monkeypatch.setattr(cli, "recoverable", lambda: [pending])
    monkeypatch.setattr(
        cli, "resume_upgrade", lambda item: resumed.append(item.device.id)
    )
    assert cli.main(["upgrade"]) == 0
    assert resumed == []
    assert cli.main(["upgrade", device.id]) == 0
    assert resumed == [device.id]
