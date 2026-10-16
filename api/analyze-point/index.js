/**
 * Azure Function: analyze-point
 * Receives {lat, lng, userLat?, userLng?, radius?}
 * Returns point metadata + distance + risk placeholder
 * (Risk will come from Azure ML / Planetary Computer in next iteration)
 */
module.exports = async function (context, req) {
  const corsHeaders = {
    'Access-Control-Allow-Origin': '*',
    'Content-Type': 'application/json',
  };

  if (req.method === 'OPTIONS') {
    context.res = { status: 204, headers: corsHeaders, body: '' };
    return;
  }

  const body = req.body || {};
  const query = req.query || {};

  const lat    = parseFloat(body.lat    ?? query.lat);
  const lng    = parseFloat(body.lng    ?? query.lng);
  const userLat = parseFloat(body.userLat ?? query.userLat);
  const userLng = parseFloat(body.userLng ?? query.userLng);
  const radius  = parseFloat(body.radius  ?? query.radius ?? 300);

  if (isNaN(lat) || isNaN(lng) || lat < -90 || lat > 90 || lng < -180 || lng > 180) {
    context.res = {
      status: 400,
      headers: corsHeaders,
      body: JSON.stringify({ error: 'Coordenadas inválidas.' }),
    };
    return;
  }

  const result = {
    point: { lat, lng },
    radius,
    timestamp: new Date().toISOString(),
    // Distance from user (if provided)
    distance: null,
    insideZone: null,
    // Placeholder fields — populated by Azure ML / Planetary Computer trigger
    ndvi: null,
    waterRisk: 'pending',
    lastSatelliteUpdate: null,
    message: 'Datos satelitales pendientes de procesamiento.',
  };

  if (!isNaN(userLat) && !isNaN(userLng)) {
    const d = haversine(userLat, userLng, lat, lng);
    result.distance = Math.round(d);
    result.insideZone = d <= radius;
    result.message = result.insideZone
      ? `Estás dentro del área objetivo (${result.distance} m).`
      : `Estás a ${formatDist(d)} del punto objetivo.`;
  }

  context.res = {
    status: 200,
    headers: corsHeaders,
    body: JSON.stringify(result),
  };
};

function haversine(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const φ1 = (lat1 * Math.PI) / 180, φ2 = (lat2 * Math.PI) / 180;
  const Δφ = ((lat2 - lat1) * Math.PI) / 180;
  const Δλ = ((lon2 - lon1) * Math.PI) / 180;
  const a = Math.sin(Δφ / 2) ** 2 + Math.cos(φ1) * Math.cos(φ2) * Math.sin(Δλ / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function formatDist(m) {
  return m >= 1000 ? (m / 1000).toFixed(2) + ' km' : Math.round(m) + ' m';
}
