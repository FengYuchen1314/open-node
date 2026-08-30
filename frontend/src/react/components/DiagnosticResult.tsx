import { CheckCircleOutlined, ExclamationCircleOutlined } from "@ant-design/icons";
import { Alert, Card, Descriptions, Empty, Flex, Space, Table, Typography } from "antd";
import { resultObject, resultRows } from "../../domain/diagnostics";

export interface DiagnosticResultProps { path: string; body: unknown }
const display = (value: unknown) => typeof value === "string" || typeof value === "number" ? String(value) : "";
const latency = (value: unknown) => typeof value === "number" && Number.isFinite(value) && value >= 0 ? `${value} ms` : "Unavailable";

export default function DiagnosticResult({ path, body }: DiagnosticResultProps) {
  const result = resultObject(body);
  const rows = resultRows(result.results);
  if (path === "/api/child/domains/latency") return <Space orientation="vertical" style={{ width: "100%" }}>
    {rows.map((row, index) => <Flex gap="small" key={index} align="start">
      {row.success === true ? <CheckCircleOutlined style={{ color: "#52c41a" }} /> : <ExclamationCircleOutlined style={{ color: "#ff4d4f" }} />}
      <div className="min-width-zero">
        <Typography.Text strong>{display(row.target || row.domain)}</Typography.Text>
        <div>{row.method === "icmp" ? row.success === true ? "ICMP host reachable" : "ICMP failed" : row.success === true ? "TCP port open" : "TCP failed"}
          {row.success === true && ` | ${latency(row.latency_ms)}`}</div>
        {Boolean(row.error || row.tcp_error) && <Typography.Paragraph type="secondary">{display(row.error || row.tcp_error)}</Typography.Paragraph>}
        {Boolean(row.icmp_error) && <Typography.Paragraph type="secondary">{display(row.icmp_error)}</Typography.Paragraph>}
      </div>
    </Flex>)}
    {!rows.length && <Empty description="No diagnostic results." image={Empty.PRESENTED_IMAGE_SIMPLE} />}
  </Space>;
  if (path === "/api/child/network/return-route-test") return <Space orientation="vertical" style={{ width: "100%" }}>
    {rows.map((row, index) => <Card key={index} size="small" title={`${display(row.carrier)} | ${display(row.target)}`}>
      <Space orientation="vertical" style={{ width: "100%" }}>
        <Typography.Text>{display(row.route_type || "Unknown")} | {row.reached === true ? "Target reached" : "Target not confirmed"}</Typography.Text>
        {Boolean(row.error) && <Alert type="error" showIcon title={display(row.error)} />}
        {Boolean(row.reason) && <Typography.Text type="secondary">{display(row.reason)}</Typography.Text>}
        <Table size="small" pagination={false} scroll={{ x: 380 }} rowKey={(_, position) => String(position)} dataSource={resultRows(row.hops)} columns={[
          { title: "Hop", dataIndex: "hop", width: 60, render: display },
          { title: "Address / evidence", render: (_, hop) => <><Typography.Text strong>{display(hop.ip)}</Typography.Text><div><Typography.Text type="secondary">{hop.asn ? `AS${display(hop.asn)}` : "ASN unavailable"} {display(hop.country)} {display(hop.region)}</Typography.Text></div></> },
          { title: "Latency", dataIndex: "rtt_ms", width: 110, render: latency },
        ]} />
      </Space>
    </Card>)}
  </Space>;
  if (path === "/api/child/logs") return <pre className="code-block" aria-label="Service log output">{display(result.logs) || "No log entries."}</pre>;
  if (Array.isArray(result.files)) return <Space orientation="vertical" style={{ width: "100%" }}>
    <Typography.Text type="secondary">{display(result.total_size)} bytes</Typography.Text>
    <Table size="small" pagination={false} scroll={{ x: 360 }} rowKey={(_, index) => String(index)} dataSource={resultRows(result.files)} locale={{ emptyText: "No log files." }} columns={[
      { title: "File", dataIndex: "name", render: display },
      { title: "Size", dataIndex: "size", render: value => `${display(value)} bytes` },
      { title: "Status", dataIndex: "active", render: value => value ? "Active" : "Rotated" },
    ]} />
  </Space>;
  return <Space orientation="vertical" style={{ width: "100%" }}>
    {result.removed !== undefined ? <Descriptions size="small" items={[
      { key: "removed", label: "Files cleared", children: display(result.removed) },
      { key: "freed", label: "Bytes freed", children: display(result.freed) },
    ]} /> : <Typography.Text>{result.success ? "Log deletion completed" : "Log deletion failed"}</Typography.Text>}
    {resultRows(result.errors).map((error, index) => <Alert key={index} type="error" showIcon title={`${display(error.name)}: ${display(error.error)}`} />)}
  </Space>;
}
