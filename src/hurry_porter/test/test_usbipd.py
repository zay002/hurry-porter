from hurry_porter.usbipd import parse_list


def test_parse_usbipd_list_connected_devices():
    text = """
Connected:
BUSID  VID:PID    DEVICE                                                        STATE
1-4    1a86:7523  USB-SERIAL CH340                                             Not shared
2-1    045e:028e  Xbox 360 Controller                                          Shared

Persisted:
GUID                                  DEVICE
"""

    devices = parse_list(text)

    assert len(devices) == 2
    assert devices[0].bus_id == "1-4"
    assert devices[0].vid == "1a86"
    assert devices[0].pid == "7523"
    assert devices[0].state == "Not shared"
    assert devices[1].name == "Xbox 360 Controller"

