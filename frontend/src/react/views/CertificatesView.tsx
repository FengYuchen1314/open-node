import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Checkbox, Col, Collapse, Descriptions, Form, Input, InputNumber, Modal, Row, Select, Space, Switch, Table, Tabs, Tag, Typography } from "../../ui";
import { CloseOutlined, DeleteOutlined, DownloadOutlined, EditOutlined, FolderOpenOutlined, PlusOutlined, ReloadOutlined, StopOutlined, UploadOutlined } from "../../ui/icons";
import { listServers } from "../../services/inventory";
import { certificateRequest, type CertificateCapabilities, type CertificateChallenge, type CertificateDetail, type CertificateVersion, type DNSProvider, type ManagedCertificate, type SelfSignedCertificateInput } from "../../services/certificates";
import type { ServerSummary } from "../../domain/inventory";
import { zhMessage, zhStatus } from "../../i18n/zh-CN";

export interface CertificatesViewProps { onUpdated?: () => void }
type Dialog = "" | "provider" | "certificate" | "self-signed" | "import" | "account" | "revoke";
const emptyCapabilities: CertificateCapabilities = { available: false, account_management: false, revocation: false, directories: [], providers: [], challenge_types: [], webroots: [] };
const challengeLabels: Record<CertificateChallenge, string> = { dns: "DNS-01", standalone: "HTTP-01 / 独立服务", webroot: "HTTP-01 / 网站根目录" };
const reasons = [{ label: "未指定", value: 0 }, { label: "密钥泄露", value: 1 }, { label: "所属关系变更", value: 3 }, { label: "已被替代", value: 4 }, { label: "停止使用", value: 5 }, { label: "权限已撤回", value: 9 }];
const initialCertificateForm = { name: "", domains: "", email: "", challenge_type: "dns" as CertificateChallenge, validation_server_id: "", provider_id: "", webroot_id: "", directory_url: "", accept_terms: false, auto_renew: true, eab_kid: "", eab_hmac_key: "" };
const initialSelfSignedForm = { name: "", domains: "", valid_days: 365 as number | null, confirm_self_signed: false };
const selfSignedWarning = "自签名证书不受浏览器和系统默认信任，不是受信 CA 签发的证书。生成只保存到本地证书库，不会自动部署或替换控制台 HTTPS。请按需在使用端单独信任此证书，不要关闭 TLS 验证。";
function date(value: number | null) { return value ? new Date(value * 1000).toLocaleString("zh-CN") : "-"; }
function color(status: string) { return ["issued", "succeeded"].includes(status) ? "success" : ["failed", "interrupted", "revoked"].includes(status) ? "error" : ["unknown", "revocation_unknown"].includes(status) ? "warning" : "default"; }
function statusLabel(status: string) { return ({ revoked: "已吊销", revocation_pending: "吊销中", revocation_unknown: "尚未确认", updating_account: "账户更新中", not_registered: "未注册", unconfirmed: "尚未确认", unavailable: "不可用", registered: "已注册" } as Record<string, string>)[status] ?? zhStatus(status); }
function needsReissue(row: ManagedCertificate) { return ["revoked", "revocation_unknown", "revocation_pending", "revoking"].includes(row.status); }
function validationNodesFor(capabilities: CertificateCapabilities) { return capabilities.remote_http_available ? (capabilities.validation_nodes ?? []).filter((node) => !node.cleanup_error) : []; }
function hostsFor(capabilities: CertificateCapabilities, challenge: CertificateChallenge) {
  return [{ label: "控制台", value: "", disabled: !capabilities.challenge_types.includes(challenge) }, ...validationNodesFor(capabilities).filter((node) => challenge === "standalone" ? node.standalone : challenge === "webroot" && node.webroots.length > 0).map((node) => ({ label: node.name, value: node.id, disabled: false }))];
}
function normalizeValidation(form: typeof initialCertificateForm, capabilities: CertificateCapabilities) {
  const hosts = hostsFor(capabilities, form.challenge_type);
  const validation_server_id = form.challenge_type === "dns" ? "" : hosts.some((option) => option.value === form.validation_server_id && !option.disabled) ? form.validation_server_id : hosts.find((option) => !option.disabled)?.value ?? "";
  const webroots = validation_server_id ? validationNodesFor(capabilities).find((node) => node.id === validation_server_id)?.webroots ?? [] : capabilities.webroots;
  return { ...form, validation_server_id, webroot_id: webroots.includes(form.webroot_id) ? form.webroot_id : webroots[0] ?? "" };
}

export default function CertificatesView(props: CertificatesViewProps) {
  const [tab, setTab] = useState("certificates");
  const [certificates, setCertificates] = useState<ManagedCertificate[]>([]);
  const [providers, setProviders] = useState<DNSProvider[]>([]);
  const [capabilities, setCapabilities] = useState<CertificateCapabilities>(emptyCapabilities);
  const [servers, setServers] = useState<ServerSummary[]>([]);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<CertificateDetail | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [dialog, setDialog] = useState<Dialog>("");
  const [editingProvider, setEditingProvider] = useState("");
  const [providerForm, setProviderForm] = useState({ name: "", provider: "cloudflare", credentials: {} as Record<string, string> });
  const [form, setForm] = useState({ ...initialCertificateForm });
  const [importForm, setImportForm] = useState({ name: "", cert_pem: "", key_pem: "" });
  const [selfSignedForm, setSelfSignedForm] = useState({ ...initialSelfSignedForm });
  const [accountForm, setAccountForm] = useState({ email: "", eab_action: "keep", eab_kid: "", eab_hmac_key: "" });
  const [revokeForm, setRevokeForm] = useState({ version_id: "", serial: "", directory_url: "", reason: 0, confirm: false });
  const [target, setTarget] = useState({ server_id: "", domain: "", cert_name: "", reload: "nginx", auto_deploy: true });
  const [force, setForce] = useState(false);
  const [confirmation, setConfirmation] = useState<{ title: string; description: string; danger?: boolean; work: () => Promise<unknown> } | null>(null);
  const request = useRef(0);
  const mounted = useRef(true);
  const loadingRef = useRef(false);
  const busyRef = useRef(false);
  const live = useRef({ selected, dialog, confirmation, props });
  live.current = { selected, dialog, confirmation, props };
  const providerFields = capabilities.providers.find((item) => item.id === providerForm.provider);
  const validationNodes = validationNodesFor(capabilities);
  const challengeTypes = [...new Set<CertificateChallenge>([...capabilities.challenge_types, ...(validationNodes.some((node) => node.standalone) ? ["standalone" as const] : []), ...(validationNodes.some((node) => node.webroots.length) ? ["webroot" as const] : [])])];
  const validationOptions = hostsFor(capabilities, form.challenge_type);
  const webrootOptions = form.validation_server_id ? validationNodes.find((node) => node.id === form.validation_server_id)?.webroots ?? [] : capabilities.webroots;
  const wildcardError = form.challenge_type !== "dns" && form.domains.trim().split(/[\s,]+/).some((name) => name.startsWith("*."));
  const canCreate = Boolean(form.name && form.domains.trim() && form.email && form.directory_url && form.accept_terms && !wildcardError && challengeTypes.includes(form.challenge_type) && (form.challenge_type === "dns" ? form.provider_id : validationOptions.some((option) => option.value === form.validation_server_id && !option.disabled)) && (form.challenge_type !== "webroot" || webrootOptions.includes(form.webroot_id)));
  const hasChallenge = providers.length > 0 || challengeTypes.some((type) => type !== "dns");
  const currentVersion = detail?.versions.find((version) => version.id === detail.certificate.version_id);
  const currentRevocation = currentVersion?.revocation;
  const selfSignedNames = selfSignedForm.domains.trim().split(/[\s,]+/).filter(Boolean);
  const canGenerate = capabilities.self_signed === true && Boolean(selfSignedForm.name.trim()) && selfSignedNames.length > 0 && selfSignedNames.length <= 20 && Number.isInteger(selfSignedForm.valid_days) && (selfSignedForm.valid_days ?? 0) >= 1 && (selfSignedForm.valid_days ?? 0) <= 3650 && selfSignedForm.confirm_self_signed;
  const canSaveAccount = Boolean(accountForm.email.trim() && (accountForm.eab_action !== "replace" || (accountForm.eab_kid && accountForm.eab_hmac_key)));
  const canSaveProvider = Boolean(providerForm.name && providerFields?.required.every((key) => providerForm.credentials[key]));
  const serverName = (id: string) => servers.find((server) => server.id === id)?.name ?? id;
  const issuerAvailable = (row: ManagedCertificate) => row.validation_server_id ? Boolean(capabilities.remote_http_available) : capabilities.available;
  const patchForm = (patch: Partial<typeof form>) => setForm((value) => normalizeValidation({ ...value, ...patch }, capabilities));

  async function refresh(replace = false) {
    if (loadingRef.current && !replace) return;
    const generation = ++request.current;
    loadingRef.current = true;
    setLoading(true);
    const current = () => mounted.current && generation === request.current;
    try {
      const [catalog, dns, options, inventory] = await Promise.all([
        certificateRequest<{ certificates: ManagedCertificate[] }>(), certificateRequest<{ providers: DNSProvider[] }>("/providers"), certificateRequest<CertificateCapabilities>("/capabilities"), listServers(),
      ]);
      if (!current()) return;
      setCertificates(catalog.certificates);
      setProviders(dns.providers);
      setCapabilities(options);
      setServers(inventory);
      setForm((value) => normalizeValidation(value, options));
      const id = live.current.selected;
      if (id) {
        const response = await certificateRequest<CertificateDetail>(`/${id}`);
        if (current() && live.current.selected === id) setDetail(response);
      }
    } catch (cause) { if (current()) setError(cause instanceof Error ? cause.message : "证书请求失败"); }
    finally { if (current()) { loadingRef.current = false; setLoading(false); } }
  }
  async function action(work: () => Promise<unknown>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
      if (mounted.current) { live.current.props.onUpdated?.(); await refresh(true); }
    } catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : "证书请求失败"); }
    finally { busyRef.current = false; if (mounted.current) setBusy(false); }
  }
  useEffect(() => {
    mounted.current = true;
    void refresh(true);
    const timer = window.setInterval(() => {
      if (!busyRef.current && !live.current.dialog && !live.current.confirmation && !document.hidden) void refresh();
    }, 5000);
    return () => { mounted.current = false; request.current += 1; window.clearInterval(timer); };
  }, []);

  function closeDialog() {
    setDialog("");
    setProviderForm((value) => ({ ...value, credentials: {} }));
    setImportForm((value) => ({ ...value, cert_pem: "", key_pem: "" }));
    setForm((value) => ({ ...value, eab_kid: "", eab_hmac_key: "" }));
    setAccountForm((value) => ({ ...value, eab_kid: "", eab_hmac_key: "" }));
    setRevokeForm((value) => ({ ...value, confirm: false }));
    setSelfSignedForm({ ...initialSelfSignedForm });
  }
  function closeDetails() { live.current.selected = ""; setSelected(""); setDetail(null); }
  function inspect(row: ManagedCertificate) {
    if (busyRef.current) return;
    live.current.selected = row.id;
    setSelected(row.id);
    setDetail(null);
    setForce(false);
    setTarget((value) => ({ ...value, domain: row.domains[0]?.startsWith("*.") ? "" : row.domains[0] ?? "", cert_name: (row.domains[0] ?? "").replace("*.", "_.").replaceAll(":", "_") }));
    void action(async () => {
      const response = await certificateRequest<CertificateDetail>(`/${row.id}`);
      if (mounted.current && live.current.selected === row.id) setDetail(response);
    });
  }
  function openProvider(provider?: DNSProvider) {
    setEditingProvider(provider?.id ?? "");
    setProviderForm({ name: provider?.name ?? "", provider: provider?.provider ?? "cloudflare", credentials: {} });
    setDialog("provider");
  }
  function openCertificate() {
    setForm(normalizeValidation({ ...initialCertificateForm, challenge_type: providers.length ? "dns" : challengeTypes.find((type) => type !== "dns") ?? "dns", provider_id: providers[0]?.id ?? "", webroot_id: capabilities.webroots[0] ?? "", directory_url: capabilities.directories[0] ?? "" }, capabilities));
    setDialog("certificate");
  }
  function openAccount() {
    setAccountForm({ email: detail?.account?.pending_email ?? detail?.certificate.email ?? "", eab_action: "keep", eab_kid: "", eab_hmac_key: "" });
    setDialog("account");
  }
  function openRevoke(version: CertificateVersion) {
    setRevokeForm({ version_id: version.id, serial: version.details.serial, reason: version.revocation?.reason ?? 0, directory_url: detail?.certificate.directory_url ?? version.revocation?.directory_url ?? "", confirm: false });
    setDialog("revoke");
  }
  function saveDialog() {
    if (dialog === "provider" && !canSaveProvider || dialog === "certificate" && !canCreate || dialog === "self-signed" && !canGenerate || dialog === "account" && !canSaveAccount || dialog === "revoke" && (!revokeForm.confirm || !revokeForm.directory_url) || dialog === "import" && (!importForm.name || !importForm.cert_pem || !importForm.key_pem)) return;
    const id = selected;
    void action(async () => {
      if (dialog === "provider") {
        const credentials = Object.fromEntries(Object.entries(providerForm.credentials).filter(([, value]) => value));
        await certificateRequest(`/providers${editingProvider ? `/${editingProvider}` : ""}`, editingProvider ? "PUT" : "POST", { ...providerForm, credentials });
      } else if (dialog === "certificate") {
        await certificateRequest("", "POST", { ...form, validation_server_id: form.challenge_type === "dns" ? null : form.validation_server_id || null, provider_id: form.challenge_type === "dns" ? form.provider_id : null, webroot_id: form.challenge_type === "webroot" ? form.webroot_id : null, domains: form.domains.trim().split(/[\s,]+/), eab_kid: form.eab_kid || null, eab_hmac_key: form.eab_hmac_key || null });
      } else if (dialog === "self-signed") {
        await certificateRequest("/self-signed", "POST", { name: selfSignedForm.name.trim(), domains: selfSignedNames, valid_days: selfSignedForm.valid_days!, purpose: "server_auth", confirm_self_signed: true } satisfies SelfSignedCertificateInput);
        if (mounted.current) setNotice("自签名证书已生成并保存。未自动部署；可在证书详情中下载，或添加部署目标后按需发布。此证书不会自动续签。");
      } else if (dialog === "account") {
        await certificateRequest(`/${id}/account`, "POST", { email: accountForm.email, eab_action: accountForm.eab_action, ...(accountForm.eab_action === "replace" ? { eab_kid: accountForm.eab_kid, eab_hmac_key: accountForm.eab_hmac_key } : {}) });
      } else if (dialog === "revoke") {
        await certificateRequest(`/${id}/versions/${revokeForm.version_id}/revoke`, "POST", { confirm: true, reason: revokeForm.reason, directory_url: revokeForm.directory_url });
      } else if (dialog === "import") await certificateRequest("/import", "POST", { ...importForm });
      if (mounted.current) closeDialog();
    });
  }
  function queue(row: ManagedCertificate, forced = false) { void action(() => certificateRequest(`/${row.id}/${row.version_id ? "renew" : "issue"}`, "POST", { force: forced })); }
  function confirmRemove(row: ManagedCertificate) {
    setConfirmation({ title: `删除证书“${row.name}”？`, description: "删除此托管证书及其已存储的证书材料？", danger: true, work: async () => { await certificateRequest(`/${row.id}`, "DELETE"); if (live.current.selected === row.id) closeDetails(); } });
  }
  function download(privateKey = false) {
    const id = selected;
    const filename = `${detail?.certificate.domains[0]?.replace("*.", "_.").replaceAll(":", "_") ?? "certificate"}.${privateKey ? "key" : "pem"}`;
    const work = async () => {
      const data = await certificateRequest<{ cert_pem: string; key_pem?: string }>(`/${id}/material?include_private_key=${privateKey}`);
      const url = URL.createObjectURL(new Blob([privateKey ? data.key_pem ?? "" : data.cert_pem], { type: "application/x-pem-file" }));
      try { const link = document.createElement("a"); link.href = url; link.download = filename; link.click(); }
      finally { URL.revokeObjectURL(url); }
    };
    if (privateKey) setConfirmation({ title: "下载私钥？", description: "下载的 PEM 文件含有私钥，请妥善保管。", work });
    else void action(work);
  }
  function saveTarget() {
    if (!target.server_id || !target.domain || !target.cert_name) return;
    void action(() => certificateRequest(`/${selected}/targets`, "POST", { ...target }));
  }
  const actionLabel = (row: ManagedCertificate) => needsReissue(row) ? "重新签发证书" : row.version_id ? "续签证书" : "签发证书";
  const dialogTitle = dialog === "provider" ? editingProvider ? "更换 DNS 凭据" : "添加 DNS 服务商" : dialog === "self-signed" ? "生成自签名证书" : dialog === "import" ? "导入 PEM" : dialog === "account" ? "编辑 ACME 账户" : dialog === "revoke" ? "吊销证书版本" : "新建证书";
  const dialogSaveLabel = dialog === "provider" ? "保存服务商" : dialog === "certificate" ? "创建证书" : dialog === "self-signed" ? "生成并保存" : dialog === "account" ? "更新账户" : dialog === "revoke" ? "吊销版本" : "导入证书";
  const saveDisabled = busy || (dialog === "provider" ? !canSaveProvider : dialog === "certificate" ? !canCreate : dialog === "self-signed" ? !canGenerate : dialog === "account" ? !canSaveAccount : dialog === "revoke" ? !revokeForm.confirm || !revokeForm.directory_url : !importForm.name || !importForm.cert_pem || !importForm.key_pem);

  const certificateList = <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
    <Space wrap><Button type="primary" aria-label="新建证书" icon={<PlusOutlined />} disabled={busy || !hasChallenge} onClick={openCertificate}>新建证书</Button><Button aria-label="生成自签名证书" icon={<PlusOutlined />} disabled={busy || capabilities.self_signed !== true} onClick={() => { setSelfSignedForm({ ...initialSelfSignedForm }); setDialog("self-signed"); }}>生成自签名证书</Button><Button aria-label="导入 PEM" icon={<UploadOutlined />} disabled={busy} onClick={() => setDialog("import")}>导入 PEM</Button>{!capabilities.available && !validationNodes.length && <Tag color="warning">ACME 不可用</Tag>}</Space>
    <Table<ManagedCertificate> rowKey="id" dataSource={certificates} loading={loading} scroll={{ x: 800 }} locale={{ emptyText: "暂无证书" }} columns={[
      { title: "证书", key: "name", render: (_, row) => <Space orientation="vertical" size={0}><Button type="link" disabled={busy} onClick={() => inspect(row)}>{row.name}</Button><Typography.Text>{row.domains.join(", ")}</Typography.Text><Typography.Text type="secondary">{row.directory_url ? `${challengeLabels[row.challenge_type]}${row.validation_server_id ? ` / ${serverName(row.validation_server_id)}` : ""}${row.webroot_id ? ` / ${row.webroot_id}` : ""}` : "本地证书（无 ACME）"}</Typography.Text></Space> },
      { title: "状态", key: "status", render: (_, row) => <Tag color={color(row.status)}>{statusLabel(row.status)}</Tag> },
      { title: "到期时间", key: "expires", render: (_, row) => date(row.expires_at) },
      { title: "操作", key: "actions", render: (_, row) => <Space wrap><Button icon={<FolderOpenOutlined />} aria-label="证书详情" title="证书详情" disabled={busy} onClick={() => inspect(row)} /><Button icon={<ReloadOutlined />} aria-label={actionLabel(row)} title={actionLabel(row)} disabled={busy || Boolean(row.active_job_id) || !row.directory_url || !issuerAvailable(row)} onClick={() => queue(row)} /><Button danger icon={<DeleteOutlined />} aria-label="删除证书" title="删除证书" disabled={busy || Boolean(row.active_job_id)} onClick={() => confirmRemove(row)} /></Space> },
    ]} />
  </Space>;
  const providerList = <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
    <Button type="primary" aria-label="添加 DNS 服务商" icon={<PlusOutlined />} disabled={busy} onClick={() => openProvider()}>添加 DNS 服务商</Button>
    <Table<DNSProvider> rowKey="id" dataSource={providers} locale={{ emptyText: "暂无 DNS 服务商" }} scroll={{ x: 600 }} columns={[
      { title: "名称", dataIndex: "name" }, { title: "服务商", dataIndex: "provider" }, { title: "凭据字段", key: "fields", render: (_, row) => row.credential_fields.join(", ") },
      { title: "操作", key: "actions", render: (_, row) => <Space><Button icon={<EditOutlined />} aria-label="更换 DNS 凭据" title="更换 DNS 凭据" disabled={busy} onClick={() => openProvider(row)} /><Button danger icon={<DeleteOutlined />} aria-label="删除 DNS 服务商" title="删除 DNS 服务商" disabled={busy} onClick={() => setConfirmation({ title: `删除 DNS 服务商“${row.name}”？`, description: "删除此 DNS 服务商及其已存储的凭据？", danger: true, work: () => certificateRequest(`/providers/${row.id}`, "DELETE") })} /></Space> },
    ]} />
  </Space>;

  return <Space orientation="vertical" size="large" style={{ width: "100%" }}>
    <Space wrap style={{ width: "100%", justifyContent: "space-between" }}><Typography.Title level={2}>证书</Typography.Title><Button icon={<ReloadOutlined />} aria-label="刷新证书" title="刷新证书" loading={loading} onClick={() => void refresh()} /></Space>
    {error && <Alert type="error" title={zhMessage(error)} showIcon closable={{ onClose: () => setError("") }} />}
    {notice && <Alert type="success" title={notice} showIcon closable={{ onClose: () => setNotice("") }} />}
    <Tabs activeKey={tab} onChange={setTab} items={[{ key: "certificates", label: "证书", children: certificateList }, { key: "providers", label: "DNS 服务商", children: providerList }]} />
    {detail && tab === "certificates" && <Card title={detail.certificate.name} extra={<Button icon={<CloseOutlined />} aria-label="关闭证书详情" title="关闭证书详情" onClick={closeDetails} />}>
      <Space orientation="vertical" size="large" style={{ width: "100%" }}>
        {detail.certificate.last_error && <Alert type="error" title={zhMessage(detail.certificate.last_error)} showIcon />}
        {currentVersion?.details.self_signed === true && <Alert type="warning" title="自签名证书" description={`${selfSignedWarning} 不支持 ACME 自动续签或 CA 吊销。`} showIcon />}
        {currentRevocation && <Alert type={currentRevocation.status === "revoked" ? "error" : "warning"} title={`${currentRevocation.status === "revoked" ? "此证书已吊销。" : "吊销结果尚未确认。"}已部署的文件仍保留在节点上。`} showIcon />}
        <Space wrap align="center">
          <Form.Item label="自动续签" style={{ marginBottom: 0 }}><Switch aria-label="自动续签" checked={detail.certificate.auto_renew} disabled={busy || Boolean(currentRevocation) || !detail.certificate.directory_url} onChange={(enabled) => void action(() => certificateRequest(`/${selected}`, "PATCH", { name: detail.certificate.name, auto_renew: enabled }))} /></Form.Item>
          <Checkbox checked={force || Boolean(currentRevocation)} disabled={busy || Boolean(currentRevocation) || !detail.certificate.directory_url} onChange={(event) => setForce(event.target.checked)}>强制续签</Checkbox>
          <Button icon={<ReloadOutlined />} aria-label={currentRevocation ? "重新签发证书" : "立即续签"} disabled={busy || !detail.certificate.version_id || !detail.certificate.directory_url || Boolean(detail.certificate.active_job_id) || !issuerAvailable(detail.certificate)} onClick={() => queue(detail.certificate, force)}>{currentRevocation ? "重新签发证书" : "立即续签"}</Button>
          <Button icon={<DownloadOutlined />} aria-label="下载证书" disabled={busy || !detail.certificate.version_id} onClick={() => download()}>下载证书</Button>
          <Button icon={<DownloadOutlined />} aria-label="下载私钥" disabled={busy || !detail.certificate.version_id} onClick={() => download(true)}>下载私钥</Button>
        </Space>
        {detail.account && <Card size="small" title="ACME 账户" extra={<Space wrap>{detail.account.retry_job_id && <Button aria-label="重试账户更新" icon={<ReloadOutlined />} disabled={busy || Boolean(detail.certificate.active_job_id) || !capabilities.account_management} onClick={() => void action(() => certificateRequest(`/${selected}/account/jobs/${detail.account?.retry_job_id}/retry`, "POST"))} />}<Button aria-label="编辑 ACME 账户" icon={<EditOutlined />} disabled={busy || Boolean(detail.certificate.active_job_id) || !capabilities.account_management} onClick={openAccount} /></Space>}>
          <Descriptions column={1} items={[{ key: "email", label: "邮箱", children: detail.account.email }, ...(detail.account.pending_email ? [{ key: "requested", label: "待更新邮箱", children: detail.account.pending_email }] : []), { key: "eab", label: "外部账户绑定", children: detail.account.eab_configured ? "已配置 EAB" : "未配置 EAB 凭据" }, { key: "state", label: "状态", children: <Tag>{statusLabel(detail.account.state)}</Tag> }]} />
        </Card>}
        <Card size="small" title="部署目标">
          <Form layout="vertical" disabled={busy} onFinish={saveTarget}>
            <Row gutter={16}>
              <Col xs={24} md={12}><Form.Item label="目标服务器"><Select aria-label="目标服务器" value={target.server_id || undefined} options={servers.map((server) => ({ label: server.name, value: server.id }))} onChange={(server_id) => setTarget((value) => ({ ...value, server_id }))} /></Form.Item></Col>
              <Col xs={24} md={12}><Form.Item label="主机名" help="填写证书覆盖的 DNS 域名或 IP，不带协议、端口或路径。"><Input aria-label="主机名" value={target.domain} onChange={(event) => setTarget((value) => ({ ...value, domain: event.target.value }))} /></Form.Item></Col>
              <Col xs={24} md={12}><Form.Item label="证书文件名"><Input aria-label="证书文件名" value={target.cert_name} onChange={(event) => setTarget((value) => ({ ...value, cert_name: event.target.value }))} /></Form.Item></Col>
              <Col xs={24} md={12}><Form.Item label="重载服务"><Select aria-label="重载服务" value={target.reload} options={["nginx", "xray", "both", "none"].map((value) => ({ label: ({ nginx: "Nginx", xray: "Xray", both: "两者", none: "不重载" } as Record<string, string>)[value], value }))} onChange={(reload) => setTarget((value) => ({ ...value, reload }))} /></Form.Item></Col>
            </Row>
            <Space wrap><Checkbox checked={target.auto_deploy} onChange={(event) => setTarget((value) => ({ ...value, auto_deploy: event.target.checked }))}>自动部署</Checkbox><Button htmlType="submit" aria-label="添加目标" icon={<PlusOutlined />} disabled={busy || !target.server_id || !target.domain || !target.cert_name}>添加目标</Button></Space>
          </Form>
          <Table<CertificateDetail["targets"][number]> style={{ marginTop: 16 }} rowKey="id" dataSource={detail.targets} pagination={false} scroll={{ x: 600 }} columns={[
            { title: "目标", key: "target", render: (_, item) => <Space orientation="vertical" size={0}><Typography.Text strong>{serverName(item.server_id)}</Typography.Text><Typography.Text>{item.domain} / {item.cert_name}</Typography.Text>{item.error && <Typography.Text type="danger">{zhMessage(item.error)}</Typography.Text>}</Space> },
            { title: "状态", key: "status", render: (_, item) => <Tag color={color(item.status)}>{statusLabel(item.status)}</Tag> },
            { title: "操作", key: "actions", render: (_, item) => <Space><Button icon={<UploadOutlined />} aria-label="部署证书" disabled={busy || Boolean(currentRevocation) || !detail.certificate.version_id} onClick={() => void action(() => certificateRequest(`/${selected}/targets/${item.id}/deploy`, "POST"))} /><Button danger icon={<DeleteOutlined />} aria-label="移除目标" disabled={busy} onClick={() => setConfirmation({ title: "移除部署目标？", description: `${item.domain} / ${item.cert_name}`, danger: true, work: () => certificateRequest(`/${selected}/targets/${item.id}`, "DELETE") })} /></Space> },
          ]} />
        </Card>
        <Card size="small" title="版本"><Table<CertificateVersion> rowKey="id" dataSource={detail.versions} pagination={false} scroll={{ x: 700 }} columns={[
          { title: "版本", key: "version", render: (_, version) => <Space orientation="vertical" size={0}><Typography.Text strong>{date(version.created_at)}</Typography.Text><Typography.Text>{version.details.issuer}</Typography.Text><Typography.Text code>{version.details.serial}</Typography.Text></Space> },
          { title: "到期时间", key: "expires", render: (_, version) => date(version.details.expires_at) },
          { title: "状态", key: "status", render: (_, version) => <Space wrap>{version.details.self_signed === true && <Tag color="warning">自签名</Tag>}{version.revocation && <Tag color={color(version.revocation.status)}>{version.revocation.status === "unknown" ? "尚未确认" : version.revocation.status === "pending" ? "吊销中" : "已吊销"}</Tag>}{version.id === detail.certificate.version_id && <Tag color={version.revocation ? "default" : "success"}>{version.revocation ? "当前" : "使用中"}</Tag>}</Space> },
          { title: "操作", key: "actions", render: (_, version) => <Space wrap>{version.id !== detail.certificate.version_id && <Button aria-label="启用版本" disabled={busy || Boolean(version.revocation) || Boolean(detail.certificate.active_job_id)} onClick={() => setConfirmation({ title: "启用此证书版本？", description: version.details.serial, work: () => certificateRequest(`/${selected}/versions/${version.id}/activate`, "POST") })}>启用版本</Button>}<Button danger icon={<StopOutlined />} aria-label={version.revocation?.status === "unknown" ? "重试吊销" : "吊销版本"} disabled={busy || version.details.self_signed === true || !capabilities.revocation || Boolean(detail.certificate.active_job_id) || Boolean(version.revocation && version.revocation.status !== "unknown")} onClick={() => openRevoke(version)}>{version.revocation?.status === "unknown" ? "重试吊销" : "吊销版本"}</Button></Space> },
        ]} /></Card>
        <Card size="small" title="任务"><Table<CertificateDetail["jobs"][number]> rowKey="id" dataSource={detail.jobs} pagination={false} scroll={{ x: 500 }} columns={[
          { title: "任务", key: "job", render: (_, job) => <Space orientation="vertical" size={0}><Typography.Text strong>{zhStatus(job.kind)}</Typography.Text><Typography.Text>{job.message ? zhMessage(job.message) : ""}</Typography.Text>{job.cleanup_pending && <Typography.Text type="warning">节点验证文件待清理</Typography.Text>}</Space> },
          { title: "创建时间", key: "created", render: (_, job) => date(job.created_at) }, { title: "状态", key: "status", render: (_, job) => <Tag color={color(job.status)}>{statusLabel(job.status)}</Tag> },
        ]} /></Card>
      </Space>
    </Card>}
    <Modal title={dialogTitle} open={Boolean(dialog)} onCancel={() => { if (!busy) closeDialog(); }} width={620} closable={!busy} mask={{ closable: !busy }} keyboard={!busy} destroyOnHidden okText={dialogSaveLabel} confirmLoading={busy} okButtonProps={{ "aria-label": dialogSaveLabel, disabled: saveDisabled, danger: dialog === "revoke", htmlType: "submit", form: "certificate-dialog-form" }} cancelButtonProps={{ disabled: busy }} styles={{ body: { maxHeight: "70vh", overflowY: "auto" } }}>
      {error && <Alert type="error" title={zhMessage(error)} showIcon style={{ marginBottom: 16 }} />}
      <Form id="certificate-dialog-form" layout="vertical" disabled={busy} onFinish={saveDialog}>
        {dialog === "provider" && <>
          <Form.Item label="服务商名称"><Input aria-label="服务商名称" value={providerForm.name} onChange={(event) => setProviderForm((value) => ({ ...value, name: event.target.value }))} /></Form.Item>
          <Form.Item label="DNS 服务商类型"><Select aria-label="DNS 服务商类型" value={providerForm.provider} disabled={busy || Boolean(editingProvider)} options={capabilities.providers.map((item) => ({ label: item.id, value: item.id }))} onChange={(provider) => setProviderForm((value) => ({ ...value, provider, credentials: {} }))} /></Form.Item>
          {(providerFields?.fields ?? []).map((field) => <Form.Item key={field} label={field} required={providerFields?.required.includes(field)}>{field.endsWith("ENDPOINT") ? <Input aria-label={field} type="url" autoComplete="off" value={providerForm.credentials[field] ?? ""} onChange={(event) => setProviderForm((value) => ({ ...value, credentials: { ...value.credentials, [field]: event.target.value } }))} /> : <Input.Password aria-label={field} autoComplete="off" value={providerForm.credentials[field] ?? ""} onChange={(event) => setProviderForm((value) => ({ ...value, credentials: { ...value.credentials, [field]: event.target.value } }))} />}</Form.Item>)}
        </>}
        {dialog === "certificate" && <>
          <Form.Item label="证书名称"><Input aria-label="证书名称" value={form.name} onChange={(event) => patchForm({ name: event.target.value })} /></Form.Item>
          <Form.Item label="DNS 域名" validateStatus={wildcardError ? "error" : undefined} help={wildcardError ? "通配符域名需要使用 DNS-01" : undefined}><Input.TextArea aria-label="DNS 域名" rows={2} value={form.domains} onChange={(event) => patchForm({ domains: event.target.value })} /></Form.Item>
          <Form.Item label="账户邮箱"><Input aria-label="账户邮箱" type="email" value={form.email} onChange={(event) => patchForm({ email: event.target.value })} /></Form.Item>
          <Form.Item label="验证方式"><Select aria-label="验证方式" value={form.challenge_type} options={challengeTypes.map((value) => ({ value, label: challengeLabels[value] }))} onChange={(challenge_type) => patchForm({ challenge_type })} /></Form.Item>
          {form.challenge_type !== "dns" && <Form.Item label="验证主机"><Select aria-label="验证主机" value={form.validation_server_id} options={validationOptions} onChange={(validation_server_id) => patchForm({ validation_server_id })} /></Form.Item>}
          {form.challenge_type === "dns" && <Form.Item label="DNS 服务商"><Select aria-label="DNS 服务商" value={form.provider_id || undefined} options={providers.map((provider) => ({ value: provider.id, label: provider.name }))} onChange={(provider_id) => patchForm({ provider_id })} /></Form.Item>}
          {form.challenge_type === "webroot" && <Form.Item label="网站根目录"><Select aria-label="网站根目录" value={form.webroot_id || undefined} options={webrootOptions.map((value) => ({ value, label: value }))} onChange={(webroot_id) => patchForm({ webroot_id })} /></Form.Item>}
          <Form.Item label="ACME 目录"><Select aria-label="ACME 目录" value={form.directory_url || undefined} options={capabilities.directories.map((value) => ({ value, label: value }))} onChange={(directory_url) => patchForm({ directory_url })} /></Form.Item>
          <Collapse items={[{ key: "eab", label: "外部账户绑定", children: <><Form.Item label="EAB 密钥 ID"><Input.Password aria-label="EAB 密钥 ID" autoComplete="off" value={form.eab_kid} onChange={(event) => patchForm({ eab_kid: event.target.value })} /></Form.Item><Form.Item label="EAB HMAC 密钥"><Input.Password aria-label="EAB HMAC 密钥" autoComplete="off" value={form.eab_hmac_key} onChange={(event) => patchForm({ eab_hmac_key: event.target.value })} /></Form.Item></> }]} />
          <Form.Item label="自动续签" style={{ marginTop: 16 }}><Switch aria-label="自动续签" checked={form.auto_renew} onChange={(auto_renew) => patchForm({ auto_renew })} /></Form.Item>
          <Checkbox checked={form.accept_terms} onChange={(event) => patchForm({ accept_terms: event.target.checked })}>我接受此 CA 的服务条款</Checkbox>
        </>}
        {dialog === "self-signed" && <>
          <Alert type="warning" title="请确认自签名信任边界" description={selfSignedWarning} showIcon style={{ marginBottom: 16 }} />
          <Form.Item label="证书名称" required><Input aria-label="证书名称" maxLength={120} value={selfSignedForm.name} onChange={(event) => setSelfSignedForm((value) => ({ ...value, name: event.target.value }))} /></Form.Item>
          <Form.Item label="DNS 域名或 IP（SAN）" required help="最多 20 项，用换行、空格或逗号分隔；支持 IPv4、IPv6 和单层通配符 DNS。不要填写协议、端口、方括号或路径。"><Input.TextArea aria-label="DNS 域名或 IP（SAN）" rows={3} maxLength={5120} value={selfSignedForm.domains} onChange={(event) => setSelfSignedForm((value) => ({ ...value, domains: event.target.value }))} /></Form.Item>
          <Form.Item label="有效天数" required help="1–3650 天，默认 365 天。到期前需手动生成并部署新证书，不会自动续签。"><InputNumber aria-label="有效天数" min={1} max={3650} precision={0} value={selfSignedForm.valid_days} style={{ width: "100%" }} onChange={(valid_days) => setSelfSignedForm((value) => ({ ...value, valid_days }))} /></Form.Item>
          <Typography.Paragraph>用途：服务器 TLS 身份验证（serverAuth）；使用 ECDSA P-256 密钥。这不是可签发其他证书的 CA 证书。</Typography.Paragraph>
          <Checkbox checked={selfSignedForm.confirm_self_signed} onChange={(event) => setSelfSignedForm((value) => ({ ...value, confirm_self_signed: event.target.checked }))}>我了解自签名证书不受浏览器默认信任</Checkbox>
        </>}
        {dialog === "account" && <>
          <Form.Item label="账户邮箱"><Input aria-label="账户邮箱" type="email" value={accountForm.email} onChange={(event) => setAccountForm((value) => ({ ...value, email: event.target.value }))} /></Form.Item>
          <Form.Item label="外部账户绑定"><Select aria-label="外部账户绑定" value={accountForm.eab_action} disabled={busy || detail?.account?.state === "registered"} options={[{ label: "保留现有凭据", value: "keep" }, { label: "替换凭据", value: "replace" }, { label: "移除凭据", value: "remove" }]} onChange={(eab_action) => setAccountForm((value) => ({ ...value, eab_action, eab_kid: "", eab_hmac_key: "" }))} /></Form.Item>
          {accountForm.eab_action === "replace" && <><Form.Item label="EAB 密钥 ID"><Input.Password aria-label="EAB 密钥 ID" autoComplete="off" value={accountForm.eab_kid} onChange={(event) => setAccountForm((value) => ({ ...value, eab_kid: event.target.value }))} /></Form.Item><Form.Item label="EAB HMAC 密钥"><Input.Password aria-label="EAB HMAC 密钥" autoComplete="off" value={accountForm.eab_hmac_key} onChange={(event) => setAccountForm((value) => ({ ...value, eab_hmac_key: event.target.value }))} /></Form.Item></>}
        </>}
        {dialog === "revoke" && <>
          <Alert type="warning" title="吊销不可撤销。已部署的文件仍保留在节点上。" showIcon />
          <Typography.Paragraph><Typography.Text strong>{detail?.certificate.name}</Typography.Text><br /><Typography.Text code>{revokeForm.serial}</Typography.Text></Typography.Paragraph>
          <Form.Item label="签发证书的 ACME 目录"><Select aria-label="签发证书的 ACME 目录" value={revokeForm.directory_url || undefined} disabled={busy || Boolean(detail?.certificate.directory_url)} options={capabilities.directories.map((value) => ({ value, label: value }))} onChange={(directory_url) => setRevokeForm((value) => ({ ...value, directory_url }))} /></Form.Item>
          <Form.Item label="吊销原因"><Select aria-label="吊销原因" value={revokeForm.reason} options={reasons} onChange={(reason) => setRevokeForm((value) => ({ ...value, reason }))} /></Form.Item>
          <Checkbox checked={revokeForm.confirm} onChange={(event) => setRevokeForm((value) => ({ ...value, confirm: event.target.checked }))}>我确认吊销此版本</Checkbox>
        </>}
        {dialog === "import" && <>
          <Form.Item label="证书名称"><Input aria-label="证书名称" value={importForm.name} onChange={(event) => setImportForm((value) => ({ ...value, name: event.target.value }))} /></Form.Item>
          <Form.Item label="证书 PEM"><Input.TextArea aria-label="证书 PEM" rows={5} spellCheck={false} value={importForm.cert_pem} onChange={(event) => setImportForm((value) => ({ ...value, cert_pem: event.target.value }))} /></Form.Item>
          <Form.Item label="私钥 PEM"><Input.TextArea aria-label="私钥 PEM" rows={5} autoComplete="off" spellCheck={false} value={importForm.key_pem} onChange={(event) => setImportForm((value) => ({ ...value, key_pem: event.target.value }))} /></Form.Item>
        </>}
      </Form>
    </Modal>
    <Modal title={confirmation?.title} open={Boolean(confirmation)} onCancel={() => { if (!busy) setConfirmation(null); }} closable={!busy} mask={{ closable: !busy }} keyboard={!busy} okText="确认" confirmLoading={busy} okButtonProps={{ "aria-label": "确认", danger: confirmation?.danger, disabled: busy }} cancelButtonProps={{ disabled: busy }} onOk={() => { if (confirmation) void action(async () => { await confirmation.work(); if (mounted.current) setConfirmation(null); }); }}>
      <Typography.Paragraph>{confirmation?.description}</Typography.Paragraph>{error && <Alert type="error" title={zhMessage(error)} showIcon />}
    </Modal>
  </Space>;
}
