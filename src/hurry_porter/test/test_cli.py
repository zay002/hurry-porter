import json

from hurry_porter.cli import main
from hurry_porter.models import DeviceDescriptor, ScanResult
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

