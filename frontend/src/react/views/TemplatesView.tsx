import { Tabs, Typography } from "../../ui";
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
      <Typography.Paragraph type="secondary">全局维护 Clash / Mihomo YAML 模板；每个套餐可选择一个模板，未选择时自动使用全局默认模板。</Typography.Paragraph>
    </div>
    <Tabs activeKey={active} onChange={change} destroyOnHidden items={[
      { key: "library", label: "模板库", children: active === "library" ? <TemplatesWorkspace /> : null },
      { key: "customizations", label: "订阅自定义", children: active === "customizations" ? <SubscriptionCustomizationsView /> : null },
    ]} />
  </main>;
}
