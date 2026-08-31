import { DeleteOutlined, DownloadOutlined, ReloadOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Checkbox, Descriptions, Empty, Form, Input, Modal, Space, Tag, Typography } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { backupInProgress, validBackupRecipient, type BackupJob, type BackupsOverview, type BackupStatus } from "../../domain/backups";
import { authState, type OperatorSession } from "../../services/auth";
import { BackupRequestError, backupCodeMessage, backupDownloadUrl, backupErrorMessage, createBackup, deleteBackup, getBackupJob, getBackups, newBackupRequestId } from "../../services/backups";
import { useAsyncScope } from "../hooks/useAsyncScope";
import { useAdministratorSession } from "../hooks/useSession";
import RestoreReviewPanel from "./RestoreReviewPanel";

const labels: Record<BackupStatus, string> = {
  queued: "等待执行", running: "正在创建", ready: "可下载", failed: "创建失败", expired: "已过期", cancelled: "已取消",
};
const colors: Record<BackupStatus, string> = {
  queued: "gold", running: "processing", ready: "success", failed: "error", expired: "default", cancelled: "default",
};
const wrapping = { overflowWrap: "anywhere" as const, minWidth: 0 };
const timeText = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });
const sizeText = (value: number) => value < 1048576 ? `${(value / 1024).toFixed(1)} KiB` : `${(value / 1048576).toFixed(1)} MiB`;

export default function BackupsView() {
  const auth = useAdministratorSession();
  if (!auth.ready || !auth.session?.authenticated) return <Alert type="warning" showIcon title="请登录管理员账户后管理备份。" />;
  return <BackupWorkspace key={`${auth.session.username}\u0000${auth.session.csrf_token}`} operator={auth.session} />;
}

function BackupWorkspace({ operator }: { operator: OperatorSession }) {
  const scope = useAsyncScope();
  const busyRef = useRef(false);
  const pendingRef = useRef<{ id: string; recipient: string } | null>(null);
  const [overview, setOverview] = useState<BackupsOverview | null>(null);
  const [jobs, setJobs] = useState<BackupJob[]>([]);
  const [recipient, setRecipient] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [remove, setRemove] = useState<BackupJob | null>(null);
  const [busy, setBusy] = useState<"" | "read" | "create" | "query" | "delete">("");
  const [pending, setPending] = useState<{ id: string; recipient: string } | null>(null);
  const [notice, setNotice] = useState<{ type: "error" | "warning" | "success" | "info"; text: string } | null>(null);
  const isCurrent = useCallback((request: number) => scope.isCurrent(request)
    && authState.session?.authenticated === true && authState.session.username === operator.username
    && authState.session.csrf_token === operator.csrf_token, [scope, operator.username, operator.csrf_token]);
  const keepPending = useCallback((value: { id: string; recipient: string } | null) => {
    pendingRef.current = value; setPending(value);
  }, []);
  const refresh = useCallback(async (silent = false) => {
    if (busyRef.current) return;
    const request = scope.capture(); busyRef.current = true; setBusy("read");
    if (!silent) setNotice(null);
    try {
      const value = await getBackups();
      if (!isCurrent(request)) return;
      setOverview(value); setJobs(value.jobs);
      if (pendingRef.current && value.jobs.some(job => job.id === pendingRef.current?.id)) {
        keepPending(null); setNotice({ type: "info", text: "已查到原备份请求的任务，没有重新创建。" });
      }
    } catch (error) {
      if (isCurrent(request)) setNotice({ type: "warning", text: backupErrorMessage(error) });
    } finally {
      if (isCurrent(request)) { busyRef.current = false; setBusy(""); }
    }
  }, [scope, isCurrent, keepPending]);
  useEffect(() => {
    busyRef.current = false;
    void refresh();
    return () => scope.invalidate();
  }, [refresh, scope]);
  useEffect(() => {
    // Reconcile status with GET only; a missing creation receipt never triggers POST.
    if (busy || (!pending && !jobs.some(job => backupInProgress(job) || job.status === "ready"))) return;
    const timer = globalThis.setTimeout(() => void refresh(true), 5000);
    return () => globalThis.clearTimeout(timer);
  }, [busy, jobs, pending, refresh]);

  const canCreate = Boolean(overview?.available && !busy && !pending && validBackupRecipient(recipient)
    && !jobs.some(backupInProgress));
  const canSubmit = canCreate && confirmed && Boolean(password) && (!overview?.requires_two_factor || Boolean(code.trim()));
  function closeCreate() {
    if (busyRef.current) return;
    setCreateOpen(false); setPassword(""); setCode(""); setConfirmed(false);
  }
  function mergeJob(value: BackupJob) {
    setJobs(previous => [value, ...previous.filter(job => job.id !== value.id)]);
  }
  async function submit() {
    if (busyRef.current || !canSubmit) return;
    const request = scope.capture();
    let id: string;
    try { id = newBackupRequestId(); }
    catch (error) { setNotice({ type: "error", text: backupErrorMessage(error) }); return; }
    const payload = { request_id: id, recipient, password, code };
    // Only the public recipient and request ID remain available for reconciliation.
    setPassword(""); setCode(""); setConfirmed(false);
    keepPending({ id, recipient }); busyRef.current = true; setBusy("create"); setNotice(null);
    try {
      const value = await createBackup(payload);
      if (!isCurrent(request)) return;
      mergeJob(value); keepPending(null); setCreateOpen(false);
      setNotice({ type: "success", text: "备份任务已受理，请等待状态变为“可下载”。" });
    } catch (error) {
      if (!isCurrent(request)) return;
      setCreateOpen(false);
      if (error instanceof BackupRequestError && !error.outcomeUnknown) keepPending(null);
      setNotice({ type: "warning", text: backupErrorMessage(error) });
    } finally {
      busyRef.current = false;
      if (isCurrent(request)) setBusy("");
    }
  }
  async function queryPending() {
    if (busyRef.current || !pendingRef.current) return;
    const request = scope.capture(), id = pendingRef.current.id;
    busyRef.current = true; setBusy("query");
    try {
      const value = await getBackupJob(id);
      if (!isCurrent(request)) return;
      mergeJob(value); keepPending(null);
      setNotice({ type: "info", text: "已查到原备份请求的任务，没有重新创建。" });
    } catch (error) {
      if (isCurrent(request)) setNotice({ type: "warning", text: backupErrorMessage(error) });
    } finally { busyRef.current = false; if (isCurrent(request)) setBusy(""); }
  }
  async function confirmDelete() {
    if (busyRef.current || !remove) return;
    const request = scope.capture(), id = remove.id;
    busyRef.current = true; setBusy("delete"); setNotice(null);
    try {
      await deleteBackup(id);
      if (!isCurrent(request)) return;
      setJobs(previous => previous.filter(job => job.id !== id)); setRemove(null);
      setNotice({ type: "success", text: "删除请求已确认。" });
    } catch (error) {
      if (isCurrent(request)) setNotice({ type: "warning", text: error instanceof BackupRequestError && !error.outcomeUnknown
        ? backupErrorMessage(error) : "删除结果尚未确认，请刷新任务状态；不会自动重新提交删除。" });
    } finally { busyRef.current = false; if (isCurrent(request)) setBusy(""); }
  }

  return <section className="page-shell" aria-label="备份与恢复">
    <div><Typography.Title level={2}>备份与恢复</Typography.Title>
      <Typography.Paragraph type="secondary">创建 age 加密备份、查看任务状态，或完成离线导入后的首次恢复复核。</Typography.Paragraph></div>
    {overview?.recovery?.blocked && <RestoreReviewPanel key={overview.recovery.record?.id} initial={overview.recovery} requiresTwoFactor={overview.requires_two_factor} operator={operator} />}
    <Alert type="warning" showIcon title="备份包含密钥和登录会话，请妥善保管。"
      description="这里只接收 age 公开接收者公钥。恢复私钥由您自行保管，请勿上传；丢失对应私钥将无法解密。" />
    {notice && <Alert type={notice.type} showIcon title={notice.text} role="alert" />}
    {overview && !overview.available && !overview.recovery?.blocked && <Alert type="warning" showIcon title={backupCodeMessage(overview.unavailable_code)} />}
    <Card title="创建加密备份">
      <Form layout="vertical" className="form-narrow" onFinish={() => {
        if (canCreate) { setPassword(""); setCode(""); setConfirmed(false); setCreateOpen(true); }
      }}>
        <Form.Item label="age 接收者公钥" htmlFor="backup-recipient" required
          validateStatus={recipient && !validBackupRecipient(recipient) ? "error" : undefined}
          help={recipient && !validBackupRecipient(recipient) ? "请输入单个 62 字符的原生 age1 公钥，不得含空白、换行或私钥。" : "只支持单个原生 X25519 age1 公钥，不支持口令、SSH、插件或多个接收者。"}>
          <Input.TextArea id="backup-recipient" value={recipient} onChange={event => setRecipient(event.target.value)}
            autoSize={{ minRows: 2, maxRows: 3 }} autoComplete="off" spellCheck={false} disabled={Boolean(busy) || createOpen || Boolean(pending)} />
        </Form.Item>
        <Space wrap><Button type="primary" htmlType="submit" icon={<SafetyCertificateOutlined aria-hidden />} aria-label="创建加密备份" disabled={!canCreate}>创建加密备份</Button>
          <Button icon={<ReloadOutlined aria-hidden />} aria-label="刷新备份状态" loading={busy === "read"} disabled={Boolean(busy)} onClick={() => void refresh()}>刷新状态</Button></Space>
      </Form>
      <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>
        加密包保留 15 分钟，最多保留两份。服务重启、退出登录或安全设置变化后，当前制品将不可用。默认仅支持单个 Web 进程；创建期间会短暂停止接收写入操作。
      </Typography.Paragraph>
    </Card>
    {pending && <Card title="原请求结果待确认" data-testid="backup-pending">
      <Typography.Paragraph>请保留此请求 ID，可查询原任务；未找到不代表任务从未执行。不会自动再次创建备份。</Typography.Paragraph>
      <Typography.Paragraph style={wrapping} copyable>{pending.id}</Typography.Paragraph>
      <Button aria-label="查询原备份请求" disabled={Boolean(busy)} loading={busy === "query"} onClick={() => void queryPending()}>查询原请求</Button>
    </Card>}
    <Card title="备份任务" loading={busy === "read" && !overview}>
      {!jobs.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无备份任务" /> : <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
        {jobs.map(job => {
          const expired = Date.parse(job.expires_at) <= Date.now();
          let download: string | null = null;
          if (job.status === "ready" && !expired) { try { download = backupDownloadUrl(job.id); } catch { /* No cross-origin download fallback. */ } }
          return <Card size="small" key={job.id} data-testid={`backup-job-${job.id}`}>
            <Space wrap style={{ marginBottom: 12 }}><Tag color={expired && job.status === "ready" ? "default" : colors[job.status]}>{expired && job.status === "ready" ? "已超过保留期限" : labels[job.status]}</Tag>
              <Typography.Text style={wrapping}>任务 {job.id}</Typography.Text></Space>
            <Descriptions size="small" column={{ xs: 1, sm: 2 }} items={[
              { key: "created", label: "创建时间", children: timeText(job.created_at) },
              { key: "expires", label: "到期时间", children: timeText(job.expires_at) },
              ...(job.size === null ? [] : [{ key: "size", label: "加密文件大小", children: sizeText(job.size) }]),
            ]} />
            {job.sha256 && <Typography.Paragraph style={{ ...wrapping, marginTop: 12 }}><Typography.Text type="secondary">加密文件 SHA-256：</Typography.Text><br />{job.sha256}</Typography.Paragraph>}
            {job.error_code && <Alert type={job.status === "failed" ? "error" : "warning"} showIcon title={backupCodeMessage(job.error_code)} style={{ marginBlock: 12 }} />}
            <Space wrap style={{ marginTop: 12 }}>
              {download && <Button type="primary" icon={<DownloadOutlined aria-hidden />} href={download} download rel="noreferrer" aria-label={`下载加密备份 ${job.id}`}>下载加密备份</Button>}
              <Button danger icon={<DeleteOutlined aria-hidden />} aria-label={`删除备份 ${job.id}`} disabled={Boolean(busy)} onClick={() => setRemove(job)}>删除</Button>
            </Space>
            {job.status === "ready" && !expired && !download && <Typography.Paragraph type="warning">请使用控制面的同源页面下载备份。</Typography.Paragraph>}
          </Card>;
        })}
      </Space>}
      <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>任务状态每 5 秒通过只读请求刷新。下载仍需服务器验证当前会话；“可下载”不代表已经完成恢复验证。</Typography.Paragraph>
    </Card>
    <Card title="恢复说明">
      <Typography.Paragraph>支持使用 open-node-backup restore 将本页的 age 加密 v1 备份离线导入新目录，然后在此完成管理员复核并显式重启。命令要求确认原实例已停止、备份来源可信；不会覆盖已有目录。私钥只在服务器命令行使用，不上传到本页。</Typography.Paragraph>
      <Typography.Paragraph style={{ marginBottom: 0 }}>浏览器上传恢复、旧版 mmwx 备份和 PostgreSQL 迁移暂不支持。本格式不同于安装器的停服整卷归档，远端 Agent 的文件与信任关系不在备份范围内。完整操作见项目 docs/backups.md。</Typography.Paragraph>
    </Card>
    <Modal title="确认创建加密备份" open={createOpen} onCancel={closeCreate} destroyOnHidden maskClosable={false} closable={busy !== "create"} keyboard={busy !== "create"}
      footer={<Space wrap><Button aria-label="取消创建备份" disabled={Boolean(busy)} onClick={closeCreate}>取消</Button>
        <Button type="primary" htmlType="submit" form="backup-create-confirmation" aria-label="确认创建备份" loading={busy === "create"} disabled={!canSubmit}>确认创建</Button></Space>}>
      <Typography.Paragraph>请确认接收者公钥，并重新验证当前管理员身份。服务器不会保存或接收您的恢复私钥。</Typography.Paragraph>
      <Typography.Paragraph style={wrapping}>{recipient}</Typography.Paragraph>
      <Form id="backup-create-confirmation" layout="vertical" onFinish={() => void submit()}>
        <Form.Item label="当前管理员密码" htmlFor="backup-password" required><Input.Password id="backup-password" value={password}
          onChange={event => setPassword(event.target.value)} autoComplete="current-password" maxLength={1024} disabled={Boolean(busy)} /></Form.Item>
        {overview?.requires_two_factor && <Form.Item label="验证器验证码或恢复码" htmlFor="backup-code" required><Input id="backup-code" value={code}
          onChange={event => setCode(event.target.value)} autoComplete="one-time-code" maxLength={64} disabled={Boolean(busy)} /></Form.Item>}
        <Checkbox checked={confirmed} onChange={event => setConfirmed(event.target.checked)} disabled={Boolean(busy)}>确认已自行保管恢复私钥</Checkbox>
      </Form>
    </Modal>
    <Modal title="确认删除备份" open={Boolean(remove)} onCancel={() => { if (!busyRef.current) setRemove(null); }} destroyOnHidden maskClosable={false}
      closable={busy !== "delete"} keyboard={busy !== "delete"} footer={<Space wrap>
        <Button aria-label="取消删除备份" disabled={Boolean(busy)} onClick={() => setRemove(null)}>取消</Button>
        <Button danger type="primary" aria-label="确认删除备份" loading={busy === "delete"} disabled={Boolean(busy)} onClick={() => void confirmDelete()}>确认删除</Button>
      </Space>}>
      <Typography.Paragraph style={wrapping}>{remove?.id}</Typography.Paragraph>
      <Typography.Paragraph>此操作会取消尚未完成的任务或删除加密制品，删除后不能继续下载。已经下载到本地的文件不会被删除。</Typography.Paragraph>
    </Modal>
  </section>;
}
