import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from "../../ui/icons";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Flex,
  Form,
  Input,
  Modal,
  Radio,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from "../../ui";
import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";

import { zhMessage, zhStatus } from "../../i18n/zh-CN";
import type {
  AgentCommand,
  AgentOperationKind,
  AgentOperationPayload,
  AgentOutboundTLSPinProbeOperationRequest,
  AgentRoutingManageOperationRequest,
  ServerSummary,
} from "../../domain/inventory";
import type {
  ServerEgressPreviewRequest,
  ServerEgressRemovePreviewRequest,
  ServerEgressRoutingSelector,
  ServerEgressTlsProbeDescriptor,
} from "../../domain/server-egress";
import { listServerCommands, listServers, queueAgentOperation } from "../../services/inventory";
import {
  applyServerEgress,
  getServerEgressCatalog,
  previewServerEgress,
  previewServerEgressRemoval,
  removeServerEgress,
} from "../../services/server-egress";
import WarpStatus from "./WarpStatus";

type JsonObject = Record<string, unknown>;
type EditorMode = "add" | "update";
type EgressSection = "visual" | "advanced";
type ManagedRoutingAction = "keep" | "set" | "remove";

interface PendingOperation {
  kind: AgentOperationKind;
  payload?: AgentOperationPayload;
}

export interface ServerEgressPanelProps {
  advancedContent?: ReactNode;
}

const POLL_INTERVAL_MS = 500;
const POLL_ATTEMPTS = 120;
const objectKeys = new WeakMap<object, string>();
let objectKeySequence = 0;

function objectKey(value: JsonObject) {
  const existing = objectKeys.get(value);
  if (existing) return existing;
  const key = `egress-object-${++objectKeySequence}`;
  objectKeys.set(value, key);
  return key;
}

function record(value: unknown): JsonObject | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : null;
}

function requiredObject(text: string, label: string): JsonObject {
  let value: unknown;
  try { value = JSON.parse(text); } catch { throw new Error(`${label}必须是有效的 JSON 对象。`); }
  const object = record(value);
  if (!object) throw new Error(`${label}必须是 JSON 对象。`);
  return object;
}

function outboundIdentity(outbound: JsonObject) {
  const tag = typeof outbound.tag === "string" ? outbound.tag.trim() : "";
  const protocol = typeof outbound.protocol === "string" ? outbound.protocol.trim() : "";
  if (!tag || !protocol) throw new Error("出站 JSON 必须包含非空的 tag 和 protocol。");
  return { tag, protocol };
}

const TLS_PIN_PROTOCOLS = new Set([
  "vless", "vmess", "trojan", "shadowsocks", "socks", "http", "anytls",
]);

function normalizedTlsPin(value: unknown) {
  if (typeof value !== "string") throw new Error("TLS 出站必须填写证书 SHA-256 Pin。");
  const pins = [...new Set(value.split(",").map(item => item.trim().replaceAll(":", "").toLowerCase()).filter(Boolean))];
  if (!pins.length || pins.length > 8 || pins.some(pin => !/^[a-f0-9]{64}$/.test(pin))) {
    throw new Error("证书 SHA-256 Pin 必须是 64 位十六进制；多张证书最多 8 项，用逗号分隔。");
  }
  return pins.join(",");
}

function tlsStream(outbound: JsonObject) {
  const stream = record(outbound.streamSettings);
  return stream && String(stream.security ?? "").trim().toLowerCase() === "tls" ? stream : null;
}

function outboundTlsPin(outbound: JsonObject) {
  const settings = record(tlsStream(outbound)?.tlsSettings);
  return typeof settings?.pinnedPeerCertSha256 === "string" ? settings.pinnedPeerCertSha256 : "";
}

function withTlsPin(outbound: JsonObject, manualPin: string) {
  const stream = tlsStream(outbound);
  if (!stream) {
    if (manualPin.trim()) throw new Error("当前出站未启用 TLS，不能设置证书 Pin。");
    return outbound;
  }
  const protocol = String(outbound.protocol ?? "").trim().toLowerCase();
  if (!TLS_PIN_PROTOCOLS.has(protocol)) throw new Error("当前出站协议不支持 TLS Pin 管理。");
  const tlsSettings = record(stream.tlsSettings);
  if (!tlsSettings) throw new Error("TLS 出站必须包含 streamSettings.tlsSettings。");
  if (Object.prototype.hasOwnProperty.call(tlsSettings, "allowInsecure")) {
    throw new Error("请删除 tlsSettings.allowInsecure；本项目只接受证书 SHA-256 Pin。");
  }
  const network = String(stream.network ?? "tcp").trim().toLowerCase();
  const rawPin = manualPin.trim() || outboundTlsPin(outbound);
  if ((network === "hysteria" || Object.prototype.hasOwnProperty.call(stream, "hysteriaSettings")) && !rawPin) return outbound;
  const pin = normalizedTlsPin(rawPin);
  return {
    ...outbound,
    streamSettings: { ...stream, tlsSettings: { ...tlsSettings, pinnedPeerCertSha256: pin } },
  };
}

function tlsPinProbeRequest(outbound: JsonObject): AgentOutboundTLSPinProbeOperationRequest {
  const stream = tlsStream(outbound);
  if (!stream) throw new Error("只有 security=tls 的出站才能探测证书 Pin。");
  const network = String(stream.network ?? "tcp").trim().toLowerCase();
  if (network === "hysteria" || Object.prototype.hasOwnProperty.call(stream, "hysteriaSettings")) {
    throw new Error("Hysteria2 使用 QUIC/UDP，不能通过 TCP 自动探测；请使用受信任证书域名验证。");
  }
  const protocol = String(outbound.protocol ?? "").trim().toLowerCase();
  if (!TLS_PIN_PROTOCOLS.has(protocol)) throw new Error("当前出站协议不支持自动探测 TLS Pin。");
  const settings = record(outbound.settings);
  if (!settings) throw new Error("出站 settings 必须是 JSON 对象。");
  let target: JsonObject;
  if (protocol === "anytls") {
    target = settings;
  } else {
    const targets = Array.isArray(settings.vnext) ? settings.vnext : Array.isArray(settings.servers) ? settings.servers : [];
    if (targets.length !== 1 || !record(targets[0])) throw new Error("自动探测要求 settings.vnext 或 settings.servers 恰好包含一个目标。");
    target = record(targets[0])!;
  }
  const address = typeof target.address === "string" ? target.address.trim() : "";
  const port = typeof target.port === "number" ? target.port : Number.NaN;
  if (!address || !Number.isInteger(port) || port < 1 || port > 65535) throw new Error("目标 address 或 port 无效。");
  const tlsSettings = record(stream.tlsSettings);
  if (!tlsSettings) throw new Error("TLS 出站必须包含 streamSettings.tlsSettings。");
  if (Object.prototype.hasOwnProperty.call(tlsSettings, "allowInsecure")) {
    throw new Error("请先删除 tlsSettings.allowInsecure；本项目不会为该配置执行探测。");
  }
  const serverName = typeof tlsSettings.serverName === "string" ? tlsSettings.serverName.trim() : "";
  const alpn = tlsSettings.alpn === undefined ? [] : tlsSettings.alpn;
  if (!Array.isArray(alpn) || alpn.some(item => typeof item !== "string")) throw new Error("tlsSettings.alpn 必须是字符串数组。");
  return {
    protocol: protocol as AgentOutboundTLSPinProbeOperationRequest["protocol"],
    address,
    port,
    ...(serverName ? { server_name: serverName } : {}),
    alpn: alpn as string[],
    timeout_ms: 8_000,
    command_timeout_ms: 20_000,
  };
}

function parsedProbePin(body: unknown) {
  const value = record(body);
  if (!value || value.success !== true || Object.keys(value).some(key => !["success", "pinned_peer_cert_sha256"].includes(key))) {
    throw new Error("Agent 返回了无效的证书探测结果。");
  }
  return normalizedTlsPin(value.pinned_peer_cert_sha256);
}

function isManagedEgressTag(tag: string) {
  return tag.startsWith("managed-egress:");
}

function parseOutbounds(body: unknown): JsonObject[] {
  const value = record(body);
  if (!value || !Array.isArray(value.outbounds) || value.outbounds.some(item => !record(item))) {
    throw new Error("Agent 返回了无效的出站列表。");
  }
  return value.outbounds as JsonObject[];
}

interface RoutingSnapshot {
  routing: JsonObject;
  hasObservatory: boolean;
  observatory?: unknown;
  hasBurstObservatory: boolean;
  burstObservatory?: unknown;
}

function parseRouting(body: unknown): RoutingSnapshot {
  const value = record(body);
  if (!value || !("routing" in value)) throw new Error("Agent 返回了无效的路由配置。");
  const routing = value.routing === null ? { rules: [] } : record(value.routing);
  if (!routing || (routing.rules !== undefined && (!Array.isArray(routing.rules) || routing.rules.some(item => !record(item))))) {
    throw new Error("Agent 返回了无效的路由配置。");
  }
  return {
    routing,
    hasObservatory: Object.prototype.hasOwnProperty.call(value, "observatory"),
    observatory: value.observatory,
    hasBurstObservatory: Object.prototype.hasOwnProperty.call(value, "burstObservatory"),
    burstObservatory: value.burstObservatory,
  };
}

function routingDocument(snapshot: RoutingSnapshot) {
  const value: JsonObject = { routing: snapshot.routing };
  if (snapshot.hasObservatory) value.observatory = snapshot.observatory;
  if (snapshot.hasBurstObservatory) value.burstObservatory = snapshot.burstObservatory;
  return value;
}

function parseWarp(body: unknown): JsonObject {
  const value = record(body);
  if (!value || typeof value.installed !== "boolean") throw new Error("Agent 返回了无效的 WARP 状态。");
  return value;
}

function commandFailure(command: AgentCommand) {
  return command.result_error || `Agent 命令 ${command.path} 执行失败。`;
}

function wait(ms: number) {
  return new Promise(resolve => window.setTimeout(resolve, ms));
}

function routingRules(routing: JsonObject) {
  return Array.isArray(routing.rules) ? routing.rules.filter((item): item is JsonObject => Boolean(record(item))) : [];
}

function routeTarget(rule: JsonObject) {
  if (typeof rule.outboundTag === "string") return rule.outboundTag;
  if (typeof rule.balancerTag === "string") return `负载均衡：${rule.balancerTag}`;
  return "未指定";
}

function routeConditions(rule: JsonObject) {
  const ignored = new Set(["outboundTag", "balancerTag", "marktag"]);
  const values = Object.entries(rule).filter(([key]) => !ignored.has(key)).map(([key, value]) => {
    if (Array.isArray(value)) return `${key}：${value.length} 项`;
    if (value !== null && typeof value === "object") return `${key}：对象`;
    return `${key}：${String(value)}`;
  });
  return values.length ? values.join("；") : "无额外条件";
}

type SelectorDraft = {
  domains: string;
  ips: string;
  inbound_tags: string;
  users: string;
  protocols: string;
  port: string;
  network: "" | "tcp" | "udp" | "tcp,udp";
};

const emptySelector = (): SelectorDraft => ({ domains: "", ips: "", inbound_tags: "", users: "", protocols: "", port: "", network: "" });
const splitSelector = (value: string) => [...new Set(value.split(/[\n,]+/).map(item => item.trim()).filter(Boolean))];

const unavailableReasons: Record<string, string> = {
  "Only physical managed nodes can be used as an egress": "仅物理受管节点可作为出口",
  "The managed node is disabled or being removed": "节点已停用或正在删除",
  "The managed node points to a missing server": "节点所属服务器不存在",
  "Source and target must be different servers": "源服务器与出口节点服务器不能相同",
  "Federated servers cannot participate in managed egress changes": "共享服务器不能参与受管出口变更",
  "The managed node lacks an authenticated inbound or proxy config": "节点缺少可认证入站或代理配置",
  "The managed node protocol cannot be converted to an Xray outbound": "该节点协议暂不能转换为 Xray 出站",
  "The source server has an unreviewed Xray configuration recovery": "源服务器有尚未处理的 Xray 配置恢复",
  "The source server needs a current Xray configuration snapshot": "源服务器缺少当前 Xray 配置快照",
  "The target server has an unreviewed Xray configuration recovery": "目标服务器有尚未处理的 Xray 配置恢复",
  "The target server needs a current Xray configuration snapshot": "目标服务器缺少当前 Xray 配置快照",
};

function unavailableCandidateText(reason?: string | null) {
  if (!reason) return "当前节点状态不满足受管出口要求";
  return unavailableReasons[reason] ?? reason;
}

function routingActionLabel(action: ManagedRoutingAction) {
  return {
    keep: "保持现有规则",
    set: "设置或替换规则",
    remove: "删除现有规则",
  }[action];
}

function ManagedNodeEgressPicker({ serverId, disabled, onChanged }: { serverId: string; disabled: boolean; onChanged: () => void | Promise<unknown> }) {
  const [catalog, setCatalog] = useState<Awaited<ReturnType<typeof getServerEgressCatalog>> | null>(null);
  const [targetNodeId, setTargetNodeId] = useState("");
  const [promote, setPromote] = useState(false);
  const [routingAction, setRoutingAction] = useState<ManagedRoutingAction>("keep");
  const [selector, setSelector] = useState<SelectorDraft>(emptySelector);
  const [preview, setPreview] = useState<{ value: Awaited<ReturnType<typeof previewServerEgress>>; request: ServerEgressPreviewRequest } | null>(null);
  const [removal, setRemoval] = useState<{ value: Awaited<ReturnType<typeof previewServerEgressRemoval>>; request: ServerEgressRemovePreviewRequest } | null>(null);
  const [result, setResult] = useState<{ id: string; status: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<"preview" | "apply" | "remove-preview" | "remove" | "">("");
  const [error, setError] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [removeConfirmed, setRemoveConfirmed] = useState(false);
  const alive = useRef(false);
  const request = useRef(0);
  const currentServer = useRef(serverId);

  function current(target: string, token: number) {
    return alive.current && currentServer.current === target && request.current === token;
  }

  function invalidate() { setPreview(null); setRemoval(null); setResult(null); setError(""); }

  async function load() {
    const target = currentServer.current;
    if (!target) { setCatalog(null); return; }
    const token = ++request.current;
    setLoading(true); setError("");
    try {
      const value = await getServerEgressCatalog(target);
      if (!current(target, token)) return;
      setCatalog(value);
      setTargetNodeId(previous => value.candidates.some(item => item.node_id === previous && item.available) ? previous : value.candidates.find(item => item.available)?.node_id ?? "");
    } catch (failure) {
      if (current(target, token)) { setCatalog(null); setTargetNodeId(""); setError(failure instanceof Error ? failure.message : "无法读取受管节点出口候选。"); }
    } finally { if (current(target, token)) setLoading(false); }
  }

  useEffect(() => {
    alive.current = true; currentServer.current = serverId; request.current += 1;
    setCatalog(null); setTargetNodeId(""); setPromote(false); setRoutingAction("keep"); setSelector(emptySelector()); setPreview(null); setRemoval(null); setResult(null); setError(""); setConfirmOpen(false); setConfirmed(false); setRemoveConfirmed(false);
    void load();
    return () => { alive.current = false; request.current += 1; };
  }, [serverId]);

  function routingValue(): ServerEgressRoutingSelector {
    const value: ServerEgressRoutingSelector = {
      domains: splitSelector(selector.domains), ips: splitSelector(selector.ips), inbound_tags: splitSelector(selector.inbound_tags),
      users: splitSelector(selector.users), protocols: splitSelector(selector.protocols),
      ...(selector.port.trim() ? { port: selector.port.trim() } : {}), ...(selector.network ? { network: selector.network } : {}),
    };
    if (![value.domains, value.ips, value.inbound_tags, value.users, value.protocols].some(items => items.length) && !value.port && !value.network) {
      throw new Error("启用定向路由时至少填写一个匹配条件。");
    }
    return value;
  }

  function payload(): ServerEgressPreviewRequest {
    if (!targetNodeId) throw new Error("请选择可用的受管节点。");
    const value: ServerEgressPreviewRequest = { target_node_id: targetNodeId, promote_to_default: promote };
    if (routingAction === "set") value.routing = routingValue();
    if (routingAction === "remove") value.routing = null;
    return value;
  }

  async function probeCandidatePin(
    target: string,
    token: number,
    descriptor: ServerEgressTlsProbeDescriptor,
  ) {
    const protocol = descriptor.protocol.trim().toLowerCase();
    const address = descriptor.address.trim();
    const serverName = descriptor.server_name?.trim();
    if (!TLS_PIN_PROTOCOLS.has(protocol) || !address) {
      throw new Error("控制面返回了无效的 TLS 探测目标。");
    }
    if (
      !Number.isInteger(descriptor.port)
      || descriptor.port < 1
      || descriptor.port > 65_535
      || !Array.isArray(descriptor.alpn)
      || descriptor.alpn.some(item => typeof item !== "string")
    ) {
      throw new Error("控制面返回了无效的 TLS 探测目标。");
    }
    const probe: AgentOutboundTLSPinProbeOperationRequest = {
      protocol: protocol as AgentOutboundTLSPinProbeOperationRequest["protocol"],
      address,
      port: descriptor.port,
      ...(serverName ? { server_name: serverName } : {}),
      alpn: descriptor.alpn,
      timeout_ms: 8_000,
      command_timeout_ms: 20_000,
    };
    const queued = await queueAgentOperation(target, "outbound_tls_pin_probe", probe);
    const commandId = queued.command.id;
    for (let attempt = 0; attempt < POLL_ATTEMPTS; attempt += 1) {
      if (!current(target, token)) return null;
      const response = await listServerCommands(target, undefined, [commandId]);
      if (!current(target, token)) return null;
      const command = response.commands.find(item => item.id === commandId);
      if (command?.status === "failed" || command?.status === "skipped") {
        throw new Error(commandFailure(command));
      }
      if (command?.status === "succeeded") return parsedProbePin(command.result_body);
      if (attempt + 1 < POLL_ATTEMPTS) await wait(POLL_INTERVAL_MS);
    }
    throw new Error("Agent 证书探测命令等待超时；请稍后重试。");
  }

  async function runPreview() {
    const target = currentServer.current, token = ++request.current;
    try {
      const body = payload(); setBusy("preview"); setError(""); setResult(null);
      const candidate = catalog?.candidates.find(item => item.node_id === body.target_node_id);
      if (candidate?.tls_probe) {
        const pin = await probeCandidatePin(target, token, candidate.tls_probe);
        if (!pin || !current(target, token)) return;
        body.pinned_peer_cert_sha256 = pin;
      }
      const value = await previewServerEgress(target, body);
      if (current(target, token)) setPreview({ value, request: body });
    } catch (failure) { if (current(target, token)) setError(failure instanceof Error ? failure.message : "无法预览受管节点出口。"); }
    finally { if (current(target, token)) setBusy(""); }
  }

  async function applyPreview() {
    if (!preview || !confirmed) return;
    const target = currentServer.current, token = ++request.current, pending = preview;
    setBusy("apply"); setError("");
    try {
      const pin = pending.value.pinned_peer_cert_sha256 ?? pending.request.pinned_peer_cert_sha256;
      const response = await applyServerEgress(target, {
        ...pending.request,
        ...(pin ? { pinned_peer_cert_sha256: pin } : {}),
        expected_preview_revision: pending.value.preview_revision,
        dispatch: true,
      });
      if (!current(target, token)) return;
      setResult({ id: response.change_set_id, status: response.change_set_status });
      setPreview(null); setConfirmOpen(false); setConfirmed(false);
      setRoutingAction("keep"); setSelector(emptySelector());
      await onChanged();
      if (!current(target, token)) return;
      const value = await getServerEgressCatalog(target);
      if (current(target, token)) setCatalog(value);
    } catch (failure) { if (current(target, token)) setError(failure instanceof Error ? failure.message : "无法应用受管节点出口。"); }
    finally { if (current(target, token)) setBusy(""); }
  }

  async function prepareRemoval(nodeId: string) {
    const target = currentServer.current, token = ++request.current;
    const body = { target_node_id: nodeId };
    setBusy("remove-preview"); setError(""); setResult(null); setRemoval(null); setRemoveConfirmed(false);
    try {
      const value = await previewServerEgressRemoval(target, body);
      if (current(target, token)) setRemoval({ value, request: body });
    } catch (failure) { if (current(target, token)) setError(failure instanceof Error ? failure.message : "无法预览受管节点出口断开操作。"); }
    finally { if (current(target, token)) setBusy(""); }
  }

  async function applyRemoval() {
    if (!removal || !removeConfirmed) return;
    const target = currentServer.current, token = ++request.current, pending = removal;
    setBusy("remove"); setError("");
    try {
      const response = await removeServerEgress(target, { ...pending.request, expected_preview_revision: pending.value.preview_revision, dispatch: true });
      if (!current(target, token)) return;
      setResult({ id: response.change_set_id, status: response.change_set_status });
      setRemoval(null); setRemoveConfirmed(false);
      await onChanged();
      if (!current(target, token)) return;
      const value = await getServerEgressCatalog(target);
      if (current(target, token)) setCatalog(value);
    } catch (failure) { if (current(target, token)) setError(failure instanceof Error ? failure.message : "无法断开受管节点出口。"); }
    finally { if (current(target, token)) setBusy(""); }
  }

  function patchSelector(field: keyof SelectorDraft, value: string) { setSelector(previous => ({ ...previous, [field]: value })); invalidate(); }
  const blocked = disabled || loading || Boolean(busy) || !serverId;
  return <Card title="其他节点出口" extra={<Button icon={<ReloadOutlined />} aria-label="刷新节点出口候选" loading={loading} disabled={disabled || Boolean(busy) || !serverId} onClick={() => void load()}>刷新候选</Button>}>
    <Flex vertical gap="middle">
      <Alert type="info" showIcon title="候选、出站凭据和跨服务器变更均由控制面生成；浏览器只提交节点 ID 与路由选择器，不读取或拼接节点凭据。" />
      {error && <Alert type="error" showIcon title={zhMessage(error)} />}
      {result && <Alert type="success" showIcon title={<>受管出口变更已下发：<Typography.Text code>{result.id}</Typography.Text>（{zhStatus(result.status)}）。可在“系统设置 → 变更记录”跟踪执行和自动回滚。</>} />}
      <Table rowKey="node_id" size="small" pagination={false} loading={loading} dataSource={catalog?.candidates ?? []} locale={{ emptyText: serverId ? "暂无可用的物理受管节点" : "请选择服务器" }} columns={[
        { title: "节点", dataIndex: "node_name" }, { title: "所在服务器", dataIndex: "server_name" }, { title: "协议", dataIndex: "protocol" },
        { title: "状态", key: "state", render: (_: unknown, item: NonNullable<typeof catalog>["candidates"][number]) => item.available ? <Space wrap><Tag color="success">可用</Tag>{item.configured && <Tag color="processing">已配置</Tag>}{item.needs_repair && <Tag color="warning">需修复</Tag>}{item.is_default && <Tag color="gold">默认</Tag>}{item.has_routing_rule && <Tag>有路由规则</Tag>}</Space> : <Typography.Text type="secondary">不可用：{unavailableCandidateText(item.unavailable_reason)}</Typography.Text> },
        { title: "操作", key: "actions", width: 100, render: (_: unknown, item: NonNullable<typeof catalog>["candidates"][number]) => item.configured ? <Button danger disabled={blocked} loading={busy === "remove-preview"} onClick={() => void prepareRemoval(item.node_id)}>断开</Button> : "—" },
      ]} />
      <Form layout="vertical">
        <Form.Item label="目标节点" required><Select aria-label="受管出口节点" showSearch optionFilterProp="label" value={targetNodeId || undefined} disabled={blocked} placeholder="选择可用节点" options={(catalog?.candidates ?? []).map(item => ({ value: item.node_id, label: `${item.node_name} · ${item.server_name} · ${item.protocol}`, disabled: !item.available }))} onChange={value => { setTargetNodeId(value); setRoutingAction("keep"); setSelector(emptySelector()); invalidate(); }} /></Form.Item>
        <Checkbox checked={promote} disabled={blocked} onChange={event => { setPromote(event.target.checked); invalidate(); }}>将该节点提升为默认出站</Checkbox>
        <Form.Item label="路由规则处理" style={{ marginTop: 12 }} extra="“保持”不会改动已有规则；删除必须显式选择。">
          <Radio.Group aria-label="受管出口路由操作" value={routingAction} disabled={blocked} onChange={event => { setRoutingAction(event.target.value as ManagedRoutingAction); invalidate(); }}>
            <Radio.Button value="keep">保持现有规则</Radio.Button>
            <Radio.Button value="set">设置或替换规则</Radio.Button>
            <Radio.Button value="remove" disabled={!catalog?.candidates.find(item => item.node_id === targetNodeId)?.has_routing_rule}>删除现有规则</Radio.Button>
          </Radio.Group>
        </Form.Item>
        {routingAction === "set" && <Card size="small" title="安全路由选择器" style={{ marginTop: 12 }}><Typography.Paragraph type="secondary">每行或逗号分隔；出口 tag 与规则 marktag 由控制面固定生成。</Typography.Paragraph><Flex gap="middle" wrap>
          <Form.Item label="域名" style={{ flex: "1 1 280px" }}><Input.TextArea aria-label="出口路由域名" rows={3} placeholder="geosite:cn" value={selector.domains} onChange={event => patchSelector("domains", event.target.value)} /></Form.Item>
          <Form.Item label="IP / GeoIP" style={{ flex: "1 1 280px" }}><Input.TextArea aria-label="出口路由 IP" rows={3} placeholder="geoip:private" value={selector.ips} onChange={event => patchSelector("ips", event.target.value)} /></Form.Item>
          <Form.Item label="入站 Tag" style={{ flex: "1 1 280px" }}><Input.TextArea aria-label="出口路由入站" rows={3} value={selector.inbound_tags} onChange={event => patchSelector("inbound_tags", event.target.value)} /></Form.Item>
          <Form.Item label="用户 email" style={{ flex: "1 1 280px" }}><Input.TextArea aria-label="出口路由用户" rows={3} value={selector.users} onChange={event => patchSelector("users", event.target.value)} /></Form.Item>
          <Form.Item label="协议" style={{ flex: "1 1 280px" }}><Input.TextArea aria-label="出口路由协议" rows={3} placeholder="bittorrent" value={selector.protocols} onChange={event => patchSelector("protocols", event.target.value)} /></Form.Item>
          <Form.Item label="端口" style={{ flex: "1 1 180px" }}><Input aria-label="出口路由端口" placeholder="80,443 或 1000-2000" value={selector.port} onChange={event => patchSelector("port", event.target.value)} /></Form.Item>
          <Form.Item label="网络" style={{ flex: "1 1 180px" }}><Select aria-label="出口路由网络" allowClear value={selector.network || undefined} options={[{ value: "tcp", label: "TCP" }, { value: "udp", label: "UDP" }, { value: "tcp,udp", label: "TCP + UDP" }]} onChange={value => patchSelector("network", value ?? "")} /></Form.Item>
        </Flex></Card>}
      </Form>
      <Button type="primary" disabled={blocked || !targetNodeId} loading={busy === "preview"} onClick={() => void runPreview()}>生成安全预览</Button>
      {preview && <Card size="small" title="受管节点出口预览"><Descriptions column={1} items={[
        { key: "source", label: "源服务器", children: preview.value.source_server_name }, { key: "target", label: "目标", children: `${preview.value.target_node_name} · ${preview.value.target_server_name}` },
        { key: "protocol", label: "协议", children: preview.value.protocol }, { key: "action", label: "操作", children: ({ create: "新建", update: "更新", repair: "修复", remove: "断开" } as const)[preview.value.action] },
        { key: "outbound", label: "受管出站 Tag", children: <Typography.Text code>{preview.value.outbound_tag}</Typography.Text> },
        { key: "routing-action", label: "路由规则处理", children: routingActionLabel(preview.value.routing_action) },
        { key: "rule", label: "受管路由标记", children: preview.value.routing_action === "set" ? <Typography.Text code>{preview.value.routing_marktag}</Typography.Text> : preview.value.routing_action === "remove" ? "删除对应标记规则" : "保持现状" },
        { key: "default", label: "应用后默认", children: preview.value.will_be_default ? "是" : "否" },
      ]} /><Button danger={preview.value.action === "repair"} disabled={blocked} onClick={() => { setConfirmOpen(true); setConfirmed(false); }}>应用此预览</Button></Card>}
    </Flex>
    <Modal open={confirmOpen} title="应用受管节点出口？" okText="应用" okButtonProps={{ disabled: !confirmed }} confirmLoading={busy === "apply"} onOk={() => void applyPreview()} onCancel={() => !busy && setConfirmOpen(false)} destroyOnHidden><Flex vertical gap="middle"><Alert type="warning" showIcon title="这会通过可回滚变更集在目标入站创建专用客户端，并更新源服务器 Xray 配置。预览已绑定两端最新快照；状态变化会拒绝应用。" /><Checkbox checked={confirmed} disabled={Boolean(busy)} onChange={event => setConfirmed(event.target.checked)}>我已核对源服务器、目标节点、默认出口和路由范围</Checkbox></Flex></Modal>
    <Modal open={Boolean(removal)} title="断开受管节点出口？" okText="确认断开" okButtonProps={{ danger: true, disabled: !removeConfirmed }} confirmLoading={busy === "remove"} onOk={() => void applyRemoval()} onCancel={() => !busy && setRemoval(null)} destroyOnHidden><Flex vertical gap="middle"><Alert type="warning" showIcon title="系统会从源服务器删除受管出站和关联路由，并在目标节点删除专用客户端。请勿改用原始出站 JSON 删除，否则可能遗留目标端客户端。" />{removal && <Descriptions column={1} items={[{ key: "target", label: "目标", children: `${removal.value.target_node_name} · ${removal.value.target_server_name}` }, { key: "outbound", label: "受管出站 Tag", children: <Typography.Text code>{removal.value.outbound_tag}</Typography.Text> }]} />}<Checkbox checked={removeConfirmed} disabled={Boolean(busy)} onChange={event => setRemoveConfirmed(event.target.checked)}>我确认同时清理源服务器出站、路由和目标节点客户端</Checkbox></Flex></Modal>
  </Card>;
}

function VisualEgressPanel() {
  const [servers, setServers] = useState<ServerSummary[]>([]);
  const [serverId, setServerId] = useState("");
  const [outbounds, setOutbounds] = useState<JsonObject[]>([]);
  const [routing, setRouting] = useState<JsonObject>({ rules: [] });
  const [routingText, setRoutingText] = useState("{\n  \"routing\": {\n    \"rules\": []\n  }\n}");
  const [warp, setWarp] = useState<JsonObject | null>(null);
  const [inventoryLoading, setInventoryLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [outboundEditor, setOutboundEditor] = useState<{ mode: EditorMode; originalTag: string } | null>(null);
  const [outboundText, setOutboundText] = useState("");
  const [outboundPin, setOutboundPin] = useState("");
  const [outboundRemoval, setOutboundRemoval] = useState("");
  const [ruleEditor, setRuleEditor] = useState(false);
  const [ruleText, setRuleText] = useState("{\n  \"type\": \"field\",\n  \"outboundTag\": \"direct\"\n}");
  const [ruleRemoval, setRuleRemoval] = useState<number | null>(null);
  const [warpAction, setWarpAction] = useState<"install" | "remove" | "">("");
  const [warpConfirmed, setWarpConfirmed] = useState(false);
  const [warpLicense, setWarpLicense] = useState("");
  const mounted = useRef(false);
  const context = useRef(0);
  const selected = useRef("");
  const inventoryRequest = useRef(0);
  const snapshotRequest = useRef(0);
  const rules = routingRules(routing);

  function isCurrent(target: string, generation: number) {
    return mounted.current && selected.current === target && context.current === generation;
  }

  async function durableCommands(target: string, generation: number, operations: PendingOperation[]) {
    const queued = await Promise.all(operations.map(operation => queueAgentOperation(target, operation.kind, operation.payload)));
    const ids = new Set(queued.map(item => item.command.id));
    for (let attempt = 0; attempt < POLL_ATTEMPTS; attempt += 1) {
      if (!isCurrent(target, generation)) return null;
      const response = await listServerCommands(target, undefined, [...ids]);
      if (!isCurrent(target, generation)) return null;
      const commands = response.commands.filter(command => ids.has(command.id));
      const failure = commands.find(command => command.status === "failed" || command.status === "skipped");
      if (failure) throw new Error(commandFailure(failure));
      if (commands.length === ids.size && commands.every(command => command.status === "succeeded")) {
        return queued.map(item => commands.find(command => command.id === item.command.id)!);
      }
      if (attempt + 1 < POLL_ATTEMPTS) await wait(POLL_INTERVAL_MS);
    }
    throw new Error("Agent 命令等待超时；命令仍保留在历史中，请稍后刷新。");
  }

  async function refreshSnapshot(target = selected.current, generation = context.current, announce = false) {
    if (!target) return false;
    const request = ++snapshotRequest.current;
    setBusy("refresh"); setError(""); if (!announce) setNotice("");
    try {
      const commands = await durableCommands(target, generation, [
        { kind: "outbounds_list" },
        { kind: "routing_read" },
        { kind: "warp_status" },
      ]);
      if (!commands || !isCurrent(target, generation) || request !== snapshotRequest.current) return false;
      const nextOutbounds = parseOutbounds(commands[0].result_body);
      const nextRouting = parseRouting(commands[1].result_body);
      const nextWarp = parseWarp(commands[2].result_body);
      setOutbounds(nextOutbounds); setRouting(nextRouting.routing); setRoutingText(JSON.stringify(routingDocument(nextRouting), null, 2)); setWarp(nextWarp);
      return true;
    } catch (failure) {
      if (isCurrent(target, generation) && request === snapshotRequest.current) {
        setError(failure instanceof Error ? failure.message : "无法读取服务器出站与路由状态。");
      }
      return false;
    } finally {
      if (isCurrent(target, generation) && request === snapshotRequest.current) setBusy("");
    }
  }

  async function mutate(kind: AgentOperationKind, payload: AgentOperationPayload | undefined, message: string) {
    const target = selected.current, generation = context.current;
    if (!target || busy) return false;
    setBusy(kind); setError(""); setNotice("");
    try {
      const commands = await durableCommands(target, generation, [{ kind, payload }]);
      if (!commands || !isCurrent(target, generation)) return false;
      const refreshed = await refreshSnapshot(target, generation, true);
      if (refreshed && isCurrent(target, generation)) setNotice(message);
      return refreshed;
    } catch (failure) {
      if (isCurrent(target, generation)) setError(failure instanceof Error ? failure.message : "服务器操作失败。");
      return false;
    } finally {
      if (isCurrent(target, generation) && busy !== "refresh") setBusy("");
    }
  }

  async function loadServers() {
    const request = ++inventoryRequest.current;
    setInventoryLoading(true); setError("");
    try {
      const local = (await listServers()).filter(server => !server.is_federated);
      if (!mounted.current || request !== inventoryRequest.current) return;
      setServers(local);
      const target = local.some(server => server.id === selected.current) ? selected.current : local[0]?.id ?? "";
      selected.current = target; setServerId(target);
    } catch (failure) {
      if (mounted.current && request === inventoryRequest.current) setError(failure instanceof Error ? failure.message : "无法读取服务器列表。");
    } finally {
      if (mounted.current && request === inventoryRequest.current) setInventoryLoading(false);
    }
  }

  function chooseServer(value: string) {
    context.current += 1; snapshotRequest.current += 1; selected.current = value;
    setServerId(value); setOutbounds([]); setRouting({ rules: [] }); setRoutingText("{\n  \"routing\": {\n    \"rules\": []\n  }\n}"); setWarp(null); setError(""); setNotice(""); setBusy("");
    setOutboundEditor(null); setOutboundPin(""); setOutboundRemoval(""); setRuleEditor(false); setRuleRemoval(null); setWarpAction(""); setWarpConfirmed(false); setWarpLicense("");
  }

  useEffect(() => {
    mounted.current = true; void loadServers();
    return () => { mounted.current = false; context.current += 1; inventoryRequest.current += 1; snapshotRequest.current += 1; };
  }, []);

  useEffect(() => {
    if (!serverId) return;
    selected.current = serverId;
    const generation = ++context.current;
    void refreshSnapshot(serverId, generation);
  }, [serverId]);

  function editOutbound(mode: EditorMode, outbound?: JsonObject) {
    const originalTag = outbound && typeof outbound.tag === "string" ? outbound.tag.trim() : "";
    if (mode === "update" && isManagedEgressTag(originalTag)) {
      setError("受管节点出口不能通过原始 JSON 编辑；请在“其他节点出口”中重新配置或断开。");
      return;
    }
    setOutboundEditor({ mode, originalTag });
    setOutboundText(JSON.stringify(outbound ?? { tag: "", protocol: "freedom", settings: {} }, null, 2));
    setOutboundPin(outbound ? outboundTlsPin(outbound) : "");
    setError("");
  }

  async function probeOutboundPin() {
    if (!outboundEditor || busy) return;
    const target = selected.current, generation = context.current;
    try {
      const outbound = requiredObject(outboundText, "出站配置"); outboundIdentity(outbound);
      const request = tlsPinProbeRequest(outbound);
      setBusy("outbound_tls_pin_probe"); setError("");
      const completed = await durableCommands(target, generation, [{ kind: "outbound_tls_pin_probe", payload: request }]);
      if (!completed || !isCurrent(target, generation)) return;
      const pin = parsedProbePin(completed[0].result_body);
      setOutboundPin(pin);
      setOutboundText(JSON.stringify(withTlsPin(outbound, pin), null, 2));
    } catch (failure) {
      if (isCurrent(target, generation)) setError(failure instanceof Error ? failure.message : "无法探测 TLS 证书 Pin。");
    } finally {
      if (isCurrent(target, generation)) setBusy("");
    }
  }

  async function saveOutbound() {
    if (!outboundEditor) return;
    try {
      const outbound = withTlsPin(requiredObject(outboundText, "出站配置"), outboundPin); outboundIdentity(outbound);
      const payload = outboundEditor.mode === "add"
        ? { action: "add" as const, outbound }
        : { action: "update" as const, tag: outboundEditor.originalTag, outbound };
      if (await mutate("outbounds_manage", payload, outboundEditor.mode === "add" ? "出站已添加。" : "出站已更新。")) { setOutboundEditor(null); setOutboundPin(""); }
    } catch (failure) { setError(failure instanceof Error ? failure.message : "出站 JSON 无效。"); }
  }

  async function removeOutbound() {
    const tag = outboundRemoval; if (!tag) return;
    if (isManagedEgressTag(tag)) {
      setOutboundRemoval("");
      setError("受管节点出口必须通过“其他节点出口”的断开操作清理，以免遗留目标节点客户端。");
      return;
    }
    if (await mutate("outbounds_manage", { action: "remove", tag }, `出站 ${tag} 已删除。`)) setOutboundRemoval("");
  }

  async function makeDefault(tag: string) {
    try {
      const tags = outbounds.map(outbound => outboundIdentity(outbound).tag);
      if (new Set(tags).size !== tags.length) throw new Error("存在重复的出站 tag，无法安全重排；请先在高级配置中修复。");
      await mutate("outbounds_manage", { action: "reorder", tags: [tag, ...tags.filter(value => value !== tag)] }, `${tag} 已设为默认出站；重启 Xray 后加载顺序生效。`);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "无法设置默认出站。"); }
  }

  async function addRule() {
    try {
      const rule = requiredObject(ruleText, "路由规则");
      if (await mutate("routing_manage", { action: "add_rule", rule, index: rules.length }, "路由规则已添加并应用。")) setRuleEditor(false);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "路由规则 JSON 无效。"); }
  }

  async function removeRule() {
    if (ruleRemoval === null) return;
    const index = ruleRemoval;
    if (await mutate("routing_manage", { action: "remove_rule", index }, `第 ${index + 1} 条路由规则已删除。`)) setRuleRemoval(null);
  }

  async function saveRouting() {
    try {
      const value = requiredObject(routingText, "完整路由配置");
      const envelope = Object.prototype.hasOwnProperty.call(value, "routing");
      const nextRouting = envelope ? record(value.routing) : value;
      if (!nextRouting) throw new Error("完整路由配置的 routing 必须是 JSON 对象。");
      const payload: AgentRoutingManageOperationRequest = { action: "set", routing: nextRouting };
      if (envelope && Object.prototype.hasOwnProperty.call(value, "observatory")) payload.observatory = value.observatory;
      if (envelope && Object.prototype.hasOwnProperty.call(value, "burstObservatory")) payload.burstObservatory = value.burstObservatory;
      await mutate("routing_manage", payload, "完整路由配置已保存并应用。");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "完整路由 JSON 无效。"); }
  }

  async function saveWarpLicense() {
    const value = warpLicense.trim();
    if (!value) { setError("请输入 WARP+ 凭据。"); return; }
    if (await mutate("warp_license", { license: value }, "WARP+ 凭据已更新。")) setWarpLicense("");
  }

  async function confirmWarp() {
    if (!warpAction || !warpConfirmed) return;
    const action = warpAction;
    const ok = await mutate(action === "install" ? "warp_install" : "warp_remove", action === "install" ? { accept_terms: true } : { confirm: true }, action === "install" ? "WARP 已安装并写入 warp-v4 / warp-v6 出站。" : "WARP 已移除。");
    if (ok) { setWarpAction(""); setWarpConfirmed(false); }
  }

  const disabled = Boolean(busy) || !serverId;
  return <Flex vertical gap="large">
    <Card title="目标服务器" extra={<Button icon={<ReloadOutlined />} aria-label="刷新出站与路由" loading={busy === "refresh" || inventoryLoading} disabled={!serverId || Boolean(busy)} onClick={() => void refreshSnapshot()}>刷新</Button>}>
      <Form layout="vertical"><Form.Item label="服务器" required>
        <Select aria-label="出站与路由服务器" showSearch optionFilterProp="label" loading={inventoryLoading} value={serverId || undefined} placeholder="选择本地主控服务器"
          options={servers.map(server => ({ value: server.id, label: server.name }))} onChange={chooseServer} />
      </Form.Item></Form>
      {!inventoryLoading && !servers.length && <Alert type="warning" showIcon title="暂无本地主控服务器；共享服务器不能在此修改 Xray 出站。" />}
      {error && <Alert type="error" role="alert" showIcon title={zhMessage(error)} />}
      {notice && <Alert type="success" showIcon title={notice} />}
    </Card>

    <Card title="Xray 出站" extra={<Button type="primary" icon={<PlusOutlined />} disabled={disabled} onClick={() => editOutbound("add")}>添加出站</Button>}>
      <Alert type="info" showIcon title="数组第一项是无路由规则命中时的默认出站；表格只显示 tag 和协议，不展示服务器地址、密钥或其他凭据。" style={{ marginBottom: 16 }} />
      <Table<JsonObject> rowKey={objectKey} dataSource={outbounds} loading={busy === "refresh"} pagination={false} locale={{ emptyText: serverId ? "暂无出站" : "请选择服务器" }} columns={[
        { title: "Tag", key: "tag", render: (_, outbound) => <Typography.Text code>{typeof outbound.tag === "string" ? outbound.tag : "未命名"}</Typography.Text> },
        { title: "协议", key: "protocol", render: (_, outbound) => typeof outbound.protocol === "string" ? outbound.protocol : "未知" },
        { title: "默认", key: "default", width: 100, render: (_, outbound, index) => index === 0 ? <Tag color="success">默认</Tag> : <Tag>否</Tag> },
        { title: "操作", key: "actions", width: 300, render: (_, outbound, index) => {
          const tag = typeof outbound.tag === "string" ? outbound.tag : "";
          const managed = isManagedEgressTag(tag);
          const warpManaged = tag === "warp-v4" || tag === "warp-v6";
          const protectedEntry = managed || warpManaged;
          const editHint = managed ? "请在其他节点出口中管理" : warpManaged ? "请在 Cloudflare WARP 中管理" : undefined;
          const deleteHint = managed ? "请使用其他节点出口中的断开操作" : warpManaged ? "请使用 Cloudflare WARP 的移除操作" : undefined;
          return <Space wrap><Button disabled={disabled || !tag || index === 0} onClick={() => void makeDefault(tag)}>设为默认</Button><Button icon={<EditOutlined />} title={editHint} disabled={disabled || !tag || protectedEntry} onClick={() => editOutbound("update", outbound)}>编辑 JSON</Button><Button danger icon={<DeleteOutlined />} title={deleteHint} disabled={disabled || !tag || protectedEntry} onClick={() => setOutboundRemoval(tag)}>删除</Button></Space>;
        } },
      ]} />
    </Card>

    <ManagedNodeEgressPicker serverId={serverId} disabled={disabled} onChanged={() => refreshSnapshot()} />

    <Card title="Xray 路由规则" extra={<Button type="primary" icon={<PlusOutlined />} disabled={disabled} onClick={() => { setRuleEditor(true); setError(""); }}>添加规则</Button>}>
      <Table<JsonObject> rowKey={objectKey} dataSource={rules} loading={busy === "refresh"} pagination={false} locale={{ emptyText: "暂无路由规则" }} columns={[
        { title: "顺序", key: "index", width: 80, render: (_, __, index) => index + 1 },
        { title: "标记", key: "marktag", render: (_, rule) => typeof rule.marktag === "string" ? <Typography.Text code>{rule.marktag}</Typography.Text> : "—" },
        { title: "出口", key: "target", render: (_, rule) => <Typography.Text code>{routeTarget(rule)}</Typography.Text> },
        { title: "匹配条件", key: "conditions", render: (_, rule) => routeConditions(rule) },
        { title: "操作", key: "actions", width: 100, render: (_, rule, index) => {
          const managed = typeof rule.marktag === "string" && rule.marktag.startsWith("managed-egress-rule:");
          return <Button danger icon={<DeleteOutlined />} title={managed ? "请使用其他节点出口中的断开操作" : undefined} disabled={disabled || managed} aria-label={`删除第 ${index + 1} 条路由规则`} onClick={() => setRuleRemoval(index)} />;
        } },
      ]} />
      <Card size="small" title="高级：完整路由配置 JSON" style={{ marginTop: 16 }}>
        <Typography.Paragraph type="secondary">顶层对象包含 routing，并按官方字段保留 observatory 与 burstObservatory；显式填写 null 表示删除对应观测站。</Typography.Paragraph>
        <Input.TextArea aria-label="完整路由配置 JSON" rows={12} spellCheck={false} value={routingText} disabled={disabled} onChange={event => setRoutingText(event.target.value)} />
        <Button type="primary" style={{ marginTop: 12 }} loading={busy === "routing_manage"} disabled={disabled} onClick={() => void saveRouting()}>保存完整路由配置</Button>
      </Card>
    </Card>

    <Card title="Cloudflare WARP" extra={<Space><Button disabled={disabled || Boolean(warp?.installed)} onClick={() => { setWarpAction("install"); setWarpConfirmed(false); }}>安装 WARP</Button><Button danger disabled={disabled || !Boolean(warp?.installed || warp?.registered)} onClick={() => { setWarpAction("remove"); setWarpConfirmed(false); }}>移除 WARP</Button></Space>}>
      {warp ? <WarpStatus body={warp} /> : <Typography.Text type="secondary">刷新后显示 WARP 状态。</Typography.Text>}
      <Descriptions style={{ marginTop: 16 }} column={1} items={[{ key: "default", label: "快捷默认出口", children: <Space wrap>{["warp-v4", "warp-v6"].filter(tag => outbounds.some(item => item.tag === tag)).map(tag => <Button key={tag} disabled={disabled || outbounds[0]?.tag === tag} onClick={() => void makeDefault(tag)}>将 {tag} 设为默认</Button>)}</Space> }]} />
      <Form layout="vertical" style={{ marginTop: 16 }}><Form.Item label="WARP+ 凭据" extra="凭据只提交给目标 Agent，保存成功后立即清空输入。"><Input.Password aria-label="WARP+ 凭据" autoComplete="off" value={warpLicense} disabled={disabled} onChange={event => setWarpLicense(event.target.value)} /></Form.Item><Button disabled={disabled || !warpLicense.trim()} loading={busy === "warp_license"} onClick={() => void saveWarpLicense()}>更新 WARP+</Button></Form>
    </Card>

    <Modal open={Boolean(outboundEditor)} width={820} title={outboundEditor?.mode === "add" ? "添加 Xray 出站" : "更新 Xray 出站"} okText="提交" okButtonProps={{ disabled: Boolean(busy) }} confirmLoading={busy === "outbounds_manage"} onOk={() => void saveOutbound()} onCancel={() => { if (!busy) { setOutboundEditor(null); setOutboundPin(""); } }} destroyOnHidden>
      <Alert type="warning" showIcon title="JSON 编辑器可能包含服务器凭据；请确认周围无人旁观，也不要把内容复制到工单或聊天中。" style={{ marginBottom: 16 }} />
      <Input.TextArea aria-label="出站 JSON" rows={18} spellCheck={false} value={outboundText} disabled={Boolean(busy)} onChange={event => setOutboundText(event.target.value)} />
      <Alert type="info" showIcon title="TLS 出站不允许 allowInsecure。可从所选服务器安全探测公网 TCP 目标的叶证书 SHA-256，也可手工填写；Pin 会写入 tlsSettings.pinnedPeerCertSha256。" style={{ marginTop: 16, marginBottom: 12 }} />
      <Form layout="vertical"><Form.Item label="证书 SHA-256 Pin" extra="64 位十六进制；轮换期可用逗号填写最多 8 项。Hysteria2/QUIC 不支持 TCP 自动探测。"><Input aria-label="证书 SHA-256 Pin" autoComplete="off" value={outboundPin} disabled={Boolean(busy)} onChange={event => setOutboundPin(event.target.value)} /></Form.Item><Button loading={busy === "outbound_tls_pin_probe"} disabled={Boolean(busy)} onClick={() => void probeOutboundPin()}>从目标服务器自动探测</Button></Form>
    </Modal>
    <Modal open={Boolean(outboundRemoval)} title="删除出站？" okText="删除" okButtonProps={{ danger: true }} confirmLoading={busy === "outbounds_manage"} onOk={() => void removeOutbound()} onCancel={() => !busy && setOutboundRemoval("")} destroyOnHidden><Typography.Paragraph>确认删除出站 <Typography.Text code>{outboundRemoval}</Typography.Text>？请先移除所有路由、负载均衡和代理引用。</Typography.Paragraph></Modal>
    <Modal open={ruleEditor} width={780} title="添加 Xray 路由规则" okText="添加" confirmLoading={busy === "routing_manage"} onOk={() => void addRule()} onCancel={() => !busy && setRuleEditor(false)} destroyOnHidden><Input.TextArea aria-label="路由规则 JSON" rows={16} spellCheck={false} value={ruleText} disabled={Boolean(busy)} onChange={event => setRuleText(event.target.value)} /></Modal>
    <Modal open={ruleRemoval !== null} title="删除路由规则？" okText="删除" okButtonProps={{ danger: true }} confirmLoading={busy === "routing_manage"} onOk={() => void removeRule()} onCancel={() => !busy && setRuleRemoval(null)} destroyOnHidden><Typography.Paragraph>确认删除当前配置中的第 {(ruleRemoval ?? 0) + 1} 条规则？提交时使用官方索引语义。</Typography.Paragraph></Modal>
    <Modal open={Boolean(warpAction)} title={warpAction === "install" ? "安装 Cloudflare WARP" : "移除 Cloudflare WARP"} okText={warpAction === "install" ? "安装" : "移除"} okButtonProps={{ danger: warpAction === "remove", disabled: !warpConfirmed }} confirmLoading={busy === "warp_install" || busy === "warp_remove"} onOk={() => void confirmWarp()} onCancel={() => !busy && setWarpAction("")} destroyOnHidden>
      <Flex vertical gap="middle"><Alert type={warpAction === "install" ? "info" : "warning"} showIcon title={warpAction === "install" ? "安装会注册 WARP 设备并写入 warp-v4、warp-v6 两个出站。" : "移除前必须先取消 WARP 的默认出站及所有路由引用。"} /><Checkbox checked={warpConfirmed} disabled={Boolean(busy)} onChange={event => setWarpConfirmed(event.target.checked)}>{warpAction === "install" ? <>我确认安装并接受 <a href="https://www.cloudflare.com/application/terms/" target="_blank" rel="noopener noreferrer">Cloudflare 应用条款</a></> : "我确认注销设备并删除 WARP 出站"}</Checkbox></Flex>
    </Modal>
  </Flex>;
}

export default function ServerEgressPanel({ advancedContent }: ServerEgressPanelProps) {
  const [active, setActive] = useState<EgressSection>("visual");
  return <section aria-label="服务器出站与路由"><Tabs activeKey={active} onChange={value => setActive(value as EgressSection)} destroyOnHidden items={[
    { key: "visual", label: "出站与路由", children: active === "visual" ? <VisualEgressPanel /> : null },
    ...(advancedContent ? [{ key: "advanced", label: "高级配置", children: active === "advanced" ? advancedContent : null }] : []),
  ]} /></section>;
}
