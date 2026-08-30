import { useEffect } from "react";
import { Alert, Button, Card, Col, Empty, Flex, Form, Radio, Row, Typography } from "antd";
import { ArrowDownOutlined, ArrowUpOutlined, DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import { newAutoSpeedRule, validAutoSpeedRule, type AutoSpeedRule } from "../../domain/auto-speed";
import StrictInputNumber from "./StrictInputNumber";

export interface AutoSpeedRuleEditorProps {
  value: AutoSpeedRule[];
  onChange: (value: AutoSpeedRule[]) => void;
  onValid?: (valid: boolean) => void;
  disabled?: boolean;
}

export default function AutoSpeedRuleEditor({ value, onChange, onValid, disabled = false }: AutoSpeedRuleEditorProps) {
  const valid = value.length <= 100 && value.every(validAutoSpeedRule);
  useEffect(() => { onValid?.(valid); }, [valid, onValid]);
  function update(index: number, change: Partial<AutoSpeedRule>) {
    if (!disabled) onChange(value.map((rule, i) => i === index ? { ...rule, ...change } : rule));
  }
  function move(index: number, direction: number) {
    const next = index + direction;
    if (disabled || next < 0 || next >= value.length) return;
    const rules = [...value];
    [rules[index], rules[next]] = [rules[next], rules[index]];
    onChange(rules);
  }
  const fields: Array<{ field: keyof Omit<AutoSpeedRule, "type">; label: string; min: number; max?: number }> = [
    { field: "threshold_mbps", label: "Trigger Mbps", min: 1 / 125000 },
    { field: "sustained_seconds", label: "Hold seconds", min: 1, max: 86400 },
    { field: "limit_mbps", label: "Cap Mbps", min: 1 / 125000 },
    { field: "limit_duration", label: "Duration seconds", min: 1, max: 86400 },
  ];
  return <section aria-label="Automatic limits">
    <Flex justify="space-between" align="center" wrap>
      <Typography.Title level={5}>Automatic limits</Typography.Title>
      <Button icon={<PlusOutlined />} aria-label="Add automatic rule" disabled={disabled || value.length >= 100}
        onClick={() => onChange([...value, newAutoSpeedRule()])}>Add rule</Button>
    </Flex>
    <Flex vertical gap="middle">
      {!value.length && <Empty description="No automatic rules" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
      {value.map((rule, index) => <Card key={index} size="small" aria-label={`Automatic rule ${index + 1}`}>
        <Flex justify="space-between" align="center" wrap gap="small">
          <Radio.Group aria-label={`Rule ${index + 1} type`} value={rule.type} disabled={disabled}
            optionType="button" options={[{ label: "Sustained", value: "sustained" }, { label: "Burst", value: "burst" }]}
            onChange={event => update(index, { type: event.target.value })} />
          <Flex gap="small">
            <Button icon={<ArrowUpOutlined />} aria-label={`Move rule ${index + 1} up`} disabled={disabled || index === 0} onClick={() => move(index, -1)} />
            <Button icon={<ArrowDownOutlined />} aria-label={`Move rule ${index + 1} down`} disabled={disabled || index === value.length - 1} onClick={() => move(index, 1)} />
            <Button icon={<DeleteOutlined />} danger aria-label={`Remove automatic rule ${index + 1}`} disabled={disabled}
              onClick={() => onChange(value.filter((_, i) => i !== index))} />
          </Flex>
        </Flex>
        <Row gutter={16}>
          {[...fields, ...(rule.type === "burst" ? [
            { field: "window_seconds" as const, label: "Window seconds", min: rule.sustained_seconds, max: 86400 },
            { field: "burst_count" as const, label: "Bursts", min: 1, max: 10000 },
          ] : [])].map(field => <Col xs={24} sm={12} key={field.field}>
            <Form.Item label={field.label} layout="vertical">
              <StrictInputNumber aria-label={field.label} value={rule[field.field]}
                aria-valuemin={field.min} aria-valuemax={field.max} disabled={disabled} style={{ width: "100%" }}
                onChange={number => update(index, { [field.field]: number ?? Number.NaN })} />
            </Form.Item>
          </Col>)}
        </Row>
        {!validAutoSpeedRule(rule) && <Alert type="error" title="Invalid automatic limit rule" showIcon />}
      </Card>)}
    </Flex>
  </section>;
}
