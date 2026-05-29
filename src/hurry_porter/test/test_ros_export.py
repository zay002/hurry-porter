import json

from hurry_porter.models import DeviceDescriptor
from hurry_porter.ros_export import build_exports, exportable_devices, render_exports, sanitize_role, shell_quote


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


def test_exportable_devices_prefers_attached_wsl_runtime_device():
    devices = [
        DeviceDescriptor(
            id="usbipd:4-3",
            kind="serial",
            locality="windows_host",
            state="Attached",
            name="USB-SERIAL CH340",
            bus_id="4-3",
            vid="1a86",
            pid="7523",
        ),
        DeviceDescriptor(
            id="serial:/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0",
            kind="serial",
            locality="wsl_native",
            state="present",
            name="CH340 serial converter",
            stable_path="/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0",
            vid="1a86",
            pid="7523",
        ),
        DeviceDescriptor(
            id="usbipd:2-8",
            kind="usb",
            locality="windows_host",
            state="Not shared",
            name="Internal webcam",
            bus_id="2-8",
            vid="13d3",
            pid="56eb",
        ),
    ]

    selected = exportable_devices(devices)
    exports = build_exports(devices)

    assert [device.id for device in selected] == ["serial:/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"]
    assert exports == {"HURRY_SERIAL_PORT": "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"}


def test_render_params_file_uses_ros2_wildcard_schema():
    devices = [
        DeviceDescriptor(
            id="serial:/dev/ttyUSB0",
            kind="serial",
            locality="wsl_native",
            state="present",
            name="USB serial",
            role="base_controller",
            stable_path="/dev/ttyUSB0",
        ),
        DeviceDescriptor(
            id="lan:192.168.1.10:arm",
            kind="lan_robot",
            locality="lan",
            state="online",
            name="arm",
            role="arm_controller",
            address="192.168.1.10",
            metadata={"open_ports": "502,30002"},
        ),
    ]

    output = render_exports(devices, "params")

    assert output.startswith("/**:\n  ros__parameters:\n")
    assert '    base_controller_port: "/dev/ttyUSB0"' in output
    assert '    arm_controller_host: "192.168.1.10"' in output
    assert "    arm_controller_ports:\n      - 502\n      - 30002" in output


def test_render_launch_file_declares_args_and_env_vars():
    devices = [
        DeviceDescriptor(
            id="input:js0",
            kind="gamepad",
            locality="wsl_native",
            state="present",
            name="Xbox Controller",
            role="driver gamepad",
            stable_path="/dev/input/js0",
        )
    ]

    output = render_exports(devices, "launch-file")

    assert "from launch.actions import DeclareLaunchArgument, SetEnvironmentVariable" in output
    assert "from launch.substitutions import LaunchConfiguration" in output
    assert 'DeclareLaunchArgument(\n            "driver_gamepad_dev",' in output
    assert 'name="HURRY_DRIVER_GAMEPAD_DEV"' in output
    assert 'value=LaunchConfiguration("driver_gamepad_dev")' in output


def test_shell_quote_and_sanitize_role_handle_spaces():
    assert sanitize_role("Driver Gamepad!") == "driver_gamepad"
    assert shell_quote("/dev/serial/by-id/usb-CH340") == "/dev/serial/by-id/usb-CH340"
    assert shell_quote("/dev/serial/by-id/usb CH340") == "'/dev/serial/by-id/usb CH340'"
