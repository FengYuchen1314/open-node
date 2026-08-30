import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Descriptions, Flex, Form, Input, Modal, Spin, Typography } from "antd";
import { CopyOutlined, ReloadOutlined } from "@ant-design/icons";
import type { ProductUserSubscriptionToken } from "../../domain/subscriptions";
import { shortCodeError } from "../../domain/subscription-links";
import { getProductUserSubscriptionToken, updateProductUserShortCode } from "../../services/subscriptions";
import { subscriberSecurity, subscriberShortCode, subscriberToken } from "../../services/subscriber-auth";

export interface SubscriptionShortCodeDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  username: string;
  subscriber?: boolean;
  onSaved?: (value: ProductUserSubscriptionToken) => void;
}

export default function SubscriptionShortCodeDialog(props: SubscriptionShortCodeDialogProps) {
  return props.open ? <ShortCodeContent key={`${props.username}:${!!props.subscriber}`} {...props} /> : null;
}

function ShortCodeContent({ username, subscriber = false, onOpenChange, onSaved }: SubscriptionShortCodeDialogProps) {
  const [detail, setDetail] = useState<ProductUserSubscriptionToken | null>(null);
  const [custom, setCustom] = useState("");
  const [password, setPassword] = useState("");
  const [factor, setFactor] = useState("");
  const [needsFactor, setNeedsFactor] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const version = useRef(0);
  const load = useCallback(async () => {
    const run = ++version.current;
    setDetail(null); setCustom(""); setError(""); setSaved(false); setPassword(""); setFactor(""); setNeedsFactor(false);
    if (!username) return;
    setBusy(true);
    try {
      const value = subscriber ? await subscriberToken() : (await getProductUserSubscriptionToken(username)).subscription;
      const security = subscriber ? await subscriberSecurity() : null;
      if (run !== version.current) return;
      setDetail(value); setCustom(value.custom_short_code ?? ""); setNeedsFactor(!!security?.totp_enabled);
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "Subscription links unavailable"); }
    finally { if (run === version.current) setBusy(false); }
  }, [username, subscriber]);
  useEffect(() => { void load(); return () => { ++version.current; }; }, [load]);
  const code = custom.trim(), invalid = shortCodeError(custom);
  const canSave = !!detail?.revision && !busy && !invalid && code !== (detail.custom_short_code ?? "")
    && (!subscriber || (!!password && (!needsFactor || !!factor.trim())));
  async function save() {
    if (!canSave || !detail) return;
    const run = ++version.current;
    setBusy(true); setError(""); setSaved(false);
    try {
      const value = subscriber ? await subscriberShortCode(code, detail.revision, { password, code: factor })
        : (await updateProductUserShortCode(username, code, detail.revision)).subscription;
      if (run !== version.current) return;
      setDetail(value); setCustom(value.custom_short_code ?? ""); setSaved(true); onSaved?.(value);
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "Short code update failed"); }
    finally { if (run === version.current) { setBusy(false); setPassword(""); setFactor(""); } }
  }
  async function copy() {
    const run = version.current;
    try { if (detail) await navigator.clipboard.writeText(detail.short_url); }
    catch { if (run === version.current) setError("Clipboard unavailable"); }
  }
  return <Modal open title="Subscription short code" width={560} destroyOnHidden mask={{ closable: !busy }} keyboard={!busy} closable={!busy}
    onCancel={() => !busy && onOpenChange(false)} onOk={() => void save()} okText="Save" cancelText={saved ? "Close" : "Cancel"}
    confirmLoading={busy} okButtonProps={{ "aria-label": "Save", "aria-busy": busy, disabled: !canSave }} cancelButtonProps={{ disabled: busy }}>
    <Flex vertical gap="middle">
      <Flex justify="space-between"><Typography.Text strong>{username}</Typography.Text><Button icon={<ReloadOutlined />} aria-label="Reload subscription links" disabled={busy} onClick={() => void load()} /></Flex>
      {busy && <Spin />}{error && <Alert type="error" title={error} showIcon />}{saved && <Alert type="success" title="Short code saved" showIcon />}
      {detail && <Form layout="vertical" preserve={false} onFinish={() => void save()}>
        <Descriptions column={2} items={[{ key: "system", label: "System code", children: detail.generated_short_code }, { key: "current", label: "Current code", children: detail.short_code }]} />
        <Form.Item label="Custom short code" validateStatus={invalid ? "error" : undefined} help={invalid || undefined}>
          <Input aria-label="Custom short code" value={custom} onChange={event => setCustom(event.target.value)} autoComplete="off" autoCapitalize="off" spellCheck={false} maxLength={16} allowClear disabled={busy} />
        </Form.Item>
        <Form.Item label="Short URL"><Flex gap="small"><Input aria-label="Short URL" value={detail.short_url} readOnly /><Button icon={<CopyOutlined />} aria-label="Copy short URL" onClick={() => void copy()} /></Flex></Form.Item>
        <Alert type="warning" title="Anyone with a subscription link can download its configuration. Custom short codes can be guessed." showIcon />
        {!!detail.custom_short_code && detail.custom_short_code !== detail.generated_short_code && code !== detail.custom_short_code && <Alert type="warning" title="The previous custom link will stop working." showIcon />}
        {subscriber && <Form.Item label="Current password"><Input.Password aria-label="Current password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="current-password" maxLength={1024} disabled={busy} /></Form.Item>}
        {subscriber && needsFactor && <Form.Item label="Authenticator or recovery code"><Input aria-label="Authenticator or recovery code" value={factor} onChange={event => setFactor(event.target.value)} autoComplete="one-time-code" maxLength={64} disabled={busy} /></Form.Item>}
      </Form>}
    </Flex>
  </Modal>;
}
