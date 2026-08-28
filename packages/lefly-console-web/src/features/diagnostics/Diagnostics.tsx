import { AlertCircle, Braces, Cable, Clock3, ListChecks, ShieldCheck } from "lucide-react";

import { capabilityLabel, connectionLabel, type WorkspaceProps } from "../../app/consoleUi";

export function Diagnostics({ gateway }: WorkspaceProps) {
  const { state } = gateway;
  const capabilities = Object.entries(state.deviceState?.capabilities.commands ?? {}).slice(0, 30);
  const commands = state.commandTimeline.slice(-20).reverse();
  const errors = state.errors.slice(-20).reverse();
  const events = state.events.slice(-20).reverse();

  return (
    <section className="workspace-panel" aria-labelledby="diagnostics-title">
      <header className="workspace-heading bilingual-workspace-heading">
        <div><h1 id="diagnostics-title">诊断</h1><p className="eyebrow">SYSTEM TRACE</p></div>
        <div className="inline-status"><Cable size={15} />{connectionLabel(state.connection)}</div>
      </header>

      <div className="diagnostic-metrics">
        <Metric icon={<Cable size={17} />} label="连接" value={connectionLabel(state.connection)} />
        <Metric icon={<Clock3 size={17} />} label="Revision" value={String(state.deviceState?.revision ?? "--")} mono />
        <Metric icon={<ListChecks size={17} />} label="队列" value={`${state.deviceState?.command_queue?.size ?? 0}/${state.deviceState?.command_queue?.capacity ?? "--"}`} mono />
        <Metric icon={<ShieldCheck size={17} />} label="租约" value={state.lease.role === "controller" ? "controller" : "readonly"} mono />
      </div>

      <div className="diagnostics-grid">
        <div className="workspace-band">
          <div className="section-heading"><Braces size={18} /><div><h2>Capabilities</h2><p>{capabilities.length} 项可用</p></div></div>
          <div className="capability-list">
            {capabilities.length === 0 ? <p className="empty-copy">目标未报告能力</p> : capabilities.map(([name, metadata]) => (
              <div key={name}><span className="capability-mark enabled" /><span>{capabilityLabel(name)}</span><code>{name}</code><strong>{metadata.scope === "control" ? "可控" : "系统"}</strong></div>
            ))}
          </div>
        </div>

        <div className="workspace-band">
          <div className="section-heading"><ListChecks size={18} /><div><h2>命令时间线</h2><p>最近 {commands.length} 条</p></div></div>
          <div className="timeline-list">
            {commands.length === 0 ? <p className="empty-copy">尚未发送命令</p> : commands.map((command) => (
              <div key={command.requestId}><span className={`timeline-state ${command.status}`} /><code>{command.requestId}</code><strong>{command.commandType}</strong><em>{command.status}</em></div>
            ))}
          </div>
        </div>

        <div className="workspace-band diagnostics-error-band">
          <div className="section-heading"><AlertCircle size={18} /><div><h2>错误</h2><p>最近 {errors.length} 条</p></div></div>
          <div className="error-list">
            {errors.length === 0 ? <p className="empty-copy success-copy">未记录错误</p> : errors.map((error) => (
              <div key={error.id}><strong>{error.code}</strong><span>{error.message}</span><code>{error.requestId ?? "system"}</code></div>
            ))}
          </div>
        </div>

        <div className="workspace-band">
          <div className="section-heading"><Braces size={18} /><div><h2>事件字段</h2><p>结构化字段，最多 20 条</p></div></div>
          <div className="event-list diagnostic-events">
            {events.length === 0 ? <p className="empty-copy">暂无事件</p> : events.map(({ event }) => (
              <div className="event-row" key={event.id}><code>{event.type}</code><span>{correlationId(event)}</span><em>{boundedFields(event.payload)}</em></div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function Metric({ icon, label, value, mono = false }: { icon: React.ReactNode; label: string; value: string; mono?: boolean }) {
  return <div className="diagnostic-metric">{icon}<span>{label}</span><strong className={mono ? "mono" : ""}>{value}</strong></div>;
}

function correlationId(event: object): string {
  return "correlation_id" in event && typeof event.correlation_id === "string"
    ? event.correlation_id
    : "telemetry";
}

function boundedFields(payload: object): string {
  return Object.entries(payload).slice(0, 6).map(([key, value]) => {
    const rendered = typeof value === "object" ? "{…}" : String(value);
    return `${key}: ${rendered.slice(0, 48)}`;
  }).join(" · ");
}
