import { CheckCircleOutlined, ExclamationCircleOutlined } from "@ant-design/icons";
import { Alert, Card, Descriptions, Empty, Flex, Space, Table, Typography } from "antd";
import { resultObject, resultRows } from "../../domain/diagnostics";
import { zhMessage, zhStatus } from "../../i18n/zh-CN";

export interface DiagnosticResultProps { path: string; body: unknown }
const display = (value: unknown) => typeof value === "string" || typeof value === "number" ? String(value) : "";
const carrierLabel = (value: unknown) => ({ telecom: "电信", unicom: "联通", mobile: "移动" } as Record<string, string>)[display(value)] ?? display(value);
const latency = (value: unknown) => typeof value === "number" && Number.isFinite(value) && value >= 0 ? `${value} ms` : "不可用";

export default function DiagnosticResult({ path, body }: DiagnosticResultProps) {
  const result = resultObject(body);
  const rows = resultRows(result.results);
  if (path === "/api/child/domains/latency") return <Space orientation="vertical" style={{ width: "100%" }}>
    {rows.map((row, index) => <Flex gap="small" key={index} align="start">
      {row.success === true ? <CheckCircleOutlined style={{ color: "#52c41a" }} /> : <ExclamationCircleOutlined style={{ color: "#ff4d4f" }} />}
      <div className="min-width-zero">
        <Typography.Text strong>{display(row.target || row.domain)}</Typography.Text>
        <div>{row.method === "icmp" ? row.success === true ? "ICMP 主机可达" : "ICMP 失败" : row.success === true ? "TCP 端口开放" : "TCP 失败"}
          {row.success === true && ` | ${latency(row.latency_ms)}`}</div>
        {Boolean(row.error || row.tcp_error) && <Typography.Paragraph type="secondary">{zhMessage(display(row.error || row.tcp_error))}</Typography.Paragraph>}
        {Boolean(row.icmp_error) && <Typography.Paragraph type="secondary">{zhMessage(display(row.icmp_error))}</Typography.Paragraph>}
      </div>
    </Flex>)}
    {!rows.length && <Empty description="暂无诊断结果。" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
  </Space>;
  if (path === "/api/child/network/return-route-test") return <Space orientation="vertical" style={{ width: "100%" }}>
    {rows.map((row, index) => <Card key={index} size="small" title={`${carrierLabel(row.carrier)} | ${display(row.target)}`}>
      <Space orientation="vertical" style={{ width: "100%" }}>
        <Typography.Text>{row.route_type ? zhStatus(display(row.route_type)) : "未知"} | {row.reached === true ? "已到达目标" : "尚未确认到达目标"}</Typography.Text>
        {Boolean(row.error) && <Alert type="error" showIcon title={zhMessage(display(row.error))} />}
        {Boolean(row.reason) && <Typography.Text type="secondary">{zhMessage(display(row.reason))}</Typography.Text>}
        <Table size="small" pagination={false} scroll={{ x: 380 }} rowKey={(_, position) => String(position)} dataSource={resultRows(row.hops)} columns={[
          { title: "跳数", dataIndex: "hop", width: 60, render: display },
          { title: "地址 / 依据", render: (_, hop) => <><Typography.Text strong>{display(hop.ip)}</Typography.Text><div><Typography.Text type="secondary">{hop.asn ? `AS${display(hop.asn)}` : "ASN 不可用"} {display(hop.country)} {display(hop.region)}</Typography.Text></div></> },
          { title: "延迟", dataIndex: "rtt_ms", width: 110, render: latency },
        ]} />
      </Space>
    </Card>)}
  </Space>;
  if (path === "/api/child/logs") return <pre className="code-block" aria-label="服务日志输出">{display(result.logs) || "暂无日志。"}</pre>;
  if (Array.isArray(result.files)) return <Space orientation="vertical" style={{ width: "100%" }}>
    <Typography.Text type="secondary">{display(result.total_size)} 字节</Typography.Text>
    <Table size="small" pagination={false} scroll={{ x: 360 }} rowKey={(_, index) => String(index)} dataSource={resultRows(result.files)} locale={{ emptyText: "暂无日志文件。" }} columns={[
      { title: "文件", dataIndex: "name", render: display },
      { title: "大小", dataIndex: "size", render: value => `${display(value)} 字节` },
      { title: "状态", dataIndex: "active", render: value => value ? "使用中" : "已轮转" },
    ]} />
  </Space>;
  return <Space orientation="vertical" style={{ width: "100%" }}>
    {result.removed !== undefined ? <Descriptions size="small" items={[
      { key: "removed", label: "已清空文件", children: display(result.removed) },
      { key: "freed", label: "已释放字节", children: display(result.freed) },
    ]} /> : <Typography.Text>{result.success ? "日志删除完成" : "日志删除失败"}</Typography.Text>}
    {resultRows(result.errors).map((error, index) => <Alert key={index} type="error" showIcon title={`${display(error.name)}：${zhMessage(display(error.error))}`} />)}
  </Space>;
}
