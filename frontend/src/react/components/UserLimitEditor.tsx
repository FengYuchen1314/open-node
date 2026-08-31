import { zhMessage } from "../../i18n/zh-CN";
import { useEffect, useState } from "react";
import { Alert, Button, Card, Descriptions, Flex, Select, Typography } from "antd";
import { CloseOutlined, PlusOutlined } from "@ant-design/icons";
import type { ManagedNode } from "../../domain/subscriptions";
import { limitSource, maxSpeed, maxTraffic, type UserLimitOverrides, type UserLimitsRead } from "../../domain/user-limits";
import LimitOverrideField from "./LimitOverrideField";

export interface UserLimitEditorProps {
  value: UserLimitOverrides;
  onChange: (value: UserLimitOverrides) => void;
  nodes: ManagedNode[];
  current: UserLimitsRead;
  disabled?: boolean;
}

export default function UserLimitEditor({ value, onChange, nodes, current, disabled = false }: UserLimitEditorProps) {
  const [selected, setSelected] = useState<string>();
  const [rows, setRows] = useState(() => [...new Set([...Object.keys(value.node_speed_limits), ...Object.keys(value.node_device_limits)])]);
  useEffect(() => {
    const keys = [...new Set([...Object.keys(value.node_speed_limits), ...Object.keys(value.node_device_limits)])];
    setRows(previous => keys.some(id => !previous.includes(id)) ? [...new Set([...previous, ...keys])] : previous);
  }, [value.node_speed_limits, value.node_device_limits]);
  const name = (id: string) => nodes.find(node => node.id === id)?.name ?? id;
  function nodeValue(field: "node_speed_limits" | "node_device_limits", id: string, next: number | null) {
    const mapping = { ...value[field] };
    if (next === null) delete mapping[id]; else mapping[id] = next;
    onChange({ ...value, [field]: mapping });
  }
  return <Flex vertical gap="large">
    <section aria-label="账户限制" style={{ paddingInline: 8 }}><Typography.Title level={5}>账户限制</Typography.Title>
      <LimitOverrideField value={value.traffic_limit_gb} onChange={next => onChange({ ...value, traffic_limit_gb: next })}
        label="流量配额" unit="GiB" maximum={maxTraffic} minimum={1 / 1024 ** 3} suggested={current.traffic_limit_bytes / 1024 ** 3} disabled={disabled} />
      <LimitOverrideField value={value.speed_limit_mbps} onChange={next => onChange({ ...value, speed_limit_mbps: next })}
        label="限速" unit="Mbps" maximum={maxSpeed} minimum={1 / 125000} suggested={current.speed_limit_mbps} disabled={disabled} />
      <LimitOverrideField value={value.device_limit} onChange={next => onChange({ ...value, device_limit: next })}
        label="连接数限制" maximum={1000000} minimum={1} integer suggested={current.device_limit} disabled={disabled} />
    </section>
    <section aria-label="节点单独限制"><Typography.Title level={5}>节点单独限制</Typography.Title>
      <Flex gap="small">
        <Select aria-label="节点" placeholder="节点" showSearch optionFilterProp="label" value={selected} disabled={disabled} style={{ flex: 1 }}
          options={nodes.filter(node => !node.removal_id && !rows.includes(node.id)).map(node => ({ label: node.name, value: node.id }))} onChange={setSelected} />
        <Button aria-label="添加节点单独限制" icon={<PlusOutlined />} disabled={disabled || !selected} onClick={() => {
          if (selected && !rows.includes(selected)) setRows([...rows, selected]);
          setSelected(undefined);
        }} />
      </Flex>
      {rows.map(id => <Card key={id} size="small" title={name(id)} aria-label={`${name(id)} 的单独限制`}
        extra={<Button aria-label={`移除 ${name(id)} 的单独限制`} icon={<CloseOutlined />} disabled={disabled} onClick={() => {
          const speeds = { ...value.node_speed_limits }, devices = { ...value.node_device_limits };
          delete speeds[id]; delete devices[id]; setRows(rows.filter(row => row !== id));
          onChange({ ...value, node_speed_limits: speeds, node_device_limits: devices });
        }} />}>
        <LimitOverrideField value={value.node_speed_limits[id] ?? null} onChange={next => nodeValue("node_speed_limits", id, next)}
          label="节点速度" unit="Mbps" maximum={maxSpeed} minimum={1 / 125000} suggested={current.speed_limit_mbps} disabled={disabled} />
        <LimitOverrideField value={value.node_device_limits[id] ?? null} onChange={next => nodeValue("node_device_limits", id, next)}
          label="节点连接数" maximum={1000000} minimum={1} integer suggested={current.device_limit} disabled={disabled} />
      </Card>)}
    </section>
    {!!current.nodes.length && <section aria-label="已保存的节点限制"><Typography.Title level={5}>已保存的节点限制</Typography.Title>
      {current.nodes.map(node => <Descriptions key={node.node_id} title={`${node.name}${node.enabled ? "" : "（已停用）"}`} size="small" column={1}
        items={[
          { key: "speed", label: zhMessage(limitSource(node.speed_source)), children: node.speed_limit_mbps ? `${node.speed_limit_mbps} Mbps` : "不限" },
          { key: "device", label: zhMessage(limitSource(node.device_source)), children: node.device_limit ? `${node.device_limit} 个连接` : "连接数不限" },
        ]} />)}
    </section>}
    {current.warnings.map(warning => <Alert key={warning} type="warning" title={zhMessage(warning)} showIcon />)}
  </Flex>;
}
