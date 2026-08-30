import { App as AntApp, Alert, Button, ConfigProvider, Drawer, Grid, Layout, Menu, Result, Space, Spin, Tag, Typography } from "antd";
import { ApartmentOutlined, DashboardOutlined, FileProtectOutlined, FileTextOutlined, HistoryOutlined, LineChartOutlined, LogoutOutlined, MenuOutlined, SafetyOutlined, SettingOutlined } from "@ant-design/icons";
import { Component, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { routes } from "../routes";
import { loadSession, signOut } from "../services/auth";
import { useAdministratorSession } from "./hooks/useSession";
import SignInView from "./views/SignInView";

const navigation = [
  { key: "/", label: "Overview", icon: <DashboardOutlined aria-hidden /> },
  { key: "/subscriptions", label: "Subscriptions", icon: <ApartmentOutlined aria-hidden /> },
  { key: "/templates", label: "Templates", icon: <FileTextOutlined aria-hidden /> },
  { key: "/changes", label: "Changes", icon: <HistoryOutlined aria-hidden /> },
  { key: "/config", label: "Config", icon: <SettingOutlined aria-hidden /> },
  { key: "/certificates", label: "Certificates", icon: <FileProtectOutlined aria-hidden /> },
  { key: "/probe", label: "Probe", icon: <LineChartOutlined aria-hidden /> },
  { key: "/access", label: "Access", icon: <SafetyOutlined aria-hidden /> },
];
class WorkspaceBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) return <Result status="error" title="Unable to load this workspace" subTitle="Reload to fetch the current application, then check the operation status. Unsaved form entries will be cleared." extra={<Button type="primary" onClick={() => window.location.reload()}>Reload application</Button>} />;
    return this.props.children;
  }
}
function WorkspaceRoutes() {
  const location = useLocation();
  return <WorkspaceBoundary key={location.pathname}><Suspense fallback={<div className="loading-page" role="status" aria-label="Loading workspace"><Spin size="large" /></div>}><Routes>
    {routes.map(route => <Route key={route.path} path={route.path} element={<route.component />} />)}
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes></Suspense></WorkspaceBoundary>;
}

function ApplicationLayout() {
  const auth = useAdministratorSession();
  const location = useLocation();
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const mobile = !screens.lg;
  const subscriber = location.pathname.replace(/\/+$/, "") === "/account";
  const sessionRequest = useRef<Promise<void> | null>(null);
  const [drawer, setDrawer] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  useEffect(() => {
    if (!subscriber && !auth.ready && !sessionRequest.current) sessionRequest.current = loadSession().finally(() => { sessionRequest.current = null; });
  }, [subscriber, auth.ready]);
  useEffect(() => { setDrawer(false); }, [location.pathname, mobile]);
  async function logout() {
    if (logoutBusy) return;
    setLogoutBusy(true); setLogoutError("");
    try { await signOut(); }
    catch (cause) { setLogoutError(cause instanceof Error ? cause.message : "Sign-out failed"); await loadSession(); }
    finally { setLogoutBusy(false); }
  }
  if (subscriber) return <WorkspaceRoutes />;
  if (!auth.ready) return <div className="auth-page" role="status" aria-label="Loading session"><Spin size="large" /></div>;
  if (!auth.session?.authenticated) return <SignInView />;
  const menu = <Menu mode="inline" selectedKeys={[location.pathname]} items={navigation} onClick={({ key }) => { navigate(key); setDrawer(false); }} />;
  return <Layout className="application-layout">
    {!mobile && <Layout.Sider theme="light" width={248} collapsible collapsed={collapsed} onCollapse={setCollapsed}><div className="application-brand"><Typography.Title level={4} style={{ margin: 0 }}>{collapsed ? "ON" : "Open Node"}</Typography.Title>{!collapsed && <Typography.Text type="secondary">Control plane</Typography.Text>}</div>{menu}</Layout.Sider>}
    <Drawer title="Open Node" placement="left" size={280} open={mobile && drawer} onClose={() => setDrawer(false)} styles={{ body: { padding: 0 } }}>{menu}</Drawer>
    <Layout>
      <Layout.Header className="application-header"><Space>{mobile && <Button icon={<MenuOutlined aria-hidden />} aria-label="Toggle navigation" onClick={() => setDrawer(true)} />}<Typography.Title level={4} style={{ margin: 0 }}>Open Node</Typography.Title></Space><Space><Tag color="success" aria-label="Free edition">Free edition</Tag><Button icon={<LogoutOutlined aria-hidden />} aria-label="Sign out" loading={logoutBusy} onClick={() => void logout()} /></Space></Layout.Header>
      <Layout.Content className="application-content">{logoutError && <Alert className="form-alert" type="error" showIcon title={logoutError} role="alert" />}<WorkspaceRoutes /></Layout.Content>
    </Layout>
  </Layout>;
}
export default function App() {
  return <ConfigProvider><AntApp><ApplicationLayout /></AntApp></ConfigProvider>;
}
