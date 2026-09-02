import { CloudDownloadOutlined, ReloadOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Checkbox, Descriptions, Popconfirm, Space, Tag, Typography } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ApplicationUpdateState } from "../../domain/application-updates";
import { authState, type OperatorSession } from "../../services/auth";
import { ApplicationUpdateRequestError, applyApplicationUpdate, checkApplicationUpdate, getApplicationUpdate } from "../../services/application-updates";
import { useAsyncScope } from "../hooks/useAsyncScope";

const terminal = new Set(["unavailable", "idle", "current", "available", "succeeded", "failed", "recovery_required"]);
const colors: Record<ApplicationUpdateState["status"], string> = {
  unavailable: "default", idle: "default", checking: "processing", current: "success",
  available: "warning", updating: "processing", succeeded: "success", failed: "error",
  recovery_required: "error",
};
const labels: Record<ApplicationUpdateState["status"], string> = {
  unavailable: "不可用", idle: "待检查", checking: "检查中", current: "已是最新",
  available: "有可用更新", updating: "更新中", succeeded: "更新完成", failed: "更新失败",
  recovery_required: "需要人工恢复",
};
const short = (value: string | null) => value === null ? "尚未检查" : value === "unknown" ? "未知" : value.slice(0, 12);
const displayTime = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
type PendingFlow = "observe" | "check" | "apply" | "one-click-check" | "one-click-apply";
interface PendingUpdate { requestId: string | null; flow: PendingFlow }

export default function ApplicationUpdatePanel({ operator }: { operator: OperatorSession }) {
  const scope = useAsyncScope();
  const busyRef = useRef(false), pendingRequest = useRef<PendingUpdate | null>(null), timer = useRef<number | null>(null);
  const [state, setState] = useState<ApplicationUpdateState | null>(null);
  const [busy, setBusy] = useState(""), [confirmed, setConfirmed] = useState(false), [polling, setPolling] = useState(false);
  const [notice, setNotice] = useState<{ type: "error" | "warning" | "success"; text: string } | null>(null);
  const current = useCallback((request: number) => scope.isCurrent(request)
    && authState.session?.authenticated === true && authState.session.username === operator.username
    && authState.session.csrf_token === operator.csrf_token, [scope, operator]);
  const applyCheckedTarget = useCallback(async (target: string) => {
    if (busyRef.current) return;
    const request = scope.begin(); busyRef.current = true; setBusy("one-click");
    setNotice({ type: "success", text: "检查完成，正在提交已精确绑定的目标版本。" });
    try {
      const accepted = await applyApplicationUpdate(target);
      if (!current(request)) return;
      pendingRequest.current = { requestId: accepted.request_id, flow: "one-click-apply" };
      setPolling(true);
      setNotice({ type: "success", text: "更新请求已受理；宿主机正在备份并验证候选镜像，页面会持续读取结果。" });
    } catch (error) {
      if (!current(request)) return;
      pendingRequest.current = null; setPolling(false);
      setNotice({
        type: error instanceof ApplicationUpdateRequestError && !error.outcomeUnknown ? "error" : "warning",
        text: error instanceof ApplicationUpdateRequestError ? error.message : "未能确认更新操作结果，请重新读取状态；不会自动重复提交。",
      });
    } finally { if (current(request)) { busyRef.current = false; setBusy(""); } }
  }, [current, scope]);
  const read = useCallback(async (request: number, quiet = false) => {
    try {
      const value = await getApplicationUpdate();
      if (!current(request)) return;
      setState(value);
      if (value.status === "checking" || value.status === "updating") {
        const flow = pendingRequest.current?.flow ?? "observe";
        pendingRequest.current = { requestId: value.request_id, flow }; setPolling(true);
      } else if (pendingRequest.current && value.request_id === pendingRequest.current.requestId && terminal.has(value.status)) {
        const flow = pendingRequest.current.flow;
        pendingRequest.current = null; setPolling(false); setConfirmed(false);
        if (flow === "one-click-check" && value.status === "available" && value.latest_revision) {
          void applyCheckedTarget(value.latest_revision);
          return;
        }
        setNotice({ type: value.status === "succeeded" || value.status === "current" ? "success" : value.status === "available" ? "warning" : "error", text: value.message });
      }
    } catch (error) {
      if (current(request) && !quiet) setNotice({
        type: error instanceof ApplicationUpdateRequestError && !error.outcomeUnknown ? "error" : "warning",
        text: error instanceof ApplicationUpdateRequestError ? error.message : "无法读取应用更新状态，请稍后重试。",
      });
      if (current(request) && quiet) setNotice({ type: "warning", text: "服务可能正在重启，仍会继续读取更新状态。" });
    }
  }, [applyCheckedTarget, current]);
  const reload = useCallback(async () => {
    if (busyRef.current) return;
    const request = scope.begin(); busyRef.current = true; setBusy("read"); setNotice(null);
    try { await read(request); } finally { if (current(request)) { busyRef.current = false; setBusy(""); } }
  }, [current, read, scope]);
  useEffect(() => { busyRef.current = false; void reload(); return () => { scope.invalidate(); if (timer.current !== null) window.clearTimeout(timer.current); }; }, [reload, scope]);
  useEffect(() => {
    if (!polling) return;
    const tick = async () => {
      const request = scope.begin(); await read(request, true);
      if (current(request) && pendingRequest.current) timer.current = window.setTimeout(() => void tick(), 2000);
    };
    timer.current = window.setTimeout(() => void tick(), 800);
    return () => { if (timer.current !== null) window.clearTimeout(timer.current); timer.current = null; };
  }, [current, polling, read, scope]);
  async function enqueue(action: "check" | "apply" | "one-click") {
    if (busyRef.current || (action === "apply" && (!state?.latest_revision || !confirmed))) return;
    const request = scope.begin(); busyRef.current = true; setBusy(action); setNotice(null);
    try {
      const accepted = action === "apply" ? await applyApplicationUpdate(state!.latest_revision!) : await checkApplicationUpdate();
      if (!current(request)) return;
      pendingRequest.current = {
        requestId: accepted.request_id,
        flow: action === "one-click" ? "one-click-check" : action,
      };
      setPolling(true);
      setNotice({ type: "success", text: action === "check" ? "检查请求已由宿主机助手受理。" : action === "one-click" ? "一键更新已开始：正在重新检查官方目标提交。" : "更新请求已受理；服务将在备份和候选镜像验证期间短暂重启。" });
    } catch (error) {
      if (!current(request)) return;
      setNotice({ type: error instanceof ApplicationUpdateRequestError && !error.outcomeUnknown ? "error" : "warning",
        text: error instanceof ApplicationUpdateRequestError ? error.message : "未能确认更新操作结果，请重新读取状态；不会自动重复提交。" });
    } finally { if (current(request)) { busyRef.current = false; setBusy(""); } }
  }
  const canApply = state?.managed === true && state.status === "available" && state.has_update === true && Boolean(state.latest_revision) && confirmed && !busy;
  const canOneClick = state?.managed === true && !["unavailable", "checking", "updating", "recovery_required"].includes(state.status) && !busy && !polling;
  const oneClickPending = polling && ["one-click-check", "one-click-apply"].includes(pendingRequest.current?.flow ?? "");
  return <Card title="应用更新" className="branding-settings-card">
    <Alert type="info" showIcon icon={<SafetyCertificateOutlined />} title="更新由宿主机固定功能助手执行；Web 容器没有 Docker socket 或宿主机配置目录权限。更新继续执行 fast-forward 校验、停机备份、候选镜像健康检查和恢复标记。" />
    {notice && <Alert className="form-alert" type={notice.type} showIcon title={notice.text} role="alert" />}
    {state && <>
      <Descriptions className="form-alert" size="small" column={{ xs: 1, sm: 2 }} bordered>
        <Descriptions.Item label="状态"><Tag color={colors[state.status]}>{labels[state.status]}</Tag></Descriptions.Item>
        <Descriptions.Item label="当前提交"><Typography.Text code copyable={state.current_revision !== "unknown"}>{short(state.current_revision)}</Typography.Text></Descriptions.Item>
        <Descriptions.Item label="目标提交">{state.release_url ? <Typography.Link href={state.release_url} target="_blank" rel="noreferrer">{short(state.latest_revision)}</Typography.Link> : short(state.latest_revision)}</Descriptions.Item>
        <Descriptions.Item label="最近检查">{displayTime(state.checked_at)}</Descriptions.Item>
      </Descriptions>
      <Typography.Paragraph type={state.status === "failed" || state.status === "recovery_required" ? "danger" : "secondary"}>{state.message}</Typography.Paragraph>
    </>}
    <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      <Space wrap>
        <Popconfirm title="执行一键更新？" description="系统会先重新检查官方 main，再把精确提交交给宿主机备份、构建和健康检查。若已是最新版本则不会重装。" okText="开始一键更新" cancelText="取消" disabled={!canOneClick} onConfirm={() => void enqueue("one-click")}>
          <Button type="primary" icon={<CloudDownloadOutlined />} aria-label="一键更新应用" loading={busy === "one-click" || oneClickPending} disabled={!canOneClick}>一键更新</Button>
        </Popconfirm>
        <Button icon={<ReloadOutlined />} aria-label="检查应用更新" loading={busy === "check"} disabled={Boolean(busy) || polling || state?.managed === false} onClick={() => void enqueue("check")}>检查更新</Button>
        <Button icon={<ReloadOutlined />} aria-label="重新读取更新状态" loading={busy === "read"} disabled={Boolean(busy)} onClick={() => void reload()}>重新读取状态</Button>
      </Space>
      <Typography.Text type="secondary">“一键更新”会重新检查目标提交，并只在目标仍完全一致时自动进入安装器更新事务。</Typography.Text>
      {state?.status === "available" && <>
        <Checkbox checked={confirmed} disabled={Boolean(busy) || polling} onChange={event => setConfirmed(event.target.checked)}>我已确认目标提交，并接受更新期间的短暂中断</Checkbox>
        <Popconfirm title="执行应用更新？" description="宿主机会先备份数据，再构建并验证候选镜像。" okText="开始更新" cancelText="取消" disabled={!canApply} onConfirm={() => void enqueue("apply")}>
          <Button type="primary" icon={<CloudDownloadOutlined />} aria-label="立即更新应用" loading={busy === "apply"} disabled={!canApply}>立即更新</Button>
        </Popconfirm>
      </>}
      {state?.status === "recovery_required" && <Alert type="error" showIcon title="请勿直接重启旧镜像。先在宿主机运行 install.sh status，按恢复标记隔离恢复升级前备份。" />}
      {state?.managed === false && <Typography.Text type="secondary">可在宿主机运行 <Typography.Text code>sudo bash /opt/open-node/install.sh update</Typography.Text>；手工 Compose、非官方仓库或非 systemd 部署不会启用网页更新。</Typography.Text>}
    </Space>
  </Card>;
}
