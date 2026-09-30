"""handoff to Nordic serial DFU after a complete firmware download."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

import serial
from serial.tools import list_ports

from angrymiao.catalog import FirmwareError, FirmwareService, Release
from angrymiao.device import (
    USB_PID,
    USB_VID,
    Device,
    DeviceError,
    _crc8,
    discover,
    probe,
)
from angrymiao.recovery import Recovery, clear_recovery, save_recovery
from angrymiao.state import record_upgraded

DFU_VID = 0x1915
DFU_PID = 0x521F


def _bootloader_ports() -> set[str]:
    return {
        str(port.device)
        for port in list_ports.comports()
        if (port.vid, port.pid) == (DFU_VID, DFU_PID)
        and "DFU" in (port.description or "").upper()
    }


def _wait_for_bootloader(before: set[str], location: str, timeout: float = 45) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ports = {
            str(port.device)
            for port in list_ports.comports()
            if (port.vid, port.pid) == (DFU_VID, DFU_PID)
            and "DFU" in (port.description or "").upper()
            and port.location == location
            and str(port.device) not in before
        }
        if len(ports) == 1:
            return ports.pop()
        if len(ports) > 1:
            raise FirmwareError("Multiple new Nordic bootloader ports appeared.")
        time.sleep(0.5)
    raise FirmwareError(
        "The device did not expose a Nordic DFU serial port within 45 seconds."
    )


def _nrfutil_base() -> list[str]:
    installed = shutil.which("nrfutil")
    if installed:
        return [installed]
    uvx = shutil.which("uvx")
    if uvx:
        return [uvx, "--python", "3.10", "--from", "nrfutil==6.1.7", "nrfutil"]
    raise FirmwareError("Nordic nrfutil or uvx is required for serial DFU.")


def _nrfutil_command(base: list[str], package: Path, port: str) -> list[str]:
    try:
        version = subprocess.run(
            [*base, "version"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if version.returncode:
            raise FirmwareError(
                f"Could not prepare Nordic nrfutil: {version.stderr.strip()}"
            )
        match = re.search(r"nrfutil version (\d+)\.(\d+)\.(\d+)", version.stdout)
        if match and tuple(map(int, match.groups())) < (6, 1, 7):
            raise FirmwareError(
                "Nordic nrfutil 6.1.7 or newer is required for serial DFU."
            )
        old = subprocess.run(
            [*base, "dfu", "serial", "--help"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FirmwareError(f"Could not prepare Nordic nrfutil: {exc}") from exc
    if old.returncode == 0:
        return [
            *base,
            "dfu",
            "serial",
            "--package",
            str(package),
            "--port",
            port,
            "--flow-control",
            "false",
            "--timeout",
            "2",
        ]
    try:
        new = subprocess.run(
            [*base, "nrf5sdk-tools", "dfu", "serial", "--help"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FirmwareError(f"Could not prepare Nordic nrfutil: {exc}") from exc
    if new.returncode == 0:
        return [
            *base,
            "nrf5sdk-tools",
            "dfu",
            "serial",
            "--package",
            str(package),
            "--port",
            port,
        ]
    raise FirmwareError("Installed nrfutil has no serial DFU command.")


def _wait_for_version(device: Device, version: str, timeout: float = 45) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            matches = [
                found
                for found in discover()
                if found.product_id == device.product_id
                and found.location == device.location
            ]
            if len(matches) == 1 and matches[0].version == version:
                return
        except DeviceError:
            pass
        time.sleep(1)
    raise FirmwareError(f"DFU finished, but {device.id} did not report {version}.")


def upgrade_device(device: Device, release: Release, service: FirmwareService) -> None:
    """downloads first, then switches one selected device into Nordic DFU mode."""
    base = _nrfutil_base()
    print("Preparing and validating firmware...", flush=True)
    package = service.prepare(release)
    command = _nrfutil_command(base, package, "__DFU_PORT__")
    current = probe(device.port, device.location)
    if current is None or (
        current.product_id,
        current.version,
        current.location,
    ) != (device.product_id, device.version, device.location):
        raise FirmwareError(
            f"{device.id} changed while preparing firmware; no DFU command was sent."
        )
    if not device.location:
        raise FirmwareError(
            f"Cannot track {device.id} across DFU without a USB location; "
            "no DFU command was sent."
        )
    if not any(
        port.device == device.port
        and port.location == device.location
        and (port.vid, port.pid) == (USB_VID, USB_PID)
        for port in list_ports.comports()
    ):
        raise FirmwareError(
            f"{device.id} moved or disconnected while preparing firmware; "
            "no DFU command was sent."
        )
    before = _bootloader_ports()
    opcode = 7 if device.product_id == "AM_DONGLE_1" else 4
    frame = bytearray(64)
    frame[:2] = bytes((1, opcode))
    frame[-1] = _crc8(frame[:-1])
    save_recovery(device, release, package)
    print("Entering DFU mode...", flush=True)
    try:
        with serial.Serial(device.port, timeout=1, write_timeout=1) as wire:
            wire.write(frame)
            wire.flush()
        bootloader_port = _wait_for_bootloader(before, device.location)
        command[command.index("--port") + 1] = bootloader_port
        _run_dfu(command, device, release)
    except (OSError, serial.SerialException, FirmwareError) as exc:
        raise FirmwareError(
            f"{exc} Verified firmware is cached. If DFU mode remains active, "
            f"run 'am upgrade {device.id}' to resume."
        ) from exc


def resume_upgrade(recovery: Recovery) -> None:
    """resumes a recorded transfer on the matching connected bootloader."""
    if not any(
        port.device == recovery.bootloader_port
        and port.location == recovery.device.location
        and (port.vid, port.pid) == (DFU_VID, DFU_PID)
        and "DFU" in (port.description or "").upper()
        for port in list_ports.comports()
    ):
        raise FirmwareError(
            f"The DFU port for {recovery.device.id} changed; run 'am list' "
            "and retry with its current ID."
        )
    command = _nrfutil_command(
        _nrfutil_base(), recovery.package, recovery.bootloader_port
    )
    try:
        _run_dfu(command, recovery.device, recovery.release)
    except FirmwareError as exc:
        raise FirmwareError(
            f"{exc} Recovery record retained; run 'am upgrade "
            f"{recovery.device.id}' to retry."
        ) from exc


def _run_dfu(command: list[str], device: Device, release: Release) -> None:
    print(
        f"Flashing {device.id} on {command[command.index('--port') + 1]}...", flush=True
    )
    try:
        completed = subprocess.run(command, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FirmwareError(f"Nordic DFU failed: {exc}") from exc
    if completed.returncode:
        raise FirmwareError(f"Nordic DFU exited with status {completed.returncode}.")
    _wait_for_version(device, release.version)
    record_upgraded(device, release.version)
    clear_recovery(device.id)
