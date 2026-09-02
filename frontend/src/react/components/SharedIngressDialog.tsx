import { ReloadOutlined, SaveOutlined, StopOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Checkbox,
  Col,
  Form,
  Input,
  Modal,
  Row,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Typography,
} from "antd";
import { useLayoutEffect, useMemo, useRef, useState } from "react";

import type { AgentCommand } from "../../domain/inventory";
import type {
  SharedIngressConfiguration,
  SharedIngressRoute,
  SharedIngressState,
  SharedIngressWebsite,
  SharedIngressWebsiteDraft,
} from "../../domain/shared-ingress";
import {
  sharedIngressCommandLabel,
  sharedIngressConfiguration,
  sharedIngressWebsiteDraft,
  normalizeSharedIngressSni,
  validateSharedIngressDraft,
} from "../../domain/shared-ingress";
import {
  applySharedIngress,
  disableSharedIngress,
  getSharedIngress,
  sharedIngressErrorMessage,
} from "../../services/shared-ingress";

export interface SharedIngressDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  serverId: string;
}

const profileLabels = {
  "vless-reality-vision": "VLESS Reality Vision",
  "vless-xhttp-reality-xmux": "VLESS XHTTP Reality XMUX",
  "anytls-shadowtls": "AnyTLS ShadowTLS",
} as const;
const emptyWebsite = () => sharedIngressWebsiteDraft(null);
const commandColors = { waiting: "default", pending: "warning", leased: "processing", succeeded: "success", failed: "error", skipped: "error" } as const;
function sameWebsite(left: SharedIngressWebsite | null, right: SharedIngressWebsite | null) {
  return left === right || Boolean(left && right
    && left.sni === right.sni && left.upstream_url === right.upstream_url
    && left.tls_address === right.tls_address && left.tls_port === right.tls_port
    && left.certificate_name === right.certificate_name && left.redirect_http === right.redirect_http);
}
function sameConfiguration(left: SharedIngressConfiguration | null, right: SharedIngressConfiguration | null) {
  return left === right || Boolean(left && right
    && left.listen_port === right.listen_port && left.listen_ipv6 === right.listen_ipv6
    && left.routes.length === right.routes.length
    && left.routes.every((route, index) => {
      const candidate = right.routes[index];
      return candidate && route.node_id === candidate.node_id && route.profile === candidate.profile
        && route.sni === candidate.sni && route.upstream_address === candidate.upstream_address
        && route.upstream_port === candidate.upstream_port;
    }) && sameWebsite(left.website, right.website));
}

export default function SharedIngressDialog({ open, onOpenChange, serverId }: SharedIngressDialogProps) {
  const generation = useRef(0);
  const busyRef = useRef(false);
  const [state, setState] = useState<SharedIngressState | null>(null);
  const [routes, setRoutes] = useState<SharedIngressRoute[]>([]);
  const [website, setWebsite] = useState<SharedIngressWebsiteDraft>(emptyWebsite);
  const [command, setCommand] = useState<AgentCommand | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [disableOpen, setDisableOpen] = useState(false);
  const [disableAcknowledged, setDisableAcknowledged] = useState(false);
  const validation = useMemo(() => validateSharedIngressDraft(routes, website), [routes, website]);
  const candidate = useMemo(
    () => sharedIngressConfiguration(state?.configuration ?? null, routes, website),
    [routes, state?.configuration, website],
  );
  const dirty = Boolean(state && candidate && !sameConfiguration(candidate, state.configuration));

  function accept(value: SharedIngressState) {
    setState(value);
    setRoutes(value.configuration?.routes.map(route => ({ ...route })) ?? []);
    setWebsite(sharedIngressWebsiteDraft(value.configuration));
  }

  async function load() {
    const run = ++generation.current;
    setState(null); setRoutes([]); setWebsite(emptyWebsite()); setCommand(null);
    setError(""); setNotice(""); setDisableOpen(false); setDisableAcknowledged(false);
    if (!open || !serverId) { setBusy(""); busyRef.current = false; return; }
    setBusy("load"); busyRef.current = true;
    try {
      const value = await getSharedIngress(serverId);
      if (run === generation.current) accept(value);
    } catch (failure) { if (run === generation.current) setError(sharedIngressErrorMessage(failure)); }
    finally { if (run === generation.current) { setBusy(""); busyRef.current = false; } }
  }

  useLayoutEffect(() => {
    busyRef.current = false; void load();
    return () => { generation.current += 1; busyRef.current = false; };
  }, [open, serverId]);

  async function save() {
    if (!state || !candidate || validation.length || (!dirty && !state.configuration) || busyRef.current) return;
    const run = ++generation.current;
    setBusy("save"); busyRef.current = true; setError(""); setNotice(""); setCommand(null);
    try {
      const result = await applySharedIngress(serverId, {
        configuration: candidate, expected_revision: state.revision, command_timeout_ms: 60_000,
      });
      if (run !== generation.current) return;
      accept(result.state); setCommand(result.command);
      setNotice(["failed", "skipped"].includes(result.command.status)
        ? "声明已经保存，但 Agent 没有应用配置，请检查命令状态。"
        : result.command.status === "succeeded"
          ? "443 分流声明已保存，Agent 已应用配置。"
          : "443 分流声明已保存，Agent 命令已排队。要求最终成功后配置才会在服务器生效。");
    } catch (failure) { if (run === generation.current) setError(sharedIngressErrorMessage(failure)); }
    finally { if (run === generation.current) { setBusy(""); busyRef.current = false; } }
  }

  async function disable() {
    if (!state?.configuration || !disableAcknowledged || busyRef.current) return;
    const run = ++generation.current;
    setBusy("disable"); busyRef.current = true; setError(""); setNotice(""); setCommand(null);
    try {
      const result = await disableSharedIngress(serverId, { expected_revision: state.revision, command_timeout_ms: 60_000 });
      if (run !== generation.current) return;
      accept(result.state); setCommand(result.command); setDisableOpen(false); setDisableAcknowledged(false);
      setNotice(["failed", "skipped"].includes(result.command.status)
        ? "禁用声明已经保存，但 Agent 未完成释放 TCP 443，请检查命令状态。"
        : "禁用声明已保存，Agent 正在释放受管 TCP 443。");
    } catch (failure) { if (run === generation.current) setError(sharedIngressErrorMessage(failure)); }
    finally { if (run === generation.current) { setBusy(""); busyRef.current = false; } }
  }

  const columns = [
    { title: "协议配置", dataIndex: "profile", key: "profile", render: (profile: keyof typeof profileLabels) => profileLabels[profile] },
    { title: "唯一 SNI", dataIndex: "sni", key: "sni", render: (sni: string) => <Typography.Text code>{sni}</Typography.Text> },
    { title: "自动路由", key: "route", render: (_: unknown, route: SharedIngressRoute) => <Typography.Text code>{route.sni} → {route.upstream_address === "::1" ? `[::1]:${route.upstream_port}` : `127.0.0.1:${route.upstream_port}`}</Typography.Text> },
  ];

  return <>
    <Modal open={open} destroyOnHidden width={920} centered title={<Space wrap><span>443 分流与网站反向代理</span><Button type="text" icon={<ReloadOutlined />} aria-label="重新读取 443 分流配置" disabled={Boolean(busy)} onClick={() => void load()} /></Space>}
      mask={{ closable: !busy }} keyboard={!busy} closable={!busy} onCancel={() => { if (!busyRef.current) onOpenChange(false); }}
      styles={{ body: { maxHeight: "calc(100dvh - 180px)", overflowY: "auto" } }}
      footer={<Space wrap><Button disabled={Boolean(busy)} onClick={() => onOpenChange(false)}>关闭</Button><Button danger icon={<StopOutlined aria-hidden />} disabled={Boolean(busy) || !state?.configuration} onClick={() => { setDisableOpen(true); setDisableAcknowledged(false); }}>确认禁用</Button><Button type="primary" icon={<SaveOutlined aria-hidden />} loading={busy === "save"} disabled={Boolean(busy) || !state || !candidate || (!dirty && !state.configuration) || Boolean(validation.length)} onClick={() => void save()}>{dirty ? "保存并下发" : "重新下发"}</Button></Space>}>
      {open && <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
        {busy === "load" && <Spin aria-label="正在读取 443 分流配置" />}
        {error && <Alert type="error" showIcon title={error} role="alert" />}
        {notice && <Alert type={command && ["failed", "skipped"].includes(command.status) ? "warning" : "success"} showIcon title={notice} role="status" />}
        <Alert type="warning" showIcon title="受管分流会独占公网 TCP 443" description="同一服务器上的 Caddy、Nginx 或其他进程不能同时监听 443。保存只表示声明和命令已创建，仍需确认 Agent 命令最终成功。" />
        {state && <Space wrap><Tag color={state.configuration ? "success" : "default"}>{state.configuration ? "已声明占用 443" : "未启用"}</Tag><Tag>配置版本 {state.revision}</Tag>{state.updated_at && <Typography.Text type="secondary">更新于 {new Date(state.updated_at).toLocaleString("zh-CN")}</Typography.Text>}</Space>}
        {command && <Alert type={command.status === "succeeded" ? "success" : ["failed", "skipped"].includes(command.status) ? "error" : "info"} showIcon title={<Space wrap><span>Agent 命令状态</span><Tag color={commandColors[command.status]}>{sharedIngressCommandLabel(command.status)}</Tag></Space>} description={<span>命令 {command.id} · {command.method} /api/child/nginx/shared-ingress{command.result_status == null ? "" : ` · HTTP ${command.result_status}`}</span>} />}

        {state && <>
          <section aria-labelledby="shared-ingress-routes-title">
            <Typography.Title level={4} id="shared-ingress-routes-title">自动节点路由</Typography.Title>
            <Typography.Paragraph type="secondary">这里只显示由节点配置生成的 SNI → 本机高位运行端口映射，不能在此手工改变节点、协议或端口。仅支持以下三种配置。</Typography.Paragraph>
            <Space wrap style={{ marginBottom: 12 }}>{Object.values(profileLabels).map(label => <Tag key={label}>{label}</Tag>)}</Space>
            <Table size="small" rowKey="node_id" pagination={false} columns={columns} dataSource={routes} locale={{ emptyText: "当前没有自动节点路由；可单独启用网站反向代理。" }} scroll={{ x: 680 }} />
          </section>

          <section aria-labelledby="shared-ingress-website-title">
            <Space wrap><Typography.Title level={4} id="shared-ingress-website-title" style={{ margin: 0 }}>网站反向代理</Typography.Title><Switch aria-label="启用网站反向代理" checked={website.enabled} disabled={Boolean(busy)} onChange={enabled => setWebsite(previous => ({ ...previous, enabled, redirect_http: enabled && !previous.enabled ? true : previous.redirect_http }))} /><Button href="/certificates">管理可信证书</Button></Space>
            {website.enabled && <Alert style={{ marginTop: 12 }} type="info" showIcon title="只需填写网站域名和上游地址"
              description="证书名称自动使用网站域名，本机自动分配高位端口，并启用 HTTP → HTTPS 308 重定向。请先在证书管理中签发同名可信证书。" />}
            <Form layout="vertical" disabled={Boolean(busy) || !website.enabled} style={{ marginTop: 12 }}>
              <Row gutter={16}>
                <Col span={24}><Form.Item label="网站域名"><Input aria-label="网站域名" value={website.sni} maxLength={253} placeholder="site.example.com" onChange={event => setWebsite(previous => ({ ...previous, sni: event.target.value, certificate_name: normalizeSharedIngressSni(event.target.value) ?? event.target.value.trim().toLowerCase() }))} /></Form.Item></Col>
                <Col span={24}><Form.Item label="绝对 HTTP(S) 上游"><Input aria-label="绝对 HTTP(S) 上游" value={website.upstream_url} maxLength={2048} placeholder="https://origin.example.net/app" onChange={event => setWebsite(previous => ({ ...previous, upstream_url: event.target.value }))} /></Form.Item></Col>
              </Row>
            </Form>
          </section>
          {validation.length > 0 && <Alert type="warning" showIcon title="保存前需要修正" description={<ul style={{ marginBottom: 0 }}>{validation.map(message => <li key={message}>{message}</li>)}</ul>} aria-live="polite" />}
          {!validation.length && candidate && <Alert type="success" showIcon title="SNI、上游和本机端口检查通过。" />}
        </>}
      </Space>}
    </Modal>
    <Modal open={disableOpen} title="确认禁用受管 443 分流" okText="禁用并下发" cancelText="取消" onCancel={() => { if (!busyRef.current) { setDisableOpen(false); setDisableAcknowledged(false); } }} onOk={() => void disable()} okButtonProps={{ danger: true, disabled: !disableAcknowledged, loading: busy === "disable" }} cancelButtonProps={{ disabled: busy === "disable" }}>
      <Alert type="warning" showIcon title="现有节点与网站入口会中断" description="禁用后会删除受管分流声明，并命令 Agent 释放 TCP 443。必须确认 Agent 命令成功，端口才真正释放。" />
      <Checkbox style={{ marginTop: 16 }} checked={disableAcknowledged} disabled={busy === "disable"} onChange={event => setDisableAcknowledged(event.target.checked)}>我确认禁用此服务器的全部受管 443 入口</Checkbox>
    </Modal>
  </>;
}
