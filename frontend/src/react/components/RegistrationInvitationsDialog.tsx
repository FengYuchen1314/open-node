import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Empty, Flex, Form, Input, Modal, Select, Spin, Tag, Typography } from "antd";
import { CheckOutlined, CopyOutlined, LinkOutlined, UserAddOutlined } from "@ant-design/icons";
import type { RegistrationInvitation, RegistrationInvitationCreateResponse } from "../../domain/registration-invitations";
import type { SubscriptionPlan } from "../../domain/subscriptions";
import { createRegistrationInvitation, listRegistrationInvitations, revokeRegistrationInvitation } from "../../services/registration-invitations";

export interface RegistrationInvitationsDialogProps { open: boolean; plans: SubscriptionPlan[]; onOpenChange: (open: boolean) => void }
export default function RegistrationInvitationsDialog(props: RegistrationInvitationsDialogProps) { return props.open ? <InvitationsContent {...props} /> : null; }
function InvitationsContent({ plans, onOpenChange }: RegistrationInvitationsDialogProps) {
  const [invitations, setInvitations] = useState<RegistrationInvitation[]>([]), [planId, setPlanId] = useState(plans[0]?.id ?? "");
  const [expiresMinutes, setExpiresMinutes] = useState(1440), [loading, setLoading] = useState(false), [saving, setSaving] = useState(false), [revoking, setRevoking] = useState("");
  const [error, setError] = useState(""), [created, setCreated] = useState<RegistrationInvitationCreateResponse | null>(null), [copied, setCopied] = useState(false);
  const [confirmRevoke, setConfirmRevoke] = useState<RegistrationInvitation | null>(null), version = useRef(0);
  const busy = saving || !!revoking;
  useEffect(() => {
    const run = ++version.current; setLoading(true);
    void listRegistrationInvitations().then(value => { if (run === version.current) setInvitations(value.invitations); }).catch(failure => { if (run === version.current) setError(failure instanceof Error ? failure.message : "Invitations unavailable"); }).finally(() => { if (run === version.current) setLoading(false); });
    return () => { ++version.current; };
  }, []);
  useEffect(() => { if (!plans.some(plan => plan.id === planId)) setPlanId(plans[0]?.id ?? ""); }, [plans, planId]);
  async function submit() {
    if (!plans.some(plan => plan.id === planId) || busy || loading) return;
    const run = ++version.current; setSaving(true); setError(""); setCreated(null); setCopied(false);
    try {
      const value = await createRegistrationInvitation({ plan_id: planId, expires_minutes: expiresMinutes });
      if (run !== version.current) return; setCreated(value); setInvitations(previous => [value.invitation, ...previous.filter(item => item.id !== value.invitation.id)]);
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "Invitation creation failed"); }
    finally { if (run === version.current) setSaving(false); }
  }
  async function revoke(item: RegistrationInvitation) {
    if (busy) return;
    const run = ++version.current; setRevoking(item.id); setError("");
    try {
      const value = await revokeRegistrationInvitation(item.id); if (run !== version.current) return;
      setInvitations(previous => previous.map(entry => entry.id === value.id ? value : entry));
      if (created?.invitation.id === value.id) { setCreated(null); setCopied(false); }
      setConfirmRevoke(null);
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "Invitation revocation failed"); }
    finally { if (run === version.current) setRevoking(""); }
  }
  async function copy() {
    if (!created) return; const run = version.current;
    try { await navigator.clipboard.writeText(created.registration_url); if (run === version.current) setCopied(true); }
    catch { if (run === version.current) setError("Clipboard access failed"); }
  }
  return <><Modal open title="Registration invitations" width={720} destroyOnHidden mask={{ closable: !busy }} keyboard={!busy} closable={!busy} onCancel={() => !busy && onOpenChange(false)} footer={<Button disabled={busy} onClick={() => onOpenChange(false)}>Close</Button>}>
    <Flex vertical gap="middle">{error && <Alert type="error" title={error} showIcon />}
      <Form layout="vertical" preserve={false} disabled={busy || loading}>
        <Form.Item label="Plan"><Select aria-label="Plan" value={planId || undefined} options={plans.map(plan => ({ label: plan.name, value: plan.id }))} onChange={setPlanId} /></Form.Item>
        <Form.Item label="Expires"><Select aria-label="Expires" value={expiresMinutes} options={[{ label: "1 hour", value: 60 }, { label: "24 hours", value: 1440 }, { label: "3 days", value: 4320 }, { label: "7 days", value: 10080 }]} onChange={setExpiresMinutes} /></Form.Item>
        <Button type="primary" icon={<UserAddOutlined />} aria-label="Create registration invitation" loading={saving} disabled={!planId || busy || loading} onClick={() => void submit()}>Create invitation</Button>
      </Form>
      {created && <Form.Item label="Registration URL"><Flex gap="small"><Input aria-label="Registration URL" readOnly value={created.registration_url} /><Button icon={copied ? <CheckOutlined /> : <CopyOutlined />} aria-label={copied ? "Copied" : "Copy registration URL"} onClick={() => void copy()} /></Flex></Form.Item>}
      {loading && <Spin />}{!loading && !invitations.length && <Empty description="No invitations." />}
      {invitations.map(item => <Card key={item.id} size="small" title={item.plan_name} extra={<Flex gap="small" align="center">{item.status === "active" && <Button icon={<LinkOutlined />} aria-label={`Revoke invitation for ${item.plan_name}`} loading={revoking === item.id} disabled={busy} onClick={() => setConfirmRevoke(item)} />}<Tag color={item.status === "active" ? "success" : item.status === "used" ? "processing" : item.status === "expired" ? "warning" : "error"}>{item.status}</Tag></Flex>}><Typography.Text>{item.used_by || `Token ${item.token_hint}...`} · {new Date(item.expires_at).toLocaleString()}</Typography.Text></Card>)}
    </Flex>
  </Modal><Modal open={!!confirmRevoke} title="Revoke invitation?" destroyOnHidden onCancel={() => !busy && setConfirmRevoke(null)} mask={{ closable: !busy }} keyboard={!busy} closable={!busy} okText="Revoke" okButtonProps={{ "aria-label": "Revoke", "aria-busy": !!revoking, danger: true }} cancelButtonProps={{ disabled: busy }} confirmLoading={!!revoking} onOk={() => confirmRevoke && void revoke(confirmRevoke)}><Typography.Paragraph>The registration link for {confirmRevoke?.plan_name} will stop working.</Typography.Paragraph></Modal></>;
}
