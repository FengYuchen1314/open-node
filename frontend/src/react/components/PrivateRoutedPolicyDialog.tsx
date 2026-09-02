import { zhMessage } from "../../i18n/zh-CN";
import { useEffect, useRef, useState } from "react";
import { Alert, Form, Modal, Switch } from "../../ui";
import type { PrivateRoutedPolicy } from "../../domain/private-routed-nodes";
import { updatePrivateRoutePolicy } from "../../services/private-routed-nodes";
import StrictInputNumber from "./StrictInputNumber";

export interface PrivateRoutedPolicyDialogProps {
  open: boolean; onOpenChange: (open: boolean) => void; policy: PrivateRoutedPolicy | null;
  onSaved?: (value: PrivateRoutedPolicy) => void;
}
export default function PrivateRoutedPolicyDialog(props: PrivateRoutedPolicyDialogProps) {
  return props.open ? <PolicyContent {...props} /> : null;
}
function PolicyContent({ policy, onOpenChange, onSaved }: PrivateRoutedPolicyDialogProps) {
  const [form, setForm] = useState(() => ({ enabled: policy?.enabled ?? false, max_nodes: policy?.max_nodes ?? 2, daily_limit: policy?.daily_limit ?? 5 }));
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const version = useRef(0);
  useEffect(() => () => { ++version.current; }, []);
  const valid = Number.isInteger(form.max_nodes) && form.max_nodes >= 1 && form.max_nodes <= 20
    && Number.isInteger(form.daily_limit) && form.daily_limit >= 1 && form.daily_limit <= 100;
  async function save() {
    if (busy || !valid) return;
    const run = ++version.current; setBusy(true); setError("");
    try { const value = await updatePrivateRoutePolicy(form); if (run === version.current) { onSaved?.(value); onOpenChange(false); } }
    catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "更新策略失败"); }
    finally { if (run === version.current) setBusy(false); }
  }
  return <Modal open title="私有路由策略" width={560} destroyOnHidden mask={{ closable: !busy }} keyboard={!busy} closable={!busy}
    onCancel={() => !busy && onOpenChange(false)} onOk={() => void save()} okText="保存" confirmLoading={busy}
    okButtonProps={{ "aria-label": "保存", "aria-busy": busy, disabled: !valid }} cancelButtonProps={{ disabled: busy }}>
    {error && <Alert type="error" title={zhMessage(error)} showIcon />}
    <Form layout="vertical" preserve={false} onFinish={() => void save()} disabled={busy}>
      <Form.Item label="允许用户创建私有路由"><Switch aria-label="允许用户创建私有路由" checked={form.enabled} onChange={enabled => setForm({ ...form, enabled })} /></Form.Item>
      <Form.Item label="每位用户的路由数"><StrictInputNumber aria-label="每位用户的路由数" value={form.max_nodes} aria-valuemin={1} aria-valuemax={20} onChange={number => setForm({ ...form, max_nodes: number ?? Number.NaN })} /></Form.Item>
      <Form.Item label="每日操作次数"><StrictInputNumber aria-label="每日操作次数" value={form.daily_limit} aria-valuemin={1} aria-valuemax={100} onChange={number => setForm({ ...form, daily_limit: number ?? Number.NaN })} /></Form.Item>
    </Form>
  </Modal>;
}
