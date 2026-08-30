import { Col, Form, Row, Select, Typography } from "antd";
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
    <Col xs={24} sm={12}><Form.Item label={`${label} mode`}>
      <Select aria-label={`${label} mode`} value={mode} disabled={disabled}
        options={[{ label: "Inherit", value: "inherit" }, { label: "Unlimited", value: "unlimited" }, { label: "Custom", value: "custom" }]}
        onChange={next => onChange(next === "inherit" ? null : next === "unlimited" ? 0 : suggested > 0 ? suggested : 1)} />
    </Form.Item></Col>
    <Col xs={24} sm={12}>{mode === "custom" ? <Form.Item label={unit ? `${label} (${unit})` : label}
      validateStatus={valid ? undefined : "error"} help={valid ? undefined : "Enter a valid positive limit"}>
      <StrictInputNumber aria-label={unit ? `${label} (${unit})` : label} value={value}
        aria-valuemin={minimum} aria-valuemax={maximum} step={integer ? 1 : undefined} disabled={disabled} style={{ width: "100%" }}
        onChange={next => onChange(next ?? Number.NaN)} />
    </Form.Item> : <Typography.Text type="secondary">{mode === "inherit" ? "Inherited" : "Unlimited"}</Typography.Text>}</Col>
  </Row>;
}
