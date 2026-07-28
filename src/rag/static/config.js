// API base URL — auto-detected by environment.
(function () {
  if (window.API_BASE) return;
  const host = window.location.hostname;
  const port = window.location.port;
  const isLocal = host === 'localhost' || host === '127.0.0.1';

  if (isLocal && port === '8000') {
    window.API_BASE = '';
  } else if (isLocal) {
    window.API_BASE = 'http://localhost:8000';
  } else {
    window.API_BASE = 'https://polarity-family-office-rag.fly.dev';
  }
})();
