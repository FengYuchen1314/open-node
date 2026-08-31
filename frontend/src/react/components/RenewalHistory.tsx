import { Table, Tag } from "antd";
import type { ReactNode } from "react";
import { renewalStatusLabels, type RenewalRequest } from "../../domain/renewals";

export const renewalDate = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
export default function RenewalHistory({ rows, total, offset, busy, administrator = false, onPage, action }: {
  rows: RenewalRequest[]; total: number; offset: number; busy: boolean; administrator?: boolean;
  onPage: (offset: number) => void; action: (row: RenewalRequest) => ReactNode;
}) {
  return <Table<RenewalRequest> rowKey="id" dataSource={rows} loading={busy} scroll={{ x: 900 }}
    pagination={{ pageSize: 20, current: offset / 20 + 1, total, showSizeChanger: false, onChange: page => onPage((page - 1) * 20), showTotal: count => `共 ${count} 项申请` }}
    columns={[
      ...(administrator ? [{ title: "用户", dataIndex: "username", key: "username", render: (value: string) => <span style={{ overflowWrap: "anywhere" }}>{value}</span> }] : []),
      { title: "套餐", dataIndex: "plan_name", key: "plan_name", render: (value: string) => <span style={{ overflowWrap: "anywhere" }}>{value}</span> },
      { title: "续费天数", dataIndex: "renew_days", key: "renew_days", render: (days: number) => `${days} 天` },
      { title: "状态", dataIndex: "status", key: "status", render: (status: RenewalRequest["status"]) => <Tag color={status === "approved" ? "success" : status === "pending" ? "processing" : "default"}>{renewalStatusLabels[status]}</Tag> },
      { title: "申请时间", dataIndex: "created_at", key: "created_at", render: renewalDate },
      { title: "原到期时间", dataIndex: "previous_end_date", key: "previous_end_date", render: renewalDate },
      { title: "新到期时间", dataIndex: "new_end_date", key: "new_end_date", render: renewalDate },
      { title: "操作", key: "action", render: (_, row) => action(row) },
    ]} />;
}
