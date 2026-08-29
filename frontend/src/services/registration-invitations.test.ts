import { describe, expect, it, vi } from "vitest";
import type { RegistrationInvitationCreate } from "../domain/registration-invitations";
import {
  createRegistrationInvitation,
  listRegistrationInvitations,
  revokeRegistrationInvitation,
} from "./registration-invitations";

const invitation = {
  id: "invite-id",
  token_hint: "abcd1234",
  plan_id: "plan-id",
  plan_name: "Standard",
  status: "active",
  used_by: null,
  expires_at: "2026-08-30T04:00:00Z",
  used_at: null,
  revoked_at: null,
  created_at: "2026-08-29T04:00:00Z",
} as const;

describe("registration invitation service", () => {
  it("lists, creates and revokes invitations", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetcher = vi.fn<typeof fetch>(async (url, init) => {
      calls.push([url, init]);
      const body = init?.method === "POST"
        ? { invitation, registration_url: "https://panel.example/account#invite=secret", license_required: false }
        : init?.method === "DELETE"
          ? { ...invitation, status: "revoked" }
          : { invitations: [invitation], license_required: false };
      return new Response(JSON.stringify(body), {
        status: init?.method === "POST" ? 201 : 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    const payload: RegistrationInvitationCreate = {
      plan_id: "plan-id",
      expires_minutes: 1440,
    };

    expect((await listRegistrationInvitations(fetcher)).invitations).toEqual([invitation]);
    expect((await createRegistrationInvitation(payload, fetcher)).invitation).toEqual(invitation);
    expect((await revokeRegistrationInvitation(invitation.id, fetcher)).status).toBe("revoked");
    expect(calls.map(([url]) => url)).toEqual([
      "/api/v1/registration-invitations",
      "/api/v1/registration-invitations",
      "/api/v1/registration-invitations/invite-id",
    ]);
    expect(JSON.parse(String(calls[1][1]?.body))).toEqual(payload);
    expect(calls[2][1]?.method).toBe("DELETE");
  });
});
