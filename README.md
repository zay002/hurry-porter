# hurry-porter

**hurry-porter 是面向 ROS 2 on WSL2 的即插即用硬件编排器。**

它不替代 [`usbipd-win`](https://github.com/dorssel/usbipd-win)，也不重写 USB/IP。它负责把真实开发中最烦的部分串起来：WSL/ROS 环境诊断、Windows USB 设备发现、`usbipd-win` attach 工作流、WSL 串口/手柄识别、局域网机器人探测、稳定角色命名，以及 ROS launch/env 参数导出。

## 为什么需要它

很多机器人开发者会在 Windows 笔记本上用 WSL2 跑 ROS 2，再去控制真实硬件。这个组合很方便，但硬件链路通常很零碎：

- USB 串口控制板需要先从 Windows attach 到 WSL2。
- 手柄可能是 USB、Bluetooth、XInput、HID，不同路径延迟和可见性不同。
- 一些机械臂、雷达或控制器走 TCP/IP，应该由 WSL2 直接连接。
- ROS launch 文件需要稳定的串口路径、设备角色和网络 endpoint。
- 掉线、重插、bus id 变化后，开发者需要重复手工排查。

`hurry-porter` 的目标是把这些步骤变成一条清晰、可诊断、可自动化的工作流。

## 当前能力

- `hurry doctor`：检查 WSL2、mirrored networking、ROS 环境、Python ABI、`usbipd-win`、`winget`、`lsusb`、`udevadm`、串口和手柄设备。
- `hurry init`：生成 `hurry.toml`，也可以根据当前扫描结果生成候选设备规则。
- `hurry scan --json`：发现 Windows USB、Windows COM/USB 串口、Windows 蓝牙/HID 手柄、WSL native serial/input、配置的局域网设备。
- `hurry attach`：通过 `usbipd-win` 将 Windows USB 设备 attach 到当前 WSL2；需要管理员 bind 时给出明确 PowerShell 命令，默认不自动提权。
- `hurry watch`：周期扫描设备，并可对 `hurry.toml` 中标记的设备自动 attach。
- `hurry ros export`：导出 ROS 2 launch/env/params/launch-file 可消费的串口、手柄、LAN 参数。
- `hurry serial send`：向 WSL 串口写入一帧 text/hex 协议，并可短暂读取回应。
- `hurry gamepad status`：区分 WSL 原生手柄、可 attach 的 USB 手柄和可走 v2 bridge 的 Windows 蓝牙/HID 手柄。
- `hurry gamepad bridge/start-agent`：v2 Windows 手柄桥，把 Windows 蓝牙/有线手柄发布成 ROS 2 `sensor_msgs/Joy`。
- `hurry waveshare-can-a`：针对微雪 USB-CAN-A 的 CAN2.0A/B 配置、发送和接收解码。
- `hurry_porter_cpp`：ROS 2 C++ 扩展点，用于后续 GameInput / Hyper-V sockets / Windows-only 输入桥接。

## 快速开始

### 1. 安装 usbipd-win

在 Windows PowerShell 中安装：

```powershell
winget install --interactive --exact dorssel.usbipd-win
```

也可以在 WSL2 中先让 `hurry` 检查并打印安装命令：

```bash
hurry setup usbipd
hurry setup usbipd --run
```

`--run` 会通过 Windows `winget` 启动安装，可能弹出 UAC 或安装确认窗口。这一步需要人工确认。

### 2. 检查常见串口驱动

很多机器人控制板、USB-CAN、USB-RS485、Arduino/ESP32 开发板在 Windows 侧会先表现为 COM 口，在 WSL attach 后表现为 `/dev/ttyUSB*` 或 `/dev/ttyACM*`。先运行：

```bash
hurry setup serial
hurry setup serial --json
```

它会检查 WSL 内核是否有常见串口模块，并列出 Windows 侧常见官方驱动入口：

| 芯片/协议 | WSL/Linux 模块 | Windows 驱动入口 |
| --- | --- | --- |
| WCH CH340/CH341 | `ch341` | <https://www.wch-ic.com/downloads/CH341SER_EXE.html> |
| Silicon Labs CP210x | `cp210x` | <https://www.silabs.com/developer-tools/usb-to-uart-bridge-vcp-drivers> |
| FTDI VCP | `ftdi_sio` | <https://ftdichip.com/drivers/vcp-drivers/> |
| Prolific PL2303 | `pl2303` | <https://www.prolific.com.tw/en/portfolio-item/pl2303gd/> |
| USB CDC ACM | `cdc_acm` | 通常使用 Windows/Linux 内置驱动 |

只从芯片厂商或设备厂商安装 Windows 驱动。WSL 侧通常不需要下载驱动，只要内核模块存在即可。

### 3. 发送串口协议帧

USB-CAN、USB-RS485 或控制板如果在 WSL 中表现为 `/dev/ttyUSB*` 或 `/dev/serial/by-id/*`，v1 只把它当作串口设备处理。你可以先 dry-run 一帧十六进制协议：

```bash
hurry serial send --port /dev/ttyUSB0 --baud 115200 --hex "01 03 00 00" --dry-run
```

确认端口、波特率和协议帧后再去掉 `--dry-run`：

```bash
hurry serial send --port /dev/ttyUSB0 --baud 115200 --hex "01 03 00 00"
hurry serial send --port /dev/ttyUSB0 --baud 115200 --text "AT" --newline
```

如果只有一个 WSL 串口，`--port` 可以省略；如果有多个串口，请显式传入稳定路径 `/dev/serial/by-id/...`。

### 4. 使用微雪 USB-CAN-A

USB-CAN-A 是 USB-串口-CAN 设备，不会在 Linux 里变成 `can0`。默认 USB 串口波特率是 `2000000`，v1 为它提供专用 CAN2.0A/B 封装：

```bash
hurry waveshare-can-a configure \
  --port /dev/ttyUSB0 \
  --can-bitrate 1000000 \
  --frame-type standard \
  --mode normal \
  --dry-run

hurry waveshare-can-a send \
  --port /dev/ttyUSB0 \
  --can-bitrate 1000000 \
  --frame-type standard \
  --id 0x123 \
  --data "11 22 33 44" \
  --dry-run

hurry waveshare-can-a send \
  --port /dev/ttyUSB0 \
  --protocol variable \
  --frame-type extended \
  --id 0x1234567 \
  --data "11 22 33 44 55 66 77 88"
```

`frame-type standard` 对应 CAN2.0A 11 位 ID，`frame-type extended` 对应 CAN2.0B 29 位 ID。接收一小段数据并解码：

```bash
hurry waveshare-can-a recv --port /dev/ttyUSB0 --duration 2.0
```

### 5. 构建 ROS 2 workspace

在 WSL2 中：

```bash
source /opt/ros/jazzy/setup.bash
colcon build
source install/setup.bash
```

### 6. 诊断和扫描

```bash
hurry doctor
hurry scan --json
hurry gamepad status --json
```

### 7. 使用 Windows 蓝牙/有线手柄桥

有线 USB 手柄如果能通过 `usbipd-win` attach 到 WSL，仍然优先走 `/dev/input/js*` 原生路线。蓝牙手柄和部分 Windows-only HID/XInput 手柄不会自然出现在 WSL 里，v2 提供一个很小的 Windows agent：Windows 侧用 `Windows.Gaming.Input` 读手柄，WSL 侧发布 ROS 2 `sensor_msgs/Joy`。

开一个 WSL 终端启动 ROS bridge：

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
hurry gamepad bridge --topic /joy
```

再开第二个 WSL 终端，让 hurry 生成并启动 Windows agent：

```bash
source install/setup.bash
hurry gamepad start-agent
```

如果想先检查命令，不启动 PowerShell：

```bash
hurry gamepad agent-command
hurry gamepad start-agent --dry-run --json
```

agent 默认向当前 WSL IPv4 的 UDP `47777` 发送 250Hz 输入包。ROS 侧轴映射为 `left_x,left_y,right_x,right_y,left_trigger,right_trigger,dpad_x,dpad_y`；按钮映射为 `A,B,X,Y,LB,RB,View,Menu,LStick,RStick,DPadUp,DPadDown,DPadLeft,DPadRight`。

### 8. 配置设备角色

生成示例配置：

```bash
hurry init
```

也可以根据当前可见设备生成候选规则，再人工调整 role：

```bash
hurry init --from-scan --force
```

示例：

```toml
[watch]
interval_seconds = 2.0
auto_attach = true

[[devices]]
role = "base_controller"
kind = "serial"
description_regex = "CH340|CP210|USB Serial|UART|CDC|FTDI"
auto_attach = true
preferred_transport = "usbipd"

[[devices]]
role = "gamepad"
kind = "gamepad"
description_regex = "Xbox|Controller|Gamepad|Joystick|DualSense"
preferred_transport = "usbipd"

[[devices]]
role = "arm_controller"
kind = "lan_robot"
lan_host = "192.168.1.10"
lan_ports = [502, 30002]
preferred_transport = "lan"
```

### 9. Attach 和导出 ROS 参数

```bash
hurry attach base_controller --dry-run
hurry attach base_controller
hurry watch --once --dry-run
hurry serial send --port /dev/ttyUSB0 --baud 115200 --hex "01 03 00 00" --dry-run
hurry waveshare-can-a send --port /dev/ttyUSB0 --id 0x123 --data "11 22" --dry-run
hurry ros export
hurry ros export --format json
hurry ros export --format launch
hurry ros export --format params --output config/hurry.generated.yaml
hurry ros export --format launch-file --output launch/hurry.generated.launch.py
```

`params` 会生成 ROS 2 wildcard 参数文件：

```yaml
/**:
  ros__parameters:
    base_controller_port: "/dev/ttyUSB0"
    arm_controller_host: "192.168.1.10"
    arm_controller_ports:
      - 502
      - 30002
```

`launch-file` 会生成一个可被 `IncludeLaunchDescription` 引入的 Python launch 文件，同时声明 launch arguments 并设置对应的 `HURRY_*` 环境变量。

## 手动硬件测试清单

以下步骤需要真实 Windows/WSL2 机器和硬件：

1. 运行 `hurry setup usbipd --run`，确认 Windows 安装器和 UAC 通过。
2. 重新打开 WSL2，运行 `hurry doctor`，确认 `usbipd-win` 为 `ok`。
3. 插入 USB 串口控制板或 USB 手柄，运行 `hurry scan --json`。
4. 如果设备显示 `Not shared`，先按 `hurry attach <role|busid> --dry-run` 输出的 bind 命令在 Windows 管理员权限下 bind。
5. 运行 `hurry attach <role|busid>`，确认 WSL2 中出现 `/dev/ttyUSB*`、`/dev/ttyACM*` 或 `/dev/input/js*`。
6. 对 USB-CAN 或控制板串口，先运行 `hurry serial send --dry-run`，确认协议帧后再实际发送。
7. 运行 `hurry ros export --format launch`，确认输出可以放进 ROS 2 launch 参数。

## 故障排查

### usbipd-win 已安装但 attach 失败

如果 `hurry doctor` 显示 `usbipd_service` 为 `Stopped`，或者 `hurry scan` 输出：

```text
usbipd: warning: The service is currently not running; a reboot should fix that.
```

先重启 Windows。`usbipd-win` 安装驱动和服务后，有时必须重启才会让 `USBIP Device Host` 服务正常运行。重启后重新进入 WSL2：

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
hurry doctor
hurry attach <busid>
```

蓝牙手柄通常不会通过 attach 蓝牙适配器解决；优先测试 USB 有线手柄的 `/dev/input/js*` 原生路线，蓝牙和 Windows-only 手柄走 v2 bridge。
如果 `hurry scan --json` 能看到 `windows_input_bridge`，表示手柄已经在 Windows 侧连接成功，可以用 `hurry gamepad bridge` 和 `hurry gamepad start-agent` 发布 ROS `/joy`。

用 `hurry gamepad status` 可以直接看当前路线：

- `wsl_native`：已经是 `/dev/input/js*`，可直接给 ROS `joy` / `joy_linux`。
- `usbipd_attach`：有线 USB 手柄还在 Windows 侧，先 `hurry attach <busid>`。
- `windows_input_bridge`：蓝牙/HID 手柄在 Windows 侧可见，用 v2 bridge 发布 `/joy`。

### USB-CAN 已经是串口设备

很多 USB-CAN 适配器有自己的转换芯片和上位机协议。只要 WSL 中已经看到 `/dev/ttyUSB0` 或 `/dev/serial/by-id/...`，v1 就直接发送厂商协议帧：

```bash
hurry serial send --port /dev/serial/by-id/<your-device> --baud 115200 --hex "01 03 00 00"
```

如果 `hurry scan --json` 只能看到 `windows_com_pending`，说明 Windows 侧看到了 COM 口，但 usbipd 当前没有可 attach 的 bus id；重插 USB-CAN 或重启 usbipd 服务后再扫描。

对微雪 USB-CAN-A，优先使用专用命令，不需要自己拼底层串口帧：

```bash
hurry waveshare-can-a send --port /dev/ttyUSB0 --frame-type standard --id 0x123 --data "11 22"
hurry waveshare-can-a send --port /dev/ttyUSB0 --frame-type extended --id 0x1234567 --data "11 22"
```

## 设计原则

- **复用成熟工具**：USB 主通道使用 `usbipd-win`，不重复造 USB/IP。
- **ROS 原生**：主 CLI 使用 Python，低延迟扩展使用 C++，符合 ROS 2 社区习惯。
- **最低延迟优先**：USB 串口/USB 手柄优先 attach 到 WSL2 原生设备节点；LAN 设备由 WSL2 直接连接；Windows-only 蓝牙/HID 手柄用 v2 bridge 进入 ROS `/joy`。
- **默认可诊断**：每个失败路径都应该给出下一步命令，而不是只抛异常。

## 开发与测试

一键运行软件层检查，不需要连接真实硬件：

```bash
scripts/software_check.sh
```

也可以分步执行：

```bash
source /opt/ros/jazzy/setup.bash
colcon build
colcon test
python3.12 -m pytest -q
```

运行 C++ 扩展点探针：

```bash
ros2 run hurry_porter_cpp hurry_latency_probe --ros-args -p transport:=placeholder
```

## 路线图

- 更完整的 `usbipd-win` 输出兼容和错误恢复。
- 自动生成 ROS 2 launch include / parameters 文件。
- udev 稳定命名辅助。
- 小型串口协议发送/回应测试用例。
- 更低延迟的 Windows 手柄桥传输：当前 v2 使用 UDP，后续可切换到 Hyper-V sockets / AF_VSOCK。
- 设备热插拔恢复和机器人 profile。

---

# hurry-porter

**hurry-porter is a plug-and-play hardware orchestrator for ROS 2 on WSL2.**

It does not replace [`usbipd-win`](https://github.com/dorssel/usbipd-win), and it does not reimplement USB/IP. Instead, it connects the practical pieces developers need every day: WSL/ROS diagnostics, Windows USB discovery, `usbipd-win` attach workflows, WSL serial/gamepad detection, LAN robot probing, stable role naming, and ROS launch/env export.

## Why

Many robotics developers run ROS 2 inside WSL2 on a Windows laptop while controlling real hardware. That setup is convenient, but the hardware path can be fragmented:

- USB serial controllers must be attached from Windows into WSL2.
- Controllers may appear through USB, Bluetooth, XInput, or HID with different latency and visibility tradeoffs.
- Some robot arms, LiDARs, or controllers use TCP/IP and should be reached directly from WSL2.
- ROS launch files need stable serial paths, device roles, and network endpoints.
- Replugging devices can change bus ids and force repeated manual debugging.

`hurry-porter` turns those steps into a diagnosable and automatable workflow.

## Features

- `hurry doctor`: checks WSL2, mirrored networking, ROS, Python ABI, `usbipd-win`, `winget`, `lsusb`, `udevadm`, serial devices, and gamepads.
- `hurry init`: creates `hurry.toml`, optionally using the current scan to generate candidate device rules.
- `hurry scan --json`: discovers Windows USB devices, Windows COM/USB serial ports, Windows Bluetooth/HID gamepads, WSL native serial/input devices, and configured LAN devices.
- `hurry attach`: attaches Windows USB devices into WSL2 through `usbipd-win`; elevated bind commands are shown explicitly and are not run by default.
- `hurry watch`: periodically scans devices and can auto-attach devices marked in `hurry.toml`.
- `hurry ros export`: exports serial, gamepad, and LAN values for ROS 2 launch/env/params/launch-file usage.
- `hurry serial send`: writes one text/hex protocol frame to a WSL serial port and can briefly read a response.
- `hurry gamepad status`: separates WSL-native gamepads, attachable USB gamepads, and Windows Bluetooth/HID gamepads.
- `hurry gamepad bridge/start-agent`: v2 Windows gamepad bridge that publishes Bluetooth/wired Windows controllers as ROS 2 `sensor_msgs/Joy`.
- `hurry waveshare-can-a`: configures, sends, and decodes CAN2.0A/B frames for Waveshare USB-CAN-A.
- `hurry_porter_cpp`: ROS 2 C++ extension point for future GameInput / Hyper-V sockets / Windows-only input bridges.

## Quick Start

Install `usbipd-win` from Windows PowerShell:

```powershell
winget install --interactive --exact dorssel.usbipd-win
```

Or let `hurry` check and print the install command from WSL2:

```bash
hurry setup usbipd
hurry setup usbipd --run
```

`--run` launches Windows `winget` and may show UAC or installer prompts. This step needs manual confirmation.

Check common serial drivers:

```bash
hurry setup serial
hurry setup serial --json
```

Many robot controllers, USB-CAN adapters, USB-RS485 adapters, and Arduino/ESP32 boards appear as Windows COM ports first, then as `/dev/ttyUSB*` or `/dev/ttyACM*` after WSL attach. `hurry setup serial` checks common WSL kernel modules and points to official Windows driver sources:

| Chip/protocol | WSL/Linux module | Windows driver source |
| --- | --- | --- |
| WCH CH340/CH341 | `ch341` | <https://www.wch-ic.com/downloads/CH341SER_EXE.html> |
| Silicon Labs CP210x | `cp210x` | <https://www.silabs.com/developer-tools/usb-to-uart-bridge-vcp-drivers> |
| FTDI VCP | `ftdi_sio` | <https://ftdichip.com/drivers/vcp-drivers/> |
| Prolific PL2303 | `pl2303` | <https://www.prolific.com.tw/en/portfolio-item/pl2303gd/> |
| USB CDC ACM | `cdc_acm` | Usually built into Windows/Linux |

Install Windows drivers only from the chip vendor or the device vendor. On WSL, no vendor download is usually needed when the kernel module exists.

Send a serial protocol frame:

```bash
hurry serial send --port /dev/ttyUSB0 --baud 115200 --hex "01 03 00 00" --dry-run
```

When the port, baud rate, and frame are confirmed:

```bash
hurry serial send --port /dev/ttyUSB0 --baud 115200 --hex "01 03 00 00"
hurry serial send --port /dev/ttyUSB0 --baud 115200 --text "AT" --newline
```

If only one WSL serial device is visible, `--port` may be omitted. Pass `/dev/serial/by-id/...` when several serial devices are present.

Use Waveshare USB-CAN-A:

```bash
hurry waveshare-can-a configure --port /dev/ttyUSB0 --can-bitrate 1000000 --frame-type standard --dry-run
hurry waveshare-can-a send --port /dev/ttyUSB0 --frame-type standard --id 0x123 --data "11 22" --dry-run
hurry waveshare-can-a send --port /dev/ttyUSB0 --frame-type extended --id 0x1234567 --data "11 22"
hurry waveshare-can-a recv --port /dev/ttyUSB0 --duration 2.0
```

`standard` maps to CAN2.0A 11-bit IDs and `extended` maps to CAN2.0B 29-bit IDs. The USB serial baud defaults to `2000000`.

Build in WSL2:

```bash
source /opt/ros/jazzy/setup.bash
colcon build
source install/setup.bash
```

Diagnose and scan:

```bash
hurry doctor
hurry scan --json
hurry gamepad status --json
```

Use the Windows gamepad bridge:

```bash
# Terminal 1 in WSL
source /opt/ros/jazzy/setup.bash
source install/setup.bash
hurry gamepad bridge --topic /joy

# Terminal 2 in WSL
source install/setup.bash
hurry gamepad start-agent
```

To inspect the PowerShell command first:

```bash
hurry gamepad agent-command
hurry gamepad start-agent --dry-run --json
```

The Windows agent reads `Windows.Gaming.Input` and sends UDP packets to the current WSL IPv4 on port `47777` at 250Hz. Axis order is `left_x,left_y,right_x,right_y,left_trigger,right_trigger,dpad_x,dpad_y`; button order is `A,B,X,Y,LB,RB,View,Menu,LStick,RStick,DPadUp,DPadDown,DPadLeft,DPadRight`.

Create a local config:

```bash
hurry init
hurry init --from-scan --force
```

Attach and export:

```bash
hurry attach base_controller --dry-run
hurry attach base_controller
hurry watch --once --dry-run
hurry serial send --port /dev/ttyUSB0 --baud 115200 --hex "01 03 00 00" --dry-run
hurry waveshare-can-a send --port /dev/ttyUSB0 --id 0x123 --data "11 22" --dry-run
hurry ros export
hurry ros export --format json
hurry ros export --format launch
hurry ros export --format params --output config/hurry.generated.yaml
hurry ros export --format launch-file --output launch/hurry.generated.launch.py
```

`params` writes a ROS 2 wildcard parameter file. `launch-file` writes an includable Python launch file that declares launch arguments and sets matching `HURRY_*` environment variables.

## Manual Hardware Test Checklist

These steps need a real Windows/WSL2 machine and hardware:

1. Run `hurry setup usbipd --run` and approve Windows installer / UAC prompts.
2. Restart WSL2 if needed, then run `hurry doctor` and confirm `usbipd-win` is `ok`.
3. Plug in a USB serial controller or USB gamepad, then run `hurry scan --json`.
4. If the device is `Not shared`, run the elevated bind command shown by `hurry attach <role|busid> --dry-run`.
5. Run `hurry attach <role|busid>` and confirm `/dev/ttyUSB*`, `/dev/ttyACM*`, or `/dev/input/js*` appears in WSL2.
6. For USB-CAN or controller serial links, run `hurry serial send --dry-run` first, then send the frame after confirming the protocol.
7. Run `hurry ros export --format launch` and use the output in a ROS 2 launch flow.

## Troubleshooting

### usbipd-win is installed but attach fails

If `hurry doctor` reports `usbipd_service` as `Stopped`, or `hurry scan` prints:

```text
usbipd: warning: The service is currently not running; a reboot should fix that.
```

Restart Windows first. After driver/service installation, `USBIP Device Host` may need a full Windows restart before attach can work. Then reopen WSL2:

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
hurry doctor
hurry attach <busid>
```

Bluetooth gamepads are usually not solved by attaching the Bluetooth adapter. Prefer native `/dev/input/js*` for attachable wired USB controllers; use the v2 bridge for Bluetooth and Windows-only controllers.
If `hurry scan --json` shows `windows_input_bridge`, the controller is connected on Windows and can be published with `hurry gamepad bridge` plus `hurry gamepad start-agent`.

Use `hurry gamepad status` to see the current route:

- `wsl_native`: already exposed as `/dev/input/js*`, ready for ROS `joy` / `joy_linux`.
- `usbipd_attach`: wired USB controller is still on Windows; run `hurry attach <busid>`.
- `windows_input_bridge`: Bluetooth/HID controller is visible on Windows; use the v2 bridge to publish `/joy`.

### USB-CAN is a serial protocol device

Many USB-CAN adapters expose a vendor serial protocol through their own conversion chip. If WSL sees `/dev/ttyUSB0` or `/dev/serial/by-id/...`, v1 writes the vendor protocol frame directly:

```bash
hurry serial send --port /dev/serial/by-id/<your-device> --baud 115200 --hex "01 03 00 00"
```

If `hurry scan --json` only shows `windows_com_pending`, Windows sees the COM port but usbipd currently has no attachable bus id. Replug the adapter or restart the usbipd service, then scan again.

For Waveshare USB-CAN-A, prefer the dedicated helper:

```bash
hurry waveshare-can-a send --port /dev/ttyUSB0 --frame-type standard --id 0x123 --data "11 22"
hurry waveshare-can-a send --port /dev/ttyUSB0 --frame-type extended --id 0x1234567 --data "11 22"
```

## Development

Run software-only checks without real hardware:

```bash
scripts/software_check.sh
```

Or run the steps manually:

```bash
source /opt/ros/jazzy/setup.bash
colcon build
colcon test
python3.12 -m pytest -q
```

Run the C++ extension probe:

```bash
ros2 run hurry_porter_cpp hurry_latency_probe --ros-args -p transport:=placeholder
```

## Design Principles

- Reuse mature tooling: USB forwarding is delegated to `usbipd-win`.
- Stay ROS-native: Python for orchestration, C++ for low-latency bridge points.
- Prefer the lowest-latency path: direct WSL device nodes for USB/serial/gamepads, direct TCP/IP for LAN robots, and a small Windows bridge for Bluetooth/HID controllers.
- Make failures actionable: diagnostics should point to the next command to run.
