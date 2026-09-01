import { DeleteOutlined, ReloadOutlined, SendOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Checkbox, Empty, Form, Input, Popconfirm, Select, Space, Tag, Typography } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { safeAnnouncementText, type Announcement, type AnnouncementCreate, type AnnouncementType } from "../../domain/announcements";
import { authState, type OperatorSession } from "../../services/auth";
import { AnnouncementRequestError, announcementErrorMessage, deleteAnnouncement, listAnnouncements, publishAnnouncement } from "../../services/announcements";
import { useAsyncScope } from "../hooks/useAsyncScope";

const typeOptions: { label: string; value: AnnouncementType }[] = [
  { label: "普通公告", value: "general" },
  { label: "系统维护", value: "maintenance" },
  { label: "订阅更新", value: "sub_update" },
];
const typeLabels = Object.fromEntries(typeOptions.map(item => [item.value, item.label])) as Record<AnnouncementType, string>;
const typeColors: Record<AnnouncementType, string> = { general: "blue", maintenance: "orange", sub_update: "green" };
const expiryOptions = [
  { label: "永不过期", value: 0 }, { label: "1 小时", value: 60 },
  { label: "1 天", value: 1440 }, { label: "7 天", value: 10080 },
];
const initialDraft: AnnouncementCreate = { type: "general", title: "", body: "", expires_minutes: 0 };
const displayTime = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "永不过期";

export default function AnnouncementsPanel({ operator }: { operator: OperatorSession }) {
  const scope = useAsyncScope();
  const busyRef = useRef(false);
  const [items, setItems] = useState<Announcement[] | null>(null);
  const [draft, setDraft] = useState<AnnouncementCreate>(initialDraft);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState<{ type: "error" | "warning" | "success"; text: string } | null>(null);
  const current = useCallback((request: number) => scope.isCurrent(request)
    && authState.session?.authenticated === true && authState.session.username === operator.username
    && authState.session.csrf_token === operator.csrf_token, [scope, operator]);
  const read = useCallback(async (request: number) => {
    const value = await listAnnouncements();
    if (!current(request)) return false;
    setItems(value.announcements);
    return true;
  }, [current]);
  const reload = useCallback(async () => {
    if (busyRef.current) return;
    const request = scope.begin(); busyRef.current = true; setBusy("read"); setNotice(null);
    try { await read(request); }
    catch (error) { if (current(request)) setNotice({ type: "error", text: announcementErrorMessage(error) }); }
    finally { if (current(request)) { busyRef.current = false; setBusy(""); } }
  }, [current, read, scope]);
  useEffect(() => { busyRef.current = false; void reload(); return () => scope.invalidate(); }, [reload, scope]);

  const title = draft.title.trim() ? safeAnnouncementText(draft.title, 100) : "";
  const body = safeAnnouncementText(draft.body, 2000, true);
  const canPublish = Boolean(title !== null && body && confirmed && !busy);
  function change(change: Partial<AnnouncementCreate>) {
    setDraft(previous => ({ ...previous, ...change })); setConfirmed(false); setNotice(null);
  }
  async function reconcile(request: number, reason: string) {
    setNotice({ type: "warning", text: `${reason}正在重新读取当前公告，没有自动重复提交。` });
    try {
      if (await read(request)) setNotice({ type: "warning", text: `${reason}已重新读取当前公告，请核对；没有自动重复提交。` });
    } catch {
      if (current(request)) setNotice({ type: "warning", text: `${reason}重新读取仍未成功，请手动重新读取；没有自动重复提交。` });
    }
  }
  async function publish() {
    if (busyRef.current || !canPublish) return;
    const request = scope.begin(); busyRef.current = true; setBusy("publish"); setNotice(null);
    try {
      const value = await publishAnnouncement({ ...draft, title: title!, body: body! });
      if (!current(request)) return;
      setItems(previous => [value, ...(previous ?? []).filter(item => item.id !== value.id)]);
      setDraft(initialDraft); setConfirmed(false); setNotice({ type: "success", text: "公告已发布。" });
    } catch (error) {
      if (!current(request)) return;
      if (!(error instanceof AnnouncementRequestError) || error.outcomeUnknown) await reconcile(request, "未收到有效的发布回执，结果尚未确认。");
      else setNotice({ type: "error", text: announcementErrorMessage(error) });
    } finally { if (current(request)) { busyRef.current = false; setBusy(""); } }
  }
  async function remove(identifier: string) {
    if (busyRef.current) return;
    const request = scope.begin(); busyRef.current = true; setBusy(`delete:${identifier}`); setNotice(null);
    try {
      await deleteAnnouncement(identifier);
      if (!current(request)) return;
      setItems(previous => previous?.filter(item => item.id !== identifier) ?? []);
      setNotice({ type: "success", text: "公告已删除。" });
    } catch (error) {
      if (!current(request)) return;
      if (!(error instanceof AnnouncementRequestError) || error.outcomeUnknown) await reconcile(request, "未收到有效的删除回执，结果尚未确认。");
      else setNotice({ type: "error", text: announcementErrorMessage(error) });
    } finally { if (current(request)) { busyRef.current = false; setBusy(""); } }
  }

  return <Card title="Web 公告" className="branding-settings-card">
    <Alert type="info" showIcon title="公告只在 Web 用户中心向有生效套餐的用户显示；正文按纯文本处理，不会执行 HTML。" />
    {notice && <Alert className="form-alert" type={notice.type} showIcon title={notice.text} role="alert" />}
    <Form layout="vertical" className="form-narrow" disabled={Boolean(busy)} onFinish={() => void publish()}>
      <Form.Item label="公告类型" htmlFor="announcement-type"><Select id="announcement-type" value={draft.type} options={typeOptions} onChange={value => change({ type: value })} /></Form.Item>
      <Form.Item label="标题（可选）" htmlFor="announcement-title" validateStatus={title === null ? "error" : undefined}
        help={title === null ? "标题最多 100 个字符，不能包含换行或控制符。" : "留空时会按公告类型生成默认标题。"}>
        <Input id="announcement-title" value={draft.title} onChange={event => change({ title: event.target.value })} autoComplete="off" />
      </Form.Item>
      <Form.Item label="正文" htmlFor="announcement-body" required validateStatus={body === null ? "error" : undefined}
        help={body === null ? "请输入 1 至 2000 个纯文本字符。" : `${Array.from(draft.body.replace(/\r\n?/g, "\n").trim()).length} / 2000`}>
        <Input.TextArea id="announcement-body" value={draft.body} rows={5} onChange={event => change({ body: event.target.value })} />
      </Form.Item>
      <Form.Item label="有效期" htmlFor="announcement-expiry"><Select id="announcement-expiry" value={draft.expires_minutes} options={expiryOptions} onChange={value => change({ expires_minutes: value })} /></Form.Item>
      <Checkbox checked={confirmed} onChange={event => { setConfirmed(event.target.checked); setNotice(null); }}>确认正文将向有生效套餐的用户显示</Checkbox>
      <div className="form-alert"><Button type="primary" htmlType="submit" icon={<SendOutlined aria-hidden />} loading={busy === "publish"} disabled={!canPublish}>发布公告</Button></div>
    </Form>
    <div className="form-alert"><Space wrap><Typography.Title level={4} style={{ margin: 0 }}>当前生效公告</Typography.Title><Button icon={<ReloadOutlined aria-hidden />} loading={busy === "read"} disabled={Boolean(busy)} onClick={() => void reload()}>重新读取公告</Button></Space></div>
    {items?.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有生效公告" />}
    <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      {items?.map(item => <Card key={item.id} size="small" title={<Space wrap><Tag color={typeColors[item.type]}>{typeLabels[item.type]}</Tag><span>{item.title}</span></Space>}
        extra={<Popconfirm title="删除这条公告？" description="删除后用户中心将不再显示。" okText="删除" cancelText="取消" onConfirm={() => void remove(item.id)} disabled={Boolean(busy)}><Button danger aria-label={`删除公告：${item.title}`} icon={<DeleteOutlined aria-hidden />} loading={busy === `delete:${item.id}`} disabled={Boolean(busy) && busy !== `delete:${item.id}`} /></Popconfirm>}>
        <Typography.Paragraph style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{item.body}</Typography.Paragraph>
        <Typography.Text type="secondary">发布：{displayTime(item.created_at)} · 到期：{displayTime(item.expires_at)}</Typography.Text>
      </Card>)}
    </Space>
  </Card>;
}
