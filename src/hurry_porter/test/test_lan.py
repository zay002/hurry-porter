from hurry_porter.lan import probe_configured


def test_probe_configured_closed_port_is_reported():
    results = probe_configured("127.0.0.1", [9], timeout=0.05)

    assert len(results) == 1
    assert results[0].host == "127.0.0.1"
    assert results[0].port == 9
    assert results[0].open is False

