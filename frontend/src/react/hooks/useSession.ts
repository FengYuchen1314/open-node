import { useSyncExternalStore } from "react";
import { getAuthSnapshot, subscribeAuthState } from "../../services/auth";
import { getSubscriberSnapshot, subscribeSubscriberState } from "../../services/subscriber-auth";

export function useAdministratorSession() {
  return useSyncExternalStore(subscribeAuthState, getAuthSnapshot, getAuthSnapshot);
}

export function useSubscriberSession() {
  return useSyncExternalStore(subscribeSubscriberState, getSubscriberSnapshot, getSubscriberSnapshot);
}
