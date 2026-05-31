import { useMutation } from '@tanstack/react-query';
import { type AuthUser, useAuthStore } from '../store/authStore';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

interface LoginVars {
  email: string;
  password: string;
}

interface TokenResponse {
  access_token: string;
  role: string;
  name: string;
}

export function useLogin() {
  const setAuth = useAuthStore((s) => s.setAuth);

  return useMutation({
    mutationFn: async ({ email, password }: LoginVars) => {
      const form = new URLSearchParams({ username: email, password });
      const resp = await fetch(`${API}/api/v1/auth/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: form.toString(),
      });
      if (!resp.ok) throw new Error('Invalid credentials');
      return resp.json() as Promise<TokenResponse>;
    },
    onSuccess: (data, variables) => {
      const user: AuthUser = {
        email: variables.email,
        role: data.role as AuthUser['role'],
        name: data.name,
      };
      setAuth(data.access_token, user);
    },
  });
}
