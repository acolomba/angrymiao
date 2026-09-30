"""persists enough information to resume a serial DFU after interruption."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path

from serial.tools import list_ports

from angrymiao.catalog import (
    FirmwareError,
    Release,
    validate_package,
)
from angrymiao.device import Device
from angrymiao.paths import firmware_root, state_root

DFU_VID = 0x1915
DFU_PID = 0x521F


@dataclass(frozen=True)
class Recovery:
    """retains the package and port for interrupted dfu."""

    device: Device
    release: Release
    package: Path
    bootloader_port: str


def _record_path(identifier: str) -> Path:
    name = hashlib.sha256(identifier.encode()).hexdigest()
    return state_root() / f"recovery-{name}.json"


def save_recovery(device: Device, release: Release, package: Path) -> None:
    """records a verified package before asking a device to enter DFU mode."""
    root = state_root()
    destination = _record_path(device.id)
    staging = destination.with_suffix(".partial")
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        record = {
            "device": asdict(device),
            "release": asdict(release),
            "package": package.name,
            "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        }
        staging.write_text(json.dumps(record), encoding="utf-8")
        os.replace(staging, destination)
    except OSError as exc:
        with suppress(OSError):
            staging.unlink(missing_ok=True)
        raise FirmwareError(f"Cannot save DFU recovery record: {exc}") from exc


def clear_recovery(identifier: str) -> None:
    """removes the record only after the target version is observed."""
    try:
        _record_path(identifier).unlink(missing_ok=True)
    except OSError as exc:
        raise FirmwareError(f"Cannot clear DFU recovery record: {exc}") from exc


def recoverable() -> list[Recovery]:
    """matches saved upgrades to connected Nordic bootloaders by usb location."""
    root = state_root()
    if not root.exists():
        return []
    bootloaders = [
        port
        for port in list_ports.comports()
        if (port.vid, port.pid) == (DFU_VID, DFU_PID)
        and "DFU" in (port.description or "").upper()
    ]
    result = []
    for record_path in root.glob("recovery-*.json"):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            device = Device(**record["device"])
            release = Release(**record["release"])
            if not device.location:
                continue
            matches = [port for port in bootloaders if port.location == device.location]
            if not matches:
                continue
            if len(matches) != 1:
                raise FirmwareError(f"Multiple DFU ports match {device.id}.")
            package_name = record["package"]
            if Path(package_name).name != package_name:
                raise FirmwareError("Recovery record has an invalid package path.")
            package = firmware_root() / package_name
            if hashlib.sha256(package.read_bytes()).hexdigest() != record["sha256"]:
                raise FirmwareError(
                    f"Cached firmware for {device.id} failed validation."
                )
            validate_package(package)
            result.append(Recovery(device, release, package, matches[0].device))
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise FirmwareError(
                f"Invalid recovery record {record_path}: {exc}"
            ) from exc
    return sorted(result, key=lambda item: item.device.id)
