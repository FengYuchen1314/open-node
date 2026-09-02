import { Tabs, Typography } from "antd";
import { useSearchParams } from "react-router-dom";

import AccessView from "./AccessView";
import AdminRenewalsView from "./AdminRenewalsView";
import BackupsView from "./BackupsView";
import ChangesView from "./ChangesView";
import NotificationsView from "./NotificationsView";
import ProbeView from "./ProbeView";
import SystemSettingsView from "./SystemSettingsView";
import SubscriptionsView from "./SubscriptionsView";

const tabs = ["general", "access", "notifications", "backups", "changes", "renewals", "probe", "migration"] as const;
type SystemTab = typeof tabs[number];

function selectedTab(value: string | null): SystemTab {
  return tabs.includes(value as SystemTab) ? value as SystemTab : "general";
}

export default function SystemWorkspaceView() {
  const [params, setParams] = useSearchParams();
  const active = selectedTab(params.get("tab"));
  function change(tab: string) {
    const next = new URLSearchParams(params);
    if (tab === "general") next.delete("tab"); else next.set("tab", tab);
    setParams(next, { replace: true });
  }
  return <div className="page-shell" data-testid="system-workspace">
    <div><Typography.Title level={2}>系统设置</Typography.Title><Typography.Paragraph type="secondary">集中管理站点、安全、通知、备份、变更、续费审核、探针和数据迁移。</Typography.Paragraph></div>
    <Tabs activeKey={active} onChange={change} destroyOnHidden items={[
      { key: "general", label: "基础设置", children: <SystemSettingsView /> },
      { key: "access", label: "访问管理", children: <AccessView /> },
      { key: "notifications", label: "通知", children: <NotificationsView /> },
      { key: "backups", label: "备份与恢复", children: <BackupsView /> },
      { key: "changes", label: "变更记录", children: <ChangesView allowPlanning={false} /> },
      { key: "renewals", label: "续费审核", children: <AdminRenewalsView /> },
      { key: "probe", label: "探针", children: <ProbeView /> },
      { key: "migration", label: "数据迁移", children: active === "migration" ? <SubscriptionsView workspace="migration" /> : null },
    ]} />
  </div>;
}
