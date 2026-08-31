import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { describe, expect, it } from "vitest";

import ProbeView from "../react/views/ProbeView";
import ProbeAdministrationPanel from "../react/components/ProbeAdministrationPanel";

async function renderProbe(publicOnly: boolean) {
  return renderToStaticMarkup(createElement(ProbeView, { publicOnly }));
}

describe("probe surface modes", () => {
  it("omits every administrator control from the public-only surface", async () => {
    const html = await renderProbe(true);

    expect(html).toContain("公共探针");
    expect(html).toContain("探针节点");
    expect(html).not.toContain("探针设置");
    expect(html).not.toContain("Worker 访问");
    expect(html).not.toContain("定时探针");
    expect(html).not.toContain("下发到期任务");
  });

  it("keeps administrator controls in the authenticated control-plane view", async () => {
    const html = renderToStaticMarkup(createElement(ProbeAdministrationPanel, {
      accessToken: "", onSettings: () => {}, onAccessToken: () => {}, onRefresh: () => {},
    }));

    expect(html).toContain("探针设置");
    expect(html).toContain("Worker 访问");
    expect(html).toContain("定时探针");
  });
});
