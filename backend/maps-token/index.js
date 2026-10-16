/**
 * Azure Function: maps-token
 * Returns the Azure Maps subscription key to the PWA frontend.
 * Key is stored in app settings (never in source code).
 */
module.exports = async function (context, req) {
  const key = process.env.AZURE_MAPS_KEY;

  if (!key) {
    context.res = {
      status: 503,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
      body: JSON.stringify({ error: 'AZURE_MAPS_KEY not configured.' }),
    };
    return;
  }

  context.res = {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*',
      'Cache-Control': 'no-store',
    },
    body: JSON.stringify({ key }),
  };
};
