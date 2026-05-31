from hurry_porter.lan import (
    MacMatch,
    find_hosts_by_mac,
    normalize_mac,
    parse_arp_table,
    parse_ip_neigh,
    probe_configured,
)


def test_probe_configured_closed_port_is_reported():
    results = probe_configured("127.0.0.1", [9], timeout=0.05)

    assert len(results) == 1
    assert results[0].host == "127.0.0.1"
    assert results[0].port == 9
    assert results[0].open is False


def test_normalize_mac_accepts_common_formats():
    assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("aabb.ccdd.eeff") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("aabbccddeeff") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("not-a-mac") is None


def test_parse_ip_neigh_extracts_ipv4_mac_entries():
    matches = parse_ip_neigh(
        """
192.168.1.10 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE
fe80::1 dev eth0 lladdr 11:22:33:44:55:66 router STALE
192.168.1.11 dev eth0 FAILED
"""
    )

    assert matches == [
        MacMatch(host="192.168.1.10", mac="aa:bb:cc:dd:ee:ff", source="ip_neigh", interface="eth0", state="reachable")
    ]


def test_parse_arp_table_extracts_complete_entries():
    matches = parse_arp_table(
        """
IP address       HW type     Flags       HW address            Mask     Device
192.168.1.10     0x1         0x2         aa:bb:cc:dd:ee:ff     *        eth0
192.168.1.11     0x1         0x0         00:00:00:00:00:00     *        eth0
"""
    )

    assert matches == [
        MacMatch(host="192.168.1.10", mac="aa:bb:cc:dd:ee:ff", source="proc_net_arp", interface="eth0", state="0x2")
    ]


def test_find_hosts_by_mac_populates_then_filters_neighbor_table(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "hurry_porter.lan.populate_neighbor_cache",
        lambda cidr, ports, timeout, limit: calls.append((cidr, ports, timeout, limit)),
    )
    monkeypatch.setattr(
        "hurry_porter.lan.read_neighbor_table",
        lambda: [
            MacMatch(host="192.168.1.20", mac="aa:bb:cc:dd:ee:ff", source="ip_neigh"),
            MacMatch(host="10.0.0.20", mac="aa:bb:cc:dd:ee:ff", source="ip_neigh"),
        ],
    )

    matches = find_hosts_by_mac("AA:BB:CC:DD:EE:FF", cidr="192.168.1.0/24", ports=[502])

    assert calls == [("192.168.1.0/24", [502], 0.05, 512)]
    assert [match.host for match in matches] == ["192.168.1.20"]
