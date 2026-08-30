// @vitest-environment jsdom
import { useState } from "react";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { routeTargets } from "../../domain/diagnostics";
import { installDom, renderUi } from "../test-utils";
import RouteProbeFields from "./RouteProbeFields";

beforeEach(installDom);
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("Return-route target numeric drafts", () => {
  it.each(["79999", "0.4", "", "-"])("does not clamp or restore the default port for %j", draft => {
    const changed = vi.fn();
    function Harness() {
      const [targets, setTargets] = useState(routeTargets());
      return <RouteProbeFields value={targets} onChange={next => { changed(next); setTargets(next); }} />;
    }
    renderUi(<Harness />);
    const input = screen.getByRole("spinbutton", { name: "Telecom port" });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: draft } });
    fireEvent.blur(input);
    fireEvent.keyDown(input, { key: "Enter" });
    const latest = changed.mock.lastCall?.[0][0].port;
    if (draft === "" || draft === "-") expect(Number.isNaN(latest)).toBe(true);
    else expect(latest).toBe(Number(draft));
    expect(screen.getByText("Use a whole-number port from 1 to 65535.")).toBeTruthy();
    expect(changed.mock.calls.every(([targets]) => targets[0].port !== 80 && targets[0].port !== 65535 && targets[0].port !== 0)).toBe(true);
  });
});
