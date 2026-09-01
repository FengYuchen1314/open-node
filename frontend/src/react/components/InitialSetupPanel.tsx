import { UploadOutlined } from "@ant-design/icons";
import { Alert, Button, Checkbox, Form, Input, Modal, Radio, Space, Typography, Upload } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import type { RestoreArchiveFormat } from "../../domain/backups";
import { completeInitialSetup, getInitialSetupStatus, prepareInitialRestore, setupErrorMessage, uploadInitialRestore, validateSetupInput, type InitialSetupStatus } from "../../services/initial-setup";
import { useAsyncScope } from "../hooks/useAsyncScope";

export default function InitialSetupPanel() {
  const scope = useAsyncScope(), busyRef = useRef(false);
  const [status, setStatus] = useState<InitialSetupStatus | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [token, setToken] = useState(""), [username, setUsername] = useState("admin");
  const [password, setPassword] = useState(""), [confirmation, setConfirmation] = useState("");
  const [site, setSite] = useState("Open Node"), [brand, setBrand] = useState("Open Node");
  const [email, setEmail] = useState(""), [nickname, setNickname] = useState(""), [avatarUrl, setAvatarUrl] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [restoreOpen, setRestoreOpen] = useState(false), [restorePrepared, setRestorePrepared] = useState(false);
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [restoreToken, setRestoreToken] = useState(""), [restoreIdentity, setRestoreIdentity] = useState("");
  const [restoreTotpKey, setRestoreTotpKey] = useState("");
  const [restoreFormat, setRestoreFormat] = useState<RestoreArchiveFormat>("age");
  const [restoreReplace, setRestoreReplace] = useState(false), [restoreTrusted, setRestoreTrusted] = useState(false);
  const refresh = useCallback(async () => {
    if (busyRef.current) return;
    const generation = scope.begin(); busyRef.current = true; setBusy(true); setError("");
    try {
      const result = await getInitialSetupStatus();
      if (scope.isCurrent(generation)) setStatus(result);
    } catch (cause) {
      if (scope.isCurrent(generation)) { setStatus(null); setError(setupErrorMessage(cause)); }
    } finally {
      if (scope.isCurrent(generation)) { busyRef.current = false; setBusy(false); }
    }
  }, [scope]);
  useEffect(() => { void refresh(); return () => { busyRef.current = false; }; }, [refresh]);

  async function submit() {
    if (busyRef.current || !status?.available || !accepted) return;
    if (password !== confirmation) { setError("两次输入的密码不一致。"); return; }
    let payload;
    try {
      payload = validateSetupInput({ setup_token: token, username, password, site_title: site, brand_title: brand,
        email, nickname, avatar_url: avatarUrl, confirm_new_install: accepted });
    } catch (cause) { setError(setupErrorMessage(cause)); return; }
    const generation = scope.begin(); busyRef.current = true; setBusy(true); setError("");
    setToken(""); setPassword(""); setConfirmation(""); setAccepted(false);
    try {
      await completeInitialSetup(payload);
      if (scope.isCurrent(generation)) setStatus({ configured: true, available: false, expires_at: null, token_required: true });
    } catch (cause) {
      if (!scope.isCurrent(generation)) return;
      setError(setupErrorMessage(cause)); setStatus(null);
      // A lost response may follow a successful commit. Read, never replay POST.
      try {
        const current = await getInitialSetupStatus();
        if (scope.isCurrent(generation)) setStatus(current);
      } catch { /* Keep inputs closed until a status read succeeds. */ }
    } finally {
      if (scope.isCurrent(generation)) { busyRef.current = false; setBusy(false); }
    }
  }

  const canRestore = Boolean(status?.available && restoreFile && restoreFile.size >= 22
    && /^[A-Za-z0-9_-]{43}$/.test(restoreToken) && (restoreFormat === "plain" || restoreIdentity)
    && restoreReplace && restoreTrusted && !busy);
  function closeRestore() {
    if (busyRef.current) return;
    setRestoreOpen(false); setRestoreFile(null); setRestoreToken(""); setRestoreIdentity("");
    setRestoreTotpKey(""); setRestoreReplace(false); setRestoreTrusted(false);
  }
  async function submitRestore() {
    if (busyRef.current || !canRestore || !restoreFile) return;
    const generation = scope.begin(), file = restoreFile;
    const payload = { setup_token: restoreToken, format: restoreFormat,
      identity: restoreFormat === "age" ? restoreIdentity : "", subscriber_totp_key: restoreTotpKey,
      confirm_replace_instance: true as const, confirm_trusted_backup: true as const };
    setRestoreToken(""); setRestoreIdentity(""); setRestoreTotpKey("");
    setRestoreReplace(false); setRestoreTrusted(false); busyRef.current = true; setBusy(true); setError("");
    try {
      const upload = await uploadInitialRestore(file, payload.setup_token);
      if (!scope.isCurrent(generation)) return;
      await prepareInitialRestore(upload.id, payload);
      if (!scope.isCurrent(generation)) return;
      setRestoreFile(null); setRestoreOpen(false); setRestorePrepared(true);
    } catch (cause) {
      if (scope.isCurrent(generation)) setError(setupErrorMessage(cause));
    } finally {
      if (scope.isCurrent(generation)) { busyRef.current = false; setBusy(false); }
    }
  }

  if (status?.configured) return <Space orientation="vertical" style={{ width: "100%" }}>
    <Alert type="success" showIcon title="此实例已初始化。请使用管理员账户登录。" description="如果管理员账户丢失，请在服务器终端恢复密码，不能重新初始化。" />
    <Button type="primary" onClick={() => window.location.reload()}>前往登录</Button>
  </Space>;
  if (restorePrepared) return <Space orientation="vertical" style={{ width: "100%" }}>
    <Alert type="success" showIcon title="恢复包已验证并隔离准备完成。"
      description="安装器管理的服务正在重启。重启后请用备份中的管理员账户登录并完成恢复复核；远端任务在复核前保持暂停。" />
    <Button type="primary" onClick={() => window.location.reload()}>重新读取实例状态</Button>
  </Space>;
  return <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
    <Alert type="info" showIcon title="尚未配置管理员账户。" description="请通过 SSH 隧道或可信 HTTPS 访问。初始化凭证由安装终端提供，有效期 30 分钟。" />
    {error && <Alert type="error" showIcon title={error} role="alert" />}
    {!status?.available ? <>
      <Typography.Paragraph>在服务器上运行安装脚本的 <Typography.Text code>setup</Typography.Text> 命令，或在面板容器内运行 <Typography.Text code>open-node-admin prepare-setup</Typography.Text>。重新生成后旧凭证立即失效，请勿分享终端输出。</Typography.Paragraph>
      <Button loading={busy} onClick={() => void refresh()}>重新读取状态</Button>
    </> : <Form layout="vertical" onFinish={() => void submit()} style={{ width: "100%" }}>
      <Form.Item label="初始化凭证" htmlFor="setup-token" required><Input.Password id="setup-token" value={token} onChange={e => setToken(e.target.value)} autoComplete="off" maxLength={43} disabled={busy} /></Form.Item>
      <Form.Item label="管理员用户名" htmlFor="setup-username" required extra="1–64 个英文字母、数字或 _.@- 字符。"><Input id="setup-username" value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" maxLength={64} disabled={busy} /></Form.Item>
      <Form.Item label="管理员昵称" htmlFor="setup-nickname" extra="可选；留空时使用管理员用户名。"><Input id="setup-nickname" value={nickname} onChange={e => setNickname(e.target.value)} autoComplete="name" maxLength={120} disabled={busy} /></Form.Item>
      <Form.Item label="管理员邮箱" htmlFor="setup-email" extra="可选；用于显示资料，不作为登录凭据。"><Input id="setup-email" type="email" value={email} onChange={e => setEmail(e.target.value)} autoComplete="email" maxLength={254} disabled={busy} /></Form.Item>
      <Form.Item label="头像 HTTPS 地址" htmlFor="setup-avatar" extra="可选；只接受 HTTPS，不会把图片上传到主控。"><Input id="setup-avatar" type="url" value={avatarUrl} onChange={e => setAvatarUrl(e.target.value)} autoComplete="url" maxLength={2048} disabled={busy} /></Form.Item>
      <Form.Item label="管理员密码" htmlFor="setup-password" required extra="至少 12 个字符，保留首尾空格。"><Input.Password id="setup-password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="new-password" disabled={busy} /></Form.Item>
      <Form.Item label="确认密码" htmlFor="setup-confirm" required><Input.Password id="setup-confirm" value={confirmation} onChange={e => setConfirmation(e.target.value)} autoComplete="new-password" disabled={busy} /></Form.Item>
      <Form.Item label="浏览器标题" htmlFor="setup-site" required><Input id="setup-site" value={site} onChange={e => setSite(e.target.value)} disabled={busy} /></Form.Item>
      <Form.Item label="页面品牌文字" htmlFor="setup-brand" required><Input id="setup-brand" value={brand} onChange={e => setBrand(e.target.value)} disabled={busy} /></Form.Item>
      <Form.Item><Checkbox checked={accepted} onChange={e => setAccepted(e.target.checked)} disabled={busy}>确认这是新安装，创建首个管理员</Checkbox></Form.Item>
      <Button type="primary" htmlType="submit" loading={busy} disabled={!accepted || !token || !username || !password || !confirmation} block>完成初始化</Button>
      <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>初始化不会自动配置域名或 HTTPS，也不会自动登录。</Typography.Paragraph>
    </Form>}
    {status?.available && <>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
        已有本项目 v1 备份时，不必创建空管理员；可使用同一初始化凭证恢复。旧版 mmwx 备份不支持直接导入。
      </Typography.Paragraph>
      <Button danger icon={<UploadOutlined aria-hidden />} disabled={busy} onClick={() => setRestoreOpen(true)}>从备份恢复现有实例</Button>
      <Modal title="初始化时从备份恢复" open={restoreOpen} onCancel={closeRestore} destroyOnHidden maskClosable={false}
        closable={!busy} keyboard={!busy} footer={<Space wrap><Button disabled={busy} onClick={closeRestore}>取消</Button>
          <Button danger type="primary" loading={busy} disabled={!canRestore} onClick={() => void submitRestore()}>验证并准备恢复</Button></Space>}>
        <Alert type="warning" showIcon title="恢复成功后，备份中的管理员和配置将替代当前空实例。"
          description="恢复先写入隔离目录，当前初始化数据库不会在验证完成前被覆盖。" />
        <Form layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item label="恢复初始化凭证" htmlFor="initial-restore-token" required><Input.Password id="initial-restore-token"
            value={restoreToken} onChange={event => setRestoreToken(event.target.value)} maxLength={43} autoComplete="off" disabled={busy} /></Form.Item>
          <Form.Item label="恢复文件" required><Space wrap><Upload accept=".age,.zip,application/octet-stream,application/zip"
            maxCount={1} showUploadList={false} beforeUpload={file => { setRestoreFile(file); return Upload.LIST_IGNORE; }} disabled={busy}>
            <Button icon={<UploadOutlined aria-hidden />} disabled={busy}>选择恢复文件</Button>
          </Upload><Typography.Text>{restoreFile?.name ?? "尚未选择文件"}</Typography.Text></Space></Form.Item>
          <Form.Item label="备份格式" required><Radio.Group value={restoreFormat} disabled={busy}
            onChange={event => { setRestoreFormat(event.target.value as RestoreArchiveFormat); if (event.target.value === "plain") setRestoreIdentity(""); }}
            options={[{ label: "age 加密包", value: "age" }, { label: "明文 v1 ZIP", value: "plain" }]} /></Form.Item>
          {restoreFormat === "age" && <Form.Item label="age 恢复私钥" htmlFor="initial-restore-identity" required
            extra="只随本次同源请求发送并用于临时解密。"><Input.TextArea id="initial-restore-identity" value={restoreIdentity}
              onChange={event => setRestoreIdentity(event.target.value)} autoSize={{ minRows: 3, maxRows: 6 }} maxLength={4096}
              autoComplete="off" spellCheck={false} disabled={busy} /></Form.Item>}
          <Form.Item label="订阅 TOTP 配置密钥" htmlFor="initial-restore-totp"
            extra="备份依赖原 OPEN_NODE_SUBSCRIBER_TOTP_KEY 时填写；不是管理员验证码。"><Input.Password id="initial-restore-totp"
              value={restoreTotpKey} onChange={event => setRestoreTotpKey(event.target.value)} maxLength={44} autoComplete="off" disabled={busy} /></Form.Item>
          <Space orientation="vertical">
            <Checkbox checked={restoreReplace} disabled={busy} onChange={event => setRestoreReplace(event.target.checked)}>确认用备份初始化这个新实例</Checkbox>
            <Checkbox checked={restoreTrusted} disabled={busy} onChange={event => setRestoreTrusted(event.target.checked)}>确认备份来源可信，并已保留原实例及其外部配置</Checkbox>
          </Space>
        </Form>
      </Modal>
    </>}
  </Space>;
}
