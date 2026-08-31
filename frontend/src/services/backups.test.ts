import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { validBackupId, validBackupRecipient, type BackupCreateRequest, type BackupJob } from "../domain/backups";
import { authState } from "./auth";
import { BackupRequestError, backupDownloadUrl, backupErrorMessage, createBackup, deleteBackup, getBackupJob, getBackups, newBackupRequestId } from "./backups";

const id = "01234567-89ab-4cde-8fab-0123456789ab";
const recipient = `age1${"q".repeat(58)}`;
const job: BackupJob = { id, status: "ready", created_at: "2026-08-31T00:00:00Z", expires_at: "2026-08-31T00:15:00Z",
  size: 4096, sha256: "a".repeat(64), error_code: null, restoration_ready: false };
const listing = { available: true, unavailable_code: null, jobs: [job], max_completed: 2, ttl_seconds: 900, requires_two_factor: true, restoration_supported: false };
const payload: BackupCreateRequest = { request_id: id, recipient, password: "PRIVATE-PASSWORD", code: "123456" };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
beforeEach(() => { authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "CSRF-FIXTURE" }; });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.useRealTimers(); });

describe("administrator backup requests", () => {
  it("uses existing cookie/CSRF authentication, exact creation fields and a matching 202 receipt", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(job, 202)); vi.stubGlobal("fetch", fetcher);
    expect(await createBackup(payload)).toEqual(job);
    const [path, init] = fetcher.mock.calls[0]!;
    expect(path).toBe("/api/v1/backups"); expect(init?.method).toBe("POST");
    expect(init?.credentials).toBe("include"); expect(init?.cache).toBe("no-store"); expect(init?.redirect).toBe("error");
    expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("CSRF-FIXTURE");
    expect(JSON.parse(String(init?.body))).toEqual(payload); expect(String(path)).not.toContain(payload.password);
  });
  it("reads only projected status metadata and bounds malformed or restore-ready responses", async () => {
    expect(await getBackups(vi.fn<typeof fetch>().mockResolvedValue(response({ ...listing, private_extra: "PRIVATE" })))).toEqual(listing);
    for (const value of [
      { ...listing, restoration_supported: true }, { ...listing, jobs: [{ ...job, restoration_ready: true }] },
      { ...listing, jobs: [{ ...job, size: "4096" }] }, { ...listing, jobs: [{ ...job, status: "PRIVATE" }] },
      { ...listing, jobs: [{ ...job, sha256: null }] }, { ...listing, jobs: [job, job] },
    ]) await expect(getBackups(vi.fn<typeof fetch>().mockResolvedValue(response(value)))).rejects.toMatchObject({ status: null });
    await expect(getBackups(vi.fn<typeof fetch>().mockResolvedValue(response({ private_extra: "x".repeat(66000) })))).rejects.toBeInstanceOf(BackupRequestError);
  });
  it("never treats the wrong HTTP status or a different request ID as an acknowledged creation", async () => {
    for (const reply of [response(job, 200), response({ ...job, id: "11234567-89ab-4cde-8fab-0123456789ab" }, 202)]) {
      const error = await createBackup(payload, vi.fn<typeof fetch>().mockResolvedValue(reply)).catch(value => value);
      expect(error).toBeInstanceOf(BackupRequestError); expect(error.outcomeUnknown).toBe(true);
    }
  });
  it("rejects invalid public keys/IDs before requests without treating shape checks as age checksum validation", async () => {
    expect(validBackupRecipient(recipient)).toBe(true);
    for (const value of [recipient + "\n", " " + recipient, recipient.toUpperCase(), "AGE-SECRET-KEY-1PRIVATE", `${recipient} ${recipient}`]) {
      const fetcher = vi.fn<typeof fetch>(); expect(validBackupRecipient(value)).toBe(false);
      expect(() => createBackup({ ...payload, recipient: value }, fetcher)).toThrow(BackupRequestError);
      expect(fetcher).not.toHaveBeenCalled();
    }
    expect(() => getBackupJob("../../PRIVATE")).toThrow(BackupRequestError);
    expect(() => deleteBackup(id.toUpperCase())).toThrow(BackupRequestError);
    expect(validBackupId(newBackupRequestId())).toBe(true);
  });
  it("maps fixed errors and discards provider bodies, arbitrary codes and even mutated Error messages", async () => {
    const error = await getBackupJob(id, vi.fn<typeof fetch>().mockResolvedValue(response({ code: "backup_not_found", detail: "PRIVATE URL" }, 404))).catch(value => value);
    error.message = "PRIVATE MUTATION";
    expect(backupErrorMessage(error)).toContain("未找到此备份任务"); expect(backupErrorMessage(error)).not.toContain("PRIVATE");
    const other = await getBackups(vi.fn<typeof fetch>().mockResolvedValue(response({ code: "PRIVATE", detail: "PRIVATE" }, 500))).catch(value => value);
    expect(backupErrorMessage(other)).not.toContain("PRIVATE"); expect(other.outcomeUnknown).toBe(true);
    expect(backupErrorMessage(new Error("PRIVATE"))).not.toContain("PRIVATE");
  });
  it("deletes only an explicit UUID and requires the exact empty 204 receipt", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    await expect(deleteBackup(id, fetcher)).resolves.toBeUndefined();
    expect(fetcher).toHaveBeenCalledWith(`/api/v1/backups/${id}`, expect.objectContaining({ method: "DELETE", redirect: "error" }));
    await expect(deleteBackup(id, vi.fn<typeof fetch>().mockResolvedValue(response({ ok: true })))).rejects.toMatchObject({ status: null });
  });
  it("constructs a same-origin native download URL without fetching or accepting an arbitrary URL", async () => {
    vi.stubGlobal("window", { location: { origin: "https://panel.example.test" } });
    vi.stubGlobal("fetch", vi.fn());
    expect(backupDownloadUrl(id)).toBe(`https://panel.example.test/api/v1/backups/${id}/download`);
    expect(() => backupDownloadUrl("https://attacker.test/PRIVATE")).toThrow(BackupRequestError);
    vi.stubEnv("VITE_API_BASE_URL", "https://attacker.test"); vi.resetModules();
    const isolated = await import("./backups"); expect(() => isolated.backupDownloadUrl(id)).toThrow();
    expect(fetch).not.toHaveBeenCalled();
  });
  it("times out a pending request safely and never replays it", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockImplementation((_path, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new Error("PRIVATE transport failure")), { once: true });
    }));
    const result = createBackup(payload, fetcher).catch(value => value);
    await vi.advanceTimersByTimeAsync(15000);
    expect(backupErrorMessage(await result)).not.toContain("PRIVATE"); expect(fetcher).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });
});
