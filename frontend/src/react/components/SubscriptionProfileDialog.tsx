import { zhMessage } from "../../i18n/zh-CN";
import { useEffect, useRef, useState } from "react";
import { Alert, Flex, Form, Input, Modal, Select, Spin, Switch } from "antd";
import type { ManagedNode, ProductUser } from "../../domain/subscriptions";
import type { SubscriptionProfile } from "../../domain/subscription-profiles";
import type { SubscriptionTemplate } from "../../domain/subscription-templates";
import { updateSubscriptionProfile } from "../../services/subscription-profiles";
import type { CustomRule, ProxyProvider } from "../../domain/subscription-customizations";
import { listCustomRules, listProxyProviders } from "../../services/subscription-customizations";

export interface SubscriptionProfileDialogProps { open: boolean; profile: SubscriptionProfile | null; nodes: ManagedNode[]; users: ProductUser[]; templates: SubscriptionTemplate[]; onOpenChange: (open: boolean) => void; onSaved?: (profile: SubscriptionProfile) => void }
export default function SubscriptionProfileDialog(props: SubscriptionProfileDialogProps) { return props.open && props.profile ? <ProfileContent key={`${props.profile.id}:${props.profile.revision}`} {...props} profile={props.profile} /> : null; }
function ProfileContent({ profile, nodes, users, templates, onOpenChange, onSaved }: SubscriptionProfileDialogProps & { profile: SubscriptionProfile }) {
  const [form, setForm] = useState(() => ({ name: profile.name, description: profile.description, node_ids: [...profile.node_ids], assigned_usernames: [...profile.assigned_usernames], clash_template_id: profile.clash_template_id, surge_template_id: profile.surge_template_id, custom_rules_enabled: profile.custom_rules_enabled ?? false, selected_custom_rule_ids: [...(profile.selected_custom_rule_ids ?? [])], proxy_providers_enabled: profile.proxy_providers_enabled ?? false, selected_proxy_provider_ids: [...(profile.selected_proxy_provider_ids ?? [])], enabled: profile.enabled }));
  const [rules, setRules] = useState<CustomRule[]>([]), [providers, setProviders] = useState<ProxyProvider[]>([]);
  const [resourcesBusy, setResourcesBusy] = useState(true);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const version = useRef(0); useEffect(() => () => { ++version.current; }, []);
  useEffect(() => {
    const run = ++version.current; setResourcesBusy(true);
    Promise.all([listCustomRules(), listProxyProviders()]).then(([ruleList, providerList]) => {
      if (run !== version.current) return;
      setRules(ruleList.rules.filter(item => item.owner_username === profile.owner_username));
      setProviders(providerList.providers.filter(item => item.owner_username === profile.owner_username));
    }).catch(failure => { if (run === version.current) setError(failure instanceof Error ? failure.message : "读取订阅规则失败"); })
      .finally(() => { if (run === version.current) setResourcesBusy(false); });
  }, [profile.owner_username]);
  function patch(change: Partial<typeof form>) { setForm(previous => ({ ...previous, ...change })); }
  async function save() {
    if (busy || !form.name.trim()) return;
    const run = ++version.current; setBusy(true); setError("");
    try {
      const value = await updateSubscriptionProfile(profile.id, { ...form, expected_revision: profile.revision });
      if (run !== version.current) return; onSaved?.(value); onOpenChange(false);
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "更新订阅配置失败"); }
    finally { if (run === version.current) setBusy(false); }
  }
  return <Modal open title="编辑订阅配置" width={720} destroyOnHidden mask={{ closable: !busy }} keyboard={!busy} closable={!busy} onCancel={() => !busy && onOpenChange(false)} okText="保存" confirmLoading={busy} okButtonProps={{ "aria-label": "保存", "aria-busy": busy, disabled: !form.name.trim() }} cancelButtonProps={{ disabled: busy }} onOk={() => void save()}>
    <Flex vertical gap="middle">{error && <Alert type="error" title={zhMessage(error)} showIcon />}{!!profile.migration_warnings.length && <Alert type="warning" title={profile.migration_warnings.map(warning => zhMessage(warning)).join("；")} showIcon />}
      <Form layout="vertical" preserve={false} disabled={busy}>
        <Form.Item label="名称"><Input aria-label="名称" value={form.name} onChange={event => patch({ name: event.target.value })} /></Form.Item>
        <Form.Item label="已启用"><Switch aria-label="已启用" checked={form.enabled} onChange={enabled => patch({ enabled })} /></Form.Item>
        <Form.Item label="说明"><Input.TextArea aria-label="说明" rows={2} value={form.description} onChange={event => patch({ description: event.target.value })} /></Form.Item>
        <Form.Item label="分配用户"><Select aria-label="分配用户" mode="multiple" value={form.assigned_usernames} onChange={assigned_usernames => patch({ assigned_usernames })} options={users.filter(user => !user.removal_id).map(user => ({ label: user.display_name || user.username, value: user.username }))} /></Form.Item>
        <Form.Item label="节点范围" extra="留空时使用各用户套餐内的全部节点"><Select aria-label="节点范围" mode="multiple" value={form.node_ids} onChange={node_ids => patch({ node_ids })} options={nodes.filter(node => !node.removal_id).map(node => ({ label: node.name, value: node.id }))} /></Form.Item>
        {(["clash", "surge"] as const).map(format => <Form.Item key={format} label={`${format === "clash" ? "Clash" : "Surge"} 模板`}><Select aria-label={`${format === "clash" ? "Clash" : "Surge"} 模板`} allowClear value={form[`${format}_template_id`] ?? undefined} onChange={value => patch({ [`${format}_template_id`]: value ?? null })} options={templates.filter(template => template.format === format).map(template => ({ label: template.name, value: template.id }))} /></Form.Item>)}
        <Form.Item label="自定义规则" extra="仅应用于 Clash / Stash。开启后留空表示应用该所有者的全部已启用规则。"><Flex vertical gap="small"><Switch aria-label="启用订阅自定义规则" checked={form.custom_rules_enabled} onChange={custom_rules_enabled => patch({ custom_rules_enabled })} />{form.custom_rules_enabled && (resourcesBusy ? <Spin size="small" /> : <Select aria-label="订阅自定义规则" mode="multiple" allowClear placeholder="留空：全部已启用规则" value={form.selected_custom_rule_ids} onChange={selected_custom_rule_ids => patch({ selected_custom_rule_ids })} options={rules.map(item => ({ label: `${item.name} · ${item.enabled ? "已启用" : "已停用"}`, value: item.id, disabled: !item.enabled }))} />)}</Flex></Form.Item>
        <Form.Item label="Proxy Provider" extra="仅应用于 Clash / Stash；公开 URL 使用当前订阅短码鉴权，不包含上游订阅地址。"><Flex vertical gap="small"><Switch aria-label="启用订阅代理集合" checked={form.proxy_providers_enabled} onChange={proxy_providers_enabled => patch({ proxy_providers_enabled })} />{form.proxy_providers_enabled && (resourcesBusy ? <Spin size="small" /> : <Select aria-label="订阅代理集合" mode="multiple" allowClear placeholder="留空：全部已启用代理集合" value={form.selected_proxy_provider_ids} onChange={selected_proxy_provider_ids => patch({ selected_proxy_provider_ids })} options={providers.map(item => ({ label: `${item.name} · ${item.enabled ? "已启用" : "已停用"}`, value: item.id, disabled: !item.enabled }))} />)}</Flex></Form.Item>
      </Form>
    </Flex>
  </Modal>;
}
