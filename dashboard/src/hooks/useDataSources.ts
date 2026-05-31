import { useQuery } from '@tanstack/react-query';
import { useAuthStore } from '../store/authStore';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export interface DataSource {
  source_name: string;
  data_source_type: string;
  bucket: string | null;
  region: string | null;
  table_count: number;
  flagged_count: number;
  max_confidence: number;
  pii_categories: string[];
}

export interface DataSourcesResponse {
  items: DataSource[];
  total: number;
}

export function useDataSources() {
  const token = useAuthStore((s) => s.token);
  return useQuery<DataSourcesResponse>({
    queryKey: ['data-sources'],
    queryFn: async () => {
      const resp = await fetch(`${API}/api/v1/data-sources`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) throw new Error(`API error ${resp.status}`);
      return resp.json() as Promise<DataSourcesResponse>;
    },
    enabled: !!token,
  });
}
