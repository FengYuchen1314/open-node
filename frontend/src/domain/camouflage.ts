export type CamouflageRegion = "los-angeles" | "san-jose" | "tokyo" | "singapore"
  | "germany" | "united-kingdom" | "netherlands";

export interface CamouflagePool {
  id: string;
  region: CamouflageRegion;
  region_label: string;
  label: string;
  server_name: string;
  target: string;
  tls_version: "TLSv1.3";
  alpn: "h2";
  cloudflare: false;
  gfw_verdict: "not_blocked";
  gfw_last_tested: string;
}

export interface CamouflagePoolCatalog {
  schema_version: 1;
  reviewed_at: string;
  probe_vantage: string;
  measurement_notice: string;
  sources: Record<string, string>;
  pools: CamouflagePool[];
  license_required: false;
}
