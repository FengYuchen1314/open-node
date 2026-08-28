export function userPath(username: string, resource: string, parameters?: Record<string, string>): string {
  const query = new URLSearchParams(parameters);
  const useQuery = username.includes("/") || username === "." || username === "..";
  if (useQuery) query.set("username", username);
  const path = useQuery ? `/user-${resource}` : `/users/${encodeURIComponent(username)}/${resource}`;
  return path + (query.size ? `?${query.toString()}` : "");
}
