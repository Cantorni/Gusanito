// Where the PWA finds the API. Locally dev-server.js proxies /api; the Azure deploy overwrites this file
// with the Function App URL (see infra/deploy.sh).
window.GUSANITO = { apiBase: '/api' };
