import { zhMessage } from "../../i18n/zh-CN";
import { useEffect, type ReactNode } from "react";
import { Card, Flex, Form, Input, Switch, Typography } from "antd";
import { aliasErrors } from "../../domain/plan-node-aliases";

export interface PlanNodeAliasesProps {
  nodes: { id: string; name: string }[];
  value: Record<string, string>;
  onChange: (value: Record<string, string>) => void;
  enabled: boolean;
  onEnabledChange: (enabled: boolean) => void;
  onValid?: (valid: boolean) => void;
  disabled?: boolean;
  renderNode?: (node: { id: string; name: string }) => ReactNode;
}

export default function PlanNodeAliases({ nodes, value, onChange, enabled, onEnabledChange, onValid, disabled, renderNode }: PlanNodeAliasesProps) {
  const ids = nodes.map(node => node.id);
  const errors = aliasErrors(value, ids);
  const valid = Object.keys(errors).length === 0;
  useEffect(() => { onValid?.(valid); }, [valid, onValid]);
  useEffect(() => {
    const allowed = new Set(nodes.map(node => node.id));
    if (Object.keys(value).some(id => !allowed.has(id))) {
      onChange(Object.fromEntries(Object.entries(value).filter(([id]) => allowed.has(id))));
    }
  }, [nodes, value, onChange]);
  return <Flex vertical gap="middle">
    <Flex gap="small" align="center">
      <Switch aria-label="自定义订阅名称" checked={enabled} disabled={disabled || !nodes.length} onChange={onEnabledChange} />
      <Typography.Text>自定义订阅名称</Typography.Text>
    </Flex>
    {nodes.map(node => <Card size="small" key={node.id} title={node.name} aria-label={node.name}>
      <Form.Item label="订阅名称" validateStatus={errors[node.id] ? "error" : undefined} help={errors[node.id] ? zhMessage(errors[node.id]) : undefined}>
        <Input aria-label={`${node.name}：订阅名称`} value={value[node.id] ?? ""} placeholder={node.name}
          allowClear disabled={disabled || !enabled} onChange={event => {
            const next = { ...value };
            if (event.target.value) next[node.id] = event.target.value; else delete next[node.id];
            onChange(next);
          }} />
      </Form.Item>
      {renderNode?.(node)}
    </Card>)}
  </Flex>;
}
