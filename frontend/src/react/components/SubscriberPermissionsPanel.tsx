import { ReloadOutlined, SaveOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Checkbox, Form, InputNumber, Space, Typography } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { subscriberFeatureLabels, subscriberFeatures, type SubscriberFeature, type SubscriberPermissionsSettings } from "../../domain/subscriber-permissions";
import { authState, type OperatorSession } from "../../services/auth";
import { getSubscriberPermissions, SubscriberPermissionsRequestError, updateSubscriberPermissions } from "../../services/subscriber-permissions";
import { useAsyncScope } from "../hooks/useAsyncScope";

export default function SubscriberPermissionsPanel({ operator }: { operator: OperatorSession }) {
  const scope = useAsyncScope(), busyRef = useRef(false);
  const [saved, setSaved] = useState<SubscriberPermissionsSettings | null>(null);
  const [draft, setDraft] = useState({ pages: [...subscriberFeatures] as SubscriberFeature[], template_quota: 0, external_source_quota: 0 });
  const [busy, setBusy] = useState<"" | "read" | "save">("");
  const [notice, setNotice] = useState<{ type: "error" | "warning" | "success"; text: string } | null>(null);
  const current = useCallback((request: number) => scope.isCurrent(request)
    && authState.session?.authenticated === true && authState.session.username === operator.username
    && authState.session.csrf_token === operator.csrf_token, [scope, operator]);
  const apply = useCallback((value: SubscriberPermissionsSettings) => {
    setSaved(value); setDraft({ pages: [...value.pages], template_quota: value.template_quota, external_source_quota: value.external_source_quota });
  }, []);
  const reload = useCallback(async () => {
    if (busyRef.current) return;
    const request = scope.begin(); busyRef.current = true; setBusy("read"); setNotice(null);
    try { const value = await getSubscriberPermissions(); if (current(request)) apply(value); }
    catch { if (current(request)) setNotice({ type: "error", text: "用户权限暂时不可用，请稍后重新读取。" }); }
    finally { if (current(request)) { busyRef.current = false; setBusy(""); } }
  }, [apply, current, scope]);
  useEffect(() => { busyRef.current = false; void reload(); return () => scope.invalidate(); }, [reload, scope]);
  const dirty = Boolean(saved && (saved.template_quota !== draft.template_quota
    || saved.external_source_quota !== draft.external_source_quota
    || saved.pages.join("\0") !== draft.pages.join("\0")));
  async function save() {
    if (!saved || !dirty || busyRef.current) return;
    const request = scope.begin(); busyRef.current = true; setBusy("save"); setNotice(null);
    try {
      const value = await updateSubscriberPermissions({ expected_revision: saved.revision, ...draft, license_required: false });
      if (current(request)) { apply(value); setNotice({ type: "success", text: "用户功能权限已保存。" }); }
    } catch (error) {
      if (!current(request)) return;
      if (error instanceof SubscriberPermissionsRequestError && error.code === "subscriber_permissions_revision_conflict") {
        setNotice({ type: "warning", text: "用户权限已被其他操作修改，正在重新读取；没有自动重复提交。" });
        try {
          const value = await getSubscriberPermissions();
          if (current(request)) { apply(value); setNotice({ type: "warning", text: "用户权限已被其他操作修改，已重新读取；没有自动重复提交。" }); }
        } catch { /* retain warning */ }
      } else setNotice({ type: "error", text: error instanceof SubscriberPermissionsRequestError ? error.message : "未能确认用户权限设置，请重新读取。" });
    } finally { if (current(request)) { busyRef.current = false; setBusy(""); } }
  }
  return <Card title="用户功能权限与配额" className="branding-settings-card">
    <Typography.Paragraph type="secondary">控制所有普通用户可以使用的可选功能。订阅和安全设置始终保留；管理员管理入口不受影响。</Typography.Paragraph>
    <Alert type="info" showIcon title="数量上限填写 0 表示不限制。关闭功能后，账户 API 也会拒绝访问，不只是隐藏页面。" />
    {notice && <Alert className="form-alert" type={notice.type} showIcon title={notice.text} />}
    <Form layout="vertical" className="form-narrow" disabled={Boolean(busy) || !saved} onFinish={() => void save()}>
      <Form.Item label="开放的账户功能"><Checkbox.Group aria-label="开放的账户功能" value={draft.pages} options={subscriberFeatures.map(value => ({ value, label: subscriberFeatureLabels[value] }))} onChange={values => setDraft(previous => ({ ...previous, pages: subscriberFeatures.filter(value => values.includes(value)) }))} /></Form.Item>
      <Form.Item label="每位用户的个人模板上限"><InputNumber aria-label="每位用户的个人模板上限" min={0} max={1000} precision={0} value={draft.template_quota} onChange={value => setDraft(previous => ({ ...previous, template_quota: value ?? 0 }))} /></Form.Item>
      <Form.Item label="每位用户的外部订阅来源上限"><InputNumber aria-label="每位用户的外部订阅来源上限" min={0} max={1000} precision={0} value={draft.external_source_quota} onChange={value => setDraft(previous => ({ ...previous, external_source_quota: value ?? 0 }))} /></Form.Item>
      <Space wrap><Button type="primary" htmlType="submit" icon={<SaveOutlined aria-hidden />} loading={busy === "save"} disabled={!dirty}>保存用户权限</Button><Button icon={<ReloadOutlined aria-hidden />} loading={busy === "read"} disabled={Boolean(busy)} onClick={() => void reload()}>重新读取用户权限</Button></Space>
    </Form>
  </Card>;
}
