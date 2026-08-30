import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Col, Form, Input, Modal, Row, Select, Space, Table, Tag, Typography } from "antd";
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { validAutoSpeedRule, type AutoSpeedRule } from "../../domain/auto-speed";
import type { AgentCommand, AgentLimiterOperationRequest, XrayRuntimeInbound } from "../../domain/inventory";
import { listServerCommands, queueAgentOperation } from "../../services/inventory";
import AutoSpeedRuleEditor from "./AutoSpeedRuleEditor";
import StrictInputNumber from "./StrictInputNumber";

export interface LimiterPanelProps {
  serverId: string;
  inbounds: XrayRuntimeInbound[];
  onCommands?: (serverId: string, commands: AgentCommand[]) => void;
}

type LimiterUser = { uid: number; email: string; speed_limit: number; device_limit: number; conn_group?: string; auto_speed_rules?: AutoSpeedRule[] };
type Policy = { inbound_tag: string; node_limit: number; users: LimiterUser[] | null; auto_speed_rules: AutoSpeedRule[] | null };
type Snapshot = {
  available: boolean; message?: string; revision?: string; inbounds?: Policy[];
  conn_counts?: Record<string, number>; user_speeds?: Record<string, number>;
  connection_rejections?: Record<string, number>;
  automatic_limits?: Record<string, { bytes_per_second: number; until: string }>;
};
type UserRow = { key: number; uid: number; email: string; mbps: number; connections: number; group: string; auto_speed_rules: AutoSpeedRule[] };
const BYTES_PER_MEGABIT = 125000;
// InputNumber range/precision props clamp on blur or Enter; never turn invalid input into unlimited zero.
const validRate = (value: number) => Number.isFinite(value) && (value === 0 || (value * BYTES_PER_MEGABIT >= 1 && value * BYTES_PER_MEGABIT <= 2 ** 50));
const tagsFor = (inbounds: XrayRuntimeInbound[], snapshot: Snapshot | null) => [...new Set([
  ...inbounds.map((item) => item.tag), ...(snapshot?.inbounds ?? []).map((item) => item.inbound_tag),
].filter((tag): tag is string => typeof tag === "string" && tag.length > 0))];

export default function LimiterPanel(props: LimiterPanelProps) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [selectedTag, setSelectedTag] = useState("");
  const [users, setUsers] = useState<UserRow[]>([]);
  const [rules, setRules] = useState<AutoSpeedRule[]>([]);
  const [nodeMbps, setNodeMbps] = useState(0);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [removalOpen, setRemovalOpen] = useState(false);
  const generation = useRef(0);
  const rowKey = useRef(0);
  const live = useRef({ ...props, selectedTag });
  live.current = { ...props, selectedTag };
  const inboundOptions = tagsFor(props.inbounds, snapshot);
  const filtered = users.filter((user) => `${user.email} ${user.group}`.toLowerCase().includes(search.toLowerCase()));
  const connections = Object.values(snapshot?.conn_counts ?? {}).reduce((total, value) => total + value, 0);
  const hasPolicy = snapshot?.inbounds?.some((item) => item.inbound_tag === selectedTag) ?? false;
  const emails = new Set<string>();
  const validUsers = users.every((user) => {
    if (typeof user.email !== "string") return false;
    const email = user.email.trim();
    if (!email || emails.has(email) || !validRate(user.mbps) || !Number.isInteger(user.connections) || user.connections < 0 || user.connections > 1000000) return false;
    emails.add(email);
    return true;
  });
  const ready = snapshot?.available === true && /^[0-9a-f]{64}$/.test(snapshot.revision ?? "") && Array.isArray(snapshot.inbounds);
  const valid = Boolean(selectedTag) && validRate(nodeMbps) && validUsers && rules.length <= 100 && rules.every(validAutoSpeedRule);

  function populate(tag: string, value: Snapshot | null) {
    const policy = value?.inbounds?.find((item) => item.inbound_tag === tag);
    const runtimeEmails = live.current.inbounds.find((item) => item.tag === tag)?.user_emails ?? [];
    setNodeMbps((policy?.node_limit ?? 0) / BYTES_PER_MEGABIT);
    setUsers((policy?.users ?? runtimeEmails.map((email): LimiterUser => ({ uid: 0, email, speed_limit: 0, device_limit: 0 }))).map((user) => ({
      key: ++rowKey.current, uid: user.uid, email: user.email, mbps: user.speed_limit / BYTES_PER_MEGABIT,
      connections: user.device_limit, group: user.conn_group ?? "", auto_speed_rules: (user.auto_speed_rules ?? []).map((rule) => ({ ...rule })),
    })));
    setRules((policy?.auto_speed_rules ?? []).map((rule) => ({ ...rule })));
    setSearch("");
    setPage(1);
  }

  async function run(kind: "limiter" | "limiter_status", body?: AgentLimiterOperationRequest) {
    const serverId = live.current.serverId;
    if (!serverId) return;
    const request = ++generation.current;
    const current = () => request === generation.current && serverId === live.current.serverId;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const queued = await queueAgentOperation(serverId, kind, body);
      for (let attempt = 0; attempt < 120; attempt += 1) {
        if (!current()) return;
        const response = await listServerCommands(serverId);
        if (!current()) return;
        live.current.onCommands?.(serverId, response.commands);
        const command = response.commands.find((item) => item.id === queued.command.id);
        if (command?.status === "failed" || command?.status === "skipped") throw new Error(command.result_error || "Limiter command failed.");
        if (command?.status === "succeeded") {
          const value = command.result_body as Snapshot | null;
          if (!value || typeof value.available !== "boolean") throw new Error("Unsupported limiter response.");
          setSnapshot(value);
          if (!value.available) throw new Error(value.message || "Native limiter unavailable.");
          if (!value.revision || !/^[0-9a-f]{64}$/.test(value.revision) || !Array.isArray(value.inbounds)) throw new Error("Invalid limiter state.");
          const options = tagsFor(live.current.inbounds, value);
          const tag = options.includes(live.current.selectedTag) ? live.current.selectedTag : options[0] ?? "";
          setSelectedTag(tag);
          live.current.selectedTag = tag;
          populate(tag, value);
          if (kind === "limiter") setMessage(body?.action === "remove" ? "Limits removed." : "Limits applied.");
          return;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 500));
      }
      throw new Error("Limiter command is still pending. Check command history.");
    } catch (failure) {
      if (current()) setError(failure instanceof Error ? failure.message : "Limiter request failed.");
    } finally {
      if (current()) setBusy(false);
    }
  }

  useEffect(() => {
    generation.current += 1;
    setSnapshot(null);
    setSelectedTag("");
    live.current.selectedTag = "";
    setUsers([]);
    setRules([]);
    setRemovalOpen(false);
    setBusy(false);
    setError("");
    setMessage("");
    void run("limiter_status");
    return () => { generation.current += 1; };
  }, [props.serverId]);

  useEffect(() => {
    if (!inboundOptions.includes(selectedTag)) {
      const tag = inboundOptions[0] ?? "";
      setSelectedTag(tag);
      live.current.selectedTag = tag;
      populate(tag, snapshot);
    }
  }, [props.inbounds, snapshot, selectedTag]);

  useEffect(() => setPage((current) => Math.min(current, Math.max(1, Math.ceil(filtered.length / 8)))), [filtered.length]);

  function updateUser(key: number, patch: Partial<UserRow>) {
    setUsers((rows) => rows.map((row) => row.key === key ? { ...row, ...patch } : row));
  }
  function addUser() {
    const options = props.inbounds.find((item) => item.tag === selectedTag)?.user_emails ?? [];
    setUsers([...users, { key: ++rowKey.current, uid: 0, email: options.find((email) => !users.some((user) => user.email === email)) ?? "", mbps: 0, connections: 0, group: "", auto_speed_rules: [] }]);
    setSearch("");
    setPage(Math.ceil((users.length + 1) / 8));
  }
  function save() {
    if (!valid || busy || !ready || !snapshot?.revision) return;
    void run("limiter", {
      inbound_tag: selectedTag, expected_revision: snapshot.revision, node_limit: Math.round(nodeMbps * BYTES_PER_MEGABIT),
      users: users.map((user) => ({ uid: user.uid, email: user.email.trim(), speed_limit: Math.round(user.mbps * BYTES_PER_MEGABIT), device_limit: user.connections, conn_group: user.group.trim(),
        ...(user.auto_speed_rules.length ? { auto_speed_rules: user.auto_speed_rules.map((rule) => ({ ...rule })) } : {}),
      })),
      auto_speed_rules: rules.map((rule) => ({ ...rule })),
    });
  }
  function remove() {
    if (!ready || !snapshot?.revision || !hasPolicy || busy) return;
    setRemovalOpen(false);
    void run("limiter", { action: "remove", inbound_tag: selectedTag, expected_revision: snapshot.revision });
  }

  return <Card size="small" title="Limits" extra={<Space wrap><Tag>{snapshot?.available ? `${connections} active connections` : "Unavailable"}</Tag><Button icon={<ReloadOutlined />} aria-label="Refresh limits" loading={busy} disabled={!props.serverId} onClick={() => void run("limiter_status")} /></Space>}>
    <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      {error && <Alert type="error" title={error} showIcon />}
      {message && <Alert type="success" title={message} showIcon />}
      <Typography.Paragraph type="secondary">Native Xray limits. Zero means unlimited. Limits use the revision returned by the latest status read.</Typography.Paragraph>
      <Form id="native-limiter-form" layout="vertical" disabled={busy || !ready} onFinish={save}>
        <Row gutter={16}>
          <Col xs={24} md={8}><Form.Item label="Inbound"><Select aria-label="Inbound" style={{ width: "100%" }} value={selectedTag || undefined} options={inboundOptions.map((value) => ({ value, label: value }))} onChange={(tag) => { setSelectedTag(tag); live.current.selectedTag = tag; populate(tag, snapshot); }} /></Form.Item></Col>
          <Col xs={24} md={8}><Form.Item label="Per-user cap Mbps" extra="0 = unlimited"><StrictInputNumber aria-label="Per-user cap Mbps" aria-valuemin={0} style={{ width: "100%" }} value={nodeMbps} onChange={(value) => setNodeMbps(value ?? Number.NaN)} /></Form.Item></Col>
          <Col xs={24} md={8}><Form.Item label="Search users"><Input aria-label="Search users" value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} allowClear /></Form.Item></Col>
        </Row>
        <Table<UserRow> rowKey="key" dataSource={filtered} locale={{ emptyText: "No users" }} scroll={{ x: 880 }} pagination={{ current: page, pageSize: 8, total: filtered.length, showSizeChanger: false, onChange: setPage }} columns={[
          { title: "Email", key: "email", render: (_, row) => <Input aria-label="Email" value={row.email} onChange={(event) => updateUser(row.key, { email: event.target.value })} /> },
          { title: "Cap Mbps", key: "rate", render: (_, row) => <StrictInputNumber aria-label="Cap Mbps" aria-valuemin={0} value={row.mbps} onChange={(value) => updateUser(row.key, { mbps: value ?? Number.NaN })} /> },
          { title: "Connections", key: "connections", render: (_, row) => <StrictInputNumber aria-label="Connections" aria-valuemin={0} aria-valuemax={1000000} value={row.connections} onChange={(value) => updateUser(row.key, { connections: value ?? Number.NaN })} /> },
          { title: "Connection group", key: "group", render: (_, row) => <Input aria-label="Connection group" value={row.group} onChange={(event) => updateUser(row.key, { group: event.target.value })} /> },
          { title: "Live usage", key: "usage", render: (_, row) => {
            const key = `${selectedTag}\0${row.email}`;
            const automatic = snapshot?.automatic_limits?.[key];
            return <Space orientation="vertical" size={0}><span>{snapshot?.conn_counts?.[row.group || row.email] ?? 0} active</span><span>{((snapshot?.user_speeds?.[row.email] ?? 0) / BYTES_PER_MEGABIT).toFixed(2)} Mbps</span><span>{snapshot?.connection_rejections?.[row.email] ?? 0} rejected</span>{row.auto_speed_rules.length > 0 && <span>{row.auto_speed_rules.length} automatic rules</span>}{automatic && <Tag color="warning">{(automatic.bytes_per_second / BYTES_PER_MEGABIT).toFixed(2)} Mbps until {automatic.until}</Tag>}</Space>;
          } },
          { title: "Actions", key: "actions", render: (_, row) => <Button danger icon={<DeleteOutlined />} aria-label={`Remove limit for ${row.email || "new user"}`} disabled={busy} onClick={() => setUsers((rows) => rows.filter((user) => user.key !== row.key))} /> },
        ]} />
        <Button icon={<PlusOutlined />} aria-label="Add limiter user" disabled={busy || !ready || !selectedTag || users.length >= 1000} onClick={addUser}>Add user</Button>
        <div style={{ marginTop: 16 }}><AutoSpeedRuleEditor value={rules} onChange={setRules} disabled={busy || !selectedTag || !ready} /></div>
        <Space wrap style={{ marginTop: 16 }}><Button type="primary" htmlType="submit" form="native-limiter-form" loading={busy} disabled={busy || !ready || !valid}>Save limits</Button><Button danger disabled={busy || !ready || !hasPolicy} onClick={() => setRemovalOpen(true)}>Remove limits</Button></Space>
      </Form>
    </Space>
    <Modal title="Remove limits?" open={removalOpen} onCancel={() => setRemovalOpen(false)} onOk={remove} okText="Remove" okButtonProps={{ danger: true }}>
      <Typography.Paragraph>Remove all native limits for <Typography.Text code>{selectedTag}</Typography.Text>?</Typography.Paragraph>
    </Modal>
  </Card>;
}
