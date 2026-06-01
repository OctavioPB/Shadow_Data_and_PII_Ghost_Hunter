import { useMutation } from '@tanstack/react-query';
import { useAuthStore, type AuthUser } from '../store/authStore';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

interface SeedResponse {
  access_token: string;
  token_type: string;
  role: string;
  name: string;
}

export function useDemoSeed() {
  const { setAuth } = useAuthStore();

  return useMutation<SeedResponse, Error>({
    mutationFn: async () => {
      let resp: Response;

      try {
        resp = await fetch(`${API}/api/v1/demo/seed`, { method: 'POST' });
      } catch {
        // TypeError: Failed to fetch -- server not reachable or CORS blocked
        throw new Error(
          `Cannot reach API at ${API}. Make sure the FastAPI server is running on port 8000.`
        );
      }

      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try {
          const body = await resp.json() as { detail?: string };
          detail = body.detail ?? detail;
        } catch {
          /* non-JSON body -- keep the status code message */
        }
        throw new Error(detail);
      }

      return resp.json() as Promise<SeedResponse>;
    },
    onSuccess: (data) => {
      const user: AuthUser = {
        email: 'dpo@company.com',
        role: data.role as AuthUser['role'],
        name: data.name,
      };
      setAuth(data.access_token, user);
    },
  });
}
