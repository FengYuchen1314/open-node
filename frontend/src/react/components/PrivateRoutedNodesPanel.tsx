import { zhMessage, zhStatus } from "../../i18n/zh-CN";
import { ArrowRightOutlined, DeleteOutlined, ReloadOutlined } from "../../ui/icons";
import { Alert, Button, Card, Flex, Form, Input, Select, Space, Table, Tag, Typography } from "../../ui";
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
      if (readScope.isCurrent(current)) setError(failure instanceof Error ? failure.message : "无法读取私有路由");
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
      if (operationScope.isCurrent(current)) setError(failure instanceof Error ? failure.message : "创建私有路由失败");
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
      if (operationScope.isCurrent(current)) setError(failure instanceof Error ? failure.message : "删除私有路由失败");
    } finally { if (operationScope.isCurrent(current)) { setBusy(""); busyRef.current = false; } }
  }
  return <section aria-label="私有路由"><Card title="路由出口" extra={<Flex align="center" gap="small" wrap>
    {state && <><Tag>{state.used_nodes}/{state.policy.max_nodes}</Tag><Tag>今日 {state.actions_today}/{state.policy.daily_limit} 次</Tag></>}
    <Button aria-label="刷新私有路由" icon={<ReloadOutlined aria-hidden />} loading={loading} onClick={() => void load()} />
  </Flex>}><Space orientation="vertical" size="middle" style={{ width: "100%" }}>
    {error && <Alert type="error" showIcon title={zhMessage(error)} />}
    {state && !state.policy.enabled && <Alert type="info" showIcon title="私有路由已停用。" />}
    {state?.policy.enabled && <Form layout="vertical" onFinish={() => void create()}>
      <Flex gap="middle" wrap align="end">
        <Form.Item label="标签" htmlFor="private-route-label" help="2–32 个英文字母、数字或连字符"><Input id="private-route-label" value={form.label} onChange={event => setForm(previous => ({ ...previous, label: event.target.value }))} maxLength={32} disabled={Boolean(busy)} /></Form.Item>
        <Form.Item label="入口节点" htmlFor="private-route-parent" style={{ minWidth: 200, flex: 1 }}><Select id="private-route-parent" value={form.parent_id || undefined} options={parentOptions} disabled={Boolean(busy)} onChange={value => setForm(previous => ({ ...previous, parent_id: value, target_node_id: previous.target_node_id === value ? "" : previous.target_node_id }))} /></Form.Item>
        <Form.Item label="出口节点" htmlFor="private-route-target" style={{ minWidth: 200, flex: 1 }}><Select id="private-route-target" value={form.target_node_id || undefined} options={targetOptions} disabled={Boolean(busy)} onChange={value => setForm(previous => ({ ...previous, target_node_id: value }))} /></Form.Item>
        <Form.Item><Button type="primary" htmlType="submit" aria-label="创建" disabled={!canCreate} loading={busy === "create"}>创建</Button></Form.Item>
      </Flex>
    </Form>}
    <Table rowKey="id" loading={loading} dataSource={state?.nodes ?? []} pagination={false} scroll={{ x: 560 }} locale={{ emptyText: "暂无私有路由。" }} columns={[
      { title: "路由", render: (_, node) => <><Typography.Text strong>{node.name}</Typography.Text><div><Space wrap>{node.parent_name}<ArrowRightOutlined aria-hidden />{node.target_name}</Space></div>{node.last_error && <Typography.Text type="danger">{zhMessage(node.last_error)}</Typography.Text>}</> },
      { title: "状态", dataIndex: "status", width: 120, render: status => <Tag color={status === "active" ? "success" : status === "failed" ? "error" : "warning"}>{zhStatus(status)}</Tag> },
      { title: "操作", width: 190, render: (_, node) => !["provisioning", "removing"].includes(node.status) && <Space wrap>
        {confirming === node.id ? <><Button danger aria-label="确认" loading={busy === node.id} disabled={Boolean(busy) && busy !== node.id} onClick={() => void remove(node)}>确认</Button><Button aria-label="取消删除私有路由" disabled={Boolean(busy)} onClick={() => setConfirming("")}>取消</Button></> : <Button danger icon={<DeleteOutlined aria-hidden />} aria-label={`删除私有路由 ${node.name}`} disabled={Boolean(busy)} onClick={() => void remove(node)} />}
      </Space> },
    ]} />
  </Space></Card></section>;
}
