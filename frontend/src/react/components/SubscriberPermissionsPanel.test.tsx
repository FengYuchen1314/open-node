// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authState } from "../../services/auth";
import { getSubscriberPermissions, updateSubscriberPermissions } from "../../services/subscriber-permissions";
import { flush, installDom, renderUi } from "../test-utils";
import SubscriberPermissionsPanel from "./SubscriberPermissionsPanel";

vi.mock("../../services/subscriber-permissions", async original => ({
  ...await original<typeof import("../../services/subscriber-permissions")>(),
  getSubscriberPermissions: vi.fn(), updateSubscriberPermissions: vi.fn(),
}));
const operator = { configured: true, authenticated: true, username: "admin", csrf_token: "csrf" };
const initial = { revision: 4, pages: ["templates", "external_subscriptions", "private_routes", "renewals"] as const, template_quota: 0, external_source_quota: 2, license_required: false as const };

beforeEach(() => {
  vi.resetAllMocks(); installDom(); authState.ready = true; authState.session = { ...operator };
  vi.mocked(getSubscriberPermissions).mockResolvedValue({ ...initial, pages: [...initial.pages] });
  vi.mocked(updateSubscriberPermissions).mockImplementation(async value => ({ revision: value.expected_revision + 1, pages: value.pages, template_quota: value.template_quota, external_source_quota: value.external_source_quota, license_required: false }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("subscriber permissions panel", () => {
  it("loads without writing and saves a canonical feature list and quotas once", async () => {
    renderUi(<SubscriberPermissionsPanel operator={operator} />); await flush();
    expect(screen.getByText(/关闭功能后，账户 API 也会拒绝访问/)).toBeTruthy();
    expect(updateSubscriberPermissions).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox", { name: "个人路由节点" }));
    fireEvent.change(screen.getByLabelText("每位用户的个人模板上限"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "保存用户权限" })); await flush();
    expect(updateSubscriberPermissions).toHaveBeenCalledExactlyOnceWith({
      expected_revision: 4, pages: ["templates", "external_subscriptions", "renewals"],
      template_quota: 3, external_source_quota: 2, license_required: false,
    });
    expect(screen.getByText("用户功能权限已保存。")).toBeTruthy();
  });
  it("fences a late save after the administrator session changes", async () => {
    let resolve!: (value: Awaited<ReturnType<typeof updateSubscriberPermissions>>) => void;
    vi.mocked(updateSubscriberPermissions).mockReturnValue(new Promise(done => { resolve = done; }));
    renderUi(<SubscriberPermissionsPanel operator={operator} />); await flush();
    fireEvent.click(screen.getByRole("checkbox", { name: "续费申请" }));
    fireEvent.click(screen.getByRole("button", { name: "保存用户权限" })); await flush();
    await act(async () => { authState.session = { ...operator, csrf_token: "new-csrf" }; resolve({ ...initial, revision: 5, pages: ["templates"] }); });
    expect(screen.queryByText("用户功能权限已保存。")).toBeNull();
  });
});
