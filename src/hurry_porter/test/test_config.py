from pathlib import Path

from hurry_porter.config import load_config, render_config_from_devices
from hurry_porter.models import DeviceDescriptor


def test_rule_matches_usb_serial_description(tmp_path):
    config_path = tmp_path / "hurry.toml"
    config_path.write_text(
        """
[[devices]]
role = "base_controller"
kind = "serial"
description_regex = "CH340|CP210"
auto_attach = true
""",
        encoding="utf-8",
    )

    config = load_config(str(config_path))
    device = DeviceDescriptor(
        id="usbipd:1-2",
        kind="serial",
        locality="windows_host",
        state="Shared",
        name="USB-SERIAL CH340",
    )

    assert config.rules[0].matches(device)
    assert config.rules[0].role == "base_controller"
    assert config.rules[0].auto_attach is True


def test_config_parses_watch_settings_and_path_regex(tmp_path):
    config_path = tmp_path / "hurry.toml"
    config_path.write_text(
        """
[watch]
interval_seconds = 0.5
auto_attach = false

[[devices]]
role = "attached_base"
kind = "serial"
path_regex = "/dev/serial/by-id/usb-Example"
""",
        encoding="utf-8",
    )

    config = load_config(str(config_path))
    device = DeviceDescriptor(
        id="serial:/dev/ttyUSB0",
        kind="serial",
        locality="wsl_native",
        state="present",
        name="Example serial",
        stable_path="/dev/serial/by-id/usb-Example",
    )

    assert config.watch.interval_seconds == 0.5
    assert config.watch.auto_attach is False
    assert config.rules[0].matches(device)


def test_example_config_loads():
    example = Path(__file__).resolve().parents[1] / "config" / "hurry.example.toml"

    config = load_config(str(example))

    assert config.watch.auto_attach is True
    assert [rule.role for rule in config.rules] == ["base_controller", "gamepad", "arm_controller"]


def test_render_config_from_devices_generates_candidate_rules():
    devices = [
        DeviceDescriptor(
            id="usbipd:3-2",
            kind="serial",
            locality="windows_host",
            state="Shared",
            name="USB-SERIAL CH340",
            bus_id="3-2",
            vid="1a86",
            pid="7523",
        ),
        DeviceDescriptor(
            id="lan:192.168.1.10:502",
            kind="lan_generic",
            locality="lan",
            state="online",
            name="192.168.1.10:502",
            address="192.168.1.10",
            metadata={"open_port": "502"},
        ),
    ]

    rendered = render_config_from_devices(devices)

    assert 'role = "base_controller"' in rendered
    assert 'vid = "1a86"' in rendered
    assert "auto_attach = true" in rendered
    assert 'lan_host = "192.168.1.10"' in rendered
    assert "lan_ports = [502]" in rendered
