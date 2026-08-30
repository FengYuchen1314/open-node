import { ArrowRightOutlined, DeleteOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Flex, Form, Input, Select, Space, Table, Tag, Typography } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import type { PrivateRoutedNode, PrivateRoutedNodesResponse } from "../../domain/private-routed-nodes";
import { createSubscriberPrivateRoute, deleteSubscriberPrivateRoute, listSubscriberPrivateRoutes } from "../../services/private-routed-nodes";
import { useAsyncScope } from "../hooks/useAsyncScope";

export default function PrivateRoutedNodesPanel() {
  const readScope = useAsyncScope();
  const operationScope = useAsyncScope();
  const busyRef = useRef(false);
  const [state, setState] = useState<PrivateRoutedNodesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState("");
  const [confirming, setConfirming] = useState("");
  const [error, setError] = useState("");
  const [form, setForm] = useState({ label: "", parent_id: "", target_node_id: "" });
  const parentOptions = (state?.candidates ?? []).filter(item => item.can_parent).map(item => ({ label: item.name, value: item.id }));
  const targetOptions = (state?.candidates ?? []).filter(item => item.can_target && item.id !== form.parent_id).map(item => ({ label: item.name, value: item.id }));
  const canCreate = Boolean(state?.policy.enabled && state.used_nodes < state.policy.max_nodes && state.actions_today < state.policy.daily_limit && /^[A-Za-z0-9-]{2,32}$/.test(form.label.trim()) && form.parent_id && form.target_node_id && form.parent_id !== form.target_node_id && !busy);
  const hasPending = state?.nodes.some(item => ["provisioning", "removing"].includes(item.status)) ?? false;

  const load = useCallback(async (silent = false) => {
    const current = readScope.begin(); if (!silent) setLoading(true);
    try {
      const result = await listSubscriberPrivateRoutes();
      if (!readScope.isCurrent(current)) return;
      setState(result); setError("");
      setForm(previous => ({ ...previous,
        parent_id: result.candidates.some(item => item.can_parent && item.id === previous.parent_id) ? previous.parent_id : "",
        target_node_id: result.candidates.some(item => item.can_target && item.id === previous.target_node_id && item.id !== previous.parent_id) ? previous.target_node_id : "",
      }));
    } catch (failure) {
      if (readScope.isCurrent(current)) setError(failure instanceof Error ? failure.message : "Private routes unavailable");
    } finally { if (readScope.isCurrent(current)) setLoading(false); }
  }, [readScope]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!hasPending) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      await load(true);
      if (!disposed) timer = setTimeout(() => void poll(), 2000);
    };
    timer = setTimeout(() => void poll(), 2000);
    return () => { disposed = true; clearTimeout(timer); };
  }, [hasPending, load]);
  async function create() {
    if (!canCreate || busyRef.current) return;
    const current = operationScope.begin(); busyRef.current = true; setBusy("create"); setError("");
    try {
      await createSubscriberPrivateRoute({ ...form, label: form.label.trim() });
      if (!operationScope.isCurrent(current)) return;
      setForm({ label: "", parent_id: "", target_node_id: "" }); await load(true);
    } catch (failure) {
      if (operationScope.isCurrent(current)) setError(failure instanceof Error ? failure.message : "Private route creation failed");
    } finally { if (operationScope.isCurrent(current)) { setBusy(""); busyRef.current = false; } }
  }
  async function remove(node: PrivateRoutedNode) {
    if (busyRef.current) return;
    if (confirming !== node.id) { setConfirming(node.id); return; }
    const current = operationScope.begin(); busyRef.current = true; setBusy(node.id); setError("");
    try {
      await deleteSubscriberPrivateRoute(node.id);
      if (!operationScope.isCurrent(current)) return;
      setConfirming(""); await load(true);
    } catch (failure) {
      if (operationScope.isCurrent(current)) setError(failure instanceof Error ? failure.message : "Private route deletion failed");
    } finally { if (operationScope.isCurrent(current)) { setBusy(""); busyRef.current = false; } }
  }
  return <section aria-label="Private routes"><Card title="Routed exits" extra={<Flex align="center" gap="small" wrap>
    {state && <><Tag>{state.used_nodes}/{state.policy.max_nodes}</Tag><Tag>{state.actions_today}/{state.policy.daily_limit} today</Tag></>}
    <Button aria-label="Refresh private routes" icon={<ReloadOutlined aria-hidden />} loading={loading} onClick={() => void load()} />
  </Flex>}><Space orientation="vertical" size="middle" style={{ width: "100%" }}>
    {error && <Alert type="error" showIcon title={error} />}
    {state && !state.policy.enabled && <Alert type="info" showIcon title="Private routes are disabled." />}
    {state?.policy.enabled && <Form layout="vertical" onFinish={() => void create()}>
      <Flex gap="middle" wrap align="end">
        <Form.Item label="Label" htmlFor="private-route-label" help="2–32 letters, digits or hyphens"><Input id="private-route-label" value={form.label} onChange={event => setForm(previous => ({ ...previous, label: event.target.value }))} maxLength={32} disabled={Boolean(busy)} /></Form.Item>
        <Form.Item label="Entry node" htmlFor="private-route-parent" style={{ minWidth: 200, flex: 1 }}><Select id="private-route-parent" value={form.parent_id || undefined} options={parentOptions} disabled={Boolean(busy)} onChange={value => setForm(previous => ({ ...previous, parent_id: value, target_node_id: previous.target_node_id === value ? "" : previous.target_node_id }))} /></Form.Item>
        <Form.Item label="Exit node" htmlFor="private-route-target" style={{ minWidth: 200, flex: 1 }}><Select id="private-route-target" value={form.target_node_id || undefined} options={targetOptions} disabled={Boolean(busy)} onChange={value => setForm(previous => ({ ...previous, target_node_id: value }))} /></Form.Item>
        <Form.Item><Button type="primary" htmlType="submit" aria-label="Create" disabled={!canCreate} loading={busy === "create"}>Create</Button></Form.Item>
      </Flex>
    </Form>}
    <Table rowKey="id" loading={loading} dataSource={state?.nodes ?? []} pagination={false} scroll={{ x: 560 }} locale={{ emptyText: "No private routes." }} columns={[
      { title: "Route", render: (_, node) => <><Typography.Text strong>{node.name}</Typography.Text><div><Space wrap>{node.parent_name}<ArrowRightOutlined aria-hidden />{node.target_name}</Space></div>{node.last_error && <Typography.Text type="danger">{node.last_error}</Typography.Text>}</> },
      { title: "Status", dataIndex: "status", width: 120, render: status => <Tag color={status === "active" ? "success" : status === "failed" ? "error" : "warning"}>{status}</Tag> },
      { title: "Action", width: 190, render: (_, node) => !["provisioning", "removing"].includes(node.status) && <Space wrap>
        {confirming === node.id ? <><Button danger aria-label="Confirm" loading={busy === node.id} disabled={Boolean(busy) && busy !== node.id} onClick={() => void remove(node)}>Confirm</Button><Button aria-label="Cancel private route deletion" disabled={Boolean(busy)} onClick={() => setConfirming("")}>Cancel</Button></> : <Button danger icon={<DeleteOutlined aria-hidden />} aria-label={`Delete private route ${node.name}`} disabled={Boolean(busy)} onClick={() => void remove(node)} />}
      </Space> },
    ]} />
  </Space></Card></section>;
}
