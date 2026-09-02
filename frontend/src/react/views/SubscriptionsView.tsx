import { zhMessage, zhStatus } from "../../i18n/zh-CN";
import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Col, Collapse, Descriptions, Empty, Flex, Form, Input, Modal, Progress, Row, Select, Spin, Switch, Tabs, Tag, Typography } from "antd";
import { CopyOutlined, DeleteOutlined, EditOutlined, KeyOutlined, LinkOutlined, PlusOutlined, ReloadOutlined, SettingOutlined, SyncOutlined, UserAddOutlined } from "@ant-design/icons";
import SubscriptionAccessPanel from "../components/SubscriptionAccessPanel";
import PlanManagementDialog from "../components/PlanManagementDialog";
import PlanNodeAliases from "../components/PlanNodeAliases";
import AutoSpeedRuleEditor from "../components/AutoSpeedRuleEditor";
import UserManagementDialog from "../components/UserManagementDialog";
import UserLoginDialog from "../components/UserLoginDialog";
import SubscriptionShortCodeDialog from "../components/SubscriptionShortCodeDialog";
import LegacyMMWXImportDialog from "../components/LegacyMMWXImportDialog";
import SubscriptionProfileDialog from "../components/SubscriptionProfileDialog";
import TemporarySubscriptionDialog from "../components/TemporarySubscriptionDialog";
import PrivateRoutedPolicyDialog from "../components/PrivateRoutedPolicyDialog";
import SubscriptionIpPolicyDialog from "../components/SubscriptionIpPolicyDialog";
import RegistrationInvitationsDialog from "../components/RegistrationInvitationsDialog";
import NodeManagementDialog from "../components/NodeManagementDialog";
import StrictInputNumber from "../components/StrictInputNumber";
import ExternalSubscriptionsPanel from "../components/ExternalSubscriptionsPanel";
import type { AutoSpeedRule } from "../../domain/auto-speed";
import type { CamouflagePoolCatalog } from "../../domain/camouflage";
import type { NodeOperation } from "../../services/node-management";
import type { UserOperation } from "../../services/user-management";
import type { PlanOperation } from "../../services/plan-management";
import type { SubscriptionTemplate } from "../../domain/subscription-templates";
import type { SubscriptionProfile } from "../../domain/subscription-profiles";
import type { TemporarySubscription } from "../../domain/temporary-subscriptions";
import type { PrivateRoutedNodesResponse } from "../../domain/private-routed-nodes";
import type { ServerKind, ServerSummary } from "../../domain/inventory";
import type { ManagedNode, ManagedNodeCreateRequest, ManagedNodeCreationMetadataResponse, ManagedNodeCreationOption, ManagedNodeType, ManagedProtocolProfile, MieruPortMappingMode, ProductUser, ProductUserRole, ProductUserSubscriptionToken, ProductUserTrafficResponse, SubscriptionCatalogBundle, SubscriptionCatalogImportResponse, SubscriptionClientFormat, SubscriptionCredential, SubscriptionDueTrafficResetResponse, SubscriptionFormatPreview, SubscriptionPlan, SubscriptionPlanAssignResponse, SubscriptionPlanCreateRequest, SubscriptionQuotaStatus, SubscriptionTemplatePreset, SubscriptionTrafficMode } from "../../domain/subscriptions";
import { listSubscriptionTemplates } from "../../services/subscription-templates";
import { extraSubscriptionFormats, subscriptionFormatHelp } from "../../domain/subscriptions";
import { listSubscriptionProfiles } from "../../services/subscription-profiles";
import { deleteTemporarySubscription, listTemporarySubscriptions } from "../../services/temporary-subscriptions";
import { listPrivateRoutes } from "../../services/private-routed-nodes";
import { fetchAppMeta } from "../../services/api";
import { listCamouflagePools } from "../../services/camouflage-pools";
import { listServers } from "../../services/inventory";
import { assignSubscriptionPlan, createManagedNode, createProductUser, createProductUserSubscriptionToken, createSubscriptionPlan, exportSubscriptionCatalog, getManagedNodeCreationMetadata, getProductUserQuota, getProductUserTraffic, getSubscriptionFormatPreview, importSubscriptionCatalog, listProductUserCredentials, listManagedNodes, listProductUsers, listSubscriptionPlans, listSubscriptionTemplatePresets, resetDueProductUserTraffic, resetProductUserTraffic, resetProductUserSubscriptionToken } from "../../services/subscriptions";

const newUserForm = () => ({ username: "", email: "", display_name: "", role: "user" as ProductUserRole, is_active: true });
const managedProtocolProfiles = ["vless-reality-vision", "vless-xhttp-reality-xmux", "anytls-shadowtls", "mieru", "socks5"] as const satisfies readonly ManagedProtocolProfile[];
const newNodeForm = (server_id = "") => ({ name: "", server_id, protocol: "vless", protocol_profile: "vless-reality-vision" as ManagedProtocolProfile,
  node_type: "physical" as ManagedNodeType, parent_id: null as string | null, target_node_id: null as string | null, inbound_tag: "", routed_outbound_tag: "", routed_rule_marktag: "", tag: "", tagsText: "", enabled: true,
  camouflage_pool_id: "", camouflage_sni: "", domestic_entry_ip: "", domestic_entry_port: null as number | null,
  mieru_port_mapping_mode: "one-to-one" as MieruPortMappingMode, ix_port: null as number | null,
  clientTemplateText: '{\n  "id": "client-{username}",\n  "email": "{username}__default"\n}', configText: "{}" });
type NodeForm = ReturnType<typeof newNodeForm>;

function presetForProfile(presets: SubscriptionTemplatePreset[], profile: ManagedProtocolProfile) {
  return presets.find(preset => preset.protocol_profile === profile || preset.id === profile);
}
function profileNodeForm(previous: NodeForm, option: ManagedNodeCreationOption, presets: SubscriptionTemplatePreset[], keepName = true): NodeForm {
  const preset = presetForProfile(presets, option.profile), camouflage = option.requires_camouflage_pool, mieru = option.profile === "mieru";
  return { ...previous, name: keepName && previous.name.trim() ? previous.name : preset?.name ?? "", protocol: option.protocol, protocol_profile: option.profile,
    node_type: "physical", parent_id: null, target_node_id: null, inbound_tag: preset?.inbound_tag ?? "", routed_outbound_tag: preset?.routed_outbound_tag ?? "",
    routed_rule_marktag: preset?.routed_rule_marktag ?? "", tag: preset?.tag ?? "", tagsText: preset?.tags.join(", ") ?? "",
    camouflage_pool_id: camouflage ? previous.camouflage_pool_id : "", camouflage_sni: camouflage ? previous.camouflage_sni : "",
    domestic_entry_ip: mieru ? previous.domestic_entry_ip : "", domestic_entry_port: mieru ? previous.domestic_entry_port : null,
    mieru_port_mapping_mode: mieru ? previous.mieru_port_mapping_mode : "one-to-one", ix_port: mieru && previous.mieru_port_mapping_mode === "manual" ? previous.ix_port : null,
    clientTemplateText: JSON.stringify(preset?.client_template ?? {}, null, 2), configText: JSON.stringify(preset?.config ?? {}, null, 2) };
}
const newPlanForm = () => ({ name: "", description: "", traffic_limit_gb: 128, cycle_days: 30, is_reset: true, reset_day: 1, speed_limit_mbps: 0, device_limit: 0, traffic_mode: "twoway" as SubscriptionTrafficMode, node_ids: [] as string[], node_name_overrides: {} as Record<string, string>, auto_speed_rules: [] as AutoSpeedRule[], node_name_override_enabled: false, clash_template_id: null as string | null, surge_template_id: null as string | null });
const profileSourceLabel = (value: string) => ({ create: "创建", import: "导入", upload: "上传", package: "套餐" } as Record<string, string>)[value] ?? zhStatus(value);
const formatOptions: { label: string; value: SubscriptionClientFormat }[] = [{ label: "Clash YAML", value: "clash" }, { label: "Surge 配置", value: "surge" }, { label: "sing-box JSON", value: "sing-box" }, { label: "Xray JSON", value: "xray" }, { label: "URI 列表", value: "uri-list" }, { label: "Base64 URI", value: "base64" }, ...extraSubscriptionFormats];
const blankToNull = (value: string) => value.trim() || null;
const splitCsv = (value: string) => value.split(",").map(item => item.trim()).filter(Boolean);
const readableError = (error: unknown) => error instanceof Error ? error.message : "请求失败。";
const formatDate = (value?: string | null) => value ? Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleDateString("zh-CN", { timeZone: "UTC" }) : "日期无效" : "未设置";
function parseJsonObject(value: string, label: string): Record<string, unknown> {
  let parsed: unknown;
  try { parsed = JSON.parse(value || "{}"); } catch { throw new Error(`${label}必须是有效的 JSON。`); }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error(`${label}必须是 JSON 对象。`);
  return parsed as Record<string, unknown>;
}
function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"]; let active = value / 1024, index = 0;
  while (active >= 1024 && index < units.length - 1) { active /= 1024; index += 1; }
  return `${active.toFixed(active >= 10 ? 1 : 2)} ${units[index]}`;
}
function quotaLabel(quota: SubscriptionQuotaStatus) { return !quota.is_active ? "未启用" : !quota.has_plan ? "未分配套餐" : quota.expired ? "已过期" : quota.over_quota ? "超出配额" : quota.reset_due ? "待重置" : "可用"; }
function quotaColor(quota: SubscriptionQuotaStatus) { return quota.over_quota || quota.expired || !quota.is_active || !quota.has_plan ? "error" : quota.reset_due ? "warning" : "success"; }
function credentialIdentifier(credential: SubscriptionCredential) { const source = credential.credential, value = source.id ?? source.password ?? source.auth ?? source.psk ?? source.pass; return typeof value === "string" ? value : credential.email; }

export default function SubscriptionsView() {
  const [servers, setServers] = useState<ServerSummary[]>([]), [users, setUsers] = useState<ProductUser[]>([]), [nodes, setNodes] = useState<ManagedNode[]>([]), [plans, setPlans] = useState<SubscriptionPlan[]>([]);
  const [templates, setTemplates] = useState<SubscriptionTemplate[]>([]), [profiles, setProfiles] = useState<SubscriptionProfile[]>([]), [temporary, setTemporary] = useState<TemporarySubscription[]>([]);
  const [privateRoutes, setPrivateRoutes] = useState<PrivateRoutedNodesResponse | null>(null), [presets, setPresets] = useState<SubscriptionTemplatePreset[]>([]), [shortLinksEnabled, setShortLinksEnabled] = useState(false);
  const [creationMetadata, setCreationMetadata] = useState<ManagedNodeCreationMetadataResponse | null>(null);
  const [camouflageCatalog, setCamouflageCatalog] = useState<CamouflagePoolCatalog | null>(null);
  const [loading, setLoading] = useState(false), [saving, setSaving] = useState(""), [error, setError] = useState(""), [success, setSuccess] = useState(""), [activeTab, setActiveTab] = useState("users");
  const [externalOpen, setExternalOpen] = useState(false);
  const [userForm, setUserForm] = useState(newUserForm), [nodeForm, setNodeForm] = useState(() => newNodeForm()), [planForm, setPlanForm] = useState(newPlanForm);
  const [assignForm, setAssignForm] = useState({ username: "", plan_id: "", start_date: "", expire_date: "", queue_agent_commands: true, no_restart: false, command_timeout_ms: 60_000 });
  const [catalogForm, setCatalogForm] = useState({ includeCredentials: false, importCredentials: false, serverMapText: "{}", catalogText: "" });
  const [aliasesValid, setAliasesValid] = useState(true), [rulesValid, setRulesValid] = useState(true), [lastAssignment, setLastAssignment] = useState<SubscriptionPlanAssignResponse | null>(null), [catalogImport, setCatalogImport] = useState<SubscriptionCatalogImportResponse | null>(null);
  const [planDialog, setPlanDialog] = useState({ id: "", mode: "edit" as PlanOperation, open: false }), [nodeDialog, setNodeDialog] = useState({ id: "", mode: "edit" as NodeOperation, open: false });
  const [userDialog, setUserDialog] = useState({ username: "", mode: "edit" as UserOperation, removalId: null as string | null, open: false }), [loginDialog, setLoginDialog] = useState({ username: "", open: false });
  const [shortCode, setShortCode] = useState({ username: "", open: false }), [ipPolicy, setIpPolicy] = useState({ username: "", open: false }), [profileDialog, setProfileDialog] = useState<SubscriptionProfile | null>(null);
  const [legacyOpen, setLegacyOpen] = useState(false), [invitationsOpen, setInvitationsOpen] = useState(false), [privatePolicyOpen, setPrivatePolicyOpen] = useState(false), [shareOpen, setShareOpen] = useState(false);
  const [updatedToken, setUpdatedToken] = useState<ProductUserSubscriptionToken | null>(null), [confirmTemporary, setConfirmTemporary] = useState<TemporarySubscription | null>(null), [confirmImport, setConfirmImport] = useState(false);
  const lifecycle = useRef(0), refreshVersion = useRef(0), operationVersion = useRef(0), operationBusy = useRef(false), userEpoch = useRef(0), selectedUsername = useRef(""), nodeFormInitialized = useRef(false);
  function selectUser(username: string) {
    if (selectedUsername.current === username) return;
    selectedUsername.current = username; ++userEpoch.current; setAssignForm(previous => ({ ...previous, username })); setLastAssignment(null); setUpdatedToken(null); setShareOpen(false);
  }
  async function refresh() {
    const run = ++refreshVersion.current, life = lifecycle.current; setLoading(true); setError("");
    try {
      const [serverList, userList, nodeList, planList, presetList, nodeCreation, poolCatalog, templateList, profileList, temporaryList, routes, meta] = await Promise.all([listServers(), listProductUsers(), listManagedNodes(), listSubscriptionPlans(), listSubscriptionTemplatePresets(), getManagedNodeCreationMetadata(), listCamouflagePools(), listSubscriptionTemplates(), listSubscriptionProfiles(), listTemporarySubscriptions(), listPrivateRoutes(), fetchAppMeta()]);
      if (run !== refreshVersion.current || life !== lifecycle.current) return;
      setServers(serverList); setUsers(userList.users); setNodes(nodeList.nodes); setPlans(planList.plans); setPresets(presetList.presets); setCreationMetadata(nodeCreation); setCamouflageCatalog(poolCatalog); setTemplates(templateList.templates); setProfiles(profileList.profiles); setTemporary(temporaryList.subscriptions); setPrivateRoutes(routes); setShortLinksEnabled(meta.short_links_enabled);
      setNodeForm(previous => {
        const server_id = serverList.some(server => server.id === previous.server_id) ? previous.server_id : serverList[0]?.id ?? "";
        const kind = serverList.find(server => server.id === server_id)?.server_kind ?? "direct";
        const allowed = nodeCreation.profiles.filter(option => managedProtocolProfiles.includes(option.profile) && option.allowed_server_kinds.includes(kind));
        const option = allowed.find(item => item.profile === previous.protocol_profile) ?? allowed[0];
        if (!option) return { ...previous, server_id };
        if (!nodeFormInitialized.current || option.profile !== previous.protocol_profile) {
          nodeFormInitialized.current = true;
          return profileNodeForm({ ...previous, server_id }, option, presetList.presets, nodeFormInitialized.current && !!previous.name.trim());
        }
        return { ...previous, server_id };
      });
      const availableUsers = userList.users.filter(user => !user.removal_id);
      if (!availableUsers.some(user => user.username === selectedUsername.current)) selectUser(availableUsers[0]?.username ?? "");
      setAssignForm(previous => planList.plans.some(plan => plan.id === previous.plan_id) ? previous : { ...previous, plan_id: planList.plans[0]?.id ?? "" });
    } catch (failure) { if (run === refreshVersion.current && life === lifecycle.current) setError(readableError(failure)); }
    finally { if (run === refreshVersion.current && life === lifecycle.current) setLoading(false); }
  }
  useEffect(() => { void refresh(); return () => { ++lifecycle.current; ++refreshVersion.current; ++operationVersion.current; ++userEpoch.current; }; }, []);
  async function perform(action: string, task: (current: () => boolean) => Promise<void>) {
    if (operationBusy.current) return;
    operationBusy.current = true; const run = ++operationVersion.current, life = lifecycle.current; setSaving(action); setError(""); setSuccess("");
    const current = () => run === operationVersion.current && life === lifecycle.current;
    try { await task(current); } catch (failure) { if (current()) setError(readableError(failure)); }
    finally { if (current()) { operationBusy.current = false; setSaving(""); } }
  }
  function submitUser() {
    if (!userForm.username.trim()) { setError("请填写用户名。"); return; }
    void perform("user", async current => {
      const username = userForm.username.trim(); await createProductUser({ username, email: blankToNull(userForm.email), display_name: blankToNull(userForm.display_name), role: userForm.role, is_active: userForm.is_active });
      if (!current()) return; setSuccess(`已创建用户 ${username}。`); setUserForm(newUserForm()); await refresh();
    });
  }
  function submitNode() {
    if (!nodeForm.server_id) { setError("请选择服务器。"); return; } if (!nodeForm.name.trim()) { setError("请填写节点名称。"); return; }
    const option = creationMetadata?.profiles.find(item => managedProtocolProfiles.includes(item.profile) && item.profile === nodeForm.protocol_profile);
    if (!option) { setError("请选择可用的协议档案。"); return; }
    const serverKind = servers.find(server => server.id === nodeForm.server_id)?.server_kind ?? "direct";
    if (!option.allowed_server_kinds.includes(serverKind)) { setError("所选协议档案不适用于这类服务器。"); return; }
    const camouflagePool = camouflageCatalog?.pools.find(pool => pool.id === nodeForm.camouflage_pool_id);
    if (option.requires_camouflage_pool && !camouflagePool) { setError("请从目录中选择伪装池。"); return; }
    const validPort = (value: number | null) => value !== null && Number.isInteger(value) && value >= 1 && value <= 65535;
    if (option.requires_domestic_entry && (!nodeForm.domestic_entry_ip.trim() || !validPort(nodeForm.domestic_entry_port))) {
      setError("请填写国内入口 IP 和 1 至 65535 的端口。"); return;
    }
    if (option.profile === "mieru" && nodeForm.mieru_port_mapping_mode === "manual" && !validPort(nodeForm.ix_port)) {
      setError("手动映射时请填写 1 至 65535 的 IX 端口。"); return;
    }
    void perform("node", async current => {
      const config = parseJsonObject(nodeForm.configText, "节点配置");
      if (option.fixed_port) config.port = option.fixed_port;
      const mieru = option.profile === "mieru", camouflage = option.requires_camouflage_pool;
      const payload: ManagedNodeCreateRequest = { name: nodeForm.name.trim(), server_id: nodeForm.server_id, protocol: option.protocol,
        protocol_profile: option.profile, node_type: "physical", parent_id: null,
        target_node_id: null, inbound_tag: blankToNull(nodeForm.inbound_tag),
        routed_outbound_tag: blankToNull(nodeForm.routed_outbound_tag), routed_rule_marktag: blankToNull(nodeForm.routed_rule_marktag),
        tag: blankToNull(nodeForm.tag), tags: splitCsv(nodeForm.tagsText), enabled: nodeForm.enabled,
        camouflage_pool_id: camouflage ? camouflagePool?.id ?? null : null,
        camouflage_sni: camouflage ? camouflagePool?.server_name ?? null : null,
        domestic_entry_ip: mieru ? blankToNull(nodeForm.domestic_entry_ip) : null,
        domestic_entry_port: mieru ? nodeForm.domestic_entry_port : null,
        mieru_port_mapping_mode: mieru ? nodeForm.mieru_port_mapping_mode : null,
        ix_port: mieru ? nodeForm.mieru_port_mapping_mode === "one-to-one" ? nodeForm.domestic_entry_port : nodeForm.ix_port : null,
        client_template: parseJsonObject(nodeForm.clientTemplateText, "客户端模板"), config };
      const result = await createManagedNode(payload); if (!current()) return;
      const runtime = result.commands?.[0];
      setSuccess(runtime
        ? `已创建节点 ${result.node.name}，真实运行配置已下发 Agent（${runtime.status}）。`
        : `已创建节点 ${result.node.name}。`);
      setNodeForm({ ...profileNodeForm(newNodeForm(result.node.server_id), option, presets, false), name: "" }); await refresh();
    });
  }
  function submitPlan() {
    if (!aliasesValid || !rulesValid) return; if (!planForm.name.trim()) { setError("请填写套餐名称。"); return; }
    void perform("plan", async current => {
      const payload: SubscriptionPlanCreateRequest = { ...planForm, name: planForm.name.trim(), description: planForm.description.trim(), reset_day: planForm.is_reset ? planForm.reset_day : 0, node_ids: [...planForm.node_ids], node_name_overrides: { ...planForm.node_name_overrides }, auto_speed_rules: planForm.auto_speed_rules.map(rule => ({ ...rule })), node_multipliers: Object.fromEntries(planForm.node_ids.map(id => [id, 1])) };
      const result = await createSubscriptionPlan(payload); if (!current()) return; setSuccess(`已创建套餐 ${result.plan.name}。`); setPlanForm(newPlanForm()); await refresh(); if (current()) setAssignForm(previous => ({ ...previous, plan_id: result.plan.id }));
    });
  }
  function submitAssignment() {
    if (!assignForm.username || !assignForm.plan_id) { setError("请选择用户和套餐。"); return; }
    const epoch = userEpoch.current;
    void perform("assign", async current => {
      const result = await assignSubscriptionPlan(assignForm.username, { plan_id: assignForm.plan_id, start_date: blankToNull(assignForm.start_date), expire_date: blankToNull(assignForm.expire_date), queue_agent_commands: assignForm.queue_agent_commands, no_restart: assignForm.no_restart, command_timeout_ms: assignForm.command_timeout_ms });
      if (!current()) return;
      if (epoch === userEpoch.current) setLastAssignment(result);
      setSuccess(result.commands.length ? `已分配套餐 ${result.plan.name}，并将 ${result.commands.length} 条命令加入队列。` : `已分配套餐 ${result.plan.name}。`); await refresh();
    });
  }
  function exportCatalog() { void perform("export", async current => { const result = await exportSubscriptionCatalog(catalogForm.includeCredentials); if (current()) { setCatalogForm(previous => ({ ...previous, catalogText: JSON.stringify(result.catalog, null, 2) })); setSuccess("订阅目录已导出。"); } }); }
  function importCatalog() { void perform("import", async current => {
    const catalog = parseJsonObject(catalogForm.catalogText, "订阅目录") as unknown as SubscriptionCatalogBundle, map = parseJsonObject(catalogForm.serverMapText, "服务器映射");
    const server_map = Object.fromEntries(Object.entries(map).filter((entry): entry is [string, string] => typeof entry[1] === "string"));
    const result = await importSubscriptionCatalog({ catalog, server_map, import_credentials: catalogForm.importCredentials }); if (!current()) return; setCatalogImport(result); setConfirmImport(false); setSuccess("订阅目录已导入。"); await refresh();
  }); }
  function revokeTemporary(value: TemporarySubscription) { void perform(`temporary:${value.id}`, async current => { await deleteTemporarySubscription(value.id); if (!current()) return; setTemporary(previous => previous.filter(item => item.id !== value.id)); setConfirmTemporary(null); setSuccess(`已撤销临时链接 ${value.label}。`); }); }
  async function copyTemporary(value: TemporarySubscription) { const life = lifecycle.current; try { await navigator.clipboard.writeText(value.subscription_url); if (life === lifecycle.current) setSuccess(`已复制临时链接 ${value.label}。`); } catch { if (life === lifecycle.current) setError("无法访问剪贴板。"); } }
  function manageUser(user: ProductUser, mode: UserOperation) { setUserDialog({ username: user.username, mode, removalId: user.removal_id ?? null, open: true }); }
  const selectedUser = users.find(user => user.username === assignForm.username), selectedUserPlan = plans.find(plan => plan.id === selectedUser?.current_plan_id);
  const temporaryNodes = nodes.filter(node => selectedUserPlan?.node_ids.includes(node.id) && !node.removal_id).map(node => ({ title: `${node.name} (${node.protocol})`, value: node.id }));
  const userOptions = users.filter(user => !user.removal_id).map(user => ({ label: user.display_name || user.username, value: user.username }));
  const nodeOptions = nodes.filter(node => !node.removal_id).map(node => ({ label: `${node.name} (${node.protocol})`, value: node.id }));
  const planOptions = plans.map(plan => ({ label: plan.name, value: plan.id })), serverName = (id: string) => servers.find(server => server.id === id)?.name ?? "未知服务器";
  const selectedServerKind: ServerKind = servers.find(server => server.id === nodeForm.server_id)?.server_kind ?? "direct";
  const availableCreationProfiles = (creationMetadata?.profiles ?? []).filter(option => managedProtocolProfiles.includes(option.profile)
    && option.allowed_server_kinds.includes(selectedServerKind));
  const selectedCreationProfile = availableCreationProfiles.find(option => option.profile === nodeForm.protocol_profile);
  const usedCamouflagePoolIds = new Set(nodes.filter(node => node.server_id === nodeForm.server_id && node.camouflage_pool_id).map(node => node.camouflage_pool_id));
  const selectedCamouflagePool = camouflageCatalog?.pools.find(pool => pool.id === nodeForm.camouflage_pool_id);
  const disabled = !!saving;
  function patchNode(change: Partial<typeof nodeForm>) { setNodeForm(previous => ({ ...previous, ...change })); }
  function selectNodeServer(server_id: string) {
    const kind = servers.find(server => server.id === server_id)?.server_kind ?? "direct";
    const allowed = (creationMetadata?.profiles ?? []).filter(option => managedProtocolProfiles.includes(option.profile) && option.allowed_server_kinds.includes(kind));
    setNodeForm(previous => {
      const option = allowed.find(item => item.profile === previous.protocol_profile) ?? allowed[0];
      const next = option && option.profile !== previous.protocol_profile ? profileNodeForm({ ...previous, server_id }, option, presets) : { ...previous, server_id };
      return nodes.some(node => node.server_id === server_id && node.camouflage_pool_id === next.camouflage_pool_id)
        ? { ...next, camouflage_pool_id: "", camouflage_sni: "" } : next;
    });
  }
  function selectNodeProfile(protocol_profile: ManagedProtocolProfile) {
    const option = availableCreationProfiles.find(item => item.profile === protocol_profile);
    if (option) setNodeForm(previous => profileNodeForm(previous, option, presets));
  }
  function selectCamouflagePool(camouflage_pool_id: string) {
    const pool = camouflageCatalog?.pools.find(item => item.id === camouflage_pool_id);
    patchNode({ camouflage_pool_id: pool?.id ?? "", camouflage_sni: pool?.server_name ?? "" });
  }
  function patchPlan(change: Partial<typeof planForm>) { setPlanForm(previous => ({ ...previous, ...change })); }
  function patchAssignment(change: Partial<typeof assignForm>) { setAssignForm(previous => ({ ...previous, ...change })); }
  // Each subscriber's token, credentials and request epochs live in a keyed child.
  // Selecting another subscriber destroys those values before any new request starts.
  return <main data-testid="subscriptions-view"><Flex vertical gap="large">
    <Flex justify="space-between" align="center" wrap gap="middle"><div><Typography.Text type="secondary">订阅</Typography.Text><Typography.Title level={2}>订阅目录与用户绑定</Typography.Title></div><Button icon={<ReloadOutlined />} aria-label="刷新订阅目录" loading={loading} disabled={loading || disabled} onClick={() => void refresh()} /></Flex>
    {error && <Alert type="error" title={zhMessage(error)} showIcon />}{success && <Alert type="success" title={success} showIcon />}
    <Collapse activeKey={externalOpen ? ["external"] : []} onChange={keys => setExternalOpen(keys.includes("external"))} destroyOnHidden items={[{
      key: "external", label: "管理外部订阅",
      children: <ExternalSubscriptionsPanel users={users} active={externalOpen} onUpdated={() => void refresh()} />,
    }]} />
    <Row gutter={[24, 24]}>
      <Col xs={24} xl={11}><Card title="工作流程" extra={loading && <Spin size="small" />}><Typography.Paragraph type="secondary">用户、托管节点、套餐与分配</Typography.Paragraph>
        <Tabs activeKey={activeTab} onChange={setActiveTab} items={[
          { key: "users", label: "用户", children: <Form layout="vertical" preserve={false} disabled={disabled} onFinish={submitUser}>
            <Form.Item label="用户名"><Input aria-label="用户名" value={userForm.username} onChange={event => setUserForm(previous => ({ ...previous, username: event.target.value }))} /></Form.Item>
            <Form.Item label="角色"><Select aria-label="角色" value={userForm.role} options={[{ label: "用户", value: "user" }, { label: "管理员", value: "admin" }]} onChange={role => setUserForm(previous => ({ ...previous, role }))} /></Form.Item>
            <Form.Item label="电子邮箱"><Input aria-label="电子邮箱" value={userForm.email} onChange={event => setUserForm(previous => ({ ...previous, email: event.target.value }))} /></Form.Item>
            <Form.Item label="显示名称"><Input aria-label="显示名称" value={userForm.display_name} onChange={event => setUserForm(previous => ({ ...previous, display_name: event.target.value }))} /></Form.Item>
            <Form.Item label="启用用户"><Switch aria-label="启用用户" checked={userForm.is_active} onChange={is_active => setUserForm(previous => ({ ...previous, is_active }))} /></Form.Item>
            <Button type="primary" htmlType="submit" icon={<PlusOutlined />} aria-label="创建用户" loading={saving === "user"}>创建用户</Button>
          </Form> },
          { key: "nodes", label: "节点", children: <Form layout="vertical" preserve={false} disabled={disabled} onFinish={submitNode}>
            <Form.Item label="名称"><Input aria-label="名称" value={nodeForm.name} onChange={event => patchNode({ name: event.target.value })} /></Form.Item>
            <Form.Item label="服务器"><Select aria-label="服务器" value={nodeForm.server_id || undefined}
              options={servers.map(server => ({ label: `${server.name}（${creationMetadata?.server_kinds[server.server_kind ?? "direct"] ?? "公网直连"}）`, value: server.id }))}
              disabled={disabled || !servers.length} onChange={selectNodeServer} /></Form.Item>
            {nodeForm.server_id && <Typography.Paragraph type="secondary">服务器类型：{creationMetadata?.server_kinds[selectedServerKind] ?? "公网直连"}</Typography.Paragraph>}
            <Form.Item label="协议档案" required><Select aria-label="协议档案" value={selectedCreationProfile?.profile}
              options={availableCreationProfiles.map(option => ({ label: option.label, value: option.profile }))}
              disabled={disabled || !availableCreationProfiles.length} onChange={selectNodeProfile} /></Form.Item>
            {!availableCreationProfiles.length && creationMetadata && <Alert type="error" showIcon title="这类服务器没有可用的协议档案。" />}
            {selectedCreationProfile && <Alert type="info" showIcon style={{ marginBottom: 16 }} title={selectedCreationProfile.label}
              description={`${selectedCreationProfile.description}${selectedCreationProfile.fixed_port ? ` 服务端口固定为 ${selectedCreationProfile.fixed_port}。` : ""}`} />}
            {selectedCreationProfile?.requires_camouflage_pool && <Row gutter={16}>
              <Col xs={24} sm={15}><Form.Item label="伪装池" required><Select aria-label="伪装池" showSearch optionFilterProp="label" value={nodeForm.camouflage_pool_id || undefined}
                placeholder="选择已验证的伪装池" options={(camouflageCatalog?.pools ?? []).map(pool => ({ value: pool.id,
                  label: `${pool.region_label} · ${pool.label} · ${pool.server_name}${usedCamouflagePoolIds.has(pool.id) ? "（已在此服务器使用）" : ""}`,
                  disabled: usedCamouflagePoolIds.has(pool.id) }))} onChange={selectCamouflagePool} /></Form.Item></Col>
              <Col xs={24} sm={9}><Form.Item label="伪装 SNI"><Input aria-label="伪装 SNI" readOnly value={selectedCamouflagePool?.server_name ?? ""} /></Form.Item></Col>
              {camouflageCatalog && <Col span={24}><Alert type="info" showIcon style={{ marginBottom: 16 }} title="伪装池目录会随测量结果变化"
                description={camouflageCatalog.measurement_notice} /></Col>}
            </Row>}
            {selectedCreationProfile?.profile === "mieru" && <>
              <Row gutter={16}><Col xs={24} sm={14}><Form.Item label="国内入口 IP" required><Input aria-label="国内入口 IP" maxLength={255} value={nodeForm.domestic_entry_ip}
                onChange={event => patchNode({ domestic_entry_ip: event.target.value })} /></Form.Item></Col>
                <Col xs={24} sm={10}><Form.Item label="国内入口端口" required><StrictInputNumber aria-label="国内入口端口" allowEmpty aria-valuemin={1} aria-valuemax={65535}
                  value={nodeForm.domestic_entry_port} onChange={domestic_entry_port => patchNode({ domestic_entry_port })} style={{ width: "100%" }} /></Form.Item></Col></Row>
              <Form.Item label="端口映射模式" required><Select aria-label="端口映射模式" value={nodeForm.mieru_port_mapping_mode}
                options={(Object.entries(creationMetadata?.mieru_mapping_modes ?? {}) as Array<[MieruPortMappingMode, string]>).map(([value, label]) => ({ value, label }))}
                onChange={mieru_port_mapping_mode => patchNode({ mieru_port_mapping_mode, ix_port: mieru_port_mapping_mode === "one-to-one" ? null : nodeForm.ix_port })} /></Form.Item>
              {nodeForm.mieru_port_mapping_mode === "one-to-one" ? <>
                <Form.Item label="IX 端口（一一对应）"><StrictInputNumber aria-label="IX 端口（一一对应）" disabled value={nodeForm.domestic_entry_port} onChange={() => {}} style={{ width: "100%" }} /></Form.Item>
                <Alert type="info" showIcon style={{ marginBottom: 16 }} title={creationMetadata?.mieru_mapping_modes["one-to-one"] ?? "IX 端口与国内入口端口一致。"} />
              </> : <>
                <Form.Item label="IX 端口" required><StrictInputNumber aria-label="IX 端口" allowEmpty aria-valuemin={1} aria-valuemax={65535} value={nodeForm.ix_port}
                  onChange={ix_port => patchNode({ ix_port })} style={{ width: "100%" }} /></Form.Item>
                <Alert type="warning" showIcon style={{ marginBottom: 16 }} title="请完成国内入口端口转发"
                  description={creationMetadata?.mieru_mapping_modes.manual ?? "手动填写 IX 端口后，还需在国内入口将流量转发到该 IX 端口。"} />
              </>}
            </>}
            {selectedCreationProfile?.profile === "socks5" && (selectedCreationProfile.warning_server_kinds ?? []).includes(selectedServerKind) && <Alert type="warning" showIcon style={{ marginBottom: 16 }}
              title={selectedCreationProfile.warning ?? "公网直连服务器使用 SOCKS5 极度不推荐，除非您知道您要做什么。"} />}
            <Form.Item label="类型"><Input aria-label="类型" value="物理节点（受管运行时）" readOnly /></Form.Item>
            {([{ key: "inbound_tag", label: "入站标签" }, { key: "routed_outbound_tag", label: "出站标签" }, { key: "routed_rule_marktag", label: "路由标记" }, { key: "tag", label: "主要标签" }, { key: "tagsText", label: "标签" }] as const).map(field => <Form.Item key={field.key} label={field.label}><Input aria-label={field.label} value={nodeForm[field.key]} onChange={event => patchNode({ [field.key]: event.target.value })} /></Form.Item>)}
            {nodeForm.node_type === "routed" && <><Form.Item label="父节点"><Select aria-label="父节点" allowClear value={nodeForm.parent_id ?? undefined} options={nodes.filter(node => !node.removal_id && node.server_id === nodeForm.server_id && node.inbound_tag === nodeForm.inbound_tag && node.protocol === nodeForm.protocol).map(node => ({ label: node.name, value: node.id }))} onChange={value => patchNode({ parent_id: value ?? null })} /></Form.Item><Form.Item label="目标节点"><Select aria-label="目标节点" allowClear value={nodeForm.target_node_id ?? undefined} options={nodeOptions} onChange={value => patchNode({ target_node_id: value ?? null })} /></Form.Item></>}
            <Form.Item label="客户端模板"><Input.TextArea aria-label="客户端模板" rows={5} value={nodeForm.clientTemplateText} onChange={event => patchNode({ clientTemplateText: event.target.value })} /></Form.Item>
            <Form.Item label="节点配置"><Input.TextArea aria-label="节点配置" rows={4} value={nodeForm.configText} onChange={event => patchNode({ configText: event.target.value })} /></Form.Item>
            <Form.Item label="已启用"><Switch aria-label="已启用" checked={nodeForm.enabled} onChange={enabled => patchNode({ enabled })} /></Form.Item>
            <Button type="primary" htmlType="submit" icon={<PlusOutlined />} aria-label="创建节点" disabled={disabled || !servers.length || !selectedCreationProfile} loading={saving === "node"}>创建节点</Button>
          </Form> },
          { key: "plans", label: "套餐", children: <Form layout="vertical" preserve={false} disabled={disabled} onFinish={submitPlan}>
            <Form.Item label="名称"><Input aria-label="名称" value={planForm.name} onChange={event => patchPlan({ name: event.target.value })} /></Form.Item>
            <Form.Item label="流量计费方式"><Select aria-label="流量计费方式" value={planForm.traffic_mode} options={[{ label: "单向计费（×1）", value: "oneway" }, { label: "双向计费（×2）", value: "twoway" }]} onChange={traffic_mode => patchPlan({ traffic_mode })} /></Form.Item>
            <Form.Item label="说明"><Input.TextArea aria-label="说明" rows={2} value={planForm.description} onChange={event => patchPlan({ description: event.target.value })} /></Form.Item>
            <Row gutter={16}>{([{ key: "traffic_limit_gb", label: "流量（GB）", min: 1 }, { key: "cycle_days", label: "周期（天）", min: 1 }, { key: "speed_limit_mbps", label: "速度（Mbps）", min: 0 }, { key: "device_limit", label: "并发连接数", min: 0 }] as const).map(field => <Col xs={24} sm={12} key={field.key}><Form.Item label={field.label}><StrictInputNumber aria-label={field.label} aria-valuemin={field.min} value={planForm[field.key]} onChange={value => patchPlan({ [field.key]: value ?? Number.NaN })} /></Form.Item></Col>)}</Row>
            <Form.Item label="按月重置"><Switch aria-label="按月重置" checked={planForm.is_reset} onChange={is_reset => patchPlan({ is_reset })} /></Form.Item>
            <Form.Item label="重置日"><StrictInputNumber aria-label="重置日" aria-valuemin={1} aria-valuemax={31} disabled={disabled || !planForm.is_reset} value={planForm.reset_day} onChange={value => patchPlan({ reset_day: value ?? Number.NaN })} /></Form.Item>
            {(["clash", "surge"] as const).map(format => <Form.Item key={format} label={`${format === "clash" ? "Clash" : "Surge"} 模板`}><Select aria-label={`${format === "clash" ? "Clash" : "Surge"} 模板`} allowClear value={planForm[`${format}_template_id`] ?? undefined} options={templates.filter(template => template.format === format).map(template => ({ label: template.name, value: template.id }))} onChange={value => patchPlan({ [`${format}_template_id`]: value ?? null })} /></Form.Item>)}
            <Form.Item label="节点"><Select aria-label="节点" mode="multiple" options={nodeOptions} value={planForm.node_ids} onChange={node_ids => patchPlan({ node_ids })} /></Form.Item>
            <PlanNodeAliases nodes={planForm.node_ids.map(id => ({ id, name: nodes.find(node => node.id === id)?.name ?? id }))} value={planForm.node_name_overrides} onChange={node_name_overrides => patchPlan({ node_name_overrides })} enabled={planForm.node_name_override_enabled} onEnabledChange={node_name_override_enabled => patchPlan({ node_name_override_enabled })} onValid={setAliasesValid} disabled={disabled} />
            <AutoSpeedRuleEditor value={planForm.auto_speed_rules} onChange={auto_speed_rules => patchPlan({ auto_speed_rules })} onValid={setRulesValid} disabled={disabled} />
            <Button type="primary" htmlType="submit" icon={<PlusOutlined />} aria-label="创建套餐" loading={saving === "plan"} disabled={disabled || !aliasesValid || !rulesValid}>创建套餐</Button>
          </Form> },
          { key: "assign", label: "分配", children: <Form layout="vertical" preserve={false} disabled={disabled} onFinish={submitAssignment}>
            <Form.Item label="用户"><Select aria-label="套餐分配用户" value={assignForm.username || undefined} options={userOptions} onChange={selectUser} disabled={disabled || !userOptions.length} /></Form.Item>
            <Form.Item label="套餐"><Select aria-label="套餐" value={assignForm.plan_id || undefined} options={planOptions} onChange={plan_id => patchAssignment({ plan_id })} disabled={disabled || !planOptions.length} /></Form.Item>
            <Form.Item label="开始日期"><Input aria-label="开始日期" type="date" value={assignForm.start_date} onChange={event => patchAssignment({ start_date: event.target.value })} /></Form.Item>
            <Form.Item label="到期日期"><Input aria-label="到期日期" type="date" value={assignForm.expire_date} onChange={event => patchAssignment({ expire_date: event.target.value })} /></Form.Item>
            <Form.Item label="同步真实节点账号"><Switch aria-label="同步真实节点账号" checked={assignForm.queue_agent_commands} onChange={queue_agent_commands => patchAssignment({ queue_agent_commands })} /></Form.Item>
            {assignForm.queue_agent_commands && <Alert type="info" title="默认同步到 Agent；Mihomo 与旧 Xray 运行态会按节点类型分别更新。" showIcon />}
            <Form.Item label="命令超时（毫秒）"><StrictInputNumber aria-label="命令超时（毫秒）" aria-valuemin={1000} aria-valuemax={300000} value={assignForm.command_timeout_ms} onChange={value => patchAssignment({ command_timeout_ms: value ?? Number.NaN })} /></Form.Item>
            <Button type="primary" htmlType="submit" aria-label="分配套餐" aria-busy={saving === "assign"} loading={saving === "assign"} disabled={disabled || !userOptions.length || !planOptions.length}>分配套餐</Button>
          </Form> },
        ]} />
      </Card></Col>
      <Col xs={24} xl={13}><Flex vertical gap="large">
        <Card title="目录状态" extra={<Tag color="success">免费版</Tag>}><Typography.Paragraph type="secondary">{users.length} 位用户，{plans.length} 个套餐</Typography.Paragraph>
          <Form.Item label="用户"><Select aria-label="订阅用户" value={assignForm.username || undefined} options={userOptions} disabled={!userOptions.length} onChange={selectUser} /></Form.Item>
          {assignForm.username ? <SubscriptionUserPanel key={assignForm.username} username={assignForm.username} user={selectedUser} servers={servers} assignment={lastAssignment} externalToken={updatedToken} canShare={!!temporaryNodes.length} onShare={() => setShareOpen(true)} onRefresh={() => void refresh()} onUserUpdated={value => setUsers(previous => previous.map(user => user.username === value.username ? value : user))} /> : <Empty description="请先创建用户，再管理订阅访问。" />}
        </Card>
        <Card title="订阅目录导入与导出"><Flex vertical gap="middle">
          <Flex gap="large" wrap><Form.Item label="导出凭据"><Switch aria-label="导出凭据" checked={catalogForm.includeCredentials} disabled={disabled} onChange={includeCredentials => setCatalogForm(previous => ({ ...previous, includeCredentials }))} /></Form.Item><Form.Item label="导入凭据"><Switch aria-label="导入凭据" checked={catalogForm.importCredentials} disabled={disabled} onChange={importCredentials => setCatalogForm(previous => ({ ...previous, importCredentials }))} /></Form.Item></Flex>
          {(catalogForm.includeCredentials || catalogForm.importCredentials) && <Alert type="warning" title="目录凭据属于敏感信息，请妥善保管导出文件。导入凭据可能覆盖现有访问数据。" showIcon />}
          <Flex gap="small" wrap><Button aria-label="导出" aria-busy={saving === "export"} loading={saving === "export"} disabled={disabled} onClick={exportCatalog}>导出</Button><Button type="primary" aria-label="导入" aria-busy={saving === "import"} loading={saving === "import"} disabled={disabled || !catalogForm.catalogText.trim()} onClick={() => setConfirmImport(true)}>导入</Button><Button disabled={disabled} onClick={() => setLegacyOpen(true)}>MMWX 身份</Button><Button disabled={disabled || !catalogForm.catalogText} onClick={() => setCatalogForm(previous => ({ ...previous, catalogText: "" }))}>清空目录</Button></Flex>
          <Form.Item label="订阅目录 JSON"><Input.TextArea aria-label="订阅目录 JSON" rows={8} disabled={disabled} value={catalogForm.catalogText} onChange={event => setCatalogForm(previous => ({ ...previous, catalogText: event.target.value }))} /></Form.Item>
          <Form.Item label="服务器映射 JSON"><Input.TextArea aria-label="服务器映射 JSON" rows={3} disabled={disabled} value={catalogForm.serverMapText} onChange={event => setCatalogForm(previous => ({ ...previous, serverMapText: event.target.value }))} /></Form.Item>
          {catalogImport && <><Flex gap="small" wrap><Tag>用户 {catalogImport.summary.created_users} / {catalogImport.summary.updated_users}</Tag><Tag>节点 {catalogImport.summary.created_nodes} / {catalogImport.summary.updated_nodes}</Tag><Tag>套餐 {catalogImport.summary.created_plans} / {catalogImport.summary.updated_plans}</Tag><Tag>凭据 {catalogImport.summary.imported_credentials}</Tag></Flex>{catalogImport.summary.warnings.map(warning => <Alert key={warning} type="warning" title={zhMessage(warning)} showIcon />)}</>}
        </Flex></Card>
        <Card title="私有路由" extra={<Flex gap="small"><Tag color={privateRoutes?.policy.enabled ? "success" : "default"}>{privateRoutes?.policy.enabled ? "已启用" : "已停用"}</Tag><Button aria-label="编辑私有路由策略" icon={<SettingOutlined />} disabled={!privateRoutes || loading || disabled} onClick={() => setPrivatePolicyOpen(true)} /></Flex>}><Flex vertical gap="small">
          {!privateRoutes?.nodes.length && <Empty description="暂无私有路由。" />}{privateRoutes?.nodes.map(item => <Card key={item.id} size="small" title={item.name} extra={<Tag color={item.status === "active" ? "success" : item.status === "failed" ? "error" : "warning"}>{zhStatus(item.status)}</Tag>}><Typography.Text>{item.username} - {item.parent_name} 至 {item.target_name}</Typography.Text>{item.last_error && <Alert type="error" title={zhMessage(item.last_error)} showIcon />}</Card>)}
        </Flex></Card>
        <Card title="注册" extra={<Button aria-label="管理注册邀请" icon={<UserAddOutlined />} disabled={!plans.length} onClick={() => setInvitationsOpen(true)} />}><Tag>{plans.length} 个套餐</Tag></Card>
        <Card title="临时链接"><Flex vertical gap="small">{!temporary.length && <Empty description="暂无临时链接。" />}{temporary.map(item => <Card key={item.id} size="small" title={item.label} extra={<Flex gap="small" wrap><Button icon={<CopyOutlined />} aria-label={`复制临时链接 ${item.label}`} onClick={() => void copyTemporary(item)} /><Button icon={<LinkOutlined />} aria-label={`撤销临时链接 ${item.label}`} disabled={disabled} loading={saving === `temporary:${item.id}`} onClick={() => setConfirmTemporary(item)} /><Tag color={item.status === "active" ? "success" : item.status === "expired" ? "warning" : "error"}>{zhStatus(item.status)}</Tag></Flex>}><Typography.Text>{item.username} - 已下载 {item.access_count}/{item.max_access} 次 - 有效期至 {new Date(item.expires_at).toLocaleString("zh-CN")}</Typography.Text></Card>)}</Flex></Card>
        <Card title="订阅配置"><Flex vertical gap="small">{!profiles.length && <Empty description="暂无已导入的订阅配置。" />}{profiles.map(item => <Card key={item.id} size="small" title={item.name} extra={<Flex gap="small"><Button icon={<SettingOutlined />} aria-label={`编辑订阅配置 ${item.name}`} onClick={() => setProfileDialog(item)} /><Tag color={item.enabled ? "success" : "warning"}>{item.enabled ? "已启用" : "待设置"}</Tag></Flex>}><Typography.Text>{item.assigned_usernames.length} 位用户 - {profileSourceLabel(item.source_type)}</Typography.Text></Card>)}</Flex></Card>
        <Card title="用户"><Flex vertical gap="small">{!users.length && <Empty description="暂无用户。" />}{users.map(user => <Card key={user.username} size="small" title={user.display_name || user.username}><Typography.Paragraph type="secondary">{user.username}</Typography.Paragraph><Flex gap="small" wrap>
          {!user.removal_id ? <>{shortLinksEnabled && <Button icon={<LinkOutlined />} aria-label={`编辑 ${user.username} 的订阅短码`} onClick={() => setShortCode({ username: user.username, open: true })} />}<Button icon={<SettingOutlined />} aria-label={`编辑 ${user.username} 的订阅 IP 访问限制`} onClick={() => setIpPolicy({ username: user.username, open: true })} /><Button icon={<KeyOutlined />} aria-label={`${user.username} 的登录设置`} onClick={() => setLoginDialog({ username: user.username, open: true })} /><Button icon={<EditOutlined />} aria-label={`编辑用户 ${user.username}`} onClick={() => manageUser(user, "edit")} /><Button danger icon={<DeleteOutlined />} aria-label={`移除用户 ${user.username}`} disabled={user.role === "admin"} onClick={() => manageUser(user, "remove")} /></> : <Button icon={<SyncOutlined />} aria-label={`查看 ${user.username} 的移除状态`} onClick={() => manageUser(user, "remove")} />}
          {user.current_plan_id && !user.removal_id && <Button icon={<LinkOutlined />} aria-label={`取消 ${user.username} 的套餐分配`} onClick={() => setPlanDialog({ id: user.username, mode: "unassign", open: true })} />}
          <Tag color={user.current_plan_id ? "processing" : "default"}>{user.removal_id ? "移除中" : !user.is_active ? "已停用" : formatDate(user.plan_expires_at)}</Tag>
        </Flex></Card>)}</Flex></Card>
        <Card title="套餐"><Flex vertical gap="small">{!plans.length && <Empty description="暂无套餐。" />}{plans.map(plan => <Card key={plan.id} size="small" title={plan.name} extra={<Flex gap="small"><Button icon={<EditOutlined />} aria-label={`编辑套餐 ${plan.name}`} onClick={() => setPlanDialog({ id: plan.id, mode: "edit", open: true })} /><Button danger icon={<DeleteOutlined />} aria-label={`移除套餐 ${plan.name}`} onClick={() => setPlanDialog({ id: plan.id, mode: "remove", open: true })} /></Flex>}><Typography.Text>{plan.traffic_limit_gb.toFixed(plan.traffic_limit_gb >= 10 ? 0 : 1)} GB / {plan.cycle_days} 天</Typography.Text> <Tag>{plan.node_ids.length} 个节点</Tag></Card>)}</Flex></Card>
        <Card title="节点"><Flex vertical gap="small">{!nodes.length && <Empty description="暂无节点。" />}{nodes.map(node => <Card key={node.id} size="small" title={node.name}><Typography.Paragraph type="secondary">{serverName(node.server_id)}</Typography.Paragraph><Flex gap="small">{!node.removal_id ? <><Button icon={<EditOutlined />} aria-label={`编辑节点 ${node.name}`} onClick={() => setNodeDialog({ id: node.id, mode: "edit", open: true })} /><Button danger icon={<DeleteOutlined />} aria-label={`移除节点 ${node.name}`} onClick={() => setNodeDialog({ id: node.id, mode: "remove", open: true })} /></> : <Button icon={<SyncOutlined />} aria-label={`节点移除状态 ${node.name}`} onClick={() => setNodeDialog({ id: node.id, mode: "remove", open: true })} />}<Tag color={node.enabled && !node.removal_id ? "success" : "warning"}>{node.removal_id ? "移除中" : zhStatus(node.node_type)}</Tag></Flex></Card>)}</Flex></Card>
        {lastAssignment && <Card title="上次分配"><Flex vertical gap="small"><Typography.Text>{lastAssignment.user.username} → {lastAssignment.plan.name}</Typography.Text><Flex gap="small"><Tag>{lastAssignment.provisioning_batches.length} 个批次</Tag><Tag>{lastAssignment.commands.length} 条命令</Tag></Flex>{lastAssignment.warnings.map(warning => <Alert key={warning} type="warning" title={zhMessage(warning)} showIcon />)}<Input.TextArea aria-label="配置下发批次" value={JSON.stringify(lastAssignment.provisioning_batches, null, 2)} readOnly rows={8} /><Typography.Text type="secondary">批次中可能包含凭据。命令进入队列并不代表 Agent 已应用。</Typography.Text></Flex></Card>}
      </Flex></Col>
    </Row>
    <PlanManagementDialog {...planDialog} nodes={nodes} onOpenChange={open => setPlanDialog(previous => ({ ...previous, open }))} onUpdated={() => void refresh()} />
    <UserManagementDialog {...userDialog} nodes={nodes} onOpenChange={open => setUserDialog(previous => ({ ...previous, open }))} onUpdated={() => void refresh()} />
    <NodeManagementDialog {...nodeDialog} nodes={nodes} onOpenChange={open => setNodeDialog(previous => ({ ...previous, open }))} onUpdated={() => void refresh()} />
    <UserLoginDialog {...loginDialog} onOpenChange={open => setLoginDialog(previous => ({ ...previous, open }))} />
    <SubscriptionShortCodeDialog {...shortCode} onOpenChange={open => setShortCode(previous => ({ ...previous, open }))} onSaved={value => { if (selectedUsername.current === value.username) setUpdatedToken(value); }} />
    <SubscriptionIpPolicyDialog {...ipPolicy} onOpenChange={open => setIpPolicy(previous => ({ ...previous, open }))} />
    <LegacyMMWXImportDialog open={legacyOpen} plans={plans} onOpenChange={setLegacyOpen} onImported={() => void refresh()} />
    <SubscriptionProfileDialog open={!!profileDialog} profile={profileDialog} nodes={nodes} users={users} templates={templates} onOpenChange={open => !open && setProfileDialog(null)} onSaved={() => void refresh()} />
    <RegistrationInvitationsDialog open={invitationsOpen} plans={plans} onOpenChange={setInvitationsOpen} />
    <TemporarySubscriptionDialog open={shareOpen} username={assignForm.username} nodes={temporaryNodes} onOpenChange={setShareOpen} onCreated={value => { setTemporary(previous => [value, ...previous.filter(item => item.id !== value.id)]); setSuccess(`已为 ${value.username} 创建临时链接。`); }} />
    <PrivateRoutedPolicyDialog open={privatePolicyOpen} policy={privateRoutes?.policy ?? null} onOpenChange={setPrivatePolicyOpen} onSaved={() => void refresh()} />
    <Modal open={!!confirmTemporary} title="撤销临时链接？" destroyOnHidden mask={{ closable: !disabled }} closable={!disabled} keyboard={!disabled} onCancel={() => !disabled && setConfirmTemporary(null)} okText="撤销" okButtonProps={{ "aria-label": "撤销", "aria-busy": !!confirmTemporary && saving === `temporary:${confirmTemporary.id}`, danger: true }} confirmLoading={!!confirmTemporary && saving === `temporary:${confirmTemporary.id}`} cancelButtonProps={{ disabled }} onOk={() => confirmTemporary && revokeTemporary(confirmTemporary)}><Typography.Paragraph>{confirmTemporary?.label}：将停止后续订阅下载。此操作不会撤回已下载的凭据。</Typography.Paragraph></Modal>
    <Modal open={confirmImport} title="导入订阅目录？" destroyOnHidden mask={{ closable: !disabled }} closable={!disabled} keyboard={!disabled} onCancel={() => !disabled && setConfirmImport(false)} okText="导入订阅目录" okButtonProps={{ "aria-label": "导入订阅目录", "aria-busy": saving === "import" }} confirmLoading={saving === "import"} cancelButtonProps={{ disabled }} onOk={importCatalog}><Typography.Paragraph>导入会按服务器映射创建或更新用户、节点及套餐。{catalogForm.importCredentials ? "凭据也会一并导入。" : "不会导入凭据。"}</Typography.Paragraph></Modal>
  </Flex></main>;
}

interface SubscriptionUserPanelProps { username: string; user: ProductUser | undefined; servers: ServerSummary[]; assignment: SubscriptionPlanAssignResponse | null; externalToken: ProductUserSubscriptionToken | null; canShare: boolean; onShare: () => void; onRefresh: () => void; onUserUpdated: (user: ProductUser) => void }
function SubscriptionUserPanel({ username, user, servers, assignment, externalToken, canShare, onShare, onRefresh, onUserUpdated }: SubscriptionUserPanelProps) {
  const [token, setToken] = useState<ProductUserSubscriptionToken | null>(null), [credentials, setCredentials] = useState<SubscriptionCredential[]>([]), [traffic, setTraffic] = useState<ProductUserTrafficResponse | null>(null), [quota, setQuota] = useState<SubscriptionQuotaStatus | null>(null), [dueReset, setDueReset] = useState<SubscriptionDueTrafficResetResponse | null>(null);
  const [format, setFormat] = useState<SubscriptionClientFormat>("clash"), [preview, setPreview] = useState<SubscriptionFormatPreview | null>(null), [formatNode, setFormatNode] = useState<string | null>(null), [previewBusy, setPreviewBusy] = useState(false), [previewError, setPreviewError] = useState("");
  const [busy, setBusy] = useState(""), [error, setError] = useState(""), [success, setSuccess] = useState(""), [confirmation, setConfirmation] = useState<"token" | "traffic" | "due" | null>(null);
  const version = useRef(0), previewVersion = useRef(0), actionBusy = useRef(false), refreshedAssignment = useRef<SubscriptionPlanAssignResponse | null>(null);
  useEffect(() => () => { ++version.current; ++previewVersion.current; }, []);
  useEffect(() => { if (externalToken?.username === username) setToken(externalToken); }, [externalToken, username]);
  useEffect(() => {
    const run = ++previewVersion.current; setPreview(null); setPreviewError(""); setFormatNode(null); setPreviewBusy(false);
    if (!token) return;
    setPreviewBusy(true);
    void getSubscriptionFormatPreview(username, format).then(value => { if (run === previewVersion.current) { setPreview(value); setFormatNode(value.nodes.find(node => node.available)?.node_id ?? null); } }).catch(failure => { if (run === previewVersion.current) setPreviewError(readableError(failure)); }).finally(() => { if (run === previewVersion.current) setPreviewBusy(false); });
    return () => { ++previewVersion.current; };
  }, [token, format, assignment, username]);
  async function perform(action: string, task: (current: () => boolean) => Promise<void>) {
    if (actionBusy.current) return; actionBusy.current = true;
    const run = ++version.current; setBusy(action); setError(""); setSuccess(""); const current = () => run === version.current;
    try { await task(current); } catch (failure) { if (current()) setError(readableError(failure)); }
    finally { if (current()) { actionBusy.current = false; setBusy(""); } }
  }
  async function fetchUsage(current: () => boolean, includeCredentials = false) {
    const [trafficValue, quotaValue, credentialsValue] = await Promise.all([getProductUserTraffic(username), getProductUserQuota(username), includeCredentials ? listProductUserCredentials(username) : Promise.resolve(null)]);
    if (!current()) return; setTraffic(trafficValue); setQuota(quotaValue.quota); if (credentialsValue) setCredentials(credentialsValue.credentials);
  }
  useEffect(() => {
    if (busy || !assignment || assignment.user.username !== username || refreshedAssignment.current === assignment) return;
    refreshedAssignment.current = assignment;
    setCredentials([]); setTraffic(null); setQuota(null);
    void perform("assignment", async current => { await fetchUsage(current, true); });
  }, [assignment, busy, username]);
  function createToken(reset = false) { void perform("token", async current => { const value = await (reset ? resetProductUserSubscriptionToken : createProductUserSubscriptionToken)(username); if (!current()) return; setToken(value.subscription); setConfirmation(null); setSuccess(reset ? `已重置 ${username} 的订阅链接。` : `${username} 的订阅链接已就绪。`); }); }
  function loadCredentials() { void perform("credentials", async current => { const value = await listProductUserCredentials(username); if (!current()) return; setCredentials(value.credentials); setSuccess(`已加载 ${value.credentials.length} 份凭据。`); }); }
  function loadTraffic() { void perform("traffic", async current => { const value = await getProductUserTraffic(username); if (!current()) return; setTraffic(value); setSuccess(`已加载原始流量 ${formatBytes(value.total)} / 计费流量 ${formatBytes(value.charged_usage_bytes)}。`); }); }
  function loadQuota() { void perform("quota", async current => { const value = await getProductUserQuota(username); if (!current()) return; setQuota(value.quota); setSuccess(`已加载剩余流量 ${formatBytes(value.quota.remaining_bytes)}。`); }); }
  function resetTraffic() { void perform("reset-traffic", async current => { const value = await resetProductUserTraffic(username); if (!current()) return; setQuota(value.quota); await fetchUsage(current); if (!current()) return; setConfirmation(null); setSuccess(`已重置 ${username} 的流量。`); onRefresh(); }); }
  function resetDue() { void perform("reset-due", async current => { const value = await resetDueProductUserTraffic({}); if (!current()) return; setDueReset(value); await fetchUsage(current); if (!current()) return; setConfirmation(null); setSuccess(`已重置 ${value.summary.reset_users} 位到期用户的流量。`); onRefresh(); }); }
  const availableNodes = (preview?.nodes ?? []).filter(node => node.available);
  let formatUrl = "";
  if (token && availableNodes.length) {
    try { const value = new URL(token.subscription_url, window.location.origin); if (format !== "clash") value.searchParams.set("format", format); else value.searchParams.delete("format"); value.searchParams.delete("node_id"); if (format === "xray" && formatNode) value.searchParams.set("node_id", formatNode); formatUrl = value.toString(); } catch { /* A malformed server response is not offered as a download link. */ }
  }
  return <Flex vertical gap="middle">
    <Typography.Title level={4}>订阅链接</Typography.Title>
    <SubscriptionAccessPanel username={username} isActive={user?.is_active ?? false} refreshKey={assignment?.user.updated_at} onUpdated={value => { onUserUpdated(value); setQuota(null); }} />
    {error && <Alert type="error" title={zhMessage(error)} showIcon />}{success && <Alert type="success" title={success} showIcon />}
    <Flex gap="small" wrap><Button type="primary" aria-label="获取链接" aria-busy={busy === "token"} disabled={!!busy} loading={busy === "token"} onClick={() => createToken()}>获取链接</Button><Button aria-label="重置" danger disabled={!!busy} onClick={() => setConfirmation("token")}>重置</Button><Button aria-label="凭据" aria-busy={busy === "credentials"} disabled={!!busy} loading={busy === "credentials"} onClick={loadCredentials}>凭据</Button><Button aria-label="流量" aria-busy={busy === "traffic"} disabled={!!busy} loading={busy === "traffic"} onClick={loadTraffic}>流量</Button><Button aria-label="分享" disabled={!!busy || !canShare} onClick={onShare}>分享</Button><Button aria-label="配额" aria-busy={busy === "quota"} disabled={!!busy} loading={busy === "quota"} onClick={loadQuota}>配额</Button><Button disabled={!!busy} onClick={() => setConfirmation("traffic")}>重置流量</Button><Button disabled={!!busy} onClick={() => setConfirmation("due")}>重置到期流量</Button></Flex>
    {token && <><Form.Item label="订阅链接"><Input aria-label="订阅链接" readOnly value={token.subscription_url} /></Form.Item>{token.short_links_enabled && <Form.Item label="订阅短链接"><Input aria-label="订阅短链接" readOnly value={token.short_url} /></Form.Item>}
      <Form.Item label="客户端格式"><Select aria-label="客户端格式" value={format} options={formatOptions} loading={previewBusy} onChange={value => { setFormat(value); setPreview(null); setFormatNode(null); }} /></Form.Item>
      <Alert className="form-alert" type="info" showIcon title={subscriptionFormatHelp(format)} />
      <Form.Item label="对应格式链接"><Input aria-label="对应格式链接" readOnly value={formatUrl} /></Form.Item>
      {format === "xray" && <Form.Item label="Xray 节点"><Select aria-label="Xray 节点" allowClear value={formatNode ?? undefined} options={availableNodes.map(node => ({ label: node.name, value: node.node_id }))} disabled={previewBusy || !availableNodes.length} onChange={value => setFormatNode(value ?? null)} /></Form.Item>}
      {previewError && <Alert type="error" title={zhMessage(previewError)} showIcon />}{preview && <><Typography.Text type="secondary">{availableNodes.length} 个可用 / {preview.nodes.length - availableNodes.length} 个已排除</Typography.Text>{preview.warnings.map(warning => <Alert key={warning} type="warning" title={zhMessage(warning)} showIcon />)}{preview.nodes.filter(node => !node.available).map(node => <Alert key={node.node_id} type="warning" title={node.name} description={node.reason ? zhMessage(node.reason) : undefined} showIcon />)}</>}
    </>}
    {quota && <Card size="small" title="配额状态" extra={<Tag color={quotaColor(quota)}>{quotaLabel(quota)}</Tag>}><Typography.Paragraph>{formatBytes(quota.charged_usage_bytes)} / {quota.traffic_limit_bytes ? formatBytes(quota.traffic_limit_bytes) : "不限"}</Typography.Paragraph><Progress percent={Math.min(quota.percent_used, 100)} status={quotaColor(quota) === "error" ? "exception" : "normal"} /><Flex gap="small" wrap><Tag>原始下载 {formatBytes(quota.download)}</Tag><Tag>原始上传 {formatBytes(quota.upload)}</Tag><Tag>剩余 {formatBytes(quota.remaining_bytes)}</Tag><Tag>已用 {quota.percent_used.toFixed(quota.percent_used >= 10 ? 1 : 2)}%</Tag></Flex><Typography.Text type="secondary">上次重置 {formatDate(quota.last_traffic_reset_at)} / 下次重置 {formatDate(quota.next_reset_at)}</Typography.Text></Card>}
    {dueReset && <><Flex gap="small"><Tag>到期重置 {dueReset.summary.reset_users}</Tag><Tag>已检查 {dueReset.summary.checked_users}</Tag></Flex>{dueReset.summary.warnings.map(warning => <Alert key={warning} type="warning" title={zhMessage(warning)} showIcon />)}</>}
    {traffic && <Card size="small" title="流量账本（原始计数）" extra={<Tag>计费流量 {formatBytes(traffic.charged_usage_bytes)}</Tag>}><Typography.Paragraph>上传 {formatBytes(traffic.upload)} / 下载 {formatBytes(traffic.download)}</Typography.Paragraph>{traffic.entries.map(entry => <Descriptions key={`${entry.server_id}:${entry.email}`} size="small" column={1} items={[{ key: "email", label: entry.archived ? "归档用量" : entry.email, children: `${entry.server_name || servers.find(server => server.id === entry.server_id)?.name || "未知服务器"} - ${formatDate(entry.last_reported_at || entry.updated_at)}` }, { key: "value", label: "流量", children: `原始流量 ${formatBytes(entry.total)} / 计费流量 ${formatBytes(entry.charged_usage_bytes)}` }]} />)}</Card>}
    {!!credentials.length && <section aria-label="订阅凭据"><Flex vertical gap="small">{credentials.map(credential => <Card key={credential.id} size="small" title={credential.email} extra={<Tag>{credential.protocol}</Tag>}><Typography.Text>{credentialIdentifier(credential)}</Typography.Text></Card>)}</Flex></section>}
    {(token || credentials.length > 0) && <Button disabled={!!busy} onClick={() => { setToken(null); setCredentials([]); setPreview(null); }}>隐藏链接和凭据</Button>}
    <Modal open={!!confirmation} title={confirmation === "token" ? "重置订阅链接？" : confirmation === "traffic" ? "重置用户流量？" : "重置到期流量？"} destroyOnHidden mask={{ closable: !busy }} keyboard={!busy} closable={!busy} onCancel={() => !busy && setConfirmation(null)} okText="确认重置" okButtonProps={{ "aria-label": "确认重置", "aria-busy": !!busy, danger: true }} confirmLoading={!!busy} cancelButtonProps={{ disabled: !!busy }} onOk={() => confirmation === "token" ? createToken(true) : confirmation === "traffic" ? resetTraffic() : resetDue()}><Typography.Paragraph>{confirmation === "token" ? `${username} 当前的订阅链接将失效。已下载的凭据不会更换。` : confirmation === "traffic" ? `重置 ${username} 的计费流量。此页面无法撤销该操作。` : "重置所有已到流量重置日期的用户，其他用户不变。"}</Typography.Paragraph></Modal>
  </Flex>;
}
