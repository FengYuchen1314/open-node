import { useEffect, useRef, useState } from "react";
import { Alert, Button, Flex, Form, Modal, Select, Spin, Switch, Tag, Upload } from "antd";
import { SearchOutlined, UploadOutlined } from "@ant-design/icons";
import type { LegacyMMWXIdentityBundle, LegacyMMWXImportPreview } from "../../domain/legacy-mmwx";
import type { SubscriptionPlan } from "../../domain/subscriptions";
import { importLegacyMMWXIdentities, previewLegacyMMWXIdentities } from "../../services/legacy-mmwx";
import StrictInputNumber from "./StrictInputNumber";

export interface LegacyMMWXImportDialogProps { open: boolean; plans: SubscriptionPlan[]; onOpenChange: (open: boolean) => void; onImported?: () => void }
export default function LegacyMMWXImportDialog(props: LegacyMMWXImportDialogProps) { return props.open ? <ImportContent {...props} /> : null; }
function ImportContent({ plans, onOpenChange, onImported }: LegacyMMWXImportDialogProps) {
  const [bundle, setBundle] = useState<LegacyMMWXIdentityBundle | null>(null), [filename, setFilename] = useState("");
  const [replaceExisting, setReplaceExisting] = useState(false), [preview, setPreview] = useState<LegacyMMWXImportPreview | null>(null), [confirmUserCount, setConfirmUserCount] = useState<number | null>(null);
  const [busy, setBusy] = useState<"preview" | "import" | "read" | "">(""), [error, setError] = useState(""), [success, setSuccess] = useState("");
  const [packageMappings, setPackageMappings] = useState<Record<number, string>>({}), version = useRef(0);
  useEffect(() => () => { ++version.current; }, []);
  function invalidatePreview() { ++version.current; setPreview(null); setConfirmUserCount(null); setError(""); setSuccess(""); }
  async function selectFile(file: File) {
    const run = ++version.current; setBundle(null); setFilename(""); setPackageMappings({}); setPreview(null); setConfirmUserCount(null); setError(""); setSuccess("");
    if (file.size > 16 * 1024 * 1024) { setError("Identity file exceeds 16 MB"); return; }
    setBusy("read");
    try {
      const value: unknown = JSON.parse(await file.text()); if (run !== version.current) return;
      if (!value || typeof value !== "object" || (value as { version?: unknown }).version !== 1 || !Array.isArray((value as { users?: unknown }).users)) throw new Error("Invalid MMWX identity bundle");
      const parsed = value as LegacyMMWXIdentityBundle;
      if (parsed.packages !== undefined && !Array.isArray(parsed.packages)) throw new Error("Invalid MMWX package list");
      setBundle(parsed); setPackageMappings(Object.fromEntries((parsed.packages ?? []).map(item => [item.source_id, ""]))); setFilename(file.name);
    } catch (failure) { if (run === version.current) setError(failure instanceof SyntaxError ? "Invalid MMWX identity JSON" : failure instanceof Error ? failure.message : "Unable to read identity file"); }
    finally { if (run === version.current) setBusy(""); }
  }
  function mappedPackages(): Record<number, string> { return Object.fromEntries(Object.entries(packageMappings).filter(([, planId]) => !!planId).map(([sourceId, planId]) => [Number(sourceId), planId])); }
  async function runPreview() {
    if (!bundle || busy) return;
    const run = ++version.current; setBusy("preview"); setPreview(null); setConfirmUserCount(null); setError(""); setSuccess("");
    try { const value = await previewLegacyMMWXIdentities(bundle, replaceExisting, undefined, mappedPackages()); if (run === version.current) setPreview(value); }
    catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "Migration preview failed"); }
    finally { if (run === version.current) setBusy(""); }
  }
  const canImport = !!bundle && !!preview?.ready && !busy && confirmUserCount === preview.total_users;
  async function applyImport() {
    if (!canImport || !bundle || !preview || confirmUserCount === null) return;
    const run = ++version.current; setBusy("import"); setError(""); setSuccess("");
    try {
      const result = await importLegacyMMWXIdentities(bundle, replaceExisting, preview, confirmUserCount, undefined, mappedPackages());
      if (run !== version.current) return;
      setPreview(result.preview); setSuccess(`Imported ${result.preview.total_users} identities`); setConfirmUserCount(null); setBundle(null); setFilename(""); setPackageMappings({}); onImported?.();
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "MMWX identity import failed"); }
    finally { if (run === version.current) setBusy(""); }
  }
  return <Modal open title="Import MMWX identities" width={680} destroyOnHidden mask={{ closable: !busy }} keyboard={!busy} closable={!busy} onCancel={() => !busy && onOpenChange(false)}
    footer={<Flex justify="space-between"><Button disabled={!!busy} onClick={() => onOpenChange(false)}>{success ? "Close" : "Cancel"}</Button><Button type="primary" aria-label="Import" aria-busy={busy === "import"} loading={busy === "import"} disabled={!canImport} onClick={() => void applyImport()}>Import</Button></Flex>}>
    <Flex vertical gap="middle">{!!busy && <Spin />}{error && <Alert type="error" title={error} showIcon />}{success && <Alert type="success" title={success} showIcon />}
      <Flex gap="small" align="center" wrap><Upload accept="application/json,.json" maxCount={1} showUploadList={false} beforeUpload={file => { void selectFile(file); return Upload.LIST_IGNORE; }} disabled={!!busy}><Button icon={<UploadOutlined />} aria-label="Select JSON" disabled={!!busy}>Select JSON</Button></Upload><span>{filename || "No file selected"}</span></Flex>
      <Form layout="vertical" preserve={false} disabled={!!busy}>
        <Form.Item label="Replace existing logins and links"><Switch aria-label="Replace existing logins and links" checked={replaceExisting} onChange={value => { invalidatePreview(); setReplaceExisting(value); }} /></Form.Item>
        {replaceExisting && <Alert type="warning" title="Existing subscriber sessions will be revoked." showIcon />}
        {!!bundle?.packages?.length && <section aria-label="Package mappings">{bundle.packages.map(item => <Form.Item key={item.source_id} label={item.name}><Select aria-label={item.name} allowClear value={packageMappings[item.source_id] || undefined} options={plans.map(plan => ({ label: plan.name, value: plan.id }))} onChange={value => { invalidatePreview(); setPackageMappings(previous => ({ ...previous, [item.source_id]: value ?? "" })); }} /></Form.Item>)}</section>}
      </Form>
      <Button type="primary" icon={<SearchOutlined />} aria-label="Preview" loading={busy === "preview"} disabled={!bundle || !!busy} onClick={() => void runPreview()}>Preview</Button>
      {preview && <><Flex gap="small" wrap>{Object.entries({ Users: preview.total_users, New: preview.new_users, Existing: preview.existing_users, TOTP: preview.imported_totp, Logins: preview.imported_accounts + preview.replaced_accounts, Links: preview.imported_tokens + preview.replaced_tokens, Profiles: preview.imported_profiles + preview.replaced_profiles, Mappings: preview.mapped_packages }).map(([label, count]) => <Tag key={label}>{label} {count}</Tag>)}</Flex>
        {preview.blockers.map(blocker => <Alert key={blocker} type="error" title={blocker} showIcon />)}{preview.warnings.map(warning => <Alert key={warning} type="warning" title={warning} showIcon />)}
        <Form.Item label={`Confirm user count (${preview.total_users})`}><StrictInputNumber aria-label={`Confirm user count (${preview.total_users})`} aria-valuemin={1} aria-valuemax={preview.total_users} value={confirmUserCount} onChange={setConfirmUserCount} disabled={!preview.ready || !!busy} /></Form.Item>
      </>}
    </Flex>
  </Modal>;
}
