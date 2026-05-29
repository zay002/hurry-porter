import json

from hurry_porter.models import DeviceDescriptor
from hurry_porter.ros_export import build_exports, render_exports, sanitize_role, shell_quote


def test_build_exports_for_serial_gamepad_and_lan():
    devices = [
        DeviceDescriptor(
            id="serial:/dev/serial/by-id/usb-CH340",
            kind="serial",
            locality="wsl_native",
            state="present",
            name="USB-SERIAL CH340",
            role="base_controller",
            stable_path="/dev/serial/by-id/usb-CH340",
        ),
        DeviceDescriptor(
            id="input:js0",
            kind="gamepad",
            locality="wsl_native",
            state="present",
            name="Xbox Controller",
            role="driver gamepad",
            stable_path="/dev/input/js0",
        ),
        DeviceDescriptor(
            id="lan:192.168.1.10:arm_controller",
            kind="lan_robot",
            locality="lan",
            state="online",
            name="arm_controller",
            role="arm_controller",
            address="192.168.1.10",
            metadata={"open_ports": "502,30002"},
        ),
    ]

    exports = build_exports(devices)

    assert exports["HURRY_BASE_CONTROLLER_PORT"] == "/dev/serial/by-id/usb-CH340"
    assert exports["HURRY_DRIVER_GAMEPAD_DEV"] == "/dev/input/js0"
    assert exports["HURRY_ARM_CONTROLLER_HOST"] == "192.168.1.10"
    assert exports["HURRY_ARM_CONTROLLER_PORTS"] == "502,30002"


def test_render_exports_json_and_launch_formats_are_machine_readable():
    devices = [
        DeviceDescriptor(
            id="usbipd:1-4",
            kind="serial",
            locality="windows_host",
            state="Shared",
            name="USB-SERIAL CH340",
            role="base",
            bus_id="1-4",
        )
    ]

    json_output = json.loads(render_exports(devices, "json"))
    launch_output = render_exports(devices, "launch")

    assert json_output["exports"]["HURRY_BASE_BUSID"] == "1-4"
    assert launch_output == "base_busid:=1-4"


def test_shell_quote_and_sanitize_role_handle_spaces():
    assert sanitize_role("Driver Gamepad!") == "driver_gamepad"
    assert shell_quote("/dev/serial/by-id/usb-CH340") == "/dev/serial/by-id/usb-CH340"
    assert shell_quote("/dev/serial/by-id/usb CH340") == "'/dev/serial/by-id/usb CH340'"

