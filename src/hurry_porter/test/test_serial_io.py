import pytest

from hurry_porter.models import DeviceDescriptor
from hurry_porter.serial_io import payload_from_hex, payload_from_text, select_serial_port, send_serial


def test_payload_from_hex_accepts_spaces_and_prefixes():
    assert payload_from_hex("0x01 02,0A") == b"\x01\x02\x0a"


def test_payload_from_hex_rejects_odd_digits():
    with pytest.raises(ValueError):
        payload_from_hex("01 2")


def test_payload_from_text_can_append_newline():
    assert payload_from_text("AT", newline=True) == b"AT\n"


def test_select_serial_port_requires_explicit_port_for_multiple_candidates():
    devices = [
        DeviceDescriptor(
            id="serial:/dev/ttyUSB0",
            kind="serial",
            locality="wsl_native",
            state="present",
            name="USB serial 0",
            stable_path="/dev/ttyUSB0",
        ),
        DeviceDescriptor(
            id="serial:/dev/ttyUSB1",
            kind="serial",
            locality="wsl_native",
            state="present",
            name="USB serial 1",
            stable_path="/dev/ttyUSB1",
        ),
    ]

    port, error = select_serial_port(devices)

    assert port is None
    assert "multiple" in error
    assert select_serial_port(devices, "/dev/ttyUSB1") == ("/dev/ttyUSB1", None)


def test_send_serial_dry_run_does_not_open_port():
    result = send_serial("/dev/does-not-exist", b"\x01\x02", dry_run=True)

    assert result.dry_run is True
    assert result.written == 0
    assert result.payload_hex == "01 02"
