from __future__ import annotations

import glob
import sys

from . import system, usbipd
from .models import DoctorCheck, DoctorReport


def collect_doctor_report() -> DoctorReport:
    checks: list[DoctorCheck] = []

    checks.append(
        DoctorCheck(
            key="wsl2",
            ok=system.is_wsl2(),
            value=system.wsl_version() or "unknown",
            detail="WSL2 is required for usbipd-win attach workflows",
            fix="Run `wsl --update` and use a WSL2 distro.",
        )
    )

    networking_mode = system.wsl_networking_mode()
    checks.append(
        DoctorCheck(
            key="wsl_networking",
            ok=networking_mode == "mirrored",
            value=networking_mode or "unknown",
            detail="mirrored mode improves ROS/LAN behavior from WSL2",
            fix="Set `[wsl2] networkingMode=mirrored` in `%UserProfile%\\.wslconfig`.",
        )
    )

    ros_distro = system.ros_distro()
    checks.append(
        DoctorCheck(
            key="ros_distro",
            ok=bool(ros_distro),
            value=ros_distro or "not sourced",
            detail="ROS environment should be sourced before using ROS export helpers",
            fix="Run `source /opt/ros/jazzy/setup.bash`.",
        )
    )

    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    checks.append(
        DoctorCheck(
            key="python_abi",
            ok=(sys.version_info.major, sys.version_info.minor) == (3, 12),
            value=f"{sys.executable} ({python_version})",
            detail="ROS2 Jazzy Python packages are built for Python 3.12 on Ubuntu 24.04",
            fix="Build and run with `/usr/bin/python3.12` or the ROS-provided colcon environment.",
        )
    )

    for command in ["ros2", "colcon", "lsusb", "udevadm", "powershell.exe"]:
        path = system.command_path(command)
        checks.append(
            DoctorCheck(
                key=f"command:{command}",
                ok=bool(path),
                value=path or "missing",
                fix=f"Install or expose `{command}` in PATH.",
            )
        )

    usbipd_path = usbipd.find_usbipd()
    checks.append(
        DoctorCheck(
            key="usbipd-win",
            ok=bool(usbipd_path),
            value=usbipd_path or "missing",
            detail="USB devices are attached to WSL through usbipd-win",
            fix="Install from Windows: `winget install --interactive --exact dorssel.usbipd-win`.",
        )
    )

    usbipd_service = usbipd.service_status()
    checks.append(
        DoctorCheck(
            key="usbipd_service",
            ok=usbipd_service.running,
            value=usbipd_service.state,
            detail=usbipd_service.detail or "The usbipd Windows service must be running before attach can work",
            fix="Restart Windows after installing usbipd-win, or start the `USBIP Device Host` service as administrator.",
        )
    )

    winget_path = system.find_windows_command("winget.exe")
    checks.append(
        DoctorCheck(
            key="winget",
            ok=bool(winget_path),
            value=winget_path or "missing",
            detail="winget is the recommended Windows installer path for usbipd-win",
        )
    )

    serial_count = len(glob.glob("/dev/serial/by-id/*")) + len(glob.glob("/dev/ttyUSB*")) + len(glob.glob("/dev/ttyACM*"))
    checks.append(
        DoctorCheck(
            key="wsl_serial_devices",
            ok=serial_count > 0,
            value=str(serial_count),
            detail="No serial devices is normal before attaching hardware",
        )
    )

    input_count = len(glob.glob("/dev/input/js*"))
    checks.append(
        DoctorCheck(
            key="wsl_gamepads",
            ok=input_count > 0,
            value=str(input_count),
            detail="No gamepad device is normal before attaching or pairing a controller",
        )
    )

    warnings = [
        "USB bind is persistent but requires elevated Windows privileges; attach is non-persistent.",
        "Bluetooth/XInput/GameInput controllers are reported in v1 but native Windows bridging is planned for v2.",
    ]
    return DoctorReport(checks=checks, warnings=warnings)
