import { describe, expect, it } from "vitest";
import { UI_LOCALE, zhMessage, zhStatus } from "./zh-CN";

describe("Simplified Chinese presentation", () => {
  it("uses the explicit Chinese locale", () => {
    expect(UI_LOCALE).toBe("zh-CN");
  });
  it.each([
    ["active", "有效"], ["failed", "失败"], ["rolling_back", "回退中"],
    ["waiting", "等待中"], ["provisioning", "配置中"], ["unsupported", "不支持"],
  ])("translates the known display state %s without replacing its source value", (value, label) => {
    const record = { status: value };
    expect(zhStatus(record.status)).toBe(label);
    expect(record.status).toBe(value);
  });
  it("does not reinterpret technical identifiers or absent values", () => {
    expect(zhStatus("VLESS")).toBe("VLESS");
    expect(zhStatus("new-runtime-state")).toBe("new-runtime-state");
    expect(zhStatus(null)).toBe("未提供");
    expect(zhStatus(undefined)).toBe("未提供");
  });
  it("translates known application feedback and preserves existing Chinese copy", () => {
    expect(zhMessage(new Error("Invalid username or password"))).toBe("用户名或密码错误。");
    expect(zhMessage("请重新登录。")).toBe("请重新登录。");
  });
  it("uses a context-specific fallback for unknown English errors", () => {
    const error = new Error("unknown upstream body https://example.test/?token=PRIVATE");
    expect(zhMessage(error, "无法加载节点，请重试。")).toBe("无法加载节点，请重试。");
    expect(zhMessage(null, "无法保存。")).toBe("无法保存。");
    expect(zhMessage(error)).not.toContain("PRIVATE");
  });
});
