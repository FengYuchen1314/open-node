import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Flex, Modal, Switch, Tag, Typography } from "antd";
import { ReloadOutlined, SyncOutlined } from "@ant-design/icons";
import type { ProductUser, SubscriptionAccessResponse } from "../../domain/subscriptions";
import { getSubscriptionAccess, setProductUserActive, syncSubscriptionAccess } from "../../services/subscriptions";

export interface SubscriptionAccessPanelProps {
  username: string; isActive: boolean; refreshKey?: string; onUpdated?: (user: ProductUser) => void;
}
export default function SubscriptionAccessPanel(props: SubscriptionAccessPanelProps) {
  return props.username ? <AccessContent key={`${props.username}:${props.refreshKey ?? ""}`} {...props} /> : null;
}
function AccessContent({ username, isActive, onUpdated }: SubscriptionAccessPanelProps) {
  const [state, setState] = useState<SubscriptionAccessResponse | null>(null), [busy, setBusy] = useState(false);
  const [error, setError] = useState(""), [confirm, setConfirm] = useState(false);
  const version = useRef(0), timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const updated = useRef(onUpdated); updated.current = onUpdated;
  async function load(sync = false) {
    const run = ++version.current; clearTimeout(timer.current);
    if (sync) setBusy(true);
    try { const result = await (sync ? syncSubscriptionAccess : getSubscriptionAccess)(username); if (run === version.current) { setState(result); setError(""); } }
    catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "Access request failed"); }
    finally { if (run === version.current) { setBusy(false); timer.current = setTimeout(() => void load(), 5000); } }
  }
  useEffect(() => { void load(); return () => { ++version.current; clearTimeout(timer.current); }; }, []);
  async function update(active: boolean) {
    if (busy) return;
    const run = ++version.current; clearTimeout(timer.current); setConfirm(false); setBusy(true); setError("");
    try {
      const response = await setProductUserActive(username, active);
      if (run !== version.current) return;
      updated.current?.(response.user); void load();
    } catch (failure) {
      if (run === version.current) { setError(failure instanceof Error ? failure.message : "User update failed"); setBusy(false); timer.current = setTimeout(() => void load(), 5000); }
    }
  }
  const reason = (value: string) => ({ available: "Enabled", disabled: "Account disabled", no_plan: "No plan", expired: "Expired", quota_exceeded: "Quota exceeded", node_not_in_plan: "Outside current plan" } as Record<string, string>)[value] ?? value;
  return <Card title="Node access" aria-label="Node access" extra={<Flex gap="small">
    <Button aria-label="Refresh access status" icon={<ReloadOutlined />} disabled={busy} onClick={() => void load()} />
    <Button aria-label="Reconcile node access" icon={<SyncOutlined />} loading={busy} disabled={!state?.managed} onClick={() => void load(true)} />
  </Flex>}>
    <Flex vertical gap="middle">
      <Flex align="center" gap="small"><Switch aria-label="Account enabled" checked={isActive} disabled={busy} onChange={active => active ? void update(true) : setConfirm(true)} /><Typography.Text>Account enabled</Typography.Text></Flex>
      {error && <Alert type="error" title={error} showIcon />}
      {state && !state.managed && <Typography.Text type="secondary">No managed credentials</Typography.Text>}
      {state?.servers.map(server => <Card key={server.server_id} size="small" title={server.server_name}
        extra={<Tag color={server.status === "applied" ? "success" : server.status === "failed" ? "error" : "warning"}>{server.status}</Tag>}>
        {server.entries.map(entry => <Flex key={`${entry.inbound_tag}:${entry.email}`} justify="space-between" wrap gap="small"><span>{entry.inbound_tag}</span><span>{reason(entry.reason)}</span></Flex>)}
        {server.error && <Alert type="error" title={server.error} showIcon />}
      </Card>)}
    </Flex>
    <Modal open={confirm} title="Disable account?" onCancel={() => setConfirm(false)} onOk={() => void update(false)} okText="Disable" okButtonProps={{ danger: true }} destroyOnHidden>
      {username} will lose access on managed nodes. Applying this change restarts Xray and disconnects its current connections.
    </Modal>
  </Card>;
}
