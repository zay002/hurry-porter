import pytest

from hurry_porter import gamepad_bridge


def test_decode_packet_maps_axes_buttons_and_clamps_values():
    state = gamepad_bridge.decode_packet_text(
        '{"schema":"hurry.gamepad.v1","source":"test","index":1,'
        '"axes":[1.5,-2,0.25],"buttons":[1,0,true,false]}'
    )

    assert state.source == "test"
    assert state.index == 1
    assert state.axes == [1.0, -1.0, 0.25]
    assert state.buttons == [1, 0, 1, 0]


def test_decode_packet_rejects_unknown_schema():
    with pytest.raises(ValueError):
        gamepad_bridge.decode_packet_text('{"schema":"other","axes":[],"buttons":[]}')


def test_build_agent_command_uses_windows_script_path(monkeypatch):
    monkeypatch.setattr(gamepad_bridge, "wsl_to_windows_path", lambda path: "C:\\hurry\\agent.ps1")

    command = gamepad_bridge.build_agent_command(target="172.20.1.2", port=48888, hz=120, index=2)

    assert command[:5] == ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    assert "C:\\hurry\\agent.ps1" in command
    assert command[-8:] == ["-Target", "172.20.1.2", "-Port", "48888", "-Hz", "120", "-Index", "2"]
