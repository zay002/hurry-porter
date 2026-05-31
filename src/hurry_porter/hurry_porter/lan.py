from __future__ import annotations

import ipaddress
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from . import system


@dataclass
class ProbeResult:
    host: str
    port: int
    open: bool
    latency_ms: float | None = None
    error: str | None = None


@dataclass
class MacMatch:
    host: str
    mac: str
    source: str
    interface: str | None = None
    state: str | None = None


NEIGHBOR_STATES = {
    "permanent",
    "noarp",
    "reachable",
    "stale",
    "delay",
    "probe",
    "failed",
    "incomplete",
}


def probe_tcp(host: str, port: int, timeout: float = 0.25) -> ProbeResult:
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            latency_ms = (time.perf_counter() - start) * 1000.0
            return ProbeResult(host, port, True, round(latency_ms, 2))
    except OSError as exc:
        return ProbeResult(host, port, False, error=str(exc))


def probe_configured(host: str, ports: list[int], timeout: float = 0.25) -> list[ProbeResult]:
    return [probe_tcp(host, port, timeout=timeout) for port in ports]


def scan_cidr(cidr: str, ports: list[int], timeout: float = 0.15, limit: int = 256) -> list[ProbeResult]:
    network = ipaddress.ip_network(cidr, strict=False)
    hosts = [str(host) for host in network.hosts()][:limit]
    jobs = [(host, port) for host in hosts for port in ports]
    results: list[ProbeResult] = []
    with ThreadPoolExecutor(max_workers=64) as executor:
        futures = [executor.submit(probe_tcp, host, port, timeout) for host, port in jobs]
        for future in as_completed(futures):
            result = future.result()
            if result.open:
                results.append(result)
    results.sort(key=lambda item: (item.host, item.port))
    return results


def normalize_mac(value: str | None) -> str | None:
    if not value:
        return None
    compact = re.sub(r"[^0-9A-Fa-f]", "", value)
    if len(compact) != 12 or not re.fullmatch(r"[0-9A-Fa-f]{12}", compact):
        return None
    pairs = [compact[index : index + 2].lower() for index in range(0, 12, 2)]
    mac = ":".join(pairs)
    if mac == "00:00:00:00:00:00":
        return None
    return mac


def parse_ip_neigh(text: str) -> list[MacMatch]:
    matches: list[MacMatch] = []
    for line in text.splitlines():
        tokens = line.split()
        if not tokens or "lladdr" not in tokens:
            continue
        host = tokens[0]
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            continue
        if address.version != 4:
            continue
        mac_index = tokens.index("lladdr") + 1
        if mac_index >= len(tokens):
            continue
        mac = normalize_mac(tokens[mac_index])
        if not mac:
            continue
        interface = _token_after(tokens, "dev")
        state = _neighbor_state(tokens[mac_index + 1 :])
        matches.append(MacMatch(host=host, mac=mac, source="ip_neigh", interface=interface, state=state))
    return matches


def parse_arp_table(text: str) -> list[MacMatch]:
    matches: list[MacMatch] = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 6:
            continue
        host, _, flags, mac_text, _, interface = fields[:6]
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            continue
        if address.version != 4 or flags == "0x0":
            continue
        mac = normalize_mac(mac_text)
        if not mac:
            continue
        matches.append(MacMatch(host=host, mac=mac, source="proc_net_arp", interface=interface, state=flags))
    return matches


def read_neighbor_table() -> list[MacMatch]:
    by_key: dict[tuple[str, str], MacMatch] = {}
    result = system.run_capture(["ip", "neigh", "show"], timeout=2.0)
    if result.ok:
        for item in parse_ip_neigh(result.stdout):
            by_key[(item.host, item.mac)] = item

    arp_text = system.read_text(Path("/proc/net/arp"))
    if arp_text:
        for item in parse_arp_table(arp_text):
            by_key.setdefault((item.host, item.mac), item)

    return sorted(by_key.values(), key=lambda item: (_ipv4_sort_key(item.host), item.mac))


def populate_neighbor_cache(
    cidr: str,
    ports: list[int] | None = None,
    timeout: float = 0.05,
    limit: int = 512,
) -> None:
    network = ipaddress.ip_network(cidr, strict=False)
    hosts = [str(host) for host in network.hosts()][:limit]
    if not hosts:
        return

    with ThreadPoolExecutor(max_workers=64) as executor:
        futures = [executor.submit(_touch_host, host, ports or [], timeout) for host in hosts]
        for future in as_completed(futures):
            future.result()

    time.sleep(max(0.05, min(timeout * 4, 0.25)))


def find_hosts_by_mac(
    mac: str,
    cidr: str | None = None,
    ports: list[int] | None = None,
    timeout: float = 0.05,
    limit: int = 512,
) -> list[MacMatch]:
    target = normalize_mac(mac)
    if not target:
        return []

    network = ipaddress.ip_network(cidr, strict=False) if cidr else None
    if cidr:
        populate_neighbor_cache(cidr, ports=ports, timeout=timeout, limit=limit)

    matches = []
    for item in read_neighbor_table():
        if item.mac != target:
            continue
        if network and ipaddress.ip_address(item.host) not in network:
            continue
        matches.append(item)
    return sorted(matches, key=lambda item: _ipv4_sort_key(item.host))


def local_ipv4_cidrs() -> list[str]:
    result = system.run_capture(["ip", "-o", "-4", "addr", "show", "scope", "global"], timeout=2.0)
    if not result.ok:
        return []

    cidrs: list[str] = []
    seen: set[str] = set()
    for line in result.stdout.splitlines():
        match = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+/\d+)", line)
        if not match:
            continue
        try:
            network = str(ipaddress.ip_interface(match.group(1)).network)
        except ValueError:
            continue
        if network not in seen:
            seen.add(network)
            cidrs.append(network)
    return cidrs


def _touch_host(host: str, ports: list[int], timeout: float) -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(b"", (host, 9))
    except OSError:
        pass

    for port in ports:
        probe_tcp(host, port, timeout=timeout)


def _token_after(tokens: list[str], name: str) -> str | None:
    if name not in tokens:
        return None
    index = tokens.index(name) + 1
    return tokens[index] if index < len(tokens) else None


def _neighbor_state(tokens: list[str]) -> str | None:
    for token in reversed(tokens):
        lowered = token.lower()
        if lowered in NEIGHBOR_STATES:
            return lowered
    return tokens[-1].lower() if tokens else None


def _ipv4_sort_key(host: str) -> int:
    try:
        return int(ipaddress.ip_address(host))
    except ValueError:
        return 0
