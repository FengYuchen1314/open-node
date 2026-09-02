import { useLayoutEffect, useRef, useState } from "react";
import { zhMessage } from "../../i18n/zh-CN";
import {
  getAgentBootstrap, issueAgentBootstrap, revokeAgentBootstrap,
  type AgentBootstrapState, type BootstrapTransport,
} from "../../services/agent-bootstrap";

interface BootstrapView {
  state: AgentBootstrapState | null;
  command: string;
  transport: BootstrapTransport;
  confirmed: boolean;
  loading: boolean;
  busy: boolean;
  error: string;
}

function emptyView(): BootstrapView {
  return { state: null, command: "", transport: "auto", confirmed: false,
    loading: false, busy: false, error: "" };
}

function mayIssue(state: AgentBootstrapState | null) {
  return Boolean(state?.configured && !state.bootstrap.claimed_at
    && !state.bootstrap.agent_registered && !state.bootstrap.server_last_heartbeat);
}

function mayRevoke(state: AgentBootstrapState | null) {
  return Boolean(state && ["issued", "claimed"].includes(state.bootstrap.status));
}

// Secrets stay in this mounted dialog only. Epoch + request sequence guards also
// cover StrictMode replay, target changes and responses received after closing.
export function useAgentBootstrap(open: boolean, serverId: string, onUpdated?: () => void, autoIssue = false) {
  const [view, setView] = useState<BootstrapView>(emptyView);
  const control = useRef({ epoch: 0, sequence: 0, active: false, serverId: "",
    issuedAt: "", autoIssue: false, view: emptyView(), timer: undefined as ReturnType<typeof setTimeout> | undefined });
  const updated = useRef(onUpdated);
  useLayoutEffect(() => { updated.current = onUpdated; }, [onUpdated]);

  function patch(next: Partial<BootstrapView>) {
    control.current.view = { ...control.current.view, ...next };
    setView(control.current.view);
  }
  function current(run: number) {
    return control.current.active && Boolean(control.current.serverId) && run === control.current.epoch;
  }
  function stopTimer() {
    clearTimeout(control.current.timer);
    control.current.timer = undefined;
  }
  function clearCommand() {
    control.current.issuedAt = "";
    patch({ command: "" });
  }
  function accept(next: AgentBootstrapState) {
    const previous = control.current.view;
    const newlyRegistered = next.bootstrap.agent_registered && !previous.state?.bootstrap.agent_registered;
    patch({ state: next });
    if (previous.command && (next.bootstrap.status !== "issued"
      || next.bootstrap.issued_at !== control.current.issuedAt || next.bootstrap.agent_registered)) {
      clearCommand();
    }
    if (newlyRegistered) updated.current?.();
  }
  function schedule(run: number) {
    stopTimer();
    if (current(run) && !control.current.view.state?.bootstrap.agent_registered) {
      control.current.timer = setTimeout(() => { void refresh(run); }, 5000);
    }
  }
  async function refresh(run = control.current.epoch) {
    if (!current(run) || control.current.view.busy) return;
    stopTimer();
    const sequence = ++control.current.sequence;
    patch({ loading: control.current.view.state === null });
    try {
      const next = await getAgentBootstrap(control.current.serverId);
      if (!current(run) || sequence !== control.current.sequence) return;
      accept(next);
      patch({ error: "" });
    } catch (cause) {
      if (!current(run) || sequence !== control.current.sequence) return;
      clearCommand();
      patch({ error: zhMessage(cause, "暂时无法获取 Agent 安装状态。") });
    } finally {
      if (current(run) && sequence === control.current.sequence) {
        patch({ loading: false });
        if (control.current.autoIssue && mayIssue(control.current.view.state)) {
          control.current.autoIssue = false;
          void issue(true);
        } else schedule(run);
      }
    }
  }
  async function issue(skipConfirmation = false) {
    const previous = control.current.view;
    if (!current(control.current.epoch) || !mayIssue(previous.state)
      || (!skipConfirmation && !previous.confirmed) || previous.busy || previous.loading) return;
    const run = ++control.current.epoch;
    stopTimer();
    patch({ busy: true, error: "" });
    clearCommand();
    try {
      const result = await issueAgentBootstrap(control.current.serverId, previous.transport);
      if (!current(run)) return;
      control.current.issuedAt = result.issued.issued_at;
      patch({ command: result.command, confirmed: false });
    } catch (cause) {
      if (current(run)) patch({ error: zhMessage(cause, "无法生成安装命令。") });
    } finally {
      if (current(run)) {
        patch({ busy: false });
        if (control.current.view.command) await refresh(run);
        else schedule(run);
      }
    }
  }
  async function revoke() {
    const previous = control.current.view;
    if (!current(control.current.epoch) || !mayRevoke(previous.state)
      || previous.busy || previous.loading) return;
    const run = ++control.current.epoch;
    stopTimer();
    patch({ busy: true, error: "" });
    clearCommand();
    try {
      const next = await revokeAgentBootstrap(control.current.serverId);
      if (current(run)) accept(next);
    } catch (cause) {
      if (current(run)) patch({ error: zhMessage(cause, "无法撤销安装凭据。") });
    } finally {
      if (current(run)) {
        patch({ busy: false });
        schedule(run);
      }
    }
  }

  useLayoutEffect(() => {
    const model = control.current;
    model.epoch += 1;
    stopTimer();
    model.serverId = serverId;
    model.active = open && Boolean(serverId);
    model.autoIssue = model.active && autoIssue;
    model.issuedAt = "";
    model.view = emptyView();
    setView(model.view);
    if (model.active) void refresh(model.epoch);
    return () => {
      model.active = false;
      model.epoch += 1;
      stopTimer();
      model.issuedAt = "";
      model.view = emptyView();
    };
  }, [open, serverId, autoIssue]);

  const visible = open && serverId === control.current.serverId ? view : emptyView();
  return { ...visible, canIssue: mayIssue(visible.state), canRevoke: mayRevoke(visible.state),
    setTransport: (transport: BootstrapTransport) => patch({ transport }),
    setConfirmed: (confirmed: boolean) => patch({ confirmed }),
    refresh: () => refresh(), issue: () => issue(autoIssue), revoke };
}
