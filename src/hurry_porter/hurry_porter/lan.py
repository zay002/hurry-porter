from __future__ import annotations

import ipaddress
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass


@dataclass
class ProbeResult:
    host: str
    port: int
    open: bool
    latency_ms: float | None = None
    error: str | None = None


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

