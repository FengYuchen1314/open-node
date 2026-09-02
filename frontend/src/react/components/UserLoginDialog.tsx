import { zhMessage } from "../../i18n/zh-CN";
import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Checkbox, Flex, Form, Input, Modal, Spin, Tag, Typography } from "../../ui";
import { ReloadOutlined } from "../../ui/icons";
import { subscriberAccount, type SubscriberAccount } from "../../services/subscriber-auth";

export interface UserLoginDialogProps { username: string; open: boolean; onOpenChange: (open: boolean) => void }
export default function UserLoginDialog(props: UserLoginDialogProps) { return props.open ? <LoginContent key={props.username} {...props} /> : null; }

function LoginContent({ username, onOpenChange }: UserLoginDialogProps) {
  const [account, setAccount] = useState<SubscriberAccount | null>(null);
  const [password, setPassword] = useState(""), [confirm, setConfirm] = useState("");
  const [resetTotp, setResetTotp] = useState(false), [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [saved, setSaved] = useState(false);
  const version = useRef(0);
  const load = useCallback(async () => {
    const run = ++version.current;
    setAccount(null); setPassword(""); setConfirm(""); setResetTotp(false); setAcknowledged(false); setSaved(false); setError(""); setBusy(true);
    try { const result = await subscriberAccount(username); if (run === version.current) setAccount(result); }
    catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "无法读取登录设置"); }
    finally { if (run === version.current) setBusy(false); }
  }, [username]);
  useEffect(() => { void load(); return () => { ++version.current; }; }, [load]);
  const valid = !!account && password.length >= 12 && password === confirm && acknowledged && !busy;
  async function submit() {
    if (!valid || !account) return;
    const run = ++version.current; setBusy(true); setError(""); setSaved(false);
    try {
      const result = await subscriberAccount(username, { expected_revision: account.revision, new_password: password, reset_totp: resetTotp });
      if (run !== version.current) return;
      setAccount(result); setSaved(true); setAcknowledged(false); setResetTotp(false);
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "重置密码失败"); }
    finally { if (run === version.current) { setBusy(false); setPassword(""); setConfirm(""); } }
  }
  return <Modal open title="用户登录" width={520} destroyOnHidden mask={{ closable: !busy }} keyboard={!busy} closable={!busy}
    onCancel={() => !busy && onOpenChange(false)} onOk={() => void submit()} okText="保存密码" cancelText="关闭"
    confirmLoading={busy} okButtonProps={{ "aria-label": "保存密码", "aria-busy": busy, disabled: !valid }} cancelButtonProps={{ disabled: busy }}>
    <Flex vertical gap="middle">
      <Flex justify="space-between"><Typography.Text strong>{username}</Typography.Text><Button icon={<ReloadOutlined />} aria-label="重新加载登录设置" disabled={busy} onClick={() => void load()} /></Flex>
      {busy && <Spin />}{error && <Alert type="error" title={zhMessage(error)} showIcon />}
      {saved && <Alert type="success" title="登录密码已保存，已有会话已全部撤销。" showIcon />}
      {account && <Form layout="vertical" preserve={false} onFinish={() => void submit()}>
        <Flex wrap gap="small"><Tag>{account.configured ? "已设置密码" : "未设置登录方式"}</Tag><Tag>双因素认证：{account.totp_enabled ? "已启用" : "未启用"}</Tag></Flex>
        <Form.Item label="新登录密码"><Input.Password aria-label="新登录密码" value={password} onChange={event => setPassword(event.target.value)} autoComplete="new-password" maxLength={1024} disabled={busy} /></Form.Item>
        <Form.Item label="确认登录密码" validateStatus={confirm && confirm !== password ? "error" : undefined} help={confirm && confirm !== password ? "两次输入的密码不一致" : undefined}>
          <Input.Password aria-label="确认登录密码" value={confirm} onChange={event => setConfirm(event.target.value)} autoComplete="new-password" maxLength={1024} disabled={busy} />
        </Form.Item>
        <Flex vertical gap="small">
          {account.totp_enabled && <Checkbox checked={resetTotp} onChange={event => setResetTotp(event.target.checked)} disabled={busy}>重置双因素认证及恢复码</Checkbox>}
          <Checkbox checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} disabled={busy}>撤销该用户的所有已有会话</Checkbox>
        </Flex>
      </Form>}
    </Flex>
  </Modal>;
}
