import { InputNumber, type InputNumberProps } from "antd";

export interface StrictInputNumberProps extends Omit<InputNumberProps<number>,
  "value" | "onChange" | "onInput" | "min" | "max" | "precision" | "parser" | "formatter"
  | "defaultValue" | "stringMode" | "changeOnBlur"> {
  value: number | null;
  onChange: (value: number | null) => void;
  /** Only an explicitly empty input means null; malformed input always means NaN. */
  allowEmpty?: boolean;
}

function parseNumber(input: string | undefined): number {
  if (!input || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(input)) return Number.NaN;
  const value = Number(input);
  // A nonzero underflow must not turn into an unlimited (zero) business value.
  if (!Number.isFinite(value) || (value === 0 && /[1-9]/.test(input.split(/[eE]/)[0] ?? ""))) return Number.NaN;
  return value;
}

function formatNumber(value: number | undefined, info: { userTyping: boolean; input: string }): string {
  if (info.userTyping) return info.input;
  return value === undefined || !Number.isFinite(Number(value)) ? "" : String(value);
}

/** Keep numeric drafts intact; callers validate business bounds before saving. */
export default function StrictInputNumber({ value, onChange, allowEmpty = false, ...props }: StrictInputNumberProps) {
  return <InputNumber<number> {...props}
    value={value === null && !allowEmpty ? Number.NaN : value}
    min={undefined} max={undefined} precision={undefined} formatter={formatNumber} stringMode={false}
    parser={parseNumber} changeOnBlur={false}
    onChange={next => {
      if (!props.disabled && !props.readOnly) onChange(next === null ? (allowEmpty ? null : Number.NaN) : next);
    }}
    onInput={input => {
      if (!props.disabled && !props.readOnly) onChange(input === "" && allowEmpty ? null : parseNumber(input));
    }} />;
}
