from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import DeviceDescriptor


@dataclass
class DeviceRule:
    role: str
    kind: str | None = None
    vid: str | None = None
    pid: str | None = None
    serial: str | None = None
    description_regex: str | None = None
    busid_regex: str | None = None
    lan_host: str | None = None
    lan_ports: list[int] = field(default_factory=list)
    auto_attach: bool = False
    preferred_transport: str | None = None

    def matches(self, device: DeviceDescriptor) -> bool:
        if self.lan_host and device.address != self.lan_host:
            return False
        if self.vid and normalize_id(self.vid) != normalize_id(device.vid):
            return False
        if self.pid and normalize_id(self.pid) != normalize_id(device.pid):
            return False
        if self.serial and self.serial.lower() not in (device.serial or "").lower():
            return False
        if self.busid_regex and not re.search(self.busid_regex, device.bus_id or ""):
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
    source: Path | None = None

    @property
    def lan_rules(self) -> list[DeviceRule]:
        return [rule for rule in self.rules if rule.lan_host]


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
    return HurryConfig(rules=rules, source=config_path)


def apply_roles(devices: list[DeviceDescriptor], config: HurryConfig) -> None:
    for device in devices:
        for rule in config.rules:
            if rule.matches(device):
                device.role = rule.role
                device.metadata["auto_attach"] = str(rule.auto_attach).lower()
                if rule.preferred_transport:
                    device.metadata["preferred_transport"] = rule.preferred_transport
                break


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
        lan_host=_optional_str(data.get("lan_host") or data.get("host")),
        lan_ports=[int(port) for port in ports],
        auto_attach=bool(data.get("auto_attach", False)),
        preferred_transport=_optional_str(data.get("preferred_transport")),
    )


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)

