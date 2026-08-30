import { computed, onScopeDispose, ref, watch, type Ref } from "vue";
import {
  getAgentBootstrap, issueAgentBootstrap, revokeAgentBootstrap,
  type AgentBootstrapState, type BootstrapTransport,
} from "../services/agent-bootstrap";

// The command contains a short-lived secret. Never put it in shared stores or storage.
export function useAgentBootstrap(open: Readonly<Ref<boolean>>, serverId: Readonly<Ref<string>>, updated: () => void) {
  const state = ref<AgentBootstrapState | null>(null);
  const command = ref("");
  const transport = ref<BootstrapTransport>("auto");
  const confirmed = ref(false);
  const loading = ref(false);
  const busy = ref(false);
  const error = ref("");
  let epoch = 0;
  let requestSequence = 0;
  let issuedAt = "";
  let timer: ReturnType<typeof setTimeout> | undefined;

  const canIssue = computed(() => Boolean(state.value?.configured
    && !state.value.bootstrap.claimed_at && !state.value.bootstrap.agent_registered
    && !state.value.bootstrap.server_last_heartbeat));
  const canRevoke = computed(() => Boolean(state.value
    && ["issued", "claimed"].includes(state.value.bootstrap.status)));

  function current(run: number) {
    return run === epoch && open.value && Boolean(serverId.value);
  }

  function stopTimer() {
    clearTimeout(timer);
    timer = undefined;
  }

  function clearCommand() {
    command.value = "";
    issuedAt = "";
  }

  function accept(next: AgentBootstrapState) {
    const newlyRegistered = next.bootstrap.agent_registered && !state.value?.bootstrap.agent_registered;
    state.value = next;
    if (command.value && (next.bootstrap.status !== "issued"
      || next.bootstrap.issued_at !== issuedAt || next.bootstrap.agent_registered)) clearCommand();
    if (newlyRegistered) updated();
  }

  function schedule(run: number) {
    stopTimer();
    if (current(run) && !state.value?.bootstrap.agent_registered) {
      timer = setTimeout(() => { void refresh(run); }, 5000);
    }
  }

  async function refresh(run = epoch) {
    if (!current(run) || busy.value) return;
    stopTimer();
    const sequence = ++requestSequence;
    loading.value = state.value === null;
    try {
      const next = await getAgentBootstrap(serverId.value);
      if (!current(run) || sequence !== requestSequence) return;
      accept(next);
      error.value = "";
    } catch (cause) {
      if (!current(run) || sequence !== requestSequence) return;
      clearCommand();
      error.value = cause instanceof Error ? cause.message : "Agent installation status is unavailable.";
    } finally {
      if (current(run) && sequence === requestSequence) {
        loading.value = false;
        schedule(run);
      }
    }
  }

  async function issue() {
    if (!current(epoch) || !canIssue.value || !confirmed.value || busy.value || loading.value) return;
    const run = ++epoch;
    stopTimer();
    busy.value = true;
    error.value = "";
    clearCommand();
    try {
      const result = await issueAgentBootstrap(serverId.value, transport.value);
      if (!current(run)) return;
      command.value = result.command;
      issuedAt = result.issued.issued_at;
      confirmed.value = false;
    } catch (cause) {
      if (current(run)) error.value = cause instanceof Error ? cause.message : "Installation command could not be generated.";
    } finally {
      if (current(run)) {
        busy.value = false;
        // Refresh only after successful issue; preserve a mutation failure message.
        if (command.value) await refresh(run);
        else schedule(run);
      }
    }
  }

  async function revoke() {
    if (!current(epoch) || !canRevoke.value || busy.value || loading.value) return;
    const run = ++epoch;
    stopTimer();
    busy.value = true;
    error.value = "";
    clearCommand();
    try {
      const next = await revokeAgentBootstrap(serverId.value);
      if (current(run)) accept(next);
    } catch (cause) {
      if (current(run)) error.value = cause instanceof Error ? cause.message : "Installation ticket could not be revoked.";
    } finally {
      if (current(run)) {
        busy.value = false;
        schedule(run);
      }
    }
  }

  function reset() {
    epoch += 1;
    stopTimer();
    clearCommand();
    state.value = null;
    confirmed.value = false;
    transport.value = "auto";
    loading.value = false;
    busy.value = false;
    error.value = "";
  }

  watch([open, serverId], () => {
    reset();
    if (open.value && serverId.value) void refresh();
  }, { immediate: true, flush: "sync" });
  onScopeDispose(reset);

  return { state, command, transport, confirmed, loading, busy, error, canIssue, canRevoke,
    refresh: () => refresh(), issue, revoke };
}
