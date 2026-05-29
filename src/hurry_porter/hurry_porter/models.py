from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any


@dataclass
class TransportCandidate:
    kind: str
    endpoint: str
    priority: int
    latency_class: str
    command_preview: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class DeviceDescriptor:
    id: str
    kind: str
    locality: str
    state: str
    name: str
    role: str | None = None
    stable_path: str | None = None
    bus_id: str | None = None
    vid: str | None = None
    pid: str | None = None
    serial: str | None = None
    address: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    transports: list[TransportCandidate] = field(default_factory=list)
    recommendation: str | None = None


@dataclass
class DoctorCheck:
    key: str
    ok: bool
    value: str | None = None
    detail: str | None = None
    fix: str | None = None


@dataclass
class DoctorReport:
    checks: list[DoctorCheck]
    warnings: list[str] = field(default_factory=list)


@dataclass
class ScanResult:
    devices: list[DeviceDescriptor]
    warnings: list[str] = field(default_factory=list)


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    return value

