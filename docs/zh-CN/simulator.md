# Simulator Lite 与 Web Console

[English](../simulator.md) | 简体中文

Simulator Lite 是一个无需硬件的 LeFly Device Protocol 端点，也是统一浏览器 Console 的本地服务器。

## 运行内置 Console

在仓库根目录安装并启动：

```bash
python -m pip install \
  packages/lefly-protocol \
  packages/lefly-sdk-python \
  packages/lefly-simulator
python -m lefly_simulator --host 127.0.0.1 --port 8766
```

打开 `http://127.0.0.1:8766/`。Console 支持目标状态、五关节运动、预设动作、灯头控制、机器人状态显示、虚拟传感器注入和诊断；独立 Agent 运行时，还可以使用 Agent 文本交互。

## 为 Console 添加可选远程目标

```bash
python -m lefly_simulator \
  --remote ws://robot-host:8766/ws/device \
  --host 127.0.0.1 \
  --port 8766
```

> 这条替代命令为 Console 增加一个远程 Device Protocol 端点。它不会启动 Text Agent，也不会修改 Agent 配置中的 `device_url`。

同一时间只选择一个目标，命令不会广播。远程目标断开后，系统不会静默回退到 Simulator。虚拟传感器注入仅适用于内置 Simulator 目标。

## 重新构建 Console

贡献者构建需要 Node.js 22.12 或更高版本：

```bash
cd packages/lefly-console-web
npm ci
npm test
npm run build
```

Vite 会把生产构建写入 `packages/lefly-simulator/src/lefly_simulator/static/`。提交源码变更时，需要同时提交对应的指纹化静态资源。

## 控制模型

- 同一时间只有一个浏览器会话持有可续期控制租约。只读页面可以明确接管，原页面会立即变为只读。
- 所有控制都受状态和设备声明能力的约束。
- revision 出现缺口后，在恢复完整状态之前禁止修改设备。
- 灯头矩阵由用户控制。
- 底座状态灯由机器人持有，只能读取。
- 手势和人脸 ID 是原始值，Console 不赋予它们永久含义。

## 故障排查

- **前端不可用：** 重新构建 Console 并重启 Simulator。
- **控件只读：** 接管控制权，或者关闭当前持有租约的会话。
- **目标陈旧或离线：** 恢复端点并等待完整状态快照。
- **模型空白：** 启用 WebGL 和硬件加速。
- **远程目标不可用：** 检查 URL 和 Device Protocol v1 行为。
