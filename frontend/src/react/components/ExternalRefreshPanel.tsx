import { Alert, Button, Card, Checkbox, Descriptions, Flex, Form, InputNumber, Modal, Select, Switch, Tag } from "../../ui";
import { useRef, useState } from "react";
import type { ExternalRefreshCode, ExternalSourceDetail, ExternalSourceRead } from "../../domain/external-subscriptions";
import { externalSubscriptionsErrorMessage, type ExternalSubscriptionsClient } from "../../services/external-subscriptions";
import { useAsyncScope } from "../hooks/useAsyncScope";

const messages: Record<ExternalRefreshCode, string> = {
  never: "尚未执行", refresh_succeeded: "刷新成功", fetch_failed: "抓取失败，保留上次节点",
  parse_failed: "来源内容无法解析，保留上次节点", credentials_unavailable: "密钥或凭据不可用，请检查原始密钥",
  source_changed: "来源已变化，本次结果未应用", worker_interrupted: "上次任务中断，稍后重新抓取",
  node_limit: "节点数量超出上限，本次结果未应用", refresh_failed: "刷新失败，保留上次节点",
  restore_paused: "备份恢复后已关闭，请重新确认设置",
};
const stamp = (value: string | null | undefined) => value ? new Date(value).toLocaleString("zh-CN") : "—";
interface Props {
  source: ExternalSourceRead;
  disabled: boolean;
  api: Pick<ExternalSubscriptionsClient, "updateExternalRefresh" | "getExternalSource">;
  onSaved: (source: ExternalSourceRead) => void;
  onRead: (detail: ExternalSourceDetail) => void;
}

export default function ExternalRefreshPanel(props: Props) {
  const [editing, setEditing] = useState(false);
  const value = props.source.refresh;
  if (!value) return null;
  return <Card size="small" title="定时刷新"><Flex vertical gap="small">
    <Descriptions column={1} size="small" items={[
      { key: "enabled", label: "自动刷新", children: <Tag color={value.enabled ? "processing" : "default"}>{!value.enabled ? "已关闭" : value.paused ? "已暂停" : value.running ? "正在抓取" : "已开启"}</Tag> },
      { key: "interval", label: "刷新间隔", children: `${value.interval_minutes} 分钟` },
      { key: "scope", label: "同步范围", children: value.scope === "all" ? "更新已保存节点，并自动加入新节点" : "只更新已保存节点" },
      { key: "next", label: "下次执行", children: stamp(value.next_run_at) },
      { key: "attempt", label: "上次开始", children: stamp(value.last_attempt_at) },
      { key: "finish", label: "上次结束", children: stamp(value.last_finished_at) },
      { key: "success", label: "最近成功", children: stamp(value.last_success_at) },
      { key: "result", label: "上次结果", children: messages[value.code] },
      { key: "counts", label: "上次变更", children: `新增 ${value.imported_count}，更新 ${value.updated_count}，缺失 ${value.missing_count}，待手动导入 ${value.new_available_count}` },
    ]} />
    {value.enabled && value.paused && <Alert type="warning" showIcon title="来源或所属账户已停用，暂不抓取上游。" />}
    {value.consecutive_failures > 0 && <Alert type="warning" showIcon title={`连续失败 ${value.consecutive_failures} 次，已延长重试间隔。`} />}
    <Button aria-label="配置外部订阅定时刷新" disabled={props.disabled} onClick={() => setEditing(true)}>配置定时刷新</Button>
    {editing && <RefreshEditor {...props} onClose={() => setEditing(false)} />}
  </Flex></Card>;
}

function RefreshEditor({ source, disabled, api, onSaved, onRead, onClose }: Props & { onClose: () => void }) {
  const scope = useAsyncScope(), busyRef = useRef(false);
  const initial = source.refresh!;
  const [enabled, setEnabled] = useState(initial.enabled);
  const [minutes, setMinutes] = useState<number | null>(initial.interval_minutes);
  const [mode, setMode] = useState(initial.scope);
  const [accepted, setAccepted] = useState(false), [stale, setStale] = useState(false);
  const [revision, setRevision] = useState(source.revision);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const valid = !disabled && !busy && !stale && minutes !== null && Number.isInteger(minutes) && minutes >= 15 && minutes <= 10080 && (!enabled || accepted);
  function close() { scope.invalidate(); onClose(); }
  async function save() {
    if (!valid || busyRef.current || minutes === null) return;
    const run = scope.begin(); busyRef.current = true; setBusy(true); setError("");
    try {
      const result = await api.updateExternalRefresh(source.id, {
        expected_revision: revision, enabled, interval_minutes: minutes, scope: mode, accept_changes: accepted,
      });
      if (scope.isCurrent(run)) { onSaved(result); onClose(); }
    } catch (failure) {
      if (scope.isCurrent(run)) { setError(externalSubscriptionsErrorMessage(failure)); setStale(true); setAccepted(false); }
    } finally { if (scope.isCurrent(run)) { busyRef.current = false; setBusy(false); } }
  }
  async function reload() {
    if (busyRef.current) return;
    const run = scope.begin(); busyRef.current = true; setBusy(true);
    try {
      const result = await api.getExternalSource(source.id);
      if (!scope.isCurrent(run)) return;
      if (!result.source.refresh) { setError("当前服务不支持定时刷新，请先升级后端。"); return; }
      const current = result.source.refresh!;
      setEnabled(current.enabled); setMinutes(current.interval_minutes); setMode(current.scope);
      setRevision(result.source.revision); setAccepted(false); setStale(false); setError(""); onRead(result);
    } catch (failure) { if (scope.isCurrent(run)) setError(externalSubscriptionsErrorMessage(failure)); }
    finally { if (scope.isCurrent(run)) { busyRef.current = false; setBusy(false); } }
  }
  return <Modal open title="配置定时刷新" onCancel={close} footer={null} width={580} style={{ maxWidth: "calc(100vw - 24px)" }}>
    <Form layout="vertical" onFinish={() => void save()}>
      <Flex vertical gap="middle">
        <Alert type="info" showIcon title="开启后将按计划访问上游链接" description="保存设置不会立即抓取，首次执行在一个间隔后。刷新会更新节点凭据、标记缺失节点，并保留本地改名和停用状态。失败时保留上次结果，连续失败会延长重试间隔。" />
        <Form.Item label="开启定时刷新"><Switch aria-label="开启外部订阅定时刷新" checked={enabled} disabled={busy || stale || disabled} onChange={v => { setEnabled(v); setAccepted(false); }} /></Form.Item>
        <Form.Item label="刷新间隔（分钟）"><InputNumber aria-label="外部订阅刷新间隔" min={15} max={10080} precision={0} value={minutes} disabled={busy || stale || disabled} onChange={setMinutes} /></Form.Item>
        <Form.Item label="同步范围"><Select aria-label="外部订阅自动同步范围" value={mode} disabled={busy || stale || disabled} onChange={v => { setMode(v); setAccepted(false); }} options={[
          { value: "saved_only", label: "只更新已保存节点" }, { value: "all", label: "更新已保存节点，并自动加入新节点" },
        ]} /></Form.Item>
        {mode === "all" && <Alert type="warning" showIcon title="新发现的可用节点会自动加入此用户的主订阅，无需逐个确认。" />}
        {enabled && <Checkbox aria-label="确认外部订阅自动变更" checked={accepted} disabled={busy || stale || disabled} onChange={e => setAccepted(e.target.checked)}>同意按所选范围自动更新外部节点和凭据</Checkbox>}
        <Alert type="info" title="更换来源链接会关闭定时刷新，需重新确认。关闭后，已发出的抓取可能仍会结束，但结果不会应用。" />
        {error && <Alert type="error" showIcon title={error} />}
        {stale && <Button aria-label="重新读取定时刷新设置" loading={busy} onClick={() => void reload()}>重新读取设置</Button>}
        <Flex gap="small"><Button type="primary" htmlType="submit" aria-label="保存外部订阅定时刷新" disabled={!valid} loading={busy}>保存设置</Button><Button onClick={close}>取消</Button></Flex>
      </Flex>
    </Form>
  </Modal>;
}
