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
import { zhMessage } from "../../i18n/zh-CN";

const formats: { label: string; value: SubscriptionClientFormat }[] = [
  { label: "Clash / Mihomo", value: "clash" }, { label: "sing-box", value: "sing-box" }, { label: "Surge", value: "surge" },
  { label: "Xray", value: "xray" }, { label: "URI 列表", value: "uri-list" }, { label: "Base64", value: "base64" },
];
const date = (value?: string | null) => value ? new Date(value).toLocaleDateString("zh-CN") : "无";
function bytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const unit = Math.min(Math.floor(Math.log(value) / Math.log(1024)), 4);
  return `${(value / 1024 ** unit).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} ${["B", "KiB", "MiB", "GiB", "TiB"][unit]}`;
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
  if (initializing || !auth.ready) return <div className="auth-page" role="status" aria-label="正在加载账户"><Spin size="large" /></div>;
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
      if (scope.isCurrent(current)) setError(zhMessage(failure, "登录失败，请稍后重试。"));
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
        const detail = zhMessage(failure, created ? "登录失败，请稍后重试。" : "注册失败，请检查填写的信息后重试。");
        setError(created ? `账户已创建，但未能登录。${detail}` : detail);
      }
    } finally { if (scope.isCurrent(current)) { setPassword(""); setConfirmPassword(""); setBusy(false); busyRef.current = false; } }
  }
  function restart() { scope.invalidate(); setChallenge(""); setCode(""); setPassword(""); setError(""); }
  function cancelRegistration() { onClearInvitation(); setPassword(""); setConfirmPassword(""); setEmail(""); setDisplayName(""); setError(""); }

  return <section className="auth-page"><Card className="auth-card"><Space orientation="vertical" size="large" style={{ width: "100%" }}>
    <div><Typography.Title level={2}>Open Node</Typography.Title><Typography.Title level={4}>{registering ? "创建用户账户" : challenge ? "双重验证" : "用户登录"}</Typography.Title></div>
    {auth.error ? <Alert type="error" showIcon title={zhMessage(auth.error, "暂时无法连接服务器。")} action={<Button icon={<ReloadOutlined aria-hidden />} aria-label="重新连接账户" onClick={() => void loadSubscriberSession()} />} /> : <Form layout="vertical" onFinish={() => void (registering ? register() : submit())}>
      {error && <Alert className="form-alert" type="error" showIcon title={error} />}
      {(!challenge || registering) && <Form.Item label="用户名" htmlFor="subscriber-username" required><Input id="subscriber-username" value={username} onChange={event => setUsername(event.target.value)} autoComplete="username" autoFocus required maxLength={80} disabled={busy} /></Form.Item>}
      {registering && <>
        <Form.Item label="显示名称" htmlFor="subscriber-display-name"><Input id="subscriber-display-name" value={displayName} onChange={event => setDisplayName(event.target.value)} autoComplete="name" maxLength={120} disabled={busy} /></Form.Item>
        <Form.Item label="邮箱" htmlFor="subscriber-email"><Input id="subscriber-email" type="email" value={email} onChange={event => setEmail(event.target.value)} autoComplete="email" maxLength={255} disabled={busy} /></Form.Item>
      </>}
      {(!challenge || registering) && <Form.Item label="密码" htmlFor="subscriber-password" required><Input.Password id="subscriber-password" value={password} onChange={event => setPassword(event.target.value)} autoComplete={registering ? "new-password" : "current-password"} minLength={registering ? 12 : undefined} required maxLength={1024} disabled={busy} /></Form.Item>}
      {registering && <Form.Item label="确认密码" htmlFor="subscriber-confirm-password" required validateStatus={confirmPassword && confirmPassword !== password ? "error" : undefined} help={confirmPassword && confirmPassword !== password ? "两次输入的密码不一致" : undefined}><Input.Password id="subscriber-confirm-password" value={confirmPassword} onChange={event => setConfirmPassword(event.target.value)} autoComplete="new-password" required minLength={12} maxLength={1024} disabled={busy} /></Form.Item>}
      {!registering && challenge && <Form.Item label="验证器验证码或恢复码" htmlFor="subscriber-login-code" required><Input id="subscriber-login-code" value={code} onChange={event => setCode(event.target.value)} autoComplete="one-time-code" autoFocus required maxLength={64} disabled={busy} /></Form.Item>}
      <Space wrap><Button type="primary" htmlType="submit" aria-label={registering ? "创建账户" : challenge ? "验证" : "登录"} icon={<LoginOutlined aria-hidden />} loading={busy} disabled={registering ? !canRegister : challenge ? !code : !username || !password}>{registering ? "创建账户" : challenge ? "验证" : "登录"}</Button>
        {registering ? <Button disabled={busy} onClick={cancelRegistration}>登录</Button> : challenge && <Button disabled={busy} onClick={restart}>返回</Button>}
      </Space>
    </Form>}
    <Link to="/">管理员登录</Link>
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
  const status = !quota?.has_plan ? "未分配套餐" : quota.expired ? "已到期" : quota.over_quota ? "流量已用尽" : "有效";
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
      if (scope.isCurrent(current)) setError(zhMessage(failure, "暂时无法加载账户信息。"));
    } finally { if (scope.isCurrent(current)) setLoading(false); }
  }, [scope]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setCopied(false); }, [url]);
  async function logout() {
    if (logoutBusy) return;
    setLogoutBusy(true); setError("");
    try { await subscriberSignOut(); }
    catch (failure) { setError(zhMessage(failure, "退出登录失败，请稍后重试。")); }
    finally { setLogoutBusy(false); }
  }
  async function copyLink() {
    if (!url) return;
    try { await navigator.clipboard.writeText(url); setCopied(true); }
    catch { setError("无法访问剪贴板，请手动复制。"); }
  }
  const subscriptionContent = profile && quota ? <Space orientation="vertical" size="large" style={{ width: "100%" }}>
    <section aria-label="当前套餐"><Card title={quota.plan_name || "尚未分配套餐"} extra={<Tag color={quota.available ? "success" : "warning"}>{status}</Tag>}>
      <Typography.Paragraph type="secondary">当前套餐</Typography.Paragraph>
      <Typography.Title level={3}>{bytes(quota.charged_usage_bytes)} <Typography.Text type="secondary">/ {quota.traffic_limit_bytes ? bytes(quota.traffic_limit_bytes) : quota.has_plan ? "不限" : "0 B"}</Typography.Text></Typography.Title>
      <Progress percent={Math.min(100, Math.max(0, quota.percent_used))} status={quota.over_quota ? "exception" : undefined} aria-label="已用流量额度" />
      <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} items={[
        { key: "expires", label: "到期时间", children: date(quota.plan_expires_at) },
        { key: "reset", label: "下次流量重置", children: date(quota.next_reset_at) },
        { key: "speed", label: "默认速度", children: profile.speed_limit_mbps ? `${profile.speed_limit_mbps} Mbps` : "不限" },
        { key: "connections", label: "连接数上限", children: profile.device_limit || "不限" },
        { key: "upload", label: "已上传", children: bytes(quota.upload) },
        { key: "download", label: "已下载", children: bytes(quota.download) },
      ]} />
    </Card></section>
    <section aria-label="订阅链接"><Card title="订阅"><Form layout="vertical">
      {profiles.length > 0 && <Form.Item label="配置" htmlFor="account-configuration"><Select id="account-configuration" value={configuration} options={[{ label: "默认订阅", value: "default" }, ...profiles.map(item => ({ label: item.name, value: item.id }))]} onChange={setConfiguration} /></Form.Item>}
      <Flex gap="middle" justify="space-between" align="center" wrap className="form-alert">
        {!selectedProfile && subscription?.short_links_enabled ? <Segmented aria-label="订阅链接类型" value={linkType} onChange={setLinkType} options={[{ label: "完整链接", value: "full" }, { label: "短链接", value: "short" }]} /> : <Tag>{selectedProfile ? "MMWX 订阅配置" : "安全链接"}</Tag>}
        {subscription?.short_links_enabled && <Button icon={<EditOutlined aria-hidden />} aria-label="编辑订阅短码" disabled={loading || !subscription || Boolean(selectedProfile)} onClick={() => setShortCodeOpen(true)} />}
      </Flex>
      {selectedProfile && (!selectedProfile.enabled || selectedProfile.warnings.length > 0) && <Alert className="form-alert" type={selectedProfile.enabled ? "warning" : "error"} showIcon title={selectedProfile.enabled ? selectedProfile.warnings.map(warning => zhMessage(warning, "此订阅配置有待处理的提示，请联系管理员检查。")).join("；") : "此订阅配置需要管理员进一步设置。"} />}
      <Form.Item label="客户端格式" htmlFor="account-client-format"><Select id="account-client-format" value={format} options={formats} onChange={setFormat} /></Form.Item>
      <Form.Item label="订阅地址" htmlFor="account-subscription-url"><Input id="account-subscription-url" value={url} readOnly /></Form.Item>
      <Space wrap><Button icon={copied ? <CheckOutlined aria-hidden /> : <CopyOutlined aria-hidden />} aria-label="复制订阅链接" disabled={!url} onClick={() => void copyLink()}>{copied ? "已复制" : "复制链接"}</Button><Button icon={<DownloadOutlined aria-hidden />} aria-label="下载订阅" href={url || undefined} disabled={!url || !quota.available || selectedProfile?.enabled === false} rel="noreferrer" download>下载</Button></Space>
      {!quota.available && <Alert className="form-alert" type="warning" showIcon title={!quota.has_plan ? "尚未分配订阅套餐" : quota.expired ? "你的套餐已到期" : "你的流量额度已用尽"} />}
    </Form></Card></section>
    {profile.node_limits?.length > 0 && <section aria-label="节点限制"><Card title="节点限制"><Table rowKey="node_id" dataSource={profile.node_limits} pagination={false} scroll={{ x: 420 }} columns={[
      { title: "节点", dataIndex: "name" }, { title: "速度", dataIndex: "speed_limit_mbps", render: value => value ? `${value} Mbps` : "不限速" },
      { title: "连接数", dataIndex: "device_limit", render: value => value ? `${value} 个连接` : "连接数不限" },
    ]} /></Card></section>}
  </Space> : null;
  return <Layout className="account-layout">
    <Layout.Header className="application-header"><Typography.Title level={4} style={{ margin: 0 }}>Open Node</Typography.Title><Space><Typography.Text>{username}</Typography.Text><Button icon={<LogoutOutlined aria-hidden />} aria-label="退出登录" loading={logoutBusy} onClick={() => void logout()} /></Space></Layout.Header>
    <Layout.Content className="account-content"><Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      <Flex gap="middle" justify="space-between" align="center"><Typography.Title level={2}>{profile?.display_name || username}</Typography.Title><Button icon={<ReloadOutlined aria-hidden />} aria-label="刷新账户" loading={loading} onClick={() => void load()} /></Flex>
      {error && <Alert type="error" showIcon title={error} />}
      {loading && <Spin aria-label="正在刷新账户" />}
      <Tabs activeKey={tab} onChange={setTab} destroyOnHidden items={[
        { key: "subscription", label: "订阅", children: subscriptionContent },
        { key: "routes", label: "路由", children: <PrivateRoutedNodesPanel /> },
        { key: "templates", label: "模板", children: <TemplatesWorkspace subscriber /> },
        { key: "security", label: "安全设置", children: <SubscriberSecurityPanel onChanged={() => void load()} /> },
      ]} />
    </Space></Layout.Content>
    <SubscriptionShortCodeDialog open={shortCodeOpen} onOpenChange={setShortCodeOpen} username={username} subscriber onSaved={setSubscription} />
  </Layout>;
}
