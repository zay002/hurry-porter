from __future__ import annotations

import glob
import json
import os
import re
from pathlib import Path

from . import system, usbipd
from .config import HurryConfig, apply_roles
from .lan import probe_configured, scan_cidr
from .models import DeviceDescriptor, ScanResult, TransportCandidate


SERIAL_HINTS = re.compile(r"CH340|CP210|USB Serial|UART|CDC|FTDI|STLink|ST-Link", re.I)
GAMEPAD_HINTS = re.compile(r"Xbox|Controller|Gamepad|Joystick|DualSense|Wireless Controller", re.I)
WINDOWS_GAMEPAD_HINTS = re.compile(
    r"Pro Controller|Xbox.*Controller|Wireless Controller|DualSense|DualShock|8BitDo|Gamepad",
    re.I,
)
WINDOWS_GENERIC_GAMEPAD_HINTS = re.compile(r"HID-compliant game controller", re.I)
WINDOWS_GAMEPAD_IGNORE = re.compile(r"Driver|Emulation|Virtual Gamepad|Bus", re.I)


def scan_devices(
    config: HurryConfig,
    lan_cidr: str | None = None,
    lan_ports: list[int] | None = None,
) -> ScanResult:
    warnings: list[str] = []
    devices: list[DeviceDescriptor] = []

    usb_devices, usb_warnings = scan_windows_usb()
    warnings.extend(usb_warnings)
    devices.extend(usb_devices)
    gamepads, gamepad_warnings = scan_windows_gamepads()
    warnings.extend(gamepad_warnings)
    devices.extend(gamepads)
    devices.extend(scan_wsl_serial())
    devices.extend(scan_wsl_input())
    devices.extend(scan_configured_lan(config))
    if lan_cidr and lan_ports:
        devices.extend(scan_lan_cidr(lan_cidr, lan_ports))

    apply_roles(devices, config)
    return ScanResult(devices=devices, warnings=warnings)


def scan_windows_usb() -> tuple[list[DeviceDescriptor], list[str]]:
    raw_devices, warnings = usbipd.list_devices()
    devices: list[DeviceDescriptor] = []
    for raw in raw_devices:
        kind = classify_name(raw.name)
        needs_bind = "not shared" in raw.state.lower()
        warnings_for_transport = ["requires elevated usbipd bind before attach"] if needs_bind else []
        recommendation = "run elevated bind, then attach" if needs_bind else "attach with usbipd-win"
        devices.append(
            DeviceDescriptor(
                id=f"usbipd:{raw.bus_id}",
                kind=kind,
                locality="windows_host",
                state=raw.state,
                name=raw.name,
                bus_id=raw.bus_id,
                vid=raw.vid,
                pid=raw.pid,
                metadata={"section": raw.section},
                transports=[
                    TransportCandidate(
                        kind="usbipd",
                        endpoint=raw.bus_id,
                        priority=10,
                        latency_class="near_native",
                        command_preview=f"usbipd.exe attach --wsl --busid {raw.bus_id}",
                        warnings=warnings_for_transport,
                    )
                ],
                recommendation=recommendation,
            )
        )
    return devices, warnings


def scan_windows_gamepads() -> tuple[list[DeviceDescriptor], list[str]]:
    result = system.powershell(
        r"""
$devices = Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
  Where-Object {
    $_.Status -eq 'OK' -and (
      $_.FriendlyName -match 'Pro Controller|Xbox.*Controller|Wireless Controller|DualSense|DualShock|8BitDo|Gamepad' -or
      $_.FriendlyName -match 'HID-compliant game controller'
    )
  } |
  Select-Object Status,Class,FriendlyName,InstanceId
$devices | ConvertTo-Json -Compress
""",
        timeout=12.0,
    )
    if not result.ok or not result.stdout.strip():
        return [], []
    return parse_windows_gamepads(result.stdout), []


def parse_windows_gamepads(text: str) -> list[DeviceDescriptor]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []

    rows = payload if isinstance(payload, list) else [payload]
    raw_rows = [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("FriendlyName")
        and row.get("InstanceId")
        and not WINDOWS_GAMEPAD_IGNORE.search(str(row.get("FriendlyName")))
        and str(row.get("Class") or "") != "System"
    ]
    named_rows = [row for row in raw_rows if WINDOWS_GAMEPAD_HINTS.search(str(row.get("FriendlyName")))]
    rows_to_use = named_rows or [
        row for row in raw_rows if WINDOWS_GENERIC_GAMEPAD_HINTS.search(str(row.get("FriendlyName")))
    ]

    devices: list[DeviceDescriptor] = []
    seen: set[str] = set()
    for row in rows_to_use:
        instance_id = str(row["InstanceId"])
        if instance_id in seen:
            continue
        seen.add(instance_id)
        name = str(row["FriendlyName"])
        status = str(row.get("Status") or "unknown")
        devices.append(
            DeviceDescriptor(
                id=f"windows-gamepad:{stable_id(instance_id)}",
                kind="gamepad",
                locality="windows_host",
                state=status,
                name=name,
                metadata={
                    "class": str(row.get("Class") or ""),
                    "instance_id": instance_id,
                    "windows_input": "true",
                },
                transports=[
                    TransportCandidate(
                        kind="windows_input_bridge",
                        endpoint=instance_id,
                        priority=30,
                        latency_class="bridge_planned",
                        warnings=[
                            "Windows Bluetooth/HID gamepad is visible, but WSL native /dev/input support requires a future Windows input bridge or USB attach"
                        ],
                    )
                ],
                recommendation="use a wired USB attach path for v1, or the planned Windows input bridge in v2",
            )
        )
    return devices


def scan_wsl_serial() -> list[DeviceDescriptor]:
    stable_paths = sorted(glob.glob("/dev/serial/by-id/*"))
    if not stable_paths:
        stable_paths = sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))

    devices: list[DeviceDescriptor] = []
    seen_real_paths: set[str] = set()
    for path_text in stable_paths:
        path = Path(path_text)
        real_path = str(path.resolve()) if path.exists() else path_text
        if real_path in seen_real_paths:
            continue
        seen_real_paths.add(real_path)
        props = udev_properties(real_path)
        stable_path = path_text if "/dev/serial/by-id/" in path_text else real_path
        name = props.get("ID_MODEL_FROM_DATABASE") or props.get("ID_MODEL") or path.name
        devices.append(
            DeviceDescriptor(
                id=f"serial:{stable_path}",
                kind="serial",
                locality="wsl_native",
                state="present",
                name=name,
                stable_path=stable_path,
                vid=_lower(props.get("ID_VENDOR_ID")),
                pid=_lower(props.get("ID_MODEL_ID")),
                serial=props.get("ID_SERIAL_SHORT") or props.get("ID_SERIAL"),
                metadata={key: value for key, value in props.items() if key.startswith("ID_")},
                transports=[
                    TransportCandidate(
                        kind="direct_linux_device",
                        endpoint=stable_path,
                        priority=1,
                        latency_class="native",
                    )
                ],
                recommendation="use directly from ROS in WSL",
            )
        )
    return devices


def scan_wsl_input() -> list[DeviceDescriptor]:
    devices: list[DeviceDescriptor] = []
    for path_text in sorted(glob.glob("/dev/input/js*")):
        path = Path(path_text)
        name = input_name(path.name) or path.name
        props = udev_properties(path_text)
        devices.append(
            DeviceDescriptor(
                id=f"input:{path.name}",
                kind="gamepad",
                locality="wsl_native",
                state="present",
                name=name,
                stable_path=path_text,
                vid=_lower(props.get("ID_VENDOR_ID")),
                pid=_lower(props.get("ID_MODEL_ID")),
                serial=props.get("ID_SERIAL_SHORT") or props.get("ID_SERIAL"),
                metadata={key: value for key, value in props.items() if key.startswith("ID_")},
                transports=[
                    TransportCandidate(
                        kind="direct_linux_device",
                        endpoint=path_text,
                        priority=1,
                        latency_class="native",
                    )
                ],
                recommendation="use ROS joy or joy_linux with this device path",
            )
        )
    return devices


def scan_configured_lan(config: HurryConfig) -> list[DeviceDescriptor]:
    devices: list[DeviceDescriptor] = []
    for rule in config.lan_rules:
        probes = probe_configured(rule.lan_host or "", rule.lan_ports)
        open_ports = [probe.port for probe in probes if probe.open]
        state = "online" if open_ports else "offline"
        ports_text = ",".join(str(port) for port in rule.lan_ports)
        devices.append(
            DeviceDescriptor(
                id=f"lan:{rule.lan_host}:{rule.role}",
                role=rule.role,
                kind=rule.kind or "lan_robot",
                locality="lan",
                state=state,
                name=rule.role,
                address=rule.lan_host,
                metadata={
                    "configured_ports": ports_text,
                    "open_ports": ",".join(str(port) for port in open_ports),
                },
                transports=[
                    TransportCandidate(
                        kind="tcp_ip",
                        endpoint=f"{rule.lan_host}:{ports_text}",
                        priority=1,
                        latency_class="lan_bound",
                        warnings=[] if open_ports else ["configured LAN endpoint is not reachable"],
                    )
                ],
                recommendation="connect directly from WSL over LAN" if open_ports else "check robot power, subnet, or firewall",
            )
        )
    return devices


def scan_lan_cidr(cidr: str, ports: list[int]) -> list[DeviceDescriptor]:
    devices: list[DeviceDescriptor] = []
    for probe in scan_cidr(cidr, ports):
        devices.append(
            DeviceDescriptor(
                id=f"lan:{probe.host}:{probe.port}",
                kind="lan_generic",
                locality="lan",
                state="online",
                name=f"{probe.host}:{probe.port}",
                address=probe.host,
                metadata={"open_port": str(probe.port), "latency_ms": str(probe.latency_ms)},
                transports=[
                    TransportCandidate(
                        kind="tcp_ip",
                        endpoint=f"{probe.host}:{probe.port}",
                        priority=20,
                        latency_class="lan_bound",
                    )
                ],
                recommendation="classify this endpoint in hurry.toml if it belongs to the robot",
            )
        )
    return devices


def classify_name(name: str) -> str:
    if SERIAL_HINTS.search(name):
        return "serial"
    if GAMEPAD_HINTS.search(name):
        return "gamepad"
    if "HID" in name.upper():
        return "hid"
    return "usb"


def udev_properties(device_path: str) -> dict[str, str]:
    result = system.run_capture(["udevadm", "info", "-q", "property", "-n", device_path], timeout=2.0)
    if not result.ok:
        return {}
    props: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key] = value
    return props


def input_name(js_name: str) -> str | None:
    value = system.read_text(Path("/sys/class/input") / js_name / "device" / "name")
    return value or None


def _lower(value: str | None) -> str | None:
    return value.lower() if value else None


def stable_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()[:96] or "device"
