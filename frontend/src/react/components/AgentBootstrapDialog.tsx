import { useLayoutEffect, useRef, useState } from "react";
import { Alert, Button, Checkbox, Descriptions, Form, Input, Modal, Select, Space, Spin, Tag, Typography } from "antd";
import { CopyOutlined, ReloadOutlined } from "@ant-design/icons";
import { useAgentBootstrap } from "../hooks/useAgentBootstrap";

export interface AgentBootstrapDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  serverId: string;
  serverName: string;
  onUpdated?: () => void;
}

const ticketLabels = { not_issued: "No installation ticket", issued: "Ticket ready",
  claimed: "Ticket claimed", expired: "Ticket expired", revoked: "Ticket revoked" };
function date(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString() : "Not observed";
}

export default function AgentBootstrapDialog({ open, onOpenChange, serverId, serverName, onUpdated }: AgentBootstrapDialogProps) {
  const model = useAgentBootstrap(open, serverId, onUpdated);
  const [copyMessage, setCopyMessage] = useState("");
  const copyEpoch = useRef(0);
  useLayoutEffect(() => {
    copyEpoch.current += 1;
    setCopyMessage("");
    return () => { copyEpoch.current += 1; };
  }, [open, serverId, model.command]);
  async function copyCommand() {
    if (!open || !model.command) return;
    const run = copyEpoch.current;
    try {
      await navigator.clipboard.writeText(model.command);
      if (run === copyEpoch.current) setCopyMessage("Copied. Keep your clipboard and shell history private.");
    } catch {
      if (run === copyEpoch.current) setCopyMessage("Clipboard access failed. Select and copy the command manually.");
    }
  }
  const state = model.state;
  return <Modal open={open} onCancel={() => onOpenChange(false)} title="Install Agent" width={760}
    destroyOnHidden styles={{ body: { maxHeight: "70dvh", overflowY: "auto" } }}
    footer={<Space wrap>
      <Button onClick={() => onOpenChange(false)}>Close</Button>
      <Button icon={<ReloadOutlined aria-hidden />} disabled={model.busy || model.loading} onClick={() => void model.refresh()}>Refresh status</Button>
    </Space>}>
    {open && <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      {(model.loading || model.busy) && <Spin aria-label="Checking installation" />}
      <Typography.Text strong>{serverName}</Typography.Text>
      <Typography.Paragraph>Generate a command here, run it as root on a new remote host, then wait for the Agent to connect.</Typography.Paragraph>
      {model.error && <Alert type="error" showIcon title={model.error} data-testid="bootstrap-error" />}
      {state && <>
        {!state.configured && <Alert type="warning" showIcon title={state.reason} />}
        <Descriptions column={1} size="small" items={[
          { key: "url", label: "Control plane", children: state.control_url ?? "HTTPS URL not configured" },
          ...(state.release ? [
            { key: "release", label: "Pinned release", children: `Agent ${state.release.agent_version} (preview) · Xray ${state.release.xray_version}` },
            { key: "host", label: "Supported host", children: state.release.platform },
          ] : []),
        ]} />
        <Space wrap data-testid="bootstrap-status">
          <Tag>{ticketLabels[state.bootstrap.status]}</Tag>
          <Tag color={state.bootstrap.agent_registered ? "success" : "warning"}>
            {state.bootstrap.agent_registered ? "Agent registered" : "Agent not yet registered"}
          </Tag>
        </Space>
        {state.bootstrap.agent_registered ? <Typography.Paragraph>
          {state.bootstrap.agent_version} · Last seen {date(state.bootstrap.agent_last_seen_at)}.
          {" "}Registration alone is not proof of a healthy installation; check the installer result and server telemetry.
        </Typography.Paragraph> : state.bootstrap.claimed_at ? <Alert type="info" showIcon title="Credential claimed"
          description="The host has claimed its credential. This does not mean installation is complete. Retry the same command only on that original host after an interruption; do not copy it to a second host. A new ticket cannot be issued for this server. Partial installations may require local recovery." />
          : state.bootstrap.server_last_heartbeat ? <Alert type="info" showIcon title="Existing host heartbeat"
            description={`This server has already reported a heartbeat (${date(state.bootstrap.server_last_heartbeat)}). Installation tickets are only available for new servers that have never connected. Inspect the existing host before creating a separate server for a new installation.`} /> : null}
        {model.canIssue && <Form layout="vertical" preserve={false} onFinish={() => void model.issue()}>
          <Typography.Paragraph>Installs a non-root managed Agent and a separate official Xray with no public proxy inbound.
            {" "}No existing service is adopted. Nginx, WARP, embedded/fork-only protocols and remote root lifecycle are not enabled.
            {" "}The host needs Python 3.11+, curl, systemd and outbound HTTPS to this control plane and GitHub.
            {" "}Missing Python venv/CA packages may be installed through apt.</Typography.Paragraph>
          <Form.Item label="Connection transport"><Select aria-label="Connection transport" value={model.transport}
            disabled={model.busy} onChange={model.setTransport} options={[
              { label: "Auto (WebSocket with HTTP fallback)", value: "auto" },
              { label: "WebSocket", value: "websocket" }, { label: "HTTP polling", value: "http" },
            ]} /></Form.Item>
          <Form.Item><Checkbox checked={model.confirmed} disabled={model.busy}
            onChange={event => model.setConfirmed(event.target.checked)}>I will use a new Debian 12 amd64 host for this server only.</Checkbox></Form.Item>
          {state.bootstrap.status === "issued" && <Typography.Paragraph type="secondary">Generating again invalidates the previous unclaimed command.</Typography.Paragraph>}
          <Button block type="primary" htmlType="submit" aria-label="Generate installation command" data-testid="bootstrap-issue" loading={model.busy}
            disabled={!model.confirmed || model.loading || model.busy}>Generate installation command</Button>
        </Form>}
        {model.command && <Space orientation="vertical" size="middle" style={{ width: "100%" }} data-testid="bootstrap-command">
          <Alert type="warning" showIcon title="Private short-lived command" description={<>
            Contains a private 10-minute ticket, not the long-lived Agent credential. Expires {date(state.bootstrap.expires_at)}.
            {" "}The first claim permits same-host retries for at most two minutes. Closing this dialog clears the displayed command;
            {" "}it does not erase your clipboard or shell history.</>} />
          <Form.Item label="Root shell installation command" layout="vertical" style={{ marginBottom: 0 }}>
            <Input.TextArea value={model.command} readOnly rows={6} aria-label="Root shell installation command"
              autoComplete="off" spellCheck={false} style={{ fontFamily: "monospace" }} />
          </Form.Item>
          <Button icon={<CopyOutlined aria-hidden />} onClick={() => void copyCommand()}>Copy command</Button>
          {copyMessage && <Typography.Text role="status">{copyMessage}</Typography.Text>}
        </Space>}
        {model.canRevoke && <Space orientation="vertical">
          <Button danger disabled={model.busy || model.loading} onClick={() => void model.revoke()}>Revoke installation ticket</Button>
          <Typography.Text type="secondary">Revocation stops future claims. It does not revoke an already-delivered Agent credential or disconnect an Agent.</Typography.Text>
        </Space>}
      </>}
    </Space>}
  </Modal>;
}
