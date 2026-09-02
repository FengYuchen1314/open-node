// @vitest-environment jsdom
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authState } from "../../services/auth";
import { ApplicationUpdateRequestError, applyApplicationUpdate, checkApplicationUpdate, getApplicationUpdate } from "../../services/application-updates";
import { flush, installDom, renderUi } from "../test-utils";
import ApplicationUpdatePanel from "./ApplicationUpdatePanel";

vi.mock("../../services/application-updates", async original => ({ ...await original<typeof import("../../services/application-updates")>(),
  applyApplicationUpdate: vi.fn(), checkApplicationUpdate: vi.fn(), getApplicationUpdate: vi.fn(),
}));
const current = "a".repeat(40), latest = "b".repeat(40);
const state = { schema_version: 1 as const, managed: true, status: "available" as const, request_id: "11111111-1111-4111-8111-111111111111",
  current_revision: current, latest_revision: latest, has_update: true, checked_at: "2026-09-01T01:02:03Z", started_at: null, completed_at: null,
  message: "发现可用更新，请核对目标提交后再执行。", release_url: `https://github.com/FengYuchen1314/open-node/commit/${latest}`, license_required: false as const };
const operator = { configured: true, authenticated: true, username: "admin", csrf_token: "csrf" };

beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks(); installDom(); authState.session = operator;
  vi.mocked(getApplicationUpdate).mockResolvedValue(state);
  vi.mocked(checkApplicationUpdate).mockResolvedValue({ accepted: true, request_id: state.request_id, action: "check", license_required: false });
  vi.mocked(applyApplicationUpdate).mockResolvedValue({ accepted: true, request_id: state.request_id, action: "apply", license_required: false });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

describe("application update panel", () => {
  it("shows exact revisions and requires explicit interruption confirmation", async () => {
    renderUi(<ApplicationUpdatePanel operator={operator} />); await flush();
    expect(screen.getByText(current.slice(0, 12))).toBeTruthy(); expect(screen.getByText(latest.slice(0, 12))).toBeTruthy();
    expect((screen.getByRole("button", { name: "立即更新应用" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByText("我已确认目标提交，并接受更新期间的短暂中断"));
    expect((screen.getByRole("button", { name: "立即更新应用" }) as HTMLButtonElement).disabled).toBe(false);
  });
  it("queues checks without inventing progress or exposing privileged controls", async () => {
    renderUi(<ApplicationUpdatePanel operator={operator} />); await flush();
    fireEvent.click(screen.getByRole("button", { name: "检查应用更新" })); await flush();
    expect(checkApplicationUpdate).toHaveBeenCalledOnce(); expect(applyApplicationUpdate).not.toHaveBeenCalled();
    expect(screen.getByText("检查请求已由宿主机助手受理。")).toBeTruthy();
    expect(document.body.textContent).toContain("没有 Docker socket");
  });
  it("checks and applies the exact observed revision through one-click update", async () => {
    renderUi(<ApplicationUpdatePanel operator={operator} />); await flush();
    fireEvent.click(screen.getByRole("button", { name: "一键更新应用" })); await flush();
    fireEvent.click(screen.getByText("开始一键更新")); await flush();
    expect(checkApplicationUpdate).toHaveBeenCalledOnce();
    expect(applyApplicationUpdate).not.toHaveBeenCalled();
    expect(screen.getByText("一键更新已开始：正在重新检查官方目标提交。")).toBeTruthy();

    await vi.advanceTimersByTimeAsync(1000); await flush();
    expect(applyApplicationUpdate).toHaveBeenCalledOnce();
    expect(applyApplicationUpdate).toHaveBeenCalledWith(latest);
    expect(screen.getByText("更新请求已受理；宿主机正在备份并验证候选镜像，页面会持续读取结果。")).toBeTruthy();
  });
  it("never replays an apply whose handoff outcome is unknown", async () => {
    vi.mocked(applyApplicationUpdate).mockRejectedValue(new ApplicationUpdateRequestError(503, "application_update_state_unavailable"));
    renderUi(<ApplicationUpdatePanel operator={operator} />); await flush();
    fireEvent.click(screen.getByRole("button", { name: "一键更新应用" })); await flush();
    fireEvent.click(screen.getByText("开始一键更新")); await flush();
    await vi.advanceTimersByTimeAsync(1000); await flush();
    expect(applyApplicationUpdate).toHaveBeenCalledOnce();
    expect(screen.getByText("更新状态暂时不可用，请稍后重新读取。")).toBeTruthy();

    await vi.advanceTimersByTimeAsync(10_000); await flush();
    expect(applyApplicationUpdate).toHaveBeenCalledOnce();
  });
  it("renders unavailable manual guidance and disables the web check", async () => {
    vi.mocked(getApplicationUpdate).mockResolvedValue({ ...state, managed: false, status: "unavailable", latest_revision: null, has_update: null, release_url: null, checked_at: null, message: "当前部署没有可用的宿主机更新助手，请使用安装脚本更新。" });
    renderUi(<ApplicationUpdatePanel operator={operator} />); await flush();
    expect((screen.getByRole("button", { name: "检查应用更新" }) as HTMLButtonElement).disabled).toBe(true);
    expect(document.body.textContent).toContain("sudo bash /opt/open-node/install.sh update");
  });
});
