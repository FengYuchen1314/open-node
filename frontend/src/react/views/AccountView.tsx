import { CopyOutlined, CheckOutlined, DownloadOutlined, EditOutlined, LoginOutlined, LogoutOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Descriptions, Flex, Form, Input, Layout, Progress, Segmented, Select, Space, Spin, Table, Tabs, Tag, Typography } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { ProductUserSubscriptionToken, SubscriptionClientFormat } from "../../domain/subscriptions";
import type { SubscriberSubscriptionProfile } from "../../domain/subscription-profiles";
import {
  loadSubscriberSession, subscriberFormatUrl, subscriberProfile, subscriberProfiles, subscriberRegister,
  subscriberSignIn, subscriberSignOut, subscriberState, subscriberToken, verifySubscriberLogin,
  type SubscriberProfile,
} from "../../services/subscriber-auth";
import PrivateRoutedNodesPanel from "../components/PrivateRoutedNodesPanel";
import SubscriberSecurityPanel from "../components/SubscriberSecurityPanel";
import SubscriptionShortCodeDialog from "../components/SubscriptionShortCodeDialog";
import TemplatesWorkspace from "../components/TemplatesWorkspace";
import { useAsyncScope } from "../hooks/useAsyncScope";
import { useSubscriberSession } from "../hooks/useSession";

const formats: { label: string; value: SubscriptionClientFormat }[] = [
  { label: "Clash / Mihomo", value: "clash" }, { label: "sing-box", value: "sing-box" }, { label: "Surge", value: "surge" },
  { label: "Xray", value: "xray" }, { label: "URI list", value: "uri-list" }, { label: "Base64", value: "base64" },
];
const date = (value?: string | null) => value ? new Date(value).toLocaleDateString() : "None";
function bytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const unit = Math.min(Math.floor(Math.log(value) / Math.log(1024)), 4);
  return `${(value / 1024 ** unit).toLocaleString(undefined, { maximumFractionDigits: 1 })} ${["B", "KiB", "MiB", "GiB", "TiB"][unit]}`;
}
function clearInvitationFragment() {
  if (typeof window !== "undefined") window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
}

export default function AccountView() {
  const auth = useSubscriberSession();
  const [initializing, setInitializing] = useState(true);
  const [invitation, setInvitation] = useState(() => typeof window === "undefined" ? "" : new URLSearchParams(window.location.hash.slice(1)).get("invite") ?? "");
  useEffect(() => {
    let active = true;
    subscriberState.ready = false;
    void loadSubscriberSession().finally(() => { if (active) setInitializing(false); });
    return () => { active = false; };
  }, []);
  const clearInvitation = useCallback(() => { setInvitation(""); clearInvitationFragment(); }, []);
  useEffect(() => { if (auth.session?.authenticated) clearInvitation(); }, [auth.session?.authenticated, clearInvitation]);
  if (initializing || !auth.ready) return <div className="auth-page" role="status" aria-label="Loading account"><Spin size="large" /></div>;
  if (!auth.session?.authenticated) return <SubscriberSignIn invitation={invitation} onClearInvitation={clearInvitation} />;
  return <SubscriberWorkspace key={auth.session.username} username={auth.session.username ?? ""} />;
}

function SubscriberSignIn({ invitation, onClearInvitation }: { invitation: string; onClearInvitation: () => void }) {
  const auth = useSubscriberSession();
  const scope = useAsyncScope();
  const busyRef = useRef(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [code, setCode] = useState("");
  const [challenge, setChallenge] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const registering = Boolean(invitation);
  const canRegister = Boolean(username.trim() && password.length >= 12 && password === confirmPassword);

  async function submit() {
    if (busyRef.current || (challenge ? !code : !username || !password)) return;
    const current = scope.begin(); busyRef.current = true; setBusy(true); setError("");
    try {
      const result = challenge ? await verifySubscriberLogin(challenge, code) : await subscriberSignIn(username, password);
      if (scope.isCurrent(current)) setChallenge(result.challenge ?? "");
    } catch (failure) {
      if (scope.isCurrent(current)) setError(failure instanceof Error ? failure.message : "Sign-in failed");
    } finally { if (scope.isCurrent(current)) { setPassword(""); setCode(""); setBusy(false); busyRef.current = false; } }
  }
  async function register() {
    if (busyRef.current || !canRegister) return;
    const current = scope.begin(); busyRef.current = true; setBusy(true); setError("");
    let created = false;
    try {
      await subscriberRegister({ token: invitation, username: username.trim(), password, email: email.trim() || null, display_name: displayName.trim() || null });
      created = true;
      if (!scope.isCurrent(current)) return;
      await subscriberSignIn(username.trim(), password);
      if (scope.isCurrent(current)) onClearInvitation();
    } catch (failure) {
      if (scope.isCurrent(current)) {
        if (created) onClearInvitation();
        const detail = failure instanceof Error ? failure.message : "Registration failed";
        setError(created ? `Account created. Sign-in failed: ${detail}` : detail);
      }
    } finally { if (scope.isCurrent(current)) { setPassword(""); setConfirmPassword(""); setBusy(false); busyRef.current = false; } }
  }
  function restart() { scope.invalidate(); setChallenge(""); setCode(""); setPassword(""); setError(""); }
  function cancelRegistration() { onClearInvitation(); setPassword(""); setConfirmPassword(""); setEmail(""); setDisplayName(""); setError(""); }

  return <section className="auth-page"><Card className="auth-card"><Space orientation="vertical" size="large" style={{ width: "100%" }}>
    <div><Typography.Title level={2}>Open Node</Typography.Title><Typography.Title level={4}>{registering ? "Create Subscriber Account" : challenge ? "Two-Factor Verification" : "Subscriber Sign-In"}</Typography.Title></div>
    {auth.error ? <Alert type="error" showIcon title={auth.error} action={<Button icon={<ReloadOutlined aria-hidden />} aria-label="Retry account connection" onClick={() => void loadSubscriberSession()} />} /> : <Form layout="vertical" onFinish={() => void (registering ? register() : submit())}>
      {error && <Alert className="form-alert" type="error" showIcon title={error} />}
      {(!challenge || registering) && <Form.Item label="Username" htmlFor="subscriber-username" required><Input id="subscriber-username" value={username} onChange={event => setUsername(event.target.value)} autoComplete="username" autoFocus required maxLength={80} disabled={busy} /></Form.Item>}
      {registering && <>
        <Form.Item label="Display name" htmlFor="subscriber-display-name"><Input id="subscriber-display-name" value={displayName} onChange={event => setDisplayName(event.target.value)} autoComplete="name" maxLength={120} disabled={busy} /></Form.Item>
        <Form.Item label="Email" htmlFor="subscriber-email"><Input id="subscriber-email" type="email" value={email} onChange={event => setEmail(event.target.value)} autoComplete="email" maxLength={255} disabled={busy} /></Form.Item>
      </>}
      {(!challenge || registering) && <Form.Item label="Password" htmlFor="subscriber-password" required><Input.Password id="subscriber-password" value={password} onChange={event => setPassword(event.target.value)} autoComplete={registering ? "new-password" : "current-password"} minLength={registering ? 12 : undefined} required maxLength={1024} disabled={busy} /></Form.Item>}
      {registering && <Form.Item label="Confirm password" htmlFor="subscriber-confirm-password" required validateStatus={confirmPassword && confirmPassword !== password ? "error" : undefined} help={confirmPassword && confirmPassword !== password ? "Passwords do not match" : undefined}><Input.Password id="subscriber-confirm-password" value={confirmPassword} onChange={event => setConfirmPassword(event.target.value)} autoComplete="new-password" required minLength={12} maxLength={1024} disabled={busy} /></Form.Item>}
      {!registering && challenge && <Form.Item label="Authenticator or recovery code" htmlFor="subscriber-login-code" required><Input id="subscriber-login-code" value={code} onChange={event => setCode(event.target.value)} autoComplete="one-time-code" autoFocus required maxLength={64} disabled={busy} /></Form.Item>}
      <Space wrap><Button type="primary" htmlType="submit" aria-label={registering ? "Create Account" : challenge ? "Verify" : "Sign In"} icon={<LoginOutlined aria-hidden />} loading={busy} disabled={registering ? !canRegister : challenge ? !code : !username || !password}>{registering ? "Create Account" : challenge ? "Verify" : "Sign In"}</Button>
        {registering ? <Button disabled={busy} onClick={cancelRegistration}>Sign In</Button> : challenge && <Button disabled={busy} onClick={restart}>Back</Button>}
      </Space>
    </Form>}
    <Link to="/">Administrator sign-in</Link>
  </Space></Card></section>;
}

function SubscriberWorkspace({ username }: { username: string }) {
  const scope = useAsyncScope();
  const [profile, setProfile] = useState<SubscriberProfile | null>(null);
  const [subscription, setSubscription] = useState<ProductUserSubscriptionToken | null>(null);
  const [profiles, setProfiles] = useState<SubscriberSubscriptionProfile[]>([]);
  const [configuration, setConfiguration] = useState("default");
  const [format, setFormat] = useState<SubscriptionClientFormat>("clash");
  const [linkType, setLinkType] = useState("full");
  const [tab, setTab] = useState("subscription");
  const [loading, setLoading] = useState(false);
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [shortCodeOpen, setShortCodeOpen] = useState(false);
  const selectedProfile = profiles.find(item => item.id === configuration) ?? null;
  const quota = profile?.quota;
  const status = !quota?.has_plan ? "No plan" : quota.expired ? "Expired" : quota.over_quota ? "Quota reached" : "Active";
  let url = "";
  try {
    const value = selectedProfile ? new URL(selectedProfile.subscription_url) : subscription ? new URL(subscriberFormatUrl(subscription, format, linkType === "short")) : null;
    if (value && ["http:", "https:"].includes(value.protocol)) { value.searchParams.set("format", format); url = value.toString(); }
  } catch { /* A malformed server URL must not break or become an executable link. */ }
  const load = useCallback(async () => {
    const current = scope.begin(); setLoading(true); setError("");
    try {
      const [account, token, extra] = await Promise.all([subscriberProfile(), subscriberToken(), subscriberProfiles()]);
      if (!scope.isCurrent(current)) return;
      setProfile(account); setSubscription(token); setProfiles(extra.profiles);
      setConfiguration(previous => previous === "default" || extra.profiles.some(item => item.id === previous) ? previous : "default");
    } catch (failure) {
      if (scope.isCurrent(current)) setError(failure instanceof Error ? failure.message : "Account unavailable");
    } finally { if (scope.isCurrent(current)) setLoading(false); }
  }, [scope]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setCopied(false); }, [url]);
  async function logout() {
    if (logoutBusy) return;
    setLogoutBusy(true); setError("");
    try { await subscriberSignOut(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Sign-out failed"); }
    finally { setLogoutBusy(false); }
  }
  async function copyLink() {
    if (!url) return;
    try { await navigator.clipboard.writeText(url); setCopied(true); }
    catch { setError("Clipboard unavailable"); }
  }
  const subscriptionContent = profile && quota ? <Space orientation="vertical" size="large" style={{ width: "100%" }}>
    <section aria-label="Current plan"><Card title={quota.plan_name || "No plan assigned"} extra={<Tag color={quota.available ? "success" : "warning"}>{status}</Tag>}>
      <Typography.Paragraph type="secondary">Current plan</Typography.Paragraph>
      <Typography.Title level={3}>{bytes(quota.charged_usage_bytes)} <Typography.Text type="secondary">/ {quota.traffic_limit_bytes ? bytes(quota.traffic_limit_bytes) : quota.has_plan ? "Unlimited" : "0 B"}</Typography.Text></Typography.Title>
      <Progress percent={Math.min(100, Math.max(0, quota.percent_used))} status={quota.over_quota ? "exception" : undefined} aria-label="Traffic quota used" />
      <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} items={[
        { key: "expires", label: "Expires", children: date(quota.plan_expires_at) },
        { key: "reset", label: "Next reset", children: date(quota.next_reset_at) },
        { key: "speed", label: "Default speed", children: profile.speed_limit_mbps ? `${profile.speed_limit_mbps} Mbps` : "Unlimited" },
        { key: "connections", label: "Connection limit", children: profile.device_limit || "Unlimited" },
        { key: "upload", label: "Uploaded", children: bytes(quota.upload) },
        { key: "download", label: "Downloaded", children: bytes(quota.download) },
      ]} />
    </Card></section>
    <section aria-label="Subscription links"><Card title="Subscription"><Form layout="vertical">
      {profiles.length > 0 && <Form.Item label="Configuration" htmlFor="account-configuration"><Select id="account-configuration" value={configuration} options={[{ label: "Default subscription", value: "default" }, ...profiles.map(item => ({ label: item.name, value: item.id }))]} onChange={setConfiguration} /></Form.Item>}
      <Flex gap="middle" justify="space-between" align="center" wrap className="form-alert">
        {!selectedProfile && subscription?.short_links_enabled ? <Segmented aria-label="Subscription link type" value={linkType} onChange={setLinkType} options={[{ label: "Full", value: "full" }, { label: "Short", value: "short" }]} /> : <Tag>{selectedProfile ? "MMWX profile" : "Secure link"}</Tag>}
        {subscription?.short_links_enabled && <Button icon={<EditOutlined aria-hidden />} aria-label="Edit subscription short code" disabled={loading || !subscription || Boolean(selectedProfile)} onClick={() => setShortCodeOpen(true)} />}
      </Flex>
      {selectedProfile && (!selectedProfile.enabled || selectedProfile.warnings.length > 0) && <Alert className="form-alert" type={selectedProfile.enabled ? "warning" : "error"} showIcon title={selectedProfile.enabled ? selectedProfile.warnings.join("; ") : "This profile needs administrator configuration."} />}
      <Form.Item label="Client format" htmlFor="account-client-format"><Select id="account-client-format" value={format} options={formats} onChange={setFormat} /></Form.Item>
      <Form.Item label="Subscription URL" htmlFor="account-subscription-url"><Input id="account-subscription-url" value={url} readOnly /></Form.Item>
      <Space wrap><Button icon={copied ? <CheckOutlined aria-hidden /> : <CopyOutlined aria-hidden />} aria-label="Copy subscription link" disabled={!url} onClick={() => void copyLink()}>{copied ? "Copied" : "Copy link"}</Button><Button icon={<DownloadOutlined aria-hidden />} aria-label="Download subscription" href={url || undefined} disabled={!url || !quota.available || selectedProfile?.enabled === false} rel="noreferrer" download>Download</Button></Space>
      {!quota.available && <Alert className="form-alert" type="warning" showIcon title={!quota.has_plan ? "No subscription plan assigned" : quota.expired ? "Your plan has expired" : "Your traffic quota has been reached"} />}
    </Form></Card></section>
    {profile.node_limits?.length > 0 && <section aria-label="Node limits"><Card title="Node limits"><Table rowKey="node_id" dataSource={profile.node_limits} pagination={false} scroll={{ x: 420 }} columns={[
      { title: "Node", dataIndex: "name" }, { title: "Speed", dataIndex: "speed_limit_mbps", render: value => value ? `${value} Mbps` : "Unlimited speed" },
      { title: "Connections", dataIndex: "device_limit", render: value => value ? `${value} connections` : "Unlimited connections" },
    ]} /></Card></section>}
  </Space> : null;
  return <Layout className="account-layout">
    <Layout.Header className="application-header"><Typography.Title level={4} style={{ margin: 0 }}>Open Node</Typography.Title><Space><Typography.Text>{username}</Typography.Text><Button icon={<LogoutOutlined aria-hidden />} aria-label="Sign out" loading={logoutBusy} onClick={() => void logout()} /></Space></Layout.Header>
    <Layout.Content className="account-content"><Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      <Flex gap="middle" justify="space-between" align="center"><Typography.Title level={2}>{profile?.display_name || username}</Typography.Title><Button icon={<ReloadOutlined aria-hidden />} aria-label="Refresh account" loading={loading} onClick={() => void load()} /></Flex>
      {error && <Alert type="error" showIcon title={error} />}
      {loading && <Spin aria-label="Refreshing account" />}
      <Tabs activeKey={tab} onChange={setTab} destroyOnHidden items={[
        { key: "subscription", label: "Subscription", children: subscriptionContent },
        { key: "routes", label: "Routes", children: <PrivateRoutedNodesPanel /> },
        { key: "templates", label: "Templates", children: <TemplatesWorkspace subscriber /> },
        { key: "security", label: "Security", children: <SubscriberSecurityPanel onChanged={() => void load()} /> },
      ]} />
    </Space></Layout.Content>
    <SubscriptionShortCodeDialog open={shortCodeOpen} onOpenChange={setShortCodeOpen} username={username} subscriber onSaved={setSubscription} />
  </Layout>;
}
