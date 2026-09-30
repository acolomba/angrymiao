"""command line firmware manager for supported Angry Miao devices."""

from __future__ import annotations

import argparse
import math
import sys

from angrymiao.catalog import FirmwareError, FirmwareService, is_newer
from angrymiao.device import Device, DeviceError, discover
from angrymiao.dfu import resume_upgrade, upgrade_device
from angrymiao.recovery import recoverable
from angrymiao.state import record_available, record_check


def _print_device(device: Device, available: str | None = None) -> None:
    line = f"{device.id:18} {device.name:18} {device.version:27}"
    if available is not None:
        state = (
            "upgrade available" if is_newer(available, device.version) else "up to date"
        )
        line += f" {available:27} {state}"
    print(line)


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number of seconds") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number")
    return seconds


# pylint: disable=too-many-locals,too-many-return-statements,too-many-branches,too-many-statements
def main(argv: list[str] | None = None) -> int:
    """runs the selected device or firmware command."""
    parser = argparse.ArgumentParser(prog="am", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List supported connected devices")
    check_parser = commands.add_parser(
        "check", help="Check connected devices for newer firmware"
    )
    check_parser.add_argument("id", nargs="?", help="Device ID from am list")
    download_parser = commands.add_parser(
        "download", help="Download the latest firmware for connected devices"
    )
    download_parser.add_argument("id", nargs="?", help="Device ID from am list")
    download_parser.add_argument(
        "--timeout",
        type=_positive_seconds,
        metavar="SECONDS",
        help="Network timeout per request in seconds",
    )
    upgrade_parser = commands.add_parser(
        "upgrade", help="Upgrade devices one at a time"
    )
    upgrade_parser.add_argument("id", nargs="?", help="Device ID from am list")
    upgrade_parser.add_argument(
        "--force",
        action="store_true",
        help="Flash even when the current version matches",
    )
    upgrade_parser.add_argument(
        "--timeout",
        type=_positive_seconds,
        metavar="SECONDS",
        help="Network timeout per request in seconds",
    )
    args = parser.parse_args(argv)

    try:
        devices = discover()
        recoveries = recoverable()
        if args.command == "list":
            print(f"{'ID':18} {'DEVICE':18} {'CURRENT FIRMWARE':27}")
            for device in devices:
                _print_device(device)
            for recovery in recoveries:
                device = recovery.device
                print(f"{device.id:18} {device.name:18} {'DFU recovery needed':27}")
            if not devices and not recoveries:
                print("No supported Angry Miao devices connected.")
            return 0

        service = (
            FirmwareService()
            if getattr(args, "timeout", None) is None
            else FirmwareService(timeout=args.timeout)
        )
        if args.command == "check":
            checked = [device for device in devices if args.id in (None, device.id)]
            checked_recoveries = [
                recovery
                for recovery in recoveries
                if args.id in (None, recovery.device.id)
            ]
            if args.id and not checked and not checked_recoveries:
                raise DeviceError(f"No supported connected device has ID {args.id!r}.")
            print(
                f"{'ID':18} {'DEVICE':18} {'CURRENT FIRMWARE':27} {'AVAILABLE FIRMWARE':27} STATUS"
            )
            for recovery in checked_recoveries:
                device = recovery.device
                print(
                    f"{device.id:18} {device.name:18} {'DFU mode':27} "
                    f"{recovery.release.version:27} recovery pending"
                )
            for device in checked:
                release = service.check(device.product_id)
                _print_device(device, release.version)
                record_check(device, release)
            if not checked and not checked_recoveries:
                print("No supported Angry Miao devices connected.")
            return 0

        if args.command == "download":
            selected = [device for device in devices if args.id in (None, device.id)]
            selected_ids = {device.id for device in selected}
            selected.extend(
                recovery.device
                for recovery in recoveries
                if args.id in (None, recovery.device.id)
                and recovery.device.id not in selected_ids
            )
            if args.id and not selected:
                raise DeviceError(f"No supported connected device has ID {args.id!r}.")
            if not selected:
                print("No supported Angry Miao devices connected.")
                return 0
            failures = 0
            for device in selected:
                try:
                    release = service.check(device.product_id)
                    is_newer(release.version, device.version)
                    record_available(device, release)
                    package = service.prepare(release, progress=True)
                    print(f"{device.id}: saved {release.version} to {package}")
                except FirmwareError as exc:
                    failures += 1
                    print(f"am: {device.id}: {exc}", file=sys.stderr)
            return 1 if failures else 0

        selected = [device for device in devices if args.id in (None, device.id)]
        selected_recoveries = [
            recovery for recovery in recoveries if args.id == recovery.device.id
        ]
        if args.id and not selected and not selected_recoveries:
            raise DeviceError(f"No supported connected device has ID {args.id!r}.")
        if not selected and not selected_recoveries:
            if recoveries:
                for recovery in recoveries:
                    print(
                        f"{recovery.device.id}: DFU recovery pending; "
                        f"run 'am upgrade {recovery.device.id}' to resume."
                    )
                return 0
            print("No supported Angry Miao devices connected.")
            return 0
        if args.id is None:
            for recovery in recoveries:
                print(
                    f"{recovery.device.id}: DFU recovery pending; "
                    f"run 'am upgrade {recovery.device.id}' to resume."
                )
        for recovery in selected_recoveries:
            print(f"{recovery.device.id}: resuming DFU", flush=True)
            resume_upgrade(recovery)
            print(f"{recovery.device.id}: upgrade verified", flush=True)
        pending = []
        for device in selected:
            release = service.check(device.product_id)
            upgrade_available = is_newer(release.version, device.version)
            if args.force or upgrade_available:
                pending.append((device, release))
            else:
                print(f"{device.id}: up to date ({device.version})")
        for device, release in pending:
            print(f"{device.id}: preparing firmware", flush=True)
            service.prepare(release, progress=True)
        for device, release in pending:
            print(f"{device.id}: {device.version} -> {release.version}", flush=True)
            upgrade_device(device, release, service)
            print(f"{device.id}: upgrade verified ({release.version})", flush=True)
        return 0
    except (DeviceError, FirmwareError) as exc:
        print(f"am: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "am: Interrupted. Run 'am list' to check for a DFU recovery record.",
            file=sys.stderr,
        )
        return 130


# pylint: enable=too-many-locals,too-many-return-statements,too-many-branches,too-many-statements
if __name__ == "__main__":
    sys.exit(main())
