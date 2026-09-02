import React, { Children, cloneElement, createContext, isValidElement, useContext, useEffect, useId, useMemo, useRef, useState } from "react";

type AnyProps = Record<string, any> & {
  onChange?: (value: any, ...args: any[]) => any;
  onClick?: (event: any, ...args: any[]) => any;
  onSubmit?: (event: any) => any;
  onFinish?: (...args: any[]) => any;
  onCancel?: (...args: any[]) => any;
  onOk?: (...args: any[]) => any;
  onClose?: (...args: any[]) => any;
  beforeUpload?: (file: File, ...args: any[]) => any;
  onDragStart?: (event: React.DragEvent<any>) => any;
  onDragOver?: (event: React.DragEvent<any>) => any;
  onDrop?: (event: React.DragEvent<any>) => any;
};
const cx = (...values: unknown[]) => values.filter(Boolean).join(" ");
const omit = (props: AnyProps, keys: string[]) => Object.fromEntries(Object.entries(props).filter(([key]) => !keys.includes(key)));

export type ColumnsType<T = any> = Array<{
  title?: React.ReactNode; key?: React.Key; dataIndex?: string | string[]; render?: (value: any, record: T, index: number) => React.ReactNode;
  sorter?: ((a: T, b: T) => number) | boolean; [key: string]: any;
}>;
export type TableColumnsType<T = any> = ColumnsType<T>;
export type InputNumberProps<T = number> = Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "onInput"> & {
  value?: T | null; onChange?: (value: any) => void; onInput?: (value: string) => void;
  parser?: (input: string | undefined) => T; formatter?: (value: T | undefined, info: { userTyping: boolean; input: string }) => string;
  stringMode?: boolean; precision?: number; changeOnBlur?: boolean; onPressEnter?: (event: React.KeyboardEvent<HTMLInputElement>) => void;
};
type UiInputProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, "prefix"> & {
  prefix?: React.ReactNode; suffix?: React.ReactNode; onSearch?: (value: string) => void; enterButton?: React.ReactNode;
  allowClear?: boolean; visibilityToggle?: boolean; onPressEnter?: (event: React.KeyboardEvent<HTMLInputElement>) => void;
};
type UiTextAreaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement> & { autoSize?: boolean | { minRows?: number; maxRows?: number } };

export function ConfigProvider({ children }: AnyProps) { return <UiApp>{children}</UiApp>; }

const AppContext = createContext<{ modal: { confirm: (_options: AnyProps) => void } }>({ modal: { confirm: () => undefined } });
function UiApp({ children }: AnyProps) {
  const [confirmation, setConfirmation] = useState<AnyProps | null>(null);
  const api = useMemo(() => ({ modal: { confirm: (options: AnyProps) => setConfirmation(options) } }), []);
  async function accept() {
    const action = confirmation?.onOk;
    setConfirmation(null);
    await action?.();
  }
  return <AppContext.Provider value={api}>{children}<Modal open={Boolean(confirmation)} title={confirmation?.title}
    okText={confirmation?.okText ?? "确定"} cancelText={confirmation?.cancelText ?? "取消"}
    onOk={() => void accept()} onCancel={() => { confirmation?.onCancel?.(); setConfirmation(null); }}>
    {confirmation?.content && <div>{confirmation.content}</div>}
  </Modal></AppContext.Provider>;
}
export const App: any = UiApp;
App.useApp = () => useContext(AppContext);
const FormDisabledContext = createContext(false);

export function Button({ children, icon, loading, type, danger, block, htmlType, className, ...props }: AnyProps) {
  const formDisabled = useContext(FormDisabledContext), disabled = props.disabled || formDisabled;
  if (props.href) return <a {...omit(props, ["disabled", "download"])} download={props.download} href={props.href} className={cx("ui-button", type && `ui-button-${type}`, danger && "ui-button-danger", block && "ui-block", disabled && "is-disabled", className)} aria-disabled={disabled || undefined}>{icon}{children && <span>{children}</span>}</a>;
  return <button {...omit(props, ["shape", "size", "disabled"])} type={htmlType ?? "button"} disabled={disabled || loading}
    className={cx("ui-button", type && `ui-button-${type}`, danger && "ui-button-danger", block && "ui-block", className)}>
    {loading ? <span className="ui-spinner" aria-label="处理中" /> : icon}{children && <span>{children}</span>}
  </button>;
}

export function Alert({ message, title, description, type = "info", showIcon, action, closable, onClose, className, ...props }: AnyProps) {
  const heading = message ?? title;
  return <section {...omit(props, ["banner"])} role="alert" className={cx("ui-alert", `ui-alert-${type}`, className)}>
    {showIcon && <span aria-hidden className="ui-alert-icon">{type === "success" ? "✓" : type === "error" ? "!" : "i"}</span>}
    <div className="ui-alert-copy">{heading && <strong>{heading}</strong>}{description && <div>{description}</div>}</div>{action}
    {closable && <button type="button" className="ui-close" aria-label="关闭" onClick={onClose}>×</button>}
  </section>;
}

export function Card({ title, extra, children, className, size, styles, loading, ...props }: AnyProps) {
  return <section {...props} className={cx("ui-card", size && `ui-card-${size}`, className)}>
    {(title || extra) && <header className="ui-card-head"><div className="ui-card-title" style={styles?.title}>{title}</div><div className="ui-card-extra">{extra}</div></header>}
    <div className="ui-card-body" style={styles?.body}>{loading ? <Spin /> : children}</div>
  </section>;
}

export function Flex({ children, vertical, gap, align, justify, wrap, className, ...props }: AnyProps) {
  const style = { display: "flex", flexDirection: vertical ? "column" : undefined, gap: gap === "small" ? 8 : gap === "middle" ? 16 : gap === "large" ? 24 : gap, alignItems: align, justifyContent: justify === "space-between" ? "space-between" : justify, flexWrap: wrap ? "wrap" : undefined, ...props.style } as React.CSSProperties;
  return <div {...omit(props, ["style"])} className={cx("ui-flex", className)} style={style}>{children}</div>;
}

export function Space({ children, direction, orientation, size, wrap, className, ...props }: AnyProps) {
  const vertical = direction === "vertical" || orientation === "vertical";
  return <div {...props} className={cx("ui-space", vertical && "ui-space-vertical", wrap && "ui-wrap", className)} style={{ gap: size === "small" ? 8 : size === "large" ? 20 : 12, ...props.style }}>{children}</div>;
}
Space.Compact = ({ children, block, className, ...props }: AnyProps) => <div {...props} className={cx("ui-compact", block && "ui-block", className)}>{children}</div>;

export function Form({ children, onFinish, onSubmit, disabled, className, ...props }: AnyProps) {
  return <form {...omit(props, ["layout", "preserve", "initialValues", "name", "colon", "labelCol", "wrapperCol"])} className={cx("ui-form", className)} aria-disabled={disabled || undefined} onSubmit={event => { event.preventDefault(); onSubmit?.(event); onFinish?.(); }}><FormDisabledContext.Provider value={Boolean(disabled)}><fieldset className="ui-form-fields" disabled={disabled}>{children}</fieldset></FormDisabledContext.Provider></form>;
}
Form.Item = ({ label, children, required, extra, help, validateStatus, htmlFor, className, ...props }: AnyProps) => {
  const nestedLabel = isValidElement(children) && Children.toArray((children.props as AnyProps).children).some(child => isValidElement(child) && Boolean((child.props as AnyProps)["aria-label"]));
  const control = isValidElement(children) && !nestedLabel && typeof label === "string" && !(children.props as AnyProps)["aria-label"]
    ? cloneElement(children as React.ReactElement<any>, { "aria-label": label }) : children;
  return <div {...omit(props, ["name", "rules", "valuePropName", "tooltip", "labelCol", "wrapperCol"])} className={cx("ui-form-item", props.layout === "vertical" && "ui-form-item-vertical", validateStatus && `ui-form-${validateStatus}`, className)}>
    {label && <div className="ui-form-label"><label htmlFor={htmlFor}>{label}</label>{required && <span className="ui-required" aria-hidden> *</span>}</div>}
    {control}{(help || extra) && <div className="ui-form-help">{help || extra}</div>}
  </div>;
};

const BaseInput = React.forwardRef<HTMLInputElement, UiInputProps>(function UiInput({ prefix, suffix, className, allowClear, visibilityToggle: _visibilityToggle, onPressEnter, ...props }, ref) {
  const formDisabled = useContext(FormDisabledContext), disabled = props.disabled || formDisabled;
  return <span className={cx("ui-input-wrap", className)}>{prefix}<input {...props} disabled={disabled} ref={ref} className="ui-input" onKeyDown={event => { props.onKeyDown?.(event); if (event.key === "Enter") onPressEnter?.(event); }} />{allowClear && props.value ? <button type="button" disabled={disabled} className="ui-close" aria-label="清空" onClick={() => props.onChange?.({ target: { value: "" }, currentTarget: { value: "" } } as any)}>×</button> : suffix}</span>;
});
const PasswordInput = React.forwardRef<HTMLInputElement, UiInputProps>((props, ref) => <BaseInput {...props} ref={ref} type="password" />);
const TextAreaInput = React.forwardRef<HTMLTextAreaElement, UiTextAreaProps>(({ className, autoSize, rows, ...props }, ref) => { const formDisabled = useContext(FormDisabledContext); return <textarea {...props} disabled={props.disabled || formDisabled} rows={rows ?? (typeof autoSize === "object" ? autoSize.minRows : undefined)} ref={ref} className={cx("ui-input", "ui-textarea", className)} />; });
const SearchInput = React.forwardRef<HTMLInputElement, UiInputProps>(({ onSearch, enterButton, ...props }, ref) => <span className="ui-search"><BaseInput {...props} ref={ref} onKeyDown={(event: any) => { props.onKeyDown?.(event); if (event.key === "Enter") onSearch?.(event.currentTarget.value); }} /><Button onClick={() => onSearch?.(String(props.value ?? ""))}>{enterButton || "搜索"}</Button></span>);
export const Input = Object.assign(BaseInput, { Password: PasswordInput, TextArea: TextAreaInput, Search: SearchInput });

export function InputNumber<T = number>({ onChange, onInput, onPressEnter, value, className, stringMode, parser, formatter, ...props }: InputNumberProps<T>) {
  const formDisabled = useContext(FormDisabledContext);
  const formatted = () => String(formatter ? formatter(value == null ? undefined : value, { userTyping: false, input: String(value ?? "") }) : value ?? "");
  const [draft, setDraft] = useState(formatted);
  const editing = useRef(false);
  useEffect(() => {
    if (!editing.current || (value != null && Number.isFinite(Number(value)))) setDraft(formatted());
  }, [value]);
  return <input {...omit(props, ["changeOnBlur", "precision"])} disabled={props.disabled || formDisabled} className={cx("ui-input", className)} type="text" inputMode="decimal" role="spinbutton" value={draft}
    onFocus={event => { editing.current = true; props.onFocus?.(event); }}
    onBlur={event => { editing.current = false; props.onBlur?.(event); }}
    onKeyDown={event => { props.onKeyDown?.(event); if (event.key === "Enter") onPressEnter?.(event); }}
    onChange={event => { const raw = event.target.value; setDraft(raw); if (onInput) onInput(raw); else onChange?.(raw === "" ? null : parser ? parser(raw) : stringMode ? raw : Number(raw)); }} />;
}

function nativeValue(options: any[], raw: string) {
  const option = options.find(item => String(item.value) === raw);
  return option ? option.value : raw;
}
export function Select({ options = [], value, onChange, mode, allowClear, placeholder, className, children, loading, ...props }: AnyProps) {
  const formDisabled = useContext(FormDisabledContext);
  const multiple = mode === "multiple" || mode === "tags";
  const current = multiple ? (value ?? []).map(String) : value == null ? "" : String(value);
  return <select {...omit(props, ["showSearch", "optionFilterProp", "popupMatchSelectWidth", "maxTagCount", "maxCount"])} disabled={props.disabled || formDisabled || loading} aria-busy={loading || undefined} className={cx("ui-select", className)} multiple={multiple} value={current}
    onChange={event => {
      if (multiple) onChange?.(Array.from(event.currentTarget.selectedOptions).map(option => nativeValue(options, option.value)));
      else onChange?.(nativeValue(options, event.currentTarget.value));
    }}>
    {(allowClear || placeholder || (!multiple && value == null)) && <option value="">{placeholder ?? "请选择"}</option>}
    {options.map((option: any, index: number) => <option className="ui-option" aria-disabled={option.disabled || undefined} key={`${String(option.value)}-${index}`} value={String(option.value)} disabled={option.disabled} onClick={() => {
      if (option.disabled) return;
      if (multiple) onChange?.((value ?? []).includes(option.value) ? (value ?? []).filter((item: any) => item !== option.value) : [...(value ?? []), option.value]);
      else onChange?.(option.value);
    }}>{typeof option.label === "string" || typeof option.label === "number" ? option.label : String(option.value)}</option>)}
    {children}
  </select>;
}

export function Checkbox({ checked, onChange, children, indeterminate, ...props }: AnyProps) {
  const formDisabled = useContext(FormDisabledContext);
  return <label className="ui-check"><input {...omit(props, ["value"])} disabled={props.disabled || formDisabled} value={props.value} type="checkbox" checked={checked} ref={element => { if (element) element.indeterminate = !!indeterminate; }} onChange={onChange} /> <span>{children}</span></label>;
}
Checkbox.Group = ({ options = [], value = [], onChange, children, ...props }: AnyProps) => {
  const bind = (child: any) => isValidElement(child) ? cloneElement(child as React.ReactElement<any>, {
    checked: value.includes((child.props as AnyProps).value),
    onChange: (event: any) => onChange?.(event.target.checked ? [...value, (child.props as AnyProps).value] : value.filter((entry: any) => entry !== (child.props as AnyProps).value)),
  }) : child;
  return <div {...props} className={cx("ui-choice-group", props.className)}>{children ? Children.map(children, bind) : options.map((option: any) => { const item = typeof option === "object" ? option : { label: option, value: option }; return <Checkbox key={String(item.value)} value={item.value} checked={value.includes(item.value)} disabled={item.disabled} onChange={(event: any) => onChange?.(event.target.checked ? [...value, item.value] : value.filter((entry: any) => entry !== item.value))}>{item.label}</Checkbox>; })}</div>;
};

export function Switch({ checked, onChange, checkedChildren, unCheckedChildren, loading, ...props }: AnyProps) {
  const formDisabled = useContext(FormDisabledContext), disabled = props.disabled || formDisabled;
  return <button {...props} disabled={disabled || loading} aria-busy={loading || undefined} type="button" role="switch" aria-checked={!!checked} className={cx("ui-switch", checked && "is-on", props.className)} onClick={() => !disabled && !loading && onChange?.(!checked)}><span>{checked ? checkedChildren : unCheckedChildren}</span></button>;
}

export function Radio({ checked, value, onChange, children, ...props }: AnyProps) {
  const context = useContext(RadioContext);
  const selected = checked ?? context?.value === value;
  return <label className="ui-radio"><input {...props} type="radio" value={value} checked={selected} onChange={event => { onChange?.(event); context?.onChange?.({ target: { value } }); }} /> <span>{children}</span></label>;
}
const RadioContext = createContext<any>(null);
Radio.Group = ({ options = [], value, onChange, children, className, ...props }: AnyProps) => <RadioContext.Provider value={{ value, onChange }}><div {...omit(props, ["optionType", "buttonStyle"])} className={cx("ui-choice-group", className)}>{children ?? options.map((option: any) => { const item = typeof option === "object" ? option : { label: option, value: option }; return <Radio key={String(item.value)} value={item.value} checked={item.value === value} disabled={item.disabled} onChange={() => onChange?.({ target: { value: item.value } })}>{item.label}</Radio>; })}</div></RadioContext.Provider>;
Radio.Button = ({ value, children, ...props }: AnyProps) => { const context = useContext(RadioContext); return <label className={cx("ui-radio-button", context?.value === value && "is-active", props.className)}><input {...omit(props, ["className"])} type="radio" value={value} checked={context?.value === value} onChange={() => context?.onChange?.({ target: { value } })} /><span>{children}</span></label>; };

export function Segmented({ options = [], value, onChange, block, className, ...props }: AnyProps) {
  return <div {...props} role="radiogroup" className={cx("ui-segmented", block && "ui-block", className)}>{options.map((option: any) => { const item = typeof option === "object" ? option : { value: option, label: option }; return <label key={String(item.value)} className={cx("ui-segmented-option", item.value === value && "is-active")}><input type="radio" name={props["aria-label"] ?? "segmented"} value={item.value} checked={item.value === value} disabled={item.disabled} onChange={() => onChange?.(item.value)} /><span>{item.label}</span></label>; })}</div>;
}

export function Modal({ open, title, children, footer, onCancel, onOk, okText = "确定", cancelText = "取消", okButtonProps = {}, cancelButtonProps = {}, confirmLoading, closable = true, closeIcon, width, className, styles, keyboard = true, ...props }: AnyProps) {
  const titleId = useId();
  const dialogRef = useRef<HTMLElement>(null);
  useEffect(() => { if (open) dialogRef.current?.focus(); }, [open]);
  useEffect(() => {
    if (!open || !keyboard) return;
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onCancel?.(); };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [keyboard, onCancel, open]);
  if (!open) return null;
  return <div className={cx("ui-modal-mask", props.centered && "is-centered")} role="presentation" onMouseDown={event => { const maskClosable = props.maskClosable ?? props.mask?.closable ?? true; if (event.target === event.currentTarget && maskClosable) onCancel?.(); }}>
    <section ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={title ? titleId : undefined} className={cx("ui-modal", className)} style={{ width, ...props.style }}>
      <header className="ui-modal-head"><div id={titleId} className="ui-dialog-title" role="heading" aria-level={2}>{title}</div>{closable && <button type="button" aria-label={isValidElement(closeIcon) ? (closeIcon.props as AnyProps)["aria-label"] ?? "关闭" : "关闭"} className="ui-close" onClick={onCancel}>{isValidElement(closeIcon) ? cloneElement(closeIcon as React.ReactElement<any>, { "aria-label": undefined }) : "×"}</button>}</header>
      <div className="ui-modal-body" style={styles?.body}>{children}</div>
      {footer !== null && <footer className="ui-modal-foot">{footer ?? <><Button {...cancelButtonProps} onClick={onCancel}>{cancelText}</Button><Button {...okButtonProps} loading={confirmLoading} type="primary" onClick={onOk}>{okText}</Button></>}</footer>}
    </section>
  </div>;
}

export function Drawer({ open, title, children, onClose, placement, width, ...props }: AnyProps) {
  if (!open && props.destroyOnHidden) return null;
  const drawerWidth = width ?? (typeof props.size === "number" ? props.size : props.size === "large" ? 520 : undefined);
  return <div className="ui-drawer-mask" hidden={!open} onMouseDown={event => event.target === event.currentTarget && onClose?.()}><aside {...omit(props, ["destroyOnHidden", "size", "styles", "maskClosable", "rootClassName"])} role="dialog" aria-label={typeof title === "string" ? title : undefined} className={cx("ui-drawer", placement && `ui-drawer-${placement}`, props.className)} style={{ width: drawerWidth, ...props.style }}><header className="ui-modal-head"><div className="ui-dialog-title">{title}</div><button type="button" aria-label="关闭" className="ui-close" onClick={onClose}>×</button></header><div className="ui-modal-body" style={props.styles?.body}>{children}</div></aside></div>;
}

export function Descriptions({ items = [], children, title, className, ...props }: AnyProps) {
  const childItems = Children.toArray(children).map((child: any, index) => ({ key: child.key ?? index, ...child.props }));
  return <section {...omit(props, ["column", "bordered", "size", "layout"])} className={cx("ui-descriptions", className)}>{title && <h3>{title}</h3>}<dl>{[...items, ...childItems].map((item: any, index) => <div key={item.key ?? index}><dt>{item.label}</dt><dd>{item.children}</dd></div>)}</dl></section>;
}
Descriptions.Item = (_props: AnyProps) => null;

function cellValue(record: any, dataIndex: any) { return Array.isArray(dataIndex) ? dataIndex.reduce((value, key) => value?.[key], record) : dataIndex == null ? undefined : record?.[dataIndex]; }
type TableProps<T> = Omit<AnyProps, "columns" | "dataSource"> & {
  columns?: ColumnsType<T>; dataSource?: T[]; rowKey?: string | ((record: T, index: number) => React.Key);
  rowSelection?: { selectedRowKeys?: React.Key[]; onChange?: (keys: React.Key[], rows: T[]) => void; getCheckboxProps?: (record: T) => AnyProps; [key: string]: any };
  expandable?: { expandedRowRender?: (record: T) => React.ReactNode; [key: string]: any };
  pagination?: false | { current?: number; pageSize?: number; total?: number; onChange?: (page: number, pageSize: number) => void; showTotal?: (total: number, range: [number, number]) => React.ReactNode; [key: string]: any };
};
export function Table<T = any>({ columns = [], dataSource = [], rowKey = "key", loading, locale, pagination, rowSelection, expandable, className, ...props }: TableProps<T>) {
  const pageSize = pagination && typeof pagination === "object" ? pagination.pageSize ?? dataSource.length : dataSource.length;
  const current = pagination && typeof pagination === "object" ? pagination.current ?? 1 : 1;
  const rows = pagination === false ? dataSource : dataSource.slice((current - 1) * pageSize, current * pageSize);
  const keyOf = (record: T, index: number) => typeof rowKey === "function" ? rowKey(record, index) : (record as any)[rowKey];
  return <div className={cx("ui-table-wrap", className)}>{loading && <Spin />}{!loading && !rows.length ? <Empty description={locale?.emptyText ?? "暂无数据"} /> : <table {...omit(props, ["scroll", "size", "sticky"])} className="ui-table">
    <thead><tr>{rowSelection && <th><Checkbox checked={dataSource.length > 0 && dataSource.every((row, index) => rowSelection.selectedRowKeys?.includes(keyOf(row, index)))} onChange={(event: any) => rowSelection.onChange?.(event.target.checked ? dataSource.map(keyOf) : [], event.target.checked ? dataSource : [])} /></th>}{columns.map((column: any, index: number) => <th key={column.key ?? column.dataIndex ?? index}>{column.title}</th>)}</tr></thead>
    <tbody>{rows.map((record, rowIndex) => { const key = keyOf(record, rowIndex), checkboxProps = rowSelection?.getCheckboxProps?.(record) ?? {}; return <React.Fragment key={key ?? rowIndex}><tr>{rowSelection && <td><Checkbox {...checkboxProps} checked={rowSelection.selectedRowKeys?.includes(key)} onChange={(event: any) => { const selected = rowSelection.selectedRowKeys ?? []; const keys = event.target.checked ? [...selected, key] : selected.filter((item: any) => item !== key); rowSelection.onChange?.(keys, dataSource.filter((row, index) => keys.includes(keyOf(row, index)))); }} /></td>}{columns.map((column: any, index: number) => { const value = cellValue(record, column.dataIndex); return <td key={column.key ?? column.dataIndex ?? index}>{column.render ? column.render(value, record, rowIndex) : value as any}</td>; })}</tr>{expandable?.expandedRowRender && expandable?.defaultExpandAllRows && <tr><td colSpan={columns.length + (rowSelection ? 1 : 0)}>{expandable.expandedRowRender(record)}</td></tr>}</React.Fragment>; })}</tbody>
  </table>}{pagination && typeof pagination === "object" && <Pagination {...pagination} total={pagination.total ?? dataSource.length} />}</div>;
}

export function Pagination({ current = 1, pageSize = 10, total = 0, onChange, ...props }: AnyProps) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const first = total ? (current - 1) * pageSize + 1 : 0, last = Math.min(total, current * pageSize);
  return <nav {...omit(props, ["showSizeChanger", "showQuickJumper", "showTotal", "hideOnSinglePage", "size", "pageSizeOptions"])} className={cx("ui-pagination", props.className)} aria-label="分页">{props.showTotal && <span>{props.showTotal(total, [first, last])}</span>}<Button title="上一页" disabled={current <= 1} onClick={() => onChange?.(current - 1, pageSize)}>上一页</Button><span>{current} / {pages}</span><Button title="下一页" disabled={current >= pages} onClick={() => onChange?.(current + 1, pageSize)}>下一页</Button></nav>;
}

export function Tabs({ items = [], activeKey, defaultActiveKey, onChange, className, ...props }: AnyProps) {
  const [local, setLocal] = useState(defaultActiveKey ?? items[0]?.key);
  const selected = activeKey ?? local;
  const choose = (key: string) => { if (activeKey == null) setLocal(key); onChange?.(key); };
  return <section {...omit(props, ["destroyOnHidden", "tabPosition"])} className={cx("ui-tabs", className)}><div role="tablist" className="ui-tablist">{items.map((item: any) => <button type="button" role="tab" aria-selected={item.key === selected} aria-controls={`ui-panel-${item.key}`} id={`ui-tab-${item.key}`} disabled={item.disabled} key={item.key} onClick={() => choose(item.key)}>{item.label}</button>)}</div>{items.map((item: any) => item.key === selected && <div role="tabpanel" aria-labelledby={`ui-tab-${item.key}`} id={`ui-panel-${item.key}`} key={item.key} className="ui-tabpanel">{item.children}</div>)}</section>;
}

export function Collapse({ items = [], defaultActiveKey = [], activeKey, onChange, ...props }: AnyProps) {
  const initial = new Set(Array.isArray(defaultActiveKey) ? defaultActiveKey : [defaultActiveKey]);
  const [open, setOpen] = useState(initial);
  const controlled = new Set(Array.isArray(activeKey) ? activeKey : activeKey == null ? [] : [activeKey]);
  const current = activeKey == null ? open : controlled;
  return <div {...omit(props, ["destroyOnHidden", "accordion", "bordered", "size", "expandIconPosition"])} className={cx("ui-collapse", props.className)}>{items.map((item: any) => { const expanded = current.has(item.key); return <section key={item.key}><button type="button" className="ui-collapse-trigger" aria-expanded={expanded} onClick={() => { const next = new Set(current); expanded ? next.delete(item.key) : next.add(item.key); if (activeKey == null) setOpen(next); onChange?.([...next]); }}>{item.label}</button>{expanded && <div className="ui-collapse-body">{item.children}</div>}</section>; })}</div>;
}

export function Popconfirm({ title, description, onConfirm, children, disabled, okText = "确定" }: AnyProps) {
  if (!isValidElement(children)) return children;
  return <InlineConfirm title={title} description={description} onConfirm={onConfirm} disabled={disabled} okText={okText}>{children}</InlineConfirm>;
}

function InlineConfirm({ title, description, onConfirm, children, disabled, okText }: AnyProps) {
  const [open, setOpen] = useState(false);
  const child = children as React.ReactElement<any>;
  return <span className="ui-popconfirm">{cloneElement(child, {
    disabled: disabled || child.props.disabled,
    onClick: (event: React.MouseEvent) => { child.props.onClick?.(event); if (!disabled) setOpen(true); },
  })}{open && <span className="ui-popconfirm-panel" role="dialog" aria-label={typeof title === "string" ? title : "确认操作"}><strong>{title}</strong>{description && <span>{description}</span>}<span className="ui-popconfirm-actions"><Button onClick={() => setOpen(false)}>取消</Button><Button danger type="primary" onClick={() => { setOpen(false); onConfirm?.(); }}>{okText}</Button></span></span>}</span>;
}

export function Upload({ children, beforeUpload, accept, disabled, multiple, showUploadList: _showUploadList, maxCount: _maxCount, ...props }: AnyProps) {
  return <label className={cx("ui-upload", disabled && "is-disabled")}><input type="file" hidden accept={accept} disabled={disabled} multiple={multiple} onChange={event => { Array.from(event.target.files ?? []).forEach(file => { if (beforeUpload?.(file) === Upload.LIST_IGNORE) Object.defineProperty(file, Upload.LIST_IGNORE, { configurable: true, value: true }); }); event.currentTarget.value = ""; }} />{children}</label>;
}
Upload.LIST_IGNORE = "__LIST_IGNORE__";

export const Typography: any = {};
Typography.Title = ({ level = 1, children, className, ...props }: AnyProps) => React.createElement(`h${Math.min(6, Math.max(1, level))}`, { ...props, className: cx("ui-title", className) }, children);
Typography.Paragraph = ({ children, copyable, ellipsis, className, ...props }: AnyProps) => <p {...omit(props, ["type", "editable"])} className={cx("ui-paragraph", props.type && `ui-text-${props.type}`, className)}>{children}{copyable && <Button aria-label="复制" onClick={() => navigator.clipboard?.writeText(String(children))}>复制</Button>}</p>;
Typography.Text = ({ children, strong, code, keyboard, copyable, className, ...props }: AnyProps) => { const content = code ? <code>{children}</code> : keyboard ? <kbd>{children}</kbd> : children; return <span {...omit(props, ["type", "ellipsis", "delete", "mark"])} className={cx("ui-text", props.type && `ui-text-${props.type}`, className)} style={{ fontWeight: strong ? 700 : undefined, ...props.style }}>{content}{copyable && <Button aria-label="复制" onClick={() => navigator.clipboard?.writeText(String(children))}>复制</Button>}</span>; };
Typography.Link = ({ children, ...props }: AnyProps) => <a {...props}>{children}</a>;

export function Tag({ children, color, closable, onClose, className, ...props }: AnyProps) { return <span {...props} className={cx("ui-tag", color && `ui-tag-${color}`, className)}>{children}{closable && <button type="button" aria-label="移除" onClick={onClose}>×</button>}</span>; }
export const Empty: any = ({ description = "暂无数据", children, ...props }: AnyProps) => <div {...props} title={typeof description === "string" ? description : undefined} className={cx("ui-empty", props.className)}><span className="ui-empty-mark" aria-hidden>—</span><div className="ui-empty-description">{description}</div>{children}</div>;
Empty.PRESENTED_IMAGE_SIMPLE = null;
export function Spin({ tip, children, spinning = true, ...props }: AnyProps) { return children ? <div className="ui-spin-container">{spinning && <div className="ui-spin-overlay"><span className="ui-spinner" />{tip}</div>}{children}</div> : spinning ? <span {...props} className={cx("ui-spinner", props.className)} aria-label={tip ?? "加载中"} /> : null; }
export function Progress({ percent = 0, status, format, showInfo = true, size: _size, className, ...props }: AnyProps) { return <div {...props} className={cx("ui-progress", status && `ui-progress-${status}`, className)}><progress max={100} value={percent} />{showInfo && <span>{format ? format(percent) : `${Math.round(percent)}%`}</span>}</div>; }
export function Divider({ children, ...props }: AnyProps) { return <div {...props} className={cx("ui-divider", props.className)}>{children && <span>{children}</span>}</div>; }
export function Avatar({ children, src, icon, size, ...props }: AnyProps) { return <span {...props} className={cx("ui-avatar", props.className)} style={{ width: size, height: size, ...props.style }}>{src ? <img src={src} alt="" /> : icon ?? children}</span>; }
export function Image(props: AnyProps) { return <img {...omit(props, ["preview"])} />; }
export function Statistic({ title, value, suffix, prefix, precision, styles, className, ...props }: AnyProps) { const display = typeof value === "number" && precision != null ? value.toFixed(precision) : value; return <div {...props} className={cx("ui-statistic", className)}><span>{title}</span><strong style={styles?.content}>{prefix}{display}{suffix}</strong></div>; }
export function Tooltip({ title, children }: AnyProps) { return isValidElement(children) ? cloneElement(children as React.ReactElement<any>, { title: typeof title === "string" ? title : undefined }) : <span title={typeof title === "string" ? title : undefined}>{children}</span>; }
export function Result({ status, title, subTitle, extra, icon, ...props }: AnyProps) { return <section {...props} className={cx("ui-result", status && `ui-result-${status}`, props.className)}>{icon}<h2>{title}</h2><div>{subTitle}</div>{extra}</section>; }
export function AutoComplete({ onChange, options = [], ...props }: AnyProps) { const listId = `${props.id ?? props["aria-label"] ?? "autocomplete"}-options`; return <><Input {...props} list={listId} onChange={event => onChange?.(event.target.value)} /><datalist id={listId}>{options.map((option: any) => <option key={option.value} value={option.value}>{option.label}</option>)}</datalist></>; }

export function Row({ children, gutter, className, ...props }: AnyProps) {
  const horizontal = Array.isArray(gutter) ? gutter[0] : gutter;
  const vertical = Array.isArray(gutter) ? gutter[1] : gutter;
  const style = { "--ui-row-gap-x": `${Number(horizontal ?? 0)}px`, rowGap: vertical, ...props.style } as React.CSSProperties;
  return <div {...omit(props, ["align", "justify"])} className={cx("ui-row", className)} style={style}>{children}</div>;
}
export function Col({ children, flex, className, ...props }: AnyProps) {
  const span = props.span ?? 24;
  const style = {
    "--ui-col": Math.min(24, Number(span)),
    "--ui-col-xs": Math.min(24, Number(props.xs ?? span)),
    "--ui-col-sm": Math.min(24, Number(props.sm ?? props.xs ?? span)),
    "--ui-col-md": Math.min(24, Number(props.md ?? props.sm ?? props.xs ?? span)),
    "--ui-col-lg": Math.min(24, Number(props.lg ?? props.md ?? props.sm ?? props.xs ?? span)),
    "--ui-col-xl": Math.min(24, Number(props.xl ?? props.lg ?? props.md ?? props.sm ?? props.xs ?? span)),
    "--ui-col-xxl": Math.min(24, Number(props.xxl ?? props.xl ?? props.lg ?? props.md ?? props.sm ?? props.xs ?? span)),
    ...(flex ? { flex } : {}), ...props.style,
  } as React.CSSProperties;
  return <div className={cx("ui-col", className)} style={style}>{children}</div>;
}

export const Layout: any = ({ children, className, ...props }: AnyProps) => <div {...props} className={cx("ui-layout", className)}>{children}</div>;
Layout.Header = ({ children, className, ...props }: AnyProps) => <header {...props} className={cx("ui-layout-header", className)}>{children}</header>;
Layout.Content = ({ children, className, ...props }: AnyProps) => <main {...props} className={cx("ui-layout-content", className)}>{children}</main>;
export const theme = { darkAlgorithm: "dark", defaultAlgorithm: "light", useToken: () => ({ token: { colorBgLayout: "var(--ui-bg)", colorBgContainer: "var(--ui-surface)", colorText: "var(--ui-text)", colorTextSecondary: "var(--ui-muted)", colorBorderSecondary: "var(--ui-border)", colorPrimary: "var(--ui-primary)", colorSuccess: "#17834f", colorWarning: "#a46000", colorError: "#c63b3b", borderRadiusLG: 14, boxShadowTertiary: "0 16px 45px rgba(15,23,42,.1)" } }) };
