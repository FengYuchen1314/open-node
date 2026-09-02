import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Empty, Flex, Form, Input, Modal, Select, Spin, Tabs, Tag, Typography, Upload } from "../../ui";
import { CopyOutlined, DeleteOutlined, DownloadOutlined, EyeOutlined, PlusOutlined, ReloadOutlined, SaveOutlined, UploadOutlined } from "../../ui/icons";
import { zhMessage } from "../../i18n/zh-CN";
import type { SubscriptionTemplate, SubscriptionTemplatePreview, SubscriptionTemplateSettings, SubscriptionTemplateWrite } from "../../domain/subscription-templates";
import { createSubscriptionTemplate, getSubscriptionTemplate, getSubscriptionTemplateStarter, listSubscriptionTemplates, previewSubscriptionTemplate, removeSubscriptionTemplate, subscriptionTemplateDownloadUrl, updateSubscriptionTemplate, updateSubscriptionTemplateSettings } from "../../services/subscription-templates";

export interface TemplatesWorkspaceProps { subscriber?: boolean }
type Draft = SubscriptionTemplateWrite & { id: string | null; revision: string };

export default function TemplatesWorkspace({ subscriber = false }: TemplatesWorkspaceProps) {
  if (subscriber) return <Alert type="info" showIcon message="订阅模板由管理员全局维护" description="用户无需管理个人模板；套餐会自动使用管理员选择的模板。" />;
  return <GlobalTemplatesWorkspace />;
}

function GlobalTemplatesWorkspace() {
  const [templates, setTemplates] = useState<SubscriptionTemplate[]>([]);
  const [settings, setSettings] = useState<SubscriptionTemplateSettings | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [preview, setPreview] = useState<SubscriptionTemplatePreview | null>(null);
  const [search, setSearch] = useState(""), [previewUsername, setPreviewUsername] = useState(""), [tab, setTab] = useState("source");
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [success, setSuccess] = useState("");
  const [removing, setRemoving] = useState(false), [confirmName, setConfirmName] = useState("");
  const run = useRef(0);
  const filtered = templates.filter(item => item.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()));

  function accept(item: SubscriptionTemplate) {
    setDraft({ id: item.id, revision: item.revision, name: item.name, format: "clash", content: item.content ?? "", owner_username: null, is_public: true });
    setPreview(null); setTab("source"); setRemoving(false); setConfirmName("");
  }
  async function refresh(selectId?: string | null) {
    const version = ++run.current; setBusy(true); setError("");
    try {
      const library = await listSubscriptionTemplates();
      if (version !== run.current) return;
      setTemplates(library.templates); setSettings(library.settings);
      const id = selectId ?? draft?.id;
      if (id && library.templates.some(item => item.id === id)) accept(await getSubscriptionTemplate(id));
      else if (id) setDraft(null);
    } catch (failure) { if (version === run.current) setError(failure instanceof Error ? failure.message : "无法读取全局模板"); }
    finally { if (version === run.current) setBusy(false); }
  }
  useEffect(() => { void refresh(null); return () => { ++run.current; }; }, []);
  async function select(id: string) {
    const version = ++run.current; setBusy(true); setError("");
    try { const item = await getSubscriptionTemplate(id); if (version === run.current) accept(item); }
    catch (failure) { if (version === run.current) setError(failure instanceof Error ? failure.message : "无法读取模板"); }
    finally { if (version === run.current) setBusy(false); }
  }
  async function createDraft() {
    const version = ++run.current; setBusy(true); setError("");
    try {
      const starter = await getSubscriptionTemplateStarter("clash"); if (version !== run.current) return;
      setDraft({ id: null, revision: "", name: "template.yaml", format: "clash", content: starter.content, owner_username: null, is_public: true }); setPreview(null); setTab("source");
    } catch (failure) { if (version === run.current) setError(failure instanceof Error ? failure.message : "无法创建模板"); }
    finally { if (version === run.current) setBusy(false); }
  }
  async function upload(file: File) {
    if (!/\.ya?ml$/i.test(file.name)) { setError("请选择 .yaml 或 .yml 文件"); return; }
    if (file.size > 2 * 1024 * 1024) { setError("模板文件不能超过 2 MiB"); return; }
    try { setDraft({ id: null, revision: "", name: file.name, format: "clash", content: await file.text(), owner_username: null, is_public: true }); setPreview(null); setTab("source"); }
    catch { setError("无法读取模板文件"); }
  }
  function patch(change: Partial<Draft>) { setDraft(value => value ? { ...value, ...change } : value); setPreview(null); }
  async function save() {
    if (!draft?.name.trim() || !draft.content || busy) return;
    const version = ++run.current; setBusy(true); setError(""); setSuccess("");
    const payload: SubscriptionTemplateWrite = { name: draft.name.trim(), format: "clash", content: draft.content, owner_username: null, is_public: true };
    try {
      const saved = draft.id ? await updateSubscriptionTemplate(draft.id, payload, draft.revision) : await createSubscriptionTemplate(payload);
      if (version !== run.current) return; await refresh(saved.id); setSuccess(`${saved.name} 已保存为全局模板`);
    } catch (failure) { if (version === run.current) setError(failure instanceof Error ? failure.message : "保存模板失败"); setBusy(false); }
  }
  async function saveDefault(value: string) {
    if (!settings || busy) return;
    const version = ++run.current; setBusy(true); setError(""); setSuccess("");
    try {
      const updated = await updateSubscriptionTemplateSettings({ ...settings, clash_template_id: value || null }, null);
      if (version === run.current) { setSettings(updated); setSuccess("全局默认模板已更新"); }
    } catch (failure) { if (version === run.current) setError(failure instanceof Error ? failure.message : "保存全局默认模板失败"); }
    finally { if (version === run.current) setBusy(false); }
  }
  async function renderPreview() {
    if (!draft || busy) return; const version = ++run.current; setBusy(true); setError("");
    try { const value = await previewSubscriptionTemplate("clash", draft.content, previewUsername || null); if (version === run.current) { setPreview(value); setTab("preview"); } }
    catch (failure) { if (version === run.current) setError(failure instanceof Error ? failure.message : "预览模板失败"); }
    finally { if (version === run.current) setBusy(false); }
  }
  function duplicate() { if (draft) setDraft({ ...draft, id: null, revision: "", name: `${draft.name.replace(/\.ya?ml$/i, "")}-copy.yaml` }); }
  async function remove() {
    if (!draft?.id || confirmName !== draft.name || busy) return; const version = ++run.current; setBusy(true); setError("");
    try { await removeSubscriptionTemplate(draft.id, draft.revision, confirmName); if (version !== run.current) return; setDraft(null); await refresh(null); setSuccess("全局模板已删除"); }
    catch (failure) { if (version === run.current) setError(failure instanceof Error ? failure.message : "删除模板失败"); setBusy(false); }
  }

  return <Flex vertical gap="large">
    <Card title="全局默认模板" extra={<Tag color="success">未指定模板的套餐自动使用</Tag>}>
      <Form.Item label="默认 Clash / Mihomo 模板" extra="初始默认模板使用 MATCH,Proxy，全部流量都经过代理。">
        <Select aria-label="全局默认模板" value={settings?.clash_template_id ?? undefined} disabled={busy || !settings} options={templates.map(item => ({ label: item.name, value: item.id }))} onChange={value => void saveDefault(value)} />
      </Form.Item>
    </Card>
    {error && <Alert type="error" showIcon message={zhMessage(error)} />}{success && <Alert type="success" showIcon message={success} />}
    <Flex justify="space-between" align="center" wrap gap="middle">
      <Input.Search aria-label="搜索模板" placeholder="搜索模板" allowClear value={search} onChange={event => setSearch(event.target.value)} />
      <Flex gap="small"><Upload accept=".yaml,.yml" showUploadList={false} beforeUpload={file => { void upload(file); return Upload.LIST_IGNORE; }} disabled={busy}><Button icon={<UploadOutlined />} aria-label="上传模板">上传</Button></Upload><Button icon={<PlusOutlined />} onClick={() => void createDraft()}>新建模板</Button><Button icon={<ReloadOutlined />} loading={busy} onClick={() => void refresh()}>刷新</Button></Flex>
    </Flex>
    <div className="template-workspace-grid">
      <aside><Card title={`模板库（${filtered.length}）`}>{busy && !templates.length ? <Spin /> : !filtered.length ? <Empty description="暂无模板" /> : <Flex vertical gap="small">{filtered.map(item => <Button key={item.id} type={draft?.id === item.id ? "primary" : "default"} block onClick={() => void select(item.id)}>{item.name}</Button>)}</Flex>}</Card></aside>
      <section><Card title={draft?.name || "模板编辑器"}>{draft ? <Flex vertical gap="middle">
        <Flex justify="space-between" wrap gap="small"><Typography.Text type="secondary">Clash / Mihomo YAML · 全局共享</Typography.Text><Flex gap="small"><Button icon={<CopyOutlined />} aria-label="复制模板" onClick={duplicate} /><Button icon={<DownloadOutlined />} aria-label="下载模板" href={draft.id ? subscriptionTemplateDownloadUrl(draft.id) : undefined} disabled={!draft.id} download /><Button danger icon={<DeleteOutlined />} aria-label="移除模板" disabled={!draft.id} onClick={() => { setConfirmName(""); setRemoving(true); }} /></Flex></Flex>
        <Form.Item label="文件名"><Input aria-label="文件名" value={draft.name} maxLength={160} onChange={event => patch({ name: event.target.value })} /></Form.Item>
        <Tabs activeKey={tab} onChange={setTab} items={[
          { key: "source", label: "源码", children: <Input.TextArea aria-label="模板源码" rows={20} value={draft.content} spellCheck={false} onChange={event => patch({ content: event.target.value })} /> },
          { key: "preview", label: "预览", children: <><Input.TextArea aria-label="渲染预览" rows={20} value={preview?.content ?? ""} readOnly spellCheck={false} />{preview && <Flex gap="small" wrap><Tag color="success">包含 {preview.included_nodes} 个节点</Tag>{preview.excluded_nodes > 0 && <Tag color="warning">排除 {preview.excluded_nodes} 个节点</Tag>}</Flex>}</> },
        ]} />
        <Form.Item label="按用户预览（可选）"><Input aria-label="预览用户" value={previewUsername} onChange={event => setPreviewUsername(event.target.value)} /></Form.Item>
        <Flex justify="end" gap="small"><Button icon={<EyeOutlined />} onClick={() => void renderPreview()}>预览</Button><Button type="primary" icon={<SaveOutlined />} loading={busy} disabled={!draft.name.trim() || !draft.content} onClick={() => void save()}>保存</Button></Flex>
      </Flex> : <Empty description="选择或新建一个全局模板" />}</Card></section>
    </div>
    <Modal open={removing} title="删除全局模板" okText="删除" okButtonProps={{ danger: true, disabled: confirmName !== draft?.name }} onCancel={() => setRemoving(false)} onOk={() => void remove()}><Typography.Paragraph>如果套餐正在使用此模板，删除会被拒绝。请输入文件名确认。</Typography.Paragraph><Input aria-label="确认文件名" value={confirmName} onChange={event => setConfirmName(event.target.value)} /></Modal>
  </Flex>;
}
