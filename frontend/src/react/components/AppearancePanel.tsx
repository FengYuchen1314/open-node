import { ReloadOutlined, SaveOutlined, UploadOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Form, Input, Segmented, Space, Typography, Upload } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { defaultAppearance, validImageUrl, type AppearanceSettings, type SiteTheme } from "../../domain/appearance";
import { authState, type OperatorSession } from "../../services/auth";
import { AppearanceRequestError, appearanceErrorMessage, getAppearanceSettings, updateAppearance, uploadAppearanceImage } from "../../services/appearance";
import { useAppearance } from "../hooks/useAppearance";
import { useAsyncScope } from "../hooks/useAsyncScope";

export default function AppearancePanel({ operator }: { operator: OperatorSession }) {
  const scope = useAsyncScope(), { captureRead, acceptRead, acceptSaved } = useAppearance();
  const busyRef = useRef(false);
  const [saved, setSaved] = useState<AppearanceSettings | null>(null);
  const [draft, setDraft] = useState({ default_theme: defaultAppearance.default_theme,
    logo_url: "", wallpaper_url: "" });
  const [files, setFiles] = useState<{ logo: File | null; wallpaper: File | null }>({ logo: null, wallpaper: null });
  const [busy, setBusy] = useState(""), [needsRead, setNeedsRead] = useState(true);
  const [notice, setNotice] = useState<{ type: "error" | "warning" | "success"; text: string } | null>(null);
  const current = useCallback((request: number) => scope.isCurrent(request)
    && authState.session?.authenticated === true && authState.session.username === operator.username
    && authState.session.csrf_token === operator.csrf_token, [scope, operator]);
  const apply = useCallback((value: AppearanceSettings) => {
    setSaved(value); setDraft({ default_theme: value.default_theme, logo_url: value.logo_url,
      wallpaper_url: value.wallpaper_url }); setNeedsRead(false);
  }, []);
  const read = useCallback(async (request: number) => {
    const generation = captureRead(), value = await getAppearanceSettings();
    if (!current(request)) return false;
    if (!acceptRead(value, generation)) throw new AppearanceRequestError(409, "appearance_revision_conflict");
    apply(value); return true;
  }, [acceptRead, apply, captureRead, current]);
  const reload = useCallback(async () => {
    if (busyRef.current) return;
    const request = scope.begin(); busyRef.current = true; setBusy("read"); setNotice(null);
    try { await read(request); }
    catch (error) { if (current(request)) { setNeedsRead(true); setNotice({ type: "error", text: appearanceErrorMessage(error) }); } }
    finally { if (current(request)) { busyRef.current = false; setBusy(""); } }
  }, [current, read, scope]);
  useEffect(() => { busyRef.current = false; void reload(); return () => scope.invalidate(); }, [reload, scope]);
  const logoOk = validImageUrl(draft.logo_url.trim(), "logo"), wallpaperOk = validImageUrl(draft.wallpaper_url.trim(), "wallpaper");
  const dirty = Boolean(saved && (saved.default_theme !== draft.default_theme || saved.logo_url !== draft.logo_url.trim()
    || saved.wallpaper_url !== draft.wallpaper_url.trim()));
  const canSave = Boolean(saved && !needsRead && logoOk && wallpaperOk && dirty && !busy);
  async function reconcile(request: number, reason: string) {
    setNeedsRead(true); setNotice({ type: "warning", text: `${reason}正在重新读取，没有自动重复提交。` });
    try { if (await read(request)) setNotice({ type: "warning", text: `${reason}已重新读取，请核对；没有自动重复提交。` }); }
    catch { if (current(request)) setNotice({ type: "warning", text: `${reason}重新读取仍未成功，请手动重新读取。` }); }
  }
  async function run(kind: "save" | "logo" | "wallpaper") {
    if (busyRef.current || !saved || needsRead || (kind === "save" && !canSave)) return;
    const file = kind === "save" ? null : files[kind]; if (kind !== "save" && !file) return;
    const request = scope.begin(); busyRef.current = true; setBusy(kind); setNotice(null);
    if (kind !== "save") setFiles(previous => ({ ...previous, [kind]: null }));
    try {
      const result = kind === "save" ? await updateAppearance({ expected_revision: saved.revision,
        default_theme: draft.default_theme, logo_url: draft.logo_url.trim(),
        wallpaper_url: draft.wallpaper_url.trim(), license_required: false })
        : await uploadAppearanceImage(kind, saved.revision, file!);
      if (!current(request)) return;
      if (!acceptSaved(result)) throw new AppearanceRequestError(409, "appearance_revision_conflict");
      apply(result); setNotice({ type: "success", text: kind === "save" ? "外观设置已保存。" : "图片已上传并启用。" });
    } catch (error) {
      if (!current(request)) return;
      const uncertain = !(error instanceof AppearanceRequestError) || error.outcomeUnknown;
      const conflict = error instanceof AppearanceRequestError && error.code === "appearance_revision_conflict";
      if (uncertain || conflict) await reconcile(request, uncertain ? "未收到有效回执，操作结果尚未确认。" : appearanceErrorMessage(error));
      else setNotice({ type: "error", text: appearanceErrorMessage(error) });
    } finally { if (current(request)) { busyRef.current = false; setBusy(""); } }
  }
  const choose = (slot: "logo" | "wallpaper", file: File) => {
    const maximum = slot === "logo" ? 2 * 1024 * 1024 : 10 * 1024 * 1024;
    if (!file.size || file.size > maximum) { setNotice({ type: "error", text: `图片不能超过 ${slot === "logo" ? 2 : 10} MiB。` }); return Upload.LIST_IGNORE; }
    setFiles(previous => ({ ...previous, [slot]: file })); setNotice(null); return Upload.LIST_IGNORE;
  };
  return <Card title="Logo、登录背景与主题" className="branding-settings-card">
    <Alert type="warning" showIcon title="图片和外部地址会公开给访客，请勿上传秘密、私人照片或带敏感参数的地址。外部图片由访客浏览器直接访问。" />
    {notice && <Alert className="form-alert" type={notice.type} showIcon title={notice.text} role="alert" />}
    {needsRead && saved && <Alert className="form-alert" type="warning" showIcon title="当前版本尚未确认，请先重新读取。" />}
    <Form layout="vertical" className="form-narrow" disabled={Boolean(busy) || !saved} onFinish={() => void run("save")}>
      <Form.Item label="默认主题"><Segmented block value={draft.default_theme} onChange={value => setDraft(previous => ({ ...previous, default_theme: value as SiteTheme }))} options={[{ label: "浅色", value: "light" }, { label: "深色", value: "dark" }, { label: "跟随系统", value: "system" }]} /></Form.Item>
      <Form.Item label="Logo 地址" htmlFor="appearance-logo-url" validateStatus={!logoOk ? "error" : undefined} help={!logoOk ? "请输入公开 HTTPS 地址，或清空后上传图片。" : "HTTPS 外链需允许匿名跨域图片；清空并保存会删除已上传的 Logo。"}><Input id="appearance-logo-url" value={draft.logo_url} onChange={event => setDraft(previous => ({ ...previous, logo_url: event.target.value }))} autoComplete="off" /></Form.Item>
      <Space wrap className="form-alert"><Upload accept=".png,.jpg,.jpeg,.webp,.gif,.svg,.ico" showUploadList={false} beforeUpload={file => choose("logo", file)}><Button icon={<UploadOutlined aria-hidden />}>选择 Logo</Button></Upload><Typography.Text>{files.logo ? "已选择新图片，尚未上传。" : "PNG/JPEG/WebP/GIF/SVG/ICO，最多 2 MiB。"}</Typography.Text><Button disabled={!files.logo || Boolean(busy)} loading={busy === "logo"} onClick={() => void run("logo")}>上传并启用 Logo</Button></Space>
      <Form.Item label="登录背景地址" htmlFor="appearance-wallpaper-url" validateStatus={!wallpaperOk ? "error" : undefined} help={!wallpaperOk ? "请输入公开 HTTPS 地址，或清空后上传图片。" : "只显示在管理员和用户登录页；清空并保存会删除已上传背景。"}><Input id="appearance-wallpaper-url" value={draft.wallpaper_url} onChange={event => setDraft(previous => ({ ...previous, wallpaper_url: event.target.value }))} autoComplete="off" /></Form.Item>
      <Space wrap className="form-alert"><Upload accept=".png,.jpg,.jpeg,.webp,.gif,.svg,.ico" showUploadList={false} beforeUpload={file => choose("wallpaper", file)}><Button icon={<UploadOutlined aria-hidden />}>选择登录背景</Button></Upload><Typography.Text>{files.wallpaper ? "已选择新图片，尚未上传。" : "最多 10 MiB，建议使用横向图片。"}</Typography.Text><Button disabled={!files.wallpaper || Boolean(busy)} loading={busy === "wallpaper"} onClick={() => void run("wallpaper")}>上传并启用登录背景</Button></Space>
      <Space wrap><Button type="primary" htmlType="submit" icon={<SaveOutlined aria-hidden />} disabled={!canSave} loading={busy === "save"}>保存外观设置</Button><Button icon={<ReloadOutlined aria-hidden />} disabled={Boolean(busy)} loading={busy === "read"} onClick={() => void reload()}>重新读取外观设置</Button></Space>
    </Form>
  </Card>;
}
