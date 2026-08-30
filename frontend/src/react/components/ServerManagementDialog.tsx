import { useLayoutEffect, useRef, useState } from "react";
import { Alert, Button, Checkbox, Descriptions, Form, Input, Modal, Space, Spin, Switch, Typography } from "antd";
import { DeleteOutlined, ReloadOutlined, SaveOutlined } from "@ant-design/icons";
import { getServerRemoval, getServerSettings, removeServer, updateServerSettings,
  type RemovalPreview, type ServerSettings } from "../../services/server-management";

export interface ServerManagementDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  serverId: string;
  mode: "edit" | "remove";
  onUpdated?: () => void;
}
const emptySettings = (): ServerSettings => ({ name: "", ip_address: null, ip_address_v6: null,
  domain: null, domain_v6: null, ipv6_enabled: true });

export default function ServerManagementDialog({ open, onOpenChange, serverId, mode, onUpdated }: ServerManagementDialogProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState("");
  const [preview, setPreview] = useState<RemovalPreview | null>(null);
  const [confirmName, setConfirmName] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [syncHosts, setSyncHosts] = useState(true);
  const [form, setForm] = useState<ServerSettings>(emptySettings);
  const generation = useRef(0);
  const busyRef = useRef(false);
  const canRemove = Boolean(preview && !preview.blockers.length
    && confirmName === preview.server_name && acknowledged && !busy);

  async function load() {
    const run = ++generation.current;
    setRevision(""); setPreview(null); setConfirmName(""); setAcknowledged(false); setError("");
    setForm(emptySettings());
    if (!open || !serverId) { setBusy(false); busyRef.current = false; return; }
    setBusy(true); busyRef.current = true;
    try {
      if (mode === "remove") {
        const value = await getServerRemoval(serverId);
        if (run === generation.current) setPreview(value);
      } else {
        const value = await getServerSettings(serverId);
        if (run !== generation.current) return;
        setRevision(value.revision);
        setForm({ name: value.server.name, domain: value.server.domain ?? null,
          ip_address: value.server.ip_address ?? null, domain_v6: value.server.domain_v6 ?? null,
          ip_address_v6: value.server.ip_address_v6 ?? null, ipv6_enabled: value.server.ipv6_enabled ?? true });
        setSyncHosts(true);
      }
    } catch (failure) {
      if (run === generation.current) setError(failure instanceof Error ? failure.message : "Server request failed");
    } finally {
      if (run === generation.current) { setBusy(false); busyRef.current = false; }
    }
  }
  async function save() {
    if (!open || busyRef.current || !revision || !form.name.trim()) return;
    const run = ++generation.current;
    setBusy(true); busyRef.current = true; setError("");
    try {
      await updateServerSettings(serverId, { ...form }, revision, syncHosts);
      if (run !== generation.current) return;
      onUpdated?.(); onOpenChange(false);
    } catch (failure) {
      if (run === generation.current) setError(failure instanceof Error ? failure.message : "Server update failed");
    } finally {
      if (run === generation.current) { setBusy(false); busyRef.current = false; }
    }
  }
  async function remove() {
    if (!open || !canRemove || !preview || busyRef.current) return;
    const run = ++generation.current;
    setBusy(true); busyRef.current = true; setError("");
    try {
      await removeServer(serverId, preview, confirmName);
      if (run !== generation.current) return;
      onUpdated?.(); onOpenChange(false);
    } catch (failure) {
      if (run === generation.current) {
        setError(failure instanceof Error ? failure.message : "Server removal failed");
        setPreview(null); setAcknowledged(false);
      }
    } finally {
      if (run === generation.current) { setBusy(false); busyRef.current = false; }
    }
  }
  useLayoutEffect(() => {
    busyRef.current = false;
    void load();
    return () => { generation.current += 1; };
  }, [open, serverId, mode]);

  return <Modal open={open} destroyOnHidden width={620} centered mask={{ closable: !busy }} keyboard={!busy}
    closable={!busy} onCancel={() => { if (!busyRef.current) onOpenChange(false); }}
    title={<Space wrap><span>{mode === "edit" ? "Edit server" : "Remove server"}</span>
      <Button type="text" icon={<ReloadOutlined />} aria-label="Reload server details" disabled={busy} onClick={() => void load()} /></Space>}
    styles={{ body: { maxHeight: "calc(100dvh - 200px)", overflowY: "auto" } }}
    footer={<Space wrap><Button disabled={busy} onClick={() => onOpenChange(false)}>Cancel</Button>
      {mode === "edit" ? <Button type="primary" aria-label="Save" icon={<SaveOutlined aria-hidden />} disabled={busy || !revision || !form.name.trim()}
        onClick={() => void save()}>Save</Button> : <Button danger type="primary" aria-label="Remove" icon={<DeleteOutlined aria-hidden />}
          disabled={!canRemove} onClick={() => void remove()}>Remove</Button>}</Space>}>
    {open && <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      {busy && <Spin aria-label="Loading server details" />}
      {error && <Alert type="error" showIcon title={error} />}
      {mode === "edit" && revision && <Form id="server-settings-form" layout="vertical" preserve={false} onFinish={() => void save()}>
        {([ ["name", "Server name"], ["domain", "Domain"], ["ip_address", "IPv4 address"],
          ["domain_v6", "IPv6 domain"], ["ip_address_v6", "IPv6 address"] ] as const).map(([key, label]) =>
          <Form.Item key={key} label={label}><Input aria-label={label} value={form[key] ?? ""} disabled={busy}
            maxLength={key === "name" ? 120 : undefined} onChange={event => setForm(previous => ({ ...previous, [key]: event.target.value }))} /></Form.Item>)}
        <Form.Item label="IPv6 enabled"><Switch aria-label="IPv6 enabled" checked={form.ipv6_enabled} disabled={busy}
          onChange={ipv6_enabled => setForm(previous => ({ ...previous, ipv6_enabled }))} /></Form.Item>
        <Checkbox checked={syncHosts} disabled={busy} onChange={event => setSyncHosts(event.target.checked)}>Update matching node addresses</Checkbox>
      </Form>}
      {mode === "remove" && preview && <>
        <Typography.Text strong>{preview.server_name}</Typography.Text>
        <Alert type="warning" showIcon title="Remote services are not stopped"
          description="This removes control-plane records. The remote Agent, Xray and existing client access are not uninstalled or stopped." />
        <Descriptions column={1} size="small" items={[
          { key: "nodes", label: "Nodes removed", children: preview.nodes.length },
          { key: "plans", label: "Plans updated", children: preview.plans.length },
          { key: "commands", label: "Command records removed", children: preview.command_count },
          { key: "unfinished", label: "Unfinished commands", children: preview.unfinished_command_count },
          { key: "telemetry", label: "Telemetry records removed", children: preview.telemetry_count },
          { key: "users", label: "User usage retained", children: preview.user_count },
          { key: "changes", label: "Change sets archived", children: preview.change_sets.length },
          { key: "certificates", label: "Certificates retained", children: preview.certificates.length },
        ]} />
        {([["Nodes", preview.nodes], ["Plans", preview.plans], ["Change sets", preview.change_sets],
          ["Certificates", preview.certificates]] as const).filter(([, items]) => items.length).map(([label, items]) =>
          <Space key={label} orientation="vertical"><Typography.Text strong>{label}</Typography.Text>
            {items.map(item => <Typography.Text key={item.id}>{item.name}</Typography.Text>)}</Space>)}
        {preview.certificates.length > 0 && <Alert type="info" showIcon title="Certificates retained"
          description="Deployment targets on this server are removed. Certificates validated by this server stop automatic renewal until a new validation server is configured." />}
        {preview.blockers.map(blocker => <Alert key={blocker} type="error" showIcon title={blocker} />)}
        <Form layout="vertical"><Form.Item label="Confirm server name"><Input aria-label="Confirm server name"
          value={confirmName} disabled={busy || Boolean(preview.blockers.length)} onChange={event => setConfirmName(event.target.value)} /></Form.Item>
          <Checkbox checked={acknowledged} disabled={busy || Boolean(preview.blockers.length)} onChange={event => setAcknowledged(event.target.checked)}>
            I accept that remote services may keep running</Checkbox></Form>
      </>}
    </Space>}
  </Modal>;
}
