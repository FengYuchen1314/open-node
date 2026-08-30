import { Descriptions, Space, Tag } from "antd";
import { resultObject } from "../../domain/diagnostics";

export interface WarpStatusProps { body: unknown }
export default function WarpStatus({ body }: WarpStatusProps) {
  const status = resultObject(body);
  const phase = ({ absent: "Not installed", configured: "Outbounds configured", needs_apply: "Needs configuration", removal_pending: "Removal pending" } as Record<string, string>)[String(status.phase)]
    ?? (status.installed ? "Outbounds configured" : "Not installed");
  const account = status.account_type === "free" ? "Free WARP" : status.license_active ? "WARP+" : String(status.account_type ?? "Unknown");
  const date = new Date(String(status.registered_at ?? ""));
  const registered = Number.isNaN(date.getTime()) ? null : date.toISOString();
  return <section aria-label="WARP result"><Space orientation="vertical" style={{ width: "100%" }}>
    <Tag color={status.installed ? "success" : status.registered ? "warning" : "default"}>{phase}</Tag>
    {Boolean(status.registered || status.installed) && <Descriptions column={1} size="small" items={[
      { key: "account", label: "Account", children: account },
      { key: "ipv4", label: "IPv4", children: String(status.addr_v4 || "None") },
      { key: "ipv6", label: "IPv6", children: String(status.addr_v6 || "None") },
      { key: "registered", label: "Registered", children: registered ? <time dateTime={registered}>{registered.slice(0, 10)}<br />{registered.slice(11, 16)} UTC</time> : "Unknown" },
    ]} />}
  </Space></section>;
}
