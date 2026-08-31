import { Alert, Button, Card, Checkbox, Descriptions, Form, Input, Space, Typography } from "antd";
import { useRef, useState } from "react";
import type { RestoreStatus } from "../../domain/backups";
import { authState, type OperatorSession } from "../../services/auth";
import { BackupRequestError, backupErrorMessage, getRestoreStatus, reviewRestore } from "../../services/backups";
import { useAsyncScope } from "../hooks/useAsyncScope";

export default function RestoreReviewPanel({ initial, requiresTwoFactor, operator }: {
  initial: RestoreStatus; requiresTwoFactor: boolean; operator: OperatorSession;
}) {
  const scope = useAsyncScope();
  const [status, setStatus] = useState(initial);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [checks, setChecks] = useState([false, false, false]);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [notice, setNotice] = useState("");
  const record = status.record;
  const current = (id: number) => scope.isCurrent(id) && authState.session?.authenticated
    && authState.session.username === operator.username && authState.session.csrf_token === operator.csrf_token;
  async function act(submit: boolean) {
    if (busyRef.current || !record) return;
    if (submit && (!status.blocked || status.restart_required || !checks.every(Boolean)
      || !password || (requiresTwoFactor && !code.trim()))) return;
    const id = scope.capture();
    busyRef.current = true; setBusy(true); setNotice("");
    const proof = { id: record.id, password, code, confirm_original_stopped: true as const,
      confirm_configuration: true as const, confirm_trusted_backup: true as const };
    setPassword(""); setCode("");
    try {
      const value = submit ? await reviewRestore(proof) : await getRestoreStatus();
      if (current(id)) {
        if (value.record?.id !== record.id) setNotice("恢复记录已变化，请重新加载页面。");
        else setStatus(value);
      }
    } catch (error) {
      if (current(id)) setNotice(error instanceof BackupRequestError && !error.outcomeUnknown
        ? backupErrorMessage(error) : "未能确认复核结果，请先刷新恢复状态。不会自动再次提交。");
    } finally { if (current(id)) { busyRef.current = false; setBusy(false); } }
  }
  if (!record) return null;
  return <Card title="恢复后的首次复核">
    <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      <Alert showIcon type={status.restart_required ? "success" : "warning"}
        title={status.restart_required ? "复核已保存，请在服务器上重启控制面" : "当前实例处于恢复隔离状态"}
        description="复核并重启前，Agent、订阅访问和后台任务均不启用。证书自动续签、自动部署和通知已关闭，需要在启用后分别检查并重新开启。" />
      <Descriptions column={{ xs: 1, sm: 2 }} size="small" items={[
        { key: "sessions", label: "失效的旧会话", children: record.invalidated_sessions },
        { key: "commands", label: "终止的 Agent 命令", children: record.cancelled_agent_commands },
        { key: "certificates", label: "终止的证书任务", children: record.cancelled_certificate_jobs },
        { key: "files", label: "隔离的旧任务文件", children: record.quarantined_files },
      ]} />
      <Typography.Paragraph style={{ overflowWrap: "anywhere", margin: 0 }}>恢复编号：{record.id}<br />备份 ZIP SHA-256：{record.archive_sha256}</Typography.Paragraph>
      <Typography.Paragraph style={{ margin: 0 }}>重启后会按恢复的用户、套餐和节点配置重新协调访问权限，可能产生新的 Agent 命令；未完成的变更集仍需单独复核。远端机器的文件、证书挑战资源和 Agent 信任关系没有随备份回滚。</Typography.Paragraph>
      {notice && <Alert type="warning" showIcon title={notice} />}
      {!status.restart_required && status.blocked && <Form layout="vertical" onFinish={() => void act(true)}>
        <Space orientation="vertical" style={{ marginBottom: 16 }}>
          {["原实例已停止，不会与本实例同时连接 Agent", "已核对部署配置、密钥、用户权限及远端状态", "备份来源可信，了解重启后的自动协调行为"].map((label, index) =>
            <Checkbox key={label} checked={checks[index]} disabled={busy} onChange={event => setChecks(old => old.map((value, key) => key === index ? event.target.checked : value))}>{label}</Checkbox>)}
        </Space>
        <Form.Item label="恢复复核密码" htmlFor="restore-password" required><Input.Password id="restore-password" value={password} autoComplete="current-password" maxLength={1024} disabled={busy} onChange={event => setPassword(event.target.value)} /></Form.Item>
        {requiresTwoFactor && <Form.Item label="恢复复核验证码或恢复码" htmlFor="restore-code" required><Input id="restore-code" value={code} autoComplete="one-time-code" maxLength={64} disabled={busy} onChange={event => setCode(event.target.value)} /></Form.Item>}
        <Button type="primary" htmlType="submit" loading={busy} disabled={busy || !checks.every(Boolean) || !password || (requiresTwoFactor && !code.trim())}>保存复核结果</Button>
      </Form>}
      {status.restart_required && <Typography.Text>请使用原部署工具显式重启新实例，例如 docker compose restart open-node。此页面不会代您重启服务器。</Typography.Text>}
      <Button disabled={busy} onClick={() => void act(false)}>刷新恢复状态</Button>
    </Space>
  </Card>;
}
