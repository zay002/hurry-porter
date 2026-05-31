from hurry_porter.config import DeviceRule, HurryConfig, apply_roles
from hurry_porter.devices import (
    parse_windows_gamepads,
    scan_configured_lan,
    scan_lan_cidr,
    scan_lan_mac,
    scan_windows_gamepads,
    scan_windows_serial_ports,
)
from hurry_porter.lan import MacMatch, ProbeResult
from hurry_porter.serial_setup import WindowsComPort
from hurry_porter.system import CommandResult
from hurry_porter.models import DeviceDescriptor


def test_apply_roles_sets_role_and_auto_attach_metadata():
    device = DeviceDescriptor(
        id="usbipd:3-2",
        kind="serial",
        locality="windows_host",
        state="Shared",
        name="USB-SERIAL CH340",
        bus_id="3-2",
        vid="1a86",
        pid="7523",
    )
    config = HurryConfig(
        rules=[
            DeviceRule(
                role="base_controller",
                kind="serial",
                vid="0x1A86",
                pid="7523",
                busid_regex=r"3-\d+",
                auto_attach=True,
                preferred_transport="usbipd",
            )
        ]
    )

    apply_roles([device], config)

    assert device.role == "base_controller"
    assert device.metadata["auto_attach"] == "true"
    assert device.metadata["preferred_transport"] == "usbipd"


def test_scan_configured_lan_marks_online_and_offline(monkeypatch):
    def fake_probe(host, ports):
        if host == "192.168.1.10":
            return [
                ProbeResult(host=host, port=502, open=True, latency_ms=1.2),
                ProbeResult(host=host, port=30002, open=False, error="refused"),
            ]
        return [ProbeResult(host=host, port=502, open=False, error="timeout")]

    monkeypatch.setattr("hurry_porter.devices.probe_configured", fake_probe)
    config = HurryConfig(
        rules=[
            DeviceRule(role="arm_controller", kind="lan_robot", lan_host="192.168.1.10", lan_ports=[502, 30002]),
            DeviceRule(role="offline_arm", kind="lan_robot", lan_host="192.168.1.11", lan_ports=[502]),
        ]
    )

    devices = scan_configured_lan(config)

    assert devices[0].state == "online"
    assert devices[0].metadata["open_ports"] == "502"
    assert devices[0].transports[0].warnings == []
    assert devices[1].state == "offline"
    assert devices[1].transports[0].warnings == ["configured LAN endpoint is not reachable"]


def test_scan_configured_lan_resolves_mac_to_ip(monkeypatch):
    monkeypatch.setattr(
        "hurry_porter.devices.find_hosts_by_mac",
        lambda mac, cidr=None, ports=None: [
            MacMatch(host="192.168.1.42", mac="aa:bb:cc:dd:ee:ff", source="ip_neigh", interface="eth0", state="reachable")
        ],
    )
    monkeypatch.setattr(
        "hurry_porter.devices.probe_configured",
        lambda host, ports: [ProbeResult(host=host, port=502, open=True, latency_ms=2.5)],
    )
    config = HurryConfig(
        rules=[
            DeviceRule(
                role="arm_controller",
                kind="lan_robot",
                lan_mac="AA-BB-CC-DD-EE-FF",
                lan_cidr="192.168.1.0/24",
                lan_ports=[502],
            )
        ]
    )

    devices = scan_configured_lan(config)

    assert devices[0].address == "192.168.1.42"
    assert devices[0].state == "online"
    assert devices[0].metadata["mac"] == "aa:bb:cc:dd:ee:ff"
    assert devices[0].metadata["mac_source"] == "ip_neigh"


def test_scan_lan_cidr_converts_open_probes_to_devices(monkeypatch):
    monkeypatch.setattr(
        "hurry_porter.devices.scan_cidr",
        lambda cidr, ports: [ProbeResult(host="192.168.1.20", port=502, open=True, latency_ms=3.4)],
    )

    devices = scan_lan_cidr("192.168.1.0/24", [502])

    assert devices[0].id == "lan:192.168.1.20:502"
    assert devices[0].metadata["latency_ms"] == "3.4"
    assert devices[0].transports[0].endpoint == "192.168.1.20:502"


def test_scan_lan_mac_returns_not_found_device(monkeypatch):
    monkeypatch.setattr("hurry_porter.devices.find_hosts_by_mac", lambda mac, cidr=None, ports=None: [])

    devices = scan_lan_mac(["aa:bb:cc:dd:ee:ff"], cidr="192.168.1.0/24", ports=[502])

    assert devices[0].id == "lan-mac:aa_bb_cc_dd_ee_ff:lan_robot"
    assert devices[0].state == "not_found"
    assert devices[0].metadata["configured_cidr"] == "192.168.1.0/24"


def test_parse_windows_gamepads_prefers_named_controller():
    devices = parse_windows_gamepads(
        """
[
  {
    "Status": "OK",
    "Class": "HIDClass",
    "FriendlyName": "HID-compliant game controller",
    "InstanceId": "HID\\\\VID_057E&PID_2009"
  },
  {
    "Status": "OK",
    "Class": "Bluetooth",
    "FriendlyName": "Pro Controller",
    "InstanceId": "BTHENUM\\\\DEV_98B69DD4790A"
  },
  {
    "Status": "OK",
    "Class": "System",
    "FriendlyName": "Nefarius Virtual Gamepad Emulation Bus",
    "InstanceId": "ROOT\\\\SYSTEM\\\\0004"
  }
]
"""
    )

    assert len(devices) == 1
    assert devices[0].name == "Pro Controller"
    assert devices[0].kind == "gamepad"
    assert devices[0].locality == "windows_host"
    assert devices[0].metadata["quirk"] == "windows_pro_controller_led_unassigned"
    assert "do not re-pair" in devices[0].metadata["windows_led_note"]
    assert devices[0].transports[0].kind == "windows_input_bridge"
    assert "hurry gamepad bridge" in devices[0].transports[0].warnings[0]
    assert "do not re-pair" in devices[0].transports[0].warnings[1]


def test_scan_windows_gamepads_uses_powershell(monkeypatch):
    monkeypatch.setattr(
        "hurry_porter.devices.system.powershell",
        lambda script, timeout: CommandResult(
            args=["powershell.exe"],
            returncode=0,
            stdout='{"Status":"OK","Class":"Bluetooth","FriendlyName":"Pro Controller","InstanceId":"BTHENUM\\\\DEV_98B69DD4790A"}',
            stderr="",
        ),
    )

    devices, warnings = scan_windows_gamepads()

    assert warnings == []
    assert devices[0].name == "Pro Controller"


def test_scan_windows_serial_ports_reports_missing_bus_id(monkeypatch):
    monkeypatch.setattr(
        "hurry_porter.devices.scan_windows_com_ports",
        lambda: [
            WindowsComPort(
                name="USB-SERIAL CH340 (COM5)",
                device_id=r"USB\VID_1A86&PID_7523\6&3AD82D6F&0&3",
                manufacturer="wch.cn",
                status="present",
                bus_id=None,
            )
        ],
    )

    devices = scan_windows_serial_ports([])

    assert devices[0].kind == "serial"
    assert devices[0].vid == "1a86"
    assert devices[0].pid == "7523"
    assert devices[0].bus_id is None
    assert devices[0].transports[0].kind == "windows_com_pending"
    assert "no attachable bus id" in devices[0].transports[0].warnings[0]
