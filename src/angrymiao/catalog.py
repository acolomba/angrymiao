"""firmware release lookup and package retrieval."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from zipfile import BadZipFile, ZipFile

import requests

from angrymiao.paths import firmware_root, state_root

API_URL = "https://diy.angrymiao.com/api/firmware/check"
PACKAGE_HOST = "angrymiao-diy.oss-cn-shenzhen.aliyuncs.com"
PASSWORDS = {"AM21": "qaz012", "AM_DONGLE_1": "qaz014"}
MAX_PACKAGE_SIZE = 32 * 1024 * 1024
DOWNLOAD_RETRIES = 2
RETAINED_PACKAGES = 3


class FirmwareError(Exception):
    """release lookup, download, or DFU failed."""


@dataclass(frozen=True)
class Release:
    """identifies a published firmware release."""

    product_id: str
    version: str
    url: str


class ProgressMeter:  # pylint: disable=too-few-public-methods
    """shows a byte count and percentage while a package is transferred."""

    def __init__(self, total: int | None) -> None:
        self.total = total
        self.last_percent = -10
        self.last_time = 0.0
        self.tty = sys.stderr.isatty()
        self.tick = 0

    def update(self, received: int, *, done: bool = False) -> None:
        """reports download progress in bytes and percentage."""

        percent = min(100, received * 100 // self.total) if self.total else None
        now = time.monotonic()
        if not done:
            if self.tty and now - self.last_time < 0.1:
                return
            if (
                not self.tty
                and percent is not None
                and percent < self.last_percent + 10
            ):
                return
            if not self.tty and percent is None and now - self.last_time < 1:
                return
        self.last_time = now
        if percent is None:
            marker = "|/-\\"[self.tick % 4]
            self.tick += 1
            message = (
                f"Downloading: [{marker}{'.' * 19}] {received:,} bytes (size unknown)"
            )
        else:
            filled = percent * 20 // 100
            meter_bar = "#" * filled + "." * (20 - filled)
            message = (
                f"Downloading: [{meter_bar}] {percent:3d}% "
                f"({received:,}/{self.total:,} bytes)"
            )
            self.last_percent = percent
        print(
            f"\r{message}" if self.tty else message,
            end="\n" if done or not self.tty else "",
            file=sys.stderr,
            flush=True,
        )


def _retained_packages(root: Path, product_id: str) -> None:
    """keeps three recent packages per product and any active recovery image."""
    protected = set()
    for record in state_root().glob("recovery-*.json"):
        try:
            name = json.loads(record.read_text(encoding="utf-8"))["package"]
            if Path(name).name == name:
                protected.add(name)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    entries: list[tuple[float, Path, Path]] = []
    for metadata_path in root.glob("*.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            package_path = metadata_path.with_suffix(".zip")
            if (
                isinstance(metadata, dict)
                and metadata.get("product_id") == product_id
                and package_path.exists()
            ):
                entries.append(
                    (
                        float(metadata.get("downloaded_at", 0)),
                        package_path,
                        metadata_path,
                    )
                )
        except (OSError, ValueError, TypeError):
            continue
    entries.sort(reverse=True)
    keep = {package.name for _, package, _ in entries if package.name in protected}
    for _, package_path, _ in entries:
        if len(keep) >= RETAINED_PACKAGES:
            break
        keep.add(package_path.name)
    for _, package_path, metadata_path in entries:
        if package_path.name not in keep:
            try:
                package_path.unlink(missing_ok=True)
                metadata_path.unlink(missing_ok=True)
            except OSError as exc:
                raise FirmwareError(f"Cannot rotate stored firmware: {exc}") from exc


def is_newer(available: str, current: str) -> bool:
    """compares versions after checking their product and hardware families."""
    if available == current:
        return False
    pattern = re.compile(r"^(.*\.N\d+\.R)(\d+(?:\.\d+)+)$", re.IGNORECASE)
    remote = pattern.fullmatch(available)
    local = pattern.fullmatch(current)
    if not remote or not local or remote.group(1).lower() != local.group(1).lower():
        raise FirmwareError(
            f"Cannot safely compare firmware versions {current!r} and {available!r}."
        )
    return tuple(map(int, remote.group(2).split("."))) > tuple(
        map(int, local.group(2).split("."))
    )


class FirmwareService:
    """queries Angry Miao's release API and retrieves its Nordic DFU zip."""

    def __init__(self, timeout: float | None = None) -> None:
        self.session = requests.Session()
        self.timeout = timeout

    def check(self, product_id: str) -> Release:
        """retrieves the latest release for a supported product."""

        if product_id not in PASSWORDS:
            raise FirmwareError(f"No firmware service mapping for {product_id}.")
        try:
            response = self.session.post(
                API_URL,
                data=json.dumps({"key": product_id, "password": PASSWORDS[product_id]}),
                timeout=(5, 10) if self.timeout is None else self.timeout,
            )
            response.raise_for_status()
            record = response.json()
            if record.get("key", "").upper() != product_id:
                raise FirmwareError(f"Unexpected firmware record for {product_id}.")
            version = record["version"]
            url = record["files"][0]["url"]
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname != PACKAGE_HOST:
                raise FirmwareError(
                    "The firmware API returned an unexpected package URL."
                )
            if not isinstance(version, str) or not version:
                raise FirmwareError("The firmware API returned no version.")
            return Release(product_id, version, url)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise FirmwareError(
                f"Invalid firmware response for {product_id}: {exc}"
            ) from exc
        except requests.RequestException as exc:
            raise FirmwareError(
                f"Firmware check failed for {product_id}: {exc}"
            ) from exc

    def prepare(self, release: Release, *, progress: bool = False) -> Path:
        """returns a validated package, retrying bounded network failures."""
        root = firmware_root()
        try:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise FirmwareError(
                f"Cannot create firmware directory {root}: {exc}"
            ) from exc
        key = hashlib.sha256(release.url.encode()).hexdigest()
        package_path = root / f"{key}.zip"
        metadata_path = root / f"{key}.json"
        if package_path.exists() and metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if isinstance(metadata, dict) and all(
                    metadata.get(field) == value
                    for field, value in (
                        ("product_id", release.product_id),
                        ("version", release.version),
                        ("url", release.url),
                        (
                            "sha256",
                            hashlib.sha256(package_path.read_bytes()).hexdigest(),
                        ),
                    )
                ):
                    validate_package(package_path)
                    if progress:
                        print(
                            f"Firmware already saved: [####################] 100% {package_path}",
                            flush=True,
                        )
                    return package_path
            except (OSError, ValueError, FirmwareError):
                pass

        partial = root / f"{key}.partial"
        last_error: Exception | None = None
        for retry in range(DOWNLOAD_RETRIES + 1):
            label = (
                "initial download"
                if retry == 0
                else f"retry {retry}/{DOWNLOAD_RETRIES}"
            )
            try:
                if progress:
                    print(f"{release.product_id}: {label}", flush=True)
                    print(
                        "Downloading: [....................] 0% (connecting)",
                        file=sys.stderr,
                        flush=True,
                    )
                self._download_url(release.url, partial, progress=progress)
                validate_package(partial)
                digest = hashlib.sha256(partial.read_bytes()).hexdigest()
                os.replace(partial, package_path)
                metadata = {
                    "product_id": release.product_id,
                    "version": release.version,
                    "url": release.url,
                    "sha256": digest,
                    "downloaded_at": time.time(),
                }
                staging = root / f"{key}.json.partial"
                staging.write_text(json.dumps(metadata), encoding="utf-8")
                os.replace(staging, metadata_path)
                _retained_packages(root, release.product_id)
                return package_path
            except (requests.RequestException, OSError) as exc:
                last_error = exc
                partial.unlink(missing_ok=True)
                if progress:
                    print(
                        f"{release.product_id}: {label} failed: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                if retry < DOWNLOAD_RETRIES:
                    time.sleep(2**retry)
            except FirmwareError:
                partial.unlink(missing_ok=True)
                raise
        raise FirmwareError(
            f"Firmware download failed after {DOWNLOAD_RETRIES} retries; "
            f"the device remains in normal mode: {last_error}"
        )

    def _download_url(  # pylint: disable=too-many-locals,too-many-branches
        self, url: str, destination: Path, *, progress: bool
    ) -> None:
        """streams a vendor package with size and checksum checks."""

        with self.session.get(
            url,
            stream=True,
            timeout=(5, 15) if self.timeout is None else self.timeout,
        ) as response:
            response.raise_for_status()
            parsed = urlparse(response.url)
            if parsed.scheme != "https" or parsed.hostname != PACKAGE_HOST:
                raise FirmwareError(
                    "The firmware download redirected outside the vendor host."
                )
            expected = response.headers.get("Content-MD5")
            digest = hashlib.md5()  # noqa: S324 - verifies the vendor's object checksum
            try:
                length = int(response.headers.get("Content-Length", "0"))
                total = length if length > 0 else None
            except ValueError:
                total = None
            if total is not None and total > MAX_PACKAGE_SIZE:
                raise FirmwareError("The firmware package exceeds the size limit.")
            meter = ProgressMeter(total) if progress else None
            if meter:
                meter.update(0)
            size = 0
            transferred = False
            try:
                with destination.open("wb") as output:
                    for block in response.iter_content(64 * 1024):
                        if not block:
                            continue
                        size += len(block)
                        if size > MAX_PACKAGE_SIZE:
                            raise FirmwareError(
                                "The firmware package exceeds the size limit."
                            )
                        output.write(block)
                        digest.update(block)
                        if meter:
                            meter.update(size)
                transferred = True
            finally:
                if meter and meter.tty and not transferred:
                    print(file=sys.stderr)
            if meter:
                meter.update(size, done=True)
            if size == 0:
                raise FirmwareError("The firmware package is empty.")
            if total is not None and size != total:
                raise FirmwareError("The firmware download was incomplete.")
            if expected:
                try:
                    matches = digest.digest() == base64.b64decode(
                        expected, validate=True
                    )
                except (binascii.Error, ValueError) as exc:
                    raise FirmwareError(
                        "The firmware object has an invalid checksum."
                    ) from exc
                if not matches:
                    raise FirmwareError(
                        "The firmware download failed its object checksum."
                    )


def validate_package(path: Path) -> None:
    """checks archive integrity and the Nordic DFU manifest."""
    try:
        with ZipFile(path) as package:
            if "manifest.json" not in package.namelist() or package.testzip():
                raise FirmwareError(
                    "The downloaded file is not a valid Nordic DFU ZIP."
                )
            manifest = json.loads(package.read("manifest.json"))
            if not isinstance(manifest.get("manifest"), dict):
                raise FirmwareError("The DFU package has no manifest.")
    except (OSError, BadZipFile, ValueError, KeyError) as exc:
        raise FirmwareError(f"Invalid DFU package: {exc}") from exc
