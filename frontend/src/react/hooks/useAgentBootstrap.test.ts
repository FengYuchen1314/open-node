// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getAgentBootstrap, issueAgentBootstrap, revokeAgentBootstrap, type AgentBootstrapState } from "../../services/agent-bootstrap";
import { useAgentBootstrap } from "./useAgentBootstrap";

vi.mock("../../services/agent-bootstrap", () => ({
  getAgentBootstrap: vi.fn(), issueAgentBootstrap: vi.fn(), revokeAgentBootstrap: vi.fn(),
}));
const read = vi.mocked(getAgentBootstrap);
const issue = vi.mocked(issueAgentBootstrap);
const revoke = vi.mocked(revokeAgentBootstrap);
const issuedAt = "2026-08-31T03:00:00Z";
const issued = { issued: { server_id: "edge", server_name: "Edge", control_url: "https://control.example", transport: "auto" as const,
  issued_at: issuedAt, expires_at: "2026-08-31T03:10:00Z" }, command: "private-short-lived-command", license_required: false as const };
function status(overrides: Partial<AgentBootstrapState["bootstrap"]> = {}): AgentBootstrapState {
  return { configured: true, control_url: "https://control.example", reason: null, license_required: false,
    release: { agent_version: "0.3.0a0", source_commit: "a".repeat(40), xray_version: "v26.3.27", platform: "Debian 12 amd64" },
    bootstrap: { server_id: "edge", server_name: "Edge", status: "not_issued", issued_at: null, expires_at: null, claimed_at: null,
      agent_registered: false, agent_registered_at: null, agent_last_seen_at: null, agent_version: null, server_last_heartbeat: null, ...overrides } };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
async function flush() { await act(async () => { for (let i = 0; i < 6; i += 1) await Promise.resolve(); }); }
async function tick(ms = 5000) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
function model() {
  const updated = vi.fn();
  const hook = renderHook(({ open, serverId }) => useAgentBootstrap(open, serverId, updated), { initialProps: { open: true, serverId: "edge" } });
  return { ...hook, updated };
}
async function readyCommand() {
  const modelValue = model(); await flush();
  read.mockResolvedValue(status({ status: "issued", issued_at: issuedAt, expires_at: issued.issued.expires_at }));
  act(() => modelValue.result.current.setConfirmed(true));
  await act(async () => { await modelValue.result.current.issue(); });
  expect(modelValue.result.current.command).toBe(issued.command);
  return modelValue;
}
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks(); read.mockResolvedValue(status());
  issue.mockResolvedValue(issued); revoke.mockResolvedValue(status({ status: "revoked" }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("React private Agent installation state", () => {
  it("opens read-only and requires an explicit new-host confirmation", async () => {
    const { result } = model(); await flush();
    expect(read).toHaveBeenCalledWith("edge"); expect(result.current.canIssue).toBe(true);
    await act(async () => { await result.current.issue(); });
    expect(issue).not.toHaveBeenCalled(); expect(result.current.command).toBe("");
  });
  it("issues using the selected transport and clears confirmation", async () => {
    const { result } = model(); await flush();
    act(() => { result.current.setTransport("http"); result.current.setConfirmed(true); });
    read.mockResolvedValue(status({ status: "issued", issued_at: issuedAt }));
    await act(async () => { await result.current.issue(); });
    expect(issue).toHaveBeenCalledWith("edge", "http"); expect(result.current.command).toBe(issued.command);
    expect(result.current.confirmed).toBe(false); expect(result.current.busy).toBe(false);
  });
  it("forgets secrets immediately on close and cannot recover them by reopening", async () => {
    const { result, rerender } = await readyCommand();
    rerender({ open: false, serverId: "edge" });
    expect(result.current.command).toBe(""); expect(result.current.state).toBeNull();
    const previousReads = read.mock.calls.length; await tick(15000); expect(read).toHaveBeenCalledTimes(previousReads);
    rerender({ open: true, serverId: "edge" }); await flush();
    expect(result.current.command).toBe(""); expect(issue).toHaveBeenCalledTimes(1);
  });
  it.each(["close", "target", "dispose"])("ignores a late issued command after %s", async action => {
    const { result, rerender, unmount, updated } = model(); await flush();
    const pending = deferred<typeof issued>(); issue.mockReturnValueOnce(pending.promise);
    act(() => result.current.setConfirmed(true));
    let work!: Promise<void>; act(() => { work = result.current.issue(); });
    if (action === "close") rerender({ open: false, serverId: "edge" });
    if (action === "target") rerender({ open: true, serverId: "another-host" });
    if (action === "dispose") unmount();
    await act(async () => { pending.resolve(issued); await work; });
    expect(result.current.command).toBe(""); expect(updated).not.toHaveBeenCalled();
    if (action !== "dispose") expect(result.current.confirmed).toBe(false);
  });
  it("ignores a status request from before the current ticket issuance", async () => {
    const { result } = model(); await flush();
    const old = deferred<AgentBootstrapState>(); read.mockReturnValueOnce(old.promise);
    let refresh!: Promise<void>; act(() => { refresh = result.current.refresh(); });
    read.mockResolvedValue(status({ status: "issued", issued_at: issuedAt }));
    act(() => result.current.setConfirmed(true));
    await act(async () => { await result.current.issue(); old.resolve(status({ status: "revoked" })); await refresh; });
    expect(result.current.command).toBe(issued.command); expect(result.current.state?.bootstrap.status).toBe("issued");
  });
  it.each(["expired", "revoked", "claimed"] as const)("forgets the command when polling observes %s", async next => {
    const { result, updated } = await readyCommand(); read.mockResolvedValue(status({ status: next, issued_at: issuedAt })); await tick();
    expect(result.current.command).toBe(""); expect(result.current.state?.bootstrap.agent_registered).toBe(false); expect(updated).not.toHaveBeenCalled();
  });
  it("forgets a command replaced by another administrator session", async () => {
    const { result } = await readyCommand(); read.mockResolvedValue(status({ status: "issued", issued_at: "2026-08-31T03:01:00Z" })); await tick();
    expect(result.current.command).toBe("");
  });
  it("separates registration from claim and stops polling after registration", async () => {
    const { result, updated } = await readyCommand();
    read.mockResolvedValue(status({ status: "claimed", issued_at: issuedAt, claimed_at: issuedAt, agent_registered: true, agent_version: "0.3.0a0" }));
    await tick(); expect(result.current.command).toBe(""); expect(result.current.canIssue).toBe(false); expect(updated).toHaveBeenCalledTimes(1);
    const calls = read.mock.calls.length; await tick(15000); expect(read).toHaveBeenCalledTimes(calls);
  });
  it.each(["expired", "revoked", "claimed"] as const)("never reissues a previously claimed %s ticket", async ticketStatus => {
    read.mockResolvedValue(status({ status: ticketStatus, claimed_at: issuedAt })); const { result } = model(); await flush();
    act(() => result.current.setConfirmed(true)); await act(async () => { await result.current.issue(); });
    expect(result.current.canIssue).toBe(false); expect(issue).not.toHaveBeenCalled();
  });
  it("fails closed when canonical HTTPS or a verified release is unavailable", async () => {
    read.mockResolvedValue({ ...status(), configured: false, reason: "Configure HTTPS" }); const { result } = model(); await flush();
    act(() => result.current.setConfirmed(true)); await act(async () => { await result.current.issue(); }); expect(issue).not.toHaveBeenCalled();
  });
  it("does not issue for a heartbeat without Agent registration", async () => {
    read.mockResolvedValue(status({ server_last_heartbeat: issuedAt })); const { result } = model(); await flush();
    act(() => result.current.setConfirmed(true)); await act(async () => { await result.current.issue(); });
    expect(result.current.canIssue).toBe(false); expect(issue).not.toHaveBeenCalled();
  });
  it("disables issuance when polling first observes an existing host heartbeat", async () => {
    const { result } = await readyCommand(); read.mockResolvedValue(status({ status: "revoked", server_last_heartbeat: issuedAt })); await tick();
    expect(result.current.canIssue).toBe(false); expect(result.current.command).toBe("");
    act(() => result.current.setConfirmed(true)); await act(async () => { await result.current.issue(); }); expect(issue).toHaveBeenCalledTimes(1);
  });
  it("revokes explicitly without claiming an already connected Agent disconnected", async () => {
    const { result } = await readyCommand(); await act(async () => { await result.current.revoke(); });
    expect(revoke).toHaveBeenCalledWith("edge"); expect(result.current.command).toBe(""); expect(result.current.state?.bootstrap.status).toBe("revoked");
  });
  it("keeps mutation errors visible and rejects duplicate concurrent issue requests", async () => {
    const { result } = model(); await flush(); const pending = deferred<typeof issued>(); issue.mockReturnValueOnce(pending.promise);
    read.mockResolvedValue(status({ status: "issued", issued_at: issuedAt })); act(() => result.current.setConfirmed(true));
    let first!: Promise<void>; act(() => { first = result.current.issue(); void result.current.issue(); }); expect(issue).toHaveBeenCalledTimes(1);
    await act(async () => { pending.resolve(issued); await first; });
    revoke.mockRejectedValue(new Error("Already claimed")); await act(async () => { await result.current.revoke(); });
    expect(result.current.error).toBe("Already claimed"); expect(result.current.command).toBe("");
  });
  it("clears any displayed secret when authentication or a status request fails", async () => {
    const { result } = await readyCommand(); read.mockRejectedValue(new Error("Administrator sign-in required")); await tick();
    expect(result.current.command).toBe(""); expect(result.current.error).toBe("Administrator sign-in required");
  });
  it("retains only the newest concurrent status response", async () => {
    const { result } = model(); await flush(); const old = deferred<AgentBootstrapState>(); read.mockReturnValueOnce(old.promise);
    let request!: Promise<void>; act(() => { request = result.current.refresh(); });
    read.mockResolvedValue(status({ status: "claimed", claimed_at: issuedAt }));
    await act(async () => { await result.current.refresh(); old.resolve(status()); await request; });
    expect(result.current.state?.bootstrap.status).toBe("claimed"); expect(result.current.canIssue).toBe(false);
  });
  it("does not apply a late revoke to a different host", async () => {
    const { result, rerender } = await readyCommand(); const pending = deferred<AgentBootstrapState>(); revoke.mockReturnValueOnce(pending.promise);
    let request!: Promise<void>; act(() => { request = result.current.revoke(); });
    read.mockResolvedValue(status({ server_id: "other", server_name: "Other" })); rerender({ open: true, serverId: "other" }); await flush();
    await act(async () => { pending.resolve(status({ status: "revoked" })); await request; });
    expect(result.current.state?.bootstrap.server_id).toBe("other"); expect(result.current.state?.bootstrap.status).toBe("not_issued");
  });
});
