import { describe, expect, it } from "vitest";
import { shortCodeError } from "./subscription-links";

describe("custom subscription short codes", () => {
  it.each(["", null, "  ", "Ab", "a_-1", "Abcdefgh12345_-x"])("accepts %s", value => {
    expect(shortCodeError(value)).toBe("");
  });
  it.each(["a", "a".repeat(17), "a/b", "a b", "a?b", "a#b", "a%b", "a\\b", ".", "..", "\u4e2d\u6587", "AdMiN", "open-node", "account"])("rejects %s", value => {
    expect(shortCodeError(value)).not.toBe("");
  });
});
