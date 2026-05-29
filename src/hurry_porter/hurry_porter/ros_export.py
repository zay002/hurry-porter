from __future__ import annotations

import json
import re

from .models import DeviceDescriptor, to_jsonable


def build_exports(devices: list[DeviceDescriptor]) -> dict[str, str]:
    exports: dict[str, str] = {}
    role_counts: dict[str, int] = {}

    for device in devices:
        role = sanitize_role(device.role or device.kind)
        role_counts[role] = role_counts.get(role, 0) + 1
        if not device.role and role_counts[role] > 1:
            role = f"{role}_{role_counts[role]}"

        prefix = f"HURRY_{role.upper()}"
        if device.kind == "serial" and device.stable_path:
            exports[f"{prefix}_PORT"] = device.stable_path
        elif device.kind == "gamepad" and device.stable_path:
            exports[f"{prefix}_DEV"] = device.stable_path
        elif device.locality == "lan" and device.address:
            exports[f"{prefix}_HOST"] = device.address
            ports = device.metadata.get("open_ports") or device.metadata.get("configured_ports")
            if ports:
                exports[f"{prefix}_PORTS"] = ports
        elif device.bus_id:
            exports[f"{prefix}_BUSID"] = device.bus_id

    return exports


def render_exports(devices: list[DeviceDescriptor], output_format: str) -> str:
    exports = build_exports(devices)
    if output_format == "json":
        return json.dumps({"exports": exports, "devices": to_jsonable(devices)}, indent=2, sort_keys=True)
    if output_format == "yaml":
        try:
            import yaml

            return yaml.safe_dump({"exports": exports, "devices": to_jsonable(devices)}, sort_keys=True)
        except ImportError:
            return json.dumps({"exports": exports, "devices": to_jsonable(devices)}, indent=2, sort_keys=True)
    if output_format == "launch":
        return "\n".join(f"{key.lower().removeprefix('hurry_')}:={value}" for key, value in sorted(exports.items()))
    return "\n".join(f"export {key}={shell_quote(value)}" for key, value in sorted(exports.items()))


def sanitize_role(value: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return sanitized or "device"


def shell_quote(value: str) -> str:
    if re.match(r"^[A-Za-z0-9_./:@,+-]+$", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"

