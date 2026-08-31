const reserved = new Set([
  "admin", "root", "system", "api", "share", "test", "user", "guest", "null", "www",
  "mmw", "mmwx", "open-node", "opennode", "account", "subscribe", "assets", "healthz",
]);

export function shortCodeError(value: string | null): string {
  const code = (value ?? "").trim();
  if (code && !/^[A-Za-z0-9_-]{2,16}$/.test(code)) return "请使用 2–16 个英文字母、数字、下划线或连字符";
  return reserved.has(code.toLowerCase()) ? "此短码为保留名称" : "";
}
