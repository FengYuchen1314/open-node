export function aliasErrors(names: Record<string, string>, ids: string[]): Record<string, string> {
  const errors: Record<string, string> = {};
  const seen = new Map<string, string>();
  for (const id of ids) {
    const name = (names[id] ?? "").trim();
    if (!name) continue;
    if (Array.from(name).length > 128 || /[\p{Cc}\p{Cs}]/u.test(name)) {
      errors[id] = "最多可用 128 个字符，不能包含控制字符";
    }
    const previous = seen.get(name);
    if (previous !== undefined) {
      errors[id] = errors[previous] = "同一套餐内的名称不能重复";
    }
    seen.set(name, id);
  }
  return errors;
}
