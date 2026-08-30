/** A memory-only store shared by API clients and React's useSyncExternalStore. */
export function createObservableState<T extends object>(initial: T) {
  const listeners = new Set<() => void>();
  let snapshot: Readonly<T> = { ...initial };
  const state = new Proxy({ ...initial }, {
    set(target, property, value) {
      if (Object.is(Reflect.get(target, property), value)) return true;
      const changed = Reflect.set(target, property, value);
      if (changed) {
        snapshot = { ...target };
        for (const listener of [...listeners]) listener();
      }
      return changed;
    },
  });
  return {
    state,
    getSnapshot: () => snapshot,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
  };
}
