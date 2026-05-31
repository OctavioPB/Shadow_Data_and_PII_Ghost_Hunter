import { useQuery } from '@tanstack/react-query';
import { useAuthStore } from '../store/authStore';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export interface RiskItem {
  table_id: string;
  source_name: string;
  data_source_type: string;
  pii_categories: string[];
  max_confidence: number;
  status: string;
  flagged_column_count: number;
  last_scanned: string;
  owner_email: string | null;
}

export interface RisksResponse {
  items: RiskItem[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

export interface StatsSummary {
  total_flagged: number;
  remediated: number;
  pending_review: number;
  compliance_score: number;
}

export interface RiskFilters {
  page?: number;
  size?: number;
  pii_category?: string;
  status?: string;
  source?: string;
  date_from?: string;
  date_to?: string;
}

function buildParams(filters: RiskFilters): string {
  const p = new URLSearchParams();
  if (filters.page) p.set('page', String(filters.page));
  if (filters.size) p.set('size', String(filters.size));
  if (filters.pii_category) p.set('pii_category', filters.pii_category);
  if (filters.status) p.set('status', filters.status);
  if (filters.source) p.set('source', filters.source);
  if (filters.date_from) p.set('date_from', filters.date_from);
  if (filters.date_to) p.set('date_to', filters.date_to);
  return p.toString();
}

async function apiFetch<T>(url: string, token: string | null): Promise<T> {
  const resp = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!resp.ok) throw new Error(`API error ${resp.status}`);
  return resp.json() as Promise<T>;
}

export function useRisks(filters: RiskFilters = {}) {
  const token = useAuthStore((s) => s.token);
  const qs = buildParams({ page: 1, size: 20, ...filters });
  return useQuery<RisksResponse>({
    queryKey: ['risks', qs],
    queryFn: () => apiFetch(`${API}/api/v1/risks?${qs}`, token),
    enabled: !!token,
  });
}

export function useStatsSummary() {
  const token = useAuthStore((s) => s.token);
  return useQuery<StatsSummary>({
    queryKey: ['stats-summary'],
    queryFn: () => apiFetch(`${API}/api/v1/stats/summary`, token),
    enabled: !!token,
  });
}
