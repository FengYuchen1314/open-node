// @vitest-environment jsdom
import { act, cleanup, screen } from "@testing-library/react";
import { Modal, Pagination, Table } from "antd";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installDom, renderUi } from "./test-utils";

beforeEach(installDom);
afterEach(async () => {
  cleanup();
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
  vi.restoreAllMocks(); vi.unstubAllGlobals();
});
describe("Ant Design Chinese defaults", () => {
  it("uses the official Chinese modal confirmation and cancellation labels", () => {
    renderUi(<Modal open title="内置文案验证">确认前请核对操作。</Modal>);
    expect(screen.getByRole("button", { name: /^确\s*定$/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^取\s*消$/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "OK" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  });
  it("uses Chinese table empty and pagination navigation text", () => {
    renderUi(<><Table rowKey="id" dataSource={[]} columns={[{ title: "名称", dataIndex: "name" }]} pagination={false} /><Pagination total={100} current={2} /></>);
    expect(screen.getByText("暂无数据", { selector: ".ant-empty-description" })).toBeTruthy();
    expect(screen.getByTitle("暂无数据")).toBeTruthy();
    expect(screen.getByTitle("上一页")).toBeTruthy();
    expect(screen.getByTitle("下一页")).toBeTruthy();
    expect(screen.queryByText("No data")).toBeNull();
  });
});
