import { CopyOutlined, CheckOutlined, ReloadOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Checkbox, Descriptions, Flex, Form, Input, Modal, Space, Tag, Typography } from "antd";
import QRCode from "qrcode";
import { useEffect, useRef, useState } from "react";
import {
  administratorSecurity, beginAdministratorTotp, confirmAdministratorTotp, disableAdministratorTotp,
  regenerateAdministratorRecoveryCodes, updateAdministratorTotpPolicy,
  type AdministratorSecurity, type AdministratorTotpEnrollment,
} from "../../services/auth";
import { useAsyncScope } from "../hooks/useAsyncScope";

type Mode = "enroll" | "disable" | "recovery" | "policy";
export default function AdministratorSecurityPanel() {
  const readScope = useAsyncScope();
  const operationScope = useAsyncScope();
  const busyRef = useRef(false);
  const [security, setSecurity] = useState<AdministratorSecurity | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dialogError, setDialogError] = useState("");
  const [mode, setMode] = useState<Mode | null>(null);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [enrollment, setEnrollment] = useState<AdministratorTotpEnrollment | null>(null);
  const [qr, setQr] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [accepted, setAccepted] = useState(false);
  const [requiredTarget, setRequiredTarget] = useState(false);
  const [copied, setCopied] = useState(false);
  const title = ({ enroll: "Enable administrator two-factor authentication", disable: "Disable administrator two-factor authentication", recovery: "Generate new administrator recovery codes", policy: requiredTarget ? "Require administrator 2FA" : "Make administrator 2FA optional" })[mode ?? "enroll"];
  const canSubmit = !busy && !recoveryCodes.length && (enrollment ? Boolean(code.trim()) : Boolean(password)) && (!(enrollment || mode !== "enroll") || Boolean(code.trim()));

  async function load() {
    const current = readScope.begin(); setLoading(true); setError("");
    try {
      const result = await administratorSecurity();
      if (readScope.isCurrent(current)) setSecurity(result);
    } catch (cause) {
      if (readScope.isCurrent(current)) setError(cause instanceof Error ? cause.message : "Administrator security settings unavailable");
    } finally { if (readScope.isCurrent(current)) setLoading(false); }
  }
  useEffect(() => { void load(); }, []);
  function clearSecrets() {
    setPassword(""); setCode(""); setEnrollment(null); setQr(""); setRecoveryCodes([]); setAccepted(false); setCopied(false);
  }
  function open(selected: Mode, required = false) {
    if (busyRef.current) return;
    operationScope.invalidate(); clearSecrets(); setMode(selected); setRequiredTarget(required); setDialogError("");
  }
  function close() {
    if (busyRef.current || (recoveryCodes.length > 0 && !accepted)) return;
    operationScope.invalidate(); setMode(null); clearSecrets(); setDialogError("");
  }
  async function submit() {
    if (!mode || busyRef.current || !canSubmit) return;
    const current = operationScope.begin(); busyRef.current = true; setBusy(true); setDialogError("");
    try {
      if (mode === "enroll" && !enrollment) {
        const result = await beginAdministratorTotp(password);
        if (!operationScope.isCurrent(current)) return;
        setEnrollment(result); setPassword("");
        const image = await QRCode.toDataURL(result.provisioning_uri, { width: 240, margin: 1 });
        if (operationScope.isCurrent(current)) setQr(image);
        return;
      }
      let codes: string[] = [];
      if (mode === "enroll") codes = await confirmAdministratorTotp(code);
      else if (mode === "disable") await disableAdministratorTotp(password, code);
      else if (mode === "recovery") codes = await regenerateAdministratorRecoveryCodes(password, code);
      else {
        const result = await updateAdministratorTotpPolicy(requiredTarget, password, code);
        if (operationScope.isCurrent(current)) setSecurity(result);
      }
      if (!operationScope.isCurrent(current)) return;
      setRecoveryCodes(codes); setEnrollment(null); setQr("");
      if (!codes.length) setMode(null);
      await load();
    } catch (cause) {
      if (operationScope.isCurrent(current)) setDialogError(cause instanceof Error ? cause.message : "Security update failed");
    } finally {
      if (operationScope.isCurrent(current)) { setPassword(""); setCode(""); setBusy(false); busyRef.current = false; }
    }
  }
  async function copyCodes() {
    const current = operationScope.capture();
    try { await navigator.clipboard.writeText(recoveryCodes.join("\n")); if (operationScope.isCurrent(current)) setCopied(true); }
    catch { if (operationScope.isCurrent(current)) setDialogError("Could not copy recovery codes"); }
  }
  return <section aria-label="Administrator security">
    <Card title="Administrator security" extra={<Button icon={<ReloadOutlined aria-hidden />} aria-label="Refresh administrator security" loading={loading} onClick={() => void load()} />}>
      <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
        <Typography.Paragraph type="secondary">Protect control-plane access with an authenticator and one-time recovery codes.</Typography.Paragraph>
        {error && <Alert type="error" showIcon title={error} />}
        {security && <>
          <Flex gap="middle" justify="space-between" wrap align="center">
            <Descriptions column={1} size="small" items={[
              { key: "totp", label: "Two-factor authentication", children: <Tag color={security.totp_enabled ? "success" : "default"}>{security.totp_enabled ? "Enabled" : "Not enabled"}</Tag> },
              ...(security.totp_enabled ? [{ key: "remaining", label: "Recovery", children: `${security.recovery_codes_remaining} recovery codes remaining` }] : []),
            ]} />
            <Space wrap>{security.totp_enabled ? <><Button onClick={() => open("recovery")}>New recovery codes</Button><Button danger disabled={security.require_totp} onClick={() => open("disable")}>Disable</Button></> : <Button type="primary" icon={<SafetyCertificateOutlined aria-hidden />} disabled={!security.totp_available} onClick={() => open("enroll")}>Enable</Button>}</Space>
          </Flex>
          {!security.totp_enabled && !security.totp_available && <Alert type="warning" showIcon title="Enrollment unavailable because the TOTP encryption key is not configured." />}
          <Flex gap="middle" justify="space-between" wrap align="center">
            <div><Typography.Text strong>Mandatory administrator 2FA</Typography.Text><Typography.Paragraph type="secondary">{security.require_totp ? "Every password login must complete a second-factor challenge." : "Administrators may sign in with a password when 2FA is not enrolled."}</Typography.Paragraph></div>
            <Button disabled={!security.totp_enabled} onClick={() => open("policy", !security.require_totp)}>{security.require_totp ? "Make optional" : "Require 2FA"}</Button>
          </Flex>
        </>}
      </Space>
    </Card>
    <Modal open={mode !== null} title={title} width={620} destroyOnHidden mask={{ closable: false }} keyboard={false} closable={false} onCancel={close} footer={<Space wrap>
      <Button disabled={busy || (recoveryCodes.length > 0 && !accepted)} onClick={close}>{recoveryCodes.length ? "Done" : "Cancel"}</Button>
      {!recoveryCodes.length && <Button type="primary" htmlType="submit" form="administrator-security-form" aria-label={mode === "enroll" && !enrollment ? "Start enrollment" : "Confirm"} loading={busy} disabled={!canSubmit}>{mode === "enroll" && !enrollment ? "Start enrollment" : "Confirm"}</Button>}
    </Space>}>
      {dialogError && <Alert className="form-alert" type="error" showIcon title={dialogError} />}
      {recoveryCodes.length ? <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
        <Alert type="success" showIcon title="Store these codes now. Existing recovery codes no longer work." />
        <Flex justify="space-between" gap="small" wrap><Typography.Text strong>One-time recovery codes</Typography.Text><Button icon={copied ? <CheckOutlined aria-hidden /> : <CopyOutlined aria-hidden />} onClick={() => void copyCodes()}>{copied ? "Copied" : "Copy"}</Button></Flex>
        <div className="recovery-grid" aria-label="Administrator recovery codes">{recoveryCodes.map(item => <Typography.Text code key={item}>{item}</Typography.Text>)}</div>
        <Checkbox checked={accepted} onChange={event => setAccepted(event.target.checked)}>I have stored the recovery codes securely</Checkbox>
      </Space> : <Form id="administrator-security-form" layout="vertical" onFinish={() => void submit()}>
        {enrollment && <>
          <Alert className="form-alert" type="info" showIcon title="Scan the QR code, then enter the current six-digit code." />
          {qr && <img src={qr} alt="Administrator authenticator enrollment QR code" width={240} height={240} className="totp-qr" />}
          <Form.Item label="Authenticator secret" htmlFor="administrator-security-secret"><Input id="administrator-security-secret" readOnly value={enrollment.secret} /></Form.Item>
        </>}
        {mode === "disable" && <Alert className="form-alert" type="warning" showIcon title="Disabling 2FA removes all recovery codes. Other administrator sessions will be revoked." />}
        {mode === "recovery" && <Alert className="form-alert" type="warning" showIcon title="Generating new codes invalidates every existing recovery code and revokes other sessions." />}
        {mode === "policy" && <Alert className="form-alert" type="info" showIcon title="Confirm this policy change with the administrator password and a current authenticator or recovery code." />}
        {!enrollment && <Form.Item label="Current password" htmlFor="administrator-security-password" required><Input.Password id="administrator-security-password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="current-password" required maxLength={1024} disabled={busy} /></Form.Item>}
        {(enrollment || mode !== "enroll") && <Form.Item label={enrollment ? "Authenticator code" : "Authenticator or recovery code"} htmlFor="administrator-security-code" required><Input id="administrator-security-code" value={code} onChange={event => setCode(event.target.value)} autoComplete="one-time-code" inputMode={enrollment ? "numeric" : "text"} required maxLength={64} disabled={busy} /></Form.Item>}
      </Form>}
    </Modal>
  </section>;
}
