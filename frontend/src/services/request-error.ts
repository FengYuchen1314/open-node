import { translateKnownMessage } from "../i18n/messages";

// These are schema field names, never values, credentials or arbitrary provider keys.
const validationFields = new Set([
  "name", "display_name", "username", "owner_username", "email", "password", "password_hash",
  "current_password", "new_password", "code", "challenge", "tags", "domain", "domain_v6",
  "ip_address", "ip_address_v6", "ipv6_enabled", "enabled", "active", "content", "format",
  "bundle", "users", "plans", "nodes", "groups", "rules", "templates", "settings", "config",
  "proxy_config", "inbound_config", "traffic_limit_gb", "traffic_limit_bytes", "speed_limit_mbps",
  "device_limit", "node_speed_limits", "node_device_limits", "node_ids", "node_names",
  "expected_revision", "confirm_name", "custom_short_code", "url", "user_agent", "server_id",
  "plan_id", "profile_id", "protocol", "port", "host", "transport", "networks", "label",
  "expires_at", "expires_in_seconds", "expires_minutes", "max_uses", "max_nodes", "daily_limit",
]);

class LocalizedRequestError extends Error {}

function validationPath(value: unknown): string {
  if (!Array.isArray(value) || value.length > 12) return "";
  const path = value.slice(1);
  return path.every(part => typeof part === "string" ? validationFields.has(part)
    : typeof part === "number" && Number.isInteger(part) && part >= 0 && part <= 1_000_000)
    ? path.join(".") : "";
}

/** Localize only trusted error text. Callers retain their existing response-shape checks. */
export function requestError(detail: unknown, fallback: string): Error {
  if (typeof detail === "string") {
    return new LocalizedRequestError(translateKnownMessage(detail) ?? fallback);
  }
  if (Array.isArray(detail)) {
    const messages = detail.slice(0, 20).flatMap((entry: unknown) => {
      if (!entry || typeof entry !== "object" || Array.isArray(entry)) return [];
      const item = entry as { msg?: unknown; loc?: unknown };
      if (typeof item.msg !== "string") return [];
      const translated = translateKnownMessage(item.msg);
      if (!translated) return [];
      const field = validationPath(item.loc);
      return [field ? `${field}: ${translated}` : translated];
    });
    if (messages.length) return new LocalizedRequestError(messages.join("；"));
  }
  return new LocalizedRequestError(fallback);
}

/** Network exceptions are untrusted too; only our own errors retain localized details. */
export function requestFailureMessage(failure: unknown, fallback: string): string {
  if (failure instanceof LocalizedRequestError) return failure.message;
  return failure instanceof Error ? translateKnownMessage(failure.message) ?? fallback : fallback;
}
