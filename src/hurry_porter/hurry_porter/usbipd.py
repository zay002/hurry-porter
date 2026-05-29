from __future__ import annotations

import re
from dataclasses import dataclass

from . import system


@dataclass
class UsbipdDevice:
    bus_id: str
    vid: str
    pid: str
    name: str
    state: str
    section: str = "connected"


BUSID_RE = re.compile(r"^\d+-\d+(?:\.\d+)*$")
VIDPID_RE = re.compile(r"^([0-9a-fA-F]{4}):([0-9a-fA-F]{4})$")


def find_usbipd() -> str | None:
    direct = system.command_path("usbipd.exe")
    if direct:
        return direct

    result = system.powershell(
        "Get-Command usbipd.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source",
        timeout=3.0,
    )
    if result.stdout.strip():
        return "usbipd.exe"
    return None


def list_devices() -> tuple[list[UsbipdDevice], list[str]]:
    exe = find_usbipd()
    if not exe:
        return [], ["usbipd-win is not installed or is not visible from WSL interop"]

    result = system.run_capture([exe, "list"], timeout=8.0)
    if not result.ok:
        detail = (result.stderr or result.stdout).strip()
        return [], [f"usbipd list failed: {detail or result.returncode}"]
    return parse_list(result.stdout), []


def parse_list(text: str) -> list[UsbipdDevice]:
    devices: list[UsbipdDevice] = []
    section = "connected"

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower().rstrip(":")
        if lowered in {"connected", "persisted"}:
            section = lowered
            continue
        if line.startswith("BUSID") or line.startswith("GUID") or line.startswith("-"):
            continue

        parts = re.split(r"\s{2,}", line)
        if len(parts) < 3:
            continue
        if not BUSID_RE.match(parts[0]):
            continue
        match = VIDPID_RE.match(parts[1])
        if not match:
            continue

        if len(parts) >= 4:
            name = parts[2]
            state = " ".join(parts[3:])
        else:
            name = parts[2]
            state = "Unknown"

        devices.append(
            UsbipdDevice(
                bus_id=parts[0],
                vid=match.group(1).lower(),
                pid=match.group(2).lower(),
                name=name,
                state=state,
                section=section,
            )
        )

    return devices


def attach(bus_id: str, dry_run: bool = False) -> system.CommandResult:
    exe = find_usbipd() or "usbipd.exe"
    args = [exe, "attach", "--wsl", "--busid", bus_id]
    if dry_run:
        return system.CommandResult(args, 0, " ".join(args), "")
    return system.run_capture(args, timeout=30.0)


def bind_elevated(bus_id: str, dry_run: bool = False) -> system.CommandResult:
    script = (
        "Start-Process -Verb RunAs -Wait -FilePath usbipd "
        f"-ArgumentList 'bind --busid {bus_id}'"
    )
    if dry_run:
        return system.CommandResult(["powershell.exe", "-Command", script], 0, script, "")
    return system.powershell(script, timeout=120.0)


def bind_command(bus_id: str) -> str:
    return f"Start-Process -Verb RunAs -Wait -FilePath usbipd -ArgumentList 'bind --busid {bus_id}'"
