import { describe, expect, it } from "vitest";
import { aliasErrors } from "./plan-node-aliases";

describe("plan node aliases", () => {
  it("ignores empty values and removed nodes", () => {
    expect(aliasErrors({ a: "  ", b: " Tokyo ", removed: "Tokyo" }, ["a", "b"])).toEqual({});
  });
  it("marks both duplicate names after trimming", () => {
    expect(Object.keys(aliasErrors({ a: "Tokyo", b: " Tokyo " }, ["a", "b"]))).toEqual(["a", "b"]);
    expect(aliasErrors({ a: "Tokyo", b: " Tokyo " }, ["a", "b"])).toEqual({
      a: "同一套餐内的名称不能重复", b: "同一套餐内的名称不能重复",
    });
    expect(aliasErrors({ a: "Tokyo", b: "tokyo" }, ["a", "b"])).toEqual({});
  });
  it.each(["a\nb", "a\tb", "a\u0000b", "a\u007fb", "a\u0085b", "x".repeat(129), "\ud800"])("rejects invalid display names", name => {
    expect(aliasErrors({ a: name }, ["a"])).toHaveProperty("a", "最多可用 128 个字符，不能包含控制字符");
  });
  it("counts Unicode code points, not UTF-16 units", () => {
    expect(aliasErrors({ a: "\u{1f680}".repeat(128) }, ["a"])).toEqual({});
    expect(aliasErrors({ a: "\u{1f680}".repeat(129) }, ["a"])).toHaveProperty("a");
    expect(aliasErrors({ a: "\u4e1c\u4eac" }, ["a"])).toEqual({});
  });
});
