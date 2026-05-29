from __future__ import annotations

import os
import time
from dataclasses import dataclass

from .serial_io import configure_serial, payload_from_hex, read_response, write_all


CAN_BITRATE_CODES = {
    1000000: 0x01,
    800000: 0x02,
    500000: 0x03,
    400000: 0x04,
    250000: 0x05,
    200000: 0x06,
    125000: 0x07,
    100000: 0x08,
    50000: 0x09,
    20000: 0x0A,
    10000: 0x0B,
    5000: 0x0C,
}

FRAME_TYPE_CODES = {"standard": 0x01, "extended": 0x02}
FRAME_FORMAT_CODES = {"data": 0x01, "remote": 0x02}
MODE_CODES = {
    "normal": 0x00,
    "silent": 0x01,
    "loopback": 0x02,
    "silent_loopback": 0x03,
}
PROTOCOL_CONFIG_CODES = {"fixed": 0x02, "variable": 0x12}

MAX_STANDARD_ID = 0x7FF
MAX_EXTENDED_ID = 0x1FFFFFFF
DEFAULT_USB_BAUD = 2_000_000


@dataclass
class CanFrame:
    can_id: int
    data: bytes
    frame_type: str = "standard"
    frame_format: str = "data"
    dlc: int | None = None

    @property
    def effective_dlc(self) -> int:
        return len(self.data) if self.dlc is None else self.dlc


@dataclass
class DecodedFrame:
    can_id: int
    data: bytes
    frame_type: str
    frame_format: str
    dlc: int
    raw: bytes
    checksum_ok: bool | None = None


@dataclass
class WaveshareCanResult:
    port: str
    baud: int
    dry_run: bool
    written: int
    payload_hex: str
    response_hex: str = ""
    decoded_frames: list[DecodedFrame] | None = None


class WaveshareCanError(ValueError):
    pass


def encode_config(
    can_bitrate: int,
    frame_type: str = "standard",
    protocol: str = "variable",
    mode: str = "normal",
    filter_id: int = 0,
    mask_id: int = 0,
    auto_retransmit: bool = True,
) -> bytes:
    bitrate_code = _lookup(CAN_BITRATE_CODES, can_bitrate, "CAN bitrate")
    frame_type_code = _lookup(FRAME_TYPE_CODES, frame_type, "frame type")
    protocol_code = _lookup(PROTOCOL_CONFIG_CODES, protocol, "protocol")
    mode_code = _lookup(MODE_CODES, mode, "mode")
    max_id = max_id_for(frame_type)
    if not 0 <= filter_id <= max_id:
        raise WaveshareCanError(f"filter id out of range for {frame_type}: 0x{filter_id:x}")
    if not 0 <= mask_id <= max_id:
        raise WaveshareCanError(f"mask id out of range for {frame_type}: 0x{mask_id:x}")

    payload = bytearray(
        [
            0xAA,
            0x55,
            protocol_code,
            bitrate_code,
            frame_type_code,
            *filter_id.to_bytes(4, "big"),
            *mask_id.to_bytes(4, "big"),
            mode_code,
            0x00 if auto_retransmit else 0x01,
            0x00,
            0x00,
            0x00,
            0x00,
        ]
    )
    payload.append(checksum(payload[2:19]))
    return bytes(payload)


def encode_frame(frame: CanFrame, protocol: str = "variable") -> bytes:
    validate_frame(frame)
    if protocol == "variable":
        return encode_variable_frame(frame)
    if protocol == "fixed":
        return encode_fixed_frame(frame)
    raise WaveshareCanError(f"unsupported protocol: {protocol}")


def encode_variable_frame(frame: CanFrame) -> bytes:
    dlc = frame.effective_dlc
    frame_type_bit = 0x20 if frame.frame_type == "extended" else 0x00
    frame_format_bit = 0x10 if frame.frame_format == "remote" else 0x00
    type_byte = 0xC0 | frame_type_bit | frame_format_bit | dlc
    id_length = 4 if frame.frame_type == "extended" else 2
    data = b"" if frame.frame_format == "remote" else frame.data
    return bytes([0xAA, type_byte]) + frame.can_id.to_bytes(id_length, "little") + data + bytes([0x55])


def encode_fixed_frame(frame: CanFrame) -> bytes:
    dlc = frame.effective_dlc
    data = b"" if frame.frame_format == "remote" else frame.data
    payload = bytearray(
        [
            0xAA,
            0x55,
            0x01,
            FRAME_TYPE_CODES[frame.frame_type],
            FRAME_FORMAT_CODES[frame.frame_format],
            *frame.can_id.to_bytes(4, "little"),
            dlc,
        ]
    )
    payload.extend(data.ljust(8, b"\x00"))
    payload.append(0x00)
    payload.append(checksum(payload[2:19]))
    return bytes(payload)


def decode_variable_frames(stream: bytes) -> list[DecodedFrame]:
    frames: list[DecodedFrame] = []
    index = 0
    while index < len(stream):
        try:
            start = stream.index(0xAA, index)
        except ValueError:
            break
        if start + 3 >= len(stream):
            break
        type_byte = stream[start + 1]
        if type_byte & 0xC0 != 0xC0:
            index = start + 1
            continue
        frame_type = "extended" if type_byte & 0x20 else "standard"
        frame_format = "remote" if type_byte & 0x10 else "data"
        dlc = type_byte & 0x0F
        id_length = 4 if frame_type == "extended" else 2
        data_length = 0 if frame_format == "remote" else dlc
        end = start + 2 + id_length + data_length
        if end >= len(stream):
            break
        if stream[end] != 0x55:
            index = start + 1
            continue
        can_id = int.from_bytes(stream[start + 2 : start + 2 + id_length], "little")
        data_start = start + 2 + id_length
        data = stream[data_start : data_start + data_length]
        frames.append(DecodedFrame(can_id, data, frame_type, frame_format, dlc, stream[start : end + 1]))
        index = end + 1
    return frames


def decode_fixed_frames(stream: bytes) -> list[DecodedFrame]:
    frames: list[DecodedFrame] = []
    index = 0
    while index + 20 <= len(stream):
        if stream[index : index + 2] != b"\xAA\x55":
            index += 1
            continue
        raw = stream[index : index + 20]
        frame_type = _reverse_lookup(FRAME_TYPE_CODES, raw[3], default="unknown")
        frame_format = _reverse_lookup(FRAME_FORMAT_CODES, raw[4], default="unknown")
        dlc = raw[9] & 0x0F
        can_id = int.from_bytes(raw[5:9], "little")
        data = raw[10 : 10 + min(dlc, 8)] if frame_format != "remote" else b""
        frames.append(
            DecodedFrame(
                can_id=can_id,
                data=data,
                frame_type=frame_type,
                frame_format=frame_format,
                dlc=dlc,
                raw=raw,
                checksum_ok=checksum(raw[2:19]) == raw[19],
            )
        )
        index += 20
    return frames


def run_transaction(
    port: str,
    payloads: list[bytes],
    baud: int = DEFAULT_USB_BAUD,
    read_timeout: float = 0.2,
    read_bytes: int = 4096,
    protocol: str = "variable",
    dry_run: bool = False,
) -> WaveshareCanResult:
    payload = b"".join(payloads)
    if dry_run:
        return WaveshareCanResult(
            port=port,
            baud=baud,
            dry_run=True,
            written=0,
            payload_hex=payload.hex(" "),
            decoded_frames=[],
        )

    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        configure_serial(fd, baud)
        written = write_all(fd, payload)
        response = read_response(fd, read_timeout, read_bytes)
    finally:
        os.close(fd)

    decoded = decode_frames(response, protocol)
    return WaveshareCanResult(
        port=port,
        baud=baud,
        dry_run=False,
        written=written,
        payload_hex=payload.hex(" "),
        response_hex=response.hex(" "),
        decoded_frames=decoded,
    )


def read_frames(
    port: str,
    baud: int = DEFAULT_USB_BAUD,
    duration: float = 2.0,
    read_bytes: int = 4096,
    protocol: str = "variable",
) -> WaveshareCanResult:
    deadline = time.monotonic() + duration
    chunks: list[bytes] = []
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        configure_serial(fd, baud)
        while time.monotonic() < deadline:
            chunks.append(read_response(fd, min(0.2, max(0.0, deadline - time.monotonic())), read_bytes))
    finally:
        os.close(fd)
    response = b"".join(chunk for chunk in chunks if chunk)
    return WaveshareCanResult(
        port=port,
        baud=baud,
        dry_run=False,
        written=0,
        payload_hex="",
        response_hex=response.hex(" "),
        decoded_frames=decode_frames(response, protocol),
    )


def decode_frames(stream: bytes, protocol: str) -> list[DecodedFrame]:
    if protocol == "variable":
        return decode_variable_frames(stream)
    if protocol == "fixed":
        return decode_fixed_frames(stream)
    raise WaveshareCanError(f"unsupported protocol: {protocol}")


def frame_to_json(frame: DecodedFrame) -> dict[str, object]:
    return {
        "id": f"0x{frame.can_id:x}",
        "frame_type": frame.frame_type,
        "frame_format": frame.frame_format,
        "dlc": frame.dlc,
        "data": frame.data.hex(" "),
        "raw": frame.raw.hex(" "),
        "checksum_ok": frame.checksum_ok,
    }


def parse_can_id(value: str | int) -> int:
    if isinstance(value, int):
        return value
    return int(str(value), 0)


def validate_frame(frame: CanFrame) -> None:
    if frame.frame_type not in FRAME_TYPE_CODES:
        raise WaveshareCanError(f"unsupported frame type: {frame.frame_type}")
    if frame.frame_format not in FRAME_FORMAT_CODES:
        raise WaveshareCanError(f"unsupported frame format: {frame.frame_format}")
    max_id = max_id_for(frame.frame_type)
    if not 0 <= frame.can_id <= max_id:
        raise WaveshareCanError(f"CAN id 0x{frame.can_id:x} out of range for {frame.frame_type}")
    if len(frame.data) > 8:
        raise WaveshareCanError("CAN2.0 data length must be <= 8 bytes")
    if not 0 <= frame.effective_dlc <= 8:
        raise WaveshareCanError("CAN2.0 DLC must be in range 0..8")
    if frame.frame_format == "data" and frame.dlc is not None and frame.dlc != len(frame.data):
        raise WaveshareCanError("data frame DLC must match data byte length")


def max_id_for(frame_type: str) -> int:
    if frame_type == "standard":
        return MAX_STANDARD_ID
    if frame_type == "extended":
        return MAX_EXTENDED_ID
    raise WaveshareCanError(f"unsupported frame type: {frame_type}")


def checksum(data: bytes | bytearray) -> int:
    return sum(data) & 0xFF


def _lookup(mapping: dict, key, label: str) -> int:
    try:
        return mapping[key]
    except KeyError as exc:
        valid = ", ".join(str(item) for item in mapping)
        raise WaveshareCanError(f"unsupported {label}: {key}; supported: {valid}") from exc


def _reverse_lookup(mapping: dict[str, int], value: int, default: str) -> str:
    for key, item in mapping.items():
        if item == value:
            return key
    return default
