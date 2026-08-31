import { Alert, Button, Card, Descriptions, Flex, Form, Input, Modal, Spin, Typography } from "antd";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { validRenewalPassphrase, type AccountRenewals, type RenewalRequest } from "../../domain/renewals";
import { cancelRenewal, getAccountRenewal, getAccountRenewals, newRenewalRequestId, renewalCodeMessage, renewalErrorMessage, RenewalRequestError, submitRenewal } from "../../services/renewals";
import { loadSubscriberSession } from "../../services/subscriber-auth";
import RenewalHistory, { renewalDate } from "../components/RenewalHistory";
import { useAsyncScope } from "../hooks/useAsyncScope";
import { useSubscriberSession } from "../hooks/useSession";

export default function RenewalRequestView() {
  const auth = useSubscriberSession();
  useEffect(() => { if (!auth.ready) void loadSubscriberSession(); }, [auth.ready]);
  if (!auth.ready) return <div role="status" aria-label="正在读取用户会话"><Spin /></div>;
  if (!auth.session?.authenticated) return <Card><Alert type="warning" title="请登录用户账户后申请续费。" /><Link to="/account">前往用户中心登录</Link></Card>;
  return <RenewalWorkspace key={`${auth.session.username}:${auth.session.csrf_token}`} />;
}

function RenewalWorkspace() {
  const scope = useAsyncScope(), busyRef = useRef(false);
  const [data, setData] = useState<AccountRenewals | null>(null), [offset, setOffset] = useState(0);
  const [passphrase, setPassphrase] = useState(""), [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [uncertainId, setUncertainId] = useState<string | null>(null), [cancelTarget, setCancelTarget] = useState<RenewalRequest | null>(null);
  async function load(next = offset) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); const run = scope.begin();
    try { const value = await getAccountRenewals(next); if (scope.isCurrent(run)) { setData(value); setOffset(next); setError(""); } }
    catch (failure) { if (scope.isCurrent(run)) setError(renewalErrorMessage(failure)); }
    finally { if (scope.isCurrent(run)) { busyRef.current = false; setBusy(false); } }
  }
  useEffect(() => { void load(0); return () => { busyRef.current = false; }; }, []);
  async function submit() {
    if (busyRef.current || !data?.eligible || uncertainId || !validRenewalPassphrase(passphrase)) return;
    const requestId = newRenewalRequestId(), secret = passphrase.trim(), run = scope.begin();
    busyRef.current = true; setBusy(true); setPassphrase(""); setError(""); setNotice("");
    try {
      const value = await submitRenewal({ request_id: requestId, passphrase: secret });
      if (!scope.isCurrent(run)) return;
      setNotice("续费申请已提交，等待管理员审核。请将同一口令提供给管理员核对。");
      setData(previous => previous ? { ...previous, eligible: false, unavailable_code: "renewal_pending", requests: [value, ...previous.requests.filter(row => row.id !== value.id)].slice(0, 20), total: previous.total + 1 } : previous);
    } catch (failure) {
      if (scope.isCurrent(run)) { setError(renewalErrorMessage(failure)); if (!(failure instanceof RenewalRequestError) || failure.outcomeUnknown) setUncertainId(requestId); }
    } finally { if (scope.isCurrent(run)) { busyRef.current = false; setBusy(false); } }
  }
  async function lookup() {
    if (busyRef.current || !uncertainId) return;
    const run = scope.begin(); busyRef.current = true; setBusy(true);
    try {
      const row = await getAccountRenewal(uncertainId);
      if (!scope.isCurrent(run)) return;
      setUncertainId(null); setError(""); setNotice("已找到原申请，请在申请记录中查看审核结果。");
      setData(previous => previous ? { ...previous, eligible: false, requests: [row, ...previous.requests.filter(item => item.id !== row.id)].slice(0, 20), total: Math.max(1, previous.total) } : previous);
      const overview = await getAccountRenewals(0);
      if (scope.isCurrent(run)) { setData(overview); setOffset(0); }
    } catch (failure) { if (scope.isCurrent(run)) setError(renewalErrorMessage(failure)); }
    finally { if (scope.isCurrent(run)) { busyRef.current = false; setBusy(false); } }
  }
  async function cancel() {
    if (busyRef.current || !cancelTarget) return;
    const run = scope.begin(); busyRef.current = true; setBusy(true);
    try {
      const row = await cancelRenewal(cancelTarget.id);
      if (scope.isCurrent(run)) {
        setData(previous => previous ? { ...previous, eligible: false, requests: previous.requests.map(item => item.id === row.id ? row : item) } : previous);
        setCancelTarget(null); setNotice("续费申请已撤回，套餐未延期。"); setError("");
        const overview = await getAccountRenewals(offset);
        if (scope.isCurrent(run)) setData(overview);
      }
    } catch (failure) { if (scope.isCurrent(run)) { setError(renewalErrorMessage(failure)); setCancelTarget(null); } }
    finally { if (scope.isCurrent(run)) { busyRef.current = false; setBusy(false); } }
  }
  return <Flex vertical gap="middle" style={{ padding: 24, maxWidth: 1200, margin: "0 auto" }}>
    <Flex justify="space-between" align="center"><Typography.Title level={2}>申请续费</Typography.Title><Link to="/account">返回用户中心</Link></Flex>
    <Alert type="info" showIcon title="提交申请不会自动扣款或延长套餐，需由管理员人工核对后批准。" description="续费只延长套餐时间，保留已有流量用量和重置规则。口令仅用于本次核对，请勿填写登录密码、银行卡信息或支付密码。" />
    {error && <Alert type="error" role="alert" showIcon title={error} />}{notice && <Alert type="success" role="status" showIcon title={notice} />}
    {uncertainId && <Alert type="warning" title="申请结果尚未确认" description={<><Typography.Paragraph copyable>{uncertainId}</Typography.Paragraph><Button loading={busy} onClick={() => void lookup()}>查询原申请</Button></>} />}
    <Card title="当前套餐" extra={<Button disabled={busy} onClick={() => void load()}>刷新续费记录</Button>}>
      {data ? <><Descriptions items={[{ key: "plan", label: "套餐", children: data.plan_name ?? "暂无套餐" }, { key: "days", label: "续费周期", children: data.renew_days ? `${data.renew_days} 天` : "—" }, { key: "expires", label: "到期时间", children: renewalDate(data.plan_expires_at) }]} />
        {!data.eligible && <Alert type="info" title={renewalCodeMessage(data.unavailable_code)} />}
        <Form layout="vertical" onFinish={() => void submit()} style={{ marginTop: 16 }}>
          <Form.Item label="续费口令" htmlFor="renewal-passphrase" extra="请自行记录此口令并提供给管理员。系统仅保存口令校验值，不支持找回原文。">
            <Input.Password id="renewal-passphrase" autoComplete="off" value={passphrase} onChange={event => setPassphrase(event.target.value)} disabled={busy || !data.eligible || !!uncertainId} />
          </Form.Item><Button type="primary" htmlType="submit" loading={busy} disabled={!data.eligible || !!uncertainId || !validRenewalPassphrase(passphrase)}>提交续费申请</Button>
        </Form></> : <Spin />}
    </Card>
    <Card title="申请记录"><RenewalHistory rows={data?.requests ?? []} total={data?.total ?? 0} offset={offset} busy={busy} onPage={next => void load(next)} action={row => row.status === "pending" ? <Button danger disabled={busy} onClick={() => setCancelTarget(row)}>撤回申请</Button> : "—"} /></Card>
    <Modal open={!!cancelTarget} title="撤回续费申请" okText="确认撤回" cancelText="返回" confirmLoading={busy} onOk={() => void cancel()} onCancel={() => { if (!busy) setCancelTarget(null); }}>
      <Typography.Paragraph>撤回后管理员不能继续批准这笔申请。此操作不改变当前套餐或已经完成的付款。</Typography.Paragraph>
    </Modal>
  </Flex>;
}
