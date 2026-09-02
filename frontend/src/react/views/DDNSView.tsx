import { EditOutlined, ReloadOutlined, SyncOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Collapse, Descriptions, Flex, Input, Modal, Select, Space, Switch, Table, Tag, Typography } from "antd";
import { useEffect, useRef, useState } from "react";

import type { DDNSProvider, DDNSServer } from "../../domain/ddns";
import { ddnsError, ddnsStatusMessage, loadDDNS, saveDDNS, syncDDNS } from "../../services/ddns";
import DNSProvidersPanel from "../components/DNSProvidersPanel";

interface Draft { enabled: boolean; provider_id: string | null; pull_address: string; pull_address_v6: string; }
const empty: Draft = { enabled: false, provider_id: null, pull_address: "", pull_address_v6: "" };
const providerLabels: Record<string, string> = {
  cloudflare: "Cloudflare", alidns: "阿里云 DNS", tencentcloud: "腾讯云 DNSPod",
  dnspod: "DNSPod Token", godaddy: "GoDaddy", namesilo: "NameSilo",
};
function date(value: string | null) { return value ? new Date(value).toLocaleString("zh-CN") : "—"; }
function state(item: DDNSServer) {
  if (!item.enabled) return <Tag>未启用</Tag>;
  if (item.pending) return <Tag icon={<SyncOutlined spin />} color="processing">同步中</Tag>;
  if (item.last_error) return <Tag color="error">等待重试</Tag>;
  if (item.last_synced_at) return <Tag color="success">已同步</Tag>;
  return <Tag color="warning">等待首次同步</Tag>;
}

export default function DDNSView() {
  const alive = useRef(true), timer = useRef<number | undefined>(undefined), sequence = useRef(0);
  const [servers, setServers] = useState<DDNSServer[]>([]), [providers, setProviders] = useState<DDNSProvider[]>([]);
  const [editing, setEditing] = useState<DDNSServer | null>(null), [draft, setDraft] = useState<Draft>(empty);
  const [providersOpen, setProvidersOpen] = useState(false);
  const [busy, setBusy] = useState(""), [error, setError] = useState(""), [notice, setNotice] = useState("");
  async function load() {
    const run = ++sequence.current; setBusy(previous => previous || "load"); setError("");
    try {
      const value = await loadDDNS(); if (!alive.current || run !== sequence.current) return;
      setServers(value.servers); setProviders(value.providers);
      const delay = value.servers.some(item => item.pending) ? 1500 : value.servers.some(item => item.enabled) ? 15000 : 0;
      window.clearTimeout(timer.current); if (delay) timer.current = window.setTimeout(() => void load(), delay);
    } catch (failure) { if (alive.current && run === sequence.current) setError(ddnsError(failure)); }
    finally { if (alive.current && run === sequence.current) setBusy(""); }
  }
  useEffect(() => {
    alive.current = true; void load();
    return () => { alive.current = false; sequence.current += 1; window.clearTimeout(timer.current); };
  }, []);
  function edit(item: DDNSServer) {
    setEditing(item); setDraft({ enabled: item.enabled, provider_id: item.provider_id,
      pull_address: item.pull_address ?? "", pull_address_v6: item.pull_address_v6 ?? "" });
    setError(""); setNotice("");
  }
  async function save() {
    if (!editing || busy) return; const current = editing; setBusy("save"); setError(""); setNotice("");
    try {
      const value = await saveDDNS(current, { enabled: draft.enabled, provider_id: draft.provider_id,
        pull_address: draft.pull_address.trim() || null, pull_address_v6: draft.pull_address_v6.trim() || null });
      if (!alive.current) return; setServers(previous => previous.map(item => item.server_id === value.server_id ? value : item));
      setEditing(null); setNotice(value.enabled ? "DDNS 设置已保存，后台正在等待或执行首次同步。" : "DDNS 已关闭，已有 DNS 记录不会自动删除。");
      window.clearTimeout(timer.current); if (value.enabled) timer.current = window.setTimeout(() => void load(), 1500);
    } catch (failure) { if (alive.current) { setError(ddnsError(failure)); void load(); } }
    finally { if (alive.current) setBusy(""); }
  }
  async function sync(item: DDNSServer) {
    if (busy) return; setBusy(`sync:${item.server_id}`); setError(""); setNotice("");
    try {
      const value = await syncDDNS(item); if (!alive.current) return;
      setServers(previous => previous.map(row => row.server_id === value.server_id ? value : row));
      setNotice("已排队手动同步；结果未知时只刷新状态，不会自动重复提交。");
      window.clearTimeout(timer.current); timer.current = window.setTimeout(() => void load(), 1000);
    } catch (failure) { if (alive.current) { setError(ddnsError(failure)); void load(); } }
    finally { if (alive.current) setBusy(""); }
  }
  const supported = providers.filter(item => item.supported);
  return <Space orientation="vertical" size="large" style={{ width: "100%" }}>
    <Flex justify="space-between" align="center" gap={16} wrap>
      <div><Typography.Title level={2} style={{ marginBottom: 4 }}>动态 DNS</Typography.Title><Typography.Text type="secondary">Agent 公网 IP 变化时，自动更新服务器域名的 A/AAAA 记录。</Typography.Text></div>
      <Button icon={<ReloadOutlined />} loading={busy === "load"} onClick={() => void load()}>刷新状态</Button>
    </Flex>
    <Alert type="info" showIcon title="DDNS 使用本页保存的 DNS 服务商凭据" description="自动模式会按域名只读探测可用服务商。保存的域名也会成为节点对外地址；关闭 DDNS 不会删除现有 DNS 记录。" />
    <Collapse activeKey={providersOpen ? ["providers"] : []} onChange={keys => setProvidersOpen(keys.includes("providers"))} destroyOnHidden items={[{ key: "providers", label: "DNS 服务商凭据", children: providersOpen ? <DNSProvidersPanel onUpdated={() => void load()} /> : null }]} />
    {error && <Alert type="error" showIcon title={error} role="alert" />}{notice && <Alert type="success" showIcon title={notice} />}
    <Card><Table<DDNSServer> rowKey="server_id" dataSource={servers} loading={busy === "load"} scroll={{ x: 980 }} locale={{ emptyText: "暂无服务器" }} columns={[
      { title: "服务器", dataIndex: "server_name", width: 150, render: (value, item) => <Space orientation="vertical" size={0}><Space><Typography.Text strong>{value}</Typography.Text>{item.is_federated && <Tag color="purple">分享</Tag>}</Space><Typography.Text type="secondary">{item.is_federated ? item.server_status === "connected" ? "拥有方在线" : "拥有方未连接" : item.server_status === "connected" ? "Agent 在线" : "Agent 未连接"}</Typography.Text></Space> },
      { title: "当前公网地址", width: 190, render: (_, item) => <Space orientation="vertical" size={0}><Typography.Text copyable={Boolean(item.ip_address)}>{item.ip_address || "无 IPv4"}</Typography.Text><Typography.Text copyable={Boolean(item.ip_address_v6)}>{item.ip_address_v6 || "无 IPv6"}</Typography.Text></Space> },
      { title: "DDNS 域名", width: 200, render: (_, item) => <Space orientation="vertical" size={0}><Typography.Text>{item.pull_address || "未设置 A 域名"}</Typography.Text><Typography.Text>{item.pull_address_v6 || (item.pull_address ? "AAAA 沿用 A 域名" : "未设置 AAAA 域名")}</Typography.Text></Space> },
      { title: "服务商", width: 150, render: (_, item) => item.provider_name ? <Space orientation="vertical" size={0}><span>{item.provider_name}</span><Typography.Text type="secondary">{providerLabels[item.provider_type ?? ""] ?? item.provider_type}</Typography.Text></Space> : "自动选择" },
      { title: "状态", width: 190, render: (_, item) => <Space orientation="vertical" size={2}>{state(item)}<Typography.Text type="secondary">{date(item.last_synced_at)}</Typography.Text>{item.last_error && <Typography.Text type="danger">{ddnsStatusMessage(item.last_error)}</Typography.Text>}</Space> },
      { title: "操作", fixed: "right", width: 175, render: (_, item) => <Space><Button icon={<EditOutlined />} onClick={() => edit(item)}>设置</Button><Button icon={<SyncOutlined />} disabled={!item.enabled || item.pending} loading={busy === `sync:${item.server_id}`} onClick={() => void sync(item)}>同步</Button></Space> },
    ]} /></Card>
    <Modal title={`DDNS 设置 · ${editing?.server_name ?? ""}`} open={Boolean(editing)} onCancel={() => !busy && setEditing(null)} onOk={() => void save()} confirmLoading={busy === "save"} okButtonProps={{ disabled: draft.enabled && !draft.pull_address.trim() && !draft.pull_address_v6.trim() }} destroyOnHidden>
      <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
        <Descriptions size="small" column={1} items={[{ key: "ip4", label: "当前 IPv4", children: editing?.ip_address || "未上报" }, { key: "ip6", label: "当前 IPv6", children: editing?.ip_address_v6 || "未上报" }]} />
        <Flex justify="space-between"><Typography.Text>启用动态 DNS</Typography.Text><Switch checked={draft.enabled} onChange={enabled => setDraft(value => ({ ...value, enabled }))} /></Flex>
        <div><Typography.Text>DNS 服务商</Typography.Text><Select style={{ width: "100%", marginTop: 6 }} value={draft.provider_id ?? "auto"} onChange={value => setDraft(row => ({ ...row, provider_id: value === "auto" ? null : value }))} options={[{ value: "auto", label: "自动选择（按域名只读探测）" }, ...supported.map(item => ({ value: item.id, label: `${item.name} · ${providerLabels[item.provider] ?? item.provider}` }))]} /></div>
        <div><Typography.Text>IPv4 域名（A）</Typography.Text><Input value={draft.pull_address} maxLength={255} placeholder="edge.example.com" onChange={event => setDraft(row => ({ ...row, pull_address: event.target.value }))} /></div>
        <div><Typography.Text>IPv6 域名（AAAA，可留空沿用 IPv4 域名）</Typography.Text><Input value={draft.pull_address_v6} maxLength={255} placeholder="edge6.example.com" onChange={event => setDraft(row => ({ ...row, pull_address_v6: event.target.value }))} /></div>
        {draft.enabled && supported.length === 0 && <Alert type="warning" showIcon title="尚未配置受支持的 DNS 服务商" description="请关闭此窗口，展开本页的“DNS 服务商凭据”，添加 Cloudflare、阿里云、腾讯云 DNSPod、DNSPod Token、GoDaddy 或 NameSilo。" />}
      </Space>
    </Modal>
  </Space>;
}
