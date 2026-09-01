import { App as AntApp, Alert, Button, ConfigProvider, Drawer, Grid, Layout, Menu, Result, Space, Spin, Tag, Typography, theme as antTheme } from "antd";
import { ApartmentOutlined, BellOutlined, CloudDownloadOutlined, CloudSyncOutlined, ControlOutlined, DashboardOutlined, FileProtectOutlined, FileTextOutlined, FilterOutlined, FundOutlined, HistoryOutlined, LineChartOutlined, LogoutOutlined, MenuOutlined, SafetyOutlined, SettingOutlined, ShareAltOutlined } from "@ant-design/icons";
import { Component, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import zhCN from "antd/locale/zh_CN";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { routes } from "../routes";
import { loadSession, signOut } from "../services/auth";
import { useAdministratorSession } from "./hooks/useSession";
import { BrandingProvider, useBranding } from "./hooks/useBranding";
import SignInView from "./views/SignInView";
import { zhMessage } from "../i18n/zh-CN";
import { AppearanceProvider, useAppearance } from "./hooks/useAppearance";
import { SiteLogo, ThemeSelector } from "./components/AppearanceChrome";

const navigation = [
  { key: "/", label: "概览", icon: <DashboardOutlined aria-hidden /> },
  { key: "/subscriptions", label: "订阅管理", icon: <ApartmentOutlined aria-hidden /> },
  { key: "/templates", label: "订阅模板", icon: <FileTextOutlined aria-hidden /> },
  { key: "/subscription-customizations", label: "订阅自定义", icon: <FilterOutlined aria-hidden /> },
  { key: "/changes", label: "变更集", icon: <HistoryOutlined aria-hidden /> },
  { key: "/config", label: "配置管理", icon: <SettingOutlined aria-hidden /> },
  { key: "/server-sharing", label: "服务器共享", icon: <ShareAltOutlined aria-hidden /> },
  { key: "/ddns", label: "动态 DNS", icon: <CloudSyncOutlined aria-hidden /> },
  { key: "/speedtests", label: "节点测速", icon: <FundOutlined aria-hidden /> },
  { key: "/certificates", label: "证书管理", icon: <FileProtectOutlined aria-hidden /> },
  { key: "/probe", label: "探针", icon: <LineChartOutlined aria-hidden /> },
  { key: "/access", label: "访问管理", icon: <SafetyOutlined aria-hidden /> },
  { key: "/notifications", label: "通知设置", icon: <BellOutlined aria-hidden /> },
  { key: "/system-settings", label: "系统设置", icon: <ControlOutlined aria-hidden /> },
  { key: "/backups", label: "备份与恢复", icon: <CloudDownloadOutlined aria-hidden /> },
  { key: "/renewals", label: "续费审核", icon: <HistoryOutlined aria-hidden /> },
];
class WorkspaceBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) return <Result status="error" title="无法加载此工作区" subTitle="请重新加载应用后检查操作状态。尚未保存的表单内容将被清除。" extra={<Button type="primary" onClick={() => window.location.reload()}>重新加载应用</Button>} />;
    return this.props.children;
  }
}
function WorkspaceRoutes() {
  const location = useLocation();
  return <WorkspaceBoundary key={location.pathname}><Suspense fallback={<div className="loading-page" role="status" aria-label="正在加载工作区"><Spin size="large" /></div>}><Routes>
    {routes.map(route => <Route key={route.path} path={route.path} element={<route.component />} />)}
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes></Suspense></WorkspaceBoundary>;
}

function ApplicationLayout() {
  const auth = useAdministratorSession();
  const { branding } = useBranding();
  const { dark } = useAppearance();
  const location = useLocation();
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const mobile = !screens.lg;
  const subscriber = routes.some(route => route.meta?.subscriber && route.path === location.pathname.replace(/\/+$/, ""));
  const sessionRequest = useRef<Promise<void> | null>(null);
  const [drawer, setDrawer] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  useEffect(() => {
    if (!subscriber && !auth.ready && !sessionRequest.current) sessionRequest.current = loadSession().finally(() => { sessionRequest.current = null; });
  }, [subscriber, auth.ready]);
  useEffect(() => { setDrawer(false); }, [location.pathname, mobile]);
  useEffect(() => {
    const title = subscriber ? "用户中心" : auth.session?.configured === false ? "首次初始化" : !auth.session?.authenticated ? "管理员登录" : navigation.find(item => item.key === location.pathname)?.label ?? "管理后台";
    document.documentElement.lang = "zh-CN";
    document.title = `${title} - ${branding.site_title}`;
  }, [auth.session?.authenticated, auth.session?.configured, branding.site_title, location.pathname, subscriber]);
  async function logout() {
    if (logoutBusy) return;
    setLogoutBusy(true); setLogoutError("");
    try { await signOut(); }
    catch (cause) { setLogoutError(zhMessage(cause, "退出登录失败，请检查会话状态后重试。")); await loadSession(); }
    finally { setLogoutBusy(false); }
  }
  if (subscriber) return <WorkspaceRoutes />;
  if (!auth.ready) return <div className="auth-page" role="status" aria-label="正在加载会话"><Spin size="large" /></div>;
  if (!auth.session?.authenticated) return <SignInView />;
  const menu = <Menu mode="inline" theme={dark ? "dark" : "light"} selectedKeys={[location.pathname]} items={navigation} onClick={({ key }) => { navigate(key); setDrawer(false); }} />;
  return <Layout className="application-layout">
    {!mobile && <Layout.Sider theme={dark ? "dark" : "light"} width={248} collapsible collapsed={collapsed} onCollapse={setCollapsed}><div className="application-brand"><SiteLogo compact={collapsed} />{!collapsed && <Typography.Title level={4} className="branding-block-text" style={{ margin: 0 }}>{branding.brand_title}</Typography.Title>}{!collapsed && <Typography.Text type="secondary">管理后台</Typography.Text>}</div>{menu}</Layout.Sider>}
    <Drawer title={<span className="branding-header-text" title={branding.brand_title}>{branding.brand_title}</span>} placement="left" size={280} open={mobile && drawer} onClose={() => setDrawer(false)} styles={{ body: { padding: 0 } }}>{menu}</Drawer>
    <Layout>
      <Layout.Header className="application-header"><div className="application-header-brand">{mobile && <Button icon={<MenuOutlined aria-hidden />} aria-label="切换导航菜单" onClick={() => setDrawer(true)} />}<SiteLogo compact /><Typography.Title level={4} className="branding-header-text" title={branding.brand_title} style={{ margin: 0 }}>{branding.brand_title}</Typography.Title></div><Space className="application-header-actions"><ThemeSelector /><Tag color="success" aria-label="免费版">免费版</Tag><Button icon={<LogoutOutlined aria-hidden />} aria-label="退出登录" loading={logoutBusy} onClick={() => void logout()} /></Space></Layout.Header>
      <Layout.Content className="application-content">{logoutError && <Alert className="form-alert" type="error" showIcon title={logoutError} role="alert" />}<WorkspaceRoutes /></Layout.Content>
    </Layout>
  </Layout>;
}
function ThemedApplication() {
  const { dark } = useAppearance();
  useEffect(() => { document.documentElement.dataset.openNodeTheme = dark ? "dark" : "light"; }, [dark]);
  return <ConfigProvider locale={zhCN} theme={{ algorithm: dark ? antTheme.darkAlgorithm : antTheme.defaultAlgorithm }}><AntApp><BrandingProvider><ApplicationLayout /></BrandingProvider></AntApp></ConfigProvider>;
}
export default function App() {
  return <AppearanceProvider><ThemedApplication /></AppearanceProvider>;
}
