import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Alert, Button, Card, Col, Descriptions, Form, Modal, Progress, Row, Select, Space, Typography } from "antd";
import { ReloadOutlined, SaveOutlined } from "@ant-design/icons";
import type { ServerSummary, ServerTraffic, TrafficSource, TrafficStatsMode } from "../../domain/inventory";
import { getServerTraffic, resetServerTraffic, updateServerTraffic } from "../../services/inventory";
import StrictInputNumber from "./StrictInputNumber";

export interface ServerTrafficPanelProps { servers: ServerSummary[] }
type Action = "read" | "save" | "reset";
const sources = [{ label: "Xray nodes", value: "xray" }, { label: "System network", value: "system" }];
const modes = [{ label: "Upload + download", value: "both" }, { label: "Upload", value: "upload" },
  { label: "Download", value: "download" }, { label: "Larger direction", value: "max" }];
const days = [{ label: "Off", value: 0 }, ...Array.from({ length: 31 }, (_, i) => ({ label: String(i + 1), value: i + 1 }))];
function bytes(value: number) {
  if (value < 1024) return `${value} B`;
  const unit = Math.min(4, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** unit).toFixed(2)} ${["B", "KiB", "MiB", "GiB", "TiB"][unit]}`;
}
function date(value: string | null) {
  return value && Number.isFinite(Date.parse(value))
    ? `${new Date(value).toISOString().replace("T", " ").slice(0, 16)} UTC` : "None";
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
  const quota = state?.traffic_limit ? Math.min(100, state.used / state.traffic_limit * 100) : 0;
  const requestRef = useRef<(action: Action, replaceForm?: boolean) => Promise<void>>(async () => {});
  async function request(action: Action, replaceForm = false) {
    if (model.current.disposed || !selected || model.current.selected !== selected
      || (action !== "read" && model.current.busy) || (action === "save" && !valid)) return;
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
        setError(failure instanceof Error ? failure.message : "Server traffic request failed");
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

  return <section aria-label="Server traffic">
    <Card title="Server traffic" extra={<Button type="text" icon={<ReloadOutlined />}
      aria-label="Refresh server traffic" title="Refresh traffic and settings" disabled={busy || !selected}
      onClick={() => void request("read", true)} />}>
      <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
        <Form.Item label="Traffic server" style={{ marginBottom: 0 }}>
          <Select aria-label="Traffic server" value={selected || undefined} disabled={busy || !servers.length}
            options={servers.map(server => ({ label: server.name, value: server.id }))} onChange={setSelected} />
        </Form.Item>
        {error && <Alert type="error" showIcon title={error} />}
        {state && <>
          <Space wrap aria-live="polite">
            <Typography.Text strong data-testid="server-traffic-used">{bytes(state.used)}</Typography.Text>
            <Typography.Text>/ {state.traffic_limit ? bytes(state.traffic_limit) : "Unlimited"}</Typography.Text>
            <TaglessSource source={state.traffic_source} />
          </Space>
          <Progress percent={quota} status={quota >= 100 ? "exception" : "normal"} showInfo={false} />
          <Descriptions column={1} size="small" items={[
            { key: "traffic", label: "Upload / download", children: `${bytes(state.upload)} / ${bytes(state.download)}` },
            { key: "report", label: "Last report", children: date(state.last_reported_at) },
            { key: "reset", label: "Last reset", children: date(state.last_reset_at) },
            { key: "next", label: "Next reset", children: date(state.next_reset_at) },
          ]} />
          <Form layout="vertical" onFinish={() => void request("save", true)}>
            <Row gutter={16}>
              <Col xs={24} sm={12}><Form.Item label="Traffic source"><Select aria-label="Traffic source"
                value={form.source} options={sources} disabled={busy} onChange={source => setForm(previous => ({ ...previous, source }))} /></Form.Item></Col>
              <Col xs={24} sm={12}><Form.Item label="Counted direction"><Select aria-label="Counted direction"
                value={form.mode} options={modes} disabled={busy} onChange={mode => setForm(previous => ({ ...previous, mode }))} /></Form.Item></Col>
              <Col xs={24} sm={12}><Form.Item label="Quota (GiB, 0 = unlimited)" validateStatus={valid ? undefined : "error"}
                help={valid ? undefined : "Enter zero for unlimited, or a positive quota of at least one byte within the safe integer range."}>
                <StrictInputNumber aria-label="Quota (GiB, 0 = unlimited)" value={form.limit} aria-valuemin={0}
                  aria-valuemax={Number.MAX_SAFE_INTEGER / 1024 ** 3} aria-invalid={!valid} disabled={busy}
                  style={{ width: "100%" }}
                  onChange={limit => setForm(previous => ({ ...previous, limit }))} /></Form.Item></Col>
              <Col xs={24} sm={12}><Form.Item label="Monthly reset day (UTC)"><Select aria-label="Monthly reset day (UTC)"
                value={form.day} options={days} disabled={busy} onChange={day => setForm(previous => ({ ...previous, day }))} /></Form.Item></Col>
            </Row>
            <Space wrap><Button type="primary" htmlType="submit" aria-label="Save" icon={<SaveOutlined aria-hidden />} disabled={busy || !valid}>Save</Button>
              <Button disabled={busy} onClick={() => setConfirmation(true)}>Reset cycle</Button></Space>
          </Form>
        </>}
      </Space>
    </Card>
    <Modal title="Reset server traffic?" open={confirmation} destroyOnHidden onCancel={() => setConfirmation(false)}
      okText="Reset" okButtonProps={{ danger: true, disabled: busy, "aria-label": "Reset" }} onOk={() => void request("reset")}>
      The current traffic cycle for {servers.find(server => server.id === selected)?.name} will start at zero for both sources.
      {" "}Historical counters and user quotas stay unchanged.
    </Modal>
  </section>;
}

function TaglessSource({ source }: { source: TrafficSource }) {
  return <Typography.Text type="secondary">{source === "system" ? "System network" : "Xray nodes"}</Typography.Text>;
}
