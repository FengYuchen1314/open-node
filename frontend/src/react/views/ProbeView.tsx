import { LineChartOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Avatar, Button, Card, Col, ConfigProvider, Descriptions, Drawer, Empty, Flex, Form, Progress, Row, Segmented, Select, Space, Spin, Statistic, Table, Tag, Tooltip, Typography, theme } from "antd";
import type { TableColumnsType } from "antd";
import { lazy, Suspense, useCallback, useEffect, useState, type ReactNode } from "react";
import type { ProbeMetricPoint, ProbePayload, ProbePingSeries, ProbeServer, ProbeSeriesResponse, ProbeSettings, ProbeSystemSeries, ProbeTargetComparison } from "../../domain/probe";
import { browserProbeAccessToken } from "../../domain/probe-surface";
import { averageLatency, buildRegionOptions, buildSparkline, filterProbeServers, isExpired, isExpiring, latencyBucketLevels, percent, probeHealth, remainingDaysLabel, returnRouteBadges, serverRegionLabel, summarizeSevenDayTraffic, trafficHotspots, trafficTotal, trafficUsed, type ProbeStatusFilter } from "../../domain/probe-insights";
import { getPublicProbePayload, getPublicProbeSeries, getPublicProbeStreamUrl, getPublicProbeTargets, type ProbeRange } from "../../services/probe-public";
import { useAsyncScope } from "../hooks/useAsyncScope";

// The public bundle does not contain the authenticated settings/task module.
const AdministrationPanel = __OPEN_NODE_PUBLIC_PROBE__ ? null : lazy(() => import("../components/ProbeAdministrationPanel"));
export interface ProbeViewProps { publicOnly?: boolean }
const ranges: ProbeRange[] = ["1h", "6h", "24h"];
function bytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  if (value < 1024) return `${Math.round(value)} B`;
  const units = ["KB", "MB", "GB", "TB"]; let scaled = value / 1024, index = 0;
  while (scaled >= 1024 && index < units.length - 1) { scaled /= 1024; index += 1; }
  return `${scaled.toFixed(scaled >= 10 ? 0 : 1)} ${units[index]}`;
}
const speed = (value: number) => `${bytes(value)}/s`;
const milliseconds = (value: number) => Number.isFinite(value) && value >= 0 ? `${value.toFixed(0)} ms` : "No sample";
const loss = (value: number) => `${value.toFixed(value >= 10 ? 0 : 1)}% loss`;
const percentage = (value: number) => `${value.toFixed(value >= 10 ? 0 : 1)}%`;
const serverName = (server: ProbeServer, index: number) => server.name || `Node ${index + 1}`;
const tone = (value: string) => value === "info" ? "blue" : value;
const optionalLatency = (value: number | null | undefined) => value == null ? "No sample" : milliseconds(value);
function renewal(server: ProbeServer) {
  const cycles = { month: "Month", quarter: "Quarter", half_year: "Half year", year: "Year" };
  return [server.expires_at ? `expires ${server.expires_at.slice(0, 10)}` : "", server.renewal_price != null ? `${server.renewal_price} ${server.renewal_currency ?? ""}`.trim() : "", server.renewal_cycle ? cycles[server.renewal_cycle] : ""].filter(Boolean).join(" / ") || "No renewal";
}
function isProbePayload(value: unknown): value is ProbePayload {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const candidate = value as Partial<ProbePayload>;
  return candidate.license_required === false && typeof candidate.enabled === "boolean" && (candidate.servers === undefined || (Array.isArray(candidate.servers) && candidate.servers.every(server => server && typeof server === "object" && typeof server.online === "boolean")));
}
function safeImageUrl(value?: string) {
  if (!value) return undefined;
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) ? url.toString() : undefined; }
  catch { return undefined; }
}

export default function ProbeView({ publicOnly: requestedPublic = false }: ProbeViewProps) {
  const publicOnly = __OPEN_NODE_PUBLIC_PROBE__ || requestedPublic;
  const payloadScope = useAsyncScope();
  const targetScope = useAsyncScope();
  const [payload, setPayload] = useState<ProbePayload | null>(null);
  const [settingsOverride, setSettingsOverride] = useState<ProbeSettings | null>(null);
  const [accessToken, setAccessToken] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [streamActive, setStreamActive] = useState(false);
  const [statusFilter, setStatusFilter] = useState<ProbeStatusFilter>("all");
  const [regionFilter, setRegionFilter] = useState("all");
  const [selected, setSelected] = useState<{ index: number; name: string } | null>(null);
  const [comparisonRange, setComparisonRange] = useState<ProbeRange>("1h");
  const [comparisons, setComparisons] = useState<ProbeTargetComparison[]>([]);
  const [comparisonLoading, setComparisonLoading] = useState(false);
  const [comparisonError, setComparisonError] = useState("");
  const [comparisonGenerated, setComparisonGenerated] = useState("");
  const [systemDark, setSystemDark] = useState(false);
  const token = browserProbeAccessToken(publicOnly, accessToken);
  const settings: ProbeSettings = { enabled: true, ...payload, ...settingsOverride };
  const interval = Math.max(1, Math.min(60, settings.refresh_interval_sec || 5)) * 1000;
  const servers = payload?.servers ?? [];
  const regions = buildRegionOptions(servers);
  const visibleServers = filterProbeServers(servers, statusFilter, regionFilter);
  const showSystem = settings.show_resource_heatmap !== false;
  const showTraffic = settings.show_traffic_quota !== false;
  const showRenewal = settings.show_renewal_timeline === true;
  const showHealth = settings.show_health_score !== false;
  const showRoutes = settings.show_return_route === true;
  const online = servers.filter(server => server.online).length;
  const expired = servers.filter(server => isExpired(server)).length;
  const expiring = servers.filter(server => isExpiring(server)).length;
  const daily = summarizeSevenDayTraffic(servers);
  const hotspots = trafficHotspots(servers);
  const selectedServer = selected ? servers[selected.index] ?? null : null;

  const refreshPayload = useCallback(async () => {
    const request = payloadScope.begin(); setLoading(true); setError("");
    try {
      const result = await getPublicProbePayload(fetch, token);
      if (!isProbePayload(result)) throw new Error("Invalid public probe snapshot.");
      if (payloadScope.isCurrent(request)) setPayload(result);
    } catch (cause) {
      if (payloadScope.isCurrent(request)) setError(cause instanceof Error ? cause.message : "Probe request failed.");
    } finally { if (payloadScope.isCurrent(request)) setLoading(false); }
  }, [payloadScope, token]);
  const refreshTargets = useCallback(async () => {
    const request = targetScope.begin(); setComparisonLoading(true); setComparisonError("");
    try {
      const result = await getPublicProbeTargets(comparisonRange, fetch, token);
      if (targetScope.isCurrent(request)) {
        setComparisons(result.targets); setComparisonGenerated(result.generated_at ? new Date(result.generated_at * 1000).toLocaleString() : "");
      }
    } catch (cause) {
      if (targetScope.isCurrent(request)) { setComparisons([]); setComparisonGenerated(""); setComparisonError(cause instanceof Error ? cause.message : "Probe target comparison failed."); }
    } finally { if (targetScope.isCurrent(request)) setComparisonLoading(false); }
  }, [targetScope, comparisonRange, token]);
  useEffect(() => { void refreshPayload(); }, [refreshPayload]);
  useEffect(() => { void refreshTargets(); }, [refreshTargets]);
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      await Promise.allSettled([refreshPayload(), refreshTargets()]);
      if (!disposed) timer = setTimeout(() => void tick(), interval);
    };
    timer = setTimeout(() => void tick(), interval);
    return () => { disposed = true; clearTimeout(timer); };
  }, [interval, refreshPayload, refreshTargets]);
  const allowStream = publicOnly || !settings.require_access_token;
  useEffect(() => {
    if (!allowStream || typeof WebSocket === "undefined") { setStreamActive(false); return; }
    let disposed = false;
    let socket: WebSocket | null = null;
    let reconnect: ReturnType<typeof setTimeout> | undefined;
    const open = () => {
      if (disposed) return;
      try {
        socket = new WebSocket(getPublicProbeStreamUrl());
        socket.onopen = () => { if (!disposed) setStreamActive(true); };
        socket.onmessage = event => {
          if (disposed) return;
          try {
            const snapshot: unknown = JSON.parse(String(event.data));
            if (isProbePayload(snapshot)) { payloadScope.invalidate(); setPayload(snapshot); setError(""); setLoading(false); }
          } catch { /* A later validated snapshot replaces malformed stream frames. */ }
        };
        socket.onerror = () => { if (!disposed) setStreamActive(false); };
        socket.onclose = () => { socket = null; if (!disposed) { setStreamActive(false); reconnect = setTimeout(open, 2000); } };
      } catch { if (!disposed) { setStreamActive(false); reconnect = setTimeout(open, 2000); } }
    };
    open();
    return () => {
      disposed = true; clearTimeout(reconnect);
      if (socket) { socket.onopen = null; socket.onmessage = null; socket.onerror = null; socket.onclose = null; socket.close(); }
    };
  }, [allowStream, publicOnly, payloadScope]);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => setSystemDark(media.matches); update(); media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (regionFilter !== "all" && !regions.some(region => region.code === regionFilter)) setRegionFilter("all");
    if (selected && (!selectedServer || serverName(selectedServer, selected.index) !== selected.name)) setSelected(null);
  }, [payload, regionFilter, selected, selectedServer]);
  const applySettings = useCallback((value: ProbeSettings) => { setSettingsOverride(value); }, []);
  const applyAccessToken = useCallback((value: string, nextSettings: ProbeSettings) => { setAccessToken(value); setSettingsOverride(nextSettings); }, []);
  const refreshAll = useCallback(() => { void refreshPayload(); void refreshTargets(); }, [refreshPayload, refreshTargets]);
  const openDetail = (server: ProbeServer) => { const index = servers.indexOf(server); if (index >= 0) setSelected({ index, name: serverName(server, index) }); };
  const columns: TableColumnsType<ProbeServer> = [
    { title: "Name", key: "name", width: 220, render: (_, server) => <>
      <Flex gap="small" align="center"><Typography.Text strong>{serverName(server, servers.indexOf(server))}</Typography.Text><Button icon={<LineChartOutlined aria-hidden />} size="small" aria-label={`Open probe details for ${serverName(server, servers.indexOf(server))}`} title="Open probe details" onClick={() => openDetail(server)} /></Flex>
      <div><Typography.Text type="secondary">{serverRegionLabel(server)}</Typography.Text></div><div><Typography.Text type="secondary">{server.provider_name ?? "No provider"}</Typography.Text></div>
    </> },
    { title: "Status", key: "status", width: 160, render: (_, server) => { const health = probeHealth(server); return <Space orientation="vertical" size={4}>
      <Tag color={server.online ? "success" : "error"}>{server.online ? "Online" : "Offline"}</Tag>
      <Typography.Text type="secondary">{server.telecom_paid_peer == null ? "Peer unknown" : server.telecom_paid_peer ? "Paid peer" : "Standard peer"}</Typography.Text>
      {showHealth && <Tooltip title={health.issues.length ? health.issues.join(", ") : "No public health issues"}><Tag color={tone(health.tone)}>{health.score} {health.label}</Tag></Tooltip>}
    </Space>; } },
    ...(showSystem ? [{ title: "System", key: "system", width: 220, render: (_: unknown, server: ProbeServer) => <>
      <Typography.Text>{server.cpu_pct != null ? `${server.cpu_pct.toFixed(1)}% CPU` : "CPU n/a"}, {server.mem_used != null && server.mem_total ? `${((server.mem_used / server.mem_total) * 100).toFixed(0)}% mem` : "mem n/a"}</Typography.Text>
      <div><Typography.Text type="secondary">{server.os || "Unknown OS"} / {server.loadavg || "No loadavg"}</Typography.Text></div>
    </> }] : []),
    { title: "Latency", key: "latency", width: 185, render: (_, server) => <>
      <Typography.Text>{!(server.ping?.length) ? "No probe" : averageLatency(server) == null ? "probe failed" : `${averageLatency(server)} ms avg`}</Typography.Text>
      <div className="probe-latency-bars">{latencyBucketLevels(server).map((bucket, index) => <span key={index} className={`is-${bucket.level}`} title={bucket.level === "none" ? "No sample" : `${bucket.ms.toFixed(0)} ms, ${bucket.loss.toFixed(1)}% loss`} />)}</div>
    </> },
    ...(showTraffic ? [{ title: "Traffic", key: "traffic", width: 220, render: (_: unknown, server: ProbeServer) => <>
      <Typography.Text>{!trafficUsed(server) && !server.traffic_limit && server.traffic_used === undefined ? "No traffic" : `${bytes(trafficUsed(server))}${server.traffic_limit ? ` / ${bytes(server.traffic_limit)}` : ""}`}</Typography.Text>
      {Boolean(server.traffic_limit) && <Progress size="small" showInfo={false} percent={percent(trafficUsed(server), server.traffic_limit)} />}
      {settings.show_traffic_7d === true && <div><Typography.Text type="secondary">{server.daily_traffic?.length ? `7d ${bytes(trafficTotal(server.daily_traffic))}` : "No 7d traffic"}</Typography.Text></div>}
      {!showRenewal && <div><Typography.Text type="secondary">{renewal(server)}</Typography.Text></div>}
    </> }] : []),
    ...(showRoutes ? [{ title: "Routes", key: "routes", width: 220, render: (_: unknown, server: ProbeServer) => <RouteBadges server={server} /> }] : []),
    ...(showRenewal ? [{ title: "Renewal", key: "renewal", width: 220, render: (_: unknown, server: ProbeServer) => <>
      <Tag color={isExpired(server) ? "error" : isExpiring(server) ? "warning" : "default"}>{remainingDaysLabel(server.expires_at) || "No expiry"}</Tag>
      <div>{renewal(server)}</div><Typography.Text type="secondary">CNY {server.renewal_price_cny ?? "n/a"}</Typography.Text>
    </> }] : []),
    { title: "Up", key: "up", width: 120, render: (_, server) => speed(server.upload_speed ?? 0) },
    { title: "Down", key: "down", width: 120, render: (_, server) => speed(server.download_speed ?? 0) },
  ];
  const dark = settings.appearance?.color_mode === "dark" || (settings.appearance?.color_mode === "system" && systemDark);
  const endpointNote = streamActive ? "Live stream connected" : !publicOnly && settings.require_access_token ? "Worker token required" : payload?.enabled ? "Probe endpoint enabled" : "Probe endpoint disabled";
  return <ConfigProvider theme={{ algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm }}><ProbeSurface publicOnly={publicOnly} dark={dark}>
    <Flex justify="space-between" align="start" gap="middle"><Flex align="start" gap="small" style={{ flex: 1, minWidth: 0 }}><Avatar src={safeImageUrl(settings.logo)} shape="square" size={48} style={{ flexShrink: 0 }}>ON</Avatar><div style={{ minWidth: 0, overflowWrap: "anywhere" }}><Typography.Text type="secondary">Public probe</Typography.Text><Typography.Title level={1} style={{ margin: "8px 0" }}>{settings.title ?? "Open Node Probe"}</Typography.Title><Typography.Paragraph type="secondary">{settings.description ?? "MMWX probe-compatible node status without license gates."}</Typography.Paragraph></div></Flex><Button style={{ flexShrink: 0 }} icon={<ReloadOutlined aria-hidden />} aria-label="Refresh probe status" loading={loading} onClick={refreshAll} /></Flex>
    {error && <Alert type="error" showIcon title={!publicOnly && settings.require_access_token && !token ? "Worker token required." : error} />}
    <section aria-label="Probe status"><Row gutter={[16, 16]}>
      <Col xs={24} sm={12} lg={8}><Card><Statistic title="Public Nodes" value={servers.length} /><Typography.Text type="secondary">{endpointNote}</Typography.Text></Card></Col>
      <Col xs={24} sm={12} lg={8}><Card><Statistic title="Online" value={online} /><Typography.Text type="secondary">{servers.length - online} offline</Typography.Text></Card></Col>
      <Col xs={24} sm={24} lg={8}><Card><Statistic title="Throughput" value={`${speed(servers.reduce((total, server) => total + (server.upload_speed ?? 0), 0))} / ${speed(servers.reduce((total, server) => total + (server.download_speed ?? 0), 0))}`} /><Typography.Text type="secondary">Upload / download</Typography.Text></Card></Col>
    </Row></section>
    {servers.length > 0 && <section aria-label="Probe insights"><Row gutter={[16, 16]}>
      {settings.show_globe && regions.length > 0 && <Col xs={24} lg={8}><Card title="Regions" extra={`${regions.length} public groups`}><Space orientation="vertical" style={{ width: "100%" }}>{regions.map(region => <div key={region.code}><Flex justify="space-between" align="center" gap="small"><Button type={regionFilter === region.code ? "primary" : "link"} onClick={() => setRegionFilter(region.code)}>{region.label}</Button><Typography.Text>{region.online}/{region.total}</Typography.Text></Flex><Progress percent={(region.total / servers.length) * 100} showInfo={false} size="small" /></div>)}</Space></Card></Col>}
      {settings.show_daily_trend && daily.length > 0 && <Col xs={24} lg={8}><Card title="Daily traffic" extra={`${bytes(daily.at(-1)?.total ?? 0)} today`}><DailyTrafficChart days={daily} /></Card></Col>}
      {settings.show_traffic_hotspots && hotspots.length > 0 && <Col xs={24} lg={8}><Card title="Traffic hotspots" extra="Top live throughput"><Space orientation="vertical" style={{ width: "100%" }}>{hotspots.map(row => <div key={row.index}><Flex gap="small" justify="space-between"><Typography.Text>{row.name}</Typography.Text><Typography.Text>{speed(row.speed)}</Typography.Text></Flex><Progress percent={row.share} showInfo={false} size="small" /></div>)}</Space></Card></Col>}
    </Row></section>}
    <Card role="region" aria-label="Target comparison" className="probe-target-compare" title="Target comparison" extra={<Space wrap><Segmented aria-label="Target comparison range" value={comparisonRange} options={ranges} onChange={setComparisonRange} /><Button icon={<ReloadOutlined aria-hidden />} aria-label="Refresh target comparison" loading={comparisonLoading} onClick={() => void refreshTargets()} /></Space>}>
      <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
        {comparisonError && <Alert type="error" showIcon title={comparisonError} />}
        {comparisonLoading && !comparisons.length && <Spin aria-label="Loading target comparison" />}
        {!comparisonLoading && !comparisonError && !comparisons.length && <Empty description="No target comparison samples." image={Empty.PRESENTED_IMAGE_SIMPLE} />}
        {[...comparisons].sort((left, right) => right.average_loss_pct - left.average_loss_pct || (right.average_ms ?? -1) - (left.average_ms ?? -1) || left.label.localeCompare(right.label)).map(target => <Card key={target.key} type="inner" title={target.label} extra={`${target.healthy_count}/${target.server_count} healthy / ${loss(target.average_loss_pct)}`}>
          <Sparkline values={comparisonValues(target)} height={64} label={`${target.label} average latency`} />
          <Descriptions size="small" column={{ xs: 1, sm: 3 }} items={[
            { key: "average", label: "average", children: optionalLatency(target.average_ms) }, { key: "best", label: "best", children: optionalLatency(target.best_ms) }, { key: "worst", label: "worst", children: optionalLatency(target.worst_ms) },
          ]} />
          <Space wrap>{target.servers.slice(0, 5).map(server => <Tag key={server.server_index} color={server.current_ms >= 0 ? "success" : "error"}>{server.server_name ?? `Node ${server.server_index + 1}`} {server.current_ms >= 0 ? milliseconds(server.current_ms) : "failed"}</Tag>)}</Space>
        </Card>)}
        {comparisonGenerated && <Typography.Text type="secondary">Updated {comparisonGenerated}</Typography.Text>}
      </Space>
    </Card>
    {!publicOnly && AdministrationPanel && <Suspense fallback={<Spin aria-label="Loading probe administration" />}><AdministrationPanel accessToken={accessToken} onSettings={applySettings} onAccessToken={applyAccessToken} onRefresh={refreshAll} /></Suspense>}
    <Card title="Probe nodes" extra={<Typography.Text type="secondary">Public read-only view</Typography.Text>}>
      {servers.length > 0 && <Flex wrap gap="middle" align="center" className="form-alert">
        <Segmented className="probe-status-toggle" aria-label="Node status filter" value={statusFilter} onChange={value => setStatusFilter(value as ProbeStatusFilter)} options={[
          { label: `All ${servers.length}`, value: "all" }, { label: `Online ${online}`, value: "online" }, { label: `Offline ${servers.length - online}`, value: "offline" }, { label: `Renewal ${expired + expiring}`, value: "renewal" }, { label: `Expired ${expired}`, value: "expired" },
        ]} />
        <Select aria-label="Region filter" className="probe-region-select" style={{ minWidth: 190 }} value={regionFilter} options={[{ label: "All regions", value: "all" }, ...regions.map(region => ({ label: `${region.label} (${region.online}/${region.total})`, value: region.code }))]} onChange={setRegionFilter} />
        <Typography.Text type="secondary">{visibleServers.length} / {servers.length} nodes</Typography.Text>
      </Flex>}
      <Table className="server-table" rowKey={server => `${serverName(server, servers.indexOf(server))}-${servers.indexOf(server)}`} dataSource={visibleServers} columns={columns} loading={loading && !servers.length} pagination={false} scroll={{ x: "max-content" }} locale={{ emptyText: servers.length ? "No nodes match the current filters." : "No public probe nodes yet." }} />
    </Card>
    <Drawer open={selected !== null} title={<Typography.Title level={3} style={{ margin: 0 }}>{selected?.name ?? "Node"}</Typography.Title>} className="probe-detail-drawer" size={560} onClose={() => setSelected(null)} destroyOnHidden>
      {selected && selectedServer && <ProbeDetails key={selected.index} server={selectedServer} serverIndex={selected.index} showRoutes={showRoutes} accessToken={token} />}
    </Drawer>
  </ProbeSurface></ConfigProvider>;
}

function ProbeSurface({ publicOnly, dark, children }: { publicOnly: boolean; dark: boolean; children: ReactNode }) {
  const { token } = theme.useToken();
  return <div className={`page-shell probe-page${publicOnly ? " public-probe-surface" : ""}`} style={{ background: token.colorBgLayout, color: token.colorText, colorScheme: dark ? "dark" : "light" }}>{children}</div>;
}

function Sparkline({ values, height = 96, label, color = "#1677ff" }: { values: (number | null | undefined)[]; height?: number; label: string; color?: string }) {
  const chart = buildSparkline(values, 320, height, 6);
  return <svg className="probe-sparkline" role="img" aria-label={label} preserveAspectRatio="none" viewBox={`0 0 320 ${height}`} style={{ height }}>
    {!chart.empty && <><polygon points={chart.areaPoints} fill={color} fillOpacity={0.12} /><polyline points={chart.points} stroke={color} strokeWidth={2} fill="none" /></>}
  </svg>;
}
function TrendCard({ label, values, format, color }: { label: string; values: (number | null | undefined)[]; format: (value: number) => string; color: string }) {
  const chart = buildSparkline(values);
  return <Card size="small" title={label} extra={chart.latest === null ? "No sample" : format(chart.latest)}><Sparkline label={`${label} trend`} values={values} color={color} /><Typography.Text type="secondary">{chart.empty ? "No samples" : `${format(chart.min)} - ${format(chart.max)}`}</Typography.Text></Card>;
}
function RouteBadges({ server }: { server: ProbeServer }) {
  return <Space wrap>{returnRouteBadges(server).map(route => <Tooltip key={route.carrier} title={`${route.carrierLabel} ${route.routeType}${route.region ? ` - ${route.region}` : ""}${route.testedAt ? `, tested ${route.testedAt}` : ""}`}><Tag color={route.missing ? "default" : route.premium ? "success" : "blue"}>{route.carrierLabel} {route.routeType}</Tag></Tooltip>)}</Space>;
}
function DailyTrafficChart({ days }: { days: ReturnType<typeof summarizeSevenDayTraffic> }) {
  const maximum = Math.max(1, ...days.map(day => day.total));
  return <><svg className="probe-daily-chart" viewBox="0 0 350 160" role="img" aria-label="Daily upload and download traffic">{days.map((day, index) => {
    const downHeight = (day.downlink / maximum) * 110, upHeight = (day.uplink / maximum) * 110;
    return <g key={day.date}><title>{day.date}: {bytes(day.total)} total</title><rect x={index * (350 / days.length) + 8} y={125 - downHeight} width={18} height={downHeight} fill="#1677ff" /><rect x={index * (350 / days.length) + 28} y={125 - upHeight} width={18} height={upHeight} fill="#fa8c16" /><text x={index * (350 / days.length) + 24} y={148} textAnchor="middle" fill="currentColor" fontSize={10}>{day.date.slice(5)}</text></g>;
  })}</svg><Space><Tag color="blue">Download</Tag><Tag color="orange">Upload</Tag></Space></>;
}
function comparisonValues(target: ProbeTargetComparison) {
  return Array.from({ length: Math.max(0, ...target.servers.map(server => server.buckets.length)) }, (_, index) => {
    const values = target.servers.map(server => server.buckets[index]?.ms).filter((value): value is number => typeof value === "number" && value >= 0);
    return values.length ? values.reduce((total, value) => total + value, 0) / values.length : null;
  });
}
function ProbeDetails({ server, serverIndex, showRoutes, accessToken }: { server: ProbeServer; serverIndex: number; showRoutes: boolean; accessToken?: string }) {
  const scope = useAsyncScope();
  const [range, setRange] = useState<ProbeRange>("1h");
  const [metric, setMetric] = useState<"ping" | "system">("ping");
  const [series, setSeries] = useState<ProbeSeriesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const request = scope.begin(); setLoading(true); setError(""); setSeries(null);
    void getPublicProbeSeries(serverIndex, { range, metric, all: metric === "ping" }, fetch, accessToken).then(result => {
      if (scope.isCurrent(request)) setSeries(result);
    }).catch(cause => { if (scope.isCurrent(request)) setError(cause instanceof Error ? cause.message : "Probe series request failed."); })
      .finally(() => { if (scope.isCurrent(request)) setLoading(false); });
    return () => scope.invalidate();
  }, [scope, serverIndex, range, metric, accessToken]);
  const ping = series?.series && "buckets" in series.series ? series.series as ProbePingSeries : null;
  const system = series?.series && "cpu_pct" in series.series ? series.series as ProbeSystemSeries : null;
  const values = (points?: ProbeMetricPoint[]) => points?.map(point => point.value) ?? [];
  const totals = new Map((system?.mem_total ?? []).map(point => [point.t, point.value]));
  return <Space className="probe-trend-grid" orientation="vertical" size="large" style={{ width: "100%" }}>
    <div><Typography.Text type="secondary">Probe details</Typography.Text><Typography.Paragraph>{serverRegionLabel(server)}</Typography.Paragraph></div>
    <Flex gap="small" wrap><Segmented aria-label="Probe metric" value={metric} onChange={setMetric} options={[{ label: "Ping", value: "ping" }, { label: "System", value: "system" }]} /><Segmented aria-label="Probe history range" value={range} options={ranges} onChange={setRange} /></Flex>
    {error && <Alert type="error" showIcon title={error} />}{loading && <Spin aria-label="Loading probe history" />}
    {!loading && metric === "ping" && <>
      {ping && <><TrendCard label="Average latency" values={ping.buckets.map(bucket => bucket.ms)} format={milliseconds} color="#1677ff" /><TrendCard label="Packet loss" values={ping.buckets.map(bucket => bucket.loss)} format={loss} color="#ff4d4f" /></>}
      <Typography.Title level={5}>Targets</Typography.Title>
      {!series?.all_series?.length && <Empty description="No target series." image={Empty.PRESENTED_IMAGE_SIMPLE} />}
      {series?.all_series?.map(target => <Card size="small" key={target.key ?? target.label} title={target.label} extra={`${target.current_ms >= 0 ? milliseconds(target.current_ms) : "failed"} / ${loss(target.loss_pct)}`}><Sparkline values={target.buckets.map(bucket => bucket.ms)} label={`${target.label} latency trend`} height={54} /></Card>)}
    </>}
    {!loading && metric === "system" && (system ? <>
      <TrendCard label="CPU" values={values(system.cpu_pct)} format={percentage} color="#52c41a" />
      <TrendCard label="Memory" values={system.mem_used.map(point => percent(point.value, totals.get(point.t)))} format={percentage} color="#722ed1" />
      <TrendCard label="Upload" values={values(system.upload_speed)} format={speed} color="#fa8c16" />
      <TrendCard label="Download" values={values(system.download_speed)} format={speed} color="#1677ff" />
    </> : <Empty description="No system samples." image={Empty.PRESENTED_IMAGE_SIMPLE} />)}
    {showRoutes && <><Typography.Title level={5}>Return routes</Typography.Title><RouteBadges server={server} /></>}
    {series?.generated_at && <Typography.Text type="secondary">Updated {new Date(series.generated_at * 1000).toLocaleString()}</Typography.Text>}
  </Space>;
}
