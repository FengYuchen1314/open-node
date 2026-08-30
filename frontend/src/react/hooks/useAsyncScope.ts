import { useEffect, useMemo, useRef } from "react";

/** Ignore work that belongs to a closed view or an older user operation. */
export function useAsyncScope() {
  const generation = useRef(0);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; generation.current += 1; };
  }, []);
  return useMemo(() => ({
    begin: () => ++generation.current,
    capture: () => generation.current,
    invalidate: () => { generation.current += 1; },
    isCurrent: (value: number) => mounted.current && value === generation.current,
  }), []);
}
