import { ReloadOutlined } from "../../ui/icons";
import { Alert, Button, Card, Checkbox, Descriptions, Flex, Form, Input, Modal, Select, Spin, Switch, Table, Tag, Typography } from "../../ui";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  notificationDefaults, validNotificationChatId, validNotificationTimezone, validNotificationToken,
  type NotificationAttemptRead, type NotificationDeliveryDetail, type NotificationDeliveryRead, type NotificationPreviewRead,
  type NotificationRetryRequest, type NotificationSettingsDraft, type NotificationSettingsRead, type NotificationState,
  type NotificationTestRequest, type NotificationTokenAction,
} from "../../domain/notifications";
import {
  getNotificationDelivery, getNotificationRequest, getNotificationSettings, listNotificationDeliveries,
  notificationCodeMessage, notificationErrorMessage, previewNotifications, retryNotificationDelivery,
  testNotification, updateNotificationSettings,
} from "../../services/notifications";
import StrictInputNumber from "../components/StrictInputNumber";
import { useAsyncScope } from "../hooks/useAsyncScope";
import { useAdministratorSession } from "../hooks/useSession";

const wrapText = { overflowWrap: "anywhere" as const, whiteSpace: "pre-wrap" as const };
const stateLabels: Record<NotificationState, string> = { queued: "排队中", sending: "发送中", accepted: "Telegram 已接受", failed: "失败", unknown: "结果未知", cancelled: "已取消" };
const stateColors: Record<NotificationState, string> = { queued: "processing", sending: "processing", accepted: "success", failed: "error", unknown: "warning", cancelled: "default" };
function StateTag({ state }: { state: NotificationState }) { return <Tag color={stateColors[state]}>{stateLabels[state]}</Tag>; }
function date(value: string | null, zone = "UTC") {
  if (!value) return "—";
  try { return `${new Intl.DateTimeFormat("zh-CN", { timeZone: zone, dateStyle: "medium", timeStyle: "medium", hour12: false }).format(new Date(value))} ${zone}`; }
  catch { return "时间不可用"; }
}
function draftFrom(value: NotificationSettingsRead): NotificationSettingsDraft {
  return { enabled: value.enabled, chat_id: value.chat_id, advance_days: value.advance_days, timezone: value.timezone, local_time: "09:00" };
}
function retryAllowed(value: NotificationDeliveryRead, now: number): boolean {
  return (value.state === "failed" || value.state === "unknown") && value.manual_retry_allowed && !!value.last_attempt_id
    && (value.retry_available_at === null || (Number.isFinite(Date.parse(value.retry_available_at)) && Date.parse(value.retry_available_at) <= now));
}
function requestUuid(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6]! & 15) | 64; bytes[8] = (bytes[8]! & 63) | 128;
  const hex = [...bytes].map(byte => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
type SendOperation = { kind: "test"; payload: NotificationTestRequest } | { kind: "retry"; deliveryId: string; payload: NotificationRetryRequest };
type PendingRequest = { operation: SendOperation; target: string; phase: "submitting" | "uncertain" };
type Confirmation = { kind: "test" | "retry"; settings: NotificationSettingsRead; delivery?: NotificationDeliveryRead; replay?: PendingRequest };

export default function NotificationsView() {
  const auth = useAdministratorSession();
  if (!auth.ready) return <div role="status" aria-label="正在读取管理员会话"><Spin /></div>;
  if (!auth.session?.authenticated) return <Alert type="warning" showIcon title="请使用管理员账户登录后管理通知。" />;
  return <NotificationsWorkspace key={auth.session.username ?? "administrator"} />;
}

function NotificationsWorkspace() {
  const lifetime = useAsyncScope(), settingsScope = useAsyncScope(), previewScope = useAsyncScope(), listScope = useAsyncScope(), detailScope = useAsyncScope(), prepareScope = useAsyncScope();
  const writeBusy = useRef(false), listBusy = useRef(false), detailBusy = useRef(false), lookupBusy = useRef(false);
  const savedRef = useRef<NotificationSettingsRead | null>(null), pendingRef = useRef<PendingRequest | null>(null), detailIdRef = useRef<string | null>(null);
  const [saved, setSaved] = useState<NotificationSettingsRead | null>(null), [draft, setDraft] = useState<NotificationSettingsDraft>({ ...notificationDefaults });
  const [tokenAction, setTokenAction] = useState<NotificationTokenAction>("keep"), [token, setToken] = useState(""), [clearConfirmed, setClearConfirmed] = useState(false);
  const [busy, setBusy] = useState("load"), [error, setError] = useState(""), [notice, setNotice] = useState(""), [needsRefresh, setNeedsRefresh] = useState(false);
  const [preview, setPreview] = useState<NotificationPreviewRead | null>(null), [previewError, setPreviewError] = useState("");
  const [deliveries, setDeliveries] = useState<NotificationDeliveryRead[]>([]), [listError, setListError] = useState(""), [listLoading, setListLoading] = useState(false);
  const [detail, setDetail] = useState<NotificationDeliveryDetail | null>(null), [detailError, setDetailError] = useState(""), [detailLoading, setDetailLoading] = useState(false);
  const [pending, setPending] = useState<PendingRequest | null>(null), [pendingError, setPendingError] = useState(""), [lookupLoading, setLookupLoading] = useState(false), [stopTracking, setStopTracking] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null), [targetConfirmed, setTargetConfirmed] = useState(false), [riskConfirmed, setRiskConfirmed] = useState(false);
  const [now, setNow] = useState(Date.now());

  function rememberPending(value: PendingRequest | null) { pendingRef.current = value; setPending(value); setStopTracking(false); }
  function acceptSaved(value: NotificationSettingsRead) {
    savedRef.current = value; setSaved(value); setDraft(draftFrom(value)); setToken(""); setTokenAction("keep"); setClearConfirmed(false); setNeedsRefresh(false);
    previewScope.invalidate(); setPreview(null); setPreviewError("");
  }
  function acceptDelivery(value: NotificationDeliveryRead) {
    listScope.invalidate(); listBusy.current = false; setListLoading(false);
    setDeliveries(rows => [value, ...rows.filter(row => row.id !== value.id)].slice(0, 50));
  }
  const loadList = useCallback(async () => {
    if (listBusy.current) return;
    const run = listScope.begin(); listBusy.current = true; setListLoading(true);
    try { const value = await listNotificationDeliveries(); if (listScope.isCurrent(run)) { setDeliveries(value.deliveries); setListError(""); } }
    catch (failure) { if (listScope.isCurrent(run)) setListError(notificationErrorMessage(failure)); }
    finally { if (listScope.isCurrent(run)) { listBusy.current = false; setListLoading(false); } }
  }, [listScope]);
  const loadDetail = useCallback(async (id: string) => {
    const run = detailScope.begin(); detailIdRef.current = id; detailBusy.current = true; setDetailLoading(true); setDetailError("");
    try { const value = await getNotificationDelivery(id); if (detailScope.isCurrent(run) && detailIdRef.current === id) setDetail(value); }
    catch (failure) { if (detailScope.isCurrent(run)) setDetailError(notificationErrorMessage(failure)); }
    finally { if (detailScope.isCurrent(run)) { detailBusy.current = false; setDetailLoading(false); } }
  }, [detailScope]);
  const lookupPending = useCallback(async () => {
    const current = pendingRef.current;
    if (!current || current.phase === "submitting" || lookupBusy.current) return;
    const run = lifetime.capture(); lookupBusy.current = true; setLookupLoading(true);
    try {
      const value = await getNotificationRequest(current.operation.payload.request_id);
      if (!lifetime.isCurrent(run) || pendingRef.current !== current) return;
      pendingRef.current = null; setPending(null); setPendingError(""); setStopTracking(false);
      listScope.invalidate(); listBusy.current = false; setListLoading(false);
      setDeliveries(rows => [value, ...rows.filter(row => row.id !== value.id)].slice(0, 50));
      setNotice("已找到原请求的投递记录；没有另建请求或重新发送。");
      if (!detailIdRef.current || detailIdRef.current === value.id) void loadDetail(value.id);
    } catch (failure) { if (lifetime.isCurrent(run) && pendingRef.current === current) setPendingError(notificationErrorMessage(failure)); }
    finally { if (lifetime.isCurrent(run)) { lookupBusy.current = false; setLookupLoading(false); } }
  }, [lifetime, listScope, loadDetail]);

  async function reloadSettings() {
    if (writeBusy.current) return;
    const run = settingsScope.begin(); writeBusy.current = true; setBusy("load"); setToken(""); setError(""); setNotice("");
    try { const value = await getNotificationSettings(); if (settingsScope.isCurrent(run)) acceptSaved(value); }
    catch (failure) { if (settingsScope.isCurrent(run)) { setError(notificationErrorMessage(failure)); setNeedsRefresh(true); } }
    finally { if (settingsScope.isCurrent(run)) { writeBusy.current = false; setBusy(""); } }
  }
  useEffect(() => {
    void reloadSettings(); void loadList();
    return () => { writeBusy.current = false; listBusy.current = false; detailBusy.current = false; lookupBusy.current = false; };
  }, []);
  useEffect(() => {
    const tick = () => {
      if (document.hidden) { setToken(""); return; }
      setNow(Date.now()); void loadList(); void lookupPending();
      if (detailIdRef.current && !detailBusy.current) void loadDetail(detailIdRef.current);
    };
    const timer = window.setInterval(tick, 5000);
    document.addEventListener("visibilitychange", tick);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", tick); pendingRef.current = null; detailIdRef.current = null; };
  }, [loadList, loadDetail, lookupPending]);

  const dirty = !!saved && (tokenAction !== "keep" || draft.enabled !== saved.enabled || draft.chat_id !== saved.chat_id || draft.advance_days !== saved.advance_days || draft.timezone !== saved.timezone);
  const validDraft = Number.isSafeInteger(draft.advance_days) && draft.advance_days >= 1 && draft.advance_days <= 365 && validNotificationTimezone(draft.timezone)
    && validNotificationChatId(draft.chat_id) && (tokenAction !== "replace" || validNotificationToken(token)) && (tokenAction !== "clear" || clearConfirmed)
    && (!draft.enabled || (validNotificationChatId(draft.chat_id, false) && tokenAction !== "clear" && (tokenAction === "replace" || saved?.has_token)));
  const configured = !!saved?.storage_ready && saved.has_token && validNotificationChatId(saved.chat_id, false);
  const storageAllowsSave = !!saved && (saved.storage_ready || (tokenAction === "keep" && !draft.enabled));
  const canSave = storageAllowsSave && dirty && validDraft && !busy && !needsRefresh && !confirmation && !pending;

  async function save() {
    if (!saved || !canSave || writeBusy.current) return;
    const run = settingsScope.begin(); writeBusy.current = true; setBusy("save"); setError(""); setNotice("");
    const payload = { ...draft, expected_revision: saved.revision, token_action: tokenAction, ...(tokenAction === "replace" ? { token } : {}) };
    setToken(""); // Clear before calling the API; failure never restores the secret.
    try { const value = await updateNotificationSettings(payload); if (settingsScope.isCurrent(run)) { acceptSaved(value); setNotice("通知配置已保存；保存操作不会发送测试消息。"); } }
    catch (failure) { if (settingsScope.isCurrent(run)) { setError(notificationErrorMessage(failure)); setNeedsRefresh(true); } }
    finally { if (settingsScope.isCurrent(run)) { writeBusy.current = false; setBusy(""); } }
  }
  async function showPreview() {
    if (!saved || busy || needsRefresh || writeBusy.current) return;
    const run = previewScope.begin(), alive = lifetime.capture(); writeBusy.current = true; setBusy("preview"); setPreviewError("");
    try { const value = await previewNotifications({ expected_revision: saved.revision }); if (previewScope.isCurrent(run)) setPreview(value); }
    catch (failure) { if (previewScope.isCurrent(run)) setPreviewError(notificationErrorMessage(failure)); }
    finally { if (lifetime.isCurrent(alive)) { writeBusy.current = false; setBusy(""); } }
  }
  async function prepare(kind: "test" | "retry", selected?: NotificationDeliveryRead, replay?: PendingRequest) {
    if (!savedRef.current || writeBusy.current || needsRefresh || (pendingRef.current && !replay)) return;
    const run = prepareScope.begin(); writeBusy.current = true; setBusy("prepare"); setError("");
    try {
      const [current, currentDetail] = await Promise.all([getNotificationSettings(), selected ? getNotificationDelivery(selected.id) : Promise.resolve(null)]);
      if (!prepareScope.isCurrent(run)) return;
      if (current.revision !== savedRef.current?.revision || (replay && (current.revision !== replay.operation.payload.expected_revision || current.chat_id !== replay.target))) {
        setNeedsRefresh(true); setError("已保存通知配置已发生变化，请重新读取配置；旧请求只能先查询对账。"); return;
      }
      savedRef.current = current; setSaved(current);
      if (!current.storage_ready || !current.has_token || !validNotificationChatId(current.chat_id, false)) { setError("请先保存可用的 Bot Token 和 Chat ID，并检查通知密钥存储。"); return; }
      if (currentDetail) { acceptDelivery(currentDetail.delivery); detailScope.invalidate(); detailBusy.current = false; setDetailLoading(false); setDetail(currentDetail); detailIdRef.current = currentDetail.delivery.id; }
      if (kind === "retry" && !replay && (!currentDetail || !retryAllowed(currentDetail.delivery, Date.now()))) { setError("当前投递还不允许人工重试，请等待期限结束并刷新记录。"); return; }
      setTargetConfirmed(false); setRiskConfirmed(false); setConfirmation({ kind, settings: current, delivery: currentDetail?.delivery, replay });
    } catch (failure) { if (prepareScope.isCurrent(run)) setError(notificationErrorMessage(failure)); }
    finally { if (prepareScope.isCurrent(run)) { writeBusy.current = false; setBusy(""); } }
  }
  async function sendConfirmed() {
    const current = confirmation;
    if (!current || !targetConfirmed || (current.kind === "retry" && !riskConfirmed) || writeBusy.current || needsRefresh) return;
    if (current.settings.revision !== savedRef.current?.revision || current.settings.chat_id !== savedRef.current.chat_id) { setError("通知目标已变化，请重新读取配置并确认。"); return; }
    if (current.kind === "retry" && !current.replay && (!current.delivery || !retryAllowed(current.delivery, Date.now()))) return;
    if (pendingRef.current && pendingRef.current !== current.replay) return;
    let operation: SendOperation;
    try {
      operation = current.replay?.operation ?? (current.kind === "test"
        ? { kind: "test", payload: { expected_revision: current.settings.revision, request_id: requestUuid() } }
        : { kind: "retry", deliveryId: current.delivery!.id, payload: { expected_revision: current.settings.revision, request_id: requestUuid(), expected_attempt_id: current.delivery!.last_attempt_id!, confirm_duplicate_risk: true } });
    } catch { setError("无法生成安全的请求 ID，请使用支持 Web Crypto 的浏览器。"); return; }
    const run = lifetime.capture(), tracked: PendingRequest = { operation, target: current.settings.chat_id, phase: "submitting" };
    writeBusy.current = true; setBusy("send"); rememberPending(tracked); setPendingError(""); setError(""); setNotice("");
    try {
      const value = operation.kind === "test" ? await testNotification(operation.payload) : await retryNotificationDelivery(operation.deliveryId, operation.payload);
      if (!lifetime.isCurrent(run) || pendingRef.current !== tracked) return;
      rememberPending(null); setConfirmation(null); acceptDelivery(value.delivery); detailScope.invalidate(); detailBusy.current = false; setDetailLoading(false);
      setDetail(value); detailIdRef.current = value.delivery.id; setNotice("已记录投递请求，请查看投递状态；请求回执不代表收件人已读。");
    } catch (failure) {
      if (lifetime.isCurrent(run) && pendingRef.current === tracked) {
        rememberPending({ ...tracked, phase: "uncertain" }); setPendingError(notificationErrorMessage(failure)); setConfirmation(null);
      }
    } finally { if (lifetime.isCurrent(run)) { writeBusy.current = false; setBusy(""); } }
  }
  function closeDetail() { detailScope.invalidate(); detailIdRef.current = null; detailBusy.current = false; setDetail(null); setDetailLoading(false); setDetailError(""); }

  return <section className="page-shell" aria-label="通知设置">
    <header><Typography.Title level={2}>通知设置</Typography.Title><Typography.Paragraph type="secondary">管理员 Telegram 配置、测试和套餐临期提醒。用户绑定、20 点日报和人工续费审批尚未接入本页；通知不会延长套餐或确认付款。</Typography.Paragraph></header>
    <Alert type="warning" showIcon title="套餐提醒会把用户名、套餐名称和到期时间发送到指定 Telegram Chat ID。" description="请确认目标聊天的成员和权限。读取、保存和预览请求本身不会发送消息；开启提醒后，独立调度器在所选时区 09:00 之后每分钟扫描，符合条件的提醒可能很快发出。关闭提醒无法撤回已发出的消息。" />
    {error && <Alert type="error" showIcon title={error} role="alert" />}{notice && <Alert type="info" showIcon title={notice} role="status" />}
    <Card title="管理员 Telegram 配置"><Flex vertical gap="middle" style={{ minWidth: 0 }}>
      <Button style={{ alignSelf: "flex-start", maxWidth: "100%", height: "auto", whiteSpace: "normal" }} icon={<ReloadOutlined aria-hidden />} aria-label="重新读取已保存通知配置" loading={busy === "load"} disabled={!!busy || !!confirmation} onClick={() => void reloadSettings()}>重新读取已保存配置（丢弃草稿）</Button>
      {saved && <Descriptions size="small" column={1} items={[
        { key: "revision", label: "已保存配置版本", children: saved.revision }, { key: "token", label: "已保存 Bot Token", children: saved.has_token ? "已配置（不回显）" : "未配置" },
        { key: "chat", label: "已保存 Chat ID", children: <span style={wrapText}>{saved.chat_id || "未配置"}</span> },
        { key: "schedule", label: "已保存提醒时间", children: `${saved.timezone} ${saved.local_time} · ${saved.enabled ? "已开启" : "已关闭"}` },
      ]} />}
      {saved && !saved.storage_ready && <Alert type="error" showIcon title="通知密钥存储不可用" description={<>{notificationCodeMessage(saved.storage_error)}<div>仍可关闭提醒并保留原 Token 密文；更换或清除 Token、发送测试和人工重试暂不可用。</div></>} />}
      {dirty && <Alert type="info" showIcon title="有尚未保存的修改。预览和测试仍使用上方已保存配置。" />}
      {needsRefresh && <Alert type="warning" showIcon title="请先重新读取已保存配置。新 Token 输入已清空，不会自动恢复或重试保存。" />}
      <Form layout="vertical" preserve={false} autoComplete="off" onFinish={() => void save()} style={{ maxWidth: 640 }}>
        <Form.Item label="套餐临期提醒"><Switch aria-label="启用套餐临期提醒" checked={draft.enabled} disabled={!saved || !!busy || !!confirmation || !!pending} onChange={enabled => setDraft(value => ({ ...value, enabled }))} /></Form.Item>
        <Form.Item label="目标 Chat ID" help="保留数字字符串；支持群聊的负数 ID，不接受用户名或链接。"><Input aria-label="Telegram Chat ID" inputMode="text" maxLength={20} value={draft.chat_id} disabled={!saved || !!busy || !!confirmation || !!pending} onChange={event => setDraft(value => ({ ...value, chat_id: event.target.value }))} /></Form.Item>
        <Form.Item label="Bot Token 操作"><Select aria-label="Bot Token 操作" value={tokenAction} disabled={!saved || !!busy || !!confirmation || !!pending} options={[{ value: "keep", label: "保留已保存的 Token" }, { value: "replace", label: "替换 Bot Token", disabled: !saved?.storage_ready }, { value: "clear", label: "清除已保存的 Token", disabled: !saved?.storage_ready }]} onChange={value => { setTokenAction(value); setToken(""); setClearConfirmed(false); }} /></Form.Item>
        {tokenAction === "replace" && <Form.Item label="新的 Bot Token" help="仅替换时提交；请求开始即清空，失败后须重新输入。"><Input.Password aria-label="新的 Telegram Bot Token" autoComplete="off" autoCapitalize="none" spellCheck={false} visibilityToggle={false} maxLength={149} value={token} disabled={!!busy || !!confirmation || !!pending} onChange={event => setToken(event.target.value)} /></Form.Item>}
        {tokenAction === "clear" && <Form.Item><Checkbox checked={clearConfirmed} disabled={!!busy || !!confirmation || !!pending} onChange={event => setClearConfirmed(event.target.checked)}>确认清除已保存的 Bot Token</Checkbox>{draft.enabled && <Typography.Paragraph type="danger">清除 Token 前，请先关闭套餐临期提醒。</Typography.Paragraph>}</Form.Item>}
        <Form.Item label="提前提醒天数" help="1–365 天，默认 7 天；输入不会被自动改成边界值。"><StrictInputNumber aria-label="提前提醒天数" value={draft.advance_days} style={{ width: "100%" }} disabled={!saved || !!busy || !!confirmation || !!pending} onChange={advance_days => setDraft(value => ({ ...value, advance_days: advance_days ?? Number.NaN }))} /></Form.Item>
        <Form.Item label="提醒时区" help="IANA 时区名称，例如 Asia/Shanghai；每天本地 09:00 起按分钟扫描，重启后只补查当前仍符合条件的套餐。"><Input aria-label="通知时区" maxLength={100} value={draft.timezone} disabled={!saved || !!busy || !!confirmation || !!pending} onChange={event => setDraft(value => ({ ...value, timezone: event.target.value }))} /></Form.Item>
        <Form.Item label="每日扫描时间"><Input aria-label="通知每日扫描时间" value="09:00" readOnly /></Form.Item>
        <Button type="primary" htmlType="submit" aria-label="保存通知配置" loading={busy === "save"} disabled={!canSave}>保存配置</Button>
      </Form>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>Token 不回显尾号，也不写入浏览器存储。请一并备份 SQLite 数据库与 notifications 密钥目录；密钥丢失时先恢复原备份，不要用新密钥覆盖原数据。</Typography.Paragraph>
    </Flex></Card>

    <Card title="预览与测试"><Flex vertical gap="middle" style={{ minWidth: 0 }}>
      <Typography.Paragraph>预览只读取已保存配置和当前候选，不排队、不发送。每个到期事件只提醒一次；候选包含已有投递记录的事件，不是下一轮待发送数量，实际发送还受投递记录和当前开关限制。测试需单独确认接收目标；套餐提醒关闭时仍可测试已保存配置。</Typography.Paragraph>
      <Flex gap="small" wrap><Button aria-label="预览已保存通知配置" loading={busy === "preview"} disabled={!saved || !!busy || needsRefresh || !!confirmation} onClick={() => void showPreview()}>预览已保存配置</Button><Button aria-label="发送 Telegram 测试" disabled={!configured || !!busy || needsRefresh || !!pending || !!confirmation} onClick={() => void prepare("test")}>发送测试消息…</Button></Flex>
      {previewError && <Alert type="error" showIcon title={previewError} />}
      {preview && <div data-testid="notification-preview"><Flex vertical gap="small">
        <Alert type={preview.is_sample ? "info" : "success"} showIcon title={preview.is_sample ? "没有符合条件的用户，以下仅为示例；不会发送。" : `当前到期窗口内有 ${preview.total} 个符合条件的用户；不是待发送数量。`} description={`已保存版本 ${preview.revision} · ${preview.timezone} ${preview.local_time} · ${preview.enabled ? "提醒已开启" : "提醒已关闭"} · 此预览没有发送`} />
        <Typography.Text>预览读取时间：{date(preview.as_of, preview.timezone)}</Typography.Text>
        <Typography.Text style={wrapText}>已保存目标 Chat ID：{preview.chat_id || "未配置"}</Typography.Text>
        <pre style={{ ...wrapText, margin: 0, maxHeight: 300, overflow: "auto", maxWidth: "100%" }}>{preview.sample_message}</pre>
        {!!preview.candidates.length && <Table size="small" rowKey={row => `${row.username}:${row.plan_id}`} pagination={false} scroll={{ x: 560 }} dataSource={preview.candidates} columns={[
          { title: "用户名", dataIndex: "username", render: value => <span style={wrapText}>{value}</span> }, { title: "套餐", dataIndex: "plan_name", render: value => <span style={wrapText}>{value}</span> },
          { title: "到期时间", dataIndex: "expires_at", render: value => date(value, preview.timezone) },
        ]} />}
        {preview.total > preview.candidates.length && <Typography.Text type="secondary">这里只展示前 {preview.candidates.length} 个候选。</Typography.Text>}
      </Flex></div>}
    </Flex></Card>

    {pending && <Card title="原请求待核实" data-testid="notification-pending"><Flex vertical gap="small">
      <Alert type="warning" showIcon title={pending.phase === "submitting" ? "请求正在提交，请勿重复点击。" : "尚未确认原请求结果，不能按失败直接再发一条。"} description="查询和自动轮询只读取回执。未找到记录也不代表消息未发送；手动再次提交会复用原请求 ID 和原始参数。" />
      <Typography.Text style={wrapText}>请求 ID：{pending.operation.payload.request_id}</Typography.Text><Typography.Text style={wrapText}>原请求目标 Chat ID：{pending.target}</Typography.Text>
      {pendingError && <Alert type="warning" showIcon title={pendingError} />}
      <Flex gap="small" wrap><Button aria-label="查询原通知请求" loading={lookupLoading} disabled={pending.phase === "submitting"} onClick={() => void lookupPending()}>查询原请求</Button><Button aria-label="使用原通知请求 ID 再次提交" disabled={!!busy || needsRefresh || pending.phase === "submitting" || !saved || saved.revision !== pending.operation.payload.expected_revision || saved.chat_id !== pending.target} onClick={() => void prepare(pending.operation.kind, undefined, pending)}>使用同一请求 ID 再次提交…</Button></Flex>
      <Checkbox aria-label="确认停止跟踪通知请求的风险" checked={stopTracking} disabled={!!busy} onChange={event => setStopTracking(event.target.checked)}>已核实投递记录，理解停止本页跟踪不会取消原请求，另发测试可能重复。</Checkbox>
      <Button danger aria-label="停止跟踪通知请求" disabled={!!busy || !stopTracking} onClick={() => { rememberPending(null); setPendingError(""); setNotice("已停止本页跟踪，原请求仍可能完成。再次发送前请检查投递记录。"); }}>停止本页跟踪</Button>
      <Typography.Text type="secondary">离开此页不会取消请求。重新进入后，请先查看投递记录再决定是否发送。</Typography.Text>
    </Flex></Card>}

    <Card title="最近 50 条投递记录"><Flex vertical gap="middle" style={{ minWidth: 0 }}>
      <Typography.Paragraph type="secondary">“Telegram 已接受”仅表示服务端收到有效接受回执，不代表收件人已读。结果未知不会自动重新发送；人工重试可能造成重复通知。</Typography.Paragraph>
      <Button style={{ alignSelf: "flex-start" }} icon={<ReloadOutlined aria-hidden />} aria-label="刷新通知投递记录" loading={listLoading} onClick={() => void loadList()}>刷新记录</Button>
      {listError && <Alert type="error" showIcon title={listError} />}
      <Table<NotificationDeliveryRead> rowKey="id" size="small" dataSource={deliveries} loading={listLoading && !deliveries.length} scroll={{ x: 860 }} pagination={{ pageSize: 10, showSizeChanger: false }} locale={{ emptyText: "暂无通知投递记录。" }} columns={[
        { title: "类型 / 用户", width: 140, render: (_, row) => <><div>{row.kind === "test" ? "管理员测试" : "套餐临期提醒"}</div><span style={wrapText}>{row.username ?? "—"}</span></> },
        { title: "目标 Chat ID", dataIndex: "chat_id", width: 170, render: value => <span style={wrapText}>{value}</span> },
        { title: "状态", width: 160, render: (_, row) => <StateTag state={row.state} /> },
        { title: "结果说明", width: 230, render: (_, row) => <span style={wrapText}>{row.code ? notificationCodeMessage(row.code) : "—"}</span> },
        { title: "创建时间", dataIndex: "created_at", width: 180, render: value => date(value) },
        { title: "操作", width: 220, render: (_, row) => <Flex gap="small" wrap><Button aria-label={`查看通知投递 ${row.id}`} onClick={() => { setDetail(null); void loadDetail(row.id); }}>查看记录</Button><Button aria-label={`人工重试通知 ${row.id}`} disabled={!configured || !!busy || needsRefresh || !!pending || !!confirmation || !retryAllowed(row, now)} onClick={() => void prepare("retry", row)}>人工重试…</Button></Flex> },
      ]} />
    </Flex></Card>

    {(detail || detailLoading || detailError) && <Card title="投递详情" data-testid="notification-detail"><Flex vertical gap="small" style={{ minWidth: 0 }}>
      <Button style={{ alignSelf: "flex-start" }} aria-label="关闭通知投递详情" onClick={closeDetail}>关闭详情</Button>{detailLoading && <Spin size="small" />}{detailError && <Alert type="error" showIcon title={detailError} />}
      {detail && <>
        <Descriptions size="small" column={1} items={[
          { key: "id", label: "投递 ID", children: <span style={wrapText}>{detail.delivery.id}</span> }, { key: "state", label: "当前状态", children: <StateTag state={detail.delivery.state} /> },
          { key: "target", label: "此投递记录的目标", children: <span style={wrapText}>{detail.delivery.chat_id}</span> },
          { key: "request", label: "最近请求 ID", children: <span style={wrapText}>{detail.delivery.request_id ?? "自动提醒事件"}</span> },
          { key: "user", label: "用户 / 套餐", children: <span style={wrapText}>{detail.delivery.username ?? "—"} / {detail.delivery.plan_name ?? "—"}</span> },
          { key: "expiry", label: "套餐到期时间", children: date(detail.delivery.expires_at) }, { key: "retry", label: "最早人工重试时间", children: date(detail.delivery.retry_available_at) },
          { key: "next", label: "下次安全重试时间", children: date(detail.delivery.next_attempt_at) },
          { key: "permission", label: "服务端人工重试许可", children: detail.delivery.manual_retry_allowed ? "已允许（仍须满足等待时间并手动确认）" : "未允许" },
        ]} />
        {detail.delivery.state === "unknown" && <Alert type="warning" showIcon title="结果未知：原消息可能已被接受，不能当作未发送。" description="请等待旧尝试的期限结束并核实目标；人工重试必须确认可能重复的风险。" />}
        {detail.delivery.code && <Alert type={detail.delivery.state === "accepted" ? "info" : "warning"} showIcon title={notificationCodeMessage(detail.delivery.code)} />}
        <Table<NotificationAttemptRead> size="small" rowKey="id" dataSource={detail.attempts} scroll={{ x: 900 }} pagination={false} locale={{ emptyText: "尚无发送尝试。" }} columns={[
          { title: "次数", dataIndex: "attempt_number", width: 70 }, { title: "状态", width: 160, render: (_, row) => <StateTag state={row.state} /> },
          { title: "目标 Chat ID", dataIndex: "chat_id", width: 170, render: value => <span style={wrapText}>{value}</span> },
          { title: "等待期限", dataIndex: "deadline_at", width: 180, render: value => date(value) },
          { title: "结果说明", width: 250, render: (_, row) => <span style={wrapText}>{row.code ? notificationCodeMessage(row.code) : "—"}{row.late_receipt_at && <div>晚到回执：{date(row.late_receipt_at)}</div>}</span> },
          { title: "Telegram 消息 ID", dataIndex: "message_id", width: 150, render: value => value ?? "—" },
        ]} />
      </>}
    </Flex></Card>}

    {confirmation && <Modal open title={confirmation.replay ? "再次提交原通知请求" : confirmation.kind === "test" ? "确认发送测试消息" : "确认人工重试通知"} width={640} style={{ top: 16, maxWidth: "calc(100vw - 24px)" }} styles={{ body: { maxHeight: "calc(100dvh - 220px)", overflowY: "auto" } }} destroyOnHidden onCancel={() => { if (!writeBusy.current) setConfirmation(null); }} closable={!busy} maskClosable={!busy} footer={<Flex gap="small" wrap justify="end"><Button aria-label="取消通知发送确认" disabled={!!busy} onClick={() => setConfirmation(null)}>取消</Button><Button type="primary" aria-label="确认提交通知发送请求" loading={busy === "send"} disabled={!!busy || !targetConfirmed || (confirmation.kind === "retry" && !riskConfirmed)} onClick={() => void sendConfirmed()}>确认提交请求</Button></Flex>}>
      <Flex vertical gap="middle">
        <Alert type="warning" showIcon title={confirmation.replay ? "复用原请求 ID 和原始参数；不会另建请求。" : confirmation.kind === "test" ? "将向以下已保存目标发送一条固定测试消息。" : "原消息可能已被接受，人工重试可能造成重复通知。"} description="读取回执、取消此确认窗口均不会发送。提交请求后关闭页面，不能撤回已发出的消息。" />
        {dirty && <Alert type="info" showIcon title="未保存的修改不会用于本次发送。" />}
        <Descriptions column={1} size="small" items={[
          { key: "chat", label: "本次已保存目标 Chat ID", children: <Typography.Text strong style={wrapText}>{confirmation.settings.chat_id}</Typography.Text> },
          { key: "revision", label: "已保存配置版本", children: confirmation.settings.revision },
          ...(confirmation.delivery ? [{ key: "old", label: "上一次记录的目标", children: <span style={wrapText}>{confirmation.delivery.chat_id}</span> }] : []),
        ]} />
        <Checkbox aria-label="确认 Telegram 接收目标" checked={targetConfirmed} onChange={event => setTargetConfirmed(event.target.checked)} disabled={!!busy}>我确认消息将发送至以上已保存 Chat ID。</Checkbox>
        {confirmation.kind === "retry" && <Checkbox aria-label="确认通知可能重复发送" checked={riskConfirmed} onChange={event => setRiskConfirmed(event.target.checked)} disabled={!!busy}>我理解原消息可能已被接受，重试可能造成重复通知。</Checkbox>}
      </Flex>
    </Modal>}
  </section>;
}
