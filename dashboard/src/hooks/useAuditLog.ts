import { useQuery } from '@tanstack/react-query';
import { useAuthStore } from '../store/authStore';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export interface AuditEntry {
  id: string;
  event_type: string;
  table_id: string | null;
  actor: string;
  timestamp: string;
  details_json: Record<string, unknown> | null;
}

export interface AuditLogResponse {
  items: AuditEntry[];
  total: number;
  page: number;
  size: number;
}

export interface AuditFilters {
  page?: number;
  size?: number;
  actor?: string;
  event_type?: string;
  table_id?: string;
  date_from?: string;
  date_to?: string;
}

export function useAuditLog(filters: AuditFilters = {}) {
  const token = useAuthStore((s) => s.token);
  const p = new URLSearchParams();
  if (filters.page) p.set('page', String(filters.page));
  if (filters.size) p.set('size', String(filters.size));
  if (filters.actor) p.set('actor', filters.actor);
  if (filters.event_type) p.set('event_type', filters.event_type);
  if (filters.table_id) p.set('table_id', filters.table_id);
  if (filters.date_from) p.set('date_from', filters.date_from);
  if (filters.date_to) p.set('date_to', filters.date_to);
  const qs = p.toString();

  return useQuery<AuditLogResponse>({
    queryKey: ['audit-log', qs],
    queryFn: async () => {
      const resp = await fetch(`${API}/api/v1/audit-log?${qs}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) throw new Error(`API error ${resp.status}`);
      return resp.json() as Promise<AuditLogResponse>;
    },
    enabled: !!token,
  });
}

export function exportAuditLog(token: string | null, filters: AuditFilters = {}): void {
  const p = new URLSearchParams();
  if (filters.actor) p.set('actor', filters.actor);
  if (filters.event_type) p.set('event_type', filters.event_type);
  if (filters.table_id) p.set('table_id', filters.table_id);
  if (filters.date_from) p.set('date_from', filters.date_from);
  if (filters.date_to) p.set('date_to', filters.date_to);

  fetch(`${API}/api/v1/audit-log/export?${p.toString()}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
    .then((resp) => resp.blob())
    .then((blob) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'audit_log.csv';
      a.click();
      URL.revokeObjectURL(url);
    });
}
