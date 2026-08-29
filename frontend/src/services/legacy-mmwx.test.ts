import { describe, expect, it } from "vitest";

import type { LegacyMMWXIdentityBundle, LegacyMMWXImportPreview } from "../domain/legacy-mmwx";
import { importLegacyMMWXIdentities, previewLegacyMMWXIdentities } from "./legacy-mmwx";

const bundle: LegacyMMWXIdentityBundle = {
  version: 1,
  source_revision: "main",
  users: [{
    username: "alice",
    password_hash: "$2a$04$secret-hash-is-sent-without-transforms-0000000000000000",
    email: null,
    display_name: "Alice",
    source_role: "user",
    is_active: true,
    totp_enabled: true,
    totp_secret: "TOTP-SECRET",
    recovery_code_hashes: ["RECOVERY-HASH"],
    token: "legacy-token",
    generated_short_code: "abc",
    custom_short_code: null,
    created_at: null,
  }],
};

const preview: LegacyMMWXImportPreview = {
  revision: "a".repeat(64),
  ready: true,
  total_users: 1,
  new_users: 1,
  existing_users: 0,
  imported_accounts: 1,
  replaced_accounts: 0,
  skipped_accounts: 0,
  imported_tokens: 1,
  replaced_tokens: 0,
  skipped_tokens: 0,
  imported_totp: 1,
  blockers: [],
  warnings: [],
  license_required: false,
};

describe("legacy MMWX identity migration", () => {
  it("sends the sensitive bundle only in preview and import request bodies", async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    const fetcher: typeof fetch = async (input, init) => {
      calls.push([String(input), init]);
      return new Response(JSON.stringify(String(input).endsWith("/preview") ? preview : { preview, applied: true }));
    };
    await previewLegacyMMWXIdentities(bundle, false, fetcher);
    await importLegacyMMWXIdentities(bundle, true, preview, 1, fetcher);
    expect(calls.map(([url]) => url)).toEqual([
      "/api/v1/migrations/mmwx/identities/preview",
      "/api/v1/migrations/mmwx/identities/import",
    ]);
    expect(JSON.parse(String(calls[0][1]?.body))).toEqual({ bundle, replace_existing: false });
    expect(JSON.parse(String(calls[1][1]?.body))).toEqual({
      bundle,
      replace_existing: true,
      expected_revision: preview.revision,
      confirm_user_count: 1,
    });
    expect(new Headers(calls[0][1]?.headers).get("Content-Type")).toBe("application/json");
  });

  it("keeps validation details visible without reconstructing rejected input", async () => {
    const fetcher: typeof fetch = async () => new Response(JSON.stringify({
      detail: [{ loc: ["body", "bundle", "users", 0, "password_hash"], msg: "Legacy password must be a bcrypt hash" }],
    }), { status: 422 });
    await expect(previewLegacyMMWXIdentities(bundle, false, fetcher)).rejects.toThrow(
      "bundle.users.0.password_hash: Legacy password must be a bcrypt hash",
    );
  });
});
