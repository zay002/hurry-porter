from __future__ import annotations

import importlib.resources
import json
import shlex
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import system


SCHEMA = "hurry.gamepad.v1"
DEFAULT_GAMEPAD_PORT = 47777
DEFAULT_GAMEPAD_HZ = 250
DEFAULT_FRAME_ID = "hurry_windows_gamepad"
DEFAULT_TOPIC = "/joy"
DEFAULT_AGENT_INDEX = 0


@dataclass
class RosJoyState:
    axes: list[float]
    buttons: list[int]
    frame_id: str
    source: str
    index: int
    connected: bool = True


def packet_to_joy_state(packet: dict[str, Any]) -> RosJoyState:
    axes = [_clamp_axis(value) for value in _list_value(packet.get("axes"))]
    buttons = [_button_value(value) for value in _list_value(packet.get("buttons"))]
    return RosJoyState(
        axes=axes,
        buttons=buttons,
        frame_id=str(packet.get("frame_id") or DEFAULT_FRAME_ID),
        source=str(packet.get("source") or "windows-gaming-input"),
        index=_int_value(packet.get("index"), DEFAULT_AGENT_INDEX),
        connected=bool(packet.get("connected", True)),
    )


def decode_packet_text(text: str) -> RosJoyState:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid gamepad packet JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("gamepad packet must be a JSON object")
    if payload.get("schema") not in {None, SCHEMA}:
        raise ValueError(f"unsupported gamepad packet schema: {payload.get('schema')}")
    return packet_to_joy_state(payload)


def joy_state_to_jsonable(state: RosJoyState) -> dict[str, Any]:
    return {
        "axes": state.axes,
        "buttons": state.buttons,
        "connected": state.connected,
        "frame_id": state.frame_id,
        "index": state.index,
        "source": state.source,
    }


def agent_script_path() -> Path:
    return Path(
        importlib.resources.files("hurry_porter").joinpath("windows", "hurry_gamepad_agent.ps1")
    )


def detect_wsl_target_ip() -> str:
    result = system.run_capture(["hostname", "-I"], timeout=2.0)
    if result.ok:
        for token in result.stdout.split():
            if _looks_like_ipv4(token) and not token.startswith("127."):
                return token
    return "127.0.0.1"


def wsl_to_windows_path(path: str | Path) -> str:
    text = str(path)
    result = system.run_capture(["wslpath", "-w", text], timeout=2.0)
    return result.stdout.strip() if result.ok and result.stdout.strip() else text


def build_agent_command(
    target: str,
    port: int = DEFAULT_GAMEPAD_PORT,
    hz: int = DEFAULT_GAMEPAD_HZ,
    index: int = DEFAULT_AGENT_INDEX,
    script_path: str | Path | None = None,
    powershell: str = "powershell.exe",
) -> list[str]:
    script = wsl_to_windows_path(script_path or agent_script_path())
    return [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        script,
        "-Target",
        target,
        "-Port",
        str(port),
        "-Hz",
        str(hz),
        "-Index",
        str(index),
    ]


def command_to_text(command: list[str]) -> str:
    return shlex.join(command)


def run_agent_command(command: list[str]) -> int:
    try:
        return subprocess.call(command)
    except FileNotFoundError as exc:
        print(str(exc))
        return 127


def run_ros_bridge(
    bind: str = "0.0.0.0",
    port: int = DEFAULT_GAMEPAD_PORT,
    topic: str = DEFAULT_TOPIC,
    frame_id: str = DEFAULT_FRAME_ID,
) -> int:
    import rclpy
    from rclpy.executors import ExternalShutdownException
    from rclpy.node import Node
    from sensor_msgs.msg import Joy

    class WindowsGamepadBridge(Node):
        def __init__(self) -> None:
            super().__init__("hurry_gamepad_bridge")
            self.publisher = self.create_publisher(Joy, topic, 10)
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind((bind, port))
            self.sock.setblocking(False)
            self.create_timer(0.002, self.poll)
            self.get_logger().info(f"listening for Windows gamepad packets on udp://{bind}:{port}")

        def poll(self) -> None:
            for _ in range(32):
                try:
                    payload, _addr = self.sock.recvfrom(8192)
                except BlockingIOError:
                    return
                try:
                    state = decode_packet_text(payload.decode("utf-8"))
                except ValueError as exc:
                    self.get_logger().warn(str(exc))
                    continue
                if not state.connected:
                    continue
                msg = Joy()
                msg.header.stamp = self.get_clock().now().to_msg()
                msg.header.frame_id = frame_id or state.frame_id
                msg.axes = state.axes
                msg.buttons = state.buttons
                self.publisher.publish(msg)

        def destroy_node(self) -> bool:
            self.sock.close()
            return super().destroy_node()

    rclpy.init()
    node = WindowsGamepadBridge()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except Exception as exc:
        message = str(exc)
        if "context is not valid" not in message and "rcl_shutdown" not in message:
            raise
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


def bridge_main() -> int:
    return run_ros_bridge()


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clamp_axis(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(-1.0, min(1.0, numeric))


def _button_value(value: Any) -> int:
    return 1 if bool(value) else 0


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _looks_like_ipv4(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(part) <= 255 for part in parts)
    except ValueError:
        return False
