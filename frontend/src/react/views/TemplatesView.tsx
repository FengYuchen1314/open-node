import { Tabs, Typography } from "antd";
import { useSearchParams } from "react-router-dom";

import TemplatesWorkspace from "../components/TemplatesWorkspace";
import SubscriptionCustomizationsView from "./SubscriptionCustomizationsView";

const tabs = ["library", "customizations"] as const;
type TemplateTab = typeof tabs[number];

function selectedTab(value: string | null): TemplateTab {
  return tabs.includes(value as TemplateTab) ? value as TemplateTab : "library";
}

export default function TemplatesView() {
  const [params, setParams] = useSearchParams();
  const active = selectedTab(params.get("tab"));

  function change(tab: string) {
    const next = new URLSearchParams(params);
    if (tab === "library") next.delete("tab"); else next.set("tab", tab);
    setParams(next, { replace: true });
  }

  return <main className="page-shell" data-testid="templates-workspace">
    <div>
      <Typography.Title level={2}>模板管理</Typography.Title>
      <Typography.Paragraph type="secondary">导入、编辑、预览和分配 Clash / Mihomo YAML 与 Surge 模板，并集中管理订阅规则、Proxy Provider 和覆写脚本。</Typography.Paragraph>
    </div>
    <Tabs activeKey={active} onChange={change} destroyOnHidden items={[
      { key: "library", label: "模板库", children: active === "library" ? <TemplatesWorkspace /> : null },
      { key: "customizations", label: "订阅自定义", children: active === "customizations" ? <SubscriptionCustomizationsView /> : null },
    ]} />
  </main>;
}
