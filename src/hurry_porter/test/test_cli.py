import json

from hurry_porter.cli import main
from hurry_porter.models import DeviceDescriptor, ScanResult, TransportCandidate
from hurry_porter.serial_setup import KernelModuleStatus, SerialDriverGuide, SerialSetupReport, WindowsComPort
from hurry_porter.windows_setup import SetupResult


def test_setup_usbipd_json_reports_existing_install(monkeypatch, capsys):
    monkeypatch.setattr(
        "hurry_porter.cli.setup_usbipd",
        lambda run: SetupResult(
            component="usbipd-win",
            installed=True,
            command="winget install --interactive --exact dorssel.usbipd-win",
            ran=run,
            ok=True,
            stdout="usbipd-win is already available",
        ),
    )

    exit_code = main(["setup", "usbipd", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["installed"] is True
    assert output["component"] == "usbipd-win"


def test_setup_serial_json_reports_modules_and_driver_guides(monkeypatch, capsys):
    monkeypatch.setattr(
        "hurry_porter.cli.setup_serial",
        lambda: SerialSetupReport(
            modules=[KernelModuleStatus(module="ch341", ok=True)],
            windows_com_ports=[WindowsComPort(name="USB-SERIAL CH340 (COM5)", manufacturer="wch.cn", status="OK")],
            driver_guides=[
                SerialDriverGuide(
                    key="wch_ch34x",
                    name="WCH CH340/CH341",
                    chips=["CH340"],
                    linux_module="ch341",
                    windows_driver_url="https://www.wch-ic.com/downloads/CH341SER_EXE.html",
                )
            ],
            hints=["attach through usbipd-win"],
        ),
    )

    exit_code = main(["setup", "serial", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["modules"][0]["module"] == "ch341"
    assert output["windows_com_ports"][0]["name"] == "USB-SERIAL CH340 (COM5)"
    assert output["driver_guides"][0]["key"] == "wch_ch34x"


def test_attach_not_shared_device_reports_elevated_bind(monkeypatch, capsys):
    device = DeviceDescriptor(
        id="usbipd:1-4",
        kind="serial",
        locality="windows_host",
        state="Not shared",
        name="USB-SERIAL CH340",
        bus_id="1-4",
    )
    monkeypatch.setattr(
        "hurry_porter.cli.scan_devices",
        lambda config: ScanResult(devices=[device], warnings=[]),
    )
    monkeypatch.setattr(
        "hurry_porter.cli.bind_command",
        lambda bus_id: f"Start-Process usbipd bind --busid {bus_id}",
    )

    exit_code = main(["attach", "1-4", "--dry-run", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["results"][0]["bind_required"] is True
    assert "bind --busid 1-4" in output["results"][0]["bind_command"]


def test_attach_missing_device_is_actionable(monkeypatch, capsys):
    monkeypatch.setattr(
        "hurry_porter.cli.scan_devices",
        lambda config: ScanResult(devices=[], warnings=[]),
    )

    exit_code = main(["attach", "missing", "--dry-run", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["error"] == "no matching usbipd devices found"
    assert "hurry scan" in output["hint"]


def test_serial_send_json_dry_run_uses_single_serial_candidate(monkeypatch, capsys):
    monkeypatch.setattr(
        "hurry_porter.cli.current_serial_candidates",
        lambda: [
            DeviceDescriptor(
                id="serial:/dev/serial/by-id/usb-can",
                kind="serial",
                locality="wsl_native",
                state="present",
                name="USB-CAN",
                stable_path="/dev/serial/by-id/usb-can",
            )
        ],
    )

    exit_code = main(["serial", "send", "--hex", "01 02 0A", "--dry-run", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["ok"] is True
    assert output["dry_run"] is True
    assert output["port"] == "/dev/serial/by-id/usb-can"
    assert output["payload_hex"] == "01 02 0a"


def test_gamepad_status_json_reports_native_usb_and_bluetooth_routes(monkeypatch, capsys):
    monkeypatch.setattr(
        "hurry_porter.cli.scan_devices",
        lambda config: ScanResult(
            devices=[
                DeviceDescriptor(
                    id="input:js0",
                    kind="gamepad",
                    locality="wsl_native",
                    state="present",
                    name="USB Gamepad",
                    stable_path="/dev/input/js0",
                ),
                DeviceDescriptor(
                    id="usbipd:4-1",
                    kind="gamepad",
                    locality="windows_host",
                    state="Shared",
                    name="Xbox Controller",
                    bus_id="4-1",
                ),
                DeviceDescriptor(
                    id="windows-gamepad:pro",
                    kind="gamepad",
                    locality="windows_host",
                    state="OK",
                    name="Pro Controller",
                    transports=[
                        TransportCandidate(
                            kind="windows_input_bridge",
                            endpoint="BTHENUM\\DEV_01",
                            priority=30,
                            latency_class="bridge_planned",
                        )
                    ],
                ),
            ],
            warnings=[],
        ),
    )

    exit_code = main(["gamepad", "status", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert [item["route"] for item in output["gamepads"]] == [
        "wsl_native",
        "usbipd_attach",
        "windows_input_bridge",
    ]
    assert output["gamepads"][0]["path"] == "/dev/input/js0"
    assert output["gamepads"][1]["bus_id"] == "4-1"
    assert "v2 bridge" in output["gamepads"][2]["action"]


def test_waveshare_can_a_send_dry_run_encodes_config_and_extended_frame(capsys):
    exit_code = main(
        [
            "waveshare-can-a",
            "send",
            "--port",
            "/dev/null",
            "--dry-run",
            "--json",
            "--protocol",
            "variable",
            "--can-bitrate",
            "1000000",
            "--frame-type",
            "extended",
            "--id",
            "0x1234567",
            "--data",
            "11 22",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["dry_run"] is True
    assert output["baud"] == 2000000
    assert output["payload_hex"].endswith("aa e2 67 45 23 01 11 22 55")


def test_waveshare_can_a_decode_json(capsys):
    exit_code = main(
        [
            "waveshare-can-a",
            "decode",
            "--json",
            "--hex",
            "aa c2 03 01 11 22 55",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["frames"][0]["id"] == "0x103"
    assert output["frames"][0]["frame_type"] == "standard"
    assert output["frames"][0]["data"] == "11 22"


def test_scan_json_prints_devices_and_warnings(monkeypatch, capsys):
    monkeypatch.setattr(
        "hurry_porter.cli.scan_devices",
        lambda config, lan_cidr=None, lan_ports=None: ScanResult(
            devices=[
                DeviceDescriptor(
                    id="usbipd:3-2",
                    kind="serial",
                    locality="windows_host",
                    state="Shared",
                    name="USB-SERIAL CH340",
                    bus_id="3-2",
                )
            ],
            warnings=["sample warning"],
        ),
    )

    exit_code = main(["scan", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["devices"][0]["id"] == "usbipd:3-2"
    assert output["warnings"] == ["sample warning"]


def test_ros_export_json_uses_discovered_devices(monkeypatch, capsys):
    monkeypatch.setattr(
        "hurry_porter.cli.scan_devices",
        lambda config: ScanResult(
            devices=[
                DeviceDescriptor(
                    id="serial:/dev/ttyUSB0",
                    kind="serial",
                    locality="wsl_native",
                    state="present",
                    name="USB serial",
                    role="base_controller",
                    stable_path="/dev/ttyUSB0",
                )
            ],
            warnings=[],
        ),
    )

    exit_code = main(["ros", "export", "--format", "json"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["exports"]["HURRY_BASE_CONTROLLER_PORT"] == "/dev/ttyUSB0"


def test_ros_export_writes_params_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        "hurry_porter.cli.scan_devices",
        lambda config: ScanResult(
            devices=[
                DeviceDescriptor(
                    id="serial:/dev/ttyUSB0",
                    kind="serial",
                    locality="wsl_native",
                    state="present",
                    name="USB serial",
                    role="base_controller",
                    stable_path="/dev/ttyUSB0",
                )
            ],
            warnings=[],
        ),
    )
    target = tmp_path / "config" / "hurry.generated.yaml"

    exit_code = main(["ros", "export", "--format", "params", "--output", str(target)])

    assert exit_code == 0
    assert "wrote" in capsys.readouterr().out
    assert 'base_controller_port: "/dev/ttyUSB0"' in target.read_text(encoding="utf-8")


def test_init_prints_default_config(capsys):
    exit_code = main(["init", "--print"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "[watch]" in output
    assert 'role = "base_controller"' in output


def test_init_from_scan_writes_candidate_config(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "hurry_porter.cli.scan_devices",
        lambda config, lan_cidr=None, lan_ports=None: ScanResult(
            devices=[
                DeviceDescriptor(
                    id="usbipd:3-2",
                    kind="serial",
                    locality="windows_host",
                    state="Shared",
                    name="USB-SERIAL CH340",
                    bus_id="3-2",
                    vid="1a86",
                    pid="7523",
                )
            ],
            warnings=[],
        ),
    )
    target = tmp_path / "hurry.toml"

    exit_code = main(["init", str(target), "--from-scan"])

    assert exit_code == 0
    assert 'role = "base_controller"' in target.read_text(encoding="utf-8")


def test_watch_once_json_dry_runs_auto_attach(monkeypatch, capsys):
    device = DeviceDescriptor(
        id="usbipd:3-2",
        kind="serial",
        locality="windows_host",
        state="Shared",
        name="USB-SERIAL CH340",
        bus_id="3-2",
        role="base_controller",
        metadata={"auto_attach": "true"},
    )
    monkeypatch.setattr(
        "hurry_porter.cli.scan_devices",
        lambda config: ScanResult(devices=[device], warnings=[]),
    )

    exit_code = main(["watch", "--once", "--dry-run", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["devices"][0]["id"] == "usbipd:3-2"
    assert output["attach_results"][0]["ok"] is True
    assert "attach --wsl --busid 3-2" in output["attach_results"][0]["attach_command"]
