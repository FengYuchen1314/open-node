import { describe, expect, it } from "vitest";
import { shortCodeError } from "./subscription-links";

describe("custom subscription short codes", () => {
  it("keeps format and reserved-name constraints distinct in Chinese", () => {
    expect(shortCodeError("a")).toBe("请使用 2–16 个英文字母、数字、下划线或连字符");
    expect(shortCodeError("AdMiN")).toBe("此短码为保留名称");
  });
  it.each(["", null, "  ", "Ab", "a_-1", "Abcdefgh12345_-x"])("accepts %s", value => {
    expect(shortCodeError(value)).toBe("");
  });
  it.each(["a", "a".repeat(17), "a/b", "a b", "a?b", "a#b", "a%b", "a\\b", ".", "..", "\u4e2d\u6587", "AdMiN", "open-node", "account"])("rejects %s", value => {
    expect(shortCodeError(value)).not.toBe("");
  });
});
