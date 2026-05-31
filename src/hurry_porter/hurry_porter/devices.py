from __future__ import annotations

import glob
import json
import os
import re
from pathlib import Path

from . import system, usbipd
from .config import DeviceRule, HurryConfig, apply_roles
from .lan import (
    MacMatch,
    find_hosts_by_mac,
    local_ipv4_cidrs,
    normalize_mac,
    probe_configured,
    read_neighbor_table,
    scan_cidr,
)
from .models import DeviceDescriptor, ScanResult, TransportCandidate
from .serial_setup import scan_windows_com_ports


SERIAL_HINTS = re.compile(r"CH340|CP210|USB Serial|UART|CDC|FTDI|STLink|ST-Link", re.I)
GAMEPAD_HINTS = re.compile(r"Xbox|Controller|Gamepad|Joystick|DualSense|Wireless Controller", re.I)
WINDOWS_GAMEPAD_HINTS = re.compile(
    r"Pro Controller|Xbox.*Controller|Wireless Controller|DualSense|DualShock|8BitDo|Gamepad",
    re.I,
)
WINDOWS_GENERIC_GAMEPAD_HINTS = re.compile(r"HID-compliant game controller", re.I)
WINDOWS_GAMEPAD_IGNORE = re.compile(r"Driver|Emulation|Virtual Gamepad|Bus", re.I)
WINDOWS_PRO_CONTROLLER_LED_NOTE = (
    "Windows Bluetooth can leave Nintendo Switch Pro Controller player LEDs in the pairing/search pattern even while "
    "HID input is connected; if state is OK and /joy changes, do not re-pair."
)


def scan_devices(
    config: HurryConfig,
    lan_cidr: str | None = None,
    lan_ports: list[int] | None = None,
    lan_macs: list[str] | None = None,
) -> ScanResult:
    warnings: list[str] = []
    devices: list[DeviceDescriptor] = []

    usb_devices, usb_warnings = scan_windows_usb()
    warnings.extend(usb_warnings)
    devices.extend(usb_devices)
    devices.extend(scan_windows_serial_ports(usb_devices))
    gamepads, gamepad_warnings = scan_windows_gamepads()
    warnings.extend(gamepad_warnings)
    devices.extend(gamepads)
    devices.extend(scan_wsl_serial())
    devices.extend(scan_wsl_input())
    devices.extend(scan_configured_lan(config))
    if lan_cidr and lan_ports:
        devices.extend(scan_lan_cidr(lan_cidr, lan_ports))
    if lan_macs:
        if lan_cidr:
            devices.extend(scan_lan_mac(lan_macs, cidr=lan_cidr, ports=lan_ports or []))
        else:
            for cidr in local_ipv4_cidrs() or [None]:
                devices.extend(scan_lan_mac(lan_macs, cidr=cidr, ports=lan_ports or []))

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


def scan_windows_serial_ports(existing_usb_devices: list[DeviceDescriptor] | None = None) -> list[DeviceDescriptor]:
    existing_bus_ids = {device.bus_id for device in existing_usb_devices or [] if device.bus_id}
    existing_identities = {
        (device.vid, device.pid)
        for device in existing_usb_devices or []
        if device.kind == "serial" and device.vid and device.pid
    }
    devices: list[DeviceDescriptor] = []
    for port in scan_windows_com_ports():
        vid, pid = parse_usb_vid_pid(port.device_id or "")
        if port.bus_id and port.bus_id in existing_bus_ids:
            continue
        if not port.bus_id and vid and pid and (vid, pid) in existing_identities:
            continue
        has_bus_id = bool(port.bus_id)
        warnings = [] if has_bus_id else ["Windows reports this COM port, but usbipd currently has no attachable bus id"]
        command_preview = f"usbipd.exe attach --wsl --busid {port.bus_id}" if has_bus_id else None
        devices.append(
            DeviceDescriptor(
                id=f"windows-com:{stable_id(port.device_id or port.name)}",
                kind="serial",
                locality="windows_host",
                state=port.status or ("bus_id_missing" if not has_bus_id else "present"),
                name=port.name,
                bus_id=port.bus_id,
                vid=vid,
                pid=pid,
                metadata={
                    "source": "windows_com",
                    "device_id": port.device_id or "",
                    "manufacturer": port.manufacturer or "",
                },
                transports=[
                    TransportCandidate(
                        kind="usbipd" if has_bus_id else "windows_com_pending",
                        endpoint=port.bus_id or port.device_id or port.name,
                        priority=12,
                        latency_class="near_native" if has_bus_id else "blocked_until_bus_id",
                        command_preview=command_preview,
                        warnings=warnings,
                    )
                ],
                recommendation=(
                    "attach with usbipd-win"
                    if has_bus_id
                    else "replug the USB serial adapter or restart usbipd service until usbipd reports a bus id"
                ),
            )
        )
    return devices


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
        metadata = {
            "class": str(row.get("Class") or ""),
            "instance_id": instance_id,
            "windows_input": "true",
        }
        transport_warnings = [
            "Windows Bluetooth/HID gamepad is visible; use `hurry gamepad bridge` plus `hurry gamepad start-agent` to publish ROS Joy"
        ]
        recommendation = "use the v2 Windows gamepad bridge, or attach wired USB gamepads through usbipd when available"
        if is_windows_pro_controller(name, instance_id):
            metadata.update(
                {
                    "controller_family": "nintendo_switch_pro",
                    "quirk": "windows_pro_controller_led_unassigned",
                    "windows_led_note": WINDOWS_PRO_CONTROLLER_LED_NOTE,
                }
            )
            transport_warnings.append(WINDOWS_PRO_CONTROLLER_LED_NOTE)
            recommendation = (
                "use the v2 Windows gamepad bridge; blinking player LEDs are a Windows Pro Controller LED quirk, not a pairing failure"
            )
        devices.append(
            DeviceDescriptor(
                id=f"windows-gamepad:{stable_id(instance_id)}",
                kind="gamepad",
                locality="windows_host",
                state=status,
                name=name,
                metadata=metadata,
                transports=[
                    TransportCandidate(
                        kind="windows_input_bridge",
                        endpoint=instance_id,
                        priority=30,
                        latency_class="udp_bridge",
                        warnings=transport_warnings,
                    )
                ],
                recommendation=recommendation,
            )
        )
    return devices


def is_windows_pro_controller(name: str, instance_id: str) -> bool:
    text = f"{name} {instance_id}".lower()
    return "pro controller" in text or ("057e" in text and "2009" in text)


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
        if rule.lan_mac:
            devices.extend(scan_configured_lan_mac(rule))
            continue
        if rule.lan_host:
            devices.append(make_lan_rule_device(rule=rule, host=rule.lan_host, mac_match=None))
    return devices


def scan_lan_cidr(cidr: str, ports: list[int]) -> list[DeviceDescriptor]:
    devices: list[DeviceDescriptor] = []
    neighbors = {item.host: item for item in read_neighbor_table()}
    for probe in scan_cidr(cidr, ports):
        metadata = {"open_port": str(probe.port), "latency_ms": str(probe.latency_ms), "scan_cidr": cidr}
        neighbor = neighbors.get(probe.host)
        if neighbor:
            metadata.update(
                {
                    "mac": neighbor.mac,
                    "mac_source": neighbor.source,
                    "interface": neighbor.interface or "",
                }
            )
        devices.append(
            DeviceDescriptor(
                id=f"lan:{probe.host}:{probe.port}",
                kind="lan_generic",
                locality="lan",
                state="online",
                name=f"{probe.host}:{probe.port}",
                address=probe.host,
                metadata=metadata,
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


def scan_lan_mac(
    macs: list[str],
    cidr: str | None = None,
    ports: list[int] | None = None,
) -> list[DeviceDescriptor]:
    devices: list[DeviceDescriptor] = []
    for mac in macs:
        normalized = normalize_mac(mac)
        if not normalized:
            continue
        matches = find_hosts_by_mac(normalized, cidr=cidr, ports=ports or [])
        if not matches:
            devices.append(make_lan_mac_not_found_device(normalized, cidr=cidr, ports=ports or []))
            continue
        for match in matches:
            rule = DeviceRule(
                role="lan_robot",
                kind="lan_robot",
                lan_mac=normalized,
                lan_cidr=cidr,
                lan_ports=ports or [],
                preferred_transport="lan",
            )
            devices.append(make_lan_rule_device(rule=rule, host=match.host, mac_match=match))
    return devices


def scan_configured_lan_mac(rule: DeviceRule) -> list[DeviceDescriptor]:
    normalized = normalize_mac(rule.lan_mac)
    if not normalized:
        return []

    matches = []
    cidrs = [rule.lan_cidr] if rule.lan_cidr else [None]
    if not rule.lan_cidr and not rule.lan_host:
        cidrs = local_ipv4_cidrs() or [None]

    for cidr in cidrs:
        matches.extend(find_hosts_by_mac(normalized, cidr=cidr, ports=rule.lan_ports))
        if matches:
            break

    if matches:
        return [make_lan_rule_device(rule=rule, host=match.host, mac_match=match) for match in matches]
    if rule.lan_host:
        return [make_lan_rule_device(rule=rule, host=rule.lan_host, mac_match=None, mac_unconfirmed=True)]
    return [make_lan_mac_not_found_device(normalized, cidr=rule.lan_cidr, ports=rule.lan_ports, role=rule.role, kind=rule.kind)]


def make_lan_rule_device(
    rule: DeviceRule,
    host: str,
    mac_match: MacMatch | None,
    mac_unconfirmed: bool = False,
) -> DeviceDescriptor:
    probes = probe_configured(host, rule.lan_ports) if rule.lan_ports else []
    open_ports = [probe.port for probe in probes if probe.open]
    ports_text = ",".join(str(port) for port in rule.lan_ports)
    warnings = lan_rule_warnings(rule, host, open_ports, mac_match, mac_unconfirmed)
    state = lan_rule_state(rule, open_ports, mac_match)
    metadata = {
        "configured_ports": ports_text,
        "open_ports": ",".join(str(port) for port in open_ports),
    }
    if rule.lan_cidr:
        metadata["configured_cidr"] = rule.lan_cidr
    if rule.lan_host:
        metadata["configured_host"] = rule.lan_host
    if rule.lan_mac:
        metadata["configured_mac"] = normalize_mac(rule.lan_mac) or rule.lan_mac
    if mac_match:
        metadata.update(
            {
                "mac": mac_match.mac,
                "mac_source": mac_match.source,
                "interface": mac_match.interface or "",
                "neighbor_state": mac_match.state or "",
            }
        )

    return DeviceDescriptor(
        id=f"lan:{host}:{rule.role}",
        role=rule.role,
        kind=rule.kind or "lan_robot",
        locality="lan",
        state=state,
        name=rule.role,
        address=host,
        metadata=metadata,
        transports=[
            TransportCandidate(
                kind="tcp_ip",
                endpoint=f"{host}:{ports_text}" if ports_text else host,
                priority=1,
                latency_class="lan_bound",
                warnings=warnings,
            )
        ],
        recommendation=lan_rule_recommendation(open_ports, mac_match, warnings),
    )


def make_lan_mac_not_found_device(
    mac: str,
    cidr: str | None = None,
    ports: list[int] | None = None,
    role: str = "lan_robot",
    kind: str | None = "lan_robot",
) -> DeviceDescriptor:
    ports_text = ",".join(str(port) for port in ports or [])
    metadata = {"mac": mac, "configured_mac": mac, "configured_ports": ports_text}
    if cidr:
        metadata["configured_cidr"] = cidr
    return DeviceDescriptor(
        id=f"lan-mac:{stable_id(mac)}:{role}",
        role=role,
        kind=kind or "lan_robot",
        locality="lan",
        state="not_found",
        name=role,
        metadata=metadata,
        transports=[
            TransportCandidate(
                kind="lan_mac_discovery",
                endpoint=f"{mac}@{cidr or 'neighbor-table'}",
                priority=40,
                latency_class="lan_bound",
                warnings=["MAC address was not found in the local neighbor table"],
            )
        ],
        recommendation="check robot power, subnet, MAC address, or run with --lan-cidr/--cidr for the correct network",
    )


def lan_rule_state(rule: DeviceRule, open_ports: list[int], mac_match: MacMatch | None) -> str:
    if open_ports:
        return "online"
    if mac_match:
        return "present"
    if rule.lan_ports:
        return "offline"
    return "configured"


def lan_rule_warnings(
    rule: DeviceRule,
    host: str,
    open_ports: list[int],
    mac_match: MacMatch | None,
    mac_unconfirmed: bool,
) -> list[str]:
    warnings: list[str] = []
    if rule.lan_ports and not open_ports:
        warnings.append("configured LAN ports are not reachable" if rule.lan_mac else "configured LAN endpoint is not reachable")
    if mac_unconfirmed:
        warnings.append("configured MAC was not confirmed in the local neighbor table")
    if rule.lan_host and mac_match and host != rule.lan_host:
        warnings.append(f"MAC resolved to {host}, not configured host {rule.lan_host}")
    return warnings


def lan_rule_recommendation(open_ports: list[int], mac_match: MacMatch | None, warnings: list[str]) -> str:
    if open_ports:
        return "connect directly from WSL over LAN"
    if mac_match and warnings:
        return "MAC resolved to an IP; verify the robot service port"
    if mac_match:
        return "use this resolved IP from ROS or add lan_ports to hurry.toml"
    return "check robot power, subnet, or firewall"


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


def parse_usb_vid_pid(value: str) -> tuple[str | None, str | None]:
    match = re.search(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})", value)
    if not match:
        return None, None
    return match.group(1).lower(), match.group(2).lower()
