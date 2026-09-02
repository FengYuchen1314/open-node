import { Alert, Button, Card, Flex, Spin, Typography } from "../../ui";
import { useEffect, useMemo, useRef } from "react";
import { Link } from "react-router-dom";
import { accountExternalSubscriptions } from "../../services/external-subscriptions";
import { loadSubscriberSession } from "../../services/subscriber-auth";
import ExternalSubscriptionsPanel from "../components/ExternalSubscriptionsPanel";
import { useSubscriberSession } from "../hooks/useSession";

export default function AccountExternalSourcesView() {
  const account = useSubscriberSession();
  const loading = useRef<Promise<void> | null>(null);
  useEffect(() => {
    if (!account.ready && !loading.current) loading.current = loadSubscriberSession().finally(() => { loading.current = null; });
  }, [account.ready]);
  const username = account.session?.authenticated ? account.session.username : null;
  return <main className="application-content" style={{ maxWidth: 1200, marginInline: "auto", width: "100%", boxSizing: "border-box", minWidth: 0 }}>
    <Flex vertical gap="middle">
      <Flex align="center" justify="space-between" wrap gap="small"><Typography.Title level={2} style={{ margin: 0 }}>我的外部订阅</Typography.Title><Link to="/account">返回用户中心</Link></Flex>
      {!account.ready ? <Spin aria-label="正在读取用户会话" /> : username ? <OwnedSources key={`${username}:${account.session?.csrf_token ?? ""}`} username={username} /> : <Card>
        <Alert type="info" showIcon title="请先登录用户中心，再管理自己的外部订阅。" />
        <Button href="/account" style={{ marginTop: 16 }}>前往用户中心登录</Button>
      </Card>}
    </Flex>
  </main>;
}

function OwnedSources({ username }: { username: string }) {
  const api = useMemo(() => accountExternalSubscriptions(username), [username]);
  const users = useMemo(() => [{ username, display_name: username, is_active: true }], [username]);
  return <ExternalSubscriptionsPanel accountUsername={username} users={users} api={api} />;
}
