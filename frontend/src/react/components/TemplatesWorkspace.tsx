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
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "Template library unavailable"); }
    finally { if (run === version.current) setBusy(false); }
  }
  useEffect(() => { void refresh(null); return () => { ++version.current; ++settingsVersion.current; }; }, []);
  async function selectTemplate(id: string) {
    if (busy) return; const run = ++version.current; setBusy(true); clearMessages(); setPreview(null);
    try { const item = await getSubscriptionTemplate(id, subscriber); if (run === version.current) acceptTemplate(item); }
    catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "Template unavailable"); }
    finally { if (run === version.current) setBusy(false); }
  }
  async function newTemplate(format: SubscriptionTemplateFormat) {
    if (busy || !canManage) return;
    const run = ++version.current; setBusy(true); clearMessages();
    try {
      const starter = await getSubscriptionTemplateStarter(format, subscriber); if (run !== version.current) return;
      setSelectedId(null); setDraft({ id: null, revision: "", name: format === "clash" ? "template.yaml" : "template.conf", format, content: starter.content, owner_username: null, is_public: false }); setPreview(null); setEditorTab("source");
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "Starter unavailable"); }
    finally { if (run === version.current) setBusy(false); }
  }
  async function save() {
    if (!draft || !editable || busy || !draft.name.trim() || !draft.content) return;
    const run = ++version.current; setBusy(true); clearMessages();
    try {
      const payload: SubscriptionTemplateWrite = { name: draft.name.trim(), format: draft.format, content: draft.content, owner_username: subscriber ? null : draft.owner_username, is_public: subscriber ? false : draft.is_public };
      const saved = draft.id ? await updateSubscriptionTemplate(draft.id, payload, draft.revision, subscriber) : await createSubscriptionTemplate(payload, subscriber);
      if (run !== version.current) return;
      await refresh(saved.id, run); if (run === version.current) setSuccess(`${saved.name} saved`);
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "Template save failed"); }
    finally { if (run === version.current) setBusy(false); }
  }
  async function runPreview() {
    if (!draft || busy || !canManage) return;
    const run = ++version.current; setBusy(true); clearMessages(); setPreview(null);
    try { const value = await previewSubscriptionTemplate(draft.format, draft.content, subscriber ? null : previewUsername || null, subscriber); if (run === version.current) { setPreview(value); setEditorTab("preview"); } }
    catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "Template preview failed"); }
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
      await refresh(null, run); if (run === version.current) setSuccess("Template removed");
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "Template removal failed"); }
    finally { if (run === version.current) setBusy(false); }
  }
  useEffect(() => {
    const run = ++settingsVersion.current; setSettingsBusy(true); setSettings(null);
    void getSubscriptionTemplateSettings(subscriber ? null : settingsUsername || null, subscriber)
      .then(value => { if (run === settingsVersion.current) setSettings(value); })
      .catch(failure => { if (run === settingsVersion.current) setError(failure instanceof Error ? failure.message : "Template settings unavailable"); })
      .finally(() => { if (run === settingsVersion.current) setSettingsBusy(false); });
    return () => { ++settingsVersion.current; };
  }, [settingsUsername, subscriber]);
  async function saveSettings() {
    if (!settings || settingsBusy || (subscriber && !settings.enabled)) return;
    const run = ++settingsVersion.current; setSettingsBusy(true); clearMessages();
    try {
      const value = await updateSubscriptionTemplateSettings(settings, subscriber ? null : settingsUsername || null, subscriber);
      if (run !== settingsVersion.current) return; setSettings(value); setSuccess(settingsUsername ? `Settings saved for ${settingsUsername}` : "Template defaults saved");
    } catch (failure) { if (run === settingsVersion.current) setError(failure instanceof Error ? failure.message : "Template settings save failed"); }
    finally { if (run === settingsVersion.current) setSettingsBusy(false); }
  }
  async function upload(file: File) {
    if (busy || !canManage) return;
    if (file.size > 2 * 1024 * 1024) { setError("Template files are limited to 2 MiB"); return; }
    const format = /\.conf$/i.test(file.name) ? "surge" : /\.ya?ml$/i.test(file.name) ? "clash" : null;
    if (!format) { setError("Choose a .yaml, .yml, or .conf file"); return; }
    const run = ++version.current; setBusy(true); clearMessages();
    try {
      const content = await file.text(); if (run !== version.current) return;
      setSelectedId(null); setDraft({ id: null, revision: "", name: file.name, format, content, owner_username: null, is_public: false }); setPreview(null); setEditorTab("source");
    } catch { if (run === version.current) setError("Unable to read template file"); }
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
    <Flex justify="space-between" align="center" wrap gap="middle"><div><Typography.Title level={2}>Subscription templates</Typography.Title><Typography.Text type="secondary">{templates.length} files</Typography.Text></div>
      <Flex gap="small"><Upload accept=".yaml,.yml,.conf" showUploadList={false} beforeUpload={file => { void upload(file); return Upload.LIST_IGNORE; }} disabled={busy || !canManage}><Button icon={<UploadOutlined />} aria-label="Upload template" disabled={busy || !canManage} /></Upload>
        <Dropdown menu={{ items: formats.map(format => ({ label: format.label, key: format.value })), onClick: ({ key }) => void newTemplate(key as SubscriptionTemplateFormat) }} disabled={busy || !canManage}><Button icon={<PlusOutlined />} aria-label="New template" disabled={busy || !canManage} /></Dropdown>
        <Button icon={<ReloadOutlined />} aria-label="Refresh templates" loading={busy} disabled={busy} onClick={() => void refresh()} />
      </Flex>
    </Flex>
    {error && <Alert type="error" title={error} showIcon />}{success && <Alert type="success" title={success} showIcon />}
    <section aria-label="Template defaults"><Card title={subscriber ? "My defaults" : settingsUsername ? "Subscriber permission" : "System defaults"}>
      <Form layout="vertical" preserve={false}>
        {!subscriber && <Form.Item label="Subscriber"><Select aria-label="Subscriber" allowClear showSearch optionFilterProp="label" value={settingsUsername || undefined} options={userOptions} disabled={settingsBusy} onChange={value => { setSettings(null); setSettingsUsername(value ?? ""); }} /></Form.Item>}
        {settingsBusy && <Spin />}{settings && <>
          {!subscriber && !!settingsUsername && <Form.Item label="Allow personal templates"><Switch aria-label="Allow personal templates" checked={settings.enabled} disabled={settingsBusy} onChange={enabled => patchSettings({ enabled })} /></Form.Item>}
          {subscriber && <Tag color={settings.enabled ? "success" : "warning"}>{settings.enabled ? "Editing enabled" : "Read only"}</Tag>}
          <Row gutter={16}>{(["clash", "surge"] as const).map(format => <Col xs={24} md={12} key={format}><Form.Item label={`${format === "clash" ? "Clash" : "Surge"} default`}><Select aria-label={`${format === "clash" ? "Clash" : "Surge"} default`} allowClear value={settings[`${format}_template_id`] ?? undefined} options={settingOptions(format)} disabled={settingsBusy || (subscriber && !settings.enabled)} onChange={value => patchSettings({ [`${format}_template_id`]: value ?? null })} /></Form.Item></Col>)}</Row>
          <Button type="primary" icon={<SaveOutlined />} aria-label="Save defaults" loading={settingsBusy} disabled={settingsBusy || (subscriber && !settings.enabled)} onClick={() => void saveSettings()}>Save defaults</Button>
        </>}
      </Form>
    </Card></section>
    <Row gutter={[24, 24]}>
      <Col xs={24} lg={7}><aside aria-label="Template library"><Card><Flex vertical gap="middle"><Form.Item label="Search"><Input aria-label="Search" allowClear value={search} onChange={event => setSearch(event.target.value)} /></Form.Item>
        <Radio.Group aria-label="Template format" value={formatFilter} onChange={event => setFormatFilter(event.target.value)} optionType="button" options={[{ label: "All", value: "all" }, { label: "Clash", value: "clash" }, { label: "Surge", value: "surge" }]} />
        {!filteredTemplates.length && <Empty description="No templates" />}
        {filteredTemplates.map(item => <Card key={item.id} size="small"><Button type={item.id === selectedId ? "primary" : "link"} block disabled={busy} onClick={() => void selectTemplate(item.id)}>{item.name}</Button><Typography.Text type="secondary">{item.format === "clash" ? "Clash" : "Surge"} · {item.owner_username || "System"} · {Math.max(1, Math.ceil(item.size_bytes / 1024))} KiB</Typography.Text>{item.is_public && <Tag>Public</Tag>}</Card>)}
      </Flex></Card></aside></Col>
      <Col xs={24} lg={17}><section aria-label="Template editor"><Card>{draft ? <Flex vertical gap="middle">
        <Flex justify="space-between" align="center" wrap><div><Typography.Title level={4}>{draft.name || "Untitled template"}</Typography.Title><Typography.Text type="secondary">{draft.format === "clash" ? "Clash / Mihomo YAML" : "Surge profile"}</Typography.Text></div>
          <Flex gap="small"><Button icon={<CopyOutlined />} aria-label="Duplicate template" disabled={busy || !canManage} onClick={duplicate} /><Button icon={<DownloadOutlined />} aria-label="Download template" href={draft.id ? subscriptionTemplateDownloadUrl(draft.id, subscriber) : undefined} disabled={!draft.id} download /><Button danger icon={<DeleteOutlined />} aria-label="Remove template" disabled={busy || !draft.id || !editable} onClick={() => { setConfirmName(""); setRemoveOpen(true); }} /></Flex>
        </Flex>
        <Form layout="vertical" preserve={false} disabled={busy || !editable}>
          <Row gutter={16}><Col xs={24} md={12}><Form.Item label="Filename"><Input aria-label="Filename" value={draft.name} maxLength={160} onChange={event => patchDraft({ name: event.target.value })} /></Form.Item></Col><Col xs={24} md={12}><Form.Item label="Format"><Select aria-label="Format" value={draft.format} options={formats} disabled={busy || !editable || !!draft.id} onChange={format => patchDraft({ format })} /></Form.Item></Col></Row>
          {!subscriber && <Row gutter={16}><Col xs={24} md={18}><Form.Item label="Owner"><Select aria-label="Owner" value={draft.owner_username ?? ""} options={[{ label: "System", value: "" }, ...userOptions]} onChange={value => patchDraft({ owner_username: value || null })} /></Form.Item></Col><Col xs={24} md={6}><Form.Item label="Public"><Switch aria-label="Public" checked={draft.is_public} onChange={is_public => patchDraft({ is_public })} /></Form.Item></Col></Row>}
        </Form>
        <Tabs activeKey={editorTab} onChange={setEditorTab} items={[
          { key: "source", label: "Source", children: <Input.TextArea aria-label="Template source" rows={18} value={draft.content} readOnly={!editable} disabled={busy} spellCheck={false} onChange={event => patchDraft({ content: event.target.value })} /> },
          { key: "preview", label: "Preview", children: <Flex vertical gap="small"><Input.TextArea aria-label="Rendered preview" rows={18} readOnly value={preview?.content ?? ""} spellCheck={false} />{preview && <><Flex gap="small" wrap><Tag color="success">{preview.included_nodes} included</Tag>{!!preview.excluded_nodes && <Tag color="warning">{preview.excluded_nodes} excluded</Tag>}</Flex>{preview.warnings.map(warning => <Alert type="warning" key={warning} title={warning} showIcon />)}</>}</Flex> },
        ]} />
        {!subscriber && <Form.Item label="Preview subscriber"><Select aria-label="Preview subscriber" allowClear showSearch optionFilterProp="label" options={userOptions} value={previewUsername || undefined} disabled={busy} onChange={value => { setPreviewUsername(value ?? ""); setPreview(null); }} /></Form.Item>}
        <Flex justify="end" gap="small"><Button icon={<EyeOutlined />} aria-label="Preview" loading={busy} disabled={busy || !canManage} onClick={() => void runPreview()}>Preview</Button><Button type="primary" icon={<SaveOutlined />} aria-label="Save" loading={busy} disabled={busy || !editable || !draft.name.trim() || !draft.content} onClick={() => void save()}>Save</Button></Flex>
      </Flex> : <Empty description="Select or create a template" />}</Card></section></Col>
    </Row>
    <Modal open={removeOpen} title="Remove template" destroyOnHidden mask={{ closable: !busy }} keyboard={!busy} closable={!busy} onCancel={() => !busy && setRemoveOpen(false)} okText="Remove" confirmLoading={busy} okButtonProps={{ "aria-label": "Remove", "aria-busy": busy, danger: true, disabled: confirmName !== draft?.name || !draft?.id || !editable }} cancelButtonProps={{ disabled: busy }} onOk={() => void remove()}>
      <Typography.Paragraph>{draft?.name}</Typography.Paragraph><Form.Item label="Confirm filename"><Input aria-label="Confirm filename" value={confirmName} disabled={busy} onChange={event => setConfirmName(event.target.value)} /></Form.Item>
    </Modal>
  </Flex>;
}
