import { ApartmentOutlined, ReloadOutlined } from "../../ui/icons";
import { Alert, Button, Card, Flex, Form, Select, Spin, Tabs, Typography } from "../../ui";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import type { ServerSummary } from "../../domain/inventory";
import { listServers } from "../../services/inventory";
import ServerEgressPanel from "../components/ServerEgressPanel";
import SharedIngressDialog from "../components/SharedIngressDialog";
import ConfigView from "./ConfigView";
import DashboardView from "./DashboardView";
import DDNSView from "./DDNSView";
import ServerSharingView from "./ServerSharingView";

const tabs = ["access", "egress", "reverse-proxy", "sharing", "ddns"] as const;
type ServerTab = typeof tabs[number];

function selectedTab(value: string | null): ServerTab {
  return tabs.includes(value as ServerTab) ? value as ServerTab : "access";
}

function ReverseProxyPanel() {
  const request = useRef(0);
  const [servers, setServers] = useState<ServerSummary[]>([]);
  const [serverId, setServerId] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    const current = ++request.current;
    setLoading(true); setError("");
    try {
      const values = (await listServers()).filter(server => !server.is_federated);
      if (current !== request.current) return;
      setServers(values);
      setServerId(previous => values.some(server => server.id === previous) ? previous : "");
    } catch {
      if (current === request.current) { setServers([]); setServerId(""); setError("无法读取可配置反向代理的服务器，请稍后重试。"); }
    } finally {
      if (current === request.current) setLoading(false);
    }
  }

  useEffect(() => { void load(); return () => { request.current += 1; }; }, []);

  return <Card title="反向代理与 443 SNI 分流" extra={<Button icon={<ReloadOutlined aria-hidden />} aria-label="刷新反向代理服务器" loading={loading} onClick={() => void load()} />}>
    <Flex vertical gap="middle">
      <Typography.Paragraph type="secondary">选择一台本地主控服务器，直接配置共享 443 入口、自动节点路由和网站 HTTPS 反向代理。</Typography.Paragraph>
      <Alert type="info" showIcon title="只列出由当前主控直接管理的服务器；共享服务器的入口由拥有方配置。" />
      {error && <Alert type="error" showIcon role="alert" title={error} />}
      <Form layout="vertical">
        <Form.Item label="服务器" required>
          <Select aria-label="反向代理服务器" allowClear showSearch optionFilterProp="label" placeholder="选择服务器"
            value={serverId || undefined} loading={loading} disabled={loading || !servers.length}
            options={servers.map(server => ({ value: server.id, label: server.name }))}
            onChange={value => { setServerId(value ?? ""); setDialogOpen(false); }} />
        </Form.Item>
      </Form>
      {loading && <Spin size="small" />}
      {!loading && !servers.length && !error && <Alert type="warning" showIcon title="暂无可配置服务器，请先在“接入与维护”中添加服务器。" />}
      <Button type="primary" icon={<ApartmentOutlined aria-hidden />} disabled={!serverId || loading} onClick={() => setDialogOpen(true)}>打开反向代理配置</Button>
    </Flex>
    <SharedIngressDialog open={dialogOpen} serverId={serverId} onOpenChange={setDialogOpen} />
  </Card>;
}

export default function ServerWorkspaceView() {
  const [params, setParams] = useSearchParams();
  const active = selectedTab(params.get("tab"));
  function change(tab: string) {
    const next = new URLSearchParams(params);
    if (tab === "access") next.delete("tab"); else next.set("tab", tab);
    setParams(next, { replace: true });
  }
  return <div className="page-shell" data-testid="server-workspace">
    <div><Typography.Title level={2}>服务器管理</Typography.Title><Typography.Paragraph type="secondary">集中完成服务器接入、服务器设置、反向代理、共享和网络工具。</Typography.Paragraph></div>
    <Tabs activeKey={active} onChange={change} destroyOnHidden items={[
      { key: "access", label: "接入与维护", children: <DashboardView /> },
      { key: "egress", label: "服务器设置", children: active === "egress" ? <ServerEgressPanel advancedContent={<ConfigView allowNodeCatalogMutations={false} />} /> : null },
      { key: "reverse-proxy", label: "反向代理", children: <ReverseProxyPanel /> },
      { key: "sharing", label: "共享接入", children: <ServerSharingView /> },
      { key: "ddns", label: "动态 DNS", children: <DDNSView /> },
    ]} />
  </div>;
}
