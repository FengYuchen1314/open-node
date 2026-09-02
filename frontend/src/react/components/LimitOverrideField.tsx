import { Col, Form, Row, Select, Typography } from "../../ui";
import { validLimit } from "../../domain/user-limits";
import StrictInputNumber from "./StrictInputNumber";

export interface LimitOverrideFieldProps {
  value: number | null;
  onChange: (value: number | null) => void;
  label: string;
  unit?: string;
  maximum: number;
  minimum: number;
  integer?: boolean;
  disabled?: boolean;
  suggested?: number;
}

export default function LimitOverrideField({ value, onChange, label, unit = "", maximum, minimum, integer = false, disabled = false, suggested = 1 }: LimitOverrideFieldProps) {
  const mode = value === null ? "inherit" : value === 0 ? "unlimited" : "custom";
  const valid = validLimit(value, maximum, minimum, integer);
  return <Row gutter={16}>
    <Col xs={24} sm={12}><Form.Item label={`${label}模式`}>
      <Select aria-label={`${label}模式`} value={mode} disabled={disabled}
        options={[{ label: "继承", value: "inherit" }, { label: "不限", value: "unlimited" }, { label: "自定义", value: "custom" }]}
        onChange={next => onChange(next === "inherit" ? null : next === "unlimited" ? 0 : suggested > 0 ? suggested : 1)} />
    </Form.Item></Col>
    <Col xs={24} sm={12}>{mode === "custom" ? <Form.Item label={unit ? `${label}（${unit}）` : label}
      validateStatus={valid ? undefined : "error"} help={valid ? undefined : "请输入有效的正数限制值"}>
      <StrictInputNumber aria-label={unit ? `${label}（${unit}）` : label} value={value}
        aria-valuemin={minimum} aria-valuemax={maximum} step={integer ? 1 : undefined} disabled={disabled} style={{ width: "100%" }}
        onChange={next => onChange(next ?? Number.NaN)} />
    </Form.Item> : <Typography.Text type="secondary">{mode === "inherit" ? "已继承" : "不限"}</Typography.Text>}</Col>
  </Row>;
}
