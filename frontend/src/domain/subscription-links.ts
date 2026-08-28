const reserved = new Set([
  "admin", "root", "system", "api", "share", "test", "user", "guest", "null", "www",
  "mmw", "mmwx", "open-node", "opennode", "account", "subscribe", "assets", "healthz",
]);

export function shortCodeError(value: string | null): string {
  const code = (value ?? "").trim();
  if (code && !/^[A-Za-z0-9_-]{2,16}$/.test(code)) return "Use 2-16 letters, digits, underscores or hyphens";
  return reserved.has(code.toLowerCase()) ? "This short code is reserved" : "";
}
