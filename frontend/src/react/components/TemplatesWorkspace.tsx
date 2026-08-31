import { zhMessage } from "../../i18n/zh-CN";
import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Col, Dropdown, Empty, Flex, Form, Input, Modal, Radio, Row, Select, Spin, Switch, Tabs, Tag, Typography, Upload } from "antd";
import { CopyOutlined, DeleteOutlined, DownloadOutlined, EyeOutlined, PlusOutlined, ReloadOutlined, SaveOutlined, UploadOutlined } from "@ant-design/icons";
import type { SubscriptionTemplate, SubscriptionTemplateFormat, SubscriptionTemplatePreview, SubscriptionTemplateSettings, SubscriptionTemplateWrite } from "../../domain/subscription-templates";
import { useSubscriberSession } from "../hooks/useSession";
import { createSubscriptionTemplate, getSubscriptionTemplate, getSubscriptionTemplateSettings, getSubscriptionTemplateStarter, listSubscriptionTemplates, previewSubscriptionTemplate, removeSubscriptionTemplate, subscriptionTemplateDownloadUrl, updateSubscriptionTemplate, updateSubscriptionTemplateSettings } from "../../services/subscription-templates";
import { listProductUsers } from "../../services/subscriptions";

export interface TemplatesWorkspaceProps { subscriber?: boolean }
type TemplateDraft = SubscriptionTemplateWrite & { id: string | null; revision: string };
const formats = [{ label: "Clash / Mihomo", value: "clash" as const }, { label: "Surge", value: "surge" as const }];
export default function TemplatesWorkspace({ subscriber = false }: TemplatesWorkspaceProps) {
  const { session } = useSubscriberSession();
  return <WorkspaceContent key={subscriber ? `subscriber:${session?.username ?? ""}` : "administrator"} subscriber={subscriber} subscriberUsername={subscriber ? session?.username ?? null : null} />;
}
function WorkspaceContent({ subscriber, subscriberUsername }: { subscriber: boolean; subscriberUsername: string | null }) {
  const [templates, setTemplates] = useState<SubscriptionTemplate[]>([]), [settings, setSettings] = useState<SubscriptionTemplateSettings | null>(null), [canManage, setCanManage] = useState(false);
  const [users, setUsers] = useState<{ username: string; display_name: string; removal_id?: string | null }[]>([]), [settingsUsername, setSettingsUsername] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null), [draft, setDraft] = useState<TemplateDraft | null>(null), [preview, setPreview] = useState<SubscriptionTemplatePreview | null>(null);
  const [previewUsername, setPreviewUsername] = useState(""), [search, setSearch] = useState(""), [formatFilter, setFormatFilter] = useState<SubscriptionTemplateFormat | "all">("all");
  const [busy, setBusy] = useState(false), [settingsBusy, setSettingsBusy] = useState(false), [error, setError] = useState(""), [success, setSuccess] = useState("");
  const [editorTab, setEditorTab] = useState("source"), [removeOpen, setRemoveOpen] = useState(false), [confirmName, setConfirmName] = useState("");
  const version = useRef(0), settingsVersion = useRef(0), settingsScope = useRef(settingsUsername); settingsScope.current = settingsUsername;
  const current = templates.find(item => item.id === selectedId), editable = canManage && (!draft?.id || !!current?.editable);
  const userOptions = users.filter(user => !user.removal_id).map(user => ({ label: user.display_name ? `${user.display_name} (${user.username})` : user.username, value: user.username }));
  const settingsOwner = subscriber ? subscriberUsername : settingsUsername || null;
  const settingOptions = (format: SubscriptionTemplateFormat) => templates.filter(item => item.format === format && (!settingsOwner || item.owner_username === settingsOwner)).map(item => ({ label: item.name, value: item.id }));
  const filteredTemplates = templates.filter(item => (formatFilter === "all" || item.format === formatFilter) && item.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  function clearMessages() { setError(""); setSuccess(""); }
  function acceptTemplate(item: SubscriptionTemplate) {
    setSelectedId(item.id); setDraft({ id: item.id, revision: item.revision, name: item.name, format: item.format, content: item.content ?? "", owner_username: item.owner_username, is_public: item.is_public }); setPreview(null); setEditorTab("source"); setRemoveOpen(false); setConfirmName("");
  }
  async function refresh(selectId: string | null = selectedId, existingRun?: number) {
    const run = existingRun ?? ++version.current, settingsRun = settingsVersion.current;
    setBusy(true); clearMessages();
    try {
      const [library, userResponse] = await Promise.all([listSubscriptionTemplates(subscriber), subscriber ? Promise.resolve(null) : listProductUsers()]);
      if (run !== version.current) return;
      setTemplates(library.templates); setCanManage(library.can_manage); setUsers(userResponse?.users ?? []);
      if (!settingsScope.current && settingsRun === settingsVersion.current) setSettings(library.settings);
      if (selectId && library.templates.some(item => item.id === selectId)) {
        const item = await getSubscriptionTemplate(selectId, subscriber); if (run !== version.current) return; acceptTemplate(item);
      } else if (selectId) { setSelectedId(null); setDraft(null); setPreview(null); }
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "无法读取订阅模板库"); }
    finally { if (run === version.current) setBusy(false); }
  }
  useEffect(() => { void refresh(null); return () => { ++version.current; ++settingsVersion.current; }; }, []);
  async function selectTemplate(id: string) {
    if (busy) return; const run = ++version.current; setBusy(true); clearMessages(); setPreview(null);
    try { const item = await getSubscriptionTemplate(id, subscriber); if (run === version.current) acceptTemplate(item); }
    catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "无法读取订阅模板"); }
    finally { if (run === version.current) setBusy(false); }
  }
  async function newTemplate(format: SubscriptionTemplateFormat) {
    if (busy || !canManage) return;
    const run = ++version.current; setBusy(true); clearMessages();
    try {
      const starter = await getSubscriptionTemplateStarter(format, subscriber); if (run !== version.current) return;
      setSelectedId(null); setDraft({ id: null, revision: "", name: format === "clash" ? "template.yaml" : "template.conf", format, content: starter.content, owner_username: null, is_public: false }); setPreview(null); setEditorTab("source");
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "无法读取初始模板"); }
    finally { if (run === version.current) setBusy(false); }
  }
  async function save() {
    if (!draft || !editable || busy || !draft.name.trim() || !draft.content) return;
    const run = ++version.current; setBusy(true); clearMessages();
    try {
      const payload: SubscriptionTemplateWrite = { name: draft.name.trim(), format: draft.format, content: draft.content, owner_username: subscriber ? null : draft.owner_username, is_public: subscriber ? false : draft.is_public };
      const saved = draft.id ? await updateSubscriptionTemplate(draft.id, payload, draft.revision, subscriber) : await createSubscriptionTemplate(payload, subscriber);
      if (run !== version.current) return;
      await refresh(saved.id, run); if (run === version.current) setSuccess(`${saved.name} 已保存`);
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "保存订阅模板失败"); }
    finally { if (run === version.current) setBusy(false); }
  }
  async function runPreview() {
    if (!draft || busy || !canManage) return;
    const run = ++version.current; setBusy(true); clearMessages(); setPreview(null);
    try { const value = await previewSubscriptionTemplate(draft.format, draft.content, subscriber ? null : previewUsername || null, subscriber); if (run === version.current) { setPreview(value); setEditorTab("preview"); } }
    catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "预览订阅模板失败"); }
    finally { if (run === version.current) setBusy(false); }
  }
  function duplicate() {
    if (!draft || busy || !canManage) return;
    ++version.current; const base = draft.name.replace(/\.(ya?ml|conf)$/i, "");
    setSelectedId(null); setDraft({ ...draft, id: null, revision: "", name: `${base}-copy${draft.format === "clash" ? ".yaml" : ".conf"}`, is_public: false }); setPreview(null); setEditorTab("source"); setConfirmName("");
  }
  async function remove() {
    if (busy || !draft?.id || !editable || confirmName !== draft.name) return;
    const run = ++version.current; setBusy(true); clearMessages();
    try {
      await removeSubscriptionTemplate(draft.id, draft.revision, confirmName, subscriber); if (run !== version.current) return;
      setRemoveOpen(false); setConfirmName(""); setSelectedId(null); setDraft(null); setPreview(null);
      await refresh(null, run); if (run === version.current) setSuccess("订阅模板已移除");
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "移除订阅模板失败"); }
    finally { if (run === version.current) setBusy(false); }
  }
  useEffect(() => {
    const run = ++settingsVersion.current; setSettingsBusy(true); setSettings(null);
    void getSubscriptionTemplateSettings(subscriber ? null : settingsUsername || null, subscriber)
      .then(value => { if (run === settingsVersion.current) setSettings(value); })
      .catch(failure => { if (run === settingsVersion.current) setError(failure instanceof Error ? failure.message : "无法读取订阅模板设置"); })
      .finally(() => { if (run === settingsVersion.current) setSettingsBusy(false); });
    return () => { ++settingsVersion.current; };
  }, [settingsUsername, subscriber]);
  async function saveSettings() {
    if (!settings || settingsBusy || (subscriber && !settings.enabled)) return;
    const run = ++settingsVersion.current; setSettingsBusy(true); clearMessages();
    try {
      const value = await updateSubscriptionTemplateSettings(settings, subscriber ? null : settingsUsername || null, subscriber);
      if (run !== settingsVersion.current) return; setSettings(value); setSuccess(settingsUsername ? `已保存 ${settingsUsername} 的设置` : "订阅模板默认设置已保存");
    } catch (failure) { if (run === settingsVersion.current) setError(failure instanceof Error ? failure.message : "保存订阅模板设置失败"); }
    finally { if (run === settingsVersion.current) setSettingsBusy(false); }
  }
  async function upload(file: File) {
    if (busy || !canManage) return;
    if (file.size > 2 * 1024 * 1024) { setError("模板文件不能超过 2 MiB"); return; }
    const format = /\.conf$/i.test(file.name) ? "surge" : /\.ya?ml$/i.test(file.name) ? "clash" : null;
    if (!format) { setError("请选择 .yaml、.yml 或 .conf 文件"); return; }
    const run = ++version.current; setBusy(true); clearMessages();
    try {
      const content = await file.text(); if (run !== version.current) return;
      setSelectedId(null); setDraft({ id: null, revision: "", name: file.name, format, content, owner_username: null, is_public: false }); setPreview(null); setEditorTab("source");
    } catch { if (run === version.current) setError("无法读取模板文件"); }
    finally { if (run === version.current) setBusy(false); }
  }
  function patchDraft(change: Partial<TemplateDraft>) {
    setDraft(previous => {
      if (!previous) return previous;
      const value = { ...previous, ...change };
      if (change.format && !value.id) value.name = `${value.name.replace(/\.(ya?ml|conf)$/i, "") || "template"}${change.format === "clash" ? ".yaml" : ".conf"}`;
      return value;
    }); setPreview(null);
  }
  function patchSettings(change: Partial<SubscriptionTemplateSettings>) { setSettings(previous => previous ? { ...previous, ...change } : previous); }
  return <Flex vertical gap="large">
    <Flex justify="space-between" align="center" wrap gap="middle"><div><Typography.Title level={2}>订阅模板</Typography.Title><Typography.Text type="secondary">{templates.length} 个文件</Typography.Text></div>
      <Flex gap="small"><Upload accept=".yaml,.yml,.conf" showUploadList={false} beforeUpload={file => { void upload(file); return Upload.LIST_IGNORE; }} disabled={busy || !canManage}><Button icon={<UploadOutlined />} aria-label="上传模板" disabled={busy || !canManage} /></Upload>
        <Dropdown menu={{ items: formats.map(format => ({ label: format.label, key: format.value })), onClick: ({ key }) => void newTemplate(key as SubscriptionTemplateFormat) }} disabled={busy || !canManage}><Button icon={<PlusOutlined />} aria-label="新建模板" disabled={busy || !canManage} /></Dropdown>
        <Button icon={<ReloadOutlined />} aria-label="刷新模板" loading={busy} disabled={busy} onClick={() => void refresh()} />
      </Flex>
    </Flex>
    {error && <Alert type="error" title={zhMessage(error)} showIcon />}{success && <Alert type="success" title={success} showIcon />}
    <section aria-label="模板默认设置"><Card title={subscriber ? "我的默认设置" : settingsUsername ? "用户权限" : "系统默认设置"}>
      <Form layout="vertical" preserve={false}>
        {!subscriber && <Form.Item label="用户"><Select aria-label="用户" allowClear showSearch optionFilterProp="label" value={settingsUsername || undefined} options={userOptions} disabled={settingsBusy} onChange={value => { setSettings(null); setSettingsUsername(value ?? ""); }} /></Form.Item>}
        {settingsBusy && <Spin />}{settings && <>
          {!subscriber && !!settingsUsername && <Form.Item label="允许个人模板"><Switch aria-label="允许个人模板" checked={settings.enabled} disabled={settingsBusy} onChange={enabled => patchSettings({ enabled })} /></Form.Item>}
          {subscriber && <Tag color={settings.enabled ? "success" : "warning"}>{settings.enabled ? "允许编辑" : "只读"}</Tag>}
          <Row gutter={16}>{(["clash", "surge"] as const).map(format => <Col xs={24} md={12} key={format}><Form.Item label={`${format === "clash" ? "Clash" : "Surge"} 默认模板`}><Select aria-label={`${format === "clash" ? "Clash" : "Surge"} 默认模板`} allowClear value={settings[`${format}_template_id`] ?? undefined} options={settingOptions(format)} disabled={settingsBusy || (subscriber && !settings.enabled)} onChange={value => patchSettings({ [`${format}_template_id`]: value ?? null })} /></Form.Item></Col>)}</Row>
          <Button type="primary" icon={<SaveOutlined />} aria-label="保存默认设置" loading={settingsBusy} disabled={settingsBusy || (subscriber && !settings.enabled)} onClick={() => void saveSettings()}>保存默认设置</Button>
        </>}
      </Form>
    </Card></section>
    <Row gutter={[24, 24]}>
      <Col xs={24} lg={7}><aside aria-label="订阅模板库"><Card><Flex vertical gap="middle"><Form.Item label="搜索"><Input aria-label="搜索" allowClear value={search} onChange={event => setSearch(event.target.value)} /></Form.Item>
        <Radio.Group aria-label="模板格式" value={formatFilter} onChange={event => setFormatFilter(event.target.value)} optionType="button" options={[{ label: "全部", value: "all" }, { label: "Clash", value: "clash" }, { label: "Surge", value: "surge" }]} />
        {!filteredTemplates.length && <Empty description="暂无模板" />}
        {filteredTemplates.map(item => <Card key={item.id} size="small"><Button type={item.id === selectedId ? "primary" : "link"} block disabled={busy} onClick={() => void selectTemplate(item.id)}>{item.name}</Button><Typography.Text type="secondary">{item.format === "clash" ? "Clash" : "Surge"} · {item.owner_username || "系统"} · {Math.max(1, Math.ceil(item.size_bytes / 1024))} KiB</Typography.Text>{item.is_public && <Tag>公开</Tag>}</Card>)}
      </Flex></Card></aside></Col>
      <Col xs={24} lg={17}><section aria-label="模板编辑器"><Card>{draft ? <Flex vertical gap="middle">
        <Flex justify="space-between" align="center" wrap><div><Typography.Title level={4}>{draft.name || "未命名模板"}</Typography.Title><Typography.Text type="secondary">{draft.format === "clash" ? "Clash / Mihomo YAML" : "Surge 配置"}</Typography.Text></div>
          <Flex gap="small"><Button icon={<CopyOutlined />} aria-label="复制模板" disabled={busy || !canManage} onClick={duplicate} /><Button icon={<DownloadOutlined />} aria-label="下载模板" href={draft.id ? subscriptionTemplateDownloadUrl(draft.id, subscriber) : undefined} disabled={!draft.id} download /><Button danger icon={<DeleteOutlined />} aria-label="移除模板" disabled={busy || !draft.id || !editable} onClick={() => { setConfirmName(""); setRemoveOpen(true); }} /></Flex>
        </Flex>
        <Form layout="vertical" preserve={false} disabled={busy || !editable}>
          <Row gutter={16}><Col xs={24} md={12}><Form.Item label="文件名"><Input aria-label="文件名" value={draft.name} maxLength={160} onChange={event => patchDraft({ name: event.target.value })} /></Form.Item></Col><Col xs={24} md={12}><Form.Item label="格式"><Select aria-label="格式" value={draft.format} options={formats} disabled={busy || !editable || !!draft.id} onChange={format => patchDraft({ format })} /></Form.Item></Col></Row>
          {!subscriber && <Row gutter={16}><Col xs={24} md={18}><Form.Item label="所属用户"><Select aria-label="所属用户" value={draft.owner_username ?? ""} options={[{ label: "系统", value: "" }, ...userOptions]} onChange={value => patchDraft({ owner_username: value || null })} /></Form.Item></Col><Col xs={24} md={6}><Form.Item label="公开"><Switch aria-label="公开" checked={draft.is_public} onChange={is_public => patchDraft({ is_public })} /></Form.Item></Col></Row>}
        </Form>
        <Tabs activeKey={editorTab} onChange={setEditorTab} items={[
          { key: "source", label: "源码", children: <Input.TextArea aria-label="模板源码" rows={18} value={draft.content} readOnly={!editable} disabled={busy} spellCheck={false} onChange={event => patchDraft({ content: event.target.value })} /> },
          { key: "preview", label: "预览", children: <Flex vertical gap="small"><Input.TextArea aria-label="渲染预览" rows={18} readOnly value={preview?.content ?? ""} spellCheck={false} />{preview && <><Flex gap="small" wrap><Tag color="success">已包含 {preview.included_nodes} 个节点</Tag>{!!preview.excluded_nodes && <Tag color="warning">已排除 {preview.excluded_nodes} 个节点</Tag>}</Flex>{preview.warnings.map(warning => <Alert type="warning" key={warning} title={zhMessage(warning)} showIcon />)}</>}</Flex> },
        ]} />
        {!subscriber && <Form.Item label="预览用户"><Select aria-label="预览用户" allowClear showSearch optionFilterProp="label" options={userOptions} value={previewUsername || undefined} disabled={busy} onChange={value => { setPreviewUsername(value ?? ""); setPreview(null); }} /></Form.Item>}
        <Flex justify="end" gap="small"><Button icon={<EyeOutlined />} aria-label="预览" loading={busy} disabled={busy || !canManage} onClick={() => void runPreview()}>预览</Button><Button type="primary" icon={<SaveOutlined />} aria-label="保存" loading={busy} disabled={busy || !editable || !draft.name.trim() || !draft.content} onClick={() => void save()}>保存</Button></Flex>
      </Flex> : <Empty description="请选择或创建模板" />}</Card></section></Col>
    </Row>
    <Modal open={removeOpen} title="移除模板" destroyOnHidden mask={{ closable: !busy }} keyboard={!busy} closable={!busy} onCancel={() => !busy && setRemoveOpen(false)} okText="移除" confirmLoading={busy} okButtonProps={{ "aria-label": "移除", "aria-busy": busy, danger: true, disabled: confirmName !== draft?.name || !draft?.id || !editable }} cancelButtonProps={{ disabled: busy }} onOk={() => void remove()}>
      <Typography.Paragraph>{draft?.name}</Typography.Paragraph><Form.Item label="确认文件名"><Input aria-label="确认文件名" value={confirmName} disabled={busy} onChange={event => setConfirmName(event.target.value)} /></Form.Item>
    </Modal>
  </Flex>;
}
