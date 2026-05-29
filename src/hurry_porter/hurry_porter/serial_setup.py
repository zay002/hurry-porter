from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import system, usbipd


@dataclass
class SerialDriverGuide:
    key: str
    name: str
    chips: list[str]
    linux_module: str
    windows_driver_url: str
    notes: list[str] = field(default_factory=list)


@dataclass
class KernelModuleStatus:
    module: str
    ok: bool
    detail: str | None = None


@dataclass
class WindowsComPort:
    name: str
    device_id: str | None = None
    manufacturer: str | None = None
    status: str | None = None
    bus_id: str | None = None


@dataclass
class SerialSetupReport:
    modules: list[KernelModuleStatus]
    windows_com_ports: list[WindowsComPort]
    driver_guides: list[SerialDriverGuide]
    hints: list[str]


SERIAL_DRIVER_GUIDES = [
    SerialDriverGuide(
        key="wch_ch34x",
        name="WCH CH340/CH341",
        chips=["CH340", "CH341", "CH343"],
        linux_module="ch341",
        windows_driver_url="https://www.wch-ic.com/downloads/CH341SER_EXE.html",
        notes=[
            "Common on Arduino clones, USB-CAN adapters, and low-cost robot controllers.",
            "Windows should show a COM port such as `USB-SERIAL CH340 (COMx)` after installation.",
        ],
    ),
    SerialDriverGuide(
        key="silabs_cp210x",
        name="Silicon Labs CP210x",
        chips=["CP2102", "CP2104", "CP2108", "CP2109"],
        linux_module="cp210x",
        windows_driver_url="https://www.silabs.com/developer-tools/usb-to-uart-bridge-vcp-drivers",
        notes=[
            "Common on ESP32 boards and embedded controller debug ports.",
            "Use the CP210x Universal Windows Driver on recent Windows versions.",
        ],
    ),
    SerialDriverGuide(
        key="ftdi_vcp",
        name="FTDI VCP",
        chips=["FT232", "FT2232", "FT4232"],
        linux_module="ftdi_sio",
        windows_driver_url="https://ftdichip.com/drivers/vcp-drivers/",
        notes=[
            "Common in industrial USB-to-RS232/RS485 adapters and older dev boards.",
            "The VCP driver makes the device appear as a standard COM port.",
        ],
    ),
    SerialDriverGuide(
        key="prolific_pl2303",
        name="Prolific PL2303",
        chips=["PL2303", "PL2303G", "PL2303HXD"],
        linux_module="pl2303",
        windows_driver_url="https://www.prolific.com.tw/en/portfolio-item/pl2303gd/",
        notes=[
            "Many newer PL2303 variants use Windows Update or Prolific WHQL packages.",
            "Counterfeit or old HXA adapters may need vendor-specific handling.",
        ],
    ),
    SerialDriverGuide(
        key="usb_cdc_acm",
        name="USB CDC ACM",
        chips=["STM32 virtual COM", "Arduino native USB", "RP2040", "Teensy"],
        linux_module="cdc_acm",
        windows_driver_url="https://learn.microsoft.com/windows-hardware/drivers/usbcon/usb-driver-installation-based-on-compatible-ids",
        notes=[
            "Usually uses built-in Windows and Linux drivers.",
            "If it does not appear as a COM port, check the board firmware USB mode.",
        ],
    ),
]


def setup_serial() -> SerialSetupReport:
    modules = [check_kernel_module(module) for module in required_linux_modules()]
    com_ports = scan_windows_com_ports()
    hints = [
        "Install Windows drivers only from the chip vendor or the device vendor.",
        "After usbipd attach, Linux should expose serial bridges as /dev/ttyUSB* or /dev/ttyACM*.",
        "For serial CAN adapters, first verify the serial node, then configure slcand/can-utils if the adapter uses SLCAN.",
    ]
    return SerialSetupReport(
        modules=modules,
        windows_com_ports=com_ports,
        driver_guides=SERIAL_DRIVER_GUIDES,
        hints=hints,
    )


def required_linux_modules() -> list[str]:
    return sorted({guide.linux_module for guide in SERIAL_DRIVER_GUIDES} | {"usbserial"})


def check_kernel_module(module: str) -> KernelModuleStatus:
    result = system.run_capture(["modinfo", module], timeout=2.0)
    if result.ok:
        return KernelModuleStatus(module=module, ok=True)
    detail = (result.stderr or result.stdout).strip() or "module metadata not found"
    return KernelModuleStatus(module=module, ok=False, detail=detail)


def scan_windows_com_ports() -> list[WindowsComPort]:
    ports = scan_windows_pnp_com_ports()
    seen = {(port.name, port.device_id) for port in ports}
    for port in scan_usbipd_state_com_ports():
        key = (port.name, port.device_id)
        if key not in seen:
            ports.append(port)
            seen.add(key)
    return ports


def scan_windows_pnp_com_ports() -> list[WindowsComPort]:
    result = system.powershell(
        r"""
$ports = Get-CimInstance Win32_PnPEntity |
  Where-Object { $_.Name -match '\(COM\d+\)' } |
  Select-Object Name,DeviceID,Manufacturer,Status
$ports | ConvertTo-Json -Compress
""",
        timeout=5.0,
    )
    if not result.ok or not result.stdout.strip():
        return []

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    rows = payload if isinstance(payload, list) else [payload]
    ports: list[WindowsComPort] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = _optional_str(row.get("Name"))
        if not name:
            continue
        ports.append(
            WindowsComPort(
                name=name,
                device_id=_optional_str(row.get("DeviceID")),
                manufacturer=_optional_str(row.get("Manufacturer")),
                status=_optional_str(row.get("Status")),
            )
        )
    return ports


def scan_usbipd_state_com_ports() -> list[WindowsComPort]:
    exe = usbipd.find_usbipd()
    if not exe:
        return []
    result = system.run_capture([exe, "state"], timeout=5.0)
    if not result.ok or not result.stdout.strip():
        return []

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    devices = payload.get("Devices", []) if isinstance(payload, dict) else []
    ports: list[WindowsComPort] = []
    for device in devices:
        if not isinstance(device, dict):
            continue
        description = _optional_str(device.get("Description"))
        if not description or not re.search(r"\(COM\d+\)", description):
            continue
        ports.append(
            WindowsComPort(
                name=description,
                device_id=_optional_str(device.get("InstanceId")),
                status="present",
                bus_id=_optional_str(device.get("BusId")),
            )
        )
    return ports


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)
