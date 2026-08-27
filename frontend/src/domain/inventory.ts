export type ConnectionMode = "auto" | "websocket" | "http" | "pull";
export type ServerStatus = "pending" | "connected" | "offline";
export type XrayMode = "external" | "embedded";

export interface ServerCreateRequest {
  name: string;
  ip_address?: string | null;
  connection_mode?: ConnectionMode;
  listen_port?: number;
  xray_mode?: XrayMode;
}

export interface ServerSummary {
  id: string;
  name: string;
  status: ServerStatus;
  connection_mode: ConnectionMode;
  listen_port: number;
  xray_mode: XrayMode;
  current_upload_speed: number;
  current_download_speed: number;
}

export interface ServerCreateResponse {
  server: ServerSummary;
  agent_token: string;
  license_required: false;
}

export const defaultServerCreateRequest = (): ServerCreateRequest => ({
  name: "",
  connection_mode: "auto",
  listen_port: 23889,
  xray_mode: "external",
});
