from __future__ import annotations

from dataclasses import dataclass

from . import system
from .usbipd import find_usbipd


USBIPD_WINGET_ID = "dorssel.usbipd-win"


@dataclass
class SetupResult:
    component: str
    installed: bool
    command: str
    ran: bool
    ok: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    hint: str | None = None


def usbipd_install_command() -> str:
    return f"winget install --interactive --exact {USBIPD_WINGET_ID}"


def setup_usbipd(run: bool = False) -> SetupResult:
    existing = find_usbipd()
    command = usbipd_install_command()
    if existing:
        return SetupResult(
            component="usbipd-win",
            installed=True,
            command=command,
            ran=False,
            ok=True,
            stdout=f"usbipd-win is already available: {existing}",
        )

    winget = system.find_windows_command("winget.exe")
    if not winget:
        return SetupResult(
            component="usbipd-win",
            installed=False,
            command=command,
            ran=False,
            ok=False,
            hint="Install App Installer / winget from Microsoft Store, then rerun this command.",
        )

    if not run:
        return SetupResult(
            component="usbipd-win",
            installed=False,
            command=command,
            ran=False,
            ok=True,
            hint="Run with `--run` to launch winget. Windows may show UAC or installer prompts.",
        )

    result = system.powershell(command, timeout=600.0)
    return SetupResult(
        component="usbipd-win",
        installed=find_usbipd() is not None,
        command=command,
        ran=True,
        ok=result.ok and find_usbipd() is not None,
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
        hint="Restart WSL if usbipd.exe is still not visible after a successful install.",
    )

