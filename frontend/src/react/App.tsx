import { App as UiApp, Alert, Button, Result, Spin, Tag } from "../ui";
import { ApartmentOutlined, BranchesOutlined, ControlOutlined, DashboardOutlined, FileTextOutlined, LockOutlined, LogoutOutlined, MenuOutlined, SafetyOutlined } from "../ui/icons";
import { Component, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { legacyRouteRedirects, routes } from "../routes";
import { loadSession, signOut } from "../services/auth";
import { useAdministratorSession } from "./hooks/useSession";
import { BrandingProvider, useBranding } from "./hooks/useBranding";
import SignInView from "./views/SignInView";
import { zhMessage } from "../i18n/zh-CN";
import { AppearanceProvider, useAppearance } from "./hooks/useAppearance";
import { SiteLogo, ThemeSelector } from "./components/AppearanceChrome";

const navigation = [
  { key: "/servers", label: "服务器管理", group: "基础设施", icon: <DashboardOutlined aria-hidden /> },
  { key: "/nodes", label: "节点管理", group: "基础设施", icon: <BranchesOutlined aria-hidden /> },
  { key: "/templates", label: "模板管理", group: "订阅交付", icon: <FileTextOutlined aria-hidden /> },
  { key: "/plans", label: "套餐管理", group: "订阅交付", icon: <ApartmentOutlined aria-hidden /> },
  { key: "/users", label: "用户管理", group: "订阅交付", icon: <SafetyOutlined aria-hidden /> },
  { key: "/certificates", label: "证书管理", group: "平台", icon: <LockOutlined aria-hidden /> },
  { key: "/system-settings", label: "系统设置", group: "平台", icon: <ControlOutlined aria-hidden /> },
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
    {legacyRouteRedirects.map(route => <Route key={route.path} path={route.path} element={<Navigate to={route.to} replace />} />)}
    <Route path="*" element={<Navigate to="/servers" replace />} />
  </Routes></Suspense></WorkspaceBoundary>;
}

function ApplicationLayout() {
  const auth = useAdministratorSession();
  const { branding } = useBranding();
  const location = useLocation();
  const navigate = useNavigate();
  const subscriber = routes.some(route => route.meta?.subscriber && route.path === location.pathname.replace(/\/+$/, ""));
  const sessionRequest = useRef<Promise<void> | null>(null);
  const [navigationOpen, setNavigationOpen] = useState(false);
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  useEffect(() => {
    if (!subscriber && !auth.ready && !sessionRequest.current) sessionRequest.current = loadSession().finally(() => { sessionRequest.current = null; });
  }, [subscriber, auth.ready]);
  useEffect(() => { setNavigationOpen(false); }, [location.pathname]);
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
  const page = navigation.find(item => item.key === location.pathname) ?? navigation[0];
  let activeGroup = "";
  return <div className={`control-shell${navigationOpen ? " navigation-open" : ""}`}>
    <button type="button" className="navigation-backdrop" aria-label="关闭导航菜单" onClick={() => setNavigationOpen(false)} />
    <aside className="control-sidebar" aria-label="管理后台导航">
      <div className="sidebar-brand">
        <span className="sidebar-logo"><SiteLogo compact /><span aria-hidden>ON</span></span>
        <span><strong className="branding-block-text" title={branding.brand_title}>{branding.brand_title}</strong><small>CONTROL CENTER</small></span>
        <button type="button" className="sidebar-close" aria-label="关闭导航菜单" onClick={() => setNavigationOpen(false)}>×</button>
      </div>
      <nav className="control-navigation" role="menu" aria-label="主导航">
        {navigation.map(item => {
          const heading = item.group !== activeGroup;
          activeGroup = item.group;
          return <div className="navigation-entry" key={item.key}>
            {heading && <div className="navigation-group">{item.group}</div>}
            <button role="menuitem" type="button" className={location.pathname === item.key ? "is-active" : ""}
              aria-current={location.pathname === item.key ? "page" : undefined}
              onClick={() => { navigate(item.key); setNavigationOpen(false); }}>
              <span className="navigation-icon">{item.icon}</span><span>{item.label}</span>
            </button>
          </div>;
        })}
      </nav>
      <div className="sidebar-foot"><span className="status-dot" aria-hidden /><span>控制面运行中</span><Tag color="success" aria-label="免费版">免费版</Tag></div>
    </aside>
    <section className="control-stage">
      <header className="application-header">
        <div className="application-header-brand">
          <Button className="navigation-trigger" icon={<MenuOutlined aria-hidden />} aria-label="切换导航菜单" onClick={() => setNavigationOpen(value => !value)} />
          <div className="header-logo"><SiteLogo compact /></div>
          <div className="header-title-block"><h1 className="branding-header-text" title={branding.brand_title}>{branding.brand_title}</h1><span>{page.group} / {page.label}</span></div>
        </div>
        <div className="application-header-actions"><ThemeSelector /><Button className="logout-button" icon={<LogoutOutlined aria-hidden />} aria-label="退出登录" loading={logoutBusy} onClick={() => void logout()}>退出</Button></div>
      </header>
      <main className="application-content">{logoutError && <Alert className="form-alert" type="error" showIcon title={logoutError} role="alert" />}<WorkspaceRoutes /></main>
    </section>
  </div>;
}
function ThemedApplication() {
  const { dark } = useAppearance();
  useEffect(() => { document.documentElement.dataset.openNodeTheme = dark ? "dark" : "light"; }, [dark]);
  return <UiApp><BrandingProvider><ApplicationLayout /></BrandingProvider></UiApp>;
}
export default function App() {
  return <AppearanceProvider><ThemedApplication /></AppearanceProvider>;
}
