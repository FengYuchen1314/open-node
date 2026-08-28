import { describe, expect, it } from "vitest";
import type { ProductUser } from "../domain/subscriptions";
import { getUserManagement, getUserRemoval, removeUser, retryUserRemoval, saveUser, userSettings } from "./user-management";

describe("user management", () => {
  const user = { username: "alice", display_name: "Alice", is_active: false, role: "user", removal_id: "old" } as ProductUser;
  it("sends only editable fields and preserves disabled state", () => {
    expect(userSettings(user)).toEqual({ display_name: "Alice", email: null, remark: "", is_active: false });
  });
  it("encodes usernames and requires revision and explicit cleanup acknowledgment", async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    const fetcher: typeof fetch = async (input, init) => {
      calls.push([String(input), init]);
      return new Response(JSON.stringify({ id: "job", status: "pending" }), { status: 202 });
    };
    await getUserManagement("a+b", fetcher);
    await saveUser("a+b", userSettings(user), "r", fetcher);
    await removeUser("a+b", "r", "a+b", false, fetcher);
    await getUserRemoval("job", fetcher);
    await retryUserRemoval("job", fetcher);
    expect(calls.map(([url]) => url)).toEqual([
      "/api/v1/users/a%2Bb/settings", "/api/v1/users/a%2Bb/settings", "/api/v1/users/a%2Bb/remove",
      "/api/v1/user-removals/job", "/api/v1/user-removals/job/retry",
    ]);
    expect(JSON.parse(String(calls[1][1]?.body))).toEqual({ ...userSettings(user), expected_revision: "r", acknowledge_runtime_restart: true });
    expect(JSON.parse(String(calls[2][1]?.body))).toEqual({ expected_revision: "r", confirm_name: "a+b", acknowledge_runtime_restart: true, acknowledge_unmanaged_credentials: false });
    expect(calls[4][1]?.method).toBe("POST");
  });
  it("does not treat an accepted pending removal as completed", async () => {
    const value = await removeUser("alice", "r", "alice", true, async () => new Response(JSON.stringify({ id: "job", status: "pending" }), { status: 202 }));
    expect(value.status).toBe("pending");
  });
  it("exposes stale revisions and field validation failures", async () => {
    await expect(getUserManagement("alice", async () => new Response(JSON.stringify({ detail: "Reload user" }), { status: 409 }))).rejects.toThrow("Reload user");
    await expect(getUserManagement("alice", async () => new Response(JSON.stringify({ detail: [{ loc: ["body", "email"], msg: "Invalid" }] }), { status: 422 }))).rejects.toThrow("email: Invalid");
  });
});
