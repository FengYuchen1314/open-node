import { translateKnownMessage } from "./messages";

export const UI_LOCALE = "zh-CN";

const statuses: Record<string, string> = {
  active: "有效", inactive: "未启用", enabled: "已启用", disabled: "已停用",
  available: "可用", unavailable: "不可用", online: "在线", offline: "离线",
  healthy: "正常", unhealthy: "异常", degraded: "降级", unknown: "未知",
  running: "运行中", stopped: "已停止", starting: "启动中", stopping: "停止中",
  pending: "待处理", queued: "排队中", leased: "已领取", dispatched: "已下发",
  waiting: "等待中", provisioning: "配置中",
  in_progress: "进行中", processing: "处理中", completed: "已完成", succeeded: "已成功",
  success: "成功", failed: "失败", error: "错误", cancelled: "已取消", canceled: "已取消",
  timeout: "超时", timed_out: "已超时", expired: "已过期", revoked: "已撤销",
  used: "已使用", unused: "未使用", ready: "就绪", not_ready: "未就绪",
  draft: "草稿", planned: "已规划", validated: "已验证", invalid: "无效",
  applying: "应用中", applied: "已应用", blocked: "受阻", skipped: "已跳过",
  rolling_back: "回退中", rolled_back: "已回退", rollback_failed: "回退失败",
  rollback_queued: "回退排队中", rollback_incomplete: "回退未完成", needs_review: "需要复核",
  compensating: "补偿中", compensated: "已补偿", compensation_failed: "补偿失败",
  partially_applied: "部分应用", partially_failed: "部分失败", needs_attention: "需要处理",
  new: "新增", updated: "已更新", unchanged: "未变化", missing: "缺失",
  present: "存在", removed: "已移除", removing: "移除中", deleting: "删除中",
  connected: "已连接", disconnected: "已断开", reconnecting: "重连中",
  supported: "支持", unsupported: "不支持", verified: "已核验", unverified: "未核验",
  managed: "托管", external: "外部", physical: "物理节点", routed: "路由节点",
  historical: "历史记录", pending_recovery: "待恢复", stale: "已过时",
  missing_runtime: "缺少运行时", unmanaged: "未托管", in_sync: "已同步",
  user: "用户", admin: "管理员", administrator: "管理员", free: "免费版",
  valid: "有效", warning: "警告", info: "提示", critical: "严重",
  light: "浅色", dark: "深色", system: "跟随系统", auto: "自动", manual: "手动",
  none: "无", never: "从未", empty: "空", all: "全部", default: "默认",
  public: "公开", private: "私有", server: "服务器", node: "节点",
  issued: "已签发", renewing: "续期中", renewed: "已续期", imported: "已导入",
  interrupted: "已中断", issuing: "签发中", revoking: "吊销中",
  update_account: "更新账户", issue: "签发", renew: "续期", revoke: "吊销",
  account: "账户更新", deployed: "已部署",
  installed: "已安装", not_installed: "未安装", installing: "安装中",
  claimed: "已领取", registered: "已注册", consumed: "已使用", exhausted: "已耗尽",
  suspended: "已暂停", restored: "已恢复", archived: "已归档", accepted: "已接受",
  rejected: "已拒绝", confirmed: "已确认", awaiting_confirmation: "等待确认",
};

/** Display only: never replace API values, identifiers, user names or configuration. */
export function zhStatus(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "未提供";
  return statuses[value.toLowerCase()] ?? value;
}

/** Display feedback only; callers must retain their API validation and secret safeguards. */
export function zhMessage(value: unknown, fallback = "操作未完成，请检查当前状态后重试。"): string {
  const message = value instanceof Error ? value.message : typeof value === "string" ? value : "";
  if (!message) return fallback;
  const translated = translateKnownMessage(message);
  if (translated) return translated;
  // Already-localized, application-owned text is not translated a second time.
  if (/[\u3400-\u9fff]/u.test(message)) return message;
  return fallback;
}
