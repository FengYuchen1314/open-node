import type {
  RegistrationInvitation,
  RegistrationInvitationCreate,
  RegistrationInvitationCreateResponse,
  RegistrationInvitationsResponse,
} from "../domain/registration-invitations";
import { authenticatedFetch } from "./auth";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";
const basePath = `${apiBaseUrl}/api/v1/registration-invitations`;

async function failure(response: Response) {
  const body = await response.json().catch(() => null);
  return new Error(
    typeof body?.detail === "string" ? body.detail : `Request failed (${response.status})`,
  );
}

export async function listRegistrationInvitations(fetcher = authenticatedFetch) {
  const response = await fetcher(basePath);
  if (!response.ok) throw await failure(response);
  return response.json() as Promise<RegistrationInvitationsResponse>;
}

export async function createRegistrationInvitation(
  payload: RegistrationInvitationCreate,
  fetcher = authenticatedFetch,
) {
  const response = await fetcher(basePath, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await failure(response);
  return response.json() as Promise<RegistrationInvitationCreateResponse>;
}

export async function revokeRegistrationInvitation(
  identifier: string,
  fetcher = authenticatedFetch,
) {
  const response = await fetcher(`${basePath}/${encodeURIComponent(identifier)}`, {
    method: "DELETE",
  });
  if (!response.ok) throw await failure(response);
  return response.json() as Promise<RegistrationInvitation>;
}
