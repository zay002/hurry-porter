from pathlib import Path

from hurry_porter.system import CommandResult
from hurry_porter.usbipd import format_bind_command, list_devices, parse_list, service_status, warning_lines
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


def test_parse_usbipd_list_mixed_fixture():
    fixture = Path(__file__).parent / "fixtures" / "usbipd_list_mixed.txt"

    devices = parse_list(fixture.read_text(encoding="utf-8"))

    assert [device.bus_id for device in devices] == ["2-4", "3-2", "4-1"]
    assert devices[0].name.startswith("G703 LIGHTSPEED")
    assert devices[0].state == "Not shared"
    assert devices[1].state == "Shared"
    assert devices[2].state == "Attached"


def test_format_bind_command_uses_full_windows_path():
    command = format_bind_command("1-4", r"C:\Program Files\usbipd-win\usbipd.exe")

    assert "Start-Process" in command
    assert r"C:\Program Files\usbipd-win\usbipd.exe" in command
    assert "bind --busid 1-4" in command


def test_warning_lines_keep_successful_usbipd_warnings():
    warnings = warning_lines("usbipd: warning: The service is currently not running; a reboot should fix that.\n")

    assert warnings == ["usbipd: warning: The service is currently not running; a reboot should fix that."]


def test_list_devices_preserves_warnings_from_stderr(monkeypatch):
    monkeypatch.setattr("hurry_porter.usbipd.find_usbipd", lambda: "usbipd.exe")
    monkeypatch.setattr(
        "hurry_porter.usbipd.system.run_capture",
        lambda args, timeout: CommandResult(
            args=args,
            returncode=0,
            stdout="""
Connected:
BUSID  VID:PID    DEVICE                                                        STATE
4-3    1a86:7523  USB-SERIAL CH340                                             Shared
""",
            stderr="usbipd: warning: The service is currently not running; a reboot should fix that.\n",
        ),
    )

    devices, warnings = list_devices()

    assert devices[0].bus_id == "4-3"
    assert warnings == ["usbipd: warning: The service is currently not running; a reboot should fix that."]


def test_service_status_reports_stopped_service(monkeypatch):
    monkeypatch.setattr(
        "hurry_porter.usbipd.system.powershell",
        lambda script, timeout: CommandResult(
            args=["powershell.exe"],
            returncode=0,
            stdout='{"Name":"usbipd","State":"Stopped","StartMode":"Auto","ExitCode":1067,"ServiceSpecificExitCode":0}',
            stderr="",
        ),
    )

    status = service_status()

    assert status.installed is True
    assert status.running is False
    assert status.state == "Stopped"
    assert status.exit_code == 1067


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
