import { Alert, Collapse, Descriptions, Empty, Flex, Space, Tag, Typography } from "antd";
import type { AgentCommand, AgentCommandStreamFrame } from "../../domain/inventory";
import { diagnosticPaths } from "../../domain/diagnostics";
import DiagnosticResult from "./DiagnosticResult";
import WarpStatus from "./WarpStatus";

export interface CommandInspectorProps {
  commands: AgentCommand[];
  streamFramesByCommand: Record<string, AgentCommandStreamFrame[]>;
  emptyText?: string;
}
const colors = { waiting: "default", pending: "warning", leased: "processing", succeeded: "success", failed: "error", skipped: "default" };
const target = (command: AgentCommand) => command.query ? `${command.path}?${command.query}` : command.path;
const json = (value: unknown) => value == null ? "" : typeof value === "string" ? value : JSON.stringify(value, null, 2);
function title(command: AgentCommand) {
  if (command.path.startsWith("/api/child/warp/")) return `WARP ${command.path.split("/").at(-1)}`;
  if (command.path === "/api/child/domains/latency") return "Domain latency";
  if (command.path === "/api/child/network/return-route-test") return "Return route";
  if (command.path === "/api/child/logs") return "Service logs";
  if (command.path === "/api/child/logs/files") return command.method === "DELETE" ? "Clear log files" : "Log files";
  return `${command.method} ${target(command)}`;
}
function subtitle(command: AgentCommand, frames: AgentCommandStreamFrame[]) {
  if (command.status === "waiting") return "Waiting for prerequisite";
  if (command.status === "skipped") return "Not executed";
  return `${command.attempts} attempts, ${command.result_status ? `status ${command.result_status}` : "waiting"}${command.stream ? `, ${frames.length} stream frames` : ""}`;
}

export default function CommandInspector({ commands, streamFramesByCommand, emptyText }: CommandInspectorProps) {
  if (!commands.length) return <Empty description={emptyText ?? "No commands queued."} image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  return <Collapse className="command-inspector" accordion items={commands.map(command => {
    const frames = streamFramesByCommand[command.id] ?? [];
    const summary = diagnosticPaths.has(command.path) || command.path.startsWith("/api/child/warp/");
    const requestItems = [
      { key: "request", label: "Request", children: command.request_id },
      { key: "timeout", label: "Timeout", children: `${command.timeout_ms} ms` },
      ...(command.depends_on_command_id ? [{ key: "prerequisite", label: "Prerequisite", children: command.depends_on_command_id }] : []),
      { key: "created", label: "Created", children: command.created_at },
      { key: "updated", label: "Updated", children: command.updated_at },
    ];
    return {
      key: command.id,
      label: <Flex gap="small" align="start" wrap data-command-id={command.id}>
        <div className="min-width-zero"><Typography.Text strong>{title(command)}</Typography.Text><div><Typography.Text type="secondary">{subtitle(command, frames)}</Typography.Text></div></div>
        <Tag color={colors[command.status]}>{command.status}</Tag>
      </Flex>,
      children: <Space orientation="vertical" style={{ width: "100%" }}>
        {diagnosticPaths.has(command.path) && command.result_body != null && <DiagnosticResult path={command.path} body={command.result_body} />}
        {command.path.startsWith("/api/child/warp/") && command.result_body != null && <WarpStatus body={command.result_body} />}
        {summary ? <Collapse size="small" items={[{ key: "request", label: "Request details", children: <>
          <Typography.Paragraph>{command.method} {target(command)}</Typography.Paragraph>
          <Descriptions size="small" column={{ xs: 1, sm: 2 }} items={requestItems} />
          {command.body != null && <pre className="code-block">{command.path === "/api/child/warp/license" ? "WARP+ credential: [redacted]" : json(command.body)}</pre>}
        </> }]} /> : <>
          <Descriptions size="small" column={{ xs: 1, sm: 2 }} items={requestItems} />
          {command.body != null && <pre className="code-block">{json(command.body)}</pre>}
        </>}
        {command.result_error && <Alert type="error" showIcon title={command.result_error} />}
        {!summary && command.result_body != null && <pre className="code-block">{json(command.result_body)}</pre>}
        {frames.length > 0 && <pre className="code-block" aria-label="Command stream">{frames.map(frame => frame.data.trimEnd()).join("\n")}</pre>}
      </Space>,
    };
  })} />;
}
