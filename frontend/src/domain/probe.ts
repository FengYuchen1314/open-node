export interface ProbeAppearance {
  theme: string;
  color_mode?: "light" | "dark" | "system";
  revision?: string;
}

export interface ProbeAppearanceUpdate {
  theme?: string | null;
  color_mode?: "light" | "dark" | "system" | null;
  revision?: string | null;
}

export interface ProbeBucket {
  ms: number;
  loss: number;
}

export interface ProbePingSeries {
  key?: string;
  label: string;
  isp?: string | null;
  current_ms: number;
  loss_pct: number;
  buckets: ProbeBucket[];
}

export interface ProbeDailyTraffic {
  date: string;
  uplink: number;
  downlink: number;
  total: number;
}

export type ProbeRouteCarrier = "telecom" | "unicom" | "mobile";

export interface ProbeReturnRoute {
  carrier: ProbeRouteCarrier;
  region?: string | null;
  route_type: string;
  tested_at?: string | null;
}

export interface ProbeServer {
  name?: string | null;
  region?: string | null;
  region_country?: string | null;
  region_name?: string | null;
  region_city?: string | null;
  online: boolean;
  upload_speed?: number | null;
  download_speed?: number | null;
  traffic_used?: number | null;
  traffic_used_up?: number | null;
  traffic_used_down?: number | null;
  traffic_used_total?: number | null;
  traffic_limit?: number | null;
  period_start?: string | null;
  period_end?: string | null;
  cumulative_up?: number | null;
  cumulative_down?: number | null;
  daily_traffic?: ProbeDailyTraffic[] | null;
  cpu_pct?: number | null;
  loadavg?: string | null;
  mem_used?: number | null;
  mem_total?: number | null;
  disk_used?: number | null;
  disk_total?: number | null;
  uptime?: number | null;
  cpu_model?: string | null;
  cpu_cores?: number | null;
  cpu_threads?: number | null;
  os?: string | null;
  kernel?: string | null;
  arch?: string | null;
  ping?: ProbePingSeries[] | null;
  expires_at?: string | null;
  renewal_price?: number | null;
  renewal_price_cny?: number | null;
  renewal_cycle?: "month" | "quarter" | "half_year" | "year" | null;
  renewal_currency?: string | null;
  provider_name?: string | null;
  provider_url?: string | null;
  telecom_paid_peer?: boolean | null;
  return_routes?: ProbeReturnRoute[] | null;
}

export interface ProbeSettings {
  enabled: boolean;
  show_globe?: boolean;
  show_daily_trend?: boolean;
  show_traffic_hotspots?: boolean;
  show_traffic_7d?: boolean;
  show_return_route?: boolean;
  show_resource_heatmap?: boolean;
  show_traffic_quota?: boolean;
  show_renewal_timeline?: boolean;
  show_health_score?: boolean;
  title?: string;
  description?: string;
  logo?: string;
  refresh_interval_sec?: number;
  appearance?: ProbeAppearance;
  updated_at?: string | null;
}

export interface ProbeSettingsUpdate {
  enabled?: boolean;
  show_globe?: boolean;
  show_daily_trend?: boolean;
  show_traffic_hotspots?: boolean;
  show_traffic_7d?: boolean;
  show_return_route?: boolean;
  show_resource_heatmap?: boolean;
  show_traffic_quota?: boolean;
  show_renewal_timeline?: boolean;
  show_health_score?: boolean;
  title?: string | null;
  description?: string | null;
  logo?: string | null;
  refresh_interval_sec?: number | null;
  appearance?: ProbeAppearanceUpdate | null;
}

export interface ProbeSettingsResponse {
  settings: ProbeSettings;
  license_required: false;
}

export interface ProbePayload extends ProbeSettings {
  servers?: ProbeServer[];
  license_required: false;
}

export interface ProbeMetricPoint {
  t: number;
  value: number;
}

export interface ProbeSystemSeries {
  cpu_pct: ProbeMetricPoint[];
  mem_used: ProbeMetricPoint[];
  mem_total: ProbeMetricPoint[];
  upload_speed: ProbeMetricPoint[];
  download_speed: ProbeMetricPoint[];
  cumulative_up: ProbeMetricPoint[];
  cumulative_down: ProbeMetricPoint[];
}

export interface ProbeSeriesResponse {
  success: boolean;
  series?: ProbePingSeries | ProbeSystemSeries | null;
  all_series?: ProbePingSeries[] | null;
  bucket_sec?: number | null;
  generated_at?: number | null;
  license_required: false;
}
