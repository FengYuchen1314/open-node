import { Alert, Button, Checkbox, Form, Input, Space, Typography } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { completeInitialSetup, getInitialSetupStatus, setupErrorMessage, validateSetupInput, type InitialSetupStatus } from "../../services/initial-setup";
import { useAsyncScope } from "../hooks/useAsyncScope";

export default function InitialSetupPanel() {
  const scope = useAsyncScope(), busyRef = useRef(false);
  const [status, setStatus] = useState<InitialSetupStatus | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [token, setToken] = useState(""), [username, setUsername] = useState("admin");
  const [password, setPassword] = useState(""), [confirmation, setConfirmation] = useState("");
  const [site, setSite] = useState("Open Node"), [brand, setBrand] = useState("Open Node");
  const [accepted, setAccepted] = useState(false);
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
      payload = validateSetupInput({ setup_token: token, username, password, site_title: site, brand_title: brand, confirm_new_install: accepted });
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

  if (status?.configured) return <Space orientation="vertical" style={{ width: "100%" }}>
    <Alert type="success" showIcon title="此实例已初始化。请使用管理员账户登录。" description="如果管理员账户丢失，请在服务器终端恢复密码，不能重新初始化。" />
    <Button type="primary" onClick={() => window.location.reload()}>前往登录</Button>
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
      <Form.Item label="管理员密码" htmlFor="setup-password" required extra="至少 12 个字符，保留首尾空格。"><Input.Password id="setup-password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="new-password" disabled={busy} /></Form.Item>
      <Form.Item label="确认密码" htmlFor="setup-confirm" required><Input.Password id="setup-confirm" value={confirmation} onChange={e => setConfirmation(e.target.value)} autoComplete="new-password" disabled={busy} /></Form.Item>
      <Form.Item label="浏览器标题" htmlFor="setup-site" required><Input id="setup-site" value={site} onChange={e => setSite(e.target.value)} disabled={busy} /></Form.Item>
      <Form.Item label="页面品牌文字" htmlFor="setup-brand" required><Input id="setup-brand" value={brand} onChange={e => setBrand(e.target.value)} disabled={busy} /></Form.Item>
      <Form.Item><Checkbox checked={accepted} onChange={e => setAccepted(e.target.checked)} disabled={busy}>确认这是新安装，创建首个管理员</Checkbox></Form.Item>
      <Button type="primary" htmlType="submit" loading={busy} disabled={!accepted || !token || !username || !password || !confirmation} block>完成初始化</Button>
      <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>初始化不会自动配置域名或 HTTPS，也不会自动登录。</Typography.Paragraph>
    </Form>}
  </Space>;
}
