import { Col, Form, Input, Row, Typography } from "antd";
import { useId } from "react";
import type { AgentReturnRouteTarget } from "../../domain/inventory";
import StrictInputNumber from "./StrictInputNumber";

export interface RouteProbeFieldsProps {
  value: AgentReturnRouteTarget[];
  onChange: (value: AgentReturnRouteTarget[]) => void;
  disabled?: boolean;
}
const names = { telecom: "电信", unicom: "联通", mobile: "移动" };
function validPort(port: number | undefined) {
  const value = port ?? 80;
  return Number.isInteger(value) && value >= 1 && value <= 65535;
}
export default function RouteProbeFields({ value, onChange, disabled }: RouteProbeFieldsProps) {
  const id = useId();
  const update = (index: number, key: "host" | "region" | "port", next: string | number | null) => onChange(value.map((target, position) => position === index ? { ...target, [key]: key === "port" ? (typeof next === "number" ? next : Number.NaN) : String(next ?? "") } : target));
  return <div className="route-probe-fields">{value.map((target, index) => <section key={target.carrier}>
    <Typography.Title level={5}>{names[target.carrier]}</Typography.Title>
    <Row gutter={12}>
      <Col xs={24} sm={16}><Form.Item label={`${names[target.carrier]}主机`} htmlFor={`${id}-${index}-host`}><Input id={`${id}-${index}-host`} value={target.host} disabled={disabled} onChange={event => update(index, "host", event.target.value)} /></Form.Item></Col>
      <Col xs={24} sm={8}><Form.Item label={`${names[target.carrier]}端口`} htmlFor={`${id}-${index}-port`} validateStatus={!validPort(target.port) ? "error" : undefined} help={!validPort(target.port) ? "请输入 1 至 65535 的整数端口。" : undefined}><StrictInputNumber id={`${id}-${index}-port`} value={target.port ?? 80} aria-valuemin={1} aria-valuemax={65535} disabled={disabled} onChange={next => update(index, "port", next)} style={{ width: "100%" }} /></Form.Item></Col>
      <Col span={24}><Form.Item label={`${names[target.carrier]}地区`} htmlFor={`${id}-${index}-region`}><Input id={`${id}-${index}-region`} value={target.region} disabled={disabled} onChange={event => update(index, "region", event.target.value)} /></Form.Item></Col>
    </Row>
  </section>)}</div>;
}
