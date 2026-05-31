import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '../store/authStore';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export interface ColumnFinding {
  column_name: string;
  pii_category: string;
  confidence: number;
  sample_count: number | null;
  status: string;
}

export interface PIIReport {
  table_id: string;
  source_name: string;
  data_source_type: string;
  owner_email: string | null;
  flagged_columns: ColumnFinding[];
  last_scanned: string | null;
}

export interface RemediateRequest {
  action: 'anonymize' | 'quarantine' | 'false_positive';
  notes?: string;
}

export function usePIIReport(tableId: string | null) {
  const token = useAuthStore((s) => s.token);
  return useQuery<PIIReport>({
    queryKey: ['pii-report', tableId],
    queryFn: async () => {
      const resp = await fetch(`${API}/api/v1/tables/${tableId}/pii-report`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) throw new Error(`API error ${resp.status}`);
      return resp.json() as Promise<PIIReport>;
    },
    enabled: !!token && !!tableId,
  });
}

export function useRemediate(tableId: string) {
  const token = useAuthStore((s) => s.token);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: RemediateRequest) => {
      const resp = await fetch(`${API}/api/v1/tables/${tableId}/remediate`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as { detail?: string }).detail ?? `Error ${resp.status}`);
      }
      return resp.json();
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['pii-report', tableId] });
      void qc.invalidateQueries({ queryKey: ['risks'] });
      void qc.invalidateQueries({ queryKey: ['stats-summary'] });
    },
  });
}
