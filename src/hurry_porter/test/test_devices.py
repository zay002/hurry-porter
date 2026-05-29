from hurry_porter.config import DeviceRule, HurryConfig, apply_roles
from hurry_porter.devices import scan_configured_lan, scan_lan_cidr
from hurry_porter.lan import ProbeResult
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


def test_scan_lan_cidr_converts_open_probes_to_devices(monkeypatch):
    monkeypatch.setattr(
        "hurry_porter.devices.scan_cidr",
        lambda cidr, ports: [ProbeResult(host="192.168.1.20", port=502, open=True, latency_ms=3.4)],
    )

    devices = scan_lan_cidr("192.168.1.0/24", [502])

    assert devices[0].id == "lan:192.168.1.20:502"
    assert devices[0].metadata["latency_ms"] == "3.4"
    assert devices[0].transports[0].endpoint == "192.168.1.20:502"

