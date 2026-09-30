"""persist observed and advertised firmware versions by connected device id."""

from __future__ import annotations

import json
import os
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from angrymiao.catalog import FirmwareError, Release
from angrymiao.device import Device
from angrymiao.paths import state_root


def _state_path() -> Path:
    return state_root() / "versions.json"


def read_versions() -> dict[str, dict[str, Any]]:
    """returns saved version observations keyed by the stable cli id."""
    path = _state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        devices = data["devices"]
        if not isinstance(devices, dict) or any(
            not isinstance(key, str) or not isinstance(value, dict)
            for key, value in devices.items()
        ):
            raise ValueError("invalid device records")
        return devices
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FirmwareError(f"Cannot read saved versions at {path}: {exc}") from exc


def _write_versions(records: dict[str, dict[str, Any]]) -> None:
    root = state_root()
    path = _state_path()
    staging = path.with_suffix(".partial")
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        staging.write_text(
            json.dumps({"devices": records}, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(staging, path)
    except OSError as exc:
        with suppress(OSError):
            staging.unlink(missing_ok=True)
        raise FirmwareError(f"Cannot save firmware versions at {path}: {exc}") from exc


def record_check(device: Device, release: Release) -> None:
    """saves both the device's reported and the vendor's advertised version."""
    if device.product_id != release.product_id:
        raise FirmwareError(f"Firmware record does not match {device.id}.")
    records = read_versions()
    item = records.get(device.id, {})
    item.update(
        {
            "product_id": device.product_id,
            "name": device.name,
            "device_version": device.version,
            "device_at": datetime.now(UTC).isoformat(),
            "available_version": release.version,
            "available_url": release.url,
            "available_at": datetime.now(UTC).isoformat(),
        }
    )
    records[device.id] = item
    _write_versions(records)


def record_available(device: Device, release: Release) -> None:
    """saves a release lookup without changing the last observed version."""
    if device.product_id != release.product_id:
        raise FirmwareError(f"Firmware record does not match {device.id}.")
    records = read_versions()
    item = records.get(device.id, {})
    item.update(
        {
            "product_id": device.product_id,
            "name": device.name,
            "available_version": release.version,
            "available_url": release.url,
            "available_at": datetime.now(UTC).isoformat(),
        }
    )
    records[device.id] = item
    _write_versions(records)


def record_upgraded(device: Device, version: str) -> None:
    """records a firmware version after DFU and a matching device reply."""
    records = read_versions()
    item = records.get(device.id, {})
    item.update(
        {
            "product_id": device.product_id,
            "name": device.name,
            "device_version": version,
            "device_at": datetime.now(UTC).isoformat(),
        }
    )
    records[device.id] = item
    _write_versions(records)
