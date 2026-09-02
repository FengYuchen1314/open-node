import { Alert, Button, Card, Input, Space, Spin, Table, Tag, Typography } from "../../ui";
import { ReloadOutlined } from "../../ui/icons";
import { useEffect, useRef, useState } from "react";
import type { AgentTelemetry, OnlineCollectionStatus } from "../../domain/inventory";
import { getLatestTelemetry } from "../../services/inventory";
import { useAdministratorSession } from "../hooks/useSession";

const messages: Record<OnlineCollectionStatus, string> = {
  ready: "采集正常",
  limited: "仅显示部分样本：有用户级别未开启统计，或已达到采集上限。人数和 IP 数不是完整总数。",
  not_configured: "尚未开启在线统计。请在 Xray 系统配置中启用 StatsService，并在用户所属策略级别中设置 statsUserOnline 为 true。",
  stopped: "Xray 已停止，暂时无法采集在线用户。",
  unsupported: "当前 Xray 不支持在线 IP 查询，请检查内核版本。",
  error: "本次在线 IP 采集失败，请检查 Xray API 和 Agent 状态，等待下一份报告。",
  unknown: "尚无在线 IP 报告。请确认 Agent 已升级到支持此功能的版本；旧版遥测不能用于判断在线人数。",
  stale: "在线 IP 数据已过期，已隐藏旧样本。请检查 Agent 连接，等待下一份报告。",
};

export default function OnlineUsersPanel({ serverId }: { serverId: string }) {
  const auth = useAdministratorSession();
  if (!auth.ready) return <Spin aria-label="正在读取管理员会话" />;
  if (!auth.session?.authenticated) return <Alert type="warning" title="请登录管理员账户后查看在线 IP。" />;
  if (!serverId) return <Alert type="info" title="请先选择服务器。" />;
  return <OnlineSample key={`${serverId}:${auth.session.username}:${auth.session.csrf_token}`} serverId={serverId} />;
}

function OnlineSample({ serverId }: { serverId: string }) {
  const [sample, setSample] = useState<AgentTelemetry | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [now, setNow] = useState(Date.now);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const reload = useRef<() => void>(() => {});
  useEffect(() => {
    let closed = false, pending = false;
    const refresh = async () => {
      if (closed || pending) return;
      pending = true; setBusy(true);
      try {
        const response = await getLatestTelemetry(serverId);
        if (closed) return;
        if (response.server_id !== serverId || (response.latest && response.latest.server_id !== serverId)) throw new Error("Wrong server");
        setSample(response.latest ?? null); setError(false); setNow(Date.now());
      } catch {
        if (!closed) { setSample(null); setError(true); }
      } finally {
        pending = false;
        if (!closed) setBusy(false);
      }
    };
    reload.current = () => { void refresh(); };
    void refresh();
    const polling = window.setInterval(() => { void refresh(); }, 30_000);
    const clock = window.setInterval(() => setNow(Date.now()), 1000);
    return () => { closed = true; window.clearInterval(polling); window.clearInterval(clock); };
  }, [serverId]);
  const collection = sample?.online_collection;
  const expired = !collection?.expires_at || !Number.isFinite(Date.parse(collection.expires_at)) || now >= Date.parse(collection.expires_at);
  const state = collection?.status ?? "unknown";
  const status = (state === "ready" || state === "limited") && expired ? "stale" : state;
  const visible = !error && (status === "ready" || status === "limited");
  const rows = visible ? Object.entries(sample?.online_users ?? {}).filter(([, ips]) => ips.length).map(([email, ips]) => ({ email, ips })) : [];
  const ipCount = new Set(rows.flatMap((row) => row.ips)).size;
  const filtered = rows.filter((row) => `${row.email} ${row.ips.join(" ")}`.toLowerCase().includes(search.toLowerCase()));
  return <Card size="small" title="在线用户与 IP" extra={<Button icon={<ReloadOutlined />} loading={busy} onClick={() => reload.current()} aria-label="刷新在线用户">刷新</Button>}>
    <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>展示 Xray 在线统计中的用户标识和 IP。当前配套内核按活跃连接统计；同一 IP 可能供多人共用，不能据此计算设备数量。页面每 30 秒读取最新报告，刷新不会触发远端扫描。</Typography.Paragraph>
      {error ? <Alert type="error" title="无法读取在线报告，已隐藏旧样本。请稍后刷新。" showIcon /> : <Alert type={status === "ready" ? "success" : status === "unknown" ? "info" : "warning"} title={messages[status]} showIcon />}
      {visible && <>
        <Space wrap><Tag>{rows.length} 个{status === "limited" ? "已采样用户" : "在线用户"}</Tag><Tag>{ipCount} 个不同 IP</Tag><Typography.Text type="secondary">报告接收时间：{collection?.received_at ? new Date(collection.received_at).toLocaleString("zh-CN") : "未知"}</Typography.Text></Space>
        <Input.Search aria-label="搜索在线用户或 IP" placeholder="搜索用户标识或 IP" value={search} allowClear onChange={(event) => { setSearch(event.target.value); setPage(1); }} />
        <Table rowKey="email" dataSource={filtered} size="small" scroll={{ x: 600 }} pagination={{ current: page, pageSize: 10, showSizeChanger: false, onChange: setPage, hideOnSinglePage: true }} locale={{ emptyText: status === "limited" ? "当前样本没有匹配记录，不能据此判断无人在线" : search ? "没有匹配记录" : "暂无在线用户" }} columns={[
          { title: "用户标识（Xray email）", dataIndex: "email", width: 260, render: (value: string) => <Typography.Text style={{ overflowWrap: "anywhere" }}>{value}</Typography.Text> },
          { title: "不同 IP 数", key: "count", width: 110, render: (_, row) => row.ips.length },
          { title: "在线 IP", key: "ips", render: (_, row) => <Space wrap>{row.ips.map((ip) => <Typography.Text code key={ip}>{ip}</Typography.Text>)}</Space> },
        ]} />
      </>}
    </Space>
  </Card>;
}
