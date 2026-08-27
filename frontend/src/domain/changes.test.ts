import { describe, expect, it } from "vitest";
import { changeSetActions, type AgentChangeSet, type AgentChangeSetStatus } from "./changes";

function change(status: AgentChangeSetStatus, blocked = false) {
  return { status, blocking_command_ids: blocked ? ["inflight"] : [] } as unknown as AgentChangeSet;
}

describe("change set actions", () => {
  it("dispatches only fresh plans", () => {
    expect(changeSetActions(change("planned")).dispatch).toBe(true);
    for (const status of ["succeeded", "failed", "dispatched", "accepted"] as const) {
      expect(changeSetActions(change(status)).dispatch).toBe(false);
    }
  });
  it("distinguishes compensation retry from ordinary rollback", () => {
    expect(changeSetActions(change("rollback_failed"))).toMatchObject({ retry: true, rollback: true });
    expect(changeSetActions(change("succeeded"))).toMatchObject({ retry: false, rollback: true });
    for (const status of ["rollback_queued", "rolled_back", "cancelled", "accepted", "needs_review"] as const) {
      expect(changeSetActions(change(status)).rollback).toBe(false);
    }
  });
  it("requires a stopped review state before releasing reservations", () => {
    for (const status of ["failed", "rollback_failed", "rollback_incomplete", "needs_review"] as const) {
      expect(changeSetActions(change(status)).accept).toBe(true);
      expect(changeSetActions(change(status, true)).accept).toBe(false);
    }
    expect(changeSetActions(change("dispatched")).accept).toBe(false);
    expect(changeSetActions(null)).toMatchObject({ dispatch: false, rollback: false, accept: false });
  });
});
