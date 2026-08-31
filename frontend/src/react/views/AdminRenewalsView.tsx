import { Alert, Button, Card, Checkbox, Descriptions, Flex, Form, Input, Modal, Select, Space, Spin, Typography } from "antd";
import { useEffect, useRef, useState } from "react";
import { renewalStatusLabels, renewalStatuses, validRenewalPassphrase, type RenewalRequest, type RenewalsPage, type RenewalStatus } from "../../domain/renewals";
import { listRenewals, renewalErrorMessage, reviewRenewal } from "../../services/renewals";
import RenewalHistory, { renewalDate } from "../components/RenewalHistory";
import { useAsyncScope } from "../hooks/useAsyncScope";
import { useAdministratorSession } from "../hooks/useSession";

export default function AdminRenewalsView() {
  const auth = useAdministratorSession();
  if (!auth.ready) return <div role="status" aria-label="正在读取管理员会话"><Spin /></div>;
  if (!auth.session?.authenticated) return <Alert type="warning" title="请登录管理员账户后审核续费。" />;
  return <ReviewWorkspace key={`${auth.session.username}:${auth.session.csrf_token}`} />;
}
function ReviewWorkspace() {
  const scope = useAsyncScope(), busyRef = useRef(false);
  const [data, setData] = useState<RenewalsPage | null>(null), [status, setStatus] = useState<RenewalStatus | "all">("pending"), [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState(""), [needsRefresh, setNeedsRefresh] = useState(false);
  const [selection, setSelection] = useState<{ row: RenewalRequest; decision: "approve" | "reject" } | null>(null);
  const [passphrase, setPassphrase] = useState(""), [confirmed, setConfirmed] = useState(false);
  async function load(nextStatus = status, nextOffset = offset) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); const run = scope.begin();
    try {
      const value = await listRenewals(nextStatus, nextOffset);
      if (scope.isCurrent(run)) { setData(value); setStatus(nextStatus); setOffset(nextOffset); setError(""); setNeedsRefresh(false); }
    } catch (failure) { if (scope.isCurrent(run)) { setError(renewalErrorMessage(failure)); setNeedsRefresh(true); } }
    finally { if (scope.isCurrent(run)) { busyRef.current = false; setBusy(false); } }
  }
  useEffect(() => { void load(); return () => { busyRef.current = false; }; }, []);
  function select(row: RenewalRequest, decision: "approve" | "reject") { setSelection({ row, decision }); setPassphrase(""); setConfirmed(false); }
  function close() { if (!busy) { setSelection(null); setPassphrase(""); setConfirmed(false); } }
  async function review() {
    if (busyRef.current || !selection || !confirmed || needsRefresh || (selection.decision === "approve" && !validRenewalPassphrase(passphrase))) return;
    const current = selection, secret = passphrase.trim(), run = scope.begin();
    busyRef.current = true; setBusy(true); setPassphrase(""); setError(""); setNotice("");
    try {
      const value = await reviewRenewal(current.row.id, current.decision === "approve" ? { decision: "approve", confirm_reviewed: true, passphrase: secret } : { decision: "reject", confirm_reviewed: true });
      if (!scope.isCurrent(run)) return;
      setSelection(null); setConfirmed(false);
      setData(previous => previous ? { ...previous, requests: previous.requests.map(row => row.id === value.request.id ? value.request : row) } : previous);
      setNotice(!value.processed ? "这笔申请此前已按相同结果处理，没有重复延期。" : current.decision === "approve" ? `续费审核已通过，套餐到期时间更新为 ${renewalDate(value.request.new_end_date)}。Agent 同步结果请查看访问管理。` : "续费申请已拒绝，套餐未延期。");
      if (value.warnings_count) setError("套餐已延期，但部分节点存在配置提示，请到订阅管理和访问管理核对。");
    } catch (failure) {
      if (scope.isCurrent(run)) { setError(renewalErrorMessage(failure)); setSelection(null); setConfirmed(false); setNeedsRefresh(true); }
    } finally { if (scope.isCurrent(run)) { busyRef.current = false; setBusy(false); } }
  }
  return <Flex vertical gap="middle">
    <Typography.Title level={2}>续费审核</Typography.Title>
    <Alert type="info" showIcon title="付款需由管理员人工核对；本页不会发起扣款或校验支付平台。" description="批准时按申请冻结的续费天数延期，以当前时间、当前到期时间和申请时到期时间中最晚者为起点。保留已用流量、重置规则和用户限制。" />
    {error && <Alert type="error" role="alert" showIcon title={error} />}{notice && <Alert type="success" role="status" showIcon title={notice} />}
    <Card title="续费申请" extra={<Space><Select aria-label="申请状态" value={status} disabled={busy || !!selection} onChange={value => void load(value, 0)} options={[{ value: "all", label: "全部状态" }, ...renewalStatuses.map(value => ({ value, label: renewalStatusLabels[value] }))]} /><Button disabled={busy || !!selection} onClick={() => void load()}>刷新申请</Button></Space>}>
      <RenewalHistory administrator rows={data?.requests ?? []} total={data?.total ?? 0} offset={offset} busy={busy} onPage={next => void load(status, next)} action={row => row.status === "pending" ? <Space><Button type="primary" disabled={busy || needsRefresh} onClick={() => select(row, "approve")}>审核通过</Button><Button danger disabled={busy || needsRefresh} onClick={() => select(row, "reject")}>拒绝申请</Button></Space> : "—"} />
    </Card>
    <Modal open={!!selection} title={selection?.decision === "approve" ? "确认续费审核" : "拒绝续费申请"} okText={selection?.decision === "approve" ? "确认通过并延期" : "确认拒绝"} cancelText="返回" confirmLoading={busy} okButtonProps={{ disabled: !confirmed || needsRefresh || (selection?.decision === "approve" && !validRenewalPassphrase(passphrase)), danger: selection?.decision === "reject" }} onOk={() => void review()} onCancel={close} maskClosable={!busy} destroyOnHidden>
      {selection && <><Descriptions column={1} items={[{ key: "user", label: "用户", children: selection.row.username }, { key: "plan", label: "套餐", children: selection.row.plan_name }, { key: "days", label: "续费天数", children: `${selection.row.renew_days} 天` }, { key: "expires", label: "申请时到期时间", children: renewalDate(selection.row.previous_end_date) }]} />
        {selection.decision === "approve" && <Form layout="vertical"><Form.Item label="用户提供的续费口令" htmlFor="review-passphrase"><Input.Password id="review-passphrase" autoComplete="off" value={passphrase} disabled={busy} onChange={event => setPassphrase(event.target.value)} /></Form.Item></Form>}
        <Checkbox checked={confirmed} disabled={busy} onChange={event => setConfirmed(event.target.checked)}>{selection.decision === "approve" ? "已人工核对续费信息，同意延长此用户的套餐" : "确认拒绝此申请，不修改用户套餐"}</Checkbox>
      </>}
    </Modal>
  </Flex>;
}
