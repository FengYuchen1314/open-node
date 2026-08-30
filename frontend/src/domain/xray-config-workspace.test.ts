import { describe, expect, it } from "vitest";

import {
  isWritableXrayFileResult,
  latestSuccessfulGetResult,
  parseJsonObjectText,
} from "./xray-config-workspace";

function result(
  createdAt: string,
  body: Record<string, unknown>,
  overrides: Partial<{
    method: string;
    path: string;
    result_status: number | null;
    status: "pending" | "succeeded" | "failed";
  }> = {},
) {
  return {
    created_at: createdAt,
    method: overrides.method ?? "GET",
    path: overrides.path ?? "/api/child/xray/config-files",
    result_body: body,
    result_status:
      "result_status" in overrides ? (overrides.result_status ?? null) : 200,
    status: overrides.status ?? "succeeded",
  };
}

describe("latestSuccessfulGetResult", () => {
  it("lets a newer list result supersede an older file read", () => {
    const oldRead = result("2026-08-30T00:00:00Z", { content: "{}", writable: true });
    const newList = result("2026-08-30T00:01:00Z", { files: { main: [] } });

    expect(
      latestSuccessfulGetResult([oldRead, newList], "/api/child/xray/config-files")
        ?.body,
    ).toBe(newList.result_body);
  });

  it.each([
    { status: "pending" as const, result_status: null },
    { status: "failed" as const, result_status: 500 },
    { status: "succeeded" as const, result_status: 304 },
  ])("does not revive an older success behind a newer $status GET", (newest) => {
    const successful = result("2026-08-30T00:00:00Z", { config: { writable: true } });
    const commands = [
      result("2026-08-30T00:01:00Z", {}, newest),
      successful,
    ];

    expect(latestSuccessfulGetResult(commands, "/api/child/xray/config-files")).toBeNull();
  });

  it("ignores a newer POST when selecting the latest GET", () => {
    const successful = result("2026-08-30T00:00:00Z", { config: { writable: true } });
    const post = result("2026-08-30T00:01:00Z", {}, { method: "POST" });

    expect(
      latestSuccessfulGetResult([successful, post], "/api/child/xray/config-files")
        ?.body,
    ).toBe(successful.result_body);
  });
});

describe("isWritableXrayFileResult", () => {
  it("requires the Agent writable flag", () => {
    expect(isWritableXrayFileResult({ writable: false }, "xray.json")).toBe(false);
    expect(isWritableXrayFileResult({}, "xray.json")).toBe(false);
    expect(isWritableXrayFileResult({ writable: true }, "xray.json")).toBe(true);
  });

  it("never treats JSONC as writable even if a result incorrectly says true", () => {
    expect(isWritableXrayFileResult({ writable: true }, "XRAY.JSONC")).toBe(false);
  });
});

describe("parseJsonObjectText", () => {
  it("accepts empty and populated JSON objects", () => {
    expect(parseJsonObjectText("{}", "DNS")).toEqual({});
    expect(parseJsonObjectText('{"servers":["1.1.1.1"]}', "DNS")).toEqual({
      servers: ["1.1.1.1"],
    });
  });

  it.each(["[]", "null", '"text"', "true"])("rejects non-object JSON %s", (value) => {
    expect(() => parseJsonObjectText(value, "Policy")).toThrow(
      "Policy must be a JSON object.",
    );
  });

  it("distinguishes malformed JSON", () => {
    expect(() => parseJsonObjectText("{", "DNS")).toThrow(
      "DNS must contain valid JSON.",
    );
  });
});
