"""usb serial discovery and read only device identification."""

from __future__ import annotations

from dataclasses import dataclass

import serial
from serial.tools import list_ports

USB_VID = 0x05AC
USB_PID = 0x0256
PRODUCTS = {
    "AM21": ("relic80", "AM Relic 80"),
    "AM_DONGLE_1": ("dongle", "AM Relic 80 dongle"),
}


class DeviceError(Exception):
    """device discovery or communication failed."""


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    product_id: str
    version: str
    port: str
    location: str


def _crc8(payload: bytes) -> int:
    result = 0
    for byte in payload:
        result ^= byte
        for _ in range(8):
            result = ((result << 1) ^ (7 if result & 0x80 else 0)) & 0xFF
    return result


def _query(wire: serial.Serial, opcode: int) -> str:
    frame = bytearray(64)
    frame[:2] = bytes((1, opcode))
    frame[-1] = _crc8(frame[:-1])
    wire.reset_input_buffer()
    wire.write(frame)
    response = bytes(wire.read(64))
    if len(response) != 64 or response[:2] != frame[:2]:
        raise DeviceError("The device did not answer its identity query.")
    if _crc8(response[:-1]) != response[-1]:
        raise DeviceError("The device returned a bad checksum.")
    size = response[2]
    if size > 60:
        raise DeviceError("The device returned an invalid reply length.")
    try:
        return response[3 : 3 + size].rstrip(b"\0").decode("ascii")
    except UnicodeDecodeError as exc:
        raise DeviceError("The device returned a non-text identity.") from exc


def probe(port: str, location: str = "") -> Device | None:
    """returns a supported device found on a vendor usb serial port."""
    try:
        with serial.Serial(port, timeout=1.5, write_timeout=1.5) as wire:
            product_id = _query(wire, 1)
            if product_id not in PRODUCTS:
                return None
            version = _query(wire, 2)
    except (OSError, serial.SerialException) as exc:
        raise DeviceError(f"Cannot query {port}: {exc}") from exc
    kind, name = PRODUCTS[product_id]
    suffix = location or port.rsplit("/", 1)[-1]
    return Device(f"{kind}@{suffix}", name, product_id, version, port, location)


def discover() -> list[Device]:
    """probes only the known Angry Miao usb serial interfaces."""
    result = []
    for port in list_ports.comports():
        if (port.vid, port.pid) == (USB_VID, USB_PID):
            device = probe(port.device, port.location or "")
            if device is not None:
                result.append(device)
    return sorted(result, key=lambda item: item.id)
