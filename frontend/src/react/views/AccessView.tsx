import { CopyOutlined, CheckOutlined, ReloadOutlined, LockOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Descriptions, Form, Input, Space, Tag, Typography } from "antd";
import { useEffect, useRef, useState } from "react";
import type { AgentIdentityInfo } from "../../domain/inventory";
import { changePassword } from "../../services/auth";
import { getAgentIdentity } from "../../services/inventory";
import AdministratorSecurityPanel from "../components/AdministratorSecurityPanel";
import { useAsyncScope } from "../hooks/useAsyncScope";
import { useAdministratorSession } from "../hooks/useSession";

export default function AccessView() {
  const auth = useAdministratorSession();
  const scope = useAsyncScope();
  const operationScope = useAsyncScope();
  const busyRef = useRef(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [identity, setIdentity] = useState<AgentIdentityInfo | null>(null);
  const [identityBusy, setIdentityBusy] = useState(false);
  const [identityError, setIdentityError] = useState("");
  const [copied, setCopied] = useState(false);

  async function loadIdentity() {
    const current = scope.begin(); setIdentityBusy(true); setIdentityError(""); setCopied(false);
    try {
      const result = await getAgentIdentity();
      if (scope.isCurrent(current)) setIdentity(result);
    } catch (cause) {
      if (scope.isCurrent(current)) setIdentityError(cause instanceof Error ? cause.message : "Agent identity unavailable");
    } finally { if (scope.isCurrent(current)) setIdentityBusy(false); }
  }
  useEffect(() => { void loadIdentity(); }, []);
  async function submit() {
    if (busyRef.current || !currentPassword || newPassword.length < 12 || !confirmation) return;
    setError("");
    if (newPassword !== confirmation) { setError("Passwords do not match"); return; }
    const current = operationScope.begin(); busyRef.current = true; setBusy(true);
    try { await changePassword(currentPassword, newPassword); }
    catch (cause) { if (operationScope.isCurrent(current)) setError(cause instanceof Error ? cause.message : "Password change failed"); }
    finally { if (operationScope.isCurrent(current)) { setCurrentPassword(""); setNewPassword(""); setConfirmation(""); setBusy(false); busyRef.current = false; } }
  }
  async function copyPublicKey() {
    if (!identity?.public_key) return;
    try { await navigator.clipboard.writeText(identity.public_key); setCopied(true); }
    catch { setIdentityError("Could not copy public key"); }
  }
  return <section className="page-shell">
    <header><Typography.Title level={2}>Access</Typography.Title><Typography.Text type="secondary">{auth.session?.username}</Typography.Text></header>
    <Card title="Change Password">
      <Form layout="vertical" className="form-narrow" onFinish={() => void submit()}>
        {error && <Alert className="form-alert" type="error" showIcon title={error} role="alert" />}
        <input value={auth.session?.username ?? ""} type="text" autoComplete="username" readOnly hidden />
        <Form.Item label="Current password" htmlFor="access-current-password" required><Input.Password id="access-current-password" value={currentPassword} onChange={event => setCurrentPassword(event.target.value)} autoComplete="current-password" required maxLength={1024} disabled={busy} /></Form.Item>
        <Form.Item label="New password" htmlFor="access-new-password" required><Input.Password id="access-new-password" value={newPassword} onChange={event => setNewPassword(event.target.value)} autoComplete="new-password" required minLength={12} maxLength={1024} disabled={busy} /></Form.Item>
        <Form.Item label="Confirm new password" htmlFor="access-confirm-password" required validateStatus={confirmation && confirmation !== newPassword ? "error" : undefined} help={confirmation && confirmation !== newPassword ? "Passwords do not match" : undefined}><Input.Password id="access-confirm-password" value={confirmation} onChange={event => setConfirmation(event.target.value)} autoComplete="new-password" required minLength={12} maxLength={1024} disabled={busy} /></Form.Item>
        <Button type="primary" htmlType="submit" aria-label="Change Password" icon={<LockOutlined aria-hidden />} loading={busy} disabled={!currentPassword || newPassword.length < 12 || !confirmation}>Change Password</Button>
      </Form>
    </Card>
    <AdministratorSecurityPanel />
    <Card title="Legacy Agent identity" extra={<Button aria-label="Refresh identity" icon={<ReloadOutlined aria-hidden />} loading={identityBusy} onClick={() => void loadIdentity()} />}>
      <Space orientation="vertical" style={{ width: "100%" }}>
        {identityError && <Alert type="error" showIcon title={identityError} />}
        {identity && <>
          <Tag color={identity.enabled ? "success" : "default"}>{identity.enabled ? identity.protocol : "Not configured"}</Tag>
          {identity.enabled && <Descriptions column={1} items={[
            { key: "key", label: "Master public key", children: <Space wrap><Typography.Text code>{identity.public_key}</Typography.Text><Button aria-label="Copy public key" icon={copied ? <CheckOutlined aria-hidden /> : <CopyOutlined aria-hidden />} onClick={() => void copyPublicKey()}>{copied ? "Copied" : "Copy"}</Button></Space> },
            { key: "fingerprint", label: "SHA-256 fingerprint", children: <Typography.Text code>{identity.fingerprint}</Typography.Text> },
          ]} />}
        </>}
      </Space>
    </Card>
  </section>;
}
