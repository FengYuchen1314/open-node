import { DeleteOutlined, ReloadOutlined, SafetyCertificateOutlined, SaveOutlined, StopOutlined } from "../../ui/icons";
import { Alert, App, Button, Card, Checkbox, Flex, Form, Input, InputNumber, Select, Space, Table, Tag, Typography } from "../../ui";
import { useEffect, useRef, useState } from "react";

import type { SecurityBan, SecurityEvent, SecurityEventKind, SecuritySettings } from "../../domain/security";
import {
  createSecurityBan,
  loadSecurityBans,
  loadSecurityEvents,
  loadSecuritySettings,
  removeSecurityBan,
  saveSecuritySettings,
  securityError,
} from "../../services/security";

const labels: Record<SecurityEventKind, string> = {
  probe: "订阅探测", ban: "自动封禁", unban: "解除封禁", ban_manual: "手动封禁",
  login_fail: "登录失败", login_locked: "登录锁定",
};
const initial: SecuritySettings = { revision: 0, brute_force_enabled: true, brute_force_max_failures: 5,
  brute_force_window_minutes: 1440, brute_force_block_minutes: 1440, skip_local_ip: true, license_required: false };
function when(value: string | null) { return value ? new Date(value).toLocaleString("zh-CN") : "永久"; }

export default function SecurityManagementPanel() {
  const { modal } = App.useApp(), alive = useRef(true);
  const [settings, setSettings] = useState<SecuritySettings | null>(null), [draft, setDraft] = useState(initial);
  const [bans, setBans] = useState<SecurityBan[]>([]), [events, setEvents] = useState<SecurityEvent[]>([]);
  const [hasMore, setHasMore] = useState(false), [offset, setOffset] = useState(0);
  const [kind, setKind] = useState<SecurityEventKind | undefined>(), [filterIp, setFilterIp] = useState("");
  const [banIp, setBanIp] = useState(""), [permanent, setPermanent] = useState(false);
  const [busy, setBusy] = useState(""), [notice, setNotice] = useState<{ type: "error" | "success" | "warning"; text: string } | null>(null);

  async function refresh(nextOffset = offset, preserveNotice = false) {
    setBusy("read"); if (!preserveNotice) setNotice(null);
    try {
      const [saved, currentBans, history] = await Promise.all([
        loadSecuritySettings(), loadSecurityBans(), loadSecurityEvents({ kind, ip: filterIp, limit: 100, offset: nextOffset }),
      ]);
      if (!alive.current) return;
      setSettings(saved); setDraft(saved); setBans(currentBans); setEvents(history.events);
      setHasMore(history.has_more); setOffset(history.offset);
    } catch (error) { if (alive.current) setNotice({ type: "error", text: securityError(error) }); }
    finally { if (alive.current) setBusy(""); }
  }
  useEffect(() => { alive.current = true; void refresh(0); return () => { alive.current = false; }; }, []);

  async function save() {
    if (!settings || busy) return;
    setBusy("save"); setNotice(null);
    try { const value = await saveSecuritySettings({ ...draft, revision: settings.revision }); if (alive.current) { setSettings(value); setDraft(value); setNotice({ type: "success", text: "安全阈值已保存并立即生效。" }); } }
    catch (error) { if (alive.current) { setNotice({ type: "warning", text: `${securityError(error)}正在重新读取，没有自动重复提交。` }); setBusy(""); await refresh(0, true); return; } }
    finally { if (alive.current) setBusy(""); }
  }
  async function addBan() {
    if (!banIp.trim() || busy) return;
    setBusy("ban"); setNotice(null);
    try { await createSecurityBan(banIp.trim(), permanent); if (alive.current) { setBanIp(""); setPermanent(false); setNotice({ type: "success", text: "IP 封禁已生效。" }); } }
    catch (error) { if (alive.current) setNotice({ type: "warning", text: `${securityError(error)}正在重新读取，没有自动重复提交。` }); }
    finally { if (alive.current) { setBusy(""); void refresh(0, true); } }
  }
  function unban(row: SecurityBan) {
    modal.confirm({ title: `解除 ${row.ip} 的封禁？`, content: "会保留解封事件，不删除历史记录。", okText: "解除封禁", cancelText: "取消", onOk: async () => {
      setBusy(`unban:${row.ip}`); setNotice(null);
      try { await removeSecurityBan(row.ip); if (alive.current) setNotice({ type: "success", text: "IP 已解除封禁。" }); }
      catch (error) { if (alive.current) setNotice({ type: "warning", text: `${securityError(error)}正在重新读取，没有自动重复提交。` }); }
      finally { if (alive.current) { setBusy(""); void refresh(0, true); } }
    } });
  }
  const dirty = Boolean(settings && ["brute_force_enabled", "brute_force_max_failures", "brute_force_window_minutes", "brute_force_block_minutes", "skip_local_ip"].some(key => settings[key as keyof SecuritySettings] !== draft[key as keyof SecuritySettings]));

  return <Card title={<Space><SafetyCertificateOutlined />安全事件与 IP 封禁</Space>}>
    <Alert type="info" showIcon title="封禁用于阻止订阅短码和 Token 枚举，不修改系统防火墙，也不拦截 Agent 管理连接。" style={{ marginBottom: 16 }} />
    {notice && <Alert type={notice.type} showIcon title={notice.text} style={{ marginBottom: 16 }} />}
    <Typography.Title level={4}>暴力探测阈值</Typography.Title>
    <Form layout="vertical" onFinish={() => void save()} disabled={!settings || Boolean(busy)}>
      <Flex gap="middle" wrap>
        <Form.Item><Checkbox checked={draft.brute_force_enabled} onChange={event => setDraft(value => ({ ...value, brute_force_enabled: event.target.checked }))}>启用订阅探测封禁</Checkbox></Form.Item>
        <Form.Item><Checkbox checked={draft.skip_local_ip} onChange={event => setDraft(value => ({ ...value, skip_local_ip: event.target.checked }))}>跳过本地与私有 IP</Checkbox></Form.Item>
      </Flex>
      <Flex gap="middle" wrap>
        <Form.Item label="失败次数"><InputNumber min={2} max={100} value={draft.brute_force_max_failures} onChange={value => value && setDraft(row => ({ ...row, brute_force_max_failures: value }))} /></Form.Item>
        <Form.Item label="统计窗口（分钟）"><InputNumber min={1} max={10080} value={draft.brute_force_window_minutes} onChange={value => value && setDraft(row => ({ ...row, brute_force_window_minutes: value }))} /></Form.Item>
        <Form.Item label="临时封禁（分钟）"><InputNumber min={1} max={43200} value={draft.brute_force_block_minutes} onChange={value => value && setDraft(row => ({ ...row, brute_force_block_minutes: value }))} /></Form.Item>
      </Flex>
      <Button type="primary" htmlType="submit" aria-label="保存安全阈值" icon={<SaveOutlined aria-hidden />} loading={busy === "save"} disabled={!dirty}>保存安全阈值</Button>
    </Form>

    <Typography.Title level={4} style={{ marginTop: 28 }}>当前封禁</Typography.Title>
    <Flex gap="middle" wrap align="center" style={{ marginBottom: 16 }}>
      <Input aria-label="要封禁的 IP" placeholder="IPv4 或 IPv6" value={banIp} onChange={event => setBanIp(event.target.value)} style={{ width: 260 }} disabled={Boolean(busy)} />
      <Checkbox checked={permanent} onChange={event => setPermanent(event.target.checked)} disabled={Boolean(busy)}>永久封禁</Checkbox>
      <Button danger aria-label="手动封禁" icon={<StopOutlined aria-hidden />} loading={busy === "ban"} disabled={!banIp.trim() || Boolean(busy)} onClick={() => void addBan()}>手动封禁</Button>
    </Flex>
    <Table<SecurityBan> rowKey="ip" dataSource={bans} pagination={false} size="small" locale={{ emptyText: "当前没有生效的封禁" }} columns={[
      { title: "IP", dataIndex: "ip" },
      { title: "来源", render: (_, row) => row.reason === "manual" ? <Tag color="orange">手动</Tag> : <Tag color="red">自动</Tag> },
      { title: "失败次数", dataIndex: "fail_count" },
      { title: "开始", render: (_, row) => when(row.banned_at) },
      { title: "到期", render: (_, row) => row.permanent ? <Tag color="red">永久</Tag> : when(row.expires_at) },
      { title: "操作者", render: (_, row) => row.actor || "系统" },
      { title: "操作", render: (_, row) => <Button danger type="link" aria-label={`解除封禁：${row.ip}`} icon={<DeleteOutlined aria-hidden />} loading={busy === `unban:${row.ip}`} onClick={() => unban(row)}>解除</Button> },
    ]} />

    <Typography.Title level={4} style={{ marginTop: 28 }}>安全事件</Typography.Title>
    <Flex gap="middle" wrap style={{ marginBottom: 16 }}>
      <Select allowClear aria-label="事件类型" placeholder="全部类型" style={{ width: 180 }} value={kind} onChange={setKind} options={Object.entries(labels).map(([value, label]) => ({ value, label }))} />
      <Input aria-label="事件 IP" placeholder="精确 IP，可留空" value={filterIp} onChange={event => setFilterIp(event.target.value)} style={{ width: 240 }} />
      <Button aria-label="应用筛选并刷新" icon={<ReloadOutlined aria-hidden />} loading={busy === "read"} disabled={Boolean(busy)} onClick={() => void refresh(0)}>应用筛选并刷新</Button>
    </Flex>
    <Table<SecurityEvent> rowKey="id" dataSource={events} pagination={false} size="small" scroll={{ x: 960 }} locale={{ emptyText: "暂无安全事件" }} columns={[
      { title: "时间", width: 180, render: (_, row) => when(row.at) },
      { title: "类型", width: 110, render: (_, row) => <Tag>{labels[row.kind]}</Tag> },
      { title: "IP", dataIndex: "ip", width: 180 },
      { title: "账户", render: (_, row) => row.username || "—", width: 130 },
      { title: "路由", render: (_, row) => row.path || "—" },
      { title: "详情", render: (_, row) => row.detail || "—", width: 100 },
      { title: "操作者", render: (_, row) => row.actor || "系统", width: 110 },
    ]} />
    <Space style={{ marginTop: 16 }}>
      <Button disabled={offset === 0 || Boolean(busy)} onClick={() => void refresh(Math.max(0, offset - 100))}>上一页</Button>
      <Typography.Text>第 {Math.floor(offset / 100) + 1} 页</Typography.Text>
      <Button disabled={!hasMore || Boolean(busy)} onClick={() => void refresh(offset + 100)}>下一页</Button>
    </Space>
  </Card>;
}
