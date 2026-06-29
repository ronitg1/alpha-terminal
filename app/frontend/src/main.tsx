import React from 'react';
import ReactDOM from 'react-dom/client';

import App from './App';
import { AuthProvider } from './components/auth/auth-provider';
import { ThemeProvider } from './providers/theme-provider';
import { AUTH_ENABLED } from './config/auth';
import { installAuthFetch } from './services/auth-fetch';

import './index.css';

// Attach the Clerk session token to backend requests. Not installed when auth is
// off, so the dormant app's fetch behavior is unchanged.
if (AUTH_ENABLED) {
  installAuthFetch();
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthProvider>
      <ThemeProvider>
        <App />
      </ThemeProvider>
    </AuthProvider>
  </React.StrictMode>
);
