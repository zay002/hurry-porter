from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from .config import load_config, render_config_from_devices, render_default_config
from .devices import scan_devices
from .doctor import collect_doctor_report
from .models import DeviceDescriptor, to_jsonable
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
    scan.set_defaults(handler=cmd_scan)

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

    ros = sub.add_parser("ros", help="ROS integration helpers")
    ros_sub = ros.add_subparsers(dest="ros_command")
    export = ros_sub.add_parser("export", help="Export ROS launch/env values from discovered devices")
    export.add_argument("--config", help="Path to hurry.toml")
    export.add_argument("--format", choices=["env", "json", "yaml", "launch", "params", "launch-file"], default="env")
    export.add_argument("--output", help="Write rendered content to a file instead of stdout")
    export.add_argument("--force", action="store_true", help="Overwrite --output if it already exists")
    export.set_defaults(handler=cmd_ros_export)

    return parser


def cmd_init(args: argparse.Namespace) -> int:
    if args.from_scan:
        config = load_config(args.config)
        scan = scan_devices(config, lan_cidr=args.lan_cidr, lan_ports=parse_ports(args.lan_ports))
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
    result = scan_devices(config, lan_cidr=args.lan_cidr, lan_ports=parse_ports(args.lan_ports))
    if args.json:
        print(json.dumps(to_jsonable(result), indent=2, sort_keys=True))
    else:
        print_scan_table(result.devices)
        for warning in result.warnings:
            print(f"warn {warning}", file=sys.stderr)
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
        return {
            "id": device.id,
            "name": device.name,
            "state": device.state,
            "route": "windows_input_bridge",
            "endpoint": bridge.endpoint,
            "latency_class": bridge.latency_class,
            "action": "planned v2 bridge; use wired USB attach for v1 low-latency testing",
        }
    return {
        "id": device.id,
        "name": device.name,
        "state": device.state,
        "route": "unknown",
        "action": device.recommendation or "run hurry scan --json for details",
    }


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
