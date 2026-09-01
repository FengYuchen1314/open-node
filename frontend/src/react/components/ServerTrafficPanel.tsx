import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Alert, Button, Card, Col, Descriptions, Form, Modal, Progress, Row, Select, Space, Typography } from "antd";
import { ReloadOutlined, SaveOutlined } from "@ant-design/icons";
import type { ServerSummary, ServerTraffic, TrafficSource, TrafficStatsMode } from "../../domain/inventory";
import { getServerTraffic, resetServerTraffic, updateServerTraffic } from "../../services/inventory";
import StrictInputNumber from "./StrictInputNumber";
import { zhMessage } from "../../i18n/zh-CN";

export interface ServerTrafficPanelProps { servers: ServerSummary[] }
type Action = "read" | "save" | "reset";
const sources = [{ label: "Xray 节点", value: "xray" }, { label: "系统网络", value: "system" }];
const modes = [{ label: "上传 + 下载", value: "both" }, { label: "上传", value: "upload" },
  { label: "下载", value: "download" }, { label: "取较大方向", value: "max" }];
const days = [{ label: "关闭", value: 0 }, ...Array.from({ length: 31 }, (_, i) => ({ label: String(i + 1), value: i + 1 }))];
function bytes(value: number) {
  if (value < 1024) return `${value} B`;
  const unit = Math.min(4, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** unit).toFixed(2)} ${["B", "KiB", "MiB", "GiB", "TiB"][unit]}`;
}
function date(value: string | null) {
  return value && Number.isFinite(Date.parse(value))
    ? `${new Date(value).toLocaleString("zh-CN", { timeZone: "UTC", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false })} UTC` : "无";
}

export default function ServerTrafficPanel({ servers }: ServerTrafficPanelProps) {
  const [selected, setSelected] = useState("");
  const [state, setState] = useState<ServerTraffic | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmation, setConfirmation] = useState(false);
  const [form, setForm] = useState({ limit: 0 as number | null, day: 0,
    source: "xray" as TrafficSource, mode: "both" as TrafficStatsMode });
  const model = useRef({ version: 0, selected: "", disposed: false, busy: false,
    state: null as ServerTraffic | null, timer: undefined as ReturnType<typeof setTimeout> | undefined });
  const limitBytes = Math.round((form.limit ?? Number.NaN) * 1024 ** 3);
  const valid = form.limit !== null && Number.isFinite(form.limit) && form.limit >= 0
    && Number.isSafeInteger(limitBytes) && (form.limit === 0 || limitBytes > 0);
  const federated = servers.find(server => server.id === selected)?.is_federated === true;
  const quota = state?.traffic_limit ? Math.min(100, state.used / state.traffic_limit * 100) : 0;
  const requestRef = useRef<(action: Action, replaceForm?: boolean) => Promise<void>>(async () => {});
  async function request(action: Action, replaceForm = false) {
    if (model.current.disposed || !selected || model.current.selected !== selected
      || (action !== "read" && (model.current.busy || federated)) || (action === "save" && !valid)) return;
    const id = selected;
    const run = ++model.current.version;
    clearTimeout(model.current.timer);
    model.current.timer = undefined;
    model.current.busy = true;
    setBusy(true);
    setError("");
    if (action !== "read") setConfirmation(false);
    try {
      const response = await (action === "save" ? updateServerTraffic(id, {
        traffic_limit: limitBytes, traffic_reset_day: form.day,
        traffic_source: form.source, traffic_stats_mode: form.mode,
      }) : action === "reset" ? resetServerTraffic(id) : getServerTraffic(id));
      if (model.current.disposed || run !== model.current.version) return;
      if (replaceForm || !model.current.state) setForm({ limit: response.traffic_limit / 1024 ** 3,
        day: response.traffic_reset_day, source: response.traffic_source, mode: response.traffic_stats_mode });
      model.current.state = response;
      setState(response);
    } catch (failure) {
      if (!model.current.disposed && run === model.current.version) {
        setError(failure instanceof Error ? failure.message : "服务器流量请求失败");
      }
    } finally {
      if (!model.current.disposed && run === model.current.version) {
        model.current.busy = false;
        setBusy(false);
        model.current.timer = setTimeout(() => void requestRef.current("read"), 10000);
      }
    }
  }
  useLayoutEffect(() => { requestRef.current = request; });
  const serverIds = servers.map(server => server.id).join("\0");
  useEffect(() => {
    setSelected(previous => servers.some(server => server.id === previous) ? previous : servers[0]?.id ?? "");
  }, [serverIds]);
  useLayoutEffect(() => {
    model.current.disposed = false;
    model.current.selected = selected;
    model.current.version += 1;
    model.current.state = null;
    model.current.busy = false;
    clearTimeout(model.current.timer);
    setState(null);
    setBusy(false);
    setError("");
    setConfirmation(false);
    void requestRef.current("read", true);
    return () => {
      model.current.disposed = true;
      model.current.version += 1;
      clearTimeout(model.current.timer);
    };
  }, [selected]);

  return <section aria-label="服务器流量">
    <Card title="服务器流量" extra={<Button type="text" icon={<ReloadOutlined />}
      aria-label="刷新服务器流量" title="刷新流量和设置" disabled={busy || !selected}
      onClick={() => void request("read", true)} />}>
      <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
        <Form.Item label="流量服务器" style={{ marginBottom: 0 }}>
          <Select aria-label="流量服务器" value={selected || undefined} disabled={busy || !servers.length}
            options={servers.map(server => ({ label: `${server.name}${server.is_federated ? "（分享）" : ""}`, value: server.id }))} onChange={setSelected} />
        </Form.Item>
        {error && <Alert type="error" showIcon title={zhMessage(error)} />}
        {federated && <Alert type="info" showIcon title="分享服务器流量由拥有方统计" description="这里展示最近一次同步的配额和用量；修改与重置只能由拥有方执行。" />}
        {state && <>
          <Space wrap aria-live="polite">
            <Typography.Text strong data-testid="server-traffic-used">{bytes(state.used)}</Typography.Text>
            <Typography.Text>/ {state.traffic_limit ? bytes(state.traffic_limit) : "不限额"}</Typography.Text>
            <TaglessSource source={state.traffic_source} />
          </Space>
          <Progress percent={quota} status={quota >= 100 ? "exception" : "normal"} showInfo={false} />
          <Descriptions column={1} size="small" items={[
            { key: "traffic", label: "上传 / 下载", children: `${bytes(state.upload)} / ${bytes(state.download)}` },
            { key: "report", label: "最近上报", children: date(state.last_reported_at) },
            { key: "reset", label: "上次重置", children: date(state.last_reset_at) },
            { key: "next", label: "下次重置", children: date(state.next_reset_at) },
          ]} />
          <Form layout="vertical" onFinish={() => void request("save", true)}>
            <Row gutter={16}>
              <Col xs={24} sm={12}><Form.Item label="流量来源"><Select aria-label="流量来源"
                value={form.source} options={sources} disabled={busy || federated} onChange={source => setForm(previous => ({ ...previous, source }))} /></Form.Item></Col>
              <Col xs={24} sm={12}><Form.Item label="统计方向"><Select aria-label="统计方向"
                value={form.mode} options={modes} disabled={busy || federated} onChange={mode => setForm(previous => ({ ...previous, mode }))} /></Form.Item></Col>
              <Col xs={24} sm={12}><Form.Item label="流量限额（GiB，0 表示不限额）" validateStatus={valid ? undefined : "error"}
                help={valid ? undefined : "输入 0 表示不限额；正数限额至少为 1 字节，且须在安全整数范围内。"}>
                <StrictInputNumber aria-label="流量限额（GiB，0 表示不限额）" value={form.limit} aria-valuemin={0}
                  aria-valuemax={Number.MAX_SAFE_INTEGER / 1024 ** 3} aria-invalid={!valid} disabled={busy || federated}
                  style={{ width: "100%" }}
                  onChange={limit => setForm(previous => ({ ...previous, limit }))} /></Form.Item></Col>
              <Col xs={24} sm={12}><Form.Item label="每月重置日（UTC）"><Select aria-label="每月重置日（UTC）"
                value={form.day} options={days} disabled={busy || federated} onChange={day => setForm(previous => ({ ...previous, day }))} /></Form.Item></Col>
            </Row>
            <Space wrap><Button type="primary" htmlType="submit" aria-label="保存" icon={<SaveOutlined aria-hidden />} disabled={busy || federated || !valid}>保存</Button>
              <Button aria-label="重置周期" disabled={busy || federated} onClick={() => setConfirmation(true)}>重置周期</Button></Space>
          </Form>
        </>}
      </Space>
    </Card>
    <Modal title="重置服务器流量？" open={confirmation} destroyOnHidden onCancel={() => setConfirmation(false)}
      okText="重置" okButtonProps={{ danger: true, disabled: busy, "aria-label": "重置" }} onOk={() => void request("reset")}>
      将把 {servers.find(server => server.id === selected)?.name} 当前流量周期的两种来源用量均从零重新计数。
      {" "}历史计数和用户流量限额保持不变。
    </Modal>
  </section>;
}

function TaglessSource({ source }: { source: TrafficSource }) {
  return <Typography.Text type="secondary">{source === "system" ? "系统网络" : "Xray 节点"}</Typography.Text>;
}
