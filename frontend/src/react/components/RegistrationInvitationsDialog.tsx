import { zhMessage, zhStatus } from "../../i18n/zh-CN";
import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Empty, Flex, Form, Input, Modal, Select, Spin, Tag, Typography } from "../../ui";
import { CheckOutlined, CopyOutlined, LinkOutlined, UserAddOutlined } from "../../ui/icons";
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
    void listRegistrationInvitations().then(value => { if (run === version.current) setInvitations(value.invitations); }).catch(failure => { if (run === version.current) setError(failure instanceof Error ? failure.message : "无法读取注册邀请"); }).finally(() => { if (run === version.current) setLoading(false); });
    return () => { ++version.current; };
  }, []);
  useEffect(() => { if (!plans.some(plan => plan.id === planId)) setPlanId(plans[0]?.id ?? ""); }, [plans, planId]);
  async function submit() {
    if (!plans.some(plan => plan.id === planId) || busy || loading) return;
    const run = ++version.current; setSaving(true); setError(""); setCreated(null); setCopied(false);
    try {
      const value = await createRegistrationInvitation({ plan_id: planId, expires_minutes: expiresMinutes });
      if (run !== version.current) return; setCreated(value); setInvitations(previous => [value.invitation, ...previous.filter(item => item.id !== value.invitation.id)]);
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "创建注册邀请失败"); }
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
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "撤销注册邀请失败"); }
    finally { if (run === version.current) setRevoking(""); }
  }
  async function copy() {
    if (!created) return; const run = version.current;
    try { await navigator.clipboard.writeText(created.registration_url); if (run === version.current) setCopied(true); }
    catch { if (run === version.current) setError("无法访问剪贴板"); }
  }
  return <><Modal open title="注册邀请" width={720} destroyOnHidden mask={{ closable: !busy }} keyboard={!busy} closable={!busy} onCancel={() => !busy && onOpenChange(false)} footer={<Button aria-label="关闭" disabled={busy} onClick={() => onOpenChange(false)}>关闭</Button>}>
    <Flex vertical gap="middle">{error && <Alert type="error" title={zhMessage(error)} showIcon />}
      <Form layout="vertical" preserve={false} disabled={busy || loading}>
        <Form.Item label="套餐"><Select aria-label="套餐" value={planId || undefined} options={plans.map(plan => ({ label: plan.name, value: plan.id }))} onChange={setPlanId} /></Form.Item>
        <Form.Item label="有效期"><Select aria-label="有效期" value={expiresMinutes} options={[{ label: "1 小时", value: 60 }, { label: "24 小时", value: 1440 }, { label: "3 天", value: 4320 }, { label: "7 天", value: 10080 }]} onChange={setExpiresMinutes} /></Form.Item>
        <Button type="primary" icon={<UserAddOutlined />} aria-label="创建注册邀请" loading={saving} disabled={!planId || busy || loading} onClick={() => void submit()}>创建邀请</Button>
      </Form>
      {created && <Form.Item label="注册链接"><Flex gap="small"><Input aria-label="注册链接" readOnly value={created.registration_url} /><Button icon={copied ? <CheckOutlined /> : <CopyOutlined />} aria-label={copied ? "已复制" : "复制注册链接"} onClick={() => void copy()} /></Flex></Form.Item>}
      {loading && <Spin />}{!loading && !invitations.length && <Empty description="暂无注册邀请。" />}
      {invitations.map(item => <Card key={item.id} size="small" title={item.plan_name} extra={<Flex gap="small" align="center">{item.status === "active" && <Button icon={<LinkOutlined />} aria-label={`撤销 ${item.plan_name} 的邀请`} loading={revoking === item.id} disabled={busy} onClick={() => setConfirmRevoke(item)} />}<Tag color={item.status === "active" ? "success" : item.status === "used" ? "processing" : item.status === "expired" ? "warning" : "error"}>{zhStatus(item.status)}</Tag></Flex>}><Typography.Text>{item.used_by || `令牌 ${item.token_hint}...`} · {new Date(item.expires_at).toLocaleString("zh-CN")}</Typography.Text></Card>)}
    </Flex>
  </Modal><Modal open={!!confirmRevoke} title="撤销邀请？" destroyOnHidden onCancel={() => !busy && setConfirmRevoke(null)} mask={{ closable: !busy }} keyboard={!busy} closable={!busy} okText="撤销" okButtonProps={{ "aria-label": "撤销", "aria-busy": !!revoking, danger: true }} cancelButtonProps={{ disabled: busy }} confirmLoading={!!revoking} onOk={() => confirmRevoke && void revoke(confirmRevoke)}><Typography.Paragraph>{confirmRevoke?.plan_name} 的注册链接将失效。</Typography.Paragraph></Modal></>;
}
