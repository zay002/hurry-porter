from __future__ import annotations

import os
import re
import select
import termios
import time
from dataclasses import dataclass

from .devices import scan_wsl_serial
from .models import DeviceDescriptor


BAUD_RATES = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
    230400: termios.B230400,
    460800: termios.B460800,
    921600: termios.B921600,
}


@dataclass
class SerialSendResult:
    port: str
    baud: int
    dry_run: bool
    written: int
    payload_hex: str
    response_hex: str = ""
    response_text: str = ""


class SerialIoError(ValueError):
    pass


def select_serial_port(candidates: list[DeviceDescriptor], requested: str | None = None) -> tuple[str | None, str | None]:
    if requested:
        return requested, None
    ports = [device.stable_path for device in candidates if device.stable_path]
    if not ports:
        return None, "no WSL serial devices found; attach the USB serial/CAN adapter first"
    if len(ports) > 1:
        return None, "multiple WSL serial devices found; pass --port explicitly"
    return ports[0], None


def current_serial_candidates() -> list[DeviceDescriptor]:
    return scan_wsl_serial()


def payload_from_hex(value: str) -> bytes:
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", value.replace("0x", "").replace("0X", ""))
    if not cleaned:
        raise SerialIoError("empty hex payload")
    if len(cleaned) % 2:
        raise SerialIoError("hex payload must contain an even number of digits")
    return bytes.fromhex(cleaned)


def payload_from_text(value: str, newline: bool = False) -> bytes:
    text = value + ("\n" if newline else "")
    return text.encode("utf-8")


def send_serial(
    port: str,
    payload: bytes,
    baud: int = 115200,
    read_timeout: float = 0.2,
    read_bytes: int = 4096,
    dry_run: bool = False,
) -> SerialSendResult:
    if baud not in BAUD_RATES:
        supported = ", ".join(str(item) for item in sorted(BAUD_RATES))
        raise SerialIoError(f"unsupported baud rate {baud}; supported values: {supported}")
    if not payload:
        raise SerialIoError("empty payload")

    if dry_run:
        return SerialSendResult(port=port, baud=baud, dry_run=True, written=0, payload_hex=payload.hex(" "))

    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        configure_serial(fd, baud)
        termios.tcflush(fd, termios.TCIOFLUSH)
        written = write_all(fd, payload)
        response = read_response(fd, read_timeout, read_bytes)
    finally:
        os.close(fd)

    return SerialSendResult(
        port=port,
        baud=baud,
        dry_run=False,
        written=written,
        payload_hex=payload.hex(" "),
        response_hex=response.hex(" "),
        response_text=response.decode("utf-8", errors="replace"),
    )


def configure_serial(fd: int, baud: int) -> None:
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] &= ~(termios.PARENB | termios.CSTOPB | termios.CSIZE)
    if hasattr(termios, "CRTSCTS"):
        attrs[2] &= ~termios.CRTSCTS
    attrs[2] |= termios.CS8 | termios.CREAD | termios.CLOCAL
    attrs[3] = 0
    attrs[4] = BAUD_RATES[baud]
    attrs[5] = BAUD_RATES[baud]
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def write_all(fd: int, payload: bytes) -> int:
    written = 0
    while written < len(payload):
        try:
            written += os.write(fd, payload[written:])
        except BlockingIOError:
            time.sleep(0.005)
    termios.tcdrain(fd)
    return written


def read_response(fd: int, timeout: float, max_bytes: int) -> bytes:
    if timeout <= 0 or max_bytes <= 0:
        return b""
    deadline = time.monotonic() + timeout
    chunks: list[bytes] = []
    remaining = max_bytes
    while remaining > 0:
        wait = deadline - time.monotonic()
        if wait <= 0:
            break
        readable, _, _ = select.select([fd], [], [], wait)
        if not readable:
            break
        try:
            chunk = os.read(fd, remaining)
        except BlockingIOError:
            continue
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
