import { zhMessage } from "../../i18n/zh-CN";
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
const titles = { password: "修改密码", enroll: "双因素认证", disable: "停用双因素认证", recovery: "生成新恢复码", link: "重置订阅链接" };
const date = (value: string) => new Date(value).toLocaleString("zh-CN");

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
      if (readScope.isCurrent(current)) setError(failure instanceof Error ? failure.message : "无法读取安全设置");
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
        if (operationScope.isCurrent(current)) { onChanged?.(); setNotice("订阅链接已重置"); }
      } else {
        const result = await updateSubscriberTotp(proof, mode === "disable");
        codes = result?.recovery_codes ?? [];
        if (operationScope.isCurrent(current)) setNotice(mode === "disable" ? "双因素认证已停用" : "恢复码已更换");
      }
      if (!operationScope.isCurrent(current)) return;
      setRecovery(codes); setEnrollment(null); setQr("");
      setPassword(""); setCode(""); setNewPassword(""); setConfirmation("");
      if (!codes.length) setMode(null);
      if (mode !== "password") await load();
    } catch (failure) {
      if (operationScope.isCurrent(current)) setDialogError(failure instanceof Error ? failure.message : "更新安全设置失败");
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
      if (readScope.isCurrent(current)) setError(failure instanceof Error ? failure.message : "撤销会话失败");
    } finally { revokeRef.current = false; if (readScope.isCurrent(current)) setLoading(false); }
  }
  async function copy(value: string) {
    const current = operationScope.capture();
    try { await navigator.clipboard.writeText(value); }
    catch { if (operationScope.isCurrent(current)) setDialogError("无法使用剪贴板"); }
  }
  function saveRecovery() {
    const url = URL.createObjectURL(new Blob([`${recovery.join("\n")}\n`], { type: "text/plain" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = "open-node-recovery-codes.txt"; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <Space orientation="vertical" size="large" style={{ width: "100%" }}>
    {error && <Alert type="error" showIcon title={zhMessage(error)} />}
    {notice && <Alert type="success" showIcon title={notice} closable onClose={() => setNotice("")} />}
    <Card title="账户安全" extra={<Button icon={<ReloadOutlined aria-hidden />} aria-label="刷新安全设置" loading={loading} onClick={() => void load()} />}>
      {security && <Space orientation="vertical" size="large" style={{ width: "100%" }}>
        <Flex justify="space-between" align="center" wrap gap="small"><Typography.Text strong>密码</Typography.Text><Button onClick={() => open("password")}>修改密码</Button></Flex>
        <Flex justify="space-between" align="center" wrap gap="middle">
          <div><Typography.Text strong>双因素认证</Typography.Text><div><Tag color={security.totp_enabled ? "success" : "default"}>{security.totp_enabled ? "已启用" : "未启用"}</Tag></div>
            {security.totp_enabled && <Typography.Text type="secondary">剩余 {security.recovery_codes_remaining} 个恢复码</Typography.Text>}
            {!security.totp_enabled && !security.totp_available && <Typography.Text type="secondary">暂时无法设置双因素认证</Typography.Text>}
          </div>
          <Space wrap>{security.totp_enabled ? <><Button onClick={() => open("recovery")}>生成新恢复码</Button><Button aria-label="停用" danger onClick={() => open("disable")}>停用</Button></> : <Button type="primary" icon={<SafetyCertificateOutlined aria-hidden />} aria-label="启用" disabled={!security.totp_available} onClick={() => open("enroll")}>启用</Button>}</Space>
        </Flex>
        <Flex justify="space-between" align="center" wrap gap="small"><Typography.Text strong>订阅链接</Typography.Text><Button onClick={() => open("link")}>重置链接</Button></Flex>
        <Flex justify="space-between" align="center" wrap gap="small"><div><Typography.Text strong>订阅 IP 访问控制</Typography.Text><div><Typography.Text type="secondary">{ipPolicy?.enabled ? `允许 ${ipPolicy.networks.length} 个网段` : "不限来源"}</Typography.Text></div></div><Button onClick={() => setIpDialog(true)}>编辑访问限制</Button></Flex>
      </Space>}
    </Card>
    <section aria-label="活跃会话"><Card title="活跃会话" extra={<Button icon={<LogoutOutlined aria-hidden />} disabled={loading || devices.length < 2} onClick={() => void revoke()}>撤销其他会话</Button>}>
      <Table rowKey="id" dataSource={devices} loading={loading} pagination={false} scroll={{ x: 540 }} locale={{ emptyText: "暂无活跃会话" }} columns={[
        { title: "设备", render: (_, device) => <><Space wrap><Typography.Text strong>{device.peer}</Typography.Text>{device.current && <Tag color="blue">当前</Tag>}</Space><Typography.Paragraph type="secondary">{device.user_agent || "未知设备"}</Typography.Paragraph></> },
        { title: "活动记录", width: 250, render: (_, device) => <Descriptions column={1} size="small" items={[
          { key: "created", label: "登录时间", children: date(device.created_at) },
          { key: "seen", label: "上次活动时间", children: date(device.last_seen_at) },
        ]} /> },
        { title: "操作", width: 96, render: (_, device) => <Button icon={<LogoutOutlined aria-hidden />} aria-label={device.current ? "退出此设备" : `撤销会话 ${device.id}`} disabled={loading} onClick={() => void revoke(device)} /> },
      ]} />
    </Card></section>
    <Modal open={mode !== null} title={recovery.length ? "恢复码" : titles[mode ?? "password"]} width={560} destroyOnHidden mask={{ closable: !busy && !recovery.length }} keyboard={!busy && !recovery.length} closable={!busy && !recovery.length} onCancel={close} footer={<Space wrap>
      <Button aria-label={recovery.length ? "完成" : "取消"} disabled={busy || (recovery.length > 0 && !accepted)} onClick={close}>{recovery.length ? "完成" : "取消"}</Button>
      {!recovery.length && <Button type="primary" htmlType="submit" form="subscriber-security-form" aria-label={mode === "enroll" ? enrollment ? "验证并启用" : "继续" : "确认"} disabled={!canSubmit} loading={busy}>{mode === "enroll" ? enrollment ? "验证并启用" : "继续" : "确认"}</Button>}
    </Space>}>
      {dialogError && <Alert className="form-alert" type="error" showIcon title={zhMessage(dialogError)} />}
      {recovery.length ? <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
        <Alert type="warning" showIcon title="请妥善保存这些一次性恢复码。原有恢复码已失效。" />
        <Flex justify="end" gap="small"><Button icon={<DownloadOutlined aria-hidden />} aria-label="下载恢复码" onClick={saveRecovery} /><Button icon={<CopyOutlined aria-hidden />} aria-label="复制恢复码" onClick={() => void copy(recovery.join("\n"))} /></Flex>
        <div className="recovery-grid" aria-label="恢复码">{recovery.map(item => <Typography.Text code key={item}>{item}</Typography.Text>)}</div>
        <Checkbox checked={accepted} onChange={event => setAccepted(event.target.checked)}>我已妥善保存恢复码</Checkbox>
      </Space> : <Form id="subscriber-security-form" layout="vertical" onFinish={() => void submit()}>
        {mode === "password" && <Alert className="form-alert" type="warning" showIcon title="所有会话都将退出登录。" />}
        {mode === "link" && <Alert className="form-alert" type="warning" showIcon title="现有订阅链接将失效。" />}
        {mode === "disable" && <Alert className="form-alert" type="warning" showIcon title="双因素认证和恢复码将被移除，其他会话将退出登录。" />}
        {mode === "recovery" && <Alert className="form-alert" type="warning" showIcon title="现有恢复码将失效，其他会话将退出登录。" />}
        {enrollment ? <>
          {qr && <img src={qr} alt="验证器绑定二维码" width={240} height={240} className="totp-qr" />}
          <Form.Item label="设置密钥" htmlFor="subscriber-setup-key"><Space.Compact style={{ width: "100%" }}><Input id="subscriber-setup-key" readOnly value={enrollment.secret} /><Button icon={<CopyOutlined aria-hidden />} aria-label="复制设置密钥" onClick={() => void copy(enrollment.secret)} /></Space.Compact></Form.Item>
          <Typography.Paragraph type="secondary" >有效期至 {date(enrollment.expires_at)}</Typography.Paragraph>
        </> : <Form.Item label="当前密码" htmlFor="subscriber-security-password" required><Input.Password id="subscriber-security-password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="current-password" required maxLength={1024} disabled={busy} /></Form.Item>}
        {mode === "password" && <>
          <Form.Item label="新密码" htmlFor="subscriber-security-new-password" required><Input.Password id="subscriber-security-new-password" value={newPassword} onChange={event => setNewPassword(event.target.value)} autoComplete="new-password" required minLength={12} maxLength={1024} disabled={busy} /></Form.Item>
          <Form.Item label="确认密码" htmlFor="subscriber-security-confirmation" required validateStatus={confirmation && confirmation !== newPassword ? "error" : undefined} help={confirmation && confirmation !== newPassword ? "两次输入的密码不一致" : undefined}><Input.Password id="subscriber-security-confirmation" value={confirmation} onChange={event => setConfirmation(event.target.value)} autoComplete="new-password" required maxLength={1024} disabled={busy} /></Form.Item>
        </>}
        {needsCode && <Form.Item label={enrollment ? "验证器验证码" : "验证器验证码或恢复码"} htmlFor="subscriber-security-code" required><Input id="subscriber-security-code" value={code} onChange={event => setCode(event.target.value)} autoComplete="one-time-code" inputMode={enrollment ? "numeric" : "text"} required maxLength={64} disabled={busy} /></Form.Item>}
      </Form>}
    </Modal>
    <SubscriptionIpPolicyDialog open={ipDialog} onOpenChange={setIpDialog} subscriber onUpdated={setIpPolicy} />
  </Space>;
}
