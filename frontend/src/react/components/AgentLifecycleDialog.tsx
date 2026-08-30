import { useLayoutEffect, useRef, useState } from "react";
import { Alert, Button, Checkbox, Descriptions, Form, Input, Modal, Space, Spin, Tag, Typography } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { AgentCommand } from "../../domain/inventory";
import { listServerCommands, queueAgentOperation } from "../../services/inventory";

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
      if (!found) throw new Error("Agent command is no longer available.");
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
      if (result.status !== "succeeded") setError(result.result_error || "Agent operation failed.");
      updated.current?.();
    } catch (failure) {
      if (current(run)) setError(failure instanceof Error ? failure.message : "Command status is unavailable.");
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
      if (result.status !== "succeeded") throw new Error(result.result_error || "Agent status failed.");
      const next = result.result_body as HostStatus | null;
      if (!next?.enabled) throw new Error("Remote Agent lifecycle is not enabled.");
      setHost(next); setPhase("ready");
      updated.current?.();
    } catch (failure) {
      if (current(run)) {
        setError(failure instanceof Error ? failure.message : "Agent status is unavailable.");
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
      if (current(run)) setError(failure instanceof Error ? failure.message : "Agent request failed.");
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
  const title = { agent_upgrade: "Upgrade Agent", agent_rollback: "Roll back Agent", agent_uninstall: "Uninstall Agent" }[displayedAction];
  const commandLabel = { agent_upgrade: "Upgrade", agent_rollback: "Roll back", agent_uninstall: "Uninstall" }[action];
  return <Modal open={open} title={title} width={560} destroyOnHidden onCancel={() => onOpenChange(false)}
    footer={<Space wrap><Button onClick={() => onOpenChange(false)}>Close</Button>
      {!removed && (error || phase === "finished") && <Button type="text" icon={<ReloadOutlined />}
        title="Refresh Agent status" aria-label="Refresh Agent status" onClick={() => void refresh()} />}
      {phase === "ready" && <Button type="primary" aria-label={commandLabel} danger={action === "agent_uninstall"} htmlType="submit"
        form="agent-lifecycle-form" disabled={!valid || submitting} loading={submitting}>{commandLabel}</Button>}</Space>}>
    {open && <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      {["loading", "working"].includes(phase) && <Spin aria-label={phase === "loading" ? "Checking Agent" : "Agent operation in progress"} />}
      <Typography.Text>{serverName}</Typography.Text>
      {error ? <Alert type="error" showIcon title={error} /> : phase === "finished" && operation?.status === "succeeded"
        ? <Alert type="success" showIcon title="Completed" /> : null}
      {host && <Descriptions column={1} size="small" items={[
        { key: "current", label: "Current version", children: host.current?.version ?? "Removed" },
        { key: "previous", label: "Previous version", children: host.previous?.version ?? "None" },
        { key: "source", label: "Release source", children: host.release_base_url },
      ]} />}
      {host?.recovery_required && <Alert type="warning" showIcon title="Host recovery required" />}
      {phase === "ready" && <Form id="agent-lifecycle-form" layout="vertical" preserve={false} onFinish={() => void submit()}>
        {action === "agent_upgrade" && <>
          <Form.Item label="Agent version"><Input aria-label="Agent version" value={version} maxLength={64}
            disabled={submitting} onChange={event => setVersion(event.target.value)} /></Form.Item>
          <Form.Item label="Wheel SHA-256"><Input.TextArea aria-label="Wheel SHA-256" value={checksum} maxLength={64}
            rows={2} disabled={submitting} onChange={event => setChecksum(event.target.value)} style={{ fontFamily: "monospace" }} /></Form.Item>
        </>}
        <Checkbox checked={confirmed} disabled={submitting} onChange={event => setConfirmed(event.target.checked)}>
          {action === "agent_uninstall" ? "Confirm Agent removal" : "Confirm Agent restart"}</Checkbox>
      </Form>}
      {operation && phase === "working" && <Tag>{operation.status === "pending" ? "Queued" : "Running"}</Tag>}
    </Space>}
  </Modal>;
}
