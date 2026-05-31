from __future__ import annotations

import json
import ipaddress
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .lan import normalize_mac
from .models import DeviceDescriptor


DEFAULT_CONFIG_TEMPLATE = """# hurry.toml
# Copy this file to the root of your ROS 2 workspace and edit roles as needed.

[watch]
interval_seconds = 2.0
auto_attach = true

[[devices]]
role = "base_controller"
kind = "serial"
description_regex = "CH340|CP210|USB Serial|UART|CDC|FTDI"
auto_attach = true
preferred_transport = "usbipd"

[[devices]]
role = "gamepad"
kind = "gamepad"
description_regex = "Xbox|Controller|Gamepad|Joystick|DualSense|Wireless Controller"
auto_attach = false
preferred_transport = "usbipd"

[[devices]]
role = "arm_controller"
kind = "lan_robot"
lan_host = "192.168.1.10"
lan_mac = "aa:bb:cc:dd:ee:ff"
lan_cidr = "192.168.1.0/24"
lan_ports = [502, 30002]
preferred_transport = "lan"
"""


@dataclass
class WatchSettings:
    interval_seconds: float = 2.0
    auto_attach: bool = True


@dataclass
class DeviceRule:
    role: str
    kind: str | None = None
    vid: str | None = None
    pid: str | None = None
    serial: str | None = None
    description_regex: str | None = None
    busid_regex: str | None = None
    path_regex: str | None = None
    lan_host: str | None = None
    lan_mac: str | None = None
    lan_cidr: str | None = None
    lan_ports: list[int] = field(default_factory=list)
    auto_attach: bool = False
    preferred_transport: str | None = None

    def matches(self, device: DeviceDescriptor) -> bool:
        if self.lan_host and device.address != self.lan_host:
            return False
        if self.lan_mac:
            device_mac = normalize_mac(device.metadata.get("mac") or device.metadata.get("configured_mac"))
            if normalize_mac(self.lan_mac) != device_mac:
                return False
        if self.lan_cidr and device.address:
            try:
                if ipaddress.ip_address(device.address) not in ipaddress.ip_network(self.lan_cidr, strict=False):
                    return False
            except ValueError:
                return False
        if self.vid and normalize_id(self.vid) != normalize_id(device.vid):
            return False
        if self.pid and normalize_id(self.pid) != normalize_id(device.pid):
            return False
        if self.serial and self.serial.lower() not in (device.serial or "").lower():
            return False
        if self.busid_regex and not re.search(self.busid_regex, device.bus_id or ""):
            return False
        if self.path_regex and not re.search(self.path_regex, device.stable_path or ""):
            return False
        if self.description_regex and not re.search(
            self.description_regex,
            " ".join([device.name, " ".join(device.metadata.values())]),
            flags=re.IGNORECASE,
        ):
            return False
        if self.kind and self.kind not in {device.kind, device.metadata.get("configured_kind")}:
            if self.description_regex or self.vid or self.pid or self.busid_regex:
                return True
            return False
        return True


@dataclass
class HurryConfig:
    rules: list[DeviceRule] = field(default_factory=list)
    watch: WatchSettings = field(default_factory=WatchSettings)
    source: Path | None = None

    @property
    def lan_rules(self) -> list[DeviceRule]:
        return [rule for rule in self.rules if rule.lan_host or rule.lan_mac]


def normalize_id(value: str | None) -> str | None:
    if not value:
        return None
    return value.lower().replace("0x", "").strip()


def default_config_candidates() -> list[Path]:
    return [
        Path.cwd() / "hurry.toml",
        Path.cwd() / ".hurry.toml",
        Path.cwd() / "config" / "hurry.toml",
    ]


def load_config(path: str | None = None) -> HurryConfig:
    config_path = Path(path).expanduser() if path else next(
        (candidate for candidate in default_config_candidates() if candidate.exists()),
        None,
    )
    if not config_path or not config_path.exists():
        return HurryConfig()

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    rules = [_rule_from_mapping(item) for item in data.get("devices", [])]
    rules.extend(_rule_from_mapping(item) for item in data.get("lan", []))
    return HurryConfig(rules=rules, watch=_watch_from_mapping(data.get("watch", {})), source=config_path)


def apply_roles(devices: list[DeviceDescriptor], config: HurryConfig) -> None:
    for device in devices:
        for rule in config.rules:
            if rule.matches(device):
                device.role = rule.role
                device.metadata["auto_attach"] = str(rule.auto_attach).lower()
                if rule.preferred_transport:
                    device.metadata["preferred_transport"] = rule.preferred_transport
                break


def render_default_config() -> str:
    return DEFAULT_CONFIG_TEMPLATE


def render_config_from_devices(devices: list[DeviceDescriptor]) -> str:
    rule_blocks: list[str] = []
    role_counts: dict[str, int] = {}

    for device in devices:
        block = _render_device_rule(device, role_counts)
        if block:
            rule_blocks.append(block)

    if not rule_blocks:
        return render_default_config()

    return "\n\n".join(
        [
            "# hurry.toml generated from current scan.",
            "# Review role names before using it in a robot launch flow.",
            "[watch]\ninterval_seconds = 2.0\nauto_attach = true",
            *rule_blocks,
        ]
    ) + "\n"


def _rule_from_mapping(data: dict[str, Any]) -> DeviceRule:
    ports = data.get("lan_ports", [])
    if isinstance(ports, int):
        ports = [ports]
    return DeviceRule(
        role=str(data["role"]),
        kind=_optional_str(data.get("kind")),
        vid=_optional_str(data.get("vid")),
        pid=_optional_str(data.get("pid")),
        serial=_optional_str(data.get("serial")),
        description_regex=_optional_str(data.get("description_regex")),
        busid_regex=_optional_str(data.get("busid_regex")),
        path_regex=_optional_str(data.get("path_regex") or data.get("stable_path_regex")),
        lan_host=_optional_str(data.get("lan_host") or data.get("host")),
        lan_mac=_optional_str(data.get("lan_mac") or data.get("mac")),
        lan_cidr=_optional_str(data.get("lan_cidr") or data.get("cidr") or data.get("network")),
        lan_ports=[int(port) for port in ports],
        auto_attach=bool(data.get("auto_attach", False)),
        preferred_transport=_optional_str(data.get("preferred_transport")),
    )


def _watch_from_mapping(data: dict[str, Any]) -> WatchSettings:
    interval = data.get("interval_seconds", data.get("interval", 2.0))
    return WatchSettings(
        interval_seconds=float(interval),
        auto_attach=bool(data.get("auto_attach", True)),
    )


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _render_device_rule(device: DeviceDescriptor, role_counts: dict[str, int]) -> str | None:
    if device.locality == "lan" and device.address:
        role = _next_role(_base_role(device), role_counts)
        ports = _ports_for_lan_device(device)
        lines = [
            "[[devices]]",
            f"role = {_toml_string(role)}",
            f"kind = {_toml_string(device.kind)}",
            f"lan_host = {_toml_string(device.address)}",
        ]
        mac = normalize_mac(device.metadata.get("mac"))
        if mac:
            lines.append(f"lan_mac = {_toml_string(mac)}")
        lan_cidr = device.metadata.get("scan_cidr") or device.metadata.get("configured_cidr")
        if lan_cidr:
            lines.append(f"lan_cidr = {_toml_string(lan_cidr)}")
        if ports:
            lines.append(f"lan_ports = [{', '.join(str(port) for port in ports)}]")
        lines.append('preferred_transport = "lan"')
        return "\n".join(lines)

    if device.kind not in {"serial", "gamepad", "hid", "usb"}:
        return None

    role = _next_role(_base_role(device), role_counts)
    lines = [
        "[[devices]]",
        f"role = {_toml_string(role)}",
        f"kind = {_toml_string(device.kind)}",
    ]
    if device.vid:
        lines.append(f"vid = {_toml_string(device.vid)}")
    if device.pid:
        lines.append(f"pid = {_toml_string(device.pid)}")
    if device.serial:
        lines.append(f"serial = {_toml_string(device.serial)}")
    if not (device.vid and device.pid):
        lines.append(f"description_regex = {_toml_string(re.escape(device.name))}")
    if device.bus_id:
        lines.append(f"busid_regex = {_toml_string(device.bus_id)}")
    if device.stable_path:
        lines.append(f"path_regex = {_toml_string(re.escape(device.stable_path))}")

    if device.locality == "windows_host":
        lines.append(f"auto_attach = {_toml_bool(device.kind == 'serial')}")
        lines.append('preferred_transport = "usbipd"')
    elif device.locality == "wsl_native":
        lines.append('preferred_transport = "direct_linux_device"')
    return "\n".join(lines)


def _base_role(device: DeviceDescriptor) -> str:
    if device.role:
        return device.role
    name = device.name.lower()
    if device.kind == "serial":
        return "base_controller"
    if device.kind == "gamepad":
        return "gamepad"
    if device.locality == "lan":
        if "arm" in name or "robot" in name:
            return "arm_controller"
        return "lan_robot"
    if "lidar" in name or "rplidar" in name:
        return "lidar"
    return device.kind or "device"


def _next_role(base: str, role_counts: dict[str, int]) -> str:
    role_counts[base] = role_counts.get(base, 0) + 1
    if role_counts[base] == 1:
        return base
    return f"{base}_{role_counts[base]}"


def _ports_for_lan_device(device: DeviceDescriptor) -> list[int]:
    ports: list[int] = []
    for key in ("configured_ports", "open_ports", "open_port"):
        value = device.metadata.get(key)
        if not value:
            continue
        for item in value.split(","):
            item = item.strip()
            if item.isdigit():
                ports.append(int(item))
    return sorted(set(ports))


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"
