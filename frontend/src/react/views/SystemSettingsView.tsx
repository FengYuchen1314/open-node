import { ReloadOutlined, SaveOutlined, UndoOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Form, Input, Space, Typography } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { defaultBranding, normalizeBrandingText, type BrandingSettings } from "../../domain/branding";
import { authState, type OperatorSession } from "../../services/auth";
import { BrandingRequestError, brandingErrorMessage, getBrandingSettings, updateBrandingSettings } from "../../services/branding";
import { useAsyncScope } from "../hooks/useAsyncScope";
import { useBranding } from "../hooks/useBranding";
import { useAdministratorSession } from "../hooks/useSession";
import AppearancePanel from "../components/AppearancePanel";
import AnnouncementsPanel from "../components/AnnouncementsPanel";
import ApplicationUpdatePanel from "../components/ApplicationUpdatePanel";
import SubscriberPermissionsPanel from "../components/SubscriberPermissionsPanel";

export default function SystemSettingsView() {
  const auth = useAdministratorSession();
  if (!auth.ready || !auth.session?.authenticated) return <Alert type="warning" showIcon title="请登录管理员账户后管理系统设置。" />;
  return <BrandingEditor key={`${auth.session.username}\u0000${auth.session.csrf_token}`} operator={auth.session} />;
}

function BrandingEditor({ operator }: { operator: OperatorSession }) {
  const scope = useAsyncScope();
  const { captureRead, acceptRead, acceptSaved } = useBranding();
  const busyRef = useRef(false);
  const [saved, setSaved] = useState<BrandingSettings | null>(null);
  const [draft, setDraft] = useState({ site_title: defaultBranding.site_title, brand_title: defaultBranding.brand_title });
  const [busy, setBusy] = useState<"" | "read" | "save">("");
  const [needsRead, setNeedsRead] = useState(true);
  const [notice, setNotice] = useState<{ type: "error" | "warning" | "success"; text: string } | null>(null);
  const isCurrent = useCallback((request: number) => scope.isCurrent(request)
    && authState.session?.authenticated === true && authState.session.username === operator.username
    && authState.session.csrf_token === operator.csrf_token, [scope, operator.username, operator.csrf_token]);
  const apply = useCallback((value: BrandingSettings) => {
    setSaved(value); setDraft({ site_title: value.site_title, brand_title: value.brand_title }); setNeedsRead(false);
  }, []);
  const read = useCallback(async (request: number) => {
    const generation = captureRead(), value = await getBrandingSettings();
    if (!isCurrent(request)) return false;
    if (!acceptRead(value, generation)) throw new BrandingRequestError(409, "branding_revision_conflict");
    apply(value); return true;
  }, [acceptRead, apply, captureRead, isCurrent]);
  const reload = useCallback(async () => {
    if (busyRef.current) return;
    const request = scope.begin(); busyRef.current = true; setBusy("read"); setNotice(null);
    try { await read(request); }
    catch (error) { if (isCurrent(request)) { setNeedsRead(true); setNotice({ type: "error", text: brandingErrorMessage(error) }); } }
    finally { if (isCurrent(request)) { busyRef.current = false; setBusy(""); } }
  }, [isCurrent, read, scope]);
  useEffect(() => {
    busyRef.current = false; void reload();
    return () => scope.invalidate();
  }, [reload, scope]);

  const site = normalizeBrandingText(draft.site_title, 80), brand = normalizeBrandingText(draft.brand_title, 40);
  const dirty = Boolean(saved && (draft.site_title !== saved.site_title || draft.brand_title !== saved.brand_title));
  const canSave = Boolean(saved && !needsRead && site !== null && brand !== null && dirty && !busy);
  async function save() {
    if (busyRef.current || !saved || !canSave) return;
    const request = scope.begin(); busyRef.current = true; setBusy("save"); setNotice(null);
    try {
      const value = await updateBrandingSettings({ expected_revision: saved.revision, site_title: site!, brand_title: brand! });
      if (!isCurrent(request)) return;
      if (!acceptSaved(value)) throw new BrandingRequestError(409, "branding_revision_conflict");
      apply(value); setNotice({ type: "success", text: "站点文字已保存。" });
    } catch (error) {
      if (!isCurrent(request)) return;
      const uncertain = !(error instanceof BrandingRequestError) || error.outcomeUnknown;
      const conflict = error instanceof BrandingRequestError && error.code === "branding_revision_conflict";
      if (uncertain || conflict) {
        setNeedsRead(true);
        const reason = uncertain ? "未收到有效的保存回执，保存结果尚未确认。" : brandingErrorMessage(error);
        setNotice({ type: "warning", text: `${reason}正在重新读取当前配置，没有自动重新提交。` });
        try {
          if (await read(request)) setNotice({ type: "warning", text: `${reason}已重新读取当前配置，请核对；没有自动重新提交。` });
        } catch {
          if (isCurrent(request)) setNotice({ type: "warning", text: `${reason}重新读取仍未成功，请手动重新读取；没有自动重新提交。` });
        }
      } else setNotice({ type: "error", text: brandingErrorMessage(error) });
    } finally { if (isCurrent(request)) { busyRef.current = false; setBusy(""); } }
  }

  return <section className="page-shell" aria-label="系统设置">
    <div><Typography.Title level={2}>系统设置</Typography.Title><Typography.Paragraph type="secondary">修改站点展示文字，不改变 Open Node 的技术标识、TOTP 或公共探针标题。</Typography.Paragraph></div>
    <Alert type="warning" showIcon title="这两项文字会公开显示在登录页和其他页面，请勿填写密码、Token 或其他秘密。" />
    {notice && <Alert type={notice.type} showIcon title={notice.text} role="alert" />}
    {needsRead && saved && <Alert type="warning" showIcon title="当前版本尚未确认，请先重新读取站点文字再保存。" />}
    <Card title="站点文字" className="branding-settings-card">
      <Form layout="vertical" onFinish={() => void save()} className="form-narrow" disabled={Boolean(busy) || !saved}>
        <Form.Item label="浏览器标题" htmlFor="branding-site-title" required validateStatus={site === null ? "error" : undefined}
          help={site === null ? "请输入 1 至 80 个 Unicode 字符，不得含换行、控制符或仅不可见字符。" : "用作浏览器标签页的标题后缀；去除首尾空白后最多 80 个 Unicode 字符。"}>
          <Input id="branding-site-title" value={draft.site_title} onChange={event => { setDraft(previous => ({ ...previous, site_title: event.target.value })); setNotice(null); }} autoComplete="off" />
          <Typography.Text type="secondary">{Array.from(draft.site_title.trim()).length} / 80</Typography.Text>
        </Form.Item>
        <Form.Item label="页面品牌文字" htmlFor="branding-brand-title" required validateStatus={brand === null ? "error" : undefined}
          help={brand === null ? "请输入 1 至 40 个 Unicode 字符，不得含换行、控制符或仅不可见字符。" : "显示在登录页、导航和用户中心；去除首尾空白后最多 40 个 Unicode 字符。"}>
          <Input id="branding-brand-title" value={draft.brand_title} onChange={event => { setDraft(previous => ({ ...previous, brand_title: event.target.value })); setNotice(null); }} autoComplete="off" />
          <Typography.Text type="secondary">{Array.from(draft.brand_title.trim()).length} / 40</Typography.Text>
        </Form.Item>
        <Typography.Paragraph type="secondary">{saved ? `已读取版本 ${saved.revision}。${dirty ? "有未保存的草稿。" : "当前表单与已保存配置一致。"}` : "尚未读取已保存配置。"}未保存的草稿不会改变站点。</Typography.Paragraph>
        <Space wrap>
          <Button type="primary" htmlType="submit" icon={<SaveOutlined aria-hidden />} aria-label="保存站点文字" loading={busy === "save"} disabled={!canSave}>保存站点文字</Button>
          <Button icon={<UndoOutlined aria-hidden />} aria-label="恢复默认草稿" disabled={Boolean(busy) || !saved} onClick={() => { setDraft({ site_title: defaultBranding.site_title, brand_title: defaultBranding.brand_title }); setNotice(null); }}>恢复默认草稿</Button>
        </Space>
      </Form>
      <Space orientation="vertical" size="small" style={{ marginTop: 24, maxWidth: "100%" }}>
        <Button icon={<ReloadOutlined aria-hidden />} aria-label="重新读取站点文字" disabled={Boolean(busy)} loading={busy === "read"} onClick={() => void reload()}>重新读取站点文字</Button>
        <Typography.Text type="secondary">恢复默认只回填草稿，仍需保存。重新读取会丢弃未保存的草稿。</Typography.Text>
      </Space>
    </Card>
    <AppearancePanel operator={operator} />
    <SubscriberPermissionsPanel operator={operator} />
    <AnnouncementsPanel operator={operator} />
    <ApplicationUpdatePanel operator={operator} />
  </section>;
}
