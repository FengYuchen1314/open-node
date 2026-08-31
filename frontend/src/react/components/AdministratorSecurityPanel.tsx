import { zhMessage } from "../../i18n/zh-CN";
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
  const title = ({ enroll: "启用管理员双因素认证", disable: "停用管理员双因素认证", recovery: "生成新的管理员恢复码", policy: requiredTarget ? "强制管理员使用双因素认证" : "将管理员双因素认证设为可选" })[mode ?? "enroll"];
  const canSubmit = !busy && !recoveryCodes.length && (enrollment ? Boolean(code.trim()) : Boolean(password)) && (!(enrollment || mode !== "enroll") || Boolean(code.trim()));

  async function load() {
    const current = readScope.begin(); setLoading(true); setError("");
    try {
      const result = await administratorSecurity();
      if (readScope.isCurrent(current)) setSecurity(result);
    } catch (cause) {
      if (readScope.isCurrent(current)) setError(cause instanceof Error ? cause.message : "无法读取管理员安全设置");
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
      if (operationScope.isCurrent(current)) setDialogError(cause instanceof Error ? cause.message : "更新安全设置失败");
    } finally {
      if (operationScope.isCurrent(current)) { setPassword(""); setCode(""); setBusy(false); busyRef.current = false; }
    }
  }
  async function copyCodes() {
    const current = operationScope.capture();
    try { await navigator.clipboard.writeText(recoveryCodes.join("\n")); if (operationScope.isCurrent(current)) setCopied(true); }
    catch { if (operationScope.isCurrent(current)) setDialogError("无法复制恢复码"); }
  }
  return <section aria-label="管理员安全">
    <Card title="管理员安全" extra={<Button icon={<ReloadOutlined aria-hidden />} aria-label="刷新管理员安全设置" loading={loading} onClick={() => void load()} />}>
      <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
        <Typography.Paragraph type="secondary">使用验证器和一次性恢复码保护控制台访问。</Typography.Paragraph>
        {error && <Alert type="error" showIcon title={zhMessage(error)} />}
        {security && <>
          <Flex gap="middle" justify="space-between" wrap align="center">
            <Descriptions column={1} size="small" items={[
              { key: "totp", label: "双因素认证", children: <Tag color={security.totp_enabled ? "success" : "default"}>{security.totp_enabled ? "已启用" : "未启用"}</Tag> },
              ...(security.totp_enabled ? [{ key: "remaining", label: "恢复方式", children: `剩余 ${security.recovery_codes_remaining} 个恢复码` }] : []),
            ]} />
            <Space wrap>{security.totp_enabled ? <><Button onClick={() => open("recovery")}>生成新恢复码</Button><Button aria-label="停用" danger disabled={security.require_totp} onClick={() => open("disable")}>停用</Button></> : <Button type="primary" icon={<SafetyCertificateOutlined aria-hidden />} aria-label="启用" disabled={!security.totp_available} onClick={() => open("enroll")}>启用</Button>}</Space>
          </Flex>
          {!security.totp_enabled && !security.totp_available && <Alert type="warning" showIcon title="尚未配置 TOTP 加密密钥，无法设置双因素认证。" />}
          <Flex gap="middle" justify="space-between" wrap align="center">
            <div><Typography.Text strong>强制管理员双因素认证</Typography.Text><Typography.Paragraph type="secondary">{security.require_totp ? "每次使用密码登录都必须完成第二因素验证。" : "未设置双因素认证时，管理员可以仅凭密码登录。"}</Typography.Paragraph></div>
            <Button disabled={!security.totp_enabled} onClick={() => open("policy", !security.require_totp)}>{security.require_totp ? "设为可选" : "强制双因素认证"}</Button>
          </Flex>
        </>}
      </Space>
    </Card>
    <Modal open={mode !== null} title={title} width={620} destroyOnHidden mask={{ closable: false }} keyboard={false} closable={false} onCancel={close} footer={<Space wrap>
      <Button aria-label={recoveryCodes.length ? "完成" : "取消"} disabled={busy || (recoveryCodes.length > 0 && !accepted)} onClick={close}>{recoveryCodes.length ? "完成" : "取消"}</Button>
      {!recoveryCodes.length && <Button type="primary" htmlType="submit" form="administrator-security-form" aria-label={mode === "enroll" && !enrollment ? "开始设置" : "确认"} loading={busy} disabled={!canSubmit}>{mode === "enroll" && !enrollment ? "开始设置" : "确认"}</Button>}
    </Space>}>
      {dialogError && <Alert className="form-alert" type="error" showIcon title={zhMessage(dialogError)} />}
      {recoveryCodes.length ? <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
        <Alert type="success" showIcon title="请立即保存这些恢复码。原有恢复码已失效。" />
        <Flex justify="space-between" gap="small" wrap><Typography.Text strong>一次性恢复码</Typography.Text><Button icon={copied ? <CheckOutlined aria-hidden /> : <CopyOutlined aria-hidden />} onClick={() => void copyCodes()}>{copied ? "已复制" : "复制"}</Button></Flex>
        <div className="recovery-grid" aria-label="管理员恢复码">{recoveryCodes.map(item => <Typography.Text code key={item}>{item}</Typography.Text>)}</div>
        <Checkbox checked={accepted} onChange={event => setAccepted(event.target.checked)}>我已妥善保存恢复码</Checkbox>
      </Space> : <Form id="administrator-security-form" layout="vertical" onFinish={() => void submit()}>
        {enrollment && <>
          <Alert className="form-alert" type="info" showIcon title="扫描二维码，然后输入当前的六位验证码。" />
          {qr && <img src={qr} alt="管理员验证器绑定二维码" width={240} height={240} className="totp-qr" />}
          <Form.Item label="验证器密钥" htmlFor="administrator-security-secret"><Input id="administrator-security-secret" readOnly value={enrollment.secret} /></Form.Item>
        </>}
        {mode === "disable" && <Alert className="form-alert" type="warning" showIcon title="停用双因素认证会移除所有恢复码，并撤销其他管理员会话。" />}
        {mode === "recovery" && <Alert className="form-alert" type="warning" showIcon title="生成新恢复码会使全部现有恢复码失效，并撤销其他会话。" />}
        {mode === "policy" && <Alert className="form-alert" type="info" showIcon title="请使用管理员密码和当前验证器验证码或恢复码，确认此策略变更。" />}
        {!enrollment && <Form.Item label="当前密码" htmlFor="administrator-security-password" required><Input.Password id="administrator-security-password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="current-password" required maxLength={1024} disabled={busy} /></Form.Item>}
        {(enrollment || mode !== "enroll") && <Form.Item label={enrollment ? "验证器验证码" : "验证器验证码或恢复码"} htmlFor="administrator-security-code" required><Input id="administrator-security-code" value={code} onChange={event => setCode(event.target.value)} autoComplete="one-time-code" inputMode={enrollment ? "numeric" : "text"} required maxLength={64} disabled={busy} /></Form.Item>}
      </Form>}
    </Modal>
  </section>;
}
