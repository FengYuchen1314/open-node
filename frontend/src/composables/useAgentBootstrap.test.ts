import { effectScope, ref } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getAgentBootstrap, issueAgentBootstrap, revokeAgentBootstrap, type AgentBootstrapState } from "../services/agent-bootstrap";
import { useAgentBootstrap } from "./useAgentBootstrap";

vi.mock("../services/agent-bootstrap", () => ({
  getAgentBootstrap: vi.fn(), issueAgentBootstrap: vi.fn(), revokeAgentBootstrap: vi.fn(),
}));

const read = vi.mocked(getAgentBootstrap);
const issue = vi.mocked(issueAgentBootstrap);
const revoke = vi.mocked(revokeAgentBootstrap);
const issuedAt = "2026-08-31T03:00:00Z";
const issued = {
  issued: { server_id: "edge", server_name: "Edge", control_url: "https://control.example", transport: "auto" as const,
    issued_at: issuedAt, expires_at: "2026-08-31T03:10:00Z" },
  command: "private-short-lived-command", license_required: false as const,
};

function status(overrides: Partial<AgentBootstrapState["bootstrap"]> = {}): AgentBootstrapState {
  return {
    configured: true, control_url: "https://control.example", reason: null, license_required: false,
    release: { agent_version: "0.3.0a0", source_commit: "a".repeat(40), xray_version: "v26.3.27", platform: "Debian 12 amd64" },
    bootstrap: { server_id: "edge", server_name: "Edge", status: "not_issued", issued_at: null, expires_at: null,
      claimed_at: null, agent_registered: false, agent_registered_at: null, agent_last_seen_at: null,
      agent_version: null, server_last_heartbeat: null, ...overrides },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

async function flush() {
  for (let index = 0; index < 6; index += 1) await Promise.resolve();
}

const scopes: ReturnType<typeof effectScope>[] = [];
function model() {
  const scope = effectScope();
  scopes.push(scope);
  const open = ref(true);
  const serverId = ref("edge");
  const updated = vi.fn();
  const vm = scope.run(() => useAgentBootstrap(open, serverId, updated))!;
  return { vm, open, serverId, updated, scope };
}

async function readyCommand() {
  const result = model();
  await flush();
  read.mockResolvedValue(status({ status: "issued", issued_at: issuedAt, expires_at: issued.issued.expires_at }));
  result.vm.confirmed.value = true;
  await result.vm.issue();
  expect(result.vm.command.value).toBe(issued.command);
  return result;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  read.mockResolvedValue(status());
  issue.mockResolvedValue(issued);
  revoke.mockResolvedValue(status({ status: "revoked" }));
});

afterEach(() => {
  for (const scope of scopes.splice(0)) scope.stop();
  vi.useRealTimers();
});

describe("private Agent installation dialog state", () => {
  it("opens read-only and requires an explicit new-host confirmation", async () => {
    const { vm } = model();
    await flush();
    expect(read).toHaveBeenCalledWith("edge");
    expect(vm.canIssue.value).toBe(true);
    await vm.issue();
    expect(issue).not.toHaveBeenCalled();
    expect(vm.command.value).toBe("");
  });

  it("issues using the selected transport and clears confirmation", async () => {
    const { vm } = model();
    await flush();
    vm.transport.value = "http";
    vm.confirmed.value = true;
    read.mockResolvedValue(status({ status: "issued", issued_at: issuedAt }));
    await vm.issue();
    expect(issue).toHaveBeenCalledWith("edge", "http");
    expect(vm.command.value).toBe(issued.command);
    expect(vm.confirmed.value).toBe(false);
    expect(vm.busy.value).toBe(false);
  });

  it("forgets secrets immediately on close and cannot recover them by reopening", async () => {
    const { vm, open } = await readyCommand();
    open.value = false;
    expect(vm.command.value).toBe("");
    expect(vm.state.value).toBeNull();
    const previousReads = read.mock.calls.length;
    await vi.advanceTimersByTimeAsync(15000);
    expect(read.mock.calls.length).toBe(previousReads);
    open.value = true;
    await flush();
    expect(vm.command.value).toBe("");
    expect(issue).toHaveBeenCalledTimes(1);
  });

  it.each(["close", "target", "dispose"])("ignores a late issued command after %s", async action => {
    const { vm, open, serverId, scope } = model();
    await flush();
    const pending = deferred<typeof issued>();
    issue.mockReturnValue(pending.promise);
    vm.confirmed.value = true;
    const work = vm.issue();
    if (action === "close") open.value = false;
    if (action === "target") serverId.value = "another-host";
    if (action === "dispose") scope.stop();
    pending.resolve(issued);
    await work;
    await flush();
    expect(vm.command.value).toBe("");
    expect(vm.confirmed.value).toBe(false);
  });

  it("ignores an old status request that arrives after ticket issuance", async () => {
    const { vm } = model();
    await flush();
    const old = deferred<AgentBootstrapState>();
    read.mockReturnValueOnce(old.promise);
    const refresh = vm.refresh();
    read.mockResolvedValue(status({ status: "issued", issued_at: issuedAt }));
    vm.confirmed.value = true;
    await vm.issue();
    old.resolve(status({ status: "revoked" }));
    await refresh;
    expect(vm.command.value).toBe(issued.command);
    expect(vm.state.value?.bootstrap.status).toBe("issued");
  });

  it.each(["expired", "revoked", "claimed"] as const)("forgets the command when polling observes %s", async next => {
    const { vm, updated } = await readyCommand();
    read.mockResolvedValue(status({ status: next, issued_at: issuedAt }));
    await vi.advanceTimersByTimeAsync(5000);
    expect(vm.command.value).toBe("");
    expect(vm.state.value?.bootstrap.agent_registered).toBe(false);
    expect(updated).not.toHaveBeenCalled();
  });

  it("forgets a command replaced by another administrator session", async () => {
    const { vm } = await readyCommand();
    read.mockResolvedValue(status({ status: "issued", issued_at: "2026-08-31T03:01:00Z" }));
    await vi.advanceTimersByTimeAsync(5000);
    expect(vm.command.value).toBe("");
  });

  it("separates registration from claim and stops polling after registration", async () => {
    const { vm, updated } = await readyCommand();
    read.mockResolvedValue(status({ status: "claimed", issued_at: issuedAt, claimed_at: issuedAt,
      agent_registered: true, agent_version: "0.3.0a0" }));
    await vi.advanceTimersByTimeAsync(5000);
    expect(vm.command.value).toBe("");
    expect(vm.canIssue.value).toBe(false);
    expect(updated).toHaveBeenCalledTimes(1);
    const calls = read.mock.calls.length;
    await vi.advanceTimersByTimeAsync(15000);
    expect(read.mock.calls.length).toBe(calls);
  });

  it.each(["expired", "revoked", "claimed"] as const)("never reissues a previously claimed %s ticket", async ticketStatus => {
    read.mockResolvedValue(status({ status: ticketStatus, claimed_at: issuedAt }));
    const { vm } = model();
    await flush();
    vm.confirmed.value = true;
    await vm.issue();
    expect(vm.canIssue.value).toBe(false);
    expect(issue).not.toHaveBeenCalled();
  });

  it("fails closed when canonical HTTPS or a verified release is unavailable", async () => {
    read.mockResolvedValue({ ...status(), configured: false, reason: "Configure HTTPS" });
    const { vm } = model();
    await flush();
    vm.confirmed.value = true;
    await vm.issue();
    expect(issue).not.toHaveBeenCalled();
  });

  it("does not issue for a server with a heartbeat but no Agent registration", async () => {
    read.mockResolvedValue(status({ server_last_heartbeat: issuedAt }));
    const { vm } = model();
    await flush();
    vm.confirmed.value = true;
    await vm.issue();
    expect(vm.canIssue.value).toBe(false);
    expect(issue).not.toHaveBeenCalled();
  });

  it("disables issuance when polling first observes an existing host heartbeat", async () => {
    const { vm } = await readyCommand();
    read.mockResolvedValue(status({ status: "revoked", server_last_heartbeat: issuedAt }));
    await vi.advanceTimersByTimeAsync(5000);
    expect(vm.canIssue.value).toBe(false);
    expect(vm.command.value).toBe("");
    vm.confirmed.value = true;
    await vm.issue();
    expect(issue).toHaveBeenCalledTimes(1);
  });

  it("revokes explicitly and clears the command without claiming the Agent disconnected", async () => {
    const { vm } = await readyCommand();
    await vm.revoke();
    expect(revoke).toHaveBeenCalledWith("edge");
    expect(vm.command.value).toBe("");
    expect(vm.state.value?.bootstrap.status).toBe("revoked");
  });

  it("keeps a mutation failure visible and prevents concurrent issue requests", async () => {
    const { vm } = model();
    await flush();
    const pending = deferred<typeof issued>();
    issue.mockReturnValueOnce(pending.promise);
    read.mockResolvedValue(status({ status: "issued", issued_at: issuedAt }));
    vm.confirmed.value = true;
    const first = vm.issue();
    await vm.issue();
    expect(issue).toHaveBeenCalledTimes(1);
    pending.resolve(issued);
    await first;
    revoke.mockRejectedValue(new Error("Already claimed"));
    await vm.revoke();
    expect(vm.error.value).toBe("Already claimed");
    expect(vm.command.value).toBe("");
  });

  it("clears any displayed secret when authentication or a status request fails", async () => {
    const { vm } = await readyCommand();
    read.mockRejectedValue(new Error("Administrator sign-in required"));
    await vi.advanceTimersByTimeAsync(5000);
    expect(vm.command.value).toBe("");
    expect(vm.error.value).toBe("Administrator sign-in required");
  });
});
