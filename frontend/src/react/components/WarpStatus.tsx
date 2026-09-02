import { Descriptions, Space, Tag } from "../../ui";
import { resultObject } from "../../domain/diagnostics";
import { zhStatus } from "../../i18n/zh-CN";

export interface WarpStatusProps { body: unknown }
export default function WarpStatus({ body }: WarpStatusProps) {
  const status = resultObject(body);
  const phase = ({ absent: "未安装", configured: "出站已配置", needs_apply: "需要配置", removal_pending: "等待删除" } as Record<string, string>)[String(status.phase)]
    ?? (status.installed ? "出站已配置" : "未安装");
  const account = status.account_type === "free" ? "WARP 免费版" : status.license_active ? "WARP+" : status.account_type ? zhStatus(String(status.account_type)) : "未知";
  const date = new Date(String(status.registered_at ?? ""));
  const registered = Number.isNaN(date.getTime()) ? null : date.toISOString();
  return <section aria-label="WARP 结果"><Space orientation="vertical" style={{ width: "100%" }}>
    <Tag color={status.installed ? "success" : status.registered ? "warning" : "default"}>{phase}</Tag>
    {Boolean(status.registered || status.installed) && <Descriptions column={1} size="small" items={[
      { key: "account", label: "账户", children: account },
      { key: "ipv4", label: "IPv4", children: String(status.addr_v4 || "无") },
      { key: "ipv6", label: "IPv6", children: String(status.addr_v6 || "无") },
      { key: "registered", label: "注册时间", children: registered ? <time dateTime={registered}>{date.toLocaleDateString("zh-CN", { timeZone: "UTC" })}<br />{date.toLocaleTimeString("zh-CN", { timeZone: "UTC", hour: "2-digit", minute: "2-digit", hour12: false })} UTC</time> : "未知" },
    ]} />}
  </Space></section>;
}
