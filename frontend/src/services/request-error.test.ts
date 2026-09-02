import { describe, expect, it } from "vitest";
import { translateKnownMessage } from "../i18n/messages";
import { requestError, requestFailureMessage } from "./request-error";

describe("trusted Chinese request feedback", () => {
  it.each([
    ["Invalid password", "密码错误。"],
    ["Invalid second factor", "双重验证失败。"],
    ["Connection unavailable", "连接不可用。"],
    ["Invalid CSRF token", "会话验证已失效，请刷新页面后重试。"],
    ["Server settings changed; refresh before saving", "服务器设置已发生变化，请刷新后保存。"],
    ["User or credentials changed; reload before saving", "用户或凭据已发生变化，请重新加载后保存。"],
    ["This short code is unavailable", "此短码不可用。"],
    ["Package mappings reference unknown legacy package IDs", "套餐映射引用了未知的旧套餐 ID。"],
    ["limiter revision changed; refresh before applying", "限速设置版本已变化，请刷新后应用。"],
    ["Use a hostname without a scheme, port or path", "请输入不含协议、端口或路径的主机名。"],
    ["Hysteria v1 cannot be imported as Hysteria2.", "不能将 Hysteria v1 作为 Hysteria2 导入。"],
    ["Verified Agent release is not available", "没有可用的已验证 Agent 发布版本。"],
    ["Set OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL to the canonical HTTPS control-plane URL; the Agent requires the default /api/v1 prefix", "请将 OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL 设置为控制面的规范 HTTPS URL；Agent 必须使用默认的 /api/v1 前缀。"],
  ])("localizes the exact known message %s", (original, translated) => {
    expect(translateKnownMessage(original)).toBe(translated);
    expect(requestError(original, "请求失败。").message).toBe(translated);
    expect(translateKnownMessage(`${original}: https://provider.example/?token=secret`)).toBeUndefined();
    expect(translateKnownMessage(`https://provider.example/?token=secret: ${original}`)).toBeUndefined();
    expect(translateKnownMessage(`${original}\n`)).toBeUndefined();
  });

  // All 22 fixed outcomes emitted by certificate_worker.py, including its
  // ADMIN_ERRORS and the four explicitly allowed CA error-code suffixes.
  it.each([
    ["Resuming reconciliation with the CA", "正在恢复与 CA 的结果核对。"],
    ["ACME execution was interrupted; retry after inspecting the certificate", "ACME 执行已中断，请检查证书后重试。"],
    ["CA reports this certificate is already revoked", "CA 已确认此证书已被吊销。"],
    ["The certificate is not due for renewal", "证书尚未到续期时间。"],
    ["CA administration did not finish; retry to reconcile its result", "CA 管理操作尚未完成，请重试以核实结果。"],
    ["ACME job failed; existing certificate material was retained", "ACME 任务失败，现有证书材料已保留。"],
    ["Operation paused; reconciliation resumes after restart", "操作已暂停，重启后将继续核对结果。"],
    ["ACME directory is not enabled by the host administrator", "主机管理员未启用此 ACME 目录。"],
    ["The existing ACME account key is missing", "现有 ACME 账户密钥缺失。"],
    ["Another key or account already occupies the new storage name", "新的存储名称已被其他密钥或账户占用。"],
    ["The ACME account is not active", "ACME 账户未处于有效状态。"],
    ["An established EAB binding cannot be changed", "已建立的 EAB 绑定不能更改。"],
    ["The CA did not confirm the requested account contact", "CA 尚未确认请求的账户联系信息。"],
    ["CA result is not confirmed; retry to reconcile", "CA 结果尚未确认，请重试以核实结果。"],
    ["CA result is not confirmed; retry to reconcile (unauthorized)", "CA 结果尚未确认，请重试以核实结果（unauthorized）。"],
    ["CA result is not confirmed; retry to reconcile (badRevocationReason)", "CA 结果尚未确认，请重试以核实结果（badRevocationReason）。"],
    ["CA result is not confirmed; retry to reconcile (accountDoesNotExist)", "CA 结果尚未确认，请重试以核实结果（accountDoesNotExist）。"],
    ["CA result is not confirmed; retry to reconcile (serverInternal)", "CA 结果尚未确认，请重试以核实结果（serverInternal）。"],
    ["CA revocation result does not match the certificate", "CA 吊销结果与此证书不匹配。"],
    ["ACME client exceeded its output limit", "ACME 客户端输出超出大小限制。"],
    ["ACME validation or issuance failed; check HTTP/DNS routing and CA settings", "ACME 验证或签发失败，请检查 HTTP/DNS 路由和 CA 设置。"],
    ["ACME job timed out; verify the result before retrying", "ACME 任务超时，请核实结果后再重试。"],
  ])("preserves the exact certificate worker outcome: %s", (original, translated) => {
    expect(translateKnownMessage(original)).toBe(translated);
    expect(requestError(original, "请求失败。").message).toBe(translated);
    expect(requestFailureMessage(new Error(original), "请求失败。")).toBe(translated);
    for (const modified of [original + " ", original + "\n", original + ": private-token", "private-token: " + original]) {
      expect(translateKnownMessage(modified)).toBeUndefined();
      expect(requestError(modified, "请求失败。").message).toBe("请求失败。");
    }
  });

  it.each([
    ["Protocol is not supported by this client format", "此客户端格式不支持该协议。"],
    ["Server address is missing", "缺少服务器地址。"],
    ["Server port is invalid", "服务器端口无效。"],
    ["User credential is missing", "缺少用户凭据。"],
    ["Snell version is not supported", "不支持此 Snell 版本。"],
    ["Snell v6 requires the Xray compatibility client", "Snell v6 需要使用 Xray 兼容客户端。"],
    ["Snell v6 mode does not provide authenticated user access", "Snell v6 当前模式不提供需要身份验证的用户访问。"],
    ["Snell obfuscation mode is not supported", "不支持此 Snell 混淆模式。"],
    ["Mieru username is missing", "缺少 Mieru 用户名。"],
    ["Mieru transport is not supported", "不支持此 Mieru 传输方式。"],
    ["Transport is not supported by this client format", "此客户端格式不支持该传输方式。"],
    ["This Trojan transport is not supported by Mihomo", "Mihomo 不支持此 Trojan 传输方式。"],
    ["This protocol does not support a V2Ray transport wrapper", "此协议不支持 V2Ray 传输封装。"],
    ["Certificate verification flag must be a boolean", "证书验证标志必须为布尔值。"],
    ["REALITY cannot disable TLS", "REALITY 不能禁用 TLS。"],
    ["This protocol requires TLS", "此协议必须使用 TLS。"],
    ["Custom TLS options require native sing-box export", "自定义 TLS 选项需要使用 sing-box 原生格式导出。"],
    ["Custom uTLS options require native sing-box export", "自定义 uTLS 选项需要使用 sing-box 原生格式导出。"],
    ["Mihomo certificate verification options require Clash export", "Mihomo 证书验证选项需要使用 Clash 格式导出。"],
    ["Mihomo Trojan requires TLS", "Mihomo 的 Trojan 必须使用 TLS。"],
    ["Xray WebSocket early data requires Sec-WebSocket-Protocol", "Xray WebSocket 早期数据必须使用 Sec-WebSocket-Protocol。"],
    ["Shadowsocks plugin conversion is not supported", "不支持 Shadowsocks 插件转换。"],
    ["Mihomo v1.19.30 URI import requires native export for HTTPUpgrade", "Mihomo v1.19.30 通过 URI 导入 HTTPUpgrade 时，需要改用原生格式导出。"],
    ["TLS for this protocol requires native client export", "此协议的 TLS 配置需要使用客户端原生格式导出。"],
    ["Custom transport headers or early data require native client export", "自定义传输头或早期数据需要使用客户端原生格式导出。"],
    ["Hysteria2 obfuscation, bandwidth or port hopping requires another format", "Hysteria2 混淆、带宽或端口跳跃配置需要使用其他格式。"],
    ["Hysteria2 obfuscation requires salamander and a password", "Hysteria2 混淆必须使用 salamander，并设置密码。"],
    ["Hysteria2 bandwidth conversion requires integer Mbps", "Hysteria2 带宽转换要求以 Mbps 为单位的整数。"],
    ["REALITY requires VLESS and a public key", "REALITY 必须使用 VLESS，并提供公钥。"],
    ["Custom certificate material requires another client format", "自定义证书内容需要使用其他客户端格式。"],
  ])("localizes the exact compatibility reason %s without echoing node names", (original, translated) => {
    expect(translateKnownMessage(original)).toBe(translated);
    expect(requestError(original, "请求失败。").message).toBe(translated);
    expect(translateKnownMessage(`${original}: https://provider.example/?token=secret`)).toBeUndefined();
    const warning = `private-token: https://provider.example/?token=secret: ${original}`;
    const feedback = `已排除不兼容节点：${translated}`;
    expect(translateKnownMessage(warning)).toBe(feedback);
    expect(requestError(warning, "请求失败。").message).toBe(feedback);
    expect(requestFailureMessage(new Error(warning), "连接失败。")).toBe(feedback);
    expect(feedback).not.toMatch(/private-token|provider\.example|secret/u);
    expect(translateKnownMessage(`${original}\n`)).toBeUndefined();
  });

  it.each([
    "https://provider.example/sub?token=secret", "上游令牌：secret", "Invalid password secret",
    "Invalid password\n", "constructor", "toString", "__proto__", "x".repeat(4097),
    "Value error, https://provider.example/secret", "Input should be greater than secret",
    "Excluded 2 nodes", "Empty group uses REJECT: ", "Empty group uses REJECT:   ",
    "Empty group uses REJECT:", "Empty group uses REJECT:private-token",
    "private-token: Invalid password", "private-token: Required", "private-token: Network error",
    "private-token: constructor", "private-token: __proto__", "private-token: unknown compatibility reason",
    "private-token: Server address is missing ", "private-token: server address is missing",
    "private-token:  Server address is missing", ": Server address is missing", "   : Server address is missing",
    "private-token\n: Server address is missing", "private-token: Server address is missing\u007f",
    "Empty group uses REJECT: private-token\n", "Empty group uses REJECT: private-token\u0000",
    "CA reports this certificate is already revoked ",
    "Provider failure: already revoked https://provider.example/?token=secret",
    "CA result is not confirmed; retry to reconcile (already revoked)",
    "CA result is not confirmed; retry to reconcile (private-token)",
    "CA result is not confirmed; retry to reconcile (serverInternal: private-token)",
  ])("never displays an unknown or modified upstream message", value => {
    expect(translateKnownMessage(value)).toBeUndefined();
    expect(requestError(value, "请求失败。").message).toBe("请求失败。");
    expect(requestFailureMessage(new Error(value), "连接失败。")).toBe("连接失败。");
  });

  it.each([
    "private-token", "https://provider.example/?token=secret", "用户的私有策略组",
    "private-token: Server address is missing",
  ])("localizes an empty-group warning without echoing the group %s", name => {
    const warning = `Empty group uses REJECT: ${name}`;
    const feedback = "空策略组已使用 REJECT，请检查模板中的组成员。";
    expect(translateKnownMessage(warning)).toBe(feedback);
    expect(requestError(warning, "请求失败。").message).toBe(feedback);
    expect(requestFailureMessage(new Error(warning), "连接失败。")).toBe(feedback);
    expect(feedback).not.toContain(name);
  });

  it("bounds composite warnings before recognizing their safe templates", () => {
    const suffix = ": Server address is missing";
    const acceptedNodeWarning = "x".repeat(4096 - suffix.length) + suffix;
    expect(translateKnownMessage(acceptedNodeWarning)).toBe("已排除不兼容节点：缺少服务器地址。");
    expect(translateKnownMessage("x" + acceptedNodeWarning)).toBeUndefined();

    const prefix = "Empty group uses REJECT: ";
    const acceptedGroupWarning = prefix + "x".repeat(4096 - prefix.length);
    expect(translateKnownMessage(acceptedGroupWarning)).toBe("空策略组已使用 REJECT，请检查模板中的组成员。");
    expect(translateKnownMessage(acceptedGroupWarning + "x")).toBeUndefined();
  });

  it.each([
    ["legacy package short code collides", "旧套餐短码存在冲突。"],
    ["package short code is already in use", "套餐短码已被使用。"],
    ["existing subscription profile will be preserved", "已有订阅配置将予以保留。"],
    ["legacy file short code collides", "旧订阅文件短码存在冲突。"],
    ["file short code is already in use", "订阅文件短码已被使用。"],
    ["raw output is imported disabled until reconfigured", "原始输出将以停用状态导入，重新配置后才能启用。"],
    ["legacy rules or scripts are not executed by Open Node", "Open Node 不会执行旧版规则或脚本。"],
    ["user removal is pending", "用户移除尚未完成。"],
    ["current plan differs from the selected legacy mapping", "用户当前套餐与所选旧套餐映射不一致。"],
    ["source administrator will import as subscriber", "来源管理员将按普通用户导入。"],
    ["existing login account will be preserved", "已有登录账户将予以保留。"],
    ["configure OPEN_NODE_SUBSCRIBER_TOTP_KEY before importing TOTP", "导入 TOTP 前请配置 OPEN_NODE_SUBSCRIBER_TOTP_KEY。"],
    ["legacy TOTP secret is invalid", "旧版 TOTP 密钥无效。"],
    ["existing subscription links will be preserved", "已有订阅链接将予以保留。"],
    ["a legacy subscription key is already in use", "旧订阅凭据已被使用。"],
  ])("localizes only the complete legacy import suffix %s", (reason, translated) => {
    const warning = `private-token: https://provider.example/?token=secret: ${reason}`;
    expect(translateKnownMessage(warning)).toBe(translated);
    expect(requestError(warning, "迁移失败。").message).toBe(translated);
    expect(requestFailureMessage(new Error(warning), "迁移失败。")).toBe(translated);
    expect(translated).not.toMatch(/private-token|provider\.example|secret/u);
    expect(translateKnownMessage(reason)).toBeUndefined();
    expect(translateKnownMessage(`${warning}: secret`)).toBeUndefined();
    expect(translateKnownMessage(`${warning}\n`)).toBeUndefined();
    expect(translateKnownMessage(`: ${reason}`)).toBeUndefined();
    expect(translateKnownMessage(`   : ${reason}`)).toBeUndefined();
  });

  it.each([
    ["Map every in-use legacy package before importing: 1", "导入前请为所有正在使用的旧套餐指定映射。"],
    ["Map every in-use legacy package before importing: 1, 12, 9007199254740991", "导入前请为所有正在使用的旧套餐指定映射。"],
    ["Legacy package 9007199254740991: selected plan no longer exists", "旧套餐映射所选的目标套餐已不存在。"],
    ["private-name: legacy template https://provider.example/?token=secret must be mapped manually", "旧模板需要手动指定映射。"],
    ["private-name: legacy template token: private-template must be mapped manually", "旧模板需要手动指定映射。"],
    ["private-name: a legacy subscription key collides with private-owner", "旧订阅凭据与另一用户的凭据冲突。"],
    ["private-name: a legacy subscription key collides with token: Server address is missing", "旧订阅凭据与另一用户的凭据冲突。"],
    ["private-name: a legacy subscription key collides with token: source administrator will import as subscriber", "旧订阅凭据与另一用户的凭据冲突。"],
  ])("localizes the complete legacy variable template without exposing parameters: %s", (warning, translated) => {
    expect(translateKnownMessage(warning)).toBe(translated);
    expect(requestError(warning, "迁移失败。").message).toBe(translated);
    expect(requestFailureMessage(new Error(warning), "迁移失败。")).toBe(translated);
    expect(translated).not.toMatch(/private|token|secret|provider\.example|9007199254740991/u);
    expect(translateKnownMessage(`${warning}\u007f`)).toBeUndefined();
  });

  it.each([
    "Map every in-use legacy package before importing: ",
    "Map every in-use legacy package before importing: 0",
    "Map every in-use legacy package before importing: -1",
    "Map every in-use legacy package before importing: 01",
    "Map every in-use legacy package before importing: 1,2",
    "Map every in-use legacy package before importing: 1, ",
    "Map every in-use legacy package before importing: 1, private-token",
    "Map every in-use legacy package before importing: 9007199254740992",
    "Map every in-use legacy package before importing: 1e3",
    "Map every in-use legacy package before importing: １",
    "Legacy package 0: selected plan no longer exists",
    "Legacy package 01: selected plan no longer exists",
    "Legacy package 9007199254740992: selected plan no longer exists",
    "Legacy package 1: selected plan no longer exists: private-token",
    "Legacy package private-token: selected plan no longer exists",
    ": legacy template private-token must be mapped manually",
    "private-name: legacy template  must be mapped manually",
    "private-name: legacy template    must be mapped manually",
    "private-name: legacy template private-token must be mapped manually: extra",
    "private-name: Legacy template private-token must be mapped manually",
    "private-name: legacy template private-token\n must be mapped manually",
    ": a legacy subscription key collides with private-owner",
    "private-name: a legacy subscription key collides with ",
    "private-name: a legacy subscription key collides with    ",
    "private-name: a legacy subscription key collides with private-owner\u0000",
    "private-name: source administrator will import as administrator",
    "private-name: source administrator will import as subscriber: private-token",
  ])("keeps malformed legacy templates behind the contextual fallback: %s", warning => {
    expect(translateKnownMessage(warning)).toBeUndefined();
    expect(requestError(warning, "迁移失败。").message).toBe("迁移失败。");
  });

  it("bounds legacy source names and filenames without echoing their contents", () => {
    const reason = ": source administrator will import as subscriber";
    expect(translateKnownMessage("名".repeat(120) + reason)).toBe("来源管理员将按普通用户导入。");
    expect(translateKnownMessage("名".repeat(121) + reason)).toBeUndefined();
    const template = (name: string, filename: string) => `${name}: legacy template ${filename} must be mapped manually`;
    expect(translateKnownMessage(template("名".repeat(120), "密".repeat(255)))).toBe("旧模板需要手动指定映射。");
    expect(translateKnownMessage(template("名".repeat(121), "密"))).toBeUndefined();
    expect(translateKnownMessage(template("名", "密".repeat(256)))).toBeUndefined();
    const collision = (name: string, other: string) => `${name}: a legacy subscription key collides with ${other}`;
    expect(translateKnownMessage(collision("名".repeat(80), "密".repeat(80)))).toBe("旧订阅凭据与另一用户的凭据冲突。");
    expect(translateKnownMessage(collision("名".repeat(81), "密"))).toBeUndefined();
    expect(translateKnownMessage(collision("名", "密".repeat(81)))).toBeUndefined();
  });

  it("preserves only fixed numeric validation parameters", () => {
    expect(translateKnownMessage("String should have at most 128 characters")).toBe("字符串最多应包含 128 个字符。");
    expect(translateKnownMessage("Input should be greater than or equal to 0.5")).toBe("输入值应大于或等于 0.5。");
    expect(translateKnownMessage("Value error, Invalid YAML")).toBe("YAML 格式无效。");
    expect(translateKnownMessage("Value error, Use a hostname without a scheme, port or path")).toBe("请输入不含协议、端口或路径的主机名。");
  });

  it("uses schema paths but never returns validation input or unknown provider keys", () => {
    const failure = requestError([
      { loc: ["body", "bundle", "users", 0, "password_hash"], msg: "Legacy password must be a bcrypt hash", input: "private-password" },
      { loc: ["body", "private-token"], msg: "Required", input: "secret" },
      { loc: ["body", { token: "private-token" }], msg: "Invalid" },
      { loc: ["body", "name"], msg: "上游返回 private-provider-body" },
    ], "参数无效。");
    expect(failure.message).toBe("bundle.users.0.password_hash: 旧密码必须为 bcrypt 哈希值。；此项必填。；值无效。");
    expect(failure.message).not.toMatch(/private|secret|上游返回/);
    expect(requestFailureMessage(failure, "连接失败。")).toBe(failure.message);
  });

  it("keeps malformed or unknown validation errors behind the context fallback", () => {
    expect(requestError([{ msg: "private-token", input: "private-input" }, null, 7], "参数无效。").message).toBe("参数无效。");
    expect(requestError({ detail: "private-token" }, "参数无效。").message).toBe("参数无效。");
    expect(requestFailureMessage("private-token", "连接失败。")).toBe("连接失败。");
  });
});
