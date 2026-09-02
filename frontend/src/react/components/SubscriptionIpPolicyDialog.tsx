import { zhMessage } from "../../i18n/zh-CN";
import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Flex, Form, Input, Modal, Spin, Tag } from "../../ui";
import type { SubscriptionIpPolicy } from "../../domain/subscriptions";
import { subscriberIpPolicy, updateSubscriberIpPolicy } from "../../services/subscriber-auth";
import { getProductUserIpPolicy, updateProductUserIpPolicy } from "../../services/subscriptions";

export interface SubscriptionIpPolicyDialogProps {
  open: boolean; onOpenChange: (open: boolean) => void; username?: string; subscriber?: boolean;
  onUpdated?: (value: SubscriptionIpPolicy) => void;
}
export default function SubscriptionIpPolicyDialog(props: SubscriptionIpPolicyDialogProps) {
  return props.open ? <IpPolicyContent key={`${props.username}:${!!props.subscriber}`} {...props} /> : null;
}
function IpPolicyContent({ username = "", subscriber = false, onOpenChange, onUpdated }: SubscriptionIpPolicyDialogProps) {
  const [policy, setPolicy] = useState<SubscriptionIpPolicy | null>(null), [value, setValue] = useState("");
  const [loading, setLoading] = useState(false), [saving, setSaving] = useState(false), [error, setError] = useState("");
  const version = useRef(0);
  const load = useCallback(async () => {
    const run = ++version.current; setLoading(true); setError("");
    try {
      const result = subscriber ? await subscriberIpPolicy() : await getProductUserIpPolicy(username);
      if (run === version.current) { setPolicy(result); setValue(result.networks.join("\n")); }
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "无法读取 IP 访问策略"); }
    finally { if (run === version.current) setLoading(false); }
  }, [username, subscriber]);
  useEffect(() => { void load(); return () => { ++version.current; }; }, [load]);
  async function save() {
    if (saving || loading || !policy) return;
    const run = ++version.current; setSaving(true); setError("");
    const networks = value.split(/[\s,]+/).map(item => item.trim()).filter(Boolean);
    try {
      const result = subscriber ? await updateSubscriberIpPolicy(networks) : await updateProductUserIpPolicy(username, networks);
      if (run !== version.current) return;
      setPolicy(result); setValue(result.networks.join("\n")); onUpdated?.(result); onOpenChange(false);
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "更新 IP 访问策略失败"); }
    finally { if (run === version.current) setSaving(false); }
  }
  return <Modal open title="订阅 IP 访问控制" width={560} destroyOnHidden mask={{ closable: false }} keyboard={!saving} closable={!saving}
    onCancel={() => !saving && onOpenChange(false)} onOk={() => void save()} okText="保存" confirmLoading={saving}
    cancelButtonProps={{ disabled: saving }} okButtonProps={{ "aria-label": "保存", "aria-busy": saving, disabled: loading || !policy }}>
    <Flex vertical gap="middle">
      <Tag color={policy?.enabled ? "warning" : "success"}>{policy?.enabled ? "限制来源" : "不限来源"}</Tag>
      {loading && <Spin />}{error && <Alert type="error" title={zhMessage(error)} showIcon />}
      <Form layout="vertical" preserve={false} onFinish={() => void save()}><Form.Item label="允许的 IP 地址和 CIDR 网段">
        <Input.TextArea aria-label="允许的 IP 地址和 CIDR 网段" value={value} onChange={event => setValue(event.target.value)} placeholder={"203.0.113.8\n2001:db8::/48"}
          rows={7} disabled={loading || saving} spellCheck={false} autoComplete="off" />
      </Form.Item></Form>
    </Flex>
  </Modal>;
}
