import { useLayoutEffect, useRef, useState } from "react";
import { Alert, Button, Checkbox, Descriptions, Form, Input, Modal, Space, Spin, Switch, Typography } from "../../ui";
import { BranchesOutlined, DeleteOutlined, ReloadOutlined, SaveOutlined } from "../../ui/icons";
import { getServerRemoval, getServerSettings, removeServer, updateServerSettings,
  type RemovalPreview, type ServerSettings } from "../../services/server-management";
import { zhMessage } from "../../i18n/zh-CN";
import SharedIngressDialog from "./SharedIngressDialog";

export interface ServerManagementDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  serverId: string;
  mode: "edit" | "remove";
  onUpdated?: () => void;
}
const emptySettings = (): ServerSettings => ({ name: "", ip_address: null, ip_address_v6: null,
  domain: null, domain_v6: null, ipv6_enabled: false });

export default function ServerManagementDialog({ open, onOpenChange, serverId, mode, onUpdated }: ServerManagementDialogProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState("");
  const [preview, setPreview] = useState<RemovalPreview | null>(null);
  const [confirmName, setConfirmName] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [syncHosts, setSyncHosts] = useState(true);
  const [sharedIngressOpen, setSharedIngressOpen] = useState(false);
  const [form, setForm] = useState<ServerSettings>(emptySettings);
  const generation = useRef(0);
  const busyRef = useRef(false);
  const canRemove = Boolean(preview && !preview.blockers.length
    && confirmName === preview.server_name && acknowledged && !busy);

  async function load() {
    const run = ++generation.current;
    setRevision(""); setPreview(null); setConfirmName(""); setAcknowledged(false); setError("");
    setForm(emptySettings());
    if (!open || !serverId) { setBusy(false); busyRef.current = false; return; }
    setBusy(true); busyRef.current = true;
    try {
      if (mode === "remove") {
        const value = await getServerRemoval(serverId);
        if (run === generation.current) setPreview(value);
      } else {
        const value = await getServerSettings(serverId);
        if (run !== generation.current) return;
        setRevision(value.revision);
        setForm({ name: value.server.name, domain: value.server.domain ?? null,
          ip_address: value.server.ip_address ?? null, domain_v6: value.server.domain_v6 ?? null,
          ip_address_v6: value.server.ip_address_v6 ?? null, ipv6_enabled: value.server.ipv6_enabled ?? false });
        setSyncHosts(true);
      }
    } catch (failure) {
      if (run === generation.current) setError(failure instanceof Error ? failure.message : "服务器请求失败");
    } finally {
      if (run === generation.current) { setBusy(false); busyRef.current = false; }
    }
  }
  async function save() {
    if (!open || busyRef.current || !revision || !form.name.trim()) return;
    const run = ++generation.current;
    setBusy(true); busyRef.current = true; setError("");
    try {
      await updateServerSettings(serverId, { ...form }, revision, syncHosts);
      if (run !== generation.current) return;
      onUpdated?.(); onOpenChange(false);
    } catch (failure) {
      if (run === generation.current) setError(failure instanceof Error ? failure.message : "更新服务器失败");
    } finally {
      if (run === generation.current) { setBusy(false); busyRef.current = false; }
    }
  }
  async function remove() {
    if (!open || !canRemove || !preview || busyRef.current) return;
    const run = ++generation.current;
    setBusy(true); busyRef.current = true; setError("");
    try {
      await removeServer(serverId, preview, confirmName);
      if (run !== generation.current) return;
      onUpdated?.(); onOpenChange(false);
    } catch (failure) {
      if (run === generation.current) {
        setError(failure instanceof Error ? failure.message : "删除服务器失败");
        setPreview(null); setAcknowledged(false);
      }
    } finally {
      if (run === generation.current) { setBusy(false); busyRef.current = false; }
    }
  }
  useLayoutEffect(() => {
    busyRef.current = false;
    setSharedIngressOpen(false);
    void load();
    return () => { generation.current += 1; };
  }, [open, serverId, mode]);

  return <Modal open={open} destroyOnHidden width={620} centered mask={{ closable: !busy }} keyboard={!busy}
    closable={!busy} onCancel={() => { if (!busyRef.current) onOpenChange(false); }}
    title={<Space wrap><span>{mode === "edit" ? "编辑服务器" : "删除服务器"}</span>
      <Button type="text" icon={<ReloadOutlined />} aria-label="重新加载服务器详情" disabled={busy} onClick={() => void load()} /></Space>}
    styles={{ body: { maxHeight: "calc(100dvh - 200px)", overflowY: "auto" } }}
    footer={<Space wrap><Button aria-label="取消" disabled={busy} onClick={() => onOpenChange(false)}>取消</Button>
      {mode === "edit" ? <Button type="primary" aria-label="保存" icon={<SaveOutlined aria-hidden />} disabled={busy || !revision || !form.name.trim()}
        onClick={() => void save()}>保存</Button> : <Button danger type="primary" aria-label="删除" icon={<DeleteOutlined aria-hidden />}
          disabled={!canRemove} onClick={() => void remove()}>删除</Button>}</Space>}>
    {open && <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      {busy && <Spin aria-label="正在加载服务器详情" />}
      {error && <Alert type="error" showIcon title={zhMessage(error)} />}
      {mode === "edit" && revision && <Form id="server-settings-form" layout="vertical" preserve={false} onFinish={() => void save()}>
        {([ ["name", "服务器名称"], ["domain", "域名"], ["ip_address", "IPv4 地址"],
          ["domain_v6", "IPv6 域名"], ["ip_address_v6", "IPv6 地址"] ] as const).map(([key, label]) =>
          <Form.Item key={key} label={label}><Input aria-label={label} value={form[key] ?? ""} disabled={busy}
            maxLength={key === "name" ? 120 : undefined} onChange={event => setForm(previous => ({ ...previous, [key]: event.target.value }))} /></Form.Item>)}
        <Form.Item label="启用 IPv6"><Switch aria-label="启用 IPv6" checked={form.ipv6_enabled} disabled={busy}
          onChange={ipv6_enabled => setForm(previous => ({ ...previous, ipv6_enabled }))} /></Form.Item>
        <Checkbox checked={syncHosts} disabled={busy} onChange={event => setSyncHosts(event.target.checked)}>更新匹配的节点地址</Checkbox>
        <div style={{ marginTop: 16 }}><Button icon={<BranchesOutlined aria-hidden />} disabled={busy} onClick={() => setSharedIngressOpen(true)}>443 分流与网站反向代理</Button></div>
      </Form>}
      {mode === "edit" && sharedIngressOpen && <SharedIngressDialog open serverId={serverId} onOpenChange={setSharedIngressOpen} />}
      {mode === "remove" && preview && <>
        <Typography.Text strong>{preview.server_name}</Typography.Text>
        <Alert type="warning" showIcon title="不会停止远端服务"
          description="此操作只删除控制台记录，不会卸载或停止远端 Agent、Xray，也不会中止现有客户端访问。" />
        <Descriptions column={1} size="small" items={[
          { key: "nodes", label: "将删除的节点", children: preview.nodes.length },
          { key: "plans", label: "将更新的套餐", children: preview.plans.length },
          { key: "commands", label: "将删除的命令记录", children: preview.command_count },
          { key: "unfinished", label: "未完成的命令", children: preview.unfinished_command_count },
          { key: "telemetry", label: "将删除的遥测记录", children: preview.telemetry_count },
          { key: "users", label: "将保留用量记录的用户数", children: preview.user_count },
          { key: "changes", label: "将归档的变更集", children: preview.change_sets.length },
          { key: "certificates", label: "将保留的证书", children: preview.certificates.length },
        ]} />
        {([["节点", preview.nodes], ["套餐", preview.plans], ["变更集", preview.change_sets],
          ["证书", preview.certificates]] as const).filter(([, items]) => items.length).map(([label, items]) =>
          <Space key={label} orientation="vertical"><Typography.Text strong>{label}</Typography.Text>
            {items.map(item => <Typography.Text key={item.id}>{item.name}</Typography.Text>)}</Space>)}
        {preview.certificates.length > 0 && <Alert type="info" showIcon title="证书将保留"
          description="此服务器上的部署目标将被删除。由此服务器完成验证的证书会停止自动续期，直到配置新的验证服务器。" />}
        {preview.blockers.map(blocker => <Alert key={blocker} type="error" showIcon title={zhMessage(blocker)} />)}
        <Form layout="vertical"><Form.Item label="确认服务器名称"><Input aria-label="确认服务器名称"
          value={confirmName} disabled={busy || Boolean(preview.blockers.length)} onChange={event => setConfirmName(event.target.value)} /></Form.Item>
          <Checkbox checked={acknowledged} disabled={busy || Boolean(preview.blockers.length)} onChange={event => setAcknowledged(event.target.checked)}>
            我接受远端服务可能继续运行</Checkbox></Form>
      </>}
    </Space>}
  </Modal>;
}
