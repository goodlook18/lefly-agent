import { useEffect, useRef, useState, type CSSProperties } from "react";
import { CircleOff, Lightbulb, Sparkles } from "lucide-react";

import { ActionButton, numericField, type WorkspaceProps } from "../../app/consoleUi";

const COLORS = ["#FFFFFF", "#FFD33D", "#F1A22E", "#F05D5E", "#2F9D68", "#20A8B5", "#438CFF"];

export function LightingPanel({ gateway }: WorkspaceProps) {
  const device = gateway.state.deviceState;
  const [brightness, setBrightness] = useState(Math.round(numericField(device?.light.brightness, 0) * 100));
  const [headColor, setHeadColor] = useState(device?.light.pixels[0] ?? "#FFFFFF");
  const brightnessDirty = useRef(false);
  const headColorDirty = useRef(false);
  const lightReason = gateway.disabledReason("light.solid");
  const brightnessReason = gateway.disabledReason("light.brightness");

  useEffect(() => {
    const reported = Math.round(numericField(device?.light.brightness, 0) * 100);
    if (brightnessDirty.current) return;
    setBrightness(reported);
  }, [device?.light.brightness]);
  useEffect(() => {
    const reported = device?.light.pixels[0];
    if (!reported) return;
    if (headColorDirty.current) return;
    setHeadColor(reported);
  }, [device?.light.pixels]);

  return (
    <section className="workspace-panel" aria-labelledby="lighting-title">
      <header className="workspace-heading bilingual-workspace-heading">
        <div><h1 id="lighting-title">灯光</h1><p className="eyebrow">LIGHT CHANNELS</p></div>
        <div className="channel-legend"><span className="head-channel" />头部矩阵<span className="strip-channel" />状态灯条</div>
      </header>

      <div className="lighting-layout">
        <div className="workspace-band light-channel-panel">
          <div className="section-heading"><Lightbulb size={18} /><div><h2>头部 RGB 矩阵</h2><p><code>head_matrix</code> · 用户控制通道</p></div></div>
          <div className="light-readout"><span style={{ background: headColor }} /><div><small>当前颜色</small><strong>{headColor}</strong></div><output>{brightness}%</output></div>
          <label className="slider-row">
            <span>亮度</span>
            <input aria-label="头部灯亮度" type="range" min="0" max="100" value={brightness} style={{ "--range-accent": "var(--color-hardware)", "--range-value": `${brightness}%` } as CSSProperties} disabled={Boolean(brightnessReason)} title={brightnessReason ?? "亮度"} onChange={(event) => {
              const nextBrightness = Number(event.target.value);
              brightnessDirty.current = true;
              setBrightness(nextBrightness);
            }} />
            <output>{brightness}%</output>
          </label>
          <div className="swatch-grid" aria-label="头部灯颜色">
            {COLORS.map((color) => <button key={color} className={`color-swatch ${headColor === color ? "selected" : ""}`} style={{ "--swatch": color } as CSSProperties} aria-label={`选择头部灯 ${color}`} aria-pressed={headColor === color} disabled={Boolean(lightReason)} title={lightReason ?? color} onClick={() => {
              headColorDirty.current = true;
              setHeadColor(color);
            }} />)}
          </div>
          <div className="command-row">
            <ActionButton className="primary-action light-output-action" ariaLabel="应用头部灯" disabledReason={lightReason ?? brightnessReason} onClick={() => {
              gateway.sendCommand("light.solid", { target: "head_matrix", color: headColor });
              gateway.sendCommand("light.brightness", { target: "head_matrix", brightness: brightness / 100 });
            }}><Sparkles size={16} />常亮</ActionButton>
            <ActionButton ariaLabel="关闭头部灯" disabledReason={brightnessReason} onClick={() => gateway.sendCommand("light.brightness", { target: "head_matrix", brightness: 0 })}><CircleOff size={16} />关闭</ActionButton>
          </div>
        </div>

        <div className="workspace-band light-channel-panel status-channel-panel">
          <div className="section-heading"><Sparkles size={18} /><div><h2>底座状态灯条</h2><p><code>status_strip</code> · 设备状态通道</p></div></div>
          <div className="status-state">
            <span className="status-dot" />
            <div><small>控制模式</small><strong>自动跟随设备状态</strong></div>
          </div>
          <div className="light-readout"><span style={{ background: device?.status_strip.color ?? "#000000" }} /><div><small>当前渲染</small><strong>{device?.status_strip.effect ?? "--"}</strong></div><output>{device?.status.mode ?? "--"}</output></div>
          <p className="mode-boundary">状态灯条由设备状态自动管理，不接受用户命令。</p>
        </div>
      </div>
    </section>
  );
}
