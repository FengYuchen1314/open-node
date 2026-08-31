import { useLayoutEffect, useRef, useState } from "react";
import { Alert, Button, Checkbox, Descriptions, Form, Input, Modal, Space, Spin, Tag, Typography } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { AgentCommand } from "../../domain/inventory";
import { listServerCommands, queueAgentOperation } from "../../services/inventory";
import { zhMessage } from "../../i18n/zh-CN";

export type AgentLifecycleAction = "agent_upgrade" | "agent_rollback" | "agent_uninstall";
export interface AgentLifecycleDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  serverId: string;
  serverName: string;
  action: AgentLifecycleAction;
  onUpdated?: () => void;
}
type Release = { version: string; sha256: string };
interface HostStatus {
  enabled: boolean;
  installation_status: string;
  current: Release | null;
  previous: Release | null;
  release_base_url: string;
  recovery_required: boolean;
  jobs?: { status: string }[];
}
type Phase = "loading" | "ready" | "working" | "finished" | "error";
const pendingPaths = /^\/api\/child\/agent\/(upgrade(?:-stream)?|uninstall(?:-stream)?|rollback)$/;

export default function AgentLifecycleDialog({ open, onOpenChange, serverId, serverName, action, onUpdated }: AgentLifecycleDialogProps) {
  const [host, setHost] = useState<HostStatus | null>(null);
  const [phase, setPhaseValue] = useState<Phase>("loading");
  const [operation, setOperation] = useState<AgentCommand | null>(null);
  const [error, setError] = useState("");
  const [version, setVersion] = useState("");
  const [checksum, setChecksum] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const control = useRef({ generation: 0, active: false, submitting: false, phase: "loading" as Phase,
    waits: new Set<() => void>() });
  const updated = useRef(onUpdated);
  useLayoutEffect(() => { updated.current = onUpdated; }, [onUpdated]);
  function setPhase(value: Phase) { control.current.phase = value; setPhaseValue(value); }
  function invalidate() {
    control.current.generation += 1;
    for (const finish of control.current.waits) finish();
    control.current.waits.clear();
    return control.current.generation;
  }
  function current(run: number) { return control.current.active && control.current.generation === run; }
  function delay() {
    return new Promise<void>(resolve => {
      const finish = () => { clearTimeout(timer); control.current.waits.delete(finish); resolve(); };
      const timer = setTimeout(finish, 1000);
      control.current.waits.add(finish);
    });
  }
  async function waitForCommand(command: AgentCommand, target: string, run: number) {
    let latest = command;
    while (current(run)) {
      if (["succeeded", "failed", "skipped"].includes(latest.status)) return latest;
      await delay();
      if (!current(run)) return null;
      const rows = await listServerCommands(target);
      if (!current(run)) return null;
      const found = rows.commands.find(row => row.id === command.id);
      if (!found) throw new Error("Agent 命令已不可用。");
      latest = found;
      if (control.current.phase === "working") setOperation(latest);
    }
    return null;
  }
  async function pollOperation(run: number, target: string, command: AgentCommand) {
    try {
      const result = await waitForCommand(command, target, run);
      if (!result || !current(run)) return;
      setOperation(result);
      if (result.result_body && typeof result.result_body === "object") {
        setHost(previous => previous ? { ...previous, ...result.result_body as Partial<HostStatus> } : null);
      }
      setPhase("finished");
      if (result.status !== "succeeded") setError(result.result_error || "Agent 操作失败。");
      updated.current?.();
    } catch (failure) {
      if (current(run)) setError(failure instanceof Error ? failure.message : "命令状态不可用。");
    }
  }
  async function loadStatus() {
    const run = invalidate();
    if (!control.current.active || !serverId) return;
    const target = serverId;
    setPhase("loading"); setError(""); setHost(null); setOperation(null);
    setConfirmed(false); setVersion(""); setChecksum(""); setSubmitting(false);
    control.current.submitting = false;
    try {
      const rows = await listServerCommands(target);
      if (!current(run)) return;
      const pending = rows.commands.find(command => ["pending", "leased", "waiting"].includes(command.status)
        && pendingPaths.test(command.path));
      if (pending) {
        setOperation(pending); setPhase("working");
        await pollOperation(run, target, pending);
        return;
      }
      const queued = await queueAgentOperation(target, "agent_lifecycle");
      if (!current(run)) return;
      const result = await waitForCommand(queued.command, target, run);
      if (!result || !current(run)) return;
      if (result.status !== "succeeded") throw new Error(result.result_error || "获取 Agent 状态失败。");
      const next = result.result_body as HostStatus | null;
      if (!next?.enabled) throw new Error("尚未启用远程 Agent 生命周期管理。");
      setHost(next); setPhase("ready");
      updated.current?.();
    } catch (failure) {
      if (current(run)) {
        setError(failure instanceof Error ? failure.message : "Agent 状态不可用。");
        setPhase("error");
      }
    }
  }
  const valid = Boolean(confirmed && host?.enabled && host.installation_status === "installed"
    && !host.recovery_required && !host.jobs?.some(job => ["queued", "running"].includes(job.status))
    && (action !== "agent_rollback" || host.previous)
    && (action !== "agent_upgrade" || (/^[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?$/.test(version.trim())
      && /^[0-9a-f]{64}$/.test(checksum.trim()))));
  async function submit() {
    if (!valid || control.current.submitting || control.current.phase !== "ready" || !control.current.active) return;
    const run = control.current.generation;
    const target = serverId;
    control.current.submitting = true; setSubmitting(true); setError("");
    try {
      const payload = action === "agent_upgrade" ? { version: version.trim(), sha256: checksum.trim() } : { confirm: true as const };
      const queued = await queueAgentOperation(target, action, payload);
      if (!current(run)) return;
      updated.current?.();
      setOperation(queued.command); setPhase("working");
      await pollOperation(run, target, queued.command);
    } catch (failure) {
      if (current(run)) setError(failure instanceof Error ? failure.message : "Agent 请求失败。");
    } finally {
      if (current(run)) { control.current.submitting = false; setSubmitting(false); }
    }
  }
  async function refresh() {
    setError("");
    if (phase === "working" && operation) await pollOperation(invalidate(), serverId, operation);
    else await loadStatus();
  }
  useLayoutEffect(() => {
    control.current.active = open && Boolean(serverId);
    if (control.current.active) void loadStatus();
    else { invalidate(); setHost(null); setOperation(null); setVersion(""); setChecksum(""); setConfirmed(false); }
    return () => { control.current.active = false; invalidate(); };
  }, [open, serverId, action]);

  const displayedAction = operation && ["working", "finished"].includes(phase)
    ? operation.path.includes("/uninstall") ? "agent_uninstall"
      : operation.path.endsWith("/rollback") ? "agent_rollback" : "agent_upgrade" : action;
  const removed = (operation?.result_body as Partial<HostStatus> | null)?.installation_status === "removed";
  const title = { agent_upgrade: "升级 Agent", agent_rollback: "回退 Agent", agent_uninstall: "卸载 Agent" }[displayedAction];
  const commandLabel = { agent_upgrade: "升级", agent_rollback: "回退", agent_uninstall: "卸载" }[action];
  return <Modal open={open} title={title} width={560} destroyOnHidden onCancel={() => onOpenChange(false)}
    footer={<Space wrap><Button aria-label="关闭" onClick={() => onOpenChange(false)}>关闭</Button>
      {!removed && (error || phase === "finished") && <Button type="text" icon={<ReloadOutlined />}
        title="刷新 Agent 状态" aria-label="刷新 Agent 状态" onClick={() => void refresh()} />}
      {phase === "ready" && <Button type="primary" aria-label={commandLabel} danger={action === "agent_uninstall"} htmlType="submit"
        form="agent-lifecycle-form" disabled={!valid || submitting} loading={submitting}>{commandLabel}</Button>}</Space>}>
    {open && <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      {["loading", "working"].includes(phase) && <Spin aria-label={phase === "loading" ? "正在检查 Agent" : "Agent 操作进行中"} />}
      <Typography.Text>{serverName}</Typography.Text>
      {error ? <Alert type="error" showIcon title={zhMessage(error)} /> : phase === "finished" && operation?.status === "succeeded"
        ? <Alert type="success" showIcon title="已完成" /> : null}
      {host && <Descriptions column={1} size="small" items={[
        { key: "current", label: "当前版本", children: host.current?.version ?? "已移除" },
        { key: "previous", label: "上一版本", children: host.previous?.version ?? "无" },
        { key: "source", label: "发布来源", children: host.release_base_url },
      ]} />}
      {host?.recovery_required && <Alert type="warning" showIcon title="需要在服务器本机恢复" />}
      {phase === "ready" && <Form id="agent-lifecycle-form" layout="vertical" preserve={false} onFinish={() => void submit()}>
        {action === "agent_upgrade" && <>
          <Form.Item label="Agent 版本"><Input aria-label="Agent 版本" value={version} maxLength={64}
            disabled={submitting} onChange={event => setVersion(event.target.value)} /></Form.Item>
          <Form.Item label="Wheel SHA-256 校验和"><Input.TextArea aria-label="Wheel SHA-256 校验和" value={checksum} maxLength={64}
            rows={2} disabled={submitting} onChange={event => setChecksum(event.target.value)} style={{ fontFamily: "monospace" }} /></Form.Item>
        </>}
        <Checkbox checked={confirmed} disabled={submitting} onChange={event => setConfirmed(event.target.checked)}>
          {action === "agent_uninstall" ? "确认卸载 Agent" : "确认重启 Agent"}</Checkbox>
      </Form>}
      {operation && phase === "working" && <Tag>{operation.status === "pending" ? "排队中" : "运行中"}</Tag>}
    </Space>}
  </Modal>;
}
