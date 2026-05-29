from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_capture(args: list[str], timeout: float = 5.0) -> CommandResult:
    try:
        proc = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return CommandResult(args, proc.returncode, proc.stdout, proc.stderr)
    except FileNotFoundError as exc:
        return CommandResult(args, 127, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        return CommandResult(args, 124, exc.stdout or "", exc.stderr or "timed out")


def command_path(name: str) -> str | None:
    return shutil.which(name)


def powershell(script: str, timeout: float = 5.0) -> CommandResult:
    exe = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not exe:
        return CommandResult(["powershell.exe"], 127, "", "PowerShell interop is unavailable")
    return run_capture([exe, "-NoProfile", "-Command", script], timeout=timeout)


def find_windows_command(name: str) -> str | None:
    direct = command_path(name)
    if direct:
        return direct
    result = powershell(
        f"Get-Command {name} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source",
        timeout=3.0,
    )
    path = result.stdout.strip()
    return path or None


def windows_path_to_wsl(path: str | None) -> str | None:
    if not path:
        return None
    cleaned = path.strip().strip('"')
    if len(cleaned) >= 3 and cleaned[1:3] == ":\\":
        drive = cleaned[0].lower()
        rest = cleaned[3:].replace("\\", "/")
        return f"/mnt/{drive}/{rest}"
    return cleaned


def is_wsl2() -> bool:
    release = platform.release().lower()
    return "microsoft" in release and "wsl2" in release


def wsl_version() -> str | None:
    result = run_capture(["wslinfo", "--wsl-version"], timeout=2.0)
    return result.stdout.strip() if result.ok else None


def wsl_networking_mode() -> str | None:
    result = run_capture(["wslinfo", "--networking-mode"], timeout=2.0)
    return result.stdout.strip() if result.ok else None


def ros_distro() -> str | None:
    return os.environ.get("ROS_DISTRO")


def count_glob(pattern: str) -> int:
    return len(list(Path().glob(pattern))) if not pattern.startswith("/") else len(list(Path("/").glob(pattern[1:])))


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
