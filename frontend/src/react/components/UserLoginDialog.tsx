import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Checkbox, Flex, Form, Input, Modal, Spin, Tag, Typography } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
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
    catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "Login settings unavailable"); }
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
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "Password reset failed"); }
    finally { if (run === version.current) { setBusy(false); setPassword(""); setConfirm(""); } }
  }
  return <Modal open title="User login" width={520} destroyOnHidden mask={{ closable: !busy }} keyboard={!busy} closable={!busy}
    onCancel={() => !busy && onOpenChange(false)} onOk={() => void submit()} okText="Save password" cancelText="Close"
    confirmLoading={busy} okButtonProps={{ "aria-label": "Save password", "aria-busy": busy, disabled: !valid }} cancelButtonProps={{ disabled: busy }}>
    <Flex vertical gap="middle">
      <Flex justify="space-between"><Typography.Text strong>{username}</Typography.Text><Button icon={<ReloadOutlined />} aria-label="Reload login settings" disabled={busy} onClick={() => void load()} /></Flex>
      {busy && <Spin />}{error && <Alert type="error" title={error} showIcon />}
      {saved && <Alert type="success" title="Login password saved. Existing sessions have been revoked." showIcon />}
      {account && <Form layout="vertical" preserve={false} onFinish={() => void submit()}>
        <Flex wrap gap="small"><Tag>{account.configured ? "Password configured" : "Login not configured"}</Tag><Tag>Two-factor: {account.totp_enabled ? "On" : "Off"}</Tag></Flex>
        <Form.Item label="New login password"><Input.Password aria-label="New login password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="new-password" maxLength={1024} disabled={busy} /></Form.Item>
        <Form.Item label="Confirm login password" validateStatus={confirm && confirm !== password ? "error" : undefined} help={confirm && confirm !== password ? "Passwords do not match" : undefined}>
          <Input.Password aria-label="Confirm login password" value={confirm} onChange={event => setConfirm(event.target.value)} autoComplete="new-password" maxLength={1024} disabled={busy} />
        </Form.Item>
        <Flex vertical gap="small">
          {account.totp_enabled && <Checkbox checked={resetTotp} onChange={event => setResetTotp(event.target.checked)} disabled={busy}>Reset two-factor authentication and recovery codes</Checkbox>}
          <Checkbox checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} disabled={busy}>Revoke all existing user sessions</Checkbox>
        </Flex>
      </Form>}
    </Flex>
  </Modal>;
}
