import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from "../../ui/icons";
import { Alert, Button, Form, Input, Modal, Select, Space, Table, Typography } from "../../ui";
import { useEffect, useRef, useState } from "react";

import { certificateRequest, type CertificateCapabilities, type DNSProvider } from "../../services/certificates";
import { zhMessage } from "../../i18n/zh-CN";

const providerNames: Record<string, string> = {
  cloudflare: "Cloudflare",
  alidns: "阿里云 DNS",
  tencentcloud: "腾讯云 DNSPod",
  dnspod: "DNSPod Token",
  godaddy: "GoDaddy",
  namesilo: "NameSilo",
};

interface DNSProvidersPanelProps {
  onUpdated?: () => void;
}

export default function DNSProvidersPanel({ onUpdated }: DNSProvidersPanelProps) {
  const request = useRef(0);
  const mounted = useRef(true);
  const [providers, setProviders] = useState<DNSProvider[]>([]);
  const [types, setTypes] = useState<CertificateCapabilities["providers"]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [removing, setRemoving] = useState<DNSProvider | null>(null);
  const [form, setForm] = useState({ name: "", provider: "cloudflare", credentials: {} as Record<string, string> });
  const selected = types.find(item => item.id === form.provider);
  const canSave = Boolean(form.name.trim() && selected && selected.required.every(key => form.credentials[key]?.trim()));

  async function refresh() {
    const run = ++request.current;
    setLoading(true); setError("");
    try {
      const [catalog, capabilities] = await Promise.all([
        certificateRequest<{ providers: DNSProvider[] }>("/providers"),
        certificateRequest<CertificateCapabilities>("/capabilities"),
      ]);
      if (!mounted.current || run !== request.current) return;
      setProviders(catalog.providers);
      setTypes(capabilities.providers);
      setForm(value => capabilities.providers.some(item => item.id === value.provider) ? value : { ...value, provider: capabilities.providers[0]?.id ?? "", credentials: {} });
    } catch (failure) {
      if (mounted.current && run === request.current) setError(zhMessage(failure, "无法读取 DNS 服务商设置。"));
    } finally {
      if (mounted.current && run === request.current) setLoading(false);
    }
  }

  useEffect(() => {
    mounted.current = true; void refresh();
    return () => { mounted.current = false; request.current += 1; };
  }, []);

  function edit(provider?: DNSProvider) {
    setEditing(provider?.id ?? null);
    setForm({ name: provider?.name ?? "", provider: provider?.provider ?? types[0]?.id ?? "cloudflare", credentials: {} });
    setError(""); setNotice(""); setOpen(true);
  }

  async function save() {
    if (!canSave || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const credentials = Object.fromEntries(Object.entries(form.credentials).map(([key, value]) => [key, value.trim()]).filter(([, value]) => value));
      await certificateRequest(`/providers${editing ? `/${editing}` : ""}`, editing ? "PUT" : "POST", { name: form.name.trim(), provider: form.provider, credentials });
      if (!mounted.current) return;
      setOpen(false); setNotice(editing ? "DNS 服务商凭据已更新。" : "DNS 服务商已添加。");
      await refresh(); onUpdated?.();
    } catch (failure) {
      if (mounted.current) setError(zhMessage(failure, "保存 DNS 服务商失败。"));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  async function remove() {
    if (!removing || busy) return;
    const target = removing;
    setBusy(true); setError(""); setNotice("");
    try {
      await certificateRequest(`/providers/${target.id}`, "DELETE");
      if (!mounted.current) return;
      setRemoving(null); setNotice(`DNS 服务商“${target.name}”已删除。`);
      await refresh(); onUpdated?.();
    } catch (failure) {
      if (mounted.current) setError(zhMessage(failure, "删除 DNS 服务商失败；请先解除正在使用它的 DDNS 配置。"));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  return <Space orientation="vertical" size="middle" style={{ width: "100%" }} data-testid="dns-providers-panel">
    <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
      <div><Typography.Title level={4} style={{ margin: 0 }}>DNS 服务商凭据</Typography.Title><Typography.Text type="secondary">凭据用于 DDNS 记录更新；这里不提供证书签发或证书管理。</Typography.Text></div>
      <Space><Button icon={<ReloadOutlined />} aria-label="刷新 DNS 服务商" loading={loading} disabled={busy} onClick={() => void refresh()} /><Button type="primary" icon={<PlusOutlined />} aria-label="添加 DNS 服务商" disabled={busy || !types.length} onClick={() => edit()}>添加 DNS 服务商</Button></Space>
    </Space>
    {error && <Alert type="error" showIcon title={error} />}{notice && <Alert type="success" showIcon title={notice} />}
    <Table<DNSProvider> rowKey="id" dataSource={providers} loading={loading} pagination={false} locale={{ emptyText: "暂无 DNS 服务商" }} scroll={{ x: 560 }} columns={[
      { title: "名称", dataIndex: "name" },
      { title: "服务商", key: "provider", render: (_, row) => providerNames[row.provider] ?? row.provider },
      { title: "已保存字段", key: "fields", render: (_, row) => row.credential_fields.join("、") || "—" },
      { title: "操作", key: "actions", render: (_, row) => <Space><Button icon={<EditOutlined />} aria-label={`更新 DNS 服务商 ${row.name}`} disabled={busy} onClick={() => edit(row)}>更新凭据</Button><Button danger icon={<DeleteOutlined />} aria-label={`删除 DNS 服务商 ${row.name}`} disabled={busy} onClick={() => setRemoving(row)}>删除</Button></Space> },
    ]} />
    <Modal open={open} title={editing ? "更新 DNS 服务商凭据" : "添加 DNS 服务商"} destroyOnHidden closable={!busy} mask={{ closable: !busy }} keyboard={!busy} onCancel={() => !busy && setOpen(false)} onOk={() => void save()} okText="保存" confirmLoading={busy} okButtonProps={{ disabled: !canSave, "aria-label": "保存 DNS 服务商" }}>
      <Form layout="vertical" disabled={busy} onFinish={() => void save()}>
        <Form.Item label="服务商名称" required><Input aria-label="服务商名称" maxLength={120} value={form.name} onChange={event => setForm(value => ({ ...value, name: event.target.value }))} /></Form.Item>
        <Form.Item label="DNS 服务商类型" required><Select aria-label="DNS 服务商类型" value={form.provider || undefined} disabled={Boolean(editing)} options={types.map(item => ({ value: item.id, label: providerNames[item.id] ?? item.id }))} onChange={provider => setForm(value => ({ ...value, provider, credentials: {} }))} /></Form.Item>
        {(selected?.fields ?? []).map(field => <Form.Item key={field} label={field} required={selected?.required.includes(field)}>{field.endsWith("ENDPOINT") ? <Input aria-label={field} type="url" autoComplete="off" value={form.credentials[field] ?? ""} onChange={event => setForm(value => ({ ...value, credentials: { ...value.credentials, [field]: event.target.value } }))} /> : <Input.Password aria-label={field} autoComplete="new-password" value={form.credentials[field] ?? ""} onChange={event => setForm(value => ({ ...value, credentials: { ...value.credentials, [field]: event.target.value } }))} />}</Form.Item>)}
      </Form>
    </Modal>
    <Modal open={Boolean(removing)} title="删除 DNS 服务商？" destroyOnHidden closable={!busy} mask={{ closable: !busy }} keyboard={!busy} onCancel={() => !busy && setRemoving(null)} onOk={() => void remove()} okText="删除" confirmLoading={busy} okButtonProps={{ danger: true, "aria-label": "确认删除 DNS 服务商" }}><Typography.Paragraph>将删除“{removing?.name}”及其加密凭据。正在使用该服务商的 DDNS 配置会阻止删除。</Typography.Paragraph></Modal>
  </Space>;
}
