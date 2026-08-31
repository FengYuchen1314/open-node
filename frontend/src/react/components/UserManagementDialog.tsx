import { zhMessage, zhStatus } from "../../i18n/zh-CN";
import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Checkbox, Flex, Form, Input, Modal, Spin, Switch, Tabs, Tag, Typography } from "antd";
import { ReloadOutlined, SyncOutlined } from "@ant-design/icons";
import type { ManagedNode, SubscriptionAccessResponse } from "../../domain/subscriptions";
import { validUserLimits } from "../../domain/user-limits";
import UserLimitEditor from "./UserLimitEditor";
import { getSubscriptionAccess, syncSubscriptionAccess } from "../../services/subscriptions";
import { getUserManagement, getUserRemoval, removeUser, retryUserRemoval, saveUser, userSettings, type UserManagementRead, type UserOperation, type UserRemoval, type UserSettings } from "../../services/user-management";

export interface UserManagementDialogProps { username: string; mode: UserOperation; removalId: string | null; nodes: ManagedNode[]; open: boolean; onOpenChange: (open: boolean) => void; onUpdated?: () => void }
export default function UserManagementDialog(props: UserManagementDialogProps) { return props.open ? <UserContent key={`${props.username}:${props.mode}:${props.removalId ?? ""}`} {...props} /> : null; }
function UserContent({ username, mode, removalId, nodes, onOpenChange, onUpdated }: UserManagementDialogProps) {
  const [detail, setDetail] = useState<UserManagementRead | null>(null), [form, setForm] = useState<UserSettings | null>(null);
  const [removal, setRemoval] = useState<UserRemoval | null>(null), [access, setAccess] = useState<SubscriptionAccessResponse | null>(null);
  const [busy, setBusy] = useState(false), [syncing, setSyncing] = useState(false), [saved, setSaved] = useState(false);
  const [error, setError] = useState(""), [statusError, setStatusError] = useState(""), [confirmName, setConfirmName] = useState("");
  const [acknowledgment, setAcknowledgment] = useState(false), [unmanaged, setUnmanaged] = useState(false), [tab, setTab] = useState("profile");
  const version = useRef(0), pollVersion = useRef(0), timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined), removalRef = useRef<UserRemoval | null>(null), completed = useRef(false);
  const updated = useRef(onUpdated); updated.current = onUpdated;
  function stop() { clearTimeout(timer.current); ++pollVersion.current; }
  function acceptRemoval(value: UserRemoval) {
    removalRef.current = value; setRemoval(value);
    if (value.status === "completed" && !completed.current) { completed.current = true; updated.current?.(); }
  }
  async function poll(run: number, retry = false) {
    clearTimeout(timer.current); const current = ++pollVersion.current; setSyncing(retry);
    try {
      if (removalRef.current) {
        const value = await (retry ? retryUserRemoval : getUserRemoval)(removalRef.current.id);
        if (run !== version.current || current !== pollVersion.current) return; acceptRemoval(value);
      } else {
        const value = await (retry ? syncSubscriptionAccess : getSubscriptionAccess)(username);
        if (run !== version.current || current !== pollVersion.current) return; setAccess(value);
      }
      setStatusError("");
    } catch (failure) { if (run === version.current && current === pollVersion.current) setStatusError(failure instanceof Error ? failure.message : "无法读取用户状态"); }
    finally {
      if (run === version.current && current === pollVersion.current) { setSyncing(false); if (!completed.current) timer.current = setTimeout(() => void poll(run), 5000); }
    }
  }
  async function load() {
    const run = ++version.current; stop(); removalRef.current = null; completed.current = false;
    setDetail(null); setForm(null); setRemoval(null); setAccess(null); setSaved(false); setError(""); setStatusError(""); setConfirmName(""); setAcknowledgment(false); setUnmanaged(false); setTab("profile"); setSyncing(false); setBusy(true);
    try {
      if (removalId) { const value = await getUserRemoval(removalId); if (run !== version.current) return; acceptRemoval(value); }
      else {
        const value = await getUserManagement(username); if (run !== version.current) return;
        setDetail(value); setForm(userSettings(value.user)); setAccess(value.access);
        if (value.user.removal_id) { const job = await getUserRemoval(value.user.removal_id); if (run !== version.current) return; acceptRemoval(job); }
      }
      if (!completed.current) timer.current = setTimeout(() => void poll(run), 5000);
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "请求用户信息失败"); }
    finally { if (run === version.current) setBusy(false); }
  }
  useEffect(() => { void load(); return () => { ++version.current; stop(); }; }, []);
  const warnings = removal?.warnings ?? detail?.warnings ?? [], servers = removal?.servers ?? access?.servers ?? [];
  const canSubmit = !busy && !!detail && !!form && !removal && acknowledgment
    && (mode === "edit" ? !!form.display_name.trim() && validUserLimits(form.limit_overrides) : !detail.blockers.length && confirmName === username && (!warnings.length || unmanaged));
  async function submit() {
    if (!canSubmit || !detail || !form) return;
    const run = ++version.current; stop(); setSyncing(false); setBusy(true); setError("");
    try {
      if (mode === "edit") {
        const value = await saveUser(username, form, detail.revision); if (run !== version.current) return;
        setDetail(value); setForm(userSettings(value.user)); setAccess(value.access); setSaved(true); updated.current?.();
      } else {
        const value = await removeUser(username, detail.revision, confirmName, unmanaged); if (run !== version.current) return;
        acceptRemoval(value); updated.current?.();
      }
      setAcknowledgment(false); if (!completed.current) void poll(run);
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "更新用户失败"); }
    finally { if (run === version.current) setBusy(false); }
  }
  function patch(change: Partial<UserSettings>) { setForm(previous => previous ? { ...previous, ...change } : previous); }
  return <Modal open title={removal ? "用户移除" : mode === "edit" ? "编辑用户" : "移除用户"} width={680} centered styles={{ body: { maxHeight: "calc(100dvh - 200px)", overflowY: "auto" } }} destroyOnHidden
    mask={{ closable: !busy }} keyboard={!busy} closable={!busy} onCancel={() => !busy && onOpenChange(false)}
    footer={<Flex justify="space-between"><Button disabled={busy} onClick={() => onOpenChange(false)}>{saved || removal ? "关闭" : "取消"}</Button>
      {!removal && <Button type="primary" aria-label={mode === "edit" ? "保存" : "移除"} aria-busy={busy} danger={mode === "remove"} disabled={!canSubmit} loading={busy} onClick={() => void submit()}>{mode === "edit" ? "保存" : "移除"}</Button>}</Flex>}>
    <Flex vertical gap="middle">
      <Flex justify="space-between" align="center"><Typography.Text strong>{username}</Typography.Text>{detail && <Tag>{zhStatus(detail.user.role)}</Tag>}<Button icon={<ReloadOutlined />} aria-label="重新加载用户详情" disabled={busy || completed.current} onClick={() => void (removal ? poll(version.current) : load())} /></Flex>
      {busy && <Spin />}{error && <Alert type="error" title={zhMessage(error)} showIcon />}{saved && <Alert type="success" title="用户已保存" showIcon />}
      {removal && <Alert type={removal.status === "completed" ? "success" : removal.status === "failed" ? "error" : "info"} title={removal.status === "completed" ? "用户已移除" : removal.status === "failed" ? "移除操作需要处理" : "正在等待 Agent 确认移除"} showIcon />}
      {detail && form && !removal && (mode === "edit" ? <Tabs activeKey={tab} onChange={setTab} items={[
        { key: "profile", label: "用户资料", children: <Form layout="vertical" preserve={false} disabled={busy}>
          <Form.Item label="显示名称"><Input aria-label="显示名称" value={form.display_name} maxLength={120} onChange={event => patch({ display_name: event.target.value })} /></Form.Item>
          <Form.Item label="电子邮箱"><Input aria-label="电子邮箱" value={form.email ?? ""} maxLength={255} onChange={event => patch({ email: event.target.value || null })} /></Form.Item>
          <Form.Item label="备注"><Input.TextArea aria-label="备注" value={form.remark} rows={3} maxLength={1000} onChange={event => patch({ remark: event.target.value })} /></Form.Item>
          <Form.Item label="启用用户"><Switch aria-label="启用用户" checked={form.is_active} disabled={busy || (detail.user.role === "admin" && form.is_active)} onChange={is_active => patch({ is_active })} /></Form.Item>
        </Form> },
        { key: "limits", label: "限制", children: <UserLimitEditor key={detail.revision} value={form.limit_overrides} onChange={limit_overrides => patch({ limit_overrides })} nodes={nodes} current={detail.limits} disabled={busy} /> },
      ]} /> : <>
        <Typography.Text>{detail.credential_count} 份已保存的凭据</Typography.Text>
        {detail.blockers.map(blocker => <Alert key={blocker} type="error" title={zhMessage(blocker)} showIcon />)}
        <Alert type="warning" title="订阅链接会立即失效。Agent 确认后，将移除用户资料及流量账本。命令历史、撤销指纹、套餐和共享节点会保留。确认后无法取消移除。" showIcon />
        <Form.Item label="确认用户名"><Input aria-label="确认用户名" value={confirmName} disabled={busy} onChange={event => setConfirmName(event.target.value)} /></Form.Item>
      </>)}
      {warnings.map(warning => <Alert key={warning} type="warning" title={zhMessage(warning)} showIcon />)}
      {detail && !removal && <>
        {mode === "remove" && !!warnings.length && <Checkbox checked={unmanaged} disabled={busy} onChange={event => setUnmanaged(event.target.checked)}>我接受自行清理未托管凭据的责任</Checkbox>}
        <Alert type="warning" title="运行时变更可能重启 Xray 并断开当前客户端。在 Agent 确认撤销前，离线节点上的凭据仍可能继续转发流量。" showIcon />
        <Checkbox checked={acknowledgment} disabled={busy} onChange={event => setAcknowledgment(event.target.checked)}>我接受运行时重启及变更待确认的影响</Checkbox>
      </>}
      {(access || removal) && <section aria-label="用户部署状态"><Flex justify="space-between"><Typography.Title level={5}>Agent 状态</Typography.Title><Button icon={<SyncOutlined />} aria-label="重试同步用户" disabled={busy || completed.current} loading={syncing} onClick={() => void poll(version.current, true)} /></Flex>
        {!servers.length && "暂无托管凭据"}{servers.map(server => <Card size="small" key={server.server_id} title={server.server_name} extra={<Tag color={server.status === "applied" ? "success" : server.status === "failed" ? "error" : "warning"}>{zhStatus(server.status)}</Tag>}>
          {server.entries.map(entry => <Flex key={`${entry.inbound_tag}:${entry.email}`} justify="space-between" wrap><span>{entry.inbound_tag}</span><span>{server.status === "applied" ? entry.enabled ? "已启用" : "已停用" : entry.enabled ? "已请求启用" : "已请求停用"}</span></Flex>)}
          {server.error && <Alert type="error" title={zhMessage(server.error)} showIcon />}
        </Card>)}{statusError && <Alert type="error" title={zhMessage(statusError)} showIcon />}
      </section>}
    </Flex>
  </Modal>;
}
