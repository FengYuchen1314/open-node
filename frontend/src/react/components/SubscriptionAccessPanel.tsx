import { zhMessage, zhStatus } from "../../i18n/zh-CN";
import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Flex, Modal, Switch, Tag, Typography } from "../../ui";
import { ReloadOutlined, SyncOutlined } from "../../ui/icons";
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
    catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "请求节点访问状态失败"); }
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
      if (run === version.current) { setError(failure instanceof Error ? failure.message : "更新用户失败"); setBusy(false); timer.current = setTimeout(() => void load(), 5000); }
    }
  }
  const reason = (value: string) => ({ available: "已启用", disabled: "账户已停用", no_plan: "未分配套餐", expired: "已过期", quota_exceeded: "超出配额", node_not_in_plan: "不在当前套餐内" } as Record<string, string>)[value] ?? zhStatus(value);
  return <Card title="节点访问" aria-label="节点访问" extra={<Flex gap="small">
    <Button aria-label="刷新访问状态" icon={<ReloadOutlined />} disabled={busy} onClick={() => void load()} />
    <Button aria-label="同步节点访问权限" icon={<SyncOutlined />} loading={busy} disabled={!state?.managed} onClick={() => void load(true)} />
  </Flex>}>
    <Flex vertical gap="middle">
      <Flex align="center" gap="small"><Switch aria-label="启用账户" checked={isActive} disabled={busy} onChange={active => active ? void update(true) : setConfirm(true)} /><Typography.Text>启用账户</Typography.Text></Flex>
      {error && <Alert type="error" title={zhMessage(error)} showIcon />}
      {state && !state.managed && <Typography.Text type="secondary">暂无托管凭据</Typography.Text>}
      {state?.servers.map(server => <Card key={server.server_id} size="small" title={server.server_name}
        extra={<Tag color={server.status === "applied" ? "success" : server.status === "failed" ? "error" : "warning"}>{zhStatus(server.status)}</Tag>}>
        {server.entries.map(entry => <Flex key={`${entry.inbound_tag}:${entry.email}`} justify="space-between" wrap gap="small"><span>{entry.inbound_tag}</span><span>{reason(entry.reason)}</span></Flex>)}
        {server.error && <Alert type="error" title={zhMessage(server.error)} showIcon />}
      </Card>)}
    </Flex>
    <Modal open={confirm} title="停用账户？" onCancel={() => setConfirm(false)} onOk={() => void update(false)} okText="停用" okButtonProps={{ "aria-label": "停用", danger: true }} destroyOnHidden>
      {username} 将失去托管节点的访问权限。应用此变更会重启 Xray，并断开其当前连接。
    </Modal>
  </Card>;
}
