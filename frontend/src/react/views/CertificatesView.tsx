import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Checkbox, Col, Collapse, Descriptions, Form, Input, Modal, Row, Select, Space, Switch, Table, Tabs, Tag, Typography } from "antd";
import { CloseOutlined, DeleteOutlined, DownloadOutlined, EditOutlined, FolderOpenOutlined, PlusOutlined, ReloadOutlined, StopOutlined, UploadOutlined } from "@ant-design/icons";
import { listServers } from "../../services/inventory";
import { certificateRequest, type CertificateCapabilities, type CertificateChallenge, type CertificateDetail, type CertificateVersion, type DNSProvider, type ManagedCertificate } from "../../services/certificates";
import type { ServerSummary } from "../../domain/inventory";

export interface CertificatesViewProps { onUpdated?: () => void }
type Dialog = "" | "provider" | "certificate" | "import" | "account" | "revoke";
const emptyCapabilities: CertificateCapabilities = { available: false, account_management: false, revocation: false, directories: [], providers: [], challenge_types: [], webroots: [] };
const challengeLabels: Record<CertificateChallenge, string> = { dns: "DNS-01", standalone: "HTTP-01 / Standalone", webroot: "HTTP-01 / Webroot" };
const reasons = [{ label: "Unspecified", value: 0 }, { label: "Key compromise", value: 1 }, { label: "Affiliation changed", value: 3 }, { label: "Superseded", value: 4 }, { label: "Cessation of operation", value: 5 }, { label: "Privilege withdrawn", value: 9 }];
const initialCertificateForm = { name: "", domains: "", email: "", challenge_type: "dns" as CertificateChallenge, validation_server_id: "", provider_id: "", webroot_id: "", directory_url: "", accept_terms: false, auto_renew: true, eab_kid: "", eab_hmac_key: "" };
function date(value: number | null) { return value ? new Date(value * 1000).toLocaleString() : "-"; }
function color(status: string) { return ["issued", "succeeded"].includes(status) ? "success" : ["failed", "interrupted", "revoked"].includes(status) ? "error" : ["unknown", "revocation_unknown"].includes(status) ? "warning" : "default"; }
function statusLabel(status: string) { return ({ revocation_pending: "Revoking", revocation_unknown: "Unconfirmed", updating_account: "Account update", not_registered: "Not registered", unconfirmed: "Unconfirmed", unavailable: "Unavailable", registered: "Registered" } as Record<string, string>)[status] ?? status; }
function needsReissue(row: ManagedCertificate) { return ["revoked", "revocation_unknown", "revocation_pending", "revoking"].includes(row.status); }
function validationNodesFor(capabilities: CertificateCapabilities) { return capabilities.remote_http_available ? (capabilities.validation_nodes ?? []).filter((node) => !node.cleanup_error) : []; }
function hostsFor(capabilities: CertificateCapabilities, challenge: CertificateChallenge) {
  return [{ label: "Control plane", value: "", disabled: !capabilities.challenge_types.includes(challenge) }, ...validationNodesFor(capabilities).filter((node) => challenge === "standalone" ? node.standalone : challenge === "webroot" && node.webroots.length > 0).map((node) => ({ label: node.name, value: node.id, disabled: false }))];
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
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [dialog, setDialog] = useState<Dialog>("");
  const [editingProvider, setEditingProvider] = useState("");
  const [providerForm, setProviderForm] = useState({ name: "", provider: "cloudflare", credentials: {} as Record<string, string> });
  const [form, setForm] = useState({ ...initialCertificateForm });
  const [importForm, setImportForm] = useState({ name: "", cert_pem: "", key_pem: "" });
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
  const currentRevocation = detail?.versions.find((version) => version.id === detail.certificate.version_id)?.revocation;
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
    } catch (cause) { if (current()) setError(cause instanceof Error ? cause.message : "Certificate request failed"); }
    finally { if (current()) { loadingRef.current = false; setLoading(false); } }
  }
  async function action(work: () => Promise<unknown>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try {
      await work();
      if (mounted.current) { live.current.props.onUpdated?.(); await refresh(true); }
    } catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : "Certificate request failed"); }
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
  }
  function closeDetails() { live.current.selected = ""; setSelected(""); setDetail(null); }
  function inspect(row: ManagedCertificate) {
    if (busyRef.current) return;
    live.current.selected = row.id;
    setSelected(row.id);
    setDetail(null);
    setForce(false);
    setTarget((value) => ({ ...value, domain: row.domains[0]?.startsWith("*.") ? "" : row.domains[0] ?? "", cert_name: (row.domains[0] ?? "").replace("*.", "_.") }));
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
    if (dialog === "provider" && !canSaveProvider || dialog === "certificate" && !canCreate || dialog === "account" && !canSaveAccount || dialog === "revoke" && (!revokeForm.confirm || !revokeForm.directory_url) || dialog === "import" && (!importForm.name || !importForm.cert_pem || !importForm.key_pem)) return;
    const id = selected;
    void action(async () => {
      if (dialog === "provider") {
        const credentials = Object.fromEntries(Object.entries(providerForm.credentials).filter(([, value]) => value));
        await certificateRequest(`/providers${editingProvider ? `/${editingProvider}` : ""}`, editingProvider ? "PUT" : "POST", { ...providerForm, credentials });
      } else if (dialog === "certificate") {
        await certificateRequest("", "POST", { ...form, validation_server_id: form.challenge_type === "dns" ? null : form.validation_server_id || null, provider_id: form.challenge_type === "dns" ? form.provider_id : null, webroot_id: form.challenge_type === "webroot" ? form.webroot_id : null, domains: form.domains.trim().split(/[\s,]+/), eab_kid: form.eab_kid || null, eab_hmac_key: form.eab_hmac_key || null });
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
    setConfirmation({ title: `Delete certificate "${row.name}"?`, description: "Delete this managed certificate and its stored material?", danger: true, work: async () => { await certificateRequest(`/${row.id}`, "DELETE"); if (live.current.selected === row.id) closeDetails(); } });
  }
  function download(privateKey = false) {
    const id = selected;
    const filename = `${detail?.certificate.domains[0]?.replace("*.", "_.") ?? "certificate"}.${privateKey ? "key" : "pem"}`;
    const work = async () => {
      const data = await certificateRequest<{ cert_pem: string; key_pem?: string }>(`/${id}/material?include_private_key=${privateKey}`);
      const url = URL.createObjectURL(new Blob([privateKey ? data.key_pem ?? "" : data.cert_pem], { type: "application/x-pem-file" }));
      try { const link = document.createElement("a"); link.href = url; link.download = filename; link.click(); }
      finally { URL.revokeObjectURL(url); }
    };
    if (privateKey) setConfirmation({ title: "Download the private key?", description: "The downloaded PEM contains private key material. Keep it secure.", work });
    else void action(work);
  }
  function saveTarget() {
    if (!target.server_id || !target.domain || !target.cert_name) return;
    void action(() => certificateRequest(`/${selected}/targets`, "POST", { ...target }));
  }
  const actionLabel = (row: ManagedCertificate) => needsReissue(row) ? "Reissue certificate" : row.version_id ? "Renew certificate" : "Issue certificate";
  const dialogTitle = dialog === "provider" ? editingProvider ? "Rotate DNS credentials" : "Add DNS provider" : dialog === "import" ? "Import PEM" : dialog === "account" ? "Edit ACME account" : dialog === "revoke" ? "Revoke certificate version" : "New certificate";
  const dialogSaveLabel = dialog === "provider" ? "Save provider" : dialog === "certificate" ? "Create certificate" : dialog === "account" ? "Update account" : dialog === "revoke" ? "Revoke version" : "Import certificate";
  const saveDisabled = busy || (dialog === "provider" ? !canSaveProvider : dialog === "certificate" ? !canCreate : dialog === "account" ? !canSaveAccount : dialog === "revoke" ? !revokeForm.confirm || !revokeForm.directory_url : !importForm.name || !importForm.cert_pem || !importForm.key_pem);

  const certificateList = <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
    <Space wrap><Button type="primary" aria-label="New certificate" icon={<PlusOutlined />} disabled={busy || !hasChallenge} onClick={openCertificate}>New certificate</Button><Button aria-label="Import PEM" icon={<UploadOutlined />} disabled={busy} onClick={() => setDialog("import")}>Import PEM</Button>{!capabilities.available && !validationNodes.length && <Tag color="warning">ACME unavailable</Tag>}</Space>
    <Table<ManagedCertificate> rowKey="id" dataSource={certificates} loading={loading} scroll={{ x: 800 }} locale={{ emptyText: "No certificates" }} columns={[
      { title: "Certificate", key: "name", render: (_, row) => <Space orientation="vertical" size={0}><Button type="link" disabled={busy} onClick={() => inspect(row)}>{row.name}</Button><Typography.Text>{row.domains.join(", ")}</Typography.Text><Typography.Text type="secondary">{row.directory_url ? `${challengeLabels[row.challenge_type]}${row.validation_server_id ? ` / ${serverName(row.validation_server_id)}` : ""}${row.webroot_id ? ` / ${row.webroot_id}` : ""}` : "Imported"}</Typography.Text></Space> },
      { title: "Status", key: "status", render: (_, row) => <Tag color={color(row.status)}>{statusLabel(row.status)}</Tag> },
      { title: "Expires", key: "expires", render: (_, row) => date(row.expires_at) },
      { title: "Actions", key: "actions", render: (_, row) => <Space wrap><Button icon={<FolderOpenOutlined />} aria-label="Certificate details" title="Certificate details" disabled={busy} onClick={() => inspect(row)} /><Button icon={<ReloadOutlined />} aria-label={actionLabel(row)} title={actionLabel(row)} disabled={busy || Boolean(row.active_job_id) || !row.directory_url || !issuerAvailable(row)} onClick={() => queue(row)} /><Button danger icon={<DeleteOutlined />} aria-label="Delete certificate" title="Delete certificate" disabled={busy || Boolean(row.active_job_id)} onClick={() => confirmRemove(row)} /></Space> },
    ]} />
  </Space>;
  const providerList = <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
    <Button type="primary" aria-label="Add DNS provider" icon={<PlusOutlined />} disabled={busy} onClick={() => openProvider()}>Add DNS provider</Button>
    <Table<DNSProvider> rowKey="id" dataSource={providers} locale={{ emptyText: "No DNS providers" }} scroll={{ x: 600 }} columns={[
      { title: "Name", dataIndex: "name" }, { title: "Provider", dataIndex: "provider" }, { title: "Credential fields", key: "fields", render: (_, row) => row.credential_fields.join(", ") },
      { title: "Actions", key: "actions", render: (_, row) => <Space><Button icon={<EditOutlined />} aria-label="Rotate DNS credentials" title="Rotate DNS credentials" disabled={busy} onClick={() => openProvider(row)} /><Button danger icon={<DeleteOutlined />} aria-label="Delete DNS provider" title="Delete DNS provider" disabled={busy} onClick={() => setConfirmation({ title: `Delete DNS provider "${row.name}"?`, description: "Remove this DNS provider and its stored credentials?", danger: true, work: () => certificateRequest(`/providers/${row.id}`, "DELETE") })} /></Space> },
    ]} />
  </Space>;

  return <Space orientation="vertical" size="large" style={{ width: "100%" }}>
    <Space wrap style={{ width: "100%", justifyContent: "space-between" }}><Typography.Title level={2}>Certificates</Typography.Title><Button icon={<ReloadOutlined />} aria-label="Refresh certificates" title="Refresh certificates" loading={loading} onClick={() => void refresh()} /></Space>
    {error && <Alert type="error" title={error} showIcon closable={{ onClose: () => setError("") }} />}
    <Tabs activeKey={tab} onChange={setTab} items={[{ key: "certificates", label: "Certificates", children: certificateList }, { key: "providers", label: "DNS providers", children: providerList }]} />
    {detail && tab === "certificates" && <Card title={detail.certificate.name} extra={<Button icon={<CloseOutlined />} aria-label="Close certificate details" title="Close certificate details" onClick={closeDetails} />}>
      <Space orientation="vertical" size="large" style={{ width: "100%" }}>
        {detail.certificate.last_error && <Alert type="error" title={detail.certificate.last_error} showIcon />}
        {currentRevocation && <Alert type={currentRevocation.status === "revoked" ? "error" : "warning"} title={`${currentRevocation.status === "revoked" ? "This certificate is revoked." : "Revocation is not yet confirmed."} Deployed files remain on nodes.`} showIcon />}
        <Space wrap align="center">
          <Form.Item label="Auto-renew" style={{ marginBottom: 0 }}><Switch aria-label="Auto-renew" checked={detail.certificate.auto_renew} disabled={busy || Boolean(currentRevocation) || !detail.certificate.directory_url} onChange={(enabled) => void action(() => certificateRequest(`/${selected}`, "PATCH", { name: detail.certificate.name, auto_renew: enabled }))} /></Form.Item>
          <Checkbox checked={force || Boolean(currentRevocation)} disabled={busy || Boolean(currentRevocation) || !detail.certificate.directory_url} onChange={(event) => setForce(event.target.checked)}>Force renewal</Checkbox>
          <Button icon={<ReloadOutlined />} aria-label={currentRevocation ? "Reissue certificate" : "Renew now"} disabled={busy || !detail.certificate.version_id || !detail.certificate.directory_url || Boolean(detail.certificate.active_job_id) || !issuerAvailable(detail.certificate)} onClick={() => queue(detail.certificate, force)}>{currentRevocation ? "Reissue certificate" : "Renew now"}</Button>
          <Button icon={<DownloadOutlined />} aria-label="Download certificate" disabled={busy || !detail.certificate.version_id} onClick={() => download()}>Download certificate</Button>
          <Button icon={<DownloadOutlined />} aria-label="Download private key" disabled={busy || !detail.certificate.version_id} onClick={() => download(true)}>Download private key</Button>
        </Space>
        {detail.account && <Card size="small" title="ACME account" extra={<Space wrap>{detail.account.retry_job_id && <Button aria-label="Retry account update" icon={<ReloadOutlined />} disabled={busy || Boolean(detail.certificate.active_job_id) || !capabilities.account_management} onClick={() => void action(() => certificateRequest(`/${selected}/account/jobs/${detail.account?.retry_job_id}/retry`, "POST"))} />}<Button aria-label="Edit ACME account" icon={<EditOutlined />} disabled={busy || Boolean(detail.certificate.active_job_id) || !capabilities.account_management} onClick={openAccount} /></Space>}>
          <Descriptions column={1} items={[{ key: "email", label: "Email", children: detail.account.email }, ...(detail.account.pending_email ? [{ key: "requested", label: "Requested", children: detail.account.pending_email }] : []), { key: "eab", label: "External account binding", children: detail.account.eab_configured ? "EAB configured" : "No EAB credentials" }, { key: "state", label: "State", children: <Tag>{statusLabel(detail.account.state)}</Tag> }]} />
        </Card>}
        <Card size="small" title="Deployment targets">
          <Form layout="vertical" disabled={busy} onFinish={saveTarget}>
            <Row gutter={16}>
              <Col xs={24} md={12}><Form.Item label="Target server"><Select aria-label="Target server" value={target.server_id || undefined} options={servers.map((server) => ({ label: server.name, value: server.id }))} onChange={(server_id) => setTarget((value) => ({ ...value, server_id }))} /></Form.Item></Col>
              <Col xs={24} md={12}><Form.Item label="Hostname"><Input aria-label="Hostname" value={target.domain} onChange={(event) => setTarget((value) => ({ ...value, domain: event.target.value }))} /></Form.Item></Col>
              <Col xs={24} md={12}><Form.Item label="Certificate filename"><Input aria-label="Certificate filename" value={target.cert_name} onChange={(event) => setTarget((value) => ({ ...value, cert_name: event.target.value }))} /></Form.Item></Col>
              <Col xs={24} md={12}><Form.Item label="Reload"><Select aria-label="Reload" value={target.reload} options={["nginx", "xray", "both", "none"].map((value) => ({ label: value, value }))} onChange={(reload) => setTarget((value) => ({ ...value, reload }))} /></Form.Item></Col>
            </Row>
            <Space wrap><Checkbox checked={target.auto_deploy} onChange={(event) => setTarget((value) => ({ ...value, auto_deploy: event.target.checked }))}>Auto-deploy</Checkbox><Button htmlType="submit" aria-label="Add target" icon={<PlusOutlined />} disabled={busy || !target.server_id || !target.domain || !target.cert_name}>Add target</Button></Space>
          </Form>
          <Table<CertificateDetail["targets"][number]> style={{ marginTop: 16 }} rowKey="id" dataSource={detail.targets} pagination={false} scroll={{ x: 600 }} columns={[
            { title: "Target", key: "target", render: (_, item) => <Space orientation="vertical" size={0}><Typography.Text strong>{serverName(item.server_id)}</Typography.Text><Typography.Text>{item.domain} / {item.cert_name}</Typography.Text>{item.error && <Typography.Text type="danger">{item.error}</Typography.Text>}</Space> },
            { title: "Status", key: "status", render: (_, item) => <Tag color={color(item.status)}>{item.status}</Tag> },
            { title: "Actions", key: "actions", render: (_, item) => <Space><Button icon={<UploadOutlined />} aria-label="Deploy certificate" disabled={busy || Boolean(currentRevocation) || !detail.certificate.version_id} onClick={() => void action(() => certificateRequest(`/${selected}/targets/${item.id}/deploy`, "POST"))} /><Button danger icon={<DeleteOutlined />} aria-label="Remove target" disabled={busy} onClick={() => setConfirmation({ title: "Remove deployment target?", description: `${item.domain} / ${item.cert_name}`, danger: true, work: () => certificateRequest(`/${selected}/targets/${item.id}`, "DELETE") })} /></Space> },
          ]} />
        </Card>
        <Card size="small" title="Versions"><Table<CertificateVersion> rowKey="id" dataSource={detail.versions} pagination={false} scroll={{ x: 700 }} columns={[
          { title: "Version", key: "version", render: (_, version) => <Space orientation="vertical" size={0}><Typography.Text strong>{date(version.created_at)}</Typography.Text><Typography.Text>{version.details.issuer}</Typography.Text><Typography.Text code>{version.details.serial}</Typography.Text></Space> },
          { title: "Expires", key: "expires", render: (_, version) => date(version.details.expires_at) },
          { title: "Status", key: "status", render: (_, version) => <Space wrap>{version.revocation && <Tag color={color(version.revocation.status)}>{version.revocation.status === "unknown" ? "Unconfirmed" : version.revocation.status === "pending" ? "Revoking" : "Revoked"}</Tag>}{version.id === detail.certificate.version_id && <Tag color={version.revocation ? "default" : "success"}>{version.revocation ? "Current" : "Active"}</Tag>}</Space> },
          { title: "Actions", key: "actions", render: (_, version) => <Space wrap>{version.id !== detail.certificate.version_id && <Button aria-label="Activate version" disabled={busy || Boolean(version.revocation) || Boolean(detail.certificate.active_job_id)} onClick={() => setConfirmation({ title: "Activate this certificate version?", description: version.details.serial, work: () => certificateRequest(`/${selected}/versions/${version.id}/activate`, "POST") })}>Activate version</Button>}<Button danger icon={<StopOutlined />} aria-label={version.revocation?.status === "unknown" ? "Retry revocation" : "Revoke version"} disabled={busy || !capabilities.revocation || Boolean(detail.certificate.active_job_id) || Boolean(version.revocation && version.revocation.status !== "unknown")} onClick={() => openRevoke(version)}>{version.revocation?.status === "unknown" ? "Retry revocation" : "Revoke version"}</Button></Space> },
        ]} /></Card>
        <Card size="small" title="Jobs"><Table<CertificateDetail["jobs"][number]> rowKey="id" dataSource={detail.jobs} pagination={false} scroll={{ x: 500 }} columns={[
          { title: "Job", key: "job", render: (_, job) => <Space orientation="vertical" size={0}><Typography.Text strong>{job.kind}</Typography.Text><Typography.Text>{job.message}</Typography.Text>{job.cleanup_pending && <Typography.Text type="warning">Node challenge cleanup pending</Typography.Text>}</Space> },
          { title: "Created", key: "created", render: (_, job) => date(job.created_at) }, { title: "Status", key: "status", render: (_, job) => <Tag color={color(job.status)}>{job.status}</Tag> },
        ]} /></Card>
      </Space>
    </Card>}
    <Modal title={dialogTitle} open={Boolean(dialog)} onCancel={() => { if (!busy) closeDialog(); }} width={620} closable={!busy} mask={{ closable: !busy }} keyboard={!busy} destroyOnHidden okText={dialogSaveLabel} confirmLoading={busy} okButtonProps={{ disabled: saveDisabled, danger: dialog === "revoke", htmlType: "submit", form: "certificate-dialog-form" }} cancelButtonProps={{ disabled: busy }} styles={{ body: { maxHeight: "70vh", overflowY: "auto" } }}>
      {error && <Alert type="error" title={error} showIcon style={{ marginBottom: 16 }} />}
      <Form id="certificate-dialog-form" layout="vertical" disabled={busy} onFinish={saveDialog}>
        {dialog === "provider" && <>
          <Form.Item label="Provider name"><Input aria-label="Provider name" value={providerForm.name} onChange={(event) => setProviderForm((value) => ({ ...value, name: event.target.value }))} /></Form.Item>
          <Form.Item label="DNS provider type"><Select aria-label="DNS provider type" value={providerForm.provider} disabled={busy || Boolean(editingProvider)} options={capabilities.providers.map((item) => ({ label: item.id, value: item.id }))} onChange={(provider) => setProviderForm((value) => ({ ...value, provider, credentials: {} }))} /></Form.Item>
          {(providerFields?.fields ?? []).map((field) => <Form.Item key={field} label={field} required={providerFields?.required.includes(field)}>{field.endsWith("ENDPOINT") ? <Input aria-label={field} type="url" autoComplete="off" value={providerForm.credentials[field] ?? ""} onChange={(event) => setProviderForm((value) => ({ ...value, credentials: { ...value.credentials, [field]: event.target.value } }))} /> : <Input.Password aria-label={field} autoComplete="off" value={providerForm.credentials[field] ?? ""} onChange={(event) => setProviderForm((value) => ({ ...value, credentials: { ...value.credentials, [field]: event.target.value } }))} />}</Form.Item>)}
        </>}
        {dialog === "certificate" && <>
          <Form.Item label="Certificate name"><Input aria-label="Certificate name" value={form.name} onChange={(event) => patchForm({ name: event.target.value })} /></Form.Item>
          <Form.Item label="DNS names" validateStatus={wildcardError ? "error" : undefined} help={wildcardError ? "Wildcard names require DNS-01" : undefined}><Input.TextArea aria-label="DNS names" rows={2} value={form.domains} onChange={(event) => patchForm({ domains: event.target.value })} /></Form.Item>
          <Form.Item label="Account email"><Input aria-label="Account email" type="email" value={form.email} onChange={(event) => patchForm({ email: event.target.value })} /></Form.Item>
          <Form.Item label="Validation method"><Select aria-label="Validation method" value={form.challenge_type} options={challengeTypes.map((value) => ({ value, label: challengeLabels[value] }))} onChange={(challenge_type) => patchForm({ challenge_type })} /></Form.Item>
          {form.challenge_type !== "dns" && <Form.Item label="Validation host"><Select aria-label="Validation host" value={form.validation_server_id} options={validationOptions} onChange={(validation_server_id) => patchForm({ validation_server_id })} /></Form.Item>}
          {form.challenge_type === "dns" && <Form.Item label="DNS provider"><Select aria-label="DNS provider" value={form.provider_id || undefined} options={providers.map((provider) => ({ value: provider.id, label: provider.name }))} onChange={(provider_id) => patchForm({ provider_id })} /></Form.Item>}
          {form.challenge_type === "webroot" && <Form.Item label="Webroot"><Select aria-label="Webroot" value={form.webroot_id || undefined} options={webrootOptions.map((value) => ({ value, label: value }))} onChange={(webroot_id) => patchForm({ webroot_id })} /></Form.Item>}
          <Form.Item label="ACME directory"><Select aria-label="ACME directory" value={form.directory_url || undefined} options={capabilities.directories.map((value) => ({ value, label: value }))} onChange={(directory_url) => patchForm({ directory_url })} /></Form.Item>
          <Collapse items={[{ key: "eab", label: "External account binding", children: <><Form.Item label="EAB key ID"><Input.Password aria-label="EAB key ID" autoComplete="off" value={form.eab_kid} onChange={(event) => patchForm({ eab_kid: event.target.value })} /></Form.Item><Form.Item label="EAB HMAC key"><Input.Password aria-label="EAB HMAC key" autoComplete="off" value={form.eab_hmac_key} onChange={(event) => patchForm({ eab_hmac_key: event.target.value })} /></Form.Item></> }]} />
          <Form.Item label="Auto-renew" style={{ marginTop: 16 }}><Switch aria-label="Auto-renew" checked={form.auto_renew} onChange={(auto_renew) => patchForm({ auto_renew })} /></Form.Item>
          <Checkbox checked={form.accept_terms} onChange={(event) => patchForm({ accept_terms: event.target.checked })}>I accept this CA's terms of service</Checkbox>
        </>}
        {dialog === "account" && <>
          <Form.Item label="Account email"><Input aria-label="Account email" type="email" value={accountForm.email} onChange={(event) => setAccountForm((value) => ({ ...value, email: event.target.value }))} /></Form.Item>
          <Form.Item label="External account binding"><Select aria-label="External account binding" value={accountForm.eab_action} disabled={busy || detail?.account?.state === "registered"} options={[{ label: "Keep existing", value: "keep" }, { label: "Replace credentials", value: "replace" }, { label: "Remove credentials", value: "remove" }]} onChange={(eab_action) => setAccountForm((value) => ({ ...value, eab_action, eab_kid: "", eab_hmac_key: "" }))} /></Form.Item>
          {accountForm.eab_action === "replace" && <><Form.Item label="EAB key ID"><Input.Password aria-label="EAB key ID" autoComplete="off" value={accountForm.eab_kid} onChange={(event) => setAccountForm((value) => ({ ...value, eab_kid: event.target.value }))} /></Form.Item><Form.Item label="EAB HMAC key"><Input.Password aria-label="EAB HMAC key" autoComplete="off" value={accountForm.eab_hmac_key} onChange={(event) => setAccountForm((value) => ({ ...value, eab_hmac_key: event.target.value }))} /></Form.Item></>}
        </>}
        {dialog === "revoke" && <>
          <Alert type="warning" title="Revocation is irreversible. Deployed files remain on nodes." showIcon />
          <Typography.Paragraph><Typography.Text strong>{detail?.certificate.name}</Typography.Text><br /><Typography.Text code>{revokeForm.serial}</Typography.Text></Typography.Paragraph>
          <Form.Item label="Issuing ACME directory"><Select aria-label="Issuing ACME directory" value={revokeForm.directory_url || undefined} disabled={busy || Boolean(detail?.certificate.directory_url)} options={capabilities.directories.map((value) => ({ value, label: value }))} onChange={(directory_url) => setRevokeForm((value) => ({ ...value, directory_url }))} /></Form.Item>
          <Form.Item label="Revocation reason"><Select aria-label="Revocation reason" value={revokeForm.reason} options={reasons} onChange={(reason) => setRevokeForm((value) => ({ ...value, reason }))} /></Form.Item>
          <Checkbox checked={revokeForm.confirm} onChange={(event) => setRevokeForm((value) => ({ ...value, confirm: event.target.checked }))}>I confirm revocation of this version</Checkbox>
        </>}
        {dialog === "import" && <>
          <Form.Item label="Certificate name"><Input aria-label="Certificate name" value={importForm.name} onChange={(event) => setImportForm((value) => ({ ...value, name: event.target.value }))} /></Form.Item>
          <Form.Item label="Certificate PEM"><Input.TextArea aria-label="Certificate PEM" rows={5} spellCheck={false} value={importForm.cert_pem} onChange={(event) => setImportForm((value) => ({ ...value, cert_pem: event.target.value }))} /></Form.Item>
          <Form.Item label="Private key PEM"><Input.TextArea aria-label="Private key PEM" rows={5} autoComplete="off" spellCheck={false} value={importForm.key_pem} onChange={(event) => setImportForm((value) => ({ ...value, key_pem: event.target.value }))} /></Form.Item>
        </>}
      </Form>
    </Modal>
    <Modal title={confirmation?.title} open={Boolean(confirmation)} onCancel={() => { if (!busy) setConfirmation(null); }} closable={!busy} mask={{ closable: !busy }} keyboard={!busy} okText="Confirm" confirmLoading={busy} okButtonProps={{ danger: confirmation?.danger, disabled: busy }} cancelButtonProps={{ disabled: busy }} onOk={() => { if (confirmation) void action(async () => { await confirmation.work(); if (mounted.current) setConfirmation(null); }); }}>
      <Typography.Paragraph>{confirmation?.description}</Typography.Paragraph>{error && <Alert type="error" title={error} showIcon />}
    </Modal>
  </Space>;
}
