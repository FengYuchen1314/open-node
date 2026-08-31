import { zhMessage } from "../../i18n/zh-CN";
import { useEffect, useRef, useState } from "react";
import { Alert, Button, Flex, Form, Input, Modal, Select } from "antd";
import { CheckOutlined, CopyOutlined } from "@ant-design/icons";
import type { TemporarySubscription } from "../../domain/temporary-subscriptions";
import { createTemporarySubscription } from "../../services/temporary-subscriptions";
import StrictInputNumber from "./StrictInputNumber";

export interface TemporarySubscriptionDialogProps {
  open: boolean; onOpenChange: (open: boolean) => void; username: string;
  nodes: { title: string; value: string }[]; onCreated?: (value: TemporarySubscription) => void;
}
export default function TemporarySubscriptionDialog(props: TemporarySubscriptionDialogProps) {
  return props.open ? <TemporaryContent key={props.username} {...props} /> : null;
}
function TemporaryContent({ username, nodes, onOpenChange, onCreated }: TemporarySubscriptionDialogProps) {
  const [form, setForm] = useState({ label: "临时订阅", node_ids: nodes.map(node => node.value), max_access: 1, expires_in_seconds: 300 });
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [copied, setCopied] = useState(false);
  const [created, setCreated] = useState<TemporarySubscription | null>(null);
  const version = useRef(0);
  useEffect(() => () => { ++version.current; }, []);
  const canCreate = !!username && !!form.label.trim() && form.node_ids.length > 0
    && form.node_ids.every(id => nodes.some(node => node.value === id)) && Number.isInteger(form.max_access)
    && form.max_access >= 1 && form.max_access <= 100 && !busy;
  async function submit() {
    if (!canCreate) return;
    const run = ++version.current; setBusy(true); setError("");
    try {
      const value = await createTemporarySubscription({ username, ...form, label: form.label.trim() });
      if (run === version.current) { setCreated(value); onCreated?.(value); }
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "创建临时订阅链接失败"); }
    finally { if (run === version.current) setBusy(false); }
  }
  async function copyLink() {
    if (!created) return;
    const run = version.current;
    try { await navigator.clipboard.writeText(created.subscription_url); if (run === version.current) setCopied(true); }
    catch { if (run === version.current) setError("无法访问剪贴板"); }
  }
  return <Modal open title="创建临时订阅链接" width={640} destroyOnHidden mask={{ closable: !busy }} keyboard={!busy} closable={!busy}
    onCancel={() => !busy && onOpenChange(false)} footer={<Flex justify="space-between"><Button disabled={busy} onClick={() => onOpenChange(false)}>{created ? "关闭" : "取消"}</Button>
      {!created && <Button type="primary" aria-label="创建" aria-busy={busy} loading={busy} disabled={!canCreate} onClick={() => void submit()}>创建</Button>}</Flex>}>
    {error && <Alert type="error" title={zhMessage(error)} showIcon />}
    {!created ? <Form layout="vertical" preserve={false} onFinish={() => void submit()} disabled={busy}>
      <Form.Item label="用户"><Input aria-label="用户" value={username} readOnly /></Form.Item>
      <Form.Item label="标签"><Input aria-label="标签" value={form.label} maxLength={120} onChange={event => setForm({ ...form, label: event.target.value })} /></Form.Item>
      <Form.Item label="节点"><Select aria-label="节点" mode="multiple" value={form.node_ids} optionFilterProp="label"
        options={nodes.map(node => ({ label: node.title, value: node.value }))} onChange={node_ids => setForm({ ...form, node_ids })} /></Form.Item>
      <Form.Item label="下载次数"><StrictInputNumber aria-label="下载次数" aria-valuemin={1} aria-valuemax={100} value={form.max_access} onChange={number => setForm({ ...form, max_access: number ?? Number.NaN })} /></Form.Item>
      <Form.Item label="有效期"><Select aria-label="有效期" value={form.expires_in_seconds} onChange={expires_in_seconds => setForm({ ...form, expires_in_seconds })}
        options={[{ label: "5 分钟", value: 300 }, { label: "15 分钟", value: 900 }, { label: "1 小时", value: 3600 }]} /></Form.Item>
    </Form> : <Flex vertical gap="middle"><Form.Item label="临时订阅链接"><Flex gap="small"><Input aria-label="临时订阅链接" value={created.subscription_url} readOnly />
      <Button icon={copied ? <CheckOutlined /> : <CopyOutlined />} aria-label={copied ? "已复制" : "复制临时订阅链接"} onClick={() => void copyLink()} /></Flex></Form.Item>
      <Alert type="info" title="链接过期不会撤回已下载的凭据。" showIcon />
    </Flex>}
  </Modal>;
}
