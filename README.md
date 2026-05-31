# hurry-porter

[![Software checks](https://github.com/zay002/hurry-porter/actions/workflows/software.yml/badge.svg)](https://github.com/zay002/hurry-porter/actions/workflows/software.yml)

面向 **ROS 2 on WSL2** 的硬件接入工具包。`hurry-porter` 负责发现、诊断和连接 Windows 主机上的 USB 串口、USB-CAN、手柄和局域网设备，让 ROS 2 侧拿到可用的端口、`/joy` 输入和 launch 参数。

当前版本：`0.2.1`

## 能力范围

| 场景 | 路径 |
| --- | --- |
| USB 串口、USB-CAN、USB 手柄 | 复用 `usbipd-win` attach 到 WSL2 |
| Waveshare USB-CAN-A | 通过厂商串口协议发送/接收 CAN2.0A/B 帧 |
| Windows 蓝牙/HID 手柄 | Windows agent -> UDP -> ROS 2 `sensor_msgs/Joy` |
| LAN 设备 | 按 TCP 端口或 MAC 地址发现机械臂 IP，并导出 endpoint |
| ROS 集成 | 导出 env、JSON、YAML params、launch-file |

不做的事：

- 不重新实现 USB/IP。
- 不把 Waveshare USB-CAN-A 伪装成 SocketCAN `can0`。
- 不接管 Windows 蓝牙配对流程。

## 要求

- Windows 11 + WSL2
- ROS 2 Jazzy
- Python `3.12`
- [`usbipd-win`](https://github.com/dorssel/usbipd-win)
- `colcon`

安装 `usbipd-win`：

```powershell
winget install --interactive --exact dorssel.usbipd-win
```

## 构建

```bash
cd ~/hurry-porter
source /opt/ros/jazzy/setup.bash
colcon build
source install/setup.bash
```

## 快速使用

检查环境和设备：

```bash
hurry doctor
hurry scan --json
```

Attach Windows USB 设备到 WSL2：

```bash
hurry attach 4-3 --dry-run
hurry attach 4-3
```

发送普通串口帧：

```bash
hurry serial send --port /dev/ttyUSB0 --baud 115200 --hex "01 02" --dry-run
hurry serial send --port /dev/ttyUSB0 --baud 115200 --hex "01 02"
```

使用 Waveshare USB-CAN-A：

```bash
hurry waveshare-can-a send \
  --port /dev/ttyUSB0 \
  --can-bitrate 1000000 \
  --frame-type standard \
  --mode loopback \
  --id 0x123 \
  --data "11 22 33 44"
```

`standard` 对应 CAN2.0A 11-bit ID，`extended` 对应 CAN2.0B 29-bit ID。USB 串口默认波特率为 `2000000`。

按 MAC 地址查找局域网机械臂：

```bash
hurry lan scan --cidr 192.168.1.0/24 --mac aa:bb:cc:dd:ee:ff --ports 502,30002 --json
```

启动 Windows 手柄桥：

```bash
# 终端 1：WSL / ROS bridge
source /opt/ros/jazzy/setup.bash
source install/setup.bash
hurry gamepad bridge --topic /joy

# 终端 2：Windows input agent
source install/setup.bash
hurry gamepad start-agent

# 终端 3：检查 ROS Joy
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 topic echo /joy sensor_msgs/msg/Joy
```

导出 ROS 参数：

```bash
hurry ros export --format json
hurry ros export --format params --output config/hurry.generated.yaml
hurry ros export --format launch-file --output launch/hurry.generated.launch.py
```

## 命令速查

| 命令 | 用途 |
| --- | --- |
| `hurry doctor` | 检查 WSL2、ROS、usbipd-win、串口和手柄状态 |
| `hurry scan --json` | 扫描 Windows USB/COM、WSL serial/input、LAN 设备 |
| `hurry attach <busid>` | 通过 usbipd-win attach USB 设备 |
| `hurry watch` | 周期扫描并按配置自动 attach |
| `hurry serial send` | 向 WSL 串口写入 text/hex 帧 |
| `hurry waveshare-can-a` | Waveshare USB-CAN-A 配置、发送、接收、解码 |
| `hurry lan scan` | 按 TCP 端口或 MAC 地址扫描 LAN 设备 |
| `hurry gamepad status` | 查看手柄路径：WSL native、usbipd、Windows bridge |
| `hurry gamepad bridge` | 在 WSL 发布 ROS 2 `/joy` |
| `hurry gamepad start-agent` | 启动 Windows.Gaming.Input agent |
| `hurry ros export` | 导出 ROS launch/env/params |

## 配置

生成 `hurry.toml`：

```bash
hurry init
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
description_regex = "Xbox|Controller|Gamepad|Joystick|DualSense|Pro Controller"
preferred_transport = "usbipd"

[[devices]]
role = "arm_controller"
kind = "lan_robot"
lan_host = "192.168.1.10"
lan_mac = "aa:bb:cc:dd:ee:ff"
lan_cidr = "192.168.1.0/24"
lan_ports = [502, 30002]
preferred_transport = "lan"
```

## 硬件备注

### USB 串口

CH340/CH341、CP210x、FTDI、PL2303、CDC ACM 通常应先在 Windows 侧安装对应驱动，再通过 `usbipd-win` attach 到 WSL2。WSL 中可见 `/dev/ttyUSB*`、`/dev/ttyACM*` 或 `/dev/serial/by-id/*` 后再由 ROS 使用。

### Waveshare USB-CAN-A

USB-CAN-A 是 USB-串口-CAN 设备。`hurry waveshare-can-a` 按 Waveshare 串口协议工作，支持 CAN2.0A/B、数据帧、远程帧、variable/fixed 协议和 loopback 模式。

### LAN 机械臂

很多机械臂的 IP 会随路由器变化，但铭牌或网页里会提供 MAC 地址。`hurry-porter` 会读取 WSL 的邻居表，并在指定 CIDR 内做轻量触达来刷新 ARP/neighbor cache；找到 MAC 后输出当前 IP 和已开放端口。

配置里可以只写 `lan_mac` + `lan_cidr`，也可以同时保留一个已知的 `lan_host` 作为兜底。

### Nintendo Switch Pro Controller

Windows 蓝牙下，Pro Controller 有时会保持玩家灯搜索/配对样式，但 PnP 状态仍是 `OK`，`/joy` 也能正常变化。这通常只是 Windows 没有分配玩家 LED，不代表需要重新配对。

检查：

```bash
hurry gamepad status --json
```

如果输出里有：

```json
"quirks": ["windows_pro_controller_led_unassigned"],
"safe_to_keep_paired": true
```

就可以继续使用，不需要重新配对。

## 开发

```bash
python3.12 -m pytest -q
source /opt/ros/jazzy/setup.bash
colcon build
colcon test
scripts/software_check.sh
```

## 相关项目

- [`usbipd-win`](https://github.com/dorssel/usbipd-win)
- [Microsoft WSL USB documentation](https://learn.microsoft.com/windows/wsl/connect-usb)
- [ROS 2 joy](https://docs.ros.org/)
- [Waveshare USB-CAN-A](https://www.waveshare.net/wiki/USB-CAN-A)

## License

MIT

---

# English

`hurry-porter` is a ROS 2 on WSL2 hardware porter for USB serial devices, Waveshare USB-CAN-A, Windows gamepads, and LAN endpoints.

It orchestrates existing system paths instead of replacing them:

- USB forwarding uses `usbipd-win`.
- Waveshare USB-CAN-A uses its vendor serial protocol, not SocketCAN.
- Windows Bluetooth/HID gamepads are bridged to ROS 2 `sensor_msgs/Joy`.
- ROS integration is exported as env, JSON, params, or launch files.

Build:

```bash
source /opt/ros/jazzy/setup.bash
colcon build
source install/setup.bash
```

Common commands:

```bash
hurry doctor
hurry scan --json
hurry attach <busid>
hurry serial send --port /dev/ttyUSB0 --baud 115200 --hex "01 02"
hurry waveshare-can-a send --port /dev/ttyUSB0 --frame-type standard --id 0x123 --data "11 22"
hurry lan scan --cidr 192.168.1.0/24 --mac aa:bb:cc:dd:ee:ff --ports 502,30002
hurry gamepad bridge --topic /joy
hurry gamepad start-agent
hurry ros export --format params
```

Run checks:

```bash
python3.12 -m pytest -q
scripts/software_check.sh
```
