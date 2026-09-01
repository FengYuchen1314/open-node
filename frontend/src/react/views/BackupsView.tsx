import { DeleteOutlined, DownloadOutlined, ReloadOutlined, SafetyCertificateOutlined, UploadOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Checkbox, Descriptions, Empty, Form, Input, Modal, Radio, Space, Tag, Typography, Upload } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { backupInProgress, validBackupRecipient, type BackupJob, type BackupsOverview, type BackupStatus, type RestoreArchiveFormat } from "../../domain/backups";
import { authState, type OperatorSession } from "../../services/auth";
import { BackupRequestError, backupCodeMessage, backupDownloadUrl, backupErrorMessage, createBackup, deleteBackup, getBackupJob, getBackups, newBackupRequestId, prepareRestoreArchive, uploadRestoreArchive } from "../../services/backups";
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
  const [restoreOpen, setRestoreOpen] = useState(false);
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [restoreFormat, setRestoreFormat] = useState<RestoreArchiveFormat>("age");
  const [restoreIdentity, setRestoreIdentity] = useState("");
  const [restoreTotpKey, setRestoreTotpKey] = useState("");
  const [restorePassword, setRestorePassword] = useState("");
  const [restoreCode, setRestoreCode] = useState("");
  const [restoreReplace, setRestoreReplace] = useState(false);
  const [restoreTrusted, setRestoreTrusted] = useState(false);
  const [remove, setRemove] = useState<BackupJob | null>(null);
  const [busy, setBusy] = useState<"" | "read" | "create" | "query" | "delete" | "restore">("");
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
  function closeRestore() {
    if (busyRef.current) return;
    setRestoreOpen(false); setRestoreFile(null); setRestoreIdentity(""); setRestoreTotpKey("");
    setRestorePassword(""); setRestoreCode(""); setRestoreReplace(false); setRestoreTrusted(false);
  }
  const canRestore = Boolean(overview?.restoration_supported && restoreFile && restoreFile.size >= 22
    && restorePassword && (!overview?.requires_two_factor || restoreCode.trim())
    && (restoreFormat === "plain" || restoreIdentity) && restoreReplace && restoreTrusted && !busy);
  async function submitRestore() {
    if (busyRef.current || !canRestore || !restoreFile) return;
    const request = scope.capture(), file = restoreFile;
    const payload = { format: restoreFormat, identity: restoreFormat === "age" ? restoreIdentity : "",
      subscriber_totp_key: restoreTotpKey, password: restorePassword, code: restoreCode,
      confirm_replace_instance: true as const, confirm_trusted_backup: true as const };
    setRestoreIdentity(""); setRestoreTotpKey(""); setRestorePassword(""); setRestoreCode("");
    setRestoreReplace(false); setRestoreTrusted(false); busyRef.current = true; setBusy("restore"); setNotice(null);
    try {
      const upload = await uploadRestoreArchive(file);
      if (!isCurrent(request)) return;
      const prepared = await prepareRestoreArchive(upload.id, payload);
      if (!isCurrent(request)) return;
      setRestoreOpen(false); setRestoreFile(null);
      setNotice({ type: "success", text: prepared.automatic_restart
        ? "恢复包已验证并隔离准备完成，服务正在重启。重启后请用备份中的管理员账户复核。"
        : "恢复包已验证并隔离准备完成。请重启 Open Node，再用备份中的管理员账户复核。" });
    } catch (error) {
      if (isCurrent(request)) setNotice({ type: "error", text: backupErrorMessage(error) });
    } finally { busyRef.current = false; if (isCurrent(request)) setBusy(""); }
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
      <Typography.Paragraph type="secondary">创建 age 加密备份、从浏览器安全准备恢复，或完成恢复后的首次复核。</Typography.Paragraph></div>
    {overview?.recovery?.blocked && <RestoreReviewPanel key={overview.recovery.record?.id} initial={overview.recovery} requiresTwoFactor={overview.requires_two_factor} operator={operator} />}
    <Alert type="warning" showIcon title="备份包含密钥和登录会话，请妥善保管。"
      description="创建备份时这里只接收 age 公钥。浏览器恢复时，age 私钥只随本次同源请求发送，用于临时解密，不写入恢复目录。" />
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
    {overview?.restoration_supported && <Card title="从备份恢复">
      <Alert type="error" showIcon title="恢复会在重启时替换当前实例数据。"
        description="服务器先在独立目录完成解密、格式校验和安全停写处理；准备成功前不会覆盖当前数据库。重启激活时会把旧实例保留在私有回滚目录。" />
      <Space wrap style={{ marginTop: 16 }}>
        <Button danger type="primary" icon={<UploadOutlined aria-hidden />} disabled={Boolean(busy) || Boolean(overview.recovery?.blocked)}
          onClick={() => { setRestoreFile(null); setRestoreFormat("age"); setRestoreOpen(true); }}>上传备份并恢复</Button>
      </Space>
      <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
        支持本项目 v1 age 加密包和明文 ZIP，不支持旧版 mmwx 备份。恢复后的远端 Agent、通知、自动任务和外部刷新保持隔离，须由管理员复核后再次重启。
      </Typography.Paragraph>
    </Card>}
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
      <Typography.Paragraph>{overview?.restoration_supported ? "当前部署支持浏览器上传恢复。" : "当前部署不支持浏览器恢复，请使用离线命令。"} 也可使用 open-node-backup restore 将 v1 备份导入全新私有目录，再在恢复实例中完成管理员复核。</Typography.Paragraph>
      <Typography.Paragraph style={{ marginBottom: 0 }}>旧版 mmwx 备份转换不在范围内。本格式不同于安装器的停服整卷归档，远端 Agent 的文件与信任关系不在备份范围内。完整操作见项目 docs/backups.md。</Typography.Paragraph>
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
    <Modal title="上传备份并准备恢复" open={restoreOpen} onCancel={closeRestore} destroyOnHidden maskClosable={false}
      closable={busy !== "restore"} keyboard={busy !== "restore"} footer={<Space wrap>
        <Button disabled={Boolean(busy)} onClick={closeRestore}>取消</Button>
        <Button danger type="primary" loading={busy === "restore"} disabled={!canRestore} onClick={() => void submitRestore()}>验证并准备恢复</Button>
      </Space>}>
      <Form layout="vertical">
        <Form.Item label="恢复文件" required extra="支持本项目生成的 .age 加密备份或明文 v1 ZIP。">
          <Space wrap><Upload accept=".age,.zip,application/octet-stream,application/zip" maxCount={1} showUploadList={false}
            beforeUpload={file => { setRestoreFile(file); return Upload.LIST_IGNORE; }} disabled={Boolean(busy)}>
            <Button icon={<UploadOutlined aria-hidden />} disabled={Boolean(busy)}>选择恢复文件</Button>
          </Upload><Typography.Text>{restoreFile?.name ?? "尚未选择文件"}</Typography.Text></Space>
        </Form.Item>
        <Form.Item label="备份格式" required><Radio.Group value={restoreFormat} disabled={Boolean(busy)}
          onChange={event => { setRestoreFormat(event.target.value as RestoreArchiveFormat); if (event.target.value === "plain") setRestoreIdentity(""); }}
          options={[{ label: "age 加密包", value: "age" }, { label: "明文 v1 ZIP", value: "plain" }]} /></Form.Item>
        {restoreFormat === "age" && <Form.Item label="age 恢复私钥" htmlFor="restore-identity" required
          extra="接受 age-keygen 生成的身份文件内容；只用于本次临时解密。"><Input.TextArea id="restore-identity" value={restoreIdentity}
            onChange={event => setRestoreIdentity(event.target.value)} autoSize={{ minRows: 3, maxRows: 6 }} maxLength={4096}
            autoComplete="off" spellCheck={false} disabled={Boolean(busy)} /></Form.Item>}
        <Form.Item label="订阅 TOTP 配置密钥" htmlFor="restore-totp-key"
          extra="仅当备份依赖原 OPEN_NODE_SUBSCRIBER_TOTP_KEY 时填写 44 字符密钥；不是管理员验证码。"><Input.Password id="restore-totp-key"
            value={restoreTotpKey} onChange={event => setRestoreTotpKey(event.target.value)} maxLength={44} autoComplete="off" disabled={Boolean(busy)} /></Form.Item>
        <Form.Item label="当前管理员密码" htmlFor="restore-password" required><Input.Password id="restore-password" value={restorePassword}
          onChange={event => setRestorePassword(event.target.value)} maxLength={1024} autoComplete="current-password" disabled={Boolean(busy)} /></Form.Item>
        {overview?.requires_two_factor && <Form.Item label="验证器验证码或恢复码" htmlFor="restore-code" required><Input id="restore-code"
          value={restoreCode} onChange={event => setRestoreCode(event.target.value)} maxLength={64} autoComplete="one-time-code" disabled={Boolean(busy)} /></Form.Item>}
        <Space orientation="vertical">
          <Checkbox checked={restoreReplace} disabled={Boolean(busy)} onChange={event => setRestoreReplace(event.target.checked)}>确认重启后用备份替换当前实例数据</Checkbox>
          <Checkbox checked={restoreTrusted} disabled={Boolean(busy)} onChange={event => setRestoreTrusted(event.target.checked)}>确认备份来源可信，且已保留当前实例的外部备份</Checkbox>
        </Space>
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
