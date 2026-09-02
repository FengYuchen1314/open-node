import { Alert, Collapse, Descriptions, Empty, Flex, Space, Tag, Typography } from "../../ui";
import type { AgentCommand, AgentCommandStreamFrame } from "../../domain/inventory";
import { diagnosticPaths } from "../../domain/diagnostics";
import DiagnosticResult from "./DiagnosticResult";
import WarpStatus from "./WarpStatus";
import { zhMessage, zhStatus } from "../../i18n/zh-CN";

export interface CommandInspectorProps {
  commands: AgentCommand[];
  streamFramesByCommand: Record<string, AgentCommandStreamFrame[]>;
  emptyText?: string;
}
const colors = { waiting: "default", pending: "warning", leased: "processing", succeeded: "success", failed: "error", skipped: "default" };
const target = (command: AgentCommand) => command.query ? `${command.path}?${command.query}` : command.path;
const json = (value: unknown) => value == null ? "" : typeof value === "string" ? value : JSON.stringify(value, null, 2);
function title(command: AgentCommand) {
  if (command.path.startsWith("/api/child/warp/")) {
    const action = command.path.split("/").at(-1) ?? "";
    return `WARP ${({ status: "状态", register: "注册", install: "安装", uninstall: "卸载", license: "许可证" } as Record<string, string>)[action] ?? action}`;
  }
  if (command.path === "/api/child/domains/latency") return "域名延迟";
  if (command.path === "/api/child/network/return-route-test") return "回程路由";
  if (command.path === "/api/child/logs") return "服务日志";
  if (command.path === "/api/child/logs/files") return command.method === "DELETE" ? "清空日志文件" : "日志文件";
  return `${command.method} ${target(command)}`;
}
function subtitle(command: AgentCommand, frames: AgentCommandStreamFrame[]) {
  if (command.status === "waiting") return "等待前置命令";
  if (command.status === "skipped") return "未执行";
  return `已尝试 ${command.attempts} 次，${command.result_status ? `状态码 ${command.result_status}` : "等待中"}${command.stream ? `，${frames.length} 个流式帧` : ""}`;
}

export default function CommandInspector({ commands, streamFramesByCommand, emptyText }: CommandInspectorProps) {
  if (!commands.length) return <Empty description={emptyText ?? "暂无排队中的命令。"} image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  return <Collapse className="command-inspector" accordion items={commands.map(command => {
    const frames = streamFramesByCommand[command.id] ?? [];
    const summary = diagnosticPaths.has(command.path) || command.path.startsWith("/api/child/warp/");
    const requestItems = [
      { key: "request", label: "请求", children: command.request_id },
      { key: "timeout", label: "超时", children: `${command.timeout_ms} ms` },
      ...(command.depends_on_command_id ? [{ key: "prerequisite", label: "前置命令", children: command.depends_on_command_id }] : []),
      { key: "created", label: "创建时间", children: new Date(command.created_at).toLocaleString("zh-CN") },
      { key: "updated", label: "更新时间", children: new Date(command.updated_at).toLocaleString("zh-CN") },
    ];
    return {
      key: command.id,
      label: <Flex gap="small" align="start" wrap data-command-id={command.id}>
        <div className="min-width-zero"><Typography.Text strong>{title(command)}</Typography.Text><div><Typography.Text type="secondary">{subtitle(command, frames)}</Typography.Text></div></div>
        <Tag color={colors[command.status]}>{zhStatus(command.status)}</Tag>
      </Flex>,
      children: <Space orientation="vertical" style={{ width: "100%" }}>
        {diagnosticPaths.has(command.path) && command.result_body != null && <DiagnosticResult path={command.path} body={command.result_body} />}
        {command.path.startsWith("/api/child/warp/") && command.result_body != null && <WarpStatus body={command.result_body} />}
        {summary ? <Collapse size="small" items={[{ key: "request", label: "请求详情", children: <>
          <Typography.Paragraph>{command.method} {target(command)}</Typography.Paragraph>
          <Descriptions size="small" column={{ xs: 1, sm: 2 }} items={requestItems} />
          {command.body != null && <pre className="code-block">{command.path === "/api/child/warp/license" ? "WARP+ 凭据：[已隐藏]" : json(command.body)}</pre>}
        </> }]} /> : <>
          <Descriptions size="small" column={{ xs: 1, sm: 2 }} items={requestItems} />
          {command.body != null && <pre className="code-block">{json(command.body)}</pre>}
        </>}
        {command.result_error && <Alert type="error" showIcon title={zhMessage(command.result_error)} />}
        {!summary && command.result_body != null && <pre className="code-block">{json(command.result_body)}</pre>}
        {frames.length > 0 && <pre className="code-block" aria-label="命令流式输出">{frames.map(frame => frame.data.trimEnd()).join("\n")}</pre>}
      </Space>,
    };
  })} />;
}
