import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Flex, Form, Input, Modal, Spin, Tag } from "antd";
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
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "IP policy unavailable"); }
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
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "IP policy update failed"); }
    finally { if (run === version.current) setSaving(false); }
  }
  return <Modal open title="Subscription IP access" width={560} destroyOnHidden mask={{ closable: false }} keyboard={!saving} closable={!saving}
    onCancel={() => !saving && onOpenChange(false)} onOk={() => void save()} okText="Save" confirmLoading={saving}
    cancelButtonProps={{ disabled: saving }} okButtonProps={{ "aria-label": "Save", "aria-busy": saving, disabled: loading || !policy }}>
    <Flex vertical gap="middle">
      <Tag color={policy?.enabled ? "warning" : "success"}>{policy?.enabled ? "Restricted" : "Unrestricted"}</Tag>
      {loading && <Spin />}{error && <Alert type="error" title={error} showIcon />}
      <Form layout="vertical" preserve={false} onFinish={() => void save()}><Form.Item label="Allowed IPs and CIDRs">
        <Input.TextArea aria-label="Allowed IPs and CIDRs" value={value} onChange={event => setValue(event.target.value)} placeholder={"203.0.113.8\n2001:db8::/48"}
          rows={7} disabled={loading || saving} spellCheck={false} autoComplete="off" />
      </Form.Item></Form>
    </Flex>
  </Modal>;
}
