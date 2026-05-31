from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from .config import load_config, render_config_from_devices, render_default_config
from .devices import scan_devices, scan_lan_cidr, scan_lan_mac
from .doctor import collect_doctor_report
from .gamepad_bridge import (
    DEFAULT_AGENT_INDEX,
    DEFAULT_FRAME_ID,
    DEFAULT_GAMEPAD_HZ,
    DEFAULT_GAMEPAD_PORT,
    DEFAULT_TOPIC,
    build_agent_command,
    command_to_text,
    decode_packet_text,
    detect_wsl_target_ip,
    joy_state_to_jsonable,
    run_agent_command,
    run_ros_bridge,
)
from .models import DeviceDescriptor, ScanResult, to_jsonable
from .ros_export import render_exports
from .serial_io import (
    SerialIoError,
    current_serial_candidates,
    payload_from_hex,
    payload_from_text,
    select_serial_port,
    send_serial,
)
from .serial_setup import setup_serial
from .usbipd import attach as usbipd_attach
from .usbipd import bind_command, bind_elevated
from .waveshare_can_a import (
    DEFAULT_USB_BAUD,
    CanFrame,
    WaveshareCanError,
    decode_frames,
    encode_config,
    encode_frame,
    frame_to_json,
    parse_can_id,
    read_frames,
    run_transaction,
)
from .windows_setup import setup_usbipd


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 2
    try:
        return args.handler(args)
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hurry", description="ROS2-on-WSL2 hardware porter")
    parser.add_argument("--config", help="Path to hurry.toml")
    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init", help="Create a starter hurry.toml")
    init.add_argument("--config", help="Path to hurry.toml used while scanning")
    init.add_argument("path", nargs="?", default="hurry.toml", help="Output path, default: hurry.toml")
    init.add_argument("--force", action="store_true", help="Overwrite the output path if it exists")
    init.add_argument("--from-scan", action="store_true", help="Generate candidate rules from currently discovered devices")
    init.add_argument("--print", dest="print_config", action="store_true", help="Print config content instead of writing a file")
    init.add_argument("--json", action="store_true", help="Print machine-readable init output")
    init.add_argument("--lan-cidr", help="Optional CIDR to actively probe with --from-scan")
    init.add_argument("--lan-ports", default="", help="Comma-separated ports used with --lan-cidr")
    init.add_argument("--lan-mac", action="append", default=[], help="MAC address to resolve while scanning LAN")
    init.set_defaults(handler=cmd_init)

    doctor = sub.add_parser("doctor", help="Check WSL2, ROS, usbipd-win, and device prerequisites")
    doctor.add_argument("--config", help="Path to hurry.toml")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable diagnostics")
    doctor.set_defaults(handler=cmd_doctor)

    scan = sub.add_parser("scan", help="Discover Windows USB/COM, WSL serial/input, and configured LAN devices")
    scan.add_argument("--config", help="Path to hurry.toml")
    scan.add_argument("--json", action="store_true", help="Print machine-readable scan output")
    scan.add_argument("--lan-cidr", help="Optional CIDR to actively probe, e.g. 192.168.1.0/24")
    scan.add_argument("--lan-ports", default="", help="Comma-separated ports used with --lan-cidr")
    scan.add_argument("--lan-mac", action="append", default=[], help="MAC address to resolve, repeat or comma-separate")
    scan.set_defaults(handler=cmd_scan)

    lan = sub.add_parser("lan", help="LAN robot discovery helpers")
    lan_sub = lan.add_subparsers(dest="lan_command")
    lan_scan = lan_sub.add_parser("scan", help="Find LAN devices by TCP ports and/or MAC address")
    lan_scan.add_argument("--cidr", help="CIDR to scan, e.g. 192.168.1.0/24")
    lan_scan.add_argument("--ports", default="", help="Comma-separated TCP ports to probe")
    lan_scan.add_argument("--mac", action="append", default=[], help="MAC address to resolve, repeat or comma-separate")
    lan_scan.add_argument("--json", action="store_true", help="Print machine-readable LAN scan output")
    lan_scan.set_defaults(handler=cmd_lan_scan)

    attach = sub.add_parser("attach", help="Attach USB devices to WSL through usbipd-win")
    attach.add_argument("--config", help="Path to hurry.toml")
    attach.add_argument("target", nargs="?", help="Role, bus id, or device id")
    attach.add_argument("--all", action="store_true", help="Attach devices marked auto_attach=true in hurry.toml")
    attach.add_argument("--dry-run", action="store_true", help="Print commands without running them")
    attach.add_argument("--elevate", action="store_true", help="Run elevated usbipd bind when required")
    attach.add_argument("--json", action="store_true", help="Print machine-readable attach results")
    attach.set_defaults(handler=cmd_attach)

    watch = sub.add_parser("watch", help="Repeatedly scan devices and optionally auto-attach configured USB")
    watch.add_argument("--config", help="Path to hurry.toml")
    watch.add_argument("--interval", type=float, default=None, help="Scan interval in seconds")
    watch.add_argument("--once", action="store_true", help="Run one scan/attach pass and exit")
    watch.add_argument("--dry-run", action="store_true", help="Print attach intent without running usbipd")
    watch.add_argument("--elevate", action="store_true", help="Run elevated usbipd bind when required")
    watch.add_argument("--json", action="store_true", help="Print each scan as JSON")
    watch.add_argument("--no-attach", action="store_true", help="Do not auto-attach configured devices")
    watch.set_defaults(handler=cmd_watch)

    setup = sub.add_parser("setup", help="Prepare Windows/WSL prerequisites")
    setup_sub = setup.add_subparsers(dest="setup_command")
    setup_usbipd_parser = setup_sub.add_parser("usbipd", help="Check or install usbipd-win through winget")
    setup_usbipd_parser.add_argument("--run", action="store_true", help="Launch winget install instead of printing guidance")
    setup_usbipd_parser.add_argument("--json", action="store_true", help="Print machine-readable setup output")
    setup_usbipd_parser.set_defaults(handler=cmd_setup_usbipd)
    setup_serial_parser = setup_sub.add_parser("serial", help="Check common USB serial drivers and setup guidance")
    setup_serial_parser.add_argument("--json", action="store_true", help="Print machine-readable serial setup output")
    setup_serial_parser.set_defaults(handler=cmd_setup_serial)

    serial = sub.add_parser("serial", help="Small serial protocol helpers")
    serial_sub = serial.add_subparsers(dest="serial_command")
    serial_send = serial_sub.add_parser("send", help="Write one text or hex protocol frame to a WSL serial port")
    serial_send.add_argument("--port", help="WSL serial path, e.g. /dev/serial/by-id/... or /dev/ttyUSB0")
    serial_send.add_argument("--baud", type=int, default=115200, help="Serial baud rate, default: 115200")
    serial_payload = serial_send.add_mutually_exclusive_group(required=True)
    serial_payload.add_argument("--hex", dest="hex_payload", help='Hex bytes, e.g. "01 03 00 00"')
    serial_payload.add_argument("--text", dest="text_payload", help="UTF-8 text payload")
    serial_send.add_argument("--newline", action="store_true", help="Append LF to --text payload")
    serial_send.add_argument("--read-timeout", type=float, default=0.2, help="Seconds to wait for a response, default: 0.2")
    serial_send.add_argument("--read-bytes", type=int, default=4096, help="Maximum response bytes to read")
    serial_send.add_argument("--dry-run", action="store_true", help="Print payload without opening the serial port")
    serial_send.add_argument("--json", action="store_true", help="Print machine-readable send result")
    serial_send.set_defaults(handler=cmd_serial_send)

    gamepad = sub.add_parser("gamepad", help="Gamepad discovery and ROS routing helpers")
    gamepad_sub = gamepad.add_subparsers(dest="gamepad_command")
    gamepad_status = gamepad_sub.add_parser("status", help="Show wired, WSL-native, and Bluetooth gamepad routes")
    gamepad_status.add_argument("--config", help="Path to hurry.toml")
    gamepad_status.add_argument("--json", action="store_true", help="Print machine-readable gamepad status")
    gamepad_status.set_defaults(handler=cmd_gamepad_status)
    gamepad_agent = gamepad_sub.add_parser("agent-command", help="Print the Windows gamepad agent command")
    add_gamepad_agent_args(gamepad_agent)
    gamepad_agent.add_argument("--json", action="store_true", help="Print machine-readable command output")
    gamepad_agent.set_defaults(handler=cmd_gamepad_agent_command)
    gamepad_start = gamepad_sub.add_parser("start-agent", help="Run the Windows gamepad agent from WSL")
    add_gamepad_agent_args(gamepad_start)
    gamepad_start.add_argument("--dry-run", action="store_true", help="Print the command without launching PowerShell")
    gamepad_start.add_argument("--json", action="store_true", help="Print machine-readable command output")
    gamepad_start.set_defaults(handler=cmd_gamepad_start_agent)
    gamepad_bridge = gamepad_sub.add_parser("bridge", help="Publish Windows gamepad packets as ROS sensor_msgs/Joy")
    gamepad_bridge.add_argument("--bind", default="0.0.0.0", help="UDP bind address, default: 0.0.0.0")
    gamepad_bridge.add_argument("--port", type=int, default=DEFAULT_GAMEPAD_PORT, help="UDP port to listen on")
    gamepad_bridge.add_argument("--topic", default=DEFAULT_TOPIC, help="ROS Joy topic, default: /joy")
    gamepad_bridge.add_argument("--frame-id", default=DEFAULT_FRAME_ID, help="ROS Joy header frame_id")
    gamepad_bridge.set_defaults(handler=cmd_gamepad_bridge)
    gamepad_decode = gamepad_sub.add_parser("decode", help="Decode a Windows gamepad bridge packet")
    gamepad_decode.add_argument("--packet", required=True, help="JSON packet emitted by the Windows gamepad agent")
    gamepad_decode.add_argument("--json", action="store_true", help="Print machine-readable decoded Joy state")
    gamepad_decode.set_defaults(handler=cmd_gamepad_decode)

    waveshare = sub.add_parser("waveshare-can-a", help="Waveshare USB-CAN-A helpers")
    waveshare_sub = waveshare.add_subparsers(dest="waveshare_command")
    config_cmd = waveshare_sub.add_parser("configure", help="Configure USB-CAN-A CAN bitrate, protocol, frame type, and mode")
    add_waveshare_common_args(config_cmd)
    add_waveshare_config_args(config_cmd)
    config_cmd.set_defaults(handler=cmd_waveshare_configure)

    send_cmd = waveshare_sub.add_parser("send", help="Send one CAN2.0A/B frame through USB-CAN-A")
    add_waveshare_common_args(send_cmd)
    add_waveshare_config_args(send_cmd)
    send_cmd.add_argument("--id", required=True, help="CAN id, e.g. 0x123 or 0x1234567")
    send_cmd.add_argument("--data", default="", help='CAN data bytes, e.g. "11 22 33"; max 8 bytes')
    send_cmd.add_argument("--remote", action="store_true", help="Send a remote frame instead of a data frame")
    send_cmd.add_argument("--dlc", type=int, help="Remote frame DLC, default: data length or 0")
    send_cmd.add_argument("--no-configure", action="store_true", help="Do not prepend the USB-CAN-A configuration command")
    send_cmd.add_argument("--read-timeout", type=float, default=0.2, help="Seconds to wait for returned frames")
    send_cmd.add_argument("--read-bytes", type=int, default=4096, help="Maximum response bytes to read")
    send_cmd.set_defaults(handler=cmd_waveshare_send)

    recv_cmd = waveshare_sub.add_parser("recv", help="Read and decode USB-CAN-A frames for a short duration")
    add_waveshare_common_args(recv_cmd)
    recv_cmd.add_argument("--duration", type=float, default=2.0, help="Seconds to read, default: 2.0")
    recv_cmd.add_argument("--read-bytes", type=int, default=4096, help="Maximum bytes per serial read")
    recv_cmd.set_defaults(handler=cmd_waveshare_recv)

    decode_cmd = waveshare_sub.add_parser("decode", help="Decode raw USB-CAN-A serial bytes")
    decode_cmd.add_argument("--protocol", choices=["variable", "fixed"], default="variable")
    decode_cmd.add_argument("--hex", required=True, dest="raw_hex", help="Raw serial bytes to decode")
    decode_cmd.add_argument("--json", action="store_true", help="Print machine-readable decode output")
    decode_cmd.set_defaults(handler=cmd_waveshare_decode)

    ros = sub.add_parser("ros", help="ROS integration helpers")
    ros_sub = ros.add_subparsers(dest="ros_command")
    export = ros_sub.add_parser("export", help="Export ROS launch/env values from discovered devices")
    export.add_argument("--config", help="Path to hurry.toml")
    export.add_argument("--format", choices=["env", "json", "yaml", "launch", "params", "launch-file"], default="env")
    export.add_argument("--output", help="Write rendered content to a file instead of stdout")
    export.add_argument("--force", action="store_true", help="Overwrite --output if it already exists")
    export.set_defaults(handler=cmd_ros_export)

    return parser


def add_waveshare_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--port", help="WSL serial path, e.g. /dev/serial/by-id/... or /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=DEFAULT_USB_BAUD, help="USB serial baud, default: 2000000")
    parser.add_argument("--protocol", choices=["variable", "fixed"], default="variable")
    parser.add_argument("--dry-run", action="store_true", help="Print encoded serial bytes without opening the port")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output")


def add_waveshare_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--can-bitrate", type=int, default=1000000, help="CAN bitrate, default: 1000000")
    parser.add_argument("--frame-type", choices=["standard", "extended"], default="standard", help="CAN2.0A standard or CAN2.0B extended")
    parser.add_argument("--mode", choices=["normal", "silent", "loopback", "silent_loopback"], default="normal")
    parser.add_argument("--filter-id", default="0x0", help="Acceptance filter id, default: 0")
    parser.add_argument("--mask-id", default="0x0", help="Acceptance mask id, default: 0")
    parser.add_argument("--no-auto-retransmit", action="store_true", help="Disable CAN auto retransmit")


def add_gamepad_agent_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", help="WSL IPv4 address for the Windows agent to send UDP packets to")
    parser.add_argument("--port", type=int, default=DEFAULT_GAMEPAD_PORT, help="UDP target port")
    parser.add_argument("--hz", type=int, default=DEFAULT_GAMEPAD_HZ, help="Polling rate for Windows.Gaming.Input")
    parser.add_argument("--index", type=int, default=DEFAULT_AGENT_INDEX, help="Windows gamepad index")


def cmd_init(args: argparse.Namespace) -> int:
    if args.from_scan:
        config = load_config(args.config)
        try:
            lan_macs = parse_macs(args.lan_mac)
        except ValueError as exc:
            return print_error(str(exc), args.json)
        scan = scan_devices(config, lan_cidr=args.lan_cidr, lan_ports=parse_ports(args.lan_ports), lan_macs=lan_macs)
        content = render_config_from_devices(scan.devices)
    else:
        content = render_default_config()

    target = Path(args.path).expanduser()
    if args.print_config:
        if args.json:
            print(json.dumps({"path": str(target), "content": content}, indent=2, sort_keys=True))
        else:
            print(content, end="" if content.endswith("\n") else "\n")
        return 0

    if target.exists() and not args.force:
        payload = {
            "ok": False,
            "path": str(target),
            "error": "target config already exists",
            "hint": "Use --force to overwrite it, or pass a different output path.",
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(payload["error"], file=sys.stderr)
            print(payload["hint"], file=sys.stderr)
        return 1

    target.write_text(content, encoding="utf-8")
    payload = {"ok": True, "path": str(target), "from_scan": bool(args.from_scan)}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"created {target}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    report = collect_doctor_report()
    if args.json:
        print(json.dumps(to_jsonable(report), indent=2, sort_keys=True))
        return 0 if all(check.ok for check in report.checks if check.key not in {"wsl_serial_devices", "wsl_gamepads"}) else 1

    for check in report.checks:
        status = "ok" if check.ok else "warn"
        value = f" {check.value}" if check.value else ""
        print(f"{status:4} {check.key}{value}")
        if not check.ok and check.fix:
            print(f"     fix: {check.fix}")
    for warning in report.warnings:
        print(f"note {warning}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    try:
        lan_macs = parse_macs(args.lan_mac)
    except ValueError as exc:
        return print_error(str(exc), args.json)
    result = scan_devices(config, lan_cidr=args.lan_cidr, lan_ports=parse_ports(args.lan_ports), lan_macs=lan_macs)
    if args.json:
        print(json.dumps(to_jsonable(result), indent=2, sort_keys=True))
    else:
        print_scan_table(result.devices)
        for warning in result.warnings:
            print(f"warn {warning}", file=sys.stderr)
    return 0


def cmd_lan_scan(args: argparse.Namespace) -> int:
    try:
        ports = parse_ports(args.ports) or []
        macs = parse_macs(args.mac)
    except ValueError as exc:
        return print_error(str(exc), args.json)

    if not ports and not macs:
        return print_error("lan scan requires --ports, --mac, or both", args.json)
    if ports and not args.cidr and not macs:
        return print_error("--ports requires --cidr unless it is used with --mac", args.json)
    if args.cidr and ports:
        devices = scan_lan_cidr(args.cidr, ports)
    else:
        devices = []
    if macs:
        from .lan import local_ipv4_cidrs

        cidrs = [args.cidr] if args.cidr else local_ipv4_cidrs() or [None]
        for cidr in cidrs:
            devices.extend(scan_lan_mac(macs, cidr=cidr, ports=ports))

    payload = ScanResult(devices=devices, warnings=[])
    if args.json:
        print(json.dumps(to_jsonable(payload), indent=2, sort_keys=True))
    else:
        print_scan_table(devices)
    return 0


def cmd_attach(args: argparse.Namespace) -> int:
    if not args.all and not args.target:
        print("attach requires a target or --all", file=sys.stderr)
        return 2

    config = load_config(args.config)
    result = scan_devices(config)
    selected = select_attach_targets(result.devices, args.target, args.all)
    attach_results: list[dict[str, object]] = []

    if not selected:
        payload = {
            "results": attach_results,
            "error": "no matching usbipd devices found",
            "hint": "Run `hurry scan` and install usbipd-win if Windows USB devices are not listed.",
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(payload["error"], file=sys.stderr)
            print(payload["hint"], file=sys.stderr)
        return 1

    for device in selected:
        attach_results.append(attach_device(device, dry_run=args.dry_run, elevate=args.elevate))

    if args.json:
        print(json.dumps({"results": attach_results}, indent=2, sort_keys=True))
    else:
        for item in attach_results:
            status = "ok" if item.get("ok") else "warn"
            print(f"{status} {item.get('bus_id')}: {item.get('name')}")
            if item.get("bind_command"):
                print(f"  bind: {item['bind_command']}")
            if item.get("attach_command"):
                print(f"  attach: {item['attach_command']}")
            if item.get("error"):
                print(f"  {item['error']}")
    return 0 if attach_results and all(item.get("ok") for item in attach_results) else 1


def cmd_watch(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    interval = args.interval if args.interval is not None else config.watch.interval_seconds
    should_attach = config.watch.auto_attach and not args.no_attach
    while True:
        result = scan_devices(config)
        attach_results: list[dict[str, object]] = []
        if should_attach:
            auto_targets = select_attach_targets(result.devices, None, all_auto=True)
            for device in auto_targets:
                attach_results.append(attach_device(device, dry_run=args.dry_run, elevate=args.elevate))

        if args.json:
            payload = to_jsonable(result)
            payload["attach_results"] = attach_results
            print(json.dumps(payload, sort_keys=True), flush=True)
        else:
            print(time.strftime("[%H:%M:%S]"))
            print_scan_table(result.devices)
            for item in attach_results:
                status = "ok" if item.get("ok") else "warn"
                print(f"{status} auto-attach {item.get('bus_id')}: {item.get('name')}")
                if item.get("bind_command"):
                    print(f"  bind: {item['bind_command']}")
                if item.get("attach_command"):
                    print(f"  attach: {item['attach_command']}")
                if item.get("error"):
                    print(f"  {item['error']}")
        if args.once:
            return 0
        time.sleep(interval)


def cmd_setup_usbipd(args: argparse.Namespace) -> int:
    result = setup_usbipd(run=args.run)
    if args.json:
        print(json.dumps(to_jsonable(result), indent=2, sort_keys=True))
    else:
        status = "ok" if result.ok else "warn"
        print(f"{status} {result.component}")
        if result.stdout:
            print(result.stdout.strip())
        print(f"command: {result.command}")
        if result.hint:
            print(f"hint: {result.hint}")
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
    return 0 if result.ok else 1


def cmd_setup_serial(args: argparse.Namespace) -> int:
    report = setup_serial()
    if args.json:
        print(json.dumps(to_jsonable(report), indent=2, sort_keys=True))
        return 0

    print("WSL serial kernel modules")
    for module in report.modules:
        status = "ok" if module.ok else "warn"
        detail = f" ({module.detail})" if module.detail else ""
        print(f"{status:4} {module.module}{detail}")

    print("\nWindows COM ports")
    if report.windows_com_ports:
        for port in report.windows_com_ports:
            manufacturer = f" [{port.manufacturer}]" if port.manufacturer else ""
            status = f" {port.status}" if port.status else ""
            bus_id = f" busid={port.bus_id}" if port.bus_id else ""
            print(f"ok   {port.name}{manufacturer}{status}{bus_id}")
    else:
        print("warn no Windows COM ports detected right now")

    print("\nCommon Windows serial driver sources")
    for guide in report.driver_guides:
        chips = ", ".join(guide.chips)
        print(f"- {guide.name}: {chips}")
        print(f"  Linux module: {guide.linux_module}")
        print(f"  Windows driver: {guide.windows_driver_url}")
        for note in guide.notes:
            print(f"  note: {note}")

    print("\nHints")
    for hint in report.hints:
        print(f"- {hint}")
    return 0


def cmd_serial_send(args: argparse.Namespace) -> int:
    candidates = current_serial_candidates()
    port, error = select_serial_port(candidates, args.port)
    if error or not port:
        payload = {
            "ok": False,
            "error": error or "missing serial port",
            "serial_candidates": to_jsonable(candidates),
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(payload["error"], file=sys.stderr)
            for device in candidates:
                print(f"candidate: {device.stable_path}  {device.name}", file=sys.stderr)
        return 1

    try:
        frame = payload_from_hex(args.hex_payload) if args.hex_payload is not None else payload_from_text(args.text_payload, args.newline)
        result = send_serial(
            port=port,
            payload=frame,
            baud=args.baud,
            read_timeout=args.read_timeout,
            read_bytes=args.read_bytes,
            dry_run=args.dry_run,
        )
    except (OSError, SerialIoError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc), "port": port}, indent=2, sort_keys=True))
        else:
            print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        payload = to_jsonable(result)
        payload["ok"] = True
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        status = "dry-run" if result.dry_run else "ok"
        print(f"{status} {result.port} baud={result.baud} bytes={result.written}")
        print(f"tx: {result.payload_hex}")
        if result.response_hex:
            print(f"rx: {result.response_hex}")
            if result.response_text.strip():
                print(f"text: {result.response_text.rstrip()}")
    return 0


def cmd_gamepad_status(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    result = scan_devices(config)
    items = [gamepad_status_item(device) for device in result.devices if device.kind == "gamepad"]

    if args.json:
        print(json.dumps({"gamepads": items, "warnings": result.warnings}, indent=2, sort_keys=True))
        return 0

    if not items:
        print("No gamepads found.")
        return 0
    for item in items:
        endpoint = item.get("path") or item.get("bus_id") or item.get("endpoint") or "-"
        print(f"{item['route']:22} {item['state']:12} {endpoint}  {item['name']}")
        print(f"  action: {item['action']}")
    return 0


def cmd_gamepad_agent_command(args: argparse.Namespace) -> int:
    target = args.target or detect_wsl_target_ip()
    command = build_agent_command(target=target, port=args.port, hz=args.hz, index=args.index)
    payload = {
        "target": target,
        "port": args.port,
        "hz": args.hz,
        "index": args.index,
        "command": command,
        "shell": command_to_text(command),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(payload["shell"])
    return 0


def cmd_gamepad_start_agent(args: argparse.Namespace) -> int:
    target = args.target or detect_wsl_target_ip()
    command = build_agent_command(target=target, port=args.port, hz=args.hz, index=args.index)
    payload = {
        "target": target,
        "port": args.port,
        "hz": args.hz,
        "index": args.index,
        "command": command,
        "shell": command_to_text(command),
        "dry_run": args.dry_run,
    }
    if args.dry_run:
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(payload["shell"])
        return 0
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True), flush=True)
    return run_agent_command(command)


def cmd_gamepad_bridge(args: argparse.Namespace) -> int:
    return run_ros_bridge(bind=args.bind, port=args.port, topic=args.topic, frame_id=args.frame_id)


def cmd_gamepad_decode(args: argparse.Namespace) -> int:
    try:
        state = decode_packet_text(args.packet)
    except ValueError as exc:
        return print_error(str(exc), args.json)
    payload = joy_state_to_jsonable(state)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"source={state.source} index={state.index} connected={state.connected}")
        print("axes: " + " ".join(f"{value:.4f}" for value in state.axes))
        print("buttons: " + " ".join(str(value) for value in state.buttons))
    return 0


def gamepad_status_item(device: DeviceDescriptor) -> dict[str, object]:
    if device.locality == "wsl_native" and device.stable_path:
        return {
            "id": device.id,
            "name": device.name,
            "state": device.state,
            "route": "wsl_native",
            "path": device.stable_path,
            "latency_class": "native",
            "action": f"use ROS joy/joy_linux with dev:={device.stable_path}",
        }
    if device.bus_id:
        return {
            "id": device.id,
            "name": device.name,
            "state": device.state,
            "route": "usbipd_attach",
            "bus_id": device.bus_id,
            "latency_class": "near_native",
            "action": f"hurry attach {device.bus_id}",
        }
    bridge = next((transport for transport in device.transports if transport.kind == "windows_input_bridge"), None)
    if bridge:
        item = {
            "id": device.id,
            "name": device.name,
            "state": device.state,
            "route": "windows_input_bridge",
            "endpoint": bridge.endpoint,
            "latency_class": bridge.latency_class,
            "action": "run `hurry gamepad bridge` in WSL and `hurry gamepad start-agent` to stream Windows Bluetooth/HID input",
        }
        quirk = device.metadata.get("quirk")
        if quirk:
            item["quirks"] = [quirk]
        if device.metadata.get("windows_led_note"):
            item["note"] = device.metadata["windows_led_note"]
            item["safe_to_keep_paired"] = device.state.upper() == "OK"
        return item
    return {
        "id": device.id,
        "name": device.name,
        "state": device.state,
        "route": "unknown",
        "action": device.recommendation or "run hurry scan --json for details",
    }


def cmd_waveshare_configure(args: argparse.Namespace) -> int:
    port, error = select_waveshare_port(args.port)
    if error or not port:
        return print_port_error(error or "missing serial port", args.json)
    try:
        payload = encode_config(
            can_bitrate=args.can_bitrate,
            frame_type=args.frame_type,
            protocol=args.protocol,
            mode=args.mode,
            filter_id=parse_can_id(args.filter_id),
            mask_id=parse_can_id(args.mask_id),
            auto_retransmit=not args.no_auto_retransmit,
        )
        result = run_transaction(
            port=port,
            payloads=[payload],
            baud=args.baud,
            protocol=args.protocol,
            dry_run=args.dry_run,
        )
    except (OSError, WaveshareCanError, SerialIoError) as exc:
        return print_error(str(exc), args.json, port=port)
    return print_waveshare_result(result, args.json)


def cmd_waveshare_send(args: argparse.Namespace) -> int:
    port, error = select_waveshare_port(args.port)
    if error or not port:
        return print_port_error(error or "missing serial port", args.json)
    try:
        frame = CanFrame(
            can_id=parse_can_id(args.id),
            data=b"" if args.remote or not args.data else payload_from_hex(args.data),
            frame_type=args.frame_type,
            frame_format="remote" if args.remote else "data",
            dlc=args.dlc,
        )
        payloads: list[bytes] = []
        if not args.no_configure:
            payloads.append(
                encode_config(
                    can_bitrate=args.can_bitrate,
                    frame_type=args.frame_type,
                    protocol=args.protocol,
                    mode=args.mode,
                    filter_id=parse_can_id(args.filter_id),
                    mask_id=parse_can_id(args.mask_id),
                    auto_retransmit=not args.no_auto_retransmit,
                )
            )
        payloads.append(encode_frame(frame, protocol=args.protocol))
        result = run_transaction(
            port=port,
            payloads=payloads,
            baud=args.baud,
            read_timeout=args.read_timeout,
            read_bytes=args.read_bytes,
            protocol=args.protocol,
            dry_run=args.dry_run,
        )
    except (OSError, WaveshareCanError, SerialIoError) as exc:
        return print_error(str(exc), args.json, port=port)
    return print_waveshare_result(result, args.json)


def cmd_waveshare_recv(args: argparse.Namespace) -> int:
    port, error = select_waveshare_port(args.port)
    if error or not port:
        return print_port_error(error or "missing serial port", args.json)
    if args.dry_run:
        result = {
            "port": port,
            "baud": args.baud,
            "protocol": args.protocol,
            "duration": args.duration,
            "dry_run": True,
        }
        print(json.dumps(result, indent=2, sort_keys=True) if args.json else result)
        return 0
    try:
        result = read_frames(
            port=port,
            baud=args.baud,
            duration=args.duration,
            read_bytes=args.read_bytes,
            protocol=args.protocol,
        )
    except (OSError, WaveshareCanError, SerialIoError) as exc:
        return print_error(str(exc), args.json, port=port)
    return print_waveshare_result(result, args.json)


def cmd_waveshare_decode(args: argparse.Namespace) -> int:
    try:
        raw = payload_from_hex(args.raw_hex)
        frames = decode_frames(raw, args.protocol)
    except (WaveshareCanError, SerialIoError) as exc:
        return print_error(str(exc), args.json)
    payload = {"frames": [frame_to_json(frame) for frame in frames]}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print_decoded_frames(payload["frames"])
    return 0


def select_waveshare_port(requested: str | None) -> tuple[str | None, str | None]:
    return select_serial_port(current_serial_candidates(), requested)


def print_waveshare_result(result, as_json: bool) -> int:
    frames = [frame_to_json(frame) for frame in (result.decoded_frames or [])]
    payload = {
        "ok": True,
        "port": result.port,
        "baud": result.baud,
        "dry_run": result.dry_run,
        "written": result.written,
        "payload_hex": result.payload_hex,
        "response_hex": result.response_hex,
        "frames": frames,
    }
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    status = "dry-run" if result.dry_run else "ok"
    print(f"{status} {result.port} baud={result.baud} bytes={result.written}")
    if result.payload_hex:
        print(f"tx: {result.payload_hex}")
    if result.response_hex:
        print(f"rx: {result.response_hex}")
    print_decoded_frames(frames)
    return 0


def print_decoded_frames(frames: list[dict[str, object]]) -> None:
    for frame in frames:
        checksum = ""
        if frame.get("checksum_ok") is not None:
            checksum = f" checksum_ok={frame['checksum_ok']}"
        print(
            f"frame {frame['frame_type']} {frame['frame_format']} "
            f"id={frame['id']} dlc={frame['dlc']} data={frame['data']}{checksum}"
        )


def print_port_error(error: str, as_json: bool) -> int:
    candidates = current_serial_candidates()
    payload = {"ok": False, "error": error, "serial_candidates": to_jsonable(candidates)}
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(error, file=sys.stderr)
        for device in candidates:
            print(f"candidate: {device.stable_path}  {device.name}", file=sys.stderr)
    return 1


def print_error(error: str, as_json: bool, **extra: object) -> int:
    payload = {"ok": False, "error": error, **extra}
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(error, file=sys.stderr)
    return 1


def cmd_ros_export(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    result = scan_devices(config)
    content = render_exports(result.devices, args.format)
    if not args.output:
        print(content, end="" if content.endswith("\n") else "\n")
        return 0

    target = Path(args.output).expanduser()
    if target.exists() and not args.force:
        print(f"target output already exists: {target}", file=sys.stderr)
        print("Use --force to overwrite it, or pass a different output path.", file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    print(f"wrote {target}")
    return 0


def print_scan_table(devices: list[DeviceDescriptor]) -> None:
    if not devices:
        print("No devices found.")
        return
    for device in devices:
        role = device.role or "-"
        endpoint = device.stable_path or device.bus_id or device.address or "-"
        print(f"{device.id:28} {device.kind:12} {device.locality:12} {device.state:12} {role:18} {endpoint}  {device.name}")


def parse_ports(value: str) -> list[int] | None:
    if not value:
        return None
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def parse_macs(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    from .lan import normalize_mac

    macs: list[str] = []
    for value in values:
        for item in value.split(","):
            if not item.strip():
                continue
            mac = normalize_mac(item)
            if not mac:
                raise ValueError(f"invalid MAC address: {item}")
            if mac not in macs:
                macs.append(mac)
    return macs or None


def attach_device(device: DeviceDescriptor, dry_run: bool = False, elevate: bool = False) -> dict[str, object]:
    item: dict[str, object] = {
        "id": device.id,
        "bus_id": device.bus_id,
        "name": device.name,
        "state": device.state,
    }
    if not device.bus_id:
        item.update({"ok": False, "error": "device has no usbipd bus id"})
        return item

    state = device.state.lower()
    if "attached" in state:
        item.update({"ok": True, "skipped": True, "reason": "already attached"})
        return item

    if "not shared" in state:
        item["bind_required"] = True
        item["bind_command"] = bind_command(device.bus_id)
        if not elevate:
            item.update({"ok": False, "error": "usbipd bind requires elevation; rerun with --elevate or run bind command manually"})
            return item

        bind_result = bind_elevated(device.bus_id, dry_run=dry_run)
        item["bind_returncode"] = bind_result.returncode
        if not bind_result.ok:
            item.update({"ok": False, "error": bind_result.stderr or bind_result.stdout})
            return item

    attach_result = usbipd_attach(device.bus_id, dry_run=dry_run)
    item.update({"ok": attach_result.ok, "attach_command": " ".join(attach_result.args)})
    if not attach_result.ok:
        item["error"] = attach_result.stderr or attach_result.stdout
    return item


def select_attach_targets(devices: list[DeviceDescriptor], target: str | None, all_auto: bool) -> list[DeviceDescriptor]:
    usb_devices = [device for device in devices if device.locality == "windows_host" and device.bus_id]
    if all_auto:
        return [device for device in usb_devices if device.metadata.get("auto_attach") == "true"]
    assert target is not None
    target_lower = target.lower()
    return [
        device
        for device in usb_devices
        if target_lower
        in {
            device.id.lower(),
            (device.role or "").lower(),
            (device.bus_id or "").lower(),
        }
        or target_lower in device.name.lower()
    ]
