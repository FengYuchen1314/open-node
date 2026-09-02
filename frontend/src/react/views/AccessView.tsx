import { CopyOutlined, CheckOutlined, ReloadOutlined, LockOutlined } from "../../ui/icons";
import { Alert, Button, Card, Descriptions, Form, Input, Space, Tag, Typography } from "../../ui";
import { useEffect, useRef, useState } from "react";
import type { AgentIdentityInfo } from "../../domain/inventory";
import { changePassword } from "../../services/auth";
import { getAgentIdentity } from "../../services/inventory";
import AdministratorSecurityPanel from "../components/AdministratorSecurityPanel";
import { useAsyncScope } from "../hooks/useAsyncScope";
import { useAdministratorSession } from "../hooks/useSession";
import { zhMessage } from "../../i18n/zh-CN";

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
      if (scope.isCurrent(current)) setIdentityError(zhMessage(cause, "暂时无法获取 Agent 身份。"));
    } finally { if (scope.isCurrent(current)) setIdentityBusy(false); }
  }
  useEffect(() => { void loadIdentity(); }, []);
  async function submit() {
    if (busyRef.current || !currentPassword || newPassword.length < 12 || !confirmation) return;
    setError("");
    if (newPassword !== confirmation) { setError("两次输入的密码不一致。"); return; }
    const current = operationScope.begin(); busyRef.current = true; setBusy(true);
    try { await changePassword(currentPassword, newPassword); }
    catch (cause) { if (operationScope.isCurrent(current)) setError(zhMessage(cause, "修改密码失败。")); }
    finally { if (operationScope.isCurrent(current)) { setCurrentPassword(""); setNewPassword(""); setConfirmation(""); setBusy(false); busyRef.current = false; } }
  }
  async function copyPublicKey() {
    if (!identity?.public_key) return;
    try { await navigator.clipboard.writeText(identity.public_key); setCopied(true); }
    catch { setIdentityError("无法复制公钥，请手动复制。"); }
  }
  return <section className="page-shell">
    <header><Typography.Title level={2}>访问管理</Typography.Title><Typography.Text type="secondary">{auth.session?.username}</Typography.Text></header>
    <Card title="修改密码">
      <Form layout="vertical" className="form-narrow" onFinish={() => void submit()}>
        {error && <Alert className="form-alert" type="error" showIcon title={error} role="alert" />}
        <input value={auth.session?.username ?? ""} type="text" autoComplete="username" readOnly hidden />
        <Form.Item label="当前密码" htmlFor="access-current-password" required><Input.Password id="access-current-password" value={currentPassword} onChange={event => setCurrentPassword(event.target.value)} autoComplete="current-password" required maxLength={1024} disabled={busy} /></Form.Item>
        <Form.Item label="新密码" htmlFor="access-new-password" required><Input.Password id="access-new-password" value={newPassword} onChange={event => setNewPassword(event.target.value)} autoComplete="new-password" required minLength={12} maxLength={1024} disabled={busy} /></Form.Item>
        <Form.Item label="确认新密码" htmlFor="access-confirm-password" required validateStatus={confirmation && confirmation !== newPassword ? "error" : undefined} help={confirmation && confirmation !== newPassword ? "两次输入的密码不一致。" : undefined}><Input.Password id="access-confirm-password" value={confirmation} onChange={event => setConfirmation(event.target.value)} autoComplete="new-password" required minLength={12} maxLength={1024} disabled={busy} /></Form.Item>
        <Button type="primary" htmlType="submit" aria-label="修改密码" icon={<LockOutlined aria-hidden />} loading={busy} disabled={!currentPassword || newPassword.length < 12 || !confirmation}>修改密码</Button>
      </Form>
    </Card>
    <AdministratorSecurityPanel />
    <Card title="旧版 Agent 身份" extra={<Button aria-label="刷新身份信息" icon={<ReloadOutlined aria-hidden />} loading={identityBusy} onClick={() => void loadIdentity()} />}>
      <Space orientation="vertical" style={{ width: "100%" }}>
        {identityError && <Alert type="error" showIcon title={identityError} />}
        {identity && <>
          <Tag color={identity.enabled ? "success" : "default"}>{identity.enabled ? identity.protocol : "未配置"}</Tag>
          {identity.enabled && <Descriptions column={1} items={[
            { key: "key", label: "控制面公钥", children: <Space wrap><Typography.Text code>{identity.public_key}</Typography.Text><Button aria-label="复制公钥" icon={copied ? <CheckOutlined aria-hidden /> : <CopyOutlined aria-hidden />} onClick={() => void copyPublicKey()}>{copied ? "已复制" : "复制"}</Button></Space> },
            { key: "fingerprint", label: "SHA-256 指纹", children: <Typography.Text code>{identity.fingerprint}</Typography.Text> },
          ]} />}
        </>}
      </Space>
    </Card>
  </section>;
}
