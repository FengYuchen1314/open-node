import type { AgentCommand } from "./inventory";

type WorkspaceCommand = Pick<
  AgentCommand,
  | "created_at"
  | "method"
  | "path"
  | "result_body"
  | "result_status"
  | "status"
>;

export interface SuccessfulCommandResult {
  body: Record<string, unknown>;
  command: WorkspaceCommand;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function createdAt(command: WorkspaceCommand) {
  const value = Date.parse(command.created_at);
  return Number.isFinite(value) ? value : Number.NEGATIVE_INFINITY;
}

/** Returns the newest GET only when that exact command completed successfully. */
export function latestSuccessfulGetResult(
  commands: readonly WorkspaceCommand[],
  path: string,
): SuccessfulCommandResult | null {
  let latest: WorkspaceCommand | null = null;
  for (const command of commands) {
    if (command.method !== "GET" || command.path !== path) {
      continue;
    }
    if (!latest || createdAt(command) > createdAt(latest)) {
      latest = command;
    }
  }
  const body = asRecord(latest?.result_body);
  if (
    !latest ||
    latest.status !== "succeeded" ||
    typeof latest.result_status !== "number" ||
    latest.result_status < 200 ||
    latest.result_status >= 300 ||
    !body
  ) {
    return null;
  }
  return { body, command: latest };
}

export function isJsoncFilename(filename: string) {
  return filename.trim().toLowerCase().endsWith(".jsonc");
}

/** The local suffix check is defense-in-depth; an Agent may never unlock JSONC writes. */
export function isWritableXrayFileResult(
  body: Record<string, unknown>,
  filename: string,
) {
  return body.writable === true && !isJsoncFilename(filename);
}

export function parseJsonObjectText(
  value: string,
  label: string,
): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value) as unknown;
  } catch {
    throw new Error(`${label} 必须包含有效的 JSON。`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${label} 必须是 JSON 对象。`);
  }
  return parsed as Record<string, unknown>;
}
