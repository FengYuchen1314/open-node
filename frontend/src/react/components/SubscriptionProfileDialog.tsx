import { useEffect, useRef, useState } from "react";
import { Alert, Flex, Form, Input, Modal, Select, Switch } from "antd";
import type { ManagedNode, ProductUser } from "../../domain/subscriptions";
import type { SubscriptionProfile } from "../../domain/subscription-profiles";
import type { SubscriptionTemplate } from "../../domain/subscription-templates";
import { updateSubscriptionProfile } from "../../services/subscription-profiles";

export interface SubscriptionProfileDialogProps { open: boolean; profile: SubscriptionProfile | null; nodes: ManagedNode[]; users: ProductUser[]; templates: SubscriptionTemplate[]; onOpenChange: (open: boolean) => void; onSaved?: (profile: SubscriptionProfile) => void }
export default function SubscriptionProfileDialog(props: SubscriptionProfileDialogProps) { return props.open && props.profile ? <ProfileContent key={`${props.profile.id}:${props.profile.revision}`} {...props} profile={props.profile} /> : null; }
function ProfileContent({ profile, nodes, users, templates, onOpenChange, onSaved }: SubscriptionProfileDialogProps & { profile: SubscriptionProfile }) {
  const [form, setForm] = useState(() => ({ name: profile.name, description: profile.description, node_ids: [...profile.node_ids], assigned_usernames: [...profile.assigned_usernames], clash_template_id: profile.clash_template_id, surge_template_id: profile.surge_template_id, enabled: profile.enabled }));
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const version = useRef(0); useEffect(() => () => { ++version.current; }, []);
  function patch(change: Partial<typeof form>) { setForm(previous => ({ ...previous, ...change })); }
  async function save() {
    if (busy || !form.name.trim()) return;
    const run = ++version.current; setBusy(true); setError("");
    try {
      const value = await updateSubscriptionProfile(profile.id, { ...form, expected_revision: profile.revision });
      if (run !== version.current) return; onSaved?.(value); onOpenChange(false);
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "Subscription profile update failed"); }
    finally { if (run === version.current) setBusy(false); }
  }
  return <Modal open title="Edit subscription profile" width={720} destroyOnHidden mask={{ closable: !busy }} keyboard={!busy} closable={!busy} onCancel={() => !busy && onOpenChange(false)} okText="Save" confirmLoading={busy} okButtonProps={{ "aria-label": "Save", "aria-busy": busy, disabled: !form.name.trim() }} cancelButtonProps={{ disabled: busy }} onOk={() => void save()}>
    <Flex vertical gap="middle">{error && <Alert type="error" title={error} showIcon />}{!!profile.migration_warnings.length && <Alert type="warning" title={profile.migration_warnings.join("; ")} showIcon />}
      <Form layout="vertical" preserve={false} disabled={busy}>
        <Form.Item label="Name"><Input aria-label="Name" value={form.name} onChange={event => patch({ name: event.target.value })} /></Form.Item>
        <Form.Item label="Enabled"><Switch aria-label="Enabled" checked={form.enabled} onChange={enabled => patch({ enabled })} /></Form.Item>
        <Form.Item label="Description"><Input.TextArea aria-label="Description" rows={2} value={form.description} onChange={event => patch({ description: event.target.value })} /></Form.Item>
        <Form.Item label="Assigned subscribers"><Select aria-label="Assigned subscribers" mode="multiple" value={form.assigned_usernames} onChange={assigned_usernames => patch({ assigned_usernames })} options={users.filter(user => !user.removal_id).map(user => ({ label: user.display_name || user.username, value: user.username }))} /></Form.Item>
        <Form.Item label="Node subset" extra="Empty uses every node in each subscriber's plan"><Select aria-label="Node subset" mode="multiple" value={form.node_ids} onChange={node_ids => patch({ node_ids })} options={nodes.filter(node => !node.removal_id).map(node => ({ label: node.name, value: node.id }))} /></Form.Item>
        {(["clash", "surge"] as const).map(format => <Form.Item key={format} label={`${format === "clash" ? "Clash" : "Surge"} template`}><Select aria-label={`${format === "clash" ? "Clash" : "Surge"} template`} allowClear value={form[`${format}_template_id`] ?? undefined} onChange={value => patch({ [`${format}_template_id`]: value ?? null })} options={templates.filter(template => template.format === format).map(template => ({ label: template.name, value: template.id }))} /></Form.Item>)}
      </Form>
    </Flex>
  </Modal>;
}
