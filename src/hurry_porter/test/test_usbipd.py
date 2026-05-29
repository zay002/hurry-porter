from hurry_porter.usbipd import format_bind_command, parse_list
from hurry_porter.devices import scan_windows_usb
from hurry_porter.usbipd import UsbipdDevice


def test_parse_usbipd_list_connected_devices():
    text = """
Connected:
BUSID  VID:PID    DEVICE                                                        STATE
1-4    1a86:7523  USB-SERIAL CH340                                             Not shared
2-1    045e:028e  Xbox 360 Controller                                          Shared

Persisted:
GUID                                  DEVICE
"""

    devices = parse_list(text)

    assert len(devices) == 2
    assert devices[0].bus_id == "1-4"
    assert devices[0].vid == "1a86"
    assert devices[0].pid == "7523"
    assert devices[0].state == "Not shared"
    assert devices[1].name == "Xbox 360 Controller"


def test_format_bind_command_uses_full_windows_path():
    command = format_bind_command("1-4", r"C:\Program Files\usbipd-win\usbipd.exe")

    assert "Start-Process" in command
    assert r"C:\Program Files\usbipd-win\usbipd.exe" in command
    assert "bind --busid 1-4" in command


def test_scan_windows_usb_classifies_serial_and_bind_warning(monkeypatch):
    monkeypatch.setattr(
        "hurry_porter.devices.usbipd.list_devices",
        lambda: (
            [
                UsbipdDevice(
                    bus_id="1-4",
                    vid="1a86",
                    pid="7523",
                    name="USB-SERIAL CH340",
                    state="Not shared",
                )
            ],
            [],
        ),
    )

    devices, warnings = scan_windows_usb()

    assert warnings == []
    assert devices[0].kind == "serial"
    assert devices[0].id == "usbipd:1-4"
    assert devices[0].transports[0].warnings == ["requires elevated usbipd bind before attach"]
