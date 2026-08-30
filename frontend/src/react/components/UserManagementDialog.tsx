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
    } catch (failure) { if (run === version.current && current === pollVersion.current) setStatusError(failure instanceof Error ? failure.message : "User status unavailable"); }
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
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "User request failed"); }
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
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "User update failed"); }
    finally { if (run === version.current) setBusy(false); }
  }
  function patch(change: Partial<UserSettings>) { setForm(previous => previous ? { ...previous, ...change } : previous); }
  return <Modal open title={removal ? "User removal" : mode === "edit" ? "Edit user" : "Remove user"} width={680} centered styles={{ body: { maxHeight: "calc(100dvh - 200px)", overflowY: "auto" } }} destroyOnHidden
    mask={{ closable: !busy }} keyboard={!busy} closable={!busy} onCancel={() => !busy && onOpenChange(false)}
    footer={<Flex justify="space-between"><Button disabled={busy} onClick={() => onOpenChange(false)}>{saved || removal ? "Close" : "Cancel"}</Button>
      {!removal && <Button type="primary" aria-label={mode === "edit" ? "Save" : "Remove"} aria-busy={busy} danger={mode === "remove"} disabled={!canSubmit} loading={busy} onClick={() => void submit()}>{mode === "edit" ? "Save" : "Remove"}</Button>}</Flex>}>
    <Flex vertical gap="middle">
      <Flex justify="space-between" align="center"><Typography.Text strong>{username}</Typography.Text>{detail && <Tag>{detail.user.role}</Tag>}<Button icon={<ReloadOutlined />} aria-label="Reload user details" disabled={busy || completed.current} onClick={() => void (removal ? poll(version.current) : load())} /></Flex>
      {busy && <Spin />}{error && <Alert type="error" title={error} showIcon />}{saved && <Alert type="success" title="User saved" showIcon />}
      {removal && <Alert type={removal.status === "completed" ? "success" : removal.status === "failed" ? "error" : "info"} title={removal.status === "completed" ? "User removed" : removal.status === "failed" ? "Removal needs attention" : "Removal pending Agent confirmation"} showIcon />}
      {detail && form && !removal && (mode === "edit" ? <Tabs activeKey={tab} onChange={setTab} items={[
        { key: "profile", label: "Profile", children: <Form layout="vertical" preserve={false} disabled={busy}>
          <Form.Item label="Display name"><Input aria-label="Display name" value={form.display_name} maxLength={120} onChange={event => patch({ display_name: event.target.value })} /></Form.Item>
          <Form.Item label="Email"><Input aria-label="Email" value={form.email ?? ""} maxLength={255} onChange={event => patch({ email: event.target.value || null })} /></Form.Item>
          <Form.Item label="Remark"><Input.TextArea aria-label="Remark" value={form.remark} rows={3} maxLength={1000} onChange={event => patch({ remark: event.target.value })} /></Form.Item>
          <Form.Item label="Active"><Switch aria-label="Active" checked={form.is_active} disabled={busy || (detail.user.role === "admin" && form.is_active)} onChange={is_active => patch({ is_active })} /></Form.Item>
        </Form> },
        { key: "limits", label: "Limits", children: <UserLimitEditor key={detail.revision} value={form.limit_overrides} onChange={limit_overrides => patch({ limit_overrides })} nodes={nodes} current={detail.limits} disabled={busy} /> },
      ]} /> : <>
        <Typography.Text>{detail.credential_count} stored credentials</Typography.Text>
        {detail.blockers.map(blocker => <Alert key={blocker} type="error" title={blocker} showIcon />)}
        <Alert type="warning" title="Subscription links stop working immediately. The profile and user traffic ledger are removed after Agent confirmation. Command history, revocation fingerprints, plans and shared nodes remain. Removal cannot be cancelled after confirmation." showIcon />
        <Form.Item label="Confirm username"><Input aria-label="Confirm username" value={confirmName} disabled={busy} onChange={event => setConfirmName(event.target.value)} /></Form.Item>
      </>)}
      {warnings.map(warning => <Alert key={warning} type="warning" title={warning} showIcon />)}
      {detail && !removal && <>
        {mode === "remove" && !!warnings.length && <Checkbox checked={unmanaged} disabled={busy} onChange={event => setUnmanaged(event.target.checked)}>I accept responsibility for unmanaged credential cleanup</Checkbox>}
        <Alert type="warning" title="Runtime changes can restart Xray and disconnect current clients. Offline credentials may still forward until the Agent confirms withdrawal." showIcon />
        <Checkbox checked={acknowledgment} disabled={busy} onChange={event => setAcknowledgment(event.target.checked)}>I accept runtime restarts and pending changes</Checkbox>
      </>}
      {(access || removal) && <section aria-label="User deployment status"><Flex justify="space-between"><Typography.Title level={5}>Agent status</Typography.Title><Button icon={<SyncOutlined />} aria-label="Retry user synchronization" disabled={busy || completed.current} loading={syncing} onClick={() => void poll(version.current, true)} /></Flex>
        {!servers.length && "No managed credentials"}{servers.map(server => <Card size="small" key={server.server_id} title={server.server_name} extra={<Tag color={server.status === "applied" ? "success" : server.status === "failed" ? "error" : "warning"}>{server.status}</Tag>}>
          {server.entries.map(entry => <Flex key={`${entry.inbound_tag}:${entry.email}`} justify="space-between" wrap><span>{entry.inbound_tag}</span><span>{server.status === "applied" ? entry.enabled ? "Enabled" : "Disabled" : entry.enabled ? "Enable requested" : "Disable requested"}</span></Flex>)}
          {server.error && <Alert type="error" title={server.error} showIcon />}
        </Card>)}{statusError && <Alert type="error" title={statusError} showIcon />}
      </section>}
    </Flex>
  </Modal>;
}
