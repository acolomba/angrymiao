# Angry Miao firmware CLI

Checks, downloads, and installs firmware for the AM Relic 80 keyboard and its
USB dongle. The `am` command reads each device's current version, asks Angry
Miao for its available release, and uses Nordic serial DFU to install the
vendor's firmware package.

The CLI was tested with both devices on macOS. Each device was successfully
flashed and verified while connected directly to the Mac.

## Features

* **Device discovery:** Identifies the keyboard and dongle by their USB serial
  responses and displays their current firmware versions.
* **Separate check and download steps:** Lets you inspect a release or save its
  package without switching a device into firmware update mode.
* **One device at a time:** Accepts a device ID so the other device can remain
  connected and usable during an update.
* **Verified downloads:** Accepts HTTPS packages from Angry Miao's firmware
  host, checks transfer integrity and the Nordic DFU ZIP, and caches packages
  for later use.
* **DFU recovery:** Retains the validated package and a recovery record if a
  transfer is interrupted. A connected bootloader can be resumed explicitly.

## Requirements

* Python 3.11 or newer and [uv](https://docs.astral.sh/uv/) to run from this
  repository.
* An AM Relic 80 (`AM21`) keyboard or its `AM_DONGLE_1` dongle, connected by
  USB. Other Angry Miao models are not supported.
* Internet access to check for releases and download firmware. A previously
  downloaded package can be reused when the same release is advertised.
* For flashing, Nordic `nrfutil` 6.1.7 or newer with serial DFU support. The
  CLI checks the tool before asking a device to enter DFU mode.

Connect the device being flashed **directly to the computer**, rather than
through a USB hub. Both updates completed this way on the test Mac; mode
switching through its hub was unreliable. Keep the USB connection in place
until the command reports `upgrade verified`. Close AM_Master first so it does
not hold the serial port.

### Nordic tool on Apple Silicon

The CLI uses `nrfutil` from `PATH` if one is installed. Otherwise, it tries to
run version 6.1.7 through `uvx`. The legacy Nordic PyPI package does not have
a native Apple Silicon dependency build, so that automatic fallback may fail
on an Apple Silicon Mac. The following setup uses an x86 Python environment
under Rosetta; it was the configuration used for both successful flashes:

```sh
mkdir -p "$HOME/.local/share/angrymiao" "$HOME/.local/bin"
arch -x86_64 /usr/bin/python3 -m venv "$HOME/.local/share/angrymiao/nrfutil-x86"
arch -x86_64 "$HOME/.local/share/angrymiao/nrfutil-x86/bin/python3" -m pip install 'nrfutil==6.1.7'
cat > "$HOME/.local/bin/nrfutil" <<'SH'
#!/bin/sh
exec /usr/bin/arch -x86_64 "$HOME/.local/share/angrymiao/nrfutil-x86/bin/python3" -m nordicsemi "$@"
SH
chmod +x "$HOME/.local/bin/nrfutil"
export PATH="$HOME/.local/bin:$PATH"
nrfutil version
```

This requires Rosetta and an x86 Python 3.9 or 3.10 at `/usr/bin/python3`.
The last command should report `nrfutil version 6.1.7`. Add
`$HOME/.local/bin` to your shell's `PATH` for future sessions. An existing
compatible `nrfutil` needs no additional setup.

## Installation

From a checkout of this repository:

```sh
uv sync
uv run am list
```

The examples below use `uv run am`. If you installed the package in another
Python environment, use its `am` command instead.

## Usage

### Find and check a device

```sh
uv run am list
uv run am check
```

`list` shows an ID such as `dongle@0-1` or `relic80@1-1` and the version
reported by the device. `check` also contacts Angry Miao and shows whether a
newer release is available. Use a device ID to limit a check:

```sh
uv run am check dongle@0-1
```

The part after `@` identifies the USB location and can change when you move a
cable. Run `am list` again after changing ports, then use the new ID.

### Download without flashing

```sh
uv run am download dongle@0-1
```

This saves and validates the advertised firmware package. The device stays in
normal mode. Omit the ID to download packages for all connected supported
devices.

### Upgrade

Check the current IDs, then update one device at a time:

```sh
uv run am list
uv run am upgrade dongle@0-1
uv run am list
uv run am upgrade relic80@1-1
```

`upgrade` downloads and validates the package before sending the DFU command.
It skips a device whose reported version already matches the advertised
version. After Nordic reports the transfer complete, the CLI waits for the
device to reconnect and report the target version. It prints
`upgrade verified` only after that check.

To reinstall the advertised release even when the versions match, add
`--force`:

```sh
uv run am upgrade --force dongle@0-1
```

An upgrade with no device ID checks all supported connected devices and
updates eligible devices sequentially. Selecting one ID is recommended when
you want to verify the dongle before interrupting the keyboard.

`check`, `download`, and `upgrade` accept `--timeout SECONDS` to change the
network request timeout. It does not change the DFU transfer deadline.

### Recover an interrupted transfer

If an update stops after the device enters DFU mode, leave it connected and
run:

```sh
uv run am list
```

If the output shows `DFU recovery needed`, resume that exact ID:

```sh
uv run am upgrade <id-from-am-list>
```

The CLI checks the bootloader's USB location and the cached package before
resuming. It does not automatically resume a bootloader during an untargeted
upgrade. If the device has already returned to normal mode, use `am list` and
`am check` to inspect it before trying again.

## Troubleshooting

* **No supported devices:** Check the USB cable and run `am list` again. The
  keyboard must be connected by USB, and AM_Master should be closed.
* **No DFU port appears:** Reconnect the device directly to the computer and
  retry using its new ID. Do not repeatedly send mode switch commands through
  a hub that has already failed.
* **Nordic tool error:** Run `nrfutil version`. Version 5.2.0 crashed before
  transfer on the test Mac; the CLI rejects it. Use version 6.1.7 or newer
  with serial DFU support.
* **Download failure:** The device remains in normal mode. Retry when the
  vendor's firmware service is reachable, or use `--timeout` for a slow
  connection.

Firmware packages are cached under
`$XDG_DATA_HOME/angrymiao/firmware` (or
`~/.local/share/angrymiao/firmware`). Version observations and DFU recovery
records are under `$XDG_STATE_HOME/angrymiao` (or
`~/.local/state/angrymiao`).

## Development

The repository was generated from
[`acolomba/blueprint-python`](https://github.com/acolomba/blueprint-python)
and uses its Python packaging, linting, test, and CI setup. Install development
dependencies and run the checks with:

```sh
uv sync --extra dev
uv run --extra dev pytest test/ -q
uv run --extra dev behave
uv run --extra dev pre-commit run --all-files
```

The pre-commit branch policy rejects commits on `main`; run the full suite
from a feature branch.

Unit tests mock the firmware service and USB devices; they do not flash
hardware.

## License

MIT. See [LICENSE](LICENSE).
