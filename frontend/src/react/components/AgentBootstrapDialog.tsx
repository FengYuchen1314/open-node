import { useLayoutEffect, useRef, useState } from "react";
import { Alert, Button, Checkbox, Descriptions, Form, Input, Modal, Select, Space, Spin, Tag, Typography } from "antd";
import { CopyOutlined, ReloadOutlined } from "@ant-design/icons";
import { useAgentBootstrap } from "../hooks/useAgentBootstrap";
import { zhMessage } from "../../i18n/zh-CN";
import type { ServerKind } from "../../domain/inventory";

export interface AgentBootstrapDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  serverId: string;
  serverName: string;
  serverKind?: ServerKind;
  onUpdated?: () => void;
}

const ticketLabels = { not_issued: "尚未签发安装票据", issued: "票据已就绪",
  claimed: "票据已兑换", expired: "票据已过期", revoked: "票据已撤销" };
function date(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString("zh-CN") : "尚未观测到";
}

const serverKindNames: Record<ServerKind, string> = { direct: "公网直连", "leased-line": "专线", residential: "家宽落地" };

export default function AgentBootstrapDialog({ open, onOpenChange, serverId, serverName, serverKind = "direct", onUpdated }: AgentBootstrapDialogProps) {
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
      if (run === copyEpoch.current) setCopyMessage("已复制。请妥善保护剪贴板和 shell 历史记录。");
    } catch {
      if (run === copyEpoch.current) setCopyMessage("无法访问剪贴板，请选中命令后手动复制。");
    }
  }
  const state = model.state;
  return <Modal open={open} onCancel={() => onOpenChange(false)} title="安装 Agent" width={760}
    destroyOnHidden styles={{ body: { maxHeight: "70dvh", overflowY: "auto" } }}
    footer={<Space wrap>
      <Button aria-label="关闭" onClick={() => onOpenChange(false)}>关闭</Button>
      <Button aria-label="刷新状态" icon={<ReloadOutlined aria-hidden />} disabled={model.busy || model.loading} onClick={() => void model.refresh()}>刷新状态</Button>
    </Space>}>
    {open && <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      {(model.loading || model.busy) && <Spin aria-label="正在检查安装状态" />}
      <Typography.Text strong>{serverName}</Typography.Text>
      <Alert type="info" showIcon title={`服务器类型：${serverKindNames[serverKind]}`}
        description={serverKind === "leased-line" ? "安装完成后，这台服务器仅允许创建 Mieru 节点。" : serverKind === "residential" ? "安装完成后，这台服务器仅允许创建 SOCKS5 节点。" : "请在生成安装命令前确认服务器用途。"} />
      <Typography.Paragraph>在此生成命令，在新的远程服务器上以 root 身份运行，然后等待 Agent 连接。</Typography.Paragraph>
      {model.error && <Alert type="error" showIcon title={zhMessage(model.error)} data-testid="bootstrap-error" />}
      {state && <>
        {!state.configured && <Alert type="warning" showIcon title={zhMessage(state.reason)} />}
        <Descriptions column={1} size="small" items={[
          { key: "url", label: "控制台", children: state.control_url ?? "尚未配置 HTTPS 地址" },
          ...(state.release ? [
            { key: "release", label: "固定发布版本", children: `Agent ${state.release.agent_version}（预览版）· Xray ${state.release.xray_version}` },
            { key: "host", label: "支持的服务器平台", children: state.release.platform },
          ] : []),
        ]} />
        <Space wrap data-testid="bootstrap-status">
          <Tag>{ticketLabels[state.bootstrap.status]}</Tag>
          <Tag color={state.bootstrap.agent_registered ? "success" : "warning"}>
            {state.bootstrap.agent_registered ? "Agent 已注册" : "Agent 尚未注册"}
          </Tag>
        </Space>
        {state.bootstrap.agent_registered ? <Typography.Paragraph>
          {state.bootstrap.agent_version} · 最近在线时间：{date(state.bootstrap.agent_last_seen_at)}。
          {" "}仅完成注册不能证明安装正常，还需检查安装器结果和服务器遥测数据。
        </Typography.Paragraph> : state.bootstrap.claimed_at ? <Alert type="info" showIcon title="凭据已领取"
          description="该服务器已领取凭据，但安装尚未确认完成。如遇中断，只能在原服务器上重试同一条命令，不要复制到另一台服务器。此服务器无法再签发新票据。部分安装状态可能需要在服务器本机恢复。" />
          : state.bootstrap.server_last_heartbeat ? <Alert type="info" showIcon title="已有服务器心跳"
            description={`此服务器已上报心跳（${date(state.bootstrap.server_last_heartbeat)}）。安装票据仅适用于从未连接过的新服务器。请先检查现有服务器，再为新安装创建独立的服务器记录。`} /> : null}
        {model.canIssue && <Form layout="vertical" preserve={false} onFinish={() => void model.issue()}>
          <Typography.Paragraph>安装以非 root 身份运行的托管 Agent 和独立的官方 Xray，不创建公开代理入站。
            {" "}不会接管现有服务，也不会启用 Nginx、WARP、仅嵌入式或分支运行时支持的协议，以及需要远程 root 权限的生命周期管理。
            {" "}服务器需要 Python 3.11+、curl、systemd，并能通过 HTTPS 访问此控制台和 GitHub。
            {" "}缺少的 Python venv 或 CA 软件包可能通过 apt 安装。</Typography.Paragraph>
          <Form.Item label="连接方式"><Select aria-label="连接方式" value={model.transport}
            disabled={model.busy} onChange={model.setTransport} options={[
              { label: "自动（WebSocket，失败时回退 HTTP）", value: "auto" },
              { label: "WebSocket", value: "websocket" }, { label: "HTTP 轮询", value: "http" },
            ]} /></Form.Item>
          <Form.Item><Checkbox checked={model.confirmed} disabled={model.busy}
            onChange={event => model.setConfirmed(event.target.checked)}>我确认使用一台全新的 Debian 12 amd64 服务器，且仅用于此服务器记录。</Checkbox></Form.Item>
          {state.bootstrap.status === "issued" && <Typography.Paragraph type="secondary">再次生成会使之前尚未兑换的命令失效。</Typography.Paragraph>}
          <Button block type="primary" htmlType="submit" aria-label="生成安装命令" data-testid="bootstrap-issue" loading={model.busy}
            disabled={!model.confirmed || model.loading || model.busy}>生成安装命令</Button>
        </Form>}
        {model.command && <Space orientation="vertical" size="middle" style={{ width: "100%" }} data-testid="bootstrap-command">
          <Alert type="warning" showIcon title="私密的短期有效命令" description={<>
            包含有效期为 10 分钟的私密票据，不含长期 Agent 凭据。到期时间：{date(state.bootstrap.expires_at)}。
            {" "}首次兑换后，同一服务器最多可在两分钟内重试。关闭此窗口会清空当前显示的命令；
            {" "}不会清除剪贴板或 shell 历史记录。</>} />
          <Form.Item label="root shell 安装命令" layout="vertical" style={{ marginBottom: 0 }}>
            <Input.TextArea value={model.command} readOnly rows={6} aria-label="root shell 安装命令"
              autoComplete="off" spellCheck={false} style={{ fontFamily: "monospace" }} />
          </Form.Item>
          <Button aria-label="复制命令" icon={<CopyOutlined aria-hidden />} onClick={() => void copyCommand()}>复制命令</Button>
          {copyMessage && <Typography.Text role="status">{copyMessage}</Typography.Text>}
        </Space>}
        {model.canRevoke && <Space orientation="vertical">
          <Button aria-label="撤销安装票据" danger disabled={model.busy || model.loading} onClick={() => void model.revoke()}>撤销安装票据</Button>
          <Typography.Text type="secondary">撤销会阻止后续兑换，但不会撤销已交付的 Agent 凭据，也不会断开 Agent 连接。</Typography.Text>
        </Space>}
      </>}
    </Space>}
  </Modal>;
}
