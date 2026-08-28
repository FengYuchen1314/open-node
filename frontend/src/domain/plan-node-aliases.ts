export function aliasErrors(names: Record<string, string>, ids: string[]): Record<string, string> {
  const errors: Record<string, string> = {};
  const seen = new Map<string, string>();
  for (const id of ids) {
    const name = (names[id] ?? "").trim();
    if (!name) continue;
    if (Array.from(name).length > 128 || /[\p{Cc}\p{Cs}]/u.test(name)) {
      errors[id] = "Use up to 128 characters without control characters";
    }
    const previous = seen.get(name);
    if (previous !== undefined) {
      errors[id] = errors[previous] = "Names within a plan must be distinct";
    }
    seen.set(name, id);
  }
  return errors;
}
