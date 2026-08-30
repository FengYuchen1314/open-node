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
import { useAdministratorSession } from "../hooks/useSession";

export default function SignInView() {
  const auth = useAdministratorSession();
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
      if (scope.isCurrent(request)) setError(cause instanceof Error ? cause.message : "Sign-in failed");
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
      if (scope.isCurrent(request)) setError(cause instanceof Error ? cause.message : "Verification failed");
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

  return <section className="auth-page"><Card className="auth-card">
    <Space orientation="vertical" size="large" style={{ width: "100%" }}>
      <div><Typography.Title level={2}>Open Node</Typography.Title><Typography.Title level={4}>Administrator Sign-In</Typography.Title></div>
      {auth.error ? <Alert type="error" showIcon title={auth.error} action={<Button aria-label="Retry connection" icon={<ReloadOutlined aria-hidden />} onClick={() => void loadSession()} />} />
        : auth.session?.configured === false ? <Alert type="warning" showIcon title="Administrator account is not configured." />
        : recoveryCodes.length ? <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
          <Alert type="success" showIcon title="Two-factor authentication is enabled. Store these one-time recovery codes before continuing." />
          <div className="recovery-grid" aria-label="Administrator recovery codes">{recoveryCodes.map(item => <Typography.Text code key={item}>{item}</Typography.Text>)}</div>
          <Checkbox checked={accepted} onChange={event => setAccepted(event.target.checked)}>I have stored the recovery codes securely</Checkbox>
          <Button type="primary" disabled={!accepted} onClick={continueLogin}>Continue to Open Node</Button>
        </Space> : challenge ? <Form layout="vertical" onFinish={() => void verify()}>
          {error && <Alert className="form-alert" type="error" showIcon title={error} role="alert" />}
          {enrollment && <>
            <Alert className="form-alert" type="info" showIcon title="Administrator 2FA is required. Scan this code before completing sign-in." />
            {qr && <img className="totp-qr" src={qr} alt="Administrator authenticator enrollment QR code" width={240} height={240} />}
            <Form.Item label="Authenticator secret" htmlFor="administrator-enrollment-secret"><Input id="administrator-enrollment-secret" value={enrollment.secret} readOnly /></Form.Item>
          </>}
          <Form.Item label={enrollment ? "Authenticator code" : "Authenticator or recovery code"} htmlFor="administrator-login-code" required><Input id="administrator-login-code" value={code} onChange={event => setCode(event.target.value)} autoComplete="one-time-code" inputMode={enrollment ? "numeric" : "text"} autoFocus required maxLength={64} disabled={busy} /></Form.Item>
          <Flex justify="space-between" gap="small"><Button disabled={busy} onClick={restart}>Start over</Button><Button type="primary" htmlType="submit" aria-label="Verify" icon={<SafetyCertificateOutlined aria-hidden />} loading={busy} disabled={!code}>Verify</Button></Flex>
        </Form> : <Form layout="vertical" onFinish={() => void submit()}>
          {error && <Alert className="form-alert" type="error" showIcon title={error} role="alert" />}
          <Form.Item label="Username" htmlFor="administrator-username" required><Input id="administrator-username" value={username} onChange={event => setUsername(event.target.value)} autoComplete="username" autoFocus required maxLength={64} disabled={busy} /></Form.Item>
          <Form.Item label="Password" htmlFor="administrator-password" required><Input.Password id="administrator-password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="current-password" required maxLength={1024} disabled={busy} /></Form.Item>
          <Button type="primary" htmlType="submit" aria-label="Sign In" icon={<LoginOutlined aria-hidden />} loading={busy} disabled={!username || !password} block>Sign In</Button>
        </Form>}
      <Link to="/account">Subscriber sign-in</Link>
    </Space>
  </Card></section>;
}
