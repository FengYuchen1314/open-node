import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Flex,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { useEffect, useMemo, useRef, useState } from "react";

import type { ExternalSourceRead } from "../../domain/external-subscriptions";
import type {
  CustomRule,
  CustomRuleMode,
  CustomRuleType,
  OverrideScript,
  OverrideScriptHook,
  OverrideScriptWrite,
  ProxyProvider,
  ProxyProviderWrite,
} from "../../domain/subscription-customizations";
import type { ProductUser } from "../../domain/subscriptions";
import { accountExternalSubscriptions, listExternalSources } from "../../services/external-subscriptions";
import {
  accountSubscriptionCustomizations,
  createCustomRule,
  createOverrideScript,
  createProxyProvider,
  deleteCustomRule,
  deleteOverrideScript,
  deleteProxyProvider,
  listCustomRules,
  listOverrideScripts,
  listProxyProviders,
  updateCustomRule,
  updateOverrideScript,
  updateProxyProvider,
} from "../../services/subscription-customizations";
import { listProductUsers } from "../../services/subscriptions";

const typeLabels: Record<CustomRuleType, string> = {
  dns: "DNS", rules: "分流规则", "rule-providers": "规则集来源",
};
const modeLabels: Record<CustomRuleMode, string> = {
  replace: "替换", prepend: "前置", append: "追加",
};
const hookLabels: Record<OverrideScriptHook, string> = {
  post_fetch: "订阅生成后（post_fetch）",
  pre_save_nodes: "节点保存前（pre_save_nodes）",
};
const emptyRule = {
  owner_username: "", name: "", type: "rules" as CustomRuleType,
  mode: "prepend" as CustomRuleMode, content: "", enabled: true,
};
const emptyProvider: ProxyProviderWrite = {
  owner_username: "", external_source_id: "", name: "", type: "http", interval: 3600,
  proxy: "DIRECT", size_limit: 0, header: {}, health_check_enabled: true,
  health_check_url: "https://www.gstatic.com/generate_204", health_check_interval: 300,
  health_check_timeout: 5000, health_check_lazy: true, health_check_expected_status: 204,
  filter: "", exclude_filter: "", exclude_type: "", geo_ip_filter: "", override: {},
  process_mode: "client", enabled: true,
};
const emptyScript: OverrideScriptWrite = {
  owner_username: "", name: "", hook: "post_fetch",
  content: "function main(config) {\n  return config;\n}", enabled: true, sort_order: 0,
};

function message(failure: unknown) {
  return failure instanceof Error ? failure.message : "订阅自定义操作失败，请重新读取状态。";
}

interface SubscriptionCustomizationsViewProps {
  subscriberUsername?: string;
  allowRules?: boolean;
  allowProviders?: boolean;
  allowScripts?: boolean;
}

export default function SubscriptionCustomizationsView({
  subscriberUsername,
  allowRules = true,
  allowProviders = true,
  allowScripts = allowRules,
}: SubscriptionCustomizationsViewProps = {}) {
  const active = useRef(true), sequence = useRef(0);
  const accountApi = useMemo(
    () => subscriberUsername ? accountSubscriptionCustomizations(subscriberUsername) : null,
    [subscriberUsername],
  );
  const accountSources = useMemo(
    () => subscriberUsername ? accountExternalSubscriptions(subscriberUsername) : null,
    [subscriberUsername],
  );
  const [users, setUsers] = useState<ProductUser[]>([]);
  const [sources, setSources] = useState<ExternalSourceRead[]>([]);
  const [rules, setRules] = useState<CustomRule[]>([]);
  const [providers, setProviders] = useState<ProxyProvider[]>([]);
  const [scripts, setScripts] = useState<OverrideScript[]>([]);
  const [ruleEdit, setRuleEdit] = useState<CustomRule | "new" | null>(null);
  const [ruleDraft, setRuleDraft] = useState(emptyRule);
  const [providerEdit, setProviderEdit] = useState<ProxyProvider | "new" | null>(null);
  const [providerDraft, setProviderDraft] = useState<ProxyProviderWrite>(emptyProvider);
  const [overrideText, setOverrideText] = useState("{}");
  const [headerText, setHeaderText] = useState("{}");
  const [scriptEdit, setScriptEdit] = useState<OverrideScript | "new" | null>(null);
  const [scriptDraft, setScriptDraft] = useState<OverrideScriptWrite>(emptyScript);
  const [scriptRemove, setScriptRemove] = useState<OverrideScript | null>(null);
  const [remove, setRemove] = useState<CustomRule | ProxyProvider | null>(null);
  const [busy, setBusy] = useState(""), [error, setError] = useState(""), [notice, setNotice] = useState("");

  async function load() {
    const run = ++sequence.current; setBusy("load"); setError("");
    try {
      const [userList, sourceList, ruleList, providerList, scriptList] = await Promise.all([
        subscriberUsername
          ? Promise.resolve({ users: [{ username: subscriberUsername, display_name: subscriberUsername } as ProductUser] })
          : listProductUsers(),
        allowProviders
          ? accountSources ? accountSources.listExternalSources() : listExternalSources()
          : Promise.resolve({ sources: [] as ExternalSourceRead[] }),
        allowRules
          ? accountApi ? accountApi.listCustomRules() : listCustomRules()
          : Promise.resolve({ rules: [] as CustomRule[] }),
        allowProviders
          ? accountApi ? accountApi.listProxyProviders() : listProxyProviders()
          : Promise.resolve({ providers: [] as ProxyProvider[] }),
        allowScripts
          ? accountApi ? accountApi.listOverrideScripts() : listOverrideScripts()
          : Promise.resolve({ scripts: [] as OverrideScript[] }),
      ]);
      if (!active.current || run !== sequence.current) return;
      setUsers(userList.users); setSources(sourceList.sources);
      setRules(ruleList.rules); setProviders(providerList.providers); setScripts(scriptList.scripts);
    } catch (failure) { if (active.current && run === sequence.current) setError(message(failure)); }
    finally { if (active.current && run === sequence.current) setBusy(""); }
  }
  useEffect(() => {
    active.current = true; void load();
    return () => { active.current = false; sequence.current += 1; };
  }, []);

  function openRule(value: CustomRule | "new") {
    setRuleEdit(value); setError(""); setNotice("");
    setRuleDraft(value === "new" ? { ...emptyRule, owner_username: users[0]?.username ?? "" } : {
      owner_username: value.owner_username, name: value.name, type: value.type,
      mode: value.mode, content: value.content, enabled: value.enabled,
    });
  }
  async function saveRule() {
    if (!ruleEdit || busy || !ruleDraft.owner_username || !ruleDraft.name.trim() || !ruleDraft.content.trim()) return;
    setBusy("rule"); setError("");
    try {
      const payload = { ...ruleDraft, name: ruleDraft.name.trim(), content: ruleDraft.content.trim() };
      const value = ruleEdit === "new"
        ? await (accountApi ? accountApi.createCustomRule(payload) : createCustomRule(payload))
        : await (accountApi ? accountApi.updateCustomRule(ruleEdit, payload) : updateCustomRule(ruleEdit, payload));
      if (!active.current) return;
      setRules(previous => ruleEdit === "new" ? [value, ...previous] : previous.map(item => item.id === value.id ? value : item));
      setRuleEdit(null); setNotice("自定义规则已保存；启用它的订阅档案会在下次下载时实时应用。 ");
    } catch (failure) { if (active.current) setError(message(failure)); }
    finally { if (active.current) setBusy(""); }
  }

  function openProvider(value: ProxyProvider | "new") {
    setProviderEdit(value); setError(""); setNotice("");
    const draft = value === "new" ? { ...emptyProvider, owner_username: users[0]?.username ?? "" } : {
      owner_username: value.owner_username, external_source_id: value.external_source_id,
      name: value.name, type: value.type, interval: value.interval, proxy: value.proxy,
      size_limit: value.size_limit, header: value.header, health_check_enabled: value.health_check_enabled,
      health_check_url: value.health_check_url,
      health_check_interval: value.health_check_interval,
      health_check_timeout: value.health_check_timeout,
      health_check_lazy: value.health_check_lazy,
      health_check_expected_status: value.health_check_expected_status,
      filter: value.filter, exclude_filter: value.exclude_filter,
      exclude_type: value.exclude_type, geo_ip_filter: value.geo_ip_filter, override: value.override,
      process_mode: value.process_mode, enabled: value.enabled,
    };
    setProviderDraft(draft); setOverrideText(JSON.stringify(draft.override, null, 2));
    setHeaderText(JSON.stringify(draft.header, null, 2));
  }
  async function saveProvider() {
    if (!providerEdit || busy || !providerDraft.owner_username || !providerDraft.external_source_id || !providerDraft.name.trim()) return;
    let override: Record<string, unknown>, header: Record<string, string[]>;
    try {
      const value = JSON.parse(overrideText || "{}") as unknown;
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
      override = value as Record<string, unknown>;
    } catch { setError("节点覆写必须是 JSON 对象；当前内容没有提交。"); return; }
    try {
      const value = JSON.parse(headerText || "{}") as unknown;
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
      header = value as Record<string, string[]>;
      if (Object.values(header).some(values => !Array.isArray(values) || values.some(item => typeof item !== "string"))) throw new Error();
    } catch { setError("请求头必须是“名称 → 字符串数组”的 JSON 对象；当前内容没有提交。"); return; }
    setBusy("provider"); setError("");
    try {
      const payload = { ...providerDraft, name: providerDraft.name.trim(), header, override };
      const value = providerEdit === "new"
        ? await (accountApi ? accountApi.createProxyProvider(payload) : createProxyProvider(payload))
        : await (accountApi ? accountApi.updateProxyProvider(providerEdit, payload) : updateProxyProvider(providerEdit, payload));
      if (!active.current) return;
      setProviders(previous => providerEdit === "new" ? [...previous, value] : previous.map(item => item.id === value.id ? value : item));
      setProviderEdit(null); setNotice("Proxy Provider 已保存；客户端模式生成 Mihomo Provider，服务端模式直接生成同名代理组。 ");
    } catch (failure) { if (active.current) setError(message(failure)); }
    finally { if (active.current) setBusy(""); }
  }

  function openScript(value: OverrideScript | "new") {
    setScriptEdit(value); setError(""); setNotice("");
    setScriptDraft(value === "new" ? { ...emptyScript, owner_username: users[0]?.username ?? "" } : {
      owner_username: value.owner_username, name: value.name, hook: value.hook,
      content: value.content, enabled: value.enabled, sort_order: value.sort_order,
    });
  }
  async function saveScript() {
    if (!scriptEdit || busy || !scriptDraft.owner_username || !scriptDraft.name.trim() || !scriptDraft.content.trim()) return;
    setBusy("script"); setError("");
    try {
      const payload = { ...scriptDraft, name: scriptDraft.name.trim(), content: scriptDraft.content.trim() };
      const value = scriptEdit === "new"
        ? await (accountApi ? accountApi.createOverrideScript(payload) : createOverrideScript(payload))
        : await (accountApi ? accountApi.updateOverrideScript(scriptEdit, payload) : updateOverrideScript(scriptEdit, payload));
      if (!active.current) return;
      setScripts(previous => scriptEdit === "new" ? [...previous, value] : previous.map(item => item.id === value.id ? value : item));
      setScriptEdit(null); setNotice("覆写脚本已保存；启用它的订阅档案会按排序值依次执行。");
    } catch (failure) { if (active.current) setError(message(failure)); }
    finally { if (active.current) setBusy(""); }
  }
  async function removeCurrentScript() {
    if (!scriptRemove || busy) return; const current = scriptRemove; setBusy("delete-script"); setError("");
    try {
      await (accountApi ? accountApi.deleteOverrideScript(current) : deleteOverrideScript(current));
      if (!active.current) return;
      setScripts(previous => previous.filter(item => item.id !== current.id));
      setScriptRemove(null); setNotice("覆写脚本已删除，订阅档案中的对应引用也已清理。");
    } catch (failure) { if (active.current) { setError(message(failure)); void load(); } }
    finally { if (active.current) setBusy(""); }
  }

  async function removeCurrent() {
    if (!remove || busy) return; const current = remove; setBusy("delete"); setError("");
    try {
      if ("content" in current) {
        await (accountApi ? accountApi.deleteCustomRule(current) : deleteCustomRule(current));
        setRules(previous => previous.filter(item => item.id !== current.id));
      } else {
        await (accountApi ? accountApi.deleteProxyProvider(current) : deleteProxyProvider(current));
        setProviders(previous => previous.filter(item => item.id !== current.id));
      }
      if (active.current) { setRemove(null); setNotice("已删除。引用它的订阅档案会自动忽略该资源。"); }
    } catch (failure) { if (active.current) { setError(message(failure)); void load(); } }
    finally { if (active.current) setBusy(""); }
  }
  const userOptions = users.filter(user => !user.removal_id).map(user => ({
    value: user.username, label: user.display_name || user.username,
  }));
  const sourceName = new Map(sources.map(source => [source.id, source.name]));
  const providerSources = sources.filter(source => source.owner_username === providerDraft.owner_username);
  const tabs = [
    allowRules ? { key: "rules", label: `自定义规则（${rules.length}）`, children: <Card extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => openRule("new")}>新建规则</Button>}><Table<CustomRule> rowKey="id" dataSource={rules} loading={busy === "load"} scroll={{ x: 820 }} columns={[
      { title: "名称", dataIndex: "name", render: (value, item) => <Space orientation="vertical" size={0}><Typography.Text strong>{value}</Typography.Text>{!subscriberUsername && <Typography.Text type="secondary">{item.owner_username}</Typography.Text>}</Space> },
      { title: "类型", dataIndex: "type", render: value => typeLabels[value as CustomRuleType] },
      { title: "应用方式", dataIndex: "mode", render: value => modeLabels[value as CustomRuleMode] },
      { title: "状态", dataIndex: "enabled", render: value => <Tag color={value ? "success" : "default"}>{value ? "已启用" : "已停用"}</Tag> },
      { title: "操作", fixed: "right" as const, width: 170, render: (_: unknown, item: CustomRule) => <Space><Button icon={<EditOutlined />} onClick={() => openRule(item)}>编辑</Button><Button danger icon={<DeleteOutlined />} onClick={() => setRemove(item)}>删除</Button></Space> },
    ]} locale={{ emptyText: "暂无自定义规则" }} /></Card> } : null,
    allowProviders ? { key: "providers", label: `Proxy Provider（${providers.length}）`, children: <Card extra={<Button type="primary" icon={<PlusOutlined />} disabled={!sources.length} onClick={() => openProvider("new")}>新建代理集合</Button>}>
      {!sources.length && <Alert className="form-alert" type="info" showIcon title="请先创建并确认一个外部订阅来源，再把它转换为 Proxy Provider。" />}
      <Table<ProxyProvider> rowKey="id" dataSource={providers} loading={busy === "load"} scroll={{ x: 920 }} columns={[
        { title: "名称", dataIndex: "name", render: (value, item) => <Space orientation="vertical" size={0}><Typography.Text strong>{value}</Typography.Text>{!subscriberUsername && <Typography.Text type="secondary">{item.owner_username}</Typography.Text>}</Space> },
        { title: "外部订阅快照", dataIndex: "external_source_id", render: value => sourceName.get(value) ?? "来源已删除" },
        { title: "处理模式", dataIndex: "process_mode", render: value => value === "mmw" ? <Tag color="processing">服务端</Tag> : <Tag>客户端</Tag> },
        { title: "刷新间隔", dataIndex: "interval", render: value => `${value} 秒` },
        { title: "状态", dataIndex: "enabled", render: value => <Tag color={value ? "success" : "default"}>{value ? "已启用" : "已停用"}</Tag> },
        { title: "操作", fixed: "right" as const, width: 170, render: (_: unknown, item: ProxyProvider) => <Space><Button icon={<EditOutlined />} onClick={() => openProvider(item)}>编辑</Button><Button danger icon={<DeleteOutlined />} onClick={() => setRemove(item)}>删除</Button></Space> },
      ]} locale={{ emptyText: "暂无 Proxy Provider" }} /></Card> } : null,
    allowScripts ? { key: "scripts", label: `覆写脚本（${scripts.length}）`, children: <Card extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => openScript("new")}>新建脚本</Button>}>
      <Alert className="form-alert" type="info" showIcon title="脚本在隔离的 QuickJS 子进程中运行，硬超时约 5 秒；没有 Node.js、浏览器、网络或文件系统 API。" />
      <Table<OverrideScript> rowKey="id" dataSource={scripts} loading={busy === "load"} scroll={{ x: 880 }} columns={[
        { title: "名称", dataIndex: "name", render: (value, item) => <Space orientation="vertical" size={0}><Typography.Text strong>{value}</Typography.Text>{!subscriberUsername && <Typography.Text type="secondary">{item.owner_username}</Typography.Text>}</Space> },
        { title: "执行阶段", dataIndex: "hook", render: value => hookLabels[value as OverrideScriptHook] },
        { title: "排序", dataIndex: "sort_order", width: 90 },
        { title: "状态", dataIndex: "enabled", render: value => <Tag color={value ? "success" : "default"}>{value ? "已启用" : "已停用"}</Tag> },
        { title: "操作", fixed: "right" as const, width: 170, render: (_: unknown, item: OverrideScript) => <Space><Button icon={<EditOutlined />} onClick={() => openScript(item)}>编辑</Button><Button danger icon={<DeleteOutlined />} onClick={() => setScriptRemove(item)}>删除</Button></Space> },
      ]} locale={{ emptyText: "暂无覆写脚本" }} />
    </Card> } : null,
  ].filter((item): item is NonNullable<typeof item> => item !== null);
  return <Flex vertical gap="middle" className="page-shell">
    <Flex justify="space-between" align="center" gap={16} wrap>
      <div><Typography.Title level={subscriberUsername ? 3 : 2}>{subscriberUsername ? "我的订阅自定义" : "订阅自定义"}</Typography.Title><Typography.Paragraph type="secondary">按 MMWX 的订阅文件语义，组合 DNS、分流规则、规则集来源、Proxy Provider 和 JavaScript 覆写脚本。</Typography.Paragraph></div>
      <Button icon={<ReloadOutlined />} loading={busy === "load"} onClick={() => void load()}>刷新</Button>
    </Flex>
    <Alert type="info" showIcon title={subscriberUsername
      ? "这些资源只属于当前账户。管理员在订阅配置中开启对应功能后，留空选择会自动应用你全部已启用的资源；Proxy Provider 不公开上游地址。"
      : "资源严格按用户隔离；需要在“订阅管理 → 订阅配置”中显式开启。Proxy Provider 只提供已确认的节点快照，脚本在无外部 API 的隔离进程中执行。"} />
    {error && <Alert type="error" showIcon role="alert" title={error} />}{notice && <Alert type="success" showIcon title={notice} />}
    <Tabs items={tabs} />

    <Modal width={760} open={Boolean(ruleEdit)} title={ruleEdit === "new" ? "新建自定义规则" : "编辑自定义规则"} okText="保存" confirmLoading={busy === "rule"} okButtonProps={{ disabled: !ruleDraft.owner_username || !ruleDraft.name.trim() || !ruleDraft.content.trim() }} onOk={() => void saveRule()} onCancel={() => !busy && setRuleEdit(null)} destroyOnHidden>
      <Form layout="vertical" disabled={busy === "rule"}><Form.Item label="所属用户" required><Select aria-label="规则所属用户" disabled={Boolean(subscriberUsername) || ruleEdit !== "new"} value={ruleDraft.owner_username} options={userOptions} onChange={owner_username => setRuleDraft(value => ({ ...value, owner_username }))} /></Form.Item><Form.Item label="名称" required><Input aria-label="规则名称" maxLength={120} value={ruleDraft.name} onChange={event => setRuleDraft(value => ({ ...value, name: event.target.value }))} /></Form.Item><Flex gap="middle"><Form.Item label="类型" style={{ flex: 1 }}><Select aria-label="规则类型" value={ruleDraft.type} options={Object.entries(typeLabels).map(([value, label]) => ({ value, label }))} onChange={type => setRuleDraft(value => ({ ...value, type }))} /></Form.Item><Form.Item label="应用方式" style={{ flex: 1 }}><Select aria-label="规则应用方式" value={ruleDraft.mode} options={Object.entries(modeLabels).map(([value, label]) => ({ value, label }))} onChange={mode => setRuleDraft(value => ({ ...value, mode }))} /></Form.Item></Flex><Form.Item label="YAML 内容" extra="支持顶层 dns / rules / rule-providers，也可直接填写对应块。"><Input.TextArea aria-label="规则 YAML 内容" rows={12} value={ruleDraft.content} onChange={event => setRuleDraft(value => ({ ...value, content: event.target.value }))} /></Form.Item><Flex justify="space-between"><Typography.Text>启用</Typography.Text><Switch aria-label="启用规则" checked={ruleDraft.enabled} onChange={enabled => setRuleDraft(value => ({ ...value, enabled }))} /></Flex></Form>
    </Modal>

    <Modal width={820} open={Boolean(providerEdit)} title={providerEdit === "new" ? "新建 Proxy Provider" : "编辑 Proxy Provider"} okText="保存" confirmLoading={busy === "provider"} okButtonProps={{ disabled: !providerDraft.owner_username || !providerDraft.external_source_id || !providerDraft.name.trim() }} onOk={() => void saveProvider()} onCancel={() => !busy && setProviderEdit(null)} destroyOnHidden>
      <Form layout="vertical" disabled={busy === "provider"}>
        <Flex gap="middle"><Form.Item label="所属用户" required style={{ flex: 1 }}><Select aria-label="代理集合所属用户" disabled={Boolean(subscriberUsername) || providerEdit !== "new"} value={providerDraft.owner_username} options={userOptions} onChange={owner_username => setProviderDraft(value => ({ ...value, owner_username, external_source_id: "" }))} /></Form.Item><Form.Item label="外部订阅快照" required style={{ flex: 1 }}><Select aria-label="代理集合外部来源" value={providerDraft.external_source_id} options={providerSources.map(source => ({ value: source.id, label: `${source.name} · ${source.available_node_count} 个可用节点` }))} onChange={external_source_id => setProviderDraft(value => ({ ...value, external_source_id }))} /></Form.Item></Flex>
        <Flex gap="middle"><Form.Item label="名称" required style={{ flex: 1 }}><Input aria-label="代理集合名称" maxLength={120} value={providerDraft.name} onChange={event => setProviderDraft(value => ({ ...value, name: event.target.value }))} /></Form.Item><Form.Item label="处理模式" style={{ flex: 1 }}><Select aria-label="代理集合处理模式" value={providerDraft.process_mode} options={[{ value: "client", label: "客户端模式（Mihomo 拉取）" }, { value: "mmw", label: "服务端模式（内联代理组）" }]} onChange={process_mode => setProviderDraft(value => ({ ...value, process_mode }))} /></Form.Item></Flex>
        {providerDraft.process_mode === "client" && <Flex gap="middle"><Form.Item label="客户端刷新间隔（秒）" style={{ flex: 1 }}><InputNumber aria-label="代理集合刷新间隔" min={60} max={604800} style={{ width: "100%" }} value={providerDraft.interval} onChange={interval => setProviderDraft(value => ({ ...value, interval: interval ?? 3600 }))} /></Form.Item><Form.Item label="拉取代理" style={{ flex: 1 }}><Input aria-label="代理集合拉取代理" maxLength={120} value={providerDraft.proxy} onChange={event => setProviderDraft(value => ({ ...value, proxy: event.target.value }))} /></Form.Item></Flex>}
        <Flex gap="middle"><Form.Item label="包含名称正则" extra="与 GeoIP 国家过滤取并集" style={{ flex: 1 }}><Input aria-label="代理集合包含正则" maxLength={1000} value={providerDraft.filter} onChange={event => setProviderDraft(value => ({ ...value, filter: event.target.value }))} /></Form.Item><Form.Item label="排除名称正则" extra="最先执行" style={{ flex: 1 }}><Input aria-label="代理集合排除正则" maxLength={1000} value={providerDraft.exclude_filter} onChange={event => setProviderDraft(value => ({ ...value, exclude_filter: event.target.value }))} /></Form.Item></Flex>
        <Flex gap="middle"><Form.Item label="排除协议（用 | 分隔）" style={{ flex: 1 }}><Input aria-label="代理集合排除协议" maxLength={1000} value={providerDraft.exclude_type} onChange={event => setProviderDraft(value => ({ ...value, exclude_type: event.target.value }))} /></Form.Item><Form.Item label="GeoIP 国家代码" extra="例如 HK,JP；需要部署者配置 IPinfo token" style={{ flex: 1 }}><Input aria-label="代理集合 GeoIP 国家代码" maxLength={512} placeholder="HK,JP" value={providerDraft.geo_ip_filter} onChange={event => setProviderDraft(value => ({ ...value, geo_ip_filter: event.target.value }))} /></Form.Item></Flex>
        {providerDraft.process_mode === "client" && <Form.Item label="客户端请求头 JSON" extra={'Mihomo 官方格式，例如 {"User-Agent":["mihomo"]}；内容会写入订阅，请勿填写秘密。'}><Input.TextArea aria-label="代理集合请求头" rows={4} value={headerText} onChange={event => setHeaderText(event.target.value)} /></Form.Item>}
        {providerDraft.process_mode === "client" && <><Checkbox checked={providerDraft.health_check_enabled} onChange={event => setProviderDraft(value => ({ ...value, health_check_enabled: event.target.checked }))}>启用客户端健康检查</Checkbox>{providerDraft.health_check_enabled && <Flex gap="middle" style={{ marginTop: 16 }}><Form.Item label="健康检查 HTTPS 地址" style={{ flex: 2 }}><Input aria-label="代理集合健康检查地址" value={providerDraft.health_check_url} onChange={event => setProviderDraft(value => ({ ...value, health_check_url: event.target.value }))} /></Form.Item><Form.Item label="间隔（秒）" style={{ flex: 1 }}><InputNumber aria-label="代理集合健康检查间隔" min={30} max={604800} value={providerDraft.health_check_interval} onChange={health_check_interval => setProviderDraft(value => ({ ...value, health_check_interval: health_check_interval ?? 300 }))} /></Form.Item></Flex>}</>}
        <Form.Item label="节点覆写 JSON" extra="客户端模式同时写入 Mihomo override；快照输出和服务端模式会应用到节点。"><Input.TextArea aria-label="代理集合节点覆写" rows={5} value={overrideText} onChange={event => setOverrideText(event.target.value)} /></Form.Item><Flex justify="space-between"><Typography.Text>启用</Typography.Text><Switch aria-label="启用代理集合" checked={providerDraft.enabled} onChange={enabled => setProviderDraft(value => ({ ...value, enabled }))} /></Flex>
      </Form>
    </Modal>
    <Modal width={820} open={Boolean(scriptEdit)} title={scriptEdit === "new" ? "新建覆写脚本" : "编辑覆写脚本"} okText="保存并检查语法" confirmLoading={busy === "script"} okButtonProps={{ disabled: !scriptDraft.owner_username || !scriptDraft.name.trim() || !scriptDraft.content.trim() }} onOk={() => void saveScript()} onCancel={() => !busy && setScriptEdit(null)} destroyOnHidden>
      <Form layout="vertical" disabled={busy === "script"}>
        <Flex gap="middle"><Form.Item label="所属用户" required style={{ flex: 1 }}><Select aria-label="脚本所属用户" disabled={Boolean(subscriberUsername) || scriptEdit !== "new"} value={scriptDraft.owner_username} options={userOptions} onChange={owner_username => setScriptDraft(value => ({ ...value, owner_username }))} /></Form.Item><Form.Item label="名称" required style={{ flex: 1 }}><Input aria-label="脚本名称" maxLength={120} value={scriptDraft.name} onChange={event => setScriptDraft(value => ({ ...value, name: event.target.value }))} /></Form.Item></Flex>
        <Flex gap="middle"><Form.Item label="执行阶段" style={{ flex: 2 }}><Select aria-label="脚本执行阶段" value={scriptDraft.hook} options={Object.entries(hookLabels).map(([value, label]) => ({ value, label }))} onChange={hook => setScriptDraft(value => ({ ...value, hook, content: hook === "post_fetch" ? "function main(config) {\n  return config;\n}" : "function main(proxies) {\n  return proxies;\n}" }))} /></Form.Item><Form.Item label="排序值" extra="数值越小越先执行" style={{ flex: 1 }}><InputNumber aria-label="脚本排序值" min={-10000} max={10000} style={{ width: "100%" }} value={scriptDraft.sort_order} onChange={sort_order => setScriptDraft(value => ({ ...value, sort_order: sort_order ?? 0 }))} /></Form.Item></Flex>
        <Form.Item label="JavaScript" extra="必须提供 main(config) 或 main(proxies)；返回 null 表示保留原值。可用 produce(proxies, target) 转换格式。"><Input.TextArea aria-label="脚本内容" rows={15} maxLength={262144} spellCheck={false} value={scriptDraft.content} onChange={event => setScriptDraft(value => ({ ...value, content: event.target.value }))} /></Form.Item>
        <Alert className="form-alert" type="warning" showIcon title="脚本无法访问网络、文件、环境变量或浏览器对象；运行失败时本次订阅会跳过该脚本并附加固定警告。" />
        <Flex justify="space-between"><Typography.Text>启用</Typography.Text><Switch aria-label="启用覆写脚本" checked={scriptDraft.enabled} onChange={enabled => setScriptDraft(value => ({ ...value, enabled }))} /></Flex>
      </Form>
    </Modal>
    <Modal open={Boolean(remove)} title="确认删除" okText="删除" cancelText="返回" okButtonProps={{ danger: true }} confirmLoading={busy === "delete"} onOk={() => void removeCurrent()} onCancel={() => !busy && setRemove(null)}><Typography.Paragraph>删除后，订阅档案会自动忽略该资源；客户端下一次更新订阅时生效。</Typography.Paragraph></Modal>
    <Modal open={Boolean(scriptRemove)} title="确认删除覆写脚本" okText="删除" cancelText="返回" okButtonProps={{ danger: true }} confirmLoading={busy === "delete-script"} onOk={() => void removeCurrentScript()} onCancel={() => !busy && setScriptRemove(null)}><Typography.Paragraph>删除后，订阅档案中的对应引用会一并清理；客户端下一次更新订阅时生效。</Typography.Paragraph></Modal>
  </Flex>;
}
