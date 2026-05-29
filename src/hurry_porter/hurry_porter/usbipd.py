from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from . import system


@dataclass
class UsbipdDevice:
    bus_id: str
    vid: str
    pid: str
    name: str
    state: str
    section: str = "connected"


@dataclass
class UsbipdServiceStatus:
    installed: bool
    running: bool
    state: str
    exit_code: int | None = None
    detail: str | None = None


BUSID_RE = re.compile(r"^\d+-\d+(?:\.\d+)*$")
VIDPID_RE = re.compile(r"^([0-9a-fA-F]{4}):([0-9a-fA-F]{4})$")
COMMON_USBIPD_PATHS = [
    "/mnt/c/Program Files/usbipd-win/usbipd.exe",
    "/mnt/c/Program Files (x86)/usbipd-win/usbipd.exe",
]
COMMON_USBIPD_WINDOWS_PATHS = [
    r"C:\Program Files\usbipd-win\usbipd.exe",
    r"C:\Program Files (x86)\usbipd-win\usbipd.exe",
]


def find_usbipd() -> str | None:
    direct = system.command_path("usbipd.exe")
    if direct:
        return direct

    for candidate in COMMON_USBIPD_PATHS:
        if Path(candidate).exists():
            return candidate

    result = system.powershell(
        "Get-Command usbipd.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source",
        timeout=3.0,
    )
    converted = system.windows_path_to_wsl(result.stdout.strip())
    if converted and Path(converted).exists():
        return converted
    return None


def find_usbipd_windows_path() -> str | None:
    result = system.powershell(
        "Get-Command usbipd.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source",
        timeout=3.0,
    )
    if result.stdout.strip():
        return result.stdout.strip()

    for candidate in COMMON_USBIPD_WINDOWS_PATHS:
        check = system.powershell(
            f"if (Test-Path '{candidate}') {{ Write-Output '{candidate}' }}",
            timeout=3.0,
        )
        if check.stdout.strip():
            return candidate
    return None


def list_devices() -> tuple[list[UsbipdDevice], list[str]]:
    exe = find_usbipd()
    if not exe:
        return [], ["usbipd-win is not installed or is not visible from WSL interop"]

    result = system.run_capture([exe, "list"], timeout=8.0)
    if not result.ok:
        detail = (result.stderr or result.stdout).strip()
        return [], [f"usbipd list failed: {detail or result.returncode}"]
    return parse_list(result.stdout), warning_lines(result.stderr)


def service_status() -> UsbipdServiceStatus:
    result = system.powershell(
        "Get-CimInstance Win32_Service -Filter \"Name='usbipd'\" "
        "| Select-Object Name,State,StartMode,ExitCode,ServiceSpecificExitCode "
        "| ConvertTo-Json -Compress",
        timeout=3.0,
    )
    if not result.ok:
        return UsbipdServiceStatus(
            installed=False,
            running=False,
            state="unknown",
            detail=(result.stderr or result.stdout).strip() or "unable to query Windows service state",
        )

    raw = result.stdout.strip()
    if not raw:
        return UsbipdServiceStatus(
            installed=False,
            running=False,
            state="missing",
            detail="Windows service `usbipd` is not installed",
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return UsbipdServiceStatus(
            installed=True,
            running=False,
            state="unknown",
            detail=raw,
        )

    state = str(data.get("State") or "unknown")
    exit_code = data.get("ExitCode")
    return UsbipdServiceStatus(
        installed=True,
        running=state.lower() == "running",
        state=state,
        exit_code=int(exit_code) if isinstance(exit_code, int) else None,
        detail=f"ExitCode={exit_code}" if exit_code not in {None, 0} else None,
    )


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


def warning_lines(text: str) -> list[str]:
    warnings: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("usbipd: warning"):
            warnings.append(stripped)
    return warnings


def attach(bus_id: str, dry_run: bool = False) -> system.CommandResult:
    exe = find_usbipd() or "usbipd.exe"
    args = [exe, "attach", "--wsl", "--busid", bus_id]
    if dry_run:
        return system.CommandResult(args, 0, " ".join(args), "")
    return system.run_capture(args, timeout=30.0)


def bind_elevated(bus_id: str, dry_run: bool = False) -> system.CommandResult:
    script = bind_command(bus_id)
    if dry_run:
        return system.CommandResult(["powershell.exe", "-Command", script], 0, script, "")
    return system.powershell(script, timeout=120.0)


def bind_command(bus_id: str) -> str:
    return format_bind_command(bus_id, find_usbipd_windows_path() or "usbipd.exe")


def format_bind_command(bus_id: str, executable: str) -> str:
    return (
        "Start-Process -Verb RunAs -Wait "
        f"-FilePath '{executable}' "
        f"-ArgumentList 'bind --busid {bus_id}'"
    )
