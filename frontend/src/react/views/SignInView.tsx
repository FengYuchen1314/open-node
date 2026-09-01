import { LoginOutlined, ReloadOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Checkbox, Flex, Form, Input, Space, Typography } from "antd";
import QRCode from "qrcode";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  acceptOperatorSession, loadSession, signIn, verifySignIn,
  type AdministratorTotpEnrollment, type OperatorLogin,
} from "../../services/auth";
import { useAsyncScope } from "../hooks/useAsyncScope";
import { useBranding } from "../hooks/useBranding";
import { useAdministratorSession } from "../hooks/useSession";
import { zhMessage } from "../../i18n/zh-CN";
import InitialSetupPanel from "../components/InitialSetupPanel";
import { LoginWallpaper, SiteLogo, ThemeSelector } from "../components/AppearanceChrome";

export default function SignInView() {
  const auth = useAdministratorSession();
  const { branding } = useBranding();
  const scope = useAsyncScope();
  const busyRef = useRef(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [challenge, setChallenge] = useState("");
  const [code, setCode] = useState("");
  const [enrollment, setEnrollment] = useState<AdministratorTotpEnrollment | null>(null);
  const [qr, setQr] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [accepted, setAccepted] = useState(false);
  const [stagedSession, setStagedSession] = useState<OperatorLogin | null>(null);

  async function submit() {
    if (busyRef.current || !username || !password) return;
    const request = scope.begin();
    busyRef.current = true;
    setBusy(true); setError("");
    try {
      const result = await signIn(username, password);
      if (!scope.isCurrent(request)) return;
      if (result.requires_2fa && result.challenge) {
        setChallenge(result.challenge); setEnrollment(result.enrollment);
        if (result.enrollment) {
          const image = await QRCode.toDataURL(result.enrollment.provisioning_uri, { width: 240, margin: 1 });
          if (scope.isCurrent(request)) setQr(image);
        } else setQr("");
      }
    } catch (cause) {
      if (scope.isCurrent(request)) setError(zhMessage(cause, "登录失败，请稍后重试。"));
    } finally {
      if (scope.isCurrent(request)) { setPassword(""); setBusy(false); busyRef.current = false; }
    }
  }
  async function verify() {
    if (busyRef.current || !challenge || !code) return;
    const request = scope.begin();
    busyRef.current = true;
    setBusy(true); setError("");
    try {
      const result = await verifySignIn(challenge, code);
      if (!scope.isCurrent(request)) return;
      if (result.recovery_codes.length) {
        setStagedSession(result); setRecoveryCodes(result.recovery_codes);
        setChallenge(""); setEnrollment(null); setQr("");
      }
    } catch (cause) {
      if (scope.isCurrent(request)) setError(zhMessage(cause, "验证失败，请检查验证码后重试。"));
    } finally {
      if (scope.isCurrent(request)) { setCode(""); setBusy(false); busyRef.current = false; }
    }
  }
  function restart() {
    if (busyRef.current) return;
    scope.invalidate(); setChallenge(""); setCode(""); setPassword(""); setEnrollment(null);
    setQr(""); setRecoveryCodes([]); setStagedSession(null); setAccepted(false); setError("");
  }
  function continueLogin() {
    if (!accepted || !stagedSession) return;
    const session = stagedSession;
    restart();
    acceptOperatorSession(session);
  }

  return <section className="auth-page"><LoginWallpaper /><Card className="auth-card">
    <Space orientation="vertical" size="large" style={{ width: "100%" }}>
      <Flex justify="space-between" align="start" gap="middle"><div><SiteLogo /><Typography.Title level={2} className="branding-block-text">{branding.brand_title}</Typography.Title><Typography.Title level={4}>{auth.session?.configured === false ? "首次初始化" : "管理员登录"}</Typography.Title></div><ThemeSelector /></Flex>
      {auth.error ? <Alert type="error" showIcon title={zhMessage(auth.error, "暂时无法连接服务器。")} action={<Button aria-label="重新连接" icon={<ReloadOutlined aria-hidden />} onClick={() => void loadSession()} />} />
        : auth.session?.configured === false ? <InitialSetupPanel />
        : recoveryCodes.length ? <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
          <Alert type="success" showIcon title="双重验证已启用。请妥善保存这些一次性恢复码后再继续。" />
          <div className="recovery-grid" aria-label="管理员恢复码">{recoveryCodes.map(item => <Typography.Text code key={item}>{item}</Typography.Text>)}</div>
          <Checkbox checked={accepted} onChange={event => setAccepted(event.target.checked)}>我已妥善保存恢复码</Checkbox>
          <Button type="primary" className="branding-continue-button" disabled={!accepted} onClick={continueLogin}>进入 {branding.brand_title}</Button>
        </Space> : challenge ? <Form layout="vertical" onFinish={() => void verify()}>
          {error && <Alert className="form-alert" type="error" showIcon title={error} role="alert" />}
          {enrollment && <>
            <Alert className="form-alert" type="info" showIcon title="管理员必须启用双重验证。请扫描二维码后继续登录。" />
            {qr && <img className="totp-qr" src={qr} alt="管理员验证器绑定二维码" width={240} height={240} />}
            <Form.Item label="验证器密钥" htmlFor="administrator-enrollment-secret"><Input id="administrator-enrollment-secret" value={enrollment.secret} readOnly /></Form.Item>
          </>}
          <Form.Item label={enrollment ? "验证器验证码" : "验证器验证码或恢复码"} htmlFor="administrator-login-code" required><Input id="administrator-login-code" value={code} onChange={event => setCode(event.target.value)} autoComplete="one-time-code" inputMode={enrollment ? "numeric" : "text"} autoFocus required maxLength={64} disabled={busy} /></Form.Item>
          <Flex justify="space-between" gap="small"><Button disabled={busy} onClick={restart}>重新开始</Button><Button type="primary" htmlType="submit" aria-label="验证" icon={<SafetyCertificateOutlined aria-hidden />} loading={busy} disabled={!code}>验证</Button></Flex>
        </Form> : <Form layout="vertical" onFinish={() => void submit()}>
          {error && <Alert className="form-alert" type="error" showIcon title={error} role="alert" />}
          <Form.Item label="用户名" htmlFor="administrator-username" required><Input id="administrator-username" value={username} onChange={event => setUsername(event.target.value)} autoComplete="username" autoFocus required maxLength={64} disabled={busy} /></Form.Item>
          <Form.Item label="密码" htmlFor="administrator-password" required><Input.Password id="administrator-password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="current-password" required maxLength={1024} disabled={busy} /></Form.Item>
          <Button type="primary" htmlType="submit" aria-label="登录" icon={<LoginOutlined aria-hidden />} loading={busy} disabled={!username || !password} block>登录</Button>
        </Form>}
      <Link to="/account">用户登录</Link>
    </Space>
  </Card></section>;
}
