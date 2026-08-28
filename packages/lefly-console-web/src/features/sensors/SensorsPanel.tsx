import { useState } from "react";
import { Eye, Hand, RadioTower, ScanFace } from "lucide-react";

import { ActionButton, type WorkspaceProps } from "../../app/consoleUi";

type SensorReading = { id?: unknown; label?: unknown };

export function SensorsPanel({ gateway }: WorkspaceProps) {
  const [gestureId, setGestureId] = useState(0);
  const [gestureLabel, setGestureLabel] = useState("");
  const [faceId, setFaceId] = useState(0);
  const [faceLabel, setFaceLabel] = useState("");
  const isSimulator = gateway.target?.kind === "simulator";
  const injectionVisible = isSimulator;
  const injectionReason = gateway.sensorInjectionDisabledReason();
  const sensorEvents = gateway.state.events.filter(({ event }) => event.type.startsWith("sensor.")).slice(-12).reverse();
  const latest = (type: string) => sensorEvents.find(({ event }) => event.type === type)?.event.payload;

  const reading = (value: unknown) => {
    if (typeof value !== "object" || value === null) return value == null ? "--" : String(value);
    const item = value as SensorReading;
    const id = typeof item.id === "number" ? `raw ID ${item.id}` : "raw ID --";
    return typeof item.label === "string" ? `${id} · ${item.label}` : id;
  };
  const injectId = (type: "gesture" | "face", id: number, label: string) => gateway.sendSensor(type, {
    id,
    ...(label.trim() ? { label: label.trim() } : {}),
  });

  return (
    <section className="workspace-panel" aria-labelledby="sensors-title">
      <header className="workspace-heading bilingual-workspace-heading">
        <div><h1 id="sensors-title">传感器</h1><p className="eyebrow">SENSOR BUS</p></div>
        <div className="inline-status"><RadioTower size={15} />{isSimulator ? "虚拟输入" : "只读遥测"}</div>
      </header>

      <div className="sensor-readings workspace-band">
        <div className="section-heading"><Eye size={18} /><div><h2>当前读数</h2></div></div>
        <dl className="reading-grid">
          <div><dt>Touch</dt><dd>{reading(latest("sensor.touch"))}</dd></div>
          <div><dt>Gesture</dt><dd>{reading(latest("sensor.vision.gesture"))}</dd></div>
          <div><dt>Face</dt><dd>{reading(latest("sensor.vision.face"))}</dd></div>
        </dl>
      </div>

      {injectionVisible && (
        <div className="workspace-band injection-lab">
          <div className="section-heading"><Hand size={18} /><div><h2>模拟器事件注入</h2></div></div>
          <div className="touch-controls">
            {(["left", "middle", "right"] as const).map((position) => (
              <ActionButton key={position} ariaLabel={`注入${position === "left" ? "左侧" : position === "middle" ? "中部" : "右侧"}触摸`} disabledReason={injectionReason} onClick={() => gateway.sendSensor("touch", { position })}>{position}</ActionButton>
            ))}
          </div>
          <div className="raw-injection-grid">
            <form onSubmit={(event) => { event.preventDefault(); injectId("gesture", gestureId, gestureLabel); }}>
              <div className="raw-form-title"><RadioTower size={16} /><strong>Gesture</strong></div>
              <label>Raw ID<input aria-label="Gesture raw ID" type="number" min="0" value={gestureId} onChange={(event) => setGestureId(Number(event.target.value))} /></label>
              <label>可选标签<input aria-label="Gesture label" value={gestureLabel} onChange={(event) => setGestureLabel(event.target.value)} /></label>
              <ActionButton type="submit" ariaLabel="注入 Gesture" disabledReason={injectionReason} onClick={() => undefined}>注入</ActionButton>
            </form>
            <form onSubmit={(event) => { event.preventDefault(); injectId("face", faceId, faceLabel); }}>
              <div className="raw-form-title"><ScanFace size={16} /><strong>Face</strong></div>
              <label>Raw ID<input aria-label="Face raw ID" type="number" min="0" value={faceId} onChange={(event) => setFaceId(Number(event.target.value))} /></label>
              <label>可选标签<input aria-label="Face label" value={faceLabel} onChange={(event) => setFaceLabel(event.target.value)} /></label>
              <ActionButton type="submit" ariaLabel="注入 Face" disabledReason={injectionReason} onClick={() => undefined}>注入</ActionButton>
            </form>
          </div>
        </div>
      )}

      <div className="workspace-band">
        <div className="section-heading"><RadioTower size={18} /><div><h2>传感器事件</h2><p>最近 {sensorEvents.length} 条</p></div></div>
        <div className="event-list">
          {sensorEvents.length === 0 ? <p className="empty-copy">暂无传感器事件</p> : sensorEvents.map(({ event }) => (
            <div className="event-row" key={event.id}><code>{event.type}</code><span>{reading(event.payload)}</span><em>{event.timestamp}</em></div>
          ))}
        </div>
      </div>
    </section>
  );
}
