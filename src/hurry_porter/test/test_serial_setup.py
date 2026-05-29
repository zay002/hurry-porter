from hurry_porter.serial_setup import scan_usbipd_state_com_ports, scan_windows_com_ports, setup_serial
from hurry_porter.system import CommandResult


def test_setup_serial_reports_common_driver_guides(monkeypatch):
    monkeypatch.setattr(
        "hurry_porter.serial_setup.system.run_capture",
        lambda args, timeout: CommandResult(args=args, returncode=0, stdout="module info", stderr=""),
    )
    monkeypatch.setattr("hurry_porter.serial_setup.scan_windows_com_ports", lambda: [])

    report = setup_serial()

    keys = {guide.key for guide in report.driver_guides}
    modules = {module.module for module in report.modules}
    assert {"wch_ch34x", "silabs_cp210x", "ftdi_vcp", "prolific_pl2303", "usb_cdc_acm"} <= keys
    assert {"ch341", "cp210x", "ftdi_sio", "pl2303", "cdc_acm", "usbserial"} <= modules


def test_scan_windows_com_ports_parses_single_port(monkeypatch):
    monkeypatch.setattr(
        "hurry_porter.serial_setup.system.powershell",
        lambda script, timeout: CommandResult(
            args=["powershell.exe"],
            returncode=0,
            stdout='{"Name":"USB-SERIAL CH340 (COM5)","DeviceID":"USB\\\\VID_1A86&PID_7523","Manufacturer":"wch.cn","Status":"OK"}',
            stderr="",
        ),
    )

    ports = scan_windows_com_ports()

    assert ports[0].name == "USB-SERIAL CH340 (COM5)"
    assert ports[0].manufacturer == "wch.cn"


def test_scan_usbipd_state_com_ports_finds_serial_description(monkeypatch):
    monkeypatch.setattr("hurry_porter.serial_setup.usbipd.find_usbipd", lambda: "usbipd.exe")
    monkeypatch.setattr(
        "hurry_porter.serial_setup.system.run_capture",
        lambda args, timeout: CommandResult(
            args=args,
            returncode=0,
            stdout="""
{
  "Devices": [
    {
      "BusId": "4-3",
      "Description": "USB-SERIAL CH340 (COM5)",
      "InstanceId": "USB\\\\VID_1A86&PID_7523"
    }
  ]
}
""",
            stderr="",
        ),
    )

    ports = scan_usbipd_state_com_ports()

    assert ports[0].name == "USB-SERIAL CH340 (COM5)"
    assert ports[0].bus_id == "4-3"
