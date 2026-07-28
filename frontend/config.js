// API base URL — auto-detected by environment.
(function () {
  if (window.API_BASE) return;
  const host = window.location.hostname;
  const port = window.location.port;
  const isLocal = host === 'localhost' || host === '127.0.0.1';

  // FastAPI serving frontend on :8000 → same origin
  if (isLocal && port === '8000') {
    window.API_BASE = '';
  } else if (isLocal) {
    // Vercel dev / other local port → point at API
    window.API_BASE = 'http://localhost:8000';
  } else {
    // Production Vercel → Fly.io API (update after deploy)
    window.API_BASE = 'https://polarity-family-office-rag.fly.dev';
  }
})();
