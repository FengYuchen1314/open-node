import { Tabs, Typography } from "antd";
import { useSearchParams } from "react-router-dom";

import NodeTopologiesView from "./NodeTopologiesView";
import SpeedTestsView from "./SpeedTestsView";
import SubscriptionsView from "./SubscriptionsView";

const tabs = ["catalog", "topologies", "speed"] as const;
type NodeTab = typeof tabs[number];

function selectedTab(value: string | null): NodeTab {
  return tabs.includes(value as NodeTab) ? value as NodeTab : "catalog";
}

export default function NodesView() {
  const [params, setParams] = useSearchParams();
  const active = selectedTab(params.get("tab"));
  function change(tab: string) {
    const next = new URLSearchParams(params);
    if (tab === "catalog") next.delete("tab"); else next.set("tab", tab);
    setParams(next, { replace: true });
  }
  return <div className="page-shell" data-testid="nodes-workspace">
    <div><Typography.Title level={2}>节点管理</Typography.Title><Typography.Paragraph type="secondary">集中创建节点、编排节点拓扑并执行节点测速。</Typography.Paragraph></div>
    <Tabs activeKey={active} onChange={change} destroyOnHidden items={[
      { key: "catalog", label: "节点目录", children: active === "catalog" ? <SubscriptionsView workspace="nodes" /> : null },
      { key: "topologies", label: "节点编排", children: active === "topologies" ? <NodeTopologiesView /> : null },
      { key: "speed", label: "节点测速", children: active === "speed" ? <SpeedTestsView /> : null },
    ]} />
  </div>;
}
