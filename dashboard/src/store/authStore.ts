import { create } from 'zustand';

export interface AuthUser {
  email: string;
  role: 'admin' | 'dpo' | 'auditor' | 'viewer';
  name: string;
}

interface AuthState {
  token: string | null;
  user: AuthUser | null;
  setAuth: (token: string, user: AuthUser) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: sessionStorage.getItem('pii_token'),
  user: (() => {
    const raw = sessionStorage.getItem('pii_user');
    return raw ? (JSON.parse(raw) as AuthUser) : null;
  })(),
  setAuth: (token, user) => {
    sessionStorage.setItem('pii_token', token);
    sessionStorage.setItem('pii_user', JSON.stringify(user));
    set({ token, user });
  },
  logout: () => {
    sessionStorage.removeItem('pii_token');
    sessionStorage.removeItem('pii_user');
    set({ token: null, user: null });
  },
}));
