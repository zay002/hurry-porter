from hurry_porter.config import load_config
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

