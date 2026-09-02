import { ReloadOutlined, SaveOutlined, UserOutlined } from "../../ui/icons";
import { Alert, Avatar, Button, Card, Flex, Form, Input, Space, Typography } from "../../ui";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  loadAdministratorProfile,
  saveAdministratorProfile,
  type AdministratorProfile,
  type OperatorSession,
} from "../../services/auth";
import { requestFailureMessage } from "../../services/request-error";
import { useAsyncScope } from "../hooks/useAsyncScope";

export default function AdministratorProfilePanel({ operator }: { operator: OperatorSession }) {
  const scope = useAsyncScope(), busyRef = useRef(false);
  const [saved, setSaved] = useState<AdministratorProfile | null>(null);
  const [draft, setDraft] = useState({ email: "", nickname: "", avatar_url: "" });
  const [busy, setBusy] = useState<"" | "read" | "save">(""), [notice, setNotice] = useState("");
  const apply = useCallback((profile: AdministratorProfile) => {
    setSaved(profile); setDraft({ email: profile.email, nickname: profile.nickname, avatar_url: profile.avatar_url });
  }, []);
  const read = useCallback(async () => {
    if (busyRef.current) return;
    const generation = scope.begin(); busyRef.current = true; setBusy("read"); setNotice("");
    try { const value = await loadAdministratorProfile(); if (scope.isCurrent(generation)) apply(value); }
    catch (error) { if (scope.isCurrent(generation)) setNotice(requestFailureMessage(error, "无法读取管理员资料。")); }
    finally { if (scope.isCurrent(generation)) { busyRef.current = false; setBusy(""); } }
  }, [apply, scope]);
  useEffect(() => { void read(); return () => { busyRef.current = false; scope.invalidate(); }; }, [read, scope]);

  const dirty = Boolean(saved && (saved.email !== draft.email || saved.nickname !== draft.nickname || saved.avatar_url !== draft.avatar_url));
  async function save() {
    if (!saved || !dirty || busyRef.current) return;
    const generation = scope.begin(); busyRef.current = true; setBusy("save"); setNotice("");
    try {
      const value = await saveAdministratorProfile({ ...draft, revision: saved.revision });
      if (scope.isCurrent(generation)) { apply(value); setNotice("管理员资料已保存。"); }
    } catch (error) {
      if (!scope.isCurrent(generation)) return;
      setNotice(`${requestFailureMessage(error, "未能确认管理员资料是否保存。")}正在重新读取，没有自动重复提交。`);
      try { const current = await loadAdministratorProfile(); if (scope.isCurrent(generation)) apply(current); } catch { /* Manual reload remains available. */ }
    } finally { if (scope.isCurrent(generation)) { busyRef.current = false; setBusy(""); } }
  }

  return <Card title="管理员资料">
    {notice && <Alert type={notice === "管理员资料已保存。" ? "success" : "warning"} showIcon title={notice} style={{ marginBottom: 16 }} />}
    <Flex gap="large" align="flex-start" wrap>
      <Avatar size={72} src={saved?.avatar_url || undefined} icon={<UserOutlined />} alt="管理员头像" />
      <Form layout="vertical" onFinish={() => void save()} className="form-narrow" style={{ flex: "1 1 360px" }} disabled={Boolean(busy) || !saved}>
        <Typography.Paragraph type="secondary">账户：{operator.username}。昵称、邮箱和头像仅用于面板资料展示，不改变登录用户名。</Typography.Paragraph>
        <Form.Item label="昵称" htmlFor="administrator-profile-nickname"><Input id="administrator-profile-nickname" maxLength={120} value={draft.nickname} onChange={event => setDraft(value => ({ ...value, nickname: event.target.value }))} autoComplete="name" /></Form.Item>
        <Form.Item label="邮箱" htmlFor="administrator-profile-email"><Input id="administrator-profile-email" type="email" maxLength={254} value={draft.email} onChange={event => setDraft(value => ({ ...value, email: event.target.value }))} autoComplete="email" /></Form.Item>
        <Form.Item label="头像 HTTPS 地址" htmlFor="administrator-profile-avatar" extra="留空显示默认头像；主控不代为上传图片。"><Input id="administrator-profile-avatar" type="url" maxLength={2048} value={draft.avatar_url} onChange={event => setDraft(value => ({ ...value, avatar_url: event.target.value }))} autoComplete="url" /></Form.Item>
        <Space wrap>
          <Button type="primary" htmlType="submit" aria-label="保存管理员资料" icon={<SaveOutlined aria-hidden />} loading={busy === "save"} disabled={!dirty}>保存管理员资料</Button>
          <Button aria-label="重新读取管理员资料" icon={<ReloadOutlined aria-hidden />} loading={busy === "read"} disabled={Boolean(busy)} onClick={() => void read()}>重新读取管理员资料</Button>
        </Space>
      </Form>
    </Flex>
  </Card>;
}
