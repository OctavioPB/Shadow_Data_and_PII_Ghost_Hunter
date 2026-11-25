import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from './App';
import { useAuthStore } from './store/authStore';

// Expired/invalid token: drop the session so the login page shows, instead of
// every query failing until the user clears storage by hand. Queries throw
// "API error 401"; mutations surface the API detail "Invalid token".
const logoutOnUnauthorized = (error: Error) => {
  if (/\b401\b/.test(error.message) || error.message === 'Invalid token') {
    useAuthStore.getState().logout();
  }
};

const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: logoutOnUnauthorized }),
  mutationCache: new MutationCache({ onError: logoutOnUnauthorized }),
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
});

const root = document.getElementById('root');
if (!root) throw new Error('Root element not found');

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>
);
