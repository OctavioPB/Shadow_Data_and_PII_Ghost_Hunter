import { describe, it, expect, beforeEach } from 'vitest';
import { useAuthStore } from './authStore';

// jsdom provides sessionStorage
beforeEach(() => {
  sessionStorage.clear();
  useAuthStore.setState({ token: null, user: null });
});

describe('authStore', () => {
  it('starts with null token and user', () => {
    const state = useAuthStore.getState();
    expect(state.token).toBeNull();
    expect(state.user).toBeNull();
  });

  it('setAuth stores token and user', () => {
    const { setAuth } = useAuthStore.getState();
    setAuth('test-token', { email: 'dpo@company.com', role: 'dpo', name: 'DPO' });
    const state = useAuthStore.getState();
    expect(state.token).toBe('test-token');
    expect(state.user?.role).toBe('dpo');
  });

  it('logout clears token and user', () => {
    const { setAuth, logout } = useAuthStore.getState();
    setAuth('test-token', { email: 'dpo@company.com', role: 'dpo', name: 'DPO' });
    logout();
    const state = useAuthStore.getState();
    expect(state.token).toBeNull();
    expect(state.user).toBeNull();
  });

  it('logout clears sessionStorage', () => {
    const { setAuth, logout } = useAuthStore.getState();
    setAuth('tok', { email: 'a@b.com', role: 'viewer', name: 'V' });
    expect(sessionStorage.getItem('pii_token')).toBe('tok');
    logout();
    expect(sessionStorage.getItem('pii_token')).toBeNull();
  });
});
