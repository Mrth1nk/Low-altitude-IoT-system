# OnboardBridge 飞机端 MAVLink Wi-Fi 数传桥

本目录是部署到嵌入式开发板上的飞机端程序，用于把飞控 MAVLink2 数据透明转发到 Station 地面站的 `Ctrl + Shift + W` Wi-Fi MAVLink 模式，并把地面站发出的 MAVLink 指令反向写回飞控。

本程序只做 MAVLink 原始字节转发和日志解析，不会自动 ARM、TAKEOFF、切模式或主动控制飞机。

## Station 协议结论

已读取地面站代码：

- `Station/src/renderer/App.tsx`：`Ctrl + Shift + W` 调用 `toggleWifiMavlinkMode()`。
- `Station/src/main/ipc.ts`：`wifi-mavlink:set-mode` 打开 `MavlinkWifiService`。
- `Station/src/main/mavlinkWifiService.ts`：使用 Node `dgram`，监听 UDP `0.0.0.0:14560`。
- `Station/src/main/mavlinkProtocol.ts`：`MAVLINK_WIFI_PORT = 14560`，解析 MAVLink v1/v2 原始字节，发送 COMMAND_LONG / SET_MODE MAVLink v2 包。

Station 的 `Ctrl + Shift + W` 模式监听 UDP `14560`，但当前真实硬件中 Wi-Fi 数传模块是 USB 串口接在开发板上。开发板程序默认不直接发以太网 UDP，而是把飞控 MAVLink 原始字节透明写入 Wi-Fi 数传串口，由数传模块负责把数据送到电脑侧链路。

因此飞机端默认链路是：

```text
飞控 -> /dev/ttyACM0 -> 开发板 -> /dev/ttyUSB0 USB Wi-Fi 数传 -> 热点/数传链路 -> 电脑 Station
Station -> 数传链路 -> /dev/ttyUSB0 -> 开发板 -> /dev/ttyACM0 -> 飞控
```

不需要 JSON，不需要 TCP，不需要自定义封装。

## 文件说明

- `main.py`：程序入口。
- `config.json`：默认配置，默认 `serial` 透明数传，飞控 `/dev/ttyACM0`，数传 `/dev/ttyUSB0`。
- `serial_bridge.py`：飞控串口和 Wi-Fi 链路之间的双向透明转发。
- `mavlink_monitor.py`：只复制一份飞控方向数据用于 heartbeat/状态日志解析。
- `wifi_telemetry_link.py`：UDP/串口 Wi-Fi 数传适配，默认 UDP。
- `device_finder.py`：列出 `/dev/ttyACM*`、`/dev/ttyUSB*`、`/dev/serial/by-id/*`。
- `logger.py`：日志工具。
- `self_test.py`：本地自检。
- `install_service.sh`、`onboard_bridge.service`：systemd 自启动。

## 开发板运行

先查设备：

```bash
cd /home/elf/project/Light/WI-FI/OnboardBridge
python3 device_finder.py
python3 main.py --list-devices
```

手动指定飞控串口和 USB Wi-Fi 数传串口：

```bash
python3 main.py \
  --fc /dev/ttyACM0 \
  --fc-baud 57600 \
  --wifi /dev/ttyUSB0 \
  --wifi-baud 57600 \
  --wifi-mode serial \
  --log logs/bridge.log
```

如果后续确认数传模块不是串口透明模式，而是开发板直接走 IP 网络，再切到 UDP 模式：

```bash
python3 main.py --fc /dev/ttyACM0 --fc-baud 57600 --wifi-mode udp --udp-target-host 地面站电脑IP
```

自动扫描：

```bash
python3 main.py --auto --log logs/bridge.log
```

模拟模式：

```bash
python3 main.py --simulate
python3 self_test.py
```

## Station 地面站测试

1. 电脑连接 Wi-Fi 数传热点。
2. 启动 Station。
3. 按 `Ctrl + Shift + W`。
4. Station 状态应显示 `WiFi MAVLink UDP 14560`。
5. 接收日志出现 `HEARTBEAT mode=... armed=... sys=... comp=...`。
6. 点击 `ARM`、`DISARM`、`LAND` 或模式按钮时，Station 会发送 MAVLink 原始包到最近发来 heartbeat 的开发板地址。

## Mission Planner 已可用时怎么对照

Mission Planner 可用说明飞控 MAVLink 与 Wi-Fi 链路基本可达。Station 收不到时优先检查：

- Station 是否按了 `Ctrl + Shift + W`。
- Windows 防火墙是否放行 UDP `14560`。
- 开发板是否使用 `--wifi-mode serial --wifi /dev/ttyUSB0`。
- 数传模块串口波特率是否与 `--wifi-baud` 一致。
- 开发板日志中 `fc_to_wifi_bytes` 是否增长。
- Station 发指令后开发板日志中 `wifi_to_fc_bytes` 是否增长。

## 常见问题

收不到 heartbeat：

- 飞控串口不是 `/dev/ttyACM0`，运行 `python3 device_finder.py --probe`。
- 波特率错误，尝试 `57600`、`115200`、`921600`。
- 串口权限不足，把用户加入 `dialout`，或临时用 sudo。
- 有其他程序占用了飞控串口，例如 Mission Planner、MAVProxy、旧桥接进程。

地面站能收不能发：

- Station 只有收到 heartbeat 后才知道回发目标。
- 确认开发板 UDP 本地端口没有被占用。
- 看开发板统计 `wifi_to_fc_bytes` 是否增长。

Wi-Fi 热点已连接但没有数据：

- 确认数传模块在板子上是 `/dev/ttyUSB0`。
- 确认数传模块工作在透明传输/MAVLink 数传模式。
- 确认数传模块波特率与程序 `--wifi-baud` 一致。

`/dev/ttyACM0` 和 `/dev/ttyUSB0` 搞反：

- 飞控通常能读到 MAVLink heartbeat。
- 飞控通常能读到 MAVLink heartbeat。
- 数传串口通常是 CH340/CP210x/FTDI，对应 `/dev/ttyUSB0` 或 `/dev/serial/by-id/usb-1a86...`。

## systemd 自启动

确认路径是 `/home/elf/project/Light/WI-FI/OnboardBridge` 后执行：

```bash
chmod +x install_service.sh
./install_service.sh
sudo journalctl -u onboard_bridge.service -f
```

常用管理命令：

```bash
sudo systemctl restart onboard_bridge.service
sudo systemctl stop onboard_bridge.service
sudo systemctl status onboard_bridge.service
```
