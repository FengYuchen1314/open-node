export type SpeedTestStatus = "running" | "ok" | "failed";

export interface SpeedTestResult {
  id: string;
  node_id: string;
  node_name: string;
  source: "master" | "tester";
  tester_id: string | null;
  tester_name: string | null;
  status: SpeedTestStatus;
  down_mbps: number | null;
  latency_ms: number | null;
  egress_ip: string | null;
  bytes: number;
  error_code: string | null;
  created_at: string;
  completed_at: string | null;
  license_required: false;
}

export interface SpeedTester {
  id: string;
  name: string;
  online: boolean;
  caps: string[];
  version: string | null;
  last_seen_at: string | null;
  created_at: string;
  created_by: string;
  license_required: false;
}

export interface SpeedTesterSecret {
  tester: SpeedTester;
  token: string;
  websocket_path: string;
  license_required: false;
}

export interface MihomoStatus {
  supported: boolean;
  ready: boolean;
  version: string;
  platform: string;
  downloading: boolean;
  message: string;
  license_required: false;
}

export interface SpeedTestRunInput {
  node_id: string;
  bytes?: number;
  tester_id?: string | null;
  threads: 1 | 8;
  buf_size?: number;
  latency_only: boolean;
}
