import { CopyOutlined, DownloadOutlined, LogoutOutlined, ReloadOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Checkbox, Descriptions, Flex, Form, Input, Modal, Space, Table, Tag, Typography } from "antd";
import QRCode from "qrcode";
import { useEffect, useRef, useState } from "react";
import type { SubscriptionIpPolicy } from "../../domain/subscriptions";
import {
  beginSubscriberTotp, clearSubscriberSession, confirmSubscriberTotp, revokeSubscriberDevice,
  subscriberChangePassword, subscriberDevices, subscriberIpPolicy, subscriberSecurity, subscriberToken, updateSubscriberTotp,
  type SubscriberDevice, type SubscriberEnrollment, type SubscriberSecurity,
} from "../../services/subscriber-auth";
import { useAsyncScope } from "../hooks/useAsyncScope";
import SubscriptionIpPolicyDialog from "./SubscriptionIpPolicyDialog";

export interface SubscriberSecurityPanelProps { onChanged?: () => void }
type Mode = "password" | "enroll" | "disable" | "recovery" | "link";
const titles = { password: "Change password", enroll: "Two-factor authentication", disable: "Disable two-factor authentication", recovery: "New recovery codes", link: "Reset subscription links" };
const date = (value: string) => new Date(value).toLocaleString();

export default function SubscriberSecurityPanel({ onChanged }: SubscriberSecurityPanelProps) {
  const readScope = useAsyncScope();
  const operationScope = useAsyncScope();
  const busyRef = useRef(false);
  const revokeRef = useRef(false);
  const [security, setSecurity] = useState<SubscriberSecurity | null>(null);
  const [devices, setDevices] = useState<SubscriberDevice[]>([]);
  const [ipPolicy, setIpPolicy] = useState<SubscriptionIpPolicy | null>(null);
  const [ipDialog, setIpDialog] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [mode, setMode] = useState<Mode | null>(null);
  const [busy, setBusy] = useState(false);
  const [dialogError, setDialogError] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [code, setCode] = useState("");
  const [enrollment, setEnrollment] = useState<SubscriberEnrollment | null>(null);
  const [qr, setQr] = useState("");
  const [recovery, setRecovery] = useState<string[]>([]);
  const [accepted, setAccepted] = useState(false);
  const needsCode = Boolean(enrollment || (security?.totp_enabled && mode !== "enroll"));
  const canSubmit = !busy && !recovery.length && (enrollment ? Boolean(code.trim()) : Boolean(password)) && (!needsCode || Boolean(code.trim())) && (mode !== "password" || (newPassword.length >= 12 && newPassword === confirmation));

  async function load() {
    const current = readScope.begin(); setLoading(true); setError("");
    try {
      const [settings, sessions, policy] = await Promise.all([subscriberSecurity(), subscriberDevices(), subscriberIpPolicy()]);
      if (readScope.isCurrent(current)) { setSecurity(settings); setDevices(sessions); setIpPolicy(policy); }
    } catch (failure) {
      if (readScope.isCurrent(current)) setError(failure instanceof Error ? failure.message : "Security settings unavailable");
    } finally { if (readScope.isCurrent(current)) setLoading(false); }
  }
  useEffect(() => { void load(); }, []);
  function clearSecrets() {
    setPassword(""); setNewPassword(""); setConfirmation(""); setCode(""); setEnrollment(null); setQr(""); setRecovery([]); setAccepted(false);
  }
  function open(action: Mode) {
    if (busyRef.current) return;
    operationScope.invalidate(); clearSecrets(); setMode(action); setDialogError("");
  }
  function close() {
    if (busyRef.current || (recovery.length > 0 && !accepted)) return;
    operationScope.invalidate(); setMode(null); clearSecrets(); setDialogError("");
  }
  async function submit() {
    if (!mode || !canSubmit || busyRef.current) return;
    const current = operationScope.begin(); busyRef.current = true; setBusy(true); setDialogError(""); setNotice("");
    try {
      const proof = { password, code };
      let codes: string[] = [];
      if (mode === "enroll") {
        if (!enrollment) {
          const result = await beginSubscriberTotp(password);
          if (!operationScope.isCurrent(current)) return;
          setEnrollment(result); setPassword("");
          const image = await QRCode.toDataURL(result.provisioning_uri, { width: 240, margin: 2 });
          if (operationScope.isCurrent(current)) setQr(image);
          return;
        }
        const result = await confirmSubscriberTotp(code);
        codes = result.recovery_codes;
      } else if (mode === "password") await subscriberChangePassword(proof, newPassword);
      else if (mode === "link") {
        await subscriberToken(proof);
        if (operationScope.isCurrent(current)) { onChanged?.(); setNotice("Subscription links reset"); }
      } else {
        const result = await updateSubscriberTotp(proof, mode === "disable");
        codes = result?.recovery_codes ?? [];
        if (operationScope.isCurrent(current)) setNotice(mode === "disable" ? "Two-factor authentication disabled" : "Recovery codes replaced");
      }
      if (!operationScope.isCurrent(current)) return;
      setRecovery(codes); setEnrollment(null); setQr("");
      setPassword(""); setCode(""); setNewPassword(""); setConfirmation("");
      if (!codes.length) setMode(null);
      if (mode !== "password") await load();
    } catch (failure) {
      if (operationScope.isCurrent(current)) setDialogError(failure instanceof Error ? failure.message : "Security update failed");
    } finally {
      if (operationScope.isCurrent(current)) { setBusy(false); busyRef.current = false; setPassword(""); setCode(""); setNewPassword(""); setConfirmation(""); }
    }
  }
  async function revoke(device?: SubscriberDevice) {
    if (loading || revokeRef.current) return;
    const current = readScope.begin(); revokeRef.current = true; setLoading(true); setError("");
    try {
      await revokeSubscriberDevice(device?.id);
      if (!readScope.isCurrent(current)) return;
      if (device?.current) clearSubscriberSession();
      else await load();
    } catch (failure) {
      if (readScope.isCurrent(current)) setError(failure instanceof Error ? failure.message : "Session revocation failed");
    } finally { revokeRef.current = false; if (readScope.isCurrent(current)) setLoading(false); }
  }
  async function copy(value: string) {
    const current = operationScope.capture();
    try { await navigator.clipboard.writeText(value); }
    catch { if (operationScope.isCurrent(current)) setDialogError("Clipboard unavailable"); }
  }
  function saveRecovery() {
    const url = URL.createObjectURL(new Blob([`${recovery.join("\n")}\n`], { type: "text/plain" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = "open-node-recovery-codes.txt"; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <Space orientation="vertical" size="large" style={{ width: "100%" }}>
    {error && <Alert type="error" showIcon title={error} />}
    {notice && <Alert type="success" showIcon title={notice} closable onClose={() => setNotice("")} />}
    <Card title="Account security" extra={<Button icon={<ReloadOutlined aria-hidden />} aria-label="Refresh security settings" loading={loading} onClick={() => void load()} />}>
      {security && <Space orientation="vertical" size="large" style={{ width: "100%" }}>
        <Flex justify="space-between" align="center" wrap gap="small"><Typography.Text strong>Password</Typography.Text><Button onClick={() => open("password")}>Change password</Button></Flex>
        <Flex justify="space-between" align="center" wrap gap="middle">
          <div><Typography.Text strong>Two-factor authentication</Typography.Text><div><Tag color={security.totp_enabled ? "success" : "default"}>{security.totp_enabled ? "Enabled" : "Not enabled"}</Tag></div>
            {security.totp_enabled && <Typography.Text type="secondary">{security.recovery_codes_remaining} recovery codes remaining</Typography.Text>}
            {!security.totp_enabled && !security.totp_available && <Typography.Text type="secondary">Enrollment unavailable</Typography.Text>}
          </div>
          <Space wrap>{security.totp_enabled ? <><Button onClick={() => open("recovery")}>New recovery codes</Button><Button danger onClick={() => open("disable")}>Disable</Button></> : <Button type="primary" icon={<SafetyCertificateOutlined aria-hidden />} disabled={!security.totp_available} onClick={() => open("enroll")}>Enable</Button>}</Space>
        </Flex>
        <Flex justify="space-between" align="center" wrap gap="small"><Typography.Text strong>Subscription links</Typography.Text><Button onClick={() => open("link")}>Reset links</Button></Flex>
        <Flex justify="space-between" align="center" wrap gap="small"><div><Typography.Text strong>Subscription IP access</Typography.Text><div><Typography.Text type="secondary">{ipPolicy?.enabled ? `${ipPolicy.networks.length} allowed ${ipPolicy.networks.length === 1 ? "range" : "ranges"}` : "Unrestricted"}</Typography.Text></div></div><Button onClick={() => setIpDialog(true)}>Edit access</Button></Flex>
      </Space>}
    </Card>
    <section aria-label="Active sessions"><Card title="Active sessions" extra={<Button icon={<LogoutOutlined aria-hidden />} disabled={loading || devices.length < 2} onClick={() => void revoke()}>Revoke others</Button>}>
      <Table rowKey="id" dataSource={devices} loading={loading} pagination={false} scroll={{ x: 540 }} locale={{ emptyText: "No active sessions" }} columns={[
        { title: "Device", render: (_, device) => <><Space wrap><Typography.Text strong>{device.peer}</Typography.Text>{device.current && <Tag color="blue">Current</Tag>}</Space><Typography.Paragraph type="secondary">{device.user_agent || "Unknown device"}</Typography.Paragraph></> },
        { title: "Activity", width: 250, render: (_, device) => <Descriptions column={1} size="small" items={[
          { key: "created", label: "Signed in", children: date(device.created_at) },
          { key: "seen", label: "Last active", children: date(device.last_seen_at) },
        ]} /> },
        { title: "Action", width: 96, render: (_, device) => <Button icon={<LogoutOutlined aria-hidden />} aria-label={device.current ? "Sign out this device" : `Revoke session ${device.id}`} disabled={loading} onClick={() => void revoke(device)} /> },
      ]} />
    </Card></section>
    <Modal open={mode !== null} title={recovery.length ? "Recovery codes" : titles[mode ?? "password"]} width={560} destroyOnHidden mask={{ closable: !busy && !recovery.length }} keyboard={!busy && !recovery.length} closable={!busy && !recovery.length} onCancel={close} footer={<Space wrap>
      <Button disabled={busy || (recovery.length > 0 && !accepted)} onClick={close}>{recovery.length ? "Done" : "Cancel"}</Button>
      {!recovery.length && <Button type="primary" htmlType="submit" form="subscriber-security-form" aria-label={mode === "enroll" ? enrollment ? "Verify and enable" : "Continue" : "Confirm"} disabled={!canSubmit} loading={busy}>{mode === "enroll" ? enrollment ? "Verify and enable" : "Continue" : "Confirm"}</Button>}
    </Space>}>
      {dialogError && <Alert className="form-alert" type="error" showIcon title={dialogError} />}
      {recovery.length ? <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
        <Alert type="warning" showIcon title="Save these one-time codes securely. Existing recovery codes no longer work." />
        <Flex justify="end" gap="small"><Button icon={<DownloadOutlined aria-hidden />} aria-label="Download recovery codes" onClick={saveRecovery} /><Button icon={<CopyOutlined aria-hidden />} aria-label="Copy recovery codes" onClick={() => void copy(recovery.join("\n"))} /></Flex>
        <div className="recovery-grid" aria-label="Recovery codes">{recovery.map(item => <Typography.Text code key={item}>{item}</Typography.Text>)}</div>
        <Checkbox checked={accepted} onChange={event => setAccepted(event.target.checked)}>I have stored my recovery codes securely</Checkbox>
      </Space> : <Form id="subscriber-security-form" layout="vertical" onFinish={() => void submit()}>
        {mode === "password" && <Alert className="form-alert" type="warning" showIcon title="All sessions will be signed out." />}
        {mode === "link" && <Alert className="form-alert" type="warning" showIcon title="Existing subscription links will stop working." />}
        {mode === "disable" && <Alert className="form-alert" type="warning" showIcon title="Two-factor authentication and recovery codes will be removed. Other sessions will be signed out." />}
        {mode === "recovery" && <Alert className="form-alert" type="warning" showIcon title="Existing recovery codes will stop working. Other sessions will be signed out." />}
        {enrollment ? <>
          {qr && <img src={qr} alt="Authenticator enrollment QR code" width={240} height={240} className="totp-qr" />}
          <Form.Item label="Setup key" htmlFor="subscriber-setup-key"><Space.Compact style={{ width: "100%" }}><Input id="subscriber-setup-key" readOnly value={enrollment.secret} /><Button icon={<CopyOutlined aria-hidden />} aria-label="Copy setup key" onClick={() => void copy(enrollment.secret)} /></Space.Compact></Form.Item>
          <Typography.Paragraph type="secondary">Expires {date(enrollment.expires_at)}</Typography.Paragraph>
        </> : <Form.Item label="Current password" htmlFor="subscriber-security-password" required><Input.Password id="subscriber-security-password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="current-password" required maxLength={1024} disabled={busy} /></Form.Item>}
        {mode === "password" && <>
          <Form.Item label="New password" htmlFor="subscriber-security-new-password" required><Input.Password id="subscriber-security-new-password" value={newPassword} onChange={event => setNewPassword(event.target.value)} autoComplete="new-password" required minLength={12} maxLength={1024} disabled={busy} /></Form.Item>
          <Form.Item label="Confirm password" htmlFor="subscriber-security-confirmation" required validateStatus={confirmation && confirmation !== newPassword ? "error" : undefined} help={confirmation && confirmation !== newPassword ? "Passwords do not match" : undefined}><Input.Password id="subscriber-security-confirmation" value={confirmation} onChange={event => setConfirmation(event.target.value)} autoComplete="new-password" required maxLength={1024} disabled={busy} /></Form.Item>
        </>}
        {needsCode && <Form.Item label={enrollment ? "Authenticator code" : "Authenticator or recovery code"} htmlFor="subscriber-security-code" required><Input id="subscriber-security-code" value={code} onChange={event => setCode(event.target.value)} autoComplete="one-time-code" inputMode={enrollment ? "numeric" : "text"} required maxLength={64} disabled={busy} /></Form.Item>}
      </Form>}
    </Modal>
    <SubscriptionIpPolicyDialog open={ipDialog} onOpenChange={setIpDialog} subscriber onUpdated={setIpPolicy} />
  </Space>;
}
