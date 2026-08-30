import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Col, Descriptions, Divider, Empty, Flex, Form, Input, Modal, Progress, Row, Select, Spin, Switch, Tabs, Tag, Typography } from "antd";
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
import type { AutoSpeedRule } from "../../domain/auto-speed";
import type { NodeOperation } from "../../services/node-management";
import type { UserOperation } from "../../services/user-management";
import type { PlanOperation } from "../../services/plan-management";
import type { SubscriptionTemplate } from "../../domain/subscription-templates";
import type { SubscriptionProfile } from "../../domain/subscription-profiles";
import type { TemporarySubscription } from "../../domain/temporary-subscriptions";
import type { PrivateRoutedNodesResponse } from "../../domain/private-routed-nodes";
import type { ServerSummary } from "../../domain/inventory";
import type { ManagedNode, ManagedNodeCreateRequest, ManagedNodeType, ProductUser, ProductUserRole, ProductUserSubscriptionToken, ProductUserTrafficResponse, SubscriptionCatalogBundle, SubscriptionCatalogImportResponse, SubscriptionClientFormat, SubscriptionCredential, SubscriptionDueTrafficResetResponse, SubscriptionFormatPreview, SubscriptionPlan, SubscriptionPlanAssignResponse, SubscriptionPlanCreateRequest, SubscriptionQuotaStatus, SubscriptionTemplatePreset, SubscriptionTrafficMode } from "../../domain/subscriptions";
import { listSubscriptionTemplates } from "../../services/subscription-templates";
import { listSubscriptionProfiles } from "../../services/subscription-profiles";
import { deleteTemporarySubscription, listTemporarySubscriptions } from "../../services/temporary-subscriptions";
import { listPrivateRoutes } from "../../services/private-routed-nodes";
import { fetchAppMeta } from "../../services/api";
import { listServers } from "../../services/inventory";
import { assignSubscriptionPlan, createManagedNode, createManagedNodeFromPreset, createProductUser, createProductUserSubscriptionToken, createSubscriptionPlan, exportSubscriptionCatalog, getProductUserQuota, getProductUserTraffic, getSubscriptionFormatPreview, importSubscriptionCatalog, listProductUserCredentials, listManagedNodes, listProductUsers, listSubscriptionPlans, listSubscriptionTemplatePresets, resetDueProductUserTraffic, resetProductUserTraffic, resetProductUserSubscriptionToken } from "../../services/subscriptions";

const newUserForm = () => ({ username: "", email: "", display_name: "", role: "user" as ProductUserRole, is_active: true });
const newNodeForm = (server_id = "") => ({ name: "", server_id, protocol: "vless", node_type: "physical" as ManagedNodeType, parent_id: null as string | null, target_node_id: null as string | null, inbound_tag: "", routed_outbound_tag: "", routed_rule_marktag: "", tag: "", tagsText: "", enabled: true, clientTemplateText: '{\n  "id": "client-{username}",\n  "email": "{username}__default"\n}', configText: "{}" });
const newPlanForm = () => ({ name: "", description: "", traffic_limit_gb: 128, cycle_days: 30, is_reset: true, reset_day: 1, speed_limit_mbps: 0, device_limit: 0, traffic_mode: "twoway" as SubscriptionTrafficMode, node_ids: [] as string[], node_name_overrides: {} as Record<string, string>, auto_speed_rules: [] as AutoSpeedRule[], node_name_override_enabled: false, clash_template_id: null as string | null, surge_template_id: null as string | null });
const formatOptions: { label: string; value: SubscriptionClientFormat }[] = [{ label: "Clash YAML", value: "clash" }, { label: "Surge profile", value: "surge" }, { label: "sing-box JSON", value: "sing-box" }, { label: "Xray JSON", value: "xray" }, { label: "URI list", value: "uri-list" }, { label: "Base64 URI", value: "base64" }];
const blankToNull = (value: string) => value.trim() || null;
const splitCsv = (value: string) => value.split(",").map(item => item.trim()).filter(Boolean);
const readableError = (error: unknown) => error instanceof Error ? error.message : "Request failed.";
const formatDate = (value?: string | null) => value ? value.slice(0, 10) : "Not set";
function parseJsonObject(value: string, label: string): Record<string, unknown> {
  let parsed: unknown;
  try { parsed = JSON.parse(value || "{}"); } catch { throw new Error(`${label} must be valid JSON.`); }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error(`${label} must be a JSON object.`);
  return parsed as Record<string, unknown>;
}
function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"]; let active = value / 1024, index = 0;
  while (active >= 1024 && index < units.length - 1) { active /= 1024; index += 1; }
  return `${active.toFixed(active >= 10 ? 1 : 2)} ${units[index]}`;
}
function quotaLabel(quota: SubscriptionQuotaStatus) { return !quota.is_active ? "Inactive" : !quota.has_plan ? "No plan" : quota.expired ? "Expired" : quota.over_quota ? "Over quota" : quota.reset_due ? "Reset due" : "Available"; }
function quotaColor(quota: SubscriptionQuotaStatus) { return quota.over_quota || quota.expired || !quota.is_active || !quota.has_plan ? "error" : quota.reset_due ? "warning" : "success"; }
function credentialIdentifier(credential: SubscriptionCredential) { const source = credential.credential, value = source.id ?? source.password ?? source.auth ?? source.psk ?? source.pass; return typeof value === "string" ? value : credential.email; }

export default function SubscriptionsView() {
  const [servers, setServers] = useState<ServerSummary[]>([]), [users, setUsers] = useState<ProductUser[]>([]), [nodes, setNodes] = useState<ManagedNode[]>([]), [plans, setPlans] = useState<SubscriptionPlan[]>([]);
  const [templates, setTemplates] = useState<SubscriptionTemplate[]>([]), [profiles, setProfiles] = useState<SubscriptionProfile[]>([]), [temporary, setTemporary] = useState<TemporarySubscription[]>([]);
  const [privateRoutes, setPrivateRoutes] = useState<PrivateRoutedNodesResponse | null>(null), [presets, setPresets] = useState<SubscriptionTemplatePreset[]>([]), [shortLinksEnabled, setShortLinksEnabled] = useState(false);
  const [loading, setLoading] = useState(false), [saving, setSaving] = useState(""), [error, setError] = useState(""), [success, setSuccess] = useState(""), [activeTab, setActiveTab] = useState("users");
  const [userForm, setUserForm] = useState(newUserForm), [nodeForm, setNodeForm] = useState(() => newNodeForm()), [planForm, setPlanForm] = useState(newPlanForm);
  const [presetForm, setPresetForm] = useState({ preset_id: "", host: "", port: 443 as number | null }), [assignForm, setAssignForm] = useState({ username: "", plan_id: "", start_date: "", expire_date: "", queue_agent_commands: false, no_restart: false, command_timeout_ms: 60_000 });
  const [catalogForm, setCatalogForm] = useState({ includeCredentials: false, importCredentials: false, serverMapText: "{}", catalogText: "" });
  const [aliasesValid, setAliasesValid] = useState(true), [rulesValid, setRulesValid] = useState(true), [lastAssignment, setLastAssignment] = useState<SubscriptionPlanAssignResponse | null>(null), [catalogImport, setCatalogImport] = useState<SubscriptionCatalogImportResponse | null>(null);
  const [planDialog, setPlanDialog] = useState({ id: "", mode: "edit" as PlanOperation, open: false }), [nodeDialog, setNodeDialog] = useState({ id: "", mode: "edit" as NodeOperation, open: false });
  const [userDialog, setUserDialog] = useState({ username: "", mode: "edit" as UserOperation, removalId: null as string | null, open: false }), [loginDialog, setLoginDialog] = useState({ username: "", open: false });
  const [shortCode, setShortCode] = useState({ username: "", open: false }), [ipPolicy, setIpPolicy] = useState({ username: "", open: false }), [profileDialog, setProfileDialog] = useState<SubscriptionProfile | null>(null);
  const [legacyOpen, setLegacyOpen] = useState(false), [invitationsOpen, setInvitationsOpen] = useState(false), [privatePolicyOpen, setPrivatePolicyOpen] = useState(false), [shareOpen, setShareOpen] = useState(false);
  const [updatedToken, setUpdatedToken] = useState<ProductUserSubscriptionToken | null>(null), [confirmTemporary, setConfirmTemporary] = useState<TemporarySubscription | null>(null), [confirmImport, setConfirmImport] = useState(false);
  const lifecycle = useRef(0), refreshVersion = useRef(0), operationVersion = useRef(0), operationBusy = useRef(false), userEpoch = useRef(0), selectedUsername = useRef("");
  function selectUser(username: string) {
    if (selectedUsername.current === username) return;
    selectedUsername.current = username; ++userEpoch.current; setAssignForm(previous => ({ ...previous, username })); setLastAssignment(null); setUpdatedToken(null); setShareOpen(false);
  }
  async function refresh() {
    const run = ++refreshVersion.current, life = lifecycle.current; setLoading(true); setError("");
    try {
      const [serverList, userList, nodeList, planList, presetList, templateList, profileList, temporaryList, routes, meta] = await Promise.all([listServers(), listProductUsers(), listManagedNodes(), listSubscriptionPlans(), listSubscriptionTemplatePresets(), listSubscriptionTemplates(), listSubscriptionProfiles(), listTemporarySubscriptions(), listPrivateRoutes(), fetchAppMeta()]);
      if (run !== refreshVersion.current || life !== lifecycle.current) return;
      setServers(serverList); setUsers(userList.users); setNodes(nodeList.nodes); setPlans(planList.plans); setPresets(presetList.presets); setTemplates(templateList.templates); setProfiles(profileList.profiles); setTemporary(temporaryList.subscriptions); setPrivateRoutes(routes); setShortLinksEnabled(meta.short_links_enabled);
      setNodeForm(previous => previous.server_id ? previous : { ...previous, server_id: serverList[0]?.id ?? "" });
      setPresetForm(previous => previous.preset_id ? previous : { ...previous, preset_id: presetList.presets[0]?.id ?? "" });
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
    if (!userForm.username.trim()) { setError("Username is required."); return; }
    void perform("user", async current => {
      const username = userForm.username.trim(); await createProductUser({ username, email: blankToNull(userForm.email), display_name: blankToNull(userForm.display_name), role: userForm.role, is_active: userForm.is_active });
      if (!current()) return; setSuccess(`Created user ${username}.`); setUserForm(newUserForm()); await refresh();
    });
  }
  function submitNode() {
    if (!nodeForm.server_id) { setError("Server is required."); return; } if (!nodeForm.name.trim()) { setError("Node name is required."); return; }
    void perform("node", async current => {
      const payload: ManagedNodeCreateRequest = { name: nodeForm.name.trim(), server_id: nodeForm.server_id, protocol: nodeForm.protocol.trim(), node_type: nodeForm.node_type, parent_id: nodeForm.node_type === "routed" ? nodeForm.parent_id : null, target_node_id: nodeForm.node_type === "routed" ? nodeForm.target_node_id : null, inbound_tag: blankToNull(nodeForm.inbound_tag), routed_outbound_tag: blankToNull(nodeForm.routed_outbound_tag), routed_rule_marktag: blankToNull(nodeForm.routed_rule_marktag), tag: blankToNull(nodeForm.tag), tags: splitCsv(nodeForm.tagsText), enabled: nodeForm.enabled, client_template: parseJsonObject(nodeForm.clientTemplateText, "Client template"), config: parseJsonObject(nodeForm.configText, "Node config") };
      const result = await createManagedNode(payload); if (!current()) return; setSuccess(`Created node ${result.node.name}.`); setNodeForm(newNodeForm(result.node.server_id)); await refresh();
    });
  }
  function applyPreset() {
    const preset = presets.find(item => item.id === presetForm.preset_id); if (!preset) { setError("Preset is required."); return; }
    if (presetForm.port !== null && (!Number.isInteger(presetForm.port) || presetForm.port < 1 || presetForm.port > 65535)) { setError("Port must be an integer from 1 to 65535, or empty to use the preset."); return; }
    const config = { ...preset.config }; if (presetForm.host.trim()) config.server = presetForm.host.trim(); if (presetForm.port !== null) config.port = presetForm.port;
    setNodeForm(previous => ({ ...previous, name: previous.name.trim() || preset.name, protocol: preset.protocol, node_type: preset.node_type, inbound_tag: preset.inbound_tag ?? "", routed_outbound_tag: preset.routed_outbound_tag ?? "", routed_rule_marktag: preset.routed_rule_marktag ?? "", tag: preset.tag ?? "", tagsText: preset.tags.join(", "), clientTemplateText: JSON.stringify(preset.client_template, null, 2), configText: JSON.stringify(config, null, 2) }));
  }
  function createPresetNode() {
    const preset = presets.find(item => item.id === presetForm.preset_id); if (!preset) { setError("Preset is required."); return; } if (!nodeForm.server_id) { setError("Server is required."); return; }
    if (presetForm.port !== null && (!Number.isInteger(presetForm.port) || presetForm.port < 1 || presetForm.port > 65535)) { setError("Port must be an integer from 1 to 65535, or empty to use the preset."); return; }
    void perform("preset", async current => {
      const result = await createManagedNodeFromPreset(preset.id, { server_id: nodeForm.server_id, name: nodeForm.name.trim() || preset.name, host: blankToNull(presetForm.host), port: presetForm.port, inbound_tag: blankToNull(nodeForm.inbound_tag), routed_outbound_tag: blankToNull(nodeForm.routed_outbound_tag), routed_rule_marktag: blankToNull(nodeForm.routed_rule_marktag), tag: blankToNull(nodeForm.tag), tags: splitCsv(nodeForm.tagsText), enabled: nodeForm.enabled });
      if (!current()) return; setSuccess(`Created node ${result.node.name}.`); setNodeForm(newNodeForm(result.node.server_id)); await refresh();
    });
  }
  function submitPlan() {
    if (!aliasesValid || !rulesValid) return; if (!planForm.name.trim()) { setError("Plan name is required."); return; }
    void perform("plan", async current => {
      const payload: SubscriptionPlanCreateRequest = { ...planForm, name: planForm.name.trim(), description: planForm.description.trim(), reset_day: planForm.is_reset ? planForm.reset_day : 0, node_ids: [...planForm.node_ids], node_name_overrides: { ...planForm.node_name_overrides }, auto_speed_rules: planForm.auto_speed_rules.map(rule => ({ ...rule })), node_multipliers: Object.fromEntries(planForm.node_ids.map(id => [id, 1])) };
      const result = await createSubscriptionPlan(payload); if (!current()) return; setSuccess(`Created plan ${result.plan.name}.`); setPlanForm(newPlanForm()); await refresh(); if (current()) setAssignForm(previous => ({ ...previous, plan_id: result.plan.id }));
    });
  }
  function submitAssignment() {
    if (!assignForm.username || !assignForm.plan_id) { setError("User and plan are required."); return; }
    const epoch = userEpoch.current;
    void perform("assign", async current => {
      const result = await assignSubscriptionPlan(assignForm.username, { plan_id: assignForm.plan_id, start_date: blankToNull(assignForm.start_date), expire_date: blankToNull(assignForm.expire_date), queue_agent_commands: assignForm.queue_agent_commands, no_restart: assignForm.no_restart, command_timeout_ms: assignForm.command_timeout_ms });
      if (!current()) return;
      if (epoch === userEpoch.current) setLastAssignment(result);
      setSuccess(result.commands.length ? `Assigned ${result.plan.name} and queued ${result.commands.length} command.` : `Assigned ${result.plan.name}.`); await refresh();
    });
  }
  function exportCatalog() { void perform("export", async current => { const result = await exportSubscriptionCatalog(catalogForm.includeCredentials); if (current()) { setCatalogForm(previous => ({ ...previous, catalogText: JSON.stringify(result.catalog, null, 2) })); setSuccess("Catalog exported."); } }); }
  function importCatalog() { void perform("import", async current => {
    const catalog = parseJsonObject(catalogForm.catalogText, "Catalog") as unknown as SubscriptionCatalogBundle, map = parseJsonObject(catalogForm.serverMapText, "Server map");
    const server_map = Object.fromEntries(Object.entries(map).filter((entry): entry is [string, string] => typeof entry[1] === "string"));
    const result = await importSubscriptionCatalog({ catalog, server_map, import_credentials: catalogForm.importCredentials }); if (!current()) return; setCatalogImport(result); setConfirmImport(false); setSuccess("Catalog imported."); await refresh();
  }); }
  function revokeTemporary(value: TemporarySubscription) { void perform(`temporary:${value.id}`, async current => { await deleteTemporarySubscription(value.id); if (!current()) return; setTemporary(previous => previous.filter(item => item.id !== value.id)); setConfirmTemporary(null); setSuccess(`Revoked temporary link ${value.label}.`); }); }
  async function copyTemporary(value: TemporarySubscription) { const life = lifecycle.current; try { await navigator.clipboard.writeText(value.subscription_url); if (life === lifecycle.current) setSuccess(`Copied temporary link ${value.label}.`); } catch { if (life === lifecycle.current) setError("Clipboard access failed."); } }
  function manageUser(user: ProductUser, mode: UserOperation) { setUserDialog({ username: user.username, mode, removalId: user.removal_id ?? null, open: true }); }
  const selectedUser = users.find(user => user.username === assignForm.username), selectedUserPlan = plans.find(plan => plan.id === selectedUser?.current_plan_id);
  const temporaryNodes = nodes.filter(node => selectedUserPlan?.node_ids.includes(node.id) && !node.removal_id).map(node => ({ title: `${node.name} (${node.protocol})`, value: node.id }));
  const userOptions = users.filter(user => !user.removal_id).map(user => ({ label: user.display_name || user.username, value: user.username }));
  const nodeOptions = nodes.filter(node => !node.removal_id).map(node => ({ label: `${node.name} (${node.protocol})`, value: node.id }));
  const planOptions = plans.map(plan => ({ label: plan.name, value: plan.id })), serverName = (id: string) => servers.find(server => server.id === id)?.name ?? "Unknown server";
  const disabled = !!saving;
  function patchNode(change: Partial<typeof nodeForm>) { setNodeForm(previous => ({ ...previous, ...change })); }
  function patchPlan(change: Partial<typeof planForm>) { setPlanForm(previous => ({ ...previous, ...change })); }
  function patchAssignment(change: Partial<typeof assignForm>) { setAssignForm(previous => ({ ...previous, ...change })); }
  // Each subscriber's token, credentials and request epochs live in a keyed child.
  // Selecting another subscriber destroys those values before any new request starts.
  return <main data-testid="subscriptions-view"><Flex vertical gap="large">
    <Flex justify="space-between" align="center" wrap gap="middle"><div><Typography.Text type="secondary">Subscriptions</Typography.Text><Typography.Title level={2}>Catalog and user binding</Typography.Title></div><Button icon={<ReloadOutlined />} aria-label="Refresh subscription catalog" loading={loading} disabled={loading || disabled} onClick={() => void refresh()} /></Flex>
    {error && <Alert type="error" title={error} showIcon />}{success && <Alert type="success" title={success} showIcon />}
    <Row gutter={[24, 24]}>
      <Col xs={24} xl={11}><Card title="Workflow" extra={loading && <Spin size="small" />}><Typography.Paragraph type="secondary">Users, managed nodes, plans, assignment</Typography.Paragraph>
        <Tabs activeKey={activeTab} onChange={setActiveTab} items={[
          { key: "users", label: "Users", children: <Form layout="vertical" preserve={false} disabled={disabled} onFinish={submitUser}>
            <Form.Item label="Username"><Input aria-label="Username" value={userForm.username} onChange={event => setUserForm(previous => ({ ...previous, username: event.target.value }))} /></Form.Item>
            <Form.Item label="Role"><Select aria-label="Role" value={userForm.role} options={[{ label: "User", value: "user" }, { label: "Admin", value: "admin" }]} onChange={role => setUserForm(previous => ({ ...previous, role }))} /></Form.Item>
            <Form.Item label="Email"><Input aria-label="Email" value={userForm.email} onChange={event => setUserForm(previous => ({ ...previous, email: event.target.value }))} /></Form.Item>
            <Form.Item label="Display name"><Input aria-label="Display name" value={userForm.display_name} onChange={event => setUserForm(previous => ({ ...previous, display_name: event.target.value }))} /></Form.Item>
            <Form.Item label="Active"><Switch aria-label="Active" checked={userForm.is_active} onChange={is_active => setUserForm(previous => ({ ...previous, is_active }))} /></Form.Item>
            <Button type="primary" htmlType="submit" icon={<PlusOutlined />} aria-label="Create user" loading={saving === "user"}>Create user</Button>
          </Form> },
          { key: "nodes", label: "Nodes", children: <Form layout="vertical" preserve={false} disabled={disabled} onFinish={submitNode}>
            <Form.Item label="Preset"><Select aria-label="Preset" value={presetForm.preset_id || undefined} options={presets.map(preset => ({ label: preset.name, value: preset.id }))} disabled={disabled || !presets.length} onChange={preset_id => setPresetForm(previous => ({ ...previous, preset_id }))} /></Form.Item>
            <Row gutter={16}><Col xs={24} sm={16}><Form.Item label="Host"><Input aria-label="Host" value={presetForm.host} onChange={event => setPresetForm(previous => ({ ...previous, host: event.target.value }))} /></Form.Item></Col><Col xs={24} sm={8}><Form.Item label="Port"><StrictInputNumber aria-label="Port" allowEmpty aria-valuemin={1} aria-valuemax={65535} value={presetForm.port} onChange={value => setPresetForm(previous => ({ ...previous, port: value }))} /></Form.Item></Col></Row>
            <Flex gap="small"><Button disabled={disabled || !presetForm.preset_id} onClick={applyPreset}>Fill</Button><Button aria-label="Preset" aria-busy={saving === "preset"} disabled={disabled || !servers.length || !presetForm.preset_id} loading={saving === "preset"} onClick={createPresetNode}>Preset</Button></Flex><Divider />
            <Form.Item label="Name"><Input aria-label="Name" value={nodeForm.name} onChange={event => patchNode({ name: event.target.value })} /></Form.Item>
            <Form.Item label="Server"><Select aria-label="Server" value={nodeForm.server_id || undefined} options={servers.map(server => ({ label: server.name, value: server.id }))} disabled={disabled || !servers.length} onChange={server_id => patchNode({ server_id })} /></Form.Item>
            <Form.Item label="Protocol"><Input aria-label="Protocol" value={nodeForm.protocol} onChange={event => patchNode({ protocol: event.target.value })} /></Form.Item>
            <Form.Item label="Type"><Select aria-label="Type" value={nodeForm.node_type} options={[{ label: "Physical", value: "physical" }, { label: "Routed", value: "routed" }]} onChange={node_type => patchNode({ node_type })} /></Form.Item>
            {([{ key: "inbound_tag", label: "Inbound tag" }, { key: "routed_outbound_tag", label: "Outbound tag" }, { key: "routed_rule_marktag", label: "Route mark" }, { key: "tag", label: "Primary tag" }, { key: "tagsText", label: "Tags" }] as const).map(field => <Form.Item key={field.key} label={field.label}><Input aria-label={field.label} value={nodeForm[field.key]} onChange={event => patchNode({ [field.key]: event.target.value })} /></Form.Item>)}
            {nodeForm.node_type === "routed" && <><Form.Item label="Parent node"><Select aria-label="Parent node" allowClear value={nodeForm.parent_id ?? undefined} options={nodes.filter(node => !node.removal_id && node.server_id === nodeForm.server_id && node.inbound_tag === nodeForm.inbound_tag && node.protocol === nodeForm.protocol).map(node => ({ label: node.name, value: node.id }))} onChange={value => patchNode({ parent_id: value ?? null })} /></Form.Item><Form.Item label="Target node"><Select aria-label="Target node" allowClear value={nodeForm.target_node_id ?? undefined} options={nodeOptions} onChange={value => patchNode({ target_node_id: value ?? null })} /></Form.Item></>}
            <Form.Item label="Client template"><Input.TextArea aria-label="Client template" rows={5} value={nodeForm.clientTemplateText} onChange={event => patchNode({ clientTemplateText: event.target.value })} /></Form.Item>
            <Form.Item label="Node config"><Input.TextArea aria-label="Node config" rows={4} value={nodeForm.configText} onChange={event => patchNode({ configText: event.target.value })} /></Form.Item>
            <Form.Item label="Enabled"><Switch aria-label="Enabled" checked={nodeForm.enabled} onChange={enabled => patchNode({ enabled })} /></Form.Item>
            <Button type="primary" htmlType="submit" icon={<PlusOutlined />} aria-label="Create node" disabled={disabled || !servers.length} loading={saving === "node"}>Create node</Button>
          </Form> },
          { key: "plans", label: "Plans", children: <Form layout="vertical" preserve={false} disabled={disabled} onFinish={submitPlan}>
            <Form.Item label="Name"><Input aria-label="Name" value={planForm.name} onChange={event => patchPlan({ name: event.target.value })} /></Form.Item>
            <Form.Item label="Traffic mode"><Select aria-label="Traffic mode" value={planForm.traffic_mode} options={[{ label: "One-way billing (x1)", value: "oneway" }, { label: "Two-way billing (x2)", value: "twoway" }]} onChange={traffic_mode => patchPlan({ traffic_mode })} /></Form.Item>
            <Form.Item label="Description"><Input.TextArea aria-label="Description" rows={2} value={planForm.description} onChange={event => patchPlan({ description: event.target.value })} /></Form.Item>
            <Row gutter={16}>{([{ key: "traffic_limit_gb", label: "Traffic GB", min: 1 }, { key: "cycle_days", label: "Cycle days", min: 1 }, { key: "speed_limit_mbps", label: "Speed Mbps", min: 0 }, { key: "device_limit", label: "Concurrent connections", min: 0 }] as const).map(field => <Col xs={24} sm={12} key={field.key}><Form.Item label={field.label}><StrictInputNumber aria-label={field.label} aria-valuemin={field.min} value={planForm[field.key]} onChange={value => patchPlan({ [field.key]: value ?? Number.NaN })} /></Form.Item></Col>)}</Row>
            <Form.Item label="Reset monthly"><Switch aria-label="Reset monthly" checked={planForm.is_reset} onChange={is_reset => patchPlan({ is_reset })} /></Form.Item>
            <Form.Item label="Reset day"><StrictInputNumber aria-label="Reset day" aria-valuemin={1} aria-valuemax={31} disabled={disabled || !planForm.is_reset} value={planForm.reset_day} onChange={value => patchPlan({ reset_day: value ?? Number.NaN })} /></Form.Item>
            {(["clash", "surge"] as const).map(format => <Form.Item key={format} label={`${format === "clash" ? "Clash" : "Surge"} template`}><Select aria-label={`${format === "clash" ? "Clash" : "Surge"} template`} allowClear value={planForm[`${format}_template_id`] ?? undefined} options={templates.filter(template => template.format === format).map(template => ({ label: template.name, value: template.id }))} onChange={value => patchPlan({ [`${format}_template_id`]: value ?? null })} /></Form.Item>)}
            <Form.Item label="Nodes"><Select aria-label="Nodes" mode="multiple" options={nodeOptions} value={planForm.node_ids} onChange={node_ids => patchPlan({ node_ids })} /></Form.Item>
            <PlanNodeAliases nodes={planForm.node_ids.map(id => ({ id, name: nodes.find(node => node.id === id)?.name ?? id }))} value={planForm.node_name_overrides} onChange={node_name_overrides => patchPlan({ node_name_overrides })} enabled={planForm.node_name_override_enabled} onEnabledChange={node_name_override_enabled => patchPlan({ node_name_override_enabled })} onValid={setAliasesValid} disabled={disabled} />
            <AutoSpeedRuleEditor value={planForm.auto_speed_rules} onChange={auto_speed_rules => patchPlan({ auto_speed_rules })} onValid={setRulesValid} disabled={disabled} />
            <Button type="primary" htmlType="submit" icon={<PlusOutlined />} aria-label="Create plan" loading={saving === "plan"} disabled={disabled || !aliasesValid || !rulesValid}>Create plan</Button>
          </Form> },
          { key: "assign", label: "Assign", children: <Form layout="vertical" preserve={false} disabled={disabled} onFinish={submitAssignment}>
            <Form.Item label="User"><Select aria-label="Assignment user" value={assignForm.username || undefined} options={userOptions} onChange={selectUser} disabled={disabled || !userOptions.length} /></Form.Item>
            <Form.Item label="Plan"><Select aria-label="Plan" value={assignForm.plan_id || undefined} options={planOptions} onChange={plan_id => patchAssignment({ plan_id })} disabled={disabled || !planOptions.length} /></Form.Item>
            <Form.Item label="Start date"><Input aria-label="Start date" type="date" value={assignForm.start_date} onChange={event => patchAssignment({ start_date: event.target.value })} /></Form.Item>
            <Form.Item label="Expire date"><Input aria-label="Expire date" type="date" value={assignForm.expire_date} onChange={event => patchAssignment({ expire_date: event.target.value })} /></Form.Item>
            <Form.Item label="Apply to nodes (restart Xray)"><Switch aria-label="Apply to nodes (restart Xray)" checked={assignForm.queue_agent_commands} onChange={queue_agent_commands => patchAssignment({ queue_agent_commands })} /></Form.Item>
            {assignForm.queue_agent_commands && <Alert type="warning" title="Applying this plan can restart Xray and disconnect current clients. Queued commands still need Agent confirmation." showIcon />}
            <Form.Item label="Command timeout"><StrictInputNumber aria-label="Command timeout" aria-valuemin={1000} aria-valuemax={300000} value={assignForm.command_timeout_ms} onChange={value => patchAssignment({ command_timeout_ms: value ?? Number.NaN })} /></Form.Item>
            <Button type="primary" htmlType="submit" aria-label="Assign plan" aria-busy={saving === "assign"} loading={saving === "assign"} disabled={disabled || !userOptions.length || !planOptions.length}>Assign plan</Button>
          </Form> },
        ]} />
      </Card></Col>
      <Col xs={24} xl={13}><Flex vertical gap="large">
        <Card title="Catalog state" extra={<Tag color="success">Free</Tag>}><Typography.Paragraph type="secondary">{users.length} users, {plans.length} plans</Typography.Paragraph>
          <Form.Item label="User"><Select aria-label="Subscription user" value={assignForm.username || undefined} options={userOptions} disabled={!userOptions.length} onChange={selectUser} /></Form.Item>
          {assignForm.username ? <SubscriptionUserPanel key={assignForm.username} username={assignForm.username} user={selectedUser} servers={servers} assignment={lastAssignment} externalToken={updatedToken} canShare={!!temporaryNodes.length} onShare={() => setShareOpen(true)} onRefresh={() => void refresh()} onUserUpdated={value => setUsers(previous => previous.map(user => user.username === value.username ? value : user))} /> : <Empty description="Create a user to manage subscription access." />}
        </Card>
        <Card title="Catalog import/export"><Flex vertical gap="middle">
          <Flex gap="large" wrap><Form.Item label="Export creds"><Switch aria-label="Export creds" checked={catalogForm.includeCredentials} disabled={disabled} onChange={includeCredentials => setCatalogForm(previous => ({ ...previous, includeCredentials }))} /></Form.Item><Form.Item label="Import creds"><Switch aria-label="Import creds" checked={catalogForm.importCredentials} disabled={disabled} onChange={importCredentials => setCatalogForm(previous => ({ ...previous, importCredentials }))} /></Form.Item></Flex>
          {(catalogForm.includeCredentials || catalogForm.importCredentials) && <Alert type="warning" title="Catalog credentials are sensitive. Keep exports private; importing credentials can replace existing access data." showIcon />}
          <Flex gap="small" wrap><Button aria-label="Export" aria-busy={saving === "export"} loading={saving === "export"} disabled={disabled} onClick={exportCatalog}>Export</Button><Button type="primary" aria-label="Import" aria-busy={saving === "import"} loading={saving === "import"} disabled={disabled || !catalogForm.catalogText.trim()} onClick={() => setConfirmImport(true)}>Import</Button><Button disabled={disabled} onClick={() => setLegacyOpen(true)}>MMWX identities</Button><Button disabled={disabled || !catalogForm.catalogText} onClick={() => setCatalogForm(previous => ({ ...previous, catalogText: "" }))}>Clear catalog</Button></Flex>
          <Form.Item label="Catalog JSON"><Input.TextArea aria-label="Catalog JSON" rows={8} disabled={disabled} value={catalogForm.catalogText} onChange={event => setCatalogForm(previous => ({ ...previous, catalogText: event.target.value }))} /></Form.Item>
          <Form.Item label="Server map JSON"><Input.TextArea aria-label="Server map JSON" rows={3} disabled={disabled} value={catalogForm.serverMapText} onChange={event => setCatalogForm(previous => ({ ...previous, serverMapText: event.target.value }))} /></Form.Item>
          {catalogImport && <><Flex gap="small" wrap><Tag>Users {catalogImport.summary.created_users} / {catalogImport.summary.updated_users}</Tag><Tag>Nodes {catalogImport.summary.created_nodes} / {catalogImport.summary.updated_nodes}</Tag><Tag>Plans {catalogImport.summary.created_plans} / {catalogImport.summary.updated_plans}</Tag><Tag>Creds {catalogImport.summary.imported_credentials}</Tag></Flex>{catalogImport.summary.warnings.map(warning => <Alert key={warning} type="warning" title={warning} showIcon />)}</>}
        </Flex></Card>
        <Card title="Private routes" extra={<Flex gap="small"><Tag color={privateRoutes?.policy.enabled ? "success" : "default"}>{privateRoutes?.policy.enabled ? "Enabled" : "Disabled"}</Tag><Button aria-label="Edit private route policy" icon={<SettingOutlined />} disabled={!privateRoutes || loading || disabled} onClick={() => setPrivatePolicyOpen(true)} /></Flex>}><Flex vertical gap="small">
          {!privateRoutes?.nodes.length && <Empty description="No private routes." />}{privateRoutes?.nodes.map(item => <Card key={item.id} size="small" title={item.name} extra={<Tag color={item.status === "active" ? "success" : item.status === "failed" ? "error" : "warning"}>{item.status}</Tag>}><Typography.Text>{item.username} - {item.parent_name} to {item.target_name}</Typography.Text>{item.last_error && <Alert type="error" title={item.last_error} showIcon />}</Card>)}
        </Flex></Card>
        <Card title="Registration" extra={<Button aria-label="Manage registration invitations" icon={<UserAddOutlined />} disabled={!plans.length} onClick={() => setInvitationsOpen(true)} />}><Tag>{plans.length} plans</Tag></Card>
        <Card title="Temporary links"><Flex vertical gap="small">{!temporary.length && <Empty description="No temporary links." />}{temporary.map(item => <Card key={item.id} size="small" title={item.label} extra={<Flex gap="small" wrap><Button icon={<CopyOutlined />} aria-label={`Copy temporary link ${item.label}`} onClick={() => void copyTemporary(item)} /><Button icon={<LinkOutlined />} aria-label={`Revoke temporary link ${item.label}`} disabled={disabled} loading={saving === `temporary:${item.id}`} onClick={() => setConfirmTemporary(item)} /><Tag color={item.status === "active" ? "success" : item.status === "expired" ? "warning" : "error"}>{item.status}</Tag></Flex>}><Typography.Text>{item.username} - {item.access_count}/{item.max_access} downloads - expires {new Date(item.expires_at).toLocaleString()}</Typography.Text></Card>)}</Flex></Card>
        <Card title="Subscription profiles"><Flex vertical gap="small">{!profiles.length && <Empty description="No imported profiles." />}{profiles.map(item => <Card key={item.id} size="small" title={item.name} extra={<Flex gap="small"><Button icon={<SettingOutlined />} aria-label={`Edit subscription profile ${item.name}`} onClick={() => setProfileDialog(item)} /><Tag color={item.enabled ? "success" : "warning"}>{item.enabled ? "Enabled" : "Needs setup"}</Tag></Flex>}><Typography.Text>{item.assigned_usernames.length} subscribers - {item.source_type}</Typography.Text></Card>)}</Flex></Card>
        <Card title="Users"><Flex vertical gap="small">{!users.length && <Empty description="No users yet." />}{users.map(user => <Card key={user.username} size="small" title={user.display_name || user.username}><Typography.Paragraph type="secondary">{user.username}</Typography.Paragraph><Flex gap="small" wrap>
          {!user.removal_id ? <>{shortLinksEnabled && <Button icon={<LinkOutlined />} aria-label={`Edit short code for ${user.username}`} onClick={() => setShortCode({ username: user.username, open: true })} />}<Button icon={<SettingOutlined />} aria-label={`Edit subscription IP access for ${user.username}`} onClick={() => setIpPolicy({ username: user.username, open: true })} /><Button icon={<KeyOutlined />} aria-label={`Login settings for ${user.username}`} onClick={() => setLoginDialog({ username: user.username, open: true })} /><Button icon={<EditOutlined />} aria-label={`Edit user ${user.username}`} onClick={() => manageUser(user, "edit")} /><Button danger icon={<DeleteOutlined />} aria-label={`Remove user ${user.username}`} disabled={user.role === "admin"} onClick={() => manageUser(user, "remove")} /></> : <Button icon={<SyncOutlined />} aria-label={`View removal for ${user.username}`} onClick={() => manageUser(user, "remove")} />}
          {user.current_plan_id && !user.removal_id && <Button icon={<LinkOutlined />} aria-label={`Unassign plan for ${user.username}`} onClick={() => setPlanDialog({ id: user.username, mode: "unassign", open: true })} />}
          <Tag color={user.current_plan_id ? "processing" : "default"}>{user.removal_id ? "Removing" : !user.is_active ? "Disabled" : formatDate(user.plan_expires_at)}</Tag>
        </Flex></Card>)}</Flex></Card>
        <Card title="Plans"><Flex vertical gap="small">{!plans.length && <Empty description="No plans yet." />}{plans.map(plan => <Card key={plan.id} size="small" title={plan.name} extra={<Flex gap="small"><Button icon={<EditOutlined />} aria-label={`Edit plan ${plan.name}`} onClick={() => setPlanDialog({ id: plan.id, mode: "edit", open: true })} /><Button danger icon={<DeleteOutlined />} aria-label={`Remove plan ${plan.name}`} onClick={() => setPlanDialog({ id: plan.id, mode: "remove", open: true })} /></Flex>}><Typography.Text>{plan.traffic_limit_gb.toFixed(plan.traffic_limit_gb >= 10 ? 0 : 1)} GB / {plan.cycle_days} days</Typography.Text> <Tag>{plan.node_ids.length} nodes</Tag></Card>)}</Flex></Card>
        <Card title="Nodes"><Flex vertical gap="small">{!nodes.length && <Empty description="No nodes yet." />}{nodes.map(node => <Card key={node.id} size="small" title={node.name}><Typography.Paragraph type="secondary">{serverName(node.server_id)}</Typography.Paragraph><Flex gap="small">{!node.removal_id ? <><Button icon={<EditOutlined />} aria-label={`Edit node ${node.name}`} onClick={() => setNodeDialog({ id: node.id, mode: "edit", open: true })} /><Button danger icon={<DeleteOutlined />} aria-label={`Remove node ${node.name}`} onClick={() => setNodeDialog({ id: node.id, mode: "remove", open: true })} /></> : <Button icon={<SyncOutlined />} aria-label={`Node removal status ${node.name}`} onClick={() => setNodeDialog({ id: node.id, mode: "remove", open: true })} />}<Tag color={node.enabled && !node.removal_id ? "success" : "warning"}>{node.removal_id ? "Removing" : node.node_type}</Tag></Flex></Card>)}</Flex></Card>
        {lastAssignment && <Card title="Last assignment"><Flex vertical gap="small"><Typography.Text>{lastAssignment.user.username} → {lastAssignment.plan.name}</Typography.Text><Flex gap="small"><Tag>{lastAssignment.provisioning_batches.length} batches</Tag><Tag>{lastAssignment.commands.length} commands</Tag></Flex>{lastAssignment.warnings.map(warning => <Alert key={warning} type="warning" title={warning} showIcon />)}<Input.TextArea aria-label="Provisioning batches" value={JSON.stringify(lastAssignment.provisioning_batches, null, 2)} readOnly rows={8} /><Typography.Text type="secondary">Batches may contain credentials. A queued command is not confirmation that the Agent applied it.</Typography.Text></Flex></Card>}
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
    <TemporarySubscriptionDialog open={shareOpen} username={assignForm.username} nodes={temporaryNodes} onOpenChange={setShareOpen} onCreated={value => { setTemporary(previous => [value, ...previous.filter(item => item.id !== value.id)]); setSuccess(`Temporary link created for ${value.username}.`); }} />
    <PrivateRoutedPolicyDialog open={privatePolicyOpen} policy={privateRoutes?.policy ?? null} onOpenChange={setPrivatePolicyOpen} onSaved={() => void refresh()} />
    <Modal open={!!confirmTemporary} title="Revoke temporary link?" destroyOnHidden mask={{ closable: !disabled }} closable={!disabled} keyboard={!disabled} onCancel={() => !disabled && setConfirmTemporary(null)} okText="Revoke" okButtonProps={{ "aria-label": "Revoke", "aria-busy": !!confirmTemporary && saving === `temporary:${confirmTemporary.id}`, danger: true }} confirmLoading={!!confirmTemporary && saving === `temporary:${confirmTemporary.id}`} cancelButtonProps={{ disabled }} onOk={() => confirmTemporary && revokeTemporary(confirmTemporary)}><Typography.Paragraph>{confirmTemporary?.label}: future subscription downloads will stop. Already downloaded credentials are not revoked by this action.</Typography.Paragraph></Modal>
    <Modal open={confirmImport} title="Import catalog?" destroyOnHidden mask={{ closable: !disabled }} closable={!disabled} keyboard={!disabled} onCancel={() => !disabled && setConfirmImport(false)} okText="Import catalog" okButtonProps={{ "aria-label": "Import catalog", "aria-busy": saving === "import" }} confirmLoading={saving === "import"} cancelButtonProps={{ disabled }} onOk={importCatalog}><Typography.Paragraph>Import creates or updates users, nodes and plans using the server map. {catalogForm.importCredentials ? "Credentials will also be imported." : "Credentials will not be imported."}</Typography.Paragraph></Modal>
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
  function createToken(reset = false) { void perform("token", async current => { const value = await (reset ? resetProductUserSubscriptionToken : createProductUserSubscriptionToken)(username); if (!current()) return; setToken(value.subscription); setConfirmation(null); setSuccess(reset ? `Subscription link reset for ${username}.` : `Subscription link ready for ${username}.`); }); }
  function loadCredentials() { void perform("credentials", async current => { const value = await listProductUserCredentials(username); if (!current()) return; setCredentials(value.credentials); setSuccess(`Loaded ${value.credentials.length} credentials.`); }); }
  function loadTraffic() { void perform("traffic", async current => { const value = await getProductUserTraffic(username); if (!current()) return; setTraffic(value); setSuccess(`Loaded ${formatBytes(value.total)} raw / ${formatBytes(value.charged_usage_bytes)} billed traffic.`); }); }
  function loadQuota() { void perform("quota", async current => { const value = await getProductUserQuota(username); if (!current()) return; setQuota(value.quota); setSuccess(`Loaded ${formatBytes(value.quota.remaining_bytes)} remaining.`); }); }
  function resetTraffic() { void perform("reset-traffic", async current => { const value = await resetProductUserTraffic(username); if (!current()) return; setQuota(value.quota); await fetchUsage(current); if (!current()) return; setConfirmation(null); setSuccess(`Traffic reset for ${username}.`); onRefresh(); }); }
  function resetDue() { void perform("reset-due", async current => { const value = await resetDueProductUserTraffic({}); if (!current()) return; setDueReset(value); await fetchUsage(current); if (!current()) return; setConfirmation(null); setSuccess(`Reset ${value.summary.reset_users} due users.`); onRefresh(); }); }
  const availableNodes = (preview?.nodes ?? []).filter(node => node.available);
  let formatUrl = "";
  if (token && availableNodes.length) {
    try { const value = new URL(token.subscription_url, window.location.origin); if (format !== "clash") value.searchParams.set("format", format); else value.searchParams.delete("format"); value.searchParams.delete("node_id"); if (format === "xray" && formatNode) value.searchParams.set("node_id", formatNode); formatUrl = value.toString(); } catch { /* A malformed server response is not offered as a download link. */ }
  }
  return <Flex vertical gap="middle">
    <Typography.Title level={4}>Subscription link</Typography.Title>
    <SubscriptionAccessPanel username={username} isActive={user?.is_active ?? false} refreshKey={assignment?.user.updated_at} onUpdated={value => { onUserUpdated(value); setQuota(null); }} />
    {error && <Alert type="error" title={error} showIcon />}{success && <Alert type="success" title={success} showIcon />}
    <Flex gap="small" wrap><Button type="primary" aria-label="Link" aria-busy={busy === "token"} disabled={!!busy} loading={busy === "token"} onClick={() => createToken()}>Link</Button><Button danger disabled={!!busy} onClick={() => setConfirmation("token")}>Reset</Button><Button aria-label="Creds" aria-busy={busy === "credentials"} disabled={!!busy} loading={busy === "credentials"} onClick={loadCredentials}>Creds</Button><Button aria-label="Traffic" aria-busy={busy === "traffic"} disabled={!!busy} loading={busy === "traffic"} onClick={loadTraffic}>Traffic</Button><Button disabled={!!busy || !canShare} onClick={onShare}>Share</Button><Button aria-label="Quota" aria-busy={busy === "quota"} disabled={!!busy} loading={busy === "quota"} onClick={loadQuota}>Quota</Button><Button disabled={!!busy} onClick={() => setConfirmation("traffic")}>Reset traffic</Button><Button disabled={!!busy} onClick={() => setConfirmation("due")}>Reset due</Button></Flex>
    {token && <><Form.Item label="Subscription URL"><Input aria-label="Subscription URL" readOnly value={token.subscription_url} /></Form.Item>{token.short_links_enabled && <Form.Item label="Short URL"><Input aria-label="Short URL" readOnly value={token.short_url} /></Form.Item>}
      <Form.Item label="Client format"><Select aria-label="Client format" value={format} options={formatOptions} loading={previewBusy} onChange={value => { setFormat(value); setPreview(null); setFormatNode(null); }} /></Form.Item>
      <Form.Item label="Format URL"><Input aria-label="Format URL" readOnly value={formatUrl} /></Form.Item>
      {format === "xray" && <Form.Item label="Xray node"><Select aria-label="Xray node" allowClear value={formatNode ?? undefined} options={availableNodes.map(node => ({ label: node.name, value: node.node_id }))} disabled={previewBusy || !availableNodes.length} onChange={value => setFormatNode(value ?? null)} /></Form.Item>}
      {previewError && <Alert type="error" title={previewError} showIcon />}{preview && <><Typography.Text type="secondary">{availableNodes.length} available / {preview.nodes.length - availableNodes.length} excluded</Typography.Text>{preview.warnings.map(warning => <Alert key={warning} type="warning" title={warning} showIcon />)}{preview.nodes.filter(node => !node.available).map(node => <Alert key={node.node_id} type="warning" title={node.name} description={node.reason} showIcon />)}</>}
    </>}
    {quota && <Card size="small" title="Quota status" extra={<Tag color={quotaColor(quota)}>{quotaLabel(quota)}</Tag>}><Typography.Paragraph>{formatBytes(quota.charged_usage_bytes)} / {quota.traffic_limit_bytes ? formatBytes(quota.traffic_limit_bytes) : "No limit"}</Typography.Paragraph><Progress percent={Math.min(quota.percent_used, 100)} status={quotaColor(quota) === "error" ? "exception" : "normal"} /><Flex gap="small" wrap><Tag>Raw down {formatBytes(quota.download)}</Tag><Tag>Raw up {formatBytes(quota.upload)}</Tag><Tag>Left {formatBytes(quota.remaining_bytes)}</Tag><Tag>Used {quota.percent_used.toFixed(quota.percent_used >= 10 ? 1 : 2)}%</Tag></Flex><Typography.Text type="secondary">Reset {formatDate(quota.last_traffic_reset_at)} / Next {formatDate(quota.next_reset_at)}</Typography.Text></Card>}
    {dueReset && <><Flex gap="small"><Tag>Due {dueReset.summary.reset_users}</Tag><Tag>Checked {dueReset.summary.checked_users}</Tag></Flex>{dueReset.summary.warnings.map(warning => <Alert key={warning} type="warning" title={warning} showIcon />)}</>}
    {traffic && <Card size="small" title="Traffic ledger (raw counters)" extra={<Tag>Billed {formatBytes(traffic.charged_usage_bytes)}</Tag>}><Typography.Paragraph>Up {formatBytes(traffic.upload)} / Down {formatBytes(traffic.download)}</Typography.Paragraph>{traffic.entries.map(entry => <Descriptions key={`${entry.server_id}:${entry.email}`} size="small" column={1} items={[{ key: "email", label: entry.archived ? "Archived usage" : entry.email, children: `${entry.server_name || servers.find(server => server.id === entry.server_id)?.name || "Unknown server"} - ${formatDate(entry.last_reported_at || entry.updated_at)}` }, { key: "value", label: "Traffic", children: `Raw ${formatBytes(entry.total)} / Billed ${formatBytes(entry.charged_usage_bytes)}` }]} />)}</Card>}
    {!!credentials.length && <section aria-label="Subscription credentials"><Flex vertical gap="small">{credentials.map(credential => <Card key={credential.id} size="small" title={credential.email} extra={<Tag>{credential.protocol}</Tag>}><Typography.Text>{credentialIdentifier(credential)}</Typography.Text></Card>)}</Flex></section>}
    {(token || credentials.length > 0) && <Button disabled={!!busy} onClick={() => { setToken(null); setCredentials([]); setPreview(null); }}>Hide links and credentials</Button>}
    <Modal open={!!confirmation} title={confirmation === "token" ? "Reset subscription link?" : confirmation === "traffic" ? "Reset user traffic?" : "Reset due traffic?"} destroyOnHidden mask={{ closable: !busy }} keyboard={!busy} closable={!busy} onCancel={() => !busy && setConfirmation(null)} okText="Confirm reset" okButtonProps={{ "aria-label": "Confirm reset", "aria-busy": !!busy, danger: true }} confirmLoading={!!busy} cancelButtonProps={{ disabled: !!busy }} onOk={() => confirmation === "token" ? createToken(true) : confirmation === "traffic" ? resetTraffic() : resetDue()}><Typography.Paragraph>{confirmation === "token" ? `The current subscription link for ${username} will stop working. Already downloaded credentials are not rotated.` : confirmation === "traffic" ? `Reset charged traffic for ${username}. This cannot be undone from this screen.` : "Reset every subscriber whose traffic reset is due. Other subscribers are unchanged."}</Typography.Paragraph></Modal>
  </Flex>;
}
